# preview_mode_manager.py (OCR設定対応版)

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QPixmap, QImage
import cv2
import numpy as np

class PreviewModeManager(QObject):
    """プレビュー描画と状態管理を一元化するクラス"""
    settings_changed_externally = Signal(dict) # UI更新用 (チェックボックス等)
    previewDataApplied = Signal(dict) # 保存要求用

    def __init__(self, preview_label,
                 roi_button, point_cb, range_cb, random_cb,
                 roi_enabled_cb, roi_mode_fixed, roi_mode_variable,
                 locale_manager, backup_click_cb=None, right_click_cb=None, parent=None):
        super().__init__(parent)

        # --- UI要素への参照 ---
        self.preview_label = preview_label
        self.roi_button = roi_button
        self.point_cb = point_cb
        self.range_cb = range_cb
        self.random_cb = random_cb
        self.backup_click_cb = backup_click_cb
        self.roi_enabled_cb = roi_enabled_cb
        self.roi_mode_fixed = roi_mode_fixed
        self.roi_mode_variable = roi_mode_variable
        self.right_click_cb = right_click_cb
        self.locale_manager = locale_manager
        self.current_mode = None

        # --- 内部設定状態 ---
        self.settings = {
            'point_click': True, 'range_click': False, 'random_click': False,
            'backup_click': False,
            'click_position': None, 'click_rect': None,
            'roi_enabled': False, 'roi_mode': 'fixed',
            'roi_rect': None, 'roi_rect_variable': None,
            'right_click': False,
            'ocr_settings': None # ★ OCR設定を追加
        }

        # プレビューラベルのシグナルを内部で接続
        self.preview_label.settingChanged.connect(self.handle_preview_data)
        self.preview_label.roiSettingChanged.connect(self.handle_preview_data)

    # --- Public Methods ---

    def update_preview(self, cv_image_or_pixmap: np.ndarray | QPixmap, settings_data: dict = None, reset_zoom: bool = True):
        """画像(np.ndarray)またはPixMapと設定データを受け取り、プレビュー表示と内部状態を更新"""

        # 1. 内部設定を更新
        self._load_settings_internal(settings_data)

        # 読み込んだ設定をUI(チェックボックスなど)に反映させるためシグナルを発行
        self.settings_changed_externally.emit(self.settings.copy())

        # 2. プレビュー画像を設定
        pixmap = self._convert_cv_to_pixmap(cv_image_or_pixmap)
        self.preview_label.set_pixmap(pixmap if pixmap else None, reset_zoom)

        # 3. プレビュー描画データを設定
        self.preview_label.set_drawing_data(self.settings if settings_data else None)

        # 4. 適切な描画モードを設定
        self._determine_and_set_drawing_mode()

        # 5. UI要素の有効/無効状態を更新
        self._update_enabled_state()


    def get_settings(self):
        """現在の内部設定を返す"""
        return self.settings.copy()

    def handle_ui_toggle(self, source_widget, checked):
        """UIからのトグル操作を処理し、状態を同期"""
        widget_name = source_widget.objectName() if hasattr(source_widget, 'objectName') else str(source_widget)

        setting_key = None
        needs_排他 = False
        value_to_set = checked

        if source_widget == self.point_cb:
            setting_key = 'point_click'; needs_排他 = True
        elif source_widget == self.range_cb:
            setting_key = 'range_click'; needs_排他 = True
        elif source_widget == self.random_cb:
            setting_key = 'random_click'
        elif self.backup_click_cb is not None and source_widget == self.backup_click_cb:
            setting_key = 'backup_click'
        elif self.right_click_cb is not None and source_widget == self.right_click_cb:
            setting_key = 'right_click'
        elif source_widget == self.roi_enabled_cb:
            setting_key = 'roi_enabled'
        elif source_widget == self.roi_mode_fixed and checked:
            setting_key = 'roi_mode'; value_to_set = 'fixed'
        elif source_widget == self.roi_mode_variable and checked:
            setting_key = 'roi_mode'; value_to_set = 'variable'

        if setting_key:
            previous_value = self.settings.get(setting_key)
            # 内部状態更新
            self.settings[setting_key] = value_to_set

            # 排他制御
            if needs_排他:
                if setting_key == 'point_click' and checked:
                    self.settings['range_click'] = False; self.settings['random_click'] = False
                    self.settings['click_rect'] = None
                elif setting_key == 'range_click':
                    if checked: self.settings['point_click'] = False; self.settings['click_position'] = None
                    else: self.settings['random_click'] = False
            if setting_key == 'random_click' and checked and not self.settings['range_click']:
                self.settings['random_click'] = False

            # 変更があればUI同期シグナル発行
            if self.settings[setting_key] != previous_value or needs_排他:
                self.settings_changed_externally.emit(self.settings.copy())
                # UIトグルの変更も保存対象（次フレームのプレビュー更新で上書きされるのを防ぐ）
                self.previewDataApplied.emit(self.settings.copy())

            # UI操作後にも描画モードとUI状態を同期
            self._determine_and_set_drawing_mode()
            self._update_enabled_state()


    def handle_preview_data(self, data: dict):
        """プレビューからの座標データを処理し、状態を同期"""
        updated = False
        if 'click_position' in data:
            self.settings['point_click'] = True; self.settings['range_click'] = False
            self.settings['random_click'] = False; self.settings['click_position'] = data['click_position']
            self.settings['click_rect'] = None; updated = True
        elif 'click_rect' in data:
            self.settings['point_click'] = False; self.settings['range_click'] = True
            self.settings['click_position'] = None; self.settings['click_rect'] = data['click_rect']
            updated = True
        elif 'roi_rect_variable' in data:
             self.settings['roi_rect_variable'] = data['roi_rect_variable']; updated = True

        if updated:
            # 保存要求通知
            self.previewDataApplied.emit(self.settings.copy())
            self._update_enabled_state()


    def sync_from_external(self, is_folder_or_no_data):
        """フォルダ選択時などにモード解除とUI状態更新"""
        self._set_drawing_mode(None)

        if is_folder_or_no_data:
            widgets_to_disable = [
                self.roi_button, self.point_cb, self.range_cb, self.random_cb,
                self.roi_enabled_cb, self.roi_mode_fixed, self.roi_mode_variable
            ]
            if self.backup_click_cb is not None:
                widgets_to_disable.append(self.backup_click_cb)
            if self.right_click_cb is not None:
                widgets_to_disable.append(self.right_click_cb)
            self._block_all_signals(True)
            try:
                for w in widgets_to_disable: w.setEnabled(False)
            finally:
                self._block_all_signals(False)


    # --- Internal Methods ---

    def _load_settings_internal(self, loaded_settings):
        """内部状態を設定データで更新"""
        if loaded_settings:
            self.settings['point_click'] = loaded_settings.get('point_click', True)
            self.settings['range_click'] = loaded_settings.get('range_click', False)
            self.settings['random_click'] = loaded_settings.get('random_click', False) and self.settings['range_click']
            self.settings['backup_click'] = bool(loaded_settings.get('backup_click', False))
            self.settings['click_position'] = loaded_settings.get('click_position')
            self.settings['click_rect'] = loaded_settings.get('click_rect')
            self.settings['roi_enabled'] = loaded_settings.get('roi_enabled', False)
            self.settings['roi_mode'] = loaded_settings.get('roi_mode', 'fixed')
            self.settings['roi_rect'] = loaded_settings.get('roi_rect')
            self.settings['roi_rect_variable'] = loaded_settings.get('roi_rect_variable')
            self.settings['right_click'] = bool(loaded_settings.get('right_click', False))
            # ★ OCR設定をロード
            self.settings['ocr_settings'] = loaded_settings.get('ocr_settings')
        else:
            self.settings = { # Reset to defaults
                'point_click': True, 'range_click': False, 'random_click': False,
                'backup_click': False,
                'click_position': None, 'click_rect': None,
                'roi_enabled': False, 'roi_mode': 'fixed',
                'roi_rect': None, 'roi_rect_variable': None,
                'right_click': False,
                'ocr_settings': None
            }


    def _determine_and_set_drawing_mode(self):
        """現在の内部設定とUI状態に基づいて描画モードを決定し、設定する"""
        target_mode = None
        if self.roi_button.isChecked():
            needs_emit = False
            if not self.settings.get('roi_enabled'):
                self.settings['roi_enabled'] = True; needs_emit = True
            if self.settings.get('roi_mode') != 'variable':
                self.settings['roi_mode'] = 'variable'; needs_emit = True
            if needs_emit:
                self.settings_changed_externally.emit(self.settings.copy())
            target_mode = 'roi_variable'
        elif self.settings.get('point_click'):
            target_mode = 'point'
        elif self.settings.get('range_click'):
            target_mode = 'range'

        self._set_drawing_mode(target_mode)


    def _drawing_mode_button_toggled(self, checked):
        """ROIボタン操作時に描画モードとUI状態を同期"""
        self._determine_and_set_drawing_mode()


    def _set_drawing_mode(self, mode):
        """描画モードを設定し、変更があればプレビューに通知"""
        if self.current_mode == mode:
            self.preview_label.set_drawing_mode(self.current_mode)
            self._update_enabled_state()
            return

        self.current_mode = mode
        lm = self.locale_manager.tr

        self._block_all_signals(True)
        try:
            if self.roi_button.isChecked() != (mode == 'roi_variable'):
                self.roi_button.setChecked(mode == 'roi_variable')
            
            is_roi_drawing = (self.current_mode == 'roi_variable')
            self.roi_button.setText(lm("item_setting_roi_button_active") if is_roi_drawing else lm("item_setting_roi_button"))

        finally:
            self._block_all_signals(False)

        self._update_enabled_state()
        self.preview_label.set_drawing_mode(self.current_mode)

    def _update_enabled_state(self):
        """現在の内部設定と描画モードに基づいて、関連UI要素の有効/無効状態を更新する"""
        is_roi_enabled = self.settings.get('roi_enabled', False)
        is_range_click = self.settings.get('range_click', False)
        is_roi_variable_mode = self.settings.get('roi_mode') == 'variable'
        is_roi_drawing_mode = (self.current_mode == 'roi_variable')

        has_click_point = self.settings.get('click_position') is not None
        has_click_range = self.settings.get('click_rect') is not None
        can_enable_roi = (has_click_point or has_click_range)

        self.point_cb.setEnabled(not is_roi_drawing_mode)
        self.range_cb.setEnabled(not is_roi_drawing_mode)
        self.random_cb.setEnabled(not is_roi_drawing_mode and is_range_click)
        # フォルダ選択時に sync_from_external() で明示的に disabled されるため、
        # 通常状態に戻ったときにここで確実に復帰させる
        if self.backup_click_cb is not None:
            self.backup_click_cb.setEnabled(not is_roi_drawing_mode)
        if self.right_click_cb is not None:
            self.right_click_cb.setEnabled(not is_roi_drawing_mode)
        self.roi_mode_fixed.setEnabled(not is_roi_drawing_mode and is_roi_enabled)
        self.roi_mode_variable.setEnabled(not is_roi_drawing_mode and is_roi_enabled)
        self.roi_button.setEnabled((is_roi_enabled and is_roi_variable_mode) or is_roi_drawing_mode)
        self.roi_enabled_cb.setEnabled(not is_roi_drawing_mode and can_enable_roi)


    def _block_all_signals(self, block):
        self.roi_button.blockSignals(block)
        self.random_cb.blockSignals(block)
        self.roi_enabled_cb.blockSignals(block)
        self.roi_mode_fixed.blockSignals(block)
        self.roi_mode_variable.blockSignals(block)
        self.point_cb.blockSignals(block)
        self.range_cb.blockSignals(block)
        if self.backup_click_cb is not None:
            self.backup_click_cb.blockSignals(block)
        if self.right_click_cb is not None:
            self.right_click_cb.blockSignals(block)

    def _convert_cv_to_pixmap(self, cv_image_or_pixmap: np.ndarray | QPixmap) -> QPixmap | None:
        """OpenCV画像(BGR)またはQPixmapをQPixmap(RGB)に変換"""
        if isinstance(cv_image_or_pixmap, QPixmap):
            if cv_image_or_pixmap.isNull():
                return None
            return cv_image_or_pixmap
        
        if cv_image_or_pixmap is None or cv_image_or_pixmap.size == 0:
            return None
        try:
            rgb_image = cv2.cvtColor(cv_image_or_pixmap, cv2.COLOR_BGR2RGB) 
            h, w, ch = rgb_image.shape
            bytes_per_line = ch * w
            q_image = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
            return QPixmap.fromImage(q_image)
        except Exception as e:
            print(f"[ERROR] PreviewModeManager._convert_cv_to_pixmap: {e}")
            return None
