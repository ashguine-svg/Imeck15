"""
input_gestures.py

グローバル入力（主にマウス）から、Imeck15固有のジェスチャ判定を行う。
CoreEngine から分離して、閾値・クールダウン等の調整点を1箇所に集約する（リファクタ第1段階）。
"""

from __future__ import annotations

import time
from threading import Timer
from typing import Optional, Tuple

try:
    from pynput import mouse
except Exception:  # pragma: no cover - 実行環境により import が失敗しうる
    mouse = None


class GlobalMouseGestureHandler:
    """
    CoreEngine の _on_global_click を置き換える薄いアダプタ。
    - 中クリック: quickCaptureRequested
    - 右クリック3連打: startMonitoringRequested
    - 右クリック2連打(タイムアウト確定): stopMonitoringRequested
    - 左右同時押し(厳格化): クイックタイマー設定ダイアログ起動
    """

    # --- クイックタイマー（左右同時押し） ---
    QT_SIMUL_SEC = 0.15          # 左右押下の同時性
    QT_SIMUL_MAX_DIST = 40       # 左右押下位置の近さ（マンハッタン距離）
    QT_HOLD_SEC = 0.35           # 両ボタンをこの時間押し続けたら起動
    QT_COOLDOWN_SEC = 2.0        # 連続誤爆防止

    def __init__(self, core_engine):
        self.core = core_engine

        # 右クリック連打判定
        self.click_timer: Optional[Timer] = None
        self.last_right_click_time: float = 0.0
        self.right_click_count: int = 0

        # クイックタイマー起動用（左右同時押し判定）
        self._qt_last_left_down: Optional[Tuple[float, int, int]] = None
        self._qt_last_right_down: Optional[Tuple[float, int, int]] = None
        self._qt_left_is_down: bool = False
        self._qt_right_is_down: bool = False
        self._qt_chord_timer: Optional[Timer] = None
        self._qt_chord_anchor: Optional[Tuple[int, int]] = None
        self._qt_last_chord_trigger_time: float = 0.0

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------
    def reset(self):
        """リスナー停止/再起動時に状態をリセットする（タイマー停止含む）。"""
        self._cancel_quick_timer_chord()
        self._cancel_right_click_timer()
        self._qt_last_left_down = None
        self._qt_last_right_down = None
        self._qt_left_is_down = False
        self._qt_right_is_down = False
        self._qt_last_chord_trigger_time = 0.0
        self.last_right_click_time = 0.0
        self.right_click_count = 0

    def on_click(self, x, y, button, pressed):
        """
        pynput.mouse.Listener(on_click=...) から呼ばれる。
        """
        if mouse is None:
            return

        # 中クリック: クイックキャプチャ（認識範囲がある時だけ）
        if pressed and button == mouse.Button.middle:
            if getattr(self.core, "recognition_area", None) is not None:
                try:
                    self.core.quickCaptureRequested.emit()
                except Exception:
                    pass
            return

        # クイックタイマー: 左右同時押し + ホールド（誤動作を減らす）
        if getattr(self.core, "recognition_area", None) is not None and button in (mouse.Button.left, mouse.Button.right):
            if button == mouse.Button.left:
                if pressed:
                    self._qt_left_is_down = True
                    self._qt_last_left_down = (time.time(), int(x), int(y))
                    if self._maybe_schedule_quick_timer_chord(int(x), int(y)):
                        return
                else:
                    self._qt_left_is_down = False
                    self._cancel_quick_timer_chord()

            if button == mouse.Button.right:
                if pressed:
                    self._qt_right_is_down = True
                    self._qt_last_right_down = (time.time(), int(x), int(y))
                    if self._maybe_schedule_quick_timer_chord(int(x), int(y)):
                        # 右クリック連打（監視開始/停止）判定に混ぜない
                        return
                else:
                    self._qt_right_is_down = False
                    self._cancel_quick_timer_chord()

        # 右クリック連打: 3回で監視開始、2回で監視停止（タイムアウトで確定）
        if button == mouse.Button.right and pressed:
            self._handle_right_click_sequence()

    # ---------------------------------------------------------------------
    # Internal: right click sequence
    # ---------------------------------------------------------------------
    def _cancel_right_click_timer(self):
        t = self.click_timer
        if t is not None:
            try:
                t.cancel()
            except Exception:
                pass
        self.click_timer = None

    def _handle_right_click_sequence(self):
        try:
            current_time = time.time()
            self._cancel_right_click_timer()

            interval = float(getattr(self.core, "CLICK_INTERVAL", 0.3))
            if current_time - float(self.last_right_click_time) > interval:
                self.right_click_count = 1
            else:
                self.right_click_count += 1
            self.last_right_click_time = current_time

            if self.right_click_count == 3:
                try:
                    self.core.logger.log("log_right_click_triple")
                except Exception:
                    pass
                try:
                    self.core.startMonitoringRequested.emit()
                except Exception:
                    pass
                self.right_click_count = 0
            else:
                self.click_timer = Timer(interval, self._handle_click_timer_timeout)
                self.click_timer.start()
        except Exception:
            # フックは絶対に落とさない
            self._cancel_right_click_timer()
            self.right_click_count = 0

    def _handle_click_timer_timeout(self):
        """
        CoreEngine._handle_click_timer 相当。
        右クリック2回で停止、3回で開始は on_click 側で即時処理。
        """
        try:
            if self.right_click_count == 2:
                try:
                    self.core.logger.log("log_right_click_double")
                except Exception:
                    pass
                try:
                    self.core.stopMonitoringRequested.emit()
                except Exception:
                    pass
        finally:
            self.right_click_count = 0
            self.click_timer = None

    # ---------------------------------------------------------------------
    # Internal: quick timer chord
    # ---------------------------------------------------------------------
    def _cancel_quick_timer_chord(self):
        t = self._qt_chord_timer
        if t is not None:
            try:
                t.cancel()
            except Exception:
                pass
        self._qt_chord_timer = None
        self._qt_chord_anchor = None

    def _maybe_schedule_quick_timer_chord(self, x: int, y: int) -> bool:
        try:
            now = time.time()
            if (now - float(self._qt_last_chord_trigger_time)) < self.QT_COOLDOWN_SEC:
                return False

            if not (self._qt_left_is_down and self._qt_right_is_down):
                return False
            if not (self._qt_last_left_down and self._qt_last_right_down):
                return False

            lt, lx, ly = self._qt_last_left_down
            rt, rx, ry = self._qt_last_right_down
            if abs(float(lt) - float(rt)) > self.QT_SIMUL_SEC:
                return False
            if (abs(int(lx) - int(rx)) + abs(int(ly) - int(ry))) > self.QT_SIMUL_MAX_DIST:
                return False

            # すでに保留中なら二重起動しない
            if self._qt_chord_timer is not None:
                return True

            self._qt_chord_anchor = (int(x), int(y))
            self._qt_chord_timer = Timer(self.QT_HOLD_SEC, self._confirm_quick_timer_chord)
            try:
                self._qt_chord_timer.daemon = True
            except Exception:
                pass
            self._qt_chord_timer.start()
            return True
        except Exception:
            return False

    def _confirm_quick_timer_chord(self):
        try:
            # timer thread
            self._qt_chord_timer = None

            if not (self._qt_left_is_down and self._qt_right_is_down):
                self._qt_chord_anchor = None
                return

            self._qt_last_chord_trigger_time = float(time.time())

            ax, ay = (0, 0)
            if self._qt_chord_anchor:
                ax, ay = self._qt_chord_anchor
            self._qt_chord_anchor = None

            # クイックタイマー設定起動（QuickTimerManager経由）
            try:
                self.core._quick_timer_manager.trigger_dialog(int(ax), int(ay))
            except Exception:
                return

            # 監視開始/停止の右クリック連打判定を汚染しない
            self._cancel_right_click_timer()
            self.right_click_count = 0
        except Exception:
            self._qt_chord_anchor = None
            return


