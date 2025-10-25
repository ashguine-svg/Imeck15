# preview_mode_manager.py
# ★★★ 修正: UIチェックボックスの状態を直接変更しないように修正 ★★★
# ★★★ 修正: _set_drawing_mode_and_update_ui メソッドを追加・役割分担 ★★★
# ★★★ 修正: _on_ui_state_changed のロジックを明確化 ★★★
# ★★★ 修正: _update_ui_elements で sender を考慮した排他制御を追加 ★★★

from PySide6.QtCore import QObject, Signal

class PreviewModeManager(QObject):
    """プレビューの描画モードと関連UIを一元管理するクラス"""
    modeChanged = Signal(str) # 描画モードの変更を通知するシグナル

    def __init__(self, roi_button, point_cb, range_cb, random_cb, locale_manager, parent=None):
        super().__init__(parent)
        self.roi_button = roi_button
        self.point_cb = point_cb
        self.range_cb = range_cb
        self.random_cb = random_cb
        self.locale_manager = locale_manager
        self.current_mode = None # 現在の描画モード (point, range, roi_variable, None)

        # UI要素の状態変化を検知するシグナルを接続
        self.roi_button.toggled.connect(self._on_ui_state_changed)
        self.point_cb.toggled.connect(self._on_ui_state_changed)
        self.range_cb.toggled.connect(self._on_ui_state_changed)
        # random_cb は range_cb の状態に依存するため、_update_ui_elements で制御
        self.range_cb.toggled.connect(self._update_ui_elements) # range_cb の状態が変わったら random_cb の有効状態も更新

    def _on_ui_state_changed(self, checked):
        """UI要素(ROIボタン, 点/範囲チェックボックス)の状態変化に応じて描画モードを決定・設定"""
        sender = self.sender()
        new_mode = None

        # チェックが入った場合、新しいモードを決定
        if checked:
            if sender == self.roi_button:
                new_mode = 'roi_variable'
            elif sender == self.point_cb:
                new_mode = 'point'
            elif sender == self.range_cb:
                new_mode = 'range'
        # チェックが外れた場合
        else:
            # 現在アクティブなモードに対応するUI要素のチェックが外されたらモード解除
            if (sender == self.roi_button and self.current_mode == 'roi_variable') or \
               (sender == self.point_cb and self.current_mode == 'point') or \
               (sender == self.range_cb and self.current_mode == 'range'):
                new_mode = None # モード解除を示す

        # モードが変化する場合のみ更新処理を実行
        # (new_modeがNoneでも、current_modeがNoneでなければ変化とみなす)
        if new_mode != self.current_mode:
            self._set_drawing_mode_and_update_ui(new_mode)
        # モードが変わらない場合でもUIの状態は更新する (例: rangeチェックONのままrandomチェックON/OFF)
        elif sender == self.random_cb:
            self._update_ui_elements()


    def _set_drawing_mode_and_update_ui(self, mode):
        """描画モードを設定し、関連UIの有効/無効状態とROIボタン表示を更新"""
        # モードが実際に変更された場合のみ処理
        if self.current_mode == mode:
            self._update_ui_elements() # UIの有効/無効状態だけは更新する
            return

        lm = self.locale_manager.tr
        self.current_mode = mode

        # --- ROIボタンの表示更新 ---
        is_roi_mode = (mode == 'roi_variable')
        self.roi_button.blockSignals(True) # 再帰的なシグナル発生を防ぐ
        self.roi_button.setChecked(is_roi_mode)
        self.roi_button.setText(lm("item_setting_roi_button_active") if is_roi_mode else lm("item_setting_roi_button"))
        self.roi_button.blockSignals(False)

        # --- UI要素の有効/無効状態と排他制御 ---
        self._update_ui_elements()

        # --- 描画モード変更を通知 ---
        self.modeChanged.emit(self.current_mode) # InteractivePreviewLabel へ通知

    def _update_ui_elements(self):
        """現在の描画モードに基づいてUI要素の有効/無効状態を更新し、排他制御を行う"""
        is_roi_mode = (self.current_mode == 'roi_variable')
        is_point_mode = (self.current_mode == 'point')
        is_range_mode = (self.current_mode == 'range')

        # シグナルをブロックして意図しない連鎖を防ぐ
        # ※注意※ ここでチェックボックスのsetChecked()は呼ばない！
        self.point_cb.blockSignals(True)
        self.range_cb.blockSignals(True)
        self.random_cb.blockSignals(True)
        # ROIボタンのブロック解除は _set_drawing_mode_and_update_ui で行うため不要

        # --- 有効/無効状態の設定 ---
        # ROIモード中はクリック設定チェックボックスを無効化
        self.point_cb.setEnabled(not is_roi_mode)
        self.range_cb.setEnabled(not is_roi_mode)
        # ランダムチェックボックスは、ROIモードでなく、かつ範囲クリックが有効な場合のみ有効
        # ※ isChecked() で現在のUIの状態を直接参照する
        self.random_cb.setEnabled(not is_roi_mode and self.range_cb.isChecked())

        # --- 排他制御 (他の *描画モードに対応する* UI要素のチェックを外す) ---
        # ユーザー操作の起点となったウィジェット(sender)は変更しない
        sender = self.sender()

        if is_roi_mode:
             # ROIモードが有効になった -> Point/Range のチェックを外す
             # (ROIボタンがsenderなので、point/rangeは必ずチェックを外して良い)
            self.point_cb.setChecked(False)
            self.range_cb.setChecked(False)
            self.random_cb.setChecked(False) # range依存なのでこれも外す
        elif is_point_mode:
            # Pointモードが有効になった -> Range のチェックを外す, ROIボタンのチェックを外す
            # (point_cb が sender)
            if sender != self.range_cb: self.range_cb.setChecked(False)
            if sender != self.random_cb: self.random_cb.setChecked(False) # range依存
            if sender != self.roi_button: self.roi_button.setChecked(False) # ROIボタンも外す
        elif is_range_mode:
            # Rangeモードが有効になった -> Point のチェックを外す, ROIボタンのチェックを外す
            # (range_cb が sender)
            if sender != self.point_cb: self.point_cb.setChecked(False)
            if sender != self.roi_button: self.roi_button.setChecked(False) # ROIボタンも外す
            # random_cb のチェック状態は維持 (ユーザー操作を尊重)
        else: # モード解除 (None)
            # モードが解除された場合 (対応するUIのチェックが外されたことが原因のはず)
            # 例: Pointチェックが外された -> is_point_mode=False になる
            # この場合、特に他のチェックを外す必要はない
            # ROIボタンのチェックを外す (これが原因でNoneになった場合を除く)
             if sender != self.roi_button: self.roi_button.setChecked(False)
            # random_cb は range_cb がチェックされていなければチェックを外す
             if not self.range_cb.isChecked():
                 if sender != self.random_cb: self.random_cb.setChecked(False)


        # シグナルブロック解除
        self.point_cb.blockSignals(False)
        self.range_cb.blockSignals(False)
        self.random_cb.blockSignals(False)
        # ROIボタンのブロック解除は _set_drawing_mode_and_update_ui で行う

    def sync_from_settings_data(self, settings_data):
        """外部データ (settings_data) からUI状態を同期します"""
        # 注意: このメソッドはUI要素のチェック状態は直接変更しません。
        #       チェック状態の設定は UIManager.set_settings_from_data が担当します。
        #       ここでは settings_data からあるべき描画モードを決定し、
        #       UI要素の有効/無効状態とROIボタン表示を更新します。

        new_mode = None
        if settings_data:
            # データに基づいて描画モードを決定 (range優先)
            if settings_data.get('range_click'):
                 new_mode = 'range'
            elif settings_data.get('point_click'):
                new_mode = 'point'
            # ROIモード('roi_variable')はUIボタンの状態に依存するため、データからは決定しない

        # 描画モードを設定し、UI有効/無効とROIボタン表示を更新
        # ※ この中で _update_ui_elements が呼ばれ、排他制御は行われない
        self._set_drawing_mode_and_update_ui(new_mode)

        # random_cb の有効状態は _update_ui_elements で設定されるが、
        # range_cb のチェック状態に依存するため、ここで再確認する
        # (set_settings_from_data で range_cb の状態が設定された後で呼ばれる想定)
        is_range_active = settings_data.get('range_click', False) if settings_data else False
        is_roi_active = self.roi_button.isChecked() # 現在のUI状態を確認
        self.random_cb.setEnabled(not is_roi_active and is_range_active)
