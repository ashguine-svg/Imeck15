"""
ui_item_dialogs.py

第3段階(B): ui.py の肥大化を抑えるため、画像アイテム系の設定ダイアログ処理を切り出す。
UIManager のメソッドから薄く委譲することで、挙動を変えずに分割する。
"""

from __future__ import annotations

from pathlib import Path
from contextlib import nullcontext

import cv2
import numpy as np

from PySide6.QtWidgets import QMessageBox, QDialog

from timer_ui import TimerSettingsDialog

# --- OCR Integration Imports ---
OCR_AVAILABLE = False
try:
    from ocr_manager import OCRConfig
    from ocr_settings_dialog import OCRSettingsDialog
    OCR_AVAILABLE = True
except Exception:
    OCR_AVAILABLE = False
# -------------------------------


def open_ocr_settings_dialog(ui) -> None:
    """
    UIManager の OCR設定ボタン処理を外出ししたもの。
    ui は UIManager 互換（必要属性/メソッドを持つ）オブジェクト。
    """
    lm = ui.locale_manager.tr

    if not OCR_AVAILABLE:
        QMessageBox.warning(ui, lm("ocr_msg_missing_title"), lm("ocr_msg_missing_text"))
        return

    path, _ = ui.get_selected_item_path()
    if not path or Path(path).is_dir():
        QMessageBox.information(ui, lm("ocr_dialog_title"), lm("ocr_info_select_item"))
        return

    file_path = Path(path)
    if not file_path.exists():
        return

    # 設定操作前に監視停止（誤クリック事故防止）
    try:
        ui._stop_monitoring_for_settings()
    except Exception:
        pass

    try:
        with open(file_path, 'rb') as f:
            file_bytes = np.fromfile(f, np.uint8)
        img = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("Decode failed")
    except Exception as e:
        QMessageBox.warning(ui, lm("ocr_error_load_title"), lm("ocr_error_load_text", str(e)))
        return

    # ファイルから現在の設定を読み込む（OCR設定を含む完全な設定を取得するため）
    settings = ui.config_manager.load_item_setting(file_path)
    
    # UIの現在の値を反映（threshold, interval_time など）
    try:
        settings['threshold'] = ui.item_settings_widgets['threshold'].value()
        settings['interval_time'] = ui.item_settings_widgets['interval_time'].value()
        settings['backup_time'] = ui.item_settings_widgets['backup_time'].value()
        settings['debounce_time'] = ui.item_settings_widgets['debounce_time'].value()
    except Exception:
        pass

    ocr_conf_dict = settings.get('ocr_settings') or {}

    config = OCRConfig()
    if "config" in ocr_conf_dict:
        cfg_data = ocr_conf_dict["config"]
        config.scale = cfg_data.get("scale", 2.0)
        config.threshold = cfg_data.get("threshold", 128)
        config.invert = cfg_data.get("invert", False)
        config.numeric_mode = cfg_data.get("numeric_mode", False)
        config.lang = cfg_data.get("lang", "eng")

    roi = ocr_conf_dict.get('roi', None)
    condition = ocr_conf_dict.get('condition', None)
    is_enabled = ocr_conf_dict.get('enabled', True)

    try:
        no_click_when_disabled = bool(ocr_conf_dict.get("no_click_when_disabled", False))

        cm = ui.core_engine.temporary_listener_pause() if getattr(ui, "core_engine", None) else nullcontext()
        with cm:
            dialog = OCRSettingsDialog(
                img,
                config,
                roi,
                condition,
                enabled=is_enabled,
                no_click_when_disabled=no_click_when_disabled,
                parent=ui
            )
            dialog.set_parent_settings(settings)

            if dialog.exec() != QDialog.Accepted:
                return

            new_conf, new_roi, new_condition, new_enabled, new_no_click_when_disabled = dialog.get_result()

        new_settings = {
            "enabled": new_enabled,
            "no_click_when_disabled": bool(new_no_click_when_disabled),
            'roi': new_roi,
            'config': {
                'scale': new_conf.scale,
                'threshold': new_conf.threshold,
                'invert': new_conf.invert,
                'lang': new_conf.lang,
                'numeric_mode': new_conf.numeric_mode
            },
            'condition': new_condition
        }
        settings['ocr_settings'] = new_settings

        if 'image_path' not in settings:
            settings['image_path'] = str(file_path)

        ui.config_manager.save_item_setting(file_path, settings)
        ui.imageSettingsChanged.emit(settings)

        # アイコンの色更新のためにツリーを再描画
        ui.update_image_tree()

        ui.logger.log(f"[INFO] OCR settings updated for {file_path.name}")
        ui.update_info_labels(settings)

    except Exception as e:
        ui.logger.log(f"[ERROR] Failed to open OCR dialog: {e}")
        import traceback
        traceback.print_exc()


def open_timer_settings_dialog(ui) -> None:
    """
    UIManager の タイマー設定ボタン処理を外出ししたもの。
    """
    path, _ = ui.get_selected_item_path()
    if not path or Path(path).is_dir():
        QMessageBox.information(ui, "Timer Settings", "Please select an image item first from the list.")
        return

    file_path = Path(path)
    current_settings = ui.config_manager.load_item_setting(file_path)

    # 設定操作前に監視停止（誤クリック事故防止）
    try:
        ui._stop_monitoring_for_settings()
    except Exception:
        pass

    cm = ui.core_engine.temporary_listener_pause() if getattr(ui, "core_engine", None) else nullcontext()
    with cm:
        dialog = TimerSettingsDialog(
            file_path,
            file_path.name,
            current_settings,
            ui.locale_manager,
            parent=ui,
            core_engine=ui.core_engine
        )
        if not dialog.exec():
            return

        timer_data, right_click = dialog.get_settings()

    current_settings['timer_mode'] = timer_data
    current_settings['right_click'] = bool(right_click)
    ui.config_manager.save_item_setting(file_path, current_settings)

    ui.logger.log(f"[INFO] Timer settings updated for {file_path.name}")
    ui.imageSettingsChanged.emit(current_settings)
    ui.update_info_labels(current_settings)


