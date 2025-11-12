# main.py (D&D対応版・多言語対応版)
# ★★★ (再起動ロジック) 解像度変更時にアプリを再起動する ★★★

import sys
import os
import socket

# ★★★ 修正箇所 1/4: ctypes をインポート ★★★
import ctypes 

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
# --- ▼▼▼ 修正: QProcess をインポート ▼▼▼ ---
from PySide6.QtCore import QObject, Signal, QTimer, QProcess
# --- ▲▲▲ 修正完了 ▲▲▲ ---

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

# --- ▼▼▼ 修正: グローバルに app インスタンスを保持 ▼▼▼ ---
app = None
# --- ▲▲▲ 修正完了 ▲▲▲ ---

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

# --- ▼▼▼ 修正: 再起動関数を追加 (ロック解放処理も追加) ▼▼▼ ---
def restart_application():
    """
    アプリケーションを再起動します。
    QProcess.startDetached を使用して、現在のプロセスが終了した後に
    新しいプロセスを開始します。
    """
    global app, _lock_socket # ★ _lock_socket をグローバル参照
    if not app:
        print("[ERROR] Application instance not found for restart.")
        return

    # ★★★ 修正箇所: 再起動の前にロックソケットを解放する ★★★
    if _lock_socket:
        try:
            _lock_socket.close()
            _lock_socket = None
            print("[INFO] Lock socket released for restart.")
        except Exception as e:
            print(f"[WARN] Failed to close lock socket: {e}")
    # ★★★ 修正完了 ★★★

    # 実行中のPythonスクリプトまたは実行可能ファイルへのパス
    executable = sys.executable
    # 現在のスクリプトのメインファイル
    script_path = os.path.abspath(sys.argv[0])
    
    # Pythonインタープリタ経由か、実行ファイルか
    if executable.lower().endswith("python.exe") or executable.lower().endswith("python"):
        args = [script_path] + sys.argv[1:]
        process_path = executable
    else:
        # .exe の場合
        args = sys.argv[1:]
        process_path = executable

    print(f"[INFO] Attempting to restart application...")
    print(f"[INFO] Process: {process_path}")
    print(f"[INFO] Args: {args}")

    # 新しいプロセスをデタッチモードで開始
    QProcess.startDetached(process_path, args)
    
    # 現在のアプリケーションを終了
    app.quit()
# --- ▲▲▲ 修正完了 ▲▲▲ ---

def main():
    global app # --- 修正: グローバル変数を参照 ---
    
    # ★★★ 修正箇所 2/4: QApplicationインスタンス化の前に DPI-Awareness を設定 ★★★
    if sys.platform == 'win32':
        try:
            # SetProcessDPIAware() の呼び出しで、UIを96DPIに固定 (仕様書推奨)
            ctypes.windll.user32.SetProcessDPIAware()
            print("[INFO] SetProcessDPIAware() called. Application is now DPI-Unaware.")
        except Exception as e:
            print(f"[WARN] Failed to call SetProcessDPIAware(): {e}")
            pass # 失敗しても続行
    # ★★★ 修正完了 ★★★

    app = QApplication.instance() or QApplication(sys.argv) # --- 修正: グローバル変数に代入 ---
    
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
    
    # app = temp_app_for_check # ← 削除
    
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
    core_engine.appContextChanged.connect(ui_manager.on_app_context_changed)
    
    # --- ▼▼▼ 修正: 再起動シグナルを接続 ▼▼▼ ---
    core_engine.restartApplicationRequested.connect(restart_application)
    # --- ▲▲▲ 修正完了 ▲▲▲ ---
    
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
    
    # ★★★ 修正箇所 3/4: monitor.py の言語変更シグナルを接続 (仕様書 [27] 対応) ★★★
    locale_manager.languageChanged.connect(performance_monitor.on_language_changed)
    
    # --- ▲▲▲ 修正完了 ▲▲▲ ---
    
    # --- ▼▼▼ 修正箇所 (解像度変更シグナルの接続) ▼▼▼ ---
    try:
        primary_screen = app.primaryScreen()
        if primary_screen:
            # 画面のジオメトリ (解像度, DPI) 変更を CoreEngine に接続
            primary_screen.geometryChanged.connect(core_engine.on_screen_geometry_changed)
            logger.log("log_screen_geometry_listener_attached")
        else:
            logger.log("log_screen_geometry_listener_failed")
    except Exception as e:
        logger.log("log_screen_geometry_listener_error", str(e))
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

    # ★★★ 修正箇所 4/4: logger インスタンスを渡す (仕様書 [26] 確認) ★★★
    ui_manager.logger = logger
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
