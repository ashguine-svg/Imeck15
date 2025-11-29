# selection.py

import sys
from PySide6.QtCore import Qt, Signal, QRect, QPoint
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
        
        screen = QApplication.primaryScreen()
        screen_geo = screen.geometry()
        self.setGeometry(screen_geo)
        
        self.setMouseTracking(True)
        self.start_pos = None
        self.end_pos = None
        self.initial_rect = initial_rect
        
        self.dpr = screen.devicePixelRatio() if screen else 1.0

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.initial_rect = None
            self.start_pos = event.globalPosition().toPoint()
            self.end_pos = self.start_pos
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self.start_pos is not None:
            self.end_pos = event.globalPosition().toPoint()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton and self.start_pos is not None:
            end_pos_global = event.globalPosition().toPoint()
            
            x_min = min(self.start_pos.x(), end_pos_global.x())
            y_min = min(self.start_pos.y(), end_pos_global.y())
            x_max = max(self.start_pos.x(), end_pos_global.x())
            y_max = max(self.start_pos.y(), end_pos_global.y())

            x1 = int(x_min * self.dpr)
            y1 = int(y_min * self.dpr)
            x2 = int(x_max * self.dpr)
            y2 = int(y_max * self.dpr)
            
            rect_tuple = (x1, y1, x2 + 1, y2 + 1)

            if (x2 - x1) > 1 and (y2 - y1) > 1:
                self.selectionComplete.emit(rect_tuple)
            
            self.close()
            self.deleteLater()

    def paintEvent(self, event):
        painter = QPainter()
        if painter.begin(self):
            try:
                outer_path = QPainterPath()
                outer_path.addRect(self.rect()) 
                
                inner_path = QPainterPath()
                current_rect = None

                if self.start_pos and self.end_pos:
                    local_start = self.mapFromGlobal(self.start_pos)
                    local_end = self.mapFromGlobal(self.end_pos)
                    current_rect = QRect(local_start, local_end).normalized()
                    
                elif self.initial_rect:
                    current_rect = self.initial_rect

                if current_rect:
                    inner_path.addRect(current_rect)
                    painter.setPen(QPen(QColor(0, 255, 255), 2))
                    painter.setBrush(Qt.NoBrush)
                    painter.drawRect(current_rect)

                final_path = outer_path.subtracted(inner_path)
                painter.fillPath(final_path, QBrush(QColor(0, 0, 0, 100)))
            finally:
                painter.end()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.initial_rect:
            x1 = int(self.initial_rect.left() * self.dpr)
            y1 = int(self.initial_rect.top() * self.dpr)
            x2 = int(self.initial_rect.right() * self.dpr)
            y2 = int(self.initial_rect.bottom() * self.dpr)
            
            coords = (x1, y1, x2 + 1, y2 + 1)
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
