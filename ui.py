# ui.py (ImportError および QCursor NameError 修正版)

import sys
import json
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QPushButton, QVBoxLayout, QWidget, QLabel,
    QFrame, QHBoxLayout, QGroupBox, QSpinBox, QDoubleSpinBox, QCheckBox,
    QGridLayout, QSizePolicy, QSpacerItem, QToolButton, QFileDialog, QLineEdit,
    QTreeWidget, QTreeWidgetItem, QMenu, QTabWidget, QTextEdit, QDialog, QMessageBox,
    QComboBox, QDialogButtonBox, QRadioButton, QButtonGroup, QScrollArea, QAbstractItemView,
    QProxyStyle, QStyle, QStyleOptionViewItem, QToolTip,
    QInputDialog
)
from PySide6.QtGui import (
    QIcon, QPixmap, QImage, QPainter, QColor, QBrush, QFont, QPalette,
    QCursor # ★★★ 修正: QCursor をインポート ★★★
)
from PySide6.QtCore import (
    Qt, QSize, QThread, Signal, QTimer, QObject, QRect, QPoint, QRectF, QPointF, QEvent
)

import os
import subprocess
import cv2
import numpy as np
from pathlib import Path
from capture import DXCAM_AVAILABLE
from floating_window import FloatingWindow
from dialogs import RecAreaSelectionDialog, FolderSettingsDialog
from custom_widgets import ScaledPixmapLabel, InteractivePreviewLabel
from preview_mode_manager import PreviewModeManager
from image_tree_widget import DraggableTreeWidget


try:
    OPENCL_AVAILABLE = cv2.ocl.haveOpenCL()
except:
    OPENCL_AVAILABLE = False

# --- UIManager ---
class UIManager(QMainWindow):
    startMonitoringRequested = Signal(); stopMonitoringRequested = Signal(); openPerformanceMonitorRequested = Signal()
    loadImagesRequested = Signal(list); setRecAreaMethodSelected = Signal(str); captureImageRequested = Signal()
    deleteItemsRequested = Signal(list)
    orderChanged = Signal()
    itemsMovedIntoFolder = Signal(list, str)
    folderSettingsChanged = Signal()
    imageSettingsChanged = Signal(dict); createFolderRequested = Signal(); moveItemIntoFolderRequested = Signal()
    moveItemOutOfFolderRequested = Signal()
    appConfigChanged = Signal()
    
    renameItemRequested = Signal(str, str)
    
    def create_colored_icon(self, color):
        """Creates a small QIcon with a specified color indicator."""
        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.transparent)
        if color != Qt.transparent:
            painter = QPainter(pixmap)
            painter.setBrush(QBrush(color))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(4, 4, 8, 8) # Example: Draw a circle
            painter.end()
        return QIcon(pixmap)

    def __init__(self, core_engine, capture_manager, config_manager, logger, locale_manager):
        super().__init__(parent=None)

        self.logger = logger
        self.locale_manager = locale_manager

        self.core_engine = core_engine
        self.capture_manager = capture_manager
        self.config_manager = config_manager

        self.item_settings_widgets = {}
        self.app_settings_widgets = {}
        self.auto_scale_widgets = {}
        self.available_langs = {}

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

        self.performance_monitor = None
        self.is_minimal_mode = False
        self.normal_ui_geometries = {}
        self.floating_window = None

        self.setup_ui()
        self.retranslate_ui()
        self.load_app_settings_to_ui()

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
        """Opens the base image directory in the system's file explorer."""
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
            
    def set_performance_monitor(self, monitor):
        self.performance_monitor = monitor

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        header_frame = QFrame()
        header_layout = QHBoxLayout(header_frame)
        self.monitor_button = QPushButton()
        self.monitor_button.setFixedSize(120, 30)
        header_layout.addWidget(self.monitor_button)
        self.perf_monitor_button = QPushButton()
        self.perf_monitor_button.setFixedSize(120, 30)
        header_layout.addWidget(self.perf_monitor_button)
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
        
        # (画像キャプチャボタンをヘッダーに移動)
        self.capture_image_button = QPushButton()
        self.capture_image_button.setFixedSize(120, 30)
        self.capture_image_button.clicked.connect(self.captureImageRequested.emit)
        header_layout.addWidget(self.capture_image_button)
        
        header_layout.addSpacerItem(QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        self.status_label = QLabel()
        self.status_label.setStyleSheet("font-size: 16px; font-weight: bold; color: green;")
        header_layout.addWidget(self.status_label)
        main_layout.addWidget(header_frame)

        content_frame = QFrame()
        content_layout = QHBoxLayout(content_frame)

        left_frame = QFrame()
        left_layout = QVBoxLayout(left_frame)
        self.list_title_label = QLabel()
        left_layout.addWidget(self.list_title_label)
        order_button_frame = QHBoxLayout()
        self.move_up_button = QPushButton()
        self.move_down_button = QPushButton()
        order_button_frame.addWidget(self.move_up_button)
        order_button_frame.addWidget(self.move_down_button)
        left_layout.addLayout(order_button_frame)
        
        self.image_tree = DraggableTreeWidget(self.config_manager)
        
        self.image_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.image_tree.setDragDropMode(QAbstractItemView.InternalMove)
        self.image_tree.setDragEnabled(True)
        self.image_tree.setAcceptDrops(True)
        self.image_tree.setDropIndicatorShown(False)
        self.image_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.image_tree.setStyleSheet("QTreeWidget { border: 1px solid darkgray; border-radius: 0px; }")
        self.image_tree.setHeaderHidden(True)
        left_layout.addWidget(self.image_tree)
        
        button_layout = QGridLayout()
        self.load_image_button = QPushButton()
        button_layout.addWidget(self.load_image_button, 0, 0)
        
        # (リネームボタンを追加)
        self.rename_button = QPushButton()
        button_layout.addWidget(self.rename_button, 0, 1)
        
        self.delete_item_button = QPushButton()
        button_layout.addWidget(self.delete_item_button, 1, 0)
        self.create_folder_button = QPushButton()
        button_layout.addWidget(self.create_folder_button, 1, 1)
        self.move_in_button = QPushButton()
        button_layout.addWidget(self.move_in_button, 2, 0)
        self.move_out_button = QPushButton()
        button_layout.addWidget(self.move_out_button, 2, 1)
        
        self.load_image_button.clicked.connect(self.load_images_dialog)
        # (capture_image_button.clicked.connect はヘッダーで接続済みの為、ここでは不要)
        self.delete_item_button.clicked.connect(self.on_delete_button_clicked)
        self.move_up_button.clicked.connect(self.move_item_up)
        self.move_down_button.clicked.connect(self.move_item_down)
        self.create_folder_button.clicked.connect(self.createFolderRequested.emit)
        self.move_in_button.clicked.connect(self.moveItemIntoFolderRequested.emit)
        self.move_out_button.clicked.connect(self.moveItemOutOfFolderRequested.emit)
        left_layout.addLayout(button_layout)
        content_layout.addWidget(left_frame, 1)

        right_frame = QFrame()
        right_layout = QVBoxLayout(right_frame)
        self.preview_tabs = QTabWidget()

        self.main_preview_widget = QWidget()
        main_preview_layout = QVBoxLayout(self.main_preview_widget)
        self.preview_label = InteractivePreviewLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        main_preview_layout.addWidget(self.preview_label)
        self.preview_tabs.addTab(self.main_preview_widget, "")

        rec_area_widget = QWidget()
        rec_area_layout = QVBoxLayout(rec_area_widget)
        rec_area_buttons_layout = QHBoxLayout()
        self.set_rec_area_button_main_ui = QPushButton()
        self.clear_rec_area_button_main_ui = QPushButton()
        rec_area_buttons_layout.addWidget(self.set_rec_area_button_main_ui)
        rec_area_buttons_layout.addWidget(self.clear_rec_area_button_main_ui)
        rec_area_layout.addLayout(rec_area_buttons_layout)
        self.rec_area_preview_label = ScaledPixmapLabel()
        self.rec_area_preview_label.setAlignment(Qt.AlignCenter)
        rec_area_layout.addWidget(self.rec_area_preview_label)
        self.preview_tabs.addTab(rec_area_widget, "")

        log_widget = QWidget()
        log_layout = QVBoxLayout(log_widget)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)
        self.preview_tabs.addTab(log_widget, "")

        self.auto_scale_group = QGroupBox()
        auto_scale_layout = QGridLayout(self.auto_scale_group)
        self.auto_scale_widgets['use_window_scale'] = QCheckBox()
        auto_scale_layout.addWidget(self.auto_scale_widgets['use_window_scale'], 0, 0, 1, 2)
        self.auto_scale_widgets['enabled'] = QCheckBox()
        auto_scale_layout.addWidget(self.auto_scale_widgets['enabled'], 1, 0, 1, 2)
        self.auto_scale_center_label = QLabel()
        auto_scale_layout.addWidget(self.auto_scale_center_label, 2, 0)
        self.auto_scale_widgets['center'] = QDoubleSpinBox(); self.auto_scale_widgets['center'].setRange(0.5, 2.0); self.auto_scale_widgets['center'].setSingleStep(0.1)
        auto_scale_layout.addWidget(self.auto_scale_widgets['center'], 2, 1)
        self.auto_scale_range_label = QLabel()
        auto_scale_layout.addWidget(self.auto_scale_range_label, 2, 2)
        self.auto_scale_widgets['range'] = QDoubleSpinBox(); self.auto_scale_widgets['range'].setRange(0.1, 0.5); self.auto_scale_widgets['range'].setSingleStep(0.05)
        auto_scale_layout.addWidget(self.auto_scale_widgets['range'], 2, 3)
        self.auto_scale_steps_label = QLabel()
        auto_scale_layout.addWidget(self.auto_scale_steps_label, 3, 0)
        self.auto_scale_widgets['steps'] = QSpinBox(); self.auto_scale_widgets['steps'].setRange(3, 11); self.auto_scale_widgets['steps'].setSingleStep(2)
        auto_scale_layout.addWidget(self.auto_scale_widgets['steps'], 3, 1)
        self.auto_scale_info_label = QLabel()
        auto_scale_layout.addWidget(self.auto_scale_info_label, 3, 2, 1, 2)
        scale_info_layout = QHBoxLayout()
        self.current_best_scale_label = QLabel()
        font = self.current_best_scale_label.font(); font.setBold(True)
        self.current_best_scale_label.setFont(font); self.current_best_scale_label.setStyleSheet("color: gray;")
        scale_info_layout.addWidget(self.current_best_scale_label)
        scale_info_layout.addSpacerItem(QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        auto_scale_layout.addLayout(scale_info_layout, 4, 0, 1, 4)
        self.as_desc_label = QLabel()
        self.as_desc_label.setWordWrap(True); self.as_desc_label.setStyleSheet("font-size: 11px; color: #555555;"); self.as_desc_label.setMinimumWidth(0)
        auto_scale_layout.addWidget(self.as_desc_label, 5, 0, 1, 4)
        self.auto_scale_group.setFlat(True)
        self.preview_tabs.addTab(self.auto_scale_group, "")

        app_settings_scroll_area = QScrollArea()
        app_settings_scroll_area.setWidgetResizable(True)
        app_settings_scroll_area.setStyleSheet("QScrollArea { border: 0; }")
        app_settings_widget = QWidget()
        app_settings_layout = QVBoxLayout(app_settings_widget)
        app_settings_layout.setSpacing(10)
        app_settings_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.app_settings_widgets['grayscale_matching'] = QCheckBox()
        app_settings_layout.addWidget(self.app_settings_widgets['grayscale_matching'])
        self.gs_desc_label = QLabel()
        self.gs_desc_label.setWordWrap(True); self.gs_desc_label.setStyleSheet("font-size: 11px; color: #555555; padding-left: 20px;")
        app_settings_layout.addWidget(self.gs_desc_label)
        self.app_settings_widgets['capture_method'] = QCheckBox()
        self.app_settings_widgets['capture_method'].setEnabled(DXCAM_AVAILABLE)
        app_settings_layout.addWidget(self.app_settings_widgets['capture_method'])
        self.dxcam_desc_label = QLabel()
        self.dxcam_desc_label.setWordWrap(True); self.dxcam_desc_label.setStyleSheet("font-size: 11px; color: #555555; padding-left: 20px;")
        app_settings_layout.addWidget(self.dxcam_desc_label)
        self.app_settings_widgets['eco_mode_enabled'] = QCheckBox()
        app_settings_layout.addWidget(self.app_settings_widgets['eco_mode_enabled'])
        self.eco_desc_label = QLabel()
        self.eco_desc_label.setWordWrap(True); self.eco_desc_label.setStyleSheet("font-size: 11px; color: #555555; padding-left: 20px;")
        app_settings_layout.addWidget(self.eco_desc_label)
        fs_layout = QHBoxLayout(); self.fs_label = QLabel()
        fs_layout.addWidget(self.fs_label)
        self.app_settings_widgets['frame_skip_rate'] = QSpinBox(); self.app_settings_widgets['frame_skip_rate'].setRange(1, 20)
        fs_layout.addWidget(self.app_settings_widgets['frame_skip_rate']); fs_layout.addStretch()
        app_settings_layout.addLayout(fs_layout)
        self.fs_desc_label = QLabel()
        self.fs_desc_label.setWordWrap(True); self.fs_desc_label.setStyleSheet("font-size: 11px; color: #555555; padding-left: 20px;")
        app_settings_layout.addWidget(self.fs_desc_label)
        self.app_settings_widgets['use_opencl'] = QCheckBox()
        self.app_settings_widgets['use_opencl'].setEnabled(OPENCL_AVAILABLE)
        app_settings_layout.addWidget(self.app_settings_widgets['use_opencl'])
        self.opencl_desc_label = QLabel()
        self.opencl_desc_label.setWordWrap(True); self.opencl_desc_label.setStyleSheet("font-size: 11px; color: #555555; padding-left: 20px;")
        app_settings_layout.addWidget(self.opencl_desc_label)
        self.stability_group = QGroupBox()
        stability_layout = QGridLayout(self.stability_group)
        self.app_settings_widgets['stability_check_enabled'] = QCheckBox()
        stability_layout.addWidget(self.app_settings_widgets['stability_check_enabled'], 0, 0)
        threshold_layout = QHBoxLayout(); self.stability_threshold_label = QLabel()
        threshold_layout.addWidget(self.stability_threshold_label)
        self.app_settings_widgets['stability_threshold'] = QSpinBox()
        self.app_settings_widgets['stability_threshold'].setRange(0, 20)
        threshold_layout.addWidget(self.app_settings_widgets['stability_threshold']); threshold_layout.addStretch()
        stability_layout.addLayout(threshold_layout, 0, 1)
        self.stability_desc_label = QLabel()
        self.stability_desc_label.setWordWrap(True); self.stability_desc_label.setStyleSheet("font-size: 11px; color: #555555;")
        stability_layout.addWidget(self.stability_desc_label, 1, 0, 1, 2)
        app_settings_layout.addWidget(self.stability_group)
        self.lw_mode_group = QGroupBox()
        lw_mode_layout = QVBoxLayout(self.lw_mode_group)
        self.app_settings_widgets['lightweight_mode_enabled'] = QCheckBox()
        lw_mode_layout.addWidget(self.app_settings_widgets['lightweight_mode_enabled'])
        preset_layout = QHBoxLayout(); self.lw_mode_preset_label = QLabel()
        preset_layout.addWidget(self.lw_mode_preset_label); self.app_settings_widgets['lightweight_mode_preset'] = QComboBox()
        preset_layout.addWidget(self.app_settings_widgets['lightweight_mode_preset']); preset_layout.addStretch()
        lw_mode_layout.addLayout(preset_layout)
        self.lw_mode_desc_label = QLabel()
        self.lw_mode_desc_label.setWordWrap(True); self.lw_mode_desc_label.setStyleSheet("font-size: 11px; color: #555555; padding-left: 20px;")
        lw_mode_layout.addWidget(self.lw_mode_desc_label); app_settings_layout.addWidget(self.lw_mode_group)
        app_settings_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))
        self.lang_label = QLabel()
        lang_layout = QHBoxLayout(); lang_layout.addWidget(self.lang_label); self.language_combo = QComboBox()
        lang_layout.addWidget(self.language_combo); lang_layout.addStretch(); app_settings_layout.addLayout(lang_layout)
        app_settings_scroll_area.setWidget(app_settings_widget)
        self.preview_tabs.addTab(app_settings_scroll_area, "")

        usage_widget = QWidget()
        usage_layout = QVBoxLayout(usage_widget)
        self.usage_text = QTextEdit()
        self.usage_text.setReadOnly(True)
        usage_layout.addWidget(self.usage_text)
        usage_widget.setLayout(usage_layout)
        self.preview_tabs.addTab(usage_widget, "")

        right_layout.addWidget(self.preview_tabs, 2)

        self.item_settings_group = QGroupBox()
        item_settings_layout = QGridLayout(self.item_settings_group)
        item_settings_layout.setColumnStretch(1, 1); item_settings_layout.setColumnStretch(3, 1)
        self.item_threshold_label = QLabel()
        item_settings_layout.addWidget(self.item_threshold_label, 0, 0)
        self.item_settings_widgets['threshold'] = QDoubleSpinBox()
        self.item_settings_widgets['threshold'].setRange(0.5, 1.0); self.item_settings_widgets['threshold'].setSingleStep(0.01); self.item_settings_widgets['threshold'].setValue(0.8)
        item_settings_layout.addWidget(self.item_settings_widgets['threshold'], 0, 1)
        self.item_interval_label = QLabel()
        item_settings_layout.addWidget(self.item_interval_label, 0, 2)
        self.item_settings_widgets['interval_time'] = QDoubleSpinBox()
        self.item_settings_widgets['interval_time'].setRange(0.1, 10.0); self.item_settings_widgets['interval_time'].setSingleStep(0.1); self.item_settings_widgets['interval_time'].setValue(1.5)
        item_settings_layout.addWidget(self.item_settings_widgets['interval_time'], 0, 3)
        self.item_settings_widgets['backup_click'] = QCheckBox()
        item_settings_layout.addWidget(self.item_settings_widgets['backup_click'], 1, 0)
        self.item_settings_widgets['backup_time'] = QDoubleSpinBox()
        self.item_settings_widgets['backup_time'].setRange(1.0, 600.0); self.item_settings_widgets['backup_time'].setSingleStep(1.0); self.item_settings_widgets['backup_time'].setValue(300.0)
        item_settings_layout.addWidget(self.item_settings_widgets['backup_time'], 1, 1)
        self.item_debounce_label = QLabel()
        item_settings_layout.addWidget(self.item_debounce_label, 1, 2)
        self.item_settings_widgets['debounce_time'] = QDoubleSpinBox()
        self.item_settings_widgets['debounce_time'].setRange(0.0, 10.0); self.item_settings_widgets['debounce_time'].setSingleStep(0.1); self.item_settings_widgets['debounce_time'].setValue(0.0)
        item_settings_layout.addWidget(self.item_settings_widgets['debounce_time'], 1, 3)
        click_type_layout = QHBoxLayout()
        self.item_settings_widgets['point_click'] = QCheckBox()
        self.item_settings_widgets['range_click'] = QCheckBox()
        self.item_settings_widgets['random_click'] = QCheckBox()
        click_type_layout.addWidget(self.item_settings_widgets['point_click'])
        click_type_layout.addWidget(self.item_settings_widgets['range_click'])
        click_type_layout.addWidget(self.item_settings_widgets['random_click'])
        item_settings_layout.addLayout(click_type_layout, 2, 0, 1, 4)
        separator = QFrame(); separator.setFrameShape(QFrame.Shape.HLine); separator.setFrameShadow(QFrame.Shadow.Sunken)
        item_settings_layout.addWidget(separator, 3, 0, 1, 4)
        self.item_settings_widgets['roi_enabled'] = QCheckBox()
        item_settings_layout.addWidget(self.item_settings_widgets['roi_enabled'], 4, 0)
        roi_mode_layout = QHBoxLayout()
        self.item_settings_widgets['roi_mode_fixed'] = QRadioButton()
        self.item_settings_widgets['roi_mode_variable'] = QRadioButton()
        self.roi_mode_group = QButtonGroup(self)
        self.roi_mode_group.addButton(self.item_settings_widgets['roi_mode_fixed'])
        self.roi_mode_group.addButton(self.item_settings_widgets['roi_mode_variable'])
        roi_mode_layout.addWidget(self.item_settings_widgets['roi_mode_fixed'])
        roi_mode_layout.addWidget(self.item_settings_widgets['roi_mode_variable'])
        item_settings_layout.addLayout(roi_mode_layout, 4, 1)
        self.item_settings_widgets['set_roi_variable_button'] = QPushButton()
        self.item_settings_widgets['set_roi_variable_button'].setCheckable(True)
        item_settings_layout.addWidget(self.item_settings_widgets['set_roi_variable_button'], 4, 2, 1, 2)

        right_layout.addWidget(self.item_settings_group, 1)
        content_layout.addWidget(right_frame, 2)
        main_layout.addWidget(content_frame)

    def connect_signals(self):
        """Connects signals from UI widgets to appropriate slots."""
        if hasattr(self, '_signals_connected') and self._signals_connected:
            return

        self.monitor_button.clicked.connect(self.toggle_monitoring)
        self.perf_monitor_button.clicked.connect(self.openPerformanceMonitorRequested.emit)
        
        self.toggle_minimal_ui_button.clicked.connect(self.toggle_minimal_ui_mode)
        self.open_image_folder_button.clicked.connect(self.open_image_folder)

        self.image_tree.itemSelectionChanged.connect(self.on_image_tree_selection_changed)
        self.image_tree.itemClicked.connect(self.on_image_tree_item_clicked)
        self.image_tree.customContextMenuRequested.connect(self.on_tree_context_menu)
        self.image_tree.orderUpdated.connect(self.orderChanged.emit)
        self.image_tree.itemsMoved.connect(self.itemsMovedIntoFolder.emit)
        
        self.rename_button.clicked.connect(self.on_rename_button_clicked)
        
        self.set_rec_area_button_main_ui.clicked.connect(self.setRecAreaDialog)
        if self.core_engine:
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

        for widget in list(self.auto_scale_widgets.values()):
            if isinstance(widget, QDoubleSpinBox): widget.valueChanged.connect(self.on_app_settings_changed)
            elif isinstance(widget, QSpinBox): widget.valueChanged.connect(self.on_app_settings_changed)
            elif isinstance(widget, QCheckBox): widget.stateChanged.connect(self.on_app_settings_changed)
        for key, widget in self.app_settings_widgets.items():
            if isinstance(widget, QSpinBox): widget.valueChanged.connect(self.on_app_settings_changed)
            elif isinstance(widget, QCheckBox): widget.stateChanged.connect(self.on_app_settings_changed)
            elif isinstance(widget, QComboBox): widget.currentTextChanged.connect(self.on_app_settings_changed)

        self.language_combo.currentTextChanged.connect(self.on_language_changed)
        self.locale_manager.languageChanged.connect(self.retranslate_ui)

        if self.core_engine:
            self.preview_mode_manager.settings_changed_externally.connect(self._update_ui_from_preview_manager)
            self.preview_mode_manager.previewDataApplied.connect(self._emit_settings_for_save)
            self.save_timer.timeout.connect(self.core_engine.save_current_settings)
            self.appConfigChanged.connect(self.core_engine.on_app_config_changed)

        self._signals_connected = True
        
    def on_language_changed(self, lang_name: str):
        """
        Handles the user selecting a new language from the dropdown.
        Saves the setting and triggers a UI re-translation.
        """
        if not lang_name or not self.available_langs:
            return
        
        lang_code = self.available_langs.get(lang_name)
        if not lang_code:
            return

        if lang_code != self.locale_manager.current_lang:
            self.app_config['language'] = lang_code
            self.config_manager.save_app_config(self.app_config)
            
            try:
                self.locale_manager.languageChanged.disconnect(self.retranslate_ui)
            except (TypeError, RuntimeError):
                pass
                
            self.locale_manager.load_locale(lang_code)
            self.retranslate_ui()
            self.locale_manager.languageChanged.connect(self.retranslate_ui)

    def _emit_settings_for_save(self, *args):
        """
        PreviewModeManagerとUIウィジェットから現在の設定を収集し、
        imageSettingsChanged シグナルを発行します。
        """
        if not hasattr(self, 'preview_mode_manager') or not self.core_engine:
            return
            
        path, _ = self.get_selected_item_path()
        if not path or Path(path).is_dir():
             # 選択されているのがフォルダであるか、何も選択されていない場合は保存を中止
             return

        settings = self.preview_mode_manager.get_settings()
              
        # image_path の設定は current_image_path が確実な CoreEngine 側で行う
        if self.core_engine.current_image_path:
             settings['image_path'] = self.core_engine.current_image_path
        else:
             # CoreEngineがパスを持っていないが、UI側で画像パスが確認できた場合
             settings['image_path'] = path 

        try:
            settings['threshold'] = self.item_settings_widgets['threshold'].value()
            settings['interval_time'] = self.item_settings_widgets['interval_time'].value()
            settings['backup_time'] = self.item_settings_widgets['backup_time'].value()
            settings['debounce_time'] = self.item_settings_widgets['debounce_time'].value()
        except KeyError:
             return
        except Exception as e:
             print(f"[ERROR] _emit_settings_for_save: {e}")
             return
 
        self.imageSettingsChanged.emit(settings)
               
    def _update_ui_from_preview_manager(self, settings: dict):
        """
        PreviewModeManagerからの通知を受けて、UIウィジェットの状態を同期します。
        """
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
        """Updates the recognition area preview label."""
        if cv_image is None or cv_image.size == 0: self.rec_area_preview_label.set_pixmap(None); self.rec_area_preview_label.setText(self.locale_manager.tr("rec_area_preview_text")); return
        try:
            rgb_image = cv2.cvtColor(cv_image, cv2.COLOR_BGR2RGB); h, w, ch = rgb_image.shape; bytes_per_line = ch * w
            q_image = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format.Format_RGB888); pixmap = QPixmap.fromImage(q_image)
            self.rec_area_preview_label.set_pixmap(pixmap); self.rec_area_preview_label.setText("")
        except Exception as e: print(f"Error converting image for rec area preview: {e}"); self.rec_area_preview_label.setText("Preview Error"); self.rec_area_preview_label.set_pixmap(None)

    def update_log(self, message: str):
        """Appends a message to the log text edit."""
        self.log_text.append(message); scrollbar = self.log_text.verticalScrollBar(); scrollbar.setValue(scrollbar.maximum())

    def closeEvent(self, event):
        """Handles the main window close event."""
        if self.floating_window:
            if self.core_engine and hasattr(self.core_engine, 'statsUpdated'):
                try:
                    self.core_engine.statsUpdated.disconnect(self.floating_window.on_stats_updated)
                except (TypeError, RuntimeError):
                    pass
            self.floating_window.close()
            
        if self.core_engine: self.core_engine.cleanup()
        self.stopMonitoringRequested.emit(); QApplication.instance().quit(); event.accept()

    def setRecAreaDialog(self):
        """Shows the dialog to choose recognition area selection method."""
        dialog = RecAreaSelectionDialog(self.locale_manager, self); dialog.selectionMade.connect(self.setRecAreaMethodSelected); dialog.move(QCursor.pos()); dialog.exec()

    def adjust_initial_size(self):
        """Adjusts the initial window size after widgets are potentially rendered."""
        self.setMinimumWidth(0); self.resize(960, 640)

    def toggle_minimal_ui_mode(self):
        """Switches between the main window and the minimal floating window."""
        lm = self.locale_manager.tr; self.is_minimal_mode = not self.is_minimal_mode
        if self.is_minimal_mode:
            self.normal_ui_geometries['main'] = self.geometry()
            if self.performance_monitor and self.performance_monitor.isVisible(): 
                self.normal_ui_geometries['perf'] = self.performance_monitor.geometry()
            
            self.showMinimized();
            if self.performance_monitor: self.performance_monitor.hide()
            
            self.floating_window = FloatingWindow(self.locale_manager)
            self.floating_window.startMonitoringRequested.connect(self.startMonitoringRequested)
            self.floating_window.stopMonitoringRequested.connect(self.stopMonitoringRequested)
            self.floating_window.captureImageRequested.connect(self.captureImageRequested)
            self.floating_window.toggleMainUIRequested.connect(self.toggle_minimal_ui_mode)
            self.floating_window.closeRequested.connect(self.toggle_minimal_ui_mode)
            self.floating_window.setRecAreaRequested.connect(self.setRecAreaDialog)
            
            if self.core_engine:
                self.core_engine.statsUpdated.connect(self.floating_window.on_stats_updated)

            current_status_text = self.status_label.text(); current_status_color = "green"
            if current_status_text == lm("status_label_monitoring"): current_status_color = "blue"
            elif current_status_text == lm("status_label_unstable"): current_status_color = "orange"
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
            if self.performance_monitor:
                if 'perf' in self.normal_ui_geometries and not self.performance_monitor.isVisible(): 
                    self.performance_monitor.show(); 
                    self.performance_monitor.setGeometry(self.normal_ui_geometries['perf'])
            
            self.activateWindow(); 
            self.toggle_minimal_ui_button.setText(lm("minimal_ui_button")); 
            self._update_capture_button_state()

    def retranslate_ui(self):
        """Sets or updates all translatable text in the UI based on the current language."""
        lm = self.locale_manager.tr

        self.setWindowTitle(lm("window_title"))
        self.perf_monitor_button.setText(lm("performance_monitor_button"))
        self.header_rec_area_button.setText(lm("recognition_area_button"))
        self.toggle_minimal_ui_button.setText(lm("minimal_ui_button") if not self.is_minimal_mode else lm("minimal_ui_button_stop"))
        self.open_image_folder_button.setText(lm("open_image_folder_button"))
        self.open_image_folder_button.setToolTip(lm("open_image_folder_tooltip"))
        self.monitor_button.setToolTip(lm("monitor_button_tooltip"))

        if not self.core_engine or not self.core_engine.is_monitoring:
            self.status_label.setText(lm("status_label_idle"))
        else:
             self.status_label.setText(lm("status_label_monitoring"))

        self.list_title_label.setText(lm("list_title"))
        self.move_up_button.setText(lm("move_up_button"))
        self.move_down_button.setText(lm("move_down_button"))
        self.load_image_button.setText(lm("add_image_button"))
        
        # (修正：リネームボタンのテキスト設定を追加)
        self.capture_image_button.setText(lm("capture_image_button"))
        self.rename_button.setText(lm("rename_button"))
        
        self.delete_item_button.setText(lm("delete_item_button"))
        self.create_folder_button.setText(lm("create_folder_button"))
        self.move_in_button.setText(lm("move_in_button"))
        self.move_out_button.setText(lm("move_out_button"))

        self.preview_tabs.setTabText(self.preview_tabs.indexOf(self.main_preview_widget), lm("tab_preview"))
        rec_area_tab_index = self.preview_tabs.indexOf(self.rec_area_preview_label.parentWidget())
        if rec_area_tab_index != -1:
            self.preview_tabs.setTabText(rec_area_tab_index, lm("tab_rec_area"))
        self.set_rec_area_button_main_ui.setText(lm("recognition_area_button"))
        self.clear_rec_area_button_main_ui.setText(lm("rec_area_clear_button"))
        self.rec_area_preview_label.setText(lm("rec_area_preview_text"))

        log_tab_index = self.preview_tabs.indexOf(self.log_text.parentWidget())
        if log_tab_index != -1:
             self.preview_tabs.setTabText(log_tab_index, lm("tab_log"))

        self.preview_tabs.setTabText(self.preview_tabs.indexOf(self.auto_scale_group), lm("tab_auto_scale"))
        self.auto_scale_group.setTitle(lm("tab_auto_scale"))
        self.auto_scale_widgets['use_window_scale'].setText(lm("auto_scale_use_window"))
        self.auto_scale_widgets['use_window_scale'].setToolTip(lm("auto_scale_use_window_tooltip"))
        self.auto_scale_widgets['enabled'].setText(lm("auto_scale_enable_search"))
        self.auto_scale_center_label.setText(lm("auto_scale_center"))
        self.auto_scale_range_label.setText(lm("auto_scale_range"))
        # self.auto_scale_widgets['range'].setValue(lm("auto_scale_range")) # 値の設定はload_app_settings_to_uiで行うためコメントアウト
        # self.auto_scale_widgets['steps'].setValue(lm("auto_scale_steps")) # 値の設定はload_app_settings_to_uiで行うためコメントアウト
        self.auto_scale_steps_label.setText(lm("auto_scale_steps"))
        self.as_desc_label.setText(lm("auto_scale_desc"))

        app_settings_tab_index = self.preview_tabs.indexOf(self.preview_tabs.findChild(QScrollArea))
        if app_settings_tab_index != -1:
            self.preview_tabs.setTabText(app_settings_tab_index, lm("tab_app_settings"))
        self.app_settings_widgets['grayscale_matching'].setText(lm("app_setting_grayscale"))
        self.gs_desc_label.setText(lm("app_setting_grayscale_desc"))
        self.app_settings_widgets['capture_method'].setText(lm("app_setting_dxcam"))
        self.dxcam_desc_label.setText(lm("app_setting_dxcam_desc"))
        self.app_settings_widgets['eco_mode_enabled'].setText(lm("app_setting_eco_mode"))
        self.eco_desc_label.setText(lm("app_setting_eco_mode_desc"))
        self.fs_label.setText(lm("app_setting_frame_skip"))
        self.fs_desc_label.setText(lm("app_setting_frame_skip_desc"))
        self.app_settings_widgets['frame_skip_rate'].setRange(1, 20) # 範囲設定を再確認
        self.app_settings_widgets['use_opencl'].setText(lm("app_setting_opencl"))
        self.opencl_desc_label.setText(lm("app_setting_opencl_desc"))
        self.stability_group.setTitle(lm("app_setting_stability_group"))
        self.app_settings_widgets['stability_check_enabled'].setText(lm("app_setting_stability_enable"))
        self.stability_threshold_label.setText(lm("app_setting_stability_threshold"))
        self.app_settings_widgets['stability_threshold'].setRange(0, 20)
        self.stability_desc_label.setText(lm("app_setting_stability_desc"))
        self.lw_mode_group.setTitle(lm("app_setting_lw_mode_group"))
        self.app_settings_widgets['lightweight_mode_enabled'].setText(lm("app_setting_lw_mode_enable"))
        self.lw_mode_preset_label.setText(lm("app_setting_lw_mode_preset"))
        current_preset_index = self.app_settings_widgets['lightweight_mode_preset'].currentIndex()
        self.app_settings_widgets['lightweight_mode_preset'].blockSignals(True)
        self.app_settings_widgets['lightweight_mode_preset'].clear()
        self.app_settings_widgets['lightweight_mode_preset'].addItems([
            lm("app_setting_lw_mode_preset_standard"),
            lm("app_setting_lw_mode_preset_performance"),
            lm("app_setting_lw_mode_preset_ultra")
        ])
        if current_preset_index != -1 and current_preset_index < self.app_settings_widgets['lightweight_mode_preset'].count():
             self.app_settings_widgets['lightweight_mode_preset'].setCurrentIndex(current_preset_index)
        self.app_settings_widgets['lightweight_mode_preset'].blockSignals(False)
        self.lw_mode_desc_label.setText(lm("app_setting_lw_mode_desc"))

        self.lang_label.setText(lm("app_setting_language_label"))
        self.available_langs.clear()
        current_lang_selection_text = self.language_combo.currentText()
        self.language_combo.blockSignals(True)
        self.language_combo.clear()
        selected_lang_code = self.locale_manager.current_lang
        found_current = False
        try:
            for file in self.locale_manager.locales_dir.glob("*.json"):
                lang_code = file.stem
                lang_name = lang_code
                try:
                    with open(file, 'r', encoding='utf-8') as f:
                        lang_data = json.load(f)
                        lang_name = lang_data.get("language_name", lang_code)
                except Exception: pass
                self.available_langs[lang_name] = lang_code
                self.language_combo.addItem(lang_name)
                if lang_code == selected_lang_code:
                    current_lang_selection_text = lang_name
                    found_current = True
        except Exception as e: print(f"Error loading languages for ComboBox: {e}")
        select_index = self.language_combo.findText(current_lang_selection_text)
        if select_index != -1: self.language_combo.setCurrentIndex(select_index)
        elif found_current: pass
        self.language_combo.blockSignals(False)

        usage_tab_index = self.preview_tabs.indexOf(self.usage_text.parentWidget())
        if usage_tab_index != -1:
             self.preview_tabs.setTabText(usage_tab_index, lm("tab_usage"))
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
        self.item_settings_widgets['set_roi_variable_button'].setText(lm("item_setting_roi_button"))
        self.item_settings_widgets['set_roi_variable_button'].setToolTip(lm("item_setting_roi_button_tooltip"))

        self.update_auto_scale_info()
        if self.core_engine:
            status_key = "monitoring" if self.core_engine.is_monitoring else "idle"
            self.set_status(status_key, "blue" if status_key == "monitoring" else "green")
        else: self.set_status("idle", "green")
        current_scale = 0.0
        if self.core_engine and self.core_engine.current_window_scale is not None:
            current_scale = self.core_engine.current_window_scale
        self.on_window_scale_calculated(current_scale)
        self._update_capture_button_state()
        
    def is_dark_mode(self):
        palette = self.palette()
        window_color = palette.color(QPalette.ColorRole.Window)
        text_color = palette.color(QPalette.ColorRole.WindowText)
        return window_color.lightness() < text_color.lightness()

    def load_app_settings_to_ui(self):
        as_conf = self.app_config.get('auto_scale', {})
        self.auto_scale_widgets['use_window_scale'].setChecked(as_conf.get('use_window_scale', True))
        self.auto_scale_widgets['enabled'].setChecked(as_conf.get('enabled', False))
        self.auto_scale_widgets['center'].setValue(as_conf.get('center', 1.0))
        self.auto_scale_widgets['range'].setValue(as_conf.get('range', 0.2))
        self.auto_scale_widgets['steps'].setValue(as_conf.get('steps', 5))

        self.app_settings_widgets['capture_method'].setChecked(self.app_config.get('capture_method', 'dxcam') == 'dxcam')
        self.app_settings_widgets['frame_skip_rate'].setValue(self.app_config.get('frame_skip_rate', 2))
        self.app_settings_widgets['grayscale_matching'].setChecked(self.app_config.get('grayscale_matching', False))
        self.app_settings_widgets['use_opencl'].setChecked(self.app_config.get('use_opencl', True))

        eco_conf = self.app_config.get('eco_mode', {})
        self.app_settings_widgets['eco_mode_enabled'].setChecked(eco_conf.get('enabled', True))

        stability_conf = self.app_config.get('screen_stability_check', {})
        self.app_settings_widgets['stability_check_enabled'].setChecked(stability_conf.get('enabled', True))
        self.app_settings_widgets['stability_threshold'].setValue(stability_conf.get('threshold', 8))

        lw_conf = self.app_config.get('lightweight_mode', {})
        self.app_settings_widgets['lightweight_mode_enabled'].setChecked(lw_conf.get('enabled', False))
        preset_internal_name = lw_conf.get('preset', 'standard')
        preset_display_key = f"app_setting_lw_mode_preset_{preset_internal_name}"
        preset_display_text = self.locale_manager.tr(preset_display_key)
        if preset_display_text == preset_display_key:
             preset_display_text = self.locale_manager.tr("app_setting_lw_mode_preset_standard")
        self.app_settings_widgets['lightweight_mode_preset'].setCurrentText(preset_display_text)

        self.update_auto_scale_info()
        self.update_dependent_widgets_state()

    def update_dependent_widgets_state(self):
        is_lw_mode_enabled = self.app_settings_widgets['lightweight_mode_enabled'].isChecked()
        self.auto_scale_group.setEnabled(not is_lw_mode_enabled)
        self.app_settings_widgets['lightweight_mode_preset'].setEnabled(is_lw_mode_enabled)
        is_stability_enabled = self.app_settings_widgets['stability_check_enabled'].isChecked()
        self.app_settings_widgets['stability_threshold'].setEnabled(is_stability_enabled)
        is_fs_user_configurable = not is_lw_mode_enabled
        self.app_settings_widgets['frame_skip_rate'].setEnabled(is_fs_user_configurable)

    def get_auto_scale_settings(self) -> dict:
        return {
            "use_window_scale": self.auto_scale_widgets['use_window_scale'].isChecked(),
            "enabled": self.auto_scale_widgets['enabled'].isChecked(),
            "center": self.auto_scale_widgets['center'].value(),
            "range": self.auto_scale_widgets['range'].value(),
            "steps": self.auto_scale_widgets['steps'].value()
        }

    def update_auto_scale_info(self):
        lm = self.locale_manager.tr
        if self.auto_scale_widgets['enabled'].isChecked():
            center = self.auto_scale_widgets['center'].value()
            range_ = self.auto_scale_widgets['range'].value()
            steps = self.auto_scale_widgets['steps'].value()
            if steps > 1:
                scales = np.linspace(center - range_, center + range_, steps)
                self.auto_scale_info_label.setText(lm("auto_scale_info_searching", f"{scales[0]:.3f}", f"{scales[-1]:.3f}"))
            else:
                 self.auto_scale_info_label.setText(lm("auto_scale_info_searching", f"{center:.3f}", f"{center:.3f}"))
            self.auto_scale_info_label.setStyleSheet("color: blue;")
        else:
            self.auto_scale_info_label.setText(lm("auto_scale_info_disabled"))
            self.auto_scale_info_label.setStyleSheet("color: gray;")

    def on_app_settings_changed(self):
        lm = self.locale_manager.tr
        self.app_config['auto_scale'] = self.get_auto_scale_settings()
        self.app_config['capture_method'] = 'dxcam' if self.app_settings_widgets['capture_method'].isChecked() else 'mss'
        self.app_config['frame_skip_rate'] = self.app_settings_widgets['frame_skip_rate'].value()
        self.app_config['grayscale_matching'] = self.app_settings_widgets['grayscale_matching'].isChecked()
        self.app_config['use_opencl'] = self.app_settings_widgets['use_opencl'].isChecked()
        self.app_config['eco_mode'] = {"enabled": self.app_settings_widgets['eco_mode_enabled'].isChecked()}
        self.app_config['screen_stability_check'] = {
            "enabled": self.app_settings_widgets['stability_check_enabled'].isChecked(),
            "threshold": self.app_settings_widgets['stability_threshold'].value()
        }
        preset_display_text = self.app_settings_widgets['lightweight_mode_preset'].currentText()
        preset_internal_name = "standard"
        if preset_display_text == lm("app_setting_lw_mode_preset_standard"): preset_internal_name = "standard"
        elif preset_display_text == lm("app_setting_lw_mode_preset_performance"): preset_internal_name = "performance"
        elif preset_display_text == lm("app_setting_lw_mode_preset_ultra"): preset_internal_name = "ultra"
        self.app_config['lightweight_mode'] = {
            "enabled": self.app_settings_widgets['lightweight_mode_enabled'].isChecked(),
            "preset": preset_internal_name
        }
        self.config_manager.save_app_config(self.app_config)
        self.update_auto_scale_info()
        self.update_dependent_widgets_state()
        self.appConfigChanged.emit()

    def update_image_tree(self):
        lm = self.locale_manager.tr
        self.image_tree.blockSignals(True)
        expanded_folders = set()
        selected_path, _ = self.get_selected_item_path()
        for i in range(self.image_tree.topLevelItemCount()):
            item = self.image_tree.topLevelItem(i)
            if item.childCount() > 0 or (item.data(0, Qt.UserRole) and Path(item.data(0, Qt.UserRole)).is_dir()):
                path = item.data(0, Qt.UserRole)
                if path and item.isExpanded():
                    expanded_folders.add(path)
        self.image_tree.clear()
        hierarchical_list = self.config_manager.get_hierarchical_list()
        item_to_reselect = None
        for item_data in hierarchical_list:
            if item_data['type'] == 'folder':
                folder_settings = item_data['settings']
                mode = folder_settings.get('mode', 'normal')
                folder_item = QTreeWidgetItem(self.image_tree, [lm("folder_item_prefix", item_data['name'])])
                folder_item.setData(0, Qt.UserRole, item_data['path'])
                folder_item.setFlags(folder_item.flags() | Qt.ItemIsDropEnabled)
                brush = QBrush(QApplication.palette().text().color())
                icon_color = Qt.transparent
                if mode == 'normal': brush = QBrush(QColor("darkgray")); icon_color = QColor("darkgray")
                elif mode == 'excluded': brush = QBrush(Qt.red); icon_color = Qt.red
                elif mode == 'priority_image': brush = QBrush(Qt.blue); icon_color = Qt.blue
                elif mode == 'priority_timer': brush = QBrush(Qt.darkGreen); icon_color = Qt.green
                folder_item.setIcon(0, self.create_colored_icon(icon_color))
                folder_item.setForeground(0, brush)
                if item_data['path'] in expanded_folders: folder_item.setExpanded(True)
                if item_data['path'] == selected_path: item_to_reselect = folder_item
                for child_data in item_data['children']:
                    child_item = QTreeWidgetItem(folder_item, [lm("image_item_prefix", child_data['name'])])
                    child_item.setData(0, Qt.UserRole, child_data['path'])
                    child_item.setForeground(0, brush)
                    if child_data['path'] == selected_path: item_to_reselect = child_item
            elif item_data['type'] == 'image':
                image_item = QTreeWidgetItem(self.image_tree, [item_data['name']])
                image_item.setData(0, Qt.UserRole, item_data['path'])
                image_item.setIcon(0, self.create_colored_icon(Qt.transparent))
                if item_data['path'] == selected_path: item_to_reselect = image_item
        if item_to_reselect: self.image_tree.setCurrentItem(item_to_reselect)
        self.image_tree.blockSignals(False)
        if item_to_reselect: self.on_image_tree_selection_changed()

    def on_tree_context_menu(self, pos):
        item = self.image_tree.itemAt(pos)
        lm = self.locale_manager.tr
        if not item: return
        path_str = item.data(0, Qt.UserRole)
        if not path_str: return
        path = Path(path_str)
        if path.is_dir():
            current_settings = self.config_manager.load_item_setting(path)
            dialog = FolderSettingsDialog(path.name, current_settings, self.locale_manager, self)
            if dialog.exec():
                new_settings = dialog.get_settings()
                self.config_manager.save_item_setting(path, new_settings)
                self.folderSettingsChanged.emit()
                self.update_image_tree()
        elif path.is_file():
            try:
                settings = self.config_manager.load_item_setting(path)
                click_mode_text = lm("context_menu_info_mode_unset")
                if settings.get('point_click'): click_mode_text = lm("context_menu_info_mode_point")
                elif settings.get('range_click'): click_mode_text = lm("context_menu_info_mode_range_random") if settings.get('random_click') else lm("context_menu_info_mode_range")
                threshold = settings.get('threshold', 0.8); interval = settings.get('interval_time', 1.5)
                pixmap = QPixmap(path_str); img_size_text = lm("context_menu_info_size_error")
                if not pixmap.isNull(): img_size_text = lm("context_menu_info_size", pixmap.width(), pixmap.height())
                mode_str = f"({click_mode_text})"
                threshold_str = lm('context_menu_info_threshold', f'{threshold:.2f}')
                interval_str = lm('context_menu_info_interval', f'{interval:.1f}')
                tooltip_text = f"{mode_str}\n{threshold_str}：{interval_str}\n{img_size_text}"
                global_pos = self.image_tree.mapToGlobal(pos); QToolTip.showText(global_pos, tooltip_text, self.image_tree)
            except Exception as e: global_pos = self.image_tree.mapToGlobal(pos); QToolTip.showText(global_pos, lm("context_menu_info_error", str(e)), self.image_tree)

    def set_tree_enabled(self, enabled: bool):
        self.image_tree.setEnabled(enabled)

    def on_cache_build_finished(self):
        self.update_image_tree()
        self.set_tree_enabled(True)
        self.is_processing_tree_change = False

    def get_selected_item_path(self):
        selected_items = self.image_tree.selectedItems()
        if not selected_items: return None, None
        item = selected_items[0]; path = item.data(0, Qt.UserRole); name = item.text(0)
        return path, name

    def on_image_tree_item_clicked(self, item, column):
        if self.is_processing_tree_change or not item: return
        path_str = item.data(0, Qt.UserRole)
        if not path_str: return
        if not Path(path_str).is_dir(): self.switch_to_preview_tab()

    def switch_to_preview_tab(self):
        if self.preview_tabs and self.main_preview_widget: self.preview_tabs.setCurrentWidget(self.main_preview_widget)

    def on_image_tree_selection_changed(self):
        self.current_best_scale_label.setText(self.locale_manager.tr("auto_scale_best_scale_default"))
        self.current_best_scale_label.setStyleSheet("color: gray;")
        path, name = self.get_selected_item_path()
        if self.core_engine: self.core_engine.load_image_and_settings(path)

    def move_item_up(self):
        if self.is_processing_tree_change: return; item = self.image_tree.currentItem();
        if not item: return; parent = item.parent()
        if parent:
            index = parent.indexOfChild(item)
            if index > 0: 
                self.set_tree_enabled(False)
                taken_item = parent.takeChild(index)
                parent.insertChild(index - 1, taken_item)
                self.image_tree.setCurrentItem(taken_item) # 選択状態を維持
            else:
                return
        else:
            index = self.image_tree.indexOfTopLevelItem(item)
            if index > 0: 
                self.set_tree_enabled(False)
                taken_item = self.image_tree.takeTopLevelItem(index)
                self.image_tree.insertTopLevelItem(index - 1, taken_item)
                self.image_tree.setCurrentItem(taken_item)
            else:
                return
        
        self.orderChanged.emit()
        self.set_tree_enabled(True)
        
    def move_item_down(self):
        if self.is_processing_tree_change: return; item = self.image_tree.currentItem();
        if not item: return; parent = item.parent()
        if parent:
            index = parent.indexOfChild(item)
            if index < parent.childCount() - 1: 
                self.set_tree_enabled(False)
                taken_item = parent.takeChild(index)
                parent.insertChild(index + 1, taken_item)
                self.image_tree.setCurrentItem(taken_item) # 選択状態を維持
            else:
                return
        else:
            index = self.image_tree.indexOfTopLevelItem(item)
            if index < self.image_tree.topLevelItemCount() - 1: 
                self.set_tree_enabled(False)
                taken_item = self.image_tree.takeTopLevelItem(index)
                self.image_tree.insertTopLevelItem(index + 1, taken_item)
                self.image_tree.setCurrentItem(taken_item)
            else:
                return
        
        self.orderChanged.emit()
        self.set_tree_enabled(True)

    def save_tree_order(self):
        """
        (UIスレッド) 現在のツリーの順序を読み取り、
        保存用のデータディクショナリとして返します。
        """
        data_to_save = {
            'top_level': [],
            'folders': {}
        }
        
        top_level_order_paths = []
        folder_data_map = {}

        for i in range(self.image_tree.topLevelItemCount()):
            item = self.image_tree.topLevelItem(i)
            if not item: continue
            
            original_path_str = item.data(0, Qt.UserRole)
            if not original_path_str: continue
            
            original_path = Path(original_path_str)
            path_str = original_path_str

            if original_path.is_dir():
                path_str = str(original_path)
            else:
                new_path = self.config_manager.base_dir / original_path.name
                path_str = str(new_path)
            
            if str(original_path) != path_str:
                item.setData(0, Qt.UserRole, path_str)
            
            top_level_order_paths.append(path_str)
            
            if Path(path_str).is_dir():
                folder_path_str = path_str
                child_order_filenames = []
                for j in range(item.childCount()):
                    child_item = item.child(j)
                    if not child_item: continue
                    
                    child_path_str = child_item.data(0, Qt.UserRole)
                    if not child_path_str: continue
                    
                    original_child_path = Path(child_path_str)
                    child_path_name = original_child_path.name
                    
                    if not original_child_path.is_dir():
                        new_child_path = Path(folder_path_str) / original_child_path.name
                        child_path_name = original_child_path.name
                        
                        if str(original_child_path) != str(new_child_path):
                            child_item.setData(0, Qt.UserRole, str(new_child_path))
                    
                    child_order_filenames.append(child_path_name)
                
                folder_data_map[folder_path_str] = child_order_filenames
        
        data_to_save['top_level'] = top_level_order_paths
        data_to_save['folders'] = folder_data_map
        
        return data_to_save

    def on_delete_button_clicked(self):
        lm = self.locale_manager.tr; selected_items = self.image_tree.selectedItems();
        if not selected_items: QMessageBox.warning(self, lm("warn_delete_title"), lm("warn_delete_no_selection")); return
        item_names = [f"'{item.text(0).strip()}'" for item in selected_items]
        message = lm("confirm_delete_message", len(item_names), ', '.join(item_names))
        reply = QMessageBox.question(self, lm("confirm_delete_title"), message, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            paths_to_delete = [item.data(0, Qt.UserRole) for item in selected_items if item.data(0, Qt.UserRole)]
            if paths_to_delete: self.deleteItemsRequested.emit(paths_to_delete)
    
    def on_rename_button_clicked(self):
        """
        「リネーム」ボタンがクリックされたときに呼び出されます。
        ダイアログを表示し、renameItemRequested シグナルを発行します。
        """
        lm = self.locale_manager.tr
        
        path_str, current_name = self.get_selected_item_path()
        if not path_str:
            QMessageBox.warning(self, lm("rename_dialog_title"), lm("warn_rename_no_selection"))
            return
            
        current_base_name = Path(current_name).stem

        new_name, ok = QInputDialog.getText(
            self, 
            lm("rename_dialog_title"), 
            lm("rename_dialog_prompt"), 
            QLineEdit.EchoMode.Normal, 
            current_base_name
        )

        if not ok:
            self.logger.log("log_rename_cancelled")
            return
        
        if not new_name.strip():
            QMessageBox.warning(self, lm("warn_rename_title"), lm("log_rename_error_empty"))
            return
            
        if any(char in new_name for char in '/\\:*?"<>|'):
            QMessageBox.warning(self, lm("warn_rename_title"), lm("log_rename_error_general", "Invalid characters in name"))
            return

        if new_name == current_base_name:
            self.logger.log("log_rename_item_no_change")
            return
            
        if Path(path_str).is_file():
            suffix = Path(path_str).suffix
            new_name_with_suffix = new_name + suffix
        else:
            new_name_with_suffix = new_name

        self.renameItemRequested.emit(path_str, new_name_with_suffix)

    def set_settings_from_data(self, settings_data):
        """Updates SpinBoxes only. PreviewModeManager handles the rest."""
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
  
    def toggle_monitoring(self):
        if self.monitor_button.text() == self.locale_manager.tr("monitor_button_start"): self.startMonitoringRequested.emit()
        else: self.stopMonitoringRequested.emit()

    def set_status(self, text_key, color="green"):
        lm = self.locale_manager.tr; display_text = ""; style_color = color
        if text_key == "monitoring": self.monitor_button.setText(lm("monitor_button_stop")); display_text = lm("status_label_monitoring"); style_color = "blue"
        elif text_key == "idle": self.monitor_button.setText(lm("monitor_button_start")); display_text = lm("status_label_idle"); style_color = "green"; self.current_best_scale_label.setText(lm("auto_scale_best_scale_default")); self.current_best_scale_label.setStyleSheet("color: gray;")
        elif text_key == "unstable": display_text = lm("status_label_unstable"); style_color = "orange"
        else: display_text = text_key
        self.status_label.setText(display_text); self.status_label.setStyleSheet(f"font-weight: bold; color: {style_color};")
        if self.floating_window: self.floating_window.update_status(display_text, style_color)
        
        # ★★★ 修正: キャプチャボタンの状態更新を追加 ★★★
        self._update_capture_button_state()

    def on_best_scale_found(self, image_path: str, scale: float):
        lm = self.locale_manager.tr; current_selected_path, _ = self.get_selected_item_path();
        if image_path and image_path == current_selected_path: self.current_best_scale_label.setText(lm("auto_scale_best_scale_found", f"{scale:.3f}")); self.current_best_scale_label.setStyleSheet("color: green;")

    def on_window_scale_calculated(self, scale: float):
        lm = self.locale_manager.tr
        if scale > 0: 
            color = "white" if self.is_dark_mode() else "purple"
            self.current_best_scale_label.setText(lm("auto_scale_window_scale_found", f"{scale:.3f}"))
            self.current_best_scale_label.setStyleSheet(f"color: {color};")
            self.auto_scale_widgets['center'].setValue(scale)
        else: 
            self.current_best_scale_label.setText(lm("auto_scale_best_scale_default"))
            self.current_best_scale_label.setStyleSheet("color: gray;")
            
        self._update_capture_button_state(scale)

    def _update_capture_button_state(self, current_scale=None):
        if not self.core_engine: return
        
        # 1. 認識範囲が設定されているか
        is_rec_area_set = self.core_engine.recognition_area is not None
        
        # 2. ウィンドウスケールが 1.0 または None か
        if current_scale is None: 
            current_scale = self.core_engine.current_window_scale
        
        # キャプチャを無効にする条件: 認識エリア未設定 OR スケールが 1.0 以外
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

    def load_images_dialog(self):
        lm = self.locale_manager.tr; file_paths, _ = QFileDialog.getOpenFileNames(self, lm("load_images_dialog_title"), str(self.config_manager.base_dir), lm("load_images_dialog_filter"));
        if file_paths: self.set_tree_enabled(False); self.loadImagesRequested.emit(file_paths)

    def update_image_preview(self, cv_image: np.ndarray, settings_data: dict = None):
        """Passes image (or splash) and settings data to PreviewModeManager."""
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
        """Hides UI elements when recognition area selection starts."""
        if self.performance_monitor: self.performance_monitor.hide()
        if self.is_minimal_mode and self.floating_window: self.floating_window.hide()

    def on_selection_process_finished(self):
        """Restores UI elements after recognition area selection finishes."""
        if self.is_minimal_mode:
            if self.floating_window: self.floating_window.show()
        else:
             if self.performance_monitor and 'perf' in self.normal_ui_geometries and not self.performance_monitor.isVisible():
                 self.performance_monitor.show()
