# monitoring_states.py
# 状態遷移ロジック
# ★★★ 修正: 通常クリック時にOCRや待機タイマーがスキップされる問題を修正 (_execute_click -> _process_matches_as_sequence) ★★★

import time
from pathlib import Path
import copy

class State:
    def __init__(self, context, parent_state=None):
        self.context = context
        self.parent_state = parent_state

    def handle(self, current_time, screen_data, last_match_time_map, pre_matches=None):
        raise NotImplementedError
    
    def get_name(self):
        return self.__class__.__name__

    def _return_to_parent_or_idle(self):
        if self.parent_state:
            self.parent_state.on_child_finished()
            self.context.transition_to(self.parent_state)
        else:
            self.context.transition_to(IdleState(self.context))
            
    def on_child_finished(self):
        pass

class IdleState(State):
    def handle(self, current_time, screen_data, last_match_time_map, pre_matches=None):
        context = self.context

        # 0. クイックタイマー
        qt_mgr = getattr(context, "_quick_timer_manager", None)
        if qt_mgr and qt_mgr.has_any():
            import cv2
            for e in qt_mgr.entries_sorted():
                trigger_time = float(e.get("trigger_time", 0))
                match_start = float(e.get("match_start_time", trigger_time - 60.0))
                if current_time < match_start:
                    continue
                if qt_mgr.remove_if_expired(e, current_time, grace_seconds=5.0):
                    continue

                template_gray = e.get("template_gray", None)
                if template_gray is None:
                    continue

                frame = getattr(context, "latest_high_res_frame", None)
                if frame is None or frame.size == 0:
                    continue

                screen_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                res = cv2.matchTemplate(screen_gray, template_gray, cv2.TM_CCOEFF_NORMED)
                _minv, maxv, _minl, maxl = cv2.minMaxLoc(res)
                if maxv >= 0.85:
                    context.transition_to(QuickTimerStandbyState(context, e, maxl))
                    return
                break
        
        # 1. タイマーアプローチ (スケジュール実行)
        if context.timer_schedule_cache:
            for path, schedule in context.timer_schedule_cache.items():
                actions = schedule['actions']
                pending_actions = [a for a in actions if not a['executed']]
                if not pending_actions: continue
                
                next_action = pending_actions[0]
                time_until_trigger = next_action['target_time'] - current_time
                
                if time_until_trigger < -1.0:
                    context.logger.log(f"[WARN] Timer action expired for {Path(path).name}. Skipping.")
                    next_action['executed'] = True
                    continue
                
                if time_until_trigger <= schedule['approach_time']:
                    target_match = None
                    if pre_matches:
                        for m in pre_matches:
                            if m['path'] == path:
                                target_match = m
                                break
                    if not target_match:
                        cache_item = context.normal_template_cache.get(path) or context.backup_template_cache.get(path)
                        if cache_item:
                            matches = context._find_best_match(*screen_data, {path: cache_item})
                            if matches: target_match = matches[0]
                    
                    if target_match:
                        new_state = TimerStandbyState(context, path, schedule)
                        context.transition_to(new_state)
                        return
        
        # 2. 候補の選定（タイマーロックされていないもの）
        all_matches = pre_matches if pre_matches is not None else []
        candidates = []
        for m in all_matches:
            is_locked_by_timer = False
            if m['path'] in context.timer_schedule_cache:
                schedule = context.timer_schedule_cache[m['path']]
                pending = [a for a in schedule['actions'] if not a['executed']]
                if pending:
                    next_action = pending[0]
                    lock_start_time = next_action['target_time'] - schedule['approach_time']
                    if current_time >= lock_start_time: is_locked_by_timer = True
            if not is_locked_by_timer:
                candidates.append(m)
        
        normal_matches = [m for m in candidates if m['path'] in context.normal_template_cache]
        backup_trigger_matches = [m for m in candidates if m['path'] in context.backup_template_cache]
        
        # 信頼度順にソート (公平な競争の準備)
        normal_matches.sort(key=lambda x: x['confidence'], reverse=True)

        # ★★★ 統合ループ ★★★
        for match in normal_matches:
            path_str = match['path']
            cache_item = context.normal_template_cache.get(path_str)
            if not cache_item: continue
            
            # クールダウンチェック (前回クリックからの経過時間)
            settings = cache_item.get('settings', {})
            interval = float(settings.get('interval', 10.0))
            last_click = last_match_time_map.get(path_str, 0)
            
            if current_time - last_click < interval:
                continue

            # ここに来た＝「最も優先度が高く、実行可能な候補」
            folder_mode = cache_item.get('folder_mode')
            
            # A. 順序優先 (Priority Sequence) -> 遷移
            if folder_mode == 'priority_sequence':
                seq_info = cache_item.get('sequence_info')
                if seq_info:
                    ordered_paths = seq_info.get('ordered_paths', [])
                    if ordered_paths and ordered_paths[0] == path_str:
                        step_interval = seq_info.get('interval', 3)
                        new_state = SequencePriorityState(context, ordered_paths, step_interval, start_index=0)
                        context.transition_to(new_state)
                        return
            
            # B. 画像認識型優先 / タイマー優先 (Priority Image/Timer) -> 遷移
            elif folder_mode == 'priority_image' or folder_mode == 'priority_timer':
                trigger_path = cache_item.get('priority_trigger_path')
                target_path = trigger_path if trigger_path else cache_item.get('folder_path')
                
                if target_path in context.priority_timers:
                    if current_time < context.priority_timers[target_path]:
                        continue # クールダウン中
                    else:
                        del context.priority_timers[target_path]

                if target_path:
                    folder_settings = context.config_manager.load_item_setting(Path(target_path))
                    timeout_sec = float(folder_settings.get('priority_image_timeout', 10.0))
                    required_children = context.folder_children_map.get(target_path, set())
                    
                    mode_type = 'timer' if folder_settings.get('priority_mode') == 'timer' else 'image'
                    
                    new_state = PriorityState(context, mode_type, target_path, timeout_sec, required_children)
                    context.transition_to(new_state)
                    return

            # C. 通常クリック -> OCR/待機処理を含む実行プロセスへ
            else:
                # ★修正: 直接 _execute_click を呼ばず、プロセッサに委譲してOCRや待機を行わせる
                # マッチした1件だけをリストにして渡すことで、この「最優先の通常画像」のみを処理対象にする
                context._process_matches_as_sequence([match], current_time, last_match_time_map)
                return

        # 3. バックアップトリガー
        if backup_trigger_matches:
            best_backup_trigger = max(backup_trigger_matches, key=lambda m: m['confidence'])
            new_state = CountdownState(context, best_backup_trigger)
            context.transition_to(new_state)
            return

class PriorityState(State):
    def __init__(self, context, mode_type, folder_path, timeout_duration_sec, required_children=None, parent_state=None):
        super().__init__(context, parent_state)
        self.mode_type = mode_type
        self.folder_path = folder_path
        
        folder_settings = context.config_manager.load_item_setting(Path(folder_path))
        self.timeout_duration = float(folder_settings.get('priority_image_timeout', timeout_duration_sec))
        if self.timeout_duration <= 0.1: self.timeout_duration = 10.0
        
        self.folder_sequence_interval = float(folder_settings.get('sequence_interval', 1.0))
        self.cooldown_time = float(folder_settings.get('cooldown_time', 0.0))
            
        self.last_detection_time = None
        self.last_action_time = time.time()
        self.last_diag_time = 0.0
        
        self.busy_until = 0.0
        
        self.required_children_names = set()
        if required_children:
            for p in required_children:
                self.required_children_names.add(Path(p).name)
                
        self.clicked_children_names = set()
        
        self.target_folder_name = Path(folder_path).name
        
        self.completion_trigger_name = None
        # タイマーモードでは「最後の画像をクリックしたら終わり」というトリガーを使わない
        if self.mode_type != 'timer':
            try:
                order_names = self.context.config_manager.load_image_order(Path(self.folder_path))
                if order_names:
                    self.completion_trigger_name = order_names[-1]
            except Exception:
                pass
        
        self.priority_normal_cache = {}
        self.priority_backup_cache = {}
        self._build_priority_cache()

        if mode_type == 'timer':
            remain = timeout_duration_sec - time.time()
            if remain < 0: remain = timeout_duration_sec 
            timeout_min = remain / 60
            self.context.logger.log("log_priority_timer_started", self.target_folder_name, f"{timeout_min:.1f}")
        else:
            self.context.logger.log("log_priority_image_started", self.target_folder_name)
            if self.completion_trigger_name:
                print(f"[DEBUG] Priority Completion Trigger: '{self.completion_trigger_name}'")

    def on_child_finished(self):
        current = time.time()
        self.busy_until = current + self.folder_sequence_interval
        self.last_action_time = current
        self.last_detection_time = current

    def _build_priority_cache(self):
        def filter_cache(source_cache, dest_cache):
            count = 0
            for p, d in source_cache.items():
                is_match = False
                f_path = d.get('folder_path')
                # パス一致または名前一致（コンテキストスタックで登録されたパスを信頼）
                if f_path and Path(f_path) == Path(self.folder_path):
                    is_match = True
                elif Path(p).parent == Path(self.folder_path):
                    is_match = True
                
                if is_match:
                    dest_cache[p] = d
                    count += 1
            return count

        c1 = filter_cache(self.context.normal_template_cache, self.priority_normal_cache)
        c2 = filter_cache(self.context.backup_template_cache, self.priority_backup_cache)

    def _get_item_interval(self, path_str):
        entry = self.priority_normal_cache.get(path_str) or self.priority_backup_cache.get(path_str)
        if entry and 'settings' in entry:
            return float(entry['settings'].get('interval_time', 0.0))
        return 0.0

    def _apply_cooldown_and_exit(self):
        if self.cooldown_time > 0:
            self.context.priority_timers[self.folder_path] = time.time() + self.cooldown_time
        
        self.context.logger.log("log_priority_image_completed", Path(self.folder_path).name)
        self._return_to_parent_or_idle()

    def handle(self, current_time, screen_data, last_match_time_map, pre_matches=None):
        if self.last_detection_time is None:
            self.last_detection_time = current_time

        if current_time < self.busy_until:
            self.last_detection_time = current_time
            self.last_action_time = current_time
            return

        all_matches = self.context._find_best_match(*screen_data, self.priority_normal_cache)
        all_matches.extend(self.context._find_best_match(*screen_data, self.priority_backup_cache))
        
        if all_matches:
            all_matches.sort(key=lambda x: (self._get_item_interval(x['path']), -x['confidence']))
            self.last_detection_time = current_time
        
        # タイムアウト判定
        elapsed_since_action = current_time - self.last_action_time
        if elapsed_since_action > 2.0:
            elapsed_since_last_seen = current_time - self.last_detection_time
            
            if self.mode_type == 'timer':
                if current_time > self.timeout_duration:
                    self.context.logger.log("log_priority_timeout", Path(self.folder_path).name)
                    self._return_to_parent_or_idle()
                    return
            elif self.mode_type == 'image':
                if elapsed_since_last_seen > self.timeout_duration:
                    self.context.logger.log("log_priority_timeout", Path(self.folder_path).name)
                    self._return_to_parent_or_idle()
                    return
        else:
            self.last_detection_time = current_time
        
        # サブフォルダへの遷移
        if all_matches:
            for match in all_matches:
                path = match['path']
                cache_item = self.context.normal_template_cache.get(path) or self.context.backup_template_cache.get(path)
                if cache_item:
                    trigger_path = cache_item.get('priority_trigger_path')
                    # 自分自身以外のトリガーがあれば遷移
                    if trigger_path and Path(trigger_path) != Path(self.folder_path):
                         if trigger_path in self.context.priority_timers:
                             if current_time < self.context.priority_timers[trigger_path]:
                                 continue
                             else:
                                 del self.context.priority_timers[trigger_path]

                         folder_settings = self.context.config_manager.load_item_setting(Path(trigger_path))
                         timeout_sec = float(folder_settings.get('priority_image_timeout', 10.0))
                         mode = 'timer' if folder_settings.get('priority_mode') == 'timer' else 'image'
                         
                         new_state = PriorityState(self.context, mode, trigger_path, timeout_sec, required_children=None, parent_state=self)
                         self.context.transition_to(new_state)
                         return

            folder_order_map = None
            if self.mode_type == 'image':
                order_names = self.context.config_manager.load_image_order(Path(self.folder_path))
                if order_names:
                    folder_order_map = {}
                    for idx, name in enumerate(order_names):
                        full_path = str(Path(self.folder_path) / name)
                        folder_order_map[full_path] = idx

            clicked_in_sequence = self.context._process_matches_as_sequence(all_matches, current_time, last_match_time_map, folder_order_map=folder_order_map)
            
            if clicked_in_sequence:
                self.last_detection_time = current_time
                self.last_action_time = current_time
                
                clicked_path = self.context._last_clicked_path
                clicked_name = Path(clicked_path).name if clicked_path else ""
                
                is_completed = False
                
                if self.mode_type != 'timer':
                    if self.completion_trigger_name and clicked_name == self.completion_trigger_name:
                        is_completed = True
                    
                    if not is_completed and clicked_name:
                        self.clicked_children_names.add(clicked_name)
                        if self.required_children_names and self.clicked_children_names.issuperset(self.required_children_names):
                            is_completed = True

                if is_completed:
                    self._apply_cooldown_and_exit()
                    return

                wait_time = 1.0
                if clicked_path:
                    wait_time = self._get_item_interval(clicked_path)
                self.busy_until = current_time + wait_time

                if self.mode_type == 'timer':
                    folder_settings = self.context.config_manager.load_item_setting(Path(self.folder_path))
                    interval_seconds = folder_settings.get('priority_interval', 10) * 60
                    self.context.priority_timers[self.folder_path] = time.time() + interval_seconds

class SequencePriorityState(State):
    def __init__(self, context, ordered_paths, interval_sec, start_index=0, parent_state=None):
        super().__init__(context, parent_state)
        self.ordered_paths = ordered_paths
        self.interval_sec = interval_sec
        self.current_index = start_index
        self.step_timeout_duration = self.interval_sec 
        self.step_start_time = time.time()
        self.clicked_time = None 
        
        if self.current_index == 0:
            self.context.logger.log("log_sequence_started", len(ordered_paths))

    def on_child_finished(self):
        self._advance_step()
    
    def _advance_step(self):
        self.current_index += 1
        self.step_start_time = time.time()
        self.clicked_time = None
        if self.current_index < len(self.ordered_paths):
            next_name = Path(self.ordered_paths[self.current_index]).name
            self.context.logger.log("log_sequence_next_step", next_name)
    
    def handle(self, current_time, screen_data, last_match_time_map, pre_matches=None):
        if self.current_index >= len(self.ordered_paths):
            self.context.logger.log("log_sequence_completed")
            self._return_to_parent_or_idle()
            return

        if self.clicked_time is not None:
            if current_time - self.clicked_time >= self.interval_sec:
                self._advance_step()
            return

        if current_time - self.step_start_time > self.step_timeout_duration * 3:
             self.context.logger.log("log_sequence_step_skipped", Path(self.ordered_paths[self.current_index]).name)
             self._advance_step()
             return

        target_path = self.ordered_paths[self.current_index]
        if Path(target_path).is_dir():
            folder_settings = self.context.config_manager.load_item_setting(Path(target_path))
            mode = folder_settings.get('mode', 'normal')
            
            if target_path in self.context.priority_timers:
                if current_time < self.context.priority_timers[target_path]:
                    self._advance_step()
                    return
                else:
                    del self.context.priority_timers[target_path]

            if mode == 'priority_sequence':
                child_order_names = self.context.config_manager.load_image_order(Path(target_path))
                child_ordered_paths = []
                for name in child_order_names:
                     full_path = Path(target_path) / name
                     c_set = self.context.config_manager.load_item_setting(full_path)
                     if c_set.get('mode') != 'excluded':
                         child_ordered_paths.append(str(full_path))
                step_interval = folder_settings.get('sequence_interval', 3)
                new_state = SequencePriorityState(self.context, child_ordered_paths, step_interval, start_index=0, parent_state=self)
                self.context.transition_to(new_state)
                return
            elif mode == 'priority_image' or mode == 'priority_timer':
                timeout_sec = float(folder_settings.get('priority_image_timeout', 10.0))
                m_type = 'timer' if mode == 'priority_timer' else 'image'
                new_state = PriorityState(self.context, m_type, target_path, timeout_sec, parent_state=self)
                self.context.transition_to(new_state)
                return
            else:
                child_order_names = self.context.config_manager.load_image_order(Path(target_path))
                child_ordered_paths = []
                for name in child_order_names:
                     full_path = Path(target_path) / name
                     c_set = self.context.config_manager.load_item_setting(full_path)
                     if c_set.get('mode') != 'excluded':
                         child_ordered_paths.append(str(full_path))
                new_state = SequencePriorityState(self.context, child_ordered_paths, 3.0, start_index=0, parent_state=self)
                self.context.transition_to(new_state)
                return

        target_cache = {}
        if target_path in self.context.normal_template_cache:
            target_cache[target_path] = self.context.normal_template_cache[target_path]
        elif target_path in self.context.backup_template_cache:
            target_cache[target_path] = self.context.backup_template_cache[target_path]
        if not target_cache:
            self._advance_step()
            return

        matches = self.context._find_best_match(*screen_data, target_cache)
        if matches:
            best_match = max(matches, key=lambda m: m['confidence'])
            self.context._execute_click(best_match)
            self.clicked_time = current_time

# 他のStateクラス（TimerStandbyState, CountdownState, QuickTimerStandbyState）は変更なし
class TimerStandbyState(State):
    def __init__(self, context, target_path, schedule, parent_state=None):
        super().__init__(context, parent_state)
        self.target_path = target_path
        self.schedule = schedule
        self.context.logger.log("log_timer_standby_started", Path(target_path).name)

    def handle(self, current_time, screen_data, last_match_time_map, pre_matches=None):
        actions = self.schedule['actions']
        pending = [a for a in actions if not a['executed']]
        if not pending:
            self.context.logger.log("log_timer_standby_finished")
            self._return_to_parent_or_idle()
            return
        next_action = pending[0]
        time_diff = next_action['target_time'] - current_time
        if time_diff <= 0:
            target_cache = {}
            if self.target_path in self.context.normal_template_cache:
                target_cache[self.target_path] = self.context.normal_template_cache[self.target_path]
            elif self.target_path in self.context.backup_template_cache:
                target_cache[self.target_path] = self.context.backup_template_cache[self.target_path]
            matches = self.context._find_best_match(*screen_data, target_cache)
            if matches:
                best_match = max(matches, key=lambda m: m['confidence'])
                if 'x' in next_action and 'y' in next_action:
                    import copy
                    settings_copy = copy.deepcopy(best_match['settings'])
                    settings_copy['point_click'] = True
                    settings_copy['range_click'] = False
                    settings_copy['click_position'] = [next_action['x'], next_action['y']]
                    best_match['settings'] = settings_copy
                self.context._execute_click(best_match)
                next_action['executed'] = True
                self.context.logger.log("log_timer_action_executed", Path(self.target_path).name)
                time.sleep(self.schedule['sequence_interval'])
                return
        return

class CountdownState(State):
    def __init__(self, context, trigger_match, parent_state=None):
        super().__init__(context, parent_state)
        self.trigger_match = trigger_match
        self.start_time = time.time()
        self.duration = trigger_match['settings'].get('backup_time', 300.0)
        path = trigger_match['path']
        self.context.logger.log("log_countdown_started", Path(path).name, f"{self.duration:.1f}")

    def handle(self, current_time, screen_data, last_match_time_map, pre_matches=None):
        context = self.context
        normal_matches = [m for m in (pre_matches if pre_matches is not None else []) if m['path'] in context.normal_template_cache]
        if normal_matches:
            context._process_matches_as_sequence(normal_matches, current_time, last_match_time_map)
            context.logger.log("log_countdown_cancelled")
            self._return_to_parent_or_idle()
            return
        elapsed_time = current_time - self.start_time
        if elapsed_time >= self.duration:
            context.logger.log("log_countdown_executing", f"{self.duration:.1f}")
            context._execute_click(self.trigger_match)
            self._return_to_parent_or_idle()
            context._cooldown_until = time.time() + 2.0
            return
            
    def get_remaining_time(self):
        elapsed = time.time() - self.start_time
        return max(0.0, self.duration - elapsed)

class QuickTimerStandbyState(State):
    def __init__(self, context, entry, match_val, parent_state=None):
        super().__init__(context, parent_state)
        self.entry = entry
        self.slot_num = entry.get("slot", "?")
        self.trigger_time = float(entry.get("trigger_time", 0))
        self.context.logger.log("log_quick_timer_standby_started", str(self.slot_num))

    def handle(self, current_time, screen_data, last_match_time_map, pre_matches=None):
        if current_time >= self.trigger_time:
            import cv2
            template_gray = self.entry.get("template_gray")
            frame = self.context.latest_high_res_frame
            if template_gray is not None and frame is not None:
                screen_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                res = cv2.matchTemplate(screen_gray, template_gray, cv2.TM_CCOEFF_NORMED)
                _minv, maxv, _minl, maxl = cv2.minMaxLoc(res)
                if maxv >= 0.8:
                    off_x, off_y = self.entry.get("click_offset", (0,0))
                    click_x = maxl[0] + off_x
                    click_y = maxl[1] + off_y
                    self.context.logger.log("log_quick_timer_fired", str(self.slot_num))
                    dummy_match = {
                        'path': f"QuickTimer_{self.slot_num}",
                        'confidence': maxv,
                        'rect': (maxl[0], maxl[1], template_gray.shape[1], template_gray.shape[0]),
                        'settings': {
                            'point_click': True,
                            'click_position': (click_x, click_y),
                            'roi_enabled': False,
                            'right_click': self.entry.get("right_click", False)
                        }
                    }
                    rec_area = self.context.recognition_area
                    if rec_area:
                        rel_x = click_x
                        rel_y = click_y
                        dummy_match['settings']['click_position'] = (rel_x, rel_y)
                        self.context.action_manager.execute_click(
                            dummy_match, 
                            self.context.recognition_area, 
                            self.context.target_hwnd, 
                            self.context.effective_capture_scale, 
                            self.context.current_window_scale
                        )
                    self.context.remove_quick_timer(self.entry["slot"])
                    self._return_to_parent_or_idle()
                    return
            self.context.logger.log("log_quick_timer_failed_not_found", str(self.slot_num))
            self.context.remove_quick_timer(self.entry["slot"])
            self._return_to_parent_or_idle()
