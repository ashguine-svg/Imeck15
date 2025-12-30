"""
cache_builder.py

CoreEngine のテンプレートキャッシュ構築フローを切り出す（リファクタ: core.py分割 / B）。
挙動を変えずに、再構築の入口を集約し、スレッドプール停止などの例外を安全に扱う。
"""

from __future__ import annotations
import threading


class CacheBuilder:
    def __init__(self, core_engine):
        self.core = core_engine
        self._is_building = False  # 重複実行防止フラグ
        self._build_lock = threading.Lock()  # スレッドセーフなロック

    # ------------------------------------------------------------
    # Core work (runs in worker thread)
    # ------------------------------------------------------------
    def build_template_cache(self):
        core = self.core
        # 重複実行防止（スレッドセーフ）
        with self._build_lock:
            if self._is_building:
                core.logger.log("[DEBUG] Cache build already in progress. Skipping duplicate request.")
                return
            
            self._is_building = True
        
        try:
            with core.cache_lock:
                current_app_name = core.environment_tracker.recognition_area_app_title
                (
                    core.normal_template_cache,
                    core.backup_template_cache,
                    core.priority_timers,
                    core.folder_children_map,
                ) = core.template_manager.build_cache(
                    core.app_config,
                    core.current_window_scale,
                    core.effective_capture_scale,
                    core.is_monitoring,
                    core.priority_timers,
                    current_app_name,
                )
        finally:
            with self._build_lock:
                self._is_building = False

    # ------------------------------------------------------------
    # Completion handler (runs in callback thread context)
    # ------------------------------------------------------------
    def on_cache_build_done(self, future, enable_tree: bool = True):
        """
        キャッシュ再構築完了コールバック。
        
        Args:
            future: スレッドプールのFutureオブジェクト（Noneの可能性あり）
            enable_tree: Trueの場合、完了後にツリーを有効化する（デフォルト: True）
                        監視開始時のキャッシュ再構築では False を指定
        """
        core = self.core
        try:
            if future:
                future.result()

            # 監視中、または設定変更時などにスケジュールを再構築する
            core._build_timer_schedule()

            core.cacheBuildFinished.emit(True)
        except Exception as e:
            core.logger.log("log_cache_build_error", str(e))
            core.cacheBuildFinished.emit(False)
        finally:
            # ★★★ 修正: Qt操作はメインスレッドで実行するようにシグナルで移譲 ★★★
            # スレッドプールのコールバックから呼ばれるため、シグナルでメインスレッドに移譲
            if enable_tree:
                if hasattr(core, '_setTreeEnabledRequested'):
                    core._setTreeEnabledRequested.emit(True)
                else:
                    # フォールバック: メインスレッドから呼ばれている場合は直接呼ぶ
                    try:
                        core.ui_manager.set_tree_enabled(True)
                    except Exception:
                        pass

    # ------------------------------------------------------------
    # Public API: request rebuild
    # ------------------------------------------------------------
    def request_rebuild(self, *, disable_tree: bool = False):
        """
        キャッシュ再構築をスレッドプールに依頼する。
        disable_tree=True の場合、開始時にツリーを無効化し、依頼できなかった場合は復帰させる。
        """
        core = self.core

        # ★★★ 修正: Qt操作はメインスレッドで実行するようにシグナルで移譲 ★★★
        # ワーカースレッドから呼ばれる可能性があるため、シグナルでメインスレッドに移譲
        if disable_tree:
            # シグナルでメインスレッドに移譲
            if hasattr(core, '_setTreeEnabledRequested'):
                core._setTreeEnabledRequested.emit(False)
            else:
                # フォールバック: メインスレッドから呼ばれている場合は直接呼ぶ
                try:
                    core.ui_manager.set_tree_enabled(False)
                except Exception:
                    pass

        if core.thread_pool:
            try:
                core.thread_pool.submit(self.build_template_cache).add_done_callback(self.on_cache_build_done)
                return True
            except RuntimeError:
                # アプリ終了時などに発生しやすいので無視するかログ出すだけにする
                core.logger.log("[WARN] Thread pool is shutting down. Skipping cache rebuild.")
        else:
            core.logger.log("[WARN] Thread pool not available. Skipping cache rebuild.")

        if disable_tree:
            # シグナルでメインスレッドに移譲
            if hasattr(core, '_setTreeEnabledRequested'):
                core._setTreeEnabledRequested.emit(True)
            else:
                # フォールバック: メインスレッドから呼ばれている場合は直接呼ぶ
                try:
                    core.ui_manager.set_tree_enabled(True)
                except Exception:
                    pass
        return False


