# floating_window.py

from PySide6.QtWidgets import (
    QDialog, QPushButton, QHBoxLayout, QLabel, QSpacerItem, QSizePolicy, QApplication, QStyle,
    QVBoxLayout # ★ QVBoxLayout をインポート
)
from PySide6.QtGui import QPainter, QColor, QFontMetrics
from PySide6.QtCore import Qt, Signal, QPoint, QEvent # ★ QEvent を追加

class FloatingWindow(QDialog):
    """
    最小UIモードで表示されるフローティングウィンドウ。
    (仕様書 3.3 適用版)
    """
    startMonitoringRequested = Signal()
    stopMonitoringRequested = Signal()
    captureImageRequested = Signal()
    toggleMainUIRequested = Signal()
    closeRequested = Signal()
    setRecAreaRequested = Signal()

    def __init__(self, locale_manager, parent=None):
        super().__init__(parent)
        self.locale_manager = locale_manager
        lm = self.locale_manager.tr
        
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowOpacity(0.85)
        
        self.setAttribute(Qt.WA_AlwaysShowToolTips, True)

        self.offset = None

        # OSのタイトルバーの高さを取得
        try:
            # QStyle.PM_TitleBarHeight を使ってOSの標準的な高さを取得
            title_bar_height = self.style().pixelMetric(QStyle.PM_TitleBarHeight)
            if title_bar_height <= 0: title_bar_height = 28 # フォールバック値
        except Exception:
            title_bar_height = 28
            
        # マージンとボタンの高さを設定
        v_margin = 2 # 縦の余白
        h_margin = 6 # 横の余白
        # ボタンの高さをタイトルバーの高さから余白を引いた値に設定
        button_height = title_bar_height - (v_margin * 2)

        # --- 1列表示のメインレイアウト (QHBoxLayout) ---
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(h_margin, v_margin, h_margin, v_margin)
        main_layout.setSpacing(4) # ウィジェット間のスペース
        
        self.setWindowTitle(lm("float_window_title"))
        
        # --- 1. ボタン ---
        self.start_button = QPushButton(lm("float_button_start"))
        self.stop_button = QPushButton(lm("float_button_stop"))
        self.capture_button = QPushButton(lm("float_button_capture"))
        self.set_rec_area_button = QPushButton(lm("float_button_rec_area"))
        self.toggle_ui_button = QPushButton(lm("float_button_toggle_ui"))
        self.close_button = QPushButton(lm("float_button_close"))
        
        buttons_list = [
            self.start_button, self.stop_button, self.capture_button, 
            self.set_rec_area_button, self.toggle_ui_button, self.close_button
        ]
        
        for btn in buttons_list:
            btn.installEventFilter(self)
        
        
        # ボタンのスタイルとサイズを設定
        button_radius = int(button_height / 2) # 角丸（円形）のための半径
        for btn in buttons_list:
            btn.setFixedSize(button_height, button_height)
            font = btn.font()
            font.setPointSize(int(button_height * 0.45)) # フォントサイズ調整
            btn.setFont(font)
            btn.setStyleSheet(
                f"QPushButton {{ border-radius: {button_radius}px; background-color: rgba(200, 200, 200, 150); color: black; }}"
                f"QPushButton:hover {{ background-color: rgba(220, 220, 220, 200); }}"
            )
        
        # 閉じるボタンだけ赤くする
        self.close_button.setStyleSheet(
            f"QPushButton {{ border-radius: {button_radius}px; background-color: rgba(231, 76, 60, 180); color: white; font-weight: bold; }}"
            f"QPushButton:hover {{ background-color: rgba(231, 76, 60, 230); }}"
        )

        # --- 2. 統計情報ラベル ---
        label_font = self.font()
        label_font.setBold(True)
        
        self.cpu_label = QLabel("CPU: ---%")
        self.fps_label = QLabel("FPS: --")
        self.status_label = QLabel(lm("float_label_status_default")) # "待機中..."
        self.clicks_label = QLabel("Clicks: 0")
        self.uptime_label = QLabel("Uptime: 00h00m00s")
        
        # --- ▼▼▼ 修正箇所 (タイマーラベルの追加) ▼▼▼ ---
        self.backup_timer_label = QLabel("BC: ---s")
        self.priority_timer_label = QLabel("●: --m")

        stats_list = [
            self.cpu_label, self.fps_label, self.status_label,
            self.clicks_label, self.uptime_label, 
            self.backup_timer_label, self.priority_timer_label # 新しいラベルを追加
        ]
        
        for label in stats_list:
            label.setFont(label_font)
            label.setStyleSheet("color: white; background-color: transparent; padding: 2px;")

        # ステータスとカウントダウンの色を個別に設定
        self.status_label.setStyleSheet("color: #90EE90; background-color: transparent; padding: 2px;") # 緑
        # バックアップタイマーはオレンジ
        self.backup_timer_label.setStyleSheet("color: #FFA500; background-color: transparent; padding: 2px;") 
        # 優先タイマーは緑
        self.priority_timer_label.setStyleSheet("color: #90EE90; background-color: transparent; padding: 2px;") 
        
        # デフォルトで非表示にする
        self.backup_timer_label.setVisible(False)
        self.priority_timer_label.setVisible(False)
        # --- ▲▲▲ 修正完了 ▲▲▲ ---


        # --- 3. レイアウトにウィジェットを追加 ---
        main_layout.addWidget(self.start_button)
        main_layout.addWidget(self.stop_button)
        main_layout.addWidget(self.capture_button)
        main_layout.addWidget(self.set_rec_area_button)
        main_layout.addWidget(self.toggle_ui_button)
        main_layout.addSpacerItem(QSpacerItem(10, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))

        # 統計情報を追加
        main_layout.addWidget(self.cpu_label)
        main_layout.addWidget(self.fps_label)
        main_layout.addWidget(self.status_label)
        main_layout.addWidget(self.clicks_label)
        main_layout.addWidget(self.uptime_label)
        
        # --- ▼▼▼ 修正箇所 (タイマーラベルのレイアウト追加) ▼▼▼ ---
        # main_layout.addWidget(self.countdown_label) # 古いラベルを削除
        main_layout.addWidget(self.backup_timer_label) # 新しいバックアップタイマーを追加
        main_layout.addWidget(self.priority_timer_label) # 新しい優先タイマーを追加
        # --- ▲▲▲ 修正完了 ▲▲▲ ---
        
        main_layout.addSpacerItem(QSpacerItem(10, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        main_layout.addWidget(self.close_button)

        # --- 4. ツールチップ ---
        self.start_button.setToolTip(lm("float_tooltip_start"))
        self.stop_button.setToolTip(lm("float_tooltip_stop"))
        self.capture_button.setToolTip(lm("float_tooltip_capture"))
        self.set_rec_area_button.setToolTip(lm("float_tooltip_rec_area"))
        self.toggle_ui_button.setToolTip(lm("float_tooltip_toggle_ui"))
        self.close_button.setToolTip(lm("float_tooltip_close"))

        # --- 5. シグナル接続 ---
        self.start_button.clicked.connect(self.startMonitoringRequested)
        self.stop_button.clicked.connect(self.stopMonitoringRequested)
        self.capture_button.clicked.connect(self.captureImageRequested)
        self.toggle_ui_button.clicked.connect(self.toggleMainUIRequested)
        self.close_button.clicked.connect(self.closeRequested)
        self.set_rec_area_button.clicked.connect(self.setRecAreaRequested)
        
        # 最小幅を自動調整し、最大幅を設定
        self.setMinimumWidth(self.sizeHint().width())
        self.setMaximumWidth(960) # ご要望の最大幅
    
    def eventFilter(self, watched_object, event):
        """
        インストールされたイベントフィルター。
        ボタンに対する右クリックイベントをすべて無視します。
        """
        buttons_list = [
            self.start_button, self.stop_button, self.capture_button, 
            self.set_rec_area_button, self.toggle_ui_button, self.close_button
        ]

        # 監視対象がリスト内のボタンであり、
        # イベントが「右クリック」のプレスまたはダブルクリックの場合
        if watched_object in buttons_list and \
           (event.type() == QEvent.Type.MouseButtonPress or event.type() == QEvent.Type.MouseButtonDblClick) and \
           event.button() == Qt.MouseButton.RightButton:
            
            # イベントを無視 (Trueを返す) し、Qtに処理させない
            return True

        # それ以外のイベントは通常通り処理する
        return super().eventFilter(watched_object, event)
    
    def on_stats_updated(self, click_count: int, uptime_str: str, timer_data: dict, cpu: float, fps: float):
        """CoreEngineから統計情報を受け取るスロット"""
        lm = self.locale_manager.tr
        
        # 1. CPU と FPS
        self.cpu_label.setText(f"CPU: {cpu:.0f}%")
        self.fps_label.setText(f"FPS: {fps:.0f}")
        
        # 2. クリック回数と稼働時間
        self.clicks_label.setText(f"Clicks: {click_count}")
        self.uptime_label.setText(f"{uptime_str}") 
            
        # --- ▼▼▼ 修正箇所 (タイマーロジックの分離) ▼▼▼ ---
            
        # 3. バックアップタイマー (BC: ---s)
        backup_remaining = timer_data.get('backup', -1.0)
        if backup_remaining >= 0:
            self.backup_timer_label.setText(f"BC: {backup_remaining:.0f}s")
            self.backup_timer_label.setVisible(True)
        else:
            self.backup_timer_label.setVisible(False)

        # 4. 優先タイマー (●: --m)
        priority_remaining_min = timer_data.get('priority', -1.0)
        if priority_remaining_min >= 0:
            self.priority_timer_label.setText(f"●: {priority_remaining_min:.0f}m")
            self.priority_timer_label.setVisible(True)
        else:
            self.priority_timer_label.setVisible(False)

    def update_status(self, text, color="green"):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; background-color: transparent;")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(50, 50, 50, 200))
        
        # ★★★ 矩形から角丸に変更 ★★★
        rect = self.rect()
        # 高さに応じた半径 (高さの半分の半径で完全な円形)
        radius = rect.height() / 2.0 
        painter.drawRoundedRect(rect, radius, radius)

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
