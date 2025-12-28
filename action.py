# action.py
# ★★★ (拡張) ライフサイクル管理用のセッション操作メソッドを追加 ★★★
# ★★★ (修正) フリーズ時は問答無用でSIGKILLし、OSの応答なしダイアログを回避する ★★★
# ★★★ (修正) Windowsで対象ウィンドウを強力に最前面化するロジックを追加 ★★★

import sys
import time
import pyautogui
import random
import psutil
import subprocess
import os
from pathlib import Path

# Windows API用のインポート (プラットフォーム依存)
if sys.platform == 'win32':
    try:
        import ctypes
        import win32gui
        import win32con
        import win32process
        import win32api
        
        # 入力ブロック用
        block_input_func = ctypes.windll.user32.BlockInput
        block_input_func.argtypes = [ctypes.wintypes.BOOL]
        block_input_func.restype = ctypes.wintypes.BOOL
        INPUT_BLOCK_AVAILABLE = True
    except (ImportError, AttributeError, OSError):
        def block_input_func(block):
            pass
        INPUT_BLOCK_AVAILABLE = False
else:
    def block_input_func(block):
        pass
    INPUT_BLOCK_AVAILABLE = False


def block_input(block: bool):
    """
    ユーザーからのマウス・キーボード入力をブロックまたは許可します。
    管理者権限で実行されている場合に最も効果的です。
    """
    if INPUT_BLOCK_AVAILABLE:
        try:
            block_input_func(block)
        except Exception as e:
            # Loggerが使えない低レベル関数のため print のまま
            print(f"[ERROR] Failed to change input block state: {e}")

class ActionManager:
    """
    ウィンドウ操作やマウスクリックなど、PC操作に関連する機能を管理するクラス。
    拡張されたライフサイクル管理機能（セッションのクリーンアップとリロード）も含みます。
    """
    def __init__(self, logger):
        self.logger = logger

    def _activate_window(self, target_hwnd) -> bool:
        """
        指定されたウィンドウをフォアグラウンドにし、アクティブ化を試みます。
        Windowsの「前面化ブロック」機能を回避するためのハックを含みます。
        
        Returns:
            bool: アクティブ化に成功したかどうか。
        """
        if not (sys.platform == 'win32' and target_hwnd):
            # Windows以外、または対象HWNDなし(矩形選択など)はそのまま成功扱い
            return True

        # ターゲットが既にフォアグラウンドなら何もしない
        try:
            current_foreground = win32gui.GetForegroundWindow()
            if current_foreground == target_hwnd:
                return True
        except Exception:
            pass

        try:
            # 現在フォアグラウンドのウィンドウのスレッドIDを取得
            foreground_hwnd = win32gui.GetForegroundWindow()
            foreground_thread_id = 0
            if foreground_hwnd:
                foreground_thread_id, _ = win32process.GetWindowThreadProcessId(foreground_hwnd)
            
            # ターゲットウィンドウのスレッドIDを取得
            target_thread_id, _ = win32process.GetWindowThreadProcessId(target_hwnd)
            
            # 自分自身のスレッドIDを取得
            current_thread_id = win32api.GetCurrentThreadId()

            # 入力処理のアタッチ（フォアグラウンドプロセスとターゲットプロセスの両方にアタッチを試みる）
            # これにより「自分がアクティブウィンドウの所有者である」とOSに誤認させ、操作権限を得る
            attached_foreground = False
            attached_target = False

            if foreground_thread_id != current_thread_id and foreground_thread_id != 0:
                try:
                    win32process.AttachThreadInput(foreground_thread_id, current_thread_id, True)
                    attached_foreground = True
                except Exception:
                    pass
            
            if target_thread_id != current_thread_id and target_thread_id != 0:
                try:
                    win32process.AttachThreadInput(target_thread_id, current_thread_id, True)
                    attached_target = True
                except Exception:
                    pass

            try:
                # 最小化されている場合は元に戻す (SW_RESTORE = 9)
                if win32gui.IsIconic(target_hwnd):
                    win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)
                else:
                    # 最小化されていなくても、隠れている可能性があるので表示を強制
                    win32gui.ShowWindow(target_hwnd, win32con.SW_SHOW)

                # Zオーダーのトップに持ってくる
                try:
                    win32gui.BringWindowToTop(target_hwnd)
                except Exception:
                    pass

                # --- リトライロジック ---
                retries = 3
                while retries > 0:
                    try:
                        win32gui.SetForegroundWindow(target_hwnd)
                    except Exception:
                        pass 
                    
                    # OSのスイッチ完了待ち
                    time.sleep(0.05)
                    
                    if win32gui.GetForegroundWindow() == target_hwnd:
                        return True
                        
                    retries -= 1
                # --- ▲▲▲ ---
                
                # 最終手段: Altキーハック
                # SetForegroundWindowが拒否された場合、Altキー入力イベントを偽装して
                # OSに「ユーザー操作があった」と認識させ、前面化を許可させる
                if win32gui.GetForegroundWindow() != target_hwnd:
                    try:
                        import ctypes
                        # Alt key press
                        ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
                        # Alt key release
                        ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
                        win32gui.SetForegroundWindow(target_hwnd)
                    except Exception:
                        pass

            finally:
                # 処理が終わったら、必ずデタッチする (忘れると入力がおかしくなる)
                if attached_foreground:
                    win32process.AttachThreadInput(foreground_thread_id, current_thread_id, False)
                if attached_target:
                    win32process.AttachThreadInput(target_thread_id, current_thread_id, False)

            # 最終チェック
            if win32gui.GetForegroundWindow() == target_hwnd:
                return True

            # リトライしても失敗した場合
            self.logger.log("log_activate_window_failed", str(target_hwnd))
            return False

        except Exception as e:
            self.logger.log("log_activate_window_error", str(e))
            # アクティブ化に失敗しても、クリック処理自体は続行させる（非アクティブでも通る場合があるため）
            return False

    def execute_click(self, match_info, recognition_area, target_hwnd, effective_capture_scale, window_scale=1.0):
        """
        マッチング情報に基づいてクリックを実行します。
        """
        
        # --- ▼▼▼ 修正: クリック前にウィンドウをアクティブ化 ▼▼▼ ---
        if target_hwnd:
            # アクティブ化を試みる (失敗してもログを出すだけで処理は止めない)
            self._activate_window(target_hwnd)
        # --- ▲▲▲ 修正完了 ▲▲▲ ---

        block_input(True)
        try:
            settings = match_info['settings']
            match_rect_in_rec_area = match_info['rect']
            path = Path(match_info['path'])
            
            base_scale = effective_capture_scale * (window_scale if window_scale else 1.0)
            scale = base_scale 
            
            rec_area_offset_x, rec_area_offset_y = (recognition_area[0], recognition_area[1]) if recognition_area else (0, 0)
            
            roi_offset_x, roi_offset_y = 0, 0
            if settings.get('roi_enabled'):
                roi_mode = settings.get('roi_mode', 'fixed')
                roi_rect = None
                
                if roi_mode == 'variable':
                    roi_rect = settings.get('roi_rect_variable')
                else:
                    roi_rect = settings.get('roi_rect')
                
                if roi_rect:
                    roi_offset_x = max(0, roi_rect[0])
                    roi_offset_y = max(0, roi_rect[1])

            click_offset_x_scaled = 0.0
            click_offset_y_scaled = 0.0
            
            if settings.get('point_click') and settings.get('click_position'):
                click_pos_in_template = settings['click_position']
                click_offset_x_scaled = (click_pos_in_template[0] - roi_offset_x) * scale
                click_offset_y_scaled = (click_pos_in_template[1] - roi_offset_y) * scale
            elif settings.get('range_click') and settings.get('click_rect'):
                click_rect_in_template = settings['click_rect']
                rect_x1_in_roi = click_rect_in_template[0] - roi_offset_x
                rect_y1_in_roi = click_rect_in_template[1] - roi_offset_y
                rect_x2_in_roi = click_rect_in_template[2] - roi_offset_x
                rect_y2_in_roi = click_rect_in_template[3] - roi_offset_y
                x1_offset_scaled = rect_x1_in_roi * scale
                y1_offset_scaled = rect_y1_in_roi * scale
                x2_offset_scaled = rect_x2_in_roi * scale
                y2_offset_scaled = rect_y2_in_roi * scale
                if settings.get('random_click', True):
                    min_x, max_x = min(x1_offset_scaled, x2_offset_scaled), max(x1_offset_scaled, x2_offset_scaled)
                    min_y, max_y = min(y1_offset_scaled, y2_offset_scaled), max(y1_offset_scaled, y2_offset_scaled)
                    click_offset_x_scaled = random.uniform(min_x, max_x)
                    click_offset_y_scaled = random.uniform(min_y, max_y)
                else:
                    click_offset_x_scaled = (x1_offset_scaled + x2_offset_scaled) / 2
                    click_offset_y_scaled = (y1_offset_scaled + y2_offset_scaled) / 2
            else:
                match_width_scaled = match_rect_in_rec_area[2] - match_rect_in_rec_area[0]
                match_height_scaled = match_rect_in_rec_area[3] - match_rect_in_rec_area[1]
                click_offset_x_scaled = match_width_scaled / 2
                click_offset_y_scaled = match_height_scaled / 2

            click_x_in_rec_area_scaled = match_rect_in_rec_area[0] + click_offset_x_scaled
            click_y_in_rec_area_scaled = match_rect_in_rec_area[1] + click_offset_y_scaled
            
            click_x_float = rec_area_offset_x + (click_x_in_rec_area_scaled / effective_capture_scale)
            click_y_float = rec_area_offset_y + (click_y_in_rec_area_scaled / effective_capture_scale)
            
            screen_width, screen_height = pyautogui.size()
            final_click_x = int(click_x_float)
            final_click_y = int(click_y_float)
            
            if not (1 <= final_click_x < screen_width - 1 and 1 <= final_click_y < screen_height - 1):
                self.logger.log("log_warn_click_out_of_bounds", final_click_x, final_click_y)
                return {'success': False, 'path': str(path)}
            
            try:
                # 右クリックONなら右クリックで実行（デフォルトは左クリック）
                btn = 'right' if bool(settings.get('right_click', False)) else 'left'
                pyautogui.click(final_click_x, final_click_y, button=btn)
                
                scale_suffix = self.logger.locale_manager.tr(
                    "log_click_success_scale", 
                    f"{match_info.get('scale', 1.0):.3f}"
                )
                
                base_message_part = self.logger.locale_manager.tr(
                    "log_click_success",
                    path.name,
                    final_click_x,
                    final_click_y,
                    f"{match_info['confidence']:.2f}"
                )
                
                final_message = base_message_part + scale_suffix
                
                self.logger.log(final_message)
                
                return {'success': True, 'path': str(path)}

            except pyautogui.FailSafeException:
                self.logger.log("log_pyautogui_failsafe")
                return {'success': False, 'path': str(path)}

        except Exception as e:
            self.logger.log("log_click_error", str(e))
            return {'success': False, 'path': match_info.get('path', 'Unknown')}
        finally:
            block_input(False)

    # --- ▼▼▼ 拡張ライフサイクル管理機能 (隠しAPI) ▼▼▼ ---

    def perform_session_cleanup(self, pid: int) -> bool:
        """
        対象のセッション（プロセス）をクリーンアップします。
        ★ 修正: いきなりSIGKILLを使用してOSの応答なしダイアログを回避する
        """
        try:
            if not psutil.pid_exists(pid):
                return False

            proc = psutil.Process(pid)
            self.logger.log(f"[INFO] Session cleanup initiated for PID: {pid}")
            
            # --- 子プロセスも巻き込んでKillする (Proton対策) ---
            try:
                children = proc.children(recursive=True)
                for child in children:
                    try:
                        child.kill() # 子プロセスも即殺
                    except psutil.NoSuchProcess:
                        pass
            except Exception:
                pass
            # ----------------------------------------------

            # 親プロセスを強制終了 (SIGKILL)
            # terminate()は使わない（応答なしダイアログが出るため）
            self.logger.log("[INFO] Forcing resource release (SIGKILL) immediately.")
            try:
                proc.kill()
            except psutil.NoSuchProcess:
                pass 

            # キル後の完全消滅確認 (最大5秒待機)
            for _ in range(10):
                if not psutil.pid_exists(pid):
                    self.logger.log("[INFO] Session terminated gracefully.")
                    return True
                time.sleep(0.5)
            
            self.logger.log("[WARN] Session cleanup timed out. Process might be a zombie.")
            return True 

        except (psutil.NoSuchProcess, psutil.AccessDenied, Exception) as e:
            self.logger.log(f"[WARN] Session cleanup exception: {e}")
            return False

    def perform_session_reload(self, exec_path: str, resource_id: str = None) -> bool:
        """
        新しいセッションをリロード（再開）します。
        resource_id がある場合は外部プロトコル経由、ない場合は直接実行します。
        """
        try:
            self.logger.log(f"[INFO] Reloading session context...")

            if resource_id:
                # 外部プロトコル (URIスキーム) を使用してリソースを呼び出す
                uri = f"steam://rungameid/{resource_id}"
                self.logger.log(f"[INFO] Triggering external protocol: {resource_id}")
                
                if sys.platform == 'win32':
                    os.startfile(uri)
                elif sys.platform == 'darwin':
                    subprocess.run(['open', uri])
                else:
                    # Linuxの場合、バックグラウンドで実行するためにPopenを使う
                    subprocess.Popen(['xdg-open', uri], 
                                     stdout=subprocess.DEVNULL, 
                                     stderr=subprocess.DEVNULL)
                
                return True

            elif exec_path and os.path.exists(exec_path):
                # 実行ファイルを直接コンテキストとしてロード
                self.logger.log(f"[INFO] Loading executable context directly: {Path(exec_path).name}")
                
                # ワーキングディレクトリを実行ファイルの場所に合わせる
                work_dir = os.path.dirname(exec_path)
                subprocess.Popen(exec_path, cwd=work_dir)
                
                return True
            
            else:
                self.logger.log("[ERROR] Reload failed: No valid resource context found.")
                return False

        except Exception as e:
            self.logger.log(f"[ERROR] Session reload exception: {e}")
            return False
    # --- ▲▲▲ 追加完了 ▲▲▲ ---
