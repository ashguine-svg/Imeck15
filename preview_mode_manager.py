# preview_mode_manager.py
# ★★★ 排他処理とUI同期ロジックを修正 ★★★

from PySide6.QtCore import QObject, Signal

class PreviewModeManager(QObject):
    """プレビューの描画モードと関連UIを一元管理するクラス"""
    modeChanged = Signal(str)

    def __init__(self, roi_button, point_cb, range_cb, random_cb, locale_manager, parent=None):
        super().__init__(parent)
        self.roi_button = roi_button
        self.point_cb = point_cb
        self.range_cb = range_cb
        self.random_cb = random_cb
        self.locale_manager = locale_manager # LocaleManagerインスタンスを保持
        self.current_mode = None

        # シグナル接続: 各ボタン/チェックボックスの状態変化を検知
        self.roi_button.toggled.connect(self._on_ui_state_changed)
        self.point_cb.toggled.connect(self._on_ui_state_changed)
        self.range_cb.toggled.connect(self._on_ui_state_changed)
        # ランダムクリックは set_mode 内で制御されるため、ここでの接続は不要 (依存関係のため)
        # self.random_cb.toggled.connect(...)

    def _on_ui_state_changed(self, checked):
        """UI部品の状態変化を検知し、あるべきモードを決定して設定するメソッド"""
        sender = self.sender() # シグナルを発行したウィジェットを取得
        new_mode = None

        # どのウィジェットがチェックされたかに基づいて新しいモードを決定
        # チェックが外された場合は基本的に何もしない (set_mode内で他の要素が制御)
        if checked:
            if sender == self.roi_button:
                new_mode = 'roi_variable'
            elif sender == self.point_cb:
                new_mode = 'point'
            elif sender == self.range_cb:
                new_mode = 'range'
            # ランダムクリックチェックボックス自体の変更はここではモードに影響しない
            # elif sender == self.random_cb:
            #     pass

            # 新しいモードが決定された場合のみ set_mode を呼び出す
            if new_mode is not None:
                self.set_mode(new_mode)
        else:
            # チェックが外された場合
            # もし現在アクティブなモードに対応するウィジェットのチェックが外されたら、モードを解除(None)
            if (sender == self.roi_button and self.current_mode == 'roi_variable') or \
               (sender == self.point_cb and self.current_mode == 'point') or \
               (sender == self.range_cb and self.current_mode == 'range'):
                self.set_mode(None) # モード解除

    def set_mode(self, mode):
        """単一のモード指定に基づき、UI全体の整合性を保つように更新します"""
        # モードが実際に変更された場合のみ処理を実行
        if self.current_mode == mode:
            return

        self.current_mode = mode

        # UI更新中の意図しないシグナル連鎖を防ぐ
        self.roi_button.blockSignals(True)
        self.point_cb.blockSignals(True)
        self.range_cb.blockSignals(True)
        self.random_cb.blockSignals(True)

        lm = self.locale_manager.tr

        # 指定されたモードに基づいて各UI要素の状態を確実に設定 (排他制御)
        if mode == 'roi_variable':
            self.roi_button.setChecked(True)
            self.roi_button.setText(lm("item_setting_roi_button_active"))
            # ROIモード時は他のクリック設定を解除・無効化
            self.point_cb.setChecked(False)
            self.range_cb.setChecked(False)
            self.random_cb.setChecked(False)
            self.point_cb.setEnabled(False)
            self.range_cb.setEnabled(False)
            self.random_cb.setEnabled(False)
        elif mode == 'point':
            self.roi_button.setChecked(False) # ROIボタンは解除
            self.roi_button.setText(lm("item_setting_roi_button"))
            self.point_cb.setChecked(True)    # ポイントクリックをチェック
            self.range_cb.setChecked(False)   # 範囲クリックは解除
            self.random_cb.setChecked(False)  # ランダムも解除
            self.point_cb.setEnabled(True)
            self.range_cb.setEnabled(True)
            self.random_cb.setEnabled(False) # ランダムは範囲クリック時のみ
        elif mode == 'range':
            self.roi_button.setChecked(False) # ROIボタンは解除
            self.roi_button.setText(lm("item_setting_roi_button"))
            self.point_cb.setChecked(False)   # ポイントクリックは解除
            self.range_cb.setChecked(True)    # 範囲クリックをチェック
            # random_cb のチェック状態は変更しない (UIでユーザーが設定した状態を維持)
            self.point_cb.setEnabled(True)
            self.range_cb.setEnabled(True)
            self.random_cb.setEnabled(True)  # ランダムを有効化
        else: # mode is None (モード解除)
            self.roi_button.setChecked(False)
            self.roi_button.setText(lm("item_setting_roi_button"))
            self.point_cb.setChecked(False)
            self.range_cb.setChecked(False)
            self.random_cb.setChecked(False)
            self.point_cb.setEnabled(True)
            self.range_cb.setEnabled(True)
            self.random_cb.setEnabled(False)

        # シグナルブロックを解除
        self.roi_button.blockSignals(False)
        self.point_cb.blockSignals(False)
        self.range_cb.blockSignals(False)
        self.random_cb.blockSignals(False)

        # モード変更を通知 (InteractivePreviewLabel が受け取る)
        self.modeChanged.emit(self.current_mode)

    def sync_from_settings_data(self, settings_data):
        """外部データ (settings_data) からUI状態と描画モードを同期します"""
        new_mode = None # デフォルトモード (何も選択されていない状態)

        if settings_data:
            # 設定データに基づいて、どのクリックモードが有効かを判断
            # range_click を優先的にチェック (両方TrueになるケースはUI操作では防がれるはずだが念のため)
            if settings_data.get('range_click'):
                 new_mode = 'range'
            elif settings_data.get('point_click'):
                new_mode = 'point'

            # ★★★ ランダムクリックの状態も復元 ★★★
            # set_mode呼び出し前にチェック状態を設定しておく
            # (set_modeはrangeモードでなければrandomを無効化＆解除するので問題ない)
            self.random_cb.blockSignals(True)
            self.random_cb.setChecked(settings_data.get('random_click', False))
            self.random_cb.blockSignals(False)

        # 決定したモードに基づいて set_mode を呼び出し、UI全体の状態を更新・同期
        # これにより、roiボタンの状態や、クリック種別チェックボックスの有効/無効も設定される
        self.set_mode(new_mode)
