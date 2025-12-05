# ui_app_settings.py

import sys
import json
import cv2
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QCheckBox, QLabel,
    QDoubleSpinBox, QSpinBox, QComboBox, QScrollArea, QGridLayout,
    QSpacerItem, QSizePolicy, QTabWidget, QFrame
)
from PySide6.QtCore import QObject, Qt

try:
    from capture import DXCAM_AVAILABLE
except ImportError:
    DXCAM_AVAILABLE = False

OPENCL_AVAILABLE = False
try:
    if cv2.ocl.haveOpenCL():
        OPENCL_AVAILABLE = True
except Exception:
    pass

class AppSettingsPanel(QObject):
    """
    右側パネルの「アプリ設定」「自動スケール」タブのロジックを管理するクラス
    モダンUI適用版：グレー系配色・余白調整
    """
    def __init__(self, ui_manager, config_manager, app_config, locale_manager):
        super().__init__(ui_manager)
        self.ui_manager = ui_manager
        self.config_manager = config_manager
        self.app_config = app_config
        self.locale_manager = locale_manager
        
        self.app_settings_widgets = {}
        self.auto_scale_widgets = {}
        self.available_langs = {}
        
        self.tab_general_scroll = None
        self.tab_auto_scale_scroll = None
        
        # Labels
        self.as_center_label = None
        self.as_range_label = None
        self.as_steps_label = None
        self.auto_scale_info_label = None
        self.as_search_desc_label = None
        self.current_best_scale_label = None
        self.as_desc_label = None
        
        self.gs_desc_label = None
        self.strict_color_desc_label = None
        self.dxcam_desc_label = None
        self.eco_desc_label = None
        self.fs_label = None
        self.fs_desc_label = None
        self.opencl_desc_label = None
        self.stability_threshold_label = None
        self.stability_desc_label = None
        self.lw_mode_preset_label = None
        self.lw_mode_desc_label = None
        self.lang_label = None
        
        # Groups
        self.general_group = None
        self.stability_group = None
        self.lw_mode_group = None
        self.auto_scale_group = None

    def apply_card_style(self, group_box):
        """
        QGroupBoxをモダンなカードスタイル（白背景、グレー枠線）にするヘルパー。
        ライトグリーンを排除し、落ち着いた配色に。
        """
        group_box.setStyleSheet("""
            QGroupBox {
                border: 1px solid #cfd8dc;
                border-radius: 8px;
                margin-top: 1.5em;
                padding-top: 15px;
                background-color: #ffffff; 
                color: #37474f;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                font-weight: bold;
                color: #37474f; /* 濃いグレー */
            }
        """)

    def setup_ui(self, tab_widget: QTabWidget):
        """タブウィジェットに設定タブを追加します"""
        self._setup_tab_app_settings(tab_widget)
        self._setup_tab_auto_scale(tab_widget)
        
        self.load_app_settings_to_ui()
        self.connect_signals()

    def _setup_tab_auto_scale(self, tab_widget: QTabWidget):
        self.tab_auto_scale_scroll = QScrollArea()
        self.tab_auto_scale_scroll.setWidgetResizable(True)
        self.tab_auto_scale_scroll.setFrameShape(QFrame.NoFrame)
        # スクロールエリアの背景色も白系に
        self.tab_auto_scale_scroll.setStyleSheet("background-color: #fafafa;")
        
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(15, 15, 15, 15)
        container_layout.setSpacing(15)
        
        self.auto_scale_group = QGroupBox()
        self.apply_card_style(self.auto_scale_group)
        
        layout = QGridLayout(self.auto_scale_group)
        layout.setContentsMargins(15, 20, 15, 15)
        layout.setVerticalSpacing(15)
        layout.setHorizontalSpacing(15)
        
        self.auto_scale_widgets['use_window_scale'] = QCheckBox()
        layout.addWidget(self.auto_scale_widgets['use_window_scale'], 0, 0, 1, 2)
        
        self.auto_scale_widgets['enabled'] = QCheckBox()
        layout.addWidget(self.auto_scale_widgets['enabled'], 1, 0, 1, 2)

        # パラメータ設定行
        center_layout = QHBoxLayout()
        self.as_center_label = QLabel()
        center_layout.addWidget(self.as_center_label)
        self.auto_scale_widgets['center'] = QDoubleSpinBox()
        self.auto_scale_widgets['center'].setRange(0.1, 5.0)
        self.auto_scale_widgets['center'].setSingleStep(0.1)
        self.auto_scale_widgets['center'].setValue(1.0)
        center_layout.addWidget(self.auto_scale_widgets['center'])
        layout.addLayout(center_layout, 2, 0)

        range_layout = QHBoxLayout()
        self.as_range_label = QLabel()
        range_layout.addWidget(self.as_range_label)
        self.auto_scale_widgets['range'] = QDoubleSpinBox()
        self.auto_scale_widgets['range'].setRange(0.01, 1.0)
        self.auto_scale_widgets['range'].setSingleStep(0.05)
        self.auto_scale_widgets['range'].setValue(0.2)
        range_layout.addWidget(self.auto_scale_widgets['range'])
        layout.addLayout(range_layout, 2, 1)

        steps_layout = QHBoxLayout()
        self.as_steps_label = QLabel()
        steps_layout.addWidget(self.as_steps_label)
        self.auto_scale_widgets['steps'] = QSpinBox()
        self.auto_scale_widgets['steps'].setRange(1, 20)
        self.auto_scale_widgets['steps'].setValue(5)
        steps_layout.addWidget(self.auto_scale_widgets['steps'])
        layout.addLayout(steps_layout, 3, 0, 1, 2)
        
        self.auto_scale_info_label = QLabel()
        font = self.auto_scale_info_label.font()
        font.setBold(True)
        self.auto_scale_info_label.setFont(font)
        layout.addWidget(self.auto_scale_info_label, 3, 2, 1, 2)
        
        self.as_search_desc_label = QLabel()
        self.as_search_desc_label.setWordWrap(True)
        self.as_search_desc_label.setStyleSheet("font-size: 11px; opacity: 0.8;")
        layout.addWidget(self.as_search_desc_label, 4, 0, 1, 4)

        # 最適スケール表示
        scale_info_layout = QHBoxLayout()
        self.current_best_scale_label = QLabel()
        font = self.current_best_scale_label.font()
        font.setBold(True)
        font.setPointSize(11)
        self.current_best_scale_label.setFont(font)
        scale_info_layout.addWidget(self.current_best_scale_label)
        scale_info_layout.addSpacerItem(QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        layout.addLayout(scale_info_layout, 5, 0, 1, 4)
        
        # 全体説明文
        self.as_desc_label = QLabel()
        self.as_desc_label.setWordWrap(True)
        self.as_desc_label.setStyleSheet("font-size: 11px; opacity: 0.8; margin-top: 10px;")
        layout.addWidget(self.as_desc_label, 6, 0, 1, 4)
        
        container_layout.addWidget(self.auto_scale_group)
        container_layout.addStretch()
        
        self.tab_auto_scale_scroll.setWidget(container)
        tab_widget.addTab(self.tab_auto_scale_scroll, "")

    def _setup_tab_app_settings(self, tab_widget: QTabWidget):
        self.tab_general_scroll = QScrollArea() 
        self.tab_general_scroll.setWidgetResizable(True)
        self.tab_general_scroll.setFrameShape(QFrame.NoFrame)
        self.tab_general_scroll.setStyleSheet("background-color: #fafafa;")
        
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(20) # グループ間の間隔
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        def add_desc(label_obj, parent_layout):
            label_obj.setWordWrap(True)
            label_obj.setStyleSheet("font-size: 11px; opacity: 0.8; padding-left: 28px; margin-bottom: 5px;")
            parent_layout.addWidget(label_obj)

        # --- Card 1: 基本設定 & パフォーマンス ---
        self.general_group = QGroupBox("General") 
        self.apply_card_style(self.general_group)
        gen_layout = QVBoxLayout(self.general_group)
        gen_layout.setContentsMargins(15, 20, 15, 15)
        gen_layout.setSpacing(12)

        def add_checkbox(key, target_layout, enabled=True):
            cb = QCheckBox()
            cb.setEnabled(enabled)
            cb.setCursor(Qt.PointingHandCursor)
            self.app_settings_widgets[key] = cb
            target_layout.addWidget(cb)
            return cb

        add_checkbox('grayscale_matching', gen_layout)
        self.gs_desc_label = QLabel()
        add_desc(self.gs_desc_label, gen_layout)
        
        add_checkbox('strict_color_matching', gen_layout)
        self.strict_color_desc_label = QLabel()
        add_desc(self.strict_color_desc_label, gen_layout)
        
        add_checkbox('capture_method', gen_layout, enabled=DXCAM_AVAILABLE)
        self.dxcam_desc_label = QLabel()
        add_desc(self.dxcam_desc_label, gen_layout)
        
        gen_layout.addSpacing(8)
        
        add_checkbox('eco_mode_enabled', gen_layout)
        self.eco_desc_label = QLabel()
        add_desc(self.eco_desc_label, gen_layout)
        
        fs_layout = QHBoxLayout()
        self.fs_label = QLabel()
        fs_layout.addWidget(self.fs_label)
        self.app_settings_widgets['frame_skip_rate'] = QSpinBox()
        self.app_settings_widgets['frame_skip_rate'].setRange(1, 20)
        fs_layout.addWidget(self.app_settings_widgets['frame_skip_rate'])
        fs_layout.addStretch()
        gen_layout.addLayout(fs_layout)
        
        self.fs_desc_label = QLabel()
        add_desc(self.fs_desc_label, gen_layout)
        
        add_checkbox('use_opencl', gen_layout, enabled=OPENCL_AVAILABLE)
        self.opencl_desc_label = QLabel()
        add_desc(self.opencl_desc_label, gen_layout)
        
        layout.addWidget(self.general_group)
        
        # --- Card 2: 画面安定性チェック ---
        self.stability_group = QGroupBox()
        self.apply_card_style(self.stability_group)
        stab_layout = QGridLayout(self.stability_group)
        stab_layout.setContentsMargins(15, 20, 15, 15)
        stab_layout.setVerticalSpacing(10)
        
        self.app_settings_widgets['stability_check_enabled'] = QCheckBox()
        stab_layout.addWidget(self.app_settings_widgets['stability_check_enabled'], 0, 0)
        
        th_layout = QHBoxLayout()
        self.stability_threshold_label = QLabel()
        th_layout.addWidget(self.stability_threshold_label)
        self.app_settings_widgets['stability_threshold'] = QSpinBox()
        self.app_settings_widgets['stability_threshold'].setRange(0, 20)
        th_layout.addWidget(self.app_settings_widgets['stability_threshold'])
        th_layout.addStretch()
        stab_layout.addLayout(th_layout, 0, 1)
        
        self.stability_desc_label = QLabel()
        self.stability_desc_label.setWordWrap(True)
        self.stability_desc_label.setStyleSheet("font-size: 11px; opacity: 0.8; margin-top: 5px;")
        stab_layout.addWidget(self.stability_desc_label, 1, 0, 1, 2)
        
        layout.addWidget(self.stability_group)
        
        # --- Card 3: 軽量化モード ---
        self.lw_mode_group = QGroupBox()
        self.apply_card_style(self.lw_mode_group)
        lw_layout = QVBoxLayout(self.lw_mode_group)
        lw_layout.setContentsMargins(15, 20, 15, 15)
        lw_layout.setSpacing(10)
        
        self.app_settings_widgets['lightweight_mode_enabled'] = QCheckBox()
        lw_layout.addWidget(self.app_settings_widgets['lightweight_mode_enabled'])
        
        preset_layout = QHBoxLayout()
        self.lw_mode_preset_label = QLabel()
        preset_layout.addWidget(self.lw_mode_preset_label)
        self.app_settings_widgets['lightweight_mode_preset'] = QComboBox()
        # ★ 幅を広げる修正 (文字切れ防止のため200px程度確保)
        self.app_settings_widgets['lightweight_mode_preset'].setMinimumWidth(200)
        preset_layout.addWidget(self.app_settings_widgets['lightweight_mode_preset'])
        preset_layout.addStretch()
        lw_layout.addLayout(preset_layout)
        
        self.lw_mode_desc_label = QLabel()
        add_desc(self.lw_mode_desc_label, lw_layout)
        
        layout.addWidget(self.lw_mode_group)
        
        layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))
        
        # --- 言語設定 (下部に配置) ---
        lang_container = QWidget()
        lang_layout = QHBoxLayout(lang_container)
        lang_layout.setContentsMargins(0, 0, 0, 0)
        
        self.lang_label = QLabel()
        lang_layout.addWidget(self.lang_label)
        self.language_combo = QComboBox()
        lang_layout.addWidget(self.language_combo)
        lang_layout.addStretch()
        
        layout.addWidget(lang_container)
        
        self.tab_general_scroll.setWidget(widget)
        tab_widget.addTab(self.tab_general_scroll, "")

    def connect_signals(self):
        for widget in list(self.auto_scale_widgets.values()):
            if isinstance(widget, QDoubleSpinBox): widget.valueChanged.connect(self.on_app_settings_changed)
            elif isinstance(widget, QSpinBox): widget.valueChanged.connect(self.on_app_settings_changed)
            elif isinstance(widget, QCheckBox): widget.stateChanged.connect(self.on_app_settings_changed)
        for key, widget in self.app_settings_widgets.items():
            if isinstance(widget, QSpinBox): widget.valueChanged.connect(self.on_app_settings_changed)
            elif isinstance(widget, QCheckBox): widget.stateChanged.connect(self.on_app_settings_changed)
            elif isinstance(widget, QComboBox): widget.currentTextChanged.connect(self.on_app_settings_changed)

        self.language_combo.currentTextChanged.connect(self.on_language_changed)

    def retranslate_ui(self):
        lm = self.locale_manager.tr
        
        # Auto Scale Tab
        self.auto_scale_group.setTitle(lm("tab_auto_scale"))
        self.auto_scale_widgets['use_window_scale'].setText(lm("auto_scale_use_window"))
        self.auto_scale_widgets['use_window_scale'].setToolTip(lm("auto_scale_use_window_tooltip"))
        self.auto_scale_widgets['enabled'].setText(lm("auto_scale_enable_search"))
        self.as_center_label.setText(lm("auto_scale_center"))
        self.as_range_label.setText(lm("auto_scale_range"))
        self.as_steps_label.setText(lm("auto_scale_steps"))
        
        if self.auto_scale_widgets['enabled'].isChecked():
            center = self.auto_scale_widgets['center'].value()
            rng = self.auto_scale_widgets['range'].value()
            min_s = center - rng
            max_s = center + rng
            self.auto_scale_info_label.setText(lm("auto_scale_info_searching", f"{min_s:.2f}", f"{max_s:.2f}"))
        else:
            self.auto_scale_info_label.setText(lm("auto_scale_info_disabled"))
        
        self.as_search_desc_label.setText(lm("auto_scale_search_desc"))
        self.as_desc_label.setText(lm("auto_scale_desc"))

        self.general_group.setTitle("基本設定 & パフォーマンス") 
        
        self.app_settings_widgets['grayscale_matching'].setText(lm("app_setting_grayscale"))
        self.gs_desc_label.setText(lm("app_setting_grayscale_desc"))
        self.app_settings_widgets['strict_color_matching'].setText(lm("app_setting_strict_color"))
        self.strict_color_desc_label.setText(lm("app_setting_strict_color_desc"))
        self.app_settings_widgets['capture_method'].setText(lm("app_setting_dxcam"))
        self.dxcam_desc_label.setText(lm("app_setting_dxcam_desc"))
        self.app_settings_widgets['eco_mode_enabled'].setText(lm("app_setting_eco_mode"))
        self.eco_desc_label.setText(lm("app_setting_eco_mode_desc"))
        self.fs_label.setText(lm("app_setting_frame_skip"))
        self.fs_desc_label.setText(lm("app_setting_frame_skip_desc"))
        self.app_settings_widgets['use_opencl'].setText(lm("app_setting_opencl"))
        self.opencl_desc_label.setText(lm("app_setting_opencl_desc"))
        
        self.stability_group.setTitle(lm("app_setting_stability_group"))
        self.app_settings_widgets['stability_check_enabled'].setText(lm("app_setting_stability_enable"))
        self.stability_threshold_label.setText(lm("app_setting_stability_threshold"))
        self.stability_desc_label.setText(lm("app_setting_stability_desc"))
        
        self.lw_mode_group.setTitle(lm("app_setting_lw_mode_group"))
        self.app_settings_widgets['lightweight_mode_enabled'].setText(lm("app_setting_lw_mode_enable"))
        self.lw_mode_preset_label.setText(lm("app_setting_lw_mode_preset"))
        
        # --- 修正: 設定からプリセット値を正しく反映させる ---
        self.app_settings_widgets['lightweight_mode_preset'].blockSignals(True)
        self.app_settings_widgets['lightweight_mode_preset'].clear()
        
        # 項目リストを定義
        preset_items = [
            lm("app_setting_lw_mode_preset_standard"),    # index 0: standard
            lm("app_setting_lw_mode_preset_performance"), # index 1: performance
            lm("app_setting_lw_mode_preset_ultra")        # index 2: ultra
        ]
        self.app_settings_widgets['lightweight_mode_preset'].addItems(preset_items)

        # Configから保存された設定値を取得 ('standard' / 'performance' / 'ultra')
        saved_preset_key = self.app_config.get('lightweight_mode', {}).get('preset', 'standard')
        
        # 内部キーをインデックスにマッピング
        target_index = 0
        if saved_preset_key == 'performance':
            target_index = 1
        elif saved_preset_key == 'ultra':
            target_index = 2
        
        # 正しいインデックスを選択
        if target_index < self.app_settings_widgets['lightweight_mode_preset'].count():
             self.app_settings_widgets['lightweight_mode_preset'].setCurrentIndex(target_index)
             
        self.app_settings_widgets['lightweight_mode_preset'].blockSignals(False)
        # ----------------------------------------------------

        self.lw_mode_desc_label.setText(lm("app_setting_lw_mode_desc"))

        self.lang_label.setText(lm("app_setting_language_label"))
        
        self.available_langs.clear()
        current_lang_selection_text = self.language_combo.currentText()
        self.language_combo.blockSignals(True)
        self.language_combo.clear()
        selected_lang_code = self.locale_manager.current_lang
        found_current = False
        try:
            for file in self.locale_manager.locales_dir.glob("*.json"):
                lang_code = file.stem
                lang_name = lang_code
                try:
                    with open(file, 'r', encoding='utf-8') as f:
                        lang_data = json.load(f)
                        lang_name = lang_data.get("language_name", lang_code)
                except Exception: pass
                self.available_langs[lang_name] = lang_code
                self.language_combo.addItem(lang_name)
                if lang_code == selected_lang_code:
                    current_lang_selection_text = lang_name
                    found_current = True
        except Exception as e: print(f"Error loading languages for ComboBox: {e}")
        
        select_index = self.language_combo.findText(current_lang_selection_text)
        if select_index != -1: self.language_combo.setCurrentIndex(select_index)
        elif found_current: pass
        self.language_combo.blockSignals(False)

    def load_app_settings_to_ui(self):
        as_conf = self.app_config.get('auto_scale', {})
        self.auto_scale_widgets['use_window_scale'].setChecked(as_conf.get('use_window_scale', True))
        
        self.auto_scale_widgets['enabled'].setChecked(as_conf.get('enabled', False))
        self.auto_scale_widgets['center'].setValue(as_conf.get('center', 1.0))
        self.auto_scale_widgets['range'].setValue(as_conf.get('range', 0.2))
        self.auto_scale_widgets['steps'].setValue(as_conf.get('steps', 5))
        
        self.app_settings_widgets['capture_method'].setChecked(self.app_config.get('capture_method', 'dxcam') == 'dxcam')
        self.app_settings_widgets['frame_skip_rate'].setValue(self.app_config.get('frame_skip_rate', 2))
        self.app_settings_widgets['grayscale_matching'].setChecked(self.app_config.get('grayscale_matching', False))
        self.app_settings_widgets['strict_color_matching'].setChecked(self.app_config.get('strict_color_matching', False))
        self.app_settings_widgets['use_opencl'].setChecked(self.app_config.get('use_opencl', True))

        eco_conf = self.app_config.get('eco_mode', {})
        self.app_settings_widgets['eco_mode_enabled'].setChecked(eco_conf.get('enabled', True))

        stability_conf = self.app_config.get('screen_stability_check', {})
        self.app_settings_widgets['stability_check_enabled'].setChecked(stability_conf.get('enabled', True))
        self.app_settings_widgets['stability_threshold'].setValue(stability_conf.get('threshold', 8))

        lw_conf = self.app_config.get('lightweight_mode', {})
        self.app_settings_widgets['lightweight_mode_enabled'].setChecked(lw_conf.get('enabled', False))
        
        self.update_dependent_widgets_state()

    def update_dependent_widgets_state(self):
        is_lw_mode_enabled = self.app_settings_widgets['lightweight_mode_enabled'].isChecked()
        self.app_settings_widgets['lightweight_mode_preset'].setEnabled(is_lw_mode_enabled)
        
        is_search_enabled = self.auto_scale_widgets['enabled'].isChecked()
        self.auto_scale_widgets['center'].setEnabled(is_search_enabled)
        self.auto_scale_widgets['range'].setEnabled(is_search_enabled)
        self.auto_scale_widgets['steps'].setEnabled(is_search_enabled)
        self.as_center_label.setEnabled(is_search_enabled)
        self.as_range_label.setEnabled(is_search_enabled)
        self.as_steps_label.setEnabled(is_search_enabled)
        self.auto_scale_info_label.setEnabled(is_search_enabled)
        
        self.as_search_desc_label.setEnabled(is_search_enabled)
        self.retranslate_ui()

        is_stability_enabled = self.app_settings_widgets['stability_check_enabled'].isChecked()
        self.app_settings_widgets['stability_threshold'].setEnabled(is_stability_enabled)
        is_fs_user_configurable = not is_lw_mode_enabled
        self.app_settings_widgets['frame_skip_rate'].setEnabled(is_fs_user_configurable)

    def get_auto_scale_settings(self) -> dict:
        return {
            "use_window_scale": self.auto_scale_widgets['use_window_scale'].isChecked(),
            "enabled": self.auto_scale_widgets['enabled'].isChecked(),
            "center": self.auto_scale_widgets['center'].value(),
            "range": self.auto_scale_widgets['range'].value(),
            "steps": self.auto_scale_widgets['steps'].value()
        }

    def on_app_settings_changed(self):
        lm = self.locale_manager.tr
        self.app_config['auto_scale'] = self.get_auto_scale_settings()
        self.app_config['capture_method'] = 'dxcam' if self.app_settings_widgets['capture_method'].isChecked() else 'mss'
        self.app_config['frame_skip_rate'] = self.app_settings_widgets['frame_skip_rate'].value()
        self.app_config['grayscale_matching'] = self.app_settings_widgets['grayscale_matching'].isChecked()
        self.app_config['strict_color_matching'] = self.app_settings_widgets['strict_color_matching'].isChecked()
        self.app_config['use_opencl'] = self.app_settings_widgets['use_opencl'].isChecked()
        self.app_config['eco_mode'] = {"enabled": self.app_settings_widgets['eco_mode_enabled'].isChecked()}
        self.app_config['screen_stability_check'] = {
            "enabled": self.app_settings_widgets['stability_check_enabled'].isChecked(),
            "threshold": self.app_settings_widgets['stability_threshold'].value()
        }
        
        preset_display_text = self.app_settings_widgets['lightweight_mode_preset'].currentText()
        preset_internal_name = "standard"
        if preset_display_text == lm("app_setting_lw_mode_preset_standard"): preset_internal_name = "standard"
        elif preset_display_text == lm("app_setting_lw_mode_preset_performance"): preset_internal_name = "performance"
        elif preset_display_text == lm("app_setting_lw_mode_preset_ultra"): preset_internal_name = "ultra"
        
        self.app_config['lightweight_mode'] = {
            "enabled": self.app_settings_widgets['lightweight_mode_enabled'].isChecked(),
            "preset": preset_internal_name
        }
        
        self.config_manager.save_app_config(self.app_config)
        self.update_dependent_widgets_state()
        self.ui_manager.appConfigChanged.emit()

    def on_language_changed(self, lang_name: str):
        if not lang_name or not self.available_langs:
            return
        
        lang_code = self.available_langs.get(lang_name)
        if not lang_code:
            return

        if lang_code != self.locale_manager.current_lang:
            self.app_config['language'] = lang_code
            self.config_manager.save_app_config(self.app_config)
            
            try:
                self.locale_manager.languageChanged.disconnect(self.ui_manager.retranslate_ui)
            except (TypeError, RuntimeError):
                pass
                
            self.locale_manager.load_locale(lang_code)
            self.ui_manager.retranslate_ui()
            self.locale_manager.languageChanged.connect(self.ui_manager.retranslate_ui)
