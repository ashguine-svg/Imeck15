# ui_tree_panel.py

import sys
import os
from pathlib import Path
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QGridLayout,
    QAbstractItemView, QMessageBox, QInputDialog, QTreeWidgetItem,
    QTreeWidget, QTreeWidgetItemIterator, QApplication, QToolTip, QFileDialog,
    QLineEdit, QToolButton, QSizePolicy, QWidget
)
from PySide6.QtGui import QPixmap, QPainter, QBrush, QColor, QIcon, QAction
from PySide6.QtCore import Qt, QObject, QSize

import qtawesome as qta

from image_tree_widget import DraggableTreeWidget
from dialogs import FolderSettingsDialog

class LeftPanel(QObject):
    """
    左側パネル（ツリーと操作ボタン）のロジックを管理するクラス
    モダンUI適用版：配色調整済み
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

    def create_colored_icon(self, color):
        """ステータス表示用の色付きドットアイコンを生成"""
        pixmap = QPixmap(14, 14)
        pixmap.fill(Qt.transparent)
        if color != Qt.transparent:
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
        # 濃いグレー
        self.list_title_label.setStyleSheet("color: #37474f;") 
        header_layout.addWidget(self.list_title_label)
        
        header_layout.addStretch()
        
        # 順序変更ボタン (グレー系アイコン)
        def create_tool_btn(icon_name, tooltip_key):
            btn = QToolButton()
            btn.setIcon(qta.icon(icon_name, color='#78909c')) 
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
        
        # 行の高さ調整 (配色はmain.pyのCSSで管理)
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
            # アイコン色: Primary/Dangerは白、通常は濃いグレー
            icon_color = 'white' if (primary or danger) else '#546e7a'
            btn.setIcon(qta.icon(icon_name, color=icon_color))
            btn.setCursor(Qt.PointingHandCursor)
            btn.setMinimumHeight(34)
            
            if primary:
                # 画像追加: 緑 (#4caf50)
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
                # 削除: オレンジ (#ff9800)
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
                # その他: 白背景 + グレー枠線 (ライトグリーン廃止)
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

        # 1. 画像追加 (緑アクセント維持)
        self.load_image_button = create_action_btn('fa5s.plus', primary=True)
        action_layout.addWidget(self.load_image_button)

        # 2. 構造管理 (横並び)
        structure_layout = QHBoxLayout()
        structure_layout.setSpacing(6)
        
        # アイコンのみの小さいボタン (サブ操作用)
        def create_small_btn(icon_name):
            btn = QPushButton()
            btn.setIcon(qta.icon(icon_name, color='#546e7a'))
            btn.setMinimumHeight(34)
            btn.setCursor(Qt.PointingHandCursor)
            # グレー枠線
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #ffffff;
                    border: 1px solid #cfd8dc;
                    border-radius: 4px;
                }
                QPushButton:hover { background-color: #f5f5f5; }
            """)
            return btn

        # フォルダ作成 (通常色)
        self.create_folder_button = create_action_btn('fa5s.folder-plus')
        # 削除 (オレンジアクセント維持)
        self.delete_item_button = create_action_btn('fa5s.trash-alt', danger=True)
        
        structure_layout.addWidget(self.create_folder_button)
        structure_layout.addWidget(self.delete_item_button)
        action_layout.addLayout(structure_layout)
        
        # 3. 編集・移動 (アイコンのみ)
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
        
        # ボタンテキスト
        self.load_image_button.setText(f" {lm('add_image_button')}")
        self.create_folder_button.setText(f" {lm('create_folder_button')}")
        self.delete_item_button.setText(f" {lm('delete_item_button')}")
        
        self.rename_button.setToolTip(lm("rename_button"))
        self.move_in_button.setToolTip(lm("move_in_button"))
        self.move_out_button.setToolTip(lm("move_out_button"))

    # --- Logic Methods ---

    def _add_items_recursive(self, parent_widget, item_list, expanded_folders, selected_path, lm):
        item_to_reselect = None
        
        for item_data in item_list:
            if item_data['type'] == 'folder':
                folder_settings = item_data['settings']
                mode = folder_settings.get('mode', 'normal')
                
                folder_item = QTreeWidgetItem(parent_widget, [lm("folder_item_prefix", item_data['name'])])
                folder_item.setData(0, Qt.UserRole, item_data['path'])
                folder_item.setFlags(folder_item.flags() | Qt.ItemIsDropEnabled)
                
                # 文字色: 濃いグレー
                brush = QBrush(QColor("#263238"))
                icon_color = Qt.transparent
                
                # モード別インジケータ色
                if mode == 'normal': icon_color = QColor("#90a4ae")
                elif mode == 'excluded': brush = QBrush(QColor("#d32f2f")); icon_color = QColor("#d32f2f")
                elif mode == 'cooldown': brush = QBrush(QColor("#7b1fa2")); icon_color = QColor("#7b1fa2")
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
                image_item.setIcon(0, self.create_colored_icon(Qt.transparent))
                
                # 画像アイテムも視認性の高い色に
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
        
        if path.is_dir():
            current_settings = self.config_manager.load_item_setting(path)
            is_root = (item.parent() is None)
            
            dialog = FolderSettingsDialog(path.name, current_settings, self.locale_manager, is_root, self.ui_manager)
            
            if dialog.exec():
                new_settings = dialog.get_settings()
                self.config_manager.save_item_setting(path, new_settings)
                self.ui_manager.folderSettingsChanged.emit()
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

                try:
                    env_list = settings.get("environment_info", [])
                    env_tooltip_lines = []
                    MAX_ENV_DISPLAY = 5 
                    if env_list:
                        env_tooltip_lines.append(lm("context_menu_env_header", min(len(env_list), MAX_ENV_DISPLAY)))
                        for env_data in env_list[-MAX_ENV_DISPLAY:]:
                            app = env_data.get("app_name")
                            res = env_data.get("resolution", "N/A")
                            dpi = env_data.get("dpi", "N/A")
                            scale = env_data.get("imeck_scale", 0.0)
                            if app:
                                env_tooltip_lines.append(lm("context_menu_env_entry", app, res, dpi, scale))
                            else:
                                env_tooltip_lines.append(lm("context_menu_env_entry_no_app", res, dpi, scale))
                    if env_tooltip_lines:
                        tooltip_text += "\n" + "\n".join(env_tooltip_lines)
                except Exception as e:
                    tooltip_text += f"\n[Env Info Error: {e}]" 

                global_pos = self.image_tree.mapToGlobal(pos)
                QToolTip.showText(global_pos, tooltip_text, self.image_tree)
            except Exception as e: 
                global_pos = self.image_tree.mapToGlobal(pos)
                QToolTip.showText(global_pos, lm("context_menu_info_error", str(e)), self.image_tree)

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

        new_name, ok = QInputDialog.getText(
            self.ui_manager, 
            lm("rename_dialog_title"), 
            lm("rename_dialog_prompt"), 
            QLineEdit.EchoMode.Normal, 
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
