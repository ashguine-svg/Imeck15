# monitor.py
# ★★★ カウントダウン表示を .format() を使うように再修正 ★★★
# ★★★ 統計情報表示を削除し、ログ専用ビューアに変更 ★★★
# ★★★ [修正] OS標準のタイトルバー（閉じる・最小化ボタン）を復活 ★★★

import sys
import time
import psutil
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QSizePolicy, QSpacerItem, QApplication # ★ QApplication を追加
)
from PySide6.QtGui import QPainter, QColor # ★ QPainter, QColor を追加
from PySide6.QtCore import Qt, Signal, QTimer, QPoint # ★ QPoint を追加

class PerformanceMonitor(QDialog):
    """
    リアルタイムのパフォーマンス情報を表示する独立したウィンドウ。
    (現在はログビューアとして機能)
    """

    # toggleMonitoringRequested シグナルは削除
    # performanceUpdated シグナルは削除

    def __init__(self, ui_manager, locale_manager, parent=None):
        """
        __init__ メソッド:
        TypeErrorを回避するため、super().__init__(parent) を正しく呼び出します。
        """
        super().__init__(parent)
        self.ui_manager = ui_manager
        self.locale_manager = locale_manager # LocaleManagerインスタンスを保持

        # --- ▼▼▼ 修正箇所 (Task 2, 3) ▼▼▼ ---
        # 最小UIとスタイルを合わせる
        # flags = (
        #     Qt.FramelessWindowHint |
        #     Qt.WindowStaysOnTopHint |
        #     Qt.Tool
        # ) # ★ 枠なしフラグを削除
        
        # ★ OS標準のタイトルバーを再表示するフラグに戻す
        flags = Qt.Window | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
        
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground) # 半透明背景は維持
        self.setWindowOpacity(0.85) # 全体の透明度は維持

        self.resize(1024, 200) # 起動時のサイズはそのまま
        self.setMinimumSize(200, 40) # ★ 最小サイズ (200x40)
        # --- ▲▲▲ 修正完了 ▲▲▲ ---

        self.process = psutil.Process()
        self.process.cpu_percent(interval=None)
        
        self.last_cpu_percent = 0.0

        # --- ▼▼▼ 修正箇所 (Task 4) ▼▼▼ ---
        # self.offset = None # ★ 枠なし用の変数のため削除
        # --- ▲▲▲ 修正完了 ▲▲▲ ---

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

        # --- ▼▼▼ 修正箇所 (Task 1: ボタン削除) ▼▼▼ ---
        # top_layout (ボタンが配置されていたレイアウト) 全体を削除
        # --- ▲▲▲ 修正完了 ▲▲▲ ---

        self.log_text_edit = QTextEdit()
        self.log_text_edit.setReadOnly(True)
        
        # --- ▼▼▼ 修正箇所 (Task 3: スタイル設定) ▼▼▼ ---
        # ログウィンドウの背景を半透明の黒に、文字を白に設定
        self.log_text_edit.setStyleSheet(
            "background-color: rgba(0, 0, 0, 100); color: white; border: none;"
        )
        # --- ▲▲▲ 修正完了 ▲▲▲ ---
        
        main_layout.addWidget(self.log_text_edit)

    def connect_signals(self):
        """シグナルとスロットを接続します"""
        # --- ▼▼▼ 修正箇所 (Task 1: 接続削除) ▼▼▼ ---
        pass # 接続するシグナルがなくなったため pass に変更
        # --- ▲▲▲ 修正完了 ▲▲▲ ---

    # --- ▼▼▼ 修正箇所 (Task 1: メソッド削除) ▼▼▼ ---
    # update_monitoring_status メソッドは削除されました
    # --- ▲▲▲ 修正完了 ▲▲▲ ---

    def update_performance_info(self):
        try:
            self.last_cpu_percent = self.process.cpu_percent(interval=None) 
            
            # プロセスが存在するかどうかの最小限のチェック
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

    # --- ▼▼▼ 修正箇所 (Task 2, 3: paintEvent 追加) ▼▼▼ ---
    def paintEvent(self, event):
        """背景を角丸で描画します"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        # 最小UI (floating_window.py) と同じ背景色
        painter.setBrush(QColor(50, 50, 50, 200)) 
        # 10px の角丸で描画
        painter.drawRoundedRect(self.rect(), 10.0, 10.0)
    # --- ▲▲▲ 修正完了 ▲▲▲ ---

    # --- ▼▼▼ 修正箇所 (Task 4: マウスイベント3種 削除) ▼▼▼ ---
    # mousePressEvent, mouseMoveEvent, mouseReleaseEvent は削除されました
    # --- ▲▲▲ 修正完了 ▲▲▲ ---

    def get_last_cpu(self):
        """CoreEngineが1秒ごとにCPU使用率を取得するためのメソッド"""
        return self.last_cpu_percent
