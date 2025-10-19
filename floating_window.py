# floating_window.py

from PySide6.QtWidgets import (
    QDialog, QPushButton, QHBoxLayout, QLabel, QSpacerItem, QSizePolicy, QApplication, QStyle
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

    # ★★★ 1. __init__ に locale_manager を追加 ★★★
    def __init__(self, locale_manager, parent=None):
        super().__init__(parent)
        self.locale_manager = locale_manager
        
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowOpacity(0.85)
        
        self.setAttribute(Qt.WA_AlwaysShowToolTips, True)

        self.offset = None

        title_bar_height = self.style().pixelMetric(QStyle.PM_TitleBarHeight)
        self.setFixedHeight(title_bar_height)
        
        layout = QHBoxLayout(self)
        margin = 2
        layout.setContentsMargins(margin * 2, margin, margin * 2, margin)
        layout.setSpacing(4)

        # ★★★ 2. ボタンのテキストを翻訳キーで設定 ★★★
        lm = self.locale_manager.tr
        self.setWindowTitle(lm("float_window_title"))
        
        self.start_button = QPushButton(lm("float_button_start"))
        self.stop_button = QPushButton(lm("float_button_stop"))
        self.capture_button = QPushButton(lm("float_button_capture"))
        self.set_rec_area_button = QPushButton(lm("float_button_rec_area"))
        self.toggle_ui_button = QPushButton(lm("float_button_toggle_ui"))
        self.close_button = QPushButton(lm("float_button_close"))
        
        button_height = title_bar_height - (margin * 2)
        
        for btn in [self.start_button, self.stop_button, self.capture_button, self.set_rec_area_button, self.toggle_ui_button, self.close_button]:
            btn.setFixedSize(button_height, button_height)
            font = btn.font()
            font.setPointSize(int(button_height * 0.45))
            btn.setFont(font)
            btn.setStyleSheet(f"QPushButton {{ border-radius: {int(button_height / 2)}px; background-color: rgba(200, 200, 200, 150); color: black; }} QPushButton:hover {{ background-color: rgba(220, 220, 220, 200); }}")
        
        self.close_button.setStyleSheet(f"QPushButton {{ border-radius: {int(button_height / 2)}px; background-color: rgba(231, 76, 60, 180); color: white; font-weight: bold; }} QPushButton:hover {{ background-color: rgba(231, 76, 60, 230); }}")

        # ★★★ 3. ラベルのテキストを翻訳キーで設定 ★★★
        self.perf_label = QLabel(lm("float_label_perf_default"))
        font = self.perf_label.font()
        font.setBold(True)
        self.perf_label.setFont(font)
        self.perf_label.setStyleSheet("color: #FFA500; background-color: transparent;")

        font_metrics = QFontMetrics(self.perf_label.font())
        max_width = font_metrics.horizontalAdvance("100% 99fps") + 5
        self.perf_label.setFixedWidth(max_width)
        self.perf_label.setAlignment(Qt.AlignCenter)

        self.status_label = QLabel(lm("float_label_status_default"))
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

        # ★★★ 4. ツールチップを翻訳キーで設定 ★★★
        self.start_button.setToolTip(lm("float_tooltip_start"))
        self.stop_button.setToolTip(lm("float_tooltip_stop"))
        self.capture_button.setToolTip(lm("float_tooltip_capture"))
        self.set_rec_area_button.setToolTip(lm("float_tooltip_rec_area"))
        self.toggle_ui_button.setToolTip(lm("float_tooltip_toggle_ui"))
        self.close_button.setToolTip(lm("float_tooltip_close"))

        self.start_button.clicked.connect(self.startMonitoringRequested)
        self.stop_button.clicked.connect(self.stopMonitoringRequested)
        self.capture_button.clicked.connect(self.captureImageRequested)
        self.toggle_ui_button.clicked.connect(self.toggleMainUIRequested)
        self.close_button.clicked.connect(self.closeRequested)
        self.set_rec_area_button.clicked.connect(self.setRecAreaRequested)
    
    def update_performance(self, cpu, fps):
        # ★★★ ここを修正 ★★★
        # perf_text = self.locale_manager.tr("float_perf_format", cpu=cpu, fps=fps) # ← 修正前
        
        # 修正後: tr() でフォーマット文字列を取得し、.format() を使う
        format_string = self.locale_manager.tr("float_perf_format") 
        try:
            perf_text = format_string.format(cpu=cpu, fps=fps)
        except KeyError: # .format() のキーが翻訳ファイルと合わない場合のエラー処理
            print(f"[WARN] floating_window: フォーマットキーが 'float_perf_format' ('{format_string}') と一致しません。")
            # フォールバック表示
            perf_text = f"{cpu:.0f}% {fps:.0f}fps"
            
        self.perf_label.setText(perf_text)

    def update_status(self, text, color="green"):
        # ★★★ 6. テキストは翻訳済みのものが渡される想定 (ui.py/core.py から) ★★★
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; background-color: transparent;")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(50, 50, 50, 200))
        radius = self.height() / 2.0
        painter.drawRoundedRect(self.rect(), radius, radius)

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
        snap_margin = 10
        
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
