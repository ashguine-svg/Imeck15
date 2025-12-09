# dialogs.py

import sys
from PySide6.QtWidgets import (
    QDialog, QPushButton, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QRadioButton, QButtonGroup, QGridLayout, QSpinBox, QDialogButtonBox,
    QApplication, QStyle
)
from PySide6.QtGui import QCursor, QIcon
from PySide6.QtCore import Qt, Signal, QTimer, QSize

import qtawesome as qta

class RecAreaSelectionDialog(QDialog):
    selectionMade = Signal(str)
    
    def __init__(self, locale_manager, parent=None):
        super().__init__(parent)
        self.locale_manager = locale_manager
        lm = self.locale_manager.tr
        
        self.setWindowTitle(lm("rec_area_dialog_title"))
        # ツールウィンドウとして設定し、枠を消す
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Popup)
        
        # スタイル調整: 白背景、グレー枠線、濃い文字色
        self.setStyleSheet("""
            QDialog {
                background-color: #ffffff;
                border: 1px solid #90a4ae;
                border-radius: 6px;
            }
            QLabel {
                color: #37474f;
                font-weight: bold;
                font-size: 13px;
                border: none;
            }
            QPushButton {
                text-align: left;
                padding-left: 15px;
                border: 1px solid #cfd8dc;
                border-radius: 4px;
                background-color: #f5f5f5;
                color: #37474f;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #eceff1;
                border-color: #b0bec5;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)
        
        label = QLabel(lm("rec_area_dialog_prompt"))
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
        
        button_layout = QVBoxLayout() 
        button_layout.setSpacing(8)
        
        def create_btn(icon_name, text_key, icon_color):
            btn = QPushButton(f" {lm(text_key)}")
            # 安全なアイコン生成 (qtawesomeのエラー回避)
            try:
                btn.setIcon(qta.icon(icon_name, color=icon_color))
            except:
                pass
            btn.setIconSize(QSize(20, 20))
            btn.setCursor(Qt.PointingHandCursor)
            btn.setMinimumHeight(40) 
            return btn
        
        self.rect_button = create_btn('fa5s.vector-square', "rec_area_dialog_rect_button", "#ff9800")
        self.rect_button.clicked.connect(lambda: self.on_select("rectangle"))
        button_layout.addWidget(self.rect_button)
        
        self.window_button = create_btn('fa5s.window-maximize', "rec_area_dialog_window_button", "#2196f3")
        self.window_button.clicked.connect(lambda: self.on_select("window"))
        button_layout.addWidget(self.window_button)

        self.fullscreen_button = create_btn('fa5s.expand', "rec_area_dialog_fullscreen_button", "#4caf50")
        self.fullscreen_button.clicked.connect(lambda: self.on_select("fullscreen"))
        button_layout.addWidget(self.fullscreen_button)

        layout.addLayout(button_layout)
        
        hint_label = QLabel("(ESC to Cancel)")
        hint_label.setStyleSheet("color: #90a4ae; font-size: 10px; font-weight: normal; border: none;")
        hint_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(hint_label)
        
        self.setFixedSize(220, 260) 
        
    def on_select(self, method):
        self.selectionMade.emit(method)
        self.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.reject()


class FolderSettingsDialog(QDialog):
    def __init__(self, folder_name, current_settings, locale_manager, is_root=True, parent=None):
        super().__init__(parent)
        self.locale_manager = locale_manager
        lm = self.locale_manager.tr
        
        self.setWindowTitle(lm("folder_dialog_title", folder_name))
        
        self.setStyleSheet("""
            QDialog {
                background-color: #ffffff;
            }
            QGroupBox {
                border: 1px solid #cfd8dc;
                border-radius: 6px;
                margin-top: 1.2em;
                padding-top: 10px;
                background-color: #fafafa;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                font-weight: bold;
                color: #37474f;
            }
            QLabel, QRadioButton {
                color: #37474f;
            }
            QSpinBox {
                min-height: 24px;
            }
        """)
        
        self.layout = QVBoxLayout(self)
        self.layout.setSpacing(15)
        self.layout.setContentsMargins(20, 20, 20, 20)

        h_container = QHBoxLayout()
        h_container.setSpacing(20)

        # --- モード選択 ---
        mode_box = QGroupBox(lm("folder_dialog_group_mode"))
        mode_layout = QVBoxLayout()
        mode_layout.setSpacing(8)
        
        self.radio_normal = QRadioButton(lm("folder_dialog_radio_normal"))
        self.radio_excluded = QRadioButton(lm("folder_dialog_radio_excluded"))
        self.radio_cooldown = QRadioButton(lm("folder_dialog_radio_cooldown"))
        self.radio_priority_image = QRadioButton(lm("folder_dialog_radio_priority_image"))
        self.radio_priority_timer = QRadioButton(lm("folder_dialog_radio_priority_timer"))
        self.radio_priority_sequence = QRadioButton(lm("folder_dialog_radio_priority_sequence"))
        
        if not is_root:
            self.radio_priority_timer.setEnabled(False)
            if current_settings.get('mode') == 'priority_timer':
                current_settings['mode'] = 'normal'
        
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.radio_normal, 0)
        self.mode_group.addButton(self.radio_excluded, 1)
        self.mode_group.addButton(self.radio_cooldown, 2)
        self.mode_group.addButton(self.radio_priority_image, 3)
        self.mode_group.addButton(self.radio_priority_timer, 4)
        self.mode_group.addButton(self.radio_priority_sequence, 5)
        
        mode_layout.addWidget(self.radio_normal)
        mode_layout.addWidget(self.radio_excluded)
        mode_layout.addWidget(self.radio_cooldown)
        mode_layout.addWidget(self.radio_priority_image)
        mode_layout.addWidget(self.radio_priority_timer)
        mode_layout.addWidget(self.radio_priority_sequence)
        mode_layout.addStretch()
        
        mode_box.setLayout(mode_layout)
        h_container.addWidget(mode_box, 1)
        
        # --- 詳細設定 ---
        details_layout = QVBoxLayout()
        details_layout.setSpacing(12)

        def create_spin_row(label_text, suffix, box_widget):
            row = QHBoxLayout()
            lbl = QLabel(label_text)
            row.addWidget(lbl)
            spin = QSpinBox()
            spin.setRange(1, 3600)
            spin.setSuffix(suffix)
            spin.setFixedWidth(100)
            row.addWidget(spin)
            row.addStretch()
            box_widget.setLayout(row)
            return spin

        # クールダウン
        self.cooldown_box = QGroupBox(lm("folder_dialog_group_cooldown"))
        self.cooldown_time_spin = create_spin_row(lm("folder_dialog_cooldown_time"), lm("folder_dialog_suffix_seconds"), self.cooldown_box)
        details_layout.addWidget(self.cooldown_box)

        # 画像優先
        self.image_priority_box = QGroupBox(lm("folder_dialog_group_image"))
        self.priority_image_timeout_spin = create_spin_row(lm("folder_dialog_image_timeout"), lm("folder_dialog_suffix_seconds"), self.image_priority_box)
        details_layout.addWidget(self.image_priority_box)

        # タイマー優先
        self.timer_priority_box = QGroupBox(lm("folder_dialog_group_timer"))
        t_layout = QGridLayout()
        t_layout.setVerticalSpacing(10)
        t_layout.addWidget(QLabel(lm("folder_dialog_timer_interval")), 0, 0)
        self.interval_spin = QSpinBox(); self.interval_spin.setRange(1, 999); self.interval_spin.setSuffix(lm("folder_dialog_suffix_minutes_interval"))
        self.interval_spin.setFixedWidth(100)
        t_layout.addWidget(self.interval_spin, 0, 1)
        t_layout.addWidget(QLabel(lm("folder_dialog_timer_timeout")), 1, 0)
        self.timeout_spin = QSpinBox(); self.timeout_spin.setRange(1, 999); self.timeout_spin.setSuffix(lm("folder_dialog_suffix_minutes_timeout"))
        self.timeout_spin.setFixedWidth(100)
        t_layout.addWidget(self.timeout_spin, 1, 1)
        t_layout.setColumnStretch(2, 1) 
        self.timer_priority_box.setLayout(t_layout)
        details_layout.addWidget(self.timer_priority_box)

        # 順序優先
        self.sequence_priority_box = QGroupBox(lm("folder_dialog_group_sequence"))
        self.sequence_interval_spin = create_spin_row(lm("folder_dialog_sequence_interval"), lm("folder_dialog_suffix_seconds"), self.sequence_priority_box)
        details_layout.addWidget(self.sequence_priority_box)

        details_layout.addStretch()
        h_container.addLayout(details_layout, 2)
        self.layout.addLayout(h_container)

        self.radio_cooldown.toggled.connect(self.cooldown_box.setEnabled)
        self.radio_priority_image.toggled.connect(self.image_priority_box.setEnabled)
        self.radio_priority_timer.toggled.connect(self.timer_priority_box.setEnabled)
        self.radio_priority_sequence.toggled.connect(self.sequence_priority_box.setEnabled)
        
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.setStyleSheet("QPushButton { min-width: 80px; padding: 6px; }")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.layout.addWidget(self.buttons)

        self.load_settings(current_settings)

    def load_settings(self, settings):
        mode = settings.get('mode', 'normal')
        if mode == 'excluded': self.radio_excluded.setChecked(True)
        elif mode == 'cooldown': self.radio_cooldown.setChecked(True)
        elif mode == 'priority_image': self.radio_priority_image.setChecked(True)
        elif mode == 'priority_timer': self.radio_priority_timer.setChecked(True)
        elif mode == 'priority_sequence': self.radio_priority_sequence.setChecked(True)
        else: self.radio_normal.setChecked(True)
        
        self.cooldown_time_spin.setValue(settings.get('cooldown_time', 30))
        self.priority_image_timeout_spin.setValue(settings.get('priority_image_timeout', 10))
        self.interval_spin.setValue(settings.get('priority_interval', 10))
        self.timeout_spin.setValue(settings.get('priority_timeout', 5))
        self.sequence_interval_spin.setValue(settings.get('sequence_interval', 3)) 
        
        self.cooldown_box.setEnabled(mode == 'cooldown')
        self.image_priority_box.setEnabled(mode == 'priority_image')
        self.timer_priority_box.setEnabled(mode == 'priority_timer')
        self.sequence_priority_box.setEnabled(mode == 'priority_sequence')

    def get_settings(self):
        mode_id = self.mode_group.checkedId()
        mode = 'normal'
        if mode_id == 1: mode = 'excluded'
        elif mode_id == 2: mode = 'cooldown'
        elif mode_id == 3: mode = 'priority_image'
        elif mode_id == 4: mode = 'priority_timer'
        elif mode_id == 5: mode = 'priority_sequence'
            
        return {
            'mode': mode,
            'cooldown_time': self.cooldown_time_spin.value(),
            'priority_image_timeout': self.priority_image_timeout_spin.value(),
            'priority_interval': self.interval_spin.value(),
            'priority_timeout': self.timeout_spin.value(),
            'sequence_interval': self.sequence_interval_spin.value()
        }


class InitializationDialog(QDialog):
    def __init__(self, core_engine, logger, locale_manager, parent=None):
        super().__init__(parent)
        self.core_engine = core_engine
        self.logger = logger
        self.locale_manager = locale_manager
        lm = self.locale_manager.tr
        
        self.setWindowTitle(lm("init_dialog_title"))
        self.setModal(True)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        layout = QVBoxLayout(self)
        label = QLabel(lm("init_dialog_text"))
        label.setStyleSheet("color: white; font-size: 14px; font-weight: bold; background-color: transparent;")
        layout.addWidget(label, 0, Qt.AlignmentFlag.AlignCenter)
        
        self.setStyleSheet("background-color: rgba(0, 0, 0, 180); border-radius: 10px; padding: 20px;")
        
        QTimer.singleShot(50, self.apply_workaround_and_close)

    def apply_workaround_and_close(self):
        if sys.platform == 'win32':
            QTimer.singleShot(50, self.accept)
            return

        try:
            ui_manager = self.parent()
            if not ui_manager:
                self.logger.log("log_linux_workaround_error_manager")
                QTimer.singleShot(50, self.accept)
                return

            opencl_checkbox = ui_manager.app_settings_widgets.get('use_opencl')
            if not opencl_checkbox or not opencl_checkbox.isEnabled():
                self.logger.log("log_linux_workaround_skip")
                QTimer.singleShot(50, self.accept)
                return

            self.logger.log("log_linux_workaround_start")
            original_state_checked = opencl_checkbox.isChecked()
            opencl_checkbox.setChecked(not original_state_checked)
            QApplication.processEvents()
            opencl_checkbox.setChecked(original_state_checked)
            QApplication.processEvents()

            lm = self.locale_manager.tr
            status_key = "log_linux_workaround_status_enabled" if original_state_checked else "log_linux_workaround_status_disabled"
            final_state = lm(status_key)
            self.logger.log("log_linux_workaround_complete", final_state)

        except Exception as e:
            self.logger.log("log_linux_workaround_error", str(e))
        
        QTimer.singleShot(250, self.accept)
