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
        
        if DXCAM_AVAILABLE:
            try:
                # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
                # 修正点: 利用可能な全モニターをスキャンし、1024x768のモニターを特定して初期化する
                target_device_idx = None
                target_output_idx = None

                try:
                    devices = dxcam.get_devices()
                    for i, device in enumerate(devices):
                        print(f"[DXCam INFO] Device [{i}]: {device.name}")
                        outputs = device.enum_outputs()
                        for j, output in enumerate(outputs):
                            print(f"  - Output [{j}]: {output.name} with resolution {output.resolution}")
                            # ダミーモニターの解像度 (1024, 768) を持つモニターを探す
                            if output.resolution == (1024, 768):
                                target_device_idx = i
                                target_output_idx = j
                                print(f"  -> Found target dummy monitor at Device {i}, Output {j}")
                                break
                        if target_device_idx is not None:
                            break
                except Exception as e:
                    print(f"[DXCam WARN] Failed to dynamically enumerate devices/outputs: {e}")

                # 目的のモニターが見つかった場合はそのインデックスを使用し、見つからなかった場合はプライマリモニター(0, 0)にフォールバック
                if target_device_idx is not None and target_output_idx is not None:
                    print(f"[INFO] Initializing DXCam with specific target: Device {target_device_idx}, Output {target_output_idx}")
                    self.dxcam_sct = dxcam.create(device_idx=target_device_idx, output_idx=target_output_idx)
                else:
                    print("[INFO] Target dummy monitor not found. Falling back to default primary monitor (output_idx=0).")
                    self.dxcam_sct = dxcam.create(output_idx=0)
                # ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★
                
                if self.dxcam_sct is None:
                    print("[WARN] DXCam instance creation failed. Falling back to MSS.")
                    self.is_dxcam_ready = False
                else:
                    self.is_dxcam_ready = True
                    print(f"[INFO] DXCam initialized successfully for output: {self.dxcam_sct.output.name} with resolution {self.dxcam_sct.output.resolution}")

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
                    # Noneが返された場合でも、致命的なエラーとは限らないため警告レベルに留める
                    # print(f"[WARN] DXCam.grab() returned None for region: {region}")
                    return None
                return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            else: # MSS fallback
                if not hasattr(self.mss_thread_local, 'sct'):
                    self.mss_thread_local.sct = mss.mss()
                sct = self.mss_thread_local.sct
                
                if region:
                    # 修正点: プライマリモニタのオフセットを取得して座標を補正
                    # sct.monitors[0]は仮想スクリーン全体、[1]がプライマリモニタ
                    # これにより、マルチモニタ環境でプライマリモニタが(0,0)にない場合でも
                    # 正しい領域をキャプチャできるようになる。
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
                    # sct.monitors[0] は全画面を含むことがあるので、[1]でプライマリモニタを期待する
                    monitor_index = 1 if len(sct.monitors) > 1 else 0
                    sct_img = sct.grab(sct.monitors[monitor_index])
                
                img_bgra = np.array(sct_img)
                return cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2BGR)
        except Exception as e:
            # DXCamのエラーはログに出力するが、監視ループを止めないようにNoneを返す
            if self.current_method == 'dxcam':
                # print(f"Capture error with {self.current_method} (region: {region}): {e}")
                pass
            else:
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
