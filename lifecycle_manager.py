"""
lifecycle_manager.py

CoreEngine のライフサイクル管理（Extended Lifecycle Hooks）を切り出す（リファクタ: core.py分割 / D）。
セッション復旧、ウィンドウ再ロック、PID検索、スケール計算などの処理を集約する。
"""

from __future__ import annotations

import sys
import os
import time
import threading
import subprocess
import shutil
import psutil

# Windows用
try:
    import win32gui
    import win32process
except ImportError:
    win32gui = None
    win32process = None


class LifecycleManager:
    def __init__(self, core_engine):
        self.core = core_engine

    # ------------------------------------------------------------
    # Session Context Attachment
    # ------------------------------------------------------------
    def attach_session_context(self, hwnd, title):
        """認識範囲設定時にセッションコンテキストをアタッチする。"""
        core = self.core
        hooks_config = core.app_config.get('extended_lifecycle_hooks', {})
        if not hooks_config.get('active', False):
            return

        core._lifecycle_hook_active = False
        core._session_context = {
            'pid': None,
            'exec_path': None,
            'resource_id': hooks_config.get('resource_link_id', ''),
            'consecutive_clicks': 0,
            # 復旧後にウィンドウ再ロックするためのヒント
            'window_title': title or None,
        }

        target_proc_name = hooks_config.get('process_marker', '').lower()
        target_win_name = hooks_config.get('window_context_marker', '').lower()

        try:
            pid = 0
            proc_name = ""
            exe_path = ""

            if sys.platform == 'win32' and win32process:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
            
            elif sys.platform.startswith('linux'):
                try:
                    res = subprocess.run(['xdotool', 'getwindowpid', str(hwnd)], capture_output=True, text=True)
                    if res.returncode == 0:
                        pid = int(res.stdout.strip())
                except Exception:
                    pass

            if pid > 0 and psutil.pid_exists(pid):
                proc = psutil.Process(pid)
                proc_name = proc.name().lower()
                try:
                    exe_path = proc.exe()
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    exe_path = ""

                match_proc = (target_proc_name and target_proc_name in proc_name)
                match_title = (target_win_name and target_win_name in title.lower())

                if match_proc or match_title:
                    core._lifecycle_hook_active = True
                    core._session_context['pid'] = pid
                    core._session_context['exec_path'] = exe_path
                    core.logger.log("[INFO] Session context attached. Lifecycle management active.")
                else:
                    core.logger.log("[DEBUG] Session context mismatch. Hooks inactive.")

        except Exception as e:
            core.logger.log(f"[WARN] Failed to attach session context: {e}")

    # ------------------------------------------------------------
    # Window Scale Calculation (Recovery)
    # ------------------------------------------------------------
    def compute_and_apply_window_scale_no_prompt(self, title: str, rect: tuple):
        """
        復旧時用: ユーザーにプロンプトを出さずに、保存済みベースサイズがあればスケールを再計算して適用する。
        既存の「auto_scale.use_window_scale」設定を尊重する。
        """
        core = self.core
        try:
            if not title or not rect or len(rect) != 4:
                return
            width = max(0, int(rect[2] - rect[0]))
            height = max(0, int(rect[3] - rect[1]))
            if width <= 0 or height <= 0:
                return

            scales_data = core.config_manager.load_window_scales()
            base_dims = scales_data.get(title) if isinstance(scales_data, dict) else None
            if not base_dims:
                # ベース未登録: 触らない（現状の挙動に合わせる）
                core.actual_window_scale = None
                core.current_window_scale = None
                core.windowScaleCalculated.emit(0.0)
                return

            base_w = float(base_dims.get('width', 0) or 0)
            calc_scale = (float(width) / base_w) if base_w > 0 else 1.0
            core.actual_window_scale = calc_scale

            # ほぼ1.0なら補正せず 1.0 扱い
            if 0.995 <= calc_scale <= 1.005:
                core.current_window_scale = 1.0
                core.windowScaleCalculated.emit(1.0)
                return

            use_window_scale = bool(core.app_config.get('auto_scale', {}).get('use_window_scale', False))
            if use_window_scale:
                core.current_window_scale = calc_scale
                core.windowScaleCalculated.emit(calc_scale)
            else:
                # 設定がOFFなら、勝手に適用しない（選択フローと同じ思想）
                core.current_window_scale = None
                core.windowScaleCalculated.emit(0.0)
        except Exception as e:
            core.logger.log(f"[WARN] Failed to compute/apply window scale on recovery: {e}")

    # ------------------------------------------------------------
    # Window Lookup by PID
    # ------------------------------------------------------------
    def find_window_rect_for_pid(self, pid: int, title_hint: str | None = None):
        """
        PIDから対象アプリのウィンドウ（ID/HWND）と矩形(rect)とタイトルを取得する（OS別）。
        戻り値: (hwnd_or_window_id, rect(x1,y1,x2,y2), title) or (None, None, None)
        """
        core = self.core
        try:
            title_hint_l = (title_hint or "").lower().strip()

            if sys.platform == 'win32' and win32gui and win32process:
                candidates = []

                def enum_cb(hwnd, _):
                    try:
                        if not win32gui.IsWindowVisible(hwnd):
                            return
                        _, wpid = win32process.GetWindowThreadProcessId(hwnd)
                        if int(wpid) != int(pid):
                            return
                        title = win32gui.GetWindowText(hwnd) or ""
                        candidates.append((hwnd, title))
                    except Exception:
                        return

                win32gui.EnumWindows(enum_cb, None)
                if not candidates:
                    return None, None, None

                # タイトルヒントがあれば優先
                chosen = None
                if title_hint_l:
                    for hwnd, title in candidates:
                        if title_hint_l in (title or "").lower():
                            chosen = (hwnd, title)
                            break
                if not chosen:
                    chosen = candidates[0]

                hwnd, title = chosen
                # クライアント領域 -> 画面座標
                client_rect = win32gui.GetClientRect(hwnd)
                left, top = win32gui.ClientToScreen(hwnd, (0, 0))
                right = left + int(client_rect[2])
                bottom = top + int(client_rect[3])
                if right <= left or bottom <= top:
                    return None, None, None
                return hwnd, (left, top, right, bottom), title

            if sys.platform.startswith('linux'):
                # Waylandだと取得できないことが多い
                if os.environ.get('WAYLAND_DISPLAY'):
                    core.logger.log("[WARN] Wayland環境のため、復旧後のウィンドウ自動再ロックに失敗する可能性があります。")

                # xdotool 必須
                if not shutil.which('xdotool') or not shutil.which('xwininfo'):
                    return None, None, None

                # PIDに紐づくウィンドウID一覧を取得
                res = subprocess.run(
                    ['xdotool', 'search', '--pid', str(int(pid)), '--onlyvisible'],
                    capture_output=True, text=True, timeout=2, check=False
                )
                if res.returncode != 0 or not res.stdout.strip():
                    return None, None, None

                win_ids = [w.strip() for w in res.stdout.strip().splitlines() if w.strip().isdigit()]
                if not win_ids:
                    return None, None, None

                # タイトルでフィルタ（あれば）
                chosen_id = None
                chosen_title = None
                if title_hint_l:
                    for wid in win_ids:
                        name_res = subprocess.run(
                            ['xdotool', 'getwindowname', wid],
                            capture_output=True, text=True, timeout=2, check=False
                        )
                        name = (name_res.stdout or "").strip()
                        if title_hint_l in name.lower():
                            chosen_id = wid
                            chosen_title = name
                            break
                if not chosen_id:
                    chosen_id = win_ids[0]
                    name_res = subprocess.run(
                        ['xdotool', 'getwindowname', chosen_id],
                        capture_output=True, text=True, timeout=2, check=False
                    )
                    chosen_title = (name_res.stdout or "").strip()

                info_res = subprocess.run(
                    ['xwininfo', '-id', chosen_id],
                    capture_output=True, text=True, timeout=2, check=False
                )
                if info_res.returncode != 0 or not info_res.stdout:
                    return None, None, None

                info = {}
                for line in info_res.stdout.splitlines():
                    if ':' in line:
                        k, v = line.split(':', 1)
                        info[k.strip()] = v.strip()

                left = int(info.get('Absolute upper-left X', '0'))
                top = int(info.get('Absolute upper-left Y', '0'))
                width = int(info.get('Width', '0'))
                height = int(info.get('Height', '0'))
                if width <= 0 or height <= 0:
                    return None, None, None
                rect = (left, top, left + width, top + height)
                return int(chosen_id), rect, (chosen_title or None)

            return None, None, None
        except Exception as e:
            core.logger.log(f"[WARN] Failed to locate window by PID: {e}")
            return None, None, None

    # ------------------------------------------------------------
    # Process Lookup
    # ------------------------------------------------------------
    def find_process_by_path(self, target_path):
        """実行パスからプロセスIDを検索する。"""
        if not target_path:
            return None
        for proc in psutil.process_iter(['pid', 'exe']):
            try:
                if proc.info['exe'] == target_path:
                    return proc.info['pid']
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return None

    # ------------------------------------------------------------
    # Recovery: Window Re-lock
    # ------------------------------------------------------------
    def relock_capture_after_recovery(self, new_pid: int):
        """
        対象アプリ再起動後に、ウィンドウID/HWND と recognition_area を再取得して再ロックする。
        監視ループ・クイックキャプチャ・通常キャプチャに影響しないよう、UI操作/プロンプトは行わない。
        """
        core = self.core
        try:
            # 「ウィンドウ指定」の場合のみ対象（矩形/フルスクはユーザー設定なので触らない）
            title_from_env = getattr(core.environment_tracker, 'recognition_area_app_title', None)
            title_hint = core._session_context.get('window_title') or title_from_env
            if not title_hint:
                return

            hooks_conf = core.app_config.get('extended_lifecycle_hooks', {})
            marker = (hooks_conf.get('window_context_marker') or "").strip()
            # window_context_marker があればそれを優先（部分一致）
            match_hint = marker or title_hint

            hwnd, rect, title = self.find_window_rect_for_pid(new_pid, match_hint)
            if not hwnd or not rect:
                core.logger.log("[WARN] Recovery succeeded but window re-lock failed (no window found).")
                return

            # 状態更新（GIL下での単純代入は原子的なので、監視スレッド側への影響を最小化）
            core.target_hwnd = hwnd
            core.recognition_area = rect

            # DXCam の target_hwnd も更新（Windowsのみ）
            try:
                if sys.platform == 'win32' and hasattr(core.capture_manager, 'dxcam_sct') and core.capture_manager.dxcam_sct and hasattr(core.capture_manager.dxcam_sct, 'target_hwnd'):
                    core.capture_manager.dxcam_sct.target_hwnd = hwnd
            except Exception as e_dx:
                core.logger.log(f"[WARN] Failed to set DXCam target HWND on recovery: {e_dx}")

            # EnvironmentTracker/ツリー更新用のコンテキストを再設定
            if title:
                core.environment_tracker.on_rec_area_set("window", title)
                core.appContextChanged.emit(title)
                # セッションヒントも更新（次回復旧用）
                core._session_context['window_title'] = title

            # ウィンドウスケール再計算（プロンプト無し）
            if title:
                self.compute_and_apply_window_scale_no_prompt(title, rect)

            # スケールが変わる可能性があるため、テンプレキャッシュを再構築（監視中でも安全）
            core._cache_builder.request_rebuild(disable_tree=False)

            core.logger.log(f"[INFO] Recovery re-locked capture window. pid={new_pid} hwnd={hwnd} rect={rect}")
        except Exception as e:
            core.logger.log(f"[WARN] Failed to re-lock capture after recovery: {e}")

    # ------------------------------------------------------------
    # Recovery: Session Recovery Execution
    # ------------------------------------------------------------
    def execute_session_recovery(self):
        """セッション復旧を実行する（非同期）。"""
        core = self.core
        if core._recovery_in_progress:
            return

        core._recovery_in_progress = True
        core.logger.log("[INFO] Initiating session recovery... Monitoring paused temporarily.")

        def _recovery_task():
            try:
                pid = core._session_context.get('pid')
                if pid:
                    core.action_manager.perform_session_cleanup(pid)
                
                exec_path = core._session_context.get('exec_path')
                res_id = core._session_context.get('resource_id')
                
                success = core.action_manager.perform_session_reload(exec_path, res_id)
                
                if success:
                    core.logger.log("[INFO] Waiting for session availability...")
                    time.sleep(15) 
                    
                    new_pid = self.find_process_by_path(exec_path)
                    if new_pid:
                        core._session_context['pid'] = new_pid
                        core._session_context['consecutive_clicks'] = 0
                        core.logger.log(f"[INFO] Session re-hooked. New PID: {new_pid}")
                        # ★ 復旧後にウィンドウ指定キャプチャを再ロック（UI映り込み/座標消失対策）
                        self.relock_capture_after_recovery(new_pid)
                    else:
                        core.logger.log("[WARN] Failed to re-hook session automatically.")

            except Exception as e:
                core.logger.log(f"[ERROR] Recovery sequence failed: {e}")
            finally:
                core._recovery_in_progress = False
                core.logger.log("[INFO] Recovery sequence finished. Resuming monitoring.")

        threading.Thread(target=_recovery_task, daemon=True).start()

