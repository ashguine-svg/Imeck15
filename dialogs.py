# dialogs.py

import sys
from PySide6.QtWidgets import (
    QDialog, QPushButton, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QRadioButton, QButtonGroup, QGridLayout, QSpinBox, QDialogButtonBox,
    QApplication
)
from PySide6.QtGui import QCursor
from PySide6.QtCore import Qt, Signal, QTimer

class RecAreaSelectionDialog(QDialog):
    selectionMade = Signal(str)
    # ★★★ 1. __init__ に locale_manager を追加 ★★★
    def __init__(self, locale_manager, parent=None):
        super().__init__(parent)
        self.locale_manager = locale_manager
        lm = self.locale_manager.tr
        
        # ★★★ 2. UI文字列を翻訳キーに置き換え ★★★
        self.setWindowTitle(lm("rec_area_dialog_title"))
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Popup)
        self.setFixedSize(200, 100)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(lm("rec_area_dialog_prompt")))
        button_layout = QHBoxLayout()
        self.rect_button = QPushButton(lm("rec_area_dialog_rect_button"))
        self.rect_button.clicked.connect(lambda: self.on_select("rectangle"))
        button_layout.addWidget(self.rect_button)
        self.window_button = QPushButton(lm("rec_area_dialog_window_button"))
        self.window_button.clicked.connect(lambda: self.on_select("window"))
        button_layout.addWidget(self.window_button)
        layout.addLayout(button_layout)
        
    def on_select(self, method):
        self.selectionMade.emit(method)
        self.accept()
    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.reject()


class FolderSettingsDialog(QDialog):
    # ★★★ 3. __init__ に locale_manager を追加 ★★★
    def __init__(self, folder_name, current_settings, locale_manager, parent=None):
        super().__init__(parent)
        self.locale_manager = locale_manager
        lm = self.locale_manager.tr
        
        # ★★★ 4. UI文字列を翻訳キーに置き換え ★★★
        self.setWindowTitle(lm("folder_dialog_title", folder_name))
        self.layout = QVBoxLayout(self)

        mode_box = QGroupBox(lm("folder_dialog_group_mode"))
        mode_layout = QVBoxLayout()
        self.radio_normal = QRadioButton(lm("folder_dialog_radio_normal"))
        self.radio_excluded = QRadioButton(lm("folder_dialog_radio_excluded"))
        self.radio_priority_image = QRadioButton(lm("folder_dialog_radio_priority_image"))
        self.radio_priority_timer = QRadioButton(lm("folder_dialog_radio_priority_timer"))
        
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.radio_normal, 0)
        self.mode_group.addButton(self.radio_excluded, 1)
        self.mode_group.addButton(self.radio_priority_image, 2)
        self.mode_group.addButton(self.radio_priority_timer, 3)
        
        mode_layout.addWidget(self.radio_normal)
        mode_layout.addWidget(self.radio_excluded)
        mode_layout.addWidget(self.radio_priority_image)
        mode_layout.addWidget(self.radio_priority_timer)
        mode_box.setLayout(mode_layout)
        self.layout.addWidget(mode_box)
        
        self.image_priority_box = QGroupBox(lm("folder_dialog_group_image"))
        image_priority_layout = QGridLayout()
        image_priority_layout.addWidget(QLabel(lm("folder_dialog_image_timeout")), 0, 0)
        self.priority_image_timeout_spin = QSpinBox()
        self.priority_image_timeout_spin.setRange(1, 999)
        self.priority_image_timeout_spin.setSuffix(lm("folder_dialog_suffix_seconds"))
        image_priority_layout.addWidget(self.priority_image_timeout_spin, 0, 1)
        self.image_priority_box.setLayout(image_priority_layout)
        self.layout.addWidget(self.image_priority_box)

        self.timer_priority_box = QGroupBox(lm("folder_dialog_group_timer"))
        timer_layout = QGridLayout()
        timer_layout.addWidget(QLabel(lm("folder_dialog_timer_interval")), 0, 0)
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 999)
        self.interval_spin.setSuffix(lm("folder_dialog_suffix_minutes_interval"))
        timer_layout.addWidget(self.interval_spin, 0, 1)
        
        timer_layout.addWidget(QLabel(lm("folder_dialog_timer_timeout")), 1, 0)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 999)
        self.timeout_spin.setSuffix(lm("folder_dialog_suffix_minutes_timeout"))
        timer_layout.addWidget(self.timeout_spin, 1, 1)
        self.timer_priority_box.setLayout(timer_layout)
        self.layout.addWidget(self.timer_priority_box)

        self.radio_priority_image.toggled.connect(self.image_priority_box.setEnabled)
        self.radio_priority_timer.toggled.connect(self.timer_priority_box.setEnabled)
        
        image_tooltip = lm("folder_dialog_tooltip_image")
        self.radio_priority_image.setToolTip(image_tooltip)
        self.image_priority_box.setToolTip(image_tooltip)
        self.radio_priority_image.setToolTipDuration(-1)
        self.image_priority_box.setToolTipDuration(-1)

        timer_tooltip = lm("folder_dialog_tooltip_timer")
        self.radio_priority_timer.setToolTip(timer_tooltip)
        self.timer_priority_box.setToolTip(timer_tooltip)
        self.radio_priority_timer.setToolTipDuration(-1)
        self.timer_priority_box.setToolTipDuration(-1)


        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.layout.addWidget(self.buttons)

        self.load_settings(current_settings)

    def load_settings(self, settings):
        mode = settings.get('mode', 'normal')
        if mode == 'excluded':
            self.radio_excluded.setChecked(True)
        elif mode == 'priority_image':
            self.radio_priority_image.setChecked(True)
        elif mode == 'priority_timer':
            self.radio_priority_timer.setChecked(True)
        else:
            self.radio_normal.setChecked(True)
        
        self.priority_image_timeout_spin.setValue(settings.get('priority_image_timeout', 10))
        self.interval_spin.setValue(settings.get('priority_interval', 10))
        self.timeout_spin.setValue(settings.get('priority_timeout', 5))
        
        self.image_priority_box.setEnabled(mode == 'priority_image')
        self.timer_priority_box.setEnabled(mode == 'priority_timer')

    def get_settings(self):
        mode_id = self.mode_group.checkedId()
        mode = 'normal'
        if mode_id == 1:
            mode = 'excluded'
        elif mode_id == 2:
            mode = 'priority_image'
        elif mode_id == 3:
            mode = 'priority_timer'
            
        return {
            'mode': mode,
            'priority_image_timeout': self.priority_image_timeout_spin.value(),
            'priority_interval': self.interval_spin.value(),
            'priority_timeout': self.timeout_spin.value()
        }


class InitializationDialog(QDialog):
    """
    Linux環境でのUIフリーズ問題を回避するため、起動時に一時的に表示されるモーダルダイアログ。
    このダイアログの表示中に、UI操作をシミュレートしてOpenCLの再初期化を行う。
    """
    # ★★★ 5. __init__ に locale_manager を追加 ★★★
    def __init__(self, core_engine, logger, locale_manager, parent=None):
        super().__init__(parent)
        self.core_engine = core_engine
        self.logger = logger
        self.locale_manager = locale_manager
        lm = self.locale_manager.tr
        
        # ★★★ 6. UI文字列を翻訳キーに置き換え ★★★
        self.setWindowTitle(lm("init_dialog_title"))
        self.setModal(True)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        layout = QVBoxLayout(self)
        label = QLabel(lm("init_dialog_text"))
        label.setStyleSheet("color: white; font-size: 14px; font-weight: bold; background-color: transparent;")
        layout.addWidget(label, 0, Qt.AlignmentFlag.AlignCenter)
        
        self.setStyleSheet("background-color: rgba(0, 0, 0, 180); border-radius: 10px; padding: 10px;")
        
        QTimer.singleShot(50, self.apply_workaround_and_close)

    def apply_workaround_and_close(self):
        """
        UI上のOpenCLチェックボックスのON/OFFをシミュレートし、ダイアログを閉じる。
        """
        if sys.platform == 'win32':
            QTimer.singleShot(50, self.accept)
            return

        try:
            ui_manager = self.parent()
            if not ui_manager:
                # ★★★ 7. ログを翻訳キーに置き換え ★★★
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
