# ocr_settings_dialog.py
# ★★★ 修正: 保存時に整数判定を行い、不要な.0を除去して内部データを最適化 ★★★

import time
import cv2
import numpy as np
import re
import sys
import webbrowser
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QSlider, QComboBox, QCheckBox, QGroupBox, QTextEdit, 
    QMessageBox, QWidget, QSizePolicy, QLineEdit, QFormLayout,
    QScrollArea, QFrame, QGridLayout, QSplitter
)
from PySide6.QtCore import Qt, Signal, Slot, QTimer, QRectF, QPoint, QPointF, QEvent
from PySide6.QtGui import QImage, QPixmap, QPainter, QPen, QColor, QBrush, QMouseEvent, QWheelEvent, QFont, QFontMetrics

from ocr_manager import OCRConfig, OCRManager, TESS_CODE_DISPLAY_MAP
from ocr_runtime import OCRRuntimeEvaluator

class OCRPreviewLabel(QLabel):
    rect_changed = Signal(tuple)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(100, 100)
        self.setMouseTracking(True)
        self.setAlignment(Qt.AlignCenter)
        self._pixmap = QPixmap()
        self.ocr_roi_rect = None
        self.parent_settings = {}
        self.is_drawing = False
        self.start_pos = QPoint()
        self.current_pos = QPoint()
        self.display_rect = QRectF()
        self.scale_x = 1.0
        self.scale_y = 1.0
        # ズームヒント表示用（文言は外部からセット。未設定時は非表示）
        self.zoom_hint_text = ""
        self.zoom_hint_enabled = True
        
        # ズーム機能用の変数
        self.base_scale = 1.0  # 基本スケール（自動フィット）
        self.user_zoom_factor = 1.0  # ユーザーズーム倍率（初期値1.0）
        self.effective_scale = 1.0  # 実効スケール（base_scale * user_zoom）
        self.zoomed_display_rect = QRectF()  # ズーム後の表示領域

    def set_image(self, pixmap: QPixmap, reset_zoom: bool = True):
        self._pixmap = pixmap if pixmap and not pixmap.isNull() else QPixmap()
        # 画像が変更されたらデフォルトの拡大率（アスペクト比を保持してプレビューエリアからはみ出ないように最大拡大）にリセット
        if reset_zoom:
            self.user_zoom_factor = 1.0
            self.zoomed_display_rect = QRectF()
        self._update_geometry()
        self.update()

    def set_zoom_hint(self, text: str):
        """ズームヒントの文言を設定"""
        self.zoom_hint_text = text if text else ""
        self.update()

    def set_roi(self, rect):
        self.ocr_roi_rect = rect
        self.update()

    def set_parent_settings(self, settings):
        self.parent_settings = settings if settings else {}
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_geometry()

    def _update_geometry(self):
        """基本スケールと表示領域を計算（ズーム前の基準）"""
        if self._pixmap.isNull() or self.width() == 0 or self.height() == 0:
            self.display_rect = QRectF()
            self.base_scale = 1.0
            self.effective_scale = 1.0
            self.scale_x = 1.0
            self.scale_y = 1.0
            self.zoomed_display_rect = QRectF()
            return
        
        w_r = self.width() / self._pixmap.width()
        h_r = self.height() / self._pixmap.height()
        self.base_scale = min(w_r, h_r)
        
        # 実効スケール（ズーム適用）
        self.effective_scale = self.base_scale * self.user_zoom_factor
        
        # 基本表示領域
        base_disp_w = self._pixmap.width() * self.base_scale
        base_disp_h = self._pixmap.height() * self.base_scale
        base_disp_x = (self.width() - base_disp_w) / 2
        base_disp_y = (self.height() - base_disp_h) / 2
        self.display_rect = QRectF(base_disp_x, base_disp_y, base_disp_w, base_disp_h)
        
        # ズーム後の表示領域
        if self.user_zoom_factor == 1.0:
            self.zoomed_display_rect = self.display_rect
        else:
            # ズームが適用されている場合は、現在のzoomed_display_rectを維持
            if not self.zoomed_display_rect.isEmpty():
                # リサイズ時は中心を維持
                center_x = self.zoomed_display_rect.x() + self.zoomed_display_rect.width() / 2
                center_y = self.zoomed_display_rect.y() + self.zoomed_display_rect.height() / 2
                new_disp_w = self._pixmap.width() * self.effective_scale
                new_disp_h = self._pixmap.height() * self.effective_scale
                self.zoomed_display_rect = QRectF(
                    center_x - new_disp_w / 2,
                    center_y - new_disp_h / 2,
                    new_disp_w,
                    new_disp_h
                )
            else:
                self.zoomed_display_rect = self.display_rect
        
        # 後方互換性のため、scale_x/yも更新
        self.scale_x = self.effective_scale
        self.scale_y = self.effective_scale

    def _map_widget_to_image(self, pos):
        """ウィジェット座標を画像座標に変換（ズーム対応）"""
        if self._pixmap.isNull():
            return None
        
        # 表示領域を決定（ズーム後の表示領域が空の場合は基本表示領域を使用）
        if self.zoomed_display_rect.isEmpty():
            display_rect = self.display_rect
            scale = self.base_scale
        else:
            display_rect = self.zoomed_display_rect
            scale = self.effective_scale
        
        if display_rect.isEmpty():
            return None
        
        # カーソル位置が表示領域外でも、拡大時には表示領域内になる可能性があるため、
        # 表示領域を基準に相対位置を計算（負の値や1以上の値も許容）
        rel_x = (pos.x() - display_rect.x()) / scale
        rel_y = (pos.y() - display_rect.y()) / scale
        
        # 画像座標に変換（画像範囲外の場合はクランプ）
        img_x = max(0, min(int(rel_x), self._pixmap.width() - 1))
        img_y = max(0, min(int(rel_y), self._pixmap.height() - 1))
        return QPoint(img_x, img_y)

    def wheelEvent(self, event: QWheelEvent):
        """マウスホイールでズーム（カーソル位置を中心に）"""
        delta = event.angleDelta().y()
        zoom_delta = delta / 120.0 * 0.1  # ホイール1クリック = 0.1倍ズーム
        self._apply_zoom(zoom_delta, event.position().toPoint())
        event.accept()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            img_pos = self._map_widget_to_image(event.pos())
            if img_pos:
                self.is_drawing = True
                self.start_pos = img_pos
                self.current_pos = img_pos
                self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self.is_drawing:
            img_pos = self._map_widget_to_image(event.pos())
            if img_pos:
                self.current_pos = img_pos
                self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton and self.is_drawing:
            self.is_drawing = False
            x = min(self.start_pos.x(), self.current_pos.x())
            y = min(self.start_pos.y(), self.current_pos.y())
            w = abs(self.current_pos.x() - self.start_pos.x())
            h = abs(self.current_pos.y() - self.start_pos.y())
            if w > 5 and h > 5:
                img_w, img_h = self._pixmap.width(), self._pixmap.height()
                x, y = max(0, min(x, img_w)), max(0, min(y, img_h))
                w, h = min(w, img_w - x), min(h, img_h - y)
                self.ocr_roi_rect = (x, y, w, h)
                self.rect_changed.emit(self.ocr_roi_rect)
            self.update()

    def _apply_zoom(self, zoom_delta, cursor_widget_pos):
        """
        マウスカーソル位置を中心にズームを適用
        
        Args:
            zoom_delta: ズーム変化量（正:拡大、負:縮小）
            cursor_widget_pos: カーソルのウィジェット座標
        """
        if self._pixmap.isNull():
            return
        
        # 1. 現在のカーソル位置を画像座標に変換
        current_image_pos = self._map_widget_to_image(cursor_widget_pos)
        if current_image_pos is None:
            return  # 画像外の場合はズームしない
        
        # 2. ズーム倍率を更新
        old_zoom = self.user_zoom_factor
        self.user_zoom_factor = max(0.1, min(15.0, self.user_zoom_factor + zoom_delta))
        
        if old_zoom == self.user_zoom_factor:
            return  # ズーム限界に達した
        
        # 3. 実効スケールを計算
        self.effective_scale = self.base_scale * self.user_zoom_factor
        
        # 4. ズーム後の表示サイズを計算
        new_display_width = self._pixmap.width() * self.effective_scale
        new_display_height = self._pixmap.height() * self.effective_scale
        
        # 5. カーソル位置を中心に表示領域を再計算
        if old_zoom > 0:
            zoom_ratio = self.user_zoom_factor / old_zoom
        else:
            zoom_ratio = self.user_zoom_factor
        
        # 現在のカーソル位置での表示領域のオフセットを計算
        if self.zoomed_display_rect.isEmpty():
            display_rect = self.display_rect
        else:
            display_rect = self.zoomed_display_rect
        
        current_offset_x = cursor_widget_pos.x() - display_rect.x()
        current_offset_y = cursor_widget_pos.y() - display_rect.y()
        
        # ズーム後の新しいオフセット（カーソル位置は変わらない）
        new_offset_x = current_offset_x * zoom_ratio
        new_offset_y = current_offset_y * zoom_ratio
        
        # 新しい表示領域の位置を計算
        # プレビューエリアの端を無視して、マウスカーソルを中心に拡大し続ける
        new_display_x = cursor_widget_pos.x() - new_offset_x
        new_display_y = cursor_widget_pos.y() - new_offset_y
        
        # 6. ズーム後の表示領域を更新
        self.zoomed_display_rect = QRectF(
            new_display_x, new_display_y,
            new_display_width, new_display_height
        )
        
        # 後方互換性のため、scale_x/yも更新
        self.scale_x = self.effective_scale
        self.scale_y = self.effective_scale
        
        # 7. 再描画
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.black)
        if self._pixmap.isNull(): return
        
        # ズーム後の表示領域を使用
        display_rect = self.zoomed_display_rect if not self.zoomed_display_rect.isEmpty() else self.display_rect
        painter.drawPixmap(display_rect.toRect(), self._pixmap)
        
        def to_screen(x, y):
            """画像座標をウィジェット座標に変換（ズーム対応）"""
            sx = display_rect.x() + x * self.effective_scale
            sy = display_rect.y() + y * self.effective_scale
            return QPointF(sx, sy)

        if self.parent_settings.get('roi_enabled'):
            roi_mode = self.parent_settings.get('roi_mode', 'fixed')
            p_roi = self.parent_settings.get('roi_rect_variable') if roi_mode == 'variable' else self.parent_settings.get('roi_rect')
            if p_roi:
                tl = to_screen(p_roi[0], p_roi[1])
                br = to_screen(p_roi[2], p_roi[3])
                painter.setPen(QPen(QColor(0, 255, 0, 200), 2))
                painter.setBrush(QColor(0, 255, 0, 50))
                painter.drawRect(QRectF(tl, br))
        
        if self.parent_settings.get('point_click') and self.parent_settings.get('click_position'):
            cx, cy = self.parent_settings['click_position']
            pt = to_screen(cx, cy)
            painter.setPen(QPen(QColor(255, 0, 0, 200), 2))
            painter.setBrush(QColor(255, 0, 0, 150))
            painter.drawEllipse(pt, 4, 4) 
        elif self.parent_settings.get('range_click') and self.parent_settings.get('click_rect'):
            c_rect = self.parent_settings['click_rect']
            tl = to_screen(c_rect[0], c_rect[1])
            br = to_screen(c_rect[2], c_rect[3])
            painter.setPen(QPen(QColor(50, 100, 255, 150), 1, Qt.DashLine))
            painter.setBrush(QColor(50, 100, 255, 30))
            painter.drawRect(QRectF(tl, br))

        if self.ocr_roi_rect:
            x, y, w, h = self.ocr_roi_rect
            tl = to_screen(x, y)
            sw = w * self.scale_x
            sh = h * self.scale_y
            painter.setPen(QPen(QColor("#9c27b0"), 2))
            painter.setBrush(QColor(156, 39, 176, 50))
            painter.drawRect(QRectF(tl.x(), tl.y(), sw, sh))

        if self.is_drawing:
            x = min(self.start_pos.x(), self.current_pos.x())
            y = min(self.start_pos.y(), self.current_pos.y())
            w = abs(self.current_pos.x() - self.start_pos.x())
            h = abs(self.current_pos.y() - self.start_pos.y())
            tl = to_screen(x, y)
            sw = w * self.scale_x
            sh = h * self.scale_y
            painter.setPen(QPen(QColor("#9c27b0"), 1, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(QRectF(tl.x(), tl.y(), sw, sh))

        # ズームヒント
        self._draw_zoom_hint(painter, display_rect)

    def _draw_zoom_hint(self, painter: QPainter, display_rect: QRectF):
        """右上にズームヒントを描画"""
        if not self.zoom_hint_enabled or not self.zoom_hint_text:
            return

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)

        margin = 8
        padding = 6
        icon_size = 12

        font = QFont(self.font())
        if font.pointSizeF() > 0:
            font.setPointSizeF(max(9.0, font.pointSizeF() * 0.9))
        painter.setFont(font)
        fm = QFontMetrics(font)

        text_w = fm.horizontalAdvance(self.zoom_hint_text)
        text_h = fm.height()
        content_w = icon_size + 6 + text_w
        content_h = max(icon_size, text_h)

        bg_w = padding * 2 + content_w
        bg_h = padding * 2 + content_h
        bg_x = self.width() - margin - bg_w
        bg_y = display_rect.y() + margin
        bg_rect = QRectF(bg_x, bg_y, bg_w, bg_h)

        painter.setBrush(QColor(0, 0, 0, 140))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(bg_rect, 6, 6)

        icon_x = bg_rect.x() + padding
        icon_y = bg_rect.y() + padding
        painter.setPen(QPen(QColor(255, 255, 255, 210), 1.5))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QRectF(icon_x, icon_y, icon_size, icon_size))
        cx = icon_x + icon_size / 2
        cy = icon_y + icon_size / 2
        line_len = icon_size * 0.35
        painter.drawLine(cx - line_len, cy, cx + line_len, cy)
        painter.drawLine(cx, cy - line_len, cx, cy + line_len)

        text_x = icon_x + icon_size + 6
        baseline = bg_rect.y() + padding + (content_h - text_h) / 2 + fm.ascent()
        painter.setPen(QColor(255, 255, 255, 230))
        painter.drawText(text_x, baseline, self.zoom_hint_text)

        painter.restore()

class ProcessedImageLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background-color: #424242; border: 1px solid #757575;")
        self._pixmap = QPixmap()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_image(self, pixmap):
        self._pixmap = pixmap if pixmap and not pixmap.isNull() else QPixmap()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        if self._pixmap.isNull():
            painter.setPen(QColor("#bdbdbd"))
            painter.drawText(self.rect(), Qt.AlignCenter, "No Image")
            return

        label_size = self.size()
        scaled = self._pixmap.scaled(label_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        
        x = (label_size.width() - scaled.width()) // 2
        y = (label_size.height() - scaled.height()) // 2
        
        painter.drawPixmap(x, y, scaled)

class OCRSettingsDialog(QDialog):
    def __init__(self, parent_image, current_config: OCRConfig, current_roi=None, current_condition=None, enabled=True, parent=None):
        super().__init__(parent)
        
        self._last_input_click_time = 0
        # タイトルバーの最小化・閉じるボタンを非表示にする
        flags = self.windowFlags() | Qt.CustomizeWindowHint  # カスタムヒントを有効化（XFCE等で×が残る対策）
        flags &= ~Qt.WindowMinMaxButtonsHint                 # 最小化ボタン除去
        flags &= ~Qt.WindowCloseButtonHint                   # 閉じるボタン除去
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_InputMethodEnabled, True)
        
        self.locale_manager = None
        if parent and hasattr(parent, 'locale_manager'):
            self.locale_manager = parent.locale_manager
        
        self.original_image = parent_image
        self.preview_image = parent_image.copy()
        self.config = current_config
        self.roi = current_roi
        self.condition = current_condition if current_condition else {"operator": ">=", "value": 0}
        self.enabled = enabled
        
        self.ocr_manager = OCRManager()
        self.parent_item_settings = {}
        self.previous_lang_idx = -1

        self.debounce_timer = QTimer()
        self.debounce_timer.setSingleShot(True)
        self.debounce_timer.setInterval(100) 
        self.debounce_timer.timeout.connect(self.update_preview_image)

        self.setup_ui()
        self.load_initial_preview()

    def tr(self, key, *args):
        if self.locale_manager:
            return self.locale_manager.tr(key, *args)
        return key

    def set_parent_settings(self, settings):
        self.parent_item_settings = settings
        if self.image_label:
            self.image_label.set_parent_settings(settings)

    def setup_ui(self):
        self.setWindowTitle(self.tr("ocr_dialog_title"))
        self.resize(1000, 680)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        # --- 上部: プレビューエリア (左右分割) ---
        preview_container = QWidget()
        preview_container.setStyleSheet("background-color: #263238; border-radius: 4px;")
        
        preview_layout = QHBoxLayout(preview_container)
        preview_layout.setContentsMargins(5, 5, 5, 5)
        preview_layout.setSpacing(10)
        
        # [左側] メイン画像 (操作用)
        left_preview_widget = QWidget()
        left_preview_layout = QVBoxLayout(left_preview_widget)
        left_preview_layout.setContentsMargins(0, 0, 0, 0)
        left_preview_layout.setSpacing(5)
        
        lbl_hint = QLabel(self.tr("ocr_hint_drag"))
        lbl_hint.setStyleSheet("color: #b0bec5; font-weight: bold; padding: 2px;")
        lbl_hint.setAlignment(Qt.AlignCenter)
        
        self.image_label = OCRPreviewLabel()
        self.image_label.set_zoom_hint(self.tr("preview_zoom_hint"))
        self.image_label.rect_changed.connect(self.on_roi_changed)
        
        left_preview_layout.addWidget(lbl_hint)
        left_preview_layout.addWidget(self.image_label, 1)
        
        # [右側] 結果表示パネル (1/4幅)
        right_result_widget = QWidget()
        right_result_widget.setFixedWidth(240) 
        right_result_layout = QVBoxLayout(right_result_widget)
        right_result_layout.setContentsMargins(0, 0, 0, 0)
        right_result_layout.setSpacing(5)
        
        # 1. 処理画像ラベル
        lbl_res_img = QLabel(self.tr("ocr_lbl_result_img"))
        if lbl_res_img.text() == "ocr_lbl_result_img": lbl_res_img.setText("Processed Image")
        lbl_res_img.setStyleSheet("color: #b0bec5; font-size: 11px; font-weight: bold;")
        right_result_layout.addWidget(lbl_res_img)
        
        # 2. 画像表示エリア (正方形)
        self.result_image_label = ProcessedImageLabel()
        self.result_image_label.setMinimumHeight(240)
        right_result_layout.addWidget(self.result_image_label, 1) # 1/3
        
        # 3. テキスト結果ラベル
        lbl_res_text = QLabel(self.tr("ocr_lbl_result_text"))
        if lbl_res_text.text() == "ocr_lbl_result_text": lbl_res_text.setText("RAW Data")
        lbl_res_text.setStyleSheet("color: #b0bec5; font-size: 11px; font-weight: bold; margin-top: 5px;")
        right_result_layout.addWidget(lbl_res_text)
        
        # 4. 結果詳細テキストエリア (下2/3)
        self.result_text_edit = QTextEdit()
        self.result_text_edit.setReadOnly(True)
        # ユーザー指示: RAWデータのみを表示
        self.result_text_edit.setStyleSheet("""
            QTextEdit {
                background-color: #37474f;
                color: #ffffff;
                border: 1px solid #546e7a;
                font-family: Consolas, monospace;
                font-size: 14px;
                font-weight: bold;
            }
        """)
        right_result_layout.addWidget(self.result_text_edit, 2) # 2/3
        
        preview_layout.addWidget(left_preview_widget, 1)
        preview_layout.addWidget(right_result_widget)
        
        main_layout.addWidget(preview_container, 1) 

        # --- 下部: 設定パネル ---
        settings_panel = QWidget()
        settings_panel.setStyleSheet("""
            QWidget#SettingsPanel {
                background-color: #f5f5f5; 
                border: 1px solid #cfd8dc; 
                border-radius: 6px;
            }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #cfd8dc;
                border-radius: 4px;
                margin-top: 6px;
                padding-top: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 3px;
            }
        """)
        settings_panel.setObjectName("SettingsPanel")
        
        panel_layout = QVBoxLayout(settings_panel)
        panel_layout.setContentsMargins(10, 10, 10, 10)
        panel_layout.setSpacing(10)

        groups_layout = QHBoxLayout()
        groups_layout.setSpacing(10)

        img_group = QGroupBox(self.tr("ocr_grp_preprocess"))
        img_layout = QGridLayout()
        img_layout.setContentsMargins(8, 8, 8, 8)
        img_layout.setVerticalSpacing(4)
        img_layout.setHorizontalSpacing(8)

        img_layout.addWidget(QLabel(self.tr("ocr_lbl_scale")), 0, 0)
        self.combo_scale = QComboBox()
        self.combo_scale.addItems(["1.0x", "2.0x", "3.0x"])
        scale_index = int(self.config.scale - 1)
        self.combo_scale.setCurrentIndex(max(0, min(scale_index, 2)))
        self.combo_scale.currentIndexChanged.connect(self.trigger_preview_update)
        img_layout.addWidget(self.combo_scale, 0, 1)

        img_layout.addWidget(QLabel(self.tr("ocr_lbl_threshold")), 1, 0)
        thresh_hbox = QHBoxLayout()
        self.slider_thresh = QSlider(Qt.Horizontal)
        self.slider_thresh.setRange(0, 255)
        self.slider_thresh.setValue(self.config.threshold)
        self.slider_thresh.valueChanged.connect(self.trigger_preview_update)
        thresh_hbox.addWidget(self.slider_thresh)
        self.label_thresh_val = QLabel(f"{self.config.threshold}")
        self.label_thresh_val.setFixedWidth(30)
        thresh_hbox.addWidget(self.label_thresh_val)
        img_layout.addLayout(thresh_hbox, 1, 1)

        self.chk_invert = QCheckBox(self.tr("ocr_chk_invert"))
        self.chk_invert.setChecked(self.config.invert)
        self.chk_invert.stateChanged.connect(self.trigger_preview_update)
        img_layout.addWidget(self.chk_invert, 2, 0, 1, 2)

        img_group.setLayout(img_layout)
        groups_layout.addWidget(img_group, 1)

        logic_group = QGroupBox(self.tr("ocr_grp_detection") + " / " + self.tr("ocr_grp_condition"))
        logic_layout = QGridLayout()
        logic_layout.setContentsMargins(8, 8, 8, 8)
        logic_layout.setVerticalSpacing(4)
        logic_layout.setHorizontalSpacing(8)
        
        logic_layout.addWidget(QLabel(self.tr("ocr_lbl_lang")), 0, 0)
        self.combo_lang = QComboBox()
        other_langs = sorted([code for code in TESS_CODE_DISPLAY_MAP.keys() if code not in ["eng", "jpn"]])
        def add_item(code, display): self.combo_lang.addItem(display, code)
        add_item("eng", TESS_CODE_DISPLAY_MAP["eng"])
        add_item("jpn", TESS_CODE_DISPLAY_MAP["jpn"])
        add_item("jpn+eng", "Japanese + English")
        for code in other_langs: add_item(code, TESS_CODE_DISPLAY_MAP[code])
        
        current_lang_code = self.config.lang
        idx = self.combo_lang.findData(current_lang_code)
        if idx >= 0: self.combo_lang.setCurrentIndex(idx)
        else:
            self.combo_lang.addItem(current_lang_code, current_lang_code)
            self.combo_lang.setCurrentIndex(self.combo_lang.count() - 1)
        self.combo_lang.currentIndexChanged.connect(self.on_language_changed)
        logic_layout.addWidget(self.combo_lang, 0, 1)

        self.chk_numeric = QCheckBox(self.tr("ocr_chk_numeric"))
        self.chk_numeric.setChecked(self.config.numeric_mode)
        self.chk_numeric.setToolTip(self.tr("ocr_tooltip_numeric"))
        self.chk_numeric.stateChanged.connect(self.on_numeric_mode_changed)
        logic_layout.addWidget(self.chk_numeric, 0, 2)

        logic_layout.addWidget(QLabel(self.tr("ocr_lbl_operator")), 1, 0)
        self.combo_operator = QComboBox()
        logic_layout.addWidget(self.combo_operator, 1, 1)
        
        self.input_target_value = QLineEdit()
        self.input_target_value.setPlaceholderText(self.tr("ocr_placeholder_target"))
        
        self.input_target_value.setReadOnly(False) 
        self.input_target_value.setStyleSheet("background-color: #ffffff; color: #333;")
        self.input_target_value.installEventFilter(self)
        
        logic_layout.addWidget(self.input_target_value, 1, 2)

        current_op_key = self.condition.get("operator", ">=")
        self.update_operator_list(current_op_key)
        
        # --- 初期値の処理 (NULL化と.0除去) ---
        init_val = str(self.condition.get("value", ""))
        
        # 1. "0" や "0.0" などの初期値を空欄にする (ユーザー要望)
        if init_val == "0" or init_val == "0.0":
            init_val = ""
        # 2. 既に値が入っている場合、末尾の.0を削除して表示する
        elif init_val.endswith(".0"):
            init_val = init_val[:-2]
            
        self.input_target_value.setText(init_val)
        # ------------------------------------

        logic_group.setLayout(logic_layout)
        groups_layout.addWidget(logic_group, 2)

        panel_layout.addLayout(groups_layout)

        bottom_bar_layout = QHBoxLayout()
        bottom_bar_layout.setSpacing(15)

        self.chk_enable = QCheckBox(self.tr("ocr_chk_enable"))
        self.chk_enable.setStyleSheet("font-weight: bold; color: #9c27b0;")
        self.chk_enable.setChecked(self.enabled)
        bottom_bar_layout.addWidget(self.chk_enable)

        self.btn_help = QPushButton("?")
        self.btn_help.setFixedWidth(30)
        self.btn_help.setToolTip(self.tr("ocr_help_tooltip"))
        self.btn_help.setStyleSheet("""
            QPushButton {
                background-color: #e3f2fd; 
                border: 1px solid #2196f3; 
                color: #1565c0; 
                font-weight: bold; 
                border-radius: 15px;
            }
            QPushButton:hover { background-color: #bbdefb; }
        """)
        self.btn_help.clicked.connect(self.show_tesseract_guide)
        bottom_bar_layout.addWidget(self.btn_help)

        line = QFrame()
        line.setFrameShape(QFrame.VLine)
        line.setFrameShadow(QFrame.Sunken)
        bottom_bar_layout.addWidget(line)

        self.btn_test = QPushButton(self.tr("ocr_btn_test"))
        self.btn_test.setIcon(QPixmap()) 
        self.btn_test.clicked.connect(self.run_ocr_test)
        self.btn_test.setStyleSheet("background-color: #e0f7fa; border: 1px solid #00acc1; padding: 4px 10px; border-radius: 4px;")
        bottom_bar_layout.addWidget(self.btn_test)

        self.text_log = QTextEdit()
        self.text_log.setReadOnly(True)
        self.text_log.setFixedHeight(45) 
        self.text_log.setStyleSheet("background-color: #fafafa; border: 1px solid #cfd8dc; font-size: 11px;")
        bottom_bar_layout.addWidget(self.text_log, 1)

        btn_box = QHBoxLayout()
        self.btn_ok = QPushButton("OK") 
        self.btn_ok.setFixedWidth(80)
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setFixedWidth(80)
        self.btn_cancel.clicked.connect(self.reject)
        
        btn_box.addWidget(self.btn_ok)
        btn_box.addWidget(self.btn_cancel)
        bottom_bar_layout.addLayout(btn_box)

        panel_layout.addLayout(bottom_bar_layout)

        main_layout.addWidget(settings_panel)
        
        self.on_numeric_mode_changed()
        self.on_enable_toggled(self.enabled)

    def eventFilter(self, source, event):
        if source == self.input_target_value and event.type() == QEvent.MouseButtonPress:
            if event.button() == Qt.LeftButton:
                if sys.platform != 'win32':
                    current_time = time.time()
                    if current_time - self._last_input_click_time < 1.0: return True
                    self._last_input_click_time = current_time
                    self.open_custom_input_dialog()
                    return True 
        return super().eventFilter(source, event)

    def open_custom_input_dialog(self):
        try: from custom_input_dialog import ask_string_custom
        except ImportError: QMessageBox.critical(self, "Error", "custom_input_dialog.py not found."); return
        current_val = self.input_target_value.text()
        title = self.tr("ocr_grp_condition")
        prompt = self.tr("ocr_placeholder_target")
        if title == "ocr_grp_condition": title = "Condition"
        if prompt == "ocr_placeholder_target": prompt = "Target Value"
        new_val, ok = ask_string_custom(self, title, prompt, current_val)
        if ok: self.input_target_value.setText(new_val)

    def on_enable_toggled(self, checked): pass
    def load_initial_preview(self):
        self.update_preview_image()
        if self.roi: self.image_label.set_roi(self.roi)

    def on_numeric_mode_changed(self):
        self.config.numeric_mode = self.chk_numeric.isChecked()
        if self.config.numeric_mode:
            if self.combo_lang.isEnabled(): self.previous_lang_idx = self.combo_lang.currentIndex()
            idx = self.combo_lang.findData("eng")
            if idx >= 0:
                self.combo_lang.blockSignals(True); self.combo_lang.setCurrentIndex(idx); self.combo_lang.blockSignals(False)
            self.combo_lang.setEnabled(False)
        else:
            self.combo_lang.setEnabled(True)
            if self.previous_lang_idx >= 0:
                self.combo_lang.blockSignals(True); self.combo_lang.setCurrentIndex(self.previous_lang_idx); self.combo_lang.blockSignals(False)
        current_op = self.combo_operator.currentData()
        self.update_operator_list(current_op)
        self.trigger_preview_update()

    def update_operator_list(self, current_op_key=None):
        if current_op_key is None: current_op_key = self.combo_operator.currentData()
        self.combo_operator.clear()
        if self.chk_numeric.isChecked():
            ops = [
                (self.tr("op_gte"), ">="),
                (self.tr("op_lte"), "<="),
                (self.tr("op_eq"), "=="),
                (self.tr("op_neq"), "!="),
                (self.tr("op_gt"), ">"),
                (self.tr("op_lt"), "<")
            ]
        else:
            ops = [(self.tr("op_contains"), "Contains"), (self.tr("op_equals"), "Equals"), (self.tr("op_regex"), "Regex")]
        for display, key in ops: self.combo_operator.addItem(display, key)
        idx = self.combo_operator.findData(current_op_key)
        if idx >= 0: self.combo_operator.setCurrentIndex(idx)
        else: self.combo_operator.setCurrentIndex(0)

    @Slot()
    def trigger_preview_update(self):
        self.label_thresh_val.setText(f"{self.slider_thresh.value()}")
        self.debounce_timer.start()

    def update_preview_image(self):
        self.config.scale = float(self.combo_scale.currentText().replace('x', ''))
        self.config.threshold = self.slider_thresh.value()
        self.config.invert = self.chk_invert.isChecked()
        self.config.lang = self.combo_lang.currentData() 
        self.config.numeric_mode = self.chk_numeric.isChecked()
        try:
            temp_img = self.original_image.copy()
            if len(temp_img.shape) == 3: gray = cv2.cvtColor(temp_img, cv2.COLOR_BGR2GRAY)
            else: gray = temp_img
            if self.config.invert: gray = cv2.bitwise_not(gray)
            _, binary = cv2.threshold(gray, self.config.threshold, 255, cv2.THRESH_BINARY)
            disp_img = cv2.cvtColor(binary, cv2.COLOR_GRAY2RGB)
            h, w, ch = disp_img.shape
            bytes_per_line = ch * w
            qt_image = QImage(disp_img.data, w, h, bytes_per_line, QImage.Format_RGB888)
            self.image_label.set_image(QPixmap.fromImage(qt_image), reset_zoom=False)  # 設定変更時はズームをリセットしない
        except Exception as e:
            self.text_log.setText(f"Preview Error: {str(e)}")

    @Slot(tuple)
    def on_roi_changed(self, rect): self.roi = rect

    @Slot()
    def on_language_changed(self):
        self.trigger_preview_update()
        selected_code = self.combo_lang.currentData()
        if not selected_code: return
        if not self.ocr_manager.is_language_ready(selected_code):
            reply = QMessageBox.question(self, self.tr("ocr_msg_download_title"), self.tr("ocr_msg_download_text", selected_code), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes: self.start_download(selected_code)

    def start_download(self, lang_code):
        targets = lang_code.split('+')
        self.btn_test.setEnabled(False)
        self.btn_ok.setEnabled(False)
        msg = self.tr("ocr_msg_downloading", lang_code)
        self.text_log.setText(msg)
        self.ocr_manager.download_progress.connect(self.on_download_progress)
        self.ocr_manager.download_finished.connect(self.on_download_finished)
        self.ocr_manager.download_languages(targets)

    @Slot(str, int)
    def on_download_progress(self, filename, percent): pass

    @Slot(bool, str)
    def on_download_finished(self, success, msg):
        try:
            self.ocr_manager.download_progress.disconnect(self.on_download_progress)
            self.ocr_manager.download_finished.disconnect(self.on_download_finished)
        except: pass
        self.btn_test.setEnabled(True)
        self.btn_ok.setEnabled(True)
        if success:
            comp_msg = self.tr("ocr_msg_download_complete")
            self.text_log.setHtml(f"<font color='green'>{comp_msg}</font>")
            self.update_preview_image()
        else:
            fail_msg = self.tr("ocr_msg_download_failed", msg)
            self.text_log.setHtml(f"<font color='red'>{fail_msg}</font>")
            err_title = self.tr("error_title_download_failed")
            if err_title == "error_title_download_failed": err_title = "Download Failed"
            QMessageBox.warning(self, err_title, fail_msg)

    @Slot()
    def run_ocr_test(self):
        if not self.roi:
            QMessageBox.warning(self, "Warning", self.tr("ocr_warn_select_area"))
            return

        self.update_preview_image()
        self.text_log.clear()
        self.text_log.setText(self.tr("ocr_log_processing"))
        # 右パネルの表示もクリア
        self.result_image_label.set_image(None)
        self.result_text_edit.clear()
        
        self.btn_test.setEnabled(False)

        self.worker = self.ocr_manager.create_worker(
            self.original_image, 
            self.config, 
            self.roi
        )
        self.worker.finished.connect(self.on_ocr_finished)
        self.worker.error.connect(self.on_ocr_error)
        self.worker.start()

    @Slot(str, object, object)
    def on_ocr_finished(self, raw_text, numeric_value, processed_img):
        self.btn_test.setEnabled(True)
        
        # --- 1. 右上パネルに画像を表示 ---
        if processed_img is not None:
            try:
                if len(processed_img.shape) == 2:
                    h, w = processed_img.shape
                    qimg = QImage(processed_img.data, w, h, w, QImage.Format_Grayscale8)
                    self.result_image_label.set_image(QPixmap.fromImage(qimg))
                else:
                    self.result_image_label.setText("Fmt Error")
            except Exception as e:
                self.result_image_label.setText("Img Error")
        
        # --- 2. 判定ロジック ---
        target_val_str = self.input_target_value.text()
        operator = self.combo_operator.currentData()
        
        result = False
        status_text = "FAIL"
        color = "orange"
        
        if self.config.numeric_mode and numeric_value is not None:
            try:
                tgt = float(target_val_str)
                nv = numeric_value
                if operator == ">=": result = (nv >= tgt)
                elif operator == "<=": result = (nv <= tgt)
                elif operator == "==": result = (nv == tgt)
                elif operator == "!=": result = (nv != tgt)
                elif operator == ">": result = (nv > tgt)
                elif operator == "<": result = (nv < tgt)
            except: pass
        else:
            res_lower = raw_text.lower()
            tgt_lower = str(target_val_str).lower()
            if operator == "Equals": result = (res_lower == tgt_lower)
            elif operator == "Contains": result = (tgt_lower in res_lower)
            elif operator == "Regex":
                try: result = bool(re.search(str(target_val_str), raw_text, re.IGNORECASE))
                except: pass

        if result:
            status_text = "PASS"
            color = "green"

        # --- 3. 下部ログ(一行)の更新 ---
        # ユーザー要望: RAWデータに.0を表示しない (整数として表示)
        display_numeric = ""
        if numeric_value is not None:
            if numeric_value.is_integer():
                display_numeric = str(int(numeric_value))
            else:
                display_numeric = str(numeric_value)

        log_html = f"<b>RAW:</b> '{raw_text}'"
        if self.config.numeric_mode:
            log_html += f" | <b>Num:</b> {display_numeric}"
        log_html += f" | <font color='{color}'><b>JUDGE:</b> {status_text}</font>"
        self.text_log.setHtml(log_html)

        # --- 4. 右下パネル(詳細: RAWデータのみ)の更新 ---
        # ユーザー要望: RAWデータのみ表示 (.0なし)
        display_text = raw_text
        if self.config.numeric_mode and numeric_value is not None:
             display_text = display_numeric
             
        self.result_text_edit.setText(display_text)

    @Slot(str)
    def on_ocr_error(self, error_msg):
        self.btn_test.setEnabled(True)
        self.text_log.setHtml(f"<font color='red'>Error: {error_msg}</font>")
        self.result_text_edit.setText(error_msg)

    # ★★★ 修正: 保存時に整数判定を行い、.0を除去して保存 ★★★
    def get_result(self):
        self.config.scale = float(self.combo_scale.currentText().replace('x', ''))
        self.config.threshold = self.slider_thresh.value()
        self.config.invert = self.chk_invert.isChecked()
        self.config.lang = self.combo_lang.currentData()
        self.config.numeric_mode = self.chk_numeric.isChecked()
        
        target_val_str = self.input_target_value.text()
        final_target_val = target_val_str # デフォルトは文字列のまま

        if self.config.numeric_mode:
            try:
                val_float = float(target_val_str)
                # 整数であれば int型として保存 (JSON上で .0 が付かない)
                if val_float.is_integer():
                    final_target_val = int(val_float)
                else:
                    final_target_val = val_float
            except:
                pass 
        
        # ★★★ 修正: currentData()がNoneの場合のフォールバック処理 ★★★
        operator_value = self.combo_operator.currentData()
        if operator_value is None:
            # currentData()がNoneの場合、currentText()から演算子を抽出
            current_text = self.combo_operator.currentText()
            # 翻訳された文字列から実際の演算子値を抽出
            if self.config.numeric_mode:
                if ">=" in current_text or "gte" in current_text.lower() or "以上" in current_text:
                    operator_value = ">="
                elif "<=" in current_text or "lte" in current_text.lower() or "以下" in current_text:
                    operator_value = "<="
                elif "==" in current_text or "eq" in current_text.lower() or "一致" in current_text:
                    operator_value = "=="
                elif "!=" in current_text or "neq" in current_text.lower() or "一致しない" in current_text:
                    operator_value = "!="
                elif ">" in current_text and "=" not in current_text or "gt" in current_text.lower() or "より大きい" in current_text:
                    operator_value = ">"
                elif "<" in current_text and "=" not in current_text or "lt" in current_text.lower() or "より小さい" in current_text:
                    operator_value = "<"
                else:
                    operator_value = ">="  # デフォルト値
            else:
                if "Contains" in current_text or "含む" in current_text or "contains" in current_text.lower():
                    operator_value = "Contains"
                elif "Equals" in current_text or "等しい" in current_text or "equals" in current_text.lower():
                    operator_value = "Equals"
                elif "Regex" in current_text or "正規表現" in current_text or "regex" in current_text.lower():
                    operator_value = "Regex"
                else:
                    operator_value = "Contains"  # デフォルト値
        
        condition_data = {"operator": operator_value, "value": final_target_val}
        return self.config, self.roi, condition_data, self.chk_enable.isChecked()

    @Slot()
    def show_tesseract_guide(self):
        title = self.tr("ocr_guide_title")
        if title == "ocr_guide_title": title = "Tesseract Installation Guide"
        info_text = ""
        is_windows = sys.platform == 'win32'
        if is_windows:
            info_text = self.tr("ocr_guide_windows_content")
            if info_text == "ocr_guide_windows_content":
                info_text = "<h3>Windows - Tesseract OCR Installation</h3>..."
        else:
            info_text = self.tr("ocr_guide_linux_content")
            if info_text == "ocr_guide_linux_content":
                info_text = "<h3>Linux - Tesseract OCR Installation</h3>..."
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(600, 450)
        layout = QVBoxLayout(dialog)
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setHtml(info_text)
        layout.addWidget(text_edit)
        if is_windows:
            btn_text = self.tr("ocr_guide_btn_download")
            if btn_text == "ocr_guide_btn_download": btn_text = "Open Download Page"
            btn_open_url = QPushButton(btn_text)
            btn_open_url.clicked.connect(lambda: webbrowser.open("https://github.com/UB-Mannheim/tesseract/wiki"))
            layout.addWidget(btn_open_url)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(dialog.accept)
        layout.addWidget(btn_close)
        dialog.exec()