"""
ui_layout_builders.py

第3段階(B): ui.py のUI構築（レイアウト組み立て）を段階的に外出しする。
まずは info labels（OCR/Timer）の生成・配置を切り出す。
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QLayout


def add_ocr_info_label(ui, layout: QLayout) -> QLabel:
    """
    OCR情報ラベル（バッジ表示対象）の生成と配置。
    ui は UIManager 互換（ui.ocr_info_label を保持する）オブジェクト。
    """
    label = QLabel("----")
    label.setStyleSheet("color: #bdbdbd; font-weight: bold; margin-right: 5px;")
    layout.addWidget(label)
    ui.ocr_info_label = label
    return label


def add_timer_info_label(ui, layout: QLayout) -> QLabel:
    """
    タイマー情報ラベル（バッジ表示対象）の生成と配置。
    ui は UIManager 互換（ui.timer_info_label を保持する）オブジェクト。
    """
    label = QLabel("--:--:--")
    label.setStyleSheet("color: #bdbdbd; font-weight: bold; margin-right: 5px;")
    layout.addWidget(label)
    ui.timer_info_label = label
    return label


