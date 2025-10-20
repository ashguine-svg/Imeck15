# monitor.py
# ★★★ カウントダウン表示を .format() を使うように再修正 ★★★

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
    performanceUpdated = Signal(float, float)

    # ★★★ 1. __init__ に locale_manager を追加 ★★★
    def __init__(self, ui_manager, locale_manager, parent=None):
        super().__init__(parent)
        self.ui_manager = ui_manager
        self.locale_manager = locale_manager # LocaleManagerインスタンスを保持

        flags = Qt.Window | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
        self.setWindowFlags(flags)

        self.resize(1024, 200)
        self.setMinimumSize(600, 100)

        self.process = psutil.Process()
        self.process.cpu_percent(interval=None)
        self.current_fps = 0.0
        self.current_clicks = 0

        self.setup_ui() # UIのセットアップ

        self.start_time = time.time()
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.update_performance_info)
        self.update_timer.start(1000)

    def setup_ui(self):
        # ★★★ 2. 翻訳キーでUIテキストを設定 ★★★
        lm = self.locale_manager.tr

        self.setWindowTitle(lm("monitor_window_title"))

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        top_layout = QHBoxLayout()
        top_layout.setContentsMargins(0, 0, 0, 0)

        self.monitor_button = QPushButton(lm("monitor_button_toggle"))
        self.monitor_button.setToolTip(lm("monitor_button_toggle_tooltip"))
        top_layout.addWidget(self.monitor_button)

        self.rec_area_button = QPushButton(lm("monitor_button_rec_area"))
        top_layout.addWidget(self.rec_area_button)

        self.backup_countdown_label = QLabel("")
        self.backup_countdown_label.setStyleSheet("font-size: 12px; color: #888888;")
        top_layout.addWidget(self.backup_countdown_label)

        top_layout.addSpacerItem(QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))

        self.perf_label = QLabel(lm("monitor_label_perf_default"))
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

    def update_monitoring_status(self, status_text_key: str, color: str):
        # ★★★ 3. ステータスをキー (monitoring, idle) で受け取る ★★★
        if status_text_key == "monitoring":
            self.monitor_button.setStyleSheet("background-color: #3399FF; color: white;")
        else:
            self.monitor_button.setStyleSheet("")

    def update_fps(self, fps):
        self.current_fps = fps

    def update_click_count(self, count):
        self.current_clicks = count

    def update_performance_info(self):
        try:
            cpu_percent = self.process.cpu_percent()
            mem_used = self.process.memory_info().rss / (1024 * 1024)
            clicks = self.current_clicks
            uptime_seconds = int(time.time() - self.start_time)
            hours, remainder = divmod(uptime_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)

            # ★★★ 4. フォーマット文字列を翻訳キーから取得し、.format() で適用 ★★★
            lm = self.locale_manager.tr
            perf_text_format = lm("monitor_perf_format")
            # .format() が失敗する可能性も考慮 (キー名が翻訳ファイルと違う場合)
            try:
                perf_text = perf_text_format.format(
                    cpu=cpu_percent,
                    mem=mem_used,
                    fps=self.current_fps,
                    clicks=clicks,
                    h=hours,
                    m=minutes,
                    s=seconds
                )
            except KeyError:
                 perf_text = f"CPU:{cpu_percent:.1f}% MEM:{mem_used:.0f}MB FPS:{self.current_fps:.1f} CLICKS:{clicks} UPTIME:{hours:02d}:{minutes:02d}:{seconds:02d} (ERR:FMT)" # Fallback
            self.perf_label.setText(perf_text)

            self.performanceUpdated.emit(cpu_percent, self.current_fps)

            if self.ui_manager and self.ui_manager.core_engine:
                countdown = self.ui_manager.core_engine.get_backup_click_countdown()
                if countdown > 0:
                    # ★★★ 修正: tr() でフォーマット文字列を取得し、.format() を使用 ★★★
                    countdown_format_string = lm("monitor_backup_countdown")
                    try:
                        # .0f で小数点以下を表示しない整数にする
                        countdown_text = countdown_format_string.format(s=countdown)
                    except KeyError: # 翻訳ファイルのキー名 {s} と一致しない場合
                        countdown_text = f"(バックアップまで: {countdown:.0f}秒) (ERR:FMT)" # Fallback
                    self.backup_countdown_label.setText(countdown_text)
                else:
                    self.backup_countdown_label.setText("")

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            self.update_timer.stop()
            self.perf_label.setText(self.locale_manager.tr("monitor_perf_error")) # エラー表示
            self.backup_countdown_label.setText("")
        except Exception as e:
            # core_engine がまだ存在しない等のエラーは無視
            if hasattr(self, 'ui_manager') and self.ui_manager and self.ui_manager.logger:
                # ★★★ 6. print を self.ui_manager.logger.log に変更 ★★★
                self.ui_manager.logger.log("monitor_log_error", str(e))

    def update_log(self, message):
        self.log_text_edit.append(message)
        scrollbar = self.log_text_edit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def closeEvent(self, event):
        event.ignore()
        self.hide()
