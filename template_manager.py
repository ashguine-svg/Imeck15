# template_manager.py

import cv2
import numpy as np
from pathlib import Path
import time

OPENCL_AVAILABLE = False
try:
    if cv2.ocl.haveOpenCL():
        OPENCL_AVAILABLE = True
except Exception:
    pass

class TemplateManager:
    """
    画像ファイルを読み込み、認識用のテンプレートキャッシュを構築・管理するクラス。
    """
    def __init__(self, config_manager, logger):
        self.config_manager = config_manager
        self.logger = logger

    def build_cache(self, app_config, current_window_scale, effective_capture_scale, is_monitoring, existing_priority_timers, current_app_name: str = None):
        """
        設定に基づいてテンプレートキャッシュを構築します。
        """
        normal_cache = {}
        backup_cache = {}
        priority_timers = {}
        folder_children_map = {} 

        auto_scale_settings = app_config.get('auto_scale', {})
        use_window_scale_base = auto_scale_settings.get('use_window_scale', True)
        
        # --- ベースとなるスケールの決定 ---
        base_window_scale = 1.0
        if use_window_scale_base and current_window_scale is not None:
            base_window_scale = current_window_scale
        
        # --- マルチスケール探索の準備 ---
        search_multipliers = [1.0] # デフォルトは変動なし
        
        if auto_scale_settings.get('enabled', False):
            center = auto_scale_settings.get('center', 1.0)
            rng = auto_scale_settings.get('range', 0.2)
            steps = auto_scale_settings.get('steps', 5)
            
            min_s = center - rng
            max_s = center + rng
            
            if steps > 1:
                search_multipliers = np.linspace(min_s, max_s, steps)
                self.logger.log("log_scale_search_enabled", steps, f"{center:.2f}")
            else:
                 search_multipliers = [center]
        
        # 最終的な適用スケールリストを作成
        scales = []
        for multiplier in search_multipliers:
            final_scale = base_window_scale * multiplier * effective_capture_scale
            if final_scale > 0:
                scales.append(final_scale)
        
        scales = sorted(list(set(scales)))

        if effective_capture_scale != 1.0:
            self.logger.log("log_capture_scale_applied", f"{effective_capture_scale:.2f}")
        
        if use_window_scale_base and current_window_scale is not None:
            self.logger.log("log_window_scale_applied", f"{current_window_scale:.3f}")

        log_scales = ", ".join([f"{s:.3f}" for s in scales])
        self.logger.log("log_final_scales", log_scales)
        
        hierarchical_list = self.config_manager.get_hierarchical_list(current_app_name)
        
        # --- 内部関数: 再帰的にリストを処理 ---
        def process_list_recursive(item_list, inherited_context=None):
            """
            inherited_context: {
                'scan_group_path': str, 
                'cooldown_time': int,
                'folder_mode': str, # ★ 追加
                'sequence_interval': int, # ★ 追加
                'ordered_children_paths': list # ★ 追加 (順序優先用)
            }
            """
            for item_data in item_list:
                if item_data['type'] == 'folder':
                    current_path = item_data['path']
                    settings = item_data['settings']
                    current_mode = settings.get('mode', 'normal')

                    if current_mode == 'excluded':
                        continue 

                    # コンテキストの決定
                    if inherited_context:
                        # 親の設定を継承
                        scan_group_path = inherited_context['scan_group_path']
                        cooldown_time = inherited_context.get('cooldown_time', 0)
                        
                        # 親が順序優先なら、子はそれを知る必要があるが、
                        # 基本的に順序優先は「ルート直下」または「そのフォルダ内」で完結するため、
                        # ここでは親の sequence 情報は継承しない（孫への遷移は monitoring_states で制御）
                        # ただし、孫が画像として登録される際に、自分がどの順序リストに属するかは知る必要があるか？
                        # → 今回の仕様では「フォルダ内の一番先頭の画像」がトリガー。
                        #    フォルダ内のアイテムリストを作成しておく。
                        folder_ordered_children = []
                        
                    else:
                        # ルートフォルダ
                        scan_group_path = current_path
                        cooldown_time = 0
                        if current_mode == 'cooldown':
                            cooldown_time = settings.get('cooldown_time', 30)

                        if current_path not in folder_children_map:
                            folder_children_map[current_path] = set()

                        if current_mode == 'priority_timer':
                            interval_seconds = settings.get('priority_interval', 10) * 60
                            if not is_monitoring:
                                 priority_timers[current_path] = time.time() + interval_seconds
                            elif current_path not in existing_priority_timers:
                                 priority_timers[current_path] = time.time() + interval_seconds
                            else:
                                 priority_timers[current_path] = existing_priority_timers[current_path]
                    
                    # ★ このフォルダ直下の画像（およびフォルダ）の順序リストを作成
                    # item_data['children'] は既にソート/同期済み
                    ordered_children_paths = []
                    for child in item_data.get('children', []):
                        # 除外設定やフォルダを除いた純粋な画像のリストにするか、
                        # フォルダも含めて再帰的にするか？
                        # 仕様：「フォルダ内の画像A・B・C・D」
                        # ここではシンプルに、このフォルダの children にある 'path' をリスト化
                        ordered_children_paths.append(child['path'])

                    # 次の階層へ渡すコンテキスト
                    next_context = {
                        'scan_group_path': scan_group_path,
                        'cooldown_time': cooldown_time,
                        'folder_mode': current_mode, # ★
                        'sequence_interval': settings.get('sequence_interval', 3), # ★
                        'ordered_children_paths': ordered_children_paths # ★
                    }

                    process_list_recursive(item_data.get('children', []), next_context)

                elif item_data['type'] == 'image':
                    # 画像処理
                    if inherited_context:
                        scan_group_path = inherited_context['scan_group_path']
                        cooldown_time = inherited_context.get('cooldown_time', 0)
                        parent_mode = inherited_context.get('folder_mode', 'normal')
                        
                        if scan_group_path in folder_children_map:
                            folder_children_map[scan_group_path].add(item_data['path'])
                            
                        # 親フォルダ設定
                        parent_path = str(Path(item_data['path']).parent)
                        
                        priority_trigger_path = None
                        sequence_info = None # ★

                        if parent_mode == 'priority_image':
                            priority_trigger_path = parent_path
                        elif parent_mode == 'priority_sequence':
                            # ★ 順序優先の場合、トリガーパスと順序情報をキャッシュに持たせる
                            priority_trigger_path = parent_path
                            sequence_info = {
                                'interval': inherited_context.get('sequence_interval', 3),
                                'ordered_paths': inherited_context.get('ordered_children_paths', [])
                            }

                    else:
                        scan_group_path = None
                        parent_mode = 'normal'
                        priority_trigger_path = None
                        cooldown_time = 0
                        sequence_info = None

                    self._process_item_for_cache(
                        item_data, 
                        scales, 
                        scan_group_path,
                        parent_mode,
                        priority_trigger_path,
                        cooldown_time,
                        sequence_info, # ★引数追加
                        normal_cache, 
                        backup_cache
                    )
        
        process_list_recursive(hierarchical_list)
        
        self.logger.log("log_cache_build_complete", len(normal_cache), len(backup_cache))
        self.logger.log("log_priority_timers", len(priority_timers))

        return normal_cache, backup_cache, priority_timers, folder_children_map

    # ★ 引数 sequence_info を追加
    def _process_item_for_cache(self, item_data, scales, folder_path, folder_mode, priority_trigger_path, cooldown_time, sequence_info, normal_cache, backup_cache):
        try:
            path = item_data['path']
            settings = self.config_manager.load_item_setting(Path(path))

            has_point_click = settings.get('point_click') and settings.get('click_position')
            has_range_click = settings.get('range_click') and settings.get('click_rect')

            if not (has_point_click or has_range_click):
                return
            
            with open(path, 'rb') as f:
                file_bytes = np.fromfile(f, np.uint8)
            original_image = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)

            if original_image is None:
                self.logger.log("log_warn_image_load_failed", Path(path).name)
                return

            image_to_process = original_image
            
            if settings.get('roi_enabled', False):
                h, w = original_image.shape[:2]
                roi_mode = settings.get('roi_mode', 'fixed')
                rect_to_use = settings.get('roi_rect_variable') if roi_mode == 'variable' else settings.get('roi_rect')
                
                if rect_to_use:
                    x1, y1, x2, y2 = max(0, rect_to_use[0]), max(0, rect_to_use[1]), min(w, rect_to_use[2]), min(h, rect_to_use[3])
                    if x1 < x2 and y1 < y2:
                        image_to_process = original_image[y1:y2, x1:x2]
                    else:
                        self.logger.log("log_warn_invalid_roi", Path(path).name)
                else:
                    self.logger.log("log_warn_unset_roi", Path(path).name)
            
            use_opencl = OPENCL_AVAILABLE and cv2.ocl.useOpenCL()

            scaled_templates = []
            for scale in scales:
                if scale <= 0: continue
                h, w = image_to_process.shape[:2]
                new_w, new_h = int(w * scale), int(h * scale)
                if new_w > 0 and new_h > 0:
                    inter = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
                    resized_image = cv2.resize(image_to_process, (new_w, new_h), interpolation=inter)
                    resized_gray = cv2.cvtColor(resized_image, cv2.COLOR_BGR2GRAY)
                    t_h, t_w = resized_image.shape[:2]
                    template_entry = {'scale': scale, 'image': resized_image, 'gray': resized_gray, 'shape': (t_h, t_w)}

                    if use_opencl:
                        try:
                            template_entry['image_umat'] = cv2.UMat(resized_image)
                            template_entry['gray_umat'] = cv2.UMat(resized_gray)
                        except Exception as e:
                            if 'image_umat' in template_entry: del template_entry['image_umat']
                            if 'gray_umat' in template_entry: del template_entry['gray_umat']
                            self.logger.log("log_umat_convert_error", Path(path).name, str(e))

                    scaled_templates.append(template_entry)

            cache_entry = {
                'settings': settings, 'path': path, 'scaled_templates': scaled_templates,
                'folder_path': folder_path,
                'folder_mode': folder_mode,
                'priority_trigger_path': priority_trigger_path,
                'cooldown_time': cooldown_time,
                'sequence_info': sequence_info # ★ 追加
            }
            
            if settings.get('backup_click', False):
                backup_cache[path] = cache_entry
            else:
                normal_cache[path] = cache_entry

        except Exception as e:
            self.logger.log("log_cache_create_failed", item_data.get('name'), str(e))
