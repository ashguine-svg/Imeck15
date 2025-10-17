# config.py (完全なコード)

import json
import shutil
from pathlib import Path
import os

class ConfigManager:
    def __init__(self, base_dir_name: str = "click_pic"):
        self.base_dir = Path.home() / base_dir_name
        self.base_dir.mkdir(exist_ok=True)
        self.folder_config_filename = "_folder_config.json"
        self.image_order_filename = "image_order.json"
        self.sub_order_filename = "_sub_order.json"
        self.app_config_path = self.base_dir / "app_config.json"
        self.window_scales_path = self.base_dir / "window_scales.json"

        self._cleanup_orphaned_json_files()

    def _cleanup_orphaned_json_files(self):
        """
        ペアとなる画像ファイルが存在しない、孤立した設定JSONファイルを削除します。
        """
        protected_files = [
            self.app_config_path.name,
            self.window_scales_path.name,
            self.image_order_filename,
            self.folder_config_filename,
            self.sub_order_filename
        ]
        
        image_extensions = ['.png', '.jpg', '.jpeg', '.bmp']

        print("[INFO] 孤立したJSONファイルのクリーンアップを開始します...")
        cleaned_count = 0
        try:
            for json_path in self.base_dir.rglob('*.json'):
                if json_path.name in protected_files:
                    continue

                base_name = json_path.stem
                parent_dir = json_path.parent
                
                has_pair = False
                for ext in image_extensions:
                    image_path = parent_dir / (base_name + ext)
                    if image_path.exists():
                        has_pair = True
                        break
                
                if not has_pair:
                    try:
                        json_path.unlink()
                        print(f"[CLEANUP] 孤立したJSONファイルを削除しました: {json_path}")
                        cleaned_count += 1
                    except OSError as e:
                        print(f"[ERROR] JSONファイルの削除に失敗しました: {json_path}, エラー: {e}")

        except Exception as e:
            print(f"[ERROR] クリーンアップ処理中に予期せぬエラーが発生しました: {e}")

        if cleaned_count > 0:
            print(f"[INFO] クリーンアップ完了。{cleaned_count}個の孤立したJSONファイルを削除しました。")
        else:
            print("[INFO] クリーンアップ完了。孤立したJSONファイルは見つかりませんでした。")

    def load_app_config(self) -> dict:
        default_config = {
            "auto_scale": {
                "enabled": False,
                "center": 1.0,
                "range": 0.2,
                "steps": 5,
                "use_window_scale": True,
            },
            "capture_method": "mss",
            "frame_skip_rate": 2,
            "grayscale_matching": False,
            "use_opencl": True,
            "lightweight_mode": {
                "enabled": True,
                "preset": "標準"
            },
            "recognition_area": None,
            "target_hwnd": None,
            "screen_stability_check": {
                "enabled": True,
                "threshold": 8
            },
            "eco_mode": {
                "enabled": True
            }
        }
        if not self.app_config_path.exists():
            return default_config
        try:
            with open(self.app_config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                config.pop('capture_scale_factor', None)

                for key, value in default_config.items():
                    if key not in config:
                        config[key] = value
                    elif isinstance(value, dict):
                         for sub_key, sub_value in value.items():
                              if sub_key not in config.get(key, {}):
                                   config[key][sub_key] = sub_value
                return config
        except (json.JSONDecodeError, Exception) as e:
            print(f"アプリケーション設定の読み込みエラー: {e}")
            return default_config

    def save_app_config(self, config: dict):
        try:
            with open(self.app_config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"アプリケーション設定の保存エラー: {e}")

    def load_window_scales(self) -> dict:
        if not self.window_scales_path.exists():
            return {}
        try:
            with open(self.window_scales_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, Exception) as e:
            print(f"ウィンドウ基準スケール設定の読み込みエラー: {e}")
            return {}

    def save_window_scales(self, scales_data: dict):
        try:
            with open(self.window_scales_path, 'w', encoding='utf-8') as f:
                json.dump(scales_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"ウィンドウ基準スケール設定の保存エラー: {e}")

    def _get_setting_path(self, item_path: Path) -> Path:
        if item_path.is_dir():
            return item_path / self.folder_config_filename
        return item_path.with_suffix('.json')

    def load_item_setting(self, item_path: Path) -> dict:
        setting_path = self._get_setting_path(item_path)
        if item_path.is_dir():
            default_setting = {
                'mode': 'normal',
                'priority_image_timeout': 10,
                'priority_interval': 10,
                'priority_timeout': 5,
            }
        else:
            default_setting = {
                'image_path': str(item_path),
                'click_position': None,
                'click_rect': None,
                'roi_enabled': False,
                'roi_mode': 'fixed',
                'roi_rect': None,
                'roi_rect_variable': None,
                'point_click': True,
                'range_click': False,
                'random_click': False,
                'interval_time': 1.5,
                'backup_click': False,
                'backup_time': 300.0,
                'threshold': 0.8,
                'debounce_time': 0.0
            }
        
        if not setting_path.exists():
            return default_setting
            
        try:
            with open(setting_path, 'r', encoding='utf-8') as f:
                setting = json.load(f)

                if item_path.is_dir() and 'is_excluded' in setting:
                    if setting['is_excluded']:
                        setting['mode'] = 'excluded'
                    else:
                        setting['mode'] = 'normal'
                    del setting['is_excluded']

                setting.pop('template_scale_enabled', None)
                setting.pop('template_scale_factor', None)
                setting.pop('matching_mode', None)
                
                for key, value in default_setting.items():
                    setting.setdefault(key, value)
                return setting
        except (json.JSONDecodeError, Exception) as e:
            print(f"設定ファイル読み込みエラー ({setting_path}): {e}")
            return default_setting

    def save_item_setting(self, item_path: Path, setting: dict):
        setting_path = self._get_setting_path(item_path)
        try:
            with open(setting_path, 'w', encoding='utf-8') as f:
                json.dump(setting, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"設定ファイル保存エラー ({setting_path}): {e}")

    def load_image_order(self, folder_path=None) -> list:
        order_path = Path(folder_path) / self.sub_order_filename if folder_path else self.base_dir / self.image_order_filename
        if not order_path.exists(): return []
        try:
            with open(order_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []

    def save_image_order(self, order_list: list, folder_path=None):
        order_path = Path(folder_path) / self.sub_order_filename if folder_path else self.base_dir / self.image_order_filename
        with open(order_path, 'w', encoding='utf-8') as f:
            json.dump(order_list, f, indent=2, ensure_ascii=False)

    def add_item(self, item_path: Path):
        target_path = self.base_dir / item_path.name
        if not target_path.exists():
            shutil.copy(item_path, target_path)
        order = self.load_image_order()
        if str(target_path) not in order:
            order.append(str(target_path))
            self.save_image_order(order)

    def remove_item(self, item_path_str: str):
        if not item_path_str: return
        item_path = Path(item_path_str)
        if not item_path.exists(): return
        try:
            if item_path.is_dir():
                setting_path = self._get_setting_path(item_path)
                if setting_path.exists():
                    try:
                        os.remove(setting_path)
                    except OSError:
                        pass
                shutil.rmtree(item_path)
            elif item_path.is_file():
                setting_path = self._get_setting_path(item_path)
                item_path.unlink()
                if setting_path.exists():
                    setting_path.unlink()
            
            parent_dir = item_path.parent
            order_file_owner = parent_dir if parent_dir != self.base_dir else None
            order = self.load_image_order(order_file_owner)
            
            item_key_in_order = str(item_path) if order_file_owner is None else item_path.name
            
            if item_key_in_order in order:
                order.remove(item_key_in_order)
                self.save_image_order(order, order_file_owner)

        except Exception as e:
            print(f"アイテムの削除中にエラーが発生しました: {e}")
            raise

    def get_ordered_item_list(self) -> list:
        ordered_paths_str = self.load_image_order()
        try:
            all_items = {p for p in self.base_dir.iterdir() if p.is_dir() or p.suffix.lower() in ('.png', '.jpg', '.jpeg', '.bmp')}
        except FileNotFoundError:
            all_items = set()
            
        all_item_paths_str = {str(p) for p in all_items}
        
        final_order = [path_str for path_str in ordered_paths_str if path_str in all_item_paths_str]
        for item in sorted(list(all_items)):
            if str(item) not in final_order:
                final_order.append(str(item))
        
        if final_order != ordered_paths_str:
            self.save_image_order(final_order)
        return [Path(p) for p in final_order]

    def get_hierarchical_list(self):
        structured_list = []
        for item_path in self.get_ordered_item_list():
            if item_path.is_file():
                structured_list.append({'type': 'image', 'path': str(item_path), 'name': item_path.name})
            elif item_path.is_dir():
                folder_settings = self.load_item_setting(item_path)
                folder_item = {
                    'type': 'folder', 
                    'path': str(item_path), 
                    'name': item_path.name, 
                    'children': [], 
                    'settings': folder_settings
                }
                
                ordered_child_names = self.load_image_order(item_path)
                all_child_images = {p for p in item_path.iterdir() if p.is_file() and p.suffix.lower() in ('.png', '.jpg', '.jpeg', '.bmp')}
                all_child_names = {p.name for p in all_child_images}
                
                final_child_order_names = [name for name in ordered_child_names if name in all_child_names]
                for child_name in sorted(list(all_child_names)):
                    if child_name not in final_child_order_names:
                        final_child_order_names.append(child_name)
                
                if final_child_order_names != ordered_child_names:
                    self.save_image_order(final_child_order_names, item_path)
                
                for child_name in final_child_order_names:
                    child_path = item_path / child_name
                    folder_item['children'].append({'type': 'image', 'path': str(child_path), 'name': child_path.name})
                structured_list.append(folder_item)
        return structured_list

    def create_folder(self, folder_name: str):
        if not folder_name:
            return False, "フォルダ名が空です。"
        try:
            folder_path = self.base_dir / folder_name
            if folder_path.exists():
                return False, f"フォルダ '{folder_name}' は既に存在します。"
            folder_path.mkdir()
            order = self.load_image_order()
            order.append(str(folder_path))
            self.save_image_order(order)
            return True, f"フォルダ '{folder_name}' を作成しました。"
        except Exception as e:
            return False, f"フォルダ作成中にエラーが発生しました: {e}"

    def move_item(self, source_path_str: str, dest_folder_path_str: str):
        try:
            source_path = Path(source_path_str)
            dest_folder_path = Path(dest_folder_path_str)
            
            if not source_path.exists() or not dest_folder_path.is_dir():
                return False, "移動元または移動先フォルダが存在しません。"

            dest_path = dest_folder_path / source_path.name
            if dest_path.exists():
                return False, f"移動先に同名のファイル/フォルダが既に存在します: {source_path.name}"
            
            shutil.move(str(source_path), str(dest_path))

            source_json_path = self._get_setting_path(source_path)
            if source_json_path.exists():
                shutil.move(str(source_json_path), dest_folder_path / source_json_path.name)
            
            # ★★★ 変更点: order listの更新はsave_tree_orderに一任するため、ここでは行わない ★★★
            # source_parent = source_path.parent
            # ... (古いorder更新ロジックを削除) ...
            
            return True, f"'{source_path.name}' を '{dest_folder_path.name}' に移動しました。"
        except Exception as e:
            return False, f"移動中にエラーが発生しました: {e}"

    # ★★★ 変更点: エラーの原因だったget_current_path_for_itemメソッドを追加 ★★★
    def get_current_path_for_item(self, item_path_str: str) -> str:
        """
        指定されたアイテム名から、現在のフルパスを再帰的に検索して返します。
        D&Dによる移動後も正しいパスを追跡するために使用します。
        """
        if not item_path_str:
            return item_path_str
            
        try:
            item_name = Path(item_path_str).name
            # .rglobを使ってサブディレクトリ内も再帰的に検索
            found_paths = list(self.base_dir.rglob(item_name))
            
            if found_paths:
                # 最初に見つかったパスを返す
                return str(found_paths[0])
            else:
                # 見つからなければ、元のパスをそのまま返す
                return item_path_str
        except Exception:
            # 何かエラーが起きても、とりあえず元のパスを返す
            return item_path_str
