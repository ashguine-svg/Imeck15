# monitor.py

import sys
import time
import psutil
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QSizePolicy, QSpacerItem, QApplication 
)
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtCore import Qt, Signal, QTimer, QPoint 

class PerformanceMonitor(QDialog):
    """
    リアルタイムのパフォーマンス情報を表示する独立したウィンドウ。
    (現在はログビューアとして機能)
    """

    def __init__(self, ui_manager, locale_manager, parent=None):
        super().__init__(parent)
        self.ui_manager = ui_manager
        self.locale_manager = locale_manager 

        flags = Qt.Window | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
        
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground) 
        self.setWindowOpacity(0.85) 

        self.resize(1024, 200) 
        self.setMinimumSize(200, 40) 

        self.process = psutil.Process()
        self.process.cpu_percent(interval=None)
        
        self.last_cpu_percent = 0.0

        self.setup_ui() 

        self.start_time = time.time()
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.update_performance_info)
        self.update_timer.start(1000)

    def setup_ui(self):
        lm = self.locale_manager.tr

        self.setWindowTitle(lm("monitor_window_title"))

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        self.log_text_edit = QTextEdit()
        self.log_text_edit.setReadOnly(True)
        
        self.log_text_edit.setStyleSheet(
            "background-color: rgba(0, 0, 0, 100); color: white; border: none;"
        )
        
        main_layout.addWidget(self.log_text_edit)

    def connect_signals(self):
        pass 

    def on_language_changed(self):
        lm = self.locale_manager.tr
        self.setWindowTitle(lm("monitor_window_title"))

    def update_performance_info(self):
        try:
            self.last_cpu_percent = self.process.cpu_percent(interval=None) 
            self.process.cpu_percent() 
            
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            self.update_timer.stop()
        except Exception as e:
            if hasattr(self, 'ui_manager') and self.ui_manager and self.ui_manager.logger:
                self.ui_manager.logger.log("monitor_log_error", str(e))

    def update_log(self, message):
        self.log_text_edit.append(message)
        scrollbar = self.log_text_edit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def closeEvent(self, event):
        event.ignore()
        self.hide()

    def paintEvent(self, event):
        painter = QPainter()
        if painter.begin(self):
            try:
                painter.setRenderHint(QPainter.Antialiasing)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(50, 50, 50, 200)) 
                painter.drawRoundedRect(self.rect(), 10.0, 10.0)
            finally:
                painter.end()

    def get_last_cpu(self):
        return self.last_cpu_percent
