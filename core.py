# core.py

import sys
import threading
import time
import cv2
import numpy as np
import os
import psutil
import subprocess
import shutil
from datetime import datetime, timedelta
# ★★★ 修正: QTimer を追加インポート ★★★
from PySide6.QtCore import QObject, Signal, Slot, Qt, QTimer
from PySide6.QtWidgets import QMessageBox, QApplication 
from pathlib import Path
from pynput import mouse
from concurrent.futures import ThreadPoolExecutor
from threading import Timer
from contextlib import contextmanager

from collections import deque
from PIL import Image
import imagehash

from action import ActionManager
from template_manager import TemplateManager
from environment_tracker import EnvironmentTracker
from monitoring_states import IdleState, PriorityState, CountdownState, SequencePriorityState, TimerStandbyState

from core_monitoring import MonitoringProcessor
from core_selection import SelectionHandler

from custom_input_dialog import ask_string_custom
from input_gestures import GlobalMouseGestureHandler
from settings_model import normalize_image_item_settings
from timer_schedule import build_timer_schedule_cache
from monitoring_controller import MonitoringController
from cache_builder import CacheBuilder
from quick_timer_manager import QuickTimerManager
from lifecycle_manager import LifecycleManager

if sys.platform == 'win32':
    try:
        import win32gui
        import win32process
    except ImportError:
        win32gui = None
        win32process = None

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
    updatePreview = Signal(np.ndarray, object, bool)  # (image, settings, reset_zoom)
    updateLog = Signal(str)
    updateRecAreaPreview = Signal(np.ndarray)
    _showUiSignal = Signal()
    selectionProcessStarted = Signal()
    selectionProcessFinished = Signal()
    _areaSelectedForProcessing = Signal(tuple)
    fpsUpdated = Signal(float)
    cacheBuildFinished = Signal(bool)
    _rebuildCacheAfterDeleteRequested = Signal()  # 削除後のキャッシュ再構築リクエスト
    _deleteRebuildCompleteRequested = Signal(object)  # 削除時のキャッシュ再構築完了リクエスト（futureを渡す）
    _moveCompleteRequested = Signal(object)  # ファイル移動完了リクエスト（futureを渡す）
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
    _saveImageDoneProcessRequested = Signal(bool, str)  # スレッドプールからメインスレッドへの移譲用
    # クイックタイマー（Shift+右クリック）: UIに予約作成ダイアログを依頼
    quickTimerDialogRequested = Signal(object)
    # クイックタイマー一覧が変わった通知（監視スレッドからも飛ぶ）
    quickTimersChanged = Signal()
    # ★★★ 追加: キャッシュ再構築時のツリー操作をメインスレッドで実行するためのシグナル ★★★
    _setTreeEnabledRequested = Signal(bool)  # ツリーの有効/無効を設定
    _resetCursorAndResumeListenerRequested = Signal()  # カーソルリセットとリスナー再開をメインスレッドで実行

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
        # キャッシュ構築フローを外出し（リファクタ: B）
        self._cache_builder = CacheBuilder(self)
        # ライフサイクル管理を外出し（リファクタ: D）
        self._lifecycle_manager = LifecycleManager(self)
        
        # ★★★ 削除後のキャッシュ再構築シグナル接続（メインスレッドで確実に実行） ★★★
        self._rebuildCacheAfterDeleteRequested.connect(self._rebuild_cache_after_delete)
        # ★★★ 削除時のキャッシュ再構築完了シグナル接続（メインスレッドで確実に実行） ★★★
        self._deleteRebuildCompleteRequested.connect(self._on_delete_rebuild_complete)
        # ★★★ ファイル移動完了シグナル接続（メインスレッドで確実に実行） ★★★
        self._moveCompleteRequested.connect(self._on_move_complete)
        
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
        
        self.timer_session_active = False
        
        # 順序変更処理の重複実行防止フラグ
        self._order_change_processing = False
        
        # キャッシュ再構築が必要かどうかのフラグ（監視開始時にまとめて再構築）
        self._cache_rebuild_pending = False
        
        # ファイル移動エラーメッセージ（メインスレッドで表示するため、スレッドセーフなロック付き）
        self._move_error_message = None
        self._move_error_lock = threading.Lock()
        
        self.timer_schedule_cache = {}
        
        self.folder_cooldowns = {}

        self.state = None
        self.state_lock = threading.RLock()
        # 監視開始/停止 + state遷移を外出し（リファクタ）
        self._monitoring_controller = MonitoringController(self)

        self._last_clicked_path = None

        self.recognition_area = None
        self._is_capturing_for_registration = False
        self.current_image_path = None
        self.current_image_settings = None
        self.current_image_mat = None

        self.target_hwnd = None

        self.priority_timers = {}
        self.folder_children_map = {}
        
        # OCR非同期処理用
        self.ocr_futures = {}
        # OCR結果保存用（インターバル待機中に完了したOCR結果を保存）
        self.ocr_results = {}
        # OCR処理開始時刻記録用（処理時間計測用）
        self.ocr_start_times = {}
        
        # 右クリック連打判定（GlobalMouseGestureHandlerが使用）
        self.CLICK_INTERVAL = 0.3

        self.mouse_listener = None
        # グローバルマウス入力 → ジェスチャ判定を分離（リファクタ第1段階）
        self._mouse_gestures = GlobalMouseGestureHandler(self)
        self._start_global_mouse_listener()

        self._showUiSignal.connect(self._show_ui_safe)
        
        self._areaSelectedForProcessing.connect(self.selection_handler.handle_area_selection)
        
        # 監視開始/停止の入口を controller に集約（A-1: 停止忘れ/入口分散を防ぐ）
        self.startMonitoringRequested.connect(self._monitoring_controller.start_monitoring)
        self.stopMonitoringRequested.connect(self._monitoring_controller.stop_monitoring)

        self.quickCaptureRequested.connect(self._perform_quick_capture)
        
        # スレッドプールからメインスレッドへの移譲シグナル接続
        self._saveImageDoneProcessRequested.connect(self._process_save_image_done)
        # ★★★ キャッシュ再構築時のツリー操作をメインスレッドで実行 ★★★
        self._setTreeEnabledRequested.connect(self.ui_manager.set_tree_enabled)
        # ★★★ 追加: カーソルリセットとリスナー再開をメインスレッドで実行するためのシグナル接続 ★★★
        self._resetCursorAndResumeListenerRequested.connect(self._handle_reset_cursor_and_resume_listener)

        self.app_config = self.ui_manager.app_config
        self.current_window_scale = None
        self.actual_window_scale = None  # ★★★ 追加: 補正前の実際のウィンドウスケールを保存 ★★★
        self._pending_window_info = None
        self._pending_scale_prompt_info = None
        self._cooldown_until = 0

        self.effective_capture_scale = 1.0
        self.effective_frame_skip_rate = 2

        self.ECO_MODE_SKIP_RATE = 50
        self.ECO_CHECK_INTERVAL = 1.0
        self.ECO_MODE_DELAY = 5.0

        self.screen_stability_hashes = deque(maxlen=3)

        # クイックタイマー予約（最大9件）
        self._quick_timer_manager = QuickTimerManager(self)
        # 互換性維持: monitoring_states 等が dict 参照しているため残す（中身は manager が所有）
        self.quick_timers = self._quick_timer_manager.timers
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

    @contextmanager
    def temporary_listener_pause(self):
        """
        ダイアログ表示中にマウスフックを一時停止するコンテキストマネージャ。
        QTimerのエラーを修正済み。
        """
        was_alive = False
        if self.mouse_listener and self.mouse_listener.is_alive():
            self.logger.log("[DEBUG] Pausing mouse listener for dialog...")
            self._stop_global_mouse_listener()
            was_alive = True
            QApplication.processEvents() 
            time.sleep(0.1)
        
        try:
            yield
        finally:
            if was_alive:
                self.logger.log("[DEBUG] Resuming mouse listener...")
                # QTimerを使って少し遅延させて再開（クリックの誤爆防止）
                QTimer.singleShot(300, self._start_global_mouse_listener)

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
        self._monitoring_controller.transition_to(new_state)
        
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
        return self._monitoring_controller.get_backup_click_countdown()

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
                    self._cache_builder.request_rebuild(disable_tree=False)
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
            except Exception as e: self.logger.log(f"log_error_listener_start: {e}"); self.mouse_listener = None

    def _stop_global_mouse_listener(self):
        if self.mouse_listener:
            if self.mouse_listener.is_alive():
                try:
                    self.mouse_listener.stop()
                    self.mouse_listener.join(timeout=0.5)
                except Exception as e: 
                    self.logger.log("log_warn_listener_stop", str(e))
            self.mouse_listener = None
        # 停止時にジェスチャ状態もリセット（タイマーが残らないように）
        try:
            if hasattr(self, "_mouse_gestures") and self._mouse_gestures:
                self._mouse_gestures.reset()
        except Exception:
            pass

    def _on_global_click(self, x, y, button, pressed):
        # ジェスチャ判定は別モジュールへ移設（機能不変）
        try:
            self._mouse_gestures.on_click(x, y, button, pressed)
        except Exception:
            pass

    def add_quick_timer(self, entry: dict) -> tuple[bool, str]:
        return self._quick_timer_manager.add(entry)

    def remove_quick_timer(self, slot: int):
        self._quick_timer_manager.remove(slot)

    def get_quick_timer_snapshot(self) -> dict:
        return self._quick_timer_manager.snapshot()
    # NOTE: 右クリック連打のタイムアウト処理は GlobalMouseGestureHandler 側へ移設済み

    @Slot()
    def _perform_quick_capture(self):
        self.logger.log("[DEBUG] Quick capture triggered via Middle Click.")
        if self.is_monitoring:
            self.logger.log("log_capture_while_monitoring") 
            self.stop_monitoring()
            self.logger.log("log_capture_proceed_after_stop")

        # UIを非表示にしてマウスカーソルが写り込まないようにする
        if self.ui_manager:
            if self.ui_manager.is_minimal_mode:
                if self.ui_manager.floating_window:
                    self.ui_manager.floating_window.hide()
            else:
                self.ui_manager.hide()
            QApplication.processEvents()
            time.sleep(0.1)  # UI非表示の処理を待つ

        # キャプチャ領域を決定
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
            # ★★★ プレキャプチャを実行（マウスカーソルが写り込まないように） ★★★
            self.logger.log("[DEBUG] Performing pre-capture for quick capture...")
            pre_captured_image = self.capture_manager.capture_frame()
            if pre_captured_image is None:
                raise Exception("Failed to capture full screen for pre-capture.")
            self.logger.log("[DEBUG] Pre-capture successful.")
            
            # マウスカーソルが移動する時間を確保
            time.sleep(0.1)
            
            # プレキャプチャ画像から指定領域を切り出す
            captured_image = None
            if capture_region and pre_captured_image is not None:
                try:
                    (x1, y1, x2, y2) = capture_region
                    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
                    # プレキャプチャ画像の範囲内かチェック
                    h, w = pre_captured_image.shape[:2]
                    x1 = max(0, min(x1, w))
                    y1 = max(0, min(y1, h))
                    x2 = max(x1, min(x2, w))
                    y2 = max(y1, min(y2, h))
                    captured_image = pre_captured_image[y1:y2, x1:x2]
                    self.logger.log("[DEBUG] Cropped region from pre-capture image.")
                except Exception as crop_e:
                    self.logger.log(f"[WARN] Failed to crop from pre-capture: {crop_e}")
                    # フォールバック: 直接キャプチャ
                    captured_image = self.capture_manager.capture_frame(region=capture_region)
            else:
                # 領域が指定されていない場合は全画面を使用
                captured_image = pre_captured_image
            
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
    
    @Slot(bool, str)
    def _process_save_image_done(self, success: bool, message: str):
        """メインスレッドで実行される。保存完了後の処理を行う。"""
        try:
            if success:
                self._log(message)
                # 画像ツリーを更新（新しく保存した画像を表示するため）
                self.ui_manager.update_image_tree()
                # アイテム作成時は即座にキャッシュ再構築を実行（最新の状態にするため）
                self.ui_manager.set_tree_enabled(False)
                if self.thread_pool:
                    future = self.thread_pool.submit(self._build_template_cache)
                    future.add_done_callback(lambda f: self._on_save_rebuild_complete(f))
                else:
                    try:
                        self._build_template_cache()
                        self._cache_builder.on_cache_build_done(None, enable_tree=True)
                    finally:
                        # on_cache_build_done で既に有効化されるが、エラー時のフォールバックとして残す
                        self.ui_manager.set_tree_enabled(True)
                        self.selection_handler._is_saving_image = False
                        self.selection_handler._reset_cursor_and_resume_listener()
            
            self.saveImageCompleted.emit(success, message)
        
        except Exception as e: 
            self.saveImageCompleted.emit(False, f"Error processing save result: {e}")
            self.ui_manager.set_tree_enabled(True)
            self.selection_handler._is_saving_image = False
            self.selection_handler._reset_cursor_and_resume_listener()
    
    def _on_save_rebuild_complete(self, future):
        """画像保存時のキャッシュ再構築完了コールバック"""
        try:
            if future:
                future.result()
            # enable_tree=True でツリーを有効化（デフォルト）
            self._cache_builder.on_cache_build_done(future, enable_tree=True)
        except Exception as e:
            self.logger.log(f"[ERROR] Cache rebuild after save failed: {e}")
            self._cache_builder.on_cache_build_done(future, enable_tree=True)
        finally:
            # on_cache_build_done で既に有効化されるが、エラー時のフォールバックとして残す
            self.ui_manager.set_tree_enabled(True)
            self.selection_handler._is_saving_image = False
            # ★★★ 修正: シグナル経由でメインスレッドに移譲（QTimer警告対策） ★★★
            self._resetCursorAndResumeListenerRequested.emit()
    
    def _handle_reset_cursor_and_resume_listener(self):
        """カーソルリセットとリスナー再開をメインスレッドで実行"""
        try:
            self.selection_handler._reset_cursor_and_resume_listener()
        except Exception as e:
            self.logger.log(f"[ERROR] Failed to reset cursor and resume listener: {e}")

    def cleanup(self):
        self.stop_monitoring()
        self._stop_global_mouse_listener()
        
        self.timer_session_active = False
        
        if self.capture_manager: self.capture_manager.cleanup()
        if hasattr(self, 'thread_pool') and self.thread_pool: self.thread_pool.shutdown(wait=False)

    def on_folder_settings_changed(self):
        self.logger.log("log_folder_settings_changed")
        # キャッシュ再構築は監視開始時にまとめて実行（UI操作時は監視停止されているため）
        self._cache_rebuild_pending = True

    def _build_template_cache(self):
        self._cache_builder.build_template_cache()

    def _build_timer_schedule(self):
        with self.cache_lock:
            self.timer_schedule_cache = build_timer_schedule_cache(
                normal_template_cache=self.normal_template_cache,
                backup_template_cache=self.backup_template_cache,
                logger=self.logger,
            )

    def start_monitoring(self):
        self._monitoring_controller.start_monitoring()

    def stop_monitoring(self):
        self._monitoring_controller.stop_monitoring()

    def delete_selected_items(self, paths_to_delete: list):
        if not paths_to_delete: return
        self.ui_manager.set_tree_enabled(False); deleted_count = 0; failed_count = 0; last_error = ""
        try:
            for path_str in paths_to_delete:
                try: self.config_manager.remove_item(path_str); self.logger.log("log_item_deleted", Path(path_str).name); deleted_count += 1
                except Exception as e: last_error = str(e); self.logger.log("log_item_delete_failed", Path(path_str).name, last_error); failed_count += 1
            if failed_count > 0: QMessageBox.critical(self.ui_manager, self.locale_manager.tr("error_title_delete_failed"), self.locale_manager.tr("error_message_delete_failed", failed_count) + f"\n{last_error}")
        finally:
            # 削除時は即座にキャッシュ再構築を実行（削除されたアイテムを移動しようとするとクラッシュするため）
            # 削除処理が完了してからキャッシュ再構築を開始（競合を防ぐため）
            if deleted_count > 0:
                # ★★★ 修正: 順序ファイルの更新を確実にするため、少し待ってからツリーを更新 ★★★
                # 削除処理で順序ファイルが更新されるのを待つ
                from PySide6.QtCore import QTimer
                QTimer.singleShot(50, lambda: self._update_tree_after_delete())
                # ★★★ 修正: シグナル経由でメインスレッドに確実に実行させる（セグフォルト対策） ★★★
                QTimer.singleShot(100, lambda: self._rebuildCacheAfterDeleteRequested.emit())
            else:
                self.ui_manager.set_tree_enabled(True)
    
    def _update_tree_after_delete(self):
        """削除後のツリー更新（メインスレッドで実行）"""
        try:
            self.ui_manager.update_image_tree()
        except Exception as e:
            self.logger.log(f"[ERROR] Failed to update image tree after delete: {e}")
            import traceback
            traceback.print_exc()
    
    def _rebuild_cache_after_delete(self):
        """削除後のキャッシュ再構築（遅延実行、メインスレッドで実行されることを想定）"""
        try:
            if self.thread_pool:
                future = self.thread_pool.submit(self._build_template_cache)
                # ★★★ 修正: シグナル経由でメインスレッドに確実に実行させる（セグフォルト対策） ★★★
                future.add_done_callback(lambda f: self._deleteRebuildCompleteRequested.emit(f))
            else:
                try:
                    self._build_template_cache()
                    self._cache_builder.on_cache_build_done(None, enable_tree=True)
                finally:
                    # on_cache_build_done で既に有効化されるが、エラー時のフォールバックとして残す
                    self.ui_manager.set_tree_enabled(True)
        except Exception as e:
            self.logger.log(f"[ERROR] _rebuild_cache_after_delete failed: {e}")
            import traceback
            traceback.print_exc()
            self.ui_manager.set_tree_enabled(True)
    
    def _on_delete_rebuild_complete(self, future):
        """削除時のキャッシュ再構築完了コールバック"""
        try:
            if future:
                future.result()
            # enable_tree=True でツリーを有効化（デフォルト）
            self._cache_builder.on_cache_build_done(future, enable_tree=True)
        except Exception as e:
            self.logger.log(f"[ERROR] Cache rebuild after delete failed: {e}")
            self._cache_builder.on_cache_build_done(future, enable_tree=True)
        finally:
            # on_cache_build_done で既に有効化されるが、エラー時のフォールバックとして残す
            self.ui_manager.set_tree_enabled(True)

    def create_folder(self):
        lm = self.locale_manager.tr
        
        # ★★★ フリーズ対策: ダイアログ呼び出し前にリスナーを停止 ★★★
        with self.temporary_listener_pause():
            folder_name, ok = ask_string_custom(
                self.ui_manager, 
                lm("create_folder_title"), 
                lm("create_folder_prompt")
            )
        
        if ok and folder_name:
            success, message_key_or_text = self.config_manager.create_folder(folder_name)
            if success:
                self.logger.log(message_key_or_text); self.ui_manager.update_image_tree()
                # フォルダ作成時は即座にキャッシュ再構築を実行（ツリー操作を安全にするため）
                self.ui_manager.set_tree_enabled(False)
                if self.thread_pool:
                    future = self.thread_pool.submit(self._build_template_cache)
                    future.add_done_callback(lambda f: self._on_create_rebuild_complete(f))
                else:
                    try:
                        self._build_template_cache()
                        self._cache_builder.on_cache_build_done(None, enable_tree=True)
                    finally:
                        # on_cache_build_done で既に有効化されるが、エラー時のフォールバックとして残す
                        self.ui_manager.set_tree_enabled(True)
            else:
                QMessageBox.warning(self.ui_manager, self.locale_manager.tr("error_title_create_folder"), self.locale_manager.tr(message_key_or_text))
    
    def _on_create_rebuild_complete(self, future):
        """フォルダ作成時のキャッシュ再構築完了コールバック"""
        try:
            if future:
                future.result()
            # enable_tree=True でツリーを有効化（デフォルト）
            self._cache_builder.on_cache_build_done(future, enable_tree=True)
        except Exception as e:
            self.logger.log(f"[ERROR] Cache rebuild after create failed: {e}")
            self._cache_builder.on_cache_build_done(future, enable_tree=True)
        finally:
            # on_cache_build_done で既に有効化されるが、エラー時のフォールバックとして残す
            self.ui_manager.set_tree_enabled(True)

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
        # D&D/メニュー移動後にツリー再構築が走るが、選択パスが変わって再選択できないと先頭が見えてしまう。
        # そこで「移動先フォルダ」を後段の update_image_tree で中央表示するために一時的に保持する。
        try:
            if hasattr(self.ui_manager, "pending_tree_center_path"):
                self.ui_manager.pending_tree_center_path = dest_folder_path_str
        except Exception:
            pass

        self.ui_manager.set_tree_enabled(False)
        if self.thread_pool:
            future = self.thread_pool.submit(self._move_items_and_rebuild_async, source_paths, dest_folder_path_str)
            # ★★★ 修正: シグナル経由でメインスレッドに確実に実行させる（セグフォルト対策） ★★★
            future.add_done_callback(lambda f: self._moveCompleteRequested.emit(f))
        else:
            self.logger.log("[WARN] Thread pool not available. Moving items synchronously.")
            try:
                self._move_items_and_rebuild_async(source_paths, dest_folder_path_str)
            finally:
                self._moveCompleteRequested.emit(None)
    
    def _on_move_complete(self, future):
        """ファイル移動完了コールバック（メインスレッドで実行、キャッシュ再構築は認識開始時に実行）"""
        try:
            if future:
                future.result()  # 例外があればここで再発生させる
                
                # エラーダイアログを表示（メインスレッドで実行、スレッドセーフに）
                error_msg = None
                with self._move_error_lock:
                    if hasattr(self, '_move_error_message') and self._move_error_message:
                        error_msg = self._move_error_message
                        self._move_error_message = None
                
                if error_msg:
                    from PySide6.QtWidgets import QMessageBox
                    QMessageBox.warning(
                        self.ui_manager,
                        self.locale_manager.tr("error_title_move_item_failed"),
                        self.locale_manager.tr("log_move_item_failed", error_msg)
                    )
                
                # 順序保存を実行（メインスレッドで実行）
                try:
                    if hasattr(self.ui_manager, 'save_tree_order'):
                        data_to_save = self.ui_manager.save_tree_order()
                        if data_to_save:
                            self.config_manager.save_tree_order_data(data_to_save)
                except Exception as e:
                    self.logger.log("[ERROR] Failed to save order after move: %s", str(e))
        except Exception as e:
            self.logger.log(f"[ERROR] Move operation failed: {e}")
        finally:
            # ツリーを更新してから有効化（メインスレッドで実行）
            try:
                self.ui_manager.update_image_tree()
            except Exception as e:
                self.logger.log(f"[ERROR] Failed to update image tree after move: {e}")
            self.ui_manager.set_tree_enabled(True) 
    
    def _move_items_and_rebuild_async(self, source_paths: list, dest_folder_path_str: str):
        """
        ファイル移動処理（ワーカースレッドで実行）。
        Qtオブジェクト操作は行わず、ファイル操作のみを実行。
        """
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
                # エラー情報を保存（メインスレッドでダイアログ表示するため、スレッドセーフに）
                with self._move_error_lock:
                    self._move_error_message = final_message

        except Exception as e:
            self.logger.log("[ERROR] _move_items_and_rebuild_async: %s", str(e))
            raise 
        
        # ★★★ 修正: キャッシュ再構築は認識開始時にまとめて実行（D&D操作中のIO割り込みを防ぐため） ★★★
        if moved_count > 0:
            self._cache_rebuild_pending = True
        
        # 順序保存はメインスレッドで実行するため、ここではフラグを立てるだけ
        # （_on_move_complete で実行）
        
        # 結果を返す（メインスレッドで処理するため）
        return moved_count, failed_count, final_message

    def move_item_out_of_folder(self):
        source_path_str, name = self.ui_manager.get_selected_item_path(); lm = self.locale_manager.tr
        if not source_path_str: QMessageBox.warning(self.ui_manager, lm("warn_move_out_no_selection"), lm("warn_move_out_no_selection_text")); return
        source_path = Path(source_path_str)
        if not source_path.is_file() or source_path.parent == self.config_manager.base_dir: QMessageBox.warning(self.ui_manager, lm("warn_move_out_not_in_folder"), lm("warn_move_out_not_in_folder_text")); return
        dest_folder_path_str = str(self.config_manager.base_dir)
        success, message_or_key = self.config_manager.move_item(source_path_str, dest_folder_path_str)
        if success:
            self.logger.log(message_or_key); self.ui_manager.update_image_tree()
            ok = self._cache_builder.request_rebuild(disable_tree=False)
            if not ok:
                self.ui_manager.set_tree_enabled(True)
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
                # ★★★ 修正: リネーム時は即座にキャッシュ再構築を実行（削除・作成時と同様） ★★★
                if self.thread_pool:
                    future = self.thread_pool.submit(self._build_template_cache)
                    future.add_done_callback(lambda f: self._on_rename_rebuild_complete(f))
                else:
                    try:
                        self._build_template_cache()
                        self._cache_builder.on_cache_build_done(None, enable_tree=True)
                    finally:
                        # on_cache_build_done で既に有効化されるが、エラー時のフォールバックとして残す
                        self.ui_manager.set_tree_enabled(True)
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
    
    def _on_rename_rebuild_complete(self, future):
        """リネーム時のキャッシュ再構築完了コールバック"""
        try:
            if future:
                future.result()
            # enable_tree=True でツリーを有効化（デフォルト）
            self._cache_builder.on_cache_build_done(future, enable_tree=True)
        except Exception as e:
            self.logger.log(f"[ERROR] Cache rebuild after rename failed: {e}")
            self._cache_builder.on_cache_build_done(future, enable_tree=True)
        finally:
            # on_cache_build_done で既に有効化されるが、エラー時のフォールバックとして残す
            self.ui_manager.set_tree_enabled(True)

    def load_image_and_settings(self, file_path: str):
        if not file_path or not Path(file_path).is_file():
            self.current_image_path = None
            self.current_image_settings = None
            self.current_image_mat = None
            self.updatePreview.emit(None, None, True)  # 画像ツリーのアイテムクリック時はリセット
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

            self.updatePreview.emit(self.current_image_mat, self.current_image_settings, True)  # 画像ツリーのアイテムクリック時はリセット
        
        except Exception as e:
            self.logger.log("log_image_load_failed", Path(file_path).name, str(e))
            self.current_image_path = None
            self.current_image_settings = None
            self.current_image_mat = None
            self.updatePreview.emit(None, None, True)  # 画像ツリーのアイテムクリック時はリセット
    
    def on_image_settings_changed(self, settings: dict):
        # UIから来る設定の型ゆれを吸収（第2段階リファクタ）
        try:
            settings = normalize_image_item_settings(settings, default_image_path=str(settings.get("image_path", "")))
        except Exception:
            pass

        image_path_from_ui = settings.get('image_path')

        if self.current_image_settings and image_path_from_ui == self.current_image_path:
            self.current_image_settings.update(settings)
            self._handle_setting_change_and_rebuild()
            self.ui_manager.save_timer.start()

    def _handle_setting_change_and_rebuild(self, request_save=False):
        if self.is_monitoring:
            self._recalculate_and_update()
            self.save_current_settings()
            self.logger.log("log_item_setting_changed_rebuild")
            self.ui_manager.set_tree_enabled(False)
            ok = self._cache_builder.request_rebuild(disable_tree=False)
            if not ok:
                self.ui_manager.set_tree_enabled(True)
        else:
            self._recalculate_and_update()

    def _recalculate_and_update(self):
        if self.current_image_mat is not None and self.current_image_settings:
            h, w = self.current_image_mat.shape[:2]
            self.current_image_settings['roi_rect'] = self.calculate_roi_rect((w, h), self.current_image_settings)

        self.updatePreview.emit(self.current_image_mat, self.current_image_settings, False)  # 設定変更時はリセットしない

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
            ok = self._cache_builder.request_rebuild(disable_tree=False)
            if not ok:
                self.ui_manager.set_tree_enabled(True)
        else: self.ui_manager.set_tree_enabled(True)

    def on_order_changed(self):
        """
        順序変更時の処理。デバウンス処理により、短時間に複数の変更が発生した場合、
        最後の1回だけ処理を実行する。
        """
        # 既存のタイマーを停止
        if hasattr(self, '_order_change_timer') and self._order_change_timer:
            self._order_change_timer.stop()
        
        # 新しいタイマーを作成（初回のみ）
        if not hasattr(self, '_order_change_timer'):
            from PySide6.QtCore import QTimer
            self._order_change_timer = QTimer(self)
            self._order_change_timer.setSingleShot(True)
            self._order_change_timer.timeout.connect(self._process_order_changed)
        
        # 500ms後に処理を実行（デバウンス）- 連続操作時のクラッシュを防ぐため延長
        self._order_change_timer.start(500)
    
    def _process_order_changed(self):
        """順序変更の実際の処理（デバウンス後の実行）"""
        # 既にキャッシュ再構築が実行中の場合はスキップ（重複実行防止）
        if hasattr(self, '_order_change_processing') and self._order_change_processing:
            self.logger.log("[DEBUG] Order change processing already in progress. Skipping duplicate request.")
            return
        
        self._order_change_processing = True
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
            self._order_change_processing = False
            return

        if self.thread_pool:
            future = self.thread_pool.submit(self._save_order_and_rebuild_async, data_to_save)
            future.add_done_callback(self._on_order_change_complete)
        else:
            self.logger.log("[WARN] Thread pool not available. Saving order and rebuilding cache synchronously.")
            try:
                self._save_order_and_rebuild_async(data_to_save)
            finally:
                self._on_order_change_complete(None)
    
    def _on_order_change_complete(self, future):
        """順序変更処理完了時のコールバック"""
        try:
            if future:
                future.result()  # 例外があればここで再発生させる
            # 順序変更時はキャッシュ再構築を行わないため、on_cache_build_done は呼ばない
        except Exception as e:
            self.logger.log(f"[ERROR] Order change processing failed: {e}")
        finally:
            self._order_change_processing = False
            # ツリーを有効化（順序変更処理が完了したため）
            try:
                self.ui_manager.set_tree_enabled(True)
            except Exception:
                pass 
                
    def _save_order_and_rebuild_async(self, data_to_save: dict):
        """
        順序ファイルの保存のみを行う（キャッシュ再構築は不要）。
        順序変更はファイル位置の変更だけで、画像内容や設定は変わらないため。
        """
        try:
            if hasattr(self.config_manager, 'save_tree_order_data'):
                self.config_manager.save_tree_order_data(data_to_save)
                self.logger.log("log_order_saved")
            else:
                self.logger.log("log_warn_save_order_data_not_found")
        except Exception as e: 
            self.logger.log("log_error_save_order", str(e))
            raise 
        # 順序変更時はキャッシュ再構築は不要（画像内容や設定は変わらないため）

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
        self._lifecycle_manager.attach_session_context(hwnd, title)

    def _compute_and_apply_window_scale_no_prompt(self, title: str, rect: tuple):
        self._lifecycle_manager.compute_and_apply_window_scale_no_prompt(title, rect)

    def _find_window_rect_for_pid(self, pid: int, title_hint: str | None = None):
        return self._lifecycle_manager.find_window_rect_for_pid(pid, title_hint)

    def _relock_capture_after_recovery(self, new_pid: int):
        self._lifecycle_manager.relock_capture_after_recovery(new_pid)

    def _execute_session_recovery(self):
        self._lifecycle_manager.execute_session_recovery()

    def _find_process_by_path(self, target_path):
        return self._lifecycle_manager.find_process_by_path(target_path)
