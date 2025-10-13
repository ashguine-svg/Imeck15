# core.py

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


from capture import CaptureManager, DXCAM_AVAILABLE
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


    def __init__(self, ui_manager, capture_manager, config_manager, logger, performance_monitor):
        super().__init__()
        self.ui_manager, self.capture_manager, self.config_manager, self.logger = ui_manager, capture_manager, config_manager, logger
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
        self.thread_pool = ThreadPoolExecutor(max_workers=worker_threads)
        self.logger.log(f"CPU論理コア数: {cpu_cores}, 認識スレッド数: {worker_threads} (最大2)")
        self.cache_lock = threading.Lock()
        
        self.right_click_timer = None
        self.last_right_click_time = 0
        self.DOUBLE_CLICK_INTERVAL = 0.3
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
        
        self.screen_stability_hashes = deque(maxlen=3)
        self.latest_frame_for_hash = None
        
        self.last_successful_click_time = 0
        self.is_eco_cooldown_active = False
        self.ECO_MODE_DELAY = 5.0
        
        self.on_app_config_changed()

        self._last_log_message = ""
        self._last_log_time = 0
        self._log_spam_filter = {"画面が不安定なためクリックを保留します。", "省エネモード待機中..."}

    def transition_to(self, new_state):
        self.state = new_state
        self._last_clicked_path = None

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

    def _log(self, message: str, force: bool = False):
        current_time = time.time()
        if not force and \
           message == self._last_log_message and \
           message in self._log_spam_filter and \
           current_time - self._last_log_time < 3.0:
            return
        self.updateLog.emit(message)
        self._last_log_message, self._last_log_time = message, current_time

    def set_opencl_enabled(self, enabled: bool):
        if OPENCL_AVAILABLE:
            try:
                cv2.ocl.setUseOpenCL(enabled)
                status = "有効" if cv2.ocl.useOpenCL() else "無効"
                self.logger.log(f"OpenCLを{status}に設定しました。")
                if self.is_monitoring:
                    self.logger.log("設定変更を反映するため、キャッシュを再構築します。")
                    self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
            except Exception as e: self.logger.log(f"OpenCLの設定変更中にエラーが発生しました: {e}")
    
    def on_app_config_changed(self):
        self.app_config = self.ui_manager.app_config
        self.capture_manager.set_capture_method(self.app_config.get('capture_method', 'dxcam'))
        self.set_opencl_enabled(self.app_config.get('use_opencl', True))
        
        lw_conf = self.app_config.get('lightweight_mode', {})
        is_lw_enabled = lw_conf.get('enabled', False)
        preset = lw_conf.get('preset', '標準')
        
        if is_lw_enabled:
            user_frame_skip = self.app_config.get('frame_skip_rate', 2)
            if preset == "標準": self.effective_capture_scale, self.effective_frame_skip_rate = 0.5, user_frame_skip + 5
            elif preset == "パフォーマンス": self.effective_capture_scale, self.effective_frame_skip_rate = 0.4, user_frame_skip + 20
            elif preset == "ウルトラ": self.effective_capture_scale, self.effective_frame_skip_rate = 0.3, user_frame_skip + 25
        else:
            self.effective_capture_scale, self.effective_frame_skip_rate = 1.0, self.app_config.get('frame_skip_rate', 2)

        self.logger.log(f"アプリ設定変更: キャプチャ={self.capture_manager.current_method}, 軽量化={is_lw_enabled}({preset}), 実効スケール={self.effective_capture_scale:.2f}, 実効スキップ={self.effective_frame_skip_rate}, OpenCL={cv2.ocl.useOpenCL() if OPENCL_AVAILABLE else 'N/A'}")

    def _show_ui_safe(self):
        if self.ui_manager: self.ui_manager.show(); self.ui_manager.activateWindow()

    def _start_global_mouse_listener(self):
        if self.mouse_listener is None or not self.mouse_listener.is_alive():
            self.mouse_listener = mouse.Listener(on_click=self._on_global_click)
            self.mouse_listener.start()

    def _stop_global_mouse_listener(self):
        if self.mouse_listener and self.mouse_listener.is_alive():
            self.mouse_listener.stop(); self.mouse_listener = None

    def _on_global_click(self, x, y, button, pressed):
        if button == mouse.Button.right and pressed:
            current_time = time.time()
            if current_time - self.last_right_click_time < self.DOUBLE_CLICK_INTERVAL:
                if self.right_click_timer: self.right_click_timer.cancel(); self.right_click_timer = None
                self.logger.log("右ダブルクリック検出: 監視を開始します。"); self.startMonitoringRequested.emit()
            else:
                self.right_click_timer = Timer(self.DOUBLE_CLICK_INTERVAL, self._handle_single_right_click)
                self.right_click_timer.start()
            self.last_right_click_time = current_time

    def _handle_single_right_click(self):
        self.logger.log("右クリック検出: 監視を停止します。"); self.stopMonitoringRequested.emit()

    def cleanup(self):
        self.stop_monitoring(); self._stop_global_mouse_listener()
        if self.capture_manager: self.capture_manager.cleanup()

    def _on_cache_build_done(self, future):
        try: future.result()
        except Exception as e: self.logger.log(f"キャッシュ構築中にエラーが発生しました: {e}")
        finally: self.cacheBuildFinished.emit()

    def capture_image_for_registration(self):
        self._is_capturing_for_registration = True; self.ui_manager.setRecAreaDialog()

    def delete_selected_item(self):
        path_str, name = self.ui_manager.get_selected_item_path()
        if not path_str: QMessageBox.warning(self.ui_manager, "警告", "削除するアイテムを選択してください。"); return
        if QMessageBox.question(self.ui_manager, "削除の確認", f"本当に '{name.lstrip('📁 ')}' を削除しますか？\n(フォルダの場合、中のファイルもすべて削除されます)", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            try:
                self.ui_manager.set_tree_enabled(False); self.config_manager.remove_item(path_str)
                self.logger.log(f"'{name}' を削除しました。")
                self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
            except Exception as e:
                self.logger.log(f"'{name}' の削除に失敗しました: {e}"); QMessageBox.critical(self.ui_manager, "エラー", f"削除に失敗しました:\n{e}")
                self.ui_manager.set_tree_enabled(True)

    def on_folder_settings_changed(self):
        self.logger.log("フォルダ設定が変更されました。キャッシュを再構築します。"); self.ui_manager.set_tree_enabled(False)
        self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
        
    def create_folder(self):
        folder_name, ok = QInputDialog.getText(self.ui_manager, "フォルダ作成", "新しいフォルダの名前を入力してください:")
        if ok and folder_name:
            success, message = self.config_manager.create_folder(folder_name)
            if success:
                self.logger.log(message); self.ui_manager.update_image_tree()
                self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
            else: QMessageBox.warning(self.ui_manager, "エラー", message)

    def move_item_into_folder(self):
        source_path_str, name = self.ui_manager.get_selected_item_path()
        if not source_path_str: QMessageBox.warning(self.ui_manager, "警告", "移動する画像を選択してください。"); return
        source_path = Path(source_path_str)
        if not source_path.is_file() or source_path.parent != self.config_manager.base_dir:
            QMessageBox.warning(self.ui_manager, "警告", "フォルダに入れることができるのは、一番上の階層にある画像ファイルだけです。"); return
        folders = [item for item in self.config_manager.get_hierarchical_list() if item['type'] == 'folder']
        if not folders: QMessageBox.information(self.ui_manager, "情報", "移動先のフォルダがありません。先にフォルダを作成してください。"); return
        folder_names = [f['name'] for f in folders]
        dest_folder_name, ok = QInputDialog.getItem(self.ui_manager, "フォルダ選択", "どのフォルダに入れますか？", folder_names, 0, False)
        if ok and dest_folder_name:
            dest_folder_path_str = str(self.config_manager.base_dir / dest_folder_name)
            success, message = self.config_manager.move_item(source_path_str, dest_folder_path_str)
            if success: 
                self.logger.log(message); self.ui_manager.update_image_tree()
                self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
            else: QMessageBox.critical(self.ui_manager, "エラー", message)

    def move_item_out_of_folder(self):
        source_path_str, name = self.ui_manager.get_selected_item_path()
        if not source_path_str: QMessageBox.warning(self.ui_manager, "警告", "フォルダから出す画像を選択してください。"); return
        source_path = Path(source_path_str)
        if not source_path.is_file() or source_path.parent == self.config_manager.base_dir:
            QMessageBox.warning(self.ui_manager, "警告", "フォルダの中にある画像ファイルを選択してください。"); return
        dest_folder_path_str = str(self.config_manager.base_dir)
        success, message = self.config_manager.move_item(source_path_str, dest_folder_path_str)
        if success: 
            self.logger.log(message); self.ui_manager.update_image_tree()
            self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
        else: QMessageBox.critical(self.ui_manager, "エラー", message)

    def load_image_and_settings(self, file_path: str):
        if file_path is None or Path(file_path).is_dir():
            self.current_image_path, self.current_image_settings, self.current_image_mat = None, None, None
            self.updatePreview.emit(None, None); return
        try:
            self.current_image_path, self.current_image_settings = file_path, self.config_manager.load_item_setting(Path(file_path))
            with open(file_path, 'rb') as f: self.current_image_mat = cv2.imdecode(np.fromfile(f, np.uint8), cv2.IMREAD_COLOR)
            if self.current_image_mat is None: raise ValueError("画像ファイルのデコードに失敗。")
        except Exception as e:
            self.logger.log(f"画像の読み込みに失敗: {file_path}, エラー: {e}")
            self.current_image_path, self.current_image_settings, self.current_image_mat = None, None, None
            self.updatePreview.emit(None, None); return
        self._recalculate_and_update(request_save=False)

    def on_image_settings_changed(self, settings: dict):
        if self.current_image_settings: self.current_image_settings.update(settings); self._recalculate_and_update()
    def on_preview_click_settings_changed(self, click_data: dict):
        if self.current_image_settings: self.current_image_settings.update(click_data); self._recalculate_and_update()

    def _recalculate_and_update(self, request_save=True):
        if self.current_image_mat is not None and self.current_image_settings:
            h, w = self.current_image_mat.shape[:2]
            self.current_image_settings['roi_rect'] = self.calculate_roi_rect((w, h), self.current_image_settings)
        self.updatePreview.emit(self.current_image_mat, self.current_image_settings)
        if request_save: self.ui_manager.request_save()

    def calculate_roi_rect(self, image_size, settings):
        if not settings.get('roi_enabled', False): return None
        center_x, center_y = -1, -1
        if settings.get('point_click') and settings.get('click_position'): center_x, center_y = settings['click_position']
        elif settings.get('range_click') and settings.get('click_rect'):
            rect = settings['click_rect']; center_x, center_y = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
        if center_x == -1: return None
        roi_w, roi_h = 200, 200
        return (int(center_x-roi_w/2), int(center_y-roi_h/2), int(center_x+roi_w/2), int(center_y+roi_h/2))

    def save_current_settings(self):
        if self.current_image_path and self.current_image_settings:
            self.config_manager.save_item_setting(Path(self.current_image_path), self.current_image_settings)
            self.logger.log(f"設定 '{Path(self.current_image_path).name}' を保存しました。")

    def load_images_into_manager(self, file_paths):
        self.ui_manager.set_tree_enabled(False)
        for fp in file_paths: self.config_manager.add_item(Path(fp))
        self._log(f"画像を{len(file_paths)}個追加しました。")
        self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)

    def on_order_changed(self):
        self.ui_manager.set_tree_enabled(False)
        self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)

    def _build_template_cache(self):
        with self.cache_lock:
            (self.normal_template_cache, self.backup_template_cache, self.priority_timers, self.folder_children_map) = \
            self.template_manager.build_cache(self.app_config, self.current_window_scale, self.effective_capture_scale, self.is_monitoring, self.priority_timers)

    def start_monitoring(self):
        if not self.recognition_area:
            QMessageBox.warning(self.ui_manager, "認識範囲未設定", "先に認識範囲を設定してください。\nヘッダーの「認識範囲設定」ボタンから設定できます。"); return
        if not self.is_monitoring:
            self.is_monitoring = True; self.state = IdleState(self)
            self._click_count, self._cooldown_until, self._last_clicked_path = 0, 0, None
            self.screen_stability_hashes.clear(); self.last_successful_click_time, self.is_eco_cooldown_active = 0, False
            self.ui_manager.set_tree_enabled(False)
            self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
            self._monitor_thread = threading.Thread(target=self._monitoring_loop, daemon=True)
            self._monitor_thread.start()
            self.updateStatus.emit("監視中...", "blue"); self.logger.log("監視を開始しました。")

    def stop_monitoring(self):
        if self.is_monitoring:
            self.is_monitoring = False; self.state = None
            if self._monitor_thread and self._monitor_thread.is_alive(): self._monitor_thread.join(timeout=1.0)
            with self.cache_lock:
                for item in list(self.normal_template_cache.values()) + list(self.backup_template_cache.values()): item['best_scale'] = None
            self.updateStatus.emit("待機中", "green"); self.logger.log("監視を停止しました。")
    
    def _monitoring_loop(self):
        last_match_time_map, fps_last_time, frame_counter = {}, time.time(), 0
        while self.is_monitoring:
            try:
                current_time = time.time()
                if self._cooldown_until > current_time: time.sleep(min(self._cooldown_until - current_time, 0.1)); continue
                frame_counter += 1
                if (delta_time := current_time - fps_last_time) >= 1.0:
                    self.fpsUpdated.emit(frame_counter / delta_time); fps_last_time, frame_counter = current_time, 0
                if isinstance(self.state, IdleState): self._check_and_activate_timer_priority_mode()
                self.is_eco_cooldown_active = self.app_config.get('eco_mode',{}).get('enabled',False) and self.last_successful_click_time > 0 and isinstance(self.state,IdleState) and (current_time-self.last_successful_click_time > self.ECO_MODE_DELAY)
                if self.is_eco_cooldown_active: self._log("省エネモード待機中..."); time.sleep(1.0); continue
                elif isinstance(self.state, CountdownState): time.sleep(1.0)
                elif (frame_counter % self.effective_frame_skip_rate) != 0: time.sleep(0.01); continue
                screen_bgr = self.capture_manager.capture_frame(region=self.recognition_area)
                if screen_bgr is None: self._log("画面のキャプチャに失敗しました。"); time.sleep(1.0); continue
                if self.effective_capture_scale != 1.0: screen_bgr = cv2.resize(screen_bgr, None, fx=self.effective_capture_scale, fy=self.effective_capture_scale, interpolation=cv2.INTER_AREA)
                self.latest_frame_for_hash, screen_gray = screen_bgr.copy(), cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)
                screen_bgr_umat, screen_gray_umat = None, None
                if cv2.ocl.useOpenCL():
                    try: screen_bgr_umat, screen_gray_umat = cv2.UMat(screen_bgr), cv2.UMat(screen_gray)
                    except Exception as e: self.logger.log(f"スクリーンショットのUMat変換に失敗: {e}")
                if self.state: self.state.handle(current_time, (screen_bgr, screen_gray, screen_bgr_umat, screen_gray_umat), last_match_time_map)
            except Exception as e: self._log(f"監視ループでエラーが発生しました: {e}", force=True); time.sleep(1.0)
            finally: time.sleep(0.01)

    def check_screen_stability(self) -> bool:
        if not hasattr(self, 'latest_frame_for_hash') or self.latest_frame_for_hash is None: return False
        h, w, _ = self.latest_frame_for_hash.shape; roi = self.latest_frame_for_hash[h//2-32:h//2+32, w//2-32:w//2+32]
        current_hash = calculate_phash(roi)
        if current_hash is None: return False
        self.screen_stability_hashes.append(current_hash)
        if len(self.screen_stability_hashes) < self.screen_stability_hashes.maxlen: return False
        threshold = self.app_config.get('screen_stability_check', {}).get('threshold', 5)
        return all((self.screen_stability_hashes[-1] - h) <= threshold for h in self.screen_stability_hashes)

    def _check_and_activate_timer_priority_mode(self):
        for path, activation_time in self.priority_timers.items():
            if time.time() >= activation_time: self.transition_to_timer_priority(path); break 

    def _process_matches_as_sequence(self, all_matches, current_time, last_match_time_map):
        if not all_matches: return False
        clickable = [m for m in all_matches if current_time-last_match_time_map.get(m['path'],0) > (m['settings'].get('interval_time',1.5) + (m['settings'].get('debounce_time',0.0) if self._last_clicked_path==m['path'] else 0))]
        if not clickable: return False
        target = min(clickable, key=lambda m: (m['settings'].get('interval_time', 1.5), -m['confidence']))
        if self.app_config.get('screen_stability_check',{}).get('enabled',True) and not self.check_screen_stability():
            self._log("画面が不安定なためクリックを保留します。"); return False
        if not self.is_monitoring: return False 
        self._execute_click(target); last_match_time_map[target['path']] = time.time()
        return True

    def _execute_final_backup_click(self, target_path):
        screen_bgr = self.capture_manager.capture_frame(region=self.recognition_area)
        if screen_bgr is None: self._log("バックアップクリック失敗: 画面キャプチャができませんでした。", force=True); return
        screen_gray, screen_bgr_umat, screen_gray_umat = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY), None, None
        if cv2.ocl.useOpenCL():
            try: screen_bgr_umat, screen_gray_umat = cv2.UMat(screen_bgr), cv2.UMat(screen_gray)
            except Exception as e: self.logger.log(f"バックアップクリック時のUMat変換に失敗: {e}")
        cache_item = self.backup_template_cache.get(target_path)
        if not cache_item: self._log(f"バックアップクリック失敗: '{Path(target_path).name}' がキャッシュにありません。", force=True); return
        matches = self._find_best_match(screen_bgr, screen_gray, screen_bgr_umat, screen_gray_umat, {target_path: cache_item})
        if matches: self._execute_click(max(matches, key=lambda m: m['confidence']))
        else: self._log(f"バックアップクリック失敗: '{Path(target_path).name}' が見つかりませんでした。", force=True)
    
    def _find_best_match(self, s_bgr, s_gray, s_bgr_umat, s_gray_umat, cache):
        with self.cache_lock:
            if not cache: return []
            use_cl, use_gs = cv2.ocl.useOpenCL(), self.app_config.get('grayscale_matching',False)
            screen = s_gray if use_gs else s_bgr
            if use_cl: screen_umat = s_gray_umat if use_gs else s_bgr_umat; screen = screen_umat if screen_umat else screen
            s_shape = screen.get().shape[:2] if use_cl and isinstance(screen,cv2.UMat) else screen.shape[:2]
            results, futures = [], []
            for path, data in cache.items():
                is_search = data['best_scale'] is None
                templates = data['scaled_templates'] if is_search else [t for t in data['scaled_templates'] if t['scale']==data['best_scale']] or data['scaled_templates']
                for t in templates:
                    template = t['gray'] if use_gs else t['image']
                    if use_cl: t_umat = t.get('gray_umat' if use_gs else 'image_umat'); template = t_umat if t_umat else template
                    task = {'path':path,'settings':data['settings'],'template':template,'scale':t['scale']}
                    if use_cl:
                        if (m:=_match_template_task(screen,task,s_shape,t['shape'])): results.append(m)
                    else: futures.append(self.thread_pool.submit(_match_template_task,screen,task,s_shape,t['shape']))
            if not use_cl:
                for f in futures:
                    if (r:=f.result()): results.append(r)
        if not results: return []
        best_match = max(results, key=lambda r: r['confidence'])
        with self.cache_lock:
            path, cache_dict = best_match['path'], self.normal_template_cache if best_match['path'] in self.normal_template_cache else self.backup_template_cache
            if (item:=cache_dict.get(path)) and item['best_scale'] is None:
                 item['best_scale'] = best_match['scale']
                 self._log(f"最適スケール発見: {Path(path).name} @ {best_match['scale']:.3f}倍 (信頼度: {best_match['confidence']:.2f})")
                 self.bestScaleFound.emit(path, best_match['scale'])
        return results

    def _execute_click(self, match_info):
        result = self.action_manager.execute_click(match_info, self.recognition_area, self.target_hwnd, self.effective_capture_scale)
        if result and result.get('success'):
            self._click_count+=1; self._last_clicked_path=result.get('path'); self.last_successful_click_time=time.time()

    def set_recognition_area(self, method: str):
        self.selectionProcessStarted.emit()
        self.ui_manager.hide()
        if self.performance_monitor: self.performance_monitor.hide()
        # ★★★ ここからが修正箇所 ★★★
        self._stop_global_mouse_listener()
        # ★★★ 修正はここまで ★★★
        if method == "rectangle":
            self.target_hwnd, self.current_window_scale = None, None
            self.windowScaleCalculated.emit(0.0)
            self.logger.log("認識範囲を四角指定に設定しました。スケールは計算されません。")
            self.selection_overlay = SelectionOverlay()
            self.selection_overlay.selectionComplete.connect(self._areaSelectedForProcessing.emit)
            self.selection_overlay.selectionCancelled.connect(self._on_selection_cancelled)
            self.selection_overlay.showFullScreen()
        elif method == "window":
            self.logger.log("ウィンドウを選択してください... (ESCキーでキャンセル)")
            self.window_selection_listener = WindowSelectionListener(self._handle_window_click_for_selection)
            self.window_selection_listener.start()
            self.keyboard_selection_listener = keyboard.Listener(on_press=self._on_key_press_for_selection)
            self.keyboard_selection_listener.start()
            
    def _on_selection_cancelled(self):
        self.logger.log("範囲選択がキャンセルされました。")
        if self._is_capturing_for_registration: self._is_capturing_for_registration = False
        if hasattr(self, 'selection_overlay'): self.selection_overlay = None
        if self.window_selection_listener: self.window_selection_listener.stop(); self.window_selection_listener = None
        if self.keyboard_selection_listener: self.keyboard_selection_listener.stop(); self.keyboard_selection_listener = None
        self.selectionProcessFinished.emit()
        self._show_ui_safe()
        # ★★★ ここからが修正箇所 ★★★
        self._start_global_mouse_listener()
        # ★★★ 修正はここまで ★★★

    def _on_key_press_for_selection(self, key):
        if key == keyboard.Key.esc:
            self.logger.log("キーボードによりウィンドウ選択がキャンセルされました。")
            if self.window_selection_listener: self.window_selection_listener.stop()
            if self.keyboard_selection_listener: self.keyboard_selection_listener.stop()
            self._showUiSignal.connect(self._on_selection_cancelled)
            self._showUiSignal.emit()
            self._showUiSignal.disconnect(self._on_selection_cancelled)
            return False

    def _handle_window_click_for_selection(self, x, y):
        if self.keyboard_selection_listener: self.keyboard_selection_listener.stop(); self.keyboard_selection_listener = None
        if sys.platform == 'win32': self._handle_window_click_for_selection_windows(x, y)
        else: self._handle_window_click_for_selection_linux(x, y)
        # ★★★ ここからが修正箇所 ★★★
        self._start_global_mouse_listener()
        # ★★★ 修正はここまで ★★★

    def _handle_window_click_for_selection_windows(self, x, y):
        try:
            hwnd = win32gui.WindowFromPoint((x, y))
            if not hwnd: return
            self.target_hwnd = hwnd
            if 'dxcam' in sys.modules and self.capture_manager.dxcam_sct: self.capture_manager.dxcam_sct.target_hwnd = hwnd
            client_rect_win = win32gui.GetClientRect(hwnd)
            left, top = win32gui.ClientToScreen(hwnd, (0, 0))
            right, bottom = left + client_rect_win[2], top + client_rect_win[3]
            if right <= left or bottom <= top:
                self.logger.log(f"ウィンドウ領域の計算結果が無効です: ({left},{top},{right},{bottom})。"); self._on_selection_cancelled(); return
            # ★★★ ここからが修正箇所 ★★★
            import pyautogui
            # ★★★ 修正はここまで ★★★
            rect = (max(0, left), max(0, top), min(pyautogui.size().width, right), min(pyautogui.size().height, bottom))
            if self._is_capturing_for_registration: self._areaSelectedForProcessing.emit(rect); self.selectionProcessFinished.emit(); return
            title = win32gui.GetWindowText(hwnd)
            self._pending_window_info = {"title": title, "dims": {'width': rect[2] - rect[0], 'height': rect[3] - rect[1]}, "rect": rect}
            if title and title not in self.config_manager.load_window_scales(): self.askToSaveWindowBaseSizeSignal.emit(title)
            else: self.process_base_size_prompt_response(False)
        except Exception as e:
            self.logger.log(f"ウィンドウ領域の取得に失敗: {e}"); self.target_hwnd = None
            self._showUiSignal.emit(); self.selectionProcessFinished.emit()
    
    def _handle_window_click_for_selection_linux(self, x, y):
        if missing := [tool for tool in ['xdotool', 'xwininfo'] if not shutil.which(tool)]:
            self.logger.log(f"エラー: {', '.join(missing)} が見つかりません。"); self._showUiSignal.emit(); self.selectionProcessFinished.emit(); return
        try:
            id_proc = subprocess.run(['xdotool', 'getmouselocation'], capture_output=True, text=True, check=True)
            window_id = [line.split(':')[1] for line in id_proc.stdout.strip().split() if 'window' in line][0]
            info_proc = subprocess.run(['xwininfo', '-id', window_id], capture_output=True, text=True, check=True)
            info = {k.strip(): v.strip() for line in info_proc.stdout.split('\n') if ':' in line for k, v in [line.split(':', 1)]}
            left, top, w, h = int(info['Absolute upper-left X']), int(info['Absolute upper-left Y']), int(info['Width']), int(info['Height'])
            title = info['xwininfo'].split('"')[1] if '"' in info.get('xwininfo', '') else f"Window (ID: {window_id})"
            if w <= 0 or h <= 0: self.logger.log(f"ウィンドウ領域の計算結果が無効です。"); self._on_selection_cancelled(); return
            # ★★★ ここからが修正箇所 ★★★
            import pyautogui
            # ★★★ 修正はここまで ★★★
            rect = (max(0, left), max(0, top), min(pyautogui.size().width, left+w), min(pyautogui.size().height, top+h))
            if self._is_capturing_for_registration: self._areaSelectedForProcessing.emit(rect); self.selectionProcessFinished.emit(); return
            self._pending_window_info = {"title": title, "dims": {'width': w, 'height': h}, "rect": rect }
            if title not in self.config_manager.load_window_scales(): self.askToSaveWindowBaseSizeSignal.emit(title)
            else: self.process_base_size_prompt_response(False)
        except Exception as e:
            self.logger.log(f"Linuxでのウィンドウ領域取得に失敗: {e}"); self._showUiSignal.emit(); self.selectionProcessFinished.emit()

    def process_base_size_prompt_response(self, save_as_base: bool):
        try:
            if not (info := self._pending_window_info): return
            title, current_dims, rect = info['title'], info['dims'], info['rect']
            if save_as_base:
                scales_data = self.config_manager.load_window_scales(); scales_data[title] = current_dims
                self.config_manager.save_window_scales(scales_data)
                self.current_window_scale = 1.0; self.logger.log(f"ウィンドウ '{title}' の基準サイズを保存しました。"); self.windowScaleCalculated.emit(1.0)
            elif title and title in (scales_data := self.config_manager.load_window_scales()):
                base_dims = scales_data[title]
                calc_scale = current_dims['width'] / base_dims['width'] if base_dims['width'] > 0 else 1.0
                if 0.995 <= calc_scale <= 1.005: self.current_window_scale = 1.0; self.logger.log(f"ウィンドウ '{title}' のスケール: {calc_scale:.3f}倍 (1.0として補正)")
                else: self._pending_scale_prompt_info = {**info, 'calculated_scale': calc_scale}; self.askToApplyWindowScaleSignal.emit(calc_scale); return
            else: self.current_window_scale = None
            self.windowScaleCalculated.emit(self.current_window_scale if self.current_window_scale is not None else 0.0)
            self._areaSelectedForProcessing.emit(rect)
        except Exception as e: self.logger.log(f"基準サイズ応答の処理中にエラー: {e}")
        finally: 
            if not self._pending_scale_prompt_info: self._pending_window_info = None; self._showUiSignal.emit(); self.selectionProcessFinished.emit()

    def process_apply_scale_prompt_response(self, apply_scale: bool):
        try:
            if not (info := self._pending_scale_prompt_info): return
            scale, rect = info['calculated_scale'], info['rect']
            if apply_scale:
                self.ui_manager.app_config['auto_scale']['use_window_scale'] = True
                self.ui_manager.auto_scale_widgets['use_window_scale'].setChecked(True)
                self.ui_manager.on_app_settings_changed()
                self.current_window_scale = scale; self.logger.log(f"ウィンドウスケール {scale:.3f}倍 を適用します。")
            else: self.current_window_scale = None; self.logger.log(f"計算されたウィンドウスケール {scale:.3f}倍 は適用されませんでした。")
            self.windowScaleCalculated.emit(self.current_window_scale if self.current_window_scale is not None else 0.0)
            self._areaSelectedForProcessing.emit(rect)
        except Exception as e: self.logger.log(f"スケール適用応答の処理中にエラー: {e}")
        finally: self._pending_scale_prompt_info, self._pending_window_info = None, None; self._showUiSignal.emit(); self.selectionProcessFinished.emit()

    def handle_area_selection(self, coords):
        if self._is_capturing_for_registration:
            self._is_capturing_for_registration = False
            QTimer.singleShot(100, lambda: self._save_captured_image(coords))
        else:
            self.recognition_area = coords
            self.logger.log(f"認識範囲を設定: {coords}")
            self._update_rec_area_preview()
            self.selectionProcessFinished.emit(); self.ui_manager.show()
        if hasattr(self, 'selection_overlay'): self.selection_overlay = None
        # ★★★ ここからが修正箇所 ★★★
        self._start_global_mouse_listener()
        # ★★★ 修正はここまで ★★★
        
    def _get_filename_from_user(self):
        if sys.platform == 'win32': return QInputDialog.getText(self.ui_manager, "ファイル名を入力", "保存するファイル名を入力してください:")
        else:
            if not shutil.which('zenity'): QMessageBox.warning(self.ui_manager, "エラー", "'zenity' が必要です。"); return None, False
            try:
                cmd = ['zenity', '--entry', '--title=ファイル名を入力', '--text=保存するファイル名を入力（拡張子不要）:']
                res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                return (res.stdout.strip(), True) if res.returncode == 0 else (None, False)
            except Exception as e: QMessageBox.critical(self.ui_manager, "エラー", f"Zenity呼出失敗:\n{e}"); return None, False

    def _save_captured_image(self, region_coords):
        try:
            captured_image = self.capture_manager.capture_frame(region=region_coords)
            self._show_ui_safe()
            if self.performance_monitor and not self.performance_monitor.isVisible(): self.performance_monitor.show()
            if captured_image is None: QMessageBox.warning(self.ui_manager, "エラー", "画像のキャプチャに失敗しました。"); self.selectionProcessFinished.emit(); return
            file_name, ok = self._get_filename_from_user()
            if ok and file_name:
                self.ui_manager.set_tree_enabled(False)
                save_path = self.config_manager.base_dir / f"{file_name}.png"
                if save_path.exists() and QMessageBox.question(self.ui_manager, "上書き確認", f"'{save_path.name}' は既に存在します。上書きしますか？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.No:
                    self.ui_manager.set_tree_enabled(True); self.selectionProcessFinished.emit(); return
                self.thread_pool.submit(self._save_image_task, captured_image, save_path).add_done_callback(self._on_save_image_done)
            else: self.selectionProcessFinished.emit()
        except Exception as e:
            QMessageBox.critical(self.ui_manager, "エラー", f"画像保存準備中にエラー:\n{e}"); self.selectionProcessFinished.emit()

    def _save_image_task(self, image, save_path):
        try:
            _, buffer = cv2.imencode('.png', image); buffer.tofile(str(save_path))
            settings = self.config_manager.load_item_setting(Path()); settings['image_path'] = str(save_path)
            self.config_manager.save_item_setting(save_path, settings); self.config_manager.add_item(save_path)
            return True, f"画像を保存しました: {save_path}"
        except Exception as e: return False, f"画像の保存に失敗しました:\n{e}"

    def _on_save_image_done(self, future):
        try:
            success, message = future.result()
            if success:
                self._log(message)
                self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
            else: QMessageBox.critical(self.ui_manager, "エラー", message); self.ui_manager.set_tree_enabled(True)
        finally: self.selectionProcessFinished.emit()
                
    def clear_recognition_area(self):
        self.recognition_area, self.current_window_scale, self.target_hwnd = None, None, None
        self.windowScaleCalculated.emit(0.0)
        if 'dxcam' in sys.modules and self.capture_manager.dxcam_sct: self.capture_manager.dxcam_sct.target_hwnd = None
        self.logger.log("認識範囲をクリアしました。"); self.updateRecAreaPreview.emit(None)
        
    def _update_rec_area_preview(self):
        img = self.capture_manager.capture_frame(region=self.recognition_area) if self.recognition_area else None
        self.updateRecAreaPreview.emit(img)
    
    def get_backup_click_countdown(self) -> float:
        if isinstance(self.state, CountdownState): return self.state.get_remaining_time()
        return -1.0
