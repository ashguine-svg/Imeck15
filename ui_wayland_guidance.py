"""
ui_wayland_guidance.py

第3段階(B): ui.py の Wayland 検出/ガイダンス表示を切り出す。
"""

from __future__ import annotations

import os
import sys

from PySide6.QtWidgets import QMessageBox, QCheckBox


def is_wayland_session() -> bool:
    if not sys.platform.startswith('linux'):
        return False
    if os.environ.get("WAYLAND_DISPLAY"):
        return True
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return True
    return False


def maybe_show_wayland_guidance(ui) -> None:
    """
    UIManager 初期化後に一度だけ呼ぶ想定。
    ui は UIManager 互換（locale_manager/app_config/config_manager を持つ）オブジェクト。
    """
    try:
        if not is_wayland_session():
            return
        if bool(ui.app_config.get("wayland_guidance_suppress", False)):
            return

        lm = ui.locale_manager.tr
        title = lm("wayland_guide_title")
        text = lm("wayland_guide_text")
        dont_show_text = lm("wayland_guide_dont_show_again")

        # 翻訳キー未追加の場合のフォールバック
        if title == "wayland_guide_title":
            title = "Waylandが検出されました"
        if text == "wayland_guide_text":
            text = (
                "Waylandセッションが検出されました。\n\n"
                "Waylandでは他アプリの前面化や入力注入が制限される場合があります。\n"
                "安定したクリックのため、可能であればXorg/X11セッションの使用を推奨します。\n\n"
                "Waylandのまま使用する場合は、監視中は対象アプリを最前面にしてください。"
            )
        if dont_show_text == "wayland_guide_dont_show_again":
            dont_show_text = "今後表示しない"

        box = QMessageBox(ui)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle(title)
        box.setText(text)
        cb = QCheckBox(dont_show_text)
        box.setCheckBox(cb)
        box.exec()

        if cb.isChecked():
            ui.app_config["wayland_guidance_suppress"] = True
            ui.config_manager.save_app_config(ui.app_config)
    except Exception:
        # ガイダンスは失敗しても致命ではない
        return


