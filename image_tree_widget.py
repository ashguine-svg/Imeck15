# image_tree_widget.py
# ★★★ 修正: D&D時の同名ファイルチェックとエラーダイアログ表示を追加 ★★★

import sys
# 修正: QMessageBox をインポートに追加
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QApplication, QMessageBox
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
        """
        ドロップイベント処理。
        """
        # 1. 視覚効果のクリア
        if self.last_highlighted_item:
            self.last_highlighted_item.setBackground(0, QBrush(Qt.transparent))
            self.last_highlighted_item = None
        self._remove_dummy_indicator()

        if event.source() != self:
            super().dropEvent(event)
            return

        # 2. 移動対象とターゲットの特定
        dragged_items = self.selectedItems()
        if not dragged_items: return

        target_item = self.itemAt(event.position().toPoint())
        pos = self.dropIndicatorPosition()
        
        # 3. ドロップ先（親アイテム）と挿入位置（インデックス）の決定
        dest_parent = None # NoneならRoot
        insert_index = -1

        target_is_folder = False
        if target_item:
            path_str = target_item.data(0, Qt.UserRole)
            if path_str and Path(path_str).is_dir():
                target_is_folder = True

        # --- A. フォルダアイコンの上にドロップ ---
        if pos == self.DropIndicatorPosition.OnItem and target_item and target_is_folder:
            dest_parent = target_item
            insert_index = 0 # フォルダの先頭へ
        
        # --- B. アイテムの上/下、または画像の上にドロップ ---
        elif target_item:
            dest_parent = target_item.parent()
            
            # ターゲットの現在のインデックスを取得
            if dest_parent:
                target_idx = dest_parent.indexOfChild(target_item)
            else:
                target_idx = self.indexOfTopLevelItem(target_item)
            
            # OnItem(画像)なら下へ、Belowなら下へ、Aboveならその位置へ
            if pos == self.DropIndicatorPosition.AboveItem:
                insert_index = target_idx
            else:
                insert_index = target_idx + 1

        # --- C. 何もない空間（ビューポート）にドロップ ---
        else:
            dest_parent = None
            insert_index = self.topLevelItemCount() # 末尾へ

        # --- ★★★ 追加: 同名ファイルチェック（移動エラー防止） ★★★ ---
        # 移動先のディレクトリパスを特定
        dest_dir_path = self.config_manager.base_dir
        if dest_parent:
            dest_dir_path = Path(dest_parent.data(0, Qt.UserRole))

        for item in dragged_items:
            # 同じ親の中での移動（並べ替え）ならファイル名重複チェックは不要
            if item.parent() == dest_parent:
                continue

            src_path_str = item.data(0, Qt.UserRole)
            if not src_path_str: continue
            
            src_path = Path(src_path_str)
            target_path = dest_dir_path / src_path.name
            
            if target_path.exists():
                # 重複エラーを表示して中止
                lm = self.config_manager.logger.locale_manager
                err_title = lm.tr("error_title_move_item_failed")
                if err_title == "error_title_move_item_failed": err_title = "Move Error"
                
                err_msg = lm.tr("log_move_item_error_exists", src_path.name)
                
                QMessageBox.warning(self, err_title, err_msg)
                event.ignore()
                return
        # -----------------------------------------------------------

        # 4. 移動処理の実行 (シグナルブロックで安全確保)
        self.blockSignals(True)
        try:
            source_parent = dragged_items[0].parent()
            items_moved_list = []
            
            for item in dragged_items:
                # 移動元の親とインデックスを取得
                current_parent = item.parent()
                if current_parent:
                    current_idx = current_parent.indexOfChild(item)
                else:
                    current_idx = self.indexOfTopLevelItem(item)

                # 同じ親の中での移動の場合、自分が抜けることでインデックスがずれるのを補正
                actual_insert_index = insert_index
                if current_parent == dest_parent:
                    if current_idx < insert_index:
                        actual_insert_index -= 1
                
                # アイテムをツリーから「引き抜く」
                if current_parent:
                    taken_item = current_parent.takeChild(current_idx)
                else:
                    taken_item = self.takeTopLevelItem(current_idx)
                
                # アイテムを新しい場所に「挿入する」
                if dest_parent:
                    dest_parent.insertChild(actual_insert_index, taken_item)
                else:
                    self.insertTopLevelItem(actual_insert_index, taken_item)
                
                # 挿入した分、次のアイテムの挿入位置をずらす
                insert_index = actual_insert_index + 1
                
                items_moved_list.append(taken_item)

            # 選択状態を復元
            self.clearSelection()
            for item in items_moved_list:
                item.setSelected(True)
            
            # 通知用のパスリスト作成
            dest_path = str(self.config_manager.base_dir)
            if dest_parent:
                dest_path = dest_parent.data(0, Qt.UserRole)
            
            source_paths = [item.data(0, Qt.UserRole) for item in items_moved_list]

        except Exception as e:
            print(f"[ERROR] Drag drop failed: {e}")
        finally:
            self.blockSignals(False)

        # 5. 変更通知
        if source_parent != dest_parent:
            self.itemsMoved.emit(source_paths, dest_path)

        self.orderUpdated.emit()
        
        event.accept()
