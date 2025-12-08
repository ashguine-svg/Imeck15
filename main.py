# main.py

import sys
import os
import socket
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
from PySide6.QtCore import QObject, Signal, QTimer, QProcess, Qt

from qt_material import apply_stylesheet

from ui import UIManager
from core import CoreEngine
from capture import CaptureManager
from config import ConfigManager
from dialogs import InitializationDialog
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

app = None

class Logger(QObject):
    logReady = Signal(str)
    
    def __init__(self, ui_manager=None):
        super().__init__()
        self.ui_manager = ui_manager
        self.locale_manager = None 

    def set_ui(self, ui_manager):
        self.ui_manager = ui_manager
        self.logReady.connect(self.ui_manager.update_log)

    def set_locale_manager(self, locale_manager):
        self.locale_manager = locale_manager

    def log(self, message: str, *args, force=False):
        try:
            if self.locale_manager:
                translated_message = self.locale_manager.tr(message, *args)
            else:
                translated_message = message % args if args else message
        except Exception:
            translated_message = f"{message} (args: {args})"

        print(f"[LOG] {translated_message}")
        self.logReady.emit(translated_message)

def restart_application():
    global app, _lock_socket
    if not app:
        print("[ERROR] Application instance not found for restart.")
        return

    if _lock_socket:
        try:
            _lock_socket.close()
            _lock_socket = None
            print("[INFO] Lock socket released for restart.")
        except Exception as e:
            print(f"[WARN] Failed to close lock socket: {e}")

    executable = sys.executable
    script_path = os.path.abspath(sys.argv[0])
    
    if executable.lower().endswith("python.exe") or executable.lower().endswith("python"):
        args = [script_path] + sys.argv[1:]
        process_path = executable
    else:
        args = sys.argv[1:]
        process_path = executable

    print(f"[INFO] Attempting to restart application...")
    QProcess.startDetached(process_path, args)
    app.quit()

def main():
    global app
    
    # 修正箇所: Windowsの高DPI設定を強化 (Nuitka exe対策)
    if sys.platform == 'win32':
        try:
            # Windows 8.1 以降向けの強力な設定 (PROCESS_SYSTEM_DPI_AWARE = 1)
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                # Windows Vista/7 向けのフォールバック
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    app = QApplication.instance() or QApplication(sys.argv)
    
    # --- テーマ適用 ---
    extra = {
        'font_family': 'Meiryo UI, Yu Gothic UI, Segoe UI, sans-serif',
        'font_size': '13px',
        'density_scale': '-1',
    }
    
    try:
        apply_stylesheet(app, theme='light_blue.xml', extra=extra)
        
        # ★ 配色カスタマイズ CSS (修正版: ブランチライン表示) ★
        custom_style = """
            /* 基本文字色 */
            QWidget {
                color: #37474f;
                font-family: 'Meiryo UI', 'Yu Gothic UI', sans-serif;
            }

            /* --- グローバル: ボタンの枠線色を強制的にグレーに --- */
            QPushButton {
                color: #37474f;
                border: 1px solid #cfd8dc;
                background-color: #ffffff;
                border-radius: 4px;
                padding: 4px 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #eceff1;
                border-color: #b0bec5;
            }
            QPushButton:pressed {
                background-color: #cfd8dc;
            }

            /* --- チェックボックス & ラジオボタン --- */
            QCheckBox, QRadioButton {
                color: #37474f;
                spacing: 8px;
            }

            QCheckBox::indicator, QRadioButton::indicator {
                width: 16px;
                height: 16px;
                background-color: #ffffff;
                border: 1px solid #90a4ae;
                border-radius: 3px;
            }
            QRadioButton::indicator {
                border-radius: 8px;
            }

            QCheckBox::indicator:hover, QRadioButton::indicator:hover {
                border-color: #546e7a;
                background-color: #eceff1;
            }

            QCheckBox::indicator:checked {
                background-color: #546e7a;
                border: 1px solid #546e7a;
            }
            QRadioButton::indicator:checked {
                background-color: #546e7a;
                border: 1px solid #546e7a;
            }

            /* --- ツリービュー (色変更のみ実施) --- */
            QTreeWidget {
                color: #263238;
                background-color: #ffffff;
                alternate-background-color: #f5f5f5;
                outline: none;
                border: 1px solid #cfd8dc;
            }
            
            /* ブランチラインの基本設定 */
            QTreeWidget::branch {
                background: palette(base);
            }

            /* 縦線（siblings がある場合） */
            QTreeWidget::branch:has-siblings:!adjoins-item {
                border-image: none;
                /* 色を #b0bec5 から transparent に変更 */
                border-left: 1px solid transparent;
            }

            /* L字型（siblings があり、アイテムに隣接） */
            QTreeWidget::branch:has-siblings:adjoins-item {
                border-image: none;
                /* 色を #b0bec5 から transparent に変更 */
                border-left: 1px solid transparent;
                border-top: 1px solid transparent;
            }

            /* 終端L字型（siblings なし、アイテムに隣接） */
            QTreeWidget::branch:!has-children:!has-siblings:adjoins-item {
                border-image: none;
                /* 色を #b0bec5 から transparent に変更 */
                border-left: 1px solid transparent;
                border-top: 1px solid transparent;
            }

            /* 閉じた状態の矢印（▶）の左側の線 */
            QTreeWidget::branch:has-children:!has-siblings:closed,
            QTreeWidget::branch:closed:has-children:has-siblings {
                border-image: none;
                /* 色を #b0bec5 から transparent に変更 */
                border-left: 1px solid transparent;
            }

            /* 開いた状態の矢印（▼）の左側の線 */
            QTreeWidget::branch:open:has-children:!has-siblings,
            QTreeWidget::branch:open:has-children:has-siblings {
                border-image: none;
                /* 色を #b0bec5 から transparent に変更 */
                border-left: 1px solid transparent;
            }

            /* 矢印インジケーター（閉じた状態: ▶） */
            QTreeWidget::branch:has-children:closed {
                background: palette(base);
            }

            /* 矢印インジケーター（開いた状態: ▼） */
            QTreeWidget::branch:has-children:open {
                background: palette(base);
            }

            /* 選択行のハイライト */
            QTreeWidget::item:selected {
                background-color: #eceff1;
                color: #000000;
                border: 1px solid #b0bec5;
                border-radius: 4px;
            }

            /* --- スクロールバー --- */
            QScrollBar:vertical {
                background: #f5f5f5;
                width: 12px;
            }
            QScrollBar::handle:vertical {
                background: #90a4ae;
                min-height: 20px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical:hover {
                background: #78909c;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }

            /* --- 入力欄 (修正: フォントサイズ標準化・パディング縮小) --- */
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
                color: #000000;
                background-color: #ffffff;
                border: 1px solid #cfd8dc;
                border-radius: 4px;
                padding: 2px 4px; /* パディングを縮小 */
                selection-background-color: #78909c;
                selection-color: #ffffff;
                /* font-weight: bold; を削除 */
                /* font-size: 14px; を削除 (標準サイズへ) */
            }
            
            QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
                border: 1px solid #546e7a;
            }
            
            /* --- スピンボックスの矢印 --- */
            QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {
                border: none;
                background: transparent;
                width: 16px; /* 幅を少し縮小 */
            }
            QAbstractSpinBox::up-arrow { 
                image: none; 
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-bottom: 4px solid #546e7a; 
            }
            QAbstractSpinBox::down-arrow { 
                image: none; 
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 4px solid #546e7a; 
            }

            /* 使い方タブ */
            QTextEdit {
                color: #212121;
                background-color: #ffffff;
                border: 1px solid #cfd8dc;
            }
            
            /* 無効化状態 */
            QWidget:disabled {
                color: #bdbdbd;
                border-color: #e0e0e0;
            }
        """
        app.setStyleSheet(app.styleSheet() + custom_style)
        
    except Exception as e:
        print(f"[WARN] Failed to apply qt-material theme: {e}")

    logger = Logger()
    locale_manager = LocaleManager()
    logger.set_locale_manager(locale_manager)

    if not check_and_lock():
        error_box = QMessageBox()
        error_box.setWindowTitle(locale_manager.tr("error_already_running_title"))
        error_box.setIcon(QMessageBox.Icon.Warning)
        error_box.setText(locale_manager.tr("error_already_running_text"))
        error_box.setStandardButtons(QMessageBox.StandardButton.Ok)
        error_box.exec()
        sys.exit(1)
    
    config_manager = ConfigManager(logger)
    capture_manager = CaptureManager(logger)
    
    ui_manager = UIManager(
        core_engine=None,
        capture_manager=capture_manager,
        config_manager=config_manager,
        logger=logger,
        locale_manager=locale_manager
    )
    
    logger.set_ui(ui_manager)
    
    core_engine = CoreEngine(
        ui_manager=ui_manager,
        capture_manager=capture_manager,
        config_manager=config_manager,
        logger=logger,
        locale_manager=locale_manager
    )
    
    ui_manager.core_engine = core_engine
    ui_manager.logger = logger
    
    core_engine.updateStatus.connect(ui_manager.set_status)
    core_engine.updatePreview.connect(ui_manager.update_image_preview)
    core_engine.updateRecAreaPreview.connect(ui_manager.update_rec_area_preview)
    core_engine.cacheBuildFinished.connect(ui_manager.on_cache_build_finished)
    
    core_engine.selectionProcessStarted.connect(ui_manager.on_selection_process_started)
    core_engine.selectionProcessFinished.connect(ui_manager.on_selection_process_finished)
    core_engine.windowScaleCalculated.connect(ui_manager.on_window_scale_calculated)
    core_engine.askToSaveWindowBaseSizeSignal.connect(ui_manager.show_prompt_to_save_base_size)
    core_engine.askToApplyWindowScaleSignal.connect(ui_manager.show_prompt_to_apply_scale)
    core_engine.appContextChanged.connect(ui_manager.on_app_context_changed)
    core_engine.restartApplicationRequested.connect(restart_application)
    
    ui_manager.startMonitoringRequested.connect(core_engine.start_monitoring)
    ui_manager.stopMonitoringRequested.connect(core_engine.stop_monitoring)
    ui_manager.loadImagesRequested.connect(core_engine.load_images_into_manager)
    ui_manager.imageSettingsChanged.connect(core_engine.on_image_settings_changed)
    ui_manager.captureImageRequested.connect(core_engine.capture_image_for_registration)
    ui_manager.deleteItemsRequested.connect(core_engine.delete_selected_items)
    ui_manager.renameItemRequested.connect(core_engine.rename_item)
    ui_manager.folderSettingsChanged.connect(core_engine.on_folder_settings_changed)
    ui_manager.orderChanged.connect(core_engine.on_order_changed)
    ui_manager.createFolderRequested.connect(core_engine.create_folder)
    ui_manager.moveItemIntoFolderRequested.connect(core_engine.move_item_into_folder)
    ui_manager.itemsMovedIntoFolder.connect(core_engine.move_items_into_folder)
    ui_manager.moveItemOutOfFolderRequested.connect(core_engine.move_item_out_of_folder)
    ui_manager.setRecAreaMethodSelected.connect(core_engine.set_recognition_area)

    ui_manager.connect_signals()
    
    try:
        primary_screen = app.primaryScreen()
        if primary_screen:
            primary_screen.geometryChanged.connect(core_engine.on_screen_geometry_changed)
            logger.log("log_screen_geometry_listener_attached")
        else:
            logger.log("log_screen_geometry_listener_failed")
    except Exception as e:
        logger.log("log_screen_geometry_listener_error", str(e))

    ui_manager.set_tree_enabled(False)
    capture_manager.prime_mss()
    future = core_engine.thread_pool.submit(core_engine._build_template_cache)
    future.add_done_callback(core_engine._on_cache_build_done)
    
    ui_manager.show()

    if sys.platform != 'win32':
        def run_initialization_dialog():
            try:
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
