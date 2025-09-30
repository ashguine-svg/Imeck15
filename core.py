# core.py

import sys
import threading
import time
import cv2
import numpy as np
import pyautogui
import random
import os
from PySide6.QtCore import QObject, Signal, QThread, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QMouseEvent, QPainter, QPen, QColor, QBrush, QPainterPath, QKeyEvent
from PySide6.QtWidgets import QDialog, QWidget, QLabel, QVBoxLayout, QMessageBox, QApplication, QInputDialog, QFileDialog
from pathlib import Path
import pynput
from concurrent.futures import ThreadPoolExecutor
from threading import Timer

import shutil
import subprocess
if sys.platform == 'win32':
    import pygetwindow as gw
    import win32gui
    import win32con
    import win32api
else:
    gw = None
    win32gui = None


from capture import CaptureManager, DXCAM_AVAILABLE
from config import ConfigManager

INPUT_BLOCK_AVAILABLE = False
if sys.platform == 'win32':
    try:
        import ctypes
        block_input_func = ctypes.windll.user32.BlockInput
        block_input_func.argtypes = [ctypes.wintypes.BOOL]
        block_input_func.restype = ctypes.wintypes.BOOL
        INPUT_BLOCK_AVAILABLE = True
        print("[INFO] User input blocking is available (requires admin rights).")
    except (ImportError, AttributeError, OSError):
        def block_input_func(block):
            pass
        INPUT_BLOCK_AVAILABLE = False
        print("[WARN] User input blocking is not available on this system.")
else:
    def block_input_func(block):
        pass
    print("[INFO] User input blocking is disabled on non-Windows OS.")


def block_input(block: bool):
    if INPUT_BLOCK_AVAILABLE:
        try:
            block_input_func(block)
        except Exception as e:
            print(f"[ERROR] Failed to change input block state: {e}")

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


class SelectionOverlay(QWidget):
    selectionComplete = Signal(tuple)
    def __init__(self, parent=None, initial_rect=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        self.setGeometry(QApplication.primaryScreen().geometry())
        self.setMouseTracking(True)
        self.start_pos, self.end_pos, self.initial_rect = None, None, initial_rect
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.initial_rect = None
            self.start_pos, self.end_pos = event.pos(), event.pos()
            self.update()
    def mouseMoveEvent(self, event: QMouseEvent):
        if self.start_pos is not None:
            self.end_pos = event.pos()
            self.update()
    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton and self.start_pos is not None:
            x1, y1 = min(self.start_pos.x(), event.pos().x()), min(self.start_pos.y(), event.pos().y())
            x2, y2 = max(self.start_pos.x(), event.pos().x()), max(self.start_pos.y(), event.pos().y())
            if x2 - x1 > 0 and y2 - y1 > 0: self.selectionComplete.emit((x1, y1, x2 + 1, y2 + 1))
            self.close(); self.deleteLater()
    def paintEvent(self, event):
        painter = QPainter(self)
        outer_path, inner_path = QPainterPath(), QPainterPath()
        outer_path.addRect(self.rect())
        current_rect = None
        if self.start_pos and self.end_pos: current_rect = QRect(self.start_pos, self.end_pos).normalized()
        elif self.initial_rect: current_rect = self.initial_rect
        if current_rect:
            inner_path.addRect(current_rect)
            painter.setPen(QPen(QColor(0, 255, 255), 2))
            painter.drawRect(current_rect)
        final_path = outer_path.subtracted(inner_path)
        painter.fillPath(final_path, QBrush(QColor(0, 0, 0, 100)))
    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.initial_rect:
            coords = (self.initial_rect.left(), self.initial_rect.top(), self.initial_rect.right() + 1, self.initial_rect.bottom() + 1)
            self.selectionComplete.emit(coords)
            self.close(); self.deleteLater()
        elif event.key() == Qt.Key_Escape: self.close(); self.deleteLater()


class WindowSelectionListener(pynput.mouse.Listener):
    def __init__(self, callback):
        super().__init__(on_click=self.on_click)
        self.callback = callback
    def on_click(self, x, y, button, pressed):
        if pressed and button == pynput.mouse.Button.left: self.callback(x, y); return False

def _match_template_task(screen_image, template_data):
    path, settings = template_data['path'], template_data['settings']
    template_image = template_data['template']
    scale = template_data['scale'] 
    
    threshold = settings.get('threshold', 0.8)
    screen_to_match = screen_image

    if isinstance(screen_to_match, cv2.UMat):
        screen_to_match = screen_to_match.get()

    screen_channels = 1 if len(screen_to_match.shape) == 2 else screen_to_match.shape[2]
    template_channels = 1 if len(template_image.shape) == 2 else template_image.shape[2]

    if screen_channels == 1 and template_channels == 3:
        template_image = cv2.cvtColor(template_image, cv2.COLOR_BGR2GRAY)

    if template_image.shape[0] > screen_to_match.shape[0] or template_image.shape[1] > screen_to_match.shape[1]:
        return None

    result = cv2.matchTemplate(screen_to_match, template_image, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)

    if max_val >= threshold:
        h, w = template_image.shape[:2]
        return {
            'path': path, 'settings': settings, 'location': max_loc,
            'confidence': max_val, 'scale': scale,
            'rect': (max_loc[0], max_loc[1], max_loc[0] + w, max_loc[1] + h)
        }
    return None


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

    def __init__(self, ui_manager, capture_manager, config_manager, logger, performance_monitor):
        super().__init__()
        self.ui_manager, self.capture_manager, self.config_manager, self.logger = ui_manager, capture_manager, config_manager, logger
        self.performance_monitor = performance_monitor
        self.logger.log(OPENCL_STATUS_MESSAGE)
        self.is_monitoring = False
        self._monitor_thread = None
        self._click_count = 0
        self.template_cache = {}
        self.recognition_area = None
        self._is_capturing_for_registration = False
        self.last_successful_click_pos = None
        self.backup_image_settings = None
        self.current_image_path = None
        self.current_image_settings = None
        self.current_image_mat = None
        cpu_cores = os.cpu_count() or 1
        worker_threads = max(2, min(cpu_cores, 4))
        self.thread_pool = ThreadPoolExecutor(max_workers=worker_threads)
        self.logger.log(f"CPUコア数: {cpu_cores}, 認識スレッド数: {worker_threads}")
        self.cache_lock = threading.Lock()
        self.right_click_timer = None
        self.last_right_click_time = 0
        self.DOUBLE_CLICK_INTERVAL = 0.3
        self.mouse_listener = pynput.mouse.Listener(on_click=self._on_global_click)
        self.mouse_listener.start()
        self._showUiSignal.connect(self._show_ui_safe)
        self._areaSelectedForProcessing.connect(self.handle_area_selection)
        self.startMonitoringRequested.connect(self.start_monitoring)
        self.stopMonitoringRequested.connect(self.stop_monitoring)
        self.app_config = self.ui_manager.app_config
        self.current_window_scale = None
        self._pending_window_info = None
        self._cooldown_until = 0
        self._last_normal_match_time = 0
        self.on_app_config_changed()

    def set_opencl_enabled(self, enabled: bool):
        if OPENCL_AVAILABLE:
            try:
                cv2.ocl.setUseOpenCL(enabled)
                status = "有効" if cv2.ocl.useOpenCL() else "無効"
                self.logger.log(f"OpenCLを{status}に設定しました。")
            except Exception as e:
                self.logger.log(f"OpenCLの設定変更中にエラーが発生しました: {e}")
    
    def on_app_config_changed(self):
        self.app_config = self.ui_manager.app_config
        self.capture_manager.set_capture_method(self.app_config.get('capture_method', 'dxcam'))
        self.frame_skip_rate = self.app_config.get('frame_skip_rate', 2)
        self.set_opencl_enabled(self.app_config.get('use_opencl', True))
        self.logger.log(f"アプリ設定変更: キャプチャ={self.capture_manager.current_method}, スキップ={self.frame_skip_rate}, OpenCL={cv2.ocl.useOpenCL() if OPENCL_AVAILABLE else 'N/A'}")

    def _show_ui_safe(self):
        if self.ui_manager:
            self.ui_manager.show()
            self.ui_manager.activateWindow()

    def _on_global_click(self, x, y, button, pressed):
        if button == pynput.mouse.Button.right and pressed:
            current_time = time.time()
            if current_time - self.last_right_click_time < self.DOUBLE_CLICK_INTERVAL:
                if self.right_click_timer is not None:
                    self.right_click_timer.cancel(); self.right_click_timer = None
                self.logger.log("右ダブルクリック検出: 監視を開始します。")
                self.startMonitoringRequested.emit()
            else:
                self.right_click_timer = Timer(self.DOUBLE_CLICK_INTERVAL, self._handle_single_right_click)
                self.right_click_timer.start()
            self.last_right_click_time = current_time

    def _handle_single_right_click(self):
        self.logger.log("右クリック検出: 監視を停止します。"); self.stopMonitoringRequested.emit()

    def cleanup(self):
        self.stop_monitoring()
        if self.mouse_listener and self.mouse_listener.is_alive(): self.mouse_listener.stop()
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
        reply = QMessageBox.question(self.ui_manager, "削除の確認", f"本当に '{name.lstrip('📁 ')}' を削除しますか？\n(フォルダの場合、中のファイルもすべて削除されます)", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            try:
                self.ui_manager.set_tree_enabled(False)
                self.config_manager.remove_item(path_str)
                self.logger.log(f"'{name}' を削除しました。")
                future = self.thread_pool.submit(self._build_template_cache); future.add_done_callback(self._on_cache_build_done)
            except Exception as e:
                self.logger.log(f"'{name}' の削除に失敗しました: {e}"); QMessageBox.critical(self.ui_manager, "エラー", f"削除に失敗しました:\n{e}")
                self.ui_manager.set_tree_enabled(True)

    def _toggle_and_rebuild_cache(self, folder_path):
        if folder_path and Path(folder_path).is_dir():
            is_excluded = self.config_manager.toggle_folder_exclusion(folder_path)
            status = "除外" if is_excluded else "対象"
            self.logger.log(f"フォルダ '{Path(folder_path).name}' を認識{status}にしました。")
            self._build_template_cache()

    def toggle_folder_exclusion(self, folder_path):
        future = self.thread_pool.submit(self._toggle_and_rebuild_cache, folder_path)
        future.add_done_callback(self._on_cache_build_done)
        
    def create_folder(self):
        folder_name, ok = QInputDialog.getText(self.ui_manager, "フォルダ作成", "新しいフォルダの名前を入力してください:")
        if ok and folder_name:
            success, message = self.config_manager.create_folder(folder_name)
            if success:
                self.logger.log(message); self.ui_manager.update_image_tree()
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
            if success: self.logger.log(message); self.ui_manager.update_image_tree()
            else: QMessageBox.critical(self.ui_manager, "エラー", message)

    def move_item_out_of_folder(self):
        source_path_str, name = self.ui_manager.get_selected_item_path()
        if not source_path_str: QMessageBox.warning(self.ui_manager, "警告", "フォルダから出す画像を選択してください。"); return
        source_path = Path(source_path_str)
        if not source_path.is_file() or source_path.parent == self.config_manager.base_dir:
            QMessageBox.warning(self.ui_manager, "警告", "フォルダの中にある画像ファイルを選択してください。"); return
        dest_folder_path_str = str(self.config_manager.base_dir)
        success, message = self.config_manager.move_item(source_path_str, dest_folder_path_str)
        if success: self.logger.log(message); self.ui_manager.update_image_tree()
        else: QMessageBox.critical(self.ui_manager, "エラー", message)

    def load_image_and_settings(self, file_path: str):
        if file_path is None:
            self.current_image_path, self.current_image_settings, self.current_image_mat = None, None, None
            self.updatePreview.emit(None, None); return
        try:
            self.current_image_path = file_path
            self.current_image_settings = self.config_manager.load_item_setting(Path(file_path))
            with open(file_path, 'rb') as f: file_bytes = np.fromfile(f, np.uint8)
            self.current_image_mat = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
            if self.current_image_mat is None: raise ValueError("画像ファイルのデコードに失敗しました。")
        except Exception as e:
            self.logger.log(f"画像の読み込みに失敗しました: {file_path}, エラー: {e}")
            self.current_image_path, self.current_image_settings, self.current_image_mat = None, None, None
            self.updatePreview.emit(None, None); return
        self._recalculate_and_update(request_save=False)

    def on_image_settings_changed(self, settings: dict):
        if self.current_image_settings: self.current_image_settings.update(settings); self._recalculate_and_update()
    def on_preview_click_settings_changed(self, click_data: dict):
        if self.current_image_settings: self.current_image_settings.update(click_data); self._recalculate_and_update()

    def _recalculate_and_update(self, request_save=True):
        if self.current_image_mat is not None and self.current_image_settings is not None:
            h, w = self.current_image_mat.shape[:2]
            self.current_image_settings['roi_rect'] = self.calculate_roi_rect((w, h), self.current_image_settings)
        self.updatePreview.emit(self.current_image_mat, self.current_image_settings)
        if request_save: self.ui_manager.request_save()

    def calculate_roi_rect(self, image_size, settings):
        if not settings.get('roi_enabled', False): return None
        img_w, img_h = image_size; center_x, center_y = -1, -1
        if settings.get('point_click') and settings.get('click_position'): center_x, center_y = settings['click_position']
        elif settings.get('range_click') and settings.get('click_rect'):
            rect = settings['click_rect']; center_x, center_y = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
        if center_x == -1: return None
        roi_w, roi_h = 200, 200; x1, y1 = center_x - roi_w / 2, center_y - roi_h / 2
        x1_clipped, y1_clipped = max(0, x1), max(0, y1); x2_clipped, y2_clipped = min(img_w, x1 + roi_w), min(img_h, y1 + roi_h)
        return (int(x1_clipped), int(y1_clipped), int(x2_clipped), int(y2_clipped))

    def save_current_settings(self):
        if self.current_image_path and self.current_image_settings:
            self.config_manager.save_item_setting(Path(self.current_image_path), self.current_image_settings)
            self.logger.log(f"設定 '{Path(self.current_image_path).name}' を保存しました。")

    def load_images_into_manager(self, file_paths):
        self.ui_manager.set_tree_enabled(False)
        for file_path in file_paths: self.config_manager.add_item(Path(file_path))
        self.updateLog.emit(f"画像を{len(file_paths)}個追加しました。")
        self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)

    def on_order_changed(self):
        self.ui_manager.set_tree_enabled(False)
        self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)

    def _build_template_cache(self):
        with self.cache_lock:
            self.template_cache.clear()
            self.backup_image_settings = None

            auto_scale_settings = self.app_config.get('auto_scale', {})
            use_auto_scale = auto_scale_settings.get('enabled', False)
            
            if self.current_window_scale is not None:
                center_scale = self.current_window_scale
                self.logger.log(f"ウィンドウ基準スケール ({center_scale:.3f}) を使用します。")
            else:
                center_scale = auto_scale_settings.get('center', 1.0)
            
            scales = [center_scale]
            if use_auto_scale:
                range_ = auto_scale_settings.get('range', 0.2)
                steps = auto_scale_settings.get('steps', 5)
                if steps > 1:
                    scales = np.linspace(center_scale - range_, center_scale + range_, steps)
                self.logger.log(f"自動スケール探索有効: {len(scales)}段階で探索 (中心: {center_scale:.3f})。")
            
            hierarchical_list = self.config_manager.get_hierarchical_list()
            items_to_process = []
            for item_data in hierarchical_list:
                if item_data['type'] == 'folder' and not item_data.get('is_excluded', False):
                    items_to_process.extend(item_data['children'])
                elif item_data['type'] == 'image':
                    items_to_process.append(item_data)
            
            for item_data in items_to_process:
                self._process_item_for_cache(item_data, scales)
            
            self.logger.log(f"テンプレートキャッシュを構築しました。({len(self.template_cache)}個の画像を認識対象)")
            if self.backup_image_settings: self.logger.log(f"バックアップクリックが画像 '{Path(self.backup_image_settings['image_path']).name}' に設定されています。")

    def _process_item_for_cache(self, item_data, scales):
        try:
            path = item_data['path']
            with open(path, 'rb') as f: file_bytes = np.fromfile(f, np.uint8)
            original_image = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)

            if original_image is not None:
                settings = self.config_manager.load_item_setting(Path(path))
                
                scaled_templates = []
                for scale in scales:
                    if scale <= 0: continue
                    h, w = original_image.shape[:2]
                    new_w, new_h = int(w * scale), int(h * scale)
                    if new_w > 0 and new_h > 0:
                        inter = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
                        resized_image = cv2.resize(original_image, (new_w, new_h), interpolation=inter)
                        scaled_templates.append({'scale': scale, 'image': resized_image})

                self.template_cache[path] = {
                    'settings': settings,
                    'path': path,
                    'scaled_templates': scaled_templates,
                    'best_scale': None if len(scales) > 1 else scales[0]
                }
                if settings.get('backup_click', False):
                    self.backup_image_settings = settings
        except Exception as e:
            self.updateLog.emit(f"キャッシュ作成失敗: {item_data.get('name')}, {e}")

    def start_monitoring(self):
        if not self.is_monitoring:
            self.is_monitoring = True
            self._click_count = 0
            self.last_successful_click_pos = None
            self._cooldown_until = 0
            self._last_normal_match_time = 0
            self.ui_manager.set_tree_enabled(False)
            self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
            self._monitor_thread = threading.Thread(target=self._monitoring_loop, daemon=True)
            self._monitor_thread.start()
            self.updateStatus.emit("監視中...", "blue")
            self.logger.log("監視を開始しました。")

    def stop_monitoring(self):
        if self.is_monitoring:
            self.is_monitoring = False
            if self._monitor_thread and self._monitor_thread.is_alive(): self._monitor_thread.join(timeout=1.0)
            
            with self.cache_lock:
                for path in self.template_cache:
                    self.template_cache[path]['best_scale'] = None
            
            self.updateStatus.emit("待機中", "green"); self.logger.log("監視を停止しました。")
    
    def _monitoring_loop(self):
        last_match_time_map = {}
        no_match_streak = 0
        
        fps_last_time = time.time()
        frame_counter = 0

        while self.is_monitoring:
            current_time = time.time()

            delta_time = current_time - fps_last_time
            frame_counter += 1
            if delta_time >= 1.0:
                fps = frame_counter / delta_time
                self.fpsUpdated.emit(fps)
                fps_last_time = current_time
                frame_counter = 0
            
            if self._cooldown_until > current_time:
                time.sleep(0.5)
                continue

            if self.backup_image_settings and self._last_normal_match_time > 0:
                backup_interval = self.backup_image_settings.get('backup_time', 300.0)
                if current_time - self._last_normal_match_time > backup_interval:
                    self.logger.log(f"{backup_interval:.1f}秒間通常マッチなしのためバックアップクリックを実行。")
                    self._execute_backup_click()
                    self._last_normal_match_time = time.time()
                    time.sleep(1.0)
                    continue
            
            sleep_duration = 0.1
            try:
                if (frame_counter % self.frame_skip_rate) != 0: 
                    time.sleep(0.01)
                    continue
                
                screen_bgr = self.capture_manager.capture_frame(region=self.recognition_area)
                if screen_bgr is None:
                    self.updateLog.emit("画面のキャプチャに失敗しました。")
                    sleep_duration = 1.0
                    continue
                
                screen_gray = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)

                match_info = self._find_best_match(screen_bgr, screen_gray)
                if match_info:
                    no_match_streak = 0
                    if not match_info['settings'].get('backup_click', False):
                        self._last_normal_match_time = time.time()
                    self._handle_match(match_info, last_match_time_map)
                else:
                    no_match_streak += 1
                    sleep_duration = min(1.0, 0.1 + no_match_streak * 0.1)
            except Exception as e:
                self.updateLog.emit(f"監視ループでエラーが発生しました: {e}")
                sleep_duration = 1.0
            finally:
                end_time = time.time() + sleep_duration
                while time.time() < end_time:
                    if not self.is_monitoring: return
                    time.sleep(0.05)

    def _find_best_match(self, screen_bgr, screen_gray):
        futures = []
        with self.cache_lock:
            if not self.template_cache: return None
            
            use_global_grayscale = self.app_config.get('grayscale_matching', False)

            for path, data in self.template_cache.items():
                is_search_phase = (data['best_scale'] is None)
                should_use_grayscale = is_search_phase or use_global_grayscale
                
                if is_search_phase:
                    templates_to_search = data['scaled_templates']
                else:
                    templates_to_search = [t for t in data['scaled_templates'] if t['scale'] == data['best_scale']]
                    if not templates_to_search:
                        templates_to_search = data['scaled_templates']
                
                screen_to_use = screen_gray if should_use_grayscale else screen_bgr

                for t in templates_to_search:
                    task_data = { 'path': path, 'settings': data['settings'], 'template': t['image'], 'scale': t['scale'] }
                    if cv2.ocl.useOpenCL():
                         try: screen_to_use_umat = cv2.UMat(screen_to_use)
                         except Exception: screen_to_use_umat = screen_to_use
                    else:
                         screen_to_use_umat = screen_to_use
                    
                    futures.append(self.thread_pool.submit(_match_template_task, screen_to_use_umat, task_data))
        
        results = [f.result() for f in futures if f.result() is not None]
        if not results: return None

        best_match = max(results, key=lambda r: r['confidence'])
        
        with self.cache_lock:
            cache_item = self.template_cache.get(best_match['path'])
            if cache_item and cache_item['best_scale'] is None:
                 cache_item['best_scale'] = best_match['scale']
                 log_msg = f"最適スケール発見: {Path(best_match['path']).name} @ {best_match['scale']:.2f}倍 (信頼度: {best_match['confidence']:.2f})"
                 self.updateLog.emit(log_msg)
                 self.bestScaleFound.emit(best_match['path'], best_match['scale'])
        
        return best_match

    def _handle_match(self, match_info, last_match_time_map):
        path, settings = match_info['path'], match_info['settings']
        interval = settings.get('interval_time', 1.5)
        current_time = time.time()

        if current_time - last_match_time_map.get(path, 0) > interval:
            self._execute_click(match_info)
            last_match_time_map[path] = current_time
            self._cooldown_until = current_time + interval
            
    def _execute_click(self, match_info):
        block_input(True)
        try:
            settings, rect = match_info['settings'], match_info['rect']
            scale = match_info.get('scale', 1.0)

            offset_x, offset_y = (self.recognition_area[0], self.recognition_area[1]) if self.recognition_area else (0, 0)
            click_x, click_y = 0, 0

            if settings.get('range_click') and settings.get('click_rect'):
                click_rect = settings['click_rect']
                x1 = offset_x + rect[0] + (click_rect[0] * scale)
                y1 = offset_y + rect[1] + (click_rect[1] * scale)
                x2 = offset_x + rect[0] + (click_rect[2] * scale)
                y2 = offset_y + rect[1] + (click_rect[3] * scale)

                if settings.get('random_click', True):
                    int_x1, int_x2 = int(min(x1, x2)), int(max(x1, x2))
                    int_y1, int_y2 = int(min(y1, y2)), int(max(y1, y2))
                    click_x = int_x1 if int_x1 >= int_x2 else random.randint(int_x1, int_x2)
                    click_y = int_y1 if int_y1 >= int_y2 else random.randint(int_y1, int_y2)
                else: click_x, click_y = (x1 + x2) / 2, (y1 + y2) / 2
            elif settings.get('point_click') and settings.get('click_position'):
                click_pos = settings['click_position']
                click_x = offset_x + rect[0] + (click_pos[0] * scale)
                click_y = offset_y + rect[1] + (click_pos[1] * scale)
            else:
                click_x, click_y = offset_x + (rect[0] + rect[2]) / 2, offset_y + (rect[1] + rect[3]) / 2
            
            pyautogui.click(click_x, click_y)
            self._click_count += 1
            self.last_successful_click_pos = (click_x, click_y)
            
            log_msg = f"クリック: {Path(settings['image_path']).name} @({int(click_x)}, {int(click_y)}) conf:{match_info['confidence']:.2f}"
            if 'scale' in match_info:
                log_msg += f" scale:{match_info['scale']:.3f}"
            self.updateLog.emit(log_msg)
        except Exception as e:
            self.updateLog.emit(f"クリック実行中にエラーが発生しました: {e}")
        finally:
            block_input(False)

    def _execute_backup_click(self):
        if self.last_successful_click_pos:
            block_input(True)
            try:
                pyautogui.click(*self.last_successful_click_pos)
                self.updateLog.emit(f"バックアップクリック実行: @{self.last_successful_click_pos}")
            finally:
                block_input(False)
        else:
            self.updateLog.emit("バックアップクリック失敗: 最後に成功したクリック位置がありません。")
        
    def set_recognition_area(self, method: str):
        self.selectionProcessStarted.emit()
        self.ui_manager.hide()
        if self.performance_monitor:
            self.performance_monitor.hide()

        if method == "rectangle":
            self.current_window_scale = None
            self.windowScaleCalculated.emit(0.0)
            self.selection_overlay = SelectionOverlay(); self.selection_overlay.selectionComplete.connect(self._areaSelectedForProcessing.emit); self.selection_overlay.showFullScreen()
        elif method == "window":
            self.window_selection_listener = WindowSelectionListener(self._handle_window_click_for_selection); self.window_selection_listener.start()
            
    def _handle_window_click_for_selection(self, x, y):
        if sys.platform == 'win32':
            self._handle_window_click_for_selection_windows(x, y)
        else:
            self._handle_window_click_for_selection_linux(x, y)

    def _handle_window_click_for_selection_windows(self, x, y):
        try:
            hwnd = win32gui.WindowFromPoint((x, y))
            if not hwnd: return
            
            # DXCamが利用可能な場合、ウィンドウハンドルをキャプチャマネージャーに設定
            if 'dxcam' in sys.modules:
                self.capture_manager.dxcam_sct.target_hwnd = hwnd

            client_rect_win = win32gui.GetClientRect(hwnd)
            left, top = win32gui.ClientToScreen(hwnd, (0, 0))
            right, bottom = left + client_rect_win[2], top + client_rect_win[3]
            rect = (left, top, right, bottom)
            
            if self._is_capturing_for_registration:
                self._areaSelectedForProcessing.emit(rect)
                self.selectionProcessFinished.emit()
                return

            title = win32gui.GetWindowText(hwnd)
            self._pending_window_info = {
                "title": title,
                "dims": {'width': client_rect_win[2], 'height': client_rect_win[3]},
                "rect": rect
            }

            if title:
                scales_data = self.config_manager.load_window_scales()
                if title not in scales_data:
                    self.askToSaveWindowBaseSizeSignal.emit(title)
                else: self.process_base_size_prompt_response(False)
            else:
                self.logger.log("警告: ウィンドウタイトルが取得できませんでした。")
                self.process_base_size_prompt_response(False)
        except Exception as e:
            self.logger.log(f"ウィンドウ領域の取得に失敗: {e}");
            self._showUiSignal.emit(); self.selectionProcessFinished.emit()
    
    def _handle_window_click_for_selection_linux(self, x, y):
        needed_tools = ['xdotool', 'xwininfo']
        missing_tools = [tool for tool in needed_tools if not shutil.which(tool)]
        if missing_tools:
            self.logger.log(f"エラー: {', '.join(missing_tools)} が見つかりません。")
            self.logger.log("ウィンドウ選択機能を使用するには、これらをインストールしてください。")
            self.logger.log("(例: sudo apt install xdotool x11-utils)")
            self._showUiSignal.emit(); self.selectionProcessFinished.emit()
            return
        
        try:
            # 1. マウス下のウィンドウIDを取得
            proc_id = subprocess.run(['xdotool', 'getmouselocation'], capture_output=True, text=True, check=True)
            window_id = [line.split(':')[1] for line in proc_id.stdout.strip().split() if 'window' in line][0]

            # 2. ウィンドウのジオメトリを詳細に取得 (xwininfo)
            proc_info = subprocess.run(['xwininfo', '-id', window_id], capture_output=True, text=True, check=True)
            info = proc_info.stdout
            
            # 3. xwininfoの出力から必要な情報をすべてパースする
            abs_x, abs_y, rel_x, rel_y, width, height = 0, 0, 0, 0, 0, 0
            title = f"Window (ID: {window_id})"
            for line in info.split('\n'):
                line = line.strip()
                if line.startswith('Absolute upper-left X:'): abs_x = int(line.split(':')[1].strip())
                elif line.startswith('Absolute upper-left Y:'): abs_y = int(line.split(':')[1].strip())
                elif line.startswith('Relative upper-left X:'): rel_x = int(line.split(':')[1].strip())
                elif line.startswith('Relative upper-left Y:'): rel_y = int(line.split(':')[1].strip())
                elif line.startswith('Width:'): width = int(line.split(':')[1].strip())
                elif line.startswith('Height:'): height = int(line.split(':')[1].strip())
                elif 'xwininfo: Window id:' in line and '"' in line:
                    title = line.split('"')[1]

            # ★★★ 変更点: ここからクライアント領域の計算ロジックを修正 ★★★
            # 4. クライアント領域を計算する
            #   - 仮説: 一部のデスクトップ環境では、`Absolute X/Y` と `Width/Height` が
            #     直接クライアント領域を示している。
            #   - そのため、`Relative X/Y` (枠のオフセット) は加算しない。
            
            client_left = abs_x
            client_top = abs_y
            client_width = width
            client_height = height
            
            client_right = client_left + client_width
            client_bottom = client_top + client_height

            rect = (client_left, client_top, client_right, client_bottom)
            
            self.logger.log(f"ウィンドウ '{title}' を検出。")
            self.logger.log(f"  - 取得したジオメトリ (xwininfo): X={abs_x}, Y={abs_y}, W={client_width}, H={client_height}")
            self.logger.log(f"  - 計算後のクライアント領域: {rect}")
            # ★★★ 変更点: ここまで ★★★

            if self._is_capturing_for_registration:
                self._areaSelectedForProcessing.emit(rect)
                self.selectionProcessFinished.emit()
                return

            self._pending_window_info = {
                "title": title,
                "dims": {'width': client_width, 'height': client_height},
                "rect": rect
            }
            
            scales_data = self.config_manager.load_window_scales()
            if title not in scales_data:
                self.askToSaveWindowBaseSizeSignal.emit(title)
            else: self.process_base_size_prompt_response(False)
        except Exception as e:
            self.logger.log(f"Linuxでのウィンドウ領域取得に失敗: {e}")
            self._showUiSignal.emit(); self.selectionProcessFinished.emit()


    def process_base_size_prompt_response(self, save_as_base: bool):
        try:
            info = self._pending_window_info
            if not info: return

            title = info['title']
            current_dims = info['dims']
            rect = info['rect']
            
            scales_data = self.config_manager.load_window_scales()

            if save_as_base:
                scales_data[title] = current_dims
                self.config_manager.save_window_scales(scales_data)
                self.current_window_scale = 1.0
                self.logger.log(f"ウィンドウ '{title}' の基準サイズを保存しました。")
            elif title and title in scales_data:
                base_dims = scales_data[title]
                if base_dims['width'] > 0:
                    scale = current_dims['width'] / base_dims['width']
                    self.current_window_scale = scale
                    self.logger.log(f"ウィンドウ '{title}' の計算スケール: {scale:.3f}")
                else:
                    self.current_window_scale = None
            else:
                self.current_window_scale = None

            self.windowScaleCalculated.emit(self.current_window_scale if self.current_window_scale is not None else 0.0)
            self._areaSelectedForProcessing.emit(rect)

        except Exception as e:
            self.logger.log(f"基準サイズ応答の処理中にエラー: {e}")
        finally:
            self._pending_window_info = None
            self._showUiSignal.emit(); self.selectionProcessFinished.emit()

    def handle_area_selection(self, coords):
        if self._is_capturing_for_registration:
            self._is_capturing_for_registration = False
            self._save_captured_image(coords)
        else:
            self.recognition_area = coords
            self.logger.log(f"認識範囲を設定: {coords}")
            self._update_rec_area_preview()
            self.selectionProcessFinished.emit()
            self.ui_manager.show()

        if hasattr(self, 'selection_overlay'):
            self.selection_overlay = None
        
    def _get_filename_from_user(self):
        """OSに応じて最適な方法でユーザーからファイル名を取得する。"""
        if sys.platform == 'win32':
            # WindowsではQInputDialogを使用
            file_name, ok = QInputDialog.getText(self.ui_manager, "ファイル名を入力", "保存するファイル名を入力してください:")
            return file_name, ok
        else:
            # LinuxではZenityを使用
            if not shutil.which('zenity'):
                QMessageBox.warning(self.ui_manager, "エラー", "名前入力機能には 'zenity' が必要です。\n'sudo apt install zenity' でインストールしてください。")
                return None, False
            
            try:
                command = [
                    'zenity', '--entry',
                    '--title=ファイル名を入力',
                    '--text=保存するファイル名を入力してください（拡張子不要）:'
                ]
                result = subprocess.run(command, capture_output=True, text=True, check=False) # check=Falseにする
                
                if result.returncode == 0: # OKが押された
                    return result.stdout.strip(), True
                else: # キャンセルまたはウィンドウが閉じられた
                    return None, False
            except Exception as e:
                self.logger.log(f"Zenityの呼び出し中にエラー: {e}")
                QMessageBox.critical(self.ui_manager, "エラー", f"Zenityの呼び出しに失敗しました:\n{e}")
                return None, False

    def _save_captured_image(self, region_coords):
        try:
            captured_image = self.capture_manager.capture_frame(region=region_coords)
            
            self._show_ui_safe()
            if self.performance_monitor and not self.performance_monitor.isVisible():
                self.performance_monitor.show()

            if captured_image is None: 
                QMessageBox.warning(self.ui_manager, "エラー", "画像のキャプチャに失敗しました。")
                return
            
            file_name, ok = self._get_filename_from_user()
            
            if ok and file_name:
                self.ui_manager.set_tree_enabled(False)
                save_path = self.config_manager.base_dir / f"{file_name}.png"
                
                if save_path.exists():
                    reply = QMessageBox.question(self.ui_manager, "上書き確認", f"ファイル '{save_path.name}' は既に存在します。\n上書きしますか？",
                                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
                    if reply == QMessageBox.StandardButton.No:
                        self.ui_manager.set_tree_enabled(True)
                        return

                try:
                    _, buffer = cv2.imencode('.png', captured_image)
                    buffer.tofile(str(save_path))

                    default_settings = self.config_manager.load_item_setting(Path())
                    default_settings['image_path'] = str(save_path)
                    self.config_manager.save_item_setting(save_path, default_settings)
                    
                    self.config_manager.add_item(save_path)
                    
                    self.logger.log(f"画像を保存しました: {save_path}")
                    self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
                except Exception as e:
                    QMessageBox.critical(self.ui_manager, "エラー", f"画像の保存に失敗しました:\n{e}")
                    self.ui_manager.set_tree_enabled(True)
        finally:
            self.selectionProcessFinished.emit()
            if not self.ui_manager.isVisible():
                self._show_ui_safe()
                
    def clear_recognition_area(self):
        self.recognition_area = None
        self.current_window_scale = None
        self.windowScaleCalculated.emit(0.0)
        # DXCamが利用可能な場合、ターゲットウィンドウをリセット
        if 'dxcam' in sys.modules:
            self.capture_manager.dxcam_sct.target_hwnd = None
        self.logger.log("認識範囲をクリアしました。");
        self.updateRecAreaPreview.emit(None)
        
    def _update_rec_area_preview(self):
        img = self.capture_manager.capture_frame(region=self.recognition_area) if self.recognition_area else None
        self.updateRecAreaPreview.emit(img)

    def get_backup_click_countdown(self) -> float:
        """バックアップクリックが有効な場合に、次の実行までの残り時間を返す"""
        if self.backup_image_settings and self._last_normal_match_time > 0:
            backup_interval = self.backup_image_settings.get('backup_time', 300.0)
            elapsed_time = time.time() - self._last_normal_match_time
            remaining_time = backup_interval - elapsed_time
            return remaining_time
        return -1.0
