# config.py

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

    def load_app_config(self) -> dict:
        default_config = {
            "auto_scale": {
                "enabled": False,
                "center": 1.0,
                "range": 0.2,
                "steps": 5,
            },
            "capture_method": "mss",
            "frame_skip_rate": 2,
            # ★★★ 変更点: グローバルなグレースケール設定を追加 ★★★
            "grayscale_matching": False
        }
        if not self.app_config_path.exists():
            return default_config
        try:
            with open(self.app_config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                for key, value in default_config.items():
                    if key not in config:
                        config[key] = value
                    elif isinstance(value, dict):
                         for sub_key, sub_value in value.items():
                              if sub_key not in config[key]:
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
            default_setting = {'is_excluded': False}
        else:
            # ★★★ 変更点: 'matching_mode'をデフォルト設定から削除 ★★★
            default_setting = {
                'image_path': str(item_path), 'click_position': None, 'click_rect': None, 'roi_rect': None,
                'roi_enabled': False, 'point_click': True, 'range_click': False,
                'random_click': False, 'interval_time': 1.5, 'backup_click': False,
                'backup_time': 300.0, 'threshold': 0.8
            }
        if not setting_path.exists():
            return default_setting
        try:
            with open(setting_path, 'r', encoding='utf-8') as f:
                setting = json.load(f)
                # 古い設定ファイルから不要なキーを削除
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

    def toggle_folder_exclusion(self, folder_path_str: str):
        folder_path = Path(folder_path_str)
        if not folder_path.is_dir(): return False
        settings = self.load_item_setting(folder_path)
        settings['is_excluded'] = not settings.get('is_excluded', False)
        self.save_item_setting(folder_path, settings)
        return settings['is_excluded']

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
                shutil.rmtree(item_path)
            elif item_path.is_file():
                setting_path = self._get_setting_path(item_path)
                item_path.unlink()
                if setting_path.exists():
                    setting_path.unlink()
            
            parent_dir = item_path.parent
            order = self.load_image_order(parent_dir if parent_dir != self.base_dir else None)
            
            item_key = str(item_path) if parent_dir == self.base_dir else item_path.name
            if item_key in order:
                order.remove(item_key)
                self.save_image_order(order, parent_dir if parent_dir != self.base_dir else None)

        except Exception as e:
            print(f"アイテムの削除中にエラーが発生しました: {e}")
            raise

    def get_ordered_item_list(self) -> list:
        ordered_paths_str = self.load_image_order()
        all_items = {p for p in self.base_dir.iterdir() if p.is_dir() or p.suffix.lower() in ('.png', '.jpg', '.jpeg', '.bmp')}
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
                folder_item = {'type': 'folder', 'path': str(item_path), 'name': item_path.name, 'children': [], 'is_excluded': folder_settings.get('is_excluded', False)}
                
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
            
            source_parent = source_path.parent
            source_order_list = self.load_image_order(None if source_parent == self.base_dir else source_parent)
            item_key_source = str(source_path) if source_parent == self.base_dir else source_path.name
            if item_key_source in source_order_list:
                source_order_list.remove(item_key_source)
                self.save_image_order(source_order_list, None if source_parent == self.base_dir else source_parent)

            dest_order_list = self.load_image_order(None if dest_folder_path == self.base_dir else dest_folder_path)
            item_key_dest = str(dest_path) if dest_folder_path == self.base_dir else dest_path.name
            dest_order_list.append(item_key_dest)
            self.save_image_order(dest_order_list, None if dest_folder_path == self.base_dir else dest_folder_path)
            
            return True, f"'{source_path.name}' を '{dest_folder_path.name}' に移動しました。"
        except Exception as e:
            return False, f"移動中にエラーが発生しました: {e}"