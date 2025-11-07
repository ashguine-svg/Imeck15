# core.py (Linuxキャプチャロジック 最終修正版)
# ★★★ D&D安定化とUI非同期化（仕様書）対応版 ★★★

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
    
    statsUpdated = Signal(int, str, dict, float, float)
    
    # --- ▼▼▼ 修正箇所 1/11 (仕様書 [40] シグナル追加) ▼▼▼ ---
    # 目的: UIスレッドにツリーの更新を安全に要求する
    treeUpdateRequested = Signal()
    # --- ▲▲▲ 修正完了 ▲▲▲ ---

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
        
        self.start_time = time.time()
        self.last_stats_emit_time = 0
        self.current_fps = 0.0

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
        
        max_thread_limit = 4 # 1. 最大値を「4」として変数に定義
        
        worker_threads = min(max(1, cpu_cores // 4), max_thread_limit)
        self.worker_threads = worker_threads
        self.logger.log("log_info_cores", cpu_cores, self.worker_threads, max_thread_limit)
        self.thread_pool = ThreadPoolExecutor(max_workers=self.worker_threads)
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
        
        # ★★★ 修正: キャプチャ前画像（プリキャプチャ）用の変数を追加 ★★★
        self.pre_captured_image_for_registration = None

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
                time.sleep(0.1) 
                
                if not self.mouse_listener.is_alive(): 
                    self.logger.log("Global mouse listener stopped successfully.")
                else: 
                    self.logger.log("[WARN] Listener stop() called but is_alive() is still true. Forcing cleanup.")
                    
            except Exception as e: 
                self.logger.log("log_warn_listener_stop", str(e))
                
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
        if not self.recognition_area:
            lm = self.locale_manager.tr
            QMessageBox.warning(
                self.ui_manager, 
                lm("warn_capture_no_rec_area_title"), 
                lm("warn_capture_no_rec_area_text")
            )
            self.ui_manager._update_capture_button_state()
            return

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
            
            # --- ▼▼▼ 修正箇所 2/11 (仕様書 [40]) ▼▼▼ ---
            if deleted_count > 0:
                self.treeUpdateRequested.emit()
            # --- ▲▲▲ 修正完了 ▲▲▲ ---

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
                self.logger.log(message_key_or_text)
                
                # --- ▼▼▼ 修正箇所 3/11 (仕様書 [40]) ▼▼▼ ---
                # self.ui_manager.update_image_tree() # 削除
                self.treeUpdateRequested.emit() # 変更
                # --- ▲▲▲ 修正完了 ▲▲▲ ---
                
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
        self.ui_manager.set_tree_enabled(False)
        if self.thread_pool:
            # --- ▼▼▼ 修正箇所 4/11 (仕様書 [39] 非同期) ▼▼▼ ---
            # _on_cache_build_done ではなく、新しいI/O専用コールバック _on_move_items_done を使う
            self.thread_pool.submit(self._move_items_async, source_paths, dest_folder_path_str).add_done_callback(self._on_move_items_done)
            # --- ▲▲▲ 修正完了 ▲▲▲ ---
        else:
            self.logger.log("[WARN] Thread pool not available. Moving items and rebuilding cache synchronously.")
            try:
                # --- ▼▼▼ 修正箇所 4/11 (仕様書 [39] 非同期) ▼▼▼ ---
                self._move_items_async(source_paths, dest_folder_path_str) # _move_items_and_rebuild_async から変更
                self._on_move_items_done(None) # _on_cache_build_done から変更
                # --- ▲▲▲ 修正完了 ▲▲▲ ---
            finally:
                pass # コールバックがUIを処理する
    
    def _move_items_async(self, source_paths: list, dest_folder_path_str: str):
        """ (ワーカースレッド) 選択されたアイテムを移動します。キャッシュ構築は行いません。"""
        moved_count = 0; failed_count = 0; final_message = ""
        try:
            for source_path_str in source_paths:
                success, message_or_key = self.config_manager.move_item(source_path_str, dest_folder_path_str)
                if success: 
                    self.logger.log(message_or_key); moved_count += 1
                else: 
                    self.logger.log("log_move_item_failed", self.locale_manager.tr(message_or_key)); failed_count += 1; final_message = self.locale_manager.tr(message_or_key)
            
            return (moved_count, failed_count, final_message)

        except Exception as e:
            self.logger.log("[ERROR] _move_items_async: %s", str(e))
            return (moved_count, failed_count, str(e))

        # --- ▼▼▼ 修正箇所 5/11 (仕様書 [39] 非同期) ▼▼▼ ---
        # self._build_template_cache() # 削除 (I/Oと重い処理を分離)
        # --- ▲▲▲ 修正完了 ▲▲▲ ---

    # --- ▼▼▼ 修正箇所 6/11 (仕様書 [39] 非同期) ▼▼▼ ---
    def _on_move_items_done(self, future):
        """
        (UIスレッド) アイテムの移動(I/O)完了後に呼び出されます。
        D&Dによる視覚的なツリーの変更は既に完了しているため、
        ここではUIの再構築は行わず、ロック解除とキャッシュ構築のみを行います。
        """
        try:
            if future:
                moved_count, failed_count, final_message = future.result()
                if failed_count > 0: 
                    self.logger.log("[ERROR] _move_items_async failed count: %s, LastError: %s", failed_count, final_message)
            
            # --- ▼▼▼ 修正箇所 ▼▼▼ ---
            # (仕様書 [39][40])
            # 1. UIツリーの更新をリクエスト
            
            # self.treeUpdateRequested.emit() # ★★★ この行をコメントアウト (削除) ★★★
            # 理由: D&D操作(image_tree_widget.py)によってUIツリーは
            # 既に視覚的に正しい状態になっているため、ここでファイルから
            # 再読み込み(update_image_tree)すると、OSのキャッシュが
            # 原因で移動したアイテムが消える競合が発生する。
            
            # --- ▲▲▲ 修正完了 ▲▲▲ ---
            
            # 2. UIを即時ロック解除
            self.ui_manager.set_tree_enabled(True) 
            # 3. 重いキャッシュ構築をバックグラウンドで実行
            if self.thread_pool:
                # このコールバックはUIをロックせず、完了シグナルも発行しない
                self.thread_pool.submit(self._build_template_cache) 
            
        except Exception as e:
            self.logger.log("[ERROR] _on_move_items_done: %s", str(e))
            # エラーが発生した場合も、UIのロックは解除する
            self.ui_manager.set_tree_enabled(True)

    def move_item_out_of_folder(self):
        source_path_str, name = self.ui_manager.get_selected_item_path(); lm = self.locale_manager.tr
        if not source_path_str: QMessageBox.warning(self.ui_manager, lm("warn_move_out_no_selection"), lm("warn_move_out_no_selection_text")); return
        source_path = Path(source_path_str)
        if not source_path.is_file() or source_path.parent == self.config_manager.base_dir: QMessageBox.warning(self.ui_manager, lm("warn_move_out_not_in_folder"), lm("warn_move_out_not_in_folder_text")); return
        dest_folder_path_str = str(self.config_manager.base_dir)
        success, message_or_key = self.config_manager.move_item(source_path_str, dest_folder_path_str)
        if success:
            self.logger.log(message_or_key)

            # --- ▼▼▼ 修正箇所 7/11 (仕様書 [40]) ▼▼▼ ---
            # self.ui_manager.update_image_tree() # 削除
            self.treeUpdateRequested.emit() # 変更
            # --- ▲▲▲ 修正完了 ▲▲▲ ---

            if self.thread_pool: self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
            else: self.ui_manager.set_tree_enabled(True)
        else: QMessageBox.critical(self.ui_manager, lm("error_title_move_out_failed"), self.locale_manager.tr(message_or_key))
    
    def rename_item(self, old_path_str: str, new_name: str):
        if not old_path_str or not new_name:
            self.logger.log("warn_rename_no_selection")
            return

        try:
            self.ui_manager.set_tree_enabled(False)
            
            success, message_or_key = self.config_manager.rename_item(old_path_str, new_name)
            
            if success:
                self.logger.log(message_or_key)

                # --- ▼▼▼ 修正箇所 8/11 (仕様書 [40]) ▼▼▼ ---
                self.treeUpdateRequested.emit()
                # --- ▲▲▲ 修正完了 ▲▲▲ ---
                
                if self.thread_pool:
                    self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
                else:
                    self.logger.log("[ERROR] Thread pool not available for rename rebuild.")
                    self._build_template_cache()
                    self._on_cache_build_done(None)
            else:
                self.logger.log("log_rename_error_general", f"Rename failed for {Path(old_path_str).name}: {self.locale_manager.tr(message_or_key)}")
                QMessageBox.warning(self.ui_manager, 
                                    self.locale_manager.tr("rename_dialog_title"), 
                                    self.locale_manager.tr(message_or_key))
                self.ui_manager.set_tree_enabled(True)

        except Exception as e:
            self.logger.log("log_rename_error_general", f"Rename exception for {Path(old_path_str).name}: {str(e)}")
            QMessageBox.critical(self.ui_manager, self.locale_manager.tr("rename_dialog_title"), str(e))
            self.ui_manager.set_tree_enabled(True)
     
    def load_image_and_settings(self, file_path: str):
        if not file_path or not Path(file_path).is_file():
            self.current_image_path = None
            self.current_image_settings = None
            self.current_image_mat = None
            self.updatePreview.emit(None, None)
            return

        try:
            self.current_image_path = file_path
            self.current_image_settings = self.config_manager.load_item_setting(Path(file_path))
            
            with open(file_path, 'rb') as f:
                file_bytes = np.fromfile(f, np.uint8)
            self.current_image_mat = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)

            if self.current_image_mat is not None and self.current_image_settings:
                h, w = self.current_image_mat.shape[:2]
                self.current_image_settings['roi_rect'] = self.calculate_roi_rect((w, h), self.current_image_settings)

            self.updatePreview.emit(self.current_image_mat, self.current_image_settings)
        
        except Exception as e:
            self.logger.log("log_image_load_failed", Path(file_path).name, str(e))
            self.current_image_path = None
            self.current_image_settings = None
            self.current_image_mat = None
            self.updatePreview.emit(None, None)
    
    def _handle_setting_change_and_rebuild(self, request_save=False):
        if self.is_monitoring:
            self._recalculate_and_update()
            self.save_current_settings()
            if self.thread_pool:
                self.logger.log("log_item_setting_changed_rebuild")
                self.ui_manager.set_tree_enabled(False)
                self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
        else:
            self._recalculate_and_update()

    def on_image_settings_changed(self, settings: dict):
        image_path_from_ui = settings.get('image_path')

        if self.current_image_settings and image_path_from_ui == self.current_image_path:
            self.current_image_settings.update(settings)
            self._handle_setting_change_and_rebuild()
            self.ui_manager.save_timer.start()

    def _recalculate_and_update(self):
        if self.current_image_mat is not None and self.current_image_settings:
            h, w = self.current_image_mat.shape[:2]
            self.current_image_settings['roi_rect'] = self.calculate_roi_rect((w, h), self.current_image_settings)

        self.updatePreview.emit(self.current_image_mat, self.current_image_settings)

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
        # --- ▼▼▼ 修正箇所 9/11 (仕様書 [39] UIロック解除) ▼▼▼ ---
        # UI操作(D&D, Up/Down)はデバウンスされるため、ここに来た時点で
        # UIをロックする必要がある
        self.ui_manager.set_tree_enabled(False)
        # --- ▲▲▲ 修正完了 ▲▲▲ ---
        
        try:
            if hasattr(self.ui_manager, 'save_tree_order'):
                data_to_save = self.ui_manager.save_tree_order() 
            else:
                self.logger.log("[ERROR] ui_manager.save_tree_order not found.")
                data_to_save = {}
        except Exception as e:
            self.logger.log("log_error_get_order_data", str(e))
            self.ui_manager.set_tree_enabled(True) # エラー時はロック解除
            return

        if self.thread_pool:
            # --- ▼▼▼ 修正箇所 9/11 (仕様書 [39] 非同期) ▼▼▼ ---
            # 1. ワーカースレッドでI/Oタスクを実行
            # 2. 完了コールバックを _on_order_save_done に変更
            self.thread_pool.submit(self._save_order_async, data_to_save).add_done_callback(self._on_order_save_done)
            # --- ▲▲▲ 修正完了 ▲▲▲ ---
        else:
            self.logger.log("[WARN] Thread pool not available. Saving order and rebuilding cache synchronously.")
            try:
                # --- ▼▼▼ 修正箇所 9/11 (仕様書 [39] 非同期) ▼▼▼ ---
                self._save_order_async(data_to_save) # _save_order_and_rebuild_async から変更
                self._on_order_save_done(None) # コールバックを手動実行
                # --- ▲▲▲ 修正完了 ▲▲▲ ---
            finally:
                pass # _on_order_save_done がUIをアンロックする
                
    def _save_order_async(self, data_to_save: dict):
        """
        (ワーカースレッド) UIスレッドから渡された順序データでJSONファイルのみを上書きします。
        キャッシュ構築は行いません。
        """
        try:
            if hasattr(self.config_manager, 'save_tree_order_data'):
                self.config_manager.save_tree_order_data(data_to_save)
                self.logger.log("log_order_saved")
            else:
                self.logger.log("log_warn_save_order_data_not_found")
                
        except Exception as e: 
            self.logger.log("log_error_save_order", str(e))
        
        # --- ▼▼▼ 修正箇所 10/11 (仕様書 [39] 非同期) ▼▼▼ ---
        # self._build_template_cache() # 削除 (I/Oと重い処理を分離)
        # --- ▲▲▲ 修正完了 ▲▲▲ ---
    
    # --- ▼▼▼ 修正箇所 11/11 (仕様書 [39] 非同期) ▼▼▼ ---
    def _on_order_save_done(self, future):
        """
        (UIスレッド) 順序の保存(I/O)完了後に呼び出されます。
        UIを即座にロック解除し、キャッシュ構築をバックグラウンドで開始します。
        """
        try:
            if future: future.result() # I/Oタスク中の例外をキャッチ
        except Exception as e: 
            self.logger.log("log_error_on_order_save_done", str(e))
        finally:
            # (仕様書 [39] 非同期)
            # 1. UIのロックを即座に解除
            self.ui_manager.set_tree_enabled(True)
            # 2. 重いキャッシュ構築をバックグラウンドタスクとして投入
            # (このタスクはUIをロックせず、完了シグナルも不要)
            if self.thread_pool:
                self.thread_pool.submit(self._build_template_cache)
    # --- ▲▲▲ 修正完了 ▲▲▲ ---
        
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
            self.priority_timers.clear()

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
                if delta_time >= 1.0: 
                    fps = frame_counter / delta_time; 
                    self.fpsUpdated.emit(fps); 
                    self.current_fps = fps
                    fps_last_time = current_time; 
                    frame_counter = 0
                
                if isinstance(current_state, IdleState): self._check_and_activate_timer_priority_mode()
                is_eco_enabled = self.app_config.get('eco_mode',{}).get('enabled',True)
                is_eco_eligible = (is_eco_enabled and self.last_successful_click_time > 0 and isinstance(current_state, IdleState) and (current_time - self.last_successful_click_time > self.ECO_MODE_DELAY))
                self.is_eco_cooldown_active = is_eco_eligible
                skip_handle = False

                if isinstance(current_state, CountdownState): time.sleep(1.0) # カウントダウン中は常に1秒待機
                elif self.is_eco_cooldown_active:
                    self._log("log_eco_mode_standby")
                    time_since_last_check = current_time - self._last_eco_check_time
                    sleep_time = 0 
                    if time_since_last_check < self.ECO_CHECK_INTERVAL:
                        sleep_time = self.ECO_CHECK_INTERVAL - time_since_last_check
                        time.sleep(sleep_time)
                        continue 
                    else: 
                        self._last_eco_check_time = current_time
                        skip_handle = True 
                elif (frame_counter % self.effective_frame_skip_rate) != 0: # 通常のフレームスキップ
                    time.sleep(0.01)
                    continue

                screen_bgr = self.capture_manager.capture_frame(region=self.recognition_area)
                if screen_bgr is None: self._log("log_capture_failed"); time.sleep(1.0); continue
                if self.effective_capture_scale != 1.0: screen_bgr = cv2.resize(screen_bgr, None, fx=self.effective_capture_scale, fy=self.effective_capture_scale, interpolation=cv2.INTER_AREA)

                self.latest_frame_for_hash = screen_bgr.copy() 
                screen_gray = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)

                screen_bgr_umat, screen_gray_umat = None, None
                if OPENCL_AVAILABLE and cv2.ocl.useOpenCL():
                    try: screen_bgr_umat = cv2.UMat(screen_bgr); screen_gray_umat = cv2.UMat(screen_gray)
                    except Exception as e: self.logger.log("log_umat_convert_failed", str(e))

                screen_data = (screen_bgr, screen_gray, screen_bgr_umat, screen_gray_umat)

                all_matches = self._find_matches_for_eco_check(screen_data, current_state)
                if self.is_eco_cooldown_active and all_matches:
                    self.last_successful_click_time = time.time() 
                    self._log("log_eco_mode_resumed", force=True)

                if not skip_handle: 
                    current_state.handle(current_time, screen_data, last_match_time_map, pre_matches=all_matches)

            except Exception as e:
                if isinstance(e, AttributeError) and "'NoneType' object has no attribute 'handle'" in str(e):
                    self.logger.log("[CRITICAL] Race condition detected (state became None unexpectedly). Loop will restart/exit.")
                else:
                    self.logger.log("log_error_monitoring_loop", str(e))
                time.sleep(1.0)
            finally:
                current_time = time.time()
                if current_time - self.last_stats_emit_time >= 1.0:
                    self.last_stats_emit_time = current_time
                    
                    uptime_seconds = int(current_time - self.start_time)
                    h = uptime_seconds // 3600
                    m = (uptime_seconds % 3600) // 60
                    s = uptime_seconds % 60
                    uptime_str = f"{h:02d}h{m:02d}m{s:02d}s"
                    
                    timer_data = {
                        'backup': self.get_backup_click_countdown(),
                        'priority': -1.0
                    }
                    if self.priority_timers:
                        active_timer_path = next(iter(self.priority_timers), None)
                        if active_timer_path:
                            remaining_sec = self.priority_timers[active_timer_path] - current_time
                            timer_data['priority'] = max(0, remaining_sec / 60.0) # 分単位
                    
                    cpu_percent = 0.0
                    
                    # --- ▼▼▼ エラー箇所 (インデント修正済み) ▼▼▼ ---
                    # 577行目の 'if'
                    if self.performance_monitor:
                        # 579行目の 'try:' ( 'if' の内側にインデント)
                        try:
                            cpu_percent = self.performance_monitor.get_last_cpu()
                        except Exception:
                            cpu_percent = 0.0 # 取得失敗
                    # --- ▲▲▲ 修正完了 ▲▲▲ ---
                    
                    fps_value = self.current_fps
                    
                    self.statsUpdated.emit(self._click_count, uptime_str, timer_data, cpu_percent, fps_value)
                
                time.sleep(0.01)

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
        if not all_matches:
            current_match_paths = set()
            keys_to_remove = [path for path in self.match_detected_at if path not in current_match_paths]
            for path in keys_to_remove:
                del self.match_detected_at[path]
            return False

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
                if path in self.match_detected_at:
                    del self.match_detected_at[path]
                continue

            if path not in self.match_detected_at:
                self.match_detected_at[path] = current_time
                self.logger.log(f"[DEBUG] Detected '{Path(path).name}'. Interval timer started ({interval:.1f}s).")
                continue
            else:
                detected_at = self.match_detected_at[path]
                time_since_detected = current_time - detected_at

                if time_since_detected >= interval:
                    clickable_after_interval.append(m)
                    self.logger.log(f"[DEBUG] Interval elapsed for '{Path(path).name}' ({time_since_detected:.2f}s >= {interval:.1f}s). Added to clickable.")
                else:
                    remaining = interval - time_since_detected
                    self.logger.log(f"[DEBUG] Waiting for interval on '{Path(path).name}'. Remaining: {remaining:.2f}s.")

        keys_to_remove = [p for p in self.match_detected_at if p not in current_match_paths]

        if keys_to_remove:
            paths_removed = []
            for p in keys_to_remove:
                if p in self.match_detected_at:
                    del self.match_detected_at[p]
                    paths_removed.append(Path(p).name)
            if paths_removed:
                self.logger.log(f"[DEBUG] Cleared detection times for disappearing images: {', '.join(paths_removed)}")

        if not clickable_after_interval:
            return False

        try:
            potential_target_match = min(clickable_after_interval, key=lambda m: (m['settings'].get('interval_time', 1.5), -m['confidence']))
        except ValueError:
            self.logger.log("[DEBUG] Error finding minimum in clickable_after_interval list.")
            return False

        try:
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
                self.last_successful_click_time = current_time
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
        results = []
        futures = []

        with self.cache_lock:
            if not cache:
                return []

            use_cl = OPENCL_AVAILABLE and cv2.ocl.useOpenCL()
            use_gs = self.app_config.get('grayscale_matching', False)

            screen_image = s_gray if use_gs else s_bgr
            if use_cl:
                screen_umat = s_gray_umat if use_gs else s_bgr_umat
                screen_image = screen_umat if screen_umat is not None else screen_image

            s_shape = screen_image.get().shape[:2] if use_cl and isinstance(screen_image, cv2.UMat) else screen_image.shape[:2]

            for path, data in cache.items():
                is_searching_scale = data['best_scale'] is None
                templates_to_check = data['scaled_templates']
                if not is_searching_scale:
                    filtered_templates = [t for t in templates_to_check if t['scale'] == data['best_scale']]
                    templates_to_check = filtered_templates if filtered_templates else data['scaled_templates']


                for t in templates_to_check:
                    try:
                        template_image = t['gray'] if use_gs else t['image']
                        if use_cl:
                            t_umat = t.get('gray_umat' if use_gs else 'image_umat')
                            template_image = t_umat if t_umat else template_image

                        task_data = {'path': path, 'settings': data['settings'], 'template': template_image, 'scale': t['scale']}
                        t_shape = t['shape']

                        # --- ▼▼▼ 修正箇所 (OpenCLでもスレッドプールを利用) ▼▼▼ ---
                        if self.thread_pool:
                            # OpenCLが有効でも、タスクの投入自体はスレッドプールで行う
                            future = self.thread_pool.submit(_match_template_task, screen_image, task_data, s_shape, t_shape)
                            futures.append(future)
                        else:
                            # フォールバック (スレッドプールが利用できない場合)
                            match_result = _match_template_task(screen_image, task_data, s_shape, t_shape)
                            if match_result:
                                results.append(match_result)
                        # --- ▲▲▲ 修正完了 ▲▲▲ ---
                    except Exception as e:
                         self.logger.log("Error during template processing for %s (scale %s): %s", Path(path).name, t.get('scale', 'N/A'), str(e))

        # --- ▼▼▼ 修正箇所 (futuresの処理をループの外に移動) ▼▼▼ ---
        # with self.cache_lock: の外側（インデントを戻す）で結果を収集します
        if futures:
            for f in futures:
                try:
                    match_result = f.result();
                    if match_result: results.append(match_result)
                except Exception as e:
                     self.logger.log("Error getting result from match thread: %s", str(e))
        # --- ▲▲▲ 修正完了 ▲▲▲ ---

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
        
        # ★★★ 修正: プリキャプチャ変数をリセット ★★★
        self.pre_captured_image_for_registration = None
        
        if method == "rectangle":
            if not self._is_capturing_for_registration: 
                self.target_hwnd = None; self.current_window_scale = None; self.windowScaleCalculated.emit(0.0); self.logger.log("log_rec_area_set_rect")
            else: 
                self.logger.log("log_capture_area_set_rect")
                # ★★★ 修正: 矩形キャプチャの場合もプリキャプチャを実行 ★★★
                try:
                    self.pre_captured_image_for_registration = self.capture_manager.capture_frame()
                    if self.pre_captured_image_for_registration is None:
                         raise Exception("Failed to capture full screen for pre-capture.")
                    self.logger.log("log_pre_capture_success")
                except Exception as e:
                    self.logger.log("log_pre_capture_failed", str(e))
                    self._on_selection_cancelled()
                    return
            
            self.selection_overlay = SelectionOverlay(); self.selection_overlay.selectionComplete.connect(self._areaSelectedForProcessing.emit); self.selection_overlay.selectionCancelled.connect(self._on_selection_cancelled); self.selection_overlay.showFullScreen()
        
        elif method == "window":
            
            # ★★★ 修正: プリキャプチャを先に実行 ★★★
            if self._is_capturing_for_registration:
                try:
                    self.pre_captured_image_for_registration = self.capture_manager.capture_frame()
                    if self.pre_captured_image_for_registration is None:
                         raise Exception("Failed to capture full screen for pre-capture.")
                    self.logger.log("log_pre_capture_success")
                except Exception as e:
                    self.logger.log("log_pre_capture_failed", str(e))
                    self._on_selection_cancelled()
                    return

            # 1. Windows の場合 (従来通り)
            if sys.platform == 'win32' and win32gui:
                if self._is_capturing_for_registration and self.recognition_area:
                    self.logger.log("log_capture_from_existing_rec_area")
                    (x1, y1, x2, y2) = self.recognition_area
                    center_x = (x1 + x2) // 2
                    center_y = (y1 + y2) // 2
                    self._handle_window_click_for_selection_windows(center_x, center_y)
                    return # ★ Windowsの場合はリスナーを起動しない
            
            # 2. Linux (X11/Wayland) または Windowsの手動フォールバック
            # 常に手動リスナーを起動
            if not self._is_capturing_for_registration:
                self.logger.log("log_rec_area_set_window")
            else:
                self.logger.log("log_capture_area_set_window")
                
            self.window_selection_listener = WindowSelectionListener(self._handle_window_click_for_selection)
            self.window_selection_listener.start()
            self.keyboard_selection_listener = keyboard.Listener(on_press=self._on_key_press_for_selection)
            self.keyboard_selection_listener.start()
            # ★★★ 修正ここまで ★★★

    def _on_selection_cancelled(self):
        self.logger.log("log_selection_cancelled"); self._is_capturing_for_registration = False
        
        # ★★★ 修正: プリキャプチャをクリア ★★★
        self.pre_captured_image_for_registration = None 
        
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
            
            if self._is_capturing_for_registration: 
                self._areaSelectedForProcessing.emit(rect)
                return
            
            title = win32gui.GetWindowText(hwnd); self._pending_window_info = {"title": title, "dims": {'width': rect[2] - rect[0], 'height': rect[3] - rect[1]}, "rect": rect}
            if title and title not in self.config_manager.load_window_scales(): self.askToSaveWindowBaseSizeSignal.emit(title)
            else: self.process_base_size_prompt_response(save_as_base=False)
        except Exception as e:
            self.logger.log("log_window_get_rect_failed", str(e))
            if not self._is_capturing_for_registration: self.target_hwnd = None
            self._showUiSignal.emit(); self.selectionProcessFinished.emit()

    def _handle_window_click_for_selection_linux(self, x, y):
        # ★★★ ここからが修正箇所 ★★★
        
        # 1. Wayland検出 (手動クリックなのでWaylandでもxdotoolが動くか試行する)
        if os.environ.get('WAYLAND_DISPLAY'):
            self.logger.log("log_linux_wayland_manual_attempt")
            # Waylandの場合、xdotoolが動作しない可能性が高いが、
            # XWayland互換レイヤーで動いている場合は getmouselocation が機能する可能性がある
            
        # 2. X11 ツールチェック
        missing_tools = [tool for tool in ['xdotool', 'xwininfo'] if not shutil.which(tool)]
        if missing_tools: 
            self.logger.log("log_linux_tool_not_found", ', '.join(missing_tools))
            self._on_selection_cancelled()
            return
        
        try:
            # 3. 'getmouselocation' を使用 (手動クリックが前提)
            # check=False にして、終了コード 1 (デスクトップクリックなど) を処理する
            id_proc = subprocess.run(
                ['xdotool', 'getmouselocation'], 
                capture_output=True, text=True, timeout=2, check=False # ★ check=False
            )

            if id_proc.returncode != 0 or not id_proc.stdout:
                stderr_output = id_proc.stderr.strip() if id_proc.stderr else "No output"
                raise ValueError(f"xdotool getmouselocation failed. Exit code: {id_proc.returncode}, Stderr: {stderr_output}")

            # 4. 'getmouselocation' の出力をパースする
            window_id_line = next((line for line in id_proc.stdout.strip().split() if line.startswith('window:')), None)
            
            if not window_id_line:
                raise ValueError(f"Could not find 'window:' in xdotool output: {id_proc.stdout}")
                
            window_id = window_id_line.split(':')[1]
            
            if not window_id.isdigit():
                 raise ValueError(f"Invalid window ID received: '{window_id}'")
            
            # 5. xwininfo 実行 (check=False に変更)
            info_proc = subprocess.run(
                ['xwininfo', '-id', window_id], 
                capture_output=True, text=True, timeout=2, check=False # ★ check=False
            )

            if info_proc.returncode != 0 or not info_proc.stdout:
                stderr_output = info_proc.stderr.strip() if info_proc.stderr else "No output"
                raise ValueError(f"xwininfo failed for ID {window_id}. Exit code: {info_proc.returncode}, Stderr: {stderr_output}")

            info = {}
            for line in info_proc.stdout.split('\n'):
                 if ':' in line: parts = line.split(':', 1); key = parts[0].strip(); value = parts[1].strip(); info[key] = value
            
            # 6. 座標パース (KeyError発生源)
            left = int(info['Absolute upper-left X']); top = int(info['Absolute upper-left Y']); width = int(info['Width']); height = int(info['Height']); title_part = info.get('xwininfo', ''); title = title_part.split('"')[1] if '"' in title_part else f"Window (ID: {window_id})"
            
            if width <= 0 or height <= 0: 
                self.logger.log("log_linux_window_invalid_rect")
                self._on_selection_cancelled()
                return
                
            try: import pyautogui; screen_width, screen_height = pyautogui.size(); rect = (max(0, left), max(0, top), min(screen_width, left + width), min(screen_height, top + height))
            except ImportError: rect = (max(0, left), max(0, top), left + width, top + height)
            
            if self._is_capturing_for_registration: 
                self._areaSelectedForProcessing.emit(rect)
                return

            self._pending_window_info = {"title": title, "dims": {'width': width, 'height': height}, "rect": rect}
            if title and title not in self.config_manager.load_window_scales(): self.askToSaveWindowBaseSizeSignal.emit(title)
            else: self.process_base_size_prompt_response(save_as_base=False)
            
        except (subprocess.TimeoutExpired, ValueError, KeyError, Exception) as e: 
            self.logger.log("log_linux_window_get_rect_failed", str(e))
            self._on_selection_cancelled()
        # ★★★ 修正ここまで ★★★

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
        else: 
            self.recognition_area = coords; 
            self.logger.log("log_rec_area_set", str(coords)); 
            self._update_rec_area_preview(); 
            self.selectionProcessFinished.emit(); 
            self._show_ui_safe()
            self.ui_manager._update_capture_button_state()
            
        if hasattr(self, 'selection_overlay'): self.selection_overlay = None
        self.logger.log("[DEBUG] Scheduling listener restart after selection completion (150ms delay)..."); QTimer.singleShot(150, self._start_global_mouse_listener)

    def _get_filename_from_user(self):
        lm = self.locale_manager.tr; return QInputDialog.getText(self.ui_manager, lm("dialog_filename_prompt_title"), lm("dialog_filename_prompt_text"))

    def _save_captured_image(self, region_coords):
        try: 
            self.ui_manager.hide();
            if self.performance_monitor: self.performance_monitor.hide()
            QTimer.singleShot(100, lambda: self._capture_and_prompt_for_save(region_coords))
        except Exception as e: 
            QMessageBox.critical(self.ui_manager, self.locale_manager.tr("error_title_capture_prepare_failed"), self.locale_manager.tr("error_message_capture_prepare_failed", str(e))); 
            self._show_ui_safe(); 
            self.selectionProcessFinished.emit()
            # ★★★ 修正: エラー時もプリキャプチャをクリア ★★★
            self.pre_captured_image_for_registration = None

    def _capture_and_prompt_for_save(self, region_coords):
        try:
            captured_image = None
            
            # ★★★ 修正: プリキャプチャからの切り抜きロジック ★★★
            if self.pre_captured_image_for_registration is not None:
                self.logger.log("log_cropping_from_pre_capture")
                try:
                    (x1, y1, x2, y2) = region_coords
                    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
                    # プリキャプチャ画像 (全画面) から指定された座標で切り抜く
                    captured_image = self.pre_captured_image_for_registration[y1:y2, x1:x2]
                    self.pre_captured_image_for_registration = None # 使用したのでクリア
                except Exception as crop_e:
                    self.logger.log("log_crop_from_pre_capture_failed", str(crop_e))
                    captured_image = None # 切り抜き失敗
                    self.pre_captured_image_for_registration = None # クリア
            else:
                # 矩形選択 (rectangle) の場合、またはプリキャプチャが失敗した場合
                self.logger.log("log_capturing_new_frame")
                captured_image = self.capture_manager.capture_frame(region=region_coords)
            # ★★★ 修正ここまで ★★★

            if captured_image is None or captured_image.size == 0: 
                self._show_ui_safe(); 
                QMessageBox.warning(self.ui_manager, self.locale_manager.tr("warn_title_capture_failed"), self.locale_manager.tr("warn_message_capture_failed")); 
                self.selectionProcessFinished.emit(); 
                return
                
            self._show_ui_safe();
            if hasattr(self.ui_manager, 'switch_to_preview_tab'): 
                self.ui_manager.switch_to_preview_tab(); 
                self.ui_manager.update_image_preview(captured_image, settings_data=None); 
                QApplication.processEvents(); 
                QTimer.singleShot(50, lambda: self._prompt_and_save_image(captured_image))
                
        except Exception as e: 
            QMessageBox.critical(self.ui_manager, self.locale_manager.tr("error_title_capture_save_failed"), self.locale_manager.tr("error_message_capture_save_failed", str(e))); 
            self._show_ui_safe(); 
            self.selectionProcessFinished.emit()
        finally:
             # ★★★ 修正: どのような場合でもプリキャプチャをクリア ★★★
             self.pre_captured_image_for_registration = None

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
            if not is_success: raise IOError("cv2.imencode failed")
            buffer.tofile(str(save_path))
            settings = self.config_manager.load_item_setting(Path()); settings['image_path'] = str(save_path); settings['point_click'] = True
            self.config_manager.save_item_setting(save_path, settings); self.config_manager.add_item(save_path)
            return True, self.locale_manager.tr("log_image_saved", str(save_path.name))
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
        self.ui_manager._update_capture_button_state()

    def _update_rec_area_preview(self):
        img = None
        if self.recognition_area:
             try: img = self.capture_manager.capture_frame(region=self.recognition_area)
             except Exception as e: self.logger.log(f"Error capturing for rec area preview: {e}")
        self.updateRecAreaPreview.emit(img)

    def get_backup_click_countdown(self) -> float:
        with self.state_lock:
            if isinstance(self.state, CountdownState): 
                return self.state.get_remaining_time()
        return -1.0
