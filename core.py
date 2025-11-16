# core.py (Linuxキャプチャロジック 最終修正版)
# ★★★ (根本修正) 解像度変更検知時に、アプリを再起動する ★★★
# ★★★ (改良 1.1) 連続キャプチャ失敗時に自動停止するロジックを追加 ★★★
# ★★★ (改良 1.2) cacheBuildFinished シグナルに成功フラグ(bool)を追加 ★★★
# ★★★ 修正: キャプチャ時のUI表示ロジックを削除し、シグナル発行に変更 ★★★

import sys
import threading
import time
import cv2
import numpy as np
import os
import psutil # ★★★ この行を追加 ★★★
# --- ▼▼▼ 修正箇所 1/6: np.ndarray をインポート (シグナル用) ▼▼▼ ---
from PySide6.QtCore import QObject, Signal, QThread, QPoint, QRect, Qt, QTimer, Slot
from PySide6.QtGui import QMouseEvent, QPainter, QPen, QColor, QBrush, QPainterPath, QKeyEvent
# --- ▲▲▲ 修正完了 ▲▲▲ ---
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
from environment_tracker import EnvironmentTracker


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
    appContextChanged = Signal(str) 
    updateStatus = Signal(str, str)
    updatePreview = Signal(np.ndarray, object)
    updateLog = Signal(str)
    updateRecAreaPreview = Signal(np.ndarray)
    _showUiSignal = Signal()
    selectionProcessStarted = Signal()
    selectionProcessFinished = Signal()
    _areaSelectedForProcessing = Signal(tuple)
    fpsUpdated = Signal(float)
    cacheBuildFinished = Signal(bool) 
    startMonitoringRequested = Signal()
    stopMonitoringRequested = Signal()
    bestScaleFound = Signal(str, float)
    windowScaleCalculated = Signal(float)
    askToSaveWindowBaseSizeSignal = Signal(str)
    askToApplyWindowScaleSignal = Signal(float)
    clickCountUpdated = Signal(int)
    
    statsUpdated = Signal(int, str, dict, float, float)
    
    restartApplicationRequested = Signal()
    
    # --- ▼▼▼ 修正箇所 2/6: 新しいシグナルを追加 ▼▼▼ ---
    capturedImageReadyForPreview = Signal(np.ndarray)
    captureFailedSignal = Signal()
    # --- ▲▲▲ 修正完了 ▲▲▲ ---

    def __init__(self, ui_manager, capture_manager, config_manager, logger, locale_manager):
        super().__init__()
        self.ui_manager = ui_manager
        self.capture_manager = capture_manager
        self.config_manager = config_manager
        self.logger = logger
        self.locale_manager = locale_manager

        self.action_manager = ActionManager(self.logger)
        self.template_manager = TemplateManager(self.config_manager, self.logger)
        
        # --- ▼▼▼ 修正箇所 (psutil の初期化) ▼▼▼ ---
        self.performance_monitor = None # 関連コード削除
        try:
            # psutil プロセスを初期化
            self.process = psutil.Process()
            # 最初の呼び出しでインターバルなしで初期化
            self.process.cpu_percent(interval=None) 
        except Exception as e:
            self.logger.log(f"log_error_psutil_init: {e}")
            self.process = None
        # --- ▲▲▲ 修正完了 ▲▲▲ ---
        
        self.environment_tracker = EnvironmentTracker(self, self.config_manager, self.logger)
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
        
        max_thread_limit = 4 
        
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
        
        self.pre_captured_image_for_registration = None

        self.on_app_config_changed()

        self._last_log_message = ""
        self._last_log_time = 0
        self._log_spam_filter = {"log_stability_hold_click", "log_eco_mode_standby", "log_stability_check_debug"}

        self.match_detected_at = {}
        
        self.consecutive_capture_failures = 0
        
        self._is_reinitializing_display = False 

    # ... (transition_to から _save_captured_image まで変更なし) ...
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
            
            click_pos = QPoint(x, y)
            
            if self.ui_manager and self.ui_manager.isVisible() and not self.ui_manager.isMinimized():
                if self.ui_manager.geometry().contains(click_pos):
                    return 

            if self.ui_manager and self.ui_manager.is_minimal_mode and self.ui_manager.floating_window and self.ui_manager.floating_window.isVisible():
                if self.ui_manager.floating_window.geometry().contains(click_pos):
                    return 
            
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
        try:
            future.result()
            self.cacheBuildFinished.emit(True)
        except Exception as e:
            self.logger.log("log_cache_build_error", str(e))
            self.cacheBuildFinished.emit(False)

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
        self.ui_manager.set_tree_enabled(False)
        if self.thread_pool:
            self.thread_pool.submit(self._move_items_and_rebuild_async, source_paths, dest_folder_path_str).add_done_callback(self._on_cache_build_done)
        else:
            self.logger.log("[WARN] Thread pool not available. Moving items and rebuilding cache synchronously.")
            try:
                self._move_items_and_rebuild_async(source_paths, dest_folder_path_str)
            finally:
                self._on_cache_build_done(None) 
    
    def _move_items_and_rebuild_async(self, source_paths: list, dest_folder_path_str: str):
        moved_count = 0; failed_count = 0; final_message = ""
        try:
            for source_path_str in source_paths:
                success, message_or_key = self.config_manager.move_item(source_path_str, dest_folder_path_str)
                if success: 
                    self.logger.log(message_or_key); moved_count += 1
                else: 
                    self.logger.log("log_move_item_failed", self.locale_manager.tr(message_or_key)); failed_count += 1; final_message = self.locale_manager.tr(message_or_key)
            
            if failed_count > 0: 
                self.logger.log("[ERROR] _move_items_and_rebuild_async failed count: %s, LastError: %s", failed_count, final_message)

        except Exception as e:
            self.logger.log("[ERROR] _move_items_and_rebuild_async: %s", str(e))
            raise 
        
        self._build_template_cache()

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
    
    def rename_item(self, old_path_str: str, new_name: str):
        if not old_path_str or not new_name:
            self.logger.log("warn_rename_no_selection")
            return

        try:
            self.ui_manager.set_tree_enabled(False)
            
            success, message_or_key = self.config_manager.rename_item(old_path_str, new_name)
            
            if success:
                self.logger.log(message_or_key)
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
        self.ui_manager.set_tree_enabled(False)
        
        try:
            if hasattr(self.ui_manager, 'save_tree_order'):
                data_to_save = self.ui_manager.save_tree_order() 
            else:
                self.logger.log("[ERROR] ui_manager.save_tree_order not found.")
                data_to_save = {}
        except Exception as e:
            self.logger.log("log_error_get_order_data", str(e))
            self.ui_manager.set_tree_enabled(True)
            return

        if self.thread_pool:
            self.thread_pool.submit(self._save_order_and_rebuild_async, data_to_save).add_done_callback(self._on_cache_build_done)
        else:
            self.logger.log("[WARN] Thread pool not available. Saving order and rebuilding cache synchronously.")
            try:
                self._save_order_and_rebuild_async(data_to_save)
            finally:
                self._on_cache_build_done(None) 
                
    def _save_order_and_rebuild_async(self, data_to_save: dict):
        try:
            if hasattr(self.config_manager, 'save_tree_order_data'):
                self.config_manager.save_tree_order_data(data_to_save)
                self.logger.log("log_order_saved")
            else:
                self.logger.log("log_warn_save_order_data_not_found")
                
        except Exception as e: 
            self.logger.log("log_error_save_order", str(e))
            raise 
        
        self._build_template_cache()
        
    def _build_template_cache(self):
        with self.cache_lock:
            current_app_name = self.environment_tracker.recognition_area_app_title
            (self.normal_template_cache, self.backup_template_cache, self.priority_timers, self.folder_children_map) = \
                self.template_manager.build_cache(self.app_config, self.current_window_scale, self.effective_capture_scale, self.is_monitoring, self.priority_timers, current_app_name)
    
    def start_monitoring(self):
        if not self.recognition_area: QMessageBox.warning(self.ui_manager, self.locale_manager.tr("warn_rec_area_not_set_title"), self.locale_manager.tr("warn_rec_area_not_set_text")); return
        
        if self._is_reinitializing_display:
            try:
                self.logger.log("log_lazy_reinitialize_capture_backend")
                self._reinitialize_capture_backend()
            except Exception as e:
                self.logger.log("log_error_reinitialize_capture", str(e))
            finally:
                self._is_reinitializing_display = False

        if not self.is_monitoring:
            self.consecutive_capture_failures = 0 
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
            
            self.logger.log("log_monitoring_stopped"); self.ui_manager.set_tree_enabled(True)
            if self._monitor_thread and self._monitor_thread.is_alive(): self._monitor_thread.join(timeout=1.0)
            with self.cache_lock:
                for cache in [self.normal_template_cache, self.backup_template_cache]:
                    for item in cache.values(): item['best_scale'] = None
            self.match_detected_at.clear()
            self.priority_timers.clear()
            
            if self.consecutive_capture_failures == 0:
                 self.updateStatus.emit("idle", "green")


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
                
                if self._is_reinitializing_display:
                    self.logger.log("log_warn_display_reinitializing_monitor_loop")
                    time.sleep(0.5) 
                    continue
                
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
                
                if isinstance(current_state, CountdownState): time.sleep(1.0) 
                
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
                
                elif (frame_counter % self.effective_frame_skip_rate) != 0: 
                    time.sleep(0.01)
                    continue

                screen_bgr = self.capture_manager.capture_frame(region=self.recognition_area)
                
                if screen_bgr is None:
                    self.consecutive_capture_failures += 1
                    self._log("log_capture_failed") 
                    
                    if self.consecutive_capture_failures >= 10:
                        self.logger.log("log_capture_failed_limit_reached", force=True)
                        self.updateStatus.emit("idle_error", "red")
                        self.is_monitoring = False 
                        
                    time.sleep(1.0) 
                    continue
                
                self.consecutive_capture_failures = 0 
                
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
                            timer_data['priority'] = max(0, remaining_sec / 60.0) 
                    
                    # --- ▼▼▼ 修正箇所 (CPU使用率の計算) ▼▼▼ ---
                    cpu_percent = 0.0
                    if self.process:
                        try:
                            # interval=None で、前回呼び出し時からの平均CPU使用率を取得
                            cpu_percent = self.process.cpu_percent(interval=None)
                        except Exception:
                            cpu_percent = 0.0 # エラー時は 0.0
                    # --- ▲▲▲ 修正完了 ▲▲▲ ---
                    
                    fps_value = self.current_fps
                    
                    self.statsUpdated.emit(self._click_count, uptime_str, timer_data, cpu_percent, fps_value)
                
                time.sleep(0.01)
    
    # ... (_find_matches_for_eco_check から _save_captured_image まで変更なし) ...
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
            strict_color = self.app_config.get('strict_color_matching', False)

            effective_strict_color = strict_color and not use_gs
            
            if effective_strict_color:
                use_cl = False

            screen_image = s_gray if use_gs else s_bgr
            if use_cl:
                screen_umat = s_gray_umat if use_gs else s_bgr_umat
                screen_image = screen_umat if screen_umat is not None else screen_image

            s_shape = screen_image.get().shape[:2] if use_cl and isinstance(screen_image, cv2.UMat) else screen_image.shape[:2]

            for path, data in cache.items():
                
                templates_to_check = data['scaled_templates']

                for t in templates_to_check:
                    try:
                        template_image = t['gray'] if use_gs else t['image']
                        if use_cl:
                            t_umat = t.get('gray_umat' if use_gs else 'image_umat')
                            template_image = t_umat if t_umat else template_image

                        task_data = {'path': path, 'settings': data['settings'], 'template': template_image, 'scale': t['scale']}
                        t_shape = t['shape']

                        if self.thread_pool and not use_cl:
                            future = self.thread_pool.submit(_match_template_task, screen_image, template_image, task_data, s_shape, t_shape, effective_strict_color)
                            futures.append(future)
                        else:
                            match_result = _match_template_task(screen_image, template_image, task_data, s_shape, t_shape, effective_strict_color)
                            if match_result:
                                results.append(match_result)
                    except Exception as e:
                         self.logger.log("Error during template processing for %s (scale %s): %s", Path(path).name, t.get('scale', 'N/A'), str(e))

        if futures:
            for f in futures:
                try:
                    match_result = f.result();
                    if match_result: results.append(match_result)
                except Exception as e:
                     self.logger.log("Error getting result from match thread: %s", str(e))

        if not results: return []
        
        return results

    def _execute_click(self, match_info):
        try:
            item_path_str = match_info['path']
            self.environment_tracker.track_environment_on_click(item_path_str)
        except Exception as e:
            self.logger.log(f"[ERROR] Failed during environment tracking pre-click: {e}")

        result = self.action_manager.execute_click(match_info, self.recognition_area, self.target_hwnd, self.effective_capture_scale)
        if result and result.get('success'): self._click_count += 1; self._last_clicked_path = result.get('path'); self.last_successful_click_time = time.time(); self.clickCountUpdated.emit(self._click_count)

    def set_recognition_area(self, method: str):
        if self._is_reinitializing_display:
            try:
                self.logger.log("log_lazy_reinitialize_capture_backend")
                self._reinitialize_capture_backend()
            except Exception as e:
                self.logger.log("log_error_reinitialize_capture", str(e))
            finally:
                self._is_reinitializing_display = False
        
        # --- ▼▼▼ 修正箇所 (前回追加した冒頭の fullscreen チェックを削除) ▼▼▼ ---
        # (このブロック全体を削除)
        # fullscreen_rect = None
        # if method == "fullscreen":
        # ...
        # --- ▲▲▲ 修正完了 ▲▲▲ ---


        self.selectionProcessStarted.emit(); self.ui_manager.hide();
        
        self._stop_global_mouse_listener()
        
        self.pre_captured_image_for_registration = None
        
        if method == "rectangle":
            if not self._is_capturing_for_registration: 
                self.target_hwnd = None; self.current_window_scale = None; self.windowScaleCalculated.emit(0.0); self.logger.log("log_rec_area_set_rect")
                self.environment_tracker.on_rec_area_set("rectangle")
            else: 
                self.logger.log("log_capture_area_set_rect")
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

            if sys.platform == 'win32' and win32gui:
                if self._is_capturing_for_registration and self.recognition_area:
                    self.logger.log("log_capture_from_existing_rec_area")
                    (x1, y1, x2, y2) = self.recognition_area
                    center_x = (x1 + x2) // 2
                    center_y = (y1 + y2) // 2
                    self._handle_window_click_for_selection_windows(center_x, center_y)
                    return 
            
            if not self._is_capturing_for_registration:
                self.logger.log("log_rec_area_set_window")
            else:
                self.logger.log("log_capture_area_set_window")
                
            self.window_selection_listener = WindowSelectionListener(self._handle_window_click_for_selection)
            self.window_selection_listener.start()
            self.keyboard_selection_listener = keyboard.Listener(on_press=self._on_key_press_for_selection)
            self.keyboard_selection_listener.start()

        # --- ▼▼▼ 修正箇所 (fullscreen の処理を "正しい手順" に修正) ▼▼▼ ---
        elif method == "fullscreen":
            if self._is_capturing_for_registration:
                # 「画像登録」時に「全画面」は選択できない (UIを復帰させる)
                self.logger.log("log_capture_area_fullscreen_disabled") 
                self._on_selection_cancelled() # UI復帰とリスナー再起動
                QMessageBox.warning(
                    self.ui_manager, 
                    self.locale_manager.tr("warn_capture_fullscreen_title"), 
                    self.locale_manager.tr("warn_capture_fullscreen_text")
                )
                return # 処理を終了
            
            try:
                # プライマリモニターの座標を取得
                screen = QApplication.primaryScreen()
                if not screen:
                    raise Exception("QApplication.primaryScreen() returned None")
                
                geo = screen.geometry()
                fullscreen_rect = (geo.left(), geo.top(), geo.right() + 1, geo.bottom() + 1)
                
                # ログと環境トラッカーの設定 (windowモードと同様に)
                self.logger.log("log_rec_area_set_fullscreen_internal") # 内部ログ (翻訳キー不要)
                self.target_hwnd = None
                self.current_window_scale = None
                self.windowScaleCalculated.emit(0.0)
                self.environment_tracker.on_rec_area_set("fullscreen")
                self.appContextChanged.emit(None)
                
                # ★★★ 重要 ★★★
                # 座標を直接セットするのではなく、
                # 選択完了シグナルを発行して handle_area_selection を呼び出す
                self._areaSelectedForProcessing.emit(fullscreen_rect)
            
            except Exception as e:
                self.logger.log("log_error_get_primary_screen_geo", str(e))
                self._on_selection_cancelled() # 失敗したらUI復帰
                return
        # --- ▲▲▲ 修正完了 ▲▲▲ ---

    def _on_selection_cancelled(self):
        self.logger.log("log_selection_cancelled"); self._is_capturing_for_registration = False
        
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
        if os.environ.get('WAYLAND_DISPLAY'):
            self.logger.log("log_linux_wayland_manual_attempt")
            
        missing_tools = [tool for tool in ['xdotool', 'xwininfo'] if not shutil.which(tool)]
        if missing_tools: 
            self.logger.log("log_linux_tool_not_found", ', '.join(missing_tools))
            self._on_selection_cancelled()
            return
        
        try:
            id_proc = subprocess.run(
                ['xdotool', 'getmouselocation'], 
                capture_output=True, text=True, timeout=2, check=False 
            )

            if id_proc.returncode != 0 or not id_proc.stdout:
                stderr_output = id_proc.stderr.strip() if id_proc.stderr else "No output"
                raise ValueError(f"xdotool getmouselocation failed. Exit code: {id_proc.returncode}, Stderr: {stderr_output}")

            window_id_line = next((line for line in id_proc.stdout.strip().split() if line.startswith('window:')), None)
            
            if not window_id_line:
                raise ValueError(f"Could not find 'window:' in xdotool output: {id_proc.stdout}")
                
            window_id = window_id_line.split(':')[1]
            
            if not window_id.isdigit():
                 raise ValueError(f"Invalid window ID received: '{window_id}'")
            
            info_proc = subprocess.run(
                ['xwininfo', '-id', window_id], 
                capture_output=True, text=True, timeout=2, check=False 
            )

            if info_proc.returncode != 0 or not info_proc.stdout:
                stderr_output = info_proc.stderr.strip() if info_proc.stderr else "No output"
                raise ValueError(f"xwininfo failed for ID {window_id}. Exit code: {info_proc.returncode}, Stderr: {stderr_output}")

            info = {}
            for line in info_proc.stdout.split('\n'):
                 if ':' in line: parts = line.split(':', 1); key = parts[0].strip(); value = parts[1].strip(); info[key] = value
            
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

    def process_base_size_prompt_response(self, save_as_base: bool):
        try:
            if not (info := self._pending_window_info): self.logger.log("Warning: process_base_size_prompt_response called with no pending info."); self._showUiSignal.emit(); self.selectionProcessFinished.emit(); return
            title, current_dims, rect = info['title'], info['dims'], info['rect']
            
            self.environment_tracker.on_rec_area_set("window", title)
            self.appContextChanged.emit(title) 
            
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

    # --- ▼▼▼ 修正箇所 (削除) ▼▼▼ ---
    # def _get_filename_from_user(self):
    #     lm = self.locale_manager.tr; return QInputDialog.getText(self.ui_manager, lm("dialog_filename_prompt_title"), lm("dialog_filename_prompt_text"))
    # --- ▲▲▲ 修正完了 ▲▲▲ ---
    
    def _save_captured_image(self, region_coords):
        try: 
            # --- ▼▼▼ 修正箇所 (変更) ▼▼▼ ---
            # UI非表示は selectionProcessStarted シグナルに任せる
            # self.ui_manager.hide(); # 削除
            
            # _do_capture_and_emit を呼び出す
            QTimer.singleShot(100, lambda: self._do_capture_and_emit(region_coords)) 
            # --- ▲▲▲ 修正完了 ▲▲▲ ---
        except Exception as e: 
            # --- ▼▼▼ 修正箇所 (変更) ▼▼▼ ---
            # UI復帰は selectionProcessFinished シグナルに任せる
            self.logger.log("error_message_capture_prepare_failed", str(e)) # ログのみに変更
            self.selectionProcessFinished.emit() 
            self.pre_captured_image_for_registration = None
            # --- ▲▲▲ 修正完了 ▲▲▲ ---

    # --- ▼▼▼ 修正箇所 4/6: _capture_and_prompt_for_save を _do_capture_and_emit にリファクタリング ▼▼▼ ---
    def _do_capture_and_emit(self, region_coords):
        """
        (Coreスレッド/タイマー)
        実際に画像をキャプチャし、成功したらUIスレッドにプレビュー準備完了シグナルを発行する。
        失敗したらUIスレッドに復帰シグナルを発行する。
        """
        try:
            captured_image = None
            
            if self.pre_captured_image_for_registration is not None:
                self.logger.log("log_cropping_from_pre_capture")
                try:
                    (x1, y1, x2, y2) = region_coords
                    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
                    captured_image = self.pre_captured_image_for_registration[y1:y2, x1:x2]
                    self.pre_captured_image_for_registration = None 
                except Exception as crop_e:
                    self.logger.log("log_crop_from_pre_capture_failed", str(crop_e))
                    captured_image = None
                    self.pre_captured_image_for_registration = None
            else:
                self.logger.log("log_capturing_new_frame")
                captured_image = self.capture_manager.capture_frame(region=region_coords)

            if captured_image is None or captured_image.size == 0: 
                # キャプチャ失敗
                self.logger.log("warn_message_capture_failed") # ログに変更
                self.captureFailedSignal.emit() # UIスレッドにQMessageBoxを表示させる
                self.selectionProcessFinished.emit() # UIを復帰させる
                return
                
            # キャプチャ成功
            # UIスレッドに画像を送り、プレビュー表示と保存ダイアログの表示を依頼する
            self.capturedImageReadyForPreview.emit(captured_image)
                
        except Exception as e: 
            # 予期せぬエラー
            self.logger.log("error_message_capture_save_failed", str(e))
            self.captureFailedSignal.emit() # UIスレッドにQMessageBoxを表示させる
            self.selectionProcessFinished.emit()
        finally:
             self.pre_captured_image_for_registration = None
             
    # --- ▼▼▼ 修正箇所 (削除) ▼▼▼ ---
    # @Slot(np.ndarray)
    # def prompt_and_save_image_task(self, captured_image):
    #     ... (このメソッド全体を削除) ...
    # --- ▲▲▲ 修正完了 ▲▲▲ --
    
    def _save_image_task(self, image, save_path, env_data: dict):
        try:
            is_success, buffer = cv2.imencode('.png', image);
            if not is_success: raise IOError("cv2.imencode failed")
            buffer.tofile(str(save_path))

            settings = self.config_manager.load_item_setting(Path()); 
            settings['image_path'] = str(save_path); 
            settings['point_click'] = True
            settings['environment_info'] = [env_data] 

            self.config_manager.save_item_setting(save_path, settings); 
            self.config_manager.add_item(save_path)
            return True, self.locale_manager.tr("log_image_saved", str(save_path.name))
        except Exception as e: return False, self.locale_manager.tr("log_image_save_failed", str(e))

    # --- ▼▼▼ 修正箇所 6/6: _on_save_image_done を修正 ▼▼▼ ---
    
    @Slot(str, np.ndarray)
    def handle_save_captured_image(self, file_name: str, captured_image: np.ndarray):
        """
        (UIスレッド) ui.py からの保存要求を受け取り、
        ワーカースレッドで保存タスクを実行します。
        """
        try:
            if not file_name:
                self.logger.log("log_rename_error_empty")
                self.selectionProcessFinished.emit()
                return

            self.ui_manager.set_tree_enabled(False)
            save_path = self.config_manager.base_dir / f"{file_name}.png"
            
            # ★ 既存のファイル名チェック (ui.py から移動)
            if save_path.exists():
                lm = self.locale_manager.tr
                reply = QMessageBox.question(self.ui_manager, lm("confirm_overwrite_title"), 
                                             lm("confirm_overwrite_message", save_path.name), 
                                             QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, 
                                             QMessageBox.StandardButton.No)
                if reply == QMessageBox.StandardButton.No:
                    self.ui_manager.set_tree_enabled(True)
                    self.selectionProcessFinished.emit() # UI復帰
                    return
            
            env_data = self.environment_tracker._collect_current_environment()
            
            if self.thread_pool: 
                self.thread_pool.submit(self._save_image_task, captured_image, save_path, env_data).add_done_callback(self._on_save_image_done)
            else: 
                self._on_save_image_done(None, success=False, message=self.locale_manager.tr("Error: Thread pool unavailable for saving."))
                self.ui_manager.set_tree_enabled(True)
                self.selectionProcessFinished.emit() # UI復帰
        
        except Exception as e:
            QMessageBox.critical(self.ui_manager, self.locale_manager.tr("error_title_capture_save_failed"), self.locale_manager.tr("error_message_capture_save_failed", str(e)))
            self.selectionProcessFinished.emit() # UI復帰
    # --- ▲▲▲ 修正完了 ▲▲▲ ---
    
    def _on_save_image_done(self, future, success=None, message=None):
        """
        (UIスレッド) 保存タスク完了時のコールバック。
        UIの復帰は selectionProcessFinished シグナルに一本化する。
        """
        try:
            if future: 
                success, message = future.result()
            
            if success:
                self._log(message)
                if self.thread_pool: 
                    self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
                else: 
                    self.ui_manager.set_tree_enabled(True)
            else: 
                QMessageBox.critical(self.ui_manager, self.locale_manager.tr("error_title_image_save_failed"), message)
                self.ui_manager.set_tree_enabled(True)
        
        except Exception as e: 
            QMessageBox.critical(self.ui_manager, self.locale_manager.tr("error_title_image_save_failed"), f"Error processing save result: {e}")
            self.ui_manager.set_tree_enabled(True)
        
        finally:
            # ★ 成功・失敗・例外に関わらず、必ずUI復帰シグナルを発行する
            self.selectionProcessFinished.emit()
    # --- ▲▲▲ 修正完了 ▲▲▲ ---

    def clear_recognition_area(self):
        self.recognition_area = None; self.current_window_scale = None; self.target_hwnd = None; self.windowScaleCalculated.emit(0.0)
        
        self.environment_tracker.on_rec_area_clear()
        self.appContextChanged.emit(None) 
        
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

    def on_screen_geometry_changed(self, rect):
        if self._is_reinitializing_display:
            return
            
        if self.recognition_area or self.is_monitoring:
            self._is_reinitializing_display = True 
            self.logger.log("log_screen_resolution_changed")
            
            if self.is_monitoring:
                self.stop_monitoring()

            if self.recognition_area:
                self.clear_recognition_area()
            
            self.logger.log("log_lazy_reinitialize_scheduled")
            self.restartApplicationRequested.emit()
