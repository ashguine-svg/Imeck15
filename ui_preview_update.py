"""
ui_preview_update.py

第3段階(B): ui.py の画像プレビュー更新系を切り出す。
UIManager から薄く委譲して、挙動を変えずに分割する。
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox


def set_settings_from_data(ui, settings_data):
    ui.item_settings_widgets['threshold'].blockSignals(True)
    ui.item_settings_widgets['interval_time'].blockSignals(True)
    ui.item_settings_widgets['backup_time'].blockSignals(True)
    ui.item_settings_widgets['debounce_time'].blockSignals(True)

    try:
        ui.item_settings_widgets['threshold'].setValue(settings_data.get('threshold', 0.8) if settings_data else 0.8)
        ui.item_settings_widgets['interval_time'].setValue(settings_data.get('interval_time', 1.5) if settings_data else 1.5)
        ui.item_settings_widgets['backup_time'].setValue(settings_data.get('backup_time', 300.0) if settings_data else 300.0)
        ui.item_settings_widgets['debounce_time'].setValue(settings_data.get('debounce_time', 0.0) if settings_data else 0.0)
    finally:
        ui.item_settings_widgets['threshold'].blockSignals(False)
        ui.item_settings_widgets['interval_time'].blockSignals(False)
        ui.item_settings_widgets['backup_time'].blockSignals(False)
        ui.item_settings_widgets['debounce_time'].blockSignals(False)


def update_image_preview(ui, cv_image, settings_data: dict = None, reset_zoom: bool = True):
    set_settings_from_data(ui, settings_data)

    image_or_splash_to_pass = cv_image
    is_folder_or_no_data = (settings_data is None and (cv_image is None or getattr(cv_image, "size", 0) == 0))

    if is_folder_or_no_data:
        if getattr(ui, "splash_pixmap", None):
            image_or_splash_to_pass = ui.splash_pixmap

    ui.preview_mode_manager.update_preview(image_or_splash_to_pass, settings_data, reset_zoom)

    if is_folder_or_no_data:
        ui.preview_mode_manager.sync_from_external(is_folder_or_no_data)

    ui.item_settings_group.setEnabled(not is_folder_or_no_data)

    # --- OCRボタンのスタイル・有効状態を切り替え ---
    if getattr(ui, "ocr_settings_btn_main", None):
        if is_folder_or_no_data:
            ui.ocr_settings_btn_main.setStyleSheet(ui.STYLE_OCR_BTN_DISABLED)
            ui.ocr_settings_btn_main.setEnabled(False)
            ui.ocr_settings_btn_main.setIcon(ui._safe_icon('fa5s.font', color='#9e9e9e'))
        else:
            ui.ocr_settings_btn_main.setStyleSheet(ui.STYLE_OCR_BTN_ENABLED)
            ui.ocr_settings_btn_main.setEnabled(True)
            ui.ocr_settings_btn_main.setIcon(ui._safe_icon('fa5s.font', color='#ffffff'))
    # ----------------------------------------------------

    # --- タイマーボタンのスタイル・有効状態を切り替え ---
    if getattr(ui, "timer_settings_btn_main", None):
        if is_folder_or_no_data:
            ui.timer_settings_btn_main.setStyleSheet(ui.STYLE_TIMER_BTN_DISABLED)
            ui.timer_settings_btn_main.setEnabled(False)
            ui.timer_settings_btn_main.setIcon(ui._safe_icon('fa5s.clock', color='#9e9e9e'))
        else:
            ui.timer_settings_btn_main.setStyleSheet(ui.STYLE_TIMER_BTN_ENABLED)
            ui.timer_settings_btn_main.setEnabled(True)
            ui.timer_settings_btn_main.setIcon(ui._safe_icon('fa5s.clock', color='#ffffff'))
    # ----------------------------------------------------

    ui.update_info_labels(settings_data)


def on_capture_failed(ui):
    lm = ui.locale_manager.tr
    QMessageBox.warning(ui, lm("warn_title_capture_failed"), lm("warn_message_capture_failed"))


def on_captured_image_ready_for_preview(ui, captured_image):
    ui.pending_captured_image = captured_image
    ui.showNormal()
    ui.raise_()
    ui.activateWindow()

    ui.switch_to_preview_tab()
    update_image_preview(ui, captured_image, settings_data=None)
    QApplication.processEvents()
    QTimer.singleShot(100, ui._prompt_for_save_filename)


