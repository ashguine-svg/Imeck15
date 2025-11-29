# image_tree_widget.py

import sys
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QApplication
from PySide6.QtGui import QBrush, QColor
from PySide6.QtCore import Qt, Signal
from pathlib import Path

class DraggableTreeWidget(QTreeWidget):
    orderUpdated = Signal()
    itemsMoved = Signal(list, str) # Emits [source_paths], dest_folder_path

    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.last_highlighted_item = None
        self.highlight_color = QApplication.palette().highlight().color().lighter(150)
        self.config_manager = config_manager

        # --- カスタムインジケータ（ダミーアイテム）の作成 ---
        self.dummy_indicator_item = QTreeWidgetItem(["――――――――――――――"])
        # 赤色などで目立たせる
        brush = QBrush(QColor("#ef5350")) 
        self.dummy_indicator_item.setForeground(0, brush)
        
        # ダミーアイテム自体は選択・ドロップ不可にする
        flags = self.dummy_indicator_item.flags()
        flags &= ~Qt.ItemIsDragEnabled
        flags &= ~Qt.ItemIsDropEnabled
        self.dummy_indicator_item.setFlags(flags)

        # 標準のインジケータは無効化（カスタムを使うため）
        self.setDropIndicatorShown(False)

    def _remove_dummy_indicator(self):
        """ツリーからダミーインジケータを安全に削除します。"""
        try:
            # 親がいる場合は親から削除
            parent = self.dummy_indicator_item.parent()
            if parent:
                parent.removeChild(self.dummy_indicator_item)
            else:
                # トップレベルにある場合はトップレベルから削除
                index = self.indexOfTopLevelItem(self.dummy_indicator_item)
                if index != -1:
                    self.takeTopLevelItem(index)
        except RuntimeError:
            pass # 既に削除されている場合は無視

    def dragEnterEvent(self, event):
        if event.source() == self:
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.source() != self:
            super().dragMoveEvent(event)
            return

        event.acceptProposedAction()

        # 前回のハイライトやインジケータをクリア
        if self.last_highlighted_item:
            self.last_highlighted_item.setBackground(0, QBrush(Qt.transparent))
            self.last_highlighted_item = None

        self._remove_dummy_indicator()

        target_item = self.itemAt(event.position().toPoint())
        pos = self.dropIndicatorPosition()
        
        # ターゲットがフォルダかどうかをチェック
        target_is_folder = False
        if target_item:
            path_str = target_item.data(0, Qt.UserRole)
            if path_str and Path(path_str).is_dir():
                target_is_folder = True

        # --- ケース1: フォルダの中にドロップ (OnItem) ---
        if pos == self.DropIndicatorPosition.OnItem and target_is_folder:
            # フォルダをハイライト
            target_item.setBackground(0, self.highlight_color)
            self.last_highlighted_item = target_item

        # --- ケース2: アイテムの間に挿入 ---
        # (OnItemだがターゲットが画像の場合、または Above/Below の場合)
        if not (pos == self.DropIndicatorPosition.OnItem and target_is_folder):
            if pos == self.DropIndicatorPosition.OnItem and target_item:
                # 画像の上にホバー -> その画像の「下」に挿入線を表示
                parent = target_item.parent()
                if parent:
                    index = parent.indexOfChild(target_item)
                    parent.insertChild(index + 1, self.dummy_indicator_item)
                else:
                    index = self.indexOfTopLevelItem(target_item)
                    self.insertTopLevelItem(index + 1, self.dummy_indicator_item)

            elif pos in [self.DropIndicatorPosition.AboveItem, self.DropIndicatorPosition.BelowItem] and target_item:
                parent = target_item.parent()
                index_offset = 1 if pos == self.DropIndicatorPosition.BelowItem else 0
                if parent:
                    index = parent.indexOfChild(target_item)
                    parent.insertChild(index + index_offset, self.dummy_indicator_item)
                else:
                    index = self.indexOfTopLevelItem(target_item)
                    self.insertTopLevelItem(index + index_offset, self.dummy_indicator_item)

            elif pos == self.DropIndicatorPosition.OnViewport:
                # 何もない空間 -> リストの末尾に追加
                self.insertTopLevelItem(self.topLevelItemCount(), self.dummy_indicator_item)

    def dragLeaveEvent(self, event):
        if self.last_highlighted_item:
            self.last_highlighted_item.setBackground(0, QBrush(Qt.transparent))
            self.last_highlighted_item = None

        self._remove_dummy_indicator()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        # ハイライトとダミーインジケータをクリア
        if self.last_highlighted_item:
            self.last_highlighted_item.setBackground(0, QBrush(Qt.transparent))
            self.last_highlighted_item = None

        self._remove_dummy_indicator()

        if event.source() != self:
            super().dropEvent(event)
            return

        target_item = self.itemAt(event.position().toPoint())
        dragged_items = self.selectedItems()
        if not dragged_items:
            return

        source_parent = dragged_items[0].parent()
        pos = self.dropIndicatorPosition()
        
        target_is_folder = False
        if target_item:
            path_str = target_item.data(0, Qt.UserRole)
            if path_str and Path(path_str).is_dir():
                target_is_folder = True

        # アイテムの複製準備
        cloned_items_data = [(item.clone(), item.data(0, Qt.UserRole)) for item in dragged_items]

        # 移動元のアイテムを削除
        for item in dragged_items:
            parent = item.parent()
            if parent:
                parent.removeChild(item)
            else:
                self.takeTopLevelItem(self.indexOfTopLevelItem(item))

        dest_parent = None
        insert_index = -1

        # ドロップ位置の決定
        if pos == self.DropIndicatorPosition.OnItem and target_item:
            if target_is_folder:
                # フォルダ内へ移動
                dest_parent = target_item
                insert_index = 0
            else:
                # 画像の「下」へ移動
                dest_parent = target_item.parent()
                if dest_parent:
                    insert_index = dest_parent.indexOfChild(target_item) + 1
                else:
                    insert_index = self.indexOfTopLevelItem(target_item) + 1

        elif target_item:
            # ターゲットの上または下へ
            dest_parent = target_item.parent()
            if dest_parent:
                insert_index = dest_parent.indexOfChild(target_item)
                if pos == self.DropIndicatorPosition.BelowItem:
                    insert_index += 1
            else:
                insert_index = self.indexOfTopLevelItem(target_item)
                if pos == self.DropIndicatorPosition.BelowItem:
                    insert_index += 1
        else:
             # 空白地帯 -> 末尾へ
             dest_parent = None
             insert_index = self.topLevelItemCount()

        # アイテムの挿入
        inserted_items = []
        if dest_parent:
            for i, (item_clone, _) in enumerate(cloned_items_data):
                dest_parent.insertChild(insert_index + i, item_clone)
                inserted_items.append(item_clone)
        else:
            for i, (item_clone, _) in enumerate(cloned_items_data):
                self.insertTopLevelItem(insert_index + i, item_clone)
                inserted_items.append(item_clone)

        # 選択状態の復元
        self.clearSelection()
        if inserted_items:
            for item in inserted_items:
                item.setSelected(True)
            self.scrollToItem(inserted_items[0])

        # フォルダ移動シグナルの発火
        if source_parent != dest_parent:
            dest_path = str(self.config_manager.base_dir) if dest_parent is None else dest_parent.data(0, Qt.UserRole)
            source_paths = [path for _, path in cloned_items_data if path]
            if source_paths and dest_path:
                self.itemsMoved.emit(source_paths, dest_path)

        self.orderUpdated.emit()
        event.accept()
