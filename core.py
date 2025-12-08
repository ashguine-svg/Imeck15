# core.py
# 認識範囲選択、ウィンドウ検出、画像保存処理を担当
# ★★★ 修正: タイマースケジュール構築ロジックを「絶対時刻指定」に対応 ★★★
# ★★★ 修正: 日またぎ判定（現在時刻より前なら明日の時刻とする）を実装 ★★★
# ★★★ 修正: enabledフラグによるフィルタリングを適用 ★★★

import sys
import threading
import time
import cv2
import numpy as np
import os
import psutil
import subprocess
from datetime import datetime, timedelta # 日付計算用に追加
from PySide6.QtCore import QObject, Signal, Slot, Qt
from PySide6.QtWidgets import QMessageBox, QApplication 
from pathlib import Path
from pynput import mouse
from concurrent.futures import ThreadPoolExecutor
from threading import Timer

from collections import deque
from PIL import Image
import imagehash

from action import ActionManager
from template_manager import TemplateManager
from environment_tracker import EnvironmentTracker
from monitoring_states import IdleState, PriorityState, CountdownState, SequencePriorityState, TimerStandbyState

from core_monitoring import MonitoringProcessor
from core_selection import SelectionHandler

if sys.platform == 'win32':
    try:
        import win32gui
        import win32process
    except ImportError:
        pass

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
    
    capturedImageReadyForPreview = Signal(np.ndarray)
    captureFailedSignal = Signal()
    saveImageCompleted = Signal(bool, str)
    quickCaptureRequested = Signal()

    def __init__(self, ui_manager, capture_manager, config_manager, logger, locale_manager):
        super().__init__()
        self.ui_manager = ui_manager
        self.capture_manager = capture_manager
        self.config_manager = config_manager
        self.logger = logger
        self.locale_manager = locale_manager

        self.action_manager = ActionManager(self.logger)
        self.template_manager = TemplateManager(self.config_manager, self.logger)
        self.environment_tracker = EnvironmentTracker(self, self.config_manager, self.logger)
        
        cpu_cores = os.cpu_count() or 8
        max_thread_limit = 4 
        worker_threads = min(max(1, cpu_cores // 4), max_thread_limit)
        self.worker_threads = worker_threads
        self.logger.log("log_info_cores", cpu_cores, self.worker_threads, max_thread_limit)
        self.thread_pool = ThreadPoolExecutor(max_workers=self.worker_threads)
        self.cache_lock = threading.Lock()
        
        self.monitoring_processor = MonitoringProcessor(self)
        self.selection_handler = SelectionHandler(self)
        
        self.performance_monitor = None
        try:
            self.process = psutil.Process()
            self.process.cpu_percent(interval=None) 
        except Exception as e:
            self.logger.log(f"log_error_psutil_init: {e}")
            self.process = None
        
        self.logger.log(OPENCL_STATUS_MESSAGE)

        self.is_monitoring = False
        self._monitor_thread = None
        self._click_count = 0
        
        self.start_time = time.time()
        self.last_stats_emit_time = 0
        self.current_fps = 0.0

        self.normal_template_cache = {}
        self.backup_template_cache = {}
        
        # タイマーセッション管理用 (絶対時刻制になったため T0 は不要だが、セッション状態として保持)
        self.timer_session_active = False 
        self.timer_schedule_cache = {}
        
        self.folder_cooldowns = {}

        self.state = None
        self.state_lock = threading.RLock()

        self._last_clicked_path = None

        self.recognition_area = None
        self._is_capturing_for_registration = False
        self.current_image_path = None
        self.current_image_settings = None
        self.current_image_mat = None

        self.target_hwnd = None

        self.priority_timers = {}
        self.folder_children_map = {}
        
        self.click_timer = None
        self.last_right_click_time = 0
        self.right_click_count = 0
        self.CLICK_INTERVAL = 0.3

        self.mouse_listener = None
        self._start_global_mouse_listener()

        self._showUiSignal.connect(self._show_ui_safe)
        
        self._areaSelectedForProcessing.connect(self.selection_handler.handle_area_selection)
        
        self.startMonitoringRequested.connect(self.start_monitoring)
        self.stopMonitoringRequested.connect(self.stop_monitoring)

        self.quickCaptureRequested.connect(self._perform_quick_capture)

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
        
        self._is_reinitializing_display = False 

        self._lifecycle_hook_active = False
        self._session_context = {
            'pid': None,
            'exec_path': None,
            'resource_id': None,
            'consecutive_clicks': 0 
        }
        self._recovery_in_progress = False

        self.on_app_config_changed()

        self._last_log_message = ""
        self._last_log_time = 0
        self._log_spam_filter = {"log_stability_hold_click", "log_eco_mode_standby", "log_stability_check_debug"}

        self.match_detected_at = {}
        self.consecutive_capture_failures = 0

    def capture_image_for_registration(self):
        self.selection_handler.capture_image_for_registration()

    def set_recognition_area(self, method: str):
        self.selection_handler.set_recognition_area(method)

    def clear_recognition_area(self):
        self.selection_handler.clear_recognition_area()
        self._lifecycle_hook_active = False 
        self._session_context = {'pid': None, 'exec_path': None, 'resource_id': None, 'consecutive_clicks': 0}
        self.logger.log("[INFO] Session context detached.")
        
    def process_base_size_prompt_response(self, save_as_base: bool):
        self.selection_handler.process_base_size_prompt_response(save_as_base)
        
    def process_apply_scale_prompt_response(self, apply_scale: bool):
        self.selection_handler.process_apply_scale_prompt_response(apply_scale)

    @Slot(str, np.ndarray)
    def handle_save_captured_image(self, file_name: str, captured_image: np.ndarray):
        self.selection_handler.handle_save_captured_image(file_name, captured_image)

    def check_screen_stability(self) -> bool:
        return self.monitoring_processor.check_screen_stability()

    def _check_and_activate_timer_priority_mode(self):
        current_time = time.time()
        for folder_path, activation_time in list(self.priority_timers.items()):
            if current_time >= activation_time: 
                self.transition_to_timer_priority(folder_path)
                break
            
    def _find_best_match(self, *args):
        return self.monitoring_processor._find_best_match(*args)

    def _process_matches_as_sequence(self, *args):
        return self.monitoring_processor.process_matches_as_sequence(*args)

    def _execute_click(self, *args):
        self.monitoring_processor.execute_click(*args)

    def transition_to(self, new_state):
        with self.state_lock:
            self.state = new_state
        self._last_clicked_path = None
        self.match_detected_at.clear()
        
    def transition_to_sequence_priority(self, ordered_paths, interval_sec):
        new_state = SequencePriorityState(self, ordered_paths, interval_sec)
        self.transition_to(new_state)

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

    def get_backup_click_countdown(self) -> float:
        with self.state_lock:
            if isinstance(self.state, CountdownState): 
                return self.state.get_remaining_time()
        return -1.0

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
        
        hooks_config = self.app_config.get('extended_lifecycle_hooks', {})
        if hooks_config.get('active', False):
             self.logger.log("[INFO] Extended Lifecycle Hooks: Enabled")
        
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
                self.mouse_listener.start()
            except Exception as e: self.logger.log(f"log_error_listener_start: Exception during listener.start(): {e}", str(e)); self.mouse_listener = None
        else: self.logger.log("[WARN] Mouse listener object was not None before start attempt. State issue?")

    def _stop_global_mouse_listener(self):
        if self.mouse_listener and self.mouse_listener.is_alive():
            try:
                self.mouse_listener.stop()
                time.sleep(0.1) 
            except Exception as e: 
                self.logger.log("log_warn_listener_stop", str(e))
        self.mouse_listener = None

    def _on_global_click(self, x, y, button, pressed):
        if pressed and button == mouse.Button.middle:
            if self.recognition_area is not None:
                self.quickCaptureRequested.emit()
            return

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

    @Slot()
    def _perform_quick_capture(self):
        self.logger.log("[DEBUG] Quick capture triggered via Middle Click.")
        if self.is_monitoring:
            self.logger.log("log_capture_while_monitoring") 
            self.stop_monitoring()
            self.logger.log("log_capture_proceed_after_stop")

        if self.ui_manager:
            if self.ui_manager.is_minimal_mode:
                if self.ui_manager.floating_window:
                    self.ui_manager.floating_window.hide()
            else:
                self.ui_manager.hide()
            QApplication.processEvents()
            time.sleep(0.2) 

        capture_region = None
        
        if self.target_hwnd and sys.platform == 'win32':
            try:
                client_rect = win32gui.GetClientRect(self.target_hwnd)
                left, top = win32gui.ClientToScreen(self.target_hwnd, (0, 0))
                right = left + client_rect[2]
                bottom = top + client_rect[3]
                
                screen = QApplication.primaryScreen()
                if screen:
                    geo = screen.geometry()
                    screen_w = geo.width()
                    screen_h = geo.height()
                    left = max(0, left)
                    top = max(0, top)
                    right = min(screen_w, right)
                    bottom = min(screen_h, bottom)

                w = right - left
                h = bottom - top
                if w > 0 and h > 0:
                    capture_region = (left, top, right, bottom)
            except Exception as e:
                self.logger.log(f"[WARN] Failed to get dynamic window rect: {e}")

        if capture_region is None and self.recognition_area is not None:
            is_window_mode = (self.environment_tracker.recognition_area_app_title is not None)
            if is_window_mode:
                capture_region = self.recognition_area

        try:
            captured_image = self.capture_manager.capture_frame(region=capture_region)
            if captured_image is not None and captured_image.size > 0:
                self.capturedImageReadyForPreview.emit(captured_image)
            else:
                self.logger.log("log_capture_failed")
                self.captureFailedSignal.emit()
                self.selectionProcessFinished.emit()
        except Exception as e:
            self.logger.log("error_message_capture_save_failed", str(e))
            self.captureFailedSignal.emit()
            self.selectionProcessFinished.emit()

    def cleanup(self):
        self.stop_monitoring()
        self._stop_global_mouse_listener()
        
        self.timer_session_active = False
        
        if self.capture_manager: self.capture_manager.cleanup()
        if hasattr(self, 'thread_pool') and self.thread_pool: self.thread_pool.shutdown(wait=False)

    def _on_cache_build_done(self, future):
        try:
            if future: future.result()
            
            # 監視中、または設定変更時などにスケジュールを再構築する
            self._build_timer_schedule()
            
            self.cacheBuildFinished.emit(True)
        except Exception as e:
            self.logger.log("log_cache_build_error", str(e))
            self.cacheBuildFinished.emit(False)

    def _build_template_cache(self):
        with self.cache_lock:
            current_app_name = self.environment_tracker.recognition_area_app_title
            (self.normal_template_cache, self.backup_template_cache, self.priority_timers, self.folder_children_map) = \
                self.template_manager.build_cache(self.app_config, self.current_window_scale, self.effective_capture_scale, self.is_monitoring, self.priority_timers, current_app_name)

    # --- ★★★ 修正: タイマースケジュール構築 (絶対時刻 & フィルタリング) ★★★ ---
    def _build_timer_schedule(self):
        self.timer_schedule_cache = {}
        # 監視が始まっていなくても、スケジュール自体は設定に基づいて計算しておく
        # (ただし、実行には monitoring loop が必要)

        with self.cache_lock:
            all_caches = list(self.normal_template_cache.items()) + list(self.backup_template_cache.items())
            
            now = datetime.now()
            
            for path, data in all_caches:
                settings = data.get('settings', {})
                timer_conf = settings.get('timer_mode', {})
                
                if timer_conf.get('enabled', False):
                    actions = []
                    
                    # 基準となるID1の時刻を取得 (なければスキップ)
                    # UI側で全てのIDの絶対時刻を計算・保存しているので、それを読み込む
                    
                    saved_actions = timer_conf.get('actions', [])
                    for act in saved_actions:
                        # --- 1. Enabled チェック ---
                        if not act.get('enabled', False):
                            continue
                        
                        # --- 2. 時刻解析とターゲット日時の決定 ---
                        time_str = act.get('display_time', "20:00:00")
                        try:
                            t_time = datetime.strptime(time_str, "%H:%M:%S").time()
                            # 今日の日付と結合
                            target_dt = datetime.combine(now.date(), t_time)
                            
                            # 日またぎ判定: もしターゲット時刻が現在時刻より前なら、明日の時刻とする
                            # (例: 現在21:00で、設定が20:00なら、明日の20:00)
                            if target_dt < now:
                                target_dt += timedelta(days=1)
                            
                            act_copy = act.copy()
                            act_copy['target_time'] = target_dt.timestamp()
                            act_copy['executed'] = False
                            actions.append(act_copy)
                            
                        except ValueError:
                            self.logger.log(f"[WARN] Invalid time format for {Path(path).name}: {time_str}")
                            continue
                    
                    # 時間順にソート
                    actions.sort(key=lambda x: x['target_time'])
                    
                    if actions:
                        self.timer_schedule_cache[path] = {
                            "approach_time": timer_conf.get('approach_time', 5) * 60, # 分 -> 秒
                            "sequence_interval": timer_conf.get('sequence_interval', 1.0),
                            "actions": actions
                        }
            
            if self.timer_schedule_cache:
                self.logger.log(f"[INFO] Timer schedule built for {len(self.timer_schedule_cache)} items.")

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
            
            self._session_context['consecutive_clicks'] = 0
            
            self.timer_session_active = True
            
            self.ui_manager.set_tree_enabled(False)
            self.folder_cooldowns.clear()
            
            if self.thread_pool:
                self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
                self._monitor_thread = threading.Thread(target=self.monitoring_processor.monitoring_loop, daemon=True)
                self._monitor_thread.start(); self.updateStatus.emit("monitoring", "blue"); self.logger.log("log_monitoring_started")
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
            self.folder_cooldowns.clear() 
            
            # タイマーセッションは維持するが、再開時にスケジュール再計算させるため特にリセットはしない
            # (monitoring_loopが止まるので実行はされない)
            
            self.updateStatus.emit("idle", "green")

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
        from PySide6.QtWidgets import QInputDialog 
        folder_name, ok = QInputDialog.getText(self.ui_manager, self.locale_manager.tr("create_folder_title"), self.locale_manager.tr("create_folder_prompt"))
        if ok and folder_name:
            success, message_key_or_text = self.config_manager.create_folder(folder_name)
            if success:
                self.logger.log(message_key_or_text); self.ui_manager.update_image_tree()
                if self.thread_pool: self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
                else: self.ui_manager.set_tree_enabled(True)
            else: QMessageBox.warning(self.ui_manager, self.locale_manager.tr("error_title_create_folder"), self.locale_manager.tr(message_key_or_text))

    def move_item_into_folder(self):
        from PySide6.QtWidgets import QInputDialog
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
    
    def on_image_settings_changed(self, settings: dict):
        image_path_from_ui = settings.get('image_path')

        if self.current_image_settings and image_path_from_ui == self.current_image_path:
            self.current_image_settings.update(settings)
            self._handle_setting_change_and_rebuild()
            self.ui_manager.save_timer.start()

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

    def on_screen_geometry_changed(self, rect):
        if self.environment_tracker:
            self.environment_tracker.refresh_screen_info()

        if self._is_reinitializing_display:
            return
            
        self._is_reinitializing_display = True 
        self.logger.log("log_screen_resolution_changed")
        
        if self.is_monitoring:
            self.stop_monitoring()

        if self.recognition_area:
            self.clear_recognition_area()
        
        self.logger.log("log_lazy_reinitialize_scheduled")
        self.restartApplicationRequested.emit()
    
    def _reinitialize_capture_backend(self):
         self.capture_manager.reinitialize_backend()

    def _attach_session_context(self, hwnd, title):
        hooks_config = self.app_config.get('extended_lifecycle_hooks', {})
        if not hooks_config.get('active', False):
            return

        self._lifecycle_hook_active = False
        self._session_context = {
            'pid': None,
            'exec_path': None,
            'resource_id': hooks_config.get('resource_link_id', ''),
            'consecutive_clicks': 0
        }

        target_proc_name = hooks_config.get('process_marker', '').lower()
        target_win_name = hooks_config.get('window_context_marker', '').lower()

        try:
            pid = 0
            proc_name = ""
            exe_path = ""

            if sys.platform == 'win32' and win32process:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
            
            elif sys.platform.startswith('linux'):
                try:
                    res = subprocess.run(['xdotool', 'getwindowpid', str(hwnd)], capture_output=True, text=True)
                    if res.returncode == 0:
                        pid = int(res.stdout.strip())
                except Exception:
                    pass

            if pid > 0 and psutil.pid_exists(pid):
                proc = psutil.Process(pid)
                proc_name = proc.name().lower()
                try:
                    exe_path = proc.exe()
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    exe_path = ""

                match_proc = (target_proc_name and target_proc_name in proc_name)
                match_title = (target_win_name and target_win_name in title.lower())

                if match_proc or match_title:
                    self._lifecycle_hook_active = True
                    self._session_context['pid'] = pid
                    self._session_context['exec_path'] = exe_path
                    self.logger.log("[INFO] Session context attached. Lifecycle management active.")
                else:
                    self.logger.log("[DEBUG] Session context mismatch. Hooks inactive.")

        except Exception as e:
            self.logger.log(f"[WARN] Failed to attach session context: {e}")

    def _execute_session_recovery(self):
        if self._recovery_in_progress:
            return

        self._recovery_in_progress = True
        self.logger.log("[INFO] Initiating session recovery... Monitoring paused temporarily.")

        def _recovery_task():
            try:
                pid = self._session_context.get('pid')
                if pid:
                    self.action_manager.perform_session_cleanup(pid)
                
                exec_path = self._session_context.get('exec_path')
                res_id = self._session_context.get('resource_id')
                
                success = self.action_manager.perform_session_reload(exec_path, res_id)
                
                if success:
                    self.logger.log("[INFO] Waiting for session availability...")
                    time.sleep(15) 
                    
                    new_pid = self._find_process_by_path(exec_path)
                    if new_pid:
                        self._session_context['pid'] = new_pid
                        self._session_context['consecutive_clicks'] = 0
                        self.logger.log(f"[INFO] Session re-hooked. New PID: {new_pid}")
                    else:
                        self.logger.log("[WARN] Failed to re-hook session automatically.")

            except Exception as e:
                self.logger.log(f"[ERROR] Recovery sequence failed: {e}")
            finally:
                self._recovery_in_progress = False
                self.logger.log("[INFO] Recovery sequence finished. Resuming monitoring.")

        threading.Thread(target=_recovery_task, daemon=True).start()

    def _find_process_by_path(self, target_path):
        if not target_path: return None
        for proc in psutil.process_iter(['pid', 'exe']):
            try:
                if proc.info['exe'] == target_path:
                    return proc.info['pid']
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return None
