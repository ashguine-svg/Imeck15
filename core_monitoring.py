# core_monitoring.py
# 監視ループ、マッチング、アクション実行を担当
# ★★★ 修正: OCR候補を全スキャンし、最も信頼度が高いものを選択するロジックに変更 ★★★

import time
import traceback
import cv2
import numpy as np
import psutil 
from pathlib import Path
import os

from matcher import _match_template_task, calculate_phash
from monitoring_states import IdleState, CountdownState

try:
    from ocr_runtime import OCRRuntimeEvaluator
    DEBUG_OCR_COORDS = os.environ.get("DEBUG_OCR_COORDS", "1") == "1"
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    DEBUG_OCR_COORDS = False

# ログ出力量制御トグル（配布ビルドで無効化可）
ENABLE_OCR_TRACE_LOG = os.environ.get("OCR_TRACE_LOG", "1") == "1"
ENABLE_OCR_SKIP_LOG = os.environ.get("OCR_SKIP_LOG", "1") == "1"

OPENCL_AVAILABLE = False
try:
    if cv2.ocl.haveOpenCL():
        OPENCL_AVAILABLE = True
except Exception:
    pass

class MonitoringProcessor:
    def __init__(self, core):
        self.core = core
        self.logger = core.logger
        self.thread_pool = core.thread_pool
        # OCR失敗後のクールダウン管理
        cooldown_env = os.environ.get("OCR_FAIL_COOLDOWN_SEC", "0.5")
        try:
            self.ocr_fail_cooldown_sec = max(0.0, float(cooldown_env))
        except Exception:
            self.ocr_fail_cooldown_sec = 0.5
        self.ocr_fail_cooldowns = {}

    def monitoring_loop(self):
        last_match_time_map = {}
        fps_last_time = time.time()
        frame_counter = 0
        last_state_check_time = time.time()

        # ★監視開始時にクリック時刻を初期化して、即座にタイムアウト判定されないようにする
        self.core.last_successful_click_time = time.time()

        while self.core.is_monitoring:
            if self.core._recovery_in_progress:
                time.sleep(0.5)
                continue

            with self.core.state_lock:
                current_state = self.core.state
            
            if not current_state:
                if not self.core.is_monitoring: break
                else: 
                    self.logger.log("[WARN] Monitoring active but state is None. Resetting to Idle.")
                    self.core.transition_to(IdleState(self.core))
                    continue

            try:
                current_time = time.time()

                if self.core._lifecycle_hook_active:
                    hooks_conf = self.core.app_config.get('extended_lifecycle_hooks', {})
                    check_interval = hooks_conf.get('state_check_interval', 5.0)
                    
                    if current_time - last_state_check_time > check_interval:
                        # 1. プロセス生存確認 (既存機能)
                        pid = self.core._session_context.get('pid')
                        if pid and not psutil.pid_exists(pid):
                            self.logger.log("[WARN] Session context lost (PID missing). Triggering lifecycle hook.")
                            self.core._execute_session_recovery()
                            continue
                        
                        # 2. 無操作(クリックなし)判定 (新機能)
                        timeout_mins = hooks_conf.get('inactivity_timeout_mins', 0)
                        if timeout_mins > 0:
                            elapsed_since_click = (current_time - self.core.last_successful_click_time) / 60.0
                            if elapsed_since_click >= timeout_mins:
                                self.logger.log(f"[WARN] No click detected for {elapsed_since_click:.1f} mins. Triggering recovery.")
                                # クリック時刻をリセットしてリカバリの重複実行を防ぐ
                                self.core.last_successful_click_time = current_time
                                self.core._execute_session_recovery()
                                continue

                        last_state_check_time = current_time

                should_process, fps_last_time, frame_counter = self._wait_for_next_frame(
                    current_time, current_state, fps_last_time, frame_counter
                )
                if not should_process:
                    continue

                screen_data, pre_matches = self._capture_and_process_image(current_state)
                if not screen_data:
                    continue

                if pre_matches is None:
                    if isinstance(current_state, (IdleState, CountdownState)):
                        pre_matches = self._find_matches_for_eco_check(screen_data, current_state)

                current_state.handle(current_time, screen_data, last_match_time_map, pre_matches=pre_matches)
           
            except Exception as e:
                if isinstance(e, AttributeError) and "'NoneType' object has no attribute 'handle'" in str(e):
                    self.logger.log("[CRITICAL] Race condition detected. Restarting loop.")
                else:
                    tb = traceback.format_exc()
                    self.logger.log(f"監視ループでエラーが発生しました: {e}\n{tb}")
                time.sleep(1.0)
            
            finally:
                self._update_statistics(time.time())
                time.sleep(0.01)

    def _wait_for_next_frame(self, current_time, current_state, fps_last_time, frame_counter):
        expired_cooldowns = [p for p, end_time in self.core.folder_cooldowns.items() if current_time >= end_time]
        for p in expired_cooldowns: del self.core.folder_cooldowns[p]
        
        if self.core._cooldown_until > current_time:
            time.sleep(min(self.core._cooldown_until - current_time, 0.1))
            return False, fps_last_time, frame_counter

        if self.core._is_reinitializing_display:
            self.logger.log("log_warn_display_reinitializing_monitor_loop")
            time.sleep(0.5)
            return False, fps_last_time, frame_counter
        
        frame_counter += 1
        delta_time = current_time - fps_last_time
        if delta_time >= 1.0:
            fps = frame_counter / delta_time
            self.core.fpsUpdated.emit(fps)
            self.core.current_fps = fps
            fps_last_time = current_time
            frame_counter = 0

        if isinstance(current_state, IdleState):
            self.core._check_and_activate_timer_priority_mode()

        is_eco_enabled = self.core.app_config.get('eco_mode', {}).get('enabled', True)
        is_eco_eligible = (is_eco_enabled and 
                           self.core.last_successful_click_time > 0 and 
                           isinstance(current_state, IdleState) and 
                           (current_time - self.core.last_successful_click_time > self.core.ECO_MODE_DELAY))
        
        self.core.is_eco_cooldown_active = is_eco_eligible

        if isinstance(current_state, CountdownState): time.sleep(1.0)
        elif self.core.is_eco_cooldown_active:
            self.core._log("log_eco_mode_standby")
            time_since_last_check = current_time - self.core._last_eco_check_time
            if time_since_last_check < self.core.ECO_CHECK_INTERVAL:
                time.sleep(self.core.ECO_CHECK_INTERVAL - time_since_last_check)
                return False, fps_last_time, frame_counter
            else:
                self.core._last_eco_check_time = current_time
        elif (frame_counter % self.core.effective_frame_skip_rate) != 0:
            time.sleep(0.01)
            return False, fps_last_time, frame_counter

        return True, fps_last_time, frame_counter

    def _capture_and_process_image(self, current_state):
        screen_bgr = self.core.capture_manager.capture_frame(region=self.core.recognition_area)
        if screen_bgr is None:
            self.core.consecutive_capture_failures += 1
            self.core._log("log_capture_failed")
            if self.core.consecutive_capture_failures >= 10:
                self.logger.log("log_capture_failed_limit_reached", force=True)
                self.core.updateStatus.emit("idle_error", "red")
                self.core.is_monitoring = False
            time.sleep(1.0)
            return None, None

        self.core.consecutive_capture_failures = 0
        self.core.latest_high_res_frame = screen_bgr.copy() 
        
        if self.core.effective_capture_scale != 1.0:
            screen_bgr = cv2.resize(screen_bgr, None, 
                                    fx=self.core.effective_capture_scale, 
                                    fy=self.core.effective_capture_scale, 
                                    interpolation=cv2.INTER_AREA)

        self.core.latest_frame_for_hash = screen_bgr.copy()
        screen_gray = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)

        screen_bgr_umat, screen_gray_umat = None, None
        if OPENCL_AVAILABLE and cv2.ocl.useOpenCL():
            try:
                screen_bgr_umat = cv2.UMat(screen_bgr)
                screen_gray_umat = cv2.UMat(screen_gray)
            except Exception as e:
                self.logger.log("log_umat_convert_failed", str(e))

        screen_data = (screen_bgr, screen_gray, screen_bgr_umat, screen_gray_umat)

        if self.core.is_eco_cooldown_active:
             all_matches = self._find_matches_for_eco_check(screen_data, current_state)
             if all_matches:
                 self.core.last_successful_click_time = time.time()
                 self.core._log("log_eco_mode_resumed", force=True)
                 return screen_data, all_matches
             else:
                 return None, None

        return screen_data, None

    def _find_matches_for_eco_check(self, screen_data, current_state):
        def filter_cache_for_eco(cache): return {p: d for p, d in cache.items() if d.get('folder_mode') not in ['excluded', 'priority_timer']}
        active_normal_cache = filter_cache_for_eco(self.core.normal_template_cache)
        normal_matches = self._find_best_match(*screen_data, active_normal_cache)
        if isinstance(current_state, IdleState):
            active_backup_cache = filter_cache_for_eco(self.core.backup_template_cache)
            backup_trigger_matches = self._find_best_match(*screen_data, active_backup_cache)
            if backup_trigger_matches: normal_matches.extend(backup_trigger_matches)
        return normal_matches

    def check_screen_stability(self) -> bool:
        if not hasattr(self.core, 'latest_frame_for_hash') or self.core.latest_frame_for_hash is None: return False
        h, w, _ = self.core.latest_frame_for_hash.shape
        if h < 64 or w < 64: self.core._log("log_stability_check_skip_size", force=True); return True
        roi = self.core.latest_frame_for_hash[0:64, 0:64]; current_hash = calculate_phash(roi)
        if current_hash is None: return False
        self.core.screen_stability_hashes.append(current_hash)
        if len(self.core.screen_stability_hashes) < self.core.screen_stability_hashes.maxlen: self.core._log("log_stability_check_history_low", len(self.core.screen_stability_hashes), self.core.screen_stability_hashes.maxlen, force=True); return False
        threshold = self.core.app_config.get('screen_stability_check', {}).get('threshold', 8); hash_diff = self.core.screen_stability_hashes[-1] - self.core.screen_stability_hashes[0]
        self.core._log("log_stability_check_debug", str(self.core.screen_stability_hashes[-1]), str(self.core.screen_stability_hashes[0]), hash_diff, threshold, force=True)
        return hash_diff <= threshold

    def _find_best_match(self, s_bgr, s_gray, s_bgr_umat, s_gray_umat, cache):
        matched_results = [] 
        futures = []
        current_time = time.time()

        with self.core.cache_lock:
            if not cache: return []
            use_cl = OPENCL_AVAILABLE and cv2.ocl.useOpenCL()
            use_gs = self.core.app_config.get('grayscale_matching', False)
            strict_color = self.core.app_config.get('strict_color_matching', False)
            effective_strict_color = strict_color and not use_gs
            if effective_strict_color: use_cl = False

            screen_image = s_gray if use_gs else s_bgr
            if use_cl:
                screen_umat = s_gray_umat if use_gs else s_bgr_umat
                screen_image = screen_umat if screen_umat is not None else screen_image

            s_shape = screen_image.get().shape[:2] if use_cl and isinstance(screen_image, cv2.UMat) else screen_image.shape[:2]

            for path, data in cache.items():
                folder_path = data.get('folder_path')
                if folder_path and folder_path in self.core.folder_cooldowns:
                    if current_time < self.core.folder_cooldowns[folder_path]: continue
                
                templates_to_check = data['scaled_templates']
                num_templates = len(templates_to_check)
                if num_templates == 0: continue

                user_threshold = data['settings'].get('threshold', 0.8)
                last_idx = data.get('last_success_index', -1)
                indices = list(range(num_templates))
                if 0 <= last_idx < num_templates: indices.insert(0, indices.pop(last_idx))

                for i in indices:
                    t = templates_to_check[i]
                    try:
                        template_image = t['gray'] if use_gs else t['image']
                        if use_cl:
                            t_umat = t.get('gray_umat' if use_gs else 'image_umat')
                            template_image = t_umat if t_umat else template_image

                        task_data = {'path': path, 'settings': data['settings'], 'template': template_image, 'scale': t['scale']}
                        t_shape = t['shape']

                        if self.core.thread_pool and not use_cl:
                            future = self.core.thread_pool.submit(_match_template_task, screen_image, template_image, task_data, s_shape, t_shape, effective_strict_color)
                            futures.append((future, i, data)) 
                        else:
                            match_result = _match_template_task(screen_image, template_image, task_data, s_shape, t_shape, effective_strict_color)
                            if match_result and match_result['confidence'] >= user_threshold:
                                data['last_success_index'] = i
                                matched_results.append((match_result, i, data))
                                break 
                    except Exception as e:
                         self.logger.log("Error during template processing for %s: %s", Path(path).name, str(e))

        if futures:
            for f, idx, data_ref in futures:
                try:
                    match_result = f.result()
                    if match_result:
                        th = data_ref['settings'].get('threshold', 0.8)
                        if match_result['confidence'] >= th:
                             data_ref['last_success_index'] = idx
                             matched_results.append((match_result, idx, data_ref))
                except Exception: pass

        if not matched_results: return []
        matched_results.sort(key=lambda x: x[0]['confidence'], reverse=True)
        return [item[0] for item in matched_results]

    def _start_ocr_task_if_needed(self, path, match, current_time):
        settings = match.get('settings', {})
        ocr_settings = settings.get('ocr_settings')
        
        if not OCR_AVAILABLE or not ocr_settings or not ocr_settings.get('enabled', False):
            return
        
        # 既にOCRタスクが実行中ならスキップ
        if path in self.core.ocr_futures:
            return
        
        screen_img = getattr(self.core, 'latest_high_res_frame', None)
        if screen_img is None:
            return
        
        match_rect = match['rect'] 
        detected_scale = match.get('scale', 1.0)
        capture_scale = self.core.effective_capture_scale
        
        # ★★★ 旧仕様に戻す: ウィンドウスケール補正を親座標計算に入れず、検出スケールでのみ扱う ★★★
        # detected_scale = base_window_scale * multiplier * effective_capture_scale
        # capture_scale = effective_capture_scale
        # real_scale = detected_scale / capture_scale = base_window_scale * multiplier
        real_scale = detected_scale / capture_scale if capture_scale else 1.0
        
        # parent_pos は軽量化のみを元に戻す（ウィンドウスケールはここでは戻さない）
        parent_x = int(match_rect[0] / capture_scale)
        parent_y = int(round(match_rect[1] / capture_scale))
        
        # ★★★ 原因特定のため、詳細なデバッグ情報を追加 ★★★
        capture_method = getattr(self.core.capture_manager, 'current_method', 'mss')
        rec_area = self.core.recognition_area
        screen_img_h, screen_img_w = screen_img.shape[:2] if screen_img is not None else (0, 0)
        
        if DEBUG_OCR_COORDS:
            try:
                # recognition_areaの期待サイズと実際の画像サイズを比較
                if rec_area:
                    expected_w = rec_area[2] - rec_area[0]
                    expected_h = rec_area[3] - rec_area[1]
                    size_match = (expected_w == screen_img_w and expected_h == screen_img_h)
                else:
                    expected_w = expected_h = 0
                    size_match = False
                
                # ★★★ 原因特定: 座標計算の詳細を出力（端数切り捨てによる誤差を確認） ★★★
                # ★★★ 修正: 実際のウィンドウスケールを考慮してparent_posを計算 ★★★
                parent_x_raw_old = match_rect[0] / capture_scale
                parent_y_raw_old = match_rect[1] / capture_scale
                parent_x_raw = match_rect[0] / capture_scale
                parent_y_raw = match_rect[1] / capture_scale
                parent_x_int = int(parent_x_raw)
                parent_y_int = int(parent_y_raw)
                parent_x_round = int(round(parent_x_raw))
                parent_y_round = int(round(parent_y_raw))
                
                # ★★★ 追加: ウィンドウスケールの情報を取得 ★★★
                window_scale = getattr(self.core, 'current_window_scale', None)
                actual_window_scale = getattr(self.core, 'actual_window_scale', None)

                # ★★★ 追加: match_rectの座標系を確認 ★★★
                # match_rectはリサイズ後の画像（effective_capture_scaleでリサイズ）内での座標
                # screen_imgはリサイズ前の画像（latest_high_res_frame）
                # ★★★ 修正: parent_pos = match_rect / detected_scale でリサイズ前の画像内での座標に変換（ウィンドウスケールの補正を考慮） ★★★
                
                print(
                    f"[OCR DEBUG CAPTURE] method={capture_method} "
                    f"recognition_area={rec_area} "
                    f"expected_size={expected_w}x{expected_h} "
                    f"captured_img={screen_img_w}x{screen_img_h} "
                    f"size_match={size_match} "
                    f"match_rect={match_rect} "
                    f"match_rect_coords=({match_rect[0]},{match_rect[1]},{match_rect[2]},{match_rect[3]}) "
                    f"detected_scale={detected_scale:.3f} "
                    f"capture_scale={capture_scale:.3f} "
                    f"real_scale={real_scale:.3f} "
                    f"window_scale={window_scale} "
                    f"actual_window_scale={actual_window_scale} "
                    f"parent_pos=({parent_x},{parent_y}) "
                    f"parent_calc_raw_old=({parent_x_raw_old:.3f},{parent_y_raw_old:.3f}) "
                    f"parent_calc_raw=({parent_x_raw:.3f},{parent_y_raw:.3f}) "
                    f"parent_calc_int=({parent_x_int},{parent_y_int}) "
                    f"parent_calc_round=({parent_x_round},{parent_y_round}) "
                    f"parent_calc_formula_old=match_rect[0]/{capture_scale:.3f}={parent_x_raw_old:.3f} "
                    f"parent_calc_formula=match_rect[0]/{capture_scale:.3f}={parent_x_raw:.3f}"
                )
            except Exception:
                pass
        
        parent_pos = (parent_x, parent_y)
        # real_scaleは既に計算済み（上記で計算）
        
        # OCRタスク開始時刻を記録（処理時間計測用）
        self.core.ocr_start_times[path] = current_time
        
        # OCRタスクを非同期実行
        # DXCamとMSSで座標系が異なる可能性があるため、キャプチャ方法を渡す
        capture_method = getattr(self.core.capture_manager, 'current_method', 'mss')
        # ★★★ 追加: capture_scaleを渡してroi_offsetの座標系変換に使用 ★★★
        future = self.thread_pool.submit(
            OCRRuntimeEvaluator.evaluate,
            screen_image=screen_img,
            parent_pos=parent_pos,
            ocr_settings=ocr_settings,
            item_settings=settings,
            current_scale=real_scale,
            capture_scale=capture_scale,  # ★★★ 追加: roi_offsetの座標系変換に使用 ★★★
            hwnd=self.core.target_hwnd,
            capture_method=capture_method
        )
        self.core.ocr_futures[path] = future

    def process_matches_as_sequence(self, all_matches, current_time, last_match_time_map):
        """
        全候補をインターバル時間に基づいて評価し、実行可能なものがあればクリックします。
        戻り値: クリックした画像の情報(dict) または なければ None
        """
        if not all_matches:
            current_match_paths = set()
            keys_to_remove = [path for path in self.core.match_detected_at if path not in current_match_paths]
            for path in keys_to_remove: del self.core.match_detected_at[path]
            return None

        clickable_after_interval = []
        current_match_paths = {m['path'] for m in all_matches}

        for m in all_matches:
            path = m['path']

            # OCRがOFFの時に「クリックしない」設定がONなら、誤クリック防止のため常にスキップ
            settings = m.get('settings', {}) or {}
            ocr_settings = settings.get('ocr_settings') or {}
            if isinstance(ocr_settings, dict):
                if (not ocr_settings.get('enabled', False)) and ocr_settings.get('no_click_when_disabled', False):
                    # 監視が止まらないよう、検出タイマーも残さない（常に無視）
                    if path in self.core.match_detected_at:
                        del self.core.match_detected_at[path]
                    continue

            # OCR失敗クールダウンチェック
            cooldown_until = self.ocr_fail_cooldowns.get(path)
            if cooldown_until and current_time < cooldown_until:
                continue

            cache_item = self.core.normal_template_cache.get(path) or self.core.backup_template_cache.get(path)
            if cache_item:
                folder_path = cache_item.get('folder_path')
                if folder_path and folder_path in self.core.folder_cooldowns:
                    if path in self.core.match_detected_at: del self.core.match_detected_at[path]
                    continue

            settings = m['settings']
            interval = settings.get('interval_time', 1.5)
            debounce = settings.get('debounce_time', 0.0)
            last_clicked = last_match_time_map.get(path, 0)

            effective_debounce = debounce if self.core._last_clicked_path == path else 0.0

            if current_time - last_clicked <= effective_debounce:
                if path in self.core.match_detected_at: del self.core.match_detected_at[path]
                continue

            if path not in self.core.match_detected_at:
                self.core.match_detected_at[path] = current_time
                self.logger.log(f"[DEBUG] Detected '{Path(path).name}'. Interval timer started ({interval:.1f}s).")
                self._start_ocr_task_if_needed(path, m, current_time)
                continue
            else:
                detected_at = self.core.match_detected_at[path]
                elapsed = current_time - detected_at
                
                # インターバル待機中にOCR結果をチェック
                ocr_settings = settings.get('ocr_settings')
                if OCR_AVAILABLE and ocr_settings and ocr_settings.get('enabled', False):
                    if path in self.core.ocr_futures:
                        future = self.core.ocr_futures[path]
                        if future.done():
                            try:
                                elapsed_time = current_time - self.core.ocr_start_times.get(path, current_time)
                                time_str = f"[{elapsed_time:.2f}s]"
                                success, log_msg, raw_text, confidence = future.result()
                                full_log_msg = f"{log_msg} {time_str}"
                                self.core.ocr_results[path] = {
                                    'success': success,
                                    'log_msg': full_log_msg,
                                    'raw_text': raw_text,
                                    'confidence': confidence
                                }
                                if success:
                                    self.logger.log(f"[OCR] {full_log_msg} (Completed during interval wait)")
                                else:
                                    self.logger.log(f"[OCR SKIP] {full_log_msg} (Completed during interval wait)")
                                del self.core.ocr_futures[path]
                                if path in self.core.ocr_start_times: del self.core.ocr_start_times[path]
                            except Exception as e:
                                self.logger.log(f"[OCR ERROR] {e}")
                                del self.core.ocr_futures[path]
                                if path in self.core.ocr_start_times: del self.core.ocr_start_times[path]
                
                if elapsed >= interval:
                    clickable_after_interval.append(m)

        keys_to_remove = [p for p in self.core.match_detected_at if p not in current_match_paths]
        for p in keys_to_remove:
            self.core.match_detected_at.pop(p, None)
            self.core.ocr_futures.pop(p, None)
            self.core.ocr_results.pop(p, None)
            self.core.ocr_start_times.pop(p, None)

        if not clickable_after_interval:
            return None

        # ここでインターバル時間が短い方を優先的にソートします
        sorted_candidates = sorted(
            clickable_after_interval, 
            key=lambda m: (m['settings'].get('interval_time', 1.5), -m['confidence'])
        )

        # 優先順位（インターバルの短いもの）を順に評価。
        # 最優先がOCR待ちならここで止め、後続はクリックしない。
        ready_candidates = []
        for target_match in sorted_candidates:
            path = target_match['path']
            settings = target_match.get('settings', {})
            ocr_settings = settings.get('ocr_settings')
            # NOTE: `and` チェーンだと None / {} がそのまま返り、ログが `ocr_required=None` 等になって誤解を招く。
            # ここは「OCRが必須かどうか」を明確に bool で扱う。
            ocr_required = bool(OCR_AVAILABLE and isinstance(ocr_settings, dict) and ocr_settings.get('enabled', False))

            # デバッグ: OCRに関与する場合のみ状態を出力（トグルで制御）
            # - OCR不要の画像まで `[OCR TRACE]` が出ると誤解を招くため、OCR必須/結果待ち/結果ありの場合に限定する
            if ENABLE_OCR_TRACE_LOG and (
                ocr_required
                or (path in self.core.ocr_results)
                or (path in self.core.ocr_futures)
            ):
                try:
                    has_result = path in self.core.ocr_results
                    has_future = path in self.core.ocr_futures
                    future_done = has_future and self.core.ocr_futures[path].done()
                    self.logger.log(
                        f"[OCR TRACE] eval path={Path(path).name} "
                        f"interval={settings.get('interval_time', 1.5):.2f} "
                        f"conf={target_match.get('confidence', 0):.2f} "
                        f"ocr_required={ocr_required} "
                        f"has_result={has_result} has_future={has_future} future_done={future_done}"
                    )
                except Exception:
                    pass

            # OCR不要（無効）の場合は、インターバル優先の順序に従って候補として積む
            if not ocr_required:
                target_match['ocr_success'] = True  # OCR不要扱いで通過
                ready_candidates.append(target_match)
                continue

            # OCR結果がキャッシュにある場合
            ocr_result = self.core.ocr_results.pop(path, None)
            if ocr_result is not None:
                success = ocr_result.get('success', False)
                if success:
                    # 通常ログを出力（読取結果・一致/不一致・信頼度・処理時間）
                    if "[0." not in ocr_result.get('log_msg', ''):
                        self.logger.log(f"[OCR] {ocr_result.get('log_msg', '')}")
                    target_match['ocr_success'] = True  # 成功フラグを付与
                    ready_candidates.append(target_match)
                    continue
                else:
                    # 失敗なら検出状態をリセットし、次の候補を検討
                    if ENABLE_OCR_SKIP_LOG:
                        self.logger.log(f"[OCR SKIP] {ocr_result.get('log_msg', '(cached result)')}")
                    self.core.match_detected_at.pop(path, None)
                    if self.ocr_fail_cooldown_sec > 0:
                        self.ocr_fail_cooldowns[path] = current_time + self.ocr_fail_cooldown_sec
                    continue

            # OCR実行中のFutureがある場合
            if path in self.core.ocr_futures:
                future = self.core.ocr_futures[path]
                if future.done():
                    try:
                        elapsed_time = current_time - self.core.ocr_start_times.get(path, current_time)
                        time_str = f"[{elapsed_time:.2f}s]"
                        success, log_msg, raw_text, confidence = future.result()
                        if success:
                            # 通常ログを出力（読取結果・一致/不一致・信頼度・処理時間）
                            if "[0." not in log_msg:
                                self.logger.log(f"[OCR] {log_msg} {time_str}")
                            target_match['ocr_success'] = True  # 成功フラグを付与
                            self.core.ocr_futures.pop(path, None)
                            self.core.ocr_start_times.pop(path, None)
                            ready_candidates.append(target_match)
                            continue
                        else:
                            # 失敗なら検出状態をリセットし、次の候補へ
                            if ENABLE_OCR_SKIP_LOG:
                                self.logger.log(f"[OCR SKIP] {log_msg} {time_str} path={Path(path).name}")
                            self.core.match_detected_at.pop(path, None)
                            if self.ocr_fail_cooldown_sec > 0:
                                self.ocr_fail_cooldowns[path] = current_time + self.ocr_fail_cooldown_sec
                    except Exception as e:
                        self.logger.log(f"[OCR ERROR] {e}")
                    finally:
                        self.core.ocr_futures.pop(path, None)
                        self.core.ocr_start_times.pop(path, None)
                        self.core.ocr_results.pop(path, None)
                else:
                    # 最優先候補がOCR待ちなら、後続を処理せず終了（優先度維持）
                    self.logger.log(f"[OCR WAIT] path={Path(path).name}")
                    return None

            # ここまでで結果がない場合は、次の候補を検討
            continue

        # 準備完了した候補があれば、最優先（すでにソート済み）をクリック
        if ready_candidates:
            best = ready_candidates[0]
            self._execute_final_action(best, current_time, last_match_time_map)
            return best

        return None

        return False

    def _execute_final_action(self, target_match, current_time, last_match_time_map):
        # 画面安定性チェック
        is_stability_check_enabled = self.core.app_config.get('screen_stability_check', {}).get('enabled', True)
        if is_stability_check_enabled and not self.core.is_eco_cooldown_active:
            if not self.check_screen_stability():
                self.core._log("log_stability_hold_click")
                self.core.updateStatus.emit("unstable", "orange")
                self.core.last_successful_click_time = current_time
                return 

        if not self.core.is_eco_cooldown_active:
            self.core.updateStatus.emit("monitoring", "blue")

        if not self.core.is_monitoring: return

        # OCR必須の場合、成功フラグがない候補はクリックしない（安全ガード）
        try:
            settings = target_match.get('settings', {})
            ocr_settings = settings.get('ocr_settings')
            ocr_required = bool(OCR_AVAILABLE and isinstance(ocr_settings, dict) and ocr_settings.get('enabled', False))
            if ocr_required and not target_match.get('ocr_success', False):
                self.logger.log(f"[OCR GUARD SKIP] path={Path(target_match.get('path',''))} required={ocr_required} success_flag={target_match.get('ocr_success', False)}")
                return
            # OCRがOFFで「クリックしない」設定がONなら、絶対にクリックしない（安全ガード）
            if isinstance(ocr_settings, dict):
                if (not ocr_settings.get('enabled', False)) and ocr_settings.get('no_click_when_disabled', False):
                    return
        except Exception:
            pass

        target_path = target_match['path']
        self.execute_click(target_match)
        click_time = time.time()
        last_match_time_map[target_path] = click_time
        if target_path in self.core.match_detected_at: del self.core.match_detected_at[target_path]
        if target_path in self.core.ocr_futures: del self.core.ocr_futures[target_path]
        if target_path in self.core.ocr_results: del self.core.ocr_results[target_path]
        if target_path in self.core.ocr_start_times: del self.core.ocr_start_times[target_path]

    def execute_click(self, match_info):
        try:
            item_path_str = match_info['path']
            self.core.environment_tracker.track_environment_on_click(item_path_str)
        except Exception as e:
            self.logger.log(f"[ERROR] Failed during environment tracking pre-click: {e}")

        result = self.core.action_manager.execute_click(
            match_info, 
            self.core.recognition_area, 
            self.core.target_hwnd, 
            self.core.effective_capture_scale,
            self.core.current_window_scale
        )
        
        if result and result.get('success'): 
            if self.core._lifecycle_hook_active:
                current_clicked_path = result.get('path')
                if current_clicked_path == self.core._last_clicked_path:
                    self.core._session_context['consecutive_clicks'] += 1
                else:
                    self.core._session_context['consecutive_clicks'] = 1
                
                hooks_conf = self.core.app_config.get('extended_lifecycle_hooks', {})
                limit = hooks_conf.get('retry_tolerance', 10)
                
                if self.core._session_context['consecutive_clicks'] >= limit:
                    self.logger.log("[WARN] Response timeout detected. Triggering lifecycle hook.")
                    self.core._execute_session_recovery()
                    return 
            
            self.core._click_count += 1
            self.core._last_clicked_path = result.get('path')
            self.core.last_successful_click_time = time.time()
            self.core.clickCountUpdated.emit(self.core._click_count)
            
            path = match_info['path']
            cache_item = self.core.normal_template_cache.get(path) or self.core.backup_template_cache.get(path)
            if cache_item:
                folder_mode = cache_item.get('folder_mode')
                if folder_mode == 'cooldown':
                    folder_path = cache_item.get('folder_path')
                    cooldown_duration = cache_item.get('cooldown_time', 30)
                    self.core.folder_cooldowns[folder_path] = time.time() + cooldown_duration
                    self.logger.log("log_folder_cooldown_started", Path(folder_path).name, str(cooldown_duration))
                    
                    keys_to_remove = []
                    for cache in [self.core.normal_template_cache, self.core.backup_template_cache]:
                        for cached_path, item in cache.items():
                            if item.get('folder_path') == folder_path and cached_path in self.core.match_detected_at:
                                keys_to_remove.append(cached_path)
                    
                    for p in keys_to_remove:
                        if p in self.core.match_detected_at: del self.core.match_detected_at[p]

    def _update_statistics(self, current_time):
        if current_time - self.core.last_stats_emit_time >= 1.0:
            self.core.last_stats_emit_time = current_time
            uptime_seconds = int(current_time - self.core.start_time)
            h = uptime_seconds // 3600
            m = (uptime_seconds % 3600) // 60
            s = uptime_seconds % 60
            uptime_str = f"{h:02d}h{m:02d}m{s:02d}s"
            
            timer_data = {'backup': self.core.get_backup_click_countdown(), 'priority': -1.0}
            if self.core.priority_timers:
                active_timer_path = next(iter(self.core.priority_timers), None)
                if active_timer_path:
                    remaining_sec = self.core.priority_timers[active_timer_path] - current_time
                    timer_data['priority'] = max(0, remaining_sec / 60.0)
            
            cpu_percent = 0.0
            if self.core.process:
                try:
                    raw_cpu = self.core.process.cpu_percent(interval=None)
                    num_cores = psutil.cpu_count() or 1
                    cpu_percent = raw_cpu / num_cores
                except Exception: cpu_percent = 0.0
            
            self.core.statsUpdated.emit(self.core._click_count, uptime_str, timer_data, cpu_percent, self.core.current_fps)
