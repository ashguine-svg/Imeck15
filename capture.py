# capture.py (DXCam スケール対応版)
# ★★★ (根本修正) MSSのスレッドセーフティに対応するため、
#     threading.local() と 再初期化フラグ (mss_reinit_required) を使用する方式に変更 ★★★
# ★★★ MSSの座標がズレる問題を修正 (region=None の場合、monitors[0]辞書を渡す) ★★★
# ★★★ MSSの内部スレッドローカルキャッシュ (_MSS_DISPLAY) を明示的に削除する ★★★

import sys
import mss
import cv2
import numpy as np
import threading
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
        DXCAM_AVAILABLE = True
    except ImportError:
        DXCAM_AVAILABLE = False
else:
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
            try:
                with dxcam.create(output_idx=None) as d:
                    if d.grab() is not None:
                        is_dxcam_preferred = True
                self.logger.log("log_dxcam_check_success")
            except Exception as e:
                self.logger.log("log_dxcam_check_failed", str(e))
                is_dxcam_preferred = False
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
                    
                    frame = self.dxcam_sct.grab(region=region)

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
                        return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

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
                    # --- ▲▲▲ 修正完了 ▲▲▲ ---
                    
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
                        if len(sct.monitors) > 0:
                            # 0番目 (sct.monitors[0]) は仮想デスクトップ全体
                            monitor_dict = sct.monitors[0]
                        else:
                            self.logger.log("log_mss_no_monitor")
                            return None
                    
                    sct_img = sct.grab(monitor_dict)
                    
                    img_bgra = np.array(sct_img)
                    return cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2BGR)

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
