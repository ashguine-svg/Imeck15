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
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("認識範囲設定")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Popup)
        self.setFixedSize(200, 100)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("設定方法を選択:"))
        button_layout = QHBoxLayout()
        self.rect_button = QPushButton("四角設定")
        self.rect_button.clicked.connect(lambda: self.on_select("rectangle"))
        button_layout.addWidget(self.rect_button)
        self.window_button = QPushButton("ウィンドウ設定")
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
    def __init__(self, folder_name, current_settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"フォルダ設定: {folder_name}")
        self.layout = QVBoxLayout(self)

        mode_box = QGroupBox("フォルダの動作モード")
        mode_layout = QVBoxLayout()
        self.radio_normal = QRadioButton("通常 (監視対象)")
        self.radio_excluded = QRadioButton("検索停止 (監視対象外)")
        self.radio_priority_image = QRadioButton("画像認識型優先")
        self.radio_priority_timer = QRadioButton("タイマー付き優先")
        
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
        
        self.image_priority_box = QGroupBox("画像認識型優先 の詳細設定")
        image_priority_layout = QGridLayout()
        image_priority_layout.addWidget(QLabel("優先モードを解除する時間:"), 0, 0)
        self.priority_image_timeout_spin = QSpinBox()
        self.priority_image_timeout_spin.setRange(1, 999)
        self.priority_image_timeout_spin.setSuffix(" 秒")
        image_priority_layout.addWidget(self.priority_image_timeout_spin, 0, 1)
        self.image_priority_box.setLayout(image_priority_layout)
        self.layout.addWidget(self.image_priority_box)

        self.timer_priority_box = QGroupBox("タイマー付き優先 の詳細設定")
        timer_layout = QGridLayout()
        timer_layout.addWidget(QLabel("有効になるまでの間隔:"), 0, 0)
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 999)
        self.interval_spin.setSuffix(" 分")
        timer_layout.addWidget(self.interval_spin, 0, 1)
        
        timer_layout.addWidget(QLabel("優先モードを解除する時間:"), 1, 0)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 999)
        self.timeout_spin.setSuffix(" 分")
        timer_layout.addWidget(self.timeout_spin, 1, 1)
        self.timer_priority_box.setLayout(timer_layout)
        self.layout.addWidget(self.timer_priority_box)

        self.radio_priority_image.toggled.connect(self.image_priority_box.setEnabled)
        self.radio_priority_timer.toggled.connect(self.timer_priority_box.setEnabled)
        
        image_tooltip = (
            "<b>画像認識型優先モードの詳細:</b><br>"
            "このフォルダ内の画像が<b>1つでも画面内に見つかる</b>と、このフォルダが優先モードになります。<br>"
            "優先モードは、以下のいずれかの条件で解除されます。<br>"
            "<ul>"
            "<li>このフォルダ内の<b>すべての画像</b>が一度ずつクリックされた。</li>"
            "<li>このフォルダ内の画像が一切見つからない状態が<b>『優先モードを解除する時間』</b>を経過した。</li>"
            "</ul>"
        )
        self.radio_priority_image.setToolTip(image_tooltip)
        self.image_priority_box.setToolTip(image_tooltip)
        self.radio_priority_image.setToolTipDuration(-1)
        self.image_priority_box.setToolTipDuration(-1)

        timer_tooltip = (
            "<b>タイマー付き優先モードの詳細:</b><br>"
            "設定した<b>『有効になるまでの間隔』</b>が経過すると、このフォルダ内の画像のみを優先的に探します。<br>"
            "優先モードは、<b>『優先モードを解除する時間』</b>が経過すると解除されます。<br>"
            "このフォルダ内の画像がクリックされると、有効化タイマーはリセットされます。"
        )
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
    def __init__(self, core_engine, logger, parent=None):
        super().__init__(parent)
        self.core_engine = core_engine
        self.logger = logger
        # parentはUIManagerのインスタンスであると想定

        self.setWindowTitle("初期化中")
        self.setModal(True)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        layout = QVBoxLayout(self)
        label = QLabel("最終初期化を実行中...")
        label.setStyleSheet("color: white; font-size: 14px; font-weight: bold; background-color: transparent;")
        layout.addWidget(label, 0, Qt.AlignmentFlag.AlignCenter)
        
        self.setStyleSheet("background-color: rgba(0, 0, 0, 180); border-radius: 10px; padding: 10px;")
        
        # ダイアログが表示された直後に処理を開始するためのタイマー
        QTimer.singleShot(50, self.apply_workaround_and_close)

    def apply_workaround_and_close(self):
        """
        UI上のOpenCLチェックボックスのON/OFFをシミュレートし、ダイアログを閉じる。
        """
        if sys.platform == 'win32':
            # Windowsではこの処理は不要
            QTimer.singleShot(50, self.accept)
            return

        try:
            ui_manager = self.parent()
            if not ui_manager:
                self.logger.log("Linux UIフリーズ対策エラー: UIManagerが見つかりません。")
                QTimer.singleShot(50, self.accept)
                return

            opencl_checkbox = ui_manager.app_settings_widgets.get('use_opencl')
            if not opencl_checkbox or not opencl_checkbox.isEnabled():
                self.logger.log("Linux UIフリーズ対策: OpenCLチェックボックスが無効なためスキップします。")
                QTimer.singleShot(50, self.accept)
                return

            self.logger.log("Linux UIフリーズ対策: UI操作をシミュレートしてOpenCLを再初期化します。")
            
            # 1. ユーザーの元の設定を記憶
            original_state_checked = opencl_checkbox.isChecked()
            
            # 2. プログラムがチェックボックスの状態を反転させる（1回目のクリック）
            #    これにより、on_app_settings_changedから始まる一連のイベントがトリガーされる
            opencl_checkbox.setChecked(not original_state_checked)
            
            # 3. Qtのイベントループを強制的に処理させ、UIの変更を即座に反映させる
            QApplication.processEvents()

            # 4. プログラムがチェックボックスを元の状態に戻す（2回目のクリック）
            opencl_checkbox.setChecked(original_state_checked)
            QApplication.processEvents()

            final_state = "有効" if original_state_checked else "無効"
            self.logger.log(f"UI操作のシミュレート完了。OpenCLの状態を「{final_state}」に復元しました。")

        except Exception as e:
            self.logger.log(f"Linux UIフリーズ対策の実行中にエラーが発生しました: {e}")
        
        # 処理が一瞬で終わってもユーザーが認識できるよう、少し待ってからダイアログを閉じる
        QTimer.singleShot(250, self.accept)
