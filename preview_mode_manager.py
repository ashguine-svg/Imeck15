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

        # 関連するUI部品のトグル信号を接続
        self.roi_button.toggled.connect(self._on_ui_state_changed)
        self.point_cb.toggled.connect(self._on_ui_state_changed)
        self.range_cb.toggled.connect(self._on_ui_state_changed)
        # ★★★ 1. 'random_cb' は独立したモードではないため、ここでの接続は不要 ★★★
        # self.random_cb.toggled.connect(self._on_ui_state_changed)

    def _on_ui_state_changed(self):
        """UI部品の状態変化を検知し、あるべきモードを決定して設定するメソッド"""
        new_mode = None
        if self.roi_button.isChecked():
            new_mode = 'roi_variable'
        elif self.point_cb.isChecked():
            new_mode = 'point'
        elif self.range_cb.isChecked():
            new_mode = 'range'
        # ★★★ 2. 'random' を独立した排他モードとして扱うのをやめる ★★★
        
        self.set_mode(new_mode)

    def set_mode(self, mode):
        """単一のモード指定に基づき、UI全体の整合性を保つように更新します"""
        if self.current_mode == mode:
            return

        self.current_mode = mode

        # 信号のループを防ぐため、一時的にブロック
        self.roi_button.blockSignals(True)
        self.point_cb.blockSignals(True)
        self.range_cb.blockSignals(True)
        self.random_cb.blockSignals(True)

        # --- モードに応じたUIの状態を再構築 ---

        # 1. チェックボックスの排他制御
        self.point_cb.setChecked(mode == 'point')
        self.range_cb.setChecked(mode == 'range')

        # 2. 依存関係のあるUIの制御
        # ★★★ 3. 'random_cb' は 'range' モードの時だけ有効化する ★★★
        is_range_mode = (mode == 'range')
        self.random_cb.setEnabled(is_range_mode)
        if not is_range_mode:
            # 範囲クリックモードでなければ、ランダムクリックも強制的にOFFにする
            self.random_cb.setChecked(False)

        # 3. ROIモードのUI制御
        if mode == 'roi_variable':
            self.roi_button.setChecked(True)
            self.roi_button.setText("ROI設定中 (再押下で終了)")
            self.point_cb.setEnabled(False)
            self.range_cb.setEnabled(False)
            self.random_cb.setEnabled(False) # ROI設定中も無効化
        else:
            self.roi_button.setChecked(False)
            self.roi_button.setText("ROI範囲設定")
            self.point_cb.setEnabled(True)
            self.range_cb.setEnabled(True)
            # random_cb の有効/無効は、上記の is_range_mode の判定に任せる

        # ブロックを解除
        self.roi_button.blockSignals(False)
        self.point_cb.blockSignals(False)
        self.range_cb.blockSignals(False)
        self.random_cb.blockSignals(False)

        # 外部にモードの変更を通知
        self.modeChanged.emit(self.current_mode)
    
    def sync_from_settings_data(self, settings_data):
        """外部データからUI状態を同期します"""
        if self.roi_button.isChecked():
            self.roi_button.setChecked(False)
        else:
            self._on_ui_state_changed()
