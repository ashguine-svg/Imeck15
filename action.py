# action.py

import sys
import time
import pyautogui
import random
from pathlib import Path

if sys.platform == 'win32':
    try:
        import ctypes
        import win32gui
        import win32con
        import win32process
        import win32api
        block_input_func = ctypes.windll.user32.BlockInput
        block_input_func.argtypes = [ctypes.wintypes.BOOL]
        block_input_func.restype = ctypes.wintypes.BOOL
        INPUT_BLOCK_AVAILABLE = True
        # ★★★ 1. print を削除 (Loggerがmain.pyで担当) ★★★
    except (ImportError, AttributeError, OSError):
        def block_input_func(block):
            pass
        INPUT_BLOCK_AVAILABLE = False
        # ★★★ 2. print を削除 (Loggerがmain.pyで担当) ★★★
else:
    def block_input_func(block):
        pass
    INPUT_BLOCK_AVAILABLE = False
    # ★★★ 3. print を削除 (Loggerがmain.pyで担当) ★★★


def block_input(block: bool):
    """
    ユーザーからのマウス・キーボード入力をブロックまたは許可します。
    管理者権限で実行されている場合に最も効果的です。
    """
    if INPUT_BLOCK_AVAILABLE:
        try:
            block_input_func(block)
        except Exception as e:
            # ★★★ 4. print を変更 (ただし、ここはLoggerが使えない低レベル関数のため print のまま) ★★★
            print(f"[ERROR] Failed to change input block state: {e}")

class ActionManager:
    """
    ウィンドウ操作やマウスクリックなど、PC操作に関連する機能を管理するクラス。
    """
    def __init__(self, logger):
        self.logger = logger

    def _activate_window(self, target_hwnd):
        """
        指定されたウィンドウをフォアグラウンドにし、アクティブ化を試みます。
        タスクバーが点滅する問題を回避するための高度な手法を使用します。
        """
        if not (sys.platform == 'win32' and target_hwnd):
            return

        # ターゲットが既にフォアグラウンドなら何もしない
        if win32gui.GetForegroundWindow() == target_hwnd:
            return

        try:
            # 現在フォアグラウンドのウィンドウのスレッドIDを取得
            foreground_thread_id, _ = win32process.GetWindowThreadProcessId(win32gui.GetForegroundWindow())
            # 自分自身のスレッドIDを取得
            current_thread_id = win32api.GetCurrentThreadId()

            # フォアグラウンドスレッドの入力処理にアタッチする
            win32process.AttachThreadInput(foreground_thread_id, current_thread_id, True)

            try:
                # 最小化されている場合は通常の状態に戻す
                if win32gui.IsIconic(target_hwnd):
                    win32gui.ShowWindow(target_hwnd, win32con.SW_NORMAL)
                
                # ウィンドウをフォアグラウンドに設定する
                win32gui.SetForegroundWindow(target_hwnd)
            finally:
                # 処理が終わったら、必ずデタッチする
                win32process.AttachThreadInput(foreground_thread_id, current_thread_id, False)

            time.sleep(0.2) # ウィンドウが切り替わるのを少し待つ
            
            # ★★★ 5. self.logger.log に変更 (翻訳キー使用) ★★★
            if win32gui.GetForegroundWindow() == target_hwnd:
                self.logger.log("log_activate_window_success", win32gui.GetWindowText(target_hwnd))
            else:
                 self.logger.log("log_activate_window_failed", win32gui.GetWindowText(target_hwnd))

        except Exception as e:
            self.logger.log("log_activate_window_error", str(e))

    def execute_click(self, match_info, recognition_area, target_hwnd, effective_capture_scale):
        """
        マッチング情報に基づいてクリックを実行します。

        Args:
            match_info (dict): _find_best_matchから得られるマッチング結果。
            recognition_area (tuple): (x1, y1, x2, y2) の形式の認識エリア座標。
            target_hwnd (int): 操作対象のウィンドウハンドル (Windowsのみ)。
            effective_capture_scale (float): 軽量化モードなどで適用されているキャプチャのスケール。

        Returns:
            dict: クリックが成功したかどうかと関連情報を含む辞書。
                  例: {'success': True, 'path': '/path/to/image.png'}
        """
        self._activate_window(target_hwnd)

        block_input(True)
        try:
            settings = match_info['settings']
            match_rect_in_rec_area = match_info['rect']
            scale = match_info.get('scale', 1.0)
            path = Path(match_info['path'])
            
            rec_area_offset_x, rec_area_offset_y = (recognition_area[0], recognition_area[1]) if recognition_area else (0, 0)
            
            roi_offset_x, roi_offset_y = 0, 0
            if settings.get('roi_enabled') and settings.get('roi_rect'):
                roi_rect = settings['roi_rect']
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
                # ★★★ 6. self.logger.log に変更 (翻訳キー使用) ★★★
                self.logger.log("log_warn_click_out_of_bounds", final_click_x, final_click_y)
                return {'success': False, 'path': str(path)}
            
            try:
                pyautogui.click(final_click_x, final_click_y)
                
                # ★★★ 7. self.logger.log に変更 (翻訳キー使用) - 最終修正案 ★★★
                
                # 1. log_click_success_scale の翻訳文字列を取得
                # JSONファイルに手を加えないため、この時点で完全な文字列に組み立てる。
                scale_suffix = self.logger.locale_manager.tr(
                    "log_click_success_scale", 
                    f"{match_info.get('scale', 1.0):.3f}"
                )
                
                # 2. log_click_success_full (または log_click_success) の翻訳結果を取得し、スケール情報を結合する
                # log_click_success_full が5つの %s を持つという既存の仮定を基に、引数を渡す。
                
                # まず、基本のメッセージ（スケールなし）を log_click_success を使って生成（4つの引数）
                base_message_part = self.logger.locale_manager.tr(
                    "log_click_success",
                    path.name,
                    final_click_x,
                    final_click_y,
                    f"{match_info['confidence']:.2f}"
                )
                
                # 3. 完成した文字列を Logger に渡す（引数なし）
                # base_message_part が既に完全な文字列（スケールなし）であることを利用し、
                # それにスケール情報を直接結合して Logger に渡す。
                
                final_message = base_message_part + scale_suffix
                
                self.logger.log(final_message)
                
                return {'success': True, 'path': str(path)}

            except pyautogui.FailSafeException:
                # ★★★ 8. self.logger.log に変更 (翻訳キー使用) ★★★
                self.logger.log("log_pyautogui_failsafe")
                return {'success': False, 'path': str(path)}

        except Exception as e:
            # ★★★ 9. self.logger.log に変更 (翻訳キー使用) ★★★
            self.logger.log("log_click_error", str(e))
            return {'success': False, 'path': match_info.get('path', 'Unknown')}
        finally:
            block_input(False)
