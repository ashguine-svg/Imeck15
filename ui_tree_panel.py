# ui_tree_panel.py
# ★★★ 修正: OCR設定ダイアログ呼び出し時に enabled 値を正しく渡す ★★★

import sys
import os
from pathlib import Path
import numpy as np
import cv2

from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QGridLayout,
    QAbstractItemView, QMessageBox, QInputDialog, QTreeWidgetItem,
    QTreeWidget, QTreeWidgetItemIterator, QApplication, QToolTip, QFileDialog,
    QLineEdit, QToolButton, QSizePolicy, QWidget, QMenu
)
from PySide6.QtGui import QPixmap, QImage, QPainter, QBrush, QColor, QIcon, QAction
from PySide6.QtCore import Qt, QObject, QSize, QRect

import qtawesome as qta

from image_tree_widget import DraggableTreeWidget
from dialogs import FolderSettingsDialog
from timer_ui import TimerSettingsDialog

# --- OCR Integration Imports ---
try:
    from ocr_manager import OCRConfig
    from ocr_settings_dialog import OCRSettingsDialog
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
# -------------------------------

from custom_input_dialog import ask_string_custom

class LeftPanel(QObject):
    """
    左側パネル（ツリーと操作ボタン）のロジックを管理するクラス
    """
    def __init__(self, ui_manager, parent_layout, config_manager, logger, locale_manager):
        super().__init__(ui_manager)
        self.ui_manager = ui_manager
        self.config_manager = config_manager
        self.logger = logger
        self.locale_manager = locale_manager
        self.core_engine = None 

        self.setup_ui(parent_layout)
        self.connect_signals()

    def _safe_icon(self, icon_name, color=None):
        try:
            if color:
                base_icon = qta.icon(icon_name, color=color)
            else:
                base_icon = qta.icon(icon_name)
            
            image = QImage(24, 24, QImage.Format_ARGB32_Premultiplied)
            image.fill(Qt.transparent)
            
            painter = QPainter()
            if painter.begin(image):
                try:
                    base_icon.paint(painter, QRect(0, 0, 24, 24))
                finally:
                    painter.end()
            
            return QIcon(QPixmap.fromImage(image))

        except Exception as e:
            print(f"[WARN] QtAwesome rendering failed for {icon_name}: {e}")
            return QIcon()

    def create_colored_icon(self, color):
        """ステータス表示用の色付きドットアイコンを生成"""
        size = 14
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)

        should_draw = False
        if isinstance(color, Qt.GlobalColor):
            if color != Qt.transparent:
                should_draw = True
        elif isinstance(color, QColor):
            if color.alpha() > 0:
                should_draw = True
        else:
            should_draw = True

        if should_draw:
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setBrush(QBrush(color))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(2, 2, 10, 10)
            painter.end()
        
        return QIcon(pixmap)

    def setup_ui(self, parent_layout):
        left_frame = QFrame()
        left_layout = QVBoxLayout(left_frame)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(8)
        
        # --- ヘッダーエリア ---
        header_layout = QHBoxLayout()
        
        self.list_title_label = QLabel()
        font = self.list_title_label.font()
        font.setPointSize(10)
        font.setBold(True)
        self.list_title_label.setFont(font)
        self.list_title_label.setStyleSheet("color: #37474f;") 
        header_layout.addWidget(self.list_title_label)
        
        header_layout.addStretch()
        
        def create_tool_btn(icon_name, tooltip_key):
            btn = QToolButton()
            btn.setIcon(self._safe_icon(icon_name, color='#78909c')) 
            btn.setIconSize(QSize(14, 14))
            btn.setFixedSize(24, 24)
            btn.setAutoRaise(True) 
            btn.setCursor(Qt.PointingHandCursor)
            return btn

        self.move_up_button = create_tool_btn('fa5s.arrow-up', "move_up_button")
        self.move_down_button = create_tool_btn('fa5s.arrow-down', "move_down_button")
        
        header_layout.addWidget(self.move_up_button)
        header_layout.addWidget(self.move_down_button)
        
        left_layout.addLayout(header_layout)
        
        # --- ツリーウィジェット ---
        self.image_tree = DraggableTreeWidget(self.config_manager)
        self.image_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.image_tree.setDragDropMode(QAbstractItemView.DragDrop)
        self.image_tree.setDragEnabled(True)
        self.image_tree.setAcceptDrops(True)
        self.image_tree.setDropIndicatorShown(False)
        self.image_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.image_tree.setHeaderHidden(True)
        
        self.image_tree.setStyleSheet("""
            QTreeWidget::item {
                height: 26px;
                padding: 2px;
            }
        """)
        left_layout.addWidget(self.image_tree)
        
        # --- アクションボタンエリア ---
        action_layout = QVBoxLayout()
        action_layout.setSpacing(8)
        
        def create_action_btn(icon_name, primary=False, danger=False):
            btn = QPushButton()
            icon_color = 'white' if (primary or danger) else '#546e7a'
            btn.setIcon(self._safe_icon(icon_name, color=icon_color))
            btn.setCursor(Qt.PointingHandCursor)
            btn.setMinimumHeight(34)
            
            if primary:
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: #4caf50; 
                        color: white; 
                        font-weight: bold;
                        border-radius: 4px;
                        border: none;
                        text-align: left;
                        padding-left: 15px;
                    }
                    QPushButton:hover { background-color: #66bb6a; }
                """)
            elif danger:
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: #ff9800; 
                        color: white; 
                        font-weight: bold;
                        border-radius: 4px;
                        border: none;
                        text-align: left;
                        padding-left: 15px;
                    }
                    QPushButton:hover { background-color: #ffa726; }
                """)
            else:
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: #ffffff; 
                        color: #37474f;
                        border: 1px solid #cfd8dc;
                        border-radius: 4px;
                        text-align: left;
                        padding-left: 10px;
                        font-weight: bold;
                    }
                    QPushButton:hover { background-color: #f5f5f5; }
                """)
            return btn

        self.load_image_button = create_action_btn('fa5s.plus', primary=True)
        action_layout.addWidget(self.load_image_button)

        structure_layout = QHBoxLayout()
        structure_layout.setSpacing(6)
        
        def create_small_btn(icon_name):
            btn = QPushButton()
            btn.setIcon(self._safe_icon(icon_name, color='#546e7a'))
            btn.setMinimumHeight(34)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #ffffff;
                    border: 1px solid #cfd8dc;
                    border-radius: 4px;
                }
                QPushButton:hover { background-color: #f5f5f5; }
            """)
            return btn

        self.create_folder_button = create_action_btn('fa5s.folder-plus')
        self.delete_item_button = create_action_btn('fa5s.trash-alt', danger=True)
        
        structure_layout.addWidget(self.create_folder_button)
        structure_layout.addWidget(self.delete_item_button)
        action_layout.addLayout(structure_layout)
        
        # 3. 編集・移動
        edit_layout = QHBoxLayout()
        edit_layout.setSpacing(6)
        
        self.rename_button = create_small_btn('fa5s.pen')
        self.move_in_button = create_small_btn('fa5s.file-import')
        self.move_out_button = create_small_btn('fa5s.file-export')
        
        edit_layout.addWidget(self.rename_button)
        edit_layout.addWidget(self.move_in_button)
        edit_layout.addWidget(self.move_out_button)
        
        action_layout.addLayout(edit_layout)
        
        left_layout.addLayout(action_layout)
        parent_layout.addWidget(left_frame, 1)

    def connect_signals(self):
        self.load_image_button.clicked.connect(self.load_images_dialog)
        self.delete_item_button.clicked.connect(self.on_delete_button_clicked)
        self.move_up_button.clicked.connect(self.move_item_up)
        self.move_down_button.clicked.connect(self.move_item_down)
        
        self.create_folder_button.clicked.connect(self.ui_manager.createFolderRequested.emit)
        self.move_in_button.clicked.connect(self.ui_manager.moveItemIntoFolderRequested.emit)
        self.move_out_button.clicked.connect(self.ui_manager.moveItemOutOfFolderRequested.emit)
        
        self.image_tree.itemSelectionChanged.connect(self.on_image_tree_selection_changed)
        self.image_tree.itemClicked.connect(self.on_image_tree_item_clicked)
        self.image_tree.customContextMenuRequested.connect(self.on_tree_context_menu)
        self.image_tree.orderUpdated.connect(self.ui_manager.orderChanged.emit)
        self.image_tree.itemsMoved.connect(self.ui_manager.itemsMovedIntoFolder.emit)
        
        self.rename_button.clicked.connect(self.on_rename_button_clicked)

    def retranslate_ui(self):
        lm = self.locale_manager.tr
        self.list_title_label.setText(lm("list_title"))
        
        self.move_up_button.setToolTip(lm("move_up_button"))
        self.move_down_button.setToolTip(lm("move_down_button"))
        
        self.load_image_button.setText(f" {lm('add_image_button')}")
        self.create_folder_button.setText(f" {lm('create_folder_button')}")
        self.delete_item_button.setText(f" {lm('delete_item_button')}")
        
        self.rename_button.setToolTip(lm("rename_button"))
        self.move_in_button.setToolTip(lm("move_in_button"))
        self.move_out_button.setToolTip(lm("move_out_button"))

    def _add_items_recursive(self, parent_widget, item_list, expanded_folders, selected_path, lm):
        item_to_reselect = None
        
        for item_data in item_list:
            if item_data['type'] == 'folder':
                folder_settings = item_data['settings']
                mode = folder_settings.get('mode', 'normal')
                
                folder_item = QTreeWidgetItem(parent_widget, [lm("folder_item_prefix", item_data['name'])])
                folder_item.setData(0, Qt.UserRole, item_data['path'])
                folder_item.setFlags(folder_item.flags() | Qt.ItemIsDropEnabled)
                
                brush = QBrush(QColor("#263238"))
                icon_color = Qt.transparent
                
                if mode == 'normal': icon_color = QColor("#90a4ae")
                elif mode == 'excluded': brush = QBrush(QColor("#d32f2f")); icon_color = QColor("#d32f2f")
                elif mode == 'cooldown': brush = QBrush(QColor("#a1887f")); icon_color = QColor("#a1887f")
                elif mode == 'priority_image': brush = QBrush(QColor("#1976d2")); icon_color = QColor("#1976d2")
                elif mode == 'priority_timer': brush = QBrush(QColor("#388e3c")); icon_color = QColor("#388e3c")
                elif mode == 'priority_sequence': brush = QBrush(QColor("#0097a7")); icon_color = QColor("#0097a7")
                
                folder_item.setIcon(0, self.create_colored_icon(icon_color))
                folder_item.setForeground(0, brush)
                
                if item_data['path'] in expanded_folders: folder_item.setExpanded(True)
                if item_data['path'] == selected_path: item_to_reselect = folder_item
                
                child_reselect = self._add_items_recursive(
                    folder_item, 
                    item_data.get('children', []), 
                    expanded_folders, 
                    selected_path, 
                    lm
                )
                if child_reselect: item_to_reselect = child_reselect

            elif item_data['type'] == 'image':
                image_item = QTreeWidgetItem(parent_widget, [item_data['name']])
                image_item.setData(0, Qt.UserRole, item_data['path'])
                
                settings = item_data.get('settings', {})
                timer_conf = settings.get('timer_mode', {})
                is_timer_enabled = False
                if timer_conf and isinstance(timer_conf, dict):
                    is_timer_enabled = timer_conf.get('enabled', False)
                
                ocr_conf = settings.get('ocr_settings')
                is_ocr_enabled = False
                if ocr_conf and isinstance(ocr_conf, dict):
                    is_ocr_enabled = ocr_conf.get('enabled', False)
                
                icon_color = Qt.transparent
                if is_timer_enabled:
                    icon_color = QColor("#ff9800")
                elif is_ocr_enabled:
                    icon_color = QColor("#9c27b0")
                
                image_item.setIcon(0, self.create_colored_icon(icon_color))
                
                brush = QBrush(QColor("#37474f"))
                image_item.setForeground(0, brush)
                
                if item_data['path'] == selected_path: item_to_reselect = image_item
        
        return item_to_reselect

    def update_image_tree(self):
        lm = self.locale_manager.tr
        self.image_tree.blockSignals(True)
        
        expanded_folders = set()
        selected_path, _ = self.get_selected_item_path()
        
        iterator = QTreeWidgetItemIterator(self.image_tree)
        while iterator.value():
            item = iterator.value()
            path = item.data(0, Qt.UserRole)
            if path and item.isExpanded():
                expanded_folders.add(path)
            iterator += 1
            
        self.image_tree.clear()

        current_app_name = None
        if self.core_engine and self.core_engine.environment_tracker:
            current_app_name = self.core_engine.environment_tracker.recognition_area_app_title
        
        hierarchical_list = self.config_manager.get_hierarchical_list(current_app_name)
        
        item_to_reselect = self._add_items_recursive(
            self.image_tree, 
            hierarchical_list, 
            expanded_folders, 
            selected_path, 
            lm
        )
        
        if item_to_reselect: 
            self.image_tree.setCurrentItem(item_to_reselect)
            self.image_tree.scrollToItem(item_to_reselect)
            
        self.image_tree.blockSignals(False)
        if item_to_reselect: self.on_image_tree_selection_changed()

    def get_selected_item_path(self):
        selected_items = self.image_tree.selectedItems()
        if not selected_items: return None, None
        item = selected_items[0]; path = item.data(0, Qt.UserRole); name = item.text(0)
        return path, name

    def on_image_tree_item_clicked(self, item, column):
        if self.ui_manager.is_processing_tree_change or not item: return
        path_str = item.data(0, Qt.UserRole)
        if not path_str: return
        if not Path(path_str).is_dir(): 
            self.ui_manager.switch_to_preview_tab()

    def on_image_tree_selection_changed(self):
        path, name = self.get_selected_item_path()
        if self.core_engine: 
            self.core_engine.load_image_and_settings(path)

    def on_tree_context_menu(self, pos):
        item = self.image_tree.itemAt(pos)
        lm = self.locale_manager.tr
        if not item: return
        path_str = item.data(0, Qt.UserRole)
        if not path_str: return
        path = Path(path_str)
        
        menu = QMenu(self.image_tree)
        
        if path.is_dir():
            text = lm("Folder Settings")
            if text == "Folder Settings": text = "フォルダ設定" 
            action_settings = menu.addAction(text)
            action_settings.triggered.connect(lambda: self._open_folder_settings(item, path))
            
        elif path.is_file():
            action_timer = menu.addAction(lm("context_menu_timer_settings"))
            action_timer.setIcon(self._safe_icon('fa5s.clock', color='#546e7a'))
            action_timer.triggered.connect(lambda: self._open_timer_settings(path))
            
            if OCR_AVAILABLE:
                action_ocr = menu.addAction(lm("ocr_settings_btn")) 
                action_ocr.setIcon(self._safe_icon('fa5s.font', color='#9c27b0'))
                action_ocr.triggered.connect(lambda: self._open_ocr_settings(path))
            
        menu.exec(self.image_tree.mapToGlobal(pos))

    def _open_folder_settings(self, item, path):
        current_settings = self.config_manager.load_item_setting(path)
        is_root = (item.parent() is None)
        
        dialog = FolderSettingsDialog(path.name, current_settings, self.locale_manager, is_root, self.ui_manager)
        
        if dialog.exec():
            new_settings = dialog.get_settings()
            self.config_manager.save_item_setting(path, new_settings)
            self.ui_manager.folderSettingsChanged.emit()
            self.update_image_tree()

    def _open_timer_settings(self, path):
        current_settings = self.config_manager.load_item_setting(path)
        
        dialog = TimerSettingsDialog(path, path.name, current_settings, self.locale_manager, 
                                     parent=self.ui_manager, 
                                     core_engine=self.core_engine)
        if dialog.exec():
            timer_data = dialog.get_settings()
            
            current_settings['timer_mode'] = timer_data
            self.config_manager.save_item_setting(path, current_settings)
            
            self.logger.log(f"[INFO] Timer settings updated for {path.name}")
            self.ui_manager.folderSettingsChanged.emit()

    def _open_ocr_settings(self, path):
        """OCR設定ダイアログを開く"""
        template_image = None
        
        if self.core_engine and self.core_engine.current_image_path == str(path):
            template_image = self.core_engine.current_image_mat
        
        if template_image is None:
            try:
                with open(path, 'rb') as f:
                    file_bytes = np.fromfile(f, np.uint8)
                template_image = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
            except Exception as e:
                self.logger.log(f"[ERROR] Failed to load image for OCR: {e}")
                return

        if template_image is None:
            QMessageBox.warning(self.ui_manager, "Error", f"Could not load image:\n{path}")
            return

        current_settings = self.config_manager.load_item_setting(path)
        saved_ocr_settings = current_settings.get('ocr_settings', {})
        
        config = OCRConfig()
        if "config" in saved_ocr_settings:
            cfg_data = saved_ocr_settings["config"]
            config.scale = cfg_data.get("scale", 2.0)
            config.threshold = cfg_data.get("threshold", 128)
            config.invert = cfg_data.get("invert", False)
            config.numeric_mode = cfg_data.get("numeric_mode", False)
            config.lang = cfg_data.get("lang", "eng")
            
        current_roi = saved_ocr_settings.get("roi", None)
        current_condition = saved_ocr_settings.get("condition", None)
        
        # ★★★ 修正: 保存された有効状態を取得 (デフォルトはTrue) ★★★
        is_enabled = saved_ocr_settings.get('enabled', True)

        # ★★★ 修正: enabled引数を渡す ★★★
        dialog = OCRSettingsDialog(template_image, config, current_roi, current_condition, enabled=is_enabled, parent=self.ui_manager)
        
        if dialog.exec():
            new_config, new_roi, new_condition, new_enabled = dialog.get_result()
            
            ocr_data = {
                "enabled": new_enabled,
                "roi": new_roi,
                "config": {
                    "scale": new_config.scale,
                    "threshold": new_config.threshold,
                    "invert": new_config.invert,
                    "numeric_mode": new_config.numeric_mode,
                    "lang": new_config.lang
                },
                "condition": new_condition
            }
            
            current_settings['ocr_settings'] = ocr_data
            self.config_manager.save_item_setting(path, current_settings)
            
            self.logger.log(f"[INFO] OCR settings saved for {path.name}")
            self.update_image_tree()

    def load_images_dialog(self):
        lm = self.locale_manager.tr
        file_paths, _ = QFileDialog.getOpenFileNames(
            self.ui_manager, 
            lm("load_images_dialog_title"), 
            str(self.config_manager.base_dir), 
            lm("load_images_dialog_filter")
        )
        if file_paths: 
            self.ui_manager.set_tree_enabled(False)
            self.ui_manager.loadImagesRequested.emit(file_paths)

    def on_delete_button_clicked(self):
        lm = self.locale_manager.tr
        selected_items = self.image_tree.selectedItems()
        if not selected_items: 
            QMessageBox.warning(self.ui_manager, lm("warn_delete_title"), lm("warn_delete_no_selection"))
            return
        item_names = [f"'{item.text(0).strip()}'" for item in selected_items]
        message = lm("confirm_delete_message", len(item_names), ', '.join(item_names))
        reply = QMessageBox.question(self.ui_manager, lm("confirm_delete_title"), message, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            paths_to_delete = [item.data(0, Qt.UserRole) for item in selected_items if item.data(0, Qt.UserRole)]
            if paths_to_delete: 
                self.ui_manager.deleteItemsRequested.emit(paths_to_delete)

    def on_rename_button_clicked(self):
        lm = self.locale_manager.tr
        path_str, current_name = self.get_selected_item_path()
        if not path_str:
            QMessageBox.warning(self.ui_manager, lm("rename_dialog_title"), lm("warn_rename_no_selection"))
            return
            
        current_base_name = Path(current_name).stem

        if self.core_engine:
            with self.core_engine.temporary_listener_pause():
                new_name, ok = ask_string_custom(
                    self.ui_manager, 
                    lm("rename_dialog_title"), 
                    lm("rename_dialog_prompt"), 
                    current_base_name
                )
        else:
            new_name, ok = ask_string_custom(
                self.ui_manager, 
                lm("rename_dialog_title"), 
                lm("rename_dialog_prompt"), 
                current_base_name
            )

        if not ok:
            self.logger.log("log_rename_cancelled")
            return
        
        if not new_name.strip():
            QMessageBox.warning(self.ui_manager, lm("warn_rename_title"), lm("log_rename_error_empty"))
            return
            
        if any(char in new_name for char in '/\\:*?"<>|'):
            QMessageBox.warning(self.ui_manager, lm("warn_rename_title"), lm("log_rename_error_general", "Invalid characters in name"))
            return

        if new_name == current_base_name:
            self.logger.log("log_rename_item_no_change")
            return
            
        if Path(path_str).is_file():
            suffix = Path(path_str).suffix
            new_name_with_suffix = new_name + suffix
        else:
            new_name_with_suffix = new_name

        self.ui_manager.renameItemRequested.emit(path_str, new_name_with_suffix)

    def move_item_up(self):
        selected_items = self.image_tree.selectedItems()
        if not selected_items or len(selected_items) != 1:
            self.logger.log(self.locale_manager.tr("log_move_item_warn_selection"))
            return
            
        item = selected_items[0]
        parent = item.parent()

        if parent:
            index = parent.indexOfChild(item)
            if index > 0:
                self.ui_manager.set_tree_enabled(False)
                taken_item = parent.takeChild(index)
                parent.insertChild(index - 1, taken_item)
                self.image_tree.setCurrentItem(taken_item) 
            else:
                return
        else:
            index = self.image_tree.indexOfTopLevelItem(item)
            if index > 0:
                self.ui_manager.set_tree_enabled(False)
                taken_item = self.image_tree.takeTopLevelItem(index)
                self.image_tree.insertTopLevelItem(index - 1, taken_item)
                self.image_tree.setCurrentItem(taken_item)
            else:
                return
        
        self.ui_manager.orderChanged.emit()
        self.ui_manager.set_tree_enabled(True)
        
    def move_item_down(self):
        selected_items = self.image_tree.selectedItems()
        if not selected_items or len(selected_items) != 1:
            self.logger.log(self.locale_manager.tr("log_move_item_warn_selection"))
            return
            
        item = selected_items[0]
        parent = item.parent()

        if parent:
            index = parent.indexOfChild(item)
            if index < parent.childCount() - 1: 
                self.ui_manager.set_tree_enabled(False)
                taken_item = parent.takeChild(index)
                parent.insertChild(index + 1, taken_item)
                self.image_tree.setCurrentItem(taken_item) 
            else:
                return
        else:
            index = self.image_tree.indexOfTopLevelItem(item)
            if index < self.image_tree.topLevelItemCount() - 1: 
                self.ui_manager.set_tree_enabled(False)
                taken_item = self.image_tree.takeTopLevelItem(index)
                self.image_tree.insertTopLevelItem(index + 1, taken_item)
                self.image_tree.setCurrentItem(taken_item)
            else:
                return
        
        self.ui_manager.orderChanged.emit()
        self.ui_manager.set_tree_enabled(True)

    def save_tree_order(self):
        data_to_save = {
            'top_level': [],
            'folders': {}
        }
        
        def process_folder_recursive(parent_item):
            child_order_filenames = []
            parent_path_str = parent_item.data(0, Qt.UserRole)
            
            for j in range(parent_item.childCount()):
                child_item = parent_item.child(j)
                if not child_item: continue
                
                child_path_str = child_item.data(0, Qt.UserRole)
                if not child_path_str: continue
                
                original_child_path = Path(child_path_str)
                child_path_name = original_child_path.name
                
                if original_child_path.is_dir():
                    process_folder_recursive(child_item)
                
                child_order_filenames.append(child_path_name)
            
            if parent_path_str:
                data_to_save['folders'][parent_path_str] = child_order_filenames

        for i in range(self.image_tree.topLevelItemCount()):
            item = self.image_tree.topLevelItem(i)
            if not item: continue
            
            path_str = item.data(0, Qt.UserRole)
            if not path_str: continue
            
            data_to_save['top_level'].append(path_str)
            
            if Path(path_str).is_dir():
                process_folder_recursive(item)
        
        return data_to_save
