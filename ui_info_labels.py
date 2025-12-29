"""
ui_info_labels.py

第3段階(B): ui.py の情報ラベル（OCR/タイマーのバッジ表示）更新を切り出す。
UIManager から薄く委譲して、挙動を変えずに分割する。
"""

from __future__ import annotations


def update_info_labels(ui, settings) -> None:
    """OCR情報ラベルとタイマー情報ラベルを更新する（バッジスタイル）"""

    # 共通のベーススタイル (角丸、パディング、フォント)
    base_style = """
        border-radius: 4px;
        padding: 2px 8px;
        margin-right: 8px;
        font-weight: bold;
        font-family: Consolas, monospace;
    """

    # 無効時のスタイル (グレー背景)
    disabled_style = base_style + """
        background-color: #f5f5f5;
        color: #bdbdbd;
        border: 1px solid #e0e0e0;
    """

    # OCR有効時のスタイル (薄い紫背景)
    ocr_enabled_style = base_style + """
        background-color: #f3e5f5;
        color: #7b1fa2;
        border: 1px solid #e1bee7;
    """

    # タイマー有効時のスタイル (薄いオレンジ背景)
    timer_enabled_style = base_style + """
        background-color: #fff3e0;
        color: #f57c00;
        border: 1px solid #ffe0b2;
    """

    # --- OCR Label ---
    ocr_text = "----"
    ocr_style = disabled_style

    if settings:
        ocr_conf = settings.get('ocr_settings', {})
        # OCRが有効、かつコンフィグが存在する場合
        if ocr_conf and ocr_conf.get('enabled', False):
            cond = ocr_conf.get('condition', {})
            op = cond.get('operator', '')
            val = str(cond.get('value', ''))

            # 表示用に短縮
            if op == "Contains":
                op = "Cont."
            elif op == "Equals":
                op = "Eq."
            elif op == "Regex":
                op = "Reg."

            ocr_text = f"{op} {val}"
            ocr_style = ocr_enabled_style

    if getattr(ui, "ocr_info_label", None):
        ui.ocr_info_label.setText(ocr_text)
        ui.ocr_info_label.setStyleSheet(ocr_style)

    # --- Timer Label ---
    timer_text = "--:--:--"
    timer_style = disabled_style

    if settings:
        timer_conf = settings.get('timer_mode', {})
        if timer_conf and timer_conf.get('enabled', False):
            actions = timer_conf.get('actions', [])
            # ID1を探す
            if not isinstance(actions, list):
                actions = []
            id1_action = next((a for a in actions if isinstance(a, dict) and a.get('id') == 1), None)
            if id1_action:
                timer_text = id1_action.get('display_time', "--:--:--")
                timer_style = timer_enabled_style

    if getattr(ui, "timer_info_label", None):
        ui.timer_info_label.setText(timer_text)
        ui.timer_info_label.setStyleSheet(timer_style)


