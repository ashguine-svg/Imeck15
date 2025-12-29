"""
ui_preview_sync.py

第3段階(B): ui.py のプレビュー/設定同期（PreviewModeManager連携）を切り出す。
UIManager から薄く委譲して、挙動を変えずに分割する。
"""

from __future__ import annotations

from pathlib import Path

from settings_model import normalize_image_item_settings


def emit_settings_for_save(ui) -> None:
    if not hasattr(ui, 'preview_mode_manager') or not ui.core_engine:
        return

    path, _ = ui.get_selected_item_path()
    target_path = None
    try:
        if ui.core_engine.current_image_path and Path(ui.core_engine.current_image_path).is_file():
            target_path = ui.core_engine.current_image_path
        elif path and Path(path).is_file():
            target_path = path
        else:
            return
    except Exception:
        return

    settings = ui.preview_mode_manager.get_settings()
    settings['image_path'] = ui.core_engine.current_image_path or target_path

    try:
        settings['threshold'] = ui.item_settings_widgets['threshold'].value()
        settings['interval_time'] = ui.item_settings_widgets['interval_time'].value()
        settings['backup_time'] = ui.item_settings_widgets['backup_time'].value()
        settings['debounce_time'] = ui.item_settings_widgets['debounce_time'].value()
    except KeyError:
        return
    except Exception:
        return

    normalized = normalize_image_item_settings(settings, default_image_path=str(settings.get("image_path", "")))
    ui.imageSettingsChanged.emit(normalized)


def update_ui_from_preview_manager(ui, settings: dict) -> None:
    if hasattr(ui, 'preview_mode_manager'):
        ui.preview_mode_manager._block_all_signals(True)
    try:
        ui.item_settings_widgets['point_click'].setChecked(settings.get('point_click', True))
        ui.item_settings_widgets['range_click'].setChecked(settings.get('range_click', False))
        ui.item_settings_widgets['random_click'].setChecked(settings.get('random_click', False))
        ui.item_settings_widgets['backup_click'].setChecked(settings.get('backup_click', False))
        ui.item_settings_widgets['roi_enabled'].setChecked(settings.get('roi_enabled', False))
        if 'right_click' in ui.item_settings_widgets:
            ui.item_settings_widgets['right_click'].setChecked(bool(settings.get('right_click', False)))

        roi_mode = settings.get('roi_mode', 'fixed')
        if roi_mode == 'variable':
            ui.item_settings_widgets['roi_mode_variable'].setChecked(True)
        else:
            ui.item_settings_widgets['roi_mode_fixed'].setChecked(True)
    finally:
        if hasattr(ui, 'preview_mode_manager'):
            ui.preview_mode_manager._block_all_signals(False)


