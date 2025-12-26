# capture.py (DXCam スケール対応版)
# ★★★ (根本修正) MSSのスレッドセーフティに対応するため、
#     threading.local() と 再初期化フラグ (mss_reinit_required) を使用する方式に変更 ★★★
# ★★★ MSSの座標がズレる問題を修正 (region=None の場合、monitors[0]辞書を渡す) ★★★
# ★★★ MSSの内部スレッドローカルキャッシュ (_MSS_DISPLAY) を明示的に削除する ★★★

import sys
import os
import mss
import cv2
import numpy as np
import threading
from pathlib import Path
from PySide6.QtCore import QObject

try:
    OPENCL_AVAILABLE = cv2.ocl.haveOpenCL()
except Exception:
    OPENCL_AVAILABLE = False

DXCAM_AVAILABLE = False
if sys.platform == 'win32':
    try:
        import dxcam
        import win32api
        import win32con
        import win32gui
        DXCAM_AVAILABLE = True
    except ImportError:
        win32gui = None
        DXCAM_AVAILABLE = False
else:
    win32gui = None
    # --- ▼▼▼ 修正: MSSの内部キャッシュを削除するためにインポート ▼▼▼ ---
    try:
        from mss.linux import _MSS_DISPLAY
    except ImportError:
        _MSS_DISPLAY = None
    # --- ▲▲▲ 修正完了 ▲▲▲ ---


class CaptureManager(QObject):
    """
    画面キャプチャのインターフェース。
    キャプチャバックエンドのインスタンスを保持し、効率的なキャプチャを行う。
    """
    def __init__(self, logger):
        super().__init__()
        self.logger = logger
        
        self.lock = threading.RLock()
        
        # --- ▼▼▼ 修正: threading.local() に戻す ▼▼▼ ---
        self.mss_thread_local = threading.local()
        self.mss_reinit_required = False # ★ 再初期化フラグ
        # --- ▲▲▲ 修正完了 ▲▲▲ ---
        
        self.dxcam_sct = None
        self.is_dxcam_ready = False
        
        self.target_width, self.target_height = 0, 0
        
        self.dxcam_error_count = 0
        self.DXCAM_ERROR_THRESHOLD = 5 
        
        is_dxcam_preferred = False
        if DXCAM_AVAILABLE:
            self.logger.log("log_dxcam_available")
            
            # --- ▼▼▼ 修正箇所: with ステートメントを削除 ▼▼▼ ---
            d = None # インスタンスを保持する変数
            try:
                d = dxcam.create(output_idx=None) # with を使わずに作成
                if d and d.grab() is not None:
                    is_dxcam_preferred = True
                    self.logger.log("log_dxcam_check_success")
                else:
                    # grab() が None を返した場合
                    is_dxcam_preferred = False
                    self.logger.log("log_dxcam_check_failed", "grab() returned None or create() failed.")
            except Exception as e:
                self.logger.log("log_dxcam_check_failed", str(e))
                is_dxcam_preferred = False
            finally:
                if d:
                    try:
                        d.release() # チェックが終わったら解放
                    except Exception:
                        pass # 解放失敗は無視
                    
                    # --- ▼▼▼ ★ 修正箇所: del d を追加 ★ ▼▼▼ ---
                    del d
                    # --- ▲▲▲ 修正完了 ▲▲▲ ---
            # --- ▲▲▲ 修正完了 ▲▲▲ ---
            
        else:
            if sys.platform == 'win32':
                self.logger.log("log_dxcam_unavailable")
            else:
                self.logger.log("log_non_windows_mss")
        
        self.current_method = None 
        self.set_capture_method('dxcam' if is_dxcam_preferred else 'mss')

    def prime_mss(self):
        """
        Main threadでMSSの初期化を試み、Linuxでの潜在的なデッドロックを回避する。
        """
        if self.current_method == 'mss' and sys.platform != 'win32':
            try:
                self.logger.log("log_mss_priming")
                if not hasattr(self.mss_thread_local, 'sct'):
                    self.mss_thread_local.sct = mss.mss()
                    
                with self.lock:
                    self.mss_thread_local.sct.grab({"top": 0, "left": 0, "width": 1, "height": 1})
                self.logger.log("log_mss_priming_success")
            except Exception as e:
                self.logger.log("log_mss_priming_failed", str(e))

    # --- ▼▼▼ 修正: MSSの内部キャッシュを削除するヘルパーメソッド ▼▼▼ ---
    def _cleanup_mss_thread_local(self, thread_name: str):
        """指定されたスレッドのMSSインスタンスと内部キャッシュをクリーンアップする"""
        if hasattr(self.mss_thread_local, 'sct'):
            try:
                self.mss_thread_local.sct.close()
                self.logger.log(f"log_mss_instance_closed ({thread_name})")
            except Exception as e:
                self.logger.log("log_mss_close_error", str(e))
            try:
                del self.mss_thread_local.sct
            except AttributeError:
                pass
        
        # ★★★ これが最も重要 ★★★
        # mss.linux が内部で使うスレッドローカルキャッシュ (_MSS_DISPLAY) を破棄する
        if sys.platform != 'win32' and _MSS_DISPLAY:
            if hasattr(_MSS_DISPLAY, "display"):
                try:
                    del _MSS_DISPLAY.display
                    self.logger.log(f"log_mss_internal_cache_cleared ({thread_name})")
                except AttributeError:
                    pass # キャッシュが存在しない
    # --- ▲▲▲ 修正完了 ▲▲▲ ---

    def set_capture_method(self, method: str):
        with self.lock:
            requested_method = 'dxcam' if (method == 'dxcam' and DXCAM_AVAILABLE) else 'mss'
            
            if requested_method == self.current_method:
                return 

            self.cleanup() 

            self.current_method = requested_method
            
            if self.current_method == 'dxcam':
                self.logger.log("log_dxcam_init_attempt")
                try:
                    if sys.platform == 'win32':
                        try:
                            w = win32api.GetSystemMetrics(0)
                            h = win32api.GetSystemMetrics(1)
                            self.logger.log(f"[DEBUG] Refreshing system metrics before dxcam.create(): {w}x{h}")
                        except Exception as e_api:
                            self.logger.log(f"[WARN] Failed to refresh system metrics: {e_api}")

                    self.dxcam_sct = dxcam.create(output_idx=None)
                    
                    if self.dxcam_sct:
                        frame = self.dxcam_sct.grab()
                        if frame is not None:
                            self.is_dxcam_ready = True
                            self.dxcam_error_count = 0
                            self.target_height, self.target_width, _ = frame.shape
                            self.logger.log("log_dxcam_init_success_resolution", self.target_width, self.target_height)
                        else:
                             raise RuntimeError("dxcam.create() returned instance but failed to grab first frame.")
                    else:
                        raise RuntimeError("dxcam.create() returned None")

                except Exception as e:
                    self.logger.log("log_dxcam_init_error_critical", str(e))
                    self.current_method = 'mss' 
                    self.is_dxcam_ready = False
                    self.dxcam_sct = None
            
            if self.current_method == 'mss':
                try:
                    self.logger.log("log_mss_init_attempt (main_thread)")
                    if not hasattr(self.mss_thread_local, 'sct'):
                         self.mss_thread_local.sct = mss.mss()
                    if sys.platform != 'win32':
                         self.mss_thread_local.sct.grab({"top": 0, "left": 0, "width": 1, "height": 1})
                    self.logger.log("log_mss_init_success (main_thread)")
                except Exception as e:
                    self.logger.log("log_mss_init_failed", str(e))
            
            self.logger.log("log_capture_method_set", self.current_method)

    def reinitialize_backend(self):
        """
        解像度変更などを理由に、現在のキャプチャバックエンドを再初期化します。
        """
        with self.lock:
            current_method_name = self.current_method
            self.logger.log("log_reinitializing_capture_backend", current_method_name)
            
            if current_method_name == 'mss':
                self.mss_reinit_required = True
                self._cleanup_mss_thread_local("main_thread")

            elif current_method_name == 'dxcam':
                self.cleanup() 
                self.current_method = None
                self.set_capture_method('dxcam')

    def capture_frame(self, region: tuple = None) -> np.ndarray:
        with self.lock:
            try:
                if self.current_method == 'dxcam' and self.is_dxcam_ready:
                    if self.dxcam_sct is None:
                        self.logger.log("log_dxcam_missing_recreate")
                        self.set_capture_method('dxcam')
                        if not self.is_dxcam_ready:
                            return None
                    
                    # ★★★ 修正: DXCamのtarget_hwndが設定されている場合、regionをウィンドウ座標に変換 ★★★
                    target_hwnd_info = None
                    if hasattr(self.dxcam_sct, 'target_hwnd'):
                        target_hwnd_info = self.dxcam_sct.target_hwnd
                    
                    # ★★★ 一時的に座標変換を無効化: DXCamのgrab(region=...)がtarget_hwnd設定時でも画面座標を期待している可能性 ★★★
                    # DXCamのtarget_hwndが設定されている場合、regionをウィンドウ座標に変換
                    dxcam_region = region
                    # 座標変換を一旦無効化してテスト（画像認識ができなくなったため）
                    # if region and target_hwnd_info and sys.platform == 'win32' and win32gui:
                    #     # ★★★ デバッグ: 座標変換の条件を確認 ★★★
                    #     self.logger.log(f"[DXCam Region Convert Check] region={region} target_hwnd={target_hwnd_info} win32gui={win32gui is not None}")
                    #     try:
                    #         # 画面座標からウィンドウ座標に変換
                    #         left_screen, top_screen = region[0], region[1]
                    #         right_screen, bottom_screen = region[2], region[3]
                    #         
                    #         # 左上をウィンドウ座標に変換
                    #         left_client, top_client = win32gui.ScreenToClient(target_hwnd_info, (left_screen, top_screen))
                    #         # 右下をウィンドウ座標に変換
                    #         right_client, bottom_client = win32gui.ScreenToClient(target_hwnd_info, (right_screen, bottom_screen))
                    #         
                    #         self.logger.log(f"[DXCam Region Convert Step1] ScreenToClient: screen=({left_screen},{top_screen},{right_screen},{bottom_screen}) -> client=({left_client},{top_client},{right_client},{bottom_client})")
                    #         
                    #         # ウィンドウのクライアント領域を取得して検証
                    #         client_rect = win32gui.GetClientRect(target_hwnd_info)
                    #         client_width = client_rect[2]
                    #         client_height = client_rect[3]
                    #         
                    #         self.logger.log(f"[DXCam Region Convert Step2] ClientRect: width={client_width} height={client_height}")
                    #         
                    #         # 変換後の座標をウィンドウの範囲内にクランプ
                    #         left_client_before = left_client
                    #         top_client_before = top_client
                    #         right_client_before = right_client
                    #         bottom_client_before = bottom_client
                    #         
                    #         left_client = max(0, min(left_client, client_width))
                    #         top_client = max(0, min(top_client, client_height))
                    #         right_client = max(left_client, min(right_client, client_width))
                    #         bottom_client = max(top_client, min(bottom_client, client_height))
                    #         
                    #         self.logger.log(f"[DXCam Region Convert Step3] Clamp: before=({left_client_before},{top_client_before},{right_client_before},{bottom_client_before}) -> after=({left_client},{top_client},{right_client},{bottom_client})")
                    #         
                    #         # 有効な範囲かチェック
                    #         if right_client > left_client and bottom_client > top_client:
                    #             dxcam_region = (left_client, top_client, right_client, bottom_client)
                    #             
                    #             # ★★★ デバッグ: 座標変換の結果を常にログ出力 ★★★
                    #             self.logger.log(f"[DXCam Region Convert] screen={region} -> window={dxcam_region} target_hwnd={target_hwnd_info} client_size={client_width}x{client_height}")
                    #         else:
                    #             # 変換後の座標が無効な場合は元のregionを使用
                    #             self.logger.log(f"[WARN] Converted region is invalid: ({left_client}, {top_client}, {right_client}, {bottom_client}), using original region")
                    #             dxcam_region = region
                    #     except Exception as e:
                    #         # 変換に失敗した場合は元のregionを使用
                    #         self.logger.log(f"[WARN] Failed to convert region to window coordinates: {e}")
                    #         import traceback
                    #         self.logger.log(f"[WARN] Traceback: {traceback.format_exc()}")
                    #         dxcam_region = region
                    
                    frame = self.dxcam_sct.grab(region=dxcam_region)

                    if frame is None:
                        self.logger.log("log_dxcam_grab_failed")
                        self.dxcam_error_count += 1
                        
                        if self.dxcam_error_count >= self.DXCAM_ERROR_THRESHOLD:
                            self.logger.log("log_dxcam_switching_to_mss")
                            self.set_capture_method('mss')
                        else:
                            return None
                    
                    else:
                        self.dxcam_error_count = 0
                        # ★★★ DXCamとMSSで返される画像サイズを比較するため、デバッグ情報を追加 ★★★
                        if region:
                            expected_width = region[2] - region[0]
                            expected_height = region[3] - region[1]
                            actual_height, actual_width = frame.shape[:2]
                            if expected_width != actual_width or expected_height != actual_height:
                                self.logger.log(f"[DXCam Size Mismatch] region={region} expected={expected_width}x{expected_height} actual={actual_width}x{actual_height}")
                            # ★★★ 原因特定: DXCamのregion解釈を確認するため、target_hwndとregionをログ出力 ★★★
                            if os.environ.get("DEBUG_OCR_COORDS", "0") == "1":
                                self.logger.log(f"[DXCam Region Debug] region={region} target_hwnd={target_hwnd_info} expected_size={expected_width}x{expected_height} actual_size={actual_width}x{actual_height}")
                        result = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                        # ★★★ デバッグ: DXCamとMSSでキャプチャされた画像の内容を比較するため、画像を保存 ★★★
                        if os.environ.get("DEBUG_SAVE_CAPTURE_FRAME", "0") == "1":
                            try:
                                from datetime import datetime
                                base_dir = Path(__file__).resolve().parent
                                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                                debug_path = base_dir / f"capture_debug_dxcam_{timestamp}.png"
                                cv2.imwrite(str(debug_path), result)
                                if region:
                                    self.logger.log(f"[DEBUG] Saved DXCam capture: region={region} size={result.shape[1]}x{result.shape[0]}")
                            except Exception:
                                pass
                        return result

                if self.current_method == 'mss':
                    
                    # --- ▼▼▼ 修正: JIT初期化と再初期化フラグのチェック ▼▼▼ ---
                    if self.mss_reinit_required:
                        self._cleanup_mss_thread_local("worker_thread")
                        self.mss_reinit_required = False 

                    if not hasattr(self.mss_thread_local, 'sct'):
                        self.logger.log("log_mss_init_attempt (worker_thread)")
                        try:
                            self.mss_thread_local.sct = mss.mss()
                        except Exception as e:
                             self.logger.log("log_mss_init_failed", str(e))
                             return None 
                             
                    sct = self.mss_thread_local.sct
                    
                    monitor_dict = None
                    if region:
                        monitor_dict = {
                            "top": region[1], 
                            "left": region[0],
                            "width": region[2] - region[0], 
                            "height": region[3] - region[1],
                        }
                        if monitor_dict["width"] <= 0 or monitor_dict["height"] <= 0:
                            self.logger.log("log_mss_invalid_region")
                            return None
                    else:
                        # --- ▼▼▼ 修正箇所 (monitors[0] -> monitors[1]) ▼▼▼ ---
                        # region=None の場合 (全画面指定)
                        
                        if len(sct.monitors) > 1:
                            # monitors[1] (プライマリモニター) を使用
                            monitor_dict = sct.monitors[1] 
                        elif len(sct.monitors) == 1:
                            # モニターが1台しかない場合 (monitors[0] しかない)
                             monitor_dict = sct.monitors[0]
                        # --- ▲▲▲ 修正完了 ▲▲▲ ---
                        else:
                            self.logger.log("log_mss_no_monitor")
                            return None
                    
                    sct_img = sct.grab(monitor_dict)
                    
                    img_bgra = np.array(sct_img)
                    # ★★★ MSSとDXCamで返される画像サイズを比較するため、デバッグ情報を追加 ★★★
                    if region:
                        expected_width = region[2] - region[0]
                        expected_height = region[3] - region[1]
                        actual_height, actual_width = img_bgra.shape[:2]
                        if expected_width != actual_width or expected_height != actual_height:
                            self.logger.log(f"[MSS Size Mismatch] region={region} expected={expected_width}x{expected_height} actual={actual_width}x{actual_height}")
                    result = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2BGR)
                    # ★★★ デバッグ: DXCamとMSSでキャプチャされた画像の内容を比較するため、画像を保存 ★★★
                    if os.environ.get("DEBUG_SAVE_CAPTURE_FRAME", "0") == "1":
                        try:
                            from datetime import datetime
                            from pathlib import Path
                            base_dir = Path(__file__).resolve().parent
                            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                            debug_path = base_dir / f"capture_debug_mss_{timestamp}.png"
                            cv2.imwrite(str(debug_path), result)
                        except Exception:
                            pass
                    return result

            except Exception as e:
                self.logger.log("log_capture_error", self.current_method, region, str(e))
                if self.current_method == 'dxcam':
                    self.dxcam_error_count += 1
                    if self.dxcam_error_count >= self.DXCAM_ERROR_THRESHOLD:
                        self.logger.log("log_dxcam_critical_error_switching")
                        self.set_capture_method('mss')
                elif self.current_method == 'mss':
                     self.logger.log("log_mss_reinit_on_error")
                     self.mss_reinit_required = True
                return None
            
            return None
            
    def cleanup(self):
        with self.lock:
            if self.dxcam_sct and DXCAM_AVAILABLE:
                try:
                    self.dxcam_sct.release()
                    self.logger.log("log_dxcam_cleanup")
                except Exception as e:
                    self.logger.log("log_dxcam_cleanup_error", str(e))
            self.dxcam_sct = None
            self.is_dxcam_ready = False

            # --- ▼▼▼ 修正: メインスレッドのMSSインスタンスのみクリーンアップ ▼▼▼ ---
            self._cleanup_mss_thread_local("main_thread_cleanup")
            # --- ▲▲▲ 修正完了 ▲▲▲ ---
