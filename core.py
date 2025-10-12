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
from pynput import mouse, keyboard
from concurrent.futures import ThreadPoolExecutor
from threading import Timer

from collections import deque
from PIL import Image
import imagehash

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


def calculate_phash(image):
    """OpenCVの画像(Numpy配列)からpHashを計算する"""
    if image is None:
        return None
    try:
        pil_image = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        return imagehash.phash(pil_image)
    except Exception:
        return None


class SelectionOverlay(QWidget):
    selectionComplete = Signal(tuple)
    selectionCancelled = Signal()

    def __init__(self, parent=None, initial_rect=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        self.setGeometry(QApplication.primaryScreen().geometry())
        self.setMouseTracking(True)
        self.start_pos, self.end_pos, self.initial_rect = None, None, initial_rect
        self.dpr = self.screen().devicePixelRatio() if self.screen() else 1.0

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.initial_rect = None
            self.start_pos = event.position().toPoint() * self.dpr
            self.end_pos = self.start_pos
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self.start_pos is not None:
            self.end_pos = event.position().toPoint() * self.dpr
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton and self.start_pos is not None:
            end_pos_scaled = event.position().toPoint() * self.dpr
            x1 = min(self.start_pos.x(), end_pos_scaled.x())
            y1 = min(self.start_pos.y(), end_pos_scaled.y())
            x2 = max(self.start_pos.x(), end_pos_scaled.x())
            y2 = max(self.start_pos.y(), end_pos_scaled.y())

            rect_tuple = (int(x1), int(y1), int(x2) + 1, int(y2) + 1)

            if rect_tuple[2] - rect_tuple[0] > 1 and rect_tuple[3] - rect_tuple[1] > 1:
                self.selectionComplete.emit(rect_tuple)
            self.close()
            self.deleteLater()

    def paintEvent(self, event):
        painter = QPainter(self)
        outer_path, inner_path = QPainterPath(), QPainterPath()
        outer_path.addRect(self.rect())
        current_rect = None

        if self.start_pos and self.end_pos:
            start_pos_logical = self.start_pos / self.dpr
            end_pos_logical = self.end_pos / self.dpr
            current_rect = QRect(start_pos_logical, end_pos_logical).normalized()
        elif self.initial_rect:
            current_rect = self.initial_rect

        if current_rect:
            inner_path.addRect(current_rect)
            painter.setPen(QPen(QColor(0, 255, 255), 2))
            painter.drawRect(current_rect)

        final_path = outer_path.subtracted(inner_path)
        painter.fillPath(final_path, QBrush(QColor(0, 0, 0, 100)))

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.initial_rect:
            x1 = self.initial_rect.left() * self.dpr
            y1 = self.initial_rect.top() * self.dpr
            x2 = self.initial_rect.right() * self.dpr
            y2 = self.initial_rect.bottom() * self.dpr
            coords = (int(x1), int(y1), int(x2) + 1, int(y2) + 1)
            self.selectionComplete.emit(coords)
            self.close()
            self.deleteLater()
        elif event.key() == Qt.Key_Escape:
            self.selectionCancelled.emit()
            self.close()
            self.deleteLater()


class WindowSelectionListener(mouse.Listener):
    def __init__(self, callback):
        super().__init__(on_click=self.on_click)
        self.callback = callback
    def on_click(self, x, y, button, pressed):
        if pressed and button == mouse.Button.left: self.callback(x, y); return False

def _match_template_task(screen_image, template_data, screen_shape, template_shape):
    path, settings = template_data['path'], template_data['settings']
    template_image = template_data['template']
    scale = template_data['scale'] 
    
    threshold = settings.get('threshold', 0.8)

    s_h, s_w = screen_shape
    t_h, t_w = template_shape

    if t_h > s_h or t_w > s_w:
        return None

    result = cv2.matchTemplate(screen_image, template_image, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)

    if max_val >= threshold:
        return {
            'path': path, 'settings': settings, 'location': max_loc,
            'confidence': max_val, 'scale': scale,
            'rect': (max_loc[0], max_loc[1], max_loc[0] + t_w, max_loc[1] + t_h)
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
    askToApplyWindowScaleSignal = Signal(float)


    def __init__(self, ui_manager, capture_manager, config_manager, logger, performance_monitor):
        super().__init__()
        self.ui_manager, self.capture_manager, self.config_manager, self.logger = ui_manager, capture_manager, config_manager, logger
        self.performance_monitor = performance_monitor
        self.logger.log(OPENCL_STATUS_MESSAGE)
        
        self.is_monitoring = False
        self._monitor_thread = None
        self._click_count = 0
        
        self.normal_template_cache = {}
        self.backup_template_cache = {}
        
        self.is_backup_countdown_active = False
        self.backup_countdown_start_time = 0
        self.active_backup_info = None

        self._last_clicked_path = None

        self.recognition_area = None
        self._is_capturing_for_registration = False
        self.current_image_path = None
        self.current_image_settings = None
        self.current_image_mat = None
        
        self.window_selection_listener = None
        self.keyboard_selection_listener = None
        
        self.target_hwnd = None
        
        self.priority_mode_info = {}
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
        self.mouse_listener = mouse.Listener(on_click=self._on_global_click)
        self.mouse_listener.start()
        
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
        
        self.on_app_config_changed()

        # ログの連続出力を抑制するための変数
        self._last_log_message = ""
        self._last_log_time = 0
        self._log_spam_filter = {"画面が不安定なためクリックを保留します。"}

    def _log(self, message: str, force: bool = False):
        """ログメッセージをフィルタリングして出力する"""
        current_time = time.time()
        if not force and \
           message == self._last_log_message and \
           message in self._log_spam_filter and \
           current_time - self._last_log_time < 3.0:
            return  # 3秒以内に同じ抑制対象メッセージが来たら無視

        self.updateLog.emit(message)
        self._last_log_message = message
        self._last_log_time = current_time

    def set_opencl_enabled(self, enabled: bool):
        if OPENCL_AVAILABLE:
            try:
                cv2.ocl.setUseOpenCL(enabled)
                status = "有効" if cv2.ocl.useOpenCL() else "無効"
                self.logger.log(f"OpenCLを{status}に設定しました。")
                if self.is_monitoring:
                    self.logger.log("設定変更を反映するため、キャッシュを再構築します。")
                    self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
            except Exception as e:
                self.logger.log(f"OpenCLの設定変更中にエラーが発生しました: {e}")
    
    def on_app_config_changed(self):
        self.app_config = self.ui_manager.app_config
        self.capture_manager.set_capture_method(self.app_config.get('capture_method', 'dxcam'))
        self.set_opencl_enabled(self.app_config.get('use_opencl', True))
        
        lw_conf = self.app_config.get('lightweight_mode', {})
        is_lw_enabled = lw_conf.get('enabled', False)
        preset = lw_conf.get('preset', '標準')
        
        if is_lw_enabled:
            user_frame_skip = self.app_config.get('frame_skip_rate', 2)
            
            if preset == "標準":
                self.effective_capture_scale = 0.5
                self.effective_frame_skip_rate = user_frame_skip + 5
            elif preset == "パフォーマンス":
                self.effective_capture_scale = 0.4
                self.effective_frame_skip_rate = user_frame_skip + 20
            elif preset == "ウルトラ":
                self.effective_capture_scale = 0.3
                self.effective_frame_skip_rate = user_frame_skip + 25
        else:
            self.effective_capture_scale = 1.0
            self.effective_frame_skip_rate = self.app_config.get('frame_skip_rate', 2)

        self.logger.log(f"アプリ設定変更: キャプチャ={self.capture_manager.current_method}, "
                        f"軽量化={is_lw_enabled}({preset}), "
                        f"実効スケール={self.effective_capture_scale:.2f}, "
                        f"実効スキップ={self.effective_frame_skip_rate}, "
                        f"OpenCL={cv2.ocl.useOpenCL() if OPENCL_AVAILABLE else 'N/A'}")

    def _show_ui_safe(self):
        if self.ui_manager:
            self.ui_manager.show()
            self.ui_manager.activateWindow()

    def _on_global_click(self, x, y, button, pressed):
        if button == mouse.Button.right and pressed:
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

    def on_folder_settings_changed(self):
        self.logger.log("フォルダ設定が変更されました。キャッシュを再構築します。")
        self.ui_manager.set_tree_enabled(False)
        self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
        
    def create_folder(self):
        folder_name, ok = QInputDialog.getText(self.ui_manager, "フォルダ作成", "新しいフォルダの名前を入力してください:")
        if ok and folder_name:
            success, message = self.config_manager.create_folder(folder_name)
            if success:
                self.logger.log(message)
                self.ui_manager.update_image_tree()
                self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
            else: 
                QMessageBox.warning(self.ui_manager, "エラー", message)

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
                self.logger.log(message)
                self.ui_manager.update_image_tree()
                self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
            else: 
                QMessageBox.critical(self.ui_manager, "エラー", message)

    def move_item_out_of_folder(self):
        source_path_str, name = self.ui_manager.get_selected_item_path()
        if not source_path_str: QMessageBox.warning(self.ui_manager, "警告", "フォルダから出す画像を選択してください。"); return
        source_path = Path(source_path_str)
        if not source_path.is_file() or source_path.parent == self.config_manager.base_dir:
            QMessageBox.warning(self.ui_manager, "警告", "フォルダの中にある画像ファイルを選択してください。"); return
        dest_folder_path_str = str(self.config_manager.base_dir)
        success, message = self.config_manager.move_item(source_path_str, dest_folder_path_str)
        if success: 
            self.logger.log(message)
            self.ui_manager.update_image_tree()
            self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)
        else: 
            QMessageBox.critical(self.ui_manager, "エラー", message)

    def load_image_and_settings(self, file_path: str):
        if file_path is None or Path(file_path).is_dir():
            self.current_image_path, self.current_image_settings, self.current_image_mat = None, None, None
            self.updatePreview.emit(None, None)
            return
            
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
        center_x, center_y = -1, -1
        if settings.get('point_click') and settings.get('click_position'):
            center_x, center_y = settings['click_position']
        elif settings.get('range_click') and settings.get('click_rect'):
            rect = settings['click_rect']
            center_x, center_y = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
        
        if center_x == -1: return None

        roi_w, roi_h = 200, 200
        x1 = center_x - roi_w / 2
        y1 = center_y - roi_h / 2
        x2 = x1 + roi_w
        y2 = y1 + roi_h
        return (int(x1), int(y1), int(x2), int(y2))

    def save_current_settings(self):
        if self.current_image_path and self.current_image_settings:
            self.config_manager.save_item_setting(Path(self.current_image_path), self.current_image_settings)
            self.logger.log(f"設定 '{Path(self.current_image_path).name}' を保存しました。")

    def load_images_into_manager(self, file_paths):
        self.ui_manager.set_tree_enabled(False)
        for file_path in file_paths: self.config_manager.add_item(Path(file_path))
        self._log(f"画像を{len(file_paths)}個追加しました。")
        self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)

    def on_order_changed(self):
        self.ui_manager.set_tree_enabled(False)
        self.thread_pool.submit(self._build_template_cache).add_done_callback(self._on_cache_build_done)

    def _build_template_cache(self):
        with self.cache_lock:
            self.normal_template_cache.clear()
            self.backup_template_cache.clear()
            self.priority_timers.clear()
            self.folder_children_map.clear()

            auto_scale_settings = self.app_config.get('auto_scale', {})
            use_window_scale_base = auto_scale_settings.get('use_window_scale', True)
            
            capture_scale = self.effective_capture_scale

            base_scales = [1.0]

            if use_window_scale_base:
                use_scale_search = auto_scale_settings.get('enabled', False) and capture_scale == 1.0
                
                center_scale = self.current_window_scale if self.current_window_scale is not None else auto_scale_settings.get('center', 1.0)

                if use_scale_search:
                    range_ = auto_scale_settings.get('range', 0.2)
                    steps = auto_scale_settings.get('steps', 5)
                    if steps > 1:
                        base_scales = np.linspace(center_scale - range_, center_scale + range_, steps)
                    self.logger.log(f"スケール検索有効: {len(base_scales)}段階で探索 (中心: {center_scale:.3f})。")
                else:
                    base_scales = [center_scale]
            
            scales = [s * capture_scale for s in base_scales]

            if capture_scale != 1.0:
                self.logger.log(f"全体キャプチャスケール（軽量化モード） {capture_scale:.2f} を適用します。")
            if use_window_scale_base and self.current_window_scale is not None:
                self.logger.log(f"ウィンドウスケール {self.current_window_scale:.3f} を適用します。")

            log_scales = ", ".join([f"{s:.3f}" for s in scales])
            self.logger.log(f"最終的なテンプレート検索スケール: [{log_scales}]")
            
            hierarchical_list = self.config_manager.get_hierarchical_list()
            
            for item_data in hierarchical_list:
                if item_data['type'] == 'folder':
                    folder_path = item_data['path']
                    folder_settings = item_data['settings']
                    folder_mode = folder_settings.get('mode', 'normal')

                    children_paths = {child['path'] for child in item_data.get('children', [])}
                    self.folder_children_map[folder_path] = children_paths

                    if folder_mode == 'priority_timer':
                        interval_seconds = folder_settings.get('priority_interval', 10) * 60
                        if not self.is_monitoring:
                             self.priority_timers[folder_path] = time.time() + interval_seconds
                        elif folder_path not in self.priority_timers:
                             self.priority_timers[folder_path] = time.time() + interval_seconds
                        
                    for child_data in item_data.get('children', []):
                        self._process_item_for_cache(child_data, scales, folder_path, folder_mode)

                elif item_data['type'] == 'image':
                    self._process_item_for_cache(item_data, scales, None, 'normal')
            
            self.logger.log(f"テンプレートキャッシュ構築完了。通常: {len(self.normal_template_cache)}件, バックアップ: {len(self.backup_template_cache)}件")
            self.logger.log(f"タイマー付き優先フォルダ: {len(self.priority_timers)}件")

    def _process_item_for_cache(self, item_data, scales, folder_path, folder_mode):
        try:
            path = item_data['path']
            settings = self.config_manager.load_item_setting(Path(path))

            # ★★★ 修正点: クリック設定の有無をチェック ★★★
            has_point_click = settings.get('point_click') and settings.get('click_position')
            has_range_click = settings.get('range_click') and settings.get('click_rect')

            if not (has_point_click or has_range_click):
                # self.logger.log(f"情報: '{Path(path).name}' はクリック設定がないため検索対象外です。")
                return # クリック設定がなければキャッシュに追加しない
            # ★★★ 修正ここまで ★★★
            
            with open(path, 'rb') as f: file_bytes = np.fromfile(f, np.uint8)
            original_image = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)

            if original_image is not None:
                image_to_process = original_image
                
                if settings.get('roi_enabled', False):
                    h, w = original_image.shape[:2]
                    roi_rect = settings.get('roi_rect') 
                    if roi_rect:
                        x1, y1, x2, y2 = max(0, roi_rect[0]), max(0, roi_rect[1]), min(w, roi_rect[2]), min(h, roi_rect[3])
                        if x1 < x2 and y1 < y2:
                            image_to_process = original_image[y1:y2, x1:x2]
                            # self.logger.log(f"'{Path(path).name}' にROIを適用しました。") # ログが多すぎるためコメントアウト
                        else:
                            self.logger.log(f"警告: '{Path(path).name}' のROI領域が無効なため、フル画像を使用します。")
                    else:
                        self.logger.log(f"警告: '{Path(path).name}' のROIが有効ですが、領域が未設定です。クリック位置を設定してください。")
                
                gray_image_to_process = cv2.cvtColor(image_to_process, cv2.COLOR_BGR2GRAY)
                use_opencl = cv2.ocl.useOpenCL()

                scaled_templates = []
                for scale in scales:
                    if scale <= 0: continue
                    h, w = image_to_process.shape[:2]
                    new_w, new_h = int(w * scale), int(h * scale)
                    if new_w > 0 and new_h > 0:
                        inter = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
                        
                        resized_image = cv2.resize(image_to_process, (new_w, new_h), interpolation=inter)
                        resized_gray = cv2.resize(gray_image_to_process, (new_w, new_h), interpolation=inter)
                        
                        t_h, t_w = resized_image.shape[:2]
                        template_entry = {'scale': scale, 'image': resized_image, 'gray': resized_gray, 'shape': (t_h, t_w)}

                        if use_opencl:
                            try:
                                template_entry['image_umat'] = cv2.UMat(resized_image)
                                template_entry['gray_umat'] = cv2.UMat(resized_gray)
                            except Exception as e:
                                self.logger.log(f"UMat変換エラー: {Path(path).name} - {e}")

                        scaled_templates.append(template_entry)

                cache_entry = {
                    'settings': settings, 'path': path, 'scaled_templates': scaled_templates,
                    'best_scale': None if len(scales) > 1 else scales[0],
                    'folder_path': folder_path, 'folder_mode': folder_mode,
                }
                
                if settings.get('backup_click', False):
                    self.backup_template_cache[path] = cache_entry
                else:
                    self.normal_template_cache[path] = cache_entry

        except Exception as e:
            self._log(f"キャッシュ作成失敗: {item_data.get('name')}, {e}", force=True)

    def start_monitoring(self):
        if self.recognition_area is None:
            self.logger.log("認識範囲が設定されていません。監視を開始できません。")
            QMessageBox.warning(
                self.ui_manager,
                "認識範囲未設定",
                "先に認識範囲を設定してください。\nヘッダーの「認識範囲設定」ボタンから設定できます。"
            )
            return

        if not self.is_monitoring:
            self.is_monitoring = True
            self._click_count = 0
            self._cooldown_until = 0
            
            self.is_backup_countdown_active = False
            self.backup_countdown_start_time = 0
            self.active_backup_info = None

            self._last_clicked_path = None
            
            self.priority_mode_info = {}
            
            self.screen_stability_hashes.clear()

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
                all_caches = list(self.normal_template_cache.values()) + list(self.backup_template_cache.values())
                for cache_item in all_caches:
                    cache_item['best_scale'] = None
            
            self.updateStatus.emit("待機中", "green"); self.logger.log("監視を停止しました。")
    
    def _monitoring_loop(self):
        last_match_time_map = {}
        fps_last_time = time.time()
        frame_counter = 0

        while self.is_monitoring:
            try:
                current_time = time.time()

                if self._cooldown_until > current_time:
                    remaining_cooldown = self._cooldown_until - current_time
                    time.sleep(min(remaining_cooldown, 0.1))
                    continue

                frame_counter += 1
                delta_time = current_time - fps_last_time
                if delta_time >= 1.0:
                    fps = frame_counter / delta_time
                    self.fpsUpdated.emit(fps)
                    fps_last_time = current_time
                    frame_counter = 0
                
                if not self.priority_mode_info:
                    self._check_and_activate_timer_priority_mode()

                if self.is_backup_countdown_active:
                    time.sleep(1.0)

                if (frame_counter % self.effective_frame_skip_rate) != 0: 
                    time.sleep(0.01)
                    continue
                
                screen_bgr = self.capture_manager.capture_frame(region=self.recognition_area)
                if screen_bgr is None:
                    self._log("画面のキャプチャに失敗しました。")
                    time.sleep(1.0)
                    continue
                
                capture_scale = self.effective_capture_scale
                if capture_scale != 1.0 and screen_bgr is not None:
                    screen_bgr = cv2.resize(
                        screen_bgr,
                        None,
                        fx=capture_scale,
                        fy=capture_scale,
                        interpolation=cv2.INTER_AREA
                    )
                
                self.latest_frame_for_hash = screen_bgr.copy()

                screen_gray = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)
                screen_bgr_umat, screen_gray_umat = None, None
                if cv2.ocl.useOpenCL():
                    try:
                        screen_bgr_umat = cv2.UMat(screen_bgr)
                        screen_gray_umat = cv2.UMat(screen_gray)
                    except Exception as e:
                        self.logger.log(f"スクリーンショットのUMat変換に失敗: {e}")

                if self.priority_mode_info:
                    self._handle_priority_state(current_time, screen_bgr, screen_gray, screen_bgr_umat, screen_gray_umat, last_match_time_map)
                elif self.is_backup_countdown_active:
                    self._handle_countdown_state(current_time, screen_bgr, screen_gray, screen_bgr_umat, screen_gray_umat, last_match_time_map)
                else:
                    self._handle_idle_state(current_time, screen_bgr, screen_gray, screen_bgr_umat, screen_gray_umat, last_match_time_map)

            except Exception as e:
                self._log(f"監視ループでエラーが発生しました: {e}", force=True)
                time.sleep(1.0)
            finally:
                time.sleep(0.01) # UIスレッドや他の処理に応答の機会を与える

    def check_screen_stability(self) -> bool:
        """クリック直前に呼び出し、画面が安定しているかを判定する"""
        if not hasattr(self, 'latest_frame_for_hash') or self.latest_frame_for_hash is None:
            return False

        h, w, _ = self.latest_frame_for_hash.shape
        center_x, center_y = w // 2, h // 2
        roi_size = 64
        center_roi = self.latest_frame_for_hash[center_y - roi_size:center_y + roi_size, center_x - roi_size:center_x + roi_size]
        
        current_hash = calculate_phash(center_roi)
        if current_hash is None:
            return False
            
        self.screen_stability_hashes.append(current_hash)

        if len(self.screen_stability_hashes) < self.screen_stability_hashes.maxlen:
            return False

        stability_conf = self.app_config.get('screen_stability_check', {})
        threshold = stability_conf.get('threshold', 5)
        
        latest_hash = self.screen_stability_hashes[-1]
        is_stable = all((latest_hash - h) <= threshold for h in self.screen_stability_hashes)
        
        return is_stable

    def _check_and_activate_timer_priority_mode(self):
        current_time = time.time()
        for path, activation_time in self.priority_timers.items():
            if current_time >= activation_time:
                self._start_timer_priority_mode(path)
                break 

    def _start_timer_priority_mode(self, folder_path):
        folder_settings = self.config_manager.load_item_setting(Path(folder_path))
        timeout_seconds = folder_settings.get('priority_timeout', 5) * 60
        self.priority_mode_info = {
            'type': 'timer',
            'folder_path': folder_path,
            'timeout_time': time.time() + timeout_seconds,
        }
        self.logger.log(f"フォルダ '{Path(folder_path).name}' のタイマー優先監視を開始しました。(解除時間: {timeout_seconds/60:.1f}分)")

    def _start_image_priority_mode(self, folder_path):
        folder_settings = self.config_manager.load_item_setting(Path(folder_path))
        timeout_seconds = folder_settings.get('priority_image_timeout', 10)
        required_children = self.folder_children_map.get(folder_path, set())

        self.priority_mode_info = {
            'type': 'image',
            'folder_path': folder_path,
            'timeout_duration': timeout_seconds,
            'no_match_since_time': time.time(),
            'required_children': required_children,
            'clicked_children': set(),
        }
        self.logger.log(f"フォルダ '{Path(folder_path).name}' の画像認識型優先監視を開始しました。")

    def _stop_priority_mode(self, reason: str):
        if self.priority_mode_info:
            folder_path = self.priority_mode_info.get('folder_path')
            mode_type = self.priority_mode_info.get('type', '不明')
            if folder_path:
                self.logger.log(f"フォルダ '{Path(folder_path).name}' の優先監視({mode_type})を終了しました。({reason})")
            self.priority_mode_info = {}
            self._last_clicked_path = None # 優先モード終了時にリセット

    def _process_matches_as_sequence(self, all_matches, current_time, last_match_time_map):
        if not all_matches:
            return False

        clickable_matches = []
        for match in all_matches:
            path = match['path']
            settings = match['settings']
            interval = settings.get('interval_time', 1.5)
            debounce = settings.get('debounce_time', 0.0)
            
            effective_interval = interval
            if self._last_clicked_path == path and debounce > 0:
                effective_interval += debounce
            
            last_match_time = last_match_time_map.get(path, 0)
            if current_time - last_match_time > effective_interval:
                clickable_matches.append(match)

        if not clickable_matches:
            return False

        clickable_matches.sort(key=lambda m: (m['settings'].get('interval_time', 1.5), -m['confidence']))
        
        target_match = clickable_matches[0]

        stability_conf = self.app_config.get('screen_stability_check', {})
        is_stability_check_enabled = stability_conf.get('enabled', True)

        if is_stability_check_enabled:
            if not self.check_screen_stability():
                self._log("画面が不安定なためクリックを保留します。")
                return False
        
        if not self.is_monitoring:
            return False 

        self._execute_click(target_match)
        
        path = target_match['path']
        last_match_time_map[path] = time.time()
        
        return True

    def _handle_priority_state(self, current_time, screen_bgr, screen_gray, screen_bgr_umat, screen_gray_umat, last_match_time_map):
        mode_type = self.priority_mode_info.get('type')
        if mode_type == 'image':
            self._handle_image_priority_state(current_time, screen_bgr, screen_gray, screen_bgr_umat, screen_gray_umat, last_match_time_map)
        else: # 'timer' or legacy
            self._handle_timer_priority_state(current_time, screen_bgr, screen_gray, screen_bgr_umat, screen_gray_umat, last_match_time_map)

    def _handle_timer_priority_state(self, current_time, screen_bgr, screen_gray, screen_bgr_umat, screen_gray_umat, last_match_time_map):
        if current_time >= self.priority_mode_info['timeout_time']:
            self._stop_priority_mode("タイムアウト")
            return
        
        folder_path = self.priority_mode_info['folder_path']
        
        def filter_by_folder(cache):
            return {p: d for p, d in cache.items() if d.get('folder_path') == folder_path}

        priority_normal_cache = filter_by_folder(self.normal_template_cache)
        priority_backup_cache = filter_by_folder(self.backup_template_cache)

        all_matches = self._find_best_match(screen_bgr, screen_gray, screen_bgr_umat, screen_gray_umat, priority_normal_cache)
        all_matches.extend(self._find_best_match(screen_bgr, screen_gray, screen_bgr_umat, screen_gray_umat, priority_backup_cache))

        clicked_in_sequence = self._process_matches_as_sequence(all_matches, current_time, last_match_time_map)

        if clicked_in_sequence:
            folder_settings = self.config_manager.load_item_setting(Path(folder_path))
            interval_seconds = folder_settings.get('priority_interval', 10) * 60
            self.priority_timers[folder_path] = time.time() + interval_seconds

    def _handle_image_priority_state(self, current_time, screen_bgr, screen_gray, screen_bgr_umat, screen_gray_umat, last_match_time_map):
        folder_path = self.priority_mode_info['folder_path']
        
        def filter_by_folder(cache):
            return {p: d for p, d in cache.items() if d.get('folder_path') == folder_path}

        priority_normal_cache = filter_by_folder(self.normal_template_cache)
        priority_backup_cache = filter_by_folder(self.backup_template_cache)

        all_matches = self._find_best_match(screen_bgr, screen_gray, screen_bgr_umat, screen_gray_umat, priority_normal_cache)
        all_matches.extend(self._find_best_match(screen_bgr, screen_gray, screen_bgr_umat, screen_gray_umat, priority_backup_cache))

        if all_matches:
            self.priority_mode_info['no_match_since_time'] = current_time
            clicked = self._process_matches_as_sequence(all_matches, current_time, last_match_time_map)
            
            if clicked and self._last_clicked_path:
                self.priority_mode_info['clicked_children'].add(self._last_clicked_path)

                required = self.priority_mode_info['required_children']
                clicked_set = self.priority_mode_info['clicked_children']
                
                if clicked_set.issuperset(required):
                    self._stop_priority_mode("完了")
                    return
        else:
            elapsed_no_match_time = current_time - self.priority_mode_info['no_match_since_time']
            if elapsed_no_match_time > self.priority_mode_info['timeout_duration']:
                self._stop_priority_mode("タイムアウト")
                return

    def _handle_idle_state(self, current_time, screen_bgr, screen_gray, screen_bgr_umat, screen_gray_umat, last_match_time_map):
        def filter_cache(cache):
            return {
                p: d for p, d in cache.items() 
                if d.get('folder_mode') not in ['excluded', 'priority_timer']
            }
        
        active_normal_cache = filter_cache(self.normal_template_cache)
        normal_matches = self._find_best_match(screen_bgr, screen_gray, screen_bgr_umat, screen_gray_umat, active_normal_cache)
        
        if normal_matches:
            for match in normal_matches:
                path = match['path']
                cache_item = self.normal_template_cache.get(path)
                if cache_item and cache_item.get('folder_mode') == 'priority_image':
                    self._start_image_priority_mode(cache_item['folder_path'])
                    return

        was_clicked = self._process_matches_as_sequence(normal_matches, current_time, last_match_time_map)
        if was_clicked:
            return

        active_backup_cache = filter_cache(self.backup_template_cache)
        backup_trigger_matches = self._find_best_match(screen_bgr, screen_gray, screen_bgr_umat, screen_gray_umat, active_backup_cache)
        if backup_trigger_matches:
            best_backup_trigger = max(backup_trigger_matches, key=lambda m: m['confidence'])
            
            self.is_backup_countdown_active = True
            self.backup_countdown_start_time = current_time
            self.active_backup_info = best_backup_trigger
            
            path = self.active_backup_info['path']
            backup_time = self.active_backup_info['settings'].get('backup_time', 300.0)
            log_msg = f"バックアップ画像 '{Path(path).name}' を検出。{backup_time:.1f}秒のカウントダウンを開始します。"
            self._log(log_msg)

    def _handle_countdown_state(self, current_time, screen_bgr, screen_gray, screen_bgr_umat, screen_gray_umat, last_match_time_map):
        def filter_cache(cache):
            return {p: d for p, d in cache.items() if d.get('folder_mode') not in ['excluded', 'priority_timer']}
        
        active_normal_cache = filter_cache(self.normal_template_cache)
        normal_matches = self._find_best_match(screen_bgr, screen_gray, screen_bgr_umat, screen_gray_umat, active_normal_cache)
        
        if normal_matches:
            self._process_matches_as_sequence(normal_matches, current_time, last_match_time_map)
            self._log("通常画像を検出したため、バックアップカウントダウンをキャンセルします。")
            self.is_backup_countdown_active = False
            self.active_backup_info = None
            self.backup_countdown_start_time = 0
            return
        
        elapsed_time = current_time - self.backup_countdown_start_time
        backup_duration = self.active_backup_info['settings'].get('backup_time', 300.0)
            
        if elapsed_time >= backup_duration:
            self._log(f"{backup_duration:.1f}秒が経過。バックアップクリックを実行します。")
            self._execute_final_backup_click()
            self.is_backup_countdown_active = False
            self.active_backup_info = None
            self.backup_countdown_start_time = 0
            self._cooldown_until = time.time() + 1.0
            return

    def _execute_final_backup_click(self):
        screen_bgr = self.capture_manager.capture_frame(region=self.recognition_area)
        if screen_bgr is None:
            self._log("バックアップクリック失敗: 画面キャプチャができませんでした。", force=True)
            return
        
        screen_gray = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)
        screen_bgr_umat, screen_gray_umat = None, None
        if cv2.ocl.useOpenCL():
            try:
                screen_bgr_umat = cv2.UMat(screen_bgr)
                screen_gray_umat = cv2.UMat(screen_gray)
            except Exception as e:
                self.logger.log(f"バックアップクリック時のUMat変換に失敗: {e}")

        target_path = self.active_backup_info['path']
        target_cache_item = self.backup_template_cache.get(target_path)
        if not target_cache_item:
            self._log(f"バックアップクリック失敗: ターゲット '{Path(target_path).name}' がキャッシュにありません。", force=True)
            return

        final_matches = self._find_best_match(screen_bgr, screen_gray, screen_bgr_umat, screen_gray_umat, {target_path: target_cache_item})

        if final_matches:
            best_match = max(final_matches, key=lambda m: m['confidence'])
            self._execute_click(best_match)
        else:
            self._log(f"バックアップクリック失敗: 画面内に '{Path(target_path).name}' が見つかりませんでした。", force=True)
    
    def _find_best_match(self, screen_bgr, screen_gray, screen_bgr_umat, screen_gray_umat, template_cache_to_search):
        with self.cache_lock:
            if not template_cache_to_search:
                return []
            
            use_opencl = cv2.ocl.useOpenCL()
            use_grayscale = self.app_config.get('grayscale_matching', False)

            if use_opencl:
                screen_to_use = screen_gray_umat if use_grayscale else screen_bgr_umat
                if screen_to_use is None:
                    screen_to_use = screen_gray if use_grayscale else screen_bgr
            else:
                screen_to_use = screen_gray if use_grayscale else screen_bgr

            screen_shape = screen_to_use.get().shape[:2] if use_opencl and isinstance(screen_to_use, cv2.UMat) else screen_to_use.shape[:2]
            
            results = []
            if use_opencl:
                for path, data in template_cache_to_search.items():
                    is_search_phase = (data['best_scale'] is None)
                    templates_to_search = data['scaled_templates'] if is_search_phase else \
                                          [t for t in data['scaled_templates'] if t['scale'] == data['best_scale']] or \
                                          data['scaled_templates']

                    for t in templates_to_search:
                        if 'image_umat' in t:
                            template_to_use = t['gray_umat'] if use_grayscale else t['image_umat']
                        else:
                            template_to_use = t['gray'] if use_grayscale else t['image']
                        
                        template_shape = t['shape']
                        task_data = {'path': path, 'settings': data['settings'], 'template': template_to_use, 'scale': t['scale']}
                        match = _match_template_task(screen_to_use, task_data, screen_shape, template_shape)
                        if match:
                            results.append(match)
            else:
                futures = []
                for path, data in template_cache_to_search.items():
                    is_search_phase = (data['best_scale'] is None)
                    templates_to_search = data['scaled_templates'] if is_search_phase else \
                                          [t for t in data['scaled_templates'] if t['scale'] == data['best_scale']] or \
                                          data['scaled_templates']

                    for t in templates_to_search:
                        template_to_use = t['gray'] if use_grayscale else t['image']
                        template_shape = t['shape']
                        task_data = {'path': path, 'settings': data['settings'], 'template': template_to_use, 'scale': t['scale']}
                        futures.append(self.thread_pool.submit(_match_template_task, screen_to_use, task_data, screen_shape, template_shape))
                
                for f in futures:
                    result = f.result()
                    if result is not None:
                        results.append(result)

        if not results:
            return []

        best_match_for_scale = max(results, key=lambda r: r['confidence'])
        
        with self.cache_lock:
            cache_to_update = self.normal_template_cache if best_match_for_scale['path'] in self.normal_template_cache else self.backup_template_cache
            cache_item = cache_to_update.get(best_match_for_scale['path'])

            if cache_item and cache_item['best_scale'] is None:
                 cache_item['best_scale'] = best_match_for_scale['scale']
                 log_msg = f"最適スケール発見: {Path(best_match_for_scale['path']).name} @ {best_match_for_scale['scale']:.3f}倍 (信頼度: {best_match_for_scale['confidence']:.2f})"
                 self._log(log_msg)
                 self.bestScaleFound.emit(best_match_for_scale['path'], best_match_for_scale['scale'])
        
        return results

    def _execute_click(self, match_info):
        if sys.platform == 'win32' and self.target_hwnd:
            try:
                current_foreground_hwnd = win32gui.GetForegroundWindow()
                if self.target_hwnd != current_foreground_hwnd:
                    if win32gui.IsIconic(self.target_hwnd):
                        win32gui.ShowWindow(self.target_hwnd, win32con.SW_RESTORE)
                    
                    win32gui.SetForegroundWindow(self.target_hwnd)
                    
                    time.sleep(0.2)
                    self._log(f"ウィンドウ '{win32gui.GetWindowText(self.target_hwnd)}' をアクティブ化しました。")
            except Exception as e:
                self._log(f"ウィンドウのアクティブ化に失敗しました: {e}", force=True)

        block_input(True)
        try:
            settings = match_info['settings']
            match_rect_in_rec_area = match_info['rect']
            scale = match_info.get('scale', 1.0)
            path = match_info['path']
            
            capture_scale = self.effective_capture_scale

            rec_area_offset_x, rec_area_offset_y = (self.recognition_area[0], self.recognition_area[1]) if self.recognition_area else (0, 0)
            
            roi_offset_x, roi_offset_y = 0, 0
            if settings.get('roi_enabled') and settings.get('roi_rect'):
                roi_rect = settings['roi_rect']
                roi_offset_x = max(0, roi_rect[0])
                roi_offset_y = max(0, roi_rect[1])

            click_offset_x_scaled = 0.0
            click_offset_y_scaled = 0.0
            
            if settings.get('point_click') and settings.get('click_position'):
                click_pos_in_template = settings['click_position']
                click_offset_x_scaled = (click_pos_in_template[0] - roi_offset_x) * scale
                click_offset_y_scaled = (click_pos_in_template[1] - roi_offset_y) * scale
            elif settings.get('range_click') and settings.get('click_rect'):
                click_rect_in_template = settings['click_rect']
                rect_x1_in_roi = click_rect_in_template[0] - roi_offset_x
                rect_y1_in_roi = click_rect_in_template[1] - roi_offset_y
                rect_x2_in_roi = click_rect_in_template[2] - roi_offset_x
                rect_y2_in_roi = click_rect_in_template[3] - roi_offset_y
                x1_offset_scaled = rect_x1_in_roi * scale
                y1_offset_scaled = rect_y1_in_roi * scale
                x2_offset_scaled = rect_x2_in_roi * scale
                y2_offset_scaled = rect_y2_in_roi * scale
                if settings.get('random_click', True):
                    min_x, max_x = min(x1_offset_scaled, x2_offset_scaled), max(x1_offset_scaled, x2_offset_scaled)
                    min_y, max_y = min(y1_offset_scaled, y2_offset_scaled), max(y1_offset_scaled, y2_offset_scaled)
                    click_offset_x_scaled = random.uniform(min_x, max_x)
                    click_offset_y_scaled = random.uniform(min_y, max_y)
                else:
                    click_offset_x_scaled = (x1_offset_scaled + x2_offset_scaled) / 2
                    click_offset_y_scaled = (y1_offset_scaled + y2_offset_scaled) / 2
            else:
                match_width_scaled = match_rect_in_rec_area[2] - match_rect_in_rec_area[0]
                match_height_scaled = match_rect_in_rec_area[3] - match_rect_in_rec_area[1]
                click_offset_x_scaled = match_width_scaled / 2
                click_offset_y_scaled = match_height_scaled / 2

            click_x_in_rec_area_scaled = match_rect_in_rec_area[0] + click_offset_x_scaled
            click_y_in_rec_area_scaled = match_rect_in_rec_area[1] + click_offset_y_scaled
            
            click_x_float = rec_area_offset_x + (click_x_in_rec_area_scaled / capture_scale)
            click_y_float = rec_area_offset_y + (click_y_in_rec_area_scaled / capture_scale)
            
            screen_width, screen_height = pyautogui.size()
            final_click_x = int(click_x_float)
            final_click_y = int(click_y_float)
            
            if not (1 <= final_click_x < screen_width - 1 and 1 <= final_click_y < screen_height - 1):
                self._log(f"警告: 計算されたクリック座標 ({final_click_x}, {final_click_y}) が画面の端すぎるためクリックを中止しました。", force=True)
                return
            
            # ★★★ ここからが修正部分 ★★★
            try:
                pyautogui.click(final_click_x, final_click_y)
                self._click_count += 1
                self._last_clicked_path = path
                
                log_msg = f"クリック: {Path(path).name} @({final_click_x}, {final_click_y}) conf:{match_info['confidence']:.2f}"
                if 'scale' in match_info:
                    log_msg += f" scale:{match_info['scale']:.3f}"
                self._log(log_msg)

            except pyautogui.FailSafeException:
                self._log("PyAutoGUIのフェイルセーフが作動しました。ユーザーがマウスを画面の隅に移動したか、座標計算に問題がある可能性があります。", force=True)
            # ★★★ 修正部分ここまで ★★★

        except Exception as e:
            self._log(f"クリック実行中にエラーが発生しました: {e}", force=True)
        finally:
            block_input(False)

    def set_recognition_area(self, method: str):
        self.selectionProcessStarted.emit()
        self.ui_manager.hide()
        if self.performance_monitor:
            self.performance_monitor.hide()

        if method == "rectangle":
            self.target_hwnd = None
            self.current_window_scale = None
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
        if self._is_capturing_for_registration:
            self._is_capturing_for_registration = False
        
        if hasattr(self, 'selection_overlay'):
            self.selection_overlay = None

        if self.window_selection_listener:
            self.window_selection_listener.stop()
            self.window_selection_listener = None
        if self.keyboard_selection_listener:
            self.keyboard_selection_listener.stop()
            self.keyboard_selection_listener = None

        self.selectionProcessFinished.emit()
        self._show_ui_safe()

    def _on_key_press_for_selection(self, key):
        if key == keyboard.Key.esc:
            self.logger.log("キーボードによりウィンドウ選択がキャンセルされました。")
            if self.window_selection_listener:
                self.window_selection_listener.stop()
            if self.keyboard_selection_listener:
                self.keyboard_selection_listener.stop()
            
            self._showUiSignal.connect(self._on_selection_cancelled)
            self._showUiSignal.emit()
            self._showUiSignal.disconnect(self._on_selection_cancelled)
            return False

    def _handle_window_click_for_selection(self, x, y):
        if self.keyboard_selection_listener:
            self.keyboard_selection_listener.stop()
            self.keyboard_selection_listener = None
        
        if sys.platform == 'win32':
            self._handle_window_click_for_selection_windows(x, y)
        else:
            self._handle_window_click_for_selection_linux(x, y)

    def _handle_window_click_for_selection_windows(self, x, y):
        try:
            hwnd = win32gui.WindowFromPoint((x, y))
            if not hwnd: return
            
            self.target_hwnd = hwnd
            
            if 'dxcam' in sys.modules and self.capture_manager.dxcam_sct:
                self.capture_manager.dxcam_sct.target_hwnd = hwnd

            client_rect_win = win32gui.GetClientRect(hwnd)
            left, top = win32gui.ClientToScreen(hwnd, (0, 0))
            right, bottom = left + client_rect_win[2], top + client_rect_win[3]
            
            screen_width, screen_height = pyautogui.size()
            left = max(0, left)
            top = max(0, top)
            right = min(screen_width, right)
            bottom = min(screen_height, bottom)

            if right <= left or bottom <= top:
                self.logger.log(f"ウィンドウ領域の計算結果が無効です: ({left},{top},{right},{bottom})。処理を中断します。")
                self.target_hwnd = None
                self._on_selection_cancelled()
                return
            
            rect = (left, top, right, bottom)
            
            if self._is_capturing_for_registration:
                self._areaSelectedForProcessing.emit(rect)
                self.selectionProcessFinished.emit()
                return

            title = win32gui.GetWindowText(hwnd)
            self._pending_window_info = {
                "title": title,
                "dims": {'width': rect[2] - rect[0], 'height': rect[3] - rect[1]},
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
            self.target_hwnd = None
            self._showUiSignal.emit(); self.selectionProcessFinished.emit()
    
    def _handle_window_click_for_selection_linux(self, x, y):
        needed_tools = ['xdotool', 'xwininfo']
        missing_tools = [tool for tool in needed_tools if not shutil.which(tool)]
        if missing_tools:
            self.logger.log(f"エラー: {', '.join(missing_tools)} が見つかりません。")
            self._showUiSignal.emit(); self.selectionProcessFinished.emit()
            return
        
        try:
            proc_id = subprocess.run(['xdotool', 'getmouselocation'], capture_output=True, text=True, check=True)
            window_id = [line.split(':')[1] for line in proc_id.stdout.strip().split() if 'window' in line][0]
            proc_info = subprocess.run(['xwininfo', '-id', window_id], capture_output=True, text=True, check=True)
            info = proc_info.stdout
            abs_x, abs_y, width, height = 0, 0, 0, 0
            title = f"Window (ID: {window_id})"
            for line in info.split('\n'):
                line = line.strip()
                if line.startswith('Absolute upper-left X:'): abs_x = int(line.split(':')[1].strip())
                elif line.startswith('Absolute upper-left Y:'): abs_y = int(line.split(':')[1].strip())
                elif line.startswith('Width:'): width = int(line.split(':')[1].strip())
                elif line.startswith('Height:'): height = int(line.split(':')[1].strip())
                elif 'xwininfo: Window id:' in line and '"' in line:
                    title = line.split('"')[1]

            left, top, right, bottom = abs_x, abs_y, abs_x + width, abs_y + height
            
            screen_width, screen_height = pyautogui.size()
            left = max(0, left)
            top = max(0, top)
            right = min(screen_width, right)
            bottom = min(screen_height, bottom)

            if right <= left or bottom <= top:
                self.logger.log(f"ウィンドウ領域の計算結果が無効です: ({left},{top},{right},{bottom})。処理を中断します。")
                self._on_selection_cancelled()
                return

            rect = (left, top, right, bottom)
            
            if self._is_capturing_for_registration:
                self._areaSelectedForProcessing.emit(rect)
                self.selectionProcessFinished.emit()
                return

            self._pending_window_info = { "title": title, "dims": {'width': rect[2] - rect[0], 'height': rect[3] - rect[1]}, "rect": rect }
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
            
            title, current_dims, rect = info['title'], info['dims'], info['rect']
            scales_data = self.config_manager.load_window_scales()
            
            if save_as_base:
                scales_data[title] = current_dims
                self.config_manager.save_window_scales(scales_data)
                self.current_window_scale = 1.0
                self.logger.log(f"ウィンドウ '{title}' の基準サイズを保存しました。")
                self.windowScaleCalculated.emit(1.0)
                self._areaSelectedForProcessing.emit(rect)
                self._showUiSignal.emit()
                self.selectionProcessFinished.emit()

            elif title and title in scales_data:
                base_dims = scales_data[title]
                calculated_scale = current_dims['width'] / base_dims['width'] if base_dims['width'] > 0 else 1.0

                if 0.995 <= calculated_scale <= 1.005:
                    self.current_window_scale = 1.0
                    self.logger.log(f"ウィンドウ '{title}' のスケールを計算: {calculated_scale:.3f}倍 (1.0として補正)")
                    self.windowScaleCalculated.emit(self.current_window_scale)
                    self._areaSelectedForProcessing.emit(rect)
                    self._showUiSignal.emit()
                    self.selectionProcessFinished.emit()
                else:
                    self._pending_scale_prompt_info = info.copy()
                    self._pending_scale_prompt_info['calculated_scale'] = calculated_scale
                    self.askToApplyWindowScaleSignal.emit(calculated_scale)
                    return 
            else:
                self.current_window_scale = None
                self.windowScaleCalculated.emit(0.0)
                self._areaSelectedForProcessing.emit(rect)
                self._showUiSignal.emit()
                self.selectionProcessFinished.emit()
        
        except Exception as e: 
            self.logger.log(f"基準サイズ応答の処理中にエラー: {e}")
            self._showUiSignal.emit()
            self.selectionProcessFinished.emit()
        finally: 
            self._pending_window_info = None

    def process_apply_scale_prompt_response(self, apply_scale: bool):
        try:
            info = self._pending_scale_prompt_info
            if not info: return

            scale = info['calculated_scale']
            rect = info['rect']

            if apply_scale:
                self.ui_manager.app_config['auto_scale']['use_window_scale'] = True
                self.ui_manager.auto_scale_widgets['use_window_scale'].setChecked(True)
                self.ui_manager.on_app_settings_changed() 
                
                self.current_window_scale = scale
                self.logger.log(f"ユーザーの選択により、ウィンドウスケール {scale:.3f}倍 を適用します。")
            else:
                self.current_window_scale = None
                self.logger.log(f"計算されたウィンドウスケール {scale:.3f}倍 は適用されませんでした。")
            
            self.windowScaleCalculated.emit(self.current_window_scale if self.current_window_scale is not None else 0.0)
            self._areaSelectedForProcessing.emit(rect)

        except Exception as e:
            self.logger.log(f"スケール適用応答の処理中にエラー: {e}")
        finally:
            self._pending_scale_prompt_info = None
            self._showUiSignal.emit()
            self.selectionProcessFinished.emit()

    def handle_area_selection(self, coords):
        if self._is_capturing_for_registration:
            self._is_capturing_for_registration = False
            QTimer.singleShot(100, lambda: self._save_captured_image(coords))
        else:
            self.recognition_area = coords
            self.logger.log(f"認識範囲を設定: {coords}")
            self._update_rec_area_preview()
            self.selectionProcessFinished.emit()
            self.ui_manager.show()
        if hasattr(self, 'selection_overlay'): self.selection_overlay = None
        
    def _get_filename_from_user(self):
        if sys.platform == 'win32':
            file_name, ok = QInputDialog.getText(self.ui_manager, "ファイル名を入力", "保存するファイル名を入力してください:")
            return file_name, ok
        else:
            if not shutil.which('zenity'):
                QMessageBox.warning(self.ui_manager, "エラー", "名前入力機能には 'zenity' が必要です。")
                return None, False
            try:
                command = ['zenity', '--entry', '--title=ファイル名を入力', '--text=保存するファイル名を入力してください（拡張子不要）:']
                result = subprocess.run(command, capture_output=True, text=True, check=False)
                return (result.stdout.strip(), True) if result.returncode == 0 else (None, False)
            except Exception as e:
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
                self.selectionProcessFinished.emit()
                if not self.ui_manager.isVisible(): self._show_ui_safe()
                return
            
            file_name, ok = self._get_filename_from_user()
            
            if ok and file_name:
                self.ui_manager.set_tree_enabled(False)
                save_path = self.config_manager.base_dir / f"{file_name}.png"
                
                if save_path.exists():
                    reply = QMessageBox.question(self.ui_manager, "上書き確認", f"'{save_path.name}' は既に存在します。上書きしますか？",
                                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
                    if reply == QMessageBox.StandardButton.No:
                        self.ui_manager.set_tree_enabled(True)
                        self.selectionProcessFinished.emit()
                        if not self.ui_manager.isVisible(): self._show_ui_safe()
                        return

                future = self.thread_pool.submit(self._save_image_task, captured_image, save_path)
                future.add_done_callback(self._on_save_image_done)

            else:
                self.selectionProcessFinished.emit()
                if not self.ui_manager.isVisible(): self._show_ui_safe()

        except Exception as e:
            QMessageBox.critical(self.ui_manager, "エラー", f"画像保存の準備中にエラーが発生しました:\n{e}")
            self.selectionProcessFinished.emit()
            if not self.ui_manager.isVisible(): self._show_ui_safe()

    def _save_image_task(self, image_to_save, save_path):
        """ワーカースレッドで実行される画像保存タスク"""
        try:
            _, buffer = cv2.imencode('.png', image_to_save)
            buffer.tofile(str(save_path))
            
            default_settings = self.config_manager.load_item_setting(Path())
            default_settings['image_path'] = str(save_path)
            self.config_manager.save_item_setting(save_path, default_settings)
            
            self.config_manager.add_item(save_path)
            
            return True, f"画像を保存しました: {save_path}"
        except Exception as e:
            return False, f"画像の保存に失敗しました:\n{e}"

    def _on_save_image_done(self, future):
        """画像保存タスク完了後のコールバック"""
        try:
            success, message = future.result()
            if success:
                self._log(message)
                cache_future = self.thread_pool.submit(self._build_template_cache)
                cache_future.add_done_callback(self._on_cache_build_done)
            else:
                QMessageBox.critical(self.ui_manager, "エラー", message)
                self.ui_manager.set_tree_enabled(True)
        
        finally:
            self.selectionProcessFinished.emit()
            if not self.ui_manager.isVisible():
                self._show_ui_safe()
                
    def clear_recognition_area(self):
        self.recognition_area = None
        self.current_window_scale = None
        self.target_hwnd = None
        self.windowScaleCalculated.emit(0.0)
        if 'dxcam' in sys.modules and self.capture_manager.dxcam_sct:
            self.capture_manager.dxcam_sct.target_hwnd = None
        self.logger.log("認識範囲をクリアしました。");
        self.updateRecAreaPreview.emit(None)
        
    def _update_rec_area_preview(self):
        img = self.capture_manager.capture_frame(region=self.recognition_area) if self.recognition_area else None
        self.updateRecAreaPreview.emit(img)
    
    def get_backup_click_countdown(self) -> float:
        if self.is_backup_countdown_active and self.active_backup_info:
            elapsed_time = time.time() - self.backup_countdown_start_time
            backup_duration = self.active_backup_info['settings'].get('backup_time', 300.0)
            remaining_time = backup_duration - elapsed_time
            return max(0, remaining_time)
        return -1.0
