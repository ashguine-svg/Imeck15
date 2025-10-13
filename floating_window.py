# floating_window.py

from PySide6.QtWidgets import (
    QDialog, QPushButton, QHBoxLayout, QLabel, QSpacerItem, QSizePolicy, QApplication
)
from PySide6.QtGui import QPainter, QColor, QFontMetrics
from PySide6.QtCore import Qt, Signal, QPoint

class FloatingWindow(QDialog):
    """
    最小UIモードで表示されるフローティングウィンドウ。
    """
    startMonitoringRequested = Signal()
    stopMonitoringRequested = Signal()
    captureImageRequested = Signal()
    toggleMainUIRequested = Signal()
    closeRequested = Signal()
    setRecAreaRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Imeck15 Minimal UI")
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowOpacity(0.85)

        self.offset = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        self.start_button = QPushButton("▶")
        self.stop_button = QPushButton("■")
        self.capture_button = QPushButton("●")
        self.set_rec_area_button = QPushButton("⌚")
        self.toggle_ui_button = QPushButton("⇔")
        self.close_button = QPushButton("×")
        
        for btn in [self.start_button, self.stop_button, self.capture_button, self.set_rec_area_button, self.toggle_ui_button, self.close_button]:
            btn.setFixedSize(24, 24)
            font = btn.font()
            font.setPointSize(10)
            btn.setFont(font)
            btn.setStyleSheet("QPushButton { border-radius: 12px; background-color: rgba(200, 200, 200, 150); color: black; } QPushButton:hover { background-color: rgba(220, 220, 220, 200); }")
        
        self.close_button.setStyleSheet("QPushButton { border-radius: 12px; background-color: rgba(231, 76, 60, 180); color: white; font-weight: bold; } QPushButton:hover { background-color: rgba(231, 76, 60, 230); }")

        self.perf_label = QLabel("---% ---fps")
        font = self.perf_label.font()
        font.setBold(True)
        self.perf_label.setFont(font)
        self.perf_label.setStyleSheet("color: #FFA500; background-color: transparent;")

        # ★★★ 追加: ラベルの横幅を固定してUIの伸縮を防ぐ ★★★
        font_metrics = QFontMetrics(self.perf_label.font())
        # "100% 99fps" のような最も長い可能性のある文字列の幅を計算
        max_width = font_metrics.horizontalAdvance("100% 99fps") + 5 # 少し余白を追加
        self.perf_label.setFixedWidth(max_width)
        self.perf_label.setAlignment(Qt.AlignCenter) # 中央揃えにすると見栄えが良い

        self.status_label = QLabel("待機中")
        font = self.status_label.font()
        font.setBold(True)
        self.status_label.setFont(font)
        self.status_label.setStyleSheet("color: #90EE90; background-color: transparent;")

        layout.addWidget(self.start_button)
        layout.addWidget(self.stop_button)
        layout.addWidget(self.capture_button)
        layout.addWidget(self.set_rec_area_button)
        layout.addWidget(self.toggle_ui_button)
        layout.addWidget(self.perf_label)
        layout.addWidget(self.status_label)
        layout.addSpacerItem(QSpacerItem(10, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        layout.addWidget(self.close_button)

        self.start_button.setToolTip("監視開始")
        self.stop_button.setToolTip("監視停止")
        self.capture_button.setToolTip("画像キャプチャ")
        self.set_rec_area_button.setToolTip("認識範囲を設定")
        self.toggle_ui_button.setToolTip("メインUIを表示/非表示")
        self.close_button.setToolTip("最小UIモードを終了")

        self.start_button.clicked.connect(self.startMonitoringRequested)
        self.stop_button.clicked.connect(self.stopMonitoringRequested)
        self.capture_button.clicked.connect(self.captureImageRequested)
        self.toggle_ui_button.clicked.connect(self.toggleMainUIRequested)
        self.close_button.clicked.connect(self.closeRequested)
        self.set_rec_area_button.clicked.connect(self.setRecAreaRequested)
    
    def update_performance(self, cpu, fps):
        self.perf_label.setText(f"{cpu:.0f}% {fps:.0f}fps")

    def update_status(self, text, color="green"):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; background-color: transparent;")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(50, 50, 50, 200))
        painter.drawRoundedRect(self.rect(), 15.0, 15.0)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self.close_button.underMouse():
                return
            self.offset = event.globalPosition().toPoint() - self.pos()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.offset is not None and event.buttons() == Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self.offset)
            event.accept()

    def mouseReleaseEvent(self, event):
        self.offset = None
        event.accept()
        
        screen_rect = QApplication.primaryScreen().availableGeometry()
        pos = self.pos()
        snap_margin = 5
        
        new_pos = QPoint(pos.x(), pos.y())
        moved = False

        if pos.x() <= screen_rect.left() + snap_margin:
            new_pos.setX(screen_rect.left())
            moved = True
        if pos.x() + self.width() >= screen_rect.right() - snap_margin:
            new_pos.setX(screen_rect.right() - self.width())
            moved = True
        if pos.y() <= screen_rect.top() + snap_margin:
            new_pos.setY(screen_rect.top())
            moved = True
        if pos.y() + self.height() >= screen_rect.bottom() - snap_margin:
            new_pos.setY(screen_rect.bottom() - self.height())
            moved = True
            
        if moved:
            self.move(new_pos)
