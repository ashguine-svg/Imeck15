# capture.py

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
    pass


class CaptureManager(QObject):
    """
    画面キャプチャのインターフェース。
    キャプチャバックエンドのインスタンスを保持し、効率的なキャプチャを行う。
    """
    # ★★★ 1. __init__ で logger を受け取る ★★★
    def __init__(self, logger):
        super().__init__()
        self.logger = logger # Loggerインスタンスを保持
        
        self.mss_thread_local = threading.local()
        self.dxcam_sct = None
        self.is_dxcam_ready = False
        
        self.target_width, self.target_height = 0, 0
        
        self.dxcam_error_count = 0
        self.DXCAM_ERROR_THRESHOLD = 5 
        
        if DXCAM_AVAILABLE:
            # ★★★ 2. print を self.logger.log に変更 (翻訳キー使用) ★★★
            self.logger.log("log_dxcam_available")
            try:
                self.logger.log("log_dxcam_init")
                self.dxcam_sct = dxcam.create()
                
                if self.dxcam_sct is None:
                    self.logger.log("log_dxcam_init_failed_instance")
                    self.is_dxcam_ready = False
                else:
                    self.is_dxcam_ready = True
                    self.logger.log("log_dxcam_init_success")
                    frame = self.dxcam_sct.grab()
                    if frame is not None:
                        self.target_height, self.target_width, _ = frame.shape
                        self.logger.log("log_dxcam_resolution", self.target_width, self.target_height)
                    else:
                        self.logger.log("log_dxcam_resolution_failed")

            except Exception as e:
                self.logger.log("log_dxcam_init_failed_general", str(e))
                self.is_dxcam_ready = False
        else:
            if sys.platform == 'win32':
                self.logger.log("log_dxcam_unavailable")
            else:
                self.logger.log("log_non_windows_mss")
        
        self.current_method = 'dxcam' if self.is_dxcam_ready else 'mss'

    def prime_mss(self):
        """
        Main threadでMSSの初期化を試み、Linuxでの潜在的なデッドロックを回避する。
        """
        if self.current_method == 'mss' and sys.platform != 'win32':
            try:
                # ★★★ 3. print を self.logger.log に変更 (翻訳キー使用) ★★★
                self.logger.log("log_mss_priming")
                with mss.mss() as sct:
                    sct.grab({"top": 0, "left": 0, "width": 1, "height": 1})
                self.logger.log("log_mss_priming_success")
            except Exception as e:
                self.logger.log("log_mss_priming_failed", str(e))

    def set_capture_method(self, method: str):
        requested_method = 'dxcam' if (method == 'dxcam' and DXCAM_AVAILABLE) else 'mss'
        
        if requested_method == self.current_method and \
           (requested_method == 'mss' or (requested_method == 'dxcam' and self.dxcam_sct is not None)):
            return

        if self.current_method == 'dxcam' and self.dxcam_sct:
            # ★★★ 4. print を self.logger.log に変更 (翻訳キー使用) ★★★
            self.logger.log("log_dxcam_release")
            try:
                self.dxcam_sct.release()
            except Exception as e:
                self.logger.log("log_dxcam_release_error", str(e))
            finally:
                self.dxcam_sct = None
                self.is_dxcam_ready = False

        self.current_method = requested_method
        
        if self.current_method == 'dxcam':
            self.logger.log("log_dxcam_init_attempt")
            try:
                self.dxcam_sct = dxcam.create()
                if self.dxcam_sct:
                    self.is_dxcam_ready = True
                    self.dxcam_error_count = 0
                    frame = self.dxcam_sct.grab()
                    if frame is not None:
                        self.target_height, self.target_width, _ = frame.shape
                        self.logger.log("log_dxcam_init_success_resolution", self.target_width, self.target_height)
                    else:
                        self.logger.log("log_dxcam_init_warn_no_frame")
                else:
                    raise RuntimeError("dxcam.create() returned None")
            except Exception as e:
                self.logger.log("log_dxcam_init_error_critical", str(e))
                self.current_method = 'mss'
                self.is_dxcam_ready = False
                self.dxcam_sct = None
        
        self.logger.log("log_capture_method_set", self.current_method)

    def capture_frame(self, region: tuple = None) -> np.ndarray:
        try:
            if self.current_method == 'dxcam' and self.is_dxcam_ready:
                if self.dxcam_sct is None:
                    # ★★★ 5. print を self.logger.log に変更 (翻訳キー使用) ★★★
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
                        return self.capture_frame(region)
                    
                    return None
                
                self.dxcam_error_count = 0
                return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

            else: # MSS fallback
                if not hasattr(self.mss_thread_local, 'sct'):
                    self.mss_thread_local.sct = mss.mss()
                sct = self.mss_thread_local.sct
                
                monitor = region
                if region:
                    monitor = {
                        "top": region[1], "left": region[0],
                        "width": region[2] - region[0], "height": region[3] - region[1],
                    }
                    if monitor["width"] <= 0 or monitor["height"] <= 0:
                        self.logger.log("log_mss_invalid_region")
                        return None
                else:
                    if len(sct.monitors) > 1:
                        monitor = sct.monitors[1]
                    else:
                        self.logger.log("log_mss_no_monitor")
                        return None

                sct_img = sct.grab(monitor)
                img_bgra = np.array(sct_img)
                return cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2BGR)

        except Exception as e:
            self.logger.log("log_capture_error", self.current_method, region, str(e))
            if self.current_method == 'dxcam':
                self.dxcam_error_count += 1
                if self.dxcam_error_count >= self.DXCAM_ERROR_THRESHOLD:
                    self.logger.log("log_dxcam_critical_error_switching")
                    self.set_capture_method('mss')
            return None
            
    def cleanup(self):
        if self.dxcam_sct and DXCAM_AVAILABLE:
            try:
                self.dxcam_sct.release()
                self.logger.log("log_dxcam_cleanup")
            except Exception as e:
                self.logger.log("log_dxcam_cleanup_error", str(e))
