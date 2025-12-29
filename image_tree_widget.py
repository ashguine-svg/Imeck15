# image_tree_widget.py
# ★★★ 修正: D&D時の同名ファイルチェックとエラーダイアログ表示を追加 ★★★
# ★★★ 修正: D&D時の自動スクロール機能を追加 ★★★

import sys
# 修正: QMessageBox をインポートに追加
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QApplication, QMessageBox, QAbstractItemView
from PySide6.QtGui import QBrush, QColor
from PySide6.QtCore import Qt, Signal, QTimer
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
        
        # --- 自動スクロール用のタイマーを初期化 ---
        self.scroll_timer = QTimer(self)
        self.scroll_timer.timeout.connect(self._perform_auto_scroll)
        self.scroll_direction = 0  # 0: 停止, 1: 下方向, -1: 上方向
        self.scroll_speed = 0  # スクロール速度（ピクセル単位）

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
    
    def _perform_auto_scroll(self):
        """自動スクロールを実行します。"""
        if self.scroll_direction == 0:
            return
        
        scroll_bar = self.verticalScrollBar()
        if scroll_bar:
            current_value = scroll_bar.value()
            new_value = current_value + (self.scroll_direction * self.scroll_speed)
            scroll_bar.setValue(new_value)
    
    def _check_and_start_auto_scroll(self, mouse_pos):
        """マウス位置に基づいて自動スクロールを開始/停止します。"""
        viewport_rect = self.viewport().rect()
        mouse_y = mouse_pos.y()
        viewport_top = viewport_rect.top()
        viewport_bottom = viewport_rect.bottom()
        
        # スクロール領域の閾値（ピクセル）
        SCROLL_THRESHOLD = 30
        
        # 上端に近い場合
        distance_from_top = mouse_y - viewport_top
        if distance_from_top < SCROLL_THRESHOLD and distance_from_top >= 0:
            # 距離に応じてスクロール速度を設定（近いほど速く、1/4の速度に調整）
            self.scroll_speed = max(1, int((SCROLL_THRESHOLD - distance_from_top) / 12))
            self.scroll_direction = -1  # 上方向
            if not self.scroll_timer.isActive():
                self.scroll_timer.start(16)  # 約60FPS
            return
        
        # 下端に近い場合
        distance_from_bottom = viewport_bottom - mouse_y
        if distance_from_bottom < SCROLL_THRESHOLD and distance_from_bottom >= 0:
            # 距離に応じてスクロール速度を設定（近いほど速く、1/4の速度に調整）
            self.scroll_speed = max(1, int((SCROLL_THRESHOLD - distance_from_bottom) / 12))
            self.scroll_direction = 1  # 下方向
            if not self.scroll_timer.isActive():
                self.scroll_timer.start(16)  # 約60FPS
            return
        
        # スクロール領域外の場合は停止
        if self.scroll_timer.isActive():
            self.scroll_timer.stop()
            self.scroll_direction = 0
            self.scroll_speed = 0

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

        error_occurred = False
        try:
            # --- ここからインデントを修正し、tryブロック内に収めます ---
            # 前回のハイライトやインジケータをクリア
            if self.last_highlighted_item:
                self.last_highlighted_item.setBackground(0, QBrush(Qt.transparent))
                self.last_highlighted_item = None

            self._remove_dummy_indicator()

            target_item = self.itemAt(event.position().toPoint())
            original_pos = self.dropIndicatorPosition()
            mouse_pos = event.position().toPoint()
            
            # --- 自動スクロールチェック ---
            self._check_and_start_auto_scroll(mouse_pos)
            
            # ターゲットがフォルダかどうかをチェック
            target_is_folder = False
            if target_item:
                path_str = target_item.data(0, Qt.UserRole)
                if path_str and Path(path_str).is_dir():
                    target_is_folder = True

                # フォルダの場合、マウス位置が上端/下端に近いかチェック（位置移動として扱う）
                pos = original_pos
                is_near_edge = False
                if target_item and target_is_folder and original_pos == self.DropIndicatorPosition.OnItem:
                    item_rect = self.visualItemRect(target_item)
                    if item_rect.isValid():
                        # 上端/下端から10ピクセル以内の場合は位置移動として扱う
                        EDGE_THRESHOLD = 10
                        mouse_y = mouse_pos.y()
                        item_top = item_rect.top()
                        item_bottom = item_rect.bottom()
                        
                        if abs(mouse_y - item_top) <= EDGE_THRESHOLD:
                            # 上端に近い → AboveItemとして扱う
                            pos = self.DropIndicatorPosition.AboveItem
                            is_near_edge = True
                        elif abs(mouse_y - item_bottom) <= EDGE_THRESHOLD:
                            # 下端に近い → BelowItemとして扱う
                            pos = self.DropIndicatorPosition.BelowItem
                            is_near_edge = True

                # --- ケース1: AboveItem/BelowItem → 位置移動（スプリットラインを表示） ---
                if pos in [self.DropIndicatorPosition.AboveItem, self.DropIndicatorPosition.BelowItem]:
                    if target_item:
                        # ターゲットアイテムの上/下にスプリットラインを表示
                        parent = target_item.parent()
                        index_offset = 1 if pos == self.DropIndicatorPosition.BelowItem else 0
                        if parent:
                            index = parent.indexOfChild(target_item)
                            parent.insertChild(index + index_offset, self.dummy_indicator_item)
                        else:
                            index = self.indexOfTopLevelItem(target_item)
                            self.insertTopLevelItem(index + index_offset, self.dummy_indicator_item)
                    else:
                        # target_itemがNoneの場合、リストの先頭または末尾にスプリットラインを表示
                        if pos == self.DropIndicatorPosition.AboveItem:
                            self.insertTopLevelItem(0, self.dummy_indicator_item)
                        else:
                            self.insertTopLevelItem(self.topLevelItemCount(), self.dummy_indicator_item)

                # --- ケース2: OnItem + フォルダ → フォルダに入れる（ハイライトのみ） ---
                elif original_pos == self.DropIndicatorPosition.OnItem and target_item and target_is_folder:
                    # is_near_edgeがFalseの場合のみハイライト（中央部分にホバーした場合）
                    if not is_near_edge:
                        target_item.setBackground(0, self.highlight_color)
                        self.last_highlighted_item = target_item
                        # スプリットラインは表示しない

                # --- ケース3: OnItem + 画像 → 位置移動（スプリットラインを表示） ---
                elif pos == self.DropIndicatorPosition.OnItem and target_item and not target_is_folder:
                    parent = target_item.parent()
                    if parent:
                        index = parent.indexOfChild(target_item)
                        parent.insertChild(index + 1, self.dummy_indicator_item)
                    else:
                        index = self.indexOfTopLevelItem(target_item)
                        self.insertTopLevelItem(index + 1, self.dummy_indicator_item)

                # --- ケース4: OnViewport → 位置移動（リストの末尾にスプリットラインを表示） ---
                elif pos == self.DropIndicatorPosition.OnViewport:
                    self.insertTopLevelItem(self.topLevelItemCount(), self.dummy_indicator_item)

        except Exception as e:
            # 例外が発生した場合、クリーンアップを実行
            error_occurred = True
            print(f"[ERROR] dragMoveEvent failed: {e}")
            # 例外が発生した場合のみ、クリーンアップを実行
            if self.last_highlighted_item:
                try:
                    self.last_highlighted_item.setBackground(0, QBrush(Qt.transparent))
                except:
                    pass
                self.last_highlighted_item = None
            try:
                self._remove_dummy_indicator()
            except:
                pass

    def dragLeaveEvent(self, event):
        if self.last_highlighted_item:
            self.last_highlighted_item.setBackground(0, QBrush(Qt.transparent))
            self.last_highlighted_item = None

        self._remove_dummy_indicator()
        
        # 自動スクロールを停止
        if self.scroll_timer.isActive():
            self.scroll_timer.stop()
            self.scroll_direction = 0
            self.scroll_speed = 0
        
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
        
        # 自動スクロールを停止
        if self.scroll_timer.isActive():
            self.scroll_timer.stop()
            self.scroll_direction = 0
            self.scroll_speed = 0

        if event.source() != self:
            super().dropEvent(event)
            return

        # 2. 移動対象とターゲットの特定
        dragged_items = self.selectedItems()
        if not dragged_items: return

        target_item = self.itemAt(event.position().toPoint())
        pos = self.dropIndicatorPosition()
        mouse_pos = event.position().toPoint()
        
        # 3. ドロップ先（親アイテム）と挿入位置（インデックス）の決定
        dest_parent = None # NoneならRoot
        insert_index = -1

        target_is_folder = False
        if target_item:
            path_str = target_item.data(0, Qt.UserRole)
            if path_str and Path(path_str).is_dir():
                target_is_folder = True

        # フォルダの場合、マウス位置が上端/下端に近いかチェック（位置移動として扱う）
        is_near_edge = False
        if target_item and target_is_folder and pos == self.DropIndicatorPosition.OnItem:
            item_rect = self.visualItemRect(target_item)
            if item_rect.isValid():
                # 上端/下端から10ピクセル以内の場合は位置移動として扱う
                EDGE_THRESHOLD = 10
                mouse_y = mouse_pos.y()
                item_top = item_rect.top()
                item_bottom = item_rect.bottom()
                
                if abs(mouse_y - item_top) <= EDGE_THRESHOLD:
                    # 上端に近い → AboveItemとして扱う
                    pos = self.DropIndicatorPosition.AboveItem
                    is_near_edge = True
                elif abs(mouse_y - item_bottom) <= EDGE_THRESHOLD:
                    # 下端に近い → BelowItemとして扱う
                    pos = self.DropIndicatorPosition.BelowItem
                    is_near_edge = True

        # --- ケース1: OnItem + フォルダ → フォルダに入れる ---
        if pos == self.DropIndicatorPosition.OnItem and target_item and target_is_folder and not is_near_edge:
            dest_parent = target_item
            insert_index = 0
        
        # --- ケース2: AboveItem/BelowItem → 位置移動 ---
        elif pos in [self.DropIndicatorPosition.AboveItem, self.DropIndicatorPosition.BelowItem]:
            if target_item:
                dest_parent = target_item.parent()
                if dest_parent:
                    target_idx = dest_parent.indexOfChild(target_item)
                else:
                    target_idx = self.indexOfTopLevelItem(target_item)
                
                if pos == self.DropIndicatorPosition.AboveItem:
                    insert_index = target_idx
                else:
                    insert_index = target_idx + 1
            else:
                # target_itemがNoneの場合
                dest_parent = None
                if pos == self.DropIndicatorPosition.AboveItem:
                    insert_index = 0
                else:
                    insert_index = self.topLevelItemCount()
        
        # --- ケース3: OnItem + 画像 → 位置移動 ---
        elif pos == self.DropIndicatorPosition.OnItem and target_item and not target_is_folder:
            dest_parent = target_item.parent()
            if dest_parent:
                target_idx = dest_parent.indexOfChild(target_item)
            else:
                target_idx = self.indexOfTopLevelItem(target_item)
            insert_index = target_idx + 1

        # --- ケース4: OnViewport → 位置移動（リストの末尾） ---
        else:
            dest_parent = None
            insert_index = self.topLevelItemCount()

        # --- ★★★ 追加: 自分自身を子要素に入れる操作を防止（ハングアップ防止） ★★★ ---
        for item in dragged_items:
            # ドロップ先がフォルダの場合、自分自身を子要素に入れようとしていないかチェック
            if dest_parent and dest_parent == item:
                # 自分自身を自分の子要素に入れようとしている
                event.ignore()
                return
            
            # ドロップ先のフォルダが、ドラッグ元のアイテムの子孫でないかチェック
            if dest_parent:
                current = dest_parent
                while current:
                    if current == item:
                        # 自分自身の子要素に入れようとしている
                        event.ignore()
                        return
                    current = current.parent()
        # -----------------------------------------------------------

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
            
            # 削除されたアイテムが存在しないかチェック
            if not src_path.exists():
                # 削除されたアイテムを移動しようとしている場合はエラー
                lm = self.config_manager.logger.locale_manager
                err_title = lm.tr("error_title_move_item_failed")
                if err_title == "error_title_move_item_failed": err_title = "Move Error"
                
                err_msg = lm.tr("log_move_item_error_not_exists")
                if err_msg == "log_move_item_error_not_exists": err_msg = f"Item '{src_path.name}' has been deleted."
                
                QMessageBox.warning(self, err_title, err_msg)
                event.ignore()
                return
            
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
            dest_path_str = str(self.config_manager.base_dir)
            if dest_parent:
                dest_path_str = dest_parent.data(0, Qt.UserRole)
            
            source_paths = [item.data(0, Qt.UserRole) for item in items_moved_list]

        except Exception as e:
            print(f"[ERROR] Drag drop failed: {e}")
        finally:
            self.blockSignals(False)

        # 5. 変更通知
        if source_parent != dest_parent:
            # フォルダ間の移動の場合、ファイル移動が完了するのを待ってから順序保存を実行するため、
            # ここではorderUpdatedシグナルを発行しない（ファイル移動完了後に発行される）
            self.itemsMoved.emit(source_paths, dest_path_str)
        else:
            # 同じ親内での移動（並べ替え）の場合、ファイル移動は不要なので即座に順序保存を実行
            self.orderUpdated.emit()
        
        # 6. 移動したアイテムを適切な位置にスクロール
        if items_moved_list:
            moved_item = items_moved_list[0]  # 最初の移動アイテムを基準にする
            
            # アイテムの位置を取得（少し待ってから取得するため、QTimerで遅延実行）
            # アイテムが削除されていないかチェックするため、パスを保存して後で検索
            moved_item_path = moved_item.data(0, Qt.UserRole) if moved_item else None
            if moved_item_path:
                QTimer.singleShot(50, lambda: self._scroll_to_moved_item_by_path(moved_item_path))
        
        event.accept()
    
    def _scroll_to_moved_item_by_path(self, item_path):
        """パスから移動したアイテムを検索してスクロールします。"""
        if not item_path:
            return
        
        # パスからアイテムを検索
        item = self._find_item_by_path(item_path)
        if not item:
            return
        
        self._scroll_to_moved_item(item)
    
    def _find_item_by_path(self, path_str):
        """パス文字列からツリーアイテムを検索します。"""
        def search_item(parent_item):
            if parent_item is None:
                # トップレベルアイテムを検索
                for i in range(self.topLevelItemCount()):
                    item = self.topLevelItem(i)
                    if item and item.data(0, Qt.UserRole) == path_str:
                        return item
                    # 子アイテムも再帰的に検索
                    if item:
                        result = search_item(item)
                        if result:
                            return result
            else:
                # 子アイテムを検索
                for i in range(parent_item.childCount()):
                    item = parent_item.child(i)
                    if item and item.data(0, Qt.UserRole) == path_str:
                        return item
                    # 孫アイテムも再帰的に検索
                    if item:
                        result = search_item(item)
                        if result:
                            return result
            return None
        
        return search_item(None)
    
    def _scroll_to_moved_item(self, item):
        """移動したアイテムを適切な位置にスクロールします。"""
        if not item:
            return
        
        try:
            # アイテムが有効かチェック（C++オブジェクトが削除されていないか）
            # visualItemRect を呼び出す前に、アイテムがツリーに存在するか確認
            parent = item.parent()
            if parent:
                # 親アイテムの子リストに存在するか確認
                found = False
                for i in range(parent.childCount()):
                    if parent.child(i) == item:
                        found = True
                        break
                if not found:
                    return
            else:
                # トップレベルアイテムか確認
                found = False
                for i in range(self.topLevelItemCount()):
                    if self.topLevelItem(i) == item:
                        found = True
                        break
                if not found:
                    return
            
            # アイテムの位置を取得
            item_rect = self.visualItemRect(item)
            if not item_rect.isValid():
                # アイテムが見えない場合は単純にスクロール
                self.scrollToItem(item, QAbstractItemView.PositionAtCenter)
                return
            
            # ビューポートの位置とサイズを取得
            viewport_rect = self.viewport().rect()
            viewport_top = viewport_rect.top()
            viewport_bottom = viewport_rect.bottom()
            viewport_height = viewport_rect.height()
            viewport_center_y = viewport_top + viewport_height // 2
            
            # アイテムの中心位置
            item_center_y = item_rect.center().y()
            
            # ビューポート内でのアイテムの相対位置を計算
            item_top_relative = item_rect.top() - viewport_top
            item_bottom_relative = item_rect.bottom() - viewport_top
            
            # 中心部分の範囲（ビューポートの30%〜70%の範囲）
            center_top_threshold = viewport_height * 0.3
            center_bottom_threshold = viewport_height * 0.7
            
            # アイテムが中心部分にあるかチェック
            if item_top_relative >= center_top_threshold and item_bottom_relative <= center_bottom_threshold:
                # 中心部分にある場合：中心にスクロール
                self.scrollToItem(item, QAbstractItemView.PositionAtCenter)
            elif item_top_relative < center_top_threshold:
                # 上部分にある場合：スクロールバーが上に来るように（アイテムを上端に）
                self.scrollToItem(item, QAbstractItemView.PositionAtTop)
            else:
                # 下部分にある場合：スクロールバーが下に来るように（アイテムを下端に）
                self.scrollToItem(item, QAbstractItemView.PositionAtBottom)
        except RuntimeError:
            # C++オブジェクトが既に削除されている場合は無視
            pass
