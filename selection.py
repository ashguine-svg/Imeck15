# selection.py

import sys
from PySide6.QtCore import Qt, Signal, QRect
from PySide6.QtGui import QMouseEvent, QPainter, QPen, QColor, QBrush, QPainterPath, QKeyEvent
from PySide6.QtWidgets import QWidget, QApplication
from pynput import mouse

class SelectionOverlay(QWidget):
    selectionComplete = Signal(tuple)
    selectionCancelled = Signal()

    def __init__(self, parent=None, initial_rect=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        self.setGeometry(QApplication.primaryScreen().geometry())
        self.setMouseTracking(True)
        self.start_pos, self.end_pos, self.initial_rect = None, None, initial_rect
        
        # --- ▼▼▼ 修正箇所 1/4 (DPI取得を削除) ▼▼▼ ---
        # self.dpr = self.screen().devicePixelRatio() if self.screen() else 1.0 # ← この行を削除
        self.dpr = 1.0 # ← 常に 1.0 を設定
        # --- ▲▲▲ 修正完了 ▲▲▲ ---

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.initial_rect = None
            
            # --- ▼▼▼ 修正箇所 2/4 (DPI乗算を削除) ▼▼▼ ---
            # self.start_pos = event.position().toPoint() * self.dpr # ← 修正前
            self.start_pos = event.position().toPoint() # ← 修正後 (物理ピクセルを取得)
            # --- ▲▲▲ 修正完了 ▲▲▲ ---
            
            self.end_pos = self.start_pos
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self.start_pos is not None:
            
            # --- ▼▼▼ 修正箇所 3/4 (DPI乗算を削除) ▼▼▼ ---
            # self.end_pos = event.position().toPoint() * self.dpr # ← 修正前
            self.end_pos = event.position().toPoint() # ← 修正後 (物理ピクセルを取得)
            # --- ▲▲▲ 修正完了 ▲▲▲ ---
            
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton and self.start_pos is not None:
            
            # --- ▼▼▼ 修正箇所 4/4 (DPI乗算を削除) ▼▼▼ ---
            # end_pos_scaled = event.position().toPoint() * self.dpr # ← 修正前
            end_pos_scaled = event.position().toPoint() # ← 修正後 (物理ピクセルを取得)
            # --- ▲▲▲ 修正完了 ▲▲▲ ---
            
            x1 = min(self.start_pos.x(), end_pos_scaled.x())
            y1 = min(self.start_pos.y(), end_pos_scaled.y())
            x2 = max(self.start_pos.x(), end_pos_scaled.x())
            y2 = max(self.start_pos.y(), end_pos_scaled.y())

            rect_tuple = (int(x1), int(y1), int(x2) + 1, int(y2) + 1)

            if rect_tuple[2] - rect_tuple[0] > 1 and rect_tuple[3] - rect_tuple[1] > 1:
                self.selectionComplete.emit(rect_tuple)
            self.close()
            self.deleteLater()

    def paintEvent(self, event):
        painter = QPainter(self)
        outer_path, inner_path = QPainterPath(), QPainterPath()
        outer_path.addRect(self.rect())
        current_rect = None

        if self.start_pos and self.end_pos:
            # (DPIが 1.0 になったため、self.dprでの除算は不要)
            start_pos_logical = self.start_pos / self.dpr
            end_pos_logical = self.end_pos / self.dpr
            current_rect = QRect(start_pos_logical, end_pos_logical).normalized()
        elif self.initial_rect:
            current_rect = self.initial_rect

        if current_rect:
            inner_path.addRect(current_rect)
            painter.setPen(QPen(QColor(0, 255, 255), 2))
            painter.drawRect(current_rect)

        final_path = outer_path.subtracted(inner_path)
        painter.fillPath(final_path, QBrush(QColor(0, 0, 0, 100)))

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.initial_rect:
            # (DPIが 1.0 になったため、self.dprでの乗算は不要)
            x1 = self.initial_rect.left() # * self.dpr
            y1 = self.initial_rect.top() # * self.dpr
            x2 = self.initial_rect.right() # * self.dpr
            y2 = self.initial_rect.bottom() # * self.dpr
            coords = (int(x1), int(y1), int(x2) + 1, int(y2) + 1)
            self.selectionComplete.emit(coords)
            self.close()
            self.deleteLater()
        elif event.key() == Qt.Key_Escape:
            self.selectionCancelled.emit()
            self.close()
            self.deleteLater()


class WindowSelectionListener(mouse.Listener):
    def __init__(self, callback):
        super().__init__(on_click=self.on_click)
        self.callback = callback
    def on_click(self, x, y, button, pressed):
        if pressed and button == mouse.Button.left: self.callback(x, y); return False
