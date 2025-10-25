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
    # ★★★ 1. __init__ で logger を受け取る ★★★
    def __init__(self, config_manager, logger):
        self.config_manager = config_manager
        self.logger = logger # Loggerインスタンスを保持

    def build_cache(self, app_config, current_window_scale, effective_capture_scale, is_monitoring, existing_priority_timers):
        """
        設定に基づいてテンプレートキャッシュを構築します。

        Args:
            app_config (dict): アプリケーション全体の設定。
            current_window_scale (float | None): 現在のウィンドウのスケール。
            effective_capture_scale (float): 軽量化モードなどを考慮した実効キャプチャスケール。
            is_monitoring (bool): 現在監視中かどうかのフラグ。
            existing_priority_timers (dict): 既存の優先タイマー情報。

        Returns:
            tuple: (normal_cache, backup_cache, priority_timers, folder_children_map) を返します。
        """
        normal_cache = {}
        backup_cache = {}
        priority_timers = {}
        folder_children_map = {}

        auto_scale_settings = app_config.get('auto_scale', {})
        use_window_scale_base = auto_scale_settings.get('use_window_scale', True)
        
        base_scales = [1.0]

        if use_window_scale_base:
            use_scale_search = auto_scale_settings.get('enabled', False) and effective_capture_scale == 1.0
            
            center_scale = current_window_scale if current_window_scale is not None else auto_scale_settings.get('center', 1.0)

            if use_scale_search:
                range_ = auto_scale_settings.get('range', 0.2)
                steps = auto_scale_settings.get('steps', 5)
                if steps > 1:
                    base_scales = np.linspace(center_scale - range_, center_scale + range_, steps)
                # ★★★ 2. ログを翻訳キーに置き換え ★★★
                self.logger.log("log_scale_search_enabled", len(base_scales), f"{center_scale:.3f}")
            else:
                base_scales = [center_scale]
        
        scales = [s * effective_capture_scale for s in base_scales]

        if effective_capture_scale != 1.0:
            self.logger.log("log_capture_scale_applied", f"{effective_capture_scale:.2f}")
        if use_window_scale_base and current_window_scale is not None:
            self.logger.log("log_window_scale_applied", f"{current_window_scale:.3f}")

        log_scales = ", ".join([f"{s:.3f}" for s in scales])
        self.logger.log("log_final_scales", log_scales)
        
        hierarchical_list = self.config_manager.get_hierarchical_list()
        
        for item_data in hierarchical_list:
            if item_data['type'] == 'folder':
                folder_path = item_data['path']
                folder_settings = item_data['settings']
                folder_mode = folder_settings.get('mode', 'normal')

                children_paths = {child['path'] for child in item_data.get('children', [])}
                folder_children_map[folder_path] = children_paths

                if folder_mode == 'priority_timer':
                    interval_seconds = folder_settings.get('priority_interval', 10) * 60
                    if not is_monitoring:
                         priority_timers[folder_path] = time.time() + interval_seconds
                    elif folder_path not in existing_priority_timers:
                         priority_timers[folder_path] = time.time() + interval_seconds
                    else:
                         priority_timers[folder_path] = existing_priority_timers[folder_path]
                    
                for child_data in item_data.get('children', []):
                    self._process_item_for_cache(child_data, scales, folder_path, folder_mode, normal_cache, backup_cache)

            elif item_data['type'] == 'image':
                self._process_item_for_cache(item_data, scales, None, 'normal', normal_cache, backup_cache)
        
        # ★★★ 3. ログを翻訳キーに置き換え ★★★
        self.logger.log("log_cache_build_complete", len(normal_cache), len(backup_cache))
        self.logger.log("log_priority_timers", len(priority_timers))

        return normal_cache, backup_cache, priority_timers, folder_children_map

    def _process_item_for_cache(self, item_data, scales, folder_path, folder_mode, normal_cache, backup_cache):
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
                # ★★★ 4. ログを翻訳キーに置き換え ★★★
                self.logger.log("log_warn_image_load_failed", Path(path).name)
                return

            image_to_process = original_image
            
            if settings.get('roi_enabled', False):
                h, w = original_image.shape[:2]
                
                roi_mode = settings.get('roi_mode', 'fixed')
                rect_to_use = None
                
                if roi_mode == 'variable':
                    rect_to_use = settings.get('roi_rect_variable')
                else:
                    rect_to_use = settings.get('roi_rect')
                
                if rect_to_use:
                    x1, y1, x2, y2 = max(0, rect_to_use[0]), max(0, rect_to_use[1]), min(w, rect_to_use[2]), min(h, rect_to_use[3])
                    if x1 < x2 and y1 < y2:
                        image_to_process = original_image[y1:y2, x1:x2]
                    else:
                        # ★★★ 5. ログを翻訳キーに置き換え ★★★
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
                            # ★★★ 修正: UMat変換時に失敗した場合の処理を追加 ★★★
                            template_entry['image_umat'] = cv2.UMat(resized_image)
                            template_entry['gray_umat'] = cv2.UMat(resized_gray)
                        except Exception as e:
                            # UMat生成に失敗した場合、そのテンプレートのエントリからUMatを削除し、numpyで続行できるようにする
                            if 'image_umat' in template_entry: del template_entry['image_umat']
                            if 'gray_umat' in template_entry: del template_entry['gray_umat']
                            # ★★★ 6. ログを翻訳キーに置き換え ★★★
                            self.logger.log("log_umat_convert_error", Path(path).name, str(e))


                    scaled_templates.append(template_entry)

            cache_entry = {
                'settings': settings, 'path': path, 'scaled_templates': scaled_templates,
                'best_scale': None if len(scales) > 1 else (scales[0] if scales else None),
                'folder_path': folder_path, 'folder_mode': folder_mode,
            }
            
            if settings.get('backup_click', False):
                backup_cache[path] = cache_entry
            else:
                normal_cache[path] = cache_entry

        except Exception as e:
            # ★★★ 7. ログを翻訳キーに置き換え ★★★
            self.logger.log("log_cache_create_failed", item_data.get('name'), str(e))
