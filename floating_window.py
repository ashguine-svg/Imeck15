# floating_window.py

from PySide6.QtWidgets import (
    QDialog, QPushButton, QHBoxLayout, QLabel, QSpacerItem, QSizePolicy, QApplication, QStyle
)
# ★★★ 修正: QRect をインポートに追加 ★★★
from PySide6.QtGui import QPainter, QColor, QFont, QPen, QIcon, QPixmap, QImage
from PySide6.QtCore import Qt, Signal, QPoint, QEvent, QSize, QRect

import qtawesome as qta

class FloatingWindow(QDialog):
    """
    最小UIモードで表示されるフローティングウィンドウ。
    タイトルバーの高さに合わせて自動調整されるモダンデザイン版。
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
        self.setAttribute(Qt.WA_AlwaysShowToolTips, True)
        self.setWindowOpacity(0.95)

        self.offset = None

        try:
            title_bar_height = self.style().pixelMetric(QStyle.PM_TitleBarHeight)
            if title_bar_height < 24: title_bar_height = 24
            if title_bar_height > 40: title_bar_height = 40
        except Exception:
            title_bar_height = 30

        # ベースサイズを保存（スケール変更用）
        self.base_height = title_bar_height
        self.base_width = 600
        self.current_scale = 1.0  # 1.0 = 標準, 2.0 = 2倍

        self.setFixedHeight(title_bar_height)
        
        v_margin = 2
        button_size = title_bar_height - (v_margin * 2)
        
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(8, 0, 8, 0)
        main_layout.setSpacing(6)
        
        self.setWindowTitle(lm("float_window_title"))
        
        # --- サイズ変更ボタン（左端に配置） ---
        self.scale_button = QPushButton()
        self.scale_button.setIcon(self._safe_icon('fa5s.search-plus', color='#64b5f6'))
        self.scale_button.setIconSize(QSize(int(button_size * 0.6), int(button_size * 0.6)))
        self.scale_button.setFixedSize(button_size, button_size)
        self.scale_button.setToolTip("UIサイズを2倍/1倍に切り替え")
        self.scale_button.setCursor(Qt.PointingHandCursor)
        self.scale_button.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(100, 181, 246, 120);
                border-radius: {button_size // 2}px;
                border: 1px solid rgba(100, 181, 246, 180);
            }}
            QPushButton:hover {{
                background-color: rgba(100, 181, 246, 180);
            }}
            QPushButton:pressed {{
                background-color: rgba(100, 181, 246, 220);
            }}
        """)
        self.scale_button.clicked.connect(self.toggle_scale)
        
        def create_float_btn(icon_name, tooltip_key, color='white'):
            btn = QPushButton()
            # 修正: _safe_icon を使用
            btn.setIcon(self._safe_icon(icon_name, color=color))
            btn.setIconSize(QSize(int(button_size * 0.6), int(button_size * 0.6)))
            btn.setFixedSize(button_size, button_size)
            btn.setToolTip(lm(tooltip_key))
            btn.setCursor(Qt.PointingHandCursor)
            
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    border-radius: {button_size // 2}px;
                    border: none;
                }}
                QPushButton:hover {{
                    background-color: rgba(255, 255, 255, 40);
                }}
                QPushButton:pressed {{
                    background-color: rgba(255, 255, 255, 80);
                }}
            """)
            return btn

        self.start_button = create_float_btn('fa5s.play', "float_tooltip_start", color='#4caf50')
        self.stop_button = create_float_btn('fa5s.stop', "float_tooltip_stop", color='#f44336')
        self.capture_button = create_float_btn('fa5s.camera', "float_tooltip_capture", color='#bdbdbd') 
        self.set_rec_area_button = create_float_btn('fa5s.crop', "float_tooltip_rec_area", color='#ff9800') 
        self.close_button = create_float_btn('fa5s.times', "float_tooltip_close", color='#9e9e9e')
        
        # アイコンボタンの情報を保存（スケール変更時に再設定用）
        self.icon_buttons_info = [
            (self.start_button, 'fa5s.play', "float_tooltip_start", '#4caf50'),
            (self.stop_button, 'fa5s.stop', "float_tooltip_stop", '#f44336'),
            (self.capture_button, 'fa5s.camera', "float_tooltip_capture", '#bdbdbd'),
            (self.set_rec_area_button, 'fa5s.crop', "float_tooltip_rec_area", '#ff9800'),
            (self.close_button, 'fa5s.times', "float_tooltip_close", '#9e9e9e'),
        ]

        label_font = self.font()
        label_font.setPixelSize(int(title_bar_height * 0.45))
        label_font.setBold(True)
        
        text_style = "color: white; background-color: transparent; margin: 0 4px;"
        
        self.status_label = QLabel(lm("float_label_status_default"))
        self.status_label.setFont(label_font)
        self.status_label.setStyleSheet(text_style)
        
        self.cpu_label = QLabel("CPU: --%")
        self.cpu_label.setFont(label_font)
        self.cpu_label.setStyleSheet(text_style)
        
        self.fps_label = QLabel("FPS: --")
        self.fps_label.setFont(label_font)
        self.fps_label.setStyleSheet(text_style)
        
        self.clicks_label = QLabel("Clk: 0")
        self.clicks_label.setFont(label_font)
        self.clicks_label.setStyleSheet(text_style)
        
        self.backup_timer_label = QLabel("BC: --s")
        self.backup_timer_label.setFont(label_font)
        self.backup_timer_label.setStyleSheet("color: #ffcc80; background-color: transparent; margin: 0 4px;")
        self.backup_timer_label.setVisible(False)
        
        self.priority_timer_label = QLabel("Pri: --m")
        self.priority_timer_label.setFont(label_font)
        self.priority_timer_label.setStyleSheet("color: #a5d6a7; background-color: transparent; margin: 0 4px;")
        self.priority_timer_label.setVisible(False)

        # サイズ変更ボタンを左端に配置
        main_layout.addWidget(self.scale_button)
        
        # 区切り線
        line_left = QLabel("|")
        line_left.setStyleSheet("color: #757575; font-weight: bold;")
        main_layout.addWidget(line_left)

        main_layout.addWidget(self.start_button)
        main_layout.addWidget(self.stop_button)
        main_layout.addWidget(self.capture_button)
        main_layout.addWidget(self.set_rec_area_button)
        
        line = QLabel("|")
        line.setStyleSheet("color: #757575; font-weight: bold;")
        main_layout.addWidget(line)
        
        main_layout.addWidget(self.status_label)
        main_layout.addStretch() 
        
        main_layout.addWidget(self.cpu_label)
        main_layout.addWidget(self.fps_label)
        main_layout.addWidget(self.clicks_label)
        
        main_layout.addWidget(self.backup_timer_label)
        main_layout.addWidget(self.priority_timer_label)
        
        main_layout.addWidget(self.close_button)

        buttons_list = [self.scale_button, self.start_button, self.stop_button, self.capture_button, self.set_rec_area_button, self.close_button]
        for btn in buttons_list:
            btn.installEventFilter(self)

        self.start_button.clicked.connect(self.startMonitoringRequested)
        self.stop_button.clicked.connect(self.stopMonitoringRequested)
        self.capture_button.clicked.connect(self.captureImageRequested)
        self.close_button.clicked.connect(self.closeRequested)
        self.set_rec_area_button.clicked.connect(self.setRecAreaRequested)
        
        self.resize(self.base_width, self.base_height)
    
    def toggle_scale(self):
        """UIサイズを2倍/1倍にトグルします。"""
        if self.current_scale == 1.0:
            self.current_scale = 2.0
        else:
            self.current_scale = 1.0
        
        self._apply_scale()
    
    def _apply_scale(self):
        """現在のスケールを適用してUIを更新します。"""
        # 新しいサイズを計算
        new_height = int(self.base_height * self.current_scale)
        new_width = int(self.base_width * self.current_scale)
        
        # 高さを更新
        self.setFixedHeight(new_height)
        
        # ボタンサイズとマージンを再計算
        v_margin = 2
        new_button_size = new_height - (v_margin * 2)
        
        # フォントサイズを更新（基本サイズ）
        base_font_size = int(new_height * 0.45)
        label_font = self.font()
        label_font.setPixelSize(base_font_size)
        label_font.setBold(True)
        
        # サイズ変更ボタンのサイズとアイコンを更新
        self.scale_button.setFixedSize(new_button_size, new_button_size)
        self.scale_button.setIconSize(QSize(int(new_button_size * 0.6), int(new_button_size * 0.6)))
        # スケールに応じてアイコンを切り替え（2倍時は縮小アイコン、1倍時は拡大アイコン）
        if self.current_scale == 2.0:
            self.scale_button.setIcon(self._safe_icon('fa5s.search-minus', color='#64b5f6'))
        else:
            self.scale_button.setIcon(self._safe_icon('fa5s.search-plus', color='#64b5f6'))
        self.scale_button.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(100, 181, 246, 120);
                border-radius: {new_button_size // 2}px;
                border: 1px solid rgba(100, 181, 246, 180);
            }}
            QPushButton:hover {{
                background-color: rgba(100, 181, 246, 180);
            }}
            QPushButton:pressed {{
                background-color: rgba(100, 181, 246, 220);
            }}
        """)
        
        # アイコンボタンのサイズとアイコンを更新（左側のボタンはそのまま）
        for btn, icon_name, tooltip_key, color in self.icon_buttons_info:
            btn.setFixedSize(new_button_size, new_button_size)
            btn.setIconSize(QSize(int(new_button_size * 0.6), int(new_button_size * 0.6)))
            btn.setIcon(self._safe_icon(icon_name, color=color))
            btn.setToolTip(self.locale_manager.tr(tooltip_key))
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    border-radius: {new_button_size // 2}px;
                    border: none;
                }}
                QPushButton:hover {{
                    background-color: rgba(255, 255, 255, 40);
                }}
                QPushButton:pressed {{
                    background-color: rgba(255, 255, 255, 80);
                }}
            """)
        
        # 横幅が1024pxを超える場合、ラベルのフォントサイズとマージンを調整
        MAX_WIDTH = 1024
        if new_width > MAX_WIDTH and self.current_scale == 2.0:
            # 左側のボタンエリアの幅を計算（概算）
            # ボタン: x2, |, 開始, 停止, キャプチャ, 認識範囲設定, | = 約6個のボタン + 区切り線2本
            left_area_width = (new_button_size * 5) + (6 * 6) + (8 * 2)  # ボタン5個 + スペーシング + マージン
            available_width = MAX_WIDTH - left_area_width - 20  # 余裕を持たせる
            
            # ラベルのフォントサイズを調整（横幅に収まるように）
            # 右側のラベル: ステータス, CPU, FPS, クリック数, バックアップタイマー, 優先タイマー, 閉じるボタン
            # フォントサイズを少し小さくする
            adjusted_font_size = int(base_font_size * 0.85)  # 15%縮小
            label_font.setPixelSize(adjusted_font_size)
            
            # ラベルのマージンを調整
            label_margin = "0 2px"  # マージンを小さく
            label_style_base = "background-color: transparent; margin: {};"
        else:
            # 通常のマージン
            label_margin = "0 4px"
            label_style_base = "background-color: transparent; margin: {};"
        
        # ラベルのフォントとスタイルを更新
        labels_info = [
            (self.status_label, None),  # ステータスラベルは色が動的に変わるので後で処理
            (self.cpu_label, "white"),
            (self.fps_label, "white"),
            (self.clicks_label, "white"),
            (self.backup_timer_label, "#ffcc80"),
            (self.priority_timer_label, "#a5d6a7"),
        ]
        
        for label, color in labels_info:
            label.setFont(label_font)
            if color:
                label.setStyleSheet(f"color: {color}; {label_style_base.format(label_margin)}")
            # ステータスラベルはupdate_statusメソッドで色が設定されるので、ここではフォントのみ更新
        
        # ウィンドウの幅を更新（最大1024pxに制限）
        final_width = min(new_width, MAX_WIDTH)
        self.resize(final_width, new_height)
        
        # レイアウトのマージンとスペーシングを調整
        main_layout = self.layout()
        if main_layout:
            if new_width > MAX_WIDTH and self.current_scale == 2.0:
                # 横幅制限時はスペーシングを少し小さく
                main_layout.setContentsMargins(8, 0, 8, 0)
                main_layout.setSpacing(4)
            else:
                main_layout.setContentsMargins(8, 0, 8, 0)
                main_layout.setSpacing(6)

    # ★★★ QtAwesome対策: 安全なアイコン生成ラッパー ★★★
    def _safe_icon(self, icon_name, color=None):
        try:
            if color:
                base_icon = qta.icon(icon_name, color=color)
            else:
                base_icon = qta.icon(icon_name)
            
            # メモリ上のQImageに描画して静的化
            image = QImage(24, 24, QImage.Format_ARGB32_Premultiplied)
            image.fill(Qt.transparent)
            
            painter = QPainter()
            if painter.begin(image):
                try:
                    # ここで QRect が必要
                    base_icon.paint(painter, QRect(0, 0, 24, 24))
                finally:
                    painter.end()
            
            return QIcon(QPixmap.fromImage(image))

        except Exception as e:
            print(f"[WARN] QtAwesome rendering failed for {icon_name}: {e}")
            return QIcon()

    def eventFilter(self, watched_object, event):
        if (event.type() == QEvent.Type.MouseButtonPress or event.type() == QEvent.Type.MouseButtonDblClick) and \
           event.button() == Qt.MouseButton.RightButton:
            return True
        return super().eventFilter(watched_object, event)

    def on_stats_updated(self, click_count: int, uptime_str: str, timer_data: dict, cpu: float, fps: float):
        self.cpu_label.setText(f"CPU: {cpu:.0f}%")
        self.fps_label.setText(f"FPS: {fps:.0f}")
        self.clicks_label.setText(f"Clk: {click_count}")
        
        backup_remaining = timer_data.get('backup', -1.0)
        if backup_remaining >= 0:
            self.backup_timer_label.setText(f"BC: {backup_remaining:.0f}s")
            self.backup_timer_label.setVisible(True)
        else:
            self.backup_timer_label.setVisible(False)

        priority_remaining_min = timer_data.get('priority', -1.0)
        if priority_remaining_min >= 0:
            self.priority_timer_label.setText(f"Pri: {priority_remaining_min:.0f}m")
            self.priority_timer_label.setVisible(True)
        else:
            self.priority_timer_label.setVisible(False)

    def reset_performance_stats(self):
        self.cpu_label.setText("CPU: --%")
        self.fps_label.setText("FPS: --")

    def update_status(self, text, color="green"):
        color_map = {"green": "#90EE90", "blue": "#64b5f6", "orange": "#ffb74d", "red": "#e57373"}
        # 16進カラー指定が渡された場合はそのまま使う
        if isinstance(color, str) and color.startswith("#"):
            hex_color = color
        else:
            hex_color = color_map.get(color, "white")
        self.status_label.setText(text)
        # 現在のスケールに応じてマージンを調整
        margin = "0 2px" if (self.current_scale == 2.0 and self.width() >= 1024) else "0 4px"
        self.status_label.setStyleSheet(f"color: {hex_color}; background-color: transparent; font-weight: bold; margin: {margin};")

    def paintEvent(self, event):
        painter = QPainter()
        if painter.begin(self):
            try:
                painter.setRenderHint(QPainter.Antialiasing)
                painter.setPen(Qt.NoPen)
                # 背景色: 濃いグレー
                painter.setBrush(QColor(33, 33, 33, 230))
                
                rect = self.rect()
                radius = 4.0 
                painter.drawRoundedRect(rect, radius, radius)
                
                painter.setBrush(QColor(158, 158, 158)) 
                painter.drawRoundedRect(0, 0, 4, rect.height(), 2, 2)
            finally:
                painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self.close_button.underMouse(): return
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
        snap = 10
        new_pos = QPoint(pos.x(), pos.y())
        moved = False
        
        if pos.x() <= screen_rect.left() + snap: new_pos.setX(screen_rect.left()); moved = True
        if pos.x() + self.width() >= screen_rect.right() - snap: new_pos.setX(screen_rect.right() - self.width()); moved = True
        if pos.y() <= screen_rect.top() + snap: new_pos.setY(screen_rect.top()); moved = True
        if pos.y() + self.height() >= screen_rect.bottom() - snap: new_pos.setY(screen_rect.bottom() - self.height()); moved = True
        
        if moved: self.move(new_pos)
