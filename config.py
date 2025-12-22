# config.py
# ★★★ (修正) クリーンアップ時に、移動した画像のJSONを自動追従(レスキュー)させるロジックを追加 ★★★
# ★★★ (拡張) 隠しライフサイクル管理機能用の設定ロードを追加 ★★★

import json
import shutil
from pathlib import Path
import os
import threading

class ConfigManager:
    def __init__(self, logger, base_dir_name: str = "click_pic"):
        self.logger = logger # Loggerインスタンスを保持
        self.base_dir = Path.home() / base_dir_name
        self.base_dir.mkdir(exist_ok=True)
        self.folder_config_filename = "_folder_config.json"
        self.image_order_filename = "image_order.json"
        self.sub_order_filename = "_sub_order.json"
        self.app_config_path = self.base_dir / "app_config.json"
        self.window_scales_path = self.base_dir / "window_scales.json"

        # ロック機構の初期化
        self.item_json_locks = {}
        self.item_json_locks_lock = threading.Lock()

        # 初期化時にクリーンアップとレスキューを実行
        self._cleanup_orphaned_json_files()

    def _cleanup_orphaned_json_files(self):
        """
        孤立した設定JSONファイルを処理します。
        画像がOS上で移動されていた場合、JSONを追従して移動させます。
        画像が完全に削除されていた場合のみ、JSONを削除します。
        """
        protected_files = [
            self.app_config_path.name,
            self.window_scales_path.name,
            self.image_order_filename,
            self.folder_config_filename,
            self.sub_order_filename
        ]
        
        image_extensions = {'.png', '.jpg', '.jpeg', '.bmp'}

        self.logger.log("log_cleanup_start")
        cleaned_count = 0
        rescued_count = 0

        try:
            # 1. 現在存在するすべての画像のマップを作成 (ファイル名(拡張子なし) -> フルパス)
            #    これにより、画像がどこへ移動していても見つけられるようにする
            all_images_map = {}
            for img_path in self.base_dir.rglob('*'):
                if img_path.is_file() and img_path.suffix.lower() in image_extensions:
                    # 同名のファイルが複数ある場合は、最後に見つかったものを優先する形になるが、
                    # 通常の移動であれば問題ないレベル
                    all_images_map[img_path.stem] = img_path

            # 2. すべてのJSONファイルをチェック
            for json_path in self.base_dir.rglob('*.json'):
                if json_path.name in protected_files:
                    continue

                base_name = json_path.stem
                parent_dir = json_path.parent
                
                # まず、同じフォルダに画像があるかチェック (通常の状態)
                has_pair_locally = False
                for ext in image_extensions:
                    if (parent_dir / (base_name + ext)).exists():
                        has_pair_locally = True
                        break
                
                if has_pair_locally:
                    continue # 正常なので何もしない

                # --- ここからレスキュー処理 ---
                # 同じフォルダにない場合、別のフォルダに画像が移動していないか探す
                if base_name in all_images_map:
                    # 画像が見つかった！ JSONをそちらに移動する
                    new_image_path = all_images_map[base_name]
                    new_json_path = new_image_path.with_suffix('.json')
                    
                    try:
                        # 移動先に既にJSONがない場合のみ移動
                        if not new_json_path.exists():
                            shutil.move(str(json_path), str(new_json_path))
                            self.logger.log(f"[INFO] Rescued config JSON: Moved from '{json_path.parent.name}' to '{new_image_path.parent.name}'")
                            rescued_count += 1
                        else:
                            # 移動先に既にJSONがあるなら、古い方は不要なので削除
                            # (例: 画像を上書き移動した場合など)
                            json_path.unlink()
                            self.logger.log(f"[INFO] Deleted duplicate JSON: {json_path.name}")
                            cleaned_count += 1
                    except Exception as e:
                        self.logger.log(f"[ERROR] Failed to move rescued JSON: {e}")
                else:
                    # 画像がどこにも見つからない -> 本当に削除されたファイル
                    try:
                        json_path.unlink()
                        self.logger.log("log_cleanup_deleted", str(json_path))
                        cleaned_count += 1
                    except OSError as e:
                        self.logger.log("log_cleanup_error_delete", str(json_path), str(e))

        except Exception as e:
            self.logger.log("log_cleanup_error_general", str(e))

        if cleaned_count > 0 or rescued_count > 0:
            self.logger.log(f"[INFO] Cleanup finished. Deleted: {cleaned_count}, Rescued: {rescued_count}")
        else:
            self.logger.log("log_cleanup_complete_none")

    def load_app_config(self) -> dict:
        default_config = {
            "auto_scale": {
                "use_window_scale": True,
                "enabled": False,
                "center": 1.0,
                "range": 0.2,
                "steps": 5
            },
            "capture_method": "mss",
            "frame_skip_rate": 2,
            "grayscale_matching": False,
            "strict_color_matching": False, 
            "use_opencl": False, 
            "lightweight_mode": {
                "enabled": True,
                "preset": "標準"
            },
            "screen_stability_check": {
                "enabled": False,
                "threshold": 8
            },
            "eco_mode": {
                "enabled": True
            },
            # --- ▼▼▼ 拡張ライフサイクル管理機能 (隠し設定) ▼▼▼ ---
            "extended_lifecycle_hooks": {
                "active": False,              # 機能の有効化フラグ
                "process_marker": "",         # 監視対象プロセス名 (例: game.exe)
                "window_context_marker": "",  # ウィンドウタイトル名 (安全装置用)
                "resource_link_id": "",       # 外部リソースID (例: 123456)
                "retry_tolerance": 10,        # 応答タイムアウト判定閾値
                "state_check_interval": 5.0   # 状態確認間隔(秒)
            },
            # --- ▲▲▲ 追加完了 ▲▲▲ ---
            "language": "en_US" 
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
            self.logger.log("log_app_config_load_error", str(e))
            return default_config

    def save_app_config(self, config: dict):
        try:
            with open(self.app_config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.logger.log("log_app_config_save_error", str(e))

    def load_window_scales(self) -> dict:
        if not self.window_scales_path.exists():
            return {}
        try:
            with open(self.window_scales_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, Exception) as e:
            self.logger.log("log_window_scales_load_error", str(e))
            return {}

    def save_window_scales(self, scales_data: dict):
        try:
            with open(self.window_scales_path, 'w', encoding='utf-8') as f:
                json.dump(scales_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.logger.log("log_window_scales_save_error", str(e))

    def _get_item_json_lock(self, json_path: Path) -> threading.Lock:
        with self.item_json_locks_lock:
            path_key = str(json_path)
            if path_key not in self.item_json_locks:
                self.item_json_locks[path_key] = threading.RLock()
            return self.item_json_locks[path_key]

    def _filter_item_by_app(self, item_settings: dict, current_app_name: str) -> bool:
        if not current_app_name:
            return True
        
        # OCR設定が有効な画像は常に表示する（environment_infoに関係なく）
        ocr_settings = item_settings.get("ocr_settings", {})
        if ocr_settings and ocr_settings.get("enabled", False):
            return True
        
        env_list = item_settings.get("environment_info", [])
        if not env_list:
            return True
        has_matching_app = False
        has_any_app_name = False
        for entry in env_list:
            app_name = entry.get("app_name")
            if app_name:
                has_any_app_name = True
                if app_name == current_app_name:
                    has_matching_app = True
                    break 
        if has_matching_app:
            return True
        if has_any_app_name and not has_matching_app:
            return False
        return True

    def _get_setting_path(self, item_path: Path) -> Path:
        if item_path.is_dir():
            return item_path / self.folder_config_filename
        return item_path.with_suffix('.json')

    def load_item_setting(self, item_path: Path) -> dict:
        setting_path = self._get_setting_path(item_path)
        file_lock = self._get_item_json_lock(setting_path)

        with file_lock: 
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
                self.logger.log("log_item_setting_load_error", str(setting_path), str(e))
                return default_setting

    def save_item_setting(self, item_path: Path, setting: dict):
        setting_path = self._get_setting_path(item_path)
        file_lock = self._get_item_json_lock(setting_path)

        with file_lock:
            try:
                with open(setting_path, 'w', encoding='utf-8') as f:
                    json.dump(setting, f, indent=2, ensure_ascii=False)
            except Exception as e:
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
        # フォルダが存在しない場合は作成（親ディレクトリのみ）
        # 注意: folder_pathが存在しない場合でも、親ディレクトリは作成する
        # ただし、folder_path自体が存在しない場合は、そのフォルダは作成しない（ファイル移動が完了していない可能性があるため）
        if folder_path:
            folder_path_obj = Path(folder_path)
            # folder_pathが存在する場合のみ、そのフォルダ内に保存
            if not folder_path_obj.exists():
                # フォルダが存在しない場合は、エラーログを出力してスキップ
                self.logger.log("[WARN] Folder does not exist, skipping order save: %s", folder_path)
                return
        # 親ディレクトリが存在しない場合は作成
        order_path.parent.mkdir(parents=True, exist_ok=True)
        with open(order_path, 'w', encoding='utf-8') as f:
            json.dump(order_list, f, indent=2, ensure_ascii=False)
            
    def save_tree_order_data(self, data_to_save: dict):
        try:
            top_level_order = data_to_save.get('top_level', [])
            self.save_image_order(top_level_order, folder_path=None)
            
            folder_data_map = data_to_save.get('folders', {})
            for folder_path_str, child_order_filenames in folder_data_map.items():
                # フォルダが存在しない場合はスキップ（D&Dで移動したフォルダがまだ移動されていない可能性があるため）
                folder_path_obj = Path(folder_path_str)
                if not folder_path_obj.exists():
                    self.logger.log("[WARN] Folder does not exist, skipping order save: %s", folder_path_str)
                    continue
                self.save_image_order(child_order_filenames, folder_path_str)
        
        except Exception as e:
            self.logger.log("log_error_save_order_data", str(e))
            raise

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
            self.logger.log("log_item_delete_error", str(e))
            raise

    def rename_item(self, item_path_str: str, new_name: str):
        try:
            if not item_path_str or not new_name:
                return False, self.logger.locale_manager.tr("log_rename_error_empty")
            if any(char in new_name for char in '/\\:*?"<>|'):
                 return False, self.logger.locale_manager.tr("log_rename_error_general", "Invalid characters in name")

            source_path = Path(item_path_str)
            if not source_path.exists():
                return False, self.logger.locale_manager.tr("log_move_item_error_not_exists")

            dest_path = source_path.with_name(new_name)
            if dest_path.exists():
                return False, self.logger.locale_manager.tr("log_rename_error_exists", new_name)

            source_json_path = self._get_setting_path(source_path)
            dest_json_path = self._get_setting_path(dest_path)

            source_path.rename(dest_path)
            if source_json_path.exists():
                source_json_path.rename(dest_json_path)

            parent_dir = source_path.parent
            order_file_owner = parent_dir if parent_dir != self.base_dir else None
            order = self.load_image_order(order_file_owner)

            item_key_source = str(source_path) if order_file_owner is None else source_path.name
            item_key_dest = str(dest_path) if order_file_owner is None else dest_path.name

            if item_key_source in order:
                index = order.index(item_key_source)
                order[index] = item_key_dest
                self.save_image_order(order, order_file_owner)
            
            return True, self.logger.locale_manager.tr("log_rename_success", source_path.name, dest_path.name)
        except Exception as e:
            return False, self.logger.locale_manager.tr("log_rename_error_general", str(e))
    
    def update_environment_info(self, item_path_str: str, env_data: dict):
        if not item_path_str: return
        try:
            item_path = Path(item_path_str)
            setting_path = self._get_setting_path(item_path)
            file_lock = self._get_item_json_lock(setting_path)
            with file_lock:
                current_settings = self.load_item_setting(item_path)
                env_list = current_settings.get("environment_info", [])
                is_duplicate = False
                for existing_env in env_list:
                    if existing_env == env_data:
                        is_duplicate = True
                        break
                if not is_duplicate:
                    env_list.append(env_data)
                    current_settings["environment_info"] = env_list
                    self.save_item_setting(item_path, current_settings)
                    self.logger.log("[DEBUG] Environment info updated for %s", item_path.name)
        except Exception as e:
            self.logger.log("[ERROR] Failed to update environment info for %s: %s", item_path_str, str(e))

    def _get_recursive_list(self, current_dir: Path, current_app_name: str = None):
        """
        指定されたディレクトリ以下のアイテムを再帰的に取得して
        階層構造のリストを作成するヘルパーメソッド。
        OSでのファイル作成・削除を検知し、順序リストを自動同期します。
        """
        structured_list = []
        
        order_file_owner = current_dir if current_dir != self.base_dir else None
        ordered_raw_names = self.load_image_order(order_file_owner)
        
        try:
            all_items_on_disk = {
                p for p in current_dir.iterdir() 
                if p.is_dir() or (p.is_file() and p.suffix.lower() in ('.png', '.jpg', '.jpeg', '.bmp'))
            }
        except FileNotFoundError:
            all_items_on_disk = set()

        all_names_on_disk = {p.name for p in all_items_on_disk}
        
        final_order_names = []
        is_order_changed = False
        
        # 順序ファイルから読み込んだ名前を処理
        for raw_name in ordered_raw_names:
            # raw_nameはフルパス（ルートの場合）またはファイル名（サブフォルダの場合）の可能性がある
            # どちらの場合でも、ファイル名を取得して比較
            name = Path(raw_name).name
            if name in all_names_on_disk:
                final_order_names.append(name)
                all_names_on_disk.remove(name)
            else:
                # 順序ファイルに記録されているが、ディスク上に存在しない
                is_order_changed = True
        
        # 順序ファイルにない新しいファイルを検出して追加（一番下に追加）
        if all_names_on_disk:
            new_names_sorted = sorted(list(all_names_on_disk))
            final_order_names.extend(new_names_sorted)
            is_order_changed = True
            
        # 順序ファイルを更新（ディスク上の状態と同期）
        if is_order_changed:
            list_to_save = []
            for name in final_order_names:
                if order_file_owner is None: # Root
                     list_to_save.append(str(current_dir / name))
                else: # Subfolder
                     list_to_save.append(name)
            try:
                # 修正: try ブロックのインデントを適用
                self.save_image_order(list_to_save, order_file_owner)
                self.logger.log("[INFO] Sync: Order file updated for %s", current_dir.name)
            except Exception as e:
                self.logger.log("[ERROR] Failed to save order file for %s: %s", current_dir.name, str(e))

        for name in final_order_names:
            item_path = current_dir / name
            item_settings = self.load_item_setting(item_path)
            
            if not self._filter_item_by_app(item_settings, current_app_name):
                continue
            
            if item_path.is_file():
                structured_list.append({
                    'type': 'image', 
                    'path': str(item_path), 
                    'name': item_path.name,
                    'settings': item_settings
                })
            elif item_path.is_dir():
                children = self._get_recursive_list(item_path, current_app_name)
                folder_item = {
                    'type': 'folder', 
                    'path': str(item_path), 
                    'name': item_path.name, 
                    'children': children, 
                    'settings': item_settings
                }
                structured_list.append(folder_item)
                
        return structured_list

    def get_hierarchical_list(self, current_app_name: str = None):
        return self._get_recursive_list(self.base_dir, current_app_name)

    def create_folder(self, folder_name: str):
        if not folder_name:
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
        """
        アイテムを指定されたフォルダへ移動します。
        画像ファイルに対応する設定JSONファイルも一緒に移動します。
        """
        try:
            source_path = Path(source_path_str)
            dest_folder_path = Path(dest_folder_path_str)
            
            if not source_path.exists() or not dest_folder_path.is_dir():
                return False, self.logger.locale_manager.tr("log_move_item_error_not_exists")

            dest_path = dest_folder_path / source_path.name
            if dest_path.exists():
                return False, self.logger.locale_manager.tr("log_move_item_error_exists", source_path.name)
            
            source_json_path = self._get_setting_path(source_path)
            dest_json_path = dest_folder_path / source_json_path.name
            
            shutil.move(str(source_path), str(dest_path))

            if source_json_path.exists():
                shutil.move(str(source_json_path), str(dest_json_path))
                self.logger.log("[DEBUG] Moved config JSON: %s", source_json_path.name)
            
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
