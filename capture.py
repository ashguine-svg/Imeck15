# capture.py

import sys
import mss
import cv2
import numpy as np
import threading
from PySide6.QtCore import QObject

# ★★★ 変更点: Windows以外ではDXCamを無効化 ★★★
DXCAM_AVAILABLE = False
if sys.platform == 'win32':
    # 条件付きインポート - Direct3D高速キャプチャ
    try:
        import dxcam
        DXCAM_AVAILABLE = True
        print("[INFO] DXCam capture backend is available.")
    except ImportError:
        DXCAM_AVAILABLE = False
        print("[INFO] DXCam not found. Falling back to MSS capture backend.")
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
        # ★★★ 変更点: Windowsの場合のみDXCamを初期化 ★★★
        if DXCAM_AVAILABLE:
            try:
                self.dxcam_sct = dxcam.create()
                if self.dxcam_sct is None:
                    print("[WARN] DXCam instance creation failed. Falling back to MSS.")
                    self.is_dxcam_ready = False
                else:
                    self.is_dxcam_ready = True
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
            # ★★★ 変更点: DXCAM_AVAILABLEのチェックを追加 ★★★
            if self.current_method == 'dxcam' and self.is_dxcam_ready and DXCAM_AVAILABLE:
                frame = self.dxcam_sct.grab(region=region)
                if frame is None: 
                    print(f"[WARN] DXCam.grab() returned None for region: {region}")
                    return None
                return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            else: # MSS fallback
                if not hasattr(self.mss_thread_local, 'sct'):
                    self.mss_thread_local.sct = mss.mss()
                sct = self.mss_thread_local.sct
                
                if region:
                    monitor = {
                        "top": region[1], "left": region[0],
                        "width": region[2] - region[0], "height": region[3] - region[1],
                    }
                    if monitor["width"] <= 0 or monitor["height"] <= 0:
                        print(f"[WARN] Invalid capture region for MSS: {monitor}")
                        return None
                    sct_img = sct.grab(monitor)
                else:
                    # sct.monitors[0] は全画面を含むことがあるので、[1]でプライマリモニタを期待する
                    monitor_index = 1 if len(sct.monitors) > 1 else 0
                    sct_img = sct.grab(sct.monitors[monitor_index])
                
                img_bgra = np.array(sct_img)
                return cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2BGR)
        except Exception as e:
            print(f"Capture error with {self.current_method} (region: {region}): {e}")
            if self.current_method == 'mss' and hasattr(self.mss_thread_local, 'sct'):
                del self.mss_thread_local.sct
            return None
            
    def cleanup(self):
        """アプリケーション終了時にリソースを解放する"""
        # ★★★ 変更点: DXCAM_AVAILABLEのチェックを追加 ★★★
        if self.dxcam_sct and DXCAM_AVAILABLE:
            try:
                self.dxcam_sct.release()
                del self.dxcam_sct
                print("[INFO] DXCam resources released.")
            except Exception as e:
                print(f"[WARN] Error releasing DXCam resources: {e}")
