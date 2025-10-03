# ui.py

import sys
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QPushButton, QVBoxLayout, QWidget, QLabel,
    QFrame, QHBoxLayout, QGroupBox, QSpinBox, QDoubleSpinBox, QCheckBox,
    QGridLayout, QSizePolicy, QSpacerItem, QToolButton, QFileDialog, QLineEdit,
    QTreeWidget, QTreeWidgetItem, QMenu, QTabWidget, QTextEdit, QDialog, QMessageBox,
    QComboBox, QDialogButtonBox, QRadioButton, QButtonGroup
)
from PySide6.QtGui import QIcon, QPixmap, QImage, QPainter, QColor, QFontMetrics, QPen, QCursor, QBrush, QFont
from PySide6.QtCore import Qt, QSize, QThread, Signal, QTimer, QObject, QRect, QPoint, QRectF, QPointF

import os
import subprocess
import cv2
import numpy as np
from pathlib import Path
from capture import DXCAM_AVAILABLE

try:
    OPENCL_AVAILABLE = cv2.ocl.haveOpenCL()
except:
    OPENCL_AVAILABLE = False


class ScaledPixmapLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap()
        self.setMinimumSize(1, 1)

    def set_pixmap(self, pixmap):
        self._pixmap = pixmap if pixmap and not pixmap.isNull() else QPixmap()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.black)
        
        if self._pixmap.isNull():
            return

        label_size = self.size()
        scaled_pixmap = self._pixmap.scaled(label_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        x = (label_size.width() - scaled_pixmap.width()) / 2
        y = (label_size.height() - scaled_pixmap.height()) / 2
        painter.drawPixmap(int(x), int(y), scaled_pixmap)


class InteractivePreviewLabel(QLabel):
    settingChanged = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(1, 1); self.setMouseTracking(True)
        self.drawing_mode = None; self.is_drawing = False; self.start_pos = QPoint()
        self.end_pos = QPoint(); self._pixmap = QPixmap(); self.click_settings = {}

    def set_pixmap(self, pixmap):
        self._pixmap = pixmap if pixmap and not pixmap.isNull() else QPixmap()
        self.update()

    def set_drawing_data(self, settings):
        self.click_settings = settings if settings else {}
        self.update()

    def set_drawing_mode(self, mode):
        self.drawing_mode = mode; self.setCursor(Qt.CrossCursor if mode else Qt.ArrowCursor)

    def _get_pixmap_rect(self):
        if self._pixmap.isNull(): return QRectF()
        pixmap_ratio = self._pixmap.width() / self._pixmap.height()
        label_ratio = self.width() / self.height()
        if pixmap_ratio > label_ratio:
            width = self.width(); height = width / pixmap_ratio
            x, y = 0, (self.height() - height) / 2
        else:
            height = self.height(); width = height * pixmap_ratio
            x, y = (self.width() - width) / 2, 0
        return QRectF(x, y, width, height)

    def _map_widget_to_image_coords(self, widget_pos):
        if self._pixmap.isNull(): return None
        pixmap_rect = self._get_pixmap_rect()
        if not pixmap_rect.contains(widget_pos): return None
        
        relative_x = (widget_pos.x() - pixmap_rect.x()) / pixmap_rect.width()
        relative_y = (widget_pos.y() - pixmap_rect.y()) / pixmap_rect.height()
        
        img_x = relative_x * self._pixmap.width(); img_y = relative_y * self._pixmap.height()
        return QPoint(int(img_x), int(img_y))

    def mousePressEvent(self, event):
        if self.drawing_mode and event.button() == Qt.LeftButton:
            img_pos = self._map_widget_to_image_coords(event.pos())
            if img_pos: self.is_drawing = True; self.start_pos, self.end_pos = img_pos, img_pos; self.update()

    def mouseMoveEvent(self, event):
        if self.is_drawing:
            img_pos = self._map_widget_to_image_coords(event.pos())
            if img_pos: self.end_pos = img_pos; self.update()

    def mouseReleaseEvent(self, event):
        if self.is_drawing and event.button() == Qt.LeftButton:
            self.is_drawing = False
            if self.drawing_mode == 'point': self.settingChanged.emit({'click_position': [self.end_pos.x(), self.end_pos.y()]})
            elif self.drawing_mode == 'range':
                rect = QRect(self.start_pos, self.end_pos).normalized()
                self.settingChanged.emit({'click_rect': [rect.left(), rect.top(), rect.right(), rect.bottom()]})

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.black)

        if self._pixmap.isNull():
            return
            
        pixmap_rect = self._get_pixmap_rect()
        painter.drawPixmap(pixmap_rect.toRect(), self._pixmap)
        
        if self._pixmap.width() == 0 or self._pixmap.height() == 0:
            return
            
        scale_x = pixmap_rect.width() / self._pixmap.width()
        scale_y = pixmap_rect.height() / self._pixmap.height()

        def to_widget_coords(img_pos):
            x = pixmap_rect.x() + img_pos[0] * scale_x
            y = pixmap_rect.y() + img_pos[1] * scale_y
            return QPointF(x, y)
            
        if self.click_settings.get('roi_enabled') and self.click_settings.get('roi_rect'):
            roi = self.click_settings['roi_rect']
            p1 = to_widget_coords((roi[0], roi[1]))
            p2 = to_widget_coords((roi[2], roi[3]))
            painter.setPen(QPen(QColor(0, 255, 0), 1))
            painter.setBrush(QColor(0, 255, 0, 40))
            painter.drawRect(QRectF(p1, p2))
            
        if self.is_drawing:
            p1 = to_widget_coords((self.start_pos.x(), self.start_pos.y()))
            p2 = to_widget_coords((self.end_pos.x(), self.end_pos.y()))
            if self.drawing_mode == 'point':
                painter.setPen(QPen(QColor(255, 0, 0), 3))
                painter.setBrush(QColor(255, 0, 0))
                painter.drawEllipse(p2, 3, 3)
            elif self.drawing_mode == 'range':
                painter.setPen(QPen(QColor(0, 0, 255), 2))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(QRectF(p1, p2))
        else:
            if self.click_settings.get('point_click') and self.click_settings.get('click_position'):
                pos = self.click_settings['click_position']
                p = to_widget_coords(pos)
                painter.setPen(QPen(QColor(255, 0, 0), 3))
                painter.setBrush(QColor(255, 0, 0))
                painter.drawEllipse(p, 3, 3)
            elif self.click_settings.get('range_click') and self.click_settings.get('click_rect'):
                rect = self.click_settings['click_rect']
                p1 = to_widget_coords((rect[0], rect[1]))
                p2 = to_widget_coords((rect[2], rect[3]))
                painter.setPen(QPen(QColor(0, 0, 255), 2))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(QRectF(p1, p2))

class RecAreaSelectionDialog(QDialog):
    selectionMade = Signal(str)
    def __init__(self, parent=None):
        super().__init__(parent); self.setWindowTitle("認識範囲設定"); self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Popup); self.setFixedSize(200, 100)
        layout = QVBoxLayout(self); layout.addWidget(QLabel("設定方法を選択:"))
        button_layout = QHBoxLayout(); self.rect_button = QPushButton("四角設定"); self.rect_button.clicked.connect(lambda: self.on_select("rectangle"))
        button_layout.addWidget(self.rect_button); self.window_button = QPushButton("ウィンドウ設定"); self.window_button.clicked.connect(lambda: self.on_select("window"))
        button_layout.addWidget(self.window_button); layout.addLayout(button_layout)
    def on_select(self, method): self.selectionMade.emit(method); self.accept()
    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape: self.reject()

class FolderSettingsDialog(QDialog):
    def __init__(self, folder_name, current_settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"フォルダ設定: {folder_name}")
        self.layout = QVBoxLayout(self)

        # モード選択
        mode_box = QGroupBox("フォルダの動作モード")
        mode_layout = QVBoxLayout()
        self.radio_normal = QRadioButton("通常 (監視対象)")
        self.radio_excluded = QRadioButton("検索停止 (監視対象外)")
        self.radio_priority = QRadioButton("タイマー付き優先")
        
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.radio_normal, 0)
        self.mode_group.addButton(self.radio_excluded, 1)
        self.mode_group.addButton(self.radio_priority, 2)
        
        mode_layout.addWidget(self.radio_normal)
        mode_layout.addWidget(self.radio_excluded)
        mode_layout.addWidget(self.radio_priority)
        mode_box.setLayout(mode_layout)
        self.layout.addWidget(mode_box)

        # タイマー設定
        self.timer_box = QGroupBox("タイマー付き優先 の詳細設定")
        timer_layout = QGridLayout()
        timer_layout.addWidget(QLabel("有効になるまでの間隔:"), 0, 0)
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 999)
        self.interval_spin.setSuffix(" 分")
        timer_layout.addWidget(self.interval_spin, 0, 1)
        
        timer_layout.addWidget(QLabel("優先モードを解除する時間:"), 1, 0)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 999)
        self.timeout_spin.setSuffix(" 分")
        timer_layout.addWidget(self.timeout_spin, 1, 1)
        self.timer_box.setLayout(timer_layout)
        self.layout.addWidget(self.timer_box)

        self.radio_priority.toggled.connect(self.timer_box.setEnabled)
        
        # ツールチップ設定
        tooltip_text = (
            "<b>タイマー付き優先モードの詳細:</b><br>"
            "設定した<b>『有効になるまでの間隔』</b>が経過すると、このフォルダ内の画像のみを優先的に探します。<br>"
            "優先モードは、以下のいずれかの条件で解除され、通常の検索に戻ります。<br>"
            "<ul>"
            "<li>このフォルダ内の<b>すべての画像</b>が一度ずつクリックされた。</li>"
            "<li>優先モード開始後、<b>『優先モードを解除する時間』</b>が経過した。</li>"
            "</ul>"
            "このフォルダ内の画像がクリックされると、有効化タイマーはリセットされます。"
        )
        self.radio_priority.setToolTip(tooltip_text)
        self.timer_box.setToolTip(tooltip_text)
        self.radio_priority.setToolTipDuration(-1) # マウスが外れるまで表示
        self.timer_box.setToolTipDuration(-1)

        # OK / Cancel ボタン
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.layout.addWidget(self.buttons)

        self.load_settings(current_settings)

    def load_settings(self, settings):
        mode = settings.get('mode', 'normal')
        if mode == 'excluded':
            self.radio_excluded.setChecked(True)
        elif mode == 'priority_timer':
            self.radio_priority.setChecked(True)
        else:
            self.radio_normal.setChecked(True)
        
        self.interval_spin.setValue(settings.get('priority_interval', 10))
        self.timeout_spin.setValue(settings.get('priority_timeout', 5))
        self.timer_box.setEnabled(mode == 'priority_timer')

    def get_settings(self):
        mode_id = self.mode_group.checkedId()
        mode = 'normal'
        if mode_id == 1:
            mode = 'excluded'
        elif mode_id == 2:
            mode = 'priority_timer'
            
        return {
            'mode': mode,
            'priority_interval': self.interval_spin.value(),
            'priority_timeout': self.timeout_spin.value()
        }

class UIManager(QMainWindow):
    startMonitoringRequested = Signal(); stopMonitoringRequested = Signal(); openPerformanceMonitorRequested = Signal()
    loadImagesRequested = Signal(list); setRecAreaMethodSelected = Signal(str); captureImageRequested = Signal()
    deleteItemRequested = Signal(); orderChanged = Signal()
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

        self.setWindowTitle("Imeck15")
        self.resize(1024, 640)
        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint)

        self.save_timer = QTimer(self); self.save_timer.setSingleShot(True); self.save_timer.setInterval(1000)
        self.is_processing_tree_change = False
        
        self.app_config = self.config_manager.load_app_config()
        
        self.setup_ui()
        self.load_app_settings_to_ui()
        
    def setup_ui(self):
        central_widget = QWidget(); self.setCentralWidget(central_widget); main_layout = QVBoxLayout(central_widget)
        header_frame = QFrame(); header_layout = QHBoxLayout(header_frame)
        self.monitor_button = QPushButton("監視開始"); self.monitor_button.setFixedSize(100, 30)
        self.monitor_button.setToolTip("右クリックで監視停止、右ダブルクリックで監視開始")
        header_layout.addWidget(self.monitor_button)
        self.perf_monitor_button = QPushButton("パフォーマンス"); self.perf_monitor_button.setFixedSize(120, 30); header_layout.addWidget(self.perf_monitor_button)
        self.header_rec_area_button = QPushButton("認識範囲設定"); self.header_rec_area_button.setFixedSize(120, 30); self.header_rec_area_button.clicked.connect(self.setRecAreaDialog)
        header_layout.addWidget(self.header_rec_area_button)
        
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
        self.image_tree = QTreeWidget(); self.image_tree.setHeaderHidden(True); left_layout.addWidget(self.image_tree)
        button_layout = QGridLayout(); load_image_button = QPushButton("画像追加"); button_layout.addWidget(load_image_button, 0, 0)
        capture_image_button = QPushButton("画像キャプチャ"); button_layout.addWidget(capture_image_button, 0, 1)
        delete_item_button = QPushButton("選択を削除"); button_layout.addWidget(delete_item_button, 1, 0)
        create_folder_button = QPushButton("フォルダを作成"); button_layout.addWidget(create_folder_button, 1, 1)
        move_in_button = QPushButton("フォルダに入れる"); button_layout.addWidget(move_in_button, 2, 0)
        move_out_button = QPushButton("フォルダから出す"); button_layout.addWidget(move_out_button, 2, 1)
        load_image_button.clicked.connect(self.load_images_dialog); capture_image_button.clicked.connect(self.captureImageRequested.emit)
        delete_item_button.clicked.connect(self.deleteItemRequested.emit); move_up_button.clicked.connect(self.move_item_up); move_down_button.clicked.connect(self.move_item_down)
        create_folder_button.clicked.connect(self.createFolderRequested.emit); move_in_button.clicked.connect(self.moveItemIntoFolderRequested.emit); move_out_button.clicked.connect(self.moveItemOutOfFolderRequested.emit)
        left_layout.addLayout(button_layout); content_layout.addWidget(left_frame, 1)
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

        auto_scale_group = QGroupBox(); auto_scale_layout = QGridLayout(auto_scale_group)
        
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
        auto_scale_layout.addWidget(as_desc_label, 5, 0, 1, 4)
        auto_scale_group.setFlat(True)
        self.preview_tabs.addTab(auto_scale_group, "自動スケール")

        app_settings_group = QGroupBox(); app_settings_layout = QGridLayout(app_settings_group)
        self.app_settings_widgets['grayscale_matching'] = QCheckBox("グレースケール検索 (高速)")
        app_settings_layout.addWidget(self.app_settings_widgets['grayscale_matching'], 0, 0)
        gs_desc_label = QLabel("<b>メリット:</b> 処理が高速になり、僅かな色の違いを無視できます。<br>"
                               "<b>デメリット:</b> 同じ形で色が違うだけの画像は区別できません。")
        gs_desc_label.setWordWrap(True); gs_desc_label.setStyleSheet("font-size: 11px; color: #555555;")
        app_settings_layout.addWidget(gs_desc_label, 0, 1)
        self.app_settings_widgets['capture_method'] = QCheckBox("DXCamを使用")
        self.app_settings_widgets['capture_method'].setEnabled(DXCAM_AVAILABLE)
        app_settings_layout.addWidget(self.app_settings_widgets['capture_method'], 1, 0)
        dxcam_desc_label = QLabel("<b>メリット:</b> ゲーム等の描画に強く、CPU負荷が低い高速なキャプチャ方式です。<br>"
                                  "<b>デメリット:</b> 一部のアプリやPC環境では動作しない場合があります。")
        dxcam_desc_label.setWordWrap(True); dxcam_desc_label.setStyleSheet("font-size: 11px; color: #555555;")
        app_settings_layout.addWidget(dxcam_desc_label, 1, 1)
        fs_layout = QHBoxLayout()
        fs_layout.addWidget(QLabel("フレームスキップ:"))
        self.app_settings_widgets['frame_skip_rate'] = QSpinBox(); self.app_settings_widgets['frame_skip_rate'].setRange(1, 10)
        fs_layout.addWidget(self.app_settings_widgets['frame_skip_rate'])
        app_settings_layout.addLayout(fs_layout, 2, 0)
        fs_desc_label = QLabel("<b>メリット:</b> 値を大きくするとCPU負荷が下がります。<br>"
                               "<b>デメリット:</b> 画面の急な変化に対する反応が遅くなります。")
        fs_desc_label.setWordWrap(True); fs_desc_label.setStyleSheet("font-size: 11px; color: #555555;")
        app_settings_layout.addWidget(fs_desc_label, 2, 1)
        self.app_settings_widgets['use_opencl'] = QCheckBox("OpenCL (GPU支援) を使用")
        self.app_settings_widgets['use_opencl'].setEnabled(OPENCL_AVAILABLE)
        app_settings_layout.addWidget(self.app_settings_widgets['use_opencl'], 3, 0)
        opencl_desc_label = QLabel("<b>メリット:</b> GPUを利用して画像処理を高速化します。特に高解像度の画面や大きな画像の認識時にCPU負荷を下げ、パフォーマンスを向上させます。<br>"
                                     "<b>デメリット:</b> 処理によっては僅かなオーバーヘッドが発生します。また、GPUドライバとの相性問題が発生する場合があります。")
        opencl_desc_label.setWordWrap(True); opencl_desc_label.setStyleSheet("font-size: 11px; color: #555555;")
        app_settings_layout.addWidget(opencl_desc_label, 3, 1)
        app_settings_layout.setColumnStretch(1, 1)
        app_settings_group.setFlat(True)
        self.preview_tabs.addTab(app_settings_group, "アプリ設定")

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

        item_settings_group = QGroupBox("画像ごとの設定"); item_settings_layout = QGridLayout(item_settings_group)
        item_settings_layout.addWidget(QLabel("認識精度:"), 0, 0)
        self.item_settings_widgets['threshold'] = QDoubleSpinBox(); self.item_settings_widgets['threshold'].setRange(0.5, 1.0); self.item_settings_widgets['threshold'].setSingleStep(0.01); self.item_settings_widgets['threshold'].setValue(0.8)
        item_settings_layout.addWidget(self.item_settings_widgets['threshold'], 0, 1)
        self.item_settings_widgets['roi_enabled'] = QCheckBox("ROI有効")
        self.item_settings_widgets['roi_enabled'].setToolTip(
            "ROI (Region of Interest) を有効にすると、設定したクリック座標を中心とした\n"
            "200x200ピクセルの範囲のみを探索対象とします。\n"
            "これにより、画面全体を探索するよりも高速にマッチングが行え、処理負荷を軽減できます。"
        )
        item_settings_layout.addWidget(self.item_settings_widgets['roi_enabled'], 0, 2)
        
        item_settings_layout.addWidget(QLabel("バックアップクリック:"), 1, 0)
        backup_layout = QHBoxLayout(); self.item_settings_widgets['backup_click'] = QCheckBox("有効"); backup_layout.addWidget(self.item_settings_widgets['backup_click'])
        self.item_settings_widgets['backup_time'] = QDoubleSpinBox(); self.item_settings_widgets['backup_time'].setRange(1.0, 600.0); self.item_settings_widgets['backup_time'].setSingleStep(1.0); self.item_settings_widgets['backup_time'].setValue(300.0)
        backup_layout.addWidget(self.item_settings_widgets['backup_time']); item_settings_layout.addLayout(backup_layout, 1, 1, 1, 2)
        
        item_settings_layout.addWidget(QLabel("インターバル(秒):"), 2, 0)
        self.item_settings_widgets['interval_time'] = QDoubleSpinBox(); self.item_settings_widgets['interval_time'].setRange(0.1, 10.0); self.item_settings_widgets['interval_time'].setSingleStep(0.1); self.item_settings_widgets['interval_time'].setValue(1.5)
        item_settings_layout.addWidget(self.item_settings_widgets['interval_time'], 2, 1)

        item_settings_layout.addWidget(QLabel("デバウンス(秒):"), 3, 0)
        self.item_settings_widgets['debounce_time'] = QDoubleSpinBox(); self.item_settings_widgets['debounce_time'].setRange(0.0, 10.0); self.item_settings_widgets['debounce_time'].setSingleStep(0.1); self.item_settings_widgets['debounce_time'].setValue(0.0)
        self.item_settings_widgets['debounce_time'].setToolTip(
            "連続で同じ画像がマッチした際、2回目のクリックタイミングを「インターバル＋デバウンス時間」に延長します。\n"
            "これにより、インターバルがより長い他の画像が先にクリックされる機会を作ることができます。"
        )
        item_settings_layout.addWidget(self.item_settings_widgets['debounce_time'], 3, 1)
        
        click_type_layout = QHBoxLayout(); self.item_settings_widgets['point_click'] = QCheckBox("1点クリック"); self.item_settings_widgets['range_click'] = QCheckBox("範囲クリック"); self.item_settings_widgets['random_click'] = QCheckBox("範囲内ランダム")
        click_type_layout.addWidget(self.item_settings_widgets['point_click']); click_type_layout.addWidget(self.item_settings_widgets['range_click']); click_type_layout.addWidget(self.item_settings_widgets['random_click'])
        item_settings_layout.addLayout(click_type_layout, 4, 0, 1, 3)
        right_layout.addWidget(item_settings_group, 1)

        content_layout.addWidget(right_frame, 2); main_layout.addWidget(content_frame)

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
        self.update_auto_scale_info()

    def get_auto_scale_settings(self) -> dict:
        return {
            "use_window_scale": self.auto_scale_widgets['use_window_scale'].isChecked(),
            "enabled": self.auto_scale_widgets['enabled'].isChecked(),
            "center": self.auto_scale_widgets['center'].value(),
            "range": self.auto_scale_widgets['range'].value(),
            "steps": self.auto_scale_widgets['steps'].value()
        }

    def update_auto_scale_info(self):
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
        self.app_config['auto_scale'] = self.get_auto_scale_settings()
        self.app_config['capture_method'] = 'dxcam' if self.app_settings_widgets['capture_method'].isChecked() else 'mss'
        self.app_config['frame_skip_rate'] = self.app_settings_widgets['frame_skip_rate'].value()
        self.app_config['grayscale_matching'] = self.app_settings_widgets['grayscale_matching'].isChecked()
        self.app_config['use_opencl'] = self.app_settings_widgets['use_opencl'].isChecked()
        self.config_manager.save_app_config(self.app_config)
        self.update_auto_scale_info()
        self.appConfigChanged.emit()

    def connect_signals(self):
        self.monitor_button.clicked.connect(self.toggle_monitoring); self.perf_monitor_button.clicked.connect(self.openPerformanceMonitorRequested.emit)
        self.image_tree.itemSelectionChanged.connect(self.on_image_tree_selection_changed)
        self.image_tree.itemClicked.connect(self.on_tree_item_clicked)
        
        self.set_rec_area_button_main_ui.clicked.connect(self.setRecAreaDialog); self.clear_rec_area_button_main_ui.clicked.connect(self.core_engine.clear_recognition_area)
        
        self.open_image_folder_button.clicked.connect(self.open_image_folder)
        
        for widget in self.item_settings_widgets.values():
            if isinstance(widget, QDoubleSpinBox): widget.valueChanged.connect(self.on_item_settings_changed)
            elif isinstance(widget, QCheckBox): widget.stateChanged.connect(self.on_item_settings_changed)
        
        self.item_settings_widgets['point_click'].toggled.connect(self.on_point_click_toggled)
        self.item_settings_widgets['range_click'].toggled.connect(self.on_range_click_toggled)
        
        for widget in list(self.auto_scale_widgets.values()) + list(self.app_settings_widgets.values()):
            if isinstance(widget, QDoubleSpinBox): widget.valueChanged.connect(self.on_app_settings_changed)
            elif isinstance(widget, QSpinBox): widget.valueChanged.connect(self.on_app_settings_changed)
            elif isinstance(widget, QCheckBox): widget.stateChanged.connect(self.on_app_settings_changed)

        self.preview_label.settingChanged.connect(self.core_engine.on_preview_click_settings_changed)
        self.save_timer.timeout.connect(self.core_engine.save_current_settings)
        self.appConfigChanged.connect(self.core_engine.on_app_config_changed)
        
    def open_image_folder(self):
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

    def on_point_click_toggled(self, checked):
        if checked:
            range_cb = self.item_settings_widgets['range_click']
            range_cb.blockSignals(True)
            range_cb.setChecked(False)
            range_cb.blockSignals(False)

    def on_range_click_toggled(self, checked):
        if checked:
            point_cb = self.item_settings_widgets['point_click']
            point_cb.blockSignals(True)
            point_cb.setChecked(False)
            point_cb.blockSignals(False)
            
    def create_colored_icon(self, color, size=16):
        pixmap = QPixmap(size, size)
        pixmap.fill(color)
        return QIcon(pixmap)

    def update_image_tree(self):
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

                color = Qt.transparent
                brush = QBrush(QApplication.palette().text().color())

                if mode == 'excluded':
                    color = Qt.red
                    brush = QBrush(Qt.red)
                elif mode == 'priority_timer':
                    color = Qt.green
                    brush = QBrush(Qt.darkGreen)

                folder_item.setIcon(0, self.create_colored_icon(color))
                folder_item.setForeground(0, brush)

                if item_data['path'] in expanded_folders: folder_item.setExpanded(True)
                if item_data['path'] == selected_path: item_to_reselect = folder_item
                
                for child_data in item_data['children']:
                    child_item = QTreeWidgetItem(folder_item, [child_data['name']])
                    child_item.setData(0, Qt.UserRole, child_data['path'])
                    child_item.setForeground(0, brush) # 子アイテムも同じ色に
                    if child_data['path'] == selected_path: item_to_reselect = child_item
            
            elif item_data['type'] == 'image':
                image_item = QTreeWidgetItem(self.image_tree, [item_data['name']])
                image_item.setData(0, Qt.UserRole, item_data['path'])
                image_item.setIcon(0, self.create_colored_icon(Qt.transparent))
                if item_data['path'] == selected_path: item_to_reselect = image_item
                
        if item_to_reselect: self.image_tree.setCurrentItem(item_to_reselect)
        self.image_tree.blockSignals(False)

    def on_tree_item_clicked(self, item, column):
        path_str = item.data(0, Qt.UserRole)
        if not path_str or not Path(path_str).is_dir():
            return

        folder_path = Path(path_str)
        current_settings = self.config_manager.load_item_setting(folder_path)

        dialog = FolderSettingsDialog(folder_path.name, current_settings, self)
        if dialog.exec():
            new_settings = dialog.get_settings()
            self.config_manager.save_item_setting(folder_path, new_settings)
            self.folderSettingsChanged.emit()

    def set_tree_enabled(self, enabled: bool):
        self.image_tree.setEnabled(enabled)

    def on_cache_build_finished(self):
        self.update_image_tree()
        self.set_tree_enabled(True)
        self.is_processing_tree_change = False
        
    def get_selected_item_path(self):
        selected_items = self.image_tree.selectedItems();
        if not selected_items: return None, None
        item = selected_items[0]; path = item.data(0, Qt.UserRole); name = item.text(0); return path, name
        
    def on_image_tree_selection_changed(self):
        if self.is_processing_tree_change: return
        self.current_best_scale_label.setText("最適スケール: ---")
        self.current_best_scale_label.setStyleSheet("color: gray;")
        
        path, name = self.get_selected_item_path()
        self.core_engine.load_image_and_settings(path)
        
    def move_item_up(self):
        if self.is_processing_tree_change: return
        self.set_tree_enabled(False); item = self.image_tree.currentItem()
        if not item: self.set_tree_enabled(True); return
        parent = item.parent()
        if parent:
            index = parent.indexOfChild(item)
            if index > 0: parent.takeChild(index); parent.insertChild(index - 1, item)
        else:
            index = self.image_tree.indexOfTopLevelItem(item)
            if index > 0: self.image_tree.takeTopLevelItem(index); self.image_tree.insertTopLevelItem(index - 1, item)
        self.image_tree.setCurrentItem(item); self.save_tree_order()
        
    def move_item_down(self):
        if self.is_processing_tree_change: return
        self.set_tree_enabled(False); item = self.image_tree.currentItem()
        if not item: self.set_tree_enabled(True); return
        parent = item.parent()
        if parent:
            index = parent.indexOfChild(item)
            if index < parent.childCount() - 1: parent.takeChild(index); parent.insertChild(index + 1, item)
        else:
            index = self.image_tree.indexOfTopLevelItem(item)
            if index < self.image_tree.topLevelItemCount() - 1: self.image_tree.takeTopLevelItem(index); self.image_tree.insertTopLevelItem(index + 1, item)
        self.image_tree.setCurrentItem(item); self.save_tree_order()
        
    def save_tree_order(self):
        top_level_order = [self.image_tree.topLevelItem(i).data(0, Qt.UserRole) for i in range(self.image_tree.topLevelItemCount())]
        self.config_manager.save_image_order(top_level_order)
        for i in range(self.image_tree.topLevelItemCount()):
            item = self.image_tree.topLevelItem(i); path = item.data(0, Qt.UserRole)
            if path and Path(path).is_dir():
                child_order = [item.child(j).text(0) for j in range(item.childCount())]
                self.config_manager.save_image_order(child_order, folder_path=path)
        self.orderChanged.emit()
        
    def get_current_item_settings(self):
        settings = {}
        for key, widget in self.item_settings_widgets.items():
            if isinstance(widget, QDoubleSpinBox): settings[key] = widget.value()
            elif isinstance(widget, QCheckBox): settings[key] = widget.isChecked()
        return settings
        
    def set_settings_from_data(self, settings_data):
        is_folder = Path(self.get_selected_item_path()[0] or "").is_dir()
        
        for widget in self.item_settings_widgets.values():
            widget.setEnabled(not is_folder)

        if not settings_data or is_folder:
            for widget in self.item_settings_widgets.values():
                widget.blockSignals(True)
                if isinstance(widget, QDoubleSpinBox): widget.setValue(0)
                elif isinstance(widget, QCheckBox): widget.setChecked(False)
                widget.blockSignals(False)
            self.preview_label.set_drawing_data(None)
            if is_folder:
                self.preview_label.setText("フォルダを選択中")
                self.preview_label.set_pixmap(None)
            return
        
        self.preview_label.set_drawing_data(settings_data)
        for key, value in settings_data.items():
            if key in self.item_settings_widgets:
                widget = self.item_settings_widgets[key]; widget.blockSignals(True)
                if isinstance(widget, (QDoubleSpinBox, QSpinBox)): widget.setValue(value if value is not None else 0)
                elif isinstance(widget, QCheckBox): widget.setChecked(bool(value))
                widget.blockSignals(False)
        self.update_drawing_mode(settings_data)

    def on_item_settings_changed(self):
        settings = self.get_current_item_settings(); self.imageSettingsChanged.emit(settings)
        
    def update_drawing_mode(self, settings):
        mode = None
        if settings and settings.get('point_click'): mode = 'point'
        elif settings and settings.get('range_click'): mode = 'range'
        self.preview_label.set_drawing_mode(mode)

    def request_save(self): self.save_timer.start()
    def toggle_monitoring(self):
        if self.monitor_button.text() == "監視開始": self.startMonitoringRequested.emit()
        else: self.stopMonitoringRequested.emit()
        
    def set_status(self, text, color="green"):
        if text == "監視中...": 
            self.monitor_button.setText("監視停止"); self.status_label.setText("監視中..."); self.status_label.setStyleSheet("font-weight: bold; color: blue;")
        elif text == "待機中": 
            self.monitor_button.setText("監視開始"); self.status_label.setText("待機中"); self.status_label.setStyleSheet("font-weight: bold; color: green;")
            self.current_best_scale_label.setText("最適スケール: ---")
            self.current_best_scale_label.setStyleSheet("color: gray;")
        else: 
            self.status_label.setText(text); self.status_label.setStyleSheet(f"font-weight: bold; color: {color};")
    
    def on_best_scale_found(self, image_path: str, scale: float):
        current_selected_path, _ = self.get_selected_item_path()
        if image_path and image_path == current_selected_path:
            self.current_best_scale_label.setText(f"最適スケール: {scale:.3f}倍")
            self.current_best_scale_label.setStyleSheet("color: green;")

    def on_window_scale_calculated(self, scale: float):
        if scale > 0:
            self.current_best_scale_label.setText(f"計算スケール: {scale:.3f}倍")
            self.current_best_scale_label.setStyleSheet("color: purple;")
            self.auto_scale_widgets['center'].setValue(scale)
        else:
            self.current_best_scale_label.setText("最適スケール: ---")
            self.current_best_scale_label.setStyleSheet("color: gray;")
            
    def prompt_to_save_base_size(self, window_title: str) -> bool:
        reply = QMessageBox.question(
            self,
            "基準サイズの確認",
            f"ウィンドウ '{window_title}'\n\nこのウィンドウの現在のサイズを基準サイズ (1.0倍) として記憶しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        return reply == QMessageBox.StandardButton.Yes

    def show_prompt_to_save_base_size(self, window_title: str):
        save_as_base = self.prompt_to_save_base_size(window_title)
        if self.core_engine:
            self.core_engine.process_base_size_prompt_response(save_as_base)
            
    def show_prompt_to_apply_scale(self, scale: float):
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
        file_paths, _ = QFileDialog.getOpenFileNames(self, "画像を選択", str(self.config_manager.base_dir), "画像ファイル (*.png *.jpg *.jpeg *.bmp)")
        if file_paths: self.set_tree_enabled(False); self.loadImagesRequested.emit(file_paths)
        
    def update_image_preview(self, cv_image: np.ndarray, settings_data: dict = None):
        self.set_settings_from_data(settings_data)
        if cv_image is None or cv_image.size == 0:
            if not (self.get_selected_item_path()[0] and Path(self.get_selected_item_path()[0]).is_dir()):
                self.preview_label.setText("画像を選択してください")
                self.preview_label.set_pixmap(None)
            return
            
        h, w = cv_image.shape[:2]; q_image = QImage(cv_image.data, w, h, 3 * w, QImage.Format.Format_BGR888)
        pixmap = QPixmap.fromImage(q_image)
        self.preview_label.set_pixmap(pixmap)
        
    def update_rec_area_preview(self, cv_image: np.ndarray):
        if cv_image is None or cv_image.size == 0:
            self.rec_area_preview_label.set_pixmap(None); self.rec_area_preview_label.setText("認識範囲プレビュー"); return
        h, w = cv_image.shape[:2]
        q_image = QImage(cv2.cvtColor(cv_image, cv2.COLOR_BGR2RGB).data, w, h, 3 * w, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(q_image)
        self.rec_area_preview_label.set_pixmap(pixmap)
        
    def update_log(self, message: str): self.log_text.append(message)
    def closeEvent(self, event):
        self.core_engine.cleanup(); self.stopMonitoringRequested.emit(); QApplication.instance().quit(); event.accept()
        
    def setRecAreaDialog(self):
        dialog = RecAreaSelectionDialog(self)
        dialog.selectionMade.connect(self.setRecAreaMethodSelected)
        dialog.move(QCursor.pos())
        dialog.exec()
