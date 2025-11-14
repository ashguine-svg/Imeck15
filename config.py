# config.py

import json
import shutil
from pathlib import Path
import os
import threading # ★ 1. threading をインポート

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

        # ★ 2. ロック機構の初期化
        # JSONファイルごとの書き込み競合を防ぐためのロック辞書
        self.item_json_locks = {}
        # ロック辞書にアクセスするための共通ロック
        self.item_json_locks_lock = threading.Lock()

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
                # --- ▼▼▼ 修正箇所 (4行削除) ▼▼▼ ---
                # "enabled": False,
                # "center": 1.0,
                # "range": 0.2,
                # "steps": 5,
                # --- ▲▲▲ 修正完了 ▲▲▲ ---
                "use_window_scale": True,
            },
            "capture_method": "mss",
            "frame_skip_rate": 2,
            "grayscale_matching": False,
            "strict_color_matching": False, 
            # --- ▼▼▼ 修正箇所 1/2 ▼▼▼ ---
            "use_opencl": False, # ★ ご要望に基づき False に変更
            # --- ▲▲▲ 修正完了 ▲▲▲ ---
            "lightweight_mode": {
                "enabled": True,
                "preset": "標準"
            },
            # --- ▼▼▼ 修正箇所 2/2 ▼▼▼ ---
            "screen_stability_check": {
                "enabled": False, # ★ ご要望に基づき False に変更
                "threshold": 8
            },
            # --- ▲▲▲ 修正完了 ▲▲▲ ---
            "eco_mode": {
                "enabled": True
            },
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

    # ★ 3. ロック取得用ヘルパーメソッド (RLock に修正)
    def _get_item_json_lock(self, json_path: Path) -> threading.Lock:
        """指定されたJSONパスに対応するロックを取得または生成します。"""
        with self.item_json_locks_lock:
            # str(json_path) をキーにすることで、異なるPathオブジェクトでも
            # 同じファイルパスなら同じロックを共有できるようにする
            path_key = str(json_path)
            if path_key not in self.item_json_locks:
                # ★★★ 修正: Lock から RLock (再入可能ロック) に変更 ★★★
                self.item_json_locks[path_key] = threading.RLock()
            return self.item_json_locks[path_key]

    # --- ▼▼▼ (新規追加) フィルタリング用ヘルパーメソッド ▼▼▼ ---
    def _filter_item_by_app(self, item_settings: dict, current_app_name: str) -> bool:
        """
        (新規) アイテムの設定と現在のアプリ名に基づき、
        そのアイテムを採用するかどうかを決定します。
        """
        # アプリ名が指定されていない場合 (全表示モード) は常に採用
        if not current_app_name:
            return True

        env_list = item_settings.get("environment_info", [])

        # 1. 環境情報が記録されていない場合は「共通」とみなし採用
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
                    break # 一致するものを発見
        
        # 2. 一致するアプリ名が見つかった場合は採用
        if has_matching_app:
            return True
        
        # 3. アプリ名が記録されているが、一致するものがない場合は不採用
        if has_any_app_name and not has_matching_app:
            return False

        # 4. アプリ名が一切記録されていない (app_name: None や 解像度のみ) 場合は採用
        return True
    # --- ▲▲▲ (新規追加) フィルタリング用ヘルパーメソッド ▲▲▲ ---

    def _get_setting_path(self, item_path: Path) -> Path:
        if item_path.is_dir():
            return item_path / self.folder_config_filename
        return item_path.with_suffix('.json')

    # ★ 4. load_item_setting にロックを追加
    def load_item_setting(self, item_path: Path) -> dict:
        setting_path = self._get_setting_path(item_path)
        # 対応するファイルロックを取得
        file_lock = self._get_item_json_lock(setting_path)

        with file_lock: # ロックを取得してファイル操作
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

    # ★ 5. save_item_setting にロックを追加
    def save_item_setting(self, item_path: Path, setting: dict):
        setting_path = self._get_setting_path(item_path)
        # 対応するファイルロックを取得
        file_lock = self._get_item_json_lock(setting_path)

        with file_lock: # ロックを取得してファイル操作
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
                self.save_image_order(child_order_filenames, folder_path_str)
        
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
    
    # ★ 6. 環境情報更新メソッド (RLock で保護するよう修正)
    def update_environment_info(self, item_path_str: str, env_data: dict):
        """
        (ワーカースレッドから実行)
        指定されたアイテムのJSONを安全に読み込み、
        新しい環境情報 (env_data) が既存でなければ追加して保存します。
        """
        if not item_path_str:
            return
            
        try:
            item_path = Path(item_path_str)
            
            # ★★★ 修正箇所 1/2: 先にロックを取得する ★★★
            setting_path = self._get_setting_path(item_path)
            file_lock = self._get_item_json_lock(setting_path)

            with file_lock: # ★★★ 修正箇所 2/2: メソッド全体をロックする ★★★
                # 1. 既存の設定を安全に読み込む (RLockなのでデッドロックしない)
                current_settings = self.load_item_setting(item_path)
                
                # 2. environment_info リストを取得 (なければ新規作成)
                env_list = current_settings.get("environment_info", [])
                
                # 3. 重複チェック
                is_duplicate = False
                for existing_env in env_list:
                    # env_data の内容が完全に一致するかチェック
                    if existing_env == env_data:
                        is_duplicate = True
                        break
                
                # 4. 重複がなければ追加
                if not is_duplicate:
                    env_list.append(env_data)
                    current_settings["environment_info"] = env_list
                    
                    # 5. 設定を安全に保存する (RLockなのでデッドロックしない)
                    self.save_item_setting(item_path, current_settings)
                    self.logger.log("[DEBUG] Environment info updated for %s", item_path.name)
                # else:
                    # self.logger.log("[DEBUG] Environment info already exists for %s", item_path.name)

        except Exception as e:
            self.logger.log("[ERROR] Failed to update environment info for %s: %s", item_path_str, str(e))

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

    # --- ▼▼▼ (修正) フィルタリング対応の get_hierarchical_list ▼▼▼ ---
    def get_hierarchical_list(self, current_app_name: str = None):
        structured_list = []
        
        # get_ordered_item_list は変更不要 (ソートされた全パスを取得)
        for item_path in self.get_ordered_item_list(): 
            
            # ★ フィルタリングのために設定を先に読み込む
            item_settings = self.load_item_setting(item_path)
            
            # ★ フィルタリングロジックの呼び出し
            if not self._filter_item_by_app(item_settings, current_app_name):
                continue # 不採用の場合はスキップ

            # --- (以下、採用されたアイテムのみ処理) ---
            
            if item_path.is_file():
                structured_list.append({
                    'type': 'image', 
                    'path': str(item_path), 
                    'name': item_path.name,
                    'settings': item_settings # ★ 読み込んだ設定を渡す
                })
            elif item_path.is_dir():
                folder_settings = item_settings # フォルダ設定として使用
                folder_item = {
                    'type': 'folder', 
                    'path': str(item_path), 
                    'name': item_path.name, 
                    'children': [], 
                    'settings': folder_settings
                }
                
                # --- ▼▼▼ 修正箇所 (get_ordered_item_list と同じロジックを適用) ▼▼▼
                
                # 1. フォルダ内の順序 (e.g., _sub_order.json) を読み込む
                ordered_child_names = self.load_image_order(item_path)
                
                # 2. フォルダ内のディスクをスキャン
                try:
                    all_child_images_on_disk = {p for p in item_path.iterdir() if p.is_file() and p.suffix.lower() in ('.png', '.jpg', '.jpeg', '.bmp')}
                except FileNotFoundError:
                    all_child_images_on_disk = set()

                all_child_names_on_disk = {p.name for p in all_child_images_on_disk}
                
                final_child_order_names = []
                
                # 3. 順序リストを走査し、ディスクに存在するアイテムだけを追加
                #    (JSONを「正」とし、ディスクスキャンの結果でフィルタリングする)
                for name in ordered_child_names:
                    if name in all_child_names_on_disk:
                        final_child_order_names.append(name)
                        # 処理済みのアイテムをディスクリストから削除
                        all_child_names_on_disk.remove(name)
                        
                # 4. (フォールバック) 順序リストになかったがディスクにあったアイテム
                #    (D&D操作とは関係ない、手動でのファイル追加などに対応)
                if all_child_names_on_disk:
                    new_child_names_sorted = sorted(list(all_child_names_on_disk))
                    final_child_order_names.extend(new_child_names_sorted)
                    
                    # ディスク上に新しいアイテムがあった場合のみ、
                    # 順序ファイル (_sub_order.json) を更新する
                    self.save_image_order(final_child_order_names, item_path)
                
                # --- ▲▲▲ 修正完了 ▲▲▲ ---
                
                for child_name in final_child_order_names:
                    child_path = item_path / child_name
                    
                    # ★ フォルダ内の子画像もフィルタリングする
                    child_settings = self.load_item_setting(child_path)
                    if self._filter_item_by_app(child_settings, current_app_name):
                        folder_item['children'].append({
                            'type': 'image', 
                            'path': str(child_path), 
                            'name': child_path.name,
                            'settings': child_settings # ★ 読み込んだ設定を渡す
                        })
                        
                structured_list.append(folder_item)
        return structured_list
    # --- ▲▲▲ (修正) フィルタリング対応完了 ▲▲▲ ---

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
