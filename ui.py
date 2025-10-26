# ui.py (D&D機能 統合版・多言語対応版・言語切り替え機能追加・インデント修正版・スプラッシュ画像対応版)
# ★★★ Linux D&Dインジケータをダミーアイテム方式に修正 ★★★
# ★★★ switch_to_preview_tab メソッドを追加 ★★★
# ★★★ プレビュー描画更新タイミングを修正 ★★★
# ★★★ 削除された setRecAreaDialog メソッドを復元 ★★★
# ★★★ update_image_preview メソッドの存在を確認 ★★★
# ★★★ 軽量化モードの保存/読み込みロジックを修正 (問題1対応) ★★★
# ★★★ [修正] 右クリック時のプロパティ表示が f-string が原因で失敗していたのを修正 ★★★
# ★★★ [再修正] 右クリック時のプロパティ表示の翻訳キー呼び出し方をJSON定義に合わせて修正 ★★★
# ★★★ [修正] ウィンドウスケールが1.0倍以外の時に画像キャプチャボタンを無効化 ★★★
# ★★★ [修正] set_settings_from_data でチェックボックス状態をデータから直接設定 ★★★
# ★★★ [修正] キャプチャボタン無効化の条件式が 0.0 (矩形選択時) を考慮していなかった問題を修正 ★★★

import sys
import json
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QPushButton, QVBoxLayout, QWidget, QLabel,
    QFrame, QHBoxLayout, QGroupBox, QSpinBox, QDoubleSpinBox, QCheckBox,
    QGridLayout, QSizePolicy, QSpacerItem, QToolButton, QFileDialog, QLineEdit,
    QTreeWidget, QTreeWidgetItem, QMenu, QTabWidget, QTextEdit, QDialog, QMessageBox,
    QComboBox, QDialogButtonBox, QRadioButton, QButtonGroup, QScrollArea, QAbstractItemView,
    QProxyStyle, QStyle, QStyleOptionViewItem, QToolTip
)
from PySide6.QtGui import (
    QIcon, QPixmap, QImage, QPainter, QColor, QFontMetrics, QPen, QCursor,
    QBrush, QFont, QPalette
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


try:
    OPENCL_AVAILABLE = cv2.ocl.haveOpenCL()
except:
    OPENCL_AVAILABLE = False

# --- DraggableTreeWidget ---
class DraggableTreeWidget(QTreeWidget):
    orderUpdated = Signal()
    itemsMoved = Signal(list, str) # Emits [source_paths], dest_folder_path

    def __init__(self, parent=None):
        super().__init__(parent)
        self.last_highlighted_item = None
        self.highlight_color = QApplication.palette().highlight().color().lighter(150)
        self.config_manager = None # Must be set externally after initialization

        # ★★★ ダミーインジケータアイテムを作成 ★★★
        self.dummy_indicator_item = QTreeWidgetItem(["――――――――――"])
        brush = QBrush(QColor("red"))
        self.dummy_indicator_item.setForeground(0, brush)
        # このアイテム自体はD&Dの対象外にする
        flags = self.dummy_indicator_item.flags()
        flags &= ~Qt.ItemIsDragEnabled
        flags &= ~Qt.ItemIsDropEnabled
        self.dummy_indicator_item.setFlags(flags)

        self.setDropIndicatorShown(False) # 標準のインジケータは無効

    def _remove_dummy_indicator(self):
        """ツリーからダミーインジケータを削除します。"""
        if self.dummy_indicator_item:
            parent = self.dummy_indicator_item.parent()
            if parent:
                parent.removeChild(self.dummy_indicator_item)
            else:
                # トップレベルアイテムかどうかを確認
                index = self.indexOfTopLevelItem(self.dummy_indicator_item)
                if index != -1:
                    self.takeTopLevelItem(index)

    def dragEnterEvent(self, event):
        # Accept drags originating from this widget itself
        if event.source() == self:
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        # Only handle drags from this widget
        if event.source() != self:
            super().dragMoveEvent(event)
            return

        event.acceptProposedAction()

        # Clear previous highlighting
        if self.last_highlighted_item:
            self.last_highlighted_item.setBackground(0, QBrush(Qt.transparent))
            self.last_highlighted_item = None

        # ★★★ ダミーインジケータを削除 ★★★
        self._remove_dummy_indicator()

        target_item = self.itemAt(event.position().toPoint())
        pos = self.dropIndicatorPosition() # Get potential drop position

        # Determine where the drop would occur and update indicator/highlight
        if pos == self.DropIndicatorPosition.OnItem and target_item:
            path_str = target_item.data(0, Qt.UserRole)
            # Highlight folders when dropping onto them
            if path_str and Path(path_str).is_dir():
                target_item.setBackground(0, self.highlight_color)
                self.last_highlighted_item = target_item
            else:
                # ★★★ フォルダ以外 (OnItem) の場合はアイテムの下にインジケータを挿入 ★★★
                parent = target_item.parent()
                if parent:
                    index = parent.indexOfChild(target_item)
                    parent.insertChild(index + 1, self.dummy_indicator_item)
                else:
                    index = self.indexOfTopLevelItem(target_item)
                    self.insertTopLevelItem(index + 1, self.dummy_indicator_item)

        elif pos in [self.DropIndicatorPosition.AboveItem, self.DropIndicatorPosition.BelowItem] and target_item:
            # ★★★ アイテムの上または下にインジケータを挿入 ★★★
            parent = target_item.parent()
            index_offset = 1 if pos == self.DropIndicatorPosition.BelowItem else 0
            if parent:
                index = parent.indexOfChild(target_item)
                parent.insertChild(index + index_offset, self.dummy_indicator_item)
            else:
                index = self.indexOfTopLevelItem(target_item)
                self.insertTopLevelItem(index + index_offset, self.dummy_indicator_item)

        elif pos == self.DropIndicatorPosition.OnViewport:
             # ★★★ 空白領域 (末尾) にインジケータを挿入 ★★★
             self.insertTopLevelItem(self.topLevelItemCount(), self.dummy_indicator_item)

    def dragLeaveEvent(self, event):
        # Clear highlighting and indicator when drag leaves the widget
        if self.last_highlighted_item:
            self.last_highlighted_item.setBackground(0, QBrush(Qt.transparent))
            self.last_highlighted_item = None

        # ★★★ ダミーインジケータを削除 ★★★
        self._remove_dummy_indicator()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        # Clear highlighting and indicator after drop
        if self.last_highlighted_item:
            self.last_highlighted_item.setBackground(0, QBrush(Qt.transparent))
            self.last_highlighted_item = None

        # ★★★ ダミーインジケータを削除 ★★★
        self._remove_dummy_indicator()

        # Only handle drops from this widget
        if event.source() != self:
            super().dropEvent(event)
            return

        target_item = self.itemAt(event.position().toPoint())
        dragged_items = self.selectedItems()
        if not dragged_items:
            return

        source_parent = dragged_items[0].parent() # Assuming all selected items have same parent initially
        pos = self.dropIndicatorPosition()

        # Clone items to re-insert later (avoids issues with modifying during iteration)
        cloned_items_data = [(item.clone(), item.data(0, Qt.UserRole)) for item in dragged_items]

        # Remove original dragged items
        for item in dragged_items:
            parent = item.parent()
            if parent:
                parent.removeChild(item)
            else:
                self.takeTopLevelItem(self.indexOfTopLevelItem(item))

        dest_parent = None
        insert_index = -1

        # Determine destination parent and index based on drop position
        if pos == self.DropIndicatorPosition.OnItem and target_item:
            path_str = target_item.data(0, Qt.UserRole)
            # If dropped onto a folder
            if path_str and Path(path_str).is_dir():
                dest_parent = target_item
                insert_index = 0 # Insert at the beginning of the folder
            else:
                # If dropped "on" a non-folder item, treat as dropping below it
                dest_parent = target_item.parent()
                if dest_parent:
                    insert_index = dest_parent.indexOfChild(target_item) + 1
                else:
                    insert_index = self.indexOfTopLevelItem(target_item) + 1
        elif target_item:
            # If dropped above or below an item
            dest_parent = target_item.parent()
            if dest_parent:
                insert_index = dest_parent.indexOfChild(target_item)
                if pos == self.DropIndicatorPosition.BelowItem:
                    insert_index += 1
            else: # Top-level item
                insert_index = self.indexOfTopLevelItem(target_item)
                if pos == self.DropIndicatorPosition.BelowItem:
                    insert_index += 1
        else: # Dropped in empty space (viewport)
             dest_parent = None
             insert_index = self.topLevelItemCount() # Insert at the end

        # Insert cloned items at the calculated position
        inserted_items = []
        if dest_parent:
            for i, (item_clone, _) in enumerate(cloned_items_data):
                dest_parent.insertChild(insert_index + i, item_clone)
                inserted_items.append(item_clone)
        else:
            for i, (item_clone, _) in enumerate(cloned_items_data):
                self.insertTopLevelItem(insert_index + i, item_clone)
                inserted_items.append(item_clone)

        # Reselect the moved items
        self.clearSelection()
        if inserted_items:
            for item in inserted_items:
                item.setSelected(True)
            self.scrollToItem(inserted_items[0]) # Scroll to the first moved item

        # Emit signal if items moved between folders or top-level
        if source_parent != dest_parent:
            dest_path = str(self.config_manager.base_dir) if dest_parent is None else dest_parent.data(0, Qt.UserRole)
            source_paths = [path for _, path in cloned_items_data if path]
            if source_paths and dest_path:
                self.itemsMoved.emit(source_paths, dest_path)

        # Always emit order update signal after drop
        self.orderUpdated.emit()
        event.accept()

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

    def __init__(self, core_engine, capture_manager, config_manager, logger, locale_manager):
        super().__init__(parent=None)
        self.core_engine = core_engine
        self.capture_manager = capture_manager
        self.config_manager = config_manager
        self.logger = logger
        self.locale_manager = locale_manager

        self.item_settings_widgets = {}
        self.app_settings_widgets = {}
        self.auto_scale_widgets = {}
        self.available_langs = {} # Dictionary to store {display_name: code}

        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint)

        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(1000) # 1 second delay for saving settings
        self.is_processing_tree_change = False # Flag to prevent redundant updates

        # Load app config and apply language setting
        self.app_config = self.config_manager.load_app_config()
        self.locale_manager.load_locale(self.app_config.get("language", "en_US"))

        # --- Splash Image Loading ---
        self.splash_pixmap = None
        try:
            locales_path = self.locale_manager.locales_dir
            splash_png_path = locales_path / "splash.png"
            splash_jpg_path = locales_path / "splash.jpg"

            if splash_png_path.exists():
                self.splash_pixmap = QPixmap(str(splash_png_path))
            elif splash_jpg_path.exists():
                self.splash_pixmap = QPixmap(str(splash_jpg_path))

            if self.splash_pixmap and self.splash_pixmap.isNull():
                self.splash_pixmap = None # Failed to load

        except Exception as e:
            self.logger.log("log_error_splash_load", str(e))
            self.splash_pixmap = None
        # --- End Splash Image Loading ---

        self.performance_monitor = None
        self.is_minimal_mode = False
        self.normal_ui_geometries = {} # Store window positions before minimizing
        self.floating_window = None

        # Create UI elements
        self.setup_ui()
        # Set initial text based on loaded language
        self.retranslate_ui()
        # Load saved settings into UI widgets
        self.load_app_settings_to_ui()

        # Initialize the manager for preview drawing modes
        self.preview_mode_manager = PreviewModeManager(
            roi_button=self.item_settings_widgets['set_roi_variable_button'],
            point_cb=self.item_settings_widgets['point_click'],
            range_cb=self.item_settings_widgets['range_click'],
            random_cb=self.item_settings_widgets['random_click'],
            locale_manager=self.locale_manager
        )

        # ★★★ [修正] キャプチャボタンの参照を保持 ★★★
        self.main_capture_button = self.capture_image_button # setup_ui で作成されるボタン
        # floating_window 内のボタンは floating_window 作成時に参照する

        # Adjust size after UI is potentially rendered
        QTimer.singleShot(100, self.adjust_initial_size)

        # Initial preview update (for splash screen)
        QTimer.singleShot(0, lambda: self.update_image_preview(None, None))
        # ★★★ [修正] 初期状態でボタンの状態を更新 ★★★
        QTimer.singleShot(0, self._update_capture_button_state)

    def set_performance_monitor(self, monitor):
        self.performance_monitor = monitor

    def setup_ui(self):
        """Creates all UI widgets without setting translatable text."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # --- Header ---
        header_frame = QFrame()
        header_layout = QHBoxLayout(header_frame)
        self.monitor_button = QPushButton() # Text set in retranslate_ui
        self.monitor_button.setFixedSize(120, 30)
        header_layout.addWidget(self.monitor_button)
        self.perf_monitor_button = QPushButton() # Text set in retranslate_ui
        self.perf_monitor_button.setFixedSize(120, 30)
        header_layout.addWidget(self.perf_monitor_button)
        self.header_rec_area_button = QPushButton() # Text set in retranslate_ui
        self.header_rec_area_button.setFixedSize(120, 30)
        self.header_rec_area_button.clicked.connect(self.setRecAreaDialog) # Connect here
        header_layout.addWidget(self.header_rec_area_button)
        self.toggle_minimal_ui_button = QPushButton() # Text set in retranslate_ui
        self.toggle_minimal_ui_button.setFixedSize(120, 30)
        header_layout.addWidget(self.toggle_minimal_ui_button)
        self.open_image_folder_button = QPushButton() # Text set in retranslate_ui
        self.open_image_folder_button.setFixedSize(120, 30)
        header_layout.addWidget(self.open_image_folder_button)
        header_layout.addSpacerItem(QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        self.status_label = QLabel() # Text set in retranslate_ui/set_status
        self.status_label.setStyleSheet("font-size: 16px; font-weight: bold; color: green;")
        header_layout.addWidget(self.status_label)
        main_layout.addWidget(header_frame)

        # --- Content Area ---
        content_frame = QFrame()
        content_layout = QHBoxLayout(content_frame)

        # --- Left Panel (Tree View & Buttons) ---
        left_frame = QFrame()
        left_layout = QVBoxLayout(left_frame)
        self.list_title_label = QLabel() # Text set in retranslate_ui
        left_layout.addWidget(self.list_title_label)
        # Order buttons
        order_button_frame = QHBoxLayout()
        self.move_up_button = QPushButton() # Text set in retranslate_ui
        self.move_down_button = QPushButton() # Text set in retranslate_ui
        order_button_frame.addWidget(self.move_up_button)
        order_button_frame.addWidget(self.move_down_button)
        left_layout.addLayout(order_button_frame)
        # Tree widget
        self.image_tree = DraggableTreeWidget()
        self.image_tree.config_manager = self.config_manager # Pass config manager for D&D logic
        self.image_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.image_tree.setDragDropMode(QAbstractItemView.InternalMove)
        self.image_tree.setDragEnabled(True)
        self.image_tree.setAcceptDrops(True)
        self.image_tree.setDropIndicatorShown(False) # Use custom indicator
        self.image_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.image_tree.setStyleSheet("QTreeWidget { border: 1px solid darkgray; border-radius: 0px; }")
        self.image_tree.setHeaderHidden(True)
        left_layout.addWidget(self.image_tree)
        # Action buttons grid
        button_layout = QGridLayout()
        self.load_image_button = QPushButton() # Text set in retranslate_ui
        button_layout.addWidget(self.load_image_button, 0, 0)
        self.capture_image_button = QPushButton() # Text set in retranslate_ui
        button_layout.addWidget(self.capture_image_button, 0, 1)
        self.delete_item_button = QPushButton() # Text set in retranslate_ui
        button_layout.addWidget(self.delete_item_button, 1, 0)
        self.create_folder_button = QPushButton() # Text set in retranslate_ui
        button_layout.addWidget(self.create_folder_button, 1, 1)
        self.move_in_button = QPushButton() # Text set in retranslate_ui
        button_layout.addWidget(self.move_in_button, 2, 0)
        self.move_out_button = QPushButton() # Text set in retranslate_ui
        button_layout.addWidget(self.move_out_button, 2, 1)
        # Connect action buttons
        self.load_image_button.clicked.connect(self.load_images_dialog)
        self.capture_image_button.clicked.connect(self.captureImageRequested.emit)
        self.delete_item_button.clicked.connect(self.on_delete_button_clicked)
        self.move_up_button.clicked.connect(self.move_item_up)
        self.move_down_button.clicked.connect(self.move_item_down)
        self.create_folder_button.clicked.connect(self.createFolderRequested.emit)
        self.move_in_button.clicked.connect(self.moveItemIntoFolderRequested.emit)
        self.move_out_button.clicked.connect(self.moveItemOutOfFolderRequested.emit)
        left_layout.addLayout(button_layout)
        content_layout.addWidget(left_frame, 1) # Left panel takes 1 part of stretch

        # --- Right Panel (Tabs & Item Settings) ---
        right_frame = QFrame()
        right_layout = QVBoxLayout(right_frame)
        # Tab Widget
        self.preview_tabs = QTabWidget()

        # Image Preview Tab
        self.main_preview_widget = QWidget()
        main_preview_layout = QVBoxLayout(self.main_preview_widget)
        self.preview_label = InteractivePreviewLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        main_preview_layout.addWidget(self.preview_label)
        self.preview_tabs.addTab(self.main_preview_widget, "") # Tab text set in retranslate_ui

        # Recognition Area Tab
        rec_area_widget = QWidget()
        rec_area_layout = QVBoxLayout(rec_area_widget)
        rec_area_buttons_layout = QHBoxLayout()
        self.set_rec_area_button_main_ui = QPushButton() # Text set in retranslate_ui
        self.clear_rec_area_button_main_ui = QPushButton() # Text set in retranslate_ui
        rec_area_buttons_layout.addWidget(self.set_rec_area_button_main_ui)
        rec_area_buttons_layout.addWidget(self.clear_rec_area_button_main_ui)
        rec_area_layout.addLayout(rec_area_buttons_layout)
        self.rec_area_preview_label = ScaledPixmapLabel() # Text/Pixmap set dynamically
        self.rec_area_preview_label.setAlignment(Qt.AlignCenter)
        rec_area_layout.addWidget(self.rec_area_preview_label)
        self.preview_tabs.addTab(rec_area_widget, "") # Tab text set in retranslate_ui

        # Log Tab
        log_widget = QWidget()
        log_layout = QVBoxLayout(log_widget)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)
        self.preview_tabs.addTab(log_widget, "") # Tab text set in retranslate_ui

        # Auto Scale Tab
        self.auto_scale_group = QGroupBox() # Title set in retranslate_ui
        auto_scale_layout = QGridLayout(self.auto_scale_group)
        self.auto_scale_widgets['use_window_scale'] = QCheckBox() # Text set in retranslate_ui
        auto_scale_layout.addWidget(self.auto_scale_widgets['use_window_scale'], 0, 0, 1, 2)
        self.auto_scale_widgets['enabled'] = QCheckBox() # Text set in retranslate_ui
        auto_scale_layout.addWidget(self.auto_scale_widgets['enabled'], 1, 0, 1, 2)
        self.auto_scale_center_label = QLabel() # Text set in retranslate_ui
        auto_scale_layout.addWidget(self.auto_scale_center_label, 2, 0)
        self.auto_scale_widgets['center'] = QDoubleSpinBox(); self.auto_scale_widgets['center'].setRange(0.5, 2.0); self.auto_scale_widgets['center'].setSingleStep(0.1)
        auto_scale_layout.addWidget(self.auto_scale_widgets['center'], 2, 1)
        self.auto_scale_range_label = QLabel() # Text set in retranslate_ui
        auto_scale_layout.addWidget(self.auto_scale_range_label, 2, 2)
        self.auto_scale_widgets['range'] = QDoubleSpinBox(); self.auto_scale_widgets['range'].setRange(0.1, 0.5); self.auto_scale_widgets['range'].setSingleStep(0.05)
        auto_scale_layout.addWidget(self.auto_scale_widgets['range'], 2, 3)
        self.auto_scale_steps_label = QLabel() # Text set in retranslate_ui
        auto_scale_layout.addWidget(self.auto_scale_steps_label, 3, 0)
        self.auto_scale_widgets['steps'] = QSpinBox(); self.auto_scale_widgets['steps'].setRange(3, 11); self.auto_scale_widgets['steps'].setSingleStep(2)
        auto_scale_layout.addWidget(self.auto_scale_widgets['steps'], 3, 1)
        self.auto_scale_info_label = QLabel() # Text set dynamically
        auto_scale_layout.addWidget(self.auto_scale_info_label, 3, 2, 1, 2)
        scale_info_layout = QHBoxLayout()
        self.current_best_scale_label = QLabel() # Text set dynamically
        font = self.current_best_scale_label.font(); font.setBold(True)
        self.current_best_scale_label.setFont(font); self.current_best_scale_label.setStyleSheet("color: gray;")
        scale_info_layout.addWidget(self.current_best_scale_label)
        scale_info_layout.addSpacerItem(QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        auto_scale_layout.addLayout(scale_info_layout, 4, 0, 1, 4)
        self.as_desc_label = QLabel() # Text set in retranslate_ui
        self.as_desc_label.setWordWrap(True); self.as_desc_label.setStyleSheet("font-size: 11px; color: #555555;"); self.as_desc_label.setMinimumWidth(0)
        auto_scale_layout.addWidget(self.as_desc_label, 5, 0, 1, 4)
        self.auto_scale_group.setFlat(True) # Remove groupbox border
        self.preview_tabs.addTab(self.auto_scale_group, "") # Tab text set in retranslate_ui

        # App Settings Tab (Scrollable)
        app_settings_scroll_area = QScrollArea()
        app_settings_scroll_area.setWidgetResizable(True)
        app_settings_scroll_area.setStyleSheet("QScrollArea { border: 0; }") # Remove border
        app_settings_widget = QWidget()
        app_settings_layout = QVBoxLayout(app_settings_widget)
        app_settings_layout.setSpacing(10)
        app_settings_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        # Grayscale
        self.app_settings_widgets['grayscale_matching'] = QCheckBox() # Text set in retranslate_ui
        app_settings_layout.addWidget(self.app_settings_widgets['grayscale_matching'])
        self.gs_desc_label = QLabel() # Text set in retranslate_ui
        self.gs_desc_label.setWordWrap(True); self.gs_desc_label.setStyleSheet("font-size: 11px; color: #555555; padding-left: 20px;")
        app_settings_layout.addWidget(self.gs_desc_label)
        # DXCam
        self.app_settings_widgets['capture_method'] = QCheckBox() # Text set in retranslate_ui
        self.app_settings_widgets['capture_method'].setEnabled(DXCAM_AVAILABLE)
        app_settings_layout.addWidget(self.app_settings_widgets['capture_method'])
        self.dxcam_desc_label = QLabel() # Text set in retranslate_ui
        self.dxcam_desc_label.setWordWrap(True); self.dxcam_desc_label.setStyleSheet("font-size: 11px; color: #555555; padding-left: 20px;")
        app_settings_layout.addWidget(self.dxcam_desc_label)
        # Eco Mode
        self.app_settings_widgets['eco_mode_enabled'] = QCheckBox() # Text set in retranslate_ui
        app_settings_layout.addWidget(self.app_settings_widgets['eco_mode_enabled'])
        self.eco_desc_label = QLabel() # Text set in retranslate_ui
        self.eco_desc_label.setWordWrap(True); self.eco_desc_label.setStyleSheet("font-size: 11px; color: #555555; padding-left: 20px;")
        app_settings_layout.addWidget(self.eco_desc_label)
        # Frame Skip
        fs_layout = QHBoxLayout(); self.fs_label = QLabel() # Text set in retranslate_ui
        fs_layout.addWidget(self.fs_label)
        self.app_settings_widgets['frame_skip_rate'] = QSpinBox(); self.app_settings_widgets['frame_skip_rate'].setRange(1, 20)
        fs_layout.addWidget(self.app_settings_widgets['frame_skip_rate']); fs_layout.addStretch()
        app_settings_layout.addLayout(fs_layout)
        self.fs_desc_label = QLabel() # Text set in retranslate_ui
        self.fs_desc_label.setWordWrap(True); self.fs_desc_label.setStyleSheet("font-size: 11px; color: #555555; padding-left: 20px;")
        app_settings_layout.addWidget(self.fs_desc_label)
        # OpenCL
        self.app_settings_widgets['use_opencl'] = QCheckBox() # Text set in retranslate_ui
        self.app_settings_widgets['use_opencl'].setEnabled(OPENCL_AVAILABLE)
        app_settings_layout.addWidget(self.app_settings_widgets['use_opencl'])
        self.opencl_desc_label = QLabel() # Text set in retranslate_ui
        self.opencl_desc_label.setWordWrap(True); self.opencl_desc_label.setStyleSheet("font-size: 11px; color: #555555; padding-left: 20px;")
        app_settings_layout.addWidget(self.opencl_desc_label)
        # Stability Check
        self.stability_group = QGroupBox() # Title set in retranslate_ui
        stability_layout = QGridLayout(self.stability_group)
        self.app_settings_widgets['stability_check_enabled'] = QCheckBox() # Text set in retranslate_ui
        stability_layout.addWidget(self.app_settings_widgets['stability_check_enabled'], 0, 0)
        threshold_layout = QHBoxLayout(); self.stability_threshold_label = QLabel() # Text set in retranslate_ui
        threshold_layout.addWidget(self.stability_threshold_label)
        self.app_settings_widgets['stability_threshold'] = QSpinBox(); self.app_settings_widgets['stability_threshold'].setRange(0, 20)
        threshold_layout.addWidget(self.app_settings_widgets['stability_threshold']); threshold_layout.addStretch()
        stability_layout.addLayout(threshold_layout, 0, 1)
        self.stability_desc_label = QLabel() # Text set in retranslate_ui
        self.stability_desc_label.setWordWrap(True); self.stability_desc_label.setStyleSheet("font-size: 11px; color: #555555;")
        stability_layout.addWidget(self.stability_desc_label, 1, 0, 1, 2)
        app_settings_layout.addWidget(self.stability_group)
        # Lightweight Mode
        self.lw_mode_group = QGroupBox() # Title set in retranslate_ui
        lw_mode_layout = QVBoxLayout(self.lw_mode_group)
        self.app_settings_widgets['lightweight_mode_enabled'] = QCheckBox() # Text set in retranslate_ui
        lw_mode_layout.addWidget(self.app_settings_widgets['lightweight_mode_enabled'])
        preset_layout = QHBoxLayout(); self.lw_mode_preset_label = QLabel() # Text set in retranslate_ui
        preset_layout.addWidget(self.lw_mode_preset_label); self.app_settings_widgets['lightweight_mode_preset'] = QComboBox() # Items added in retranslate_ui
        preset_layout.addWidget(self.app_settings_widgets['lightweight_mode_preset']); preset_layout.addStretch()
        lw_mode_layout.addLayout(preset_layout)
        self.lw_mode_desc_label = QLabel() # Text set in retranslate_ui
        self.lw_mode_desc_label.setWordWrap(True); self.lw_mode_desc_label.setStyleSheet("font-size: 11px; color: #555555; padding-left: 20px;")
        lw_mode_layout.addWidget(self.lw_mode_desc_label); app_settings_layout.addWidget(self.lw_mode_group)
        # Spacer before language setting
        app_settings_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))
        # Language Setting
        self.lang_label = QLabel() # Text set in retranslate_ui
        lang_layout = QHBoxLayout(); lang_layout.addWidget(self.lang_label); self.language_combo = QComboBox() # Items added in retranslate_ui
        lang_layout.addWidget(self.language_combo); lang_layout.addStretch(); app_settings_layout.addLayout(lang_layout)
        # Set scroll area widget and add tab
        app_settings_scroll_area.setWidget(app_settings_widget)
        self.preview_tabs.addTab(app_settings_scroll_area, "") # Tab text set in retranslate_ui

        # Usage Tab
        usage_widget = QWidget()
        usage_layout = QVBoxLayout(usage_widget)
        self.usage_text = QTextEdit()
        self.usage_text.setReadOnly(True)
        usage_layout.addWidget(self.usage_text) # Content set in retranslate_ui
        usage_widget.setLayout(usage_layout)
        self.preview_tabs.addTab(usage_widget, "") # Tab text set in retranslate_ui

        right_layout.addWidget(self.preview_tabs, 2) # Tabs take 2 parts of stretch

        # --- Item Settings Group ---
        self.item_settings_group = QGroupBox() # Title set in retranslate_ui
        item_settings_layout = QGridLayout(self.item_settings_group)
        item_settings_layout.setColumnStretch(1, 1); item_settings_layout.setColumnStretch(3, 1) # Allow spin boxes to expand
        # Threshold
        self.item_threshold_label = QLabel() # Text set in retranslate_ui
        item_settings_layout.addWidget(self.item_threshold_label, 0, 0)
        self.item_settings_widgets['threshold'] = QDoubleSpinBox(); self.item_settings_widgets['threshold'].setRange(0.5, 1.0); self.item_settings_widgets['threshold'].setSingleStep(0.01); self.item_settings_widgets['threshold'].setValue(0.8)
        item_settings_layout.addWidget(self.item_settings_widgets['threshold'], 0, 1)
        # Interval
        self.item_interval_label = QLabel() # Text set in retranslate_ui
        item_settings_layout.addWidget(self.item_interval_label, 0, 2)
        self.item_settings_widgets['interval_time'] = QDoubleSpinBox(); self.item_settings_widgets['interval_time'].setRange(0.1, 10.0); self.item_settings_widgets['interval_time'].setSingleStep(0.1); self.item_settings_widgets['interval_time'].setValue(1.5)
        item_settings_layout.addWidget(self.item_settings_widgets['interval_time'], 0, 3)
        # Backup Click
        self.item_settings_widgets['backup_click'] = QCheckBox() # Text set in retranslate_ui
        item_settings_layout.addWidget(self.item_settings_widgets['backup_click'], 1, 0)
        self.item_settings_widgets['backup_time'] = QDoubleSpinBox(); self.item_settings_widgets['backup_time'].setRange(1.0, 600.0); self.item_settings_widgets['backup_time'].setSingleStep(1.0); self.item_settings_widgets['backup_time'].setValue(300.0)
        item_settings_layout.addWidget(self.item_settings_widgets['backup_time'], 1, 1)
        # Debounce
        self.item_debounce_label = QLabel() # Text set in retranslate_ui
        item_settings_layout.addWidget(self.item_debounce_label, 1, 2)
        self.item_settings_widgets['debounce_time'] = QDoubleSpinBox(); self.item_settings_widgets['debounce_time'].setRange(0.0, 10.0); self.item_settings_widgets['debounce_time'].setSingleStep(0.1); self.item_settings_widgets['debounce_time'].setValue(0.0)
        item_settings_layout.addWidget(self.item_settings_widgets['debounce_time'], 1, 3)
        # Click Type
        click_type_layout = QHBoxLayout()
        self.item_settings_widgets['point_click'] = QCheckBox() # Text set in retranslate_ui
        self.item_settings_widgets['range_click'] = QCheckBox() # Text set in retranslate_ui
        self.item_settings_widgets['random_click'] = QCheckBox() # Text set in retranslate_ui
        click_type_layout.addWidget(self.item_settings_widgets['point_click'])
        click_type_layout.addWidget(self.item_settings_widgets['range_click'])
        click_type_layout.addWidget(self.item_settings_widgets['random_click'])
        item_settings_layout.addLayout(click_type_layout, 2, 0, 1, 4)
        # Separator
        separator = QFrame(); separator.setFrameShape(QFrame.Shape.HLine); separator.setFrameShadow(QFrame.Shadow.Sunken)
        item_settings_layout.addWidget(separator, 3, 0, 1, 4)
        # ROI Settings
        self.item_settings_widgets['roi_enabled'] = QCheckBox() # Text set in retranslate_ui
        item_settings_layout.addWidget(self.item_settings_widgets['roi_enabled'], 4, 0)
        roi_mode_layout = QHBoxLayout()
        self.item_settings_widgets['roi_mode_fixed'] = QRadioButton() # Text set in retranslate_ui
        self.item_settings_widgets['roi_mode_variable'] = QRadioButton() # Text set in retranslate_ui
        self.roi_mode_group = QButtonGroup(self) # Group for exclusive selection
        self.roi_mode_group.addButton(self.item_settings_widgets['roi_mode_fixed'])
        self.roi_mode_group.addButton(self.item_settings_widgets['roi_mode_variable'])
        roi_mode_layout.addWidget(self.item_settings_widgets['roi_mode_fixed'])
        roi_mode_layout.addWidget(self.item_settings_widgets['roi_mode_variable'])
        item_settings_layout.addLayout(roi_mode_layout, 4, 1)
        self.item_settings_widgets['set_roi_variable_button'] = QPushButton() # Text set in retranslate_ui
        self.item_settings_widgets['set_roi_variable_button'].setCheckable(True) # Make it a toggle button
        item_settings_layout.addWidget(self.item_settings_widgets['set_roi_variable_button'], 4, 2, 1, 2)

        right_layout.addWidget(self.item_settings_group, 1) # Item settings take 1 part of stretch
        content_layout.addWidget(right_frame, 2) # Right panel takes 2 parts
        main_layout.addWidget(content_frame)

    def retranslate_ui(self):
        """Sets or updates all translatable text in the UI based on the current language."""
        lm = self.locale_manager.tr

        self.setWindowTitle(lm("window_title"))
        # Header buttons (Monitor button text changes based on state in set_status)
        self.perf_monitor_button.setText(lm("performance_monitor_button"))
        self.header_rec_area_button.setText(lm("recognition_area_button"))
        self.toggle_minimal_ui_button.setText(lm("minimal_ui_button") if not self.is_minimal_mode else lm("minimal_ui_button_stop"))
        self.open_image_folder_button.setText(lm("open_image_folder_button"))
        self.open_image_folder_button.setToolTip(lm("open_image_folder_tooltip"))
        self.monitor_button.setToolTip(lm("monitor_button_tooltip"))

        # Status Label (Initial text, updated by set_status)
        if not self.core_engine or not self.core_engine.is_monitoring:
            self.status_label.setText(lm("status_label_idle"))
        else:
             self.status_label.setText(lm("status_label_monitoring")) # Should be updated by set_status anyway

        # Left Panel
        self.list_title_label.setText(lm("list_title"))
        self.move_up_button.setText(lm("move_up_button"))
        self.move_down_button.setText(lm("move_down_button"))
        self.load_image_button.setText(lm("add_image_button"))
        self.capture_image_button.setText(lm("capture_image_button"))
        self.delete_item_button.setText(lm("delete_item_button"))
        self.create_folder_button.setText(lm("create_folder_button"))
        self.move_in_button.setText(lm("move_in_button"))
        self.move_out_button.setText(lm("move_out_button"))

        # --- Right Panel Tabs ---
        self.preview_tabs.setTabText(self.preview_tabs.indexOf(self.main_preview_widget), lm("tab_preview"))
        # Rec Area Tab
        rec_area_tab_index = self.preview_tabs.indexOf(self.rec_area_preview_label.parentWidget())
        if rec_area_tab_index != -1:
            self.preview_tabs.setTabText(rec_area_tab_index, lm("tab_rec_area"))
        self.set_rec_area_button_main_ui.setText(lm("recognition_area_button"))
        self.clear_rec_area_button_main_ui.setText(lm("rec_area_clear_button"))
        self.rec_area_preview_label.setText(lm("rec_area_preview_text")) # Default text if no preview

        # Log Tab
        log_tab_index = self.preview_tabs.indexOf(self.log_text.parentWidget())
        if log_tab_index != -1:
             self.preview_tabs.setTabText(log_tab_index, lm("tab_log"))

        # Auto Scale Tab
        self.preview_tabs.setTabText(self.preview_tabs.indexOf(self.auto_scale_group), lm("tab_auto_scale"))
        self.auto_scale_group.setTitle(lm("tab_auto_scale"))
        self.auto_scale_widgets['use_window_scale'].setText(lm("auto_scale_use_window"))
        self.auto_scale_widgets['use_window_scale'].setToolTip(lm("auto_scale_use_window_tooltip"))
        self.auto_scale_widgets['enabled'].setText(lm("auto_scale_enable_search"))
        self.auto_scale_center_label.setText(lm("auto_scale_center"))
        self.auto_scale_range_label.setText(lm("auto_scale_range"))
        self.auto_scale_steps_label.setText(lm("auto_scale_steps"))
        self.as_desc_label.setText(lm("auto_scale_desc"))
        # Dynamic labels updated elsewhere: auto_scale_info_label, current_best_scale_label

        # App Settings Tab
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
        self.app_settings_widgets['use_opencl'].setText(lm("app_setting_opencl"))
        self.opencl_desc_label.setText(lm("app_setting_opencl_desc"))
        self.stability_group.setTitle(lm("app_setting_stability_group"))
        self.app_settings_widgets['stability_check_enabled'].setText(lm("app_setting_stability_enable"))
        self.stability_threshold_label.setText(lm("app_setting_stability_threshold"))
        self.stability_desc_label.setText(lm("app_setting_stability_desc"))
        self.lw_mode_group.setTitle(lm("app_setting_lw_mode_group"))
        self.app_settings_widgets['lightweight_mode_enabled'].setText(lm("app_setting_lw_mode_enable"))
        self.lw_mode_preset_label.setText(lm("app_setting_lw_mode_preset"))
        # Lightweight preset combo box items (update texts while preserving selection)
        current_preset_index = self.app_settings_widgets['lightweight_mode_preset'].currentIndex()
        self.app_settings_widgets['lightweight_mode_preset'].blockSignals(True)
        self.app_settings_widgets['lightweight_mode_preset'].clear()
        # Add items using translated keys
        self.app_settings_widgets['lightweight_mode_preset'].addItems([
            lm("app_setting_lw_mode_preset_standard"),
            lm("app_setting_lw_mode_preset_performance"),
            lm("app_setting_lw_mode_preset_ultra")
        ])
        # Restore selection if possible
        if current_preset_index != -1 and current_preset_index < self.app_settings_widgets['lightweight_mode_preset'].count():
             self.app_settings_widgets['lightweight_mode_preset'].setCurrentIndex(current_preset_index)
        self.app_settings_widgets['lightweight_mode_preset'].blockSignals(False)
        self.lw_mode_desc_label.setText(lm("app_setting_lw_mode_desc"))

        # Language setting
        self.lang_label.setText(lm("app_setting_language_label"))
        # Language combo box items (update while preserving selection)
        self.available_langs.clear() # Clear previous language list
        current_lang_selection_text = self.language_combo.currentText() # Store current selection text
        self.language_combo.blockSignals(True)
        self.language_combo.clear()
        selected_lang_code = self.locale_manager.current_lang # Get currently active language code
        found_current = False
        try:
            # Populate available_langs and ComboBox
            for file in self.locale_manager.locales_dir.glob("*.json"):
                lang_code = file.stem
                lang_name = lang_code # Default display name
                try:
                    with open(file, 'r', encoding='utf-8') as f:
                        lang_data = json.load(f)
                        lang_name = lang_data.get("language_name", lang_code)
                except Exception:
                    pass # Use code as name if JSON load fails
                self.available_langs[lang_name] = lang_code
                self.language_combo.addItem(lang_name)
                if lang_code == selected_lang_code:
                    current_lang_selection_text = lang_name # Update selection text if code matches
                    found_current = True
        except Exception as e:
            print(f"Error loading languages for ComboBox: {e}")
        # Try to restore selection
        select_index = self.language_combo.findText(current_lang_selection_text)
        if select_index != -1:
            self.language_combo.setCurrentIndex(select_index)
        elif found_current: # Fallback if previous text not found but current code was
             pass # Already set by loop finding the current code
        self.language_combo.blockSignals(False)


        # Usage Tab
        usage_tab_index = self.preview_tabs.indexOf(self.usage_text.parentWidget())
        if usage_tab_index != -1:
             self.preview_tabs.setTabText(usage_tab_index, lm("tab_usage"))
        # Load Usage HTML based on current language
        try:
            usage_html_path_str = lm("usage_html_path") # Get path from JSON
            # Construct full path relative to main script/executable directory
            base_path = Path(os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else __file__))
            usage_html_path = base_path / usage_html_path_str
            if usage_html_path.exists():
                with open(usage_html_path, 'r', encoding='utf-8') as f:
                    self.usage_text.setHtml(f.read())
            else:
                self.usage_text.setText(f"Usage file not found: {usage_html_path}")
        except Exception as e:
            self.usage_text.setText(f"Error loading usage file ({usage_html_path_str}): {e}")


        # --- Item Settings Group ---
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
        # ROI button text managed by PreviewModeManager
        self.item_settings_widgets['set_roi_variable_button'].setToolTip(lm("item_setting_roi_button_tooltip"))

        # Force update dynamic labels affected by language change
        self.update_auto_scale_info()
        # Update status bar according to current core engine state
        if self.core_engine:
            status_key = "monitoring" if self.core_engine.is_monitoring else "idle"
            # Retrieve color associated with status if needed (optional)
            self.set_status(status_key, "blue" if status_key == "monitoring" else "green")
        else:
             self.set_status("idle", "green") # Default if core_engine not ready
        # Update scale label based on current scale value
        current_scale = 0.0
        if self.core_engine and self.core_engine.current_window_scale is not None:
            current_scale = self.core_engine.current_window_scale
        self.on_window_scale_calculated(current_scale)
        # ★★★ [修正] 言語変更時にボタンの状態も更新 ★★★
        self._update_capture_button_state()


    def is_dark_mode(self):
        palette = self.palette()
        window_color = palette.color(QPalette.ColorRole.Window)
        text_color = palette.color(QPalette.ColorRole.WindowText)
        # Basic check: darker background than text usually means dark mode
        return window_color.lightness() < text_color.lightness()

    def load_app_settings_to_ui(self):
        """Loads settings from self.app_config into the UI widgets."""
        # Auto Scale
        as_conf = self.app_config.get('auto_scale', {})
        self.auto_scale_widgets['use_window_scale'].setChecked(as_conf.get('use_window_scale', True))
        self.auto_scale_widgets['enabled'].setChecked(as_conf.get('enabled', False))
        self.auto_scale_widgets['center'].setValue(as_conf.get('center', 1.0))
        self.auto_scale_widgets['range'].setValue(as_conf.get('range', 0.2))
        self.auto_scale_widgets['steps'].setValue(as_conf.get('steps', 5))

        # App Settings
        self.app_settings_widgets['capture_method'].setChecked(self.app_config.get('capture_method', 'dxcam') == 'dxcam')
        self.app_settings_widgets['frame_skip_rate'].setValue(self.app_config.get('frame_skip_rate', 2))
        self.app_settings_widgets['grayscale_matching'].setChecked(self.app_config.get('grayscale_matching', False))
        self.app_settings_widgets['use_opencl'].setChecked(self.app_config.get('use_opencl', True))

        eco_conf = self.app_config.get('eco_mode', {})
        self.app_settings_widgets['eco_mode_enabled'].setChecked(eco_conf.get('enabled', True)) # Default True

        stability_conf = self.app_config.get('screen_stability_check', {})
        self.app_settings_widgets['stability_check_enabled'].setChecked(stability_conf.get('enabled', True))
        self.app_settings_widgets['stability_threshold'].setValue(stability_conf.get('threshold', 8))

        lw_conf = self.app_config.get('lightweight_mode', {})
        self.app_settings_widgets['lightweight_mode_enabled'].setChecked(lw_conf.get('enabled', False)) # Default False

        # Set Lightweight preset ComboBox based on internal name from config
        # ★★★ 修正: 内部名(英語)を読み込み、それに対応する翻訳キーで表示名を設定 ★★★
        preset_internal_name = lw_conf.get('preset', 'standard') # Get internal name (e.g., "standard")
        preset_display_key = f"app_setting_lw_mode_preset_{preset_internal_name}" # Construct translation key (e.g., "app_setting_lw_mode_preset_standard")
        preset_display_text = self.locale_manager.tr(preset_display_key) # Get translated display name

        # Fallback if translation key is missing (e.g., key mismatch)
        if preset_display_text == preset_display_key:
             preset_display_text = self.locale_manager.tr("app_setting_lw_mode_preset_standard")

        self.app_settings_widgets['lightweight_mode_preset'].setCurrentText(preset_display_text)
        # ★★★ 修正ここまで ★★★

        # Language ComboBox selection is handled by retranslate_ui based on locale_manager.current_lang

        # Update UI states based on loaded settings
        self.update_auto_scale_info()
        self.update_dependent_widgets_state()

    def update_dependent_widgets_state(self):
        """Enable/disable widgets based on the state of others."""
        is_lw_mode_enabled = self.app_settings_widgets['lightweight_mode_enabled'].isChecked()
        # Auto scale group disabled if lightweight mode is on
        self.auto_scale_group.setEnabled(not is_lw_mode_enabled)
        # Lightweight preset combo enabled only if lightweight mode is on
        self.app_settings_widgets['lightweight_mode_preset'].setEnabled(is_lw_mode_enabled)

        is_stability_enabled = self.app_settings_widgets['stability_check_enabled'].isChecked()
        # Stability threshold spinbox enabled only if stability check is on
        self.app_settings_widgets['stability_threshold'].setEnabled(is_stability_enabled)

        # Frame skip spinbox disabled if lightweight mode is on (as it's controlled by preset)
        is_fs_user_configurable = not is_lw_mode_enabled
        self.app_settings_widgets['frame_skip_rate'].setEnabled(is_fs_user_configurable)

    def get_auto_scale_settings(self) -> dict:
        """Reads auto scale settings from UI widgets."""
        return {
            "use_window_scale": self.auto_scale_widgets['use_window_scale'].isChecked(),
            "enabled": self.auto_scale_widgets['enabled'].isChecked(),
            "center": self.auto_scale_widgets['center'].value(),
            "range": self.auto_scale_widgets['range'].value(),
            "steps": self.auto_scale_widgets['steps'].value()
        }

    def update_auto_scale_info(self):
        """Updates the label showing the scale search range."""
        lm = self.locale_manager.tr
        if self.auto_scale_widgets['enabled'].isChecked():
            center = self.auto_scale_widgets['center'].value()
            range_ = self.auto_scale_widgets['range'].value()
            steps = self.auto_scale_widgets['steps'].value()
            # Calculate search range based on steps
            if steps > 1:
                scales = np.linspace(center - range_, center + range_, steps)
                self.auto_scale_info_label.setText(lm("auto_scale_info_searching", f"{scales[0]:.3f}", f"{scales[-1]:.3f}"))
            else: # Handle steps=1 case
                 self.auto_scale_info_label.setText(lm("auto_scale_info_searching", f"{center:.3f}", f"{center:.3f}"))
            self.auto_scale_info_label.setStyleSheet("color: blue;")
        else:
            self.auto_scale_info_label.setText(lm("auto_scale_info_disabled"))
            self.auto_scale_info_label.setStyleSheet("color: gray;")

    def on_app_settings_changed(self):
        """Saves app settings when UI widgets are changed."""
        lm = self.locale_manager.tr
        self.app_config['auto_scale'] = self.get_auto_scale_settings()
        self.app_config['capture_method'] = 'dxcam' if self.app_settings_widgets['capture_method'].isChecked() else 'mss'
        self.app_config['frame_skip_rate'] = self.app_settings_widgets['frame_skip_rate'].value()
        self.app_config['grayscale_matching'] = self.app_settings_widgets['grayscale_matching'].isChecked()
        self.app_config['use_opencl'] = self.app_settings_widgets['use_opencl'].isChecked()
        self.app_config['eco_mode'] = {
            "enabled": self.app_settings_widgets['eco_mode_enabled'].isChecked()
        }
        self.app_config['screen_stability_check'] = {
            "enabled": self.app_settings_widgets['stability_check_enabled'].isChecked(),
            "threshold": self.app_settings_widgets['stability_threshold'].value()
        }

        # ★★★ 修正: 表示名(日本語)から内部名(英語)に変換して保存 ★★★
        preset_display_text = self.app_settings_widgets['lightweight_mode_preset'].currentText()
        preset_internal_name = "standard" # Default

        # Find the internal name corresponding to the display text
        if preset_display_text == lm("app_setting_lw_mode_preset_standard"):
            preset_internal_name = "standard"
        elif preset_display_text == lm("app_setting_lw_mode_preset_performance"):
            preset_internal_name = "performance"
        elif preset_display_text == lm("app_setting_lw_mode_preset_ultra"):
            preset_internal_name = "ultra"
        # If display text doesn't match known translations, keep default "standard"

        self.app_config['lightweight_mode'] = {
            "enabled": self.app_settings_widgets['lightweight_mode_enabled'].isChecked(),
            "preset": preset_internal_name # 英語の内部名を保存
        }
        # ★★★ 修正ここまで ★★★

        # Language setting is saved separately in on_language_changed

        self.config_manager.save_app_config(self.app_config)
        # Update related UI elements
        self.update_auto_scale_info()
        self.update_dependent_widgets_state()
        # Notify core engine of changes
        self.appConfigChanged.emit()


    def connect_signals(self):
        """Connects signals from UI widgets to appropriate slots."""
        # Prevent double connections
        if hasattr(self, '_signals_connected') and self._signals_connected:
            return

        # Header buttons
        self.monitor_button.clicked.connect(self.toggle_monitoring)
        self.perf_monitor_button.clicked.connect(self.openPerformanceMonitorRequested.emit)
        # header_rec_area_button connected in setup_ui
        self.toggle_minimal_ui_button.clicked.connect(self.toggle_minimal_ui_mode)
        self.open_image_folder_button.clicked.connect(self.open_image_folder)

        # Tree widget and associated buttons
        self.image_tree.itemSelectionChanged.connect(self.on_image_tree_selection_changed)
        self.image_tree.itemClicked.connect(self.on_image_tree_item_clicked)
        self.image_tree.customContextMenuRequested.connect(self.on_tree_context_menu)
        self.image_tree.orderUpdated.connect(self.orderChanged.emit)
        self.image_tree.itemsMoved.connect(self.itemsMovedIntoFolder.emit)
        # load_image_button, capture_image_button, etc. connected in setup_ui

        # Rec area tab buttons
        self.set_rec_area_button_main_ui.clicked.connect(self.setRecAreaDialog)
        if self.core_engine: # Connect clear button only if core engine exists
            self.clear_rec_area_button_main_ui.clicked.connect(self.core_engine.clear_recognition_area)
            # ★★★ [修正] CoreEngineからのスケール変更通知をボタン状態更新に接続 ★★★
            self.core_engine.windowScaleCalculated.connect(self._update_capture_button_state)

        # Item settings widgets -> trigger saving and UI updates
        for widget in self.item_settings_widgets.values():
            if isinstance(widget, QDoubleSpinBox):
                widget.valueChanged.connect(self.on_item_settings_changed)
            elif isinstance(widget, QCheckBox):
                widget.stateChanged.connect(self.on_item_settings_changed)
            elif isinstance(widget, QRadioButton):
                # Use toggled for radio buttons to catch changes reliably
                widget.toggled.connect(self.on_item_settings_changed)

        # Auto scale widgets -> trigger app config save
        for widget in list(self.auto_scale_widgets.values()):
            if isinstance(widget, QDoubleSpinBox):
                widget.valueChanged.connect(self.on_app_settings_changed)
            elif isinstance(widget, QSpinBox):
                widget.valueChanged.connect(self.on_app_settings_changed)
            elif isinstance(widget, QCheckBox):
                widget.stateChanged.connect(self.on_app_settings_changed)

        # App settings widgets -> trigger app config save
        for key, widget in self.app_settings_widgets.items():
            if isinstance(widget, QSpinBox):
                widget.valueChanged.connect(self.on_app_settings_changed)
            elif isinstance(widget, QCheckBox):
                widget.stateChanged.connect(self.on_app_settings_changed)
            elif isinstance(widget, QComboBox):
                 # Connect ComboBox changes (like lightweight preset)
                 widget.currentTextChanged.connect(self.on_app_settings_changed)

        # Language ComboBox -> trigger language change logic
        self.language_combo.currentTextChanged.connect(self.on_language_changed)
        # LocaleManager signal -> update UI text
        self.locale_manager.languageChanged.connect(self.retranslate_ui)

        # Preview interactions
        self.preview_mode_manager.modeChanged.connect(self.preview_label.set_drawing_mode)
        if self.core_engine:
            self.preview_label.settingChanged.connect(self.core_engine.on_preview_click_settings_changed)
            self.preview_label.roiSettingChanged.connect(self.core_engine.on_roi_settings_changed)
            # Timer for delayed saving of item settings
            self.save_timer.timeout.connect(self.core_engine.save_current_settings)
            # App config changes notification
            self.appConfigChanged.connect(self.core_engine.on_app_config_changed)

        self._signals_connected = True


    def on_language_changed(self, text):
        """Handles selection change in the language ComboBox."""
        selected_display_name = text
        if not selected_display_name:
            return

        # Find the language code corresponding to the selected display name
        lang_code = self.available_langs.get(selected_display_name)

        # If a valid code is found and it's different from the current language
        if lang_code and lang_code != self.locale_manager.current_lang:
            # Save the new language code to app config
            self.app_config['language'] = lang_code
            self.config_manager.save_app_config(self.app_config)
            # Load the new language file (this will trigger retranslate_ui via signal)
            self.locale_manager.load_locale(lang_code)

    def open_image_folder(self):
        """Opens the base image directory in the system's file explorer."""
        folder_path = str(self.config_manager.base_dir)
        try:
            if sys.platform == 'win32':
                os.startfile(folder_path) # For Windows
            elif sys.platform == 'darwin':
                subprocess.run(['open', folder_path]) # For macOS
            else:
                subprocess.run(['xdg-open', folder_path]) # For Linux
            self.logger.log("log_open_folder", folder_path)
        except Exception as e:
            self.logger.log("log_error_open_folder", str(e))
            QMessageBox.warning(self, self.locale_manager.tr("error_title_open_folder"), self.locale_manager.tr("error_message_open_folder", str(e)))

    def create_colored_icon(self, color, size=16):
        """Creates a QIcon with a rounded square of the specified color."""
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent) # Start with transparency
        if color == Qt.transparent:
            return QIcon(pixmap) # Return transparent icon if color is transparent

        # Draw rounded rectangle
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(Qt.black, 1) # Black outline
        painter.setPen(pen)
        brush = QBrush(color) # Fill with specified color
        painter.setBrush(brush)
        # Draw slightly inset to account for pen width
        rect = QRectF(0.5, 0.5, size - 1, size - 1)
        painter.drawRoundedRect(rect, 3.0, 3.0) # 3px corner radius
        painter.end()
        return QIcon(pixmap)

    def update_image_tree(self):
        """Reloads and repopulates the image tree view based on config."""
        lm = self.locale_manager.tr
        self.image_tree.blockSignals(True) # Prevent signals during update

        # Store expanded state and selection
        expanded_folders = set()
        selected_path, _ = self.get_selected_item_path()
        for i in range(self.image_tree.topLevelItemCount()):
            item = self.image_tree.topLevelItem(i)
            # Check if it's a folder (has children or path is directory)
            if item.childCount() > 0 or (item.data(0, Qt.UserRole) and Path(item.data(0, Qt.UserRole)).is_dir()):
                path = item.data(0, Qt.UserRole)
                if path and item.isExpanded():
                    expanded_folders.add(path)
        self.image_tree.clear()

        # Get structured list from config manager
        hierarchical_list = self.config_manager.get_hierarchical_list()
        item_to_reselect = None

        # Populate tree
        for item_data in hierarchical_list:
            if item_data['type'] == 'folder':
                folder_settings = item_data['settings']
                mode = folder_settings.get('mode', 'normal')
                folder_item = QTreeWidgetItem(self.image_tree, [lm("folder_item_prefix", item_data['name'])])
                folder_item.setData(0, Qt.UserRole, item_data['path'])
                folder_item.setFlags(folder_item.flags() | Qt.ItemIsDropEnabled) # Allow dropping onto folders

                # Set text color and icon based on folder mode
                brush = QBrush(QApplication.palette().text().color()) # Default text color
                icon_color = Qt.transparent
                if mode == 'normal': brush = QBrush(QColor("darkgray")); icon_color = QColor("darkgray")
                elif mode == 'excluded': brush = QBrush(Qt.red); icon_color = Qt.red
                elif mode == 'priority_image': brush = QBrush(Qt.blue); icon_color = Qt.blue
                elif mode == 'priority_timer': brush = QBrush(Qt.darkGreen); icon_color = Qt.green
                folder_item.setIcon(0, self.create_colored_icon(icon_color))
                folder_item.setForeground(0, brush)

                # Restore expanded state and selection
                if item_data['path'] in expanded_folders: folder_item.setExpanded(True)
                if item_data['path'] == selected_path: item_to_reselect = folder_item

                # Add child images to the folder item
                for child_data in item_data['children']:
                    child_item = QTreeWidgetItem(folder_item, [lm("image_item_prefix", child_data['name'])])
                    child_item.setData(0, Qt.UserRole, child_data['path'])
                    child_item.setForeground(0, brush) # Child inherits folder color
                    if child_data['path'] == selected_path: item_to_reselect = child_item

            elif item_data['type'] == 'image':
                # Add top-level image item
                image_item = QTreeWidgetItem(self.image_tree, [item_data['name']])
                image_item.setData(0, Qt.UserRole, item_data['path'])
                image_item.setIcon(0, self.create_colored_icon(Qt.transparent)) # No color icon for top-level images
                if item_data['path'] == selected_path: item_to_reselect = image_item

        # Restore selection
        if item_to_reselect:
            self.image_tree.setCurrentItem(item_to_reselect)

        self.image_tree.blockSignals(False) # Re-enable signals
        # Manually trigger selection change update if something was reselected
        if item_to_reselect:
             self.on_image_tree_selection_changed()

    def on_tree_context_menu(self, pos):
        """Shows context menu for tree items (folder settings or image info tooltip)."""
        item = self.image_tree.itemAt(pos)
        lm = self.locale_manager.tr
        if not item: return
        path_str = item.data(0, Qt.UserRole)
        if not path_str: return
        path = Path(path_str)

        if path.is_dir():
            # Show folder settings dialog for folders
            current_settings = self.config_manager.load_item_setting(path)
            dialog = FolderSettingsDialog(path.name, current_settings, self.locale_manager, self)
            if dialog.exec():
                new_settings = dialog.get_settings()
                self.config_manager.save_item_setting(path, new_settings)
                self.folderSettingsChanged.emit() # Notify core engine to update cache
                self.update_image_tree() # Update tree visuals

        elif path.is_file():
            # Show tooltip with info for image files
            try:
                settings = self.config_manager.load_item_setting(path)

                # Determine click mode text by translating the mode key
                click_mode_text = lm("context_menu_info_mode_unset") # Default
                if settings.get('point_click'):
                    click_mode_text = lm("context_menu_info_mode_point")
                elif settings.get('range_click'):
                    click_mode_text = lm("context_menu_info_mode_range_random") if settings.get('random_click') else lm("context_menu_info_mode_range")

                # Get other settings
                threshold = settings.get('threshold', 0.8)
                interval = settings.get('interval_time', 1.5)

                # Get image size
                pixmap = QPixmap(path_str)
                img_size_text = lm("context_menu_info_size_error") # Default
                if not pixmap.isNull():
                    # ★★★ [再修正] JSONに合わせて %s を使う ★★★
                    img_size_text = lm("context_menu_info_size", pixmap.width(), pixmap.height())

                # ★★★ [再修正] JSONのキーに合わせて %s を使い、Python側で文字列を組み立てる ★★★
                # "context_menu_info_mode": "({mode})" -> Python側で括弧を追加
                mode_str = f"({click_mode_text})"

                # "context_menu_info_threshold": "認識精度 %s"
                threshold_str = lm('context_menu_info_threshold', f'{threshold:.2f}')
                # "context_menu_info_interval": "インターバル %s秒"
                interval_str = lm('context_menu_info_interval', f'{interval:.1f}')

                tooltip_text = (
                    f"{mode_str}\n"
                    f"{threshold_str}：{interval_str}\n"
                    f"{img_size_text}"
                )
                # ★★★ [再修正] ここまで ★★★

                # Show tooltip at cursor position
                global_pos = self.image_tree.mapToGlobal(pos)
                QToolTip.showText(global_pos, tooltip_text, self.image_tree)
            except Exception as e:
                # Show error in tooltip if info retrieval fails
                global_pos = self.image_tree.mapToGlobal(pos)
                QToolTip.showText(global_pos, lm("context_menu_info_error", str(e)), self.image_tree)


    def set_tree_enabled(self, enabled: bool):
        """Enables or disables the image tree view."""
        self.image_tree.setEnabled(enabled)

    def on_cache_build_finished(self):
        """Called when the core engine finishes building the template cache."""
        self.update_image_tree() # Refresh tree view
        self.set_tree_enabled(True) # Re-enable tree
        self.is_processing_tree_change = False

    def get_selected_item_path(self):
        """Returns the path and name of the currently selected tree item."""
        selected_items = self.image_tree.selectedItems()
        if not selected_items:
            return None, None
        item = selected_items[0]
        path = item.data(0, Qt.UserRole)
        name = item.text(0)
        return path, name

    def on_image_tree_item_clicked(self, item, column):
        """Handles clicks on tree items, switching tabs if necessary."""
        if self.is_processing_tree_change or not item:
            return
        path_str = item.data(0, Qt.UserRole)
        if not path_str:
            return
        # If an image file is clicked, switch to the preview tab
        if not Path(path_str).is_dir():
            self.switch_to_preview_tab() # Use the new method


    def switch_to_preview_tab(self):
        """Switches the main tab widget to the image preview tab."""
        if self.preview_tabs and self.main_preview_widget:
            self.preview_tabs.setCurrentWidget(self.main_preview_widget)

    def on_image_tree_selection_changed(self):
        """Loads settings and preview when the tree selection changes."""
        if self.is_processing_tree_change: return
        # Reset best scale label when selection changes
        self.current_best_scale_label.setText(self.locale_manager.tr("auto_scale_best_scale_default"))
        self.current_best_scale_label.setStyleSheet("color: gray;")

        path, name = self.get_selected_item_path()
        # Ask core engine to load image and settings for the selected path
        if self.core_engine:
            self.core_engine.load_image_and_settings(path)

    def move_item_up(self):
        """Moves the selected item one position up in the tree."""
        if self.is_processing_tree_change: return
        item = self.image_tree.currentItem()
        if not item: return
        parent = item.parent()
        if parent: # Item is inside a folder
            index = parent.indexOfChild(item)
            if index > 0: # Can move up
                self.set_tree_enabled(False) # Disable tree during modification
                taken_item = parent.takeChild(index) # Remove item
                if taken_item: parent.insertChild(index - 1, taken_item) # Insert one position earlier
        else: # Item is top-level
            index = self.image_tree.indexOfTopLevelItem(item)
            if index > 0: # Can move up
                self.set_tree_enabled(False)
                taken_item = self.image_tree.takeTopLevelItem(index) # Remove item
                if taken_item: self.image_tree.insertTopLevelItem(index - 1, taken_item) # Insert one position earlier
        # Reselect and notify about order change
        self.image_tree.setCurrentItem(item)
        self.orderChanged.emit()
        self.set_tree_enabled(True) # Re-enable tree

    def move_item_down(self):
        """Moves the selected item one position down in the tree."""
        if self.is_processing_tree_change: return
        item = self.image_tree.currentItem()
        if not item: return
        parent = item.parent()
        if parent: # Item is inside a folder
            index = parent.indexOfChild(item)
            if index < parent.childCount() - 1: # Can move down
                self.set_tree_enabled(False)
                taken_item = parent.takeChild(index)
                if taken_item: parent.insertChild(index + 1, taken_item) # Insert one position later
        else: # Item is top-level
            index = self.image_tree.indexOfTopLevelItem(item)
            if index < self.image_tree.topLevelItemCount() - 1: # Can move down
                self.set_tree_enabled(False)
                taken_item = self.image_tree.takeTopLevelItem(index)
                if taken_item: self.image_tree.insertTopLevelItem(index + 1, taken_item) # Insert one position later
        # Reselect and notify about order change
        self.image_tree.setCurrentItem(item)
        self.orderChanged.emit()
        self.set_tree_enabled(True)

    def save_tree_order(self):
        """Saves the current order of items in the tree view to config files."""
        top_level_order = []
        # Save top-level order
        for i in range(self.image_tree.topLevelItemCount()):
            item = self.image_tree.topLevelItem(i)
            original_path_str = item.data(0, Qt.UserRole)
            if not original_path_str: continue # Skip if path data is missing
            original_path = Path(original_path_str)

            # Ensure path points to the correct location (base dir for top-level)
            if original_path.is_dir():
                path_str = str(original_path) # Folder path remains the same relative to base
            else:
                new_path = self.config_manager.base_dir / original_path.name
                path_str = str(new_path)
                # Update item data if path changed (e.g., after moving out of folder)
                if str(original_path) != path_str:
                    item.setData(0, Qt.UserRole, path_str)
            top_level_order.append(path_str)
        self.config_manager.save_image_order(top_level_order) # Save order to image_order.json

        # Save order within each folder
        for i in range(self.image_tree.topLevelItemCount()):
            folder_item = self.image_tree.topLevelItem(i)
            folder_path_str = folder_item.data(0, Qt.UserRole)

            if folder_path_str and Path(folder_path_str).is_dir():
                child_order_filenames = []
                for j in range(folder_item.childCount()):
                    child_item = folder_item.child(j)
                    child_path_str = child_item.data(0, Qt.UserRole)
                    if not child_path_str: continue
                    original_path = Path(child_path_str)

                    # Ensure child path points to correct location within the folder
                    if not original_path.is_dir():
                        new_path = Path(folder_path_str) / original_path.name
                        # Update item data if path changed (e.g., after moving into folder)
                        if str(original_path) != str(new_path):
                            child_item.setData(0, Qt.UserRole, str(new_path))
                        # Store just the filename for sub-order config
                        child_order_filenames.append(original_path.name)

                # Save child order to _sub_order.json within the folder
                self.config_manager.save_image_order(child_order_filenames, folder_path=folder_path_str)

    def on_delete_button_clicked(self):
        """Handles the 'Delete Selected' button click."""
        lm = self.locale_manager.tr
        selected_items = self.image_tree.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, lm("warn_delete_title"), lm("warn_delete_no_selection"))
            return

        # Prepare confirmation message
        item_names = [f"'{item.text(0).strip()}'" for item in selected_items]
        message = lm("confirm_delete_message", len(item_names), ', '.join(item_names))

        # Show confirmation dialog
        reply = QMessageBox.question(
            self,
            lm("confirm_delete_title"),
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No # Default to No
        )

        # If confirmed, emit signal with paths to delete
        if reply == QMessageBox.StandardButton.Yes:
            paths_to_delete = [item.data(0, Qt.UserRole) for item in selected_items if item.data(0, Qt.UserRole)]
            if paths_to_delete:
                self.deleteItemsRequested.emit(paths_to_delete)

    def get_current_item_settings(self):
        """Reads the current values from the item settings widgets."""
        settings = {}
        # Read values from SpinBox, CheckBox, etc.
        for key, widget in self.item_settings_widgets.items():
            if isinstance(widget, QDoubleSpinBox):
                settings[key] = widget.value()
            elif isinstance(widget, QCheckBox):
                settings[key] = widget.isChecked()
            # Radio buttons handled separately
        # Read ROI mode from RadioButtons
        if self.item_settings_widgets['roi_mode_fixed'].isChecked():
            settings['roi_mode'] = 'fixed'
        elif self.item_settings_widgets['roi_mode_variable'].isChecked():
            settings['roi_mode'] = 'variable'
        return settings

    # ★★★ 修正: チェックボックスの状態を settings_data から直接設定 ★★★
    def set_settings_from_data(self, settings_data):
        """Updates the item settings widgets based on loaded data."""
        selected_path, _ = self.get_selected_item_path()
        is_folder = selected_path and Path(selected_path).is_dir()

        all_widgets = list(self.item_settings_widgets.values()) + \
                      [self.item_settings_widgets['roi_mode_fixed'],
                       self.item_settings_widgets['roi_mode_variable']]

        enable_widgets = not is_folder and settings_data is not None
        for widget in all_widgets:
            widget.setEnabled(enable_widgets)

        if not enable_widgets:
            # --- (リセット処理) ---
            for widget in all_widgets:
                widget.blockSignals(True)
                if isinstance(widget, QDoubleSpinBox): widget.setValue(0)
                elif isinstance(widget, QCheckBox): widget.setChecked(False)
                elif isinstance(widget, QRadioButton):
                    # QButtonGroup に属している RadioButton のチェックを外す正しい方法
                    if hasattr(self, 'roi_mode_group') and self.roi_mode_group.id(widget) != -1:
                        widget.setAutoExclusive(False)
                        widget.setChecked(False)
                        widget.setAutoExclusive(True)
                    else:
                        widget.setChecked(False)
            for widget in all_widgets: widget.blockSignals(False) # ブロック解除

            if is_folder:
                if self.splash_pixmap:
                    self.preview_label.set_pixmap(self.splash_pixmap)
                    self.preview_label.setText("")
                else:
                    self.preview_label.setText(self.locale_manager.tr("preview_folder_selected"))
                    self.preview_label.set_pixmap(None)

            # PreviewModeManager を同期 (モードとUI有効/無効のみ更新)
            self.preview_mode_manager.sync_from_settings_data(None)
            self._update_roi_widgets_state() # ROIウィジェットの有効/無効更新
            self.preview_label.set_drawing_data(None) # 描画データクリア
            return
        # --- (リセット処理ここまで) ---

        # settings_data から値を読み込み、UIウィジェットに設定
        for key, value in settings_data.items():
            if key in self.item_settings_widgets:
                widget = self.item_settings_widgets[key]
                widget.blockSignals(True) # 値設定中のシグナル発生を防ぐ
                if isinstance(widget, (QDoubleSpinBox, QSpinBox)):
                    widget.setValue(value if value is not None else 0)
                elif isinstance(widget, QCheckBox):
                     # ★★★ クリック種別チェックボックスの状態を settings_data から直接設定 ★★★
                    widget.setChecked(bool(value))
                # RadioButton (ROIモード) は後で別途設定
                widget.blockSignals(False) # ブロック解除

        # ROIモード RadioButtons を設定
        roi_mode = settings_data.get('roi_mode', 'fixed')
        # RadioButtonのチェック状態を設定する際もシグナルをブロック
        self.item_settings_widgets['roi_mode_fixed'].blockSignals(True)
        self.item_settings_widgets['roi_mode_variable'].blockSignals(True)
        if roi_mode == 'variable':
            self.item_settings_widgets['roi_mode_variable'].setChecked(True)
            self.item_settings_widgets['roi_mode_fixed'].setChecked(False) # 排他なので片方をFalseに
        else: # fixed or unknown, default to fixed
            self.item_settings_widgets['roi_mode_fixed'].setChecked(True)
            self.item_settings_widgets['roi_mode_variable'].setChecked(False) # 排他なので片方をFalseに
        self.item_settings_widgets['roi_mode_fixed'].blockSignals(False)
        self.item_settings_widgets['roi_mode_variable'].blockSignals(False)

        # PreviewModeManager を同期 (モードとUI有効/無効のみ更新)
        # ※ この中で _update_ui_elements が呼ばれる
        self.preview_mode_manager.sync_from_settings_data(settings_data)
        # ROI関連ウィジェットの有効/無効状態を更新
        self._update_roi_widgets_state()
        # 描画データを更新
        self.preview_label.set_drawing_data(settings_data)


    def on_item_settings_changed(self, *args):
        """Handles changes in item settings widgets."""
        # 現在のUIから設定を取得
        current_ui_settings = self.get_current_item_settings()

        # CoreEngine に変更を通知 (CoreEngine側で排他制御が行われる)
        self.imageSettingsChanged.emit(current_ui_settings)

        # 依存ウィジェットの状態を更新 (例: ROIボタンや random_cb の有効/無効)
        # ※ PreviewModeManager 側でシグナル起点で更新されるので、ここでの _update_roi_widgets_state() 呼び出しは不要かも？
        #    ただし、念のため残しておく。PreviewModeManager 側も更新されるはず。
        self._update_roi_widgets_state()
        # ★★★ random_cb の有効状態も更新 ★★★
        is_roi_enabled = self.item_settings_widgets['roi_enabled'].isChecked()
        is_range_click_enabled = self.item_settings_widgets['range_click'].isChecked()
        self.item_settings_widgets['random_click'].setEnabled(not is_roi_enabled and is_range_click_enabled)


        # CoreEngine が保持する最新の設定データでプレビューを更新
        # ※ CoreEngine側で排他制御された後のデータが送られてくる
        if self.core_engine and self.core_engine.current_image_settings:
            # プレビューの描画データを更新
            self.preview_label.set_drawing_data(self.core_engine.current_image_settings)
            self.preview_label.update() # 明示的に再描画を要求

        # 遅延保存を要求
        self.request_save()


    def _update_roi_widgets_state(self):
        """Updates enable state of ROI mode and variable ROI button."""
        is_roi_enabled = self.item_settings_widgets['roi_enabled'].isChecked()
        is_variable_mode = self.item_settings_widgets['roi_mode_variable'].isChecked()

        # Enable mode selection only if ROI is enabled
        self.item_settings_widgets['roi_mode_fixed'].setEnabled(is_roi_enabled)
        self.item_settings_widgets['roi_mode_variable'].setEnabled(is_roi_enabled)
        # Enable variable ROI button only if ROI is enabled AND variable mode is selected
        self.item_settings_widgets['set_roi_variable_button'].setEnabled(is_roi_enabled and is_variable_mode)


    def request_save(self):
        """Starts the timer to save item settings after a short delay."""
        if self.core_engine:
            self.save_timer.start() # Timer will call core_engine.save_current_settings

    def toggle_monitoring(self):
        """Starts or stops monitoring based on the monitor button's text."""
        # Check button text against translated strings
        if self.monitor_button.text() == self.locale_manager.tr("monitor_button_start"):
            self.startMonitoringRequested.emit()
        else:
            self.stopMonitoringRequested.emit()

    def set_status(self, text_key, color="green"):
        """Updates the status label and monitor button text based on status key."""
        lm = self.locale_manager.tr
        display_text = ""
        style_color = color

        # Determine display text and button state based on the key
        if text_key == "monitoring":
            self.monitor_button.setText(lm("monitor_button_stop"))
            display_text = lm("status_label_monitoring")
            style_color = "blue"
        elif text_key == "idle":
            self.monitor_button.setText(lm("monitor_button_start"))
            display_text = lm("status_label_idle")
            style_color = "green"
            # Reset scale label when returning to idle
            self.current_best_scale_label.setText(lm("auto_scale_best_scale_default"))
            self.current_best_scale_label.setStyleSheet("color: gray;")
        elif text_key == "unstable":
            # Monitor button doesn't change text, only status label
            display_text = lm("status_label_unstable")
            style_color = "orange"
        else:
            display_text = text_key # Show the key itself if unknown

        # Update status label
        self.status_label.setText(display_text)
        self.status_label.setStyleSheet(f"font-weight: bold; color: {style_color};")

        # Update floating window status if it exists
        if self.floating_window:
            self.floating_window.update_status(display_text, style_color)

    def on_best_scale_found(self, image_path: str, scale: float):
        """Updates the best scale label if the found scale matches the selected item."""
        lm = self.locale_manager.tr
        current_selected_path, _ = self.get_selected_item_path()
        # Only update label if the found scale belongs to the currently selected image
        if image_path and image_path == current_selected_path:
            self.current_best_scale_label.setText(lm("auto_scale_best_scale_found", f"{scale:.3f}"))
            self.current_best_scale_label.setStyleSheet("color: green;")

    def on_window_scale_calculated(self, scale: float):
        """Updates the scale label and auto-scale center value based on window calculation."""
        lm = self.locale_manager.tr
        if scale > 0: # Valid scale calculated
            self.current_best_scale_label.setText(lm("auto_scale_window_scale_found", f"{scale:.3f}"))
            # Use different color to indicate it's from window calculation
            color = "white" if self.is_dark_mode() else "purple"
            self.current_best_scale_label.setStyleSheet(f"color: {color};")
            # Update the center value for potential scale search
            self.auto_scale_widgets['center'].setValue(scale)
        else: # No valid scale (e.g., rectangle selection or error)
            self.current_best_scale_label.setText(lm("auto_scale_best_scale_default"))
            self.current_best_scale_label.setStyleSheet("color: gray;")

        # ★★★ [修正] スケール値が変わったので、キャプチャボタンの状態を更新 ★★★
        self._update_capture_button_state(scale)

    # ★★★ [追加] キャプチャボタンの状態を更新するメソッド ★★★
    # ★★★ [修正] 0.0 (矩形選択/スケールなし) の場合を許可する ★★★
    def _update_capture_button_state(self, current_scale=None):
        """Enables/disables capture buttons based on the current window scale."""
        # CoreEngine から最新のスケールを取得 (引数で渡されない場合)
        if current_scale is None and self.core_engine:
            current_scale = self.core_engine.current_window_scale

        # スケールが 1.0, 0.0 (矩形選択/クリア時), または None (未設定) の場合のみキャプチャを許可
        enable_capture = (current_scale is None or current_scale == 1.0 or current_scale == 0.0)
        tooltip = "" if enable_capture else self.locale_manager.tr("warn_capture_disabled_scale")

        # メインUIのボタン
        if hasattr(self, 'main_capture_button'):
            self.main_capture_button.setEnabled(enable_capture)
            self.main_capture_button.setToolTip(tooltip)

        # 最小UI (フローティングウィンドウ) のボタン
        if self.floating_window and hasattr(self.floating_window, 'capture_button'):
            self.floating_window.capture_button.setEnabled(enable_capture)
            self.floating_window.capture_button.setToolTip(tooltip)

    def prompt_to_save_base_size(self, window_title: str) -> bool:
        """Shows a dialog asking whether to save the current window size as base."""
        lm = self.locale_manager.tr
        reply = QMessageBox.question(
            self,
            lm("base_size_prompt_title"),
            lm("base_size_prompt_message", window_title),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes # Default to Yes
        )
        return reply == QMessageBox.StandardButton.Yes

    def show_prompt_to_save_base_size(self, window_title: str):
        """Handles the signal to show the base size prompt."""
        save_as_base = self.prompt_to_save_base_size(window_title)
        # Pass the response back to the core engine
        if self.core_engine:
            self.core_engine.process_base_size_prompt_response(save_as_base)

    def show_prompt_to_apply_scale(self, scale: float):
        """Handles the signal to show the apply scale prompt."""
        lm = self.locale_manager.tr
        reply = QMessageBox.question(
            self,
            lm("apply_scale_prompt_title"),
            lm("apply_scale_prompt_message", f"{scale:.3f}"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes # Default to Yes
        )
        apply_scale = (reply == QMessageBox.StandardButton.Yes)
        # Pass the response back to the core engine
        if self.core_engine:
            self.core_engine.process_apply_scale_prompt_response(apply_scale)

    def load_images_dialog(self):
        """Opens a dialog to select image files to add."""
        lm = self.locale_manager.tr
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            lm("load_images_dialog_title"),
            str(self.config_manager.base_dir), # Start in base directory
            lm("load_images_dialog_filter") # Filter for image files
        )
        if file_paths:
            self.set_tree_enabled(False) # Disable tree during loading
            self.loadImagesRequested.emit(file_paths) # Emit signal with selected paths

    def update_image_preview(self, cv_image: np.ndarray, settings_data: dict = None):
        """Updates the image preview label with the given OpenCV image and settings."""
        # Core から受け取った最新の設定データでUI要素と描画データを更新
        self.set_settings_from_data(settings_data)

        # If image is None or empty, clear the preview or show splash
        if cv_image is None or cv_image.size == 0:
            selected_path, _ = self.get_selected_item_path()
            # フォルダ選択時は set_settings_from_data 内で処理される
            if not (selected_path and Path(selected_path).is_dir()):
                if self.splash_pixmap:
                    self.preview_label.set_pixmap(self.splash_pixmap)
                    self.preview_label.setText("") # Clear text
                else:
                    self.preview_label.setText(self.locale_manager.tr("preview_default_text"))
                    self.preview_label.set_pixmap(None) # Clear the pixmap
            self.preview_label.update()
            return

        # Convert OpenCV BGR image to QPixmap for display
        try:
            rgb_image = cv2.cvtColor(cv_image, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb_image.shape
            bytes_per_line = ch * w
            q_image = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
            pixmap = QPixmap.fromImage(q_image)
            self.preview_label.set_pixmap(pixmap) # Display the image
            self.preview_label.setText("") # Clear any previous text
        except Exception as e:
            print(f"Error converting image for preview: {e}")
            self.preview_label.setText("Preview Error")
            self.preview_label.set_pixmap(None)

        # 再描画を要求
        self.preview_label.update()


    def update_rec_area_preview(self, cv_image: np.ndarray):
        """Updates the recognition area preview label."""
        if cv_image is None or cv_image.size == 0:
            self.rec_area_preview_label.set_pixmap(None)
            self.rec_area_preview_label.setText(self.locale_manager.tr("rec_area_preview_text"))
            return

        # Convert OpenCV BGR image to QPixmap
        try:
            rgb_image = cv2.cvtColor(cv_image, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb_image.shape
            bytes_per_line = ch * w
            q_image = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
            pixmap = QPixmap.fromImage(q_image)
            self.rec_area_preview_label.set_pixmap(pixmap)
            self.rec_area_preview_label.setText("") # Clear default text
        except Exception as e:
             print(f"Error converting image for rec area preview: {e}")
             self.rec_area_preview_label.setText("Preview Error")
             self.rec_area_preview_label.set_pixmap(None)


    def update_log(self, message: str):
        """Appends a message to the log text edit."""
        self.log_text.append(message)
        # Auto-scroll to the bottom
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def closeEvent(self, event):
        """Handles the main window close event."""
        # Ensure floating window is closed
        if self.floating_window:
            self.floating_window.close()
        # Clean up core engine resources
        if self.core_engine:
            self.core_engine.cleanup()
        # Ensure monitoring is stopped (redundant if cleanup works, but safe)
        self.stopMonitoringRequested.emit()
        # Quit the application properly
        QApplication.instance().quit()
        event.accept()

    # ★★★ 復元された setRecAreaDialog メソッド ★★★
    def setRecAreaDialog(self):
        """Shows the dialog to choose recognition area selection method."""
        # Pass locale manager to the dialog
        dialog = RecAreaSelectionDialog(self.locale_manager, self)
        # Connect signal to core engine slot
        dialog.selectionMade.connect(self.setRecAreaMethodSelected)
        # Show dialog near cursor
        dialog.move(QCursor.pos())
        dialog.exec()

    def adjust_initial_size(self):
        """Adjusts the initial window size after widgets are potentially rendered."""
        self.setMinimumWidth(0) # Reset minimum width if it was constrained
        self.resize(960, 640) # Set desired initial size

    def toggle_minimal_ui_mode(self):
        """Switches between the main window and the minimal floating window."""
        lm = self.locale_manager.tr
        self.is_minimal_mode = not self.is_minimal_mode

        if self.is_minimal_mode:
            # Save current window geometries
            self.normal_ui_geometries['main'] = self.geometry()
            if self.performance_monitor and self.performance_monitor.isVisible():
                self.normal_ui_geometries['perf'] = self.performance_monitor.geometry()

            # Minimize main window and hide performance monitor
            self.showMinimized()
            if self.performance_monitor:
                self.performance_monitor.hide()

            # Create and show floating window
            self.floating_window = FloatingWindow(self.locale_manager)
            # Connect signals from floating window to main UI/core engine signals
            self.floating_window.startMonitoringRequested.connect(self.startMonitoringRequested)
            self.floating_window.stopMonitoringRequested.connect(self.stopMonitoringRequested)
            self.floating_window.captureImageRequested.connect(self.captureImageRequested)
            self.floating_window.toggleMainUIRequested.connect(self.toggle_minimal_ui_mode)
            self.floating_window.closeRequested.connect(self.toggle_minimal_ui_mode) # Closing float window reverts to normal
            self.floating_window.setRecAreaRequested.connect(self.setRecAreaDialog)

            # Connect performance updates to floating window label
            if self.performance_monitor:
                self.performance_monitor.performanceUpdated.connect(self.floating_window.update_performance)

            # Sync current status to floating window
            current_status_text = self.status_label.text()
            current_status_color = "green" # Default
            if current_status_text == lm("status_label_monitoring"): current_status_color = "blue"
            elif current_status_text == lm("status_label_unstable"): current_status_color = "orange"
            self.floating_window.update_status(current_status_text, current_status_color)

            self.floating_window.show()
            self.toggle_minimal_ui_button.setText(lm("minimal_ui_button_stop"))
            # ★★★ [修正] 最小UIモード切替時もボタン状態を更新 ★★★
            self._update_capture_button_state()
        else:
            # Switching back to normal UI
            if self.floating_window:
                # Disconnect performance update signal
                if self.performance_monitor:
                    if hasattr(self.performance_monitor, 'performanceUpdated'):
                        try:
                            # Safely disconnect
                            self.performance_monitor.performanceUpdated.disconnect(self.floating_window.update_performance)
                        except (TypeError, RuntimeError):
                            pass # Ignore if already disconnected or object deleted
                self.floating_window.close()
                self.floating_window = None

            # Restore main window and performance monitor
            self.showNormal()
            if 'main' in self.normal_ui_geometries:
                self.setGeometry(self.normal_ui_geometries['main'])

            if self.performance_monitor:
                # Show performance monitor only if it was visible before minimizing
                if 'perf' in self.normal_ui_geometries and not self.performance_monitor.isVisible():
                    self.performance_monitor.show()
                    self.performance_monitor.setGeometry(self.normal_ui_geometries['perf'])

            self.activateWindow() # Bring main window to front
            self.toggle_minimal_ui_button.setText(lm("minimal_ui_button"))
            # ★★★ [修正] 通常UIモード切替時もボタン状態を更新 ★★★
            self._update_capture_button_state()

    def on_selection_process_started(self):
        """Hides UI elements when recognition area selection starts."""
        if self.performance_monitor:
            self.performance_monitor.hide()
        if self.is_minimal_mode and self.floating_window:
            self.floating_window.hide()
        # Main window is hidden by core_engine before calling this

    def on_selection_process_finished(self):
        """Restores UI elements after recognition area selection finishes."""
        if self.is_minimal_mode:
            # Restore floating window if in minimal mode
            if self.floating_window:
                self.floating_window.show()
        else:
            # Restore performance monitor if it was open before selection
             if self.performance_monitor and 'perf' in self.normal_ui_geometries and not self.performance_monitor.isVisible():
                 self.performance_monitor.show()
        # Main window is shown by core_engine after calling this
