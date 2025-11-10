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
        """
        関連するモジュールへの参照を保持します。
        
        Args:
            core_engine (CoreEngine): CoreEngineのインスタンス (スケール値やスレッドプールへのアクセス用)
            config_manager (ConfigManager): ConfigManagerのインスタンス (書き込み処理の呼び出し用)
            logger (Logger): Loggerインスタンス
        """
        self.core_engine = core_engine
        self.config_manager = config_manager
        self.logger = logger
        
        # CoreEngineが保持していたアプリ名をこちらで保持
        self.recognition_area_app_title = None

    def on_rec_area_set(self, method: str, title: str = None):
        """
        認識範囲が設定または変更されたときに CoreEngine から呼び出されます。
        
        Args:
            method (str): 'rectangle' または 'window'
            title (str, optional): 'window' の場合のアプリ名
        """
        if method == "rectangle":
            self.recognition_area_app_title = None
            self.logger.log("[DEBUG] EnvironmentTracker: App title cleared (Rectangle mode).")
        elif method == "window" and title:
            self.recognition_area_app_title = title
            self.logger.log(f"[DEBUG] EnvironmentTracker: App title set to '{title}'.")

    def on_rec_area_clear(self):
        """認識範囲がクリアされたときに CoreEngine から呼び出されます。"""
        self.recognition_area_app_title = None
        self.logger.log("[DEBUG] EnvironmentTracker: App title cleared (Area cleared).")

    def _collect_current_environment(self) -> dict:
        """
        現在の実行環境（解像度、DPI、スケール）を収集します。
        
        Returns:
            dict: 仕様書に定義された env_data 辞書
        """
        
        # 1. アプリ名
        app_name = self.recognition_area_app_title

        # 2. 解像度
        resolution_str = "Unknown"
        screen = QApplication.primaryScreen()
        
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

        # 3. DPI
        dpi = 96
        if screen:
            try:
                # 物理DPI (WindowsのDPI Unaware設定下では 96 が返る想定)
                dpi = screen.logicalDotsPerInch()
            except Exception:
                dpi = 96 # フォールバック

        # 4. Imeck15 スケール値
        imeck_scale = 1.0
        if self.core_engine.current_window_scale is not None:
            # ★ 修正: この行をインデントします
            imeck_scale = self.core_engine.current_window_scale
        else:
            imeck_scale = self.core_engine.effective_capture_scale

        return {
            "app_name": app_name,
            "resolution": resolution_str,
            "dpi": int(dpi),
            "imeck_scale": imeck_scale # ★ round() を削除
        }

    def track_environment_on_click(self, item_path_str: str):
        """
        (CoreEngine._execute_click から) クリック実行直前に呼び出されます。
        環境情報を収集し、非同期での書き込みタスクをスレッドプールに投入します。
        
        Args:
            item_path_str (str): マッチした画像のファイルパス
        """
        if not item_path_str:
            return
            
        # 1. 現在の環境情報を収集
        env_data = self._collect_current_environment()
        
        # 2. ワーカースレッドプールに、ConfigManagerの更新タスクを投げる
        # これにより、監視ループはブロックされずに続行する
        thread_pool = self.core_engine.thread_pool
        
        if thread_pool:
            thread_pool.submit(
                self.config_manager.update_environment_info, 
                item_path_str, 
                env_data
            )
        else:
            self.logger.log("[WARN] Thread pool not available. Cannot track environment info.")
