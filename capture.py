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
        print("[INFO] DXCam capture backend is available.")
    except ImportError:
        DXCAM_AVAILABLE = False
        print("[INFO] DXCam not found or win32api is missing. Falling back to MSS capture backend.")
else:
    print("[INFO] Running on non-Windows OS. Using MSS capture backend.")


class CaptureManager(QObject):
    """
    画面キャプチャのインターフェース。
    キャプチャバックエンドのインスタンスを保持し、効率的なキャプチャを行う。
    """
    def __init__(self):
        super().__init__()
        
        self.mss_thread_local = threading.local()
        self.dxcam_sct = None
        self.is_dxcam_ready = False
        
        self.target_width, self.target_height = 0, 0
        
        self.dxcam_error_count = 0
        self.DXCAM_ERROR_THRESHOLD = 5 
        
        if DXCAM_AVAILABLE:
            try:
                print("[INFO] Initializing DXCam with default primary monitor...")
                self.dxcam_sct = dxcam.create()
                
                if self.dxcam_sct is None:
                    print("[WARN] DXCam instance creation failed. Falling back to MSS.")
                    self.is_dxcam_ready = False
                else:
                    self.is_dxcam_ready = True
                    print(f"[INFO] DXCam initialized successfully.")
                    frame = self.dxcam_sct.grab()
                    if frame is not None:
                        self.target_height, self.target_width, _ = frame.shape
                        print(f"[INFO] Target monitor resolution: {self.target_width}x{self.target_height}")
                    else:
                        print("[WARN] Could not determine target monitor resolution at init. This might indicate a problem.")

            except Exception as e:
                print(f"[WARN] DXCam initialization failed: {e}. Falling back to MSS.")
                self.is_dxcam_ready = False
        
        self.current_method = 'dxcam' if self.is_dxcam_ready else 'mss'

    def prime_mss(self):
        """
        Main threadでMSSの初期化を試み、Linuxでの潜在的なデッドロックを回避する。
        """
        if self.current_method == 'mss' and sys.platform != 'win32':
            try:
                print("[INFO] Priming MSS instance on the main thread...")
                # 1x1ピクセルのキャプチャを試みることで、X11への接続を強制する
                with mss.mss() as sct:
                    sct.grab({"top": 0, "left": 0, "width": 1, "height": 1})
                print("[INFO] MSS priming successful.")
            except Exception as e:
                print(f"[WARN] Failed to prime MSS instance on the main thread: {e}")

    def set_capture_method(self, method: str):
        requested_method = 'dxcam' if (method == 'dxcam' and DXCAM_AVAILABLE) else 'mss'
        
        if requested_method == self.current_method and \
           (requested_method == 'mss' or (requested_method == 'dxcam' and self.dxcam_sct is not None)):
            return

        if self.current_method == 'dxcam' and self.dxcam_sct:
            print("[INFO] Releasing existing DXCam instance...")
            try:
                self.dxcam_sct.release()
            except Exception as e:
                print(f"[WARN] Error releasing DXCam instance: {e}")
            finally:
                self.dxcam_sct = None
                self.is_dxcam_ready = False

        self.current_method = requested_method
        
        if self.current_method == 'dxcam':
            print("[INFO] Attempting to initialize DXCam...")
            try:
                self.dxcam_sct = dxcam.create()
                if self.dxcam_sct:
                    self.is_dxcam_ready = True
                    self.dxcam_error_count = 0
                    frame = self.dxcam_sct.grab()
                    if frame is not None:
                        self.target_height, self.target_width, _ = frame.shape
                        print(f"[INFO] DXCam initialized successfully. Resolution: {self.target_width}x{self.target_height}")
                    else:
                        print("[WARN] DXCam initialized, but could not grab initial frame.")
                else:
                    raise RuntimeError("dxcam.create() returned None")
            except Exception as e:
                print(f"[ERROR] Critical error during DXCam initialization: {e}. Falling back to MSS.")
                self.current_method = 'mss'
                self.is_dxcam_ready = False
                self.dxcam_sct = None
        
        print(f"[INFO] Capture method is now set to: {self.current_method}")

    def capture_frame(self, region: tuple = None) -> np.ndarray:
        try:
            if self.current_method == 'dxcam' and self.is_dxcam_ready:
                if self.dxcam_sct is None:
                    print("[WARN] DXCam instance was missing. Attempting to re-create...")
                    self.set_capture_method('dxcam')
                    if not self.is_dxcam_ready:
                        return None
                
                frame = self.dxcam_sct.grab(region=region)

                if frame is None:
                    print("[WARN] DXCam.grab() failed (returned None).")
                    self.dxcam_error_count += 1
                    
                    if self.dxcam_error_count >= self.DXCAM_ERROR_THRESHOLD:
                        print("[WARN] DXCam failed repeatedly. Switching to MSS for this session.")
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
                        print("[WARN] MSS capture requested with invalid region size.")
                        return None
                else:
                    if len(sct.monitors) > 1:
                        monitor = sct.monitors[1]
                    else:
                        print("[ERROR] MSS could not find a primary monitor.")
                        return None

                sct_img = sct.grab(monitor)
                img_bgra = np.array(sct_img)
                return cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2BGR)

        except Exception as e:
            print(f"Capture error with {self.current_method} (region: {region}): {e}")
            if self.current_method == 'dxcam':
                self.dxcam_error_count += 1
                if self.dxcam_error_count >= self.DXCAM_ERROR_THRESHOLD:
                    print("[WARN] DXCam encountered a critical error. Switching to MSS for this session.")
                    self.set_capture_method('mss')
            return None
            
    def cleanup(self):
        if self.dxcam_sct and DXCAM_AVAILABLE:
            try:
                self.dxcam_sct.release()
                print("[INFO] DXCam resources released.")
            except Exception as e:
                print(f"[WARN] Error releasing DXCam resources: {e}")
