# ui.py

import sys
import json
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QPushButton, QVBoxLayout, QWidget, QLabel,
    QFrame, QHBoxLayout, QGroupBox, QSpinBox, QDoubleSpinBox, QCheckBox,
    QGridLayout, QSizePolicy, QSpacerItem, QToolButton, QFileDialog, QLineEdit,
    QTreeWidget, QTreeWidgetItem, QMenu, QTabWidget, QTextEdit, QDialog, QMessageBox,
    QComboBox, QDialogButtonBox, QRadioButton, QButtonGroup, QScrollArea, QAbstractItemView,
    QProxyStyle, QStyle, QStyleOptionViewItem, QToolTip,
    QInputDialog, QTreeWidgetItemIterator
)
from PySide6.QtGui import (
    QIcon, QPixmap, QImage, QPainter, QColor, QBrush, QFont, QPalette,
    QCursor 
)
from PySide6.QtCore import (
    Qt, QSize, QThread, Signal, QTimer, QObject, QRect, QPoint, QRectF, QPointF, QEvent,
    Slot
)

import os
import subprocess
import cv2
import numpy as np
from pathlib import Path

# --- 分割されたモジュールのインポート ---
from ui_tree_panel import LeftPanel
from ui_app_settings import AppSettingsPanel

from capture import DXCAM_AVAILABLE
from floating_window import FloatingWindow
from dialogs import RecAreaSelectionDialog
from custom_widgets import ScaledPixmapLabel, InteractivePreviewLabel
from preview_mode_manager import PreviewModeManager

try:
    OPENCL_AVAILABLE = cv2.ocl.haveOpenCL()
except:
    OPENCL_AVAILABLE = False

# --- UIManager ---
class UIManager(QMainWindow):
    startMonitoringRequested = Signal(); stopMonitoringRequested = Signal();
    loadImagesRequested = Signal(list); setRecAreaMethodSelected = Signal(str); captureImageRequested = Signal()
    deleteItemsRequested = Signal(list)
    orderChanged = Signal()
    itemsMovedIntoFolder = Signal(list, str)
    folderSettingsChanged = Signal()
    imageSettingsChanged = Signal(dict); createFolderRequested = Signal(); moveItemIntoFolderRequested = Signal()
    moveItemOutOfFolderRequested = Signal()
    appConfigChanged = Signal()
    
    renameItemRequested = Signal(str, str)
    
    saveCapturedImageRequested = Signal(str, np.ndarray)
    
    def __init__(self, core_engine, capture_manager, config_manager, logger, locale_manager):
        super().__init__(parent=None)

        self.logger = logger
        self.locale_manager = locale_manager

        self.core_engine = core_engine
        self.capture_manager = capture_manager
        self.config_manager = config_manager
        
        # --- サブパネルのインスタンス ---
        self.left_panel = None
        self.app_settings_panel = None

        # --- 外部互換性のための属性（これらはサブパネル生成後に参照を貼る） ---
        self.item_settings_widgets = {}
        self.app_settings_widgets = {} # AppSettingsPanelへ委譲
        self.auto_scale_widgets = {}   # AppSettingsPanelへ委譲
        self.available_langs = {}      # AppSettingsPanelへ委譲
        self.image_tree = None         # LeftPanelへ委譲

        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint)

        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(1000)
        self.is_processing_tree_change = False

        self.app_config = self.config_manager.load_app_config()
        self.locale_manager.load_locale(self.app_config.get("language", "en_US"))

        self.splash_pixmap = None
        try:
            locales_path = self.locale_manager.locales_dir
            splash_paths = [locales_path / "splash.png", locales_path / "splash.jpg"]
            for p in splash_paths:
                if p.exists(): self.splash_pixmap = QPixmap(str(p)); break
            if self.splash_pixmap and self.splash_pixmap.isNull(): self.splash_pixmap = None

        except Exception as e: 
            self.logger.log("log_error_splash_load", str(e)); self.splash_pixmap = None

        self.is_minimal_mode = False
        self.normal_ui_geometries = {}
        self.floating_window = None
        
        self.pending_captured_image = None

        self.setup_ui()
        
        # --- 互換性エイリアスの設定 (main.py等からのアクセス用) ---
        self.image_tree = self.left_panel.image_tree
        self.app_settings_widgets = self.app_settings_panel.app_settings_widgets
        self.auto_scale_widgets = self.app_settings_panel.auto_scale_widgets
        self.available_langs = self.app_settings_panel.available_langs
        self.language_combo = self.app_settings_panel.language_combo
        
        self.retranslate_ui()

        self.preview_mode_manager = PreviewModeManager(
            preview_label=self.preview_label,
            roi_button=self.item_settings_widgets['set_roi_variable_button'],
            point_cb=self.item_settings_widgets['point_click'],
            range_cb=self.item_settings_widgets['range_click'],
            random_cb=self.item_settings_widgets['random_click'],
            roi_enabled_cb=self.item_settings_widgets['roi_enabled'],
            roi_mode_fixed=self.item_settings_widgets['roi_mode_fixed'],
            roi_mode_variable=self.item_settings_widgets['roi_mode_variable'],
            locale_manager=self.locale_manager
        )

        self.main_capture_button = self.capture_image_button

        QTimer.singleShot(100, self.adjust_initial_size)
        QTimer.singleShot(0, lambda: self.update_image_preview(None, None))
        QTimer.singleShot(0, self._update_capture_button_state)
            
    def open_image_folder(self):
        folder_path = str(self.config_manager.base_dir)
        try:
            if sys.platform == 'win32':
                os.startfile(folder_path)
            elif sys.platform == 'darwin':
                subprocess.run(['open', folder_path])
            else:
                subprocess.run(['xdg-open', folder_path])
            self.logger.log("log_open_folder", folder_path)
        except Exception as e:
            self.logger.log("log_error_open_folder", str(e))
            QMessageBox.warning(self, self.locale_manager.tr("error_title_open_folder"), self.locale_manager.tr("error_message_open_folder", str(e)))
            
    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        self.main_layout = QVBoxLayout(central_widget)

        # 1. ヘッダーエリア
        self._setup_header(self.main_layout)

        # コンテンツエリア
        content_frame = QFrame()
        self.content_layout = QHBoxLayout(content_frame)
        
        # 2. 左パネル (ui_tree_panel.py へ委譲)
        self.left_panel = LeftPanel(self, self.content_layout, self.config_manager, self.logger, self.locale_manager)

        # 3. 右パネル (ガワだけ作成)
        self._setup_right_panel(self.content_layout)

        self.main_layout.addWidget(content_frame)
        
        # --- ★★★ タブの順序制御 ★★★ ---
        # 1. [画像プレビュー]
        self._setup_tab_preview()
        
        # 2. [認識範囲]
        self._setup_tab_rec_area()
        
        # 3 & 4. [アプリ設定] -> [自動スケール] (AppSettingsPanel へ委譲)
        self.app_settings_panel = AppSettingsPanel(self, self.config_manager, self.app_config, self.locale_manager)
        self.app_settings_panel.setup_ui(self.preview_tabs)
        
        # 5. [使い方]
        self._setup_tab_usage()
        
        # 6. [ログ]
        self._setup_tab_log()
        # --- ▲▲▲ 修正完了 ▲▲▲ ---

    def _setup_header(self, parent_layout):
        header_frame = QFrame()
        header_layout = QHBoxLayout(header_frame)
        
        self.monitor_button = QPushButton()
        self.monitor_button.setFixedSize(120, 30)
        header_layout.addWidget(self.monitor_button)
        
        self.header_rec_area_button = QPushButton()
        self.header_rec_area_button.setFixedSize(120, 30)
        self.header_rec_area_button.clicked.connect(self.setRecAreaDialog)
        header_layout.addWidget(self.header_rec_area_button)
        
        self.toggle_minimal_ui_button = QPushButton()
        self.toggle_minimal_ui_button.setFixedSize(120, 30)
        header_layout.addWidget(self.toggle_minimal_ui_button)
        
        self.open_image_folder_button = QPushButton()
        self.open_image_folder_button.setFixedSize(120, 30)
        header_layout.addWidget(self.open_image_folder_button)
        
        self.capture_image_button = QPushButton()
        self.capture_image_button.setFixedSize(120, 30)
        self.capture_image_button.clicked.connect(self.captureImageRequested.emit)
        header_layout.addWidget(self.capture_image_button)
        
        header_layout.addSpacerItem(QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        
        self.status_label = QLabel()
        self.status_label.setStyleSheet("font-size: 16px; font-weight: bold; color: green;")
        header_layout.addWidget(self.status_label)
        
        parent_layout.addWidget(header_frame)

    def _setup_right_panel(self, parent_layout):
        right_frame = QFrame()
        right_layout = QVBoxLayout(right_frame)
        
        self.preview_tabs = QTabWidget()
        
        # ★★★ 修正: ここでのタブ追加処理を削除し setup_ui に移動 ★★★
        
        right_layout.addWidget(self.preview_tabs, 2)
        
        self._setup_item_settings_group(right_layout)
        parent_layout.addWidget(right_frame, 2)

    def _setup_tab_preview(self):
        self.main_preview_widget = QWidget()
        layout = QVBoxLayout(self.main_preview_widget)
        self.preview_label = InteractivePreviewLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.preview_label)
        self.preview_tabs.addTab(self.main_preview_widget, "")

    def _setup_tab_rec_area(self):
        rec_area_widget = QWidget()
        layout = QVBoxLayout(rec_area_widget)
        buttons_layout = QHBoxLayout()
        self.set_rec_area_button_main_ui = QPushButton()
        self.clear_rec_area_button_main_ui = QPushButton()
        buttons_layout.addWidget(self.set_rec_area_button_main_ui)
        buttons_layout.addWidget(self.clear_rec_area_button_main_ui)
        layout.addLayout(buttons_layout)
        
        self.rec_area_preview_label = ScaledPixmapLabel()
        self.rec_area_preview_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.rec_area_preview_label)
        self.preview_tabs.addTab(rec_area_widget, "")

    def _setup_tab_log(self):
        log_widget = QWidget()
        layout = QVBoxLayout(log_widget)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)
        self.preview_tabs.addTab(log_widget, "")

    def _setup_tab_usage(self):
        usage_widget = QWidget()
        layout = QVBoxLayout(usage_widget)
        self.usage_text = QTextEdit()
        self.usage_text.setReadOnly(True)
        layout.addWidget(self.usage_text)
        usage_widget.setLayout(layout)
        self.preview_tabs.addTab(usage_widget, "")

    def _setup_item_settings_group(self, parent_layout):
        self.item_settings_group = QGroupBox()
        layout = QGridLayout(self.item_settings_group)
        layout.setColumnStretch(1, 1); layout.setColumnStretch(3, 1)
        
        self.item_threshold_label = QLabel()
        layout.addWidget(self.item_threshold_label, 0, 0)
        self.item_settings_widgets['threshold'] = QDoubleSpinBox()
        self.item_settings_widgets['threshold'].setRange(0.5, 1.0); self.item_settings_widgets['threshold'].setSingleStep(0.01); self.item_settings_widgets['threshold'].setValue(0.8)
        layout.addWidget(self.item_settings_widgets['threshold'], 0, 1)
        
        self.item_interval_label = QLabel()
        layout.addWidget(self.item_interval_label, 0, 2)
        self.item_settings_widgets['interval_time'] = QDoubleSpinBox()
        self.item_settings_widgets['interval_time'].setRange(0.1, 10.0); self.item_settings_widgets['interval_time'].setSingleStep(0.1); self.item_settings_widgets['interval_time'].setValue(1.5)
        layout.addWidget(self.item_settings_widgets['interval_time'], 0, 3)
        
        self.item_settings_widgets['backup_click'] = QCheckBox()
        layout.addWidget(self.item_settings_widgets['backup_click'], 1, 0)
        self.item_settings_widgets['backup_time'] = QDoubleSpinBox()
        self.item_settings_widgets['backup_time'].setRange(1.0, 600.0); self.item_settings_widgets['backup_time'].setSingleStep(1.0); self.item_settings_widgets['backup_time'].setValue(300.0)
        layout.addWidget(self.item_settings_widgets['backup_time'], 1, 1)
        
        self.item_debounce_label = QLabel()
        layout.addWidget(self.item_debounce_label, 1, 2)
        self.item_settings_widgets['debounce_time'] = QDoubleSpinBox()
        self.item_settings_widgets['debounce_time'].setRange(0.0, 10.0); self.item_settings_widgets['debounce_time'].setSingleStep(0.1); self.item_settings_widgets['debounce_time'].setValue(0.0)
        layout.addWidget(self.item_settings_widgets['debounce_time'], 1, 3)
        
        click_type_layout = QHBoxLayout()
        self.item_settings_widgets['point_click'] = QCheckBox()
        self.item_settings_widgets['range_click'] = QCheckBox()
        self.item_settings_widgets['random_click'] = QCheckBox()
        click_type_layout.addWidget(self.item_settings_widgets['point_click'])
        click_type_layout.addWidget(self.item_settings_widgets['range_click'])
        click_type_layout.addWidget(self.item_settings_widgets['random_click'])
        layout.addLayout(click_type_layout, 2, 0, 1, 4)
        
        separator = QFrame(); separator.setFrameShape(QFrame.Shape.HLine); separator.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(separator, 3, 0, 1, 4)
        
        self.item_settings_widgets['roi_enabled'] = QCheckBox()
        layout.addWidget(self.item_settings_widgets['roi_enabled'], 4, 0)
        
        roi_mode_layout = QHBoxLayout()
        self.item_settings_widgets['roi_mode_fixed'] = QRadioButton()
        self.item_settings_widgets['roi_mode_variable'] = QRadioButton()
        self.roi_mode_group = QButtonGroup(self)
        self.roi_mode_group.addButton(self.item_settings_widgets['roi_mode_fixed'])
        self.roi_mode_group.addButton(self.item_settings_widgets['roi_mode_variable'])
        roi_mode_layout.addWidget(self.item_settings_widgets['roi_mode_fixed'])
        roi_mode_layout.addWidget(self.item_settings_widgets['roi_mode_variable'])
        layout.addLayout(roi_mode_layout, 4, 1)
        
        self.item_settings_widgets['set_roi_variable_button'] = QPushButton()
        self.item_settings_widgets['set_roi_variable_button'].setCheckable(True)
        layout.addWidget(self.item_settings_widgets['set_roi_variable_button'], 4, 2, 1, 2)
        
        parent_layout.addWidget(self.item_settings_group, 1)

    def changeEvent(self, event):
        if event.type() == QEvent.PaletteChange or event.type() == QEvent.ThemeChange:
            if hasattr(self, 'left_panel') and self.left_panel.image_tree:
                self.left_panel.image_tree.style().unpolish(self.left_panel.image_tree)
                self.left_panel.image_tree.style().polish(self.left_panel.image_tree)
        super().changeEvent(event)
    
    def connect_signals(self):
        if hasattr(self, '_signals_connected') and self._signals_connected:
            return

        self.monitor_button.clicked.connect(self.toggle_monitoring)
        self.toggle_minimal_ui_button.clicked.connect(self.toggle_minimal_ui_mode)
        self.open_image_folder_button.clicked.connect(self.open_image_folder)

        self.set_rec_area_button_main_ui.clicked.connect(self.setRecAreaDialog)
        if self.core_engine:
            # core_engineへの逆参照セット
            if self.left_panel: self.left_panel.core_engine = self.core_engine
            
            self.clear_rec_area_button_main_ui.clicked.connect(self.core_engine.clear_recognition_area)
            self.core_engine.windowScaleCalculated.connect(self._update_capture_button_state)

        # Item Settings Signals
        self.item_settings_widgets['threshold'].valueChanged.connect(self._emit_settings_for_save)
        self.item_settings_widgets['interval_time'].valueChanged.connect(self._emit_settings_for_save)
        self.item_settings_widgets['backup_time'].valueChanged.connect(self._emit_settings_for_save)
        self.item_settings_widgets['debounce_time'].valueChanged.connect(self._emit_settings_for_save)

        self.item_settings_widgets['backup_click'].stateChanged.connect(
            lambda state, w=self.item_settings_widgets['backup_click']: self.preview_mode_manager.handle_ui_toggle(w, bool(state))
        )
        self.item_settings_widgets['point_click'].toggled.connect(
             lambda checked, w=self.item_settings_widgets['point_click']: self.preview_mode_manager.handle_ui_toggle(w, checked)
        )
        self.item_settings_widgets['range_click'].toggled.connect(
             lambda checked, w=self.item_settings_widgets['range_click']: self.preview_mode_manager.handle_ui_toggle(w, checked)
        )
        self.item_settings_widgets['random_click'].stateChanged.connect(
             lambda state, w=self.item_settings_widgets['random_click']: self.preview_mode_manager.handle_ui_toggle(w, bool(state))
        )
        self.item_settings_widgets['roi_enabled'].stateChanged.connect(
             lambda state, w=self.item_settings_widgets['roi_enabled']: self.preview_mode_manager.handle_ui_toggle(w, bool(state))
        )
        self.item_settings_widgets['roi_mode_fixed'].toggled.connect(
             lambda checked, w=self.item_settings_widgets['roi_mode_fixed']: self.preview_mode_manager.handle_ui_toggle(w, checked)
        )
        self.item_settings_widgets['roi_mode_variable'].toggled.connect(
             lambda checked, w=self.item_settings_widgets['roi_mode_variable']: self.preview_mode_manager.handle_ui_toggle(w, checked)
        )
        self.item_settings_widgets['set_roi_variable_button'].toggled.connect(
            self.preview_mode_manager._drawing_mode_button_toggled
        )

        if self.core_engine:
            self.preview_mode_manager.settings_changed_externally.connect(self._update_ui_from_preview_manager)
            self.preview_mode_manager.previewDataApplied.connect(self._emit_settings_for_save)
            self.save_timer.timeout.connect(self.core_engine.save_current_settings)
            self.appConfigChanged.connect(self.core_engine.on_app_config_changed)
            
            self.core_engine.capturedImageReadyForPreview.connect(self.on_captured_image_ready_for_preview)
            self.core_engine.captureFailedSignal.connect(self.on_capture_failed)
            
            self.saveCapturedImageRequested.connect(self.core_engine.handle_save_captured_image)

        self._signals_connected = True

    def _emit_settings_for_save(self, *args):
        if not hasattr(self, 'preview_mode_manager') or not self.core_engine: return
            
        path, _ = self.get_selected_item_path()
        if not path or Path(path).is_dir(): return

        settings = self.preview_mode_manager.get_settings()
        if self.core_engine.current_image_path:
             settings['image_path'] = self.core_engine.current_image_path
        else:
             settings['image_path'] = path 

        try:
            settings['threshold'] = self.item_settings_widgets['threshold'].value()
            settings['interval_time'] = self.item_settings_widgets['interval_time'].value()
            settings['backup_time'] = self.item_settings_widgets['backup_time'].value()
            settings['debounce_time'] = self.item_settings_widgets['debounce_time'].value()
        except KeyError: return
        except Exception: return
 
        self.imageSettingsChanged.emit(settings)
               
    def _update_ui_from_preview_manager(self, settings: dict):
        if hasattr(self, 'preview_mode_manager'):
            self.preview_mode_manager._block_all_signals(True)
        try:
            self.item_settings_widgets['point_click'].setChecked(settings.get('point_click', True))
            self.item_settings_widgets['range_click'].setChecked(settings.get('range_click', False))
            self.item_settings_widgets['random_click'].setChecked(settings.get('random_click', False))
            self.item_settings_widgets['backup_click'].setChecked(settings.get('backup_click', False))
            self.item_settings_widgets['roi_enabled'].setChecked(settings.get('roi_enabled', False))
            
            roi_mode = settings.get('roi_mode', 'fixed')
            if roi_mode == 'variable':
                self.item_settings_widgets['roi_mode_variable'].setChecked(True)
            else:
                self.item_settings_widgets['roi_mode_fixed'].setChecked(True)
        finally:
            if hasattr(self, 'preview_mode_manager'):
                self.preview_mode_manager._block_all_signals(False)

    def update_rec_area_preview(self, cv_image: np.ndarray):
        if cv_image is None or cv_image.size == 0: self.rec_area_preview_label.set_pixmap(None); self.rec_area_preview_label.setText(self.locale_manager.tr("rec_area_preview_text")); return
        try:
            rgb_image = cv2.cvtColor(cv_image, cv2.COLOR_BGR2RGB); h, w, ch = rgb_image.shape; bytes_per_line = ch * w
            q_image = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format.Format_RGB888); pixmap = QPixmap.fromImage(q_image)
            self.rec_area_preview_label.set_pixmap(pixmap); self.rec_area_preview_label.setText("")
        except Exception as e: print(f"Error converting image for rec area preview: {e}"); self.rec_area_preview_label.setText("Preview Error"); self.rec_area_preview_label.set_pixmap(None)

    def update_log(self, message: str):
        self.log_text.append(message); scrollbar = self.log_text.verticalScrollBar(); scrollbar.setValue(scrollbar.maximum())

    def closeEvent(self, event):
        if self.floating_window:
            if self.core_engine and hasattr(self.core_engine, 'statsUpdated'):
                try:
                    self.core_engine.statsUpdated.disconnect(self.floating_window.on_stats_updated)
                except (TypeError, RuntimeError):
                    pass
            self.floating_window.close()
            
        if self.core_engine: self.core_engine.cleanup()
        self.stopMonitoringRequested.emit(); QApplication.instance().quit(); event.accept()

    def _handle_rec_area_selection(self, method: str):
        QTimer.singleShot(0, lambda: self.setRecAreaMethodSelected.emit(method))

    def setRecAreaDialog(self):
        dialog = RecAreaSelectionDialog(self.locale_manager, self)
        dialog.selectionMade.connect(self._handle_rec_area_selection)
        dialog.move(QCursor.pos())
        dialog.exec()

    def adjust_initial_size(self):
        self.setMinimumWidth(0); self.resize(960, 640)

    def toggle_minimal_ui_mode(self):
        lm = self.locale_manager.tr; self.is_minimal_mode = not self.is_minimal_mode
        if self.is_minimal_mode:
            self.normal_ui_geometries['main'] = self.geometry()
            self.showMinimized();
            
            self.floating_window = FloatingWindow(self.locale_manager)
            self.floating_window.startMonitoringRequested.connect(self.startMonitoringRequested)
            self.floating_window.stopMonitoringRequested.connect(self.stopMonitoringRequested)
            self.floating_window.captureImageRequested.connect(self.captureImageRequested)
            self.floating_window.closeRequested.connect(self.toggle_minimal_ui_mode)
            self.floating_window.setRecAreaRequested.connect(self.setRecAreaDialog)
            
            if self.core_engine:
                self.core_engine.statsUpdated.connect(self.floating_window.on_stats_updated)

            current_status_text = self.status_label.text(); current_status_color = "green"
            if current_status_text == lm("status_label_monitoring"): current_status_color = "blue"
            elif current_status_text == lm("status_label_unstable"): current_status_color = "orange"
            elif current_status_text == lm("status_label_idle_error"): current_status_color = "red"
            self.floating_window.update_status(current_status_text, current_status_color); 
            self.floating_window.show(); 
            self.toggle_minimal_ui_button.setText(lm("minimal_ui_button_stop")); 
            self._update_capture_button_state()
            
        else:
            if self.floating_window:
                if self.core_engine and hasattr(self.core_engine, 'statsUpdated'):
                    try:
                        self.core_engine.statsUpdated.disconnect(self.floating_window.on_stats_updated)
                    except (TypeError, RuntimeError):
                        pass
                self.floating_window.close(); self.floating_window = None
            
            self.showNormal();
            if 'main' in self.normal_ui_geometries: self.setGeometry(self.normal_ui_geometries['main'])
            
            self.activateWindow(); 
            self.toggle_minimal_ui_button.setText(lm("minimal_ui_button")); 
            self._update_capture_button_state()

    def retranslate_ui(self):
        lm = self.locale_manager.tr

        self.setWindowTitle(lm("window_title"))
        self.header_rec_area_button.setText(lm("recognition_area_button"))
        self.toggle_minimal_ui_button.setText(lm("minimal_ui_button") if not self.is_minimal_mode else lm("minimal_ui_button_stop"))
        self.open_image_folder_button.setText(lm("open_image_folder_button"))
        self.open_image_folder_button.setToolTip(lm("open_image_folder_tooltip"))
        self.monitor_button.setToolTip(lm("monitor_button_tooltip"))

        if not self.core_engine or not self.core_engine.is_monitoring:
            self.status_label.setText(lm("status_label_idle"))
        else:
             self.status_label.setText(lm("status_label_monitoring"))

        self.capture_image_button.setText(lm("capture_image_button"))

        # Left Panel Retranslation
        if self.left_panel: self.left_panel.retranslate_ui()
        # App Settings Retranslation
        if self.app_settings_panel: self.app_settings_panel.retranslate_ui()

        # Tabs Titles
        self.preview_tabs.setTabText(self.preview_tabs.indexOf(self.main_preview_widget), lm("tab_preview"))
        
        # RecArea Tab
        rec_area_tab_index = self.preview_tabs.indexOf(self.rec_area_preview_label.parentWidget())
        if rec_area_tab_index != -1: self.preview_tabs.setTabText(rec_area_tab_index, lm("tab_rec_area"))
        self.set_rec_area_button_main_ui.setText(lm("recognition_area_button"))
        self.clear_rec_area_button_main_ui.setText(lm("rec_area_clear_button"))
        self.rec_area_preview_label.setText(lm("rec_area_preview_text"))

        # Log Tab
        log_tab_index = self.preview_tabs.indexOf(self.log_text.parentWidget())
        if log_tab_index != -1: self.preview_tabs.setTabText(log_tab_index, lm("tab_log"))

        # Auto Scale Tab
        if self.app_settings_panel and self.app_settings_panel.auto_scale_group:
            idx_as = self.preview_tabs.indexOf(self.app_settings_panel.auto_scale_group)
            if idx_as != -1: self.preview_tabs.setTabText(idx_as, lm("tab_auto_scale"))

        # App Settings Tab (Find QScrollArea parent for app_settings_widgets)
        if self.app_settings_panel and self.app_settings_panel.app_settings_widgets:
            # Use a reliable widget to find the scroll area tab
            sample_widget = self.app_settings_panel.app_settings_widgets.get('grayscale_matching')
            if sample_widget:
                parent = sample_widget.parentWidget()
                while parent:
                    if isinstance(parent, QScrollArea):
                        idx_app = self.preview_tabs.indexOf(parent)
                        if idx_app != -1:
                            self.preview_tabs.setTabText(idx_app, lm("tab_app_settings"))
                        break
                    parent = parent.parentWidget()

        # Usage Tab
        usage_tab_index = self.preview_tabs.indexOf(self.usage_text.parentWidget())
        if usage_tab_index != -1: self.preview_tabs.setTabText(usage_tab_index, lm("tab_usage"))
        try:
            usage_html_path_str = lm("usage_html_path")
            base_path = Path(os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else __file__))
            usage_html_path = base_path / usage_html_path_str
            if usage_html_path.exists():
                with open(usage_html_path, 'r', encoding='utf-8') as f:
                    self.usage_text.setHtml(f.read())
            else: self.usage_text.setText(f"Usage file not found: {usage_html_path}")
        except Exception as e: self.usage_text.setText(f"Error loading usage file ({usage_html_path_str}): {e}")

        # Item Settings
        self.item_settings_group.setTitle(lm("group_item_settings"))
        self.item_threshold_label.setText(lm("item_setting_threshold"))
        self.item_interval_label.setText(lm("item_setting_interval"))
        self.item_settings_widgets['backup_click'].setText(lm("item_setting_backup_click"))
        self.item_debounce_label.setText(lm("item_setting_debounce"))
        self.item_settings_widgets['debounce_time'].setToolTip(lm("item_setting_debounce_tooltip"))
        self.item_settings_widgets['point_click'].setText(lm("item_setting_point_click"))
        self.item_settings_widgets['point_click'].setToolTip(lm("item_setting_point_click_tooltip"))
        self.item_settings_widgets['range_click'].setText(lm("item_setting_range_click"))
        self.item_settings_widgets['range_click'].setToolTip(lm("item_setting_range_click_tooltip"))
        self.item_settings_widgets['random_click'].setText(lm("item_setting_random_click"))
        self.item_settings_widgets['random_click'].setToolTip(lm("item_setting_random_click_tooltip"))
        self.item_settings_widgets['roi_enabled'].setText(lm("item_setting_roi_enable"))
        self.item_settings_widgets['roi_enabled'].setToolTip(lm("item_setting_roi_enable_tooltip"))
        self.item_settings_widgets['roi_mode_fixed'].setText(lm("item_setting_roi_mode_fixed"))
        self.item_settings_widgets['roi_mode_fixed'].setToolTip(lm("item_setting_roi_mode_fixed_tooltip"))
        self.item_settings_widgets['roi_mode_variable'].setText(lm("item_setting_roi_mode_variable"))
        self.item_settings_widgets['roi_mode_variable'].setToolTip(lm("item_setting_roi_mode_variable_tooltip"))
        self.item_settings_widgets['set_roi_variable_button'].setText(lm("item_setting_roi_button"))
        self.item_settings_widgets['set_roi_variable_button'].setToolTip(lm("item_setting_roi_button_tooltip"))

        if self.core_engine:
            current_status = self.status_label.text()
            if current_status == lm("status_label_idle") or current_status == lm("status_label_idle_error"):
                self.set_status("idle", "green")
            elif current_status == lm("status_label_monitoring"):
                self.set_status("monitoring", "blue")
        else: 
            self.set_status("idle", "green")
            
        current_scale = 0.0
        if self.core_engine and self.core_engine.current_window_scale is not None:
            current_scale = self.core_engine.current_window_scale
        self.on_window_scale_calculated(current_scale)
        self._update_capture_button_state()
        
    def on_cache_build_finished(self, success: bool):
        lm = self.locale_manager.tr
        
        if success:
            self.update_image_tree()
            self.set_tree_enabled(True)
        else:
            self.set_tree_enabled(False)
            QMessageBox.critical(self, 
                                 lm("error_title_cache_build_failed"), 
                                 lm("error_message_cache_build_failed"))
        
        self.is_processing_tree_change = False

    def on_app_context_changed(self, app_name: str):
        lm = self.locale_manager.tr
        if self.left_panel:
            if app_name:
                self.left_panel.list_title_label.setText(app_name)
            else:
                self.left_panel.list_title_label.setText(lm("list_title")) 
        self.update_image_tree()
        if self.core_engine and self.core_engine.thread_pool:
             self.set_tree_enabled(False)
             self.core_engine.thread_pool.submit(self.core_engine._build_template_cache).add_done_callback(self.core_engine._on_cache_build_done)

    def is_dark_mode(self):
        palette = self.palette()
        window_color = palette.color(QPalette.ColorRole.Window)
        text_color = palette.color(QPalette.ColorRole.WindowText)
        return window_color.lightness() < text_color.lightness()

    # --- 委譲メソッド (Compatibility Wrappers) ---
    def set_tree_enabled(self, enabled: bool):
        if self.left_panel: self.left_panel.image_tree.setEnabled(enabled)

    def update_image_tree(self):
        if self.left_panel: self.left_panel.update_image_tree()

    def get_selected_item_path(self):
        if self.left_panel: return self.left_panel.get_selected_item_path()
        return None, None

    def switch_to_preview_tab(self):
        if self.preview_tabs and self.main_preview_widget: 
            self.preview_tabs.setCurrentWidget(self.main_preview_widget)

    def save_tree_order(self):
        if self.left_panel: return self.left_panel.save_tree_order()
        return {}

    # --- Item Settings Sync Logic ---
    def set_settings_from_data(self, settings_data):
        self.item_settings_widgets['threshold'].blockSignals(True)
        self.item_settings_widgets['interval_time'].blockSignals(True)
        self.item_settings_widgets['backup_time'].blockSignals(True)
        self.item_settings_widgets['debounce_time'].blockSignals(True)

        try:
            self.item_settings_widgets['threshold'].setValue(settings_data.get('threshold', 0.8) if settings_data else 0.8)
            self.item_settings_widgets['interval_time'].setValue(settings_data.get('interval_time', 1.5) if settings_data else 1.5)
            self.item_settings_widgets['backup_time'].setValue(settings_data.get('backup_time', 300.0) if settings_data else 300.0)
            self.item_settings_widgets['debounce_time'].setValue(settings_data.get('debounce_time', 0.0) if settings_data else 0.0)
        finally:
            self.item_settings_widgets['threshold'].blockSignals(False)
            self.item_settings_widgets['interval_time'].blockSignals(False)
            self.item_settings_widgets['backup_time'].blockSignals(False)
            self.item_settings_widgets['debounce_time'].blockSignals(False)
  
    def on_app_settings_changed(self):
        """
        CoreEngine (SelectionHandler) からの呼び出しを
        AppSettingsPanel に転送します。
        """
        if self.app_settings_panel:
            self.app_settings_panel.on_app_settings_changed()
    
    def toggle_monitoring(self):
        if self.monitor_button.text() == self.locale_manager.tr("monitor_button_start"): self.startMonitoringRequested.emit()
        else: self.stopMonitoringRequested.emit()

    def set_status(self, text_key, color="green"):
        lm = self.locale_manager.tr
        display_text = ""
        style_color = color
        is_idle = False 

        if text_key == "monitoring":
            self.monitor_button.setText(lm("monitor_button_stop"))
            display_text = lm("status_label_monitoring")
            style_color = "blue"
        elif text_key == "idle":
            self.monitor_button.setText(lm("monitor_button_start"))
            display_text = lm("status_label_idle")
            style_color = "green"
            if self.app_settings_panel:
                self.app_settings_panel.current_best_scale_label.setText(lm("auto_scale_best_scale_default"))
                self.app_settings_panel.current_best_scale_label.setStyleSheet("color: gray;")
            is_idle = True 
        elif text_key == "unstable":
            display_text = lm("status_label_unstable")
            style_color = "orange"
        elif text_key == "idle_error":
            self.monitor_button.setText(lm("monitor_button_start"))
            display_text = lm("status_label_idle_error")
            style_color = "red"
            is_idle = True 
        else:
            display_text = text_key
            
        self.status_label.setText(display_text)
        self.status_label.setStyleSheet(f"font-weight: bold; color: {style_color};")
        
        if self.floating_window:
            self.floating_window.update_status(display_text, style_color)
            if is_idle and hasattr(self.floating_window, 'reset_performance_stats'):
                self.floating_window.reset_performance_stats()

        self._update_capture_button_state()

    def on_window_scale_calculated(self, scale: float):
        lm = self.locale_manager.tr
        if not self.app_settings_panel: return
        
        label = self.app_settings_panel.current_best_scale_label
        if scale > 0: 
            color = "white" if self.is_dark_mode() else "purple"
            label.setText(lm("auto_scale_window_scale_found", f"{scale:.3f}"))
            label.setStyleSheet(f"color: {color};")
        else: 
            label.setText(lm("auto_scale_best_scale_default"))
            label.setStyleSheet("color: gray;")
            
        self._update_capture_button_state(scale)

    def _update_capture_button_state(self, current_scale=None):
        if not self.core_engine: return
        
        is_rec_area_set = self.core_engine.recognition_area is not None
        
        if current_scale is None: 
            current_scale = self.core_engine.current_window_scale
        
        is_disabled_by_scale = (current_scale is not None and 
                                current_scale != 0.0 and 
                                not (0.995 <= current_scale <= 1.005))
                                
        enable_capture = is_rec_area_set and not is_disabled_by_scale
        
        tooltip = ""
        if not is_rec_area_set:
            tooltip = self.locale_manager.tr("warn_capture_disabled_no_area")
        elif is_disabled_by_scale:
            tooltip = self.locale_manager.tr("warn_capture_disabled_scale")
            
        if hasattr(self, 'main_capture_button'): 
            self.main_capture_button.setEnabled(enable_capture)
            self.main_capture_button.setToolTip(tooltip)
            
        if self.floating_window and hasattr(self.floating_window, 'capture_button'): 
            self.floating_window.capture_button.setEnabled(enable_capture)
            self.floating_window.capture_button.setToolTip(tooltip)

    def prompt_to_save_base_size(self, window_title: str) -> bool:
        lm = self.locale_manager.tr; reply = QMessageBox.question(self, lm("base_size_prompt_title"), lm("base_size_prompt_message", window_title), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes); return reply == QMessageBox.StandardButton.Yes

    def show_prompt_to_save_base_size(self, window_title: str):
        save_as_base = self.prompt_to_save_base_size(window_title);
        if self.core_engine: self.core_engine.process_base_size_prompt_response(save_as_base)

    def show_prompt_to_apply_scale(self, scale: float):
        lm = self.locale_manager.tr; reply = QMessageBox.question(self, lm("apply_scale_prompt_title"), lm("apply_scale_prompt_message", f"{scale:.3f}"), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes); apply_scale = (reply == QMessageBox.StandardButton.Yes);
        if self.core_engine: self.core_engine.process_apply_scale_prompt_response(apply_scale)

    def update_image_preview(self, cv_image: np.ndarray, settings_data: dict = None):
        self.set_settings_from_data(settings_data)

        image_or_splash_to_pass = cv_image
        is_folder_or_no_data = (settings_data is None and (cv_image is None or cv_image.size == 0))

        if is_folder_or_no_data:
            if self.splash_pixmap:
                image_or_splash_to_pass = self.splash_pixmap
        
        self.preview_mode_manager.update_preview(image_or_splash_to_pass, settings_data)

        if is_folder_or_no_data:
            self.preview_mode_manager.sync_from_external(is_folder_or_no_data)

        self.item_settings_group.setEnabled(not is_folder_or_no_data)
   
    def on_selection_process_started(self):
        if self.is_minimal_mode and self.floating_window: 
            self.floating_window.hide()
        elif not self.is_minimal_mode:
            self.hide()

    @Slot()
    @Slot()
    def on_selection_process_finished(self):
        if self.is_minimal_mode:
            if self.floating_window: 
                self.floating_window.show()
            if self.isVisible() and not self.isMinimized():
                self.showMinimized()
        else:
            self.showNormal()
            self.raise_()
            self.activateWindow()
    
    def _get_filename_from_user(self):
        lm = self.locale_manager.tr
        return QInputDialog.getText(self, lm("dialog_filename_prompt_title"), lm("dialog_filename_prompt_text"))
    
    @Slot()
    def on_capture_failed(self):
        lm = self.locale_manager.tr
        QMessageBox.warning(self, lm("warn_title_capture_failed"), lm("warn_message_capture_failed"))
    
    @Slot(np.ndarray)
    def on_captured_image_ready_for_preview(self, captured_image):
        self.pending_captured_image = captured_image
        self.showNormal()
        self.raise_()
        self.activateWindow()
        
        self.switch_to_preview_tab()
        self.update_image_preview(captured_image, settings_data=None)
        QApplication.processEvents()
        QTimer.singleShot(100, self._prompt_for_save_filename)
    
    def _prompt_for_save_filename(self):
        if self.pending_captured_image is None:
            if self.core_engine:
                self.core_engine.selectionProcessFinished.emit()
            return

        captured_image = self.pending_captured_image
        self.pending_captured_image = None
        
        try:
            file_name, ok = self._get_filename_from_user()
            if ok and file_name:
                self.set_tree_enabled(False)
                self.saveCapturedImageRequested.emit(file_name, captured_image)
            else:
                if self.core_engine:
                    self.core_engine.selectionProcessFinished.emit()
                    
        except Exception as e:
            QMessageBox.critical(self, self.locale_manager.tr("error_title_capture_save_failed"), self.locale_manager.tr("error_message_capture_save_failed", str(e)))
            if self.core_engine:
                self.core_engine.selectionProcessFinished.emit()
