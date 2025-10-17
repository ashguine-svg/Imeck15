# preview_mode_manager.py

from PySide6.QtCore import QObject, Signal

class PreviewModeManager(QObject):
    """プレビューの描画モードと関連UIを一元管理するクラス"""
    modeChanged = Signal(str)

    def __init__(self, roi_button, point_cb, range_cb, random_cb, parent=None):
        super().__init__(parent)
        self.roi_button = roi_button
        self.point_cb = point_cb
        self.range_cb = range_cb
        self.random_cb = random_cb
        self.current_mode = None

        self.roi_button.toggled.connect(self._on_ui_state_changed)
        self.point_cb.toggled.connect(self._on_ui_state_changed)
        self.range_cb.toggled.connect(self._on_ui_state_changed)

    def _on_ui_state_changed(self):
        """UI部品の状態変化を検知し、あるべきモードを決定して設定するメソッド"""
        new_mode = None
        if self.roi_button.isChecked():
            new_mode = 'roi_variable'
        elif self.point_cb.isChecked():
            new_mode = 'point'
        elif self.range_cb.isChecked():
            new_mode = 'range'
        
        self.set_mode(new_mode)

    def set_mode(self, mode):
        """単一のモード指定に基づき、UI全体の整合性を保つように更新します"""
        if self.current_mode == mode:
            return

        self.current_mode = mode

        self.roi_button.blockSignals(True)
        self.point_cb.blockSignals(True)
        self.range_cb.blockSignals(True)
        self.random_cb.blockSignals(True)

        self.point_cb.setChecked(mode == 'point')
        self.range_cb.setChecked(mode == 'range')

        is_range_mode = (mode == 'range')
        self.random_cb.setEnabled(is_range_mode)
        if not is_range_mode:
            self.random_cb.setChecked(False)

        if mode == 'roi_variable':
            self.roi_button.setChecked(True)
            self.roi_button.setText("ROI設定中 (再押下で終了)")
            self.point_cb.setEnabled(False)
            self.range_cb.setEnabled(False)
            self.random_cb.setEnabled(False)
        else:
            self.roi_button.setChecked(False)
            self.roi_button.setText("ROI範囲設定")
            self.point_cb.setEnabled(True)
            self.range_cb.setEnabled(True)

        self.roi_button.blockSignals(False)
        self.point_cb.blockSignals(False)
        self.range_cb.blockSignals(False)
        self.random_cb.blockSignals(False)

        self.modeChanged.emit(self.current_mode)
    
    def sync_from_settings_data(self, settings_data):
        """外部データからUI状態を同期します"""
        if self.roi_button.isChecked():
            self.roi_button.setChecked(False)
        else:
            self._on_ui_state_changed()
