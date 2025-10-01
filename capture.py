# capture.py

import sys
import mss
import cv2
import numpy as np
import threading
from PySide6.QtCore import QObject

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
        
        # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
        # 修正点: DXCAMが認識する画面解像度を保持する変数を初期化
        self.target_width, self.target_height = 0, 0
        # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
        
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
                        # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
                        # 修正点: 取得した解像度を変数に保存
                        self.target_height, self.target_width, _ = frame.shape
                        print(f"[INFO] Target monitor resolution: {self.target_width}x{self.target_height}")
                        # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
                    else:
                        print("[WARN] Could not determine target monitor resolution at init. This might indicate a problem.")

            except Exception as e:
                print(f"[WARN] DXCam initialization failed: {e}. Falling back to MSS.")
                self.is_dxcam_ready = False
        
        self.current_method = 'dxcam' if self.is_dxcam_ready else 'mss'

    def set_capture_method(self, method: str):
        if method == 'dxcam' and self.is_dxcam_ready:
            self.current_method = 'dxcam'
        else:
            self.current_method = 'mss'
        print(f"[INFO] Capture method set to: {self.current_method}")

    def capture_frame(self, region: tuple = None) -> np.ndarray:
        """
        保持しているインスタンスを使って単発の画面キャプチャを実行する。
        regionは (left, top, right, bottom) のタプル。
        """
        try:
            if self.current_method == 'dxcam' and self.is_dxcam_ready and DXCAM_AVAILABLE:
                if self.dxcam_sct is None:
                    print("[WARN] DXCam instance is missing, attempting to re-create...")
                    self.dxcam_sct = dxcam.create()
                    if self.dxcam_sct is None:
                        print("[ERROR] Failed to re-create DXCam instance. Falling back to MSS for this frame.")
                        self.current_method = 'mss'
                        return self.capture_frame(region)
                    # 再作成時に解像度を再取得
                    frame = self.dxcam_sct.grab()
                    if frame is not None:
                        self.target_height, self.target_width, _ = frame.shape

                # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
                # 修正点: 認識範囲の座標が画面解像度内に収まるようにクリッピングする
                validated_region = region
                if region is not None and self.target_width > 0 and self.target_height > 0:
                    # 座標がDXCAMの認識する画面解像度の範囲内に収まるように値を調整
                    left = max(0, region[0])
                    top = max(0, region[1])
                    right = min(self.target_width, region[2])
                    bottom = min(self.target_height, region[3])

                    # 幅と高さが0より大きいことを確認
                    if right > left and bottom > top:
                        validated_region = (left, top, right, bottom)
                    else:
                        # 領域が無効な場合はフルスクリーンキャプチャにフォールバック
                        print(f"[WARN] Invalid region provided after clipping {region}, falling back to fullscreen grab for this frame.")
                        validated_region = None
                # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
                
                frame = self.dxcam_sct.grab(region=validated_region)

                if frame is None:
                    print("[WARN] DXCam.grab() failed (returned None). Attempting to re-initialize for the next frame.")
                    try:
                        self.dxcam_sct.release()
                    except Exception:
                        pass
                    self.dxcam_sct = None
                    return None
                
                return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

            else: # MSS fallback
                if not hasattr(self.mss_thread_local, 'sct'):
                    self.mss_thread_local.sct = mss.mss()
                sct = self.mss_thread_local.sct
                
                if region:
                    monitor_index = 1 if len(sct.monitors) > 1 else 0
                    primary_monitor = sct.monitors[monitor_index]
                    offset_x = primary_monitor["left"]
                    offset_y = primary_monitor["top"]
                    
                    monitor = {
                        "top": region[1] + offset_y, 
                        "left": region[0] + offset_x,
                        "width": region[2] - region[0], 
                        "height": region[3] - region[1],
                    }

                    if monitor["width"] <= 0 or monitor["height"] <= 0:
                        print(f"[WARN] Invalid capture region for MSS: {monitor}")
                        return None
                    sct_img = sct.grab(monitor)
                else:
                    monitor_index = 1 if len(sct.monitors) > 1 else 0
                    sct_img = sct.grab(sct.monitors[monitor_index])
                
                img_bgra = np.array(sct_img)
                return cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2BGR)
        except Exception as e:
            if self.current_method == 'dxcam':
                print(f"[ERROR] An unexpected error occurred in DXCam capture: {e}. Attempting re-initialization.")
                try:
                    if self.dxcam_sct:
                        self.dxcam_sct.release()
                except Exception:
                    pass
                self.dxcam_sct = None
            else:
                 print(f"Capture error with {self.current_method} (region: {region}): {e}")

            if self.current_method == 'mss' and hasattr(self.mss_thread_local, 'sct'):
                del self.mss_thread_local.sct
            return None
            
    def cleanup(self):
        """アプリケーション終了時にリソースを解放する"""
        if self.dxcam_sct and DXCAM_AVAILABLE:
            try:
                self.dxcam_sct.release()
                del self.dxcam_sct
                print("[INFO] DXCam resources released.")
            except Exception as e:
                print(f"[WARN] Error releasing DXCam resources: {e}")
