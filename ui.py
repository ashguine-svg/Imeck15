# ui.py

import sys
import os
import subprocess
import cv2
import numpy as np
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QPushButton, QVBoxLayout, QWidget, QLabel,
    QFrame, QHBoxLayout, QGroupBox, QSpinBox, QDoubleSpinBox, QCheckBox,
    QGridLayout, QSizePolicy, QSpacerItem, QToolButton, QFileDialog, QLineEdit,
    QTreeWidget, QTreeWidgetItem, QMenu, QTabWidget, QTextEdit, QDialog, QMessageBox,
    QComboBox, QDialogButtonBox, QRadioButton, QButtonGroup, QScrollArea, QAbstractItemView,
    QStyle, QToolTip, QInputDialog
)
from PySide6.QtGui import (
    QIcon, QPixmap, QImage, QPainter, QColor, QBrush, QFont, QPalette,
    QCursor 
)
from PySide6.QtCore import (
    Qt, QSize, Signal, QTimer, QObject, QRect, QPoint, QEvent, Slot
)

import qtawesome as qta

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
        
        self.left_panel = None
        self.app_settings_panel = None

        self.item_settings_widgets = {}
        self.app_settings_widgets = {} 
        self.auto_scale_widgets = {}   
        self.available_langs = {}      
        self.image_tree = None         

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

    # ★★★ 修正: QtAwesomeアイコンをQImageに焼き付けて静的化する ★★★
    def _safe_icon(self, icon_name, color=None, size=None):
        try:
            # 1. qtawesomeでアイコンオブジェクト生成
            if color:
                base_icon = qta.icon(icon_name, color=color)
            else:
                base_icon = qta.icon(icon_name)
            
            # 2. メモリ上のQImageに描画 (Type 3 - 安全)
            # サイズ指定がなければデフォルト24x24
            s = size if size else QSize(24, 24)
            image = QImage(s, QImage.Format_ARGB32_Premultiplied)
            image.fill(Qt.transparent)
            
            painter = QPainter()
            if painter.begin(image):
                try:
                    # アイコンをQImageに描画
                    base_icon.paint(painter, QRect(0, 0, s.width(), s.height()))
                finally:
                    painter.end()
            
            # 3. 焼き付けた画像からQIconを生成して返す
            # これにより、以降の再描画でqtawesome内部のQPainter(QPixmap)が呼ばれるのを防ぐ
            return QIcon(QPixmap.fromImage(image))

        except Exception as e:
            print(f"[WARN] QtAwesome rendering failed for {icon_name}: {e}")
            return QIcon()
            
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
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # 1. ヘッダーエリア
        self._setup_header(self.main_layout)

        # コンテンツエリア
        content_frame = QFrame()
        self.content_layout = QHBoxLayout(content_frame)
        self.content_layout.setContentsMargins(10, 10, 10, 10)
        self.content_layout.setSpacing(15)
        
        # 2. 左パネル
        self.left_panel = LeftPanel(self, self.content_layout, self.config_manager, self.logger, self.locale_manager)

        # 3. 右パネル
        self._setup_right_panel(self.content_layout)

        self.main_layout.addWidget(content_frame)
        
        # --- タブ設定 ---
        self._setup_tab_preview()
        self._setup_tab_rec_area()
        
        self.app_settings_panel = AppSettingsPanel(self, self.config_manager, self.app_config, self.locale_manager)
        self.app_settings_panel.setup_ui(self.preview_tabs)
        
        self._setup_tab_usage()
        self._setup_tab_log()

    def _setup_header(self, parent_layout):
        header_frame = QFrame()
        header_frame.setProperty('class', 'header_frame')
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(15, 8, 15, 8)
        header_layout.setSpacing(12)
        
        def create_header_btn(icon_name, text_key, checkable=False, primary=False):
            btn = QPushButton()
            color = 'white' if primary else '#5f6368' 
            # 修正: _safe_icon を使用 (サイズ指定)
            btn.setIcon(self._safe_icon(icon_name, color=color, size=QSize(18, 18)))
            btn.setIconSize(QSize(18, 18))
            
            if primary:
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: #009688; 
                        color: white; 
                        font-weight: bold;
                        border-radius: 4px;
                        padding: 6px 15px;
                        border: none;
                    }
                    QPushButton:hover { background-color: #26a69a; }
                """)
            else:
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: #ffffff; 
                        color: #333333;
                        border: 1px solid #cfd8dc;
                        border-radius: 4px;
                        padding: 6px 15px;
                        font-weight: bold;
                    }
                    QPushButton:hover { background-color: #f1f3f4; }
                """)
            
            btn.setCursor(Qt.PointingHandCursor)
            if checkable:
                btn.setCheckable(True)
            return btn

        self.monitor_button = create_header_btn('fa5s.play', "monitor_button_start", primary=True)
        header_layout.addWidget(self.monitor_button)
        
        line = QFrame()
        line.setFrameShape(QFrame.VLine)
        line.setFrameShadow(QFrame.Sunken)
        line.setStyleSheet("color: #e0e0e0;")
        header_layout.addWidget(line)

        self.header_rec_area_button = create_header_btn('fa5s.crop', "recognition_area_button")
        self.header_rec_area_button.clicked.connect(self.setRecAreaDialog)
        header_layout.addWidget(self.header_rec_area_button)
        
        self.toggle_minimal_ui_button = create_header_btn('fa5s.window-minimize', "minimal_ui_button")
        header_layout.addWidget(self.toggle_minimal_ui_button)
        
        self.capture_image_button = create_header_btn('fa5s.camera', "capture_image_button")
        self.capture_image_button.clicked.connect(self.captureImageRequested.emit)
        header_layout.addWidget(self.capture_image_button)

        header_layout.addStretch()
        
        self.open_image_folder_button = create_header_btn('fa5s.folder-open', "open_image_folder_button")
        header_layout.addWidget(self.open_image_folder_button)
        
        self.status_label = QLabel()
        font = self.status_label.font()
        font.setBold(True)
        font.setPointSize(11)
        self.status_label.setFont(font)
        self.status_label.setStyleSheet("color: #4caf50; margin-left: 10px;") 
        header_layout.addWidget(self.status_label)
        
        parent_layout.addWidget(header_frame)

    def _setup_right_panel(self, parent_layout):
        right_frame = QFrame()
        right_layout = QVBoxLayout(right_frame)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)
        
        self.preview_tabs = QTabWidget()
        self.preview_tabs.setStyleSheet("""
            QTabWidget::pane { 
                border: 1px solid #cfd8dc; 
                top: -1px; 
            }
            QTabBar::tab { 
                border: 1px solid #cfd8dc; 
                border-bottom: none;
                padding: 6px 12px; 
                margin-right: 2px; 
                background-color: #f5f5f5;
                color: #616161;
            }
            QTabBar::tab:selected { 
                background-color: #ffffff; 
                color: #37474f; 
                font-weight: bold;
                border-bottom: 2px solid #37474f;
            }
            QTabBar::tab:hover {
                background-color: #e0f2f1;
            }
        """)
        
        right_layout.addWidget(self.preview_tabs, 3) 
        
        self._setup_item_settings_group(right_layout)
        parent_layout.addWidget(right_frame, 3) 

    def _setup_tab_preview(self):
        self.main_preview_widget = QWidget()
        layout = QVBoxLayout(self.main_preview_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.preview_label = InteractivePreviewLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.preview_label.setStyleSheet("background-color: #263238;")
        
        layout.addWidget(self.preview_label)
        self.preview_tabs.addTab(self.main_preview_widget, "")

    def _setup_tab_rec_area(self):
        rec_area_widget = QWidget()
        layout = QVBoxLayout(rec_area_widget)
        
        buttons_layout = QHBoxLayout()
        self.set_rec_area_button_main_ui = QPushButton()
        # 修正: _safe_icon を使用
        self.set_rec_area_button_main_ui.setIcon(self._safe_icon('fa5s.crop', color='#546e7a'))
        self.set_rec_area_button_main_ui.setStyleSheet("""
            QPushButton {
                background-color: #ffffff;
                color: #37474f;
                border: 1px solid #cfd8dc;
                border-radius: 4px;
                padding: 6px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #f5f5f5; border-color: #b0bec5; }
        """)
        
        self.clear_rec_area_button_main_ui = QPushButton()
        # 修正: _safe_icon を使用
        self.clear_rec_area_button_main_ui.setIcon(self._safe_icon('fa5s.times', color='#546e7a'))
        self.clear_rec_area_button_main_ui.setStyleSheet("""
            QPushButton {
                background-color: #ffffff;
                color: #37474f;
                border: 1px solid #cfd8dc;
                border-radius: 4px;
                padding: 6px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #f5f5f5; border-color: #b0bec5; }
        """)
        
        buttons_layout.addWidget(self.set_rec_area_button_main_ui)
        buttons_layout.addWidget(self.clear_rec_area_button_main_ui)
        buttons_layout.addStretch()
        layout.addLayout(buttons_layout)
        
        self.rec_area_preview_label = ScaledPixmapLabel()
        self.rec_area_preview_label.setAlignment(Qt.AlignCenter)
        self.rec_area_preview_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.rec_area_preview_label.setStyleSheet("background-color: black; border: 1px solid #555;")
        layout.addWidget(self.rec_area_preview_label)
        
        self.preview_tabs.addTab(rec_area_widget, "")

    def _setup_tab_log(self):
        log_widget = QWidget()
        layout = QVBoxLayout(log_widget)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet("""
            QTextEdit {
                background-color: #263238; 
                color: #e0e0e0;
                font-family: Consolas, monospace; 
                font-size: 11px;
                border: none;
            }
        """)
        layout.addWidget(self.log_text)
        self.preview_tabs.addTab(log_widget, "")

    def _setup_tab_usage(self):
        usage_widget = QWidget()
        layout = QVBoxLayout(usage_widget)
        self.usage_text = QTextEdit()
        self.usage_text.setReadOnly(True)
        layout.addWidget(self.usage_text)
        self.preview_tabs.addTab(usage_widget, "")

    def _setup_item_settings_group(self, parent_layout):
        self.item_settings_group = QGroupBox()
        self.item_settings_group.setStyleSheet("""
            QGroupBox {
                border: 1px solid #cfd8dc;
                border-radius: 8px;
                margin-top: 1.5em;
                padding-top: 15px;
                background-color: #fafafa;
                color: #37474f; 
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                font-weight: bold;
                color: #37474f;
            }
            QLabel, QCheckBox, QRadioButton {
                color: #212121;
            }
        """)
        
        layout = QGridLayout(self.item_settings_group)
        layout.setVerticalSpacing(12)
        layout.setHorizontalSpacing(15)
        
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
        backup_layout = QHBoxLayout()
        backup_layout.setContentsMargins(0,0,0,0)
        backup_layout.addWidget(self.item_settings_widgets['backup_click'])
        backup_layout.addSpacing(10)
        self.item_settings_widgets['backup_time'] = QDoubleSpinBox()
        self.item_settings_widgets['backup_time'].setRange(1.0, 600.0); self.item_settings_widgets['backup_time'].setSingleStep(1.0); self.item_settings_widgets['backup_time'].setValue(300.0)
        self.item_settings_widgets['backup_time'].setFixedWidth(90)
        backup_layout.addWidget(self.item_settings_widgets['backup_time'])
        backup_layout.addStretch()
        
        layout.addLayout(backup_layout, 1, 0, 1, 2)
        
        self.item_debounce_label = QLabel()
        layout.addWidget(self.item_debounce_label, 1, 2)
        self.item_settings_widgets['debounce_time'] = QDoubleSpinBox()
        self.item_settings_widgets['debounce_time'].setRange(0.0, 10.0); self.item_settings_widgets['debounce_time'].setSingleStep(0.1); self.item_settings_widgets['debounce_time'].setValue(0.0)
        layout.addWidget(self.item_settings_widgets['debounce_time'], 1, 3)
        
        click_type_layout = QHBoxLayout()
        click_type_layout.setSpacing(10)
        
        self.item_settings_widgets['point_click'] = QCheckBox()
        click_type_layout.addWidget(self.item_settings_widgets['point_click'])
        
        range_group_frame = QFrame()
        range_group_frame.setStyleSheet("QFrame { border: 1px solid #cfd8dc; border-radius: 4px; background-color: #ffffff; }")
        range_group_layout = QHBoxLayout(range_group_frame)
        range_group_layout.setContentsMargins(8, 4, 8, 4)
        
        self.item_settings_widgets['range_click'] = QCheckBox()
        self.item_settings_widgets['random_click'] = QCheckBox()
        range_group_layout.addWidget(self.item_settings_widgets['range_click'])
        range_group_layout.addWidget(self.item_settings_widgets['random_click'])
        
        click_type_layout.addWidget(range_group_frame)
        click_type_layout.addStretch()
        
        layout.addLayout(click_type_layout, 2, 0, 1, 4)
        
        separator = QFrame(); separator.setFrameShape(QFrame.Shape.HLine); separator.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(separator, 3, 0, 1, 4)
        
        roi_layout = QHBoxLayout()
        roi_layout.setSpacing(10)
        
        self.item_settings_widgets['roi_enabled'] = QCheckBox()
        roi_layout.addWidget(self.item_settings_widgets['roi_enabled'])
        
        self.item_settings_widgets['roi_mode_fixed'] = QRadioButton()
        self.roi_mode_group = QButtonGroup(self)
        self.roi_mode_group.addButton(self.item_settings_widgets['roi_mode_fixed'])
        roi_layout.addWidget(self.item_settings_widgets['roi_mode_fixed'])
        
        var_group_frame = QFrame()
        var_group_frame.setStyleSheet("QFrame { border: 1px solid #cfd8dc; border-radius: 4px; background-color: #ffffff; }")
        var_group_layout = QHBoxLayout(var_group_frame)
        var_group_layout.setContentsMargins(8, 4, 8, 4)
        
        self.item_settings_widgets['roi_mode_variable'] = QRadioButton()
        self.roi_mode_group.addButton(self.item_settings_widgets['roi_mode_variable'])
        
        var_group_layout.addWidget(self.item_settings_widgets['roi_mode_variable'])
        var_group_layout.addSpacing(20) 
        
        self.item_settings_widgets['set_roi_variable_button'] = QPushButton()
        self.item_settings_widgets['set_roi_variable_button'].setCheckable(True)
        # 修正: _safe_icon を使用
        self.item_settings_widgets['set_roi_variable_button'].setIcon(self._safe_icon('fa5s.vector-square', color='#37474f'))
        self.item_settings_widgets['set_roi_variable_button'].setFixedWidth(180) 

        self.item_settings_widgets['set_roi_variable_button'].setStyleSheet("""
            QPushButton {
                background-color: #ffffff;
                border: 1px solid #cfd8dc;
                border-radius: 4px;
                font-weight: bold; 
                color: #37474f;
                padding: 4px 8px;
            }
            QPushButton:hover {
                background-color: #eceff1;
                border-color: #b0bec5;
            }
            QPushButton:checked {
                background-color: #cfd8dc;
                border-color: #90a4ae;
            }
        """)
        
        var_group_layout.addWidget(self.item_settings_widgets['set_roi_variable_button'])
        
        roi_layout.addWidget(var_group_frame)
        roi_layout.addStretch()
        
        layout.addLayout(roi_layout, 4, 0, 1, 4)
        
        parent_layout.addWidget(self.item_settings_group)

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
            if self.left_panel: self.left_panel.core_engine = self.core_engine
            
            self.clear_rec_area_button_main_ui.clicked.connect(self.core_engine.clear_recognition_area)
            self.core_engine.windowScaleCalculated.connect(self._update_capture_button_state)

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
        
        # --- 表示位置の計算ロジック (上下判定・UIモード別処理) ---
        cursor_pos = QCursor.pos()
        screen = QApplication.screenAt(cursor_pos)
        if not screen:
            screen = QApplication.primaryScreen()
            
        screen_rect = screen.geometry()
        screen_center_y = screen_rect.center().y()
        
        # ダイアログの高さ (dialogs.pyで setFixedSize されている値)
        dialog_height = dialog.height()
        
        final_pos = cursor_pos

        # 2. 押された位置の判定はメインUIと最小UIで別処理にする
        if self.is_minimal_mode:
            # --- 最小UIモードの場合 ---
            # 1. 画面中央より上なら左上起点、下なら左下起点
            if cursor_pos.y() < screen_center_y:
                # 上半分: そのまま (左上起点)
                final_pos = cursor_pos
            else:
                # 下半分: Y座標を高さ分マイナス (左下起点)
                final_pos = QPoint(cursor_pos.x(), cursor_pos.y() - dialog_height)
        else:
            # --- メインUIモードの場合 ---
            # 1. 画面中央より上なら左上起点、下なら左下起点
            if cursor_pos.y() < screen_center_y:
                # 上半分: そのまま (左上起点)
                final_pos = cursor_pos
            else:
                # 下半分: Y座標を高さ分マイナス (左下起点)
                final_pos = QPoint(cursor_pos.x(), cursor_pos.y() - dialog_height)
        
        dialog.move(final_pos)
        dialog.exec()

    def adjust_initial_size(self):
        self.setMinimumWidth(0); self.resize(1000, 680)

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

            current_status_text = self.status_label.text()
            current_status_color = "green"
            if current_status_text == lm("status_label_monitoring"): current_status_color = "blue"
            elif current_status_text == lm("status_label_unstable"): current_status_color = "orange"
            elif current_status_text == lm("status_label_idle_error"): current_status_color = "red"
            
            self.floating_window.update_status(current_status_text, current_status_color); 
            self.floating_window.show(); 
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
            self._update_capture_button_state()

    def retranslate_ui(self):
        lm = self.locale_manager.tr

        self.setWindowTitle(lm("window_title"))
        self.header_rec_area_button.setText(f" {lm('recognition_area_button')}")
        self.toggle_minimal_ui_button.setText(f" {lm('minimal_ui_button')}")
        self.open_image_folder_button.setText(f" {lm('open_image_folder_button')}")
        self.open_image_folder_button.setToolTip(lm("open_image_folder_tooltip"))
        self.monitor_button.setToolTip(lm("monitor_button_tooltip"))

        if not self.core_engine or not self.core_engine.is_monitoring:
            self.status_label.setText(lm("status_label_idle"))
        else:
             self.status_label.setText(lm("status_label_monitoring"))

        self.capture_image_button.setText(f" {lm('capture_image_button')}")

        if self.left_panel: self.left_panel.retranslate_ui()
        if self.app_settings_panel: self.app_settings_panel.retranslate_ui()

        self.preview_tabs.setTabText(self.preview_tabs.indexOf(self.main_preview_widget), lm("tab_preview"))
        
        rec_area_tab_index = self.preview_tabs.indexOf(self.rec_area_preview_label.parentWidget())
        if rec_area_tab_index != -1: self.preview_tabs.setTabText(rec_area_tab_index, lm("tab_rec_area"))
        self.set_rec_area_button_main_ui.setText(f" {lm('recognition_area_button')}")
        self.clear_rec_area_button_main_ui.setText(f" {lm('rec_area_clear_button')}")
        self.rec_area_preview_label.setText(lm("rec_area_preview_text"))

        log_tab_index = self.preview_tabs.indexOf(self.log_text.parentWidget())
        if log_tab_index != -1: self.preview_tabs.setTabText(log_tab_index, lm("tab_log"))

        if self.app_settings_panel:
            if self.app_settings_panel.tab_general_scroll:
                idx_gen = self.preview_tabs.indexOf(self.app_settings_panel.tab_general_scroll)
                if idx_gen != -1: self.preview_tabs.setTabText(idx_gen, lm("tab_app_settings"))
            
            if self.app_settings_panel.tab_auto_scale_scroll:
                idx_as = self.preview_tabs.indexOf(self.app_settings_panel.tab_auto_scale_scroll)
                if idx_as != -1: self.preview_tabs.setTabText(idx_as, lm("tab_auto_scale"))

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
        self.item_settings_widgets['set_roi_variable_button'].setText(f" {lm('item_setting_roi_button')}")
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
        if self.app_settings_panel:
            self.app_settings_panel.on_app_settings_changed()
    
    def toggle_monitoring(self):
        lm = self.locale_manager.tr
        if not self.core_engine.is_monitoring:
            self.startMonitoringRequested.emit()
        else:
            self.stopMonitoringRequested.emit()

    def set_status(self, text_key, color="green"):
        lm = self.locale_manager.tr
        display_text = ""
        style_color = color
        is_idle = False 

        def set_monitor_btn(icon, text, primary=False):
            # 修正: _safe_icon を使用
            self.monitor_button.setIcon(self._safe_icon(icon, color='white' if primary else '#5f6368'))
            self.monitor_button.setText(f" {text}")
            if primary:
                self.monitor_button.setStyleSheet("""
                    QPushButton { background-color: #e53935; color: white; font-weight: bold; border-radius: 4px; border: none; }
                    QPushButton:hover { background-color: #ef5350; }
                """)
            else:
                self.monitor_button.setStyleSheet("""
                    QPushButton { background-color: #009688; color: white; font-weight: bold; border-radius: 4px; border: none; }
                    QPushButton:hover { background-color: #26a69a; }
                """)

        if text_key == "monitoring":
            set_monitor_btn('fa5s.stop', lm("monitor_button_stop"), primary=True)
            display_text = lm("status_label_monitoring")
            style_color = "#2196f3" 
        elif text_key == "idle":
            set_monitor_btn('fa5s.play', lm("monitor_button_start"), primary=False)
            display_text = lm("status_label_idle")
            style_color = "#4caf50" 
            if self.app_settings_panel:
                self.app_settings_panel.current_best_scale_label.setText(lm("auto_scale_best_scale_default"))
                self.app_settings_panel.current_best_scale_label.setStyleSheet("color: gray;")
            is_idle = True 
        elif text_key == "unstable":
            display_text = lm("status_label_unstable")
            style_color = "#ff9800" 
        elif text_key == "idle_error":
            set_monitor_btn('fa5s.play', lm("monitor_button_start"), primary=False)
            display_text = lm("status_label_idle_error")
            style_color = "#f44336" 
            is_idle = True 
        else:
            display_text = text_key
            
        self.status_label.setText(display_text)
        self.status_label.setStyleSheet(f"font-weight: bold; color: {style_color}; font-size: 14px;")
        
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
            # ★ 修正: 認識範囲未設定時のツールチップキーを変更
            tooltip = self.locale_manager.tr("warn_capture_disabled_scale")
        elif is_disabled_by_scale:
            tooltip = self.locale_manager.tr("warn_capture_disabled_scale")
            
        if hasattr(self, 'main_capture_button'): 
            self.main_capture_button.setEnabled(enable_capture)
            self.main_capture_button.setToolTip(tooltip)
            
            # --- グレーアウト処理 (修正: 通常色をグレーに) ---
            if enable_capture:
                self.main_capture_button.setStyleSheet("""
                    QPushButton {
                        background-color: #ffffff; 
                        color: #333333;
                        border: 1px solid #dadce0;
                        border-radius: 4px;
                        padding: 6px 15px;
                        font-weight: bold;
                    }
                    QPushButton:hover { background-color: #f1f3f4; }
                """)
                # 修正: _safe_icon を使用
                self.main_capture_button.setIcon(self._safe_icon('fa5s.camera', color='#5f6368'))
            else:
                self.main_capture_button.setStyleSheet("""
                    QPushButton {
                        background-color: #f0f0f0; 
                        color: #bdbdbd;
                        border: 1px solid #e0e0e0;
                        border-radius: 4px;
                        padding: 6px 15px;
                        font-weight: bold;
                    }
                """)
                # 修正: _safe_icon を使用
                self.main_capture_button.setIcon(self._safe_icon('fa5s.camera', color='#bdbdbd'))
            
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
