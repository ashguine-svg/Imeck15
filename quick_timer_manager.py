"""
quick_timer_manager.py

CoreEngine のクイックタイマー（最大9スロット）管理を切り出す（リファクタ: core.py分割 / C）。
Core側の公開API（add/remove/snapshot, dialog trigger）は維持しつつ、実装をここに集約する。
"""

from __future__ import annotations

import time
from typing import Dict, Any, List, Tuple


class QuickTimerManager:
    def __init__(self, core_engine):
        self.core = core_engine
        # slot(int:1-9) -> entry(dict)
        self.timers: Dict[int, Dict[str, Any]] = {}

    # ------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------
    def add(self, entry: dict) -> tuple[bool, str]:
        """
        entry: {
          'minutes': int,
          'trigger_time': float,
          'match_start_time': float,
          'template_bgr': np.ndarray,
          'template_gray': np.ndarray,
          'click_offset': (dx, dy),
          'right_click': bool,
        }
        """
        try:
            for slot in range(1, 10):
                if slot not in self.timers:
                    e = dict(entry)
                    e["slot"] = slot
                    self.timers[slot] = e
                    try:
                        self.core.quickTimersChanged.emit()
                    except Exception:
                        pass
                    return True, str(slot)
            return False, "quick_timer_err_slots_full"
        except Exception:
            return False, "quick_timer_err_unexpected"

    def remove(self, slot: int) -> None:
        try:
            slot = int(slot)
            if slot in self.timers:
                del self.timers[slot]
                try:
                    self.core.quickTimersChanged.emit()
                except Exception:
                    pass
        except Exception:
            pass

    def snapshot(self) -> dict:
        """UI表示用のスナップショット（Qtスレッド安全性のため shallow コピー）。"""
        try:
            snap = {}
            for slot, e in self.timers.items():
                snap[slot] = {
                    "slot": slot,
                    "trigger_time": float(e.get("trigger_time", 0)),
                    "match_start_time": float(e.get("match_start_time", 0)),
                    "minutes": int(e.get("minutes", 0)),
                    "click_offset": tuple(e.get("click_offset", (0, 0))),
                    "right_click": bool(e.get("right_click", False)),
                    "template_bgr": e.get("template_bgr"),
                    "template_size": (
                        int(e.get("template_gray").shape[1]),
                        int(e.get("template_gray").shape[0]),
                    ) if e.get("template_gray") is not None else (0, 0),
                }
            return snap
        except Exception:
            return {}

    # ------------------------------------------------------------
    # Helpers for monitoring_states
    # ------------------------------------------------------------
    def has_any(self) -> bool:
        return bool(self.timers)

    def entries_sorted(self) -> List[Dict[str, Any]]:
        entries = list(self.timers.values())
        entries.sort(key=lambda e: float(e.get("trigger_time", 0)))
        return entries

    def remove_if_expired(self, entry: Dict[str, Any], current_time: float, *, grace_seconds: float = 5.0) -> bool:
        """期限切れなら削除して True、まだなら False。"""
        try:
            trigger_time = float(entry.get("trigger_time", 0))
            if current_time > trigger_time + float(grace_seconds):
                slot = int(entry.get("slot"))
                self.remove(slot)
                return True
        except Exception:
            pass
        return False

    # ------------------------------------------------------------
    # Dialog trigger
    # ------------------------------------------------------------
    def trigger_dialog(self, screen_x: int, screen_y: int) -> None:
        """
        マウスジェスチャ起点で「クイックタイマー作成ダイアログを開く」ためのpayloadをemitする。
        監視中は誤クリック回避のため stopMonitoringRequested も emit する。
        """
        core = self.core
        try:
            rec = core.recognition_area
            if not rec:
                return

            # 設定中は誤クリックを避けるため監視を停止する
            try:
                if core.is_monitoring:
                    core.stopMonitoringRequested.emit()
            except Exception:
                pass

            # 停止中は latest_high_res_frame が更新されないため、その場で1枚キャプチャする
            frame = getattr(core, "latest_high_res_frame", None)
            if frame is None:
                try:
                    x0, y0, x1, y1 = rec
                    frame = core.capture_manager.capture_frame((int(x0), int(y0), int(x1), int(y1)))
                except Exception:
                    frame = None

            if frame is not None:
                payload = {
                    "screen_x": int(screen_x),
                    "screen_y": int(screen_y),
                    "rec_area": tuple(rec),
                    "frame": frame.copy(),
                    "time": time.time(),
                }
                core.quickTimerDialogRequested.emit(payload)
        except Exception:
            pass


