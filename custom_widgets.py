# custom_widgets.py

from PySide6.QtWidgets import QLabel, QSizePolicy
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QPixmap
from PySide6.QtCore import Qt, Signal, QPoint, QRect, QRectF, QPointF

class ScaledPixmapLabel(QLabel):
    """
    アスペクト比を維持して画像を表示するラベル。
    余白は黒で塗りつぶされます。
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap()
        # レイアウトに合わせて伸縮するように設定
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(1, 1)
        self.setAlignment(Qt.AlignCenter)

    def set_pixmap(self, pixmap):
        self._pixmap = pixmap if pixmap and not pixmap.isNull() else QPixmap()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        # 背景を黒で塗りつぶす
        painter.fillRect(self.rect(), Qt.black)
        
        if self._pixmap.isNull():
            return

        # 描画領域の計算 (アスペクト比維持)
        label_size = self.size()
        scaled_pixmap = self._pixmap.scaled(label_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        
        x = (label_size.width() - scaled_pixmap.width()) // 2
        y = (label_size.height() - scaled_pixmap.height()) // 2
        
        painter.drawPixmap(x, y, scaled_pixmap)


class InteractivePreviewLabel(QLabel):
    """
    マウス操作可能なプレビューラベル。
    座標変換を行い、クリック位置やROI矩形を描画します。
    """
    settingChanged = Signal(dict)
    roiSettingChanged = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        # レイアウトに合わせて伸縮
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
        
        self.pixmap_display_rect = QRectF()
        self.scale_x = 1.0
        self.scale_y = 1.0

    def set_pixmap(self, pixmap):
        self._pixmap = pixmap if pixmap and not pixmap.isNull() else QPixmap()
        self._update_geometry_cache()
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
        """
        ウィジェットサイズと画像サイズから、
        画像の表示位置(pixmap_display_rect)とスケール比率を計算してキャッシュします。
        """
        if self._pixmap.isNull() or self.width() == 0 or self.height() == 0 or self._pixmap.height() == 0:
            self.pixmap_display_rect = QRectF()
            self.scale_x, self.scale_y = 1.0, 1.0
            return

        pixmap_ratio = self._pixmap.width() / self._pixmap.height()
        label_ratio = self.width() / self.height()

        if pixmap_ratio > label_ratio:
            # 横幅基準
            width = self.width()
            height = self.width() / pixmap_ratio
            x = 0
            y = (self.height() - height) / 2
        else:
            # 高さ基準
            height = self.height()
            width = self.height() * pixmap_ratio
            x = (self.width() - width) / 2
            y = 0
        
        self.pixmap_display_rect = QRectF(x, y, width, height)

        if self._pixmap.width() > 0 and self._pixmap.height() > 0:
            self.scale_x = self.pixmap_display_rect.width() / self._pixmap.width()
            self.scale_y = self.pixmap_display_rect.height() / self._pixmap.height()
        else:
            self.scale_x, self.scale_y = 1.0, 1.0

    def _map_widget_to_image_coords(self, widget_pos):
        if self._pixmap.isNull() or not self.pixmap_display_rect.contains(widget_pos):
            return None
        
        relative_x = (widget_pos.x() - self.pixmap_display_rect.x()) / self.pixmap_display_rect.width()
        relative_y = (widget_pos.y() - self.pixmap_display_rect.y()) / self.pixmap_display_rect.height()
        
        img_x = relative_x * self._pixmap.width()
        img_y = relative_y * self._pixmap.height()
        return QPoint(int(img_x), int(img_y))

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

    def paintEvent(self, event):
        painter = QPainter(self)
        # 背景黒塗り
        painter.fillRect(self.rect(), Qt.black)

        if self._pixmap.isNull():
            return
            
        # 画像描画
        painter.drawPixmap(self.pixmap_display_rect.toRect(), self._pixmap)
        
        if self._pixmap.width() == 0 or self._pixmap.height() == 0:
            return
            
        def to_widget_coords(img_pos):
            x = self.pixmap_display_rect.x() + img_pos[0] * self.scale_x
            y = self.pixmap_display_rect.y() + img_pos[1] * self.scale_y
            return QPointF(x, y)
        
        painter.setRenderHint(QPainter.Antialiasing)

        # --- 描画ロジック ---
        
        # 1. 保存済みのROIを描画
        if self.click_settings.get('roi_enabled'):
            roi_mode = self.click_settings.get('roi_mode', 'fixed')
            roi_rect_data = None
            if roi_mode == 'fixed':
                roi_rect_data = self.click_settings.get('roi_rect')
            elif roi_mode == 'variable':
                roi_rect_data = self.click_settings.get('roi_rect_variable')
            
            if roi_rect_data:
                p1 = to_widget_coords((roi_rect_data[0], roi_rect_data[1]))
                p2 = to_widget_coords((roi_rect_data[2], roi_rect_data[3]))
                # ROIは視認性の高い緑、少し太めに
                painter.setPen(QPen(QColor(0, 255, 0), 2))
                painter.setBrush(QColor(0, 255, 0, 30))
                painter.drawRect(QRectF(p1, p2))

        # 2. 保存済みのクリック設定を描画
        if self.click_settings.get('point_click') and self.click_settings.get('click_position'):
            p = to_widget_coords(self.click_settings['click_position'])
            # クリック点は赤、太め
            painter.setPen(QPen(QColor(255, 50, 50), 3))
            painter.setBrush(QColor(255, 50, 50))
            painter.drawEllipse(p, 4, 4)
            
        elif self.click_settings.get('range_click') and self.click_settings.get('click_rect'):
            rect = self.click_settings['click_rect']
            p1 = to_widget_coords((rect[0], rect[1]))
            p2 = to_widget_coords((rect[2], rect[3]))
            # 範囲クリックは青
            painter.setPen(QPen(QColor(50, 100, 255), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(QRectF(p1, p2))
            
        # 3. ユーザーが現在ドラッグ中の図形を描画
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
