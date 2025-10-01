# monitor.py

import sys
import time
import psutil
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QSizePolicy, QSpacerItem
)
from PySide6.QtCore import Qt, Signal, QTimer

class PerformanceMonitor(QDialog):
    """
    リアルタイムのパフォーマンス情報を表示する独立したウィンドウ。
    """
    
    toggleMonitoringRequested = Signal()
    
    def __init__(self, ui_manager, parent=None):
        super().__init__(parent)
        self.ui_manager = ui_manager
        self.setWindowTitle("パフォーマンスモニター")
        
        # ★★★ 変更点: Qt.Windowフラグを追加して、ウィンドウを完全に独立させる ★★★
        flags = Qt.Window | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
        self.setWindowFlags(flags)
        
        self.resize(1024, 200)
        # ★★★ 変更点: ご依頼の通り、最小の高さを100に設定 ★★★
        self.setMinimumSize(600, 100)
        
        self.process = psutil.Process()
        self.process.cpu_percent(interval=None)
        self.current_fps = 0.0
        
        self.setup_ui()
        self.start_time = time.time()
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.update_performance_info)
        self.update_timer.start(1000)

    def setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        top_layout = QHBoxLayout()
        top_layout.setContentsMargins(0, 0, 0, 0)

        self.monitor_button = QPushButton("監視開始/停止")
        # ★★★ 変更点: メインUIと同じツールチップを追加 ★★★
        self.monitor_button.setToolTip("右クリックで監視停止、右ダブルクリックで監視開始")
        top_layout.addWidget(self.monitor_button)

        self.rec_area_button = QPushButton("認識範囲設定")
        top_layout.addWidget(self.rec_area_button)

        self.backup_countdown_label = QLabel("")
        self.backup_countdown_label.setStyleSheet("font-size: 12px; color: #888888;")
        top_layout.addWidget(self.backup_countdown_label)

        top_layout.addSpacerItem(QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        
        self.perf_label = QLabel("CPU: --%  メモリ: --MB  FPS: --.-  クリック: 0  稼働: 00:00:00")
        self.perf_label.setStyleSheet("font-size: 14px;")
        top_layout.addWidget(self.perf_label)

        main_layout.addLayout(top_layout)

        self.log_text_edit = QTextEdit()
        self.log_text_edit.setReadOnly(True)
        main_layout.addWidget(self.log_text_edit)
        
    def connect_signals(self):
        """シグナルとスロットを接続します"""
        self.monitor_button.clicked.connect(self.toggleMonitoringRequested.emit)
        if self.ui_manager:
            self.rec_area_button.clicked.connect(self.ui_manager.setRecAreaDialog)
            
    # ★★★ 新規追加: 監視状態に応じてボタンの色を変更するメソッド ★★★
    def update_monitoring_status(self, status_text: str, color: str):
        if status_text == "監視中...":
            self.monitor_button.setStyleSheet("background-color: #3399FF; color: white;")
        else:
            self.monitor_button.setStyleSheet("") # デフォルトのスタイルに戻す

    def update_fps(self, fps):
        self.current_fps = fps
        
    def update_performance_info(self):
        try:
            cpu_percent = self.process.cpu_percent()
            mem_used = self.process.memory_info().rss / (1024 * 1024)
            clicks = self.ui_manager.core_engine._click_count if self.ui_manager and self.ui_manager.core_engine else 0
            uptime_seconds = int(time.time() - self.start_time)
            hours, remainder = divmod(uptime_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            
            perf_text = (f"CPU: {cpu_percent:.1f}%  メモリ: {mem_used:.1f}MB  FPS: {self.current_fps:.1f}  "
                         f"クリック: {clicks}  稼働: {hours:02d}:{minutes:02d}:{seconds:02d}")
            self.perf_label.setText(perf_text)

            if self.ui_manager and self.ui_manager.core_engine:
                countdown = self.ui_manager.core_engine.get_backup_click_countdown()
                if countdown > 0:
                    self.backup_countdown_label.setText(f"(バックアップまで: {countdown:.0f}秒)")
                else:
                    self.backup_countdown_label.setText("")

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            self.update_timer.stop()
        except Exception as e:
            if "core_engine" not in str(e):
                print(f"パフォーマンス情報更新エラー: {e}")

    def update_log(self, message):
        self.log_text_edit.append(message)
        scrollbar = self.log_text_edit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def closeEvent(self, event):
        event.ignore()
        self.hide()
