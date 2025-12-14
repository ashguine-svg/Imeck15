# main.py
# ★★★ 修正: 2重起動時に既存の翻訳キーを使って警告メッセージを表示 ★★★

import sys
import os
import socket
import ctypes
import requests  # 追加: ダウンロード用
import logging   # 追加: ログ用
import time      # 追加: リトライ待機用
from pathlib import Path # 追加: パス操作用

# パス設定
try:
    if getattr(sys, 'frozen', False):
        script_directory = os.path.dirname(sys.executable)
    else:
        script_directory = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, script_directory)
except NameError:
    sys.path.insert(0, os.getcwd())

# IME設定
if sys.platform == 'linux':
    if "QT_IM_MODULE" not in os.environ:
        os.environ["QT_IM_MODULE"] = "fcitx"

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QObject, Signal, QTimer, QProcess, Qt
from qt_material import apply_stylesheet

# 既存モジュールのインポート
from ui import UIManager
from core import CoreEngine
from capture import CaptureManager
from config import ConfigManager
from dialogs import InitializationDialog
from locale_manager import LocaleManager

# 追加: OCR言語マッピングのインポート
from ocr_manager import LOCALE_TO_TESS_CODE

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
            if self.locale_manager: translated_message = self.locale_manager.tr(message, *args)
            else: translated_message = message % args if args else message
        except Exception: translated_message = f"{message} (args: {args})"
        print(f"[LOG] {translated_message}")
        self.logReady.emit(translated_message)

# ----------------------------------------------------------------------
# 追加機能: Tesseract初期化と自動ダウンロード (リトライ版)
# ----------------------------------------------------------------------
def initialize_tesseract(logger_instance, config_manager):
    """
    OCR機能に必要なデータと環境変数をセットアップします。
    ホームディレクトリ下の click_pic/tessdata に英語(eng)と現在のアプリ設定言語をダウンロードします。
    """
    tessdata_dir = Path.home() / "click_pic" / "tessdata"

    # 1. 保存先ディレクトリの作成
    if not tessdata_dir.exists():
        try:
            tessdata_dir.mkdir(parents=True, exist_ok=True)
            logger_instance.log(f"[INIT] ディレクトリを作成しました: {tessdata_dir}")
        except Exception as e:
            logger_instance.log(f"[ERROR] tessdataディレクトリ作成失敗: {e}")
            return False

    # 2. 環境変数 TESSDATA_PREFIX の設定
    os.environ['TESSDATA_PREFIX'] = str(tessdata_dir)
    logger_instance.log(f"[INIT] TESSDATA_PREFIX を設定: {tessdata_dir}")

    # 3. ダウンロード対象の言語リストを作成
    required_langs = {'eng'} # 英語は基本機能として必須
    
    # アプリ設定から現在の言語を取得
    try:
        app_config = config_manager.load_app_config()
        current_locale = app_config.get("language", "en_US")
        
        target_tess_code = LOCALE_TO_TESS_CODE.get(current_locale)
        if target_tess_code:
            required_langs.add(target_tess_code)
            
    except Exception as e:
        logger_instance.log(f"[WARN] 設定読み込み中にエラー発生。デフォルト(eng)のみ確認します: {e}")

    logger_instance.log(f"[INIT] OCR言語データを確認中: {list(required_langs)}")

    # 4. 学習データのダウンロード (高速版 tessdata_fast)
    base_url = "https://github.com/tesseract-ocr/tessdata_fast/raw/main/{}.traineddata"
    headers = {'User-Agent': 'Mozilla/5.0'} 

    all_success = True
    
    MAX_RETRIES = 3
    RETRY_DELAY = 2 # 秒

    for lang in required_langs:
        file_path = tessdata_dir / f"{lang}.traineddata"
        
        if not file_path.exists():
            logger_instance.log(f"[INIT] {lang}.traineddata がありません。ダウンロード中...")
            
            # リトライループ
            download_success = False
            last_error = None
            
            for attempt in range(MAX_RETRIES):
                try:
                    url = base_url.format(lang)
                    # タイムアウト設定
                    response = requests.get(url, headers=headers, stream=True, timeout=30)
                    response.raise_for_status()
                    
                    with open(file_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)
                    
                    logger_instance.log(f"[INIT] ダウンロード完了: {lang}")
                    download_success = True
                    break # 成功したらループを抜ける
                    
                except Exception as e:
                    last_error = e
                    if attempt < MAX_RETRIES - 1:
                        logger_instance.log(f"[WARN] ダウンロード失敗 ({attempt+1}/{MAX_RETRIES})。{RETRY_DELAY}秒後に再試行します: {e}")
                        time.sleep(RETRY_DELAY)
                        # 失敗した不完全なファイルがあれば削除してリトライ
                        if file_path.exists():
                            try: file_path.unlink()
                            except: pass
            
            if not download_success:
                logger_instance.log(f"[ERROR] {lang} のダウンロードに失敗しました (Final): {last_error}")
                all_success = False
        else:
            # 既に存在する場合は何もしない
            pass

    return all_success

def restart_application():
    global app, _lock_socket
    if not app: return
    if _lock_socket:
        try: _lock_socket.close(); _lock_socket = None
        except Exception: pass
    executable = sys.executable
    script_path = os.path.abspath(sys.argv[0])
    if executable.lower().endswith("python.exe") or executable.lower().endswith("python"):
        args = [script_path] + sys.argv[1:]
        process_path = executable
    else:
        args = sys.argv[1:]
        process_path = executable
    QProcess.startDetached(process_path, args)
    app.quit()

def main():
    global app
    if sys.platform == 'win32':
        try: ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception: pass

    app = QApplication.instance() or QApplication(sys.argv)
    
    # テーマ設定
    extra = {'font_family': 'Meiryo UI, Yu Gothic UI, Segoe UI, sans-serif', 'font_size': '13px', 'density_scale': '-1'}
    try:
        apply_stylesheet(app, theme='light_blue.xml', extra=extra)
        custom_style = """
            QWidget { color: #37474f; font-family: 'Meiryo UI', 'Yu Gothic UI', sans-serif; }
            QPushButton { color: #37474f; border: 1px solid #cfd8dc; background-color: #ffffff; border-radius: 4px; padding: 4px 12px; font-weight: bold; }
            QPushButton:hover { background-color: #eceff1; border-color: #b0bec5; }
            QPushButton:pressed { background-color: #cfd8dc; }
            QCheckBox, QRadioButton { color: #37474f; spacing: 8px; }
            QCheckBox::indicator, QRadioButton::indicator { width: 16px; height: 16px; background-color: #ffffff; border: 1px solid #90a4ae; border-radius: 3px; }
            QRadioButton::indicator { border-radius: 8px; }
            QCheckBox::indicator:hover, QRadioButton::indicator:hover { border-color: #546e7a; background-color: #eceff1; }
            QCheckBox::indicator:checked, QRadioButton::indicator:checked { background-color: #546e7a; border: 1px solid #546e7a; }
            QTreeWidget { color: #263238; background-color: #ffffff; alternate-background-color: #f5f5f5; outline: none; border: 1px solid #cfd8dc; }
            QTreeWidget::branch { background: palette(base); }
            QTreeWidget::branch:has-siblings:!adjoins-item { border-image: none; border-left: 1px solid transparent; }
            QTreeWidget::branch:has-siblings:adjoins-item { border-image: none; border-left: 1px solid transparent; border-top: 1px solid transparent; }
            QTreeWidget::branch:!has-children:!has-siblings:adjoins-item { border-image: none; border-left: 1px solid transparent; border-top: 1px solid transparent; }
            QTreeWidget::branch:has-children:!has-siblings:closed, QTreeWidget::branch:closed:has-children:has-siblings { border-image: none; border-left: 1px solid transparent; }
            QTreeWidget::branch:open:has-children:!has-siblings, QTreeWidget::branch:open:has-children:has-siblings { border-image: none; border-left: 1px solid transparent; }
            QTreeWidget::branch:has-children:closed { background: palette(base); }
            QTreeWidget::branch:has-children:open { background: palette(base); }
            QTreeWidget::item:selected { background-color: #eceff1; color: #000000; border: 1px solid #b0bec5; border-radius: 4px; }
            QScrollBar:vertical { background: #f5f5f5; width: 12px; }
            QScrollBar::handle:vertical { background: #90a4ae; min-height: 20px; border-radius: 6px; }
            QScrollBar::handle:vertical:hover { background: #78909c; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { color: #000000; background-color: #ffffff; border: 1px solid #cfd8dc; border-radius: 4px; padding: 2px 4px; selection-background-color: #78909c; selection-color: #ffffff; }
            QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border: 1px solid #546e7a; }
            QAbstractSpinBox::up-button, QAbstractSpinBox::down-button { border: none; background: transparent; width: 16px; }
            QAbstractSpinBox::up-arrow { image: none; border-left: 4px solid transparent; border-right: 4px solid transparent; border-bottom: 4px solid #546e7a; }
            QAbstractSpinBox::down-arrow { image: none; border-left: 4px solid transparent; border-right: 4px solid transparent; border-top: 4px solid #546e7a; }
            QTextEdit { color: #212121; background-color: #ffffff; border: 1px solid #cfd8dc; }
            QWidget:disabled { color: #bdbdbd; border-color: #e0e0e0; }
        """
        app.setStyleSheet(app.styleSheet() + custom_style)
    except Exception as e: print(f"[WARN] Failed to apply theme: {e}")

    logger = Logger()
    locale_manager = LocaleManager()
    logger.set_locale_manager(locale_manager)

    # ★★★ 修正: 2重起動時はメッセージボックスを出して終了 ★★★
    if not check_and_lock():
        # JSON内のキーを使用
        title = locale_manager.tr("error_already_running_title")
        msg = locale_manager.tr("error_already_running_text")
        
        # 万が一キーが存在しない場合のフォールバック
        if title == "error_already_running_title": 
            title = "Startup Error"
        if msg == "error_already_running_text": 
            msg = "Imeck15 is already running."
            
        QMessageBox.warning(None, title, msg)
        sys.exit(1)
    
    # ConfigManager初期化
    config_manager = ConfigManager(logger)

    logger.log("OCR環境を確認中...", force=True)
    if not initialize_tesseract(logger, config_manager):
        logger.log("[WARN] OCRデータの準備に失敗しました。OCR機能は動作しない可能性があります。", force=True)
    else:
        logger.log("OCR環境の準備が完了しました。", force=True)

    capture_manager = CaptureManager(logger)
    ui_manager = UIManager(None, capture_manager, config_manager, logger, locale_manager)
    logger.set_ui(ui_manager)
    core_engine = CoreEngine(ui_manager, capture_manager, config_manager, logger, locale_manager)
    ui_manager.core_engine = core_engine
    
    # シグナル接続
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
    ui_manager.saveCapturedImageRequested.connect(core_engine.handle_save_captured_image)

    ui_manager.connect_signals()
    
    try:
        primary_screen = app.primaryScreen()
        if primary_screen:
            primary_screen.geometryChanged.connect(core_engine.on_screen_geometry_changed)
    except Exception: pass

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
            except Exception: pass
        QTimer.singleShot(200, run_initialization_dialog)
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
