"""
monitoring_controller.py

CoreEngine の監視開始/停止と state 遷移を切り出す（リファクタ: core.py分割）。
UI/他モジュールから呼ばれる CoreEngine の公開メソッド名は維持し、Core側では委譲する。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from monitoring_states import IdleState
from monitoring_states import CountdownState


class MonitoringController:
    def __init__(self, core_engine):
        self.core = core_engine

    # ------------------------------------------------------------
    # State transition
    # ------------------------------------------------------------
    def transition_to(self, new_state):
        core = self.core
        with core.state_lock:
            core.state = new_state
        core._last_clicked_path = None
        core.match_detected_at.clear()

    def get_backup_click_countdown(self) -> float:
        """
        Backupクリックのカウントダウン残り秒数を返す。
        CountdownStateでない場合は -1.0。
        """
        core = self.core
        with core.state_lock:
            if isinstance(core.state, CountdownState):
                return core.state.get_remaining_time()
        return -1.0

    # ------------------------------------------------------------
    # Monitoring start/stop
    # ------------------------------------------------------------
    def start_monitoring(self):
        core = self.core
        if not core.recognition_area:
            QMessageBox.warning(
                core.ui_manager,
                core.locale_manager.tr("warn_rec_area_not_set_title"),
                core.locale_manager.tr("warn_rec_area_not_set_text"),
            )
            return

        if core._is_reinitializing_display:
            try:
                core.logger.log("log_lazy_reinitialize_capture_backend")
                core._reinitialize_capture_backend()
            except Exception as e:
                core.logger.log("log_error_reinitialize_capture", str(e))
            finally:
                core._is_reinitializing_display = False

        if not core.is_monitoring:
            core.consecutive_capture_failures = 0
            core.is_monitoring = True
            self.transition_to(IdleState(core))
            core._click_count = 0
            core._cooldown_until = 0
            core._last_clicked_path = None
            core.screen_stability_hashes.clear()
            core.last_successful_click_time = 0
            core.is_eco_cooldown_active = False
            core._last_eco_check_time = time.time()
            core.match_detected_at.clear()

            core._session_context['consecutive_clicks'] = 0
            core.timer_session_active = True

            core.ui_manager.set_tree_enabled(False)
            core.folder_cooldowns.clear()

            if core.thread_pool:
                # キャッシュ再構築が必要な場合のみ実行（UI操作時の変更をまとめて反映）
                if core._cache_rebuild_pending:
                    # 監視開始時はツリーを有効化しない（監視中はツリーを無効化したまま）
                    core.thread_pool.submit(core._build_template_cache).add_done_callback(
                        lambda f: core._cache_builder.on_cache_build_done(f, enable_tree=False)
                    )
                    core._cache_rebuild_pending = False
                else:
                    # キャッシュ再構築が不要な場合でも、タイマースケジュールは再構築する
                    # （既存のキャッシュからタイマースケジュールを構築）
                    try:
                        core._build_timer_schedule()
                        core.cacheBuildFinished.emit(True)
                    except Exception as e:
                        core.logger.log(f"[WARN] Failed to build timer schedule: {e}")
                        core.cacheBuildFinished.emit(False)
                
                core._monitor_thread = threading.Thread(target=core.monitoring_processor.monitoring_loop, daemon=True)
                core._monitor_thread.start()
                core.updateStatus.emit("monitoring", "blue")
                core.logger.log("log_monitoring_started")
            else:
                core.logger.log("Error: Thread pool not available to start monitoring.")
                core.is_monitoring = False
                core.ui_manager.set_tree_enabled(True)

    def stop_monitoring(self):
        core = self.core
        if core.is_monitoring:
            core.is_monitoring = False
            with core.state_lock:
                core.state = None

            core.logger.log("log_monitoring_stopped")
            core.ui_manager.set_tree_enabled(True)
            if core._monitor_thread and core._monitor_thread.is_alive():
                core._monitor_thread.join(timeout=1.0)
            with core.cache_lock:
                for cache in [core.normal_template_cache, core.backup_template_cache]:
                    for item in cache.values():
                        item['best_scale'] = None
            core.match_detected_at.clear()
            core.priority_timers.clear()
            core.folder_cooldowns.clear()
            core.ocr_futures.clear()
            core.ocr_results.clear()
            core.ocr_start_times.clear()
            core.updateStatus.emit("idle", "green")


