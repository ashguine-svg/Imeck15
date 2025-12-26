# custom_widgets.py

from PySide6.QtWidgets import QLabel, QSizePolicy
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QPixmap, QImage, QWheelEvent, QFont, QFontMetrics
from PySide6.QtCore import Qt, Signal, QPoint, QRect, QRectF, QPointF

class ScaledPixmapLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(1, 1)
        self.setAlignment(Qt.AlignCenter)

    def set_pixmap(self, pixmap):
        self._pixmap = pixmap if pixmap and not pixmap.isNull() else QPixmap()
        self.update()

    def paintEvent(self, event):
        painter = QPainter()
        if painter.begin(self):
            try:
                painter.fillRect(self.rect(), Qt.black)
                
                if self._pixmap.isNull():
                    return

                label_size = self.size()
                scaled_pixmap = self._pixmap.scaled(label_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                
                if scaled_pixmap.isNull():
                    return

                x = (label_size.width() - scaled_pixmap.width()) // 2
                y = (label_size.height() - scaled_pixmap.height()) // 2
                
                painter.drawPixmap(x, y, scaled_pixmap)
            finally:
                painter.end()


class InteractivePreviewLabel(QLabel):
    settingChanged = Signal(dict)
    roiSettingChanged = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(1, 1)
        self.setMouseTracking(True)
        self.setAlignment(Qt.AlignCenter)
        
        self.drawing_mode = None
        self.is_drawing = False
        self.start_pos = QPoint()
        self.end_pos = QPoint()
        self._pixmap = QPixmap()
        self.click_settings = {}
        # ズームヒント表示用（文言は外部からセット。未設定時は非表示）
        self.zoom_hint_text = ""
        self.zoom_hint_enabled = True
        
        self.pixmap_display_rect = QRectF()
        self.scale_x = 1.0
        self.scale_y = 1.0

        # ズーム機能用の変数
        self.base_scale_x = 1.0  # 基本スケール（自動フィット）
        self.base_scale_y = 1.0
        self.user_zoom_factor = 1.0  # ユーザーズーム倍率（初期値1.0）
        self.effective_scale_x = 1.0  # 実効スケール（base_scale * user_zoom）
        self.effective_scale_y = 1.0
        self.zoomed_display_rect = QRectF()  # ズーム後の表示領域

    def set_pixmap(self, pixmap, reset_zoom: bool = True):
        self._pixmap = pixmap if pixmap and not pixmap.isNull() else QPixmap()
        # 画像が変更されたらデフォルトの拡大率（アスペクト比を保持してプレビューエリアからはみ出ないように最大拡大）にリセット
        if reset_zoom:
            self.user_zoom_factor = 1.0
            self.zoomed_display_rect = QRectF()
        self._update_geometry_cache()
        self.update()

    def set_zoom_hint(self, text: str):
        """ズームヒントの文言を設定"""
        self.zoom_hint_text = text if text else ""
        self.update()

    def set_drawing_data(self, settings):
        self.click_settings = settings if settings else {}
        self.update()

    def set_drawing_mode(self, mode):
        self.drawing_mode = mode
        self.setCursor(Qt.CrossCursor if mode else Qt.ArrowCursor)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_geometry_cache()

    def _update_geometry_cache(self):
        """基本スケールと表示領域を計算（ズーム前の基準）"""
        if self._pixmap.isNull() or self.width() == 0 or self.height() == 0 or self._pixmap.height() == 0:
            self.pixmap_display_rect = QRectF()
            self.scale_x, self.scale_y = 1.0, 1.0
            self.base_scale_x, self.base_scale_y = 1.0, 1.0
            self.effective_scale_x, self.effective_scale_y = 1.0, 1.0
            self.zoomed_display_rect = QRectF()
            return

        pixmap_ratio = self._pixmap.width() / self._pixmap.height()
        label_ratio = self.width() / self.height()

        if pixmap_ratio > label_ratio:
            base_width = self.width()
            base_height = self.width() / pixmap_ratio
            base_x = 0
            base_y = (self.height() - base_height) / 2
        else:
            base_height = self.height()
            base_width = self.height() * pixmap_ratio
            base_x = (self.width() - base_width) / 2
            base_y = 0
        
        self.pixmap_display_rect = QRectF(base_x, base_y, base_width, base_height)

        # 基本スケールを計算
        if self._pixmap.width() > 0 and self._pixmap.height() > 0:
            self.base_scale_x = self.pixmap_display_rect.width() / self._pixmap.width()
            self.base_scale_y = self.pixmap_display_rect.height() / self._pixmap.height()
        else:
            self.base_scale_x, self.base_scale_y = 1.0, 1.0
        
        # 実効スケール（ズーム適用）
        self.effective_scale_x = self.base_scale_x * self.user_zoom_factor
        self.effective_scale_y = self.base_scale_y * self.user_zoom_factor
        
        # ズーム後の表示領域（初期状態は基本表示領域と同じ）
        if self.user_zoom_factor == 1.0:
            self.zoomed_display_rect = self.pixmap_display_rect
        else:
            # ズームが適用されている場合は、現在のzoomed_display_rectを維持
            # （リサイズ時は中心を維持）
            if not self.zoomed_display_rect.isEmpty():
                # リサイズ時は中心を維持
                center_x = self.zoomed_display_rect.x() + self.zoomed_display_rect.width() / 2
                center_y = self.zoomed_display_rect.y() + self.zoomed_display_rect.height() / 2
                new_width = self._pixmap.width() * self.effective_scale_x
                new_height = self._pixmap.height() * self.effective_scale_y
                self.zoomed_display_rect = QRectF(
                    center_x - new_width / 2,
                    center_y - new_height / 2,
                    new_width,
                    new_height
                )
            else:
                self.zoomed_display_rect = self.pixmap_display_rect
        
        # 後方互換性のため、scale_x/yも更新
        self.scale_x = self.effective_scale_x
        self.scale_y = self.effective_scale_y

    def _map_widget_to_image_coords(self, widget_pos):
        """ウィジェット座標を画像座標に変換（ズーム対応）"""
        if self._pixmap.isNull():
            return None
        
        # 表示領域を決定（ズーム後の表示領域が空の場合は基本表示領域を使用）
        if self.zoomed_display_rect.isEmpty():
            display_rect = self.pixmap_display_rect
        else:
            display_rect = self.zoomed_display_rect
        
        if display_rect.isEmpty():
            return None
        
        # カーソル位置が表示領域外でも、拡大時には表示領域内になる可能性があるため、
        # 表示領域を基準に相対位置を計算（負の値や1以上の値も許容）
        relative_x = (widget_pos.x() - display_rect.x()) / display_rect.width()
        relative_y = (widget_pos.y() - display_rect.y()) / display_rect.height()
        
        # 画像座標に変換（画像範囲外の場合はクランプ）
        img_x = relative_x * self._pixmap.width()
        img_y = relative_y * self._pixmap.height()
        img_x = max(0, min(int(img_x), self._pixmap.width() - 1))
        img_y = max(0, min(int(img_y), self._pixmap.height() - 1))
        return QPoint(img_x, img_y)

    def mousePressEvent(self, event):
        if self.drawing_mode and event.button() == Qt.LeftButton:
            img_pos = self._map_widget_to_image_coords(event.pos())
            if img_pos:
                self.is_drawing = True
                self.start_pos = img_pos
                self.end_pos = img_pos
                self.update()

    def mouseMoveEvent(self, event):
        if self.is_drawing:
            img_pos = self._map_widget_to_image_coords(event.pos())
            if img_pos:
                self.end_pos = img_pos
                self.update()

    def wheelEvent(self, event: QWheelEvent):
        """マウスホイールでズーム（カーソル位置を中心に）"""
        delta = event.angleDelta().y()
        zoom_delta = delta / 120.0 * 0.1  # ホイール1クリック = 0.1倍ズーム
        self._apply_zoom(zoom_delta, event.position().toPoint())
        event.accept()

    def mouseReleaseEvent(self, event):
        if self.is_drawing and event.button() == Qt.LeftButton:
            self.is_drawing = False
            
            if self.drawing_mode == 'point':
                self.settingChanged.emit({'click_position': [self.end_pos.x(), self.end_pos.y()]})
            elif self.drawing_mode == 'range':
                rect = QRect(self.start_pos, self.end_pos).normalized()
                self.settingChanged.emit({'click_rect': [rect.left(), rect.top(), rect.right(), rect.bottom()]})
            elif self.drawing_mode == 'roi_variable':
                rect = QRect(self.start_pos, self.end_pos).normalized()
                if rect.width() > 1 and rect.height() > 1:
                    self.roiSettingChanged.emit({'roi_rect_variable': [rect.left(), rect.top(), rect.right(), rect.bottom()]})

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
        current_image_pos = self._map_widget_to_image_coords(cursor_widget_pos)
        if current_image_pos is None:
            return  # 画像外の場合はズームしない
        
        # 2. ズーム倍率を更新
        old_zoom = self.user_zoom_factor
        self.user_zoom_factor = max(0.1, min(15.0, self.user_zoom_factor + zoom_delta))
        
        if old_zoom == self.user_zoom_factor:
            return  # ズーム限界に達した
        
        # 3. 実効スケールを計算
        self.effective_scale_x = self.base_scale_x * self.user_zoom_factor
        self.effective_scale_y = self.base_scale_y * self.user_zoom_factor
        
        # 4. ズーム後の表示サイズを計算
        new_display_width = self._pixmap.width() * self.effective_scale_x
        new_display_height = self._pixmap.height() * self.effective_scale_y
        
        # 5. カーソル位置を中心に表示領域を再計算
        # カーソル位置の画像座標が、ズーム後も同じウィジェット位置に来るように調整
        if old_zoom > 0:
            zoom_ratio = self.user_zoom_factor / old_zoom
        else:
            zoom_ratio = self.user_zoom_factor
        
        # 現在のカーソル位置での表示領域のオフセットを計算
        if self.zoomed_display_rect.isEmpty():
            display_rect = self.pixmap_display_rect
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
        self.scale_x = self.effective_scale_x
        self.scale_y = self.effective_scale_y
        
        # 7. 再描画
        self.update()

    def paintEvent(self, event):
        painter = QPainter()
        if painter.begin(self):
            try:
                painter.fillRect(self.rect(), Qt.black)

                if self._pixmap.isNull():
                    return
                
                # ズーム後の表示領域を使用
                display_rect = self.zoomed_display_rect if not self.zoomed_display_rect.isEmpty() else self.pixmap_display_rect
                painter.drawPixmap(display_rect.toRect(), self._pixmap)
                
                if self._pixmap.width() == 0 or self._pixmap.height() == 0:
                    return
                    
                def to_widget_coords(img_pos):
                    """画像座標をウィジェット座標に変換（ズーム対応）"""
                    x = display_rect.x() + img_pos[0] * self.effective_scale_x
                    y = display_rect.y() + img_pos[1] * self.effective_scale_y
                    return QPointF(x, y)
                
                painter.setRenderHint(QPainter.Antialiasing)

                # --- 1. OCR範囲 (紫枠) の描画 ---
                # settings辞書に 'ocr_settings' が含まれている場合に描画
                ocr_settings = self.click_settings.get('ocr_settings')
                if ocr_settings and ocr_settings.get('enabled') and ocr_settings.get('roi'):
                    roi_rect = ocr_settings.get('roi') # (x, y, w, h)
                    if roi_rect and len(roi_rect) == 4:
                        rx, ry, rw, rh = roi_rect
                        p_tl = to_widget_coords((rx, ry))
                        p_br = to_widget_coords((rx + rw, ry + rh))
                        
                        # 紫色のペンとブラシ
                        painter.setPen(QPen(QColor("#9c27b0"), 2, Qt.SolidLine))
                        painter.setBrush(QColor(156, 39, 176, 60)) # 透明度設定
                        painter.drawRect(QRectF(p_tl, p_br))

                # --- 2. 通常のROI (緑枠) の描画 ---
                if self.click_settings.get('roi_enabled'):
                    roi_mode = self.click_settings.get('roi_mode', 'fixed')
                    roi_rect_data = None
                    if roi_mode == 'fixed':
                        roi_rect_data = self.click_settings.get('roi_rect')
                    elif roi_mode == 'variable':
                        roi_rect_data = self.click_settings.get('roi_rect_variable')
                    
                    if roi_rect_data:
                        # (x1, y1, x2, y2)
                        p1 = to_widget_coords((roi_rect_data[0], roi_rect_data[1]))
                        p2 = to_widget_coords((roi_rect_data[2], roi_rect_data[3]))
                        painter.setPen(QPen(QColor(0, 255, 0), 2))
                        painter.setBrush(QColor(0, 255, 0, 30))
                        painter.drawRect(QRectF(p1, p2))

                # --- 3. クリックポイント (赤点) の描画 ---
                if self.click_settings.get('point_click') and self.click_settings.get('click_position'):
                    p = to_widget_coords(self.click_settings['click_position'])
                    painter.setPen(QPen(QColor(255, 50, 50), 3))
                    painter.setBrush(QColor(255, 50, 50))
                    painter.drawEllipse(p, 4, 4)
                    
                # --- 4. クリック範囲 (青枠) の描画 ---
                elif self.click_settings.get('range_click') and self.click_settings.get('click_rect'):
                    rect = self.click_settings['click_rect']
                    # (x1, y1, x2, y2)
                    p1 = to_widget_coords((rect[0], rect[1]))
                    p2 = to_widget_coords((rect[2], rect[3]))
                    painter.setPen(QPen(QColor(50, 100, 255), 2))
                    painter.setBrush(Qt.NoBrush)
                    painter.drawRect(QRectF(p1, p2))
                    
                # --- 5. ドラッグ中の描画 ---
                if self.is_drawing:
                    p1 = to_widget_coords((self.start_pos.x(), self.start_pos.y()))
                    p2 = to_widget_coords((self.end_pos.x(), self.end_pos.y()))
                    
                    if self.drawing_mode == 'point':
                        painter.setPen(QPen(QColor(255, 50, 50), 3))
                        painter.setBrush(QColor(255, 50, 50))
                        painter.drawEllipse(p2, 4, 4)
                    elif self.drawing_mode == 'range':
                        painter.setPen(QPen(QColor(50, 100, 255), 2))
                        painter.setBrush(Qt.NoBrush)
                        painter.drawRect(QRectF(p1, p2))
                    elif self.drawing_mode == 'roi_variable':
                        painter.setPen(QPen(QColor(0, 255, 0), 2))
                        painter.setBrush(QColor(0, 255, 0, 30))
                        painter.drawRect(QRectF(p1, p2))

                # --- 6. ズームヒントの描画 ---
                self._draw_zoom_hint(painter, display_rect)
            finally:
                painter.end()

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
        # 少し小さめのフォントで重ならないようにする
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

        # 背景
        painter.setBrush(QColor(0, 0, 0, 140))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(bg_rect, 6, 6)

        # アイコン（丸＋十字）
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

        # テキスト
        text_x = icon_x + icon_size + 6
        baseline = bg_rect.y() + padding + (content_h - text_h) / 2 + fm.ascent()
        painter.setPen(QColor(255, 255, 255, 230))
        painter.drawText(text_x, baseline, self.zoom_hint_text)

        painter.restore()
