# selection.py
# ★★★ 修正: DPIスケーリング対応 (Global座標 * DPR = 物理座標) ★★★

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
        
        # プライマリスクリーンのジオメトリを取得して設定
        screen = QApplication.primaryScreen()
        screen_geo = screen.geometry()
        self.setGeometry(screen_geo)
        
        self.setMouseTracking(True)
        self.start_pos = None
        self.end_pos = None
        self.initial_rect = initial_rect
        
        # ★ 追加: デバイスピクセル比 (DPR) を取得
        # Linux等でスケーリング(125%, 150%など)が有効な場合の物理座標計算に使用
        self.dpr = screen.devicePixelRatio() if screen else 1.0

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.initial_rect = None
            # 画面全体の絶対座標(Global)を取得
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
            
            # --- ★★★ 座標計算の修正箇所 ★★★ ---
            # 1. 座標の正規化 (左上と右下を判定)
            x_min = min(self.start_pos.x(), end_pos_global.x())
            y_min = min(self.start_pos.y(), end_pos_global.y())
            x_max = max(self.start_pos.x(), end_pos_global.x())
            y_max = max(self.start_pos.y(), end_pos_global.y())

            # 2. DPIスケーリングを適用して物理ピクセルに変換
            # (Qtの論理座標 -> MSSの物理座標)
            x1 = int(x_min * self.dpr)
            y1 = int(y_min * self.dpr)
            x2 = int(x_max * self.dpr)
            y2 = int(y_max * self.dpr)
            
            # 幅と高さの計算 (+1ピクセル補正はMSS等の仕様に合わせて調整)
            # x2, y2 は右下の座標として渡すため、そのままでOK (MSSは region={'top': y, 'left': x, 'width': w, 'height': h} 形式だが、
            # ここでは tuple (x1, y1, x2, y2) を返し、受け取り側で width/height を計算する想定)
            
            # 念のため width/height が正になるように計算
            # core.py 側では (x1, y1, x2, y2) を受け取っている
            rect_tuple = (x1, y1, x2 + 1, y2 + 1)

            # 極小サイズの誤操作防止
            if (x2 - x1) > 1 and (y2 - y1) > 1:
                self.selectionComplete.emit(rect_tuple)
            
            self.close()
            self.deleteLater()

    def paintEvent(self, event):
        painter = QPainter(self)
        outer_path = QPainterPath()
        outer_path.addRect(self.rect()) # ウィンドウ全体の矩形
        
        inner_path = QPainterPath()
        current_rect = None

        if self.start_pos and self.end_pos:
            # 描画は論理座標(Local)で行う必要があるため変換
            local_start = self.mapFromGlobal(self.start_pos)
            local_end = self.mapFromGlobal(self.end_pos)
            current_rect = QRect(local_start, local_end).normalized()
            
        elif self.initial_rect:
            # initial_rectがある場合の処理 (Local座標と仮定)
            current_rect = self.initial_rect

        if current_rect:
            inner_path.addRect(current_rect)
            painter.setPen(QPen(QColor(0, 255, 255), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(current_rect)

        # 選択範囲外を半透明の黒で塗りつぶす
        final_path = outer_path.subtracted(inner_path)
        painter.fillPath(final_path, QBrush(QColor(0, 0, 0, 100)))

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.initial_rect:
            # 初期矩形がある場合のエンター確定 (DPI適用)
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
