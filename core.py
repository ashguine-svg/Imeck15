# core.py (D&D対応・右クリック動作変更・多言語対応・インデント修正版)
# ★★★ 認識範囲設定と画像キャプチャのロジックを分離 ＆ AttributeError を修正 ★★★
# ★★★ キャプチャプレビュー表示 ＆ 認識範囲設定後のUI再表示を修正 ★★★
# ★★★ キャプチャプレビュー表示の確実性を向上 (タブ切り替え、遅延追加) ★★★
# ★★★ リスナー再開処理を再度遅延させ、ログを追加 ★★★
# ★★★ 監視停止時の競合状態 (NoneType.handle) を RLock で修正 ★★★
# ★★★ 軽量化モードのプリセット判定を英語の内部名に変更 (問題1対応) ★★★

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
        self.locale_manager = locale_manager # LocaleManagerインスタンスを保持

        self.action_manager = ActionManager(self.logger)
        self.template_manager = TemplateManager(self.config_manager, self.logger)
        self.performance_monitor = performance_monitor
        self.logger.log(OPENCL_STATUS_MESSAGE) # OpenCL status is logged directly

        self.is_monitoring = False
        self._monitor_thread = None
        self._click_count = 0

        self.normal_template_cache = {}
        self.backup_template_cache = {}

        self.state = None
        # ★★★ 状態変数へのアクセスを保護するための RLock を追加 ★★★
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

        # Thread pool initialization
        cpu_cores = os.cpu_count() or 8
        worker_threads = min(max(1, cpu_cores // 4), 2)
        self.logger.log("log_info_cores", cpu_cores, worker_threads)
        self.thread_pool = ThreadPoolExecutor(max_workers=worker_threads) # Ensure this line exists and is indented
        self.cache_lock = threading.Lock()

        # Right-click handling variables
        self.click_timer = None
        self.last_right_click_time = 0
        self.right_click_count = 0
        self.CLICK_INTERVAL = 0.3 # Max interval for double/triple click

        # Global mouse listener
        self.mouse_listener = None
        self._start_global_mouse_listener()

        # Signal connections
        self._showUiSignal.connect(self._show_ui_safe)
        self._areaSelectedForProcessing.connect(self.handle_area_selection)
        self.startMonitoringRequested.connect(self.start_monitoring)
        self.stopMonitoringRequested.connect(self.stop_monitoring)

        # App configuration and scaling
        self.app_config = self.ui_manager.app_config
        self.current_window_scale = None
        self._pending_window_info = None
        self._pending_scale_prompt_info = None
        self._cooldown_until = 0

        self.effective_capture_scale = 1.0
        self.effective_frame_skip_rate = 2

        # Eco mode settings
        self.ECO_MODE_SKIP_RATE = 50 # Example skip rate, consider making configurable
        self.ECO_CHECK_INTERVAL = 1.0 # Check every 1 second in Eco Mode
        self.ECO_MODE_DELAY = 5.0 # Delay before entering Eco Mode after last click

        # Screen stability check variables
        self.screen_stability_hashes = deque(maxlen=3)
        self.latest_frame_for_hash = None

        # Eco mode state variables
        self.last_successful_click_time = 0
        self.is_eco_cooldown_active = False
        self._last_eco_check_time = 0 # Initialize Eco Mode check timer

        # Apply initial app config settings
        self.on_app_config_changed()

        # Log spam filter variables
        self._last_log_message = ""
        self._last_log_time = 0
        # Filter uses keys directly now
        self._log_spam_filter = {"log_stability_hold_click", "log_eco_mode_standby"}

    def transition_to(self, new_state):
        # ★★★ state の書き込みをロックで保護 ★★★
        with self.state_lock:
            self.state = new_state
        self._last_clicked_path = None

    def transition_to_timer_priority(self, folder_path):
        folder_settings = self.config_manager.load_item_setting(Path(folder_path))
        timeout_seconds = folder_settings.get('priority_timeout', 5) * 60
        timeout_time = time.time() + timeout_seconds
        new_state = PriorityState(self, 'timer', folder_path, timeout_time)
        self.transition_to(new_state)

    def transition_to_image_priority(self, folder_path):
        # Default timeout for image priority (e.g., 5 minutes)
        timeout_time = time.time() + 300
        required_children = self.folder_children_map.get(folder_path, set())
        new_state = PriorityState(self, 'image', folder_path, timeout_time, required_children)
        self.transition_to(new_state)

    def transition_to_countdown(self, trigger_match):
        new_state = CountdownState(self, trigger_match)
        self.transition_to(new_state)

    def _log(self, message: str, *args, force: bool = False):
        """Logs a message, translating if it's a key, respects spam filter."""
        current_time = time.time()
        log_key = message # Use the message itself as the key for filtering

        # Check spam filter unless forced
        if not force and \
           log_key == self._last_log_message and \
           log_key in self._log_spam_filter and \
           current_time - self._last_log_time < 3.0: # 3 second filter window
            return

        # Pass key and args to the logger for translation and formatting
        self.logger.log(log_key, *args)
        self._last_log_message = log_key
        self._last_log_time = current_time

    def set_opencl_enabled(self, enabled: bool):
        """Enables or disables OpenCL usage."""
        if OPENCL_AVAILABLE:
            try:
                cv2.ocl.setUseOpenCL(enabled)
                status_key = "log_linux_workaround_status_enabled" if cv2.ocl.useOpenCL() else "log_linux_workaround_status_disabled"
                status = self.locale_manager.tr(status_key)
                self.logger.log("log_opencl_set", status)
                # Rebuild cache if monitoring to apply change
                if self.is_monitoring:
                    self.logger.log("log_opencl_rebuild")
                    # Use existing thread pool
                    self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
            except Exception as e:
                self.logger.log("log_opencl_error", str(e))

    def on_app_config_changed(self):
        """Applies application settings changes."""
        self.app_config = self.ui_manager.app_config
        # Set capture method
        self.capture_manager.set_capture_method(self.app_config.get('capture_method', 'dxcam'))
        # Set OpenCL state
        self.set_opencl_enabled(self.app_config.get('use_opencl', True))

        # Determine effective capture scale and frame skip based on lightweight mode
        lw_conf = self.app_config.get('lightweight_mode', {})
        is_lw_enabled = lw_conf.get('enabled', False)
        # ★★★ 修正: デフォルト値を 'standard' (英語) に変更 ★★★
        preset_internal = lw_conf.get('preset', 'standard') # Use internal name

        # Map internal preset name to settings
        if is_lw_enabled:
            user_frame_skip = self.app_config.get('frame_skip_rate', 2)
            # ★★★ 修正: 比較を英語の内部名に変更 ★★★
            if preset_internal == "standard":
                self.effective_capture_scale, self.effective_frame_skip_rate = 0.5, user_frame_skip + 5
            elif preset_internal == "performance":
                self.effective_capture_scale, self.effective_frame_skip_rate = 0.4, user_frame_skip + 20
            elif preset_internal == "ultra":
                self.effective_capture_scale, self.effective_frame_skip_rate = 0.3, user_frame_skip + 25
            else: # Fallback to standard if name is unknown
                 self.effective_capture_scale, self.effective_frame_skip_rate = 0.5, user_frame_skip + 5
        else:
            # Not lightweight mode, use base settings
            self.effective_capture_scale = 1.0
            self.effective_frame_skip_rate = self.app_config.get('frame_skip_rate', 2)

        # Log the effective settings
        self.logger.log(
            "log_app_config_changed",
            self.capture_manager.current_method,
            is_lw_enabled,
            preset_internal, # Log internal name
            f"{self.effective_capture_scale:.2f}",
            self.effective_frame_skip_rate,
            cv2.ocl.useOpenCL() if OPENCL_AVAILABLE else 'N/A'
        )

    def _show_ui_safe(self):
        """Safely shows and activates the main UI window."""
        if self.ui_manager:
            self.ui_manager.show()
            self.ui_manager.raise_() # Bring to front
            try:
                self.ui_manager.activateWindow() # Attempt activation
            except Exception as e:
                # Log specific warning for SetForegroundWindow failure (common OS restriction)
                if 'SetForegroundWindow' in str(e):
                    self.logger.log("log_warn_set_foreground_failed")
                else:
                    self.logger.log("log_warn_activate_window_error", str(e))

    # ★★★ 開始/停止ログを追加、開始前に既存リスナー停止処理を追加 ★★★
    def _start_global_mouse_listener(self):
        """Starts the global mouse listener if not already running."""
        # 念のため、既存のリスナーが動作中なら停止を試みる
        if self.mouse_listener and self.mouse_listener.is_alive():
            self.logger.log("[DEBUG] Stopping existing listener before starting a new one.")
            self._stop_global_mouse_listener() # Call the existing stop method
            # time.sleep(0.1) # 必要なら短い待機を入れる (通常は不要)

        if self.mouse_listener is None:
            self.logger.log("Attempting to start global mouse listener...") # 開始試行ログ
            try:
                self.mouse_listener = mouse.Listener(on_click=self._on_global_click)
                self.logger.log("[DEBUG] Listener object created. Calling start()...") # start() 呼び出し前ログ
                self.mouse_listener.start()
                # start() が完了したか、実際に動作しているか確認
                if self.mouse_listener.is_alive():
                    self.logger.log("Global mouse listener started successfully (is_alive() confirmed).") # 成功ログ
                else:
                    self.logger.log("[ERROR] Listener start() called but is_alive() is false! Listener might have failed silently.")
                    self.mouse_listener = None # 失敗した場合はリセット
            except Exception as e:
                # start() 自体が例外を投げた場合
                self.logger.log(f"log_error_listener_start: Exception during listener.start(): {e}", str(e)) # エラーログ
                self.mouse_listener = None # Ensure listener is None if start fails
        else:
             self.logger.log("[WARN] Mouse listener object was not None before start attempt. State issue?")


    # ★★★ 開始/停止ログを追加 ★★★
    def _stop_global_mouse_listener(self):
        """Stops the global mouse listener if running."""
        if self.mouse_listener and self.mouse_listener.is_alive():
            self.logger.log("Attempting to stop global mouse listener...") # 停止試行ログ
            try:
                # pynput の stop() は完了を待たないことがあるため、join() も試す
                self.mouse_listener.stop()
                # self.mouse_listener.join(timeout=0.5) # join を試す場合 (タイムアウト付き)
                # is_alive で停止を確認
                if not self.mouse_listener.is_alive():
                    self.logger.log("Global mouse listener stopped successfully.") # 成功ログ
                else:
                    self.logger.log("[WARN] Listener stop() called but is_alive() is still true.")
            except Exception as e:
                self.logger.log("log_warn_listener_stop", str(e)) # 既存のエラーログ
        # else:
            # self.logger.log("Mouse listener is not running or already stopped.") # 実行中でない場合 (任意)
        self.mouse_listener = None # Clean up reference


    def _on_global_click(self, x, y, button, pressed):
        """Handles global right-clicks for start/stop monitoring."""
        if button == mouse.Button.right and pressed:
            current_time = time.time()

            # Cancel any pending single/double click timer
            if self.click_timer:
                self.click_timer.cancel()
                self.click_timer = None

            # Reset count if interval is too long
            if current_time - self.last_right_click_time > self.CLICK_INTERVAL:
                self.right_click_count = 1
            else:
                self.right_click_count += 1

            self.last_right_click_time = current_time

            # Handle triple-click immediately
            if self.right_click_count == 3:
                self.logger.log("log_right_click_triple") # 既存のログ
                self.startMonitoringRequested.emit()
                self.right_click_count = 0 # Reset after action
            else:
                # Start timer for single/double click detection
                self.click_timer = Timer(self.CLICK_INTERVAL, self._handle_click_timer)
                self.click_timer.start()


    def _handle_click_timer(self):
        """Called after CLICK_INTERVAL to determine single vs double click."""
        if self.right_click_count == 1:
            pass # Ignore single click (likely for context menus)
        elif self.right_click_count == 2:
            self.logger.log("log_right_click_double") # 既存のログ
            self.stopMonitoringRequested.emit()

        # Reset count and timer reference
        self.right_click_count = 0
        self.click_timer = None

    def cleanup(self):
        """Cleans up resources before application exit."""
        self.stop_monitoring()
        self._stop_global_mouse_listener()
        if self.capture_manager:
            self.capture_manager.cleanup()
        # Shutdown thread pool gracefully (optional, Python usually handles this)
        if hasattr(self, 'thread_pool') and self.thread_pool:
            self.thread_pool.shutdown(wait=False) # Don't wait indefinitely

    def _on_cache_build_done(self, future):
        """Callback executed when template cache build finishes."""
        try:
            future.result() # Check for exceptions during build
        except Exception as e:
            self.logger.log("log_cache_build_error", str(e))
        finally:
            self.cacheBuildFinished.emit() # Notify UI

    def capture_image_for_registration(self):
        """Initiates the process to capture an image for registration."""
        self._is_capturing_for_registration = True
        # Ask UI manager to show the Rec Area selection dialog
        self.ui_manager.setRecAreaDialog()

    def delete_selected_items(self, paths_to_delete: list):
        """Deletes selected items (images or folders) from configuration."""
        if not paths_to_delete:
            return

        self.ui_manager.set_tree_enabled(False) # Disable tree during operation
        deleted_count = 0
        failed_count = 0
        last_error = ""
        try:
            for path_str in paths_to_delete:
                try:
                    self.config_manager.remove_item(path_str)
                    self.logger.log("log_item_deleted", Path(path_str).name)
                    deleted_count += 1
                except Exception as e:
                    last_error = str(e)
                    self.logger.log("log_item_delete_failed", Path(path_str).name, last_error)
                    failed_count += 1

            # Show error message if any deletions failed
            if failed_count > 0:
                 QMessageBox.critical(
                     self.ui_manager,
                     self.locale_manager.tr("error_title_delete_failed"),
                     self.locale_manager.tr("error_message_delete_failed", failed_count) + f"\n{last_error}"
                 )
        finally:
            # Rebuild cache after deletion
            if self.thread_pool: # Check if pool exists
                self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
            else: # If pool doesn't exist (e.g., during shutdown), re-enable tree directly
                 self.ui_manager.set_tree_enabled(True)

    def on_folder_settings_changed(self):
        """Handles folder settings changes by rebuilding cache."""
        self.logger.log("log_folder_settings_changed")
        self.ui_manager.set_tree_enabled(False)
        if self.thread_pool:
            self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
        else:
             self.ui_manager.set_tree_enabled(True)


    def create_folder(self):
        """Shows dialog to create a new folder."""
        folder_name, ok = QInputDialog.getText(
            self.ui_manager,
            self.locale_manager.tr("create_folder_title"),
            self.locale_manager.tr("create_folder_prompt")
        )
        if ok and folder_name:
            success, message_key_or_text = self.config_manager.create_folder(folder_name)
            if success:
                # If success, message_key_or_text is already translated text
                self.logger.log(message_key_or_text)
                self.ui_manager.update_image_tree()
                # Rebuild cache
                if self.thread_pool:
                    self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
                else:
                    self.ui_manager.set_tree_enabled(True)

            else:
                # If failure, message_key_or_text is an error key or raw message
                QMessageBox.warning(
                    self.ui_manager,
                    self.locale_manager.tr("error_title_create_folder"),
                    self.locale_manager.tr(message_key_or_text) # Try to translate potential key
                )

    def move_item_into_folder(self):
        """Handles the 'Move Into Folder' button click."""
        selected_items = self.ui_manager.image_tree.selectedItems()
        source_paths = [item.data(0, Qt.UserRole) for item in selected_items if item.data(0, Qt.UserRole)]
        lm = self.locale_manager.tr

        if not source_paths:
            QMessageBox.warning(self.ui_manager, lm("warn_move_item_no_selection"), lm("warn_move_item_no_selection_text"))
            return

        # Check if selected items are valid for moving into a folder (top-level images)
        for path_str in source_paths:
            source_path = Path(path_str)
            if not source_path.is_file() or source_path.parent != self.config_manager.base_dir:
                QMessageBox.warning(self.ui_manager, lm("warn_move_item_not_image"), lm("warn_move_item_not_image_text"))
                return

        # Get list of available folders
        folders = [item for item in self.config_manager.get_hierarchical_list() if item['type'] == 'folder']
        if not folders:
            QMessageBox.information(self.ui_manager, lm("info_move_item_no_folder"), lm("info_move_item_no_folder_text"))
            return

        # Show dialog to choose destination folder
        folder_names = [f['name'] for f in folders]
        dest_folder_name, ok = QInputDialog.getItem(
            self.ui_manager,
            lm("move_item_dialog_title"),
            lm("move_item_dialog_prompt"),
            folder_names, 0, False
        )

        # If a folder is chosen, move the items
        if ok and dest_folder_name:
            dest_folder_path_str = str(self.config_manager.base_dir / dest_folder_name)
            self.move_items_into_folder(source_paths, dest_folder_path_str)

    def move_items_into_folder(self, source_paths: list, dest_folder_path_str: str):
        """Moves a list of items into the specified destination folder."""
        self.ui_manager.set_tree_enabled(False)
        moved_count = 0
        failed_count = 0
        final_message = ""

        try:
            for source_path_str in source_paths:
                success, message_or_key = self.config_manager.move_item(source_path_str, dest_folder_path_str)
                if success:
                    # Log the success message (already translated by config_manager)
                    self.logger.log(message_or_key)
                    moved_count += 1
                else:
                    # Log failure using error key
                    self.logger.log("log_move_item_failed", self.locale_manager.tr(message_or_key))
                    failed_count += 1
                    final_message = self.locale_manager.tr(message_or_key) # Store last error

            if failed_count > 0:
                QMessageBox.critical(
                    self.ui_manager,
                    self.locale_manager.tr("error_title_move_item_failed"),
                    self.locale_manager.tr("error_message_move_item_failed", failed_count, final_message)
                )
        finally:
            # Rebuild cache after moving
            if self.thread_pool:
                self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
            else:
                 self.ui_manager.set_tree_enabled(True)


    def move_item_out_of_folder(self):
        """Handles the 'Move Out of Folder' button click."""
        source_path_str, name = self.ui_manager.get_selected_item_path()
        lm = self.locale_manager.tr

        if not source_path_str:
            QMessageBox.warning(self.ui_manager, lm("warn_move_out_no_selection"), lm("warn_move_out_no_selection_text"))
            return
        source_path = Path(source_path_str)
        # Check if selected item is valid for moving out (image file inside a folder)
        if not source_path.is_file() or source_path.parent == self.config_manager.base_dir:
            QMessageBox.warning(self.ui_manager, lm("warn_move_out_not_in_folder"), lm("warn_move_out_not_in_folder_text"))
            return

        # Destination is always the base directory
        dest_folder_path_str = str(self.config_manager.base_dir)
        success, message_or_key = self.config_manager.move_item(source_path_str, dest_folder_path_str)
        if success:
            self.logger.log(message_or_key) # Already translated
            self.ui_manager.update_image_tree()
            # Rebuild cache
            if self.thread_pool:
                self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
            else:
                 self.ui_manager.set_tree_enabled(True)

        else:
            QMessageBox.critical(self.ui_manager, lm("error_title_move_out_failed"), self.locale_manager.tr(message_or_key))

    def load_image_and_settings(self, file_path: str):
        """Loads image data and settings for the given file path."""
        # Clear current data if path is invalid or a directory
        if file_path is None or Path(file_path).is_dir():
            self.current_image_path = None
            self.current_image_settings = None
            self.current_image_mat = None
            self.updatePreview.emit(None, None) # Clear preview
            return

        try:
            self.current_image_path = file_path
            self.current_image_settings = self.config_manager.load_item_setting(Path(file_path))
            # Load image using OpenCV, handling potential non-ASCII paths
            with open(file_path, 'rb') as f:
                file_bytes = np.frombuffer(f.read(), np.uint8)
                self.current_image_mat = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)

            if self.current_image_mat is None:
                # Raise error if decoding fails
                raise ValueError(self.locale_manager.tr("log_image_decode_failed"))

        except Exception as e:
            self.logger.log("log_image_load_failed", file_path, str(e))
            # Clear data on failure
            self.current_image_path = None
            self.current_image_settings = None
            self.current_image_mat = None
            self.updatePreview.emit(None, None) # Clear preview
            return

        # If load successful, update preview and settings UI
        self._recalculate_and_update(request_save=False) # Don't trigger save on load

    def on_image_settings_changed(self, settings: dict):
        """Handles changes from the item settings UI."""
        if self.current_image_settings:
            self.current_image_settings.update(settings)
            self._recalculate_and_update() # Recalculate ROI, update preview, trigger save

    def on_roi_settings_changed(self, roi_data: dict):
        """Handles changes to the variable ROI rectangle from the preview label."""
        if self.current_image_settings:
            self.current_image_settings.update(roi_data) # roi_data contains {'roi_rect_variable': [...]}
            self._recalculate_and_update()

    def on_preview_click_settings_changed(self, click_data: dict):
        """Handles changes to click position/rect from the preview label."""
        if self.current_image_settings:
            self.current_image_settings.update(click_data) # click_data has {'click_position': [...]} or {'click_rect': [...]}
            self._recalculate_and_update()

    def _recalculate_and_update(self, request_save=True):
        """Recalculates fixed ROI if needed, updates preview, and optionally triggers save."""
        if self.current_image_mat is not None and self.current_image_settings:
            h, w = self.current_image_mat.shape[:2]
            # Always calculate the fixed ROI rectangle based on current settings
            # calculate_roi_rect handles logic for fixed vs variable mode
            self.current_image_settings['roi_rect'] = self.calculate_roi_rect((w, h), self.current_image_settings)

        # Emit signal to update the preview label (image and overlays)
        self.updatePreview.emit(self.current_image_mat, self.current_image_settings)

        # Trigger delayed save if requested
        if request_save:
            self.ui_manager.request_save()

    def calculate_roi_rect(self, image_size, settings):
        """
        Calculates the ROI rectangle based on settings (fixed or variable).
        Returns None if ROI is disabled or cannot be calculated.
        """
        if not settings.get('roi_enabled', False):
            return None

        roi_mode = settings.get('roi_mode', 'fixed')

        if roi_mode == 'variable':
            # For variable mode, return the user-defined rectangle directly
            return settings.get('roi_rect_variable') # Returns None if not set

        # --- Logic for fixed mode ---
        center_x, center_y = -1, -1
        # Determine center based on point or range click setting
        if settings.get('point_click') and settings.get('click_position'):
            center_x, center_y = settings['click_position']
        elif settings.get('range_click') and settings.get('click_rect'):
            rect = settings['click_rect']
            center_x = (rect[0] + rect[2]) / 2
            center_y = (rect[1] + rect[3]) / 2

        # If no valid center point is found, cannot calculate fixed ROI
        if center_x == -1:
            return None

        # Calculate fixed ROI (e.g., 200x200 centered)
        roi_w, roi_h = 200, 200
        x1 = int(center_x - roi_w / 2)
        y1 = int(center_y - roi_h / 2)
        x2 = int(center_x + roi_w / 2)
        y2 = int(center_y + roi_h / 2)

        # Return coordinates ensuring they are within image bounds (handled during processing)
        # Note: TemplateManager clamps these coordinates further
        return (x1, y1, x2, y2)


    def save_current_settings(self):
        """Saves the current image's settings to its JSON file."""
        if self.current_image_path and self.current_image_settings:
            self.config_manager.save_item_setting(Path(self.current_image_path), self.current_image_settings)
            self.logger.log("log_settings_saved", Path(self.current_image_path).name)

    def load_images_into_manager(self, file_paths):
        """Adds new images from file paths to the configuration."""
        self.ui_manager.set_tree_enabled(False)
        added_count = 0
        for fp in file_paths:
            try:
                self.config_manager.add_item(Path(fp))
                added_count += 1
            except Exception as e:
                 # Log error but continue trying others
                 self.logger.log("Error adding item %s: %s", Path(fp).name, str(e))

        if added_count > 0:
            self._log("log_images_added", added_count)
            # Rebuild cache after adding images
            if self.thread_pool:
                self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
            else:
                 self.ui_manager.set_tree_enabled(True)
        else:
             self.ui_manager.set_tree_enabled(True) # Re-enable if nothing was added


    def on_order_changed(self):
        """Handles changes in the tree item order by saving the new order."""
        try:
            # Check if UIManager has the save method (it should)
            if hasattr(self.ui_manager, 'save_tree_order'):
                self.ui_manager.save_tree_order()
                self.logger.log("log_order_saved")
            else:
                self.logger.log("log_warn_save_order_failed")
        except Exception as e:
            self.logger.log("log_error_save_order", str(e))

        # Rebuild cache after order change (might affect priority if modes are used)
        self.ui_manager.set_tree_enabled(False)
        if self.thread_pool:
            self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
        else:
            self.ui_manager.set_tree_enabled(True)


    def _build_template_cache(self):
        """Builds the template caches in a separate thread."""
        with self.cache_lock: # Ensure thread safety
            (
                self.normal_template_cache,
                self.backup_template_cache,
                self.priority_timers, # Overwrite or update timers based on current config
                self.folder_children_map
            ) = self.template_manager.build_cache(
                    self.app_config,
                    self.current_window_scale,
                    self.effective_capture_scale,
                    self.is_monitoring,
                    self.priority_timers # Pass existing timers to preserve state if monitoring
                )

    def start_monitoring(self):
        """Starts the monitoring loop."""
        # Check if recognition area is set
        if not self.recognition_area:
            QMessageBox.warning(
                self.ui_manager,
                self.locale_manager.tr("warn_rec_area_not_set_title"),
                self.locale_manager.tr("warn_rec_area_not_set_text")
            )
            return

        # Start only if not already monitoring
        if not self.is_monitoring:
            self.is_monitoring = True
            self.transition_to(IdleState(self)) # Start in Idle state
            # Reset counters and state variables
            self._click_count = 0
            self._cooldown_until = 0
            self._last_clicked_path = None
            self.screen_stability_hashes.clear()
            self.last_successful_click_time = 0
            self.is_eco_cooldown_active = False
            self._last_eco_check_time = time.time() # Reset Eco check timer

            # Rebuild cache with current settings and start monitoring thread
            self.ui_manager.set_tree_enabled(False)
            if self.thread_pool:
                # Submit cache build, then start loop in callback (or handle potential race condition)
                # For simplicity, starting loop immediately after submitting build
                self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
                self._monitor_thread = threading.Thread(target=self._monitoring_loop, daemon=True)
                self._monitor_thread.start()
                self.updateStatus.emit("monitoring", "blue")
                self.logger.log("log_monitoring_started")
            else:
                 # Handle case where thread pool might not be initialized
                 self.logger.log("Error: Thread pool not available to start monitoring.")
                 self.is_monitoring = False
                 self.ui_manager.set_tree_enabled(True)


    def stop_monitoring(self):
        """Stops the monitoring loop."""
        if self.is_monitoring:
            self.is_monitoring = False
            # ★★★ state の書き込みをロックで保護 ★★★
            with self.state_lock:
                self.state = None # Clear current state
            
            # Wait for monitoring thread to finish
            if self._monitor_thread and self._monitor_thread.is_alive():
                self._monitor_thread.join(timeout=1.0) # Wait up to 1 second

            # Clear best scale cache when stopping (forces re-search on next start if needed)
            with self.cache_lock:
                for cache in [self.normal_template_cache, self.backup_template_cache]:
                    for item in cache.values():
                        item['best_scale'] = None

            self.updateStatus.emit("idle", "green")
            self.logger.log("log_monitoring_stopped")
            self.ui_manager.set_tree_enabled(True) # Re-enable tree

    def _monitoring_loop(self):
        """The main loop that captures screen, finds matches, and triggers actions."""
        last_match_time_map = {} # Tracks last click time per image path
        fps_last_time = time.time()
        frame_counter = 0

        while self.is_monitoring:
            # ★★★ 修正: state をロックを取得してローカル変数にコピー ★★★
            with self.state_lock:
                current_state = self.state

            # ★★★ 修正: 監視停止時に current_state が None になるためチェック ★★★
            if not current_state:
                if not self.is_monitoring:
                    break # 監視が意図通り停止した場合、ループを抜ける
                else:
                    # 監視中にもかかわらず state が None になった場合 (異常系)
                    self.logger.log("[WARN] Monitoring is active but state is None. Re-initializing to IdleState.")
                    self.transition_to(IdleState(self)) # IdleState にリセット
                    continue # 次のループで state を再取得

            try:
                current_time = time.time()

                # --- Cooldown Check ---
                # Skip iteration if in post-click cooldown
                if self._cooldown_until > current_time:
                    sleep_duration = min(self._cooldown_until - current_time, 0.1)
                    time.sleep(sleep_duration)
                    continue

                # --- FPS Calculation ---
                frame_counter += 1
                delta_time = current_time - fps_last_time
                if delta_time >= 1.0:
                    fps = frame_counter / delta_time
                    self.fpsUpdated.emit(fps)
                    fps_last_time = current_time
                    frame_counter = 0

                # --- State Management ---
                # Check for timer priority activation if in Idle state
                # ★★★ 修正: self.state -> current_state ★★★
                if isinstance(current_state, IdleState):
                    self._check_and_activate_timer_priority_mode()

                # --- Eco Mode Logic ---
                is_eco_enabled = self.app_config.get('eco_mode',{}).get('enabled',True)
                # Eligible for Eco Mode if enabled, Idle state, and past delay since last click
                is_eco_eligible = (is_eco_enabled and
                                   self.last_successful_click_time > 0 and
                                   # ★★★ 修正: self.state -> current_state ★★★
                                   isinstance(current_state, IdleState) and
                                   (current_time - self.last_successful_click_time > self.ECO_MODE_DELAY))

                self.is_eco_cooldown_active = is_eco_eligible

                # --- Frame Skipping Logic ---
                skip_capture_and_handle = False

                # 1. CountdownState: Always wait ~1 second per loop iteration
                # ★★★ 修正: self.state -> current_state ★★★
                if isinstance(current_state, CountdownState):
                    time.sleep(1.0) # Effectively limits FPS to 1

                # 2. Eco Mode: Skip if not yet time for the interval check
                elif self.is_eco_cooldown_active:
                    self._log("log_eco_mode_standby") # Log standby status
                    # Check if ECO_CHECK_INTERVAL has passed since last check
                    if current_time - self._last_eco_check_time < self.ECO_CHECK_INTERVAL:
                        # Calculate remaining sleep time and wait
                        sleep_time = self.ECO_CHECK_INTERVAL - (current_time - self._last_eco_check_time)
                        if sleep_time > 0:
                            time.sleep(sleep_time)
                        skip_capture_and_handle = True # Skip rest of loop this iteration
                    else:
                        self._last_eco_check_time = current_time # Reset check timer, proceed to capture

                # 3. Normal Frame Skip: Skip based on effective_frame_skip_rate
                elif (frame_counter % self.effective_frame_skip_rate) != 0:
                    time.sleep(0.01) # Short sleep to yield CPU
                    skip_capture_and_handle = True

                # If skipping, restart loop
                if skip_capture_and_handle:
                    continue

                # --- Screen Capture and Processing ---
                screen_bgr = self.capture_manager.capture_frame(region=self.recognition_area)
                if screen_bgr is None:
                    self._log("log_capture_failed")
                    time.sleep(1.0) # Wait longer if capture fails
                    continue

                # Apply lightweight scaling if needed
                if self.effective_capture_scale != 1.0:
                    screen_bgr = cv2.resize(screen_bgr, None,
                                            fx=self.effective_capture_scale,
                                            fy=self.effective_capture_scale,
                                            interpolation=cv2.INTER_AREA) # Use INTER_AREA for downscaling

                # Keep copy for hashing, convert to grayscale
                self.latest_frame_for_hash = screen_bgr.copy()
                screen_gray = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)

                # Convert to UMat for OpenCL if enabled
                screen_bgr_umat, screen_gray_umat = None, None
                if OPENCL_AVAILABLE and cv2.ocl.useOpenCL():
                    try:
                        screen_bgr_umat = cv2.UMat(screen_bgr)
                        screen_gray_umat = cv2.UMat(screen_gray)
                    except Exception as e:
                        self.logger.log("log_umat_convert_failed", str(e))

                # --- Match Finding and State Handling ---
                screen_data = (screen_bgr, screen_gray, screen_bgr_umat, screen_gray_umat)
                # Find potential matches (used for Eco mode exit check)
                # ★★★ 修正: _find_matches_for_eco_check に current_state を渡す ★★★
                all_matches = self._find_matches_for_eco_check(screen_data, current_state)

                # If in Eco Mode and matches are found, exit Eco Mode immediately
                if self.is_eco_cooldown_active and all_matches:
                    self.last_successful_click_time = time.time() # Reset Eco timer
                    self._log("log_eco_mode_resumed", force=True) # Log resumption

                # Delegate handling to the current state object
                # ★★★ 修正: self.state.handle -> current_state.handle ★★★
                current_state.handle(current_time, screen_data, last_match_time_map, pre_matches=all_matches)

            except Exception as e:
                # ★★★ 修正: レースコンディション起因の AttributeError を特別にハンドル ★★★
                if isinstance(e, AttributeError) and "'NoneType' object has no attribute 'handle'" in str(e):
                     self.logger.log("[CRITICAL] Race condition detected (state became None unexpectedly). Loop will restart/exit.")
                else:
                    # Log general errors in the loop
                    self.logger.log("log_error_monitoring_loop", str(e))
                time.sleep(1.0) # Wait after error
            finally:
                # Short sleep in each loop iteration to prevent busy-waiting
                time.sleep(0.01)


    # ★★★ 修正: current_state を引数として受け取る ★★★
    def _find_matches_for_eco_check(self, screen_data, current_state):
        """Finds matches relevant for checking if Eco Mode should be exited."""
        def filter_cache_for_eco(cache):
            # Exclude items in 'excluded' or 'priority_timer' folders
            return {
                p: d for p, d in cache.items()
                if d.get('folder_mode') not in ['excluded', 'priority_timer']
            }

        # Filter caches
        active_normal_cache = filter_cache_for_eco(self.normal_template_cache)
        # Find matches in normal cache
        normal_matches = self._find_best_match(*screen_data, active_normal_cache)

        # Also check backup triggers only if in IdleState
        # ★★★ 修正: self.state -> current_state ★★★
        if isinstance(current_state, IdleState):
            active_backup_cache = filter_cache_for_eco(self.backup_template_cache)
            backup_trigger_matches = self._find_best_match(*screen_data, active_backup_cache)
            if backup_trigger_matches:
                # Combine results if backup triggers found
                normal_matches.extend(backup_trigger_matches)

        return normal_matches


    def check_screen_stability(self) -> bool:
        """Checks if the screen content (top-left corner) is stable based on image hashing."""
        if not hasattr(self, 'latest_frame_for_hash') or self.latest_frame_for_hash is None:
            return False # Cannot check if no frame available

        h, w, _ = self.latest_frame_for_hash.shape
        # Skip check if capture area is too small (assume stable)
        if h < 64 or w < 64:
            self._log("log_stability_check_skip_size", force=True)
            return True

        # Use top-left 64x64 region for hashing
        roi = self.latest_frame_for_hash[0:64, 0:64]
        current_hash = calculate_phash(roi)

        if current_hash is None:
            return False # Hash calculation failed

        # Add hash to history deque
        self.screen_stability_hashes.append(current_hash)

        # Need enough history to compare
        if len(self.screen_stability_hashes) < self.screen_stability_hashes.maxlen:
            self._log("log_stability_check_history_low",
                      len(self.screen_stability_hashes),
                      self.screen_stability_hashes.maxlen,
                      force=True)
            return False # Not enough data yet

        # Compare oldest and newest hashes
        threshold = self.app_config.get('screen_stability_check', {}).get('threshold', 8)
        hash_diff = self.screen_stability_hashes[-1] - self.screen_stability_hashes[0] # Hamming distance

        # Log debug info (optional, can be noisy)
        # self._log("log_stability_check_debug",
        #           str(self.screen_stability_hashes[-1]),
        #           str(self.screen_stability_hashes[0]),
        #           hash_diff, threshold, force=True)

        # Return True if difference is within threshold (stable)
        return hash_diff <= threshold

    def _check_and_activate_timer_priority_mode(self):
        """Checks if any timer priority folders should become active."""
        current_time = time.time()
        for folder_path, activation_time in list(self.priority_timers.items()): # Iterate over copy
            if current_time >= activation_time:
                # Transition to PriorityState for this folder
                self.transition_to_timer_priority(folder_path)
                # Remove from timers dict as it's now active
                # (PriorityState will handle reset if needed)
                # del self.priority_timers[folder_path] # Let PriorityState handle timer reset logic
                break # Only activate one per cycle


    def _process_matches_as_sequence(self, all_matches, current_time, last_match_time_map):
        """Processes found matches, clicks the highest priority one respecting intervals."""
        if not all_matches:
            return False # No matches found

        # Filter matches that are ready to be clicked (past interval + debounce)
        clickable = []
        for m in all_matches:
            path = m['path']
            settings = m['settings']
            interval = settings.get('interval_time', 1.5)
            debounce = settings.get('debounce_time', 0.0) if self._last_clicked_path == path else 0.0
            last_clicked = last_match_time_map.get(path, 0) # Default to 0 if never clicked

            if current_time - last_clicked > (interval + debounce):
                clickable.append(m)

        # If no clickable matches, return False
        if not clickable:
            return False

        # Find the best match among clickable ones (lowest interval, then highest confidence)
        target_match = min(clickable, key=lambda m: (m['settings'].get('interval_time', 1.5), -m['confidence']))

        # --- Stability Check ---
        is_stability_check_enabled = self.app_config.get('screen_stability_check',{}).get('enabled',True)
        # Check stability only if enabled AND not currently in Eco Mode cooldown
        if is_stability_check_enabled and not self.is_eco_cooldown_active:
            if not self.check_screen_stability():
                self._log("log_stability_hold_click")
                self.updateStatus.emit("unstable", "orange")
                # Update last successful click time to prevent immediate Eco Mode entry
                self.last_successful_click_time = current_time
                return False # Do not click if unstable

        # If stable or check skipped, ensure status is "Monitoring" (unless in Eco)
        if not self.is_eco_cooldown_active:
             self.updateStatus.emit("monitoring", "blue")

        # --- Execute Click ---
        if not self.is_monitoring: return False # Double check if stopped during processing
        # Execute the click action
        self._execute_click(target_match)
        # Update the last click time for this specific image path
        last_match_time_map[target_match['path']] = time.time()
        return True # Click was executed

    def _execute_final_backup_click(self, target_path):
        """Captures screen and attempts to click the specified backup target."""
        # Capture a fresh frame specifically for the backup click
        screen_bgr = self.capture_manager.capture_frame(region=self.recognition_area)
        if screen_bgr is None:
            self._log("log_backup_click_failed_capture", force=True)
            return

        # Prepare image data (Grayscale, UMat if needed)
        screen_gray = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)
        screen_bgr_umat, screen_gray_umat = None, None
        if OPENCL_AVAILABLE and cv2.ocl.useOpenCL():
            try:
                screen_bgr_umat = cv2.UMat(screen_bgr)
                screen_gray_umat = cv2.UMat(screen_gray)
            except Exception as e:
                self.logger.log("log_backup_click_failed_umat", str(e))

        # Get the cached data for the specific backup target
        cache_item = self.backup_template_cache.get(target_path)
        if not cache_item:
            self._log("log_backup_click_failed_cache", Path(target_path).name, force=True)
            return

        # Perform match finding only for this target
        screen_data = (screen_bgr, screen_gray, screen_bgr_umat, screen_gray_umat)
        matches = self._find_best_match(*screen_data, {target_path: cache_item})

        # If found, execute click on the best match (highest confidence)
        if matches:
            best_match = max(matches, key=lambda m: m['confidence'])
            self._execute_click(best_match)
        else:
            self._log("log_backup_click_failed_not_found", Path(target_path).name, force=True)


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
                    else:
                        # Submit task to thread pool for parallel execution
                        if self.thread_pool:
                            future = self.thread_pool.submit(_match_template_task, screen_image, task_data, s_shape, t_shape)
                            futures.append(future)

        # Collect results from thread pool futures if not using OpenCL
        if not use_cl:
            for f in futures:
                try:
                    match_result = f.result() # Wait for task completion and get result
                    if match_result:
                        results.append(match_result)
                except Exception as e:
                     self.logger.log("Error getting result from match thread: %s", str(e))


        # If no matches found above threshold, return empty list
        if not results:
            return []

        # Find the single best match overall (highest confidence)
        best_match_overall = max(results, key=lambda r: r['confidence'])

        # --- Update Best Scale Cache ---
        best_match_path = best_match_overall['path']
        best_match_scale = best_match_overall['scale']
        # Determine which cache the best match belongs to
        target_cache = None
        if best_match_path in self.normal_template_cache:
            target_cache = self.normal_template_cache
        elif best_match_path in self.backup_template_cache:
             target_cache = self.backup_template_cache

        # If the best match's scale was being searched, cache it
        if target_cache:
             with self.cache_lock:
                 cache_item = target_cache.get(best_match_path)
                 if cache_item and cache_item['best_scale'] is None:
                      cache_item['best_scale'] = best_match_scale
                      # Log the newly found best scale
                      self._log("log_best_scale_found", Path(best_match_path).name,
                                f"{best_match_scale:.3f}", f"{best_match_overall['confidence']:.2f}")
                      # Emit signal to update UI label
                      self.bestScaleFound.emit(best_match_path, best_match_scale)

        # Return all matches found (above threshold)
        return results


    def _execute_click(self, match_info):
        """Executes the click action using ActionManager."""
        result = self.action_manager.execute_click(
            match_info,
            self.recognition_area,
            self.target_hwnd,
            self.effective_capture_scale
        )
        if result and result.get('success'):
            self._click_count += 1
            self._last_clicked_path = result.get('path')
            self.last_successful_click_time = time.time() # Update time for Eco Mode
            self.clickCountUpdated.emit(self._click_count) # Notify UI/Monitor

    def set_recognition_area(self, method: str):
        """
        認識エリアまたは画像キャプチャのための選択プロセスを開始します。
        _is_capturing_for_registration フラグに応じて動作を切り替えます。
        """
        self.selectionProcessStarted.emit() # Notify UI to hide
        self.ui_manager.hide()
        if self.performance_monitor:
            self.performance_monitor.hide()
        self._stop_global_mouse_listener() # Stop listener during selection

        if method == "rectangle":
            # 認識範囲設定モードの場合のみ、ウィンドウ関連設定をリセット
            if not self._is_capturing_for_registration:
                self.target_hwnd = None
                self.current_window_scale = None
                self.windowScaleCalculated.emit(0.0) # Notify UI
                self.logger.log("log_rec_area_set_rect")
            else:
                self.logger.log("log_capture_area_set_rect") # 画像キャプチャのための矩形選択

            # Create and show overlay for rectangle selection
            self.selection_overlay = SelectionOverlay()
            self.selection_overlay.selectionComplete.connect(self._areaSelectedForProcessing.emit)
            self.selection_overlay.selectionCancelled.connect(self._on_selection_cancelled)
            self.selection_overlay.showFullScreen()

        elif method == "window":
            # 認識範囲設定モードの場合のみ、ログを記録
            if not self._is_capturing_for_registration:
                self.logger.log("log_rec_area_set_window")
            else:
                self.logger.log("log_capture_area_set_window") # 画像キャプチャのためのウィンドウ選択

            # Start listeners for window click and ESC key
            self.window_selection_listener = WindowSelectionListener(self._handle_window_click_for_selection)
            self.window_selection_listener.start()
            self.keyboard_selection_listener = keyboard.Listener(on_press=self._on_key_press_for_selection)
            self.keyboard_selection_listener.start()

    def _on_selection_cancelled(self):
        """Handles cancellation of the selection process."""
        self.logger.log("log_selection_cancelled")
        self._is_capturing_for_registration = False # Reset flag if capturing
        # Clean up selection tools
        if hasattr(self, 'selection_overlay') and self.selection_overlay:
            self.selection_overlay.close()
            self.selection_overlay = None
        if self.window_selection_listener:
            self.window_selection_listener.stop()
            self.window_selection_listener = None
        if self.keyboard_selection_listener:
            self.keyboard_selection_listener.stop()
            self.keyboard_selection_listener = None
        # Notify UI and restore visibility
        self.selectionProcessFinished.emit()
        self._show_ui_safe() # 必ずUIを再表示
        # パフォーマンスモニタの復元は UIManager 側で行う

        # リスナー再開を遅延させ、タイマーセット直前にログを追加
        self.logger.log("[DEBUG] Scheduling listener restart after cancellation (150ms delay)...")
        QTimer.singleShot(150, self._start_global_mouse_listener)

    def _on_key_press_for_selection(self, key):
        """Handles ESC key press during window selection."""
        if key == keyboard.Key.esc:
            self.logger.log("log_selection_cancelled_key")
            # Stop listeners safely
            if self.window_selection_listener: self.window_selection_listener.stop()
            if self.keyboard_selection_listener: self.keyboard_selection_listener.stop()
            # Use signal to trigger cancellation cleanup on main thread
            # Connect temporarily, emit, then disconnect to avoid multiple calls
            self._showUiSignal.connect(self._on_selection_cancelled)
            self._showUiSignal.emit()
            try: # Disconnect might fail if already handled
                 self._showUiSignal.disconnect(self._on_selection_cancelled)
            except RuntimeError: pass
            return False # Stop keyboard listener


    def _handle_window_click_for_selection(self, x, y):
        """Callback for when a window is clicked during selection."""
        # Stop keyboard listener immediately
        if self.keyboard_selection_listener:
            self.keyboard_selection_listener.stop()
            self.keyboard_selection_listener = None
        # Call platform-specific handler
        if sys.platform == 'win32' and win32gui:
            self._handle_window_click_for_selection_windows(x, y)
        elif sys.platform.startswith('linux'):
             self._handle_window_click_for_selection_linux(x, y)
        else:
             self.logger.log("Window selection not supported on this platform.")
             self._on_selection_cancelled() # Cancel if not supported
             return # Skip restarting mouse listener

        # リスナーの再開は handle_area_selection または _on_selection_cancelled で行う

    def _handle_window_click_for_selection_windows(self, x, y):
        """Gets window handle and client area rectangle on Windows."""
        try:
            hwnd = win32gui.WindowFromPoint((x, y))
            if not hwnd:
                self._on_selection_cancelled()
                return # Clicked on desktop or invalid area

            # 認識範囲設定モードの場合のみ target_hwnd を設定
            if not self._is_capturing_for_registration:
                self.target_hwnd = hwnd
                # If using DXCam, set its target HWND
                if 'dxcam' in sys.modules and hasattr(self.capture_manager, 'dxcam_sct') and self.capture_manager.dxcam_sct:
                    try:
                        self.capture_manager.dxcam_sct.target_hwnd = hwnd
                    except Exception as dxcam_err:
                         self.logger.log(f"Error setting DXCam target HWND: {dxcam_err}")


            # Get client area rectangle relative to screen
            client_rect_win = win32gui.GetClientRect(hwnd)
            left, top = win32gui.ClientToScreen(hwnd, (0, 0))
            right = left + client_rect_win[2]
            bottom = top + client_rect_win[3]

            # Basic validation
            if right <= left or bottom <= top:
                self.logger.log("log_window_invalid_rect", left, top, right, bottom)
                self._on_selection_cancelled()
                return

            # Clamp coordinates to screen bounds (using pyautogui for screen size)
            try:
                import pyautogui
                screen_width, screen_height = pyautogui.size()
                rect = (max(0, left), max(0, top), min(screen_width, right), min(screen_height, bottom))
            except ImportError:
                 # Fallback if pyautogui not available (less accurate clamping)
                 rect = (max(0, left), max(0, top), right, bottom)


            # If just capturing for registration, emit coords and finish
            if self._is_capturing_for_registration:
                self._areaSelectedForProcessing.emit(rect)
                self.selectionProcessFinished.emit() # ここでUIを復元させるトリガー
                # UI表示は selectionProcessFinished を受けた UIManager が行う
                return

            # Process for setting recognition area (check base size, calculate scale)
            title = win32gui.GetWindowText(hwnd)
            self._pending_window_info = {
                "title": title,
                "dims": {'width': rect[2] - rect[0], 'height': rect[3] - rect[1]},
                "rect": rect
            }
            # Ask to save base size if window title is new
            if title and title not in self.config_manager.load_window_scales():
                self.askToSaveWindowBaseSizeSignal.emit(title)
            else:
                # If window known, proceed without asking to save base size
                self.process_base_size_prompt_response(save_as_base=False)

        except Exception as e:
            self.logger.log("log_window_get_rect_failed", str(e))
            # 認識範囲設定モードの場合のみ target_hwnd を None に
            if not self._is_capturing_for_registration:
                self.target_hwnd = None
            # Show UI and finish selection process on error
            self._showUiSignal.emit()
            self.selectionProcessFinished.emit()

    def _handle_window_click_for_selection_linux(self, x, y):
        """Gets window ID and geometry using xdotool and xwininfo on Linux."""
        # Check if required tools are installed
        missing_tools = [tool for tool in ['xdotool', 'xwininfo'] if not shutil.which(tool)]
        if missing_tools:
            self.logger.log("log_linux_tool_not_found", ', '.join(missing_tools))
            self._showUiSignal.emit(); self.selectionProcessFinished.emit()
            return

        try:
            # Get window ID at mouse location
            id_proc = subprocess.run(['xdotool', 'getmouselocation'], capture_output=True, text=True, check=True, timeout=2)
            # Extract window ID (handle potential variations in output format)
            window_id_line = next((line for line in id_proc.stdout.strip().split() if line.startswith('window:')), None)
            if not window_id_line: raise ValueError("Could not find window ID in xdotool output.")
            window_id = window_id_line.split(':')[1]


            # Get window info using ID
            info_proc = subprocess.run(['xwininfo', '-id', window_id], capture_output=True, text=True, check=True, timeout=2)
            # Parse xwininfo output into a dictionary
            info = {}
            for line in info_proc.stdout.split('\n'):
                 if ':' in line:
                     parts = line.split(':', 1)
                     key = parts[0].strip()
                     value = parts[1].strip()
                     info[key] = value

            # Extract necessary geometry and title
            left = int(info['Absolute upper-left X'])
            top = int(info['Absolute upper-left Y'])
            width = int(info['Width'])
            height = int(info['Height'])
            # Extract title (usually within quotes)
            title_part = info.get('xwininfo', '')
            title = title_part.split('"')[1] if '"' in title_part else f"Window (ID: {window_id})"

            # Basic validation
            if width <= 0 or height <= 0:
                self.logger.log("log_linux_window_invalid_rect")
                self._on_selection_cancelled()
                return

            # Clamp coordinates to screen bounds
            try:
                import pyautogui
                screen_width, screen_height = pyautogui.size()
                rect = (max(0, left), max(0, top), min(screen_width, left + width), min(screen_height, top + height))
            except ImportError:
                 rect = (max(0, left), max(0, top), left + width, top + height)


            # If just capturing for registration, emit coords and finish
            if self._is_capturing_for_registration:
                self._areaSelectedForProcessing.emit(rect)
                self.selectionProcessFinished.emit() # ここでUIを復元させるトリガー
                return

            # Process for setting recognition area
            self._pending_window_info = {
                "title": title,
                "dims": {'width': width, 'height': height},
                "rect": rect
            }
            if title and title not in self.config_manager.load_window_scales():
                self.askToSaveWindowBaseSizeSignal.emit(title)
            else:
                self.process_base_size_prompt_response(save_as_base=False)

        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, KeyError, Exception) as e:
            self.logger.log("log_linux_window_get_rect_failed", str(e))
            # Show UI and finish on error
            self._showUiSignal.emit(); self.selectionProcessFinished.emit()


    def process_base_size_prompt_response(self, save_as_base: bool):
        """Processes the user's response to the 'save base size' prompt."""
        try:
            # Ensure pending info exists
            if not (info := self._pending_window_info):
                 self.logger.log("Warning: process_base_size_prompt_response called with no pending info.")
                 # Attempt to gracefully finish selection process if possible
                 self._showUiSignal.emit(); self.selectionProcessFinished.emit()
                 return

            title, current_dims, rect = info['title'], info['dims'], info['rect']

            if save_as_base:
                # Save current dimensions as base size
                scales_data = self.config_manager.load_window_scales()
                scales_data[title] = current_dims
                self.config_manager.save_window_scales(scales_data)
                self.current_window_scale = 1.0 # Current window is now the base
                self.logger.log("log_window_base_size_saved", title)
                self.windowScaleCalculated.emit(1.0)
                # Proceed to set area
                self._areaSelectedForProcessing.emit(rect)

            elif title and title in (scales_data := self.config_manager.load_window_scales()):
                # Window known, calculate scale relative to saved base size
                base_dims = scales_data[title]
                # Calculate scale based on width (avoid division by zero)
                calc_scale = current_dims['width'] / base_dims['width'] if base_dims['width'] > 0 else 1.0

                # Treat scales very close to 1.0 as 1.0 (avoids minor precision issues)
                if 0.995 <= calc_scale <= 1.005:
                    self.current_window_scale = 1.0
                    self.logger.log("log_window_scale_calc", title, f"{calc_scale:.3f}")
                    self.windowScaleCalculated.emit(1.0)
                    self._areaSelectedForProcessing.emit(rect)
                else:
                    # If scale is significantly different, ask user whether to apply it
                    self._pending_scale_prompt_info = {**info, 'calculated_scale': calc_scale}
                    self.askToApplyWindowScaleSignal.emit(calc_scale)
                    return # Wait for user response from apply scale prompt

            else:
                # Window not known and user chose not to save base size, or title is empty
                self.current_window_scale = None # No scale applied
                self.windowScaleCalculated.emit(0.0)
                self._areaSelectedForProcessing.emit(rect)

        except Exception as e:
            self.logger.log("log_error_base_size_process", str(e))
            # Ensure UI is shown and process finishes even on error
            if not self._pending_scale_prompt_info: # Only finish if not waiting for apply prompt
                 self._pending_window_info = None
                 self._showUiSignal.emit(); self.selectionProcessFinished.emit()

        finally:
             # Clean up pending info only if not waiting for the apply scale prompt
            if not self._pending_scale_prompt_info:
                self._pending_window_info = None
                # UI表示は handle_area_selection で行うため、ここでは emit しない
                self.selectionProcessFinished.emit() # Finish シグナルのみ


    def process_apply_scale_prompt_response(self, apply_scale: bool):
        """Processes the user's response to the 'apply calculated scale' prompt."""
        try:
             # Ensure pending info exists
            if not (info := self._pending_scale_prompt_info):
                self.logger.log("Warning: process_apply_scale_prompt_response called with no pending info.")
                # Attempt to gracefully finish
                self._pending_window_info = None
                self._showUiSignal.emit(); self.selectionProcessFinished.emit()
                return

            scale, rect = info['calculated_scale'], info['rect']

            if apply_scale:
                # Enable 'Use Window Scale' setting in UI and config
                self.ui_manager.app_config['auto_scale']['use_window_scale'] = True
                self.ui_manager.auto_scale_widgets['use_window_scale'].setChecked(True)
                # Trigger settings save and notify core engine
                self.ui_manager.on_app_settings_changed()

                # Apply the calculated scale
                self.current_window_scale = scale
                self.logger.log("log_window_scale_applied", f"{scale:.3f}")
            else:
                # Do not apply scale
                self.current_window_scale = None
                self.logger.log("log_window_scale_not_applied", f"{scale:.3f}")

            # Notify UI about the final scale and set the recognition area
            self.windowScaleCalculated.emit(self.current_window_scale if self.current_window_scale is not None else 0.0)
            self._areaSelectedForProcessing.emit(rect)

        except Exception as e:
            self.logger.log("log_error_apply_scale_process", str(e))
        finally:
            # Clean up pending info and finish selection process
            self._pending_scale_prompt_info = None
            self._pending_window_info = None
            # UI表示は handle_area_selection で行うため、ここでは emit しない
            self.selectionProcessFinished.emit() # Finish シグナルのみ

    def handle_area_selection(self, coords):
        """Handles the final selected coordinates (from overlay or window)."""
        if self._is_capturing_for_registration:
            # If capturing for registration, save the image after a short delay
            self._is_capturing_for_registration = False
            # Use QTimer to ensure capture happens after overlay is closed
            QTimer.singleShot(100, lambda: self._save_captured_image(coords))
            # UI表示は _save_captured_image 内で行う
        else:
            # If setting recognition area, store coords and update preview
            self.recognition_area = coords
            self.logger.log("log_rec_area_set", str(coords))
            self._update_rec_area_preview()
            # Finish selection process
            self.selectionProcessFinished.emit()
            # 認識範囲設定完了後にUIを再表示
            self._show_ui_safe()

        # Clean up overlay reference if it exists
        if hasattr(self, 'selection_overlay'):
            self.selection_overlay = None

        # ★★★ リスナー再開を遅延させ、タイマーセット直前にログを追加 ★★★
        self.logger.log("[DEBUG] Scheduling listener restart after selection completion (150ms delay)...")
        QTimer.singleShot(150, self._start_global_mouse_listener)


    def _get_filename_from_user(self):
        """Prompts user for filename using platform-appropriate dialog."""
        lm = self.locale_manager.tr
        if sys.platform == 'win32':
            # Use QInputDialog on Windows
            return QInputDialog.getText(
                self.ui_manager,
                lm("dialog_filename_prompt_title"),
                lm("dialog_filename_prompt_text")
            )
        else:
            # Use zenity on Linux if available
            if not shutil.which('zenity'):
                QMessageBox.warning(self.ui_manager, lm("error_title_zenity_missing"), lm("error_message_zenity_missing"))
                return None, False
            try:
                cmd = ['zenity', '--entry', f'--title={lm("zenity_title")}', f'--text={lm("zenity_text")}']
                # Run zenity and capture output
                res = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=60) # Add timeout
                # Return filename and True if OK clicked, else None and False
                return (res.stdout.strip(), res.returncode == 0)
            except (subprocess.TimeoutExpired, Exception) as e:
                QMessageBox.critical(self.ui_manager, lm("error_title_zenity_failed"), lm("error_message_zenity_failed", str(e)))
                return None, False


    def _save_captured_image(self, region_coords):
        """Coordinates capturing image and prompting user for filename."""
        try:
            # Hide UI elements briefly during capture
            self.ui_manager.hide()
            if self.performance_monitor: self.performance_monitor.hide()
            # Use QTimer to delay capture until UI is hidden
            QTimer.singleShot(100, lambda: self._capture_and_prompt_for_save(region_coords))
        except Exception as e:
            # Show error and restore UI if preparation fails
            QMessageBox.critical(self.ui_manager, self.locale_manager.tr("error_title_capture_prepare_failed"), self.locale_manager.tr("error_message_capture_prepare_failed", str(e)))
            self._show_ui_safe()
            self.selectionProcessFinished.emit()


    def _capture_and_prompt_for_save(self, region_coords):
        """Captures the image, shows preview, and asks for filename."""
        try:
            # Capture the selected region
            captured_image = self.capture_manager.capture_frame(region=region_coords)

            if captured_image is None or captured_image.size == 0:
                 # Handle capture failure
                 self._show_ui_safe() # Show UI to display error
                 QMessageBox.warning(self.ui_manager, self.locale_manager.tr("warn_title_capture_failed"), self.locale_manager.tr("warn_message_capture_failed"))
                 self.selectionProcessFinished.emit()
                 return # Stop if capture failed

            # Show UI, ensure preview tab is active, update preview, process events
            self._show_ui_safe()
            if hasattr(self.ui_manager, 'switch_to_preview_tab'):
                self.ui_manager.switch_to_preview_tab() # プレビュータブに切り替え
            self.ui_manager.update_image_preview(captured_image, settings_data=None)
            QApplication.processEvents() # Force UI update

            # 遅延後にファイル名入力ダイアログを表示する
            QTimer.singleShot(50, lambda: self._prompt_and_save_image(captured_image))

        except Exception as e:
            # Show error and restore UI if this stage fails
            QMessageBox.critical(self.ui_manager, self.locale_manager.tr("error_title_capture_save_failed"), self.locale_manager.tr("error_message_capture_save_failed", str(e)))
            self._show_ui_safe()
            self.selectionProcessFinished.emit()

    def _prompt_and_save_image(self, captured_image):
        """Prompts for filename and saves the image."""
        try:
            # Prompt user for filename (after preview is hopefully shown)
            file_name, ok = self._get_filename_from_user()

            if ok and file_name:
                self.ui_manager.set_tree_enabled(False) # Disable tree during save
                save_path = self.config_manager.base_dir / f"{file_name}.png"
                # Check for overwrite
                if save_path.exists():
                    lm = self.locale_manager.tr
                    reply = QMessageBox.question(
                        self.ui_manager,
                        lm("confirm_overwrite_title"),
                        lm("confirm_overwrite_message", save_path.name),
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No
                    )
                    if reply == QMessageBox.StandardButton.No:
                        self.ui_manager.set_tree_enabled(True) # Re-enable tree
                        self.selectionProcessFinished.emit() # Finish process
                        self._show_ui_safe() # Ensure UI visible if cancelled here
                        return # Stop if overwrite cancelled

                # Submit save task to thread pool
                if self.thread_pool:
                    self.thread_pool.submit(self._save_image_task, captured_image, save_path).add_done_callback(self._on_save_image_done)
                else:
                     # Fallback if pool not available
                     self._on_save_image_done(None, success=False, message=self.locale_manager.tr("Error: Thread pool unavailable for saving."))
                     self.ui_manager.set_tree_enabled(True)
                     self.selectionProcessFinished.emit()
                     self._show_ui_safe()

            else: # User cancelled filename input
                self.selectionProcessFinished.emit()
                # If cancelled, ensure UI is visible
                self._show_ui_safe()

        except Exception as e:
             # Show error and restore UI if saving process fails
            QMessageBox.critical(self.ui_manager, self.locale_manager.tr("error_title_capture_save_failed"), self.locale_manager.tr("error_message_capture_save_failed", str(e)))
            self._show_ui_safe()
            self.selectionProcessFinished.emit()


    def _save_image_task(self, image, save_path):
        """Saves the captured image and its initial settings (executed in thread pool)."""
        try:
            # Encode image to PNG and save to file (handling non-ASCII paths)
            is_success, buffer = cv2.imencode('.png', image)
            if not is_success: raise IOError("cv2.imencode failed")
            buffer.tofile(str(save_path)) # Use tofile for non-ASCII paths

            # Create default settings (point click enabled by default)
            settings = self.config_manager.load_item_setting(Path()) # Get defaults
            settings['image_path'] = str(save_path) # Store path in settings
            settings['point_click'] = True # Default to point click enabled

            # Save initial settings JSON
            self.config_manager.save_item_setting(save_path, settings)
            # Add item to main order list
            self.config_manager.add_item(save_path)

            # Return success status and translated message
            return True, self.locale_manager.tr("log_image_saved", str(save_path))
        except Exception as e:
            # Return failure status and translated error message
            return False, self.locale_manager.tr("log_image_save_failed", str(e))


    def _on_save_image_done(self, future, success=None, message=None):
        """Callback executed after image save task finishes."""
        try:
             # If called from future, get results
            if future:
                 success, message = future.result()

            if success:
                self._log(message) # Log the translated success message
                # Rebuild cache to include the new image
                if self.thread_pool:
                     self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
                # If pool unavailable, cache won't update immediately, just re-enable tree
                else:
                     self.ui_manager.set_tree_enabled(True)
            else:
                # Show error message if save failed
                QMessageBox.critical(self.ui_manager, self.locale_manager.tr("error_title_image_save_failed"), message)
                self.ui_manager.set_tree_enabled(True) # Re-enable tree on failure

        except Exception as e:
             # Handle exceptions from getting future result
             QMessageBox.critical(self.ui_manager, self.locale_manager.tr("error_title_image_save_failed"), f"Error processing save result: {e}")
             self.ui_manager.set_tree_enabled(True)
        finally:
            # Always emit finished signal, unless cache build is pending
            if not (future and success and self.thread_pool):
                 self.selectionProcessFinished.emit()
                 # Ensure UI is visible after save process completes (success or fail)
                 self._show_ui_safe()


    def clear_recognition_area(self):
        """Clears the current recognition area and related settings."""
        self.recognition_area = None
        self.current_window_scale = None
        self.target_hwnd = None
        self.windowScaleCalculated.emit(0.0) # Notify UI
        # Reset DXCam target if applicable
        if 'dxcam' in sys.modules and hasattr(self.capture_manager, 'dxcam_sct') and self.capture_manager.dxcam_sct:
            try:
                self.capture_manager.dxcam_sct.target_hwnd = None
            except Exception as dxcam_err:
                 self.logger.log(f"Error resetting DXCam target HWND: {dxcam_err}")

        self.logger.log("log_rec_area_cleared")
        self.updateRecAreaPreview.emit(None) # Clear preview


    def _update_rec_area_preview(self):
        """Updates the recognition area preview tab with the current area content."""
        img = None
        if self.recognition_area:
             try:
                 img = self.capture_manager.capture_frame(region=self.recognition_area)
             except Exception as e:
                  self.logger.log(f"Error capturing for rec area preview: {e}")
        self.updateRecAreaPreview.emit(img) # Emit captured image or None


    def get_backup_click_countdown(self) -> float:
        """Returns the remaining time for backup click countdown, or -1.0 if not active."""
        if isinstance(self.state, CountdownState):
            return self.state.get_remaining_time()
        return -1.0
