# main.py (D&D対応版・多言語対応版)

import sys
import os
import socket

# 実行されたスクリプト自身の場所を特定し、モジュール検索パスの先頭に追加する
try:
    if getattr(sys, 'frozen', False):
        script_directory = os.path.dirname(sys.executable)
    else:
        script_directory = os.path.dirname(os.path.abspath(__file__))
    
    sys.path.insert(0, script_directory)
except NameError:
    sys.path.insert(0, os.getcwd())

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QObject, Signal, QTimer

from ui import UIManager
from core import CoreEngine
from capture import CaptureManager
from config import ConfigManager
from monitor import PerformanceMonitor
from dialogs import InitializationDialog
# ★★★ 1. LocaleManager と QObject/Signal (Logger用) をインポート ★★★
from locale_manager import LocaleManager


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
    
    # ★★★ 2. __init__ を修正 (locale_manager を None で初期化) ★★★
    def __init__(self, ui_manager=None, perf_monitor=None):
        super().__init__()
        self.ui_manager = ui_manager
        self.perf_monitor = perf_monitor
        self.locale_manager = None # LocaleManagerインスタンスを保持

    def set_ui(self, ui_manager, perf_monitor):
        self.ui_manager = ui_manager
        self.perf_monitor = perf_monitor
        self.logReady.connect(self.ui_manager.update_log)
        self.logReady.connect(self.perf_monitor.update_log)

    # ★★★ 3. set_locale_manager メソッドを追加 ★★★
    def set_locale_manager(self, locale_manager):
        """LocaleManagerを設定し、キー翻訳を有効にします。"""
        self.locale_manager = locale_manager

    # ★★★ 4. log メソッドを修正 (キー翻訳に対応) ★★★
    def log(self, message: str, *args):
        """
        ログメッセージを記録します。
        messageが翻訳キーであれば翻訳し、そうでなければそのまま使用します。
        """
        try:
            if self.locale_manager:
                # message をキーとして翻訳を試みる
                translated_message = self.locale_manager.tr(message, *args)
            else:
                # LocaleManagerが設定される前 (起動直後など)
                translated_message = message % args if args else message
        except Exception:
             # フォーマット失敗時などのフォールバック
            translated_message = f"{message} (args: {args})"

        print(f"[LOG] {translated_message}")
        self.logReady.emit(translated_message)


def main():
    temp_app_for_check = QApplication.instance() or QApplication(sys.argv)
    
    # ★★★ 5. LocaleManagerを早期にインスタンス化 ★★★
    # (QMessageBoxの前にLoggerとLocaleManagerが必要になる可能性があるため)
    logger = Logger()
    locale_manager = LocaleManager()
    logger.set_locale_manager(locale_manager) # Loggerに設定

    if not check_and_lock():
        error_box = QMessageBox()
        # ★★★ 6. ハードコードされた文字列をキーに置き換え ★★★
        error_box.setWindowTitle(locale_manager.tr("error_already_running_title"))
        error_box.setIcon(QMessageBox.Icon.Warning)
        error_box.setText(locale_manager.tr("error_already_running_text"))
        error_box.setStandardButtons(QMessageBox.StandardButton.Ok)
        error_box.exec()
        sys.exit(1)
    
    app = temp_app_for_check
    
    # LoggerとLocaleManagerは作成済み
    config_manager = ConfigManager(logger) # ConfigManagerにもLoggerを渡す
    capture_manager = CaptureManager(logger) # CaptureManagerにもLoggerを渡す
    
    ui_manager = UIManager(
        core_engine=None,
        capture_manager=capture_manager,
        config_manager=config_manager,
        logger=logger,
        locale_manager=locale_manager # ★★★ 7. UIManagerに渡す ★★★
    )
    
    performance_monitor = PerformanceMonitor(ui_manager, locale_manager, parent=None) # ★★★ 8. Monitorに渡す ★★★
    
    ui_manager.set_performance_monitor(performance_monitor)
    
    logger.set_ui(ui_manager, performance_monitor)
    
    core_engine = CoreEngine(
        ui_manager=ui_manager,
        capture_manager=capture_manager,
        config_manager=config_manager,
        logger=logger,
        performance_monitor=performance_monitor,
        locale_manager=locale_manager # ★★★ 9. CoreEngineに渡す ★★★
    )
    
    ui_manager.core_engine = core_engine
    
    # --- ▼▼▼ 修正箇所 ▼▼▼ ---
    
    # --- シグナル接続 ---
    core_engine.updateStatus.connect(ui_manager.set_status)
    # core_engine.updateStatus.connect(performance_monitor.update_monitoring_status) # ★★★ 削除 (monitor.py からメソッド削除のため) ★★★
    core_engine.updatePreview.connect(ui_manager.update_image_preview)
    core_engine.updateRecAreaPreview.connect(ui_manager.update_rec_area_preview)
    
    # (performance_monitor から統計機能が削除されたため、該当の接続を削除)
    
    core_engine.cacheBuildFinished.connect(ui_manager.on_cache_build_finished)
    
    # (statsUpdated, clickCountUpdated, fpsUpdated は、
    #  ui.py の toggle_minimal_ui_mode で動的に接続/切断されます)
    
    core_engine.selectionProcessStarted.connect(ui_manager.on_selection_process_started)
    core_engine.selectionProcessFinished.connect(ui_manager.on_selection_process_finished)

    core_engine.bestScaleFound.connect(ui_manager.on_best_scale_found)
    core_engine.windowScaleCalculated.connect(ui_manager.on_window_scale_calculated)
    core_engine.askToSaveWindowBaseSizeSignal.connect(ui_manager.show_prompt_to_save_base_size)
    core_engine.askToApplyWindowScaleSignal.connect(ui_manager.show_prompt_to_apply_scale)

    ui_manager.startMonitoringRequested.connect(core_engine.start_monitoring)
    ui_manager.stopMonitoringRequested.connect(core_engine.stop_monitoring)
    ui_manager.loadImagesRequested.connect(core_engine.load_images_into_manager)
    ui_manager.imageSettingsChanged.connect(core_engine.on_image_settings_changed)
    ui_manager.captureImageRequested.connect(core_engine.capture_image_for_registration)
    ui_manager.deleteItemsRequested.connect(core_engine.delete_selected_items)
    # --- ▼▼▼ 修正箇所 (リネームシグナルを接続) ▼▼▼ ---
    ui_manager.renameItemRequested.connect(core_engine.rename_item)
    # --- ▲▲▲ 修正完了 ▲▲▲ ---
    
    ui_manager.folderSettingsChanged.connect(core_engine.on_folder_settings_changed)
    
    ui_manager.orderChanged.connect(core_engine.on_order_changed)
    ui_manager.createFolderRequested.connect(core_engine.create_folder)
    
    ui_manager.moveItemIntoFolderRequested.connect(core_engine.move_item_into_folder)
    
    ui_manager.itemsMovedIntoFolder.connect(core_engine.move_items_into_folder)

    ui_manager.moveItemOutOfFolderRequested.connect(core_engine.move_item_out_of_folder)
    ui_manager.openPerformanceMonitorRequested.connect(performance_monitor.show)
    ui_manager.setRecAreaMethodSelected.connect(core_engine.set_recognition_area)

    ui_manager.connect_signals()
    
    # (monitor.py からボタンとシグナルを削除したため、以下の行を削除)
    # performance_monitor.toggleMonitoringRequested.connect(ui_manager.toggle_monitoring)
    performance_monitor.connect_signals()
    
    # --- ▲▲▲ 修正完了 ▲▲▲ ---
    
    # --- 起動シーケンス (変更なし) ---
    ui_manager.set_tree_enabled(False)
    capture_manager.prime_mss()
    future = core_engine.thread_pool.submit(core_engine._build_template_cache)
    future.add_done_callback(core_engine._on_cache_build_done)
    
    ui_manager.show()

    if sys.platform != 'win32':
        def run_initialization_dialog():
            try:
                # ★★★ 10. InitializationDialog に locale_manager を渡す ★★★
                dialog = InitializationDialog(core_engine, logger, locale_manager, ui_manager)
                dialog.exec()
            except ImportError:
                logger.log("init_dialog_error_not_found")
            except Exception as e:
                logger.log("init_dialog_error_exec", str(e))
        
        QTimer.singleShot(200, run_initialization_dialog)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
