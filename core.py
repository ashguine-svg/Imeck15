# core.py (D&D対応・右クリック動作変更・多言語対応・インデント修正版)
# ★★★ 認識範囲設定と画像キャプチャのロジックを分離 ＆ AttributeError を修正 ★★★
# ★★★ キャプチャプレビュー表示 ＆ 認識範囲設定後のUI再表示を修正 ★★★
# ★★★ キャプチャプレビュー表示の確実性を向上 (タブ切り替え、遅延追加) ★★★
# ★★★ リスナー再開処理を再度遅延させ、ログを追加 ★★★
# ★★★ 監視停止時の競合状態 (NoneType.handle) を RLock で修正 ★★★
# ★★★ 軽量化モードのプリセット判定を英語の内部名に変更 (問題1対応) ★★★
# ★★★ [修正] 監視停止時のUI更新タイミングを変更 (競合状態の解消) ★★★
# ★★★ [修正] 画像設定変更時にキャッシュを再構築し即時反映させる ★★★
# ★★★ [修正] 画面安定チェックのログにスコアと閾値を追加 (ユーザー要望) ★★★
# ★★★ [修正] 省エネモード (Eco Mode) がCPU負荷を下げられていなかった問題を修正 ★★★
# ★★★ [修正] on_image_settings_changed, on_preview_click_settings_changed にクリック種別の排他制御追加 ★★★
# ★★★ [修正] 監視中の設定変更が即時保存されず、キャッシュ再構築に反映されない競合状態を修正 ★★★
# ★★★ [修正] インターバルの定義を「マッチ後クリックまでの遅延」に変更 ★★★
# core.py (リファクタリング適用版)

import sys
import threading
import time
import cv2
import numpy as np
import os
from PySide6.QtCore import QObject, Signal, QThread, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QMouseEvent, QPainter, QPen, QColor, QBrush, QPainterPath, QKeyEvent
from PySide6.QtWidgets import QDialog, QWidget, QLabel, QVBoxLayout, QMessageBox, QApplication, QInputDialog, QFileDialog
from pathlib import Path
from pynput import mouse, keyboard
from concurrent.futures import ThreadPoolExecutor
from threading import Timer

from collections import deque
from PIL import Image
import imagehash

import shutil
import subprocess
if sys.platform == 'win32':
    import win32gui
else:
    win32gui = None


from capture import CaptureManager
from config import ConfigManager
from selection import SelectionOverlay, WindowSelectionListener
from matcher import _match_template_task, calculate_phash
from action import ActionManager
from template_manager import TemplateManager
from monitoring_states import IdleState, PriorityState, CountdownState


OPENCL_AVAILABLE = False
OPENCL_STATUS_MESSAGE = ""
try:
    if cv2.ocl.haveOpenCL():
        OPENCL_AVAILABLE = True
        OPENCL_STATUS_MESSAGE = "[INFO] OpenCL (GPU support) is available."
    else:
        OPENCL_STATUS_MESSAGE = "[INFO] OpenCL is not available."
except Exception as e:
    OPENCL_STATUS_MESSAGE = f"[WARN] Could not configure OpenCL: {e}"

class CoreEngine(QObject):
    updateStatus = Signal(str, str)
    updatePreview = Signal(np.ndarray, object)
    updateLog = Signal(str)
    updateRecAreaPreview = Signal(np.ndarray)
    _showUiSignal = Signal()
    selectionProcessStarted = Signal()
    selectionProcessFinished = Signal()
    _areaSelectedForProcessing = Signal(tuple)
    fpsUpdated = Signal(float)
    cacheBuildFinished = Signal()
    startMonitoringRequested = Signal()
    stopMonitoringRequested = Signal()
    bestScaleFound = Signal(str, float)
    windowScaleCalculated = Signal(float)
    askToSaveWindowBaseSizeSignal = Signal(str)
    askToApplyWindowScaleSignal = Signal(float)
    clickCountUpdated = Signal(int)

    def __init__(self, ui_manager, capture_manager, config_manager, logger, performance_monitor, locale_manager):
        super().__init__()
        self.ui_manager = ui_manager
        self.capture_manager = capture_manager
        self.config_manager = config_manager
        self.logger = logger
        self.locale_manager = locale_manager

        self.action_manager = ActionManager(self.logger)
        self.template_manager = TemplateManager(self.config_manager, self.logger)
        self.performance_monitor = performance_monitor
        self.logger.log(OPENCL_STATUS_MESSAGE)

        self.is_monitoring = False
        self._monitor_thread = None
        self._click_count = 0

        self.normal_template_cache = {}
        self.backup_template_cache = {}

        self.state = None
        self.state_lock = threading.RLock()

        self._last_clicked_path = None

        self.recognition_area = None
        self._is_capturing_for_registration = False
        self.current_image_path = None
        self.current_image_settings = None
        self.current_image_mat = None

        self.window_selection_listener = None
        self.keyboard_selection_listener = None

        self.target_hwnd = None

        self.priority_timers = {}
        self.folder_children_map = {}

        cpu_cores = os.cpu_count() or 8
        worker_threads = min(max(1, cpu_cores // 4), 2)
        self.logger.log("log_info_cores", cpu_cores, worker_threads)
        self.thread_pool = ThreadPoolExecutor(max_workers=worker_threads)
        self.cache_lock = threading.Lock()

        self.click_timer = None
        self.last_right_click_time = 0
        self.right_click_count = 0
        self.CLICK_INTERVAL = 0.3

        self.mouse_listener = None
        self._start_global_mouse_listener()

        self._showUiSignal.connect(self._show_ui_safe)
        self._areaSelectedForProcessing.connect(self.handle_area_selection)
        self.startMonitoringRequested.connect(self.start_monitoring)
        self.stopMonitoringRequested.connect(self.stop_monitoring)

        self.app_config = self.ui_manager.app_config
        self.current_window_scale = None
        self._pending_window_info = None
        self._pending_scale_prompt_info = None
        self._cooldown_until = 0

        self.effective_capture_scale = 1.0
        self.effective_frame_skip_rate = 2

        self.ECO_MODE_SKIP_RATE = 50
        self.ECO_CHECK_INTERVAL = 1.0
        self.ECO_MODE_DELAY = 5.0

        self.screen_stability_hashes = deque(maxlen=3)
        self.latest_frame_for_hash = None

        self.last_successful_click_time = 0
        self.is_eco_cooldown_active = False
        self._last_eco_check_time = 0

        self.on_app_config_changed()

        self._last_log_message = ""
        self._last_log_time = 0
        self._log_spam_filter = {"log_stability_hold_click", "log_eco_mode_standby", "log_stability_check_debug"}

        self.match_detected_at = {}

    def transition_to(self, new_state):
        with self.state_lock:
            self.state = new_state
        self._last_clicked_path = None
        self.match_detected_at.clear()

    def transition_to_timer_priority(self, folder_path):
        folder_settings = self.config_manager.load_item_setting(Path(folder_path))
        timeout_seconds = folder_settings.get('priority_timeout', 5) * 60
        timeout_time = time.time() + timeout_seconds
        new_state = PriorityState(self, 'timer', folder_path, timeout_time)
        self.transition_to(new_state)

    def transition_to_image_priority(self, folder_path):
        timeout_time = time.time() + 300
        required_children = self.folder_children_map.get(folder_path, set())
        new_state = PriorityState(self, 'image', folder_path, timeout_time, required_children)
        self.transition_to(new_state)

    def transition_to_countdown(self, trigger_match):
        new_state = CountdownState(self, trigger_match)
        self.transition_to(new_state)

    def _log(self, message: str, *args, force: bool = False):
        current_time = time.time()
        log_key = message
        if not force and \
           log_key == self._last_log_message and \
           log_key in self._log_spam_filter and \
           current_time - self._last_log_time < 3.0:
            return
        self.logger.log(log_key, *args)
        self._last_log_message = log_key
        self._last_log_time = current_time

    def set_opencl_enabled(self, enabled: bool):
        if OPENCL_AVAILABLE:
            try:
                cv2.ocl.setUseOpenCL(enabled)
                status_key = "log_linux_workaround_status_enabled" if cv2.ocl.useOpenCL() else "log_linux_workaround_status_disabled"
                status = self.locale_manager.tr(status_key)
                self.logger.log("log_opencl_set", status)
                if self.is_monitoring:
                    self.logger.log("log_opencl_rebuild")
                    self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
            except Exception as e:
                self.logger.log("log_opencl_error", str(e))

    def on_app_config_changed(self):
        self.app_config = self.ui_manager.app_config
        self.capture_manager.set_capture_method(self.app_config.get('capture_method', 'dxcam'))
        self.set_opencl_enabled(self.app_config.get('use_opencl', True))
        lw_conf = self.app_config.get('lightweight_mode', {})
        is_lw_enabled = lw_conf.get('enabled', False)
        preset_internal = lw_conf.get('preset', 'standard')
        if is_lw_enabled:
            user_frame_skip = self.app_config.get('frame_skip_rate', 2)
            if preset_internal == "standard": self.effective_capture_scale, self.effective_frame_skip_rate = 0.5, user_frame_skip + 5
            elif preset_internal == "performance": self.effective_capture_scale, self.effective_frame_skip_rate = 0.4, user_frame_skip + 20
            elif preset_internal == "ultra": self.effective_capture_scale, self.effective_frame_skip_rate = 0.3, user_frame_skip + 25
            else: self.effective_capture_scale, self.effective_frame_skip_rate = 0.5, user_frame_skip + 5
        else:
            self.effective_capture_scale = 1.0
            self.effective_frame_skip_rate = self.app_config.get('frame_skip_rate', 2)
        self.logger.log(
            "log_app_config_changed",
            self.capture_manager.current_method, is_lw_enabled, preset_internal,
            f"{self.effective_capture_scale:.2f}", self.effective_frame_skip_rate,
            cv2.ocl.useOpenCL() if OPENCL_AVAILABLE else 'N/A'
        )

    def _show_ui_safe(self):
        if self.ui_manager:
            self.ui_manager.show()
            self.ui_manager.raise_()
            try:
                self.ui_manager.activateWindow()
            except Exception as e:
                if 'SetForegroundWindow' in str(e): self.logger.log("log_warn_set_foreground_failed")
                else: self.logger.log("log_warn_activate_window_error", str(e))

    def _start_global_mouse_listener(self):
        if self.mouse_listener and self.mouse_listener.is_alive():
            self.logger.log("[DEBUG] Stopping existing listener before starting a new one.")
            self._stop_global_mouse_listener()
        if self.mouse_listener is None:
            self.logger.log("Attempting to start global mouse listener...")
            try:
                self.mouse_listener = mouse.Listener(on_click=self._on_global_click)
                self.logger.log("[DEBUG] Listener object created. Calling start()...")
                self.mouse_listener.start()
                if self.mouse_listener.is_alive(): self.logger.log("Global mouse listener started successfully (is_alive() confirmed).")
                else: self.logger.log("[ERROR] Listener start() called but is_alive() is false! Listener might have failed silently."); self.mouse_listener = None
            except Exception as e: self.logger.log(f"log_error_listener_start: Exception during listener.start(): {e}", str(e)); self.mouse_listener = None
        else: self.logger.log("[WARN] Mouse listener object was not None before start attempt. State issue?")

    def _stop_global_mouse_listener(self):
        if self.mouse_listener and self.mouse_listener.is_alive():
            self.logger.log("Attempting to stop global mouse listener...")
            try:
                self.mouse_listener.stop()
                if not self.mouse_listener.is_alive(): self.logger.log("Global mouse listener stopped successfully.")
                else: self.logger.log("[WARN] Listener stop() called but is_alive() is still true.")
            except Exception as e: self.logger.log("log_warn_listener_stop", str(e))
        self.mouse_listener = None

    def _on_global_click(self, x, y, button, pressed):
        if button == mouse.Button.right and pressed:
            current_time = time.time()
            if self.click_timer: self.click_timer.cancel(); self.click_timer = None
            if current_time - self.last_right_click_time > self.CLICK_INTERVAL: self.right_click_count = 1
            else: self.right_click_count += 1
            self.last_right_click_time = current_time
            if self.right_click_count == 3:
                self.logger.log("log_right_click_triple"); self.startMonitoringRequested.emit(); self.right_click_count = 0
            else: self.click_timer = Timer(self.CLICK_INTERVAL, self._handle_click_timer); self.click_timer.start()

    def _handle_click_timer(self):
        if self.right_click_count == 2: self.logger.log("log_right_click_double"); self.stopMonitoringRequested.emit()
        self.right_click_count = 0; self.click_timer = None

    def cleanup(self):
        self.stop_monitoring()
        self._stop_global_mouse_listener()
        if self.capture_manager: self.capture_manager.cleanup()
        if hasattr(self, 'thread_pool') and self.thread_pool: self.thread_pool.shutdown(wait=False)

    def _on_cache_build_done(self, future):
        try: future.result()
        except Exception as e: self.logger.log("log_cache_build_error", str(e))
        finally: self.cacheBuildFinished.emit()

    def capture_image_for_registration(self):
        self._is_capturing_for_registration = True
        self.ui_manager.setRecAreaDialog()

    def delete_selected_items(self, paths_to_delete: list):
        if not paths_to_delete: return
        self.ui_manager.set_tree_enabled(False); deleted_count = 0; failed_count = 0; last_error = ""
        try:
            for path_str in paths_to_delete:
                try: self.config_manager.remove_item(path_str); self.logger.log("log_item_deleted", Path(path_str).name); deleted_count += 1
                except Exception as e: last_error = str(e); self.logger.log("log_item_delete_failed", Path(path_str).name, last_error); failed_count += 1
            if failed_count > 0: QMessageBox.critical(self.ui_manager, self.locale_manager.tr("error_title_delete_failed"), self.locale_manager.tr("error_message_delete_failed", failed_count) + f"\n{last_error}")
        finally:
            if self.thread_pool: self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
            else: self.ui_manager.set_tree_enabled(True)

    def on_folder_settings_changed(self):
        self.logger.log("log_folder_settings_changed")
        self.ui_manager.set_tree_enabled(False)
        if self.thread_pool: self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
        else: self.ui_manager.set_tree_enabled(True)

    def create_folder(self):
        folder_name, ok = QInputDialog.getText(self.ui_manager, self.locale_manager.tr("create_folder_title"), self.locale_manager.tr("create_folder_prompt"))
        if ok and folder_name:
            success, message_key_or_text = self.config_manager.create_folder(folder_name)
            if success:
                self.logger.log(message_key_or_text); self.ui_manager.update_image_tree()
                if self.thread_pool: self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
                else: self.ui_manager.set_tree_enabled(True)
            else: QMessageBox.warning(self.ui_manager, self.locale_manager.tr("error_title_create_folder"), self.locale_manager.tr(message_key_or_text))

    def move_item_into_folder(self):
        selected_items = self.ui_manager.image_tree.selectedItems(); source_paths = [item.data(0, Qt.UserRole) for item in selected_items if item.data(0, Qt.UserRole)]; lm = self.locale_manager.tr
        if not source_paths: QMessageBox.warning(self.ui_manager, lm("warn_move_item_no_selection"), lm("warn_move_item_no_selection_text")); return
        for path_str in source_paths:
            source_path = Path(path_str)
            if not source_path.is_file() or source_path.parent != self.config_manager.base_dir: QMessageBox.warning(self.ui_manager, lm("warn_move_item_not_image"), lm("warn_move_item_not_image_text")); return
        folders = [item for item in self.config_manager.get_hierarchical_list() if item['type'] == 'folder']
        if not folders: QMessageBox.information(self.ui_manager, lm("info_move_item_no_folder"), lm("info_move_item_no_folder_text")); return
        folder_names = [f['name'] for f in folders]
        dest_folder_name, ok = QInputDialog.getItem(self.ui_manager, lm("move_item_dialog_title"), lm("move_item_dialog_prompt"), folder_names, 0, False)
        if ok and dest_folder_name: dest_folder_path_str = str(self.config_manager.base_dir / dest_folder_name); self.move_items_into_folder(source_paths, dest_folder_path_str)

    def move_items_into_folder(self, source_paths: list, dest_folder_path_str: str):
        self.ui_manager.set_tree_enabled(False); moved_count = 0; failed_count = 0; final_message = ""
        try:
            for source_path_str in source_paths:
                success, message_or_key = self.config_manager.move_item(source_path_str, dest_folder_path_str)
                if success: self.logger.log(message_or_key); moved_count += 1
                else: self.logger.log("log_move_item_failed", self.locale_manager.tr(message_or_key)); failed_count += 1; final_message = self.locale_manager.tr(message_or_key)
            if failed_count > 0: QMessageBox.critical(self.ui_manager, self.locale_manager.tr("error_title_move_item_failed"), self.locale_manager.tr("error_message_move_item_failed", failed_count, final_message))
        finally:
            if self.thread_pool: self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
            else: self.ui_manager.set_tree_enabled(True)

    def move_item_out_of_folder(self):
        source_path_str, name = self.ui_manager.get_selected_item_path(); lm = self.locale_manager.tr
        if not source_path_str: QMessageBox.warning(self.ui_manager, lm("warn_move_out_no_selection"), lm("warn_move_out_no_selection_text")); return
        source_path = Path(source_path_str)
        if not source_path.is_file() or source_path.parent == self.config_manager.base_dir: QMessageBox.warning(self.ui_manager, lm("warn_move_out_not_in_folder"), lm("warn_move_out_not_in_folder_text")); return
        dest_folder_path_str = str(self.config_manager.base_dir)
        success, message_or_key = self.config_manager.move_item(source_path_str, dest_folder_path_str)
        if success:
            self.logger.log(message_or_key); self.ui_manager.update_image_tree()
            if self.thread_pool: self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
            else: self.ui_manager.set_tree_enabled(True)
        else: QMessageBox.critical(self.ui_manager, lm("error_title_move_out_failed"), self.locale_manager.tr(message_or_key))

    # --- 修正: load_image_and_settings ---
    def load_image_and_settings(self, file_path: str):
        """Loads image data and settings for the given file path."""
        
        # --- ▼▼▼ 修正箇所 ▼▼▼ ---
        if file_path is None or Path(file_path).is_dir():
            self.current_image_path = None
            self.current_image_settings = None
            self.current_image_mat = None
            # self.ui_manager.set_settings_from_data(None) # _recalculate_and_update が担当
            # return <-- 削除
        
        else:
            try:
                self.current_image_path = file_path
                loaded_settings = self.config_manager.load_item_setting(Path(file_path))
                self.current_image_settings = loaded_settings # CoreEngineも保持
    
                with open(file_path, 'rb') as f:
                    file_bytes = np.frombuffer(f.read(), np.uint8)
                    self.current_image_mat = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
    
                if self.current_image_mat is None:
                    raise ValueError(self.locale_manager.tr("log_image_decode_failed"))

            except Exception as e:
                self.logger.log("log_image_load_failed", file_path, str(e))
                self.current_image_path = None
                self.current_image_settings = None
                self.current_image_mat = None
                # self.ui_manager.set_settings_from_data(None) # _recalculate_and_update が担当
                # return <-- 削除
        
        # 1. PreviewModeManagerにロードさせる (UI更新もトリガーされる)
        #    (画像が無い場合は self.current_image_settings は None になっている)
        self.ui_manager.set_settings_from_data(self.current_image_settings)
        
        # 2. 固定ROI計算と最終的なプレビュー更新
        #    (画像が無い場合は self.current_image_mat は None になっている)
        self._recalculate_and_update()
        # --- ▲▲▲ 修正箇所 ▲▲▲ ---
    def _handle_setting_change_and_rebuild(self, request_save=False): # request_saveのデフォルトはFalseのまま
        """
        Applies setting changes, updates preview, and handles saving and cache rebuild based on monitoring state.
        Saving is now triggered internally by CoreEngine after receiving settings.
        """
        if self.is_monitoring:
            self._recalculate_and_update() # プレビュー更新 (固定ROI再計算含む)
            self.save_current_settings()    # 即時保存
            if self.thread_pool:            # キャッシュ再構築
                self.logger.log("log_item_setting_changed_rebuild")
                self.ui_manager.set_tree_enabled(False)
                self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
        else:
            self._recalculate_and_update() # プレビュー更新 (固定ROI再計算含む)
            # 保存は on_image_settings_changed で save_timer.start() により遅延実行される

    # --- 修正: on_image_settings_changed ---
    def on_image_settings_changed(self, settings: dict):
        """Handles changes emitted from PreviewModeManager (via UIManager's _emit_settings_for_save)."""
        image_path_from_ui = settings.get('image_path')

        if self.current_image_settings and image_path_from_ui == self.current_image_path:
            # --- 排他制御ロジックは PreviewModeManager が担当するため削除 ---

            # PreviewModeManager で処理済みの設定をそのまま適用
            self.current_image_settings.update(settings)

            # 変更を適用し、保存とキャッシュ再構築をハンドル
            # _recalculate_and_update は固定ROI計算とプレビュー更新を行う
            # 保存要求は CoreEngine 側でタイマーを開始するため False を渡す必要もなくなった
            self._handle_setting_change_and_rebuild() # request_save 引数を削除

            # Core Engine 側で遅延保存タイマーを開始
            self.ui_manager.save_timer.start()

    # --- 修正: on_preview_click_settings_changed は削除 ---
    # (メソッド全体を削除)

    # --- 修正: _recalculate_and_update ---
    def _recalculate_and_update(self): # request_save パラメータを削除
        """Recalculates fixed ROI if needed and updates the preview signal."""
        if self.current_image_mat is not None and self.current_image_settings:
            h, w = self.current_image_mat.shape[:2]
            self.current_image_settings['roi_rect'] = self.calculate_roi_rect((w, h), self.current_image_settings)

        self.updatePreview.emit(self.current_image_mat, self.current_image_settings)

        # --- 保存要求ロジックは削除 ---

    # --- (calculate_roi_rect から monitoring_loop までは変更なし) ---
    def calculate_roi_rect(self, image_size, settings):
        if not settings.get('roi_enabled', False): return None
        roi_mode = settings.get('roi_mode', 'fixed')
        if roi_mode == 'variable': return settings.get('roi_rect_variable')
        center_x, center_y = -1, -1
        if settings.get('point_click') and settings.get('click_position'): center_x, center_y = settings['click_position']
        elif settings.get('range_click') and settings.get('click_rect'): rect = settings['click_rect']; center_x = (rect[0] + rect[2]) / 2; center_y = (rect[1] + rect[3]) / 2
        if center_x == -1: return None
        roi_w, roi_h = 200, 200; x1 = int(center_x - roi_w / 2); y1 = int(center_y - roi_h / 2); x2 = int(center_x + roi_w / 2); y2 = int(center_y + roi_h / 2)
        return (x1, y1, x2, y2)

    def save_current_settings(self):
        if self.current_image_path and self.current_image_settings:
            self.config_manager.save_item_setting(Path(self.current_image_path), self.current_image_settings)
            self.logger.log("log_settings_saved", Path(self.current_image_path).name)

    def load_images_into_manager(self, file_paths):
        self.ui_manager.set_tree_enabled(False); added_count = 0
        for fp in file_paths:
            try: self.config_manager.add_item(Path(fp)); added_count += 1
            except Exception as e: self.logger.log("Error adding item %s: %s", Path(fp).name, str(e))
        if added_count > 0:
            self._log("log_images_added", added_count)
            if self.thread_pool: self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
            else: self.ui_manager.set_tree_enabled(True)
        else: self.ui_manager.set_tree_enabled(True)

    def on_order_changed(self):
        try:
            if hasattr(self.ui_manager, 'save_tree_order'): self.ui_manager.save_tree_order(); self.logger.log("log_order_saved")
            else: self.logger.log("log_warn_save_order_failed")
        except Exception as e: self.logger.log("log_error_save_order", str(e))
        self.ui_manager.set_tree_enabled(False)
        if self.thread_pool: self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
        else: self.ui_manager.set_tree_enabled(True)

    def _build_template_cache(self):
        with self.cache_lock:
            (self.normal_template_cache, self.backup_template_cache, self.priority_timers, self.folder_children_map) = \
                self.template_manager.build_cache(self.app_config, self.current_window_scale, self.effective_capture_scale, self.is_monitoring, self.priority_timers)

    def start_monitoring(self):
        if not self.recognition_area: QMessageBox.warning(self.ui_manager, self.locale_manager.tr("warn_rec_area_not_set_title"), self.locale_manager.tr("warn_rec_area_not_set_text")); return
        if not self.is_monitoring:
            self.is_monitoring = True; self.transition_to(IdleState(self)); self._click_count = 0; self._cooldown_until = 0; self._last_clicked_path = None; self.screen_stability_hashes.clear(); self.last_successful_click_time = 0; self.is_eco_cooldown_active = False; self._last_eco_check_time = time.time(); self.match_detected_at.clear()
            self.ui_manager.set_tree_enabled(False)
            if self.thread_pool:
                self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
                self._monitor_thread = threading.Thread(target=self._monitoring_loop, daemon=True); self._monitor_thread.start(); self.updateStatus.emit("monitoring", "blue"); self.logger.log("log_monitoring_started")
            else: self.logger.log("Error: Thread pool not available to start monitoring."); self.is_monitoring = False; self.ui_manager.set_tree_enabled(True)

    def stop_monitoring(self):
        if self.is_monitoring:
            self.is_monitoring = False
            with self.state_lock: self.state = None
            self.updateStatus.emit("idle", "green"); self.logger.log("log_monitoring_stopped"); self.ui_manager.set_tree_enabled(True)
            if self._monitor_thread and self._monitor_thread.is_alive(): self._monitor_thread.join(timeout=1.0)
            with self.cache_lock:
                for cache in [self.normal_template_cache, self.backup_template_cache]:
                    for item in cache.values(): item['best_scale'] = None
            self.match_detected_at.clear()

    def _monitoring_loop(self):
        last_match_time_map = {}; fps_last_time = time.time(); frame_counter = 0
        while self.is_monitoring:
            with self.state_lock: current_state = self.state
            if not current_state:
                if not self.is_monitoring: break
                else: self.logger.log("[WARN] Monitoring is active but state is None. Re-initializing to IdleState."); self.transition_to(IdleState(self)); continue
            try:
                current_time = time.time()
                if self._cooldown_until > current_time: time.sleep(min(self._cooldown_until - current_time, 0.1)); continue
                frame_counter += 1; delta_time = current_time - fps_last_time
                if delta_time >= 1.0: fps = frame_counter / delta_time; self.fpsUpdated.emit(fps); fps_last_time = current_time; frame_counter = 0
                if isinstance(current_state, IdleState): self._check_and_activate_timer_priority_mode()
                is_eco_enabled = self.app_config.get('eco_mode',{}).get('enabled',True)
                is_eco_eligible = (is_eco_enabled and self.last_successful_click_time > 0 and isinstance(current_state, IdleState) and (current_time - self.last_successful_click_time > self.ECO_MODE_DELAY))
                self.is_eco_cooldown_active = is_eco_eligible
                skip_handle = False

                if isinstance(current_state, CountdownState): time.sleep(1.0) # カウントダウン中は常に1秒待機
                # --- ▼▼▼ 修正箇所 ▼▼▼ ---
                elif self.is_eco_cooldown_active:
                    self._log("log_eco_mode_standby")
                    time_since_last_check = current_time - self._last_eco_check_time
                    sleep_time = 0 # ★★★ sleep_time を初期化 ★★★
                    if time_since_last_check < self.ECO_CHECK_INTERVAL:
                        sleep_time = self.ECO_CHECK_INTERVAL - time_since_last_check
                        # sleep_time > 0 のチェックは不要 (計算上必ず正になるため)
                        time.sleep(sleep_time)
                        continue # スリープしたらループの残りをスキップ
                    else: # チェック時刻になった場合
                        self._last_eco_check_time = current_time
                        skip_handle = True # state.handle はスキップするが、画像チェックは行う
                # --- ▲▲▲ 修正箇所 ▲▲▲ ---
                elif (frame_counter % self.effective_frame_skip_rate) != 0: # 通常のフレームスキップ
                    time.sleep(0.01)
                    continue

                # --- フレーム取得と処理 (省エネモードでも実行される) ---
                screen_bgr = self.capture_manager.capture_frame(region=self.recognition_area)
                if screen_bgr is None: self._log("log_capture_failed"); time.sleep(1.0); continue
                if self.effective_capture_scale != 1.0: screen_bgr = cv2.resize(screen_bgr, None, fx=self.effective_capture_scale, fy=self.effective_capture_scale, interpolation=cv2.INTER_AREA)

                self.latest_frame_for_hash = screen_bgr.copy() # 安定性チェック用にコピー
                screen_gray = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)

                # --- OpenCL UMat 変換 ---
                screen_bgr_umat, screen_gray_umat = None, None
                if OPENCL_AVAILABLE and cv2.ocl.useOpenCL():
                    try: screen_bgr_umat = cv2.UMat(screen_bgr); screen_gray_umat = cv2.UMat(screen_gray)
                    except Exception as e: self.logger.log("log_umat_convert_failed", str(e))

                screen_data = (screen_bgr, screen_gray, screen_bgr_umat, screen_gray_umat)

                # --- 省エネモード復帰チェック ---
                all_matches = self._find_matches_for_eco_check(screen_data, current_state)
                if self.is_eco_cooldown_active and all_matches:
                    self.last_successful_click_time = time.time() # マッチが見つかったらタイマーリセット
                    self._log("log_eco_mode_resumed", force=True)
                    # is_eco_cooldown_active は次のループで False になる

                # --- 状態に応じた処理 (省エネモードのチェック後) ---
                if not skip_handle: # 省エネモードのチェックタイミングでなければ実行
                    current_state.handle(current_time, screen_data, last_match_time_map, pre_matches=all_matches)

            except Exception as e:
                # 監視中の予期せぬエラー
                if isinstance(e, AttributeError) and "'NoneType' object has no attribute 'handle'" in str(e):
                    # 監視停止直後の競合状態のエラーは、より具体的にログ出力
                    self.logger.log("[CRITICAL] Race condition detected (state became None unexpectedly). Loop will restart/exit.")
                else:
                    # その他の一般的なエラー
                    self.logger.log("log_error_monitoring_loop", str(e))
                # エラー発生時は少し待機
                time.sleep(1.0)
            finally:
                # ループの最後にわずかな待機時間を入れる (CPU使用率抑制)
                time.sleep(0.01)

    # --- (_find_matches_for_eco_check から get_backup_click_countdown までは変更なし) ---
    def _find_matches_for_eco_check(self, screen_data, current_state):
        def filter_cache_for_eco(cache): return {p: d for p, d in cache.items() if d.get('folder_mode') not in ['excluded', 'priority_timer']}
        active_normal_cache = filter_cache_for_eco(self.normal_template_cache); normal_matches = self._find_best_match(*screen_data, active_normal_cache)
        if isinstance(current_state, IdleState):
            active_backup_cache = filter_cache_for_eco(self.backup_template_cache); backup_trigger_matches = self._find_best_match(*screen_data, active_backup_cache)
            if backup_trigger_matches: normal_matches.extend(backup_trigger_matches)
        return normal_matches

    def check_screen_stability(self) -> bool:
        if not hasattr(self, 'latest_frame_for_hash') or self.latest_frame_for_hash is None: return False
        h, w, _ = self.latest_frame_for_hash.shape
        if h < 64 or w < 64: self._log("log_stability_check_skip_size", force=True); return True
        roi = self.latest_frame_for_hash[0:64, 0:64]; current_hash = calculate_phash(roi)
        if current_hash is None: return False
        self.screen_stability_hashes.append(current_hash)
        if len(self.screen_stability_hashes) < self.screen_stability_hashes.maxlen: self._log("log_stability_check_history_low", len(self.screen_stability_hashes), self.screen_stability_hashes.maxlen, force=True); return False
        threshold = self.app_config.get('screen_stability_check', {}).get('threshold', 8); hash_diff = self.screen_stability_hashes[-1] - self.screen_stability_hashes[0]
        self._log("log_stability_check_debug", str(self.screen_stability_hashes[-1]), str(self.screen_stability_hashes[0]), hash_diff, threshold, force=True)
        return hash_diff <= threshold

    def _check_and_activate_timer_priority_mode(self):
        current_time = time.time()
        for folder_path, activation_time in list(self.priority_timers.items()):
            if current_time >= activation_time: self.transition_to_timer_priority(folder_path); break

    def _process_matches_as_sequence(self, all_matches, current_time, last_match_time_map):
        # --- ▼▼▼ 修正箇所 (L. 638) ▼▼▼ ---
        # 1行で書かれていた if ブロックを、正しいインデントに修正
        if not all_matches:
            current_match_paths = set()
            keys_to_remove = [path for path in self.match_detected_at if path not in current_match_paths]
            for path in keys_to_remove:
                del self.match_detected_at[path]
            return False
        # --- ▲▲▲ 修正箇所 ▲▲▲ ---

        clickable_after_interval = []
        current_match_paths = {m['path'] for m in all_matches}

        for m in all_matches:
            path = m['path']
            settings = m['settings']
            interval = settings.get('interval_time', 1.5)
            debounce = settings.get('debounce_time', 0.0)
            last_clicked = last_match_time_map.get(path, 0)

            effective_debounce = debounce if self._last_clicked_path == path else 0.0

            if current_time - last_clicked <= effective_debounce:
                # デバウンス期間中のため、検出タイマーもリセット
                if path in self.match_detected_at:
                    del self.match_detected_at[path]
                continue

            if path not in self.match_detected_at:
                # 新規検出
                self.match_detected_at[path] = current_time
                self.logger.log(f"[DEBUG] Detected '{Path(path).name}'. Interval timer started ({interval:.1f}s).")
                continue
            else:
                # 検出継続中
                detected_at = self.match_detected_at[path]
                time_since_detected = current_time - detected_at

                if time_since_detected >= interval:
                    # インターバル経過
                    clickable_after_interval.append(m)
                    self.logger.log(f"[DEBUG] Interval elapsed for '{Path(path).name}' ({time_since_detected:.2f}s >= {interval:.1f}s). Added to clickable.")
                else:
                    # インターバル待機中
                    remaining = interval - time_since_detected
                    self.logger.log(f"[DEBUG] Waiting for interval on '{Path(path).name}'. Remaining: {remaining:.2f}s.")

        keys_to_remove = [p for p in self.match_detected_at if p not in current_match_paths]

        # --- ▼▼▼ 修正箇所 (L. 676) ▼▼▼ ---
        # ログ出力がループ内にあると大量に出力されるため、ループの外に移動
        if keys_to_remove:
            paths_removed = []
            for p in keys_to_remove:
                if p in self.match_detected_at: # 念のため存在確認
                    del self.match_detected_at[p]
                    paths_removed.append(Path(p).name)
            if paths_removed:
                self.logger.log(f"[DEBUG] Cleared detection times for disappearing images: {', '.join(paths_removed)}")
        # --- ▲▲▲ 修正箇所 ▲▲▲ ---

        if not clickable_after_interval:
            return False

        try:
            # インターバル時間が最短で、信頼度が最も高いものを優先
            potential_target_match = min(clickable_after_interval, key=lambda m: (m['settings'].get('interval_time', 1.5), -m['confidence']))
        except ValueError:
            self.logger.log("[DEBUG] Error finding minimum in clickable_after_interval list.")
            return False

        try:
            # --- デバッグログ (変更なし) ---
            target_name = Path(potential_target_match['path']).name
            target_interval_setting = potential_target_match['settings'].get('interval_time', 1.5)
            target_conf = potential_target_match['confidence']
            target_detected_at = self.match_detected_at.get(potential_target_match['path'], 0)
            target_time_since_detected = current_time - target_detected_at

            self.logger.log(f"[DEBUG] Potential click target: {target_name}, IntervalSetting: {target_interval_setting:.1f}s, TimeSinceDetected: {target_time_since_detected:.2f}s, Conf: {target_conf:.2f}")

            if len(clickable_after_interval) > 1:
                other_candidates = []
                for item in clickable_after_interval:
                    if item['path'] != potential_target_match['path']:
                        other_name = Path(item['path']).name
                        other_interval = item['settings'].get('interval_time', 1.5)
                        other_conf = item['confidence']
                        other_candidates.append(f"{other_name}({other_interval:.1f}s,{other_conf:.2f})")
                if other_candidates:
                    self.logger.log(f"[DEBUG] Other candidates passed interval: {', '.join(other_candidates)}")

        except Exception as log_e:
            self.logger.log(f"[ERROR] Failed to generate interval diagnostic log: {log_e}")

        is_stability_check_enabled = self.app_config.get('screen_stability_check', {}).get('enabled', True)

        if is_stability_check_enabled and not self.is_eco_cooldown_active:
            if not self.check_screen_stability():
                self._log("log_stability_hold_click")
                self.updateStatus.emit("unstable", "orange")
                self.last_successful_click_time = current_time # 不安定でもクリックは試みた（スキップした）ので、エコモードタイマーをリセット
                self.logger.log(f"[DEBUG] Click skipped for {Path(potential_target_match['path']).name} due to screen instability (interval timer continues).")
                return False

        if not self.is_eco_cooldown_active:
            self.updateStatus.emit("monitoring", "blue")

        if not self.is_monitoring:
            return False

        target_path = potential_target_match['path']
        self.logger.log(f"[DEBUG] Executing click for {Path(target_path).name}")
        self._execute_click(potential_target_match)

        click_time = time.time()
        last_match_time_map[target_path] = click_time

        if target_path in self.match_detected_at:
            del self.match_detected_at[target_path]
            self.logger.log(f"[DEBUG] Reset detection time for clicked image: {Path(target_path).name}")

        self.logger.log(f"[DEBUG] Updated last_match_time_map for {Path(target_path).name} to {click_time:.2f}")
        return True

    def _find_best_match(self, s_bgr, s_gray, s_bgr_umat, s_gray_umat, cache):
        """Finds the best template match within the given screen data using the specified cache."""
        results = []
        futures = []

        with self.cache_lock: # Ensure cache access is thread-safe
            if not cache:
                return [] # Return empty list if cache is empty

            use_cl = OPENCL_AVAILABLE and cv2.ocl.useOpenCL()
            use_gs = self.app_config.get('grayscale_matching', False)

            # Select screen image based on grayscale setting and OpenCL availability
            screen_image = s_gray if use_gs else s_bgr
            if use_cl:
                screen_umat = s_gray_umat if use_gs else s_bgr_umat
                # Use UMat if available, otherwise fallback to NumPy array
                screen_image = screen_umat if screen_umat is not None else screen_image

            # Get screen shape safely (handling UMat)
            s_shape = screen_image.get().shape[:2] if use_cl and isinstance(screen_image, cv2.UMat) else screen_image.shape[:2]


            # Iterate through templates in the cache
            for path, data in cache.items():
                # Determine which scales to check (all if searching, only best if found)
                is_searching_scale = data['best_scale'] is None
                templates_to_check = data['scaled_templates']
                if not is_searching_scale:
                    # Filter for only the best scale if already found
                    filtered_templates = [t for t in templates_to_check if t['scale'] == data['best_scale']]
                    # Use filtered list, or fallback to all if filter somehow results in empty list
                    templates_to_check = filtered_templates if filtered_templates else data['scaled_templates']


                for t in templates_to_check:
                    # --- 修正: try ブロックを開始 ---
                    try:
                        # Select template image (color/gray, UMat/NumPy)
                        template_image = t['gray'] if use_gs else t['image']
                        if use_cl:
                            t_umat = t.get('gray_umat' if use_gs else 'image_umat')
                            template_image = t_umat if t_umat else template_image

                        # Prepare task data for matching function
                        task_data = {'path': path, 'settings': data['settings'], 'template': template_image, 'scale': t['scale']}
                        t_shape = t['shape']

                        # Perform matching: directly if using OpenCL, submit to thread pool otherwise
                        if use_cl:
                            match_result = _match_template_task(screen_image, task_data, s_shape, t_shape)
                            if match_result:
                                results.append(match_result)
                        else: # --- 修正: else も try ブロック内に含める ---
                            # Submit task to thread pool for parallel execution
                            if self.thread_pool:
                                future = self.thread_pool.submit(_match_template_task, screen_image, task_data, s_shape, t_shape)
                                futures.append(future)
                    # --- 修正: except ブロックの位置とインデントを修正 ---
                    except Exception as e:
                         # テンプレートごとの処理中にエラーが発生した場合のログ
                         self.logger.log("Error during template processing for %s (scale %s): %s", Path(path).name, t.get('scale', 'N/A'), str(e))

        # --- スレッドプールの結果取得 (ここは変更なし、ただし try/except は重要) ---
        if not use_cl:
            for f in futures:
                try:
                    match_result = f.result(); # Wait for task completion and get result
                    if match_result: results.append(match_result)
                except Exception as e:
                     # スレッドからの結果取得時にエラーが発生した場合のログ
                     self.logger.log("Error getting result from match thread: %s", str(e))

        # --- (以降のベストマッチ判定、キャッシュ更新、return results は変更なし) ---
        if not results: return []
        best_match_overall = max(results, key=lambda r: r['confidence']); best_match_path = best_match_overall['path']; best_match_scale = best_match_overall['scale']
        target_cache = None
        if best_match_path in self.normal_template_cache: target_cache = self.normal_template_cache
        elif best_match_path in self.backup_template_cache: target_cache = self.backup_template_cache
        if target_cache:
             with self.cache_lock:
                 cache_item = target_cache.get(best_match_path)
                 if cache_item and cache_item['best_scale'] is None: cache_item['best_scale'] = best_match_scale; self._log("log_best_scale_found", Path(best_match_path).name, f"{best_match_scale:.3f}", f"{best_match_overall['confidence']:.2f}"); self.bestScaleFound.emit(best_match_path, best_match_scale)
        return results

    def _execute_click(self, match_info):
        result = self.action_manager.execute_click(match_info, self.recognition_area, self.target_hwnd, self.effective_capture_scale)
        if result and result.get('success'): self._click_count += 1; self._last_clicked_path = result.get('path'); self.last_successful_click_time = time.time(); self.clickCountUpdated.emit(self._click_count)

    def set_recognition_area(self, method: str):
        self.selectionProcessStarted.emit(); self.ui_manager.hide();
        if self.performance_monitor: self.performance_monitor.hide()
        self._stop_global_mouse_listener()
        if method == "rectangle":
            if not self._is_capturing_for_registration: self.target_hwnd = None; self.current_window_scale = None; self.windowScaleCalculated.emit(0.0); self.logger.log("log_rec_area_set_rect")
            else: self.logger.log("log_capture_area_set_rect")
            self.selection_overlay = SelectionOverlay(); self.selection_overlay.selectionComplete.connect(self._areaSelectedForProcessing.emit); self.selection_overlay.selectionCancelled.connect(self._on_selection_cancelled); self.selection_overlay.showFullScreen()
        elif method == "window":
            if not self._is_capturing_for_registration: self.logger.log("log_rec_area_set_window")
            else: self.logger.log("log_capture_area_set_window")
            self.window_selection_listener = WindowSelectionListener(self._handle_window_click_for_selection); self.window_selection_listener.start(); self.keyboard_selection_listener = keyboard.Listener(on_press=self._on_key_press_for_selection); self.keyboard_selection_listener.start()

    def _on_selection_cancelled(self):
        self.logger.log("log_selection_cancelled"); self._is_capturing_for_registration = False
        if self.window_selection_listener and self.window_selection_listener.is_alive(): self.window_selection_listener.stop(); self.window_selection_listener = None
        if self.keyboard_selection_listener and self.keyboard_selection_listener.is_alive(): self.keyboard_selection_listener.stop(); self.keyboard_selection_listener = None
        if hasattr(self, 'selection_overlay') and self.selection_overlay: self.selection_overlay.close(); self.selection_overlay = None
        self.selectionProcessFinished.emit(); self._show_ui_safe()
        self.logger.log("[DEBUG] Scheduling listener restart after cancellation (150ms delay)..."); QTimer.singleShot(150, self._start_global_mouse_listener)

    def _on_key_press_for_selection(self, key):
        if key == keyboard.Key.esc:
            self.logger.log("log_selection_cancelled_key")
            if self.keyboard_selection_listener and self.keyboard_selection_listener.is_alive(): self.keyboard_selection_listener.stop(); self.keyboard_selection_listener = None
            if self.window_selection_listener and self.window_selection_listener.is_alive(): self.window_selection_listener.stop(); self.window_selection_listener = None
            self._on_selection_cancelled(); return False

    def _handle_window_click_for_selection(self, x, y):
        if self.keyboard_selection_listener: self.keyboard_selection_listener.stop(); self.keyboard_selection_listener = None
        if sys.platform == 'win32' and win32gui: self._handle_window_click_for_selection_windows(x, y)
        elif sys.platform.startswith('linux'): self._handle_window_click_for_selection_linux(x, y)
        else: self.logger.log("Window selection not supported on this platform."); self._on_selection_cancelled(); return

    def _handle_window_click_for_selection_windows(self, x, y):
        try:
            hwnd = win32gui.WindowFromPoint((x, y))
            if not hwnd: self._on_selection_cancelled(); return
            if not self._is_capturing_for_registration:
                self.target_hwnd = hwnd
                if 'dxcam' in sys.modules and hasattr(self.capture_manager, 'dxcam_sct') and self.capture_manager.dxcam_sct:
                    try: self.capture_manager.dxcam_sct.target_hwnd = hwnd
                    except Exception as dxcam_err: self.logger.log(f"Error setting DXCam target HWND: {dxcam_err}")
            client_rect_win = win32gui.GetClientRect(hwnd); left, top = win32gui.ClientToScreen(hwnd, (0, 0)); right = left + client_rect_win[2]; bottom = top + client_rect_win[3]
            if right <= left or bottom <= top: self.logger.log("log_window_invalid_rect", left, top, right, bottom); self._on_selection_cancelled(); return
            try: import pyautogui; screen_width, screen_height = pyautogui.size(); rect = (max(0, left), max(0, top), min(screen_width, right), min(screen_height, bottom))
            except ImportError: rect = (max(0, left), max(0, top), right, bottom)
            if self._is_capturing_for_registration: self._areaSelectedForProcessing.emit(rect); self.selectionProcessFinished.emit(); return
            title = win32gui.GetWindowText(hwnd); self._pending_window_info = {"title": title, "dims": {'width': rect[2] - rect[0], 'height': rect[3] - rect[1]}, "rect": rect}
            if title and title not in self.config_manager.load_window_scales(): self.askToSaveWindowBaseSizeSignal.emit(title)
            else: self.process_base_size_prompt_response(save_as_base=False)
        except Exception as e:
            self.logger.log("log_window_get_rect_failed", str(e))
            if not self._is_capturing_for_registration: self.target_hwnd = None
            self._showUiSignal.emit(); self.selectionProcessFinished.emit()

    def _handle_window_click_for_selection_linux(self, x, y):
        missing_tools = [tool for tool in ['xdotool', 'xwininfo'] if not shutil.which(tool)]
        if missing_tools: self.logger.log("log_linux_tool_not_found", ', '.join(missing_tools)); self._showUiSignal.emit(); self.selectionProcessFinished.emit(); return
        try:
            id_proc = subprocess.run(['xdotool', 'getmouselocation'], capture_output=True, text=True, check=True, timeout=2); window_id_line = next((line for line in id_proc.stdout.strip().split() if line.startswith('window:')), None)
            if not window_id_line: raise ValueError("Could not find window ID in xdotool output."); window_id = window_id_line.split(':')[1]
            info_proc = subprocess.run(['xwininfo', '-id', window_id], capture_output=True, text=True, check=True, timeout=2); info = {}
            for line in info_proc.stdout.split('\n'):
                 if ':' in line: parts = line.split(':', 1); key = parts[0].strip(); value = parts[1].strip(); info[key] = value
            left = int(info['Absolute upper-left X']); top = int(info['Absolute upper-left Y']); width = int(info['Width']); height = int(info['Height']); title_part = info.get('xwininfo', ''); title = title_part.split('"')[1] if '"' in title_part else f"Window (ID: {window_id})"
            if width <= 0 or height <= 0: self.logger.log("log_linux_window_invalid_rect"); self._on_selection_cancelled(); return
            try: import pyautogui; screen_width, screen_height = pyautogui.size(); rect = (max(0, left), max(0, top), min(screen_width, left + width), min(screen_height, top + height))
            except ImportError: rect = (max(0, left), max(0, top), left + width, top + height)
            if self._is_capturing_for_registration: self._areaSelectedForProcessing.emit(rect); self.selectionProcessFinished.emit(); return
            self._pending_window_info = {"title": title, "dims": {'width': width, 'height': height}, "rect": rect}
            if title and title not in self.config_manager.load_window_scales(): self.askToSaveWindowBaseSizeSignal.emit(title)
            else: self.process_base_size_prompt_response(save_as_base=False)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, KeyError, Exception) as e: self.logger.log("log_linux_window_get_rect_failed", str(e)); self._showUiSignal.emit(); self.selectionProcessFinished.emit()

    def process_base_size_prompt_response(self, save_as_base: bool):
        try:
            if not (info := self._pending_window_info): self.logger.log("Warning: process_base_size_prompt_response called with no pending info."); self._showUiSignal.emit(); self.selectionProcessFinished.emit(); return
            title, current_dims, rect = info['title'], info['dims'], info['rect']
            if save_as_base:
                scales_data = self.config_manager.load_window_scales(); scales_data[title] = current_dims; self.config_manager.save_window_scales(scales_data); self.current_window_scale = 1.0; self.logger.log("log_window_base_size_saved", title); self.windowScaleCalculated.emit(1.0); self._areaSelectedForProcessing.emit(rect)
            elif title and title in (scales_data := self.config_manager.load_window_scales()):
                base_dims = scales_data[title]; calc_scale = current_dims['width'] / base_dims['width'] if base_dims['width'] > 0 else 1.0
                if 0.995 <= calc_scale <= 1.005: self.current_window_scale = 1.0; self.logger.log("log_window_scale_calc", title, f"{calc_scale:.3f}"); self.windowScaleCalculated.emit(1.0); self._areaSelectedForProcessing.emit(rect)
                else: self._pending_scale_prompt_info = {**info, 'calculated_scale': calc_scale}; self.askToApplyWindowScaleSignal.emit(calc_scale); return
            else: self.current_window_scale = None; self.windowScaleCalculated.emit(0.0); self._areaSelectedForProcessing.emit(rect)
        except Exception as e:
            self.logger.log("log_error_base_size_process", str(e))
            if not self._pending_scale_prompt_info: self._pending_window_info = None; self._showUiSignal.emit(); self.selectionProcessFinished.emit()
        finally:
            if not self._pending_scale_prompt_info: self._pending_window_info = None; self.selectionProcessFinished.emit()

    def process_apply_scale_prompt_response(self, apply_scale: bool):
        try:
            if not (info := self._pending_scale_prompt_info): self.logger.log("Warning: process_apply_scale_prompt_response called with no pending info."); self._pending_window_info = None; self._showUiSignal.emit(); self.selectionProcessFinished.emit(); return
            scale, rect = info['calculated_scale'], info['rect']
            if apply_scale:
                self.ui_manager.app_config['auto_scale']['use_window_scale'] = True; self.ui_manager.auto_scale_widgets['use_window_scale'].setChecked(True); self.ui_manager.on_app_settings_changed(); self.current_window_scale = scale; self.logger.log("log_window_scale_applied", f"{scale:.3f}")
            else: self.current_window_scale = None; self.logger.log("log_window_scale_not_applied", f"{scale:.3f}")
            self.windowScaleCalculated.emit(self.current_window_scale if self.current_window_scale is not None else 0.0); self._areaSelectedForProcessing.emit(rect)
        except Exception as e: self.logger.log("log_error_apply_scale_process", str(e))
        finally: self._pending_scale_prompt_info = None; self._pending_window_info = None; self.selectionProcessFinished.emit()

    def handle_area_selection(self, coords):
        if self._is_capturing_for_registration: self._is_capturing_for_registration = False; QTimer.singleShot(100, lambda: self._save_captured_image(coords))
        else: self.recognition_area = coords; self.logger.log("log_rec_area_set", str(coords)); self._update_rec_area_preview(); self.selectionProcessFinished.emit(); self._show_ui_safe()
        if hasattr(self, 'selection_overlay'): self.selection_overlay = None
        self.logger.log("[DEBUG] Scheduling listener restart after selection completion (150ms delay)..."); QTimer.singleShot(150, self._start_global_mouse_listener)

    def _get_filename_from_user(self):
        lm = self.locale_manager.tr; return QInputDialog.getText(self.ui_manager, lm("dialog_filename_prompt_title"), lm("dialog_filename_prompt_text"))

    def _save_captured_image(self, region_coords):
        # ★★★ ここが修正対象の try ブロックです ★★★
        try: 
            self.ui_manager.hide();
            if self.performance_monitor: self.performance_monitor.hide() # ★ 修正: hide() のみ呼ぶ
            QTimer.singleShot(100, lambda: self._capture_and_prompt_for_save(region_coords)) # ★ 修正: 呼び出しをタイマーに移す
        except Exception as e: QMessageBox.critical(self.ui_manager, self.locale_manager.tr("error_title_capture_prepare_failed"), self.locale_manager.tr("error_message_capture_prepare_failed", str(e))); self._show_ui_safe(); self.selectionProcessFinished.emit()

    def _capture_and_prompt_for_save(self, region_coords):
        try:
            captured_image = self.capture_manager.capture_frame(region=region_coords)
            if captured_image is None or captured_image.size == 0: self._show_ui_safe(); QMessageBox.warning(self.ui_manager, self.locale_manager.tr("warn_title_capture_failed"), self.locale_manager.tr("warn_message_capture_failed")); self.selectionProcessFinished.emit(); return
            self._show_ui_safe();
            if hasattr(self.ui_manager, 'switch_to_preview_tab'): self.ui_manager.switch_to_preview_tab(); self.ui_manager.update_image_preview(captured_image, settings_data=None); QApplication.processEvents(); QTimer.singleShot(50, lambda: self._prompt_and_save_image(captured_image))
        except Exception as e: QMessageBox.critical(self.ui_manager, self.locale_manager.tr("error_title_capture_save_failed"), self.locale_manager.tr("error_message_capture_save_failed", str(e))); self._show_ui_safe(); self.selectionProcessFinished.emit()

    def _prompt_and_save_image(self, captured_image):
        try:
            file_name, ok = self._get_filename_from_user()
            if ok and file_name:
                self.ui_manager.set_tree_enabled(False); save_path = self.config_manager.base_dir / f"{file_name}.png"
                if save_path.exists():
                    lm = self.locale_manager.tr; reply = QMessageBox.question(self.ui_manager, lm("confirm_overwrite_title"), lm("confirm_overwrite_message", save_path.name), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
                    if reply == QMessageBox.StandardButton.No: self.ui_manager.set_tree_enabled(True); self.selectionProcessFinished.emit(); self._show_ui_safe(); return
                if self.thread_pool: self.thread_pool.submit(self._save_image_task, captured_image, save_path).add_done_callback(self._on_save_image_done)
                else: self._on_save_image_done(None, success=False, message=self.locale_manager.tr("Error: Thread pool unavailable for saving.")); self.ui_manager.set_tree_enabled(True); self.selectionProcessFinished.emit(); self._show_ui_safe()
            else: self.selectionProcessFinished.emit(); self._show_ui_safe()
        except Exception as e: QMessageBox.critical(self.ui_manager, self.locale_manager.tr("error_title_capture_save_failed"), self.locale_manager.tr("error_message_capture_save_failed", str(e))); self._show_ui_safe(); self.selectionProcessFinished.emit()

    def _save_image_task(self, image, save_path):
        try:
            is_success, buffer = cv2.imencode('.png', image);
            if not is_success: raise IOError("cv2.imencode failed") # ★
            buffer.tofile(str(save_path)) # ★
            settings = self.config_manager.load_item_setting(Path()); settings['image_path'] = str(save_path); settings['point_click'] = True
            self.config_manager.save_item_setting(save_path, settings); self.config_manager.add_item(save_path)
            return True, self.locale_manager.tr("log_image_saved", str(save_path.name)) # ★
        except Exception as e: return False, self.locale_manager.tr("log_image_save_failed", str(e))

    def _on_save_image_done(self, future, success=None, message=None):
        try:
            if future: success, message = future.result()
            if success:
                self._log(message)
                if self.thread_pool: self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
                else: self.ui_manager.set_tree_enabled(True)
            else: QMessageBox.critical(self.ui_manager, self.locale_manager.tr("error_title_image_save_failed"), message); self.ui_manager.set_tree_enabled(True)
        except Exception as e: QMessageBox.critical(self.ui_manager, self.locale_manager.tr("error_title_image_save_failed"), f"Error processing save result: {e}"); self.ui_manager.set_tree_enabled(True)
        finally:
            if not (future and success and self.thread_pool): self.selectionProcessFinished.emit(); self._show_ui_safe()

    def clear_recognition_area(self):
        self.recognition_area = None; self.current_window_scale = None; self.target_hwnd = None; self.windowScaleCalculated.emit(0.0)
        if 'dxcam' in sys.modules and hasattr(self.capture_manager, 'dxcam_sct') and self.capture_manager.dxcam_sct:
            try: self.capture_manager.dxcam_sct.target_hwnd = None
            except Exception as dxcam_err: self.logger.log(f"Error resetting DXCam target HWND: {dxcam_err}")
        self.logger.log("log_rec_area_cleared"); self.updateRecAreaPreview.emit(None)

    def _update_rec_area_preview(self):
        img = None
        if self.recognition_area:
             try: img = self.capture_manager.capture_frame(region=self.recognition_area)
             except Exception as e: self.logger.log(f"Error capturing for rec area preview: {e}")
        self.updateRecAreaPreview.emit(img)

    def get_backup_click_countdown(self) -> float:
        # ★ state_lock を使用 ★
        with self.state_lock:
            if isinstance(self.state, CountdownState): 
                return self.state.get_remaining_time()
        return -1.0
