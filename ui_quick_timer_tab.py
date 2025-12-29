"""
ui_quick_timer_tab.py

第3段階(B): ui.py のクイックタイマーTab関連を切り出す。
UIManager から薄く委譲して、挙動を変えずに分割する。
"""

from __future__ import annotations

import time
from pathlib import Path

import cv2

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap, QImage, QPainter, QPen, QColor
from PySide6.QtWidgets import (
    QScrollArea,
    QWidget,
    QVBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QMessageBox,
    QDialog,
)

from quick_timer_dialog import QuickTimerCreateDialog


def setup_quick_timer_tab(ui, tab_widget) -> None:
    # スクロール可能なコンテナ（9枠を表示すると縦に長くなるため）
    ui.quick_timer_scroll = QScrollArea()
    ui.quick_timer_scroll.setWidgetResizable(True)
    ui.quick_timer_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    ui.quick_timer_scroll.setStyleSheet("QScrollArea { border: none; }")

    ui.quick_timer_widget = QWidget()  # scroll内の実体
    layout = QVBoxLayout(ui.quick_timer_widget)
    layout.setContentsMargins(10, 10, 10, 10)
    layout.setSpacing(8)

    # 使い方（翻訳キーで表示）: 言語切替ですぐ反映できるよう self に保持
    ui.quick_timer_usage_label = QLabel(ui.locale_manager.tr("quick_timer_usage_hint"))
    ui.quick_timer_usage_label.setWordWrap(True)
    ui.quick_timer_usage_label.setStyleSheet(
        "color:#37474f; background-color:#ffffff; border:1px solid #cfd8dc; border-radius:6px; padding:8px;"
    )
    layout.addWidget(ui.quick_timer_usage_label)

    ui.quick_timer_rows = []  # list of dict widgets
    for slot in range(1, 10):
        row = QFrame()
        row.setStyleSheet("background-color:#fafafa; border:1px solid #cfd8dc; border-radius:6px;")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(8, 8, 8, 8)
        row_layout.setSpacing(10)

        lbl_slot = QLabel(f"{slot}")
        lbl_slot.setFixedWidth(22)
        lbl_slot.setStyleSheet("font-weight:bold; color:#37474f;")
        row_layout.addWidget(lbl_slot)

        thumb = QLabel()
        thumb.setFixedSize(100, 100)
        thumb.setStyleSheet("background-color:#263238; border:1px solid #546e7a;")
        thumb.setAlignment(Qt.AlignCenter)
        row_layout.addWidget(thumb)

        info = QVBoxLayout()
        lbl_minutes = QLabel("")
        lbl_target = QLabel("")
        lbl_countdown = QLabel("")
        for l in (lbl_minutes, lbl_target, lbl_countdown):
            l.setStyleSheet("color:#37474f;")
        info.addWidget(lbl_minutes)
        info.addWidget(lbl_target)
        info.addWidget(lbl_countdown)
        row_layout.addLayout(info, 1)

        btn_del = QPushButton()
        btn_del.setIcon(ui._safe_icon('fa5s.trash', color='#546e7a'))
        btn_del.setFixedWidth(90)
        btn_del.clicked.connect(lambda _=False, s=slot: delete_quick_timer(ui, s))
        row_layout.addWidget(btn_del)

        ui.quick_timer_rows.append(
            {
                "slot": slot,
                "thumb": thumb,
                "lbl_minutes": lbl_minutes,
                "lbl_target": lbl_target,
                "lbl_countdown": lbl_countdown,
                "btn_del": btn_del,
            }
        )
        layout.addWidget(row)

    layout.addStretch()

    ui.quick_timer_scroll.setWidget(ui.quick_timer_widget)
    tab_widget.addTab(ui.quick_timer_scroll, "")

    ui.quick_timer_ui_timer = QTimer(ui)
    ui.quick_timer_ui_timer.setInterval(1000)
    ui.quick_timer_ui_timer.timeout.connect(ui.update_quick_timer_tab)
    ui.quick_timer_ui_timer.start()


def delete_quick_timer(ui, slot: int) -> None:
    if ui.core_engine:
        ui.core_engine.remove_quick_timer(int(slot))


def update_quick_timer_tab(ui) -> None:
    lm = ui.locale_manager.tr
    # 言語切替が retranslate_ui のタイミングに依存して取りこぼさないよう、
    # ここでも毎回「使い方」を最新言語で更新する（1秒周期で必ず反映される）
    if hasattr(ui, "quick_timer_usage_label") and ui.quick_timer_usage_label:
        ui.quick_timer_usage_label.setText(lm("quick_timer_usage_hint"))

    snap = ui.core_engine.get_quick_timer_snapshot() if ui.core_engine else {}
    now = time.time()

    for row in ui.quick_timer_rows:
        slot = row["slot"]
        e = snap.get(slot)
        if not e:
            row["thumb"].setPixmap(QPixmap())
            row["thumb"].setText("")
            row["lbl_minutes"].setText(lm("quick_timer_empty"))
            row["lbl_target"].setText("")
            row["lbl_countdown"].setText("")
            row["btn_del"].setText(lm("quick_timer_btn_delete"))
            row["btn_del"].setEnabled(False)
            continue

        mins = int(e.get("minutes", 0))
        trg = float(e.get("trigger_time", 0))
        remain = max(0, int(trg - now))
        hh = remain // 3600
        mm = (remain % 3600) // 60
        ss = remain % 60

        row["lbl_minutes"].setText(lm("quick_timer_slot_label", slot, mins))
        row["lbl_target"].setText(lm("quick_timer_target_time_label", time.strftime("%H:%M:%S", time.localtime(trg))))
        row["lbl_countdown"].setText(lm("quick_timer_countdown_label", f"{hh:02d}:{mm:02d}:{ss:02d}"))
        row["btn_del"].setText(lm("quick_timer_btn_delete"))
        row["btn_del"].setEnabled(True)

        # サムネ: ROI画像を100x100内に最大表示、クリック点を赤丸
        try:
            img = e.get("template_bgr")
            if img is not None and hasattr(img, "shape"):
                rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                h, w = rgb.shape[:2]
                qimg = QImage(rgb.data, w, h, rgb.strides[0], QImage.Format_RGB888)
                pm = QPixmap.fromImage(qimg.copy()).scaled(100, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                # 赤点を描画（オフセットをスケールして描画）
                dx, dy = e.get("click_offset", (0, 0))
                tw, th = e.get("template_size", (w, h))
                painter = QPainter(pm)
                painter.setPen(QPen(QColor("#ef5350"), 2))
                painter.setBrush(QColor(239, 83, 80, 180))  # 塗りつぶし
                if tw and th:
                    px = int((dx / tw) * pm.width())
                    py = int((dy / th) * pm.height())
                    painter.drawEllipse(px - 5, py - 5, 10, 10)
                painter.end()
                row["thumb"].setPixmap(pm)
            else:
                row["thumb"].setPixmap(QPixmap())
        except Exception:
            row["thumb"].setPixmap(QPixmap())


def open_quick_timer_dialog(ui, payload: object) -> None:
    lm = ui.locale_manager.tr
    # 予約の作成自体は監視中/停止中どちらでも可能（クリック実行のみ監視ループ内）
    if not ui.core_engine:
        QMessageBox.warning(ui, lm("quick_timer_dialog_title"), lm("quick_timer_err_unexpected"))
        return

    try:
        rec = payload.get("rec_area")
        frame = payload.get("frame")
        sx = int(payload.get("screen_x"))
        sy = int(payload.get("screen_y"))
        if not rec:
            QMessageBox.warning(ui, lm("quick_timer_dialog_title"), lm("quick_timer_err_no_rec_area"))
            return
        if frame is None:
            QMessageBox.warning(ui, lm("quick_timer_dialog_title"), lm("quick_timer_err_unexpected"))
            return
        rx0, ry0, rx1, ry1 = rec
        if not (rx0 <= sx < rx1 and ry0 <= sy < ry1):
            QMessageBox.warning(ui, lm("quick_timer_dialog_title"), lm("quick_timer_err_outside_area"))
            return
        cx = sx - rx0
        cy = sy - ry0
    except Exception:
        QMessageBox.warning(ui, lm("quick_timer_dialog_title"), lm("quick_timer_err_unexpected"))
        return

    # デフォルトの右クリックON/OFFは「現在選択中の画像設定」に合わせる（未選択ならFalse）
    default_right_click = False
    try:
        sel_path, _ = ui.get_selected_item_path()
        if sel_path and not Path(sel_path).is_dir():
            s = ui.config_manager.load_item_setting(Path(sel_path))
            default_right_click = bool(s.get("right_click", False))
    except Exception:
        default_right_click = False

    with ui.core_engine.temporary_listener_pause():
        dlg = QuickTimerCreateDialog(frame, (cx, cy), locale_manager=ui.locale_manager, right_click=default_right_click, parent=ui)
        if dlg.exec() != QDialog.Accepted:
            return
        minutes, roi_rect, click_pt, right_click = dlg.get_result()

    # エントリ作成
    rx, ry, rw, rh = roi_rect
    tx, ty = click_pt
    template_bgr = frame[ry:ry + rh, rx:rx + rw].copy()
    template_gray = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)
    dx = int(tx - rx)
    dy = int(ty - ry)
    now = time.time()
    trigger_time = now + int(minutes) * 60
    entry = {
        "minutes": int(minutes),
        "trigger_time": float(trigger_time),
        "match_start_time": float(trigger_time - 60.0),
        "template_bgr": template_bgr,
        "template_gray": template_gray,
        "click_offset": (dx, dy),
        "right_click": bool(right_click),
    }

    ok, msg = ui.core_engine.add_quick_timer(entry)
    if not ok:
        QMessageBox.warning(ui, lm("quick_timer_dialog_title"), lm(msg))
    ui.update_quick_timer_tab()


