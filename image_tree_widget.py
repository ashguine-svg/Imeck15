# image_tree_widget.py

import sys # (ui.py には無かったが、Pathlibのためにあると堅牢)
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QApplication
from PySide6.QtGui import QBrush, QColor
from PySide6.QtCore import Qt, Signal
from pathlib import Path
# config_manager はコンストラクタで受け取るため、ここではインポート不要

class DraggableTreeWidget(QTreeWidget):
    orderUpdated = Signal()
    itemsMoved = Signal(list, str) # Emits [source_paths], dest_folder_path

    # --- ▼▼▼ 修正箇所 (コンストラクタ) ▼▼▼ ---
    def __init__(self, config_manager, parent=None): # config_manager を引数で受け取る
        super().__init__(parent)
        self.last_highlighted_item = None
        self.highlight_color = QApplication.palette().highlight().color().lighter(150)
        self.config_manager = config_manager # 引数で受け取ったものを設定
    # --- ▲▲▲ 修正箇所 ▲▲▲ ---

        self.dummy_indicator_item = QTreeWidgetItem(["――――――――――"])
        brush = QBrush(QColor("red"))
        self.dummy_indicator_item.setForeground(0, brush)
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
                index = self.indexOfTopLevelItem(self.dummy_indicator_item)
                if index != -1:
                    self.takeTopLevelItem(index)

    def dragEnterEvent(self, event):
        if event.source() == self:
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    # (前回修正した dragMoveEvent をそのまま貼り付け)
    def dragMoveEvent(self, event):
        if event.source() != self:
            super().dragMoveEvent(event)
            return

        event.acceptProposedAction()

        if self.last_highlighted_item:
            self.last_highlighted_item.setBackground(0, QBrush(Qt.transparent))
            self.last_highlighted_item = None

        self._remove_dummy_indicator()

        target_item = self.itemAt(event.position().toPoint())
        pos = self.dropIndicatorPosition()
        
        # 1. ドラッグ中のアイテムにフォルダが含まれているかチェック
        dragged_items = self.selectedItems()
        is_dragging_folder = False
        if dragged_items:
            for item in dragged_items:
                dragged_path_str = item.data(0, Qt.UserRole)
                if dragged_path_str and Path(dragged_path_str).is_dir():
                    is_dragging_folder = True
                    break
        
        # 2. ターゲットがフォルダかどうかをチェック
        target_is_folder = False
        if target_item:
            path_str = target_item.data(0, Qt.UserRole)
            if path_str and Path(path_str).is_dir():
                target_is_folder = True

        # 3. ターゲットがフォルダで、かつ「上 (OnItem)」にドロップしようとした場合
        if pos == self.DropIndicatorPosition.OnItem and target_is_folder:
            
            # 4. もしドラッグ中のアイテムに *フォルダ* が含まれていたら
            if is_dragging_folder:
                # フォルダ ON フォルダ (ネスト) は禁止 -> 「下」にドロップとして扱う
                pos = self.DropIndicatorPosition.BelowItem
            else:
                # 画像 ON フォルダ は許可 -> ハイライトする
                target_item.setBackground(0, self.highlight_color)
                self.last_highlighted_item = target_item

        # (残りのロジックは、OnItem 且つ target_is_folder 以外の場合)
        if not (pos == self.DropIndicatorPosition.OnItem and target_is_folder):
            if pos == self.DropIndicatorPosition.OnItem and target_item:
                # (ターゲットが「画像」の場合)
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
                 self.insertTopLevelItem(self.topLevelItemCount(), self.dummy_indicator_item)

    def dragLeaveEvent(self, event):
        if self.last_highlighted_item:
            self.last_highlighted_item.setBackground(0, QBrush(Qt.transparent))
            self.last_highlighted_item = None

        self._remove_dummy_indicator()
        super().dragLeaveEvent(event)

    # (前回修正した dropEvent をベースに、競合ロジックを修正)
    def dropEvent(self, event):
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
        
        # 1. ドラッグ中のアイテムにフォルダが含まれているかチェック
        is_dragging_folder = False
        if dragged_items:
            for item in dragged_items:
                dragged_path_str = item.data(0, Qt.UserRole)
                if dragged_path_str and Path(dragged_path_str).is_dir():
                    is_dragging_folder = True
                    break

        # 2. ターゲットがフォルダかどうかをチェック
        target_is_folder = False
        if target_item:
            path_str = target_item.data(0, Qt.UserRole)
            if path_str and Path(path_str).is_dir():
                target_is_folder = True

        # 3. ターゲットがフォルダで、「上 (OnItem)」ドロップの場合
        if pos == self.DropIndicatorPosition.OnItem and target_is_folder:
            
            # 4. もしドラッグ中のアイテムに *フォルダ* が含まれていたら
            if is_dragging_folder:
                # フォルダ ON フォルダ (ネスト) は禁止 -> 「下」にドロップとして扱う
                pos = self.DropIndicatorPosition.BelowItem
            # else:
                # 画像 ON フォルダ は許可 -> pos は OnItem のまま変更しない

        cloned_items_data = [(item.clone(), item.data(0, Qt.UserRole)) for item in dragged_items]

        for item in dragged_items:
            parent = item.parent()
            if parent:
                parent.removeChild(item)
            else:
                self.takeTopLevelItem(self.indexOfTopLevelItem(item))

        dest_parent = None
        insert_index = -1

        if pos == self.DropIndicatorPosition.OnItem and target_item:
            
            # ターゲットがフォルダ (画像 ON フォルダ の場合)
            if target_is_folder:
                dest_parent = target_item
                insert_index = 0 # フォルダの先頭に追加
            else:
                # ターゲットが画像 (画像 ON 画像 の場合) -> 「下」にドロップ (親は同じ)
                dest_parent = target_item.parent()
                if dest_parent:
                    insert_index = dest_parent.indexOfChild(target_item) + 1
                else:
                    insert_index = self.indexOfTopLevelItem(target_item) + 1
        elif target_item:
            # (元のロジック) アイテムの上または下にドロップ
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
             dest_parent = None
             insert_index = self.topLevelItemCount()

        inserted_items = []
        if dest_parent:
            for i, (item_clone, _) in enumerate(cloned_items_data):
                dest_parent.insertChild(insert_index + i, item_clone)
                inserted_items.append(item_clone)
        else:
            for i, (item_clone, _) in enumerate(cloned_items_data):
                self.insertTopLevelItem(insert_index + i, item_clone)
                inserted_items.append(item_clone)

        self.clearSelection()
        if inserted_items:
            for item in inserted_items:
                item.setSelected(True)
            self.scrollToItem(inserted_items[0])

        # --- ▼▼▼ 修正箇所 (D&D 競合バグ修正) ▼▼▼ ---
        # 
        # 目的: D&Dによる「フォルダ移動」と「順序変更」のシグナル競合を解消する
        
        is_move_operation = (source_parent != dest_parent)

        if is_move_operation:
            # 1.「フォルダ移動」の場合
            dest_path = str(self.config_manager.base_dir) if dest_parent is None else dest_parent.data(0, Qt.UserRole)
            source_paths = [path for _, path in cloned_items_data if path]
            if source_paths and dest_path:
                # itemsMoved シグナル *のみ* を発行する
                # (ui.py 側で orderUpdated のタイマーをキャンセルする)
                self.itemsMoved.emit(source_paths, dest_path)
        else:
            # 2.「順序変更」(同じ親の中での並び替え) の場合
            # orderUpdated シグナル *のみ* を発行する
            self.orderUpdated.emit()
            
        # (以前は両方発行される可能性があった `self.orderUpdated.emit()` を else 句に移動)
        # --- ▲▲▲ 修正完了 ▲▲▲ ---

        event.accept()
