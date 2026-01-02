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

    def _collect_images_recursively(self, children_list):
        """
        子要素リストから再帰的に画像パスのみを抽出し、
        ツリーの並び順（深さ優先探索）通りに並べたフラットなリストを作成する。
        """
        images = []
        for child in children_list:
            settings = child.get('settings', {})
            if settings.get('mode') == 'excluded':
                continue

            if child['type'] == 'image':
                images.append(child['path'])
            elif child['type'] == 'folder':
                images.extend(self._collect_images_recursively(child.get('children', [])))
        return images

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
        search_multipliers = [1.0] 
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
        
        # --- 内部関数: コンテキストスタック方式（階層構造をフラットに解決） ---
        def process_list_recursive(item_list, active_contexts=None):
            if active_contexts is None:
                active_contexts = []

            for item_data in item_list:
                if item_data['type'] == 'folder':
                    current_path = item_data['path']
                    settings = item_data['settings']
                    current_mode = settings.get('mode', 'normal')

                    if current_mode == 'excluded':
                        continue 

                    # フォルダマップの初期化
                    if current_path not in folder_children_map:
                        folder_children_map[current_path] = set()

                    # --- コンテキスト（グループ）の定義 ---
                    
                    cooldown_time = 0
                    if current_mode == 'cooldown':
                        cooldown_time = settings.get('cooldown_time', 30)

                    # タイマー設定の登録
                    if current_mode == 'priority_timer':
                        interval_seconds = settings.get('priority_interval', 10) * 60
                        if not is_monitoring:
                             priority_timers[current_path] = time.time() + interval_seconds
                        elif current_path not in existing_priority_timers:
                             priority_timers[current_path] = time.time() + interval_seconds
                        else:
                             priority_timers[current_path] = existing_priority_timers[current_path]
                        
                        # ★ タイマーモード継続用フラグ（必須）
                        folder_children_map[current_path].add("___TIMER_KEEPALIVE___")
                    
                    # シーケンス情報の準備
                    sequence_info = None
                    ordered_children_paths = []
                    if current_mode == 'priority_sequence':
                        for child in item_data.get('children', []):
                            child_settings = child.get('settings', {})
                            if child_settings.get('mode') != 'excluded':
                                ordered_children_paths.append(child['path'])
                        sequence_info = {
                            'interval': settings.get('sequence_interval', 3),
                            'ordered_paths': ordered_children_paths
                        }

                    # コンテキストオブジェクトの作成
                    new_context = {
                        'path': current_path,
                        'mode': current_mode,
                        'cooldown_time': cooldown_time,
                        'sequence_info': sequence_info,
                        'trigger_path': current_path if current_mode != 'normal' else None
                    }

                    # スタックに積んで再帰
                    next_active_contexts = active_contexts + [new_context]
                    process_list_recursive(item_data.get('children', []), next_active_contexts)

                elif item_data['type'] == 'image':
                    # --- 画像の処理 ---
                    path = item_data['path']
                    
                    # 1. 各コンテキストへの所属登録（これがロック機能の基礎）
                    #    画像は、現在アクティブな「すべての親フォルダ」のメンバーになります。
                    #    しかし、親フォルダの画像は、子フォルダのメンバーにはなりません（ここが重要）。
                    for ctx in active_contexts:
                        group_path = ctx['path']
                        if group_path in folder_children_map:
                            folder_children_map[group_path].add(path)

                    # 2. キャッシュエントリの作成（トリガー情報の設定）
                    
                    # 直近の「特別なモード」を持つ親（または自分）を探す
                    nearest_special_ctx = None
                    for ctx in reversed(active_contexts):
                        if ctx['mode'] in ['priority_timer', 'priority_image', 'priority_sequence', 'cooldown']:
                            nearest_special_ctx = ctx
                            break
                    
                    # ターゲットとなるコンテキストを決定
                    # 見つからなければ直近の親（通常フォルダ）
                    target_ctx = nearest_special_ctx if nearest_special_ctx else (active_contexts[-1] if active_contexts else None)

                    if target_ctx:
                        scan_group_path = target_ctx['path']
                        folder_mode = target_ctx['mode']
                        cooldown_time = target_ctx['cooldown_time']
                        sequence_info = target_ctx['sequence_info']
                        priority_trigger_path = target_ctx['trigger_path']
                        
                        # ★★★ 修正: モードはそのまま保持する（normalに戻さない） ★★★
                        # これにより、クリック時に正しく MonitoringProcessor が反応し、
                        # PriorityState / SequenceState に移行します。
                        # 移行後は folder_children_map[scan_group_path] だけを監視するため、
                        # 親フォルダの画像は除外され、「ロック」されます。
                        
                    else:
                        scan_group_path = None
                        folder_mode = 'normal'
                        cooldown_time = 0
                        sequence_info = None
                        priority_trigger_path = None

                    self._process_item_for_cache(
                        item_data, 
                        scales, 
                        scan_group_path,
                        folder_mode,
                        priority_trigger_path,
                        cooldown_time,
                        sequence_info,
                        normal_cache, 
                        backup_cache
                    )
        
        process_list_recursive(hierarchical_list)
        
        self.logger.log("log_cache_build_complete", len(normal_cache), len(backup_cache))
        self.logger.log("log_priority_timers", len(priority_timers))

        return normal_cache, backup_cache, priority_timers, folder_children_map

    def _process_item_for_cache(self, item_data, scales, folder_path, folder_mode, priority_trigger_path, cooldown_time, sequence_info, normal_cache, backup_cache):
        try:
            path = item_data['path']
            path_obj = Path(path)
            if not path_obj.exists() or not path_obj.is_file():
                self.logger.log("log_warn_image_load_failed", path_obj.name)
                return
            
            settings = self.config_manager.load_item_setting(path_obj)
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
                'sequence_info': sequence_info
            }
            
            if settings.get('backup_click', False):
                backup_cache[path] = cache_entry
            else:
                normal_cache[path] = cache_entry

        except Exception as e:
            self.logger.log("log_cache_create_failed", item_data.get('name'), str(e))
