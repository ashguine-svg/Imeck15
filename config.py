# config.py

import json
import shutil
from pathlib import Path
import os

class ConfigManager:
    # ★★★ 1. __init__ で logger を受け取る ★★★
    def __init__(self, logger, base_dir_name: str = "click_pic"):
        self.logger = logger # Loggerインスタンスを保持
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

        # ★★★ 2. print を self.logger.log に変更 (翻訳キー使用) ★★★
        self.logger.log("log_cleanup_start")
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
                        self.logger.log("log_cleanup_deleted", str(json_path))
                        cleaned_count += 1
                    except OSError as e:
                        self.logger.log("log_cleanup_error_delete", str(json_path), str(e))

        except Exception as e:
            self.logger.log("log_cleanup_error_general", str(e))

        if cleaned_count > 0:
            self.logger.log("log_cleanup_complete_deleted", cleaned_count)
        else:
            self.logger.log("log_cleanup_complete_none")

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
            "screen_stability_check": {
                "enabled": True,
                "threshold": 8
            },
            "eco_mode": {
                "enabled": True
            },
            "language": "en_US" # ★★★ 3. 言語設定のデフォルトを追加 ★★★
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
            # ★★★ 4. print を self.logger.log に変更 (翻訳キー使用) ★★★
            self.logger.log("log_app_config_load_error", str(e))
            return default_config

    def save_app_config(self, config: dict):
        try:
            with open(self.app_config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            # ★★★ 5. print を self.logger.log に変更 (翻訳キー使用) ★★★
            self.logger.log("log_app_config_save_error", str(e))

    def load_window_scales(self) -> dict:
        if not self.window_scales_path.exists():
            return {}
        try:
            with open(self.window_scales_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, Exception) as e:
            # ★★★ 6. print を self.logger.log に変更 (翻訳キー使用) ★★★
            self.logger.log("log_window_scales_load_error", str(e))
            return {}

    def save_window_scales(self, scales_data: dict):
        try:
            with open(self.window_scales_path, 'w', encoding='utf-8') as f:
                json.dump(scales_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            # ★★★ 7. print を self.logger.log に変更 (翻訳キー使用) ★★★
            self.logger.log("log_window_scales_save_error", str(e))

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
            # ★★★ 8. print を self.logger.log に変更 (翻訳キー使用) ★★★
            self.logger.log("log_item_setting_load_error", str(setting_path), str(e))
            return default_setting

    def save_item_setting(self, item_path: Path, setting: dict):
        setting_path = self._get_setting_path(item_path)
        try:
            with open(setting_path, 'w', encoding='utf-8') as f:
                json.dump(setting, f, indent=2, ensure_ascii=False)
        except Exception as e:
            # ★★★ 9. print を self.logger.log に変更 (翻訳キー使用) ★★★
            self.logger.log("log_item_setting_save_error", str(setting_path), str(e))

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
            
    def save_tree_order_data(self, data_to_save: dict):
        """
        (ワーカースレッド) UIスレッドから渡された順序データでJSONファイルを上書きします。
        """
        try:
            # 1. トップレベルの順序を保存
            top_level_order = data_to_save.get('top_level', [])
            self.save_image_order(top_level_order, folder_path=None)
            
            # 2. 各フォルダの順序を保存
            folder_data_map = data_to_save.get('folders', {})
            for folder_path_str, child_order_filenames in folder_data_map.items():
                self.save_image_order(child_order_filenames, folder_path=folder_path_str)
        
        except Exception as e:
            # このログは core.py の _save_order_and_rebuild_async の
            # except ブロックでキャッチされます。
            self.logger.log("log_error_save_order_data", str(e))
            raise # エラーを呼び出し元に伝播させます

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
            # ★★★ 10. print を self.logger.log に変更 (翻訳キー使用) ★★★
            self.logger.log("log_item_delete_error", str(e))
            raise

    # --- 修正箇所A (rename_item メソッドの翻訳キーを修正) ---
    def rename_item(self, item_path_str: str, new_name: str):
        """
        ファイルまたはフォルダの名前を変更し、関連する設定ファイルと順序リストも更新します。
        (クロスプラットフォーム対応)
        """
        try:
            # 1. パスの検証と準備
            if not item_path_str or not new_name:
                # ★ 修正: log_rename_item_error_empty -> log_rename_error_empty
                return False, self.logger.locale_manager.tr("log_rename_error_empty")
            
            # OS依存の禁止文字チェック (簡易版)
            if any(char in new_name for char in '/\\:*?"<>|'):
                 # ★ 修正: log_rename_item_error_invalid_chars -> log_rename_error_general (汎用キー)
                 return False, self.logger.locale_manager.tr("log_rename_error_general", "Invalid characters in name")

            source_path = Path(item_path_str)
            if not source_path.exists():
                return False, self.logger.locale_manager.tr("log_move_item_error_not_exists")

            # 2. 新しいパスの決定と衝突確認
            dest_path = source_path.with_name(new_name)
            if dest_path.exists():
                # ★ 修正: log_move_item_error_exists -> log_rename_error_exists (JSONに合わせる)
                return False, self.logger.locale_manager.tr("log_rename_error_exists", new_name)

            source_json_path = self._get_setting_path(source_path)
            dest_json_path = self._get_setting_path(dest_path)

            # 3. 物理ファイル/フォルダのリネーム
            source_path.rename(dest_path)

            # 4. JSON設定のリネーム (存在する場合)
            if source_json_path.exists():
                source_json_path.rename(dest_json_path)

            # 5. 親の順序リストを更新
            parent_dir = source_path.parent
            order_file_owner = parent_dir if parent_dir != self.base_dir else None
            order = self.load_image_order(order_file_owner)

            # 順序リスト内のキー (トップレベルはフルパス、フォルダ内はファイル名)
            item_key_source = str(source_path) if order_file_owner is None else source_path.name
            item_key_dest = str(dest_path) if order_file_owner is None else dest_path.name

            if item_key_source in order:
                index = order.index(item_key_source)
                order[index] = item_key_dest
                self.save_image_order(order, order_file_owner)
            
            # ★ 修正: log_rename_item_success -> log_rename_success
            return True, self.logger.locale_manager.tr("log_rename_success", source_path.name, dest_path.name)
        
        except Exception as e:
            # ★ 修正: log_rename_item_error_general -> log_rename_error_general
            return False, self.logger.locale_manager.tr("log_rename_error_general", str(e))
    
    # --- 修正箇所B (get_ordered_item_list を完全なコードに置き換え) ---
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
        
        # ★★★ 'None' ではなく、必ずリスト [Path(p)] を返すようにします ★★★
        return [Path(p) for p in final_order]
    # --- 修正完了 ---

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
            # ★★★ 11. 修正 ★★★
            return False, self.logger.locale_manager.tr("log_create_folder_error_empty")
        try:
            folder_path = self.base_dir / folder_name
            if folder_path.exists():
                return False, self.logger.locale_manager.tr("log_create_folder_error_exists", folder_name)
            folder_path.mkdir()
            order = self.load_image_order()
            order.append(str(folder_path))
            self.save_image_order(order)
            return True, self.logger.locale_manager.tr("log_create_folder_success", folder_name)
        except Exception as e:
            return False, self.logger.locale_manager.tr("log_create_folder_error_general", str(e))

    def move_item(self, source_path_str: str, dest_folder_path_str: str):
        try:
            source_path = Path(source_path_str)
            dest_folder_path = Path(dest_folder_path_str)
            
            if not source_path.exists() or not dest_folder_path.is_dir():
                # ★★★ 12. 修正 ★★★
                return False, self.logger.locale_manager.tr("log_move_item_error_not_exists")

            dest_path = dest_folder_path / source_path.name
            if dest_path.exists():
                return False, self.logger.locale_manager.tr("log_move_item_error_exists", source_path.name)
            
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
            
            return True, self.logger.locale_manager.tr("log_move_item_success", source_path.name, dest_folder_path.name)
        except Exception as e:
            return False, self.logger.locale_manager.tr("log_move_item_error_general", str(e))
