import cv2
import numpy as np
from PySide6.QtCore import Qt, QRectF, QPoint, Signal
from PySide6.QtGui import QImage, QPixmap, QPainter, QPen, QColor, QWheelEvent
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSpinBox, QPushButton, QDialogButtonBox, QCheckBox


def _bgr_to_qpixmap(img_bgr: np.ndarray) -> QPixmap:
    if img_bgr is None or img_bgr.size == 0:
        return QPixmap()
    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    qimg = QImage(rgb.data, w, h, rgb.strides[0], QImage.Format_RGB888)
    return QPixmap.fromImage(qimg.copy())


class QuickTimerImageLabel(QLabel):
    roi_changed = Signal(tuple)  # (x, y, w, h) in image coords
    click_changed = Signal(tuple)  # (x, y) in image coords

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 200)
        self.setAlignment(Qt.AlignCenter)
        self.setMouseTracking(True)
        self._pixmap = QPixmap()
        self._img_w = 0
        self._img_h = 0

        self._is_dragging = False
        self._drag_start_img = QPoint()
        self._drag_end_img = QPoint()
        self._press_img = QPoint()

        self.roi_rect = None  # (x, y, w, h) in image coords
        self.click_point = None  # (x, y) in image coords

        # --- ズーム（メインUIのInteractivePreviewLabelと同等のモデル） ---
        self.pixmap_display_rect = QRectF()
        self.base_scale_x = 1.0
        self.base_scale_y = 1.0
        self.user_zoom_factor = 1.0  # 0.1 ~ 15.0
        self.effective_scale_x = 1.0
        self.effective_scale_y = 1.0
        self.zoomed_display_rect = QRectF()

    def set_image(self, img_bgr: np.ndarray):
        pm = _bgr_to_qpixmap(img_bgr)
        self._pixmap = pm
        self._img_w = pm.width()
        self._img_h = pm.height()
        # 画像が切り替わったらズームをリセット
        self.user_zoom_factor = 1.0
        self.zoomed_display_rect = QRectF()
        self._update_geometry_cache()
        self.update()

    def set_click_point(self, x: int, y: int):
        self.click_point = (int(x), int(y))
        self.update()

    def set_roi_rect(self, rect):
        self.roi_rect = rect
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_geometry_cache()

    def _update_geometry_cache(self):
        """基本スケールと表示領域を計算（ズーム前の基準）"""
        if self._pixmap.isNull() or self.width() == 0 or self.height() == 0 or self._pixmap.height() == 0:
            self.pixmap_display_rect = QRectF()
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

        if self._pixmap.width() > 0 and self._pixmap.height() > 0:
            self.base_scale_x = self.pixmap_display_rect.width() / self._pixmap.width()
            self.base_scale_y = self.pixmap_display_rect.height() / self._pixmap.height()
        else:
            self.base_scale_x, self.base_scale_y = 1.0, 1.0

        # 実効スケール（ズーム適用）
        self.effective_scale_x = self.base_scale_x * self.user_zoom_factor
        self.effective_scale_y = self.base_scale_y * self.user_zoom_factor

        # ズーム未適用なら基本表示領域に戻す
        if self.user_zoom_factor == 1.0:
            self.zoomed_display_rect = self.pixmap_display_rect
        else:
            # リサイズ時は中心を維持
            if not self.zoomed_display_rect.isEmpty():
                cx = self.zoomed_display_rect.x() + self.zoomed_display_rect.width() / 2
                cy = self.zoomed_display_rect.y() + self.zoomed_display_rect.height() / 2
                nw = self._pixmap.width() * self.effective_scale_x
                nh = self._pixmap.height() * self.effective_scale_y
                self.zoomed_display_rect = QRectF(cx - nw / 2, cy - nh / 2, nw, nh)
            else:
                self.zoomed_display_rect = self.pixmap_display_rect

    def _display_rect(self) -> QRectF:
        """描画に使用する表示領域（ズーム適用時はzoomed_display_rect）"""
        if not self.zoomed_display_rect.isEmpty():
            return self.zoomed_display_rect
        return self.pixmap_display_rect

    def _widget_to_image(self, p: QPoint):
        if self._pixmap.isNull():
            return None
        display_rect = self._display_rect()
        if display_rect.isEmpty():
            return None

        # 表示領域を基準に画像座標へ（ズーム対応）
        img_x = (p.x() - display_rect.x()) / max(1e-6, self.effective_scale_x)
        img_y = (p.y() - display_rect.y()) / max(1e-6, self.effective_scale_y)
        img_x = max(0, min(int(img_x), self._img_w - 1))
        img_y = max(0, min(int(img_y), self._img_h - 1))
        return QPoint(img_x, img_y)

    def wheelEvent(self, event: QWheelEvent):
        """マウスホイールでズーム（カーソル位置を中心に）"""
        if self._pixmap.isNull():
            return
        delta = event.angleDelta().y()
        zoom_delta = (delta / 120.0) * 0.1  # 1ノッチ=0.1
        self._apply_zoom(zoom_delta, event.position().toPoint())
        event.accept()

    def _apply_zoom(self, zoom_delta: float, cursor_widget_pos: QPoint):
        if self._pixmap.isNull():
            return

        # 画像上の点を中心にズームする（画像外でもクランプ）
        _ = self._widget_to_image(cursor_widget_pos)

        old_zoom = self.user_zoom_factor
        self.user_zoom_factor = max(0.1, min(15.0, self.user_zoom_factor + zoom_delta))
        if old_zoom == self.user_zoom_factor:
            return

        # baseは最新の状態で計算しておく
        self._update_geometry_cache()

        # 直前の表示領域（ズーム前）を基準にオフセットを計算
        old_display = self.zoomed_display_rect if not self.zoomed_display_rect.isEmpty() else self.pixmap_display_rect
        if old_display.isEmpty():
            old_display = self.pixmap_display_rect

        zoom_ratio = self.user_zoom_factor / old_zoom if old_zoom > 0 else self.user_zoom_factor

        cur_off_x = cursor_widget_pos.x() - old_display.x()
        cur_off_y = cursor_widget_pos.y() - old_display.y()
        new_off_x = cur_off_x * zoom_ratio
        new_off_y = cur_off_y * zoom_ratio

        new_w = self._pixmap.width() * self.effective_scale_x
        new_h = self._pixmap.height() * self.effective_scale_y
        new_x = cursor_widget_pos.x() - new_off_x
        new_y = cursor_widget_pos.y() - new_off_y

        self.zoomed_display_rect = QRectF(new_x, new_y, new_w, new_h)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            ip = self._widget_to_image(event.pos())
            if ip:
                self._is_dragging = True
                self._drag_start_img = ip
                self._drag_end_img = ip
                self._press_img = ip
                self.update()

    def mouseMoveEvent(self, event):
        if self._is_dragging:
            ip = self._widget_to_image(event.pos())
            if ip:
                self._drag_end_img = ip
                self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._is_dragging:
            self._is_dragging = False
            # ほぼクリック（ドラッグ距離が小さい）ならクリック位置の調整として扱う
            dx = abs(self._drag_end_img.x() - self._press_img.x())
            dy = abs(self._drag_end_img.y() - self._press_img.y())
            if dx + dy <= 4:
                self.click_point = (int(self._drag_end_img.x()), int(self._drag_end_img.y()))
                self.click_changed.emit(self.click_point)
            else:
                x1 = min(self._drag_start_img.x(), self._drag_end_img.x())
                y1 = min(self._drag_start_img.y(), self._drag_end_img.y())
                x2 = max(self._drag_start_img.x(), self._drag_end_img.x())
                y2 = max(self._drag_start_img.y(), self._drag_end_img.y())
                w = max(1, x2 - x1)
                h = max(1, y2 - y1)
                self.roi_rect = (x1, y1, w, h)
                self.roi_changed.emit(self.roi_rect)
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#263238"))
        if self._pixmap.isNull():
            painter.setPen(QColor("#bdbdbd"))
            painter.drawText(self.rect(), Qt.AlignCenter, "No Image")
            return

        display_rect = self._display_rect()
        if display_rect.isEmpty():
            # 念のためのフォールバック
            self._update_geometry_cache()
            display_rect = self._display_rect()

        painter.drawPixmap(display_rect.toRect(), self._pixmap)

        def to_widget(img_x: int, img_y: int):
            return (
                display_rect.x() + img_x * self.effective_scale_x,
                display_rect.y() + img_y * self.effective_scale_y,
            )

        painter.setRenderHint(QPainter.Antialiasing)

        # ROI（確定）: 緑枠 + 半透明塗りつぶし（メインUIのクリック設定と合わせる）
        if self.roi_rect:
            rx, ry, rw, rh = self.roi_rect
            sx, sy = to_widget(rx, ry)
            ex, ey = to_widget(rx + rw, ry + rh)
            roi_rect = QRectF(sx, sy, ex - sx, ey - sy).normalized()
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 255, 0, 30))
            painter.drawRect(roi_rect)
            painter.setPen(QPen(QColor(0, 255, 0), 2, Qt.SolidLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(roi_rect)

        # ROI（ドラッグ中のプレビュー）: 緑枠 + 半透明塗りつぶし（動的表示）
        if self._is_dragging:
            x1 = min(self._drag_start_img.x(), self._drag_end_img.x())
            y1 = min(self._drag_start_img.y(), self._drag_end_img.y())
            x2 = max(self._drag_start_img.x(), self._drag_end_img.x())
            y2 = max(self._drag_start_img.y(), self._drag_end_img.y())
            sx, sy = to_widget(x1, y1)
            ex, ey = to_widget(x2, y2)
            drag_rect = QRectF(sx, sy, ex - sx, ey - sy).normalized()
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 255, 0, 30))
            painter.drawRect(drag_rect)
            painter.setPen(QPen(QColor(0, 255, 0), 2, Qt.SolidLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(drag_rect)

        # クリック点（赤丸: 塗りつぶし）
        if self.click_point:
            cx, cy = self.click_point
            px, py = to_widget(cx, cy)
            pen = QPen(QColor("#ef5350"))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.setBrush(QColor(239, 83, 80, 200))
            painter.drawEllipse(int(px) - 6, int(py) - 6, 12, 12)


class QuickTimerCreateDialog(QDialog):
    """
    認識範囲フレーム上で、ROI(最小50x50)とN分後を指定してクイックタイマー予約を作成する。
    """
    def __init__(self, img_bgr: np.ndarray, click_point_xy: tuple, locale_manager=None, right_click: bool = False, parent=None):
        super().__init__(parent)
        self.locale_manager = locale_manager
        self._img_bgr = img_bgr
        self._click_x, self._click_y = int(click_point_xy[0]), int(click_point_xy[1])

        self._roi_rect = None
        self._minutes = 1
        self._right_click = bool(right_click)

        self.setWindowTitle(self.tr("quick_timer_dialog_title"))
        # タイトルバーの×を消す（OK/Cancelで確実に抜ける）
        flags = self.windowFlags() | Qt.CustomizeWindowHint
        flags &= ~Qt.WindowMinMaxButtonsHint
        flags &= ~Qt.WindowCloseButtonHint
        self.setWindowFlags(flags)
        self.resize(920, 640)

        layout = QVBoxLayout(self)

        hint = QLabel(self.tr("quick_timer_dialog_hint"))
        hint.setStyleSheet("color: #37474f; font-weight: bold;")
        layout.addWidget(hint)

        self.image_label = QuickTimerImageLabel()
        self.image_label.set_image(img_bgr)
        self.image_label.set_click_point(self._click_x, self._click_y)
        self.image_label.roi_changed.connect(self._on_roi_changed)
        self.image_label.click_changed.connect(self._on_click_changed)
        layout.addWidget(self.image_label, 1)

        controls = QHBoxLayout()
        lbl_min = QLabel(self.tr("quick_timer_minutes_label"))
        controls.addWidget(lbl_min)

        self.spin_minutes = QSpinBox()
        self.spin_minutes.setRange(1, 999)
        self.spin_minutes.setValue(1)
        controls.addWidget(self.spin_minutes)

        self.chk_right_click = QCheckBox(self.tr("item_setting_right_click"))
        self.chk_right_click.setChecked(self._right_click)
        self.chk_right_click.setToolTip(self.tr("item_setting_right_click_tooltip"))
        controls.addWidget(self.chk_right_click)

        controls.addStretch()
        layout.addLayout(controls)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # 初期ROI: 50x50以上をクリック点中心に
        self._set_default_roi()

    def tr(self, key: str, *args):
        if self.locale_manager:
            return self.locale_manager.tr(key, *args)
        return key

    def _set_default_roi(self):
        if self._img_bgr is None or self._img_bgr.size == 0:
            return
        h, w = self._img_bgr.shape[:2]
        size = 120
        x1 = max(0, min(w - size, self._click_x - size // 2))
        y1 = max(0, min(h - size, self._click_y - size // 2))
        self._roi_rect = (int(x1), int(y1), int(size), int(size))
        self.image_label.set_roi_rect(self._roi_rect)

    def _on_roi_changed(self, rect):
        self._roi_rect = rect

    def _on_click_changed(self, pt):
        try:
            self._click_x, self._click_y = int(pt[0]), int(pt[1])
            self.image_label.set_click_point(self._click_x, self._click_y)
        except Exception:
            pass

    def _on_accept(self):
        self._minutes = int(self.spin_minutes.value())
        if not self._roi_rect:
            return
        rx, ry, rw, rh = self._roi_rect
        # 最小50x50
        if rw < 50 or rh < 50:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, self.tr("quick_timer_dialog_title"), self.tr("quick_timer_err_roi_small"))
            return
        # クリック点がROI内
        if not (rx <= self._click_x < rx + rw and ry <= self._click_y < ry + rh):
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, self.tr("quick_timer_dialog_title"), self.tr("quick_timer_err_click_outside_roi"))
            return
        self.accept()

    def get_result(self):
        """
        Returns: (minutes:int, roi_rect:(x,y,w,h), click_point:(x,y), right_click:bool)
        """
        return self._minutes, self._roi_rect, (self._click_x, self._click_y), bool(self.chk_right_click.isChecked())


