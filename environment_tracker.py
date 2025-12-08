# environment_tracker.py

import sys
from PySide6.QtWidgets import QApplication
try:
    import pyautogui
    PYAUTOGUI_AVAILABLE = True
except ImportError:
    PYAUTOGUI_AVAILABLE = False
    
class EnvironmentTracker:
    """
    画像マッチング成功時の実行環境（アプリ名、解像度、DPI、スケール）を
    収集・管理し、非同期でJSONに書き込む責務を持つクラス。
    """
    
    def __init__(self, core_engine, config_manager, logger):
        self.core_engine = core_engine
        self.config_manager = config_manager
        self.logger = logger
        
        # CoreEngineが保持していたアプリ名をこちらで保持
        self.recognition_area_app_title = None

        # ★★★ 修正: 画面情報をキャッシュする変数を初期化 ★★★
        self.cached_resolution = "Unknown"
        self.cached_dpi = 96
        
        # 初期化時に一度情報を取得しておく（ここはメインスレッドで実行される前提）
        self.refresh_screen_info()

    def on_rec_area_set(self, method: str, title: str = None):
        """認識範囲設定時のコールバック"""
        if method == "rectangle":
            self.recognition_area_app_title = None
            self.logger.log("[DEBUG] EnvironmentTracker: App title cleared (Rectangle mode).")
        elif method == "fullscreen":
            self.recognition_area_app_title = None
            self.logger.log("[DEBUG] EnvironmentTracker: App title cleared (Fullscreen mode).")
        elif method == "window" and title:
            self.recognition_area_app_title = title
            self.logger.log(f"[DEBUG] EnvironmentTracker: App title set to '{title}'.")

    def on_rec_area_clear(self):
        """認識範囲クリア時のコールバック"""
        self.recognition_area_app_title = None
        self.logger.log("[DEBUG] EnvironmentTracker: App title cleared (Area cleared).")

    # --- ▼▼▼ 追加: メインスレッドから安全に呼び出して情報を更新するメソッド ▼▼▼ ---
    def refresh_screen_info(self):
        """
        現在の画面解像度とDPIを取得してキャッシュを更新します。
        必ずメインスレッドから呼び出してください。
        """
        try:
            screen = QApplication.primaryScreen()
            
            # 1. 解像度の取得
            resolution_str = "Unknown"
            if PYAUTOGUI_AVAILABLE:
                try:
                    screen_size = pyautogui.size()
                    resolution_str = f"{screen_size.width}x{screen_size.height}"
                except Exception:
                    if screen:
                        geo = screen.geometry()
                        resolution_str = f"{geo.width()}x{geo.height()}"
            elif screen:
                geo = screen.geometry()
                resolution_str = f"{geo.width()}x{geo.height()}"
            
            self.cached_resolution = resolution_str

            # 2. DPIの取得
            dpi = 96
            if screen:
                try:
                    dpi = screen.logicalDotsPerInch()
                except Exception:
                    dpi = 96
            self.cached_dpi = int(dpi)

            self.logger.log(f"[DEBUG] Environment info refreshed: {self.cached_resolution}, DPI={self.cached_dpi}")

        except Exception as e:
            self.logger.log(f"[ERROR] Failed to refresh screen info: {e}")
    # --- ▲▲▲ 追加完了 ▲▲▲ ---

    def _collect_current_environment(self) -> dict:
        """
        現在の実行環境（解像度、DPI、スケール）を収集します。
        ★ ワーカースレッドから呼ばれても安全なように、キャッシュされた値を返します。
        """
        
        # 1. アプリ名 (文字列アクセスは安全)
        app_name = self.recognition_area_app_title

        # 2. 解像度 (キャッシュを使用)
        resolution_str = self.cached_resolution

        # 3. DPI (キャッシュを使用)
        dpi = self.cached_dpi

        # 4. Imeck15 スケール値 (単純な数値アクセスは安全)
        imeck_scale = 1.0
        if self.core_engine.current_window_scale is not None:
            imeck_scale = self.core_engine.current_window_scale
        else:
            imeck_scale = self.core_engine.effective_capture_scale

        return {
            "app_name": app_name,
            "resolution": resolution_str,
            "dpi": dpi,
            "imeck_scale": imeck_scale
        }

    def track_environment_on_click(self, item_path_str: str):
        """
        クリック実行時に環境情報を収集し、保存タスクを発行します。
        """
        if not item_path_str:
            return
            
        # キャッシュされた情報を収集 (安全)
        env_data = self._collect_current_environment()
        
        thread_pool = self.core_engine.thread_pool
        
        if thread_pool:
            thread_pool.submit(
                self.config_manager.update_environment_info, 
                item_path_str, 
                env_data
            )
        else:
            self.logger.log("[WARN] Thread pool not available. Cannot track environment info.")
