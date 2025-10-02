# main.py

import sys
import os
import socket

# ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
# 実行されたスクリプト自身の場所を特定し、モジュール検索パスの先頭に追加する
# これにより、バッチファイルのPYTHONPATH設定に依存せず、ui.pyなどを確実に見つけられるようになる
try:
    # PyInstallerやNuitkaでExe化した場合にも対応できる、より堅牢なパス取得方法
    if getattr(sys, 'frozen', False):
        script_directory = os.path.dirname(sys.executable)
    else:
        script_directory = os.path.dirname(os.path.abspath(__file__))
    
    sys.path.insert(0, script_directory)
except NameError:
    # 対話モードなどで__file__がない場合のフォールバック
    sys.path.insert(0, os.getcwd())
# ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QObject, Signal

from ui import UIManager
from core import CoreEngine
from capture import CaptureManager
from config import ConfigManager
from monitor import PerformanceMonitor

LOCK_PORT = 54321
_lock_socket = None

def check_and_lock():
    global _lock_socket
    _lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _lock_socket.bind(("127.0.0.1", LOCK_PORT))
        return True
    except OSError:
        _lock_socket = None
        return False

class Logger(QObject):
    logReady = Signal(str)
    
    def __init__(self, ui_manager=None, perf_monitor=None):
        super().__init__()
        self.ui_manager = ui_manager
        self.perf_monitor = perf_monitor

    def set_ui(self, ui_manager, perf_monitor):
        self.ui_manager = ui_manager
        self.perf_monitor = perf_monitor
        self.logReady.connect(self.ui_manager.update_log)
        self.logReady.connect(self.perf_monitor.update_log)

    def log(self, message):
        print(f"[LOG] {message}")
        self.logReady.emit(message)

def main():
    temp_app_for_check = QApplication.instance() or QApplication(sys.argv)
    
    if not check_and_lock():
        error_box = QMessageBox()
        error_box.setWindowTitle("起動エラー")
        error_box.setIcon(QMessageBox.Icon.Warning)
        error_box.setText("Imeck15は既に起動しています。")
        error_box.setStandardButtons(QMessageBox.StandardButton.Ok)
        error_box.exec()
        sys.exit(1)
    
    app = temp_app_for_check
    
    logger = Logger()
    config_manager = ConfigManager()
    capture_manager = CaptureManager()
    
    ui_manager = UIManager(
        core_engine=None,
        capture_manager=capture_manager,
        config_manager=config_manager,
        logger=logger
    )
    
    performance_monitor = PerformanceMonitor(ui_manager, parent=None)
    
    logger.set_ui(ui_manager, performance_monitor)
    
    core_engine = CoreEngine(
        ui_manager=ui_manager,
        capture_manager=capture_manager,
        config_manager=config_manager,
        logger=logger,
        performance_monitor=performance_monitor
    )
    
    ui_manager.core_engine = core_engine
    
    core_engine.updateStatus.connect(ui_manager.set_status)
    core_engine.updateStatus.connect(performance_monitor.update_monitoring_status)
    core_engine.updateLog.connect(logger.log)
    core_engine.updatePreview.connect(ui_manager.update_image_preview)
    core_engine.updateRecAreaPreview.connect(ui_manager.update_rec_area_preview)
    core_engine.fpsUpdated.connect(performance_monitor.update_fps)
    core_engine.cacheBuildFinished.connect(ui_manager.on_cache_build_finished)
    core_engine.selectionProcessStarted.connect(performance_monitor.hide)
    core_engine.selectionProcessFinished.connect(performance_monitor.show)
    core_engine.bestScaleFound.connect(ui_manager.on_best_scale_found)
    core_engine.windowScaleCalculated.connect(ui_manager.on_window_scale_calculated)
    core_engine.askToSaveWindowBaseSizeSignal.connect(ui_manager.show_prompt_to_save_base_size)
    
    # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
    # 修正点: 欠落していたシグナル接続を1行追加
    # これにより、ウィンドウ倍率が異なる場合に表示される確認ダイアログが正しく機能します。
    core_engine.askToApplyWindowScaleSignal.connect(ui_manager.show_prompt_to_apply_scale)
    # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★

    ui_manager.startMonitoringRequested.connect(core_engine.start_monitoring)
    ui_manager.stopMonitoringRequested.connect(core_engine.stop_monitoring)
    ui_manager.loadImagesRequested.connect(core_engine.load_images_into_manager)
    ui_manager.imageSettingsChanged.connect(core_engine.on_image_settings_changed)
    ui_manager.captureImageRequested.connect(core_engine.capture_image_for_registration)
    ui_manager.deleteItemRequested.connect(core_engine.delete_selected_item)
    ui_manager.toggleFolderExclusionRequested.connect(core_engine.toggle_folder_exclusion)
    ui_manager.orderChanged.connect(core_engine.on_order_changed)
    ui_manager.createFolderRequested.connect(core_engine.create_folder)
    ui_manager.moveItemIntoFolderRequested.connect(core_engine.move_item_into_folder)
    ui_manager.moveItemOutOfFolderRequested.connect(core_engine.move_item_out_of_folder)
    ui_manager.openPerformanceMonitorRequested.connect(performance_monitor.show)
    ui_manager.setRecAreaMethodSelected.connect(core_engine.set_recognition_area)

    ui_manager.connect_signals()
    
    performance_monitor.toggleMonitoringRequested.connect(ui_manager.toggle_monitoring)
    performance_monitor.connect_signals()
    
    ui_manager.set_tree_enabled(False)
    future = core_engine.thread_pool.submit(core_engine._build_template_cache)
    future.add_done_callback(core_engine._on_cache_build_done)
    
    ui_manager.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
