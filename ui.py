# ui.py (D&D機能 統合版)

import sys
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QPushButton, QVBoxLayout, QWidget, QLabel,
    QFrame, QHBoxLayout, QGroupBox, QSpinBox, QDoubleSpinBox, QCheckBox,
    QGridLayout, QSizePolicy, QSpacerItem, QToolButton, QFileDialog, QLineEdit,
    QTreeWidget, QTreeWidgetItem, QMenu, QTabWidget, QTextEdit, QDialog, QMessageBox,
    QComboBox, QDialogButtonBox, QRadioButton, QButtonGroup, QScrollArea, QAbstractItemView,
    QProxyStyle, QStyle, QStyleOptionViewItem
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

# ★★★ D&D機能のために CustomTreeStyle クラスを追加 ★★★
class CustomTreeStyle(QProxyStyle):
    """QTreeWidgetのアイテム描画に介入してドロップインジケータ線を描画するスタイル"""
    def drawControl(self, element, option, painter, widget=None):
        # アイテム描画 (CE_ItemViewItem) に介入
        if element == QStyle.CE_ItemViewItem and isinstance(widget, DraggableTreeWidget):
            # まずデフォルトのアイテム描画を実行
            super().drawControl(element, option, painter, widget)

            tree_widget = widget
            # QStyleOptionViewItemからインデックスを取得
            if isinstance(option, QStyleOptionViewItem):
                index = option.index
                item = tree_widget.itemFromIndex(index)

                # このアイテムがインジケータ描画対象か？
                if item and item == tree_widget.drop_indicator_item and tree_widget.drop_indicator_pos:
                    painter.save()
                    try:
                        pen = QPen(QColor("red"), 2) # 赤色, 2px
                        pen.setCapStyle(Qt.FlatCap)
                        painter.setPen(pen)
                        rect = option.rect # アイテムの描画矩形

                        if rect.isValid():
                            # 線のY座標 (矩形の上辺か下辺か)
                            y = rect.top() if tree_widget.drop_indicator_pos == QAbstractItemView.DropIndicatorPosition.AboveItem else rect.bottom()
                            # 線がアイテム境界の外側に来るように1pxずらす
                            y += -1 if tree_widget.drop_indicator_pos == QAbstractItemView.DropIndicatorPosition.AboveItem else 1

                            # 線の左右X座標 (少し内側に)
                            left = rect.left() + 1
                            right = rect.right() - 1

                            # 描画
                            if left < right:
                                painter.drawLine(left, y, right, y)
                    finally:
                        painter.restore()
            else:
                 # option が QStyleOptionViewItem でない場合はデフォルト処理
                 super().drawControl(element, option, painter, widget)

        else:
            # 他のコントロール要素はデフォルトの描画に任せる
            super().drawControl(element, option, painter, widget)

    # PE_IndicatorBranch の描画は完全に無効化する（線は drawControl で描くため）
    def drawPrimitive(self, element, option, painter, widget=None):
        if element == QStyle.PE_IndicatorBranch and isinstance(widget, DraggableTreeWidget):
             return # 何も描画しない
        super().drawPrimitive(element, option, painter, widget)


# ★★★ D&D機能のために DraggableTreeWidget クラスを追加 ★★★
class DraggableTreeWidget(QTreeWidget):
    """ドラッグ＆ドロップによる順序変更と視覚的フィードバックをサポートするカスタムQTreeWidget。"""
    orderUpdated = Signal()
    itemsMoved = Signal(list, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.last_highlighted_item = None
        self.highlight_color = QApplication.palette().highlight().color().lighter(150)
        self.config_manager = None # UIManagerから設定される

        # ★★★ 変更点: ダミーアイテム用の変数を保持 ★★★
        self.dummy_indicator_item = None # 挿入するダミーアイテム
        self.setDropIndicatorShown(False) # 標準インジケータは使わない

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

        # --- リセット処理 ---
        if self.last_highlighted_item:
            self.last_highlighted_item.setBackground(0, QBrush(Qt.transparent))
            self.last_highlighted_item = None
        self._remove_dummy_indicator() # 既存のダミーを削除

        # --- ドロップ先の特定とインジケータ設定 ---
        target_item = self.itemAt(event.position().toPoint())
        pos = self.dropIndicatorPosition()

        if pos == self.DropIndicatorPosition.OnItem and target_item:
            path_str = target_item.data(0, Qt.UserRole)
            if path_str and Path(path_str).is_dir():
                # フォルダの上にドロップ -> ハイライト
                target_item.setBackground(0, self.highlight_color)
                self.last_highlighted_item = target_item
            else:
                # ファイルの上 -> その下にダミーアイテムを挿入 (Belowとして扱う)
                self._insert_dummy_indicator(target_item, self.DropIndicatorPosition.BelowItem)

        elif pos in [self.DropIndicatorPosition.AboveItem, self.DropIndicatorPosition.BelowItem] and target_item:
            # アイテムの間にドロップ -> ダミーアイテムを挿入
            self._insert_dummy_indicator(target_item, pos)

        elif pos == self.DropIndicatorPosition.OnViewport:
             # ビューポート (アイテムがない場所) にドロップ -> 末尾にダミーアイテムを挿入
             self._insert_dummy_indicator(None, self.DropIndicatorPosition.BelowItem) # target_item=None で末尾扱い

    def dragLeaveEvent(self, event):
        # --- リセット処理 ---
        if self.last_highlighted_item:
            self.last_highlighted_item.setBackground(0, QBrush(Qt.transparent))
            self.last_highlighted_item = None
        self._remove_dummy_indicator()

        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        # --- リセット処理 ---
        if self.last_highlighted_item:
            self.last_highlighted_item.setBackground(0, QBrush(Qt.transparent))
            self.last_highlighted_item = None
        self._remove_dummy_indicator() # ★★★ dropEvent開始時にダミーを削除 ★★★

        # --- ドロップ処理本体 (変更なし) ---
        if event.source() != self:
            super().dropEvent(event)
            return

        target_item = self.itemAt(event.position().toPoint()) # ダミー削除後のアイテムを取得
        dragged_items = self.selectedItems()
        if not dragged_items:
            return

        source_parent = dragged_items[0].parent()
        pos = self.dropIndicatorPosition() # ダミー削除後の位置を取得

        cloned_items = [item.clone() for item in dragged_items]

        for item in dragged_items:
            parent = item.parent()
            if parent:
                parent.removeChild(item)
            else:
                self.takeTopLevelItem(self.indexOfTopLevelItem(item))

        dest_parent = None
        insert_index = -1

        if pos == self.DropIndicatorPosition.OnItem and target_item:
            path_str = target_item.data(0, Qt.UserRole)
            if path_str and Path(path_str).is_dir(): # フォルダドロップ
                dest_parent = target_item
                insert_index = 0
            else: # ファイルの上 (Below扱い)
                dest_parent = target_item.parent()
                if dest_parent:
                    insert_index = dest_parent.indexOfChild(target_item) + 1
                else:
                    insert_index = self.indexOfTopLevelItem(target_item) + 1
        elif target_item: # アイテムの間
            dest_parent = target_item.parent()
            if dest_parent:
                insert_index = dest_parent.indexOfChild(target_item)
                if pos == self.DropIndicatorPosition.BelowItem:
                    insert_index += 1
            else:
                insert_index = self.indexOfTopLevelItem(target_item)
                if pos == self.DropIndicatorPosition.BelowItem:
                    insert_index += 1
        else: # OnViewport (末尾)
             dest_parent = None
             insert_index = self.topLevelItemCount()


        if dest_parent:
            for i, item in enumerate(cloned_items):
                dest_parent.insertChild(insert_index + i, item)
        else:
            for i, item in enumerate(cloned_items):
                self.insertTopLevelItem(insert_index + i, item)

        self.clearSelection()
        for item in cloned_items:
            item.setSelected(True)
            self.scrollToItem(item)

        if source_parent != dest_parent:
            dest_path = str(self.config_manager.base_dir) if dest_parent is None else dest_parent.data(0, Qt.UserRole)
            source_paths = [dragged.data(0, Qt.UserRole) for dragged in dragged_items if dragged.data(0, Qt.UserRole)]
            if source_paths and dest_path:
                self.itemsMoved.emit(source_paths, dest_path)

        self.orderUpdated.emit()
        event.accept()

    # ★★★ 変更点: ダミーアイテム挿入メソッドを修正 ★★★
    def _insert_dummy_indicator(self, target_item, pos):
        if self.dummy_indicator_item: # 既にあったら削除
             self._remove_dummy_indicator()

        self.dummy_indicator_item = QTreeWidgetItem()
        self.dummy_indicator_item.setText(0, "――――――　") # 罫線テキストを設定
        self.dummy_indicator_item.setForeground(0, QBrush(QColor("red"))) # 文字色を赤に
        # self.dummy_indicator_item.setSizeHint(0, QSize(0, 5)) # 高さはテキストに任せるので削除
        self.dummy_indicator_item.setFlags(Qt.ItemIsEnabled) # 選択不可にする

        if target_item:
            parent = target_item.parent()
            if parent: # 子アイテムの場合
                index = parent.indexOfChild(target_item)
                if pos == self.DropIndicatorPosition.BelowItem:
                    index += 1
                parent.insertChild(index, self.dummy_indicator_item)
            else: # トップレベルアイテムの場合
                index = self.indexOfTopLevelItem(target_item)
                if pos == self.DropIndicatorPosition.BelowItem:
                    index += 1
                self.insertTopLevelItem(index, self.dummy_indicator_item)
        else: # target_item が None (末尾) の場合
            self.addTopLevelItem(self.dummy_indicator_item)

    # ★★★ 変更点なし: ダミーアイテム削除メソッド ★★★
    def _remove_dummy_indicator(self):
        if self.dummy_indicator_item:
            parent = self.dummy_indicator_item.parent()
            if parent:
                parent.removeChild(self.dummy_indicator_item)
            else:
                 index = self.indexOfTopLevelItem(self.dummy_indicator_item)
                 if index != -1:
                    self.takeTopLevelItem(index)
            self.dummy_indicator_item = None


class UIManager(QMainWindow):
    startMonitoringRequested = Signal(); stopMonitoringRequested = Signal(); openPerformanceMonitorRequested = Signal()
    loadImagesRequested = Signal(list); setRecAreaMethodSelected = Signal(str); captureImageRequested = Signal()
    
    # ★★★ D&D変更点: deleteItemRequested を deleteItemsRequested に変更 (複数削除対応) ★★★
    deleteItemsRequested = Signal(list)
    orderChanged = Signal()
    # ★★★ D&D変更点: D&Dによるアイテム移動シグナルを追加 ★★★
    itemsMovedIntoFolder = Signal(list, str) 
    
    folderSettingsChanged = Signal()
    imageSettingsChanged = Signal(dict); createFolderRequested = Signal(); moveItemIntoFolderRequested = Signal()
    moveItemOutOfFolderRequested = Signal()
    appConfigChanged = Signal()

    def __init__(self, core_engine, capture_manager, config_manager, logger):
        super().__init__(parent=None)
        self.core_engine, self.capture_manager, self.config_manager, self.logger = core_engine, capture_manager, config_manager, logger
        self.item_settings_widgets = {}
        self.app_settings_widgets = {}
        self.auto_scale_widgets = {}

        self.setWindowTitle("Imeck15 v1.4.1 (D&D 統合版)") # バージョン情報は任意
        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint)

        self.save_timer = QTimer(self); self.save_timer.setSingleShot(True); self.save_timer.setInterval(1000)
        self.is_processing_tree_change = False
        
        self.app_config = self.config_manager.load_app_config()
        
        self.performance_monitor = None
        self.is_minimal_mode = False
        self.normal_ui_geometries = {}
        self.floating_window = None
        
        self.setup_ui()
        self.load_app_settings_to_ui()

        self.preview_mode_manager = PreviewModeManager(
            roi_button=self.item_settings_widgets['set_roi_variable_button'],
            point_cb=self.item_settings_widgets['point_click'],
            range_cb=self.item_settings_widgets['range_click'],
            random_cb=self.item_settings_widgets['random_click']
        )
        
        QTimer.singleShot(100, self.adjust_initial_size)
    
    def set_performance_monitor(self, monitor):
        self.performance_monitor = monitor
        
    def setup_ui(self):
        # (このメソッドは変更ありません)
        central_widget = QWidget(); self.setCentralWidget(central_widget); main_layout = QVBoxLayout(central_widget)
        header_frame = QFrame(); header_layout = QHBoxLayout(header_frame)
        self.monitor_button = QPushButton("監視開始"); self.monitor_button.setFixedSize(100, 30)
        self.monitor_button.setToolTip(
            "監視を開始します。\n"
            "右ダブルクリックで停止・右トリプルクリックで開始"
        )
        header_layout.addWidget(self.monitor_button)
        self.perf_monitor_button = QPushButton("パフォーマンス"); self.perf_monitor_button.setFixedSize(120, 30); header_layout.addWidget(self.perf_monitor_button)
        self.header_rec_area_button = QPushButton("認識範囲設定"); self.header_rec_area_button.setFixedSize(120, 30); self.header_rec_area_button.clicked.connect(self.setRecAreaDialog)
        header_layout.addWidget(self.header_rec_area_button)
        
        self.toggle_minimal_ui_button = QPushButton("最小UIモード")
        self.toggle_minimal_ui_button.setFixedSize(120, 30)
        header_layout.addWidget(self.toggle_minimal_ui_button)

        self.open_image_folder_button = QPushButton("画像フォルダ")
        self.open_image_folder_button.setFixedSize(120, 30)
        self.open_image_folder_button.setToolTip("登録画像が保存されているフォルダを開きます")
        header_layout.addWidget(self.open_image_folder_button)
        
        header_layout.addSpacerItem(QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        self.status_label = QLabel("待機中"); self.status_label.setStyleSheet("font-size: 16px; font-weight: bold; color: green;"); header_layout.addWidget(self.status_label)
        main_layout.addWidget(header_frame); content_frame = QFrame(); content_layout = QHBoxLayout(content_frame)
        left_frame = QFrame(); left_layout = QVBoxLayout(left_frame); left_layout.addWidget(QLabel("登録済み画像"))
        order_button_frame = QHBoxLayout(); move_up_button = QPushButton("▲ 上げる"); move_down_button = QPushButton("▼ 下げる")
        order_button_frame.addWidget(move_up_button); order_button_frame.addWidget(move_down_button); left_layout.addLayout(order_button_frame)
        
        # ★★★ D&D変更点: QTreeWidget を DraggableTreeWidget に変更 ★★★
        self.image_tree = DraggableTreeWidget()
        self.image_tree.config_manager = self.config_manager
        self.image_tree.setSelectionMode(QAbstractItemView.ExtendedSelection) # 複数選択を許可
        self.image_tree.setDragDropMode(QAbstractItemView.InternalMove) # D&Dモードを内部移動に設定
        self.image_tree.setDragEnabled(True)
        self.image_tree.setAcceptDrops(True)
        self.image_tree.setDropIndicatorShown(False) # カスタムインジケータを使うため標準はOFF
        self.image_tree.setContextMenuPolicy(Qt.CustomContextMenu) # 右クリックメニュー用にポリシー変更

        self.image_tree.setStyleSheet("""
            QTreeWidget {
                border: 1px solid darkgray;
                border-radius: 0px;
            }
        """)
        self.image_tree.setHeaderHidden(True); left_layout.addWidget(self.image_tree)
        button_layout = QGridLayout(); load_image_button = QPushButton("画像追加"); button_layout.addWidget(load_image_button, 0, 0)
        capture_image_button = QPushButton("画像キャプチャ"); button_layout.addWidget(capture_image_button, 0, 1)
        delete_item_button = QPushButton("選択を削除"); button_layout.addWidget(delete_item_button, 1, 0)
        create_folder_button = QPushButton("フォルダを作成"); button_layout.addWidget(create_folder_button, 1, 1)
        move_in_button = QPushButton("フォルダに入れる"); button_layout.addWidget(move_in_button, 2, 0)
        move_out_button = QPushButton("フォルダから出す"); button_layout.addWidget(move_out_button, 2, 1)
        
        load_image_button.clicked.connect(self.load_images_dialog); capture_image_button.clicked.connect(self.captureImageRequested.emit)
        
        # ★★★ D&D変更点: 削除ボタンの接続先を変更 (複数削除対応のため) ★★★
        delete_item_button.clicked.connect(self.on_delete_button_clicked)
        
        move_up_button.clicked.connect(self.move_item_up); move_down_button.clicked.connect(self.move_item_down)
        create_folder_button.clicked.connect(self.createFolderRequested.emit); move_in_button.clicked.connect(self.moveItemIntoFolderRequested.emit); move_out_button.clicked.connect(self.moveItemOutOfFolderRequested.emit)
        left_layout.addLayout(button_layout); content_layout.addWidget(left_frame, 1)
        
        # --- 右側のUI (変更なし) ---
        right_frame = QFrame(); right_layout = QVBoxLayout(right_frame)
        self.preview_tabs = QTabWidget()
        main_preview_widget = QWidget(); main_preview_layout = QVBoxLayout(main_preview_widget)
        self.preview_label = InteractivePreviewLabel(); self.preview_label.setAlignment(Qt.AlignCenter)
        main_preview_layout.addWidget(self.preview_label)
        self.preview_tabs.addTab(main_preview_widget, "画像プレビュー")
        rec_area_widget = QWidget(); rec_area_layout = QVBoxLayout(rec_area_widget)
        rec_area_buttons_layout = QHBoxLayout()
        self.set_rec_area_button_main_ui = QPushButton("認識範囲設定"); self.clear_rec_area_button_main_ui = QPushButton("クリア")
        rec_area_buttons_layout.addWidget(self.set_rec_area_button_main_ui); rec_area_buttons_layout.addWidget(self.clear_rec_area_button_main_ui); rec_area_layout.addLayout(rec_area_buttons_layout)
        self.rec_area_preview_label = ScaledPixmapLabel("認識範囲プレビュー"); self.rec_area_preview_label.setAlignment(Qt.AlignCenter)
        rec_area_layout.addWidget(self.rec_area_preview_label)
        self.preview_tabs.addTab(rec_area_widget, "認識範囲")
        log_widget = QWidget(); log_layout = QVBoxLayout(log_widget)
        self.log_text = QTextEdit(); self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)
        self.preview_tabs.addTab(log_widget, "ログ")
        self.auto_scale_group = QGroupBox(); auto_scale_layout = QGridLayout(self.auto_scale_group)
        self.auto_scale_widgets['use_window_scale'] = QCheckBox("ウィンドウスケール基準")
        self.auto_scale_widgets['use_window_scale'].setToolTip(
            "ON: ウィンドウや探索で得られた最適スケールをテンプレートに適用します。\n"
            "OFF: スケール補正を無効にし、常に元の画像サイズ(1.0倍)で認識を試みます。"
        )
        auto_scale_layout.addWidget(self.auto_scale_widgets['use_window_scale'], 0, 0, 1, 2)
        self.auto_scale_widgets['enabled'] = QCheckBox("スケール検索を有効にする")
        auto_scale_layout.addWidget(self.auto_scale_widgets['enabled'], 1, 0, 1, 2)
        auto_scale_layout.addWidget(QLabel("中心:"), 2, 0); self.auto_scale_widgets['center'] = QDoubleSpinBox(); self.auto_scale_widgets['center'].setRange(0.5, 2.0); self.auto_scale_widgets['center'].setSingleStep(0.1); auto_scale_layout.addWidget(self.auto_scale_widgets['center'], 2, 1)
        auto_scale_layout.addWidget(QLabel("範囲(±):"), 2, 2); self.auto_scale_widgets['range'] = QDoubleSpinBox(); self.auto_scale_widgets['range'].setRange(0.1, 0.5); self.auto_scale_widgets['range'].setSingleStep(0.05); auto_scale_layout.addWidget(self.auto_scale_widgets['range'], 2, 3)
        auto_scale_layout.addWidget(QLabel("ステップ数:"), 3, 0); self.auto_scale_widgets['steps'] = QSpinBox(); self.auto_scale_widgets['steps'].setRange(3, 11); self.auto_scale_widgets['steps'].setSingleStep(2); auto_scale_layout.addWidget(self.auto_scale_widgets['steps'], 3, 1)
        self.auto_scale_info_label = QLabel("探索: 0.80 ... 1.20"); auto_scale_layout.addWidget(self.auto_scale_info_label, 3, 2, 1, 2)
        scale_info_layout = QHBoxLayout()
        self.current_best_scale_label = QLabel("最適スケール: ---")
        font = self.current_best_scale_label.font(); font.setBold(True)
        self.current_best_scale_label.setFont(font)
        self.current_best_scale_label.setStyleSheet("color: gray;")
        scale_info_layout.addWidget(self.current_best_scale_label)
        scale_info_layout.addSpacerItem(QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        auto_scale_layout.addLayout(scale_info_layout, 4, 0, 1, 4)
        as_desc_label = QLabel(
            "<b>ウィンドウスケール基準:</b><br>"
            "認識範囲をウィンドウに設定すると、その基準サイズからの拡縮率を自動計算し、スケールとして適用します。<br><br>"
            "<b>スケール検索:</b><br>"
            "上記が使えない場合、これを有効にすると設定した範囲で最適なスケールを探索します。監視開始直後の負荷が高くなります。"
        )
        as_desc_label.setWordWrap(True)
        as_desc_label.setStyleSheet("font-size: 11px; color: #555555;")
        as_desc_label.setMinimumWidth(0)
        auto_scale_layout.addWidget(as_desc_label, 5, 0, 1, 4)
        self.auto_scale_group.setFlat(True)
        self.preview_tabs.addTab(self.auto_scale_group, "自動スケール")
        app_settings_scroll_area = QScrollArea()
        app_settings_scroll_area.setWidgetResizable(True)
        app_settings_scroll_area.setStyleSheet("QScrollArea { border: 0; }")
        app_settings_widget = QWidget()
        app_settings_layout = QVBoxLayout(app_settings_widget)
        app_settings_layout.setSpacing(10)
        app_settings_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.app_settings_widgets['grayscale_matching'] = QCheckBox("グレースケール検索 (高速)")
        app_settings_layout.addWidget(self.app_settings_widgets['grayscale_matching'])
        gs_desc_label = QLabel("<b>メリット:</b> 処理が高速になり、僅かな色のの違いを無視できます。<br>"
                               "<b>デメリット:</b> 同じ形で色が違うだけの画像は区別できません。")
        gs_desc_label.setWordWrap(True)
        gs_desc_label.setStyleSheet("font-size: 11px; color: #555555; padding-left: 20px;")
        app_settings_layout.addWidget(gs_desc_label)
        self.app_settings_widgets['capture_method'] = QCheckBox("DXCamを使用")
        self.app_settings_widgets['capture_method'].setEnabled(DXCAM_AVAILABLE)
        app_settings_layout.addWidget(self.app_settings_widgets['capture_method'])
        dxcam_desc_label = QLabel("<b>メリット:</b> ゲーム等の描画に強く、CPU負荷が低い高速なキャプチャ方式です。<br>"
                                  "<b>デメリット:</b> 一部のアプリやPC環境では動作しない場合があります。")
        dxcam_desc_label.setWordWrap(True)
        dxcam_desc_label.setStyleSheet("font-size: 11px; color: #555555; padding-left: 20px;")
        app_settings_layout.addWidget(dxcam_desc_label)
        self.app_settings_widgets['eco_mode_enabled'] = QCheckBox("省エネモード")
        app_settings_layout.addWidget(self.app_settings_widgets['eco_mode_enabled'])
        eco_desc_label = QLabel("クリック後、5秒間マッチする画像がない場合にCPU負荷を低減するため、監視を1秒に1回の低頻度モードに移行します。")
        eco_desc_label.setWordWrap(True)
        eco_desc_label.setStyleSheet("font-size: 11px; color: #555555; padding-left: 20px;")
        app_settings_layout.addWidget(eco_desc_label)
        fs_layout = QHBoxLayout()
        fs_layout.addWidget(QLabel("フレームスキップ:"))
        self.app_settings_widgets['frame_skip_rate'] = QSpinBox()
        self.app_settings_widgets['frame_skip_rate'].setRange(1, 20)
        fs_layout.addWidget(self.app_settings_widgets['frame_skip_rate'])
        fs_layout.addStretch()
        app_settings_layout.addLayout(fs_layout)
        fs_desc_label = QLabel("<b>メリット:</b> 値を大きくするとCPU負荷が下がります。<br>"
                               "<b>デメリット:</b> 画面の急な変化に対する反応が遅くなります。")
        fs_desc_label.setWordWrap(True)
        fs_desc_label.setStyleSheet("font-size: 11px; color: #555555; padding-left: 20px;")
        app_settings_layout.addWidget(fs_desc_label)
        self.app_settings_widgets['use_opencl'] = QCheckBox("OpenCL (GPU支援) を使用")
        self.app_settings_widgets['use_opencl'].setEnabled(OPENCL_AVAILABLE)
        app_settings_layout.addWidget(self.app_settings_widgets['use_opencl'])
        opencl_desc_label = QLabel(
            "<b>メリット:</b> GPUを利用して画像処理を高速化します。特に高解像度の画面や大きな画像の認識時にCPU負荷を下げ、パフォーマンスを向上させます。<br>"
            "<b>デメリット:</b> 処理によっては僅かなオーバーヘッドが発生します。また、GPUドライバとの相性問題が発生する場合があります。<br><br>"
            "<font color='red'><b>【注意】</b>Linux環境や特定のゲームとの併用時に、"
            "<code>amdgpu_cs_query_fence_status failed</code> のようなエラーが出て不安定になる場合は、"
            "このオプションを<b>オフ</b>にしてください。</font>"
        )
        opencl_desc_label.setWordWrap(True)
        opencl_desc_label.setStyleSheet("font-size: 11px; color: #555555; padding-left: 20px;")
        app_settings_layout.addWidget(opencl_desc_label)
        stability_group = QGroupBox("画面安定性チェック")
        stability_layout = QGridLayout(stability_group)
        self.app_settings_widgets['stability_check_enabled'] = QCheckBox("有効にする")
        stability_layout.addWidget(self.app_settings_widgets['stability_check_enabled'], 0, 0)
        threshold_layout = QHBoxLayout()
        threshold_layout.addWidget(QLabel("閾値:"))
        self.app_settings_widgets['stability_threshold'] = QSpinBox()
        self.app_settings_widgets['stability_threshold'].setRange(0, 20)
        threshold_layout.addWidget(self.app_settings_widgets['stability_threshold'])
        threshold_layout.addStretch()
        stability_layout.addLayout(threshold_layout, 0, 1)
        stability_desc_label = QLabel(
            "画面の描画中やエフェクト発生時を検出し、安定するまでクリックを保留します。<br>"
            "数値を大きくすると、より大きな画面変化があっても「安定」とみなすようになります。"
        )
        stability_desc_label.setWordWrap(True)
        stability_desc_label.setStyleSheet("font-size: 11px; color: #555555;")
        stability_layout.addWidget(stability_desc_label, 1, 0, 1, 2)
        app_settings_layout.addWidget(stability_group)
        lw_mode_group = QGroupBox("軽量化モード")
        lw_mode_layout = QVBoxLayout(lw_mode_group)
        self.app_settings_widgets['lightweight_mode_enabled'] = QCheckBox("軽量化モードを有効にする")
        lw_mode_layout.addWidget(self.app_settings_widgets['lightweight_mode_enabled'])
        preset_layout = QHBoxLayout()
        preset_layout.addWidget(QLabel("プリセット:"))
        self.app_settings_widgets['lightweight_mode_preset'] = QComboBox()
        self.app_settings_widgets['lightweight_mode_preset'].addItems(["標準", "パフォーマンス", "ウルトラ"])
        preset_layout.addWidget(self.app_settings_widgets['lightweight_mode_preset'])
        preset_layout.addStretch()
        lw_mode_layout.addLayout(preset_layout)
        cs_desc_label = QLabel(
            "<b>標準 (Standard):</b> デフォルト設定 (スケール:0.5倍, スキップ:+5)<br>"
            "<b>パフォーマンス (Performance):</b> 標準より高いパフォーマンスを発揮します (スケール:0.4倍, スキップ:+20)<br>"
            "<b>ウルトラ (Ultra):</b> 最大限の軽量化を試みますが、環境によっては動作しない可能性があります (スケール:0.3倍, スキップ:+25)<br>"
            "<br><b>注意:</b> 軽量化モードを有効にすると、自動スケール機能は無効になります。"
        )
        cs_desc_label.setWordWrap(True)
        cs_desc_label.setStyleSheet("font-size: 11px; color: #555555; padding-left: 20px;")
        lw_mode_layout.addWidget(cs_desc_label)
        app_settings_layout.addWidget(lw_mode_group)
        app_settings_scroll_area.setWidget(app_settings_widget)
        self.preview_tabs.addTab(app_settings_scroll_area, "アプリ設定")
        usage_widget = QWidget()
        usage_layout = QVBoxLayout(usage_widget)
        usage_text = QTextEdit()
        usage_text.setReadOnly(True)
        usage_html = """
        <!DOCTYPE html>
        <html>
        <head>
        <style>
            body { font-family: sans-serif; font-size: 13px; }
            h3 { color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 5px;}
            h4 { color: #34495e; margin-top: 15px; margin-bottom: 5px; }
            p, li { line-height: 1.6; }
            b { color: #e74c3c; }
            code { background-color: #f4f4f4; padding: 2px 4px; border-radius: 3px; font-family: monospace; }
            .important { border-left: 3px solid #f39c12; padding-left: 10px; background-color: #fef9e7; margin: 10px 0;}
        </style>
        </head>
        <body>
            <h3>Imeck15 画像ごとのクリック設定ガイド</h3>
            <p>
                このガイドでは、登録した画像を見つけたときに、どのようにクリック動作をさせるかを設定する方法について説明します。主にウィンドウで表示されるアプリケーションの操作を自動化することを目的としています。
            </p>

            <h4>1. クリックさせたい画像の登録方法</h4>
            <p>
                まず、クリックの目印となる画像を登録します。「画像キャプチャ」機能を使うのが基本です。<br>
                <b>ポイント：</b>ボタンやアイコンなど、クリックしたい対象を<b>部品のように小さく切り取る</b>ことをお勧めします。
            </p>
            <ul>
                <li><b>理由1：処理が高速になる</b><br>画面全体から探すよりも、小さな画像を探す方がPCへの負荷が軽くなります。</li>
                <li><b>理由2：正確なクリックができる</b><br>画面内に同じボタンが複数あっても、特定の部分だけを切り取っておけば、狙った場所を正確にクリックできます。</li>
            </ul>

            <h4>2. クリックの順番をコントロールする方法（インターバル設定）</h4>
            <p>
                「インターバル」は、一度クリックしてから次に<b>同じ画像</b>を再度クリックするまでの最低待ち時間（秒）です。
            </p>
            <div class="important">
                <b>【重要】クリックの優先順位の仕組み</b><br>
                監視中にクリック可能な画像が画面内に複数見つかった場合、Imeck15は<b>「インターバル」の設定値が最も短いものを優先してクリック</b>し、他の画像へのクリックは行いません。この仕組みを利用して、クリックの順序を制御します。
            </div>

            <h4>3. 1つの画面で複数の場所をクリックするテクニック</h4>
            <p>
                上記の「インターバル設定」の仕組みを応用すると、1つの画面で複数の箇所を順番にクリックさせることができます。<br>
                <b>前提条件：</b>クリックすると、その場所の画像や文字が変化する（消える、グレーアウトするなど）必要があります。
            </p>
            <p><b>設定手順の例：</b></p>
            <ol>
                <li>画面内でクリックしたい部品A、B、Cをそれぞれ画像として登録します。</li>
                <li>クリックしたい順番に、インターバルの時間を短く設定します。(例: A: <code>1.5</code>秒, B: <code>2.0</code>秒, C: <code>2.5</code>秒)</li>
                <li>監視を開始すると、まずインターバルが最も短い<b>部品A</b>がクリックされます。</li>
                <li>クリック後、部品Aが画面から消えると、次の監視では<b>部品B</b>がクリック対象になります。</li>
                <li>同様に、最後に<b>部品C</b>がクリックされます。</li>
            </ol>

            <h4>4. 認識の精度と範囲を調整する方法</h4>
            <ul>
                <li><b>認識精度（閾値）：</b><br>画像がどれくらい似ていたら「同じ」と判断するかの設定です。通常は<code>0.8</code>程度で十分ですが、僅かな文字の違いなどを厳密に区別したい場合は<code>0.9</code>以上に設定すると効果的です。</li>
                <li><b>探索範囲（ROI設定）：</b><br>「ROI有効」にすると、クリック座標を中心とした<b>200x200ピクセルの範囲のみ</b>を探索対象にします。処理が非常に高速になり、PCへの負荷を大幅に軽減できます。</li>
            </ul>

            <h4>5. 特殊な状況で役立つ「デバウンス」設定</h4>
            <p>
                「デバウンス」は、「短いインターバルの画像Aをクリックした後、別の画像Bをクリックし、その後、少し間を置いてから再び画像Aで次の画面に進む」といった複雑な操作を実現したい場合に使用します。
            </p>
            <p>
                <b>仕組み：</b>デバウンス時間を設定すると、同じ画像が連続でクリック対象になった場合、2回目のクリックまでの待ち時間が<b>「インターバル ＋ デバウンス時間」</b>に延長されます。これにより、他の画像が先にクリックされる機会を作ることができます。
            </p>
        </body>
        </html>
        """
        usage_text.setHtml(usage_html)
        usage_layout.addWidget(usage_text)
        usage_widget.setLayout(usage_layout)
        self.preview_tabs.addTab(usage_widget, "使い方")
        right_layout.addWidget(self.preview_tabs, 2)
        item_settings_group = QGroupBox("画像ごとの設定")
        item_settings_layout = QGridLayout(item_settings_group)
        item_settings_layout.setColumnStretch(1, 1)
        item_settings_layout.setColumnStretch(3, 1)
        item_settings_layout.addWidget(QLabel("認識精度:"), 0, 0)
        self.item_settings_widgets['threshold'] = QDoubleSpinBox()
        self.item_settings_widgets['threshold'].setRange(0.5, 1.0)
        self.item_settings_widgets['threshold'].setSingleStep(0.01)
        self.item_settings_widgets['threshold'].setValue(0.8)
        item_settings_layout.addWidget(self.item_settings_widgets['threshold'], 0, 1)
        item_settings_layout.addWidget(QLabel("インターバル(秒):"), 0, 2)
        self.item_settings_widgets['interval_time'] = QDoubleSpinBox()
        self.item_settings_widgets['interval_time'].setRange(0.1, 10.0)
        self.item_settings_widgets['interval_time'].setSingleStep(0.1)
        self.item_settings_widgets['interval_time'].setValue(1.5)
        item_settings_layout.addWidget(self.item_settings_widgets['interval_time'], 0, 3)
        self.item_settings_widgets['backup_click'] = QCheckBox("バックアップクリック")
        item_settings_layout.addWidget(self.item_settings_widgets['backup_click'], 1, 0)
        self.item_settings_widgets['backup_time'] = QDoubleSpinBox()
        self.item_settings_widgets['backup_time'].setRange(1.0, 600.0)
        self.item_settings_widgets['backup_time'].setSingleStep(1.0)
        self.item_settings_widgets['backup_time'].setValue(300.0)
        item_settings_layout.addWidget(self.item_settings_widgets['backup_time'], 1, 1)
        item_settings_layout.addWidget(QLabel("デバウンス(秒):"), 1, 2)
        self.item_settings_widgets['debounce_time'] = QDoubleSpinBox()
        self.item_settings_widgets['debounce_time'].setRange(0.0, 10.0)
        self.item_settings_widgets['debounce_time'].setSingleStep(0.1)
        self.item_settings_widgets['debounce_time'].setValue(0.0)
        self.item_settings_widgets['debounce_time'].setToolTip(
            "連続で同じ画像がマッチした際、2回目のクリックタイミングを「インターバル＋デバウンス時間」に延長します。\n"
            "これにより、インターバルがより長い他の画像が先にクリックされる機会を作ることができます。"
        )
        item_settings_layout.addWidget(self.item_settings_widgets['debounce_time'], 1, 3)
        click_type_layout = QHBoxLayout()
        self.item_settings_widgets['point_click'] = QCheckBox("1点クリック")
        self.item_settings_widgets['range_click'] = QCheckBox("範囲クリック")
        self.item_settings_widgets['random_click'] = QCheckBox("範囲内ランダム")
        self.item_settings_widgets['point_click'].setToolTip("プレビュー画像上の1点をクリック座標として設定します。")
        self.item_settings_widgets['range_click'].setToolTip("プレビュー画像上で矩形範囲を設定し、その中心またはランダムな位置をクリックします。")
        self.item_settings_widgets['random_click'].setToolTip("範囲クリックが有効な場合、クリック座標を範囲内でランダムに決定します。")
        click_type_layout.addWidget(self.item_settings_widgets['point_click'])
        click_type_layout.addWidget(self.item_settings_widgets['range_click'])
        click_type_layout.addWidget(self.item_settings_widgets['random_click'])
        item_settings_layout.addLayout(click_type_layout, 2, 0, 1, 4)
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        item_settings_layout.addWidget(separator, 3, 0, 1, 4)
        self.item_settings_widgets['roi_enabled'] = QCheckBox("ROI有効")
        self.item_settings_widgets['roi_enabled'].setToolTip(
            "ROI (Region of Interest) を有効にすると、指定した範囲のみを探索対象とします。\n"
            "これにより、画面全体を探索するよりも高速にマッチングが行え、処理負荷を軽減できます。\n\n"
            "・固定: クリック座標を中心に200x200ピクセルの範囲を自動設定します。\n"
            "・可変: プレビュー上でドラッグして、探索範囲を自由に設定できます。"
        )
        item_settings_layout.addWidget(self.item_settings_widgets['roi_enabled'], 4, 0)
        roi_mode_layout = QHBoxLayout()
        self.item_settings_widgets['roi_mode_fixed'] = QRadioButton("固定")
        self.item_settings_widgets['roi_mode_variable'] = QRadioButton("可変")
        self.item_settings_widgets['roi_mode_fixed'].setToolTip("設定されたクリック座標を中心に、固定の200x200ピクセル範囲をROIとします。")
        self.item_settings_widgets['roi_mode_variable'].setToolTip("プレビュー上でドラッグして、任意の探索範囲を設定します。")
        self.roi_mode_group = QButtonGroup(self)
        self.roi_mode_group.addButton(self.item_settings_widgets['roi_mode_fixed'])
        self.roi_mode_group.addButton(self.item_settings_widgets['roi_mode_variable'])
        roi_mode_layout.addWidget(self.item_settings_widgets['roi_mode_fixed'])
        roi_mode_layout.addWidget(self.item_settings_widgets['roi_mode_variable'])
        item_settings_layout.addLayout(roi_mode_layout, 4, 1)
        self.item_settings_widgets['set_roi_variable_button'] = QPushButton("ROI範囲設定")
        self.item_settings_widgets['set_roi_variable_button'].setCheckable(True)
        self.item_settings_widgets['set_roi_variable_button'].setToolTip(
            "画像の認識領域（ROI）を設定します。\n"
            "設定中は、クリック座標や範囲の描画がROI内での相対座標になります。\n"
            "再押下で設定を解除します。"
        )
        item_settings_layout.addWidget(self.item_settings_widgets['set_roi_variable_button'], 4, 2, 1, 2)
        right_layout.addWidget(item_settings_group, 1)
        content_layout.addWidget(right_frame, 2)
        main_layout.addWidget(content_frame)

    def is_dark_mode(self):
        palette = self.palette()
        window_color = palette.color(QPalette.ColorRole.Window)
        text_color = palette.color(QPalette.ColorRole.WindowText)
        return window_color.lightness() < text_color.lightness()

    def load_app_settings_to_ui(self):
        # (v1.4.1から変更なし)
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
        self.app_settings_widgets['eco_mode_enabled'].setChecked(eco_conf.get('enabled', False))

        stability_conf = self.app_config.get('screen_stability_check', {})
        self.app_settings_widgets['stability_check_enabled'].setChecked(stability_conf.get('enabled', True))
        # ★★★ v1.4.1 (ui.py) の '5' から D&D版 (ui(D&D).py) の '8' に修正 ★★★
        self.app_settings_widgets['stability_threshold'].setValue(stability_conf.get('threshold', 8))

        lw_conf = self.app_config.get('lightweight_mode', {})
        self.app_settings_widgets['lightweight_mode_enabled'].setChecked(lw_conf.get('enabled', False))
        self.app_settings_widgets['lightweight_mode_preset'].setCurrentText(lw_conf.get('preset', '標準'))
        
        self.update_auto_scale_info()
        self.update_dependent_widgets_state()

    def update_dependent_widgets_state(self):
        # (v1.4.1から変更なし)
        is_lw_mode_enabled = self.app_settings_widgets['lightweight_mode_enabled'].isChecked()
        self.auto_scale_group.setEnabled(not is_lw_mode_enabled)
        self.app_settings_widgets['lightweight_mode_preset'].setEnabled(is_lw_mode_enabled)
        is_stability_enabled = self.app_settings_widgets['stability_check_enabled'].isChecked()
        self.app_settings_widgets['stability_threshold'].setEnabled(is_stability_enabled)
        is_fs_user_configurable = not is_lw_mode_enabled
        self.app_settings_widgets['frame_skip_rate'].setEnabled(is_fs_user_configurable)

    def get_auto_scale_settings(self) -> dict:
        # (v1.4.1から変更なし)
        return {
            "use_window_scale": self.auto_scale_widgets['use_window_scale'].isChecked(),
            "enabled": self.auto_scale_widgets['enabled'].isChecked(),
            "center": self.auto_scale_widgets['center'].value(),
            "range": self.auto_scale_widgets['range'].value(),
            "steps": self.auto_scale_widgets['steps'].value()
        }

    def update_auto_scale_info(self):
        # (v1.4.1から変更なし)
        if self.auto_scale_widgets['enabled'].isChecked():
            center = self.auto_scale_widgets['center'].value()
            range_ = self.auto_scale_widgets['range'].value()
            steps = self.auto_scale_widgets['steps'].value()
            scales = np.linspace(center - range_, center + range_, steps)
            self.auto_scale_info_label.setText(f"探索: {scales[0]:.3f} ... {scales[-1]:.3f}")
            self.auto_scale_info_label.setStyleSheet("color: blue;")
        else:
            self.auto_scale_info_label.setText("無効")
            self.auto_scale_info_label.setStyleSheet("color: gray;")

    def on_app_settings_changed(self):
        # (v1.4.1から変更なし、stability_threshold のデフォルト値は load_app_settings_to_ui でセットされる)
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
        self.app_config['lightweight_mode'] = {
            "enabled": self.app_settings_widgets['lightweight_mode_enabled'].isChecked(),
            "preset": self.app_settings_widgets['lightweight_mode_preset'].currentText()
        }
        self.config_manager.save_app_config(self.app_config)
        self.update_auto_scale_info()
        self.update_dependent_widgets_state()
        self.appConfigChanged.emit()

    def connect_signals(self):
        # ★★★ D&D変更点: 接続ロジックを D&D版 (ui(D&D).py) に合わせる ★★★
        if hasattr(self, '_signals_connected') and self._signals_connected:
            return
            
        self.monitor_button.clicked.connect(self.toggle_monitoring)
        self.perf_monitor_button.clicked.connect(self.openPerformanceMonitorRequested.emit)
        self.image_tree.itemSelectionChanged.connect(self.on_image_tree_selection_changed)
        
        # itemClicked -> customContextMenuRequested
        self.image_tree.customContextMenuRequested.connect(self.on_tree_context_menu)
        # D&Dシグナルを追加
        self.image_tree.orderUpdated.connect(self.orderChanged.emit)
        self.image_tree.itemsMoved.connect(self.itemsMovedIntoFolder.emit)

        self.set_rec_area_button_main_ui.clicked.connect(self.setRecAreaDialog)
        self.toggle_minimal_ui_button.clicked.connect(self.toggle_minimal_ui_mode)
        self.open_image_folder_button.clicked.connect(self.open_image_folder)
        
        for widget in self.item_settings_widgets.values():
            if isinstance(widget, QDoubleSpinBox): widget.valueChanged.connect(self.on_item_settings_changed)
            elif isinstance(widget, QCheckBox): widget.stateChanged.connect(self.on_item_settings_changed)
            elif isinstance(widget, QRadioButton): widget.toggled.connect(self.on_item_settings_changed)
        
        for widget in list(self.auto_scale_widgets.values()):
            if isinstance(widget, QDoubleSpinBox): widget.valueChanged.connect(self.on_app_settings_changed)
            elif isinstance(widget, QSpinBox): widget.valueChanged.connect(self.on_app_settings_changed)
            elif isinstance(widget, QCheckBox): widget.stateChanged.connect(self.on_app_settings_changed)
        
        for key, widget in self.app_settings_widgets.items():
            if isinstance(widget, QSpinBox):
                widget.valueChanged.connect(self.on_app_settings_changed)
            elif isinstance(widget, QCheckBox):
                widget.stateChanged.connect(self.on_app_settings_changed)
            elif isinstance(widget, QComboBox):
                widget.currentTextChanged.connect(self.on_app_settings_changed)

        self.preview_mode_manager.modeChanged.connect(self.preview_label.set_drawing_mode)
        
        if self.core_engine:
            self.clear_rec_area_button_main_ui.clicked.connect(self.core_engine.clear_recognition_area)
            self.preview_label.settingChanged.connect(self.core_engine.on_preview_click_settings_changed)
            self.preview_label.roiSettingChanged.connect(self.core_engine.on_roi_settings_changed)
            self.save_timer.timeout.connect(self.core_engine.save_current_settings)
            self.appConfigChanged.connect(self.core_engine.on_app_config_changed)
            
        self._signals_connected = True
        
    def open_image_folder(self):
        # (v1.4.1から変更なし)
        folder_path = str(self.config_manager.base_dir)
        try:
            if sys.platform == 'win32':
                os.startfile(folder_path)
            elif sys.platform == 'darwin':
                subprocess.run(['open', folder_path])
            else:
                subprocess.run(['xdg-open', folder_path])
            self.logger.log(f"画像フォルダを開きました: {folder_path}")
        except Exception as e:
            self.logger.log(f"画像フォルダを開けませんでした: {e}")
            QMessageBox.warning(self, "エラー", f"フォルダを開けませんでした:\n{e}")

    def create_colored_icon(self, color, size=16):
        # (v1.4.1から変更なし)
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        if color == Qt.transparent:
            return QIcon(pixmap)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(Qt.black, 1)
        painter.setPen(pen)
        brush = QBrush(color)
        painter.setBrush(brush)
        rect = QRectF(0.5, 0.5, size - 1, size - 1)
        painter.drawRoundedRect(rect, 3.0, 3.0)
        painter.end()
        return QIcon(pixmap)

    def update_image_tree(self):
        # ★★★ D&D変更点: D&D版 (ui(D&D).py) のロジックに更新 ★★★
        self.image_tree.blockSignals(True)
        expanded_folders, (selected_path, _) = set(), self.get_selected_item_path()
        for i in range(self.image_tree.topLevelItemCount()):
            item = self.image_tree.topLevelItem(i)
            if item.childCount() > 0:
                path = item.data(0, Qt.UserRole)
                if path and item.isExpanded(): expanded_folders.add(path)
        self.image_tree.clear()
        
        hierarchical_list = self.config_manager.get_hierarchical_list()
        item_to_reselect = None
        
        for item_data in hierarchical_list:
            if item_data['type'] == 'folder':
                folder_settings = item_data['settings']
                mode = folder_settings.get('mode', 'normal')

                folder_item = QTreeWidgetItem(self.image_tree, [f"📁 {item_data['name']}"])
                folder_item.setData(0, Qt.UserRole, item_data['path'])
                # D&Dドロップターゲットとして有効化
                folder_item.setFlags(folder_item.flags() | Qt.ItemIsDropEnabled)

                brush = QBrush(QApplication.palette().text().color())
                icon_color = Qt.transparent

                if mode == 'normal':
                    brush = QBrush(QColor("darkgray"))
                    icon_color = QColor("darkgray")
                elif mode == 'excluded':
                    brush = QBrush(Qt.red)
                    icon_color = Qt.red
                elif mode == 'priority_image':
                    brush = QBrush(Qt.blue)
                    icon_color = Qt.blue
                elif mode == 'priority_timer':
                    brush = QBrush(Qt.darkGreen)
                    icon_color = Qt.green

                folder_item.setIcon(0, self.create_colored_icon(icon_color))
                folder_item.setForeground(0, brush)

                if item_data['path'] in expanded_folders: folder_item.setExpanded(True)
                if item_data['path'] == selected_path: item_to_reselect = folder_item
                
                for child_data in item_data['children']:
                    child_item = QTreeWidgetItem(folder_item, [f"  {child_data['name']}"])
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
        # 選択変更を強制的に呼び出してプレビューを更新
        self.on_image_tree_selection_changed()

    # ★★★ D&D変更点: on_tree_item_clicked を on_tree_context_menu に変更 ★★★
    def on_tree_context_menu(self, pos):
        item = self.image_tree.itemAt(pos)
        if not item:
            return

        path_str = item.data(0, Qt.UserRole)
        # フォルダ以外（画像アイテム）は無視
        if not path_str or not Path(path_str).is_dir():
            return

        # フォルダ設定ダイアログを開く
        folder_path = Path(path_str)
        current_settings = self.config_manager.load_item_setting(folder_path)

        dialog = FolderSettingsDialog(folder_path.name, current_settings, self)
        if dialog.exec():
            new_settings = dialog.get_settings()
            self.config_manager.save_item_setting(folder_path, new_settings)
            self.folderSettingsChanged.emit()

    def set_tree_enabled(self, enabled: bool):
        # (v1.4.1から変更なし)
        self.image_tree.setEnabled(enabled)

    def on_cache_build_finished(self):
        # (v1.4.1から変更なし)
        self.update_image_tree()
        self.set_tree_enabled(True)
        self.is_processing_tree_change = False
        
    def get_selected_item_path(self):
        # (v1.4.1から変更なし)
        selected_items = self.image_tree.selectedItems();
        if not selected_items: return None, None
        item = selected_items[0]; path = item.data(0, Qt.UserRole); name = item.text(0); return path, name
        
    def on_image_tree_selection_changed(self):
        # (v1.4.1から変更なし)
        if self.is_processing_tree_change: return
        self.current_best_scale_label.setText("最適スケール: ---")
        self.current_best_scale_label.setStyleSheet("color: gray;")
        
        path, name = self.get_selected_item_path()
        if self.core_engine:
            self.core_engine.load_image_and_settings(path)
        
    # ★★★ D&D変更点: move_item_up/down を D&D版 (ui(D&D).py) のロジックに更新 ★★★
    def move_item_up(self):
        if self.is_processing_tree_change: return
        item = self.image_tree.currentItem()
        if not item: return
        parent = item.parent()
        if parent:
            index = parent.indexOfChild(item)
            if index > 0:
                self.set_tree_enabled(False)
                parent.takeChild(index)
                parent.insertChild(index - 1, item)
        else:
            index = self.image_tree.indexOfTopLevelItem(item)
            if index > 0:
                self.set_tree_enabled(False)
                self.image_tree.takeTopLevelItem(index)
                self.image_tree.insertTopLevelItem(index - 1, item)
        self.image_tree.setCurrentItem(item); self.orderChanged.emit() # save_tree_order() を削除
        self.set_tree_enabled(True) # emit後にTrueに戻す
        
    def move_item_down(self):
        if self.is_processing_tree_change: return
        item = self.image_tree.currentItem()
        if not item: return
        parent = item.parent()
        if parent:
            index = parent.indexOfChild(item)
            if index < parent.childCount() - 1:
                self.set_tree_enabled(False)
                parent.takeChild(index)
                parent.insertChild(index + 1, item)
        else:
            index = self.image_tree.indexOfTopLevelItem(item)
            if index < self.image_tree.topLevelItemCount() - 1:
                self.set_tree_enabled(False)
                self.image_tree.takeTopLevelItem(index)
                self.image_tree.insertTopLevelItem(index + 1, item)
        self.image_tree.setCurrentItem(item); self.orderChanged.emit() # save_tree_order() を削除
        self.set_tree_enabled(True) # emit後にTrueに戻す
        
    # ★★★ D&D変更点: save_tree_order を D&D版 (ui(D&D).py) のロジックに更新 ★★★
    def save_tree_order(self):
        top_level_order = []
        for i in range(self.image_tree.topLevelItemCount()):
            item = self.image_tree.topLevelItem(i)
            original_path = Path(item.data(0, Qt.UserRole))
            
            if original_path.is_dir():
                path_str = str(original_path)
            else:
                # D&Dによりルートに移動された画像の場合
                new_path = self.config_manager.base_dir / original_path.name
                path_str = str(new_path)
                if str(original_path) != path_str:
                    item.setData(0, Qt.UserRole, path_str) # データも更新

            top_level_order.append(path_str)
        self.config_manager.save_image_order(top_level_order)

        # サブフォルダの順序も保存
        for i in range(self.image_tree.topLevelItemCount()):
            folder_item = self.image_tree.topLevelItem(i)
            folder_path_str = folder_item.data(0, Qt.UserRole)
            
            if folder_path_str and Path(folder_path_str).is_dir():
                child_order_filenames = []
                for j in range(folder_item.childCount()):
                    child_item = folder_item.child(j)
                    original_path = Path(child_item.data(0, Qt.UserRole))
                    
                    if not original_path.is_dir():
                        # D&Dによりフォルダに移動された画像の場合
                        new_path = Path(folder_path_str) / original_path.name
                        
                        if str(original_path) != str(new_path):
                            child_item.setData(0, Qt.UserRole, str(new_path)) # データも更新

                        child_order_filenames.append(original_path.name)

                self.config_manager.save_image_order(child_order_filenames, folder_path=folder_path_str)
    
    # ★★★ D&D変更点: 複数削除に対応する on_delete_button_clicked を追加 ★★★
    def on_delete_button_clicked(self):
        selected_items = self.image_tree.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "警告", "削除するアイテムを選択してください。")
            return

        item_names = [f"'{item.text(0).strip()}'" for item in selected_items]
        
        reply = QMessageBox.question(
            self,
            "削除の確認",
            f"{len(item_names)}個のアイテム ({', '.join(item_names)}) を本当に削除しますか？\n(フォルダの場合、中のファイルもすべて削除されます)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            paths_to_delete = [item.data(0, Qt.UserRole) for item in selected_items if item.data(0, Qt.UserRole)]
            if paths_to_delete:
                self.deleteItemsRequested.emit(paths_to_delete)

    def get_current_item_settings(self):
        # (v1.4.1から変更なし)
        settings = {}
        for key, widget in self.item_settings_widgets.items():
            if isinstance(widget, QDoubleSpinBox):
                settings[key] = widget.value()
            elif isinstance(widget, QCheckBox):
                settings[key] = widget.isChecked()
        
        if self.item_settings_widgets['roi_mode_fixed'].isChecked():
            settings['roi_mode'] = 'fixed'
        elif self.item_settings_widgets['roi_mode_variable'].isChecked():
            settings['roi_mode'] = 'variable'

        return settings
        
    def set_settings_from_data(self, settings_data):
        # ★★★ D&D変更点: D&D版 (ui(D&D).py) のロジックに更新 (選択パス取得方法の修正) ★★★
        selected_path, _ = self.get_selected_item_path()
        is_folder = selected_path and Path(selected_path).is_dir()
        
        all_widgets = list(self.item_settings_widgets.values()) + \
                      [self.item_settings_widgets['roi_mode_fixed'], self.item_settings_widgets['roi_mode_variable']]

        for widget in all_widgets:
            widget.setEnabled(not is_folder)

        if not settings_data or is_folder:
            for widget in all_widgets:
                widget.blockSignals(True)
                if isinstance(widget, QDoubleSpinBox): widget.setValue(0)
                elif isinstance(widget, QCheckBox): widget.setChecked(False)
                elif isinstance(widget, QRadioButton): widget.setAutoExclusive(False); widget.setChecked(False); widget.setAutoExclusive(True)
            self.preview_label.set_drawing_data(None)
            if is_folder:
                self.preview_label.setText("フォルダを選択中")
                self.preview_label.set_pixmap(None)
            for widget in all_widgets: widget.blockSignals(False)
            
            self.preview_mode_manager.sync_from_settings_data(settings_data)
            self._update_roi_widgets_state()
            return
        
        self.preview_label.set_drawing_data(settings_data)
        for key, value in settings_data.items():
            if key in self.item_settings_widgets:
                widget = self.item_settings_widgets[key]
                widget.blockSignals(True)
                if isinstance(widget, (QDoubleSpinBox, QSpinBox)):
                    widget.setValue(value if value is not None else 0)
                elif isinstance(widget, QCheckBox):
                    widget.setChecked(bool(value))
                widget.blockSignals(False)
        
        roi_mode = settings_data.get('roi_mode', 'fixed')
        self.item_settings_widgets['roi_mode_fixed'].blockSignals(True)
        self.item_settings_widgets['roi_mode_variable'].blockSignals(True)
        if roi_mode == 'variable':
            self.item_settings_widgets['roi_mode_variable'].setChecked(True)
        else:
            self.item_settings_widgets['roi_mode_fixed'].setChecked(True)
        self.item_settings_widgets['roi_mode_fixed'].blockSignals(False)
        self.item_settings_widgets['roi_mode_variable'].blockSignals(False)
            
        self.preview_mode_manager.sync_from_settings_data(settings_data)
        self._update_roi_widgets_state()

    # ★★★ D&D変更点: on_item_settings_changed のロジックを D&D版 (ui(D&D).py) に更新 ★★★
    def on_item_settings_changed(self, *args):
        settings = self.get_current_item_settings()
        self.imageSettingsChanged.emit(settings)
        self._update_roi_widgets_state()
        # プレビューにも即時反映
        self.preview_label.set_drawing_data(self.get_current_item_settings())

    def _update_roi_widgets_state(self):
        # (v1.4.1から変更なし)
        is_roi_enabled = self.item_settings_widgets['roi_enabled'].isChecked()
        is_variable_mode = self.item_settings_widgets['roi_mode_variable'].isChecked()

        self.item_settings_widgets['roi_mode_fixed'].setEnabled(is_roi_enabled)
        self.item_settings_widgets['roi_mode_variable'].setEnabled(is_roi_enabled)
        self.item_settings_widgets['set_roi_variable_button'].setEnabled(is_roi_enabled and is_variable_mode)

    def request_save(self): 
        # (v1.4.1から変更なし)
        if self.core_engine:
            self.save_timer.start()

    def toggle_monitoring(self):
        # (v1.4.1から変更なし)
        if self.monitor_button.text() == "監視開始": self.startMonitoringRequested.emit()
        else: self.stopMonitoringRequested.emit()
        
    def set_status(self, text, color="green"):
        # (v1.4.1から変更なし)
        display_text = text
        style_color = color
        if text == "監視中...":
            self.monitor_button.setText("監視停止")
            display_text = "監視中..."
            style_color = "blue"
        elif text == "待機中":
            self.monitor_button.setText("監視開始")
            display_text = "待機中"
            style_color = "green"
            self.current_best_scale_label.setText("最適スケール: ---")
            self.current_best_scale_label.setStyleSheet("color: gray;")
        
        self.status_label.setText(display_text)
        self.status_label.setStyleSheet(f"font-weight: bold; color: {style_color};")
        
        if self.floating_window:
            self.floating_window.update_status(display_text, style_color)

    def on_best_scale_found(self, image_path: str, scale: float):
        # (v1.4.1から変更なし)
        current_selected_path, _ = self.get_selected_item_path()
        if image_path and image_path == current_selected_path:
            self.current_best_scale_label.setText(f"最適スケール: {scale:.3f}倍")
            self.current_best_scale_label.setStyleSheet("color: green;")

    def on_window_scale_calculated(self, scale: float):
        # (v1.4.1から変更なし)
        if scale > 0:
            self.current_best_scale_label.setText(f"計算スケール: {scale:.3f}倍")
            color = "white" if self.is_dark_mode() else "purple"
            self.current_best_scale_label.setStyleSheet(f"color: {color};")
            self.auto_scale_widgets['center'].setValue(scale)
        else:
            self.current_best_scale_label.setText("最適スケール: ---")
            self.current_best_scale_label.setStyleSheet("color: gray;")
            
    def prompt_to_save_base_size(self, window_title: str) -> bool:
        # (v1.4.1から変更なし)
        reply = QMessageBox.question(
            self,
            "基準サイズの確認",
            f"ウィンドウ '{window_title}'\n\nこのウィンドウの現在のサイズを基準サイズ (1.0倍) として記憶しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        return reply == QMessageBox.StandardButton.Yes

    def show_prompt_to_save_base_size(self, window_title: str):
        # (v1.4.1から変更なし)
        save_as_base = self.prompt_to_save_base_size(window_title)
        if self.core_engine:
            self.core_engine.process_base_size_prompt_response(save_as_base)
            
    def show_prompt_to_apply_scale(self, scale: float):
        # (v1.4.1から変更なし)
        reply = QMessageBox.question(
            self,
            "スケール適用の確認",
            f"認識範囲のスケールが {scale:.3f}倍 です。\nこの倍率でスケーリングを有効にしますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        apply_scale = (reply == QMessageBox.StandardButton.Yes)
        if self.core_engine:
            self.core_engine.process_apply_scale_prompt_response(apply_scale)

    def load_images_dialog(self):
        # (v1.4.1から変更なし)
        file_paths, _ = QFileDialog.getOpenFileNames(self, "画像を選択", str(self.config_manager.base_dir), "画像ファイル (*.png *.jpg *.jpeg *.bmp)")
        if file_paths: self.set_tree_enabled(False); self.loadImagesRequested.emit(file_paths)
        
    def update_image_preview(self, cv_image: np.ndarray, settings_data: dict = None):
        # ★★★ D&D変更点: D&D版 (ui(D&D).py) のロジックに更新 (選択パス取得方法の修正) ★★★
        self.set_settings_from_data(settings_data)
        if cv_image is None or cv_image.size == 0:
            selected_path, _ = self.get_selected_item_path()
            if not (selected_path and Path(selected_path).is_dir()):
                self.preview_label.setText("画像を選択してください")
            self.preview_label.set_pixmap(None)
            return
            
        rgb_image = cv2.cvtColor(cv_image, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb_image.shape
        bytes_per_line = ch * w
        q_image = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(q_image)
        self.preview_label.set_pixmap(pixmap)
        
    def update_rec_area_preview(self, cv_image: np.ndarray):
        # (v1.4.1から変更なし)
        if cv_image is None or cv_image.size == 0:
            self.rec_area_preview_label.set_pixmap(None)
            self.rec_area_preview_label.setText("認識範囲プレビュー")
            return
        
        rgb_image = cv2.cvtColor(cv_image, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb_image.shape
        bytes_per_line = ch * w
        q_image = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(q_image)
        self.rec_area_preview_label.set_pixmap(pixmap)
        
    def update_log(self, message: str): 
        # (v1.4.1から変更なし)
        self.log_text.append(message)
    
    def closeEvent(self, event):
        # (v1.4.1から変更なし)
        if self.floating_window:
            self.floating_window.close()
        if self.core_engine:
            self.core_engine.cleanup()
        self.stopMonitoringRequested.emit()
        QApplication.instance().quit()
        event.accept()
        
    def setRecAreaDialog(self):
        # (v1.4.1から変更なし)
        dialog = RecAreaSelectionDialog(self)
        dialog.selectionMade.connect(self.setRecAreaMethodSelected)
        dialog.move(QCursor.pos())
        dialog.exec()

    def adjust_initial_size(self):
        # (v1.4.1から変更なし)
        self.setMinimumWidth(0)
        self.resize(960, 640)

    def toggle_minimal_ui_mode(self):
        # ★★★ D&D変更点: D&D版 (ui(D&D).py) のロジックに更新 ★★★
        self.is_minimal_mode = not self.is_minimal_mode
        if self.is_minimal_mode:
            self.normal_ui_geometries['main'] = self.geometry()
            if self.performance_monitor and self.performance_monitor.isVisible():
                self.normal_ui_geometries['perf'] = self.performance_monitor.geometry()

            self.showMinimized()
            if self.performance_monitor:
                self.performance_monitor.hide()
            
            self.floating_window = FloatingWindow()
            self.floating_window.startMonitoringRequested.connect(self.startMonitoringRequested)
            self.floating_window.stopMonitoringRequested.connect(self.stopMonitoringRequested)
            self.floating_window.captureImageRequested.connect(self.captureImageRequested)
            self.floating_window.toggleMainUIRequested.connect(self.toggle_minimal_ui_mode)
            self.floating_window.closeRequested.connect(self.toggle_minimal_ui_mode)
            self.floating_window.setRecAreaRequested.connect(self.setRecAreaDialog)
            
            if self.performance_monitor:
                self.performance_monitor.performanceUpdated.connect(self.floating_window.update_performance)

            # 現在のステータスを正確に引き継ぐ
            current_status_text = self.status_label.text()
            current_status_color = self.status_label.palette().color(QPalette.ColorRole.WindowText).name()
            if current_status_text == "監視中...":
                current_status_color = "blue"
            elif current_status_text == "待機中":
                current_status_color = "green"
            self.floating_window.update_status(current_status_text, current_status_color)
            
            self.floating_window.show()
            self.toggle_minimal_ui_button.setText("最小UIモード停止")
        else:
            if self.floating_window:
                # disconnect 処理を追加
                if self.performance_monitor:
                    if hasattr(self.performance_monitor, 'performanceUpdated'):
                        try:
                            self.performance_monitor.performanceUpdated.disconnect(self.floating_window.update_performance)
                        except (TypeError, RuntimeError):
                            pass # 接続されていない場合のエラーを無視
                self.floating_window.close()
                self.floating_window = None
            
            self.showNormal()
            if 'main' in self.normal_ui_geometries:
                self.setGeometry(self.normal_ui_geometries['main'])
            
            if self.performance_monitor:
                if 'perf' in self.normal_ui_geometries and not self.performance_monitor.isVisible():
                    self.performance_monitor.show()
                    self.performance_monitor.setGeometry(self.normal_ui_geometries['perf'])
            
            self.activateWindow()
            self.toggle_minimal_ui_button.setText("最小UIモード")

    def on_selection_process_started(self):
        # (v1.4.1から変更なし)
        if self.performance_monitor:
            self.performance_monitor.hide()
        if self.is_minimal_mode and self.floating_window:
            self.floating_window.hide()

    def on_selection_process_finished(self):
        # (v1.4.1から変更なし)
        if self.is_minimal_mode:
            if self.floating_window:
                self.floating_window.show()
        else:
            if self.performance_monitor and not self.performance_monitor.isVisible():
                self.performance_monitor.show()
