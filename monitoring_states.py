# monitoring_states.py

import time
from pathlib import Path

class State:
    def __init__(self, context, parent_state=None):
        self.context = context
        self.parent_state = parent_state # ★ 親ステートへの参照 (スタック構造)

    def handle(self, current_time, screen_data, last_match_time_map, pre_matches=None):
        raise NotImplementedError
    
    def get_name(self):
        return self.__class__.__name__

    def _return_to_parent_or_idle(self):
        """処理完了時に親ステートへ戻るか、親がいなければIdleへ戻る"""
        if self.parent_state:
            self.parent_state.on_child_finished() # 親に「子の処理が終わった」と通知
            self.context.transition_to(self.parent_state)
        else:
            self.context.transition_to(IdleState(self.context))
            
    def on_child_finished(self):
        """子ステートから復帰した際のフック (必要に応じてオーバーライド)"""
        pass

class IdleState(State):
    def handle(self, current_time, screen_data, last_match_time_map, pre_matches=None):
        context = self.context
        all_matches = pre_matches if pre_matches is not None else []
        
        normal_matches = [m for m in all_matches if m['path'] in context.normal_template_cache]
        backup_trigger_matches = [m for m in all_matches if m['path'] in context.backup_template_cache]

        if normal_matches:
            for match in normal_matches:
                path = match['path']
                cache_item = context.normal_template_cache.get(path)
                
                if cache_item:
                    folder_mode = cache_item.get('folder_mode')
                    
                    # --- 画像優先モードへの遷移 ---
                    if folder_mode == 'priority_image':
                        trigger_path = cache_item.get('priority_trigger_path')
                        target_path = trigger_path if trigger_path else cache_item['folder_path']
                        
                        # ★ 修正: CoreEngineのメソッドを使わず直接インスタンス化
                        timeout_time = time.time() + 300 # デフォルト値
                        required_children = context.folder_children_map.get(target_path, set())
                        new_state = PriorityState(context, 'image', target_path, timeout_time, required_children)
                        context.transition_to(new_state)
                        return
                    
                    # --- 順序優先モードへの遷移 ---
                    if folder_mode == 'priority_sequence':
                        seq_info = cache_item.get('sequence_info')
                        if seq_info:
                            ordered_paths = seq_info.get('ordered_paths', [])
                            # リストの先頭画像がマッチした場合のみトリガー
                            if ordered_paths and ordered_paths[0] == path:
                                # まず最初の画像のクリック処理を行う
                                context._process_matches_as_sequence([match], current_time, last_match_time_map)
                                
                                # ★ 修正: CoreEngineのメソッドを使わず直接インスタンス化
                                step_interval = seq_info.get('interval', 3)
                                new_state = SequencePriorityState(context, ordered_paths, step_interval, start_index=1, parent_state=None)
                                context.transition_to(new_state)
                                return

        was_clicked = context._process_matches_as_sequence(normal_matches, current_time, last_match_time_map)
        if was_clicked:
            return

        if backup_trigger_matches:
            best_backup_trigger = max(backup_trigger_matches, key=lambda m: m['confidence'])
            # ★ 修正: 直接インスタンス化
            new_state = CountdownState(context, best_backup_trigger)
            context.transition_to(new_state)
            return

class PriorityState(State):
    def __init__(self, context, mode_type, folder_path, timeout_time, required_children=None, parent_state=None):
        super().__init__(context, parent_state)
        self.mode_type = mode_type
        self.folder_path = folder_path
        self.timeout_time = timeout_time
        self.no_match_since_time = time.time() if mode_type == 'image' else None
        self.required_children = required_children if required_children is not None else set()
        self.clicked_children = set()
        
        folder_name = Path(folder_path).name
        if mode_type == 'timer':
            timeout_min = (timeout_time - time.time()) / 60
            self.context.logger.log("log_priority_timer_started", folder_name, f"{timeout_min:.1f}")
        else:
            self.context.logger.log("log_priority_image_started", folder_name)

    def handle(self, current_time, screen_data, last_match_time_map, pre_matches=None):
        # タイムアウト判定
        if current_time >= self.timeout_time:
            self.context.logger.log("log_priority_timeout", Path(self.folder_path).name)
            self._return_to_parent_or_idle()
            return

        def filter_by_folder(cache):
            return {p: d for p, d in cache.items() if d.get('folder_path') == self.folder_path}

        priority_normal_cache = filter_by_folder(self.context.normal_template_cache)
        priority_backup_cache = filter_by_folder(self.context.backup_template_cache)
        
        all_matches = self.context._find_best_match(*screen_data, priority_normal_cache)
        all_matches.extend(self.context._find_best_match(*screen_data, priority_backup_cache))
        
        # 画像優先モードで、一定時間見つからない場合のタイムアウト判定
        if not all_matches and self.mode_type == 'image':
            if current_time - self.no_match_since_time > 10:
                self.context.logger.log("log_priority_image_timeout", Path(self.folder_path).name)
                self._return_to_parent_or_idle()
                return
        
        if all_matches:
            if self.mode_type == 'image': self.no_match_since_time = current_time

            for match in all_matches:
                path = match['path']
                cache_item = self.context.normal_template_cache.get(path) or self.context.backup_template_cache.get(path)
                
                if cache_item:
                    trigger_path = cache_item.get('priority_trigger_path')
                    # ネストされたフォルダの処理分岐
                    if trigger_path and trigger_path != self.folder_path:
                         # ★ 修正: 直接インスタンス化
                         timeout_time = time.time() + 300 
                         required_children = self.context.folder_children_map.get(trigger_path, set())
                         new_state = PriorityState(self.context, 'image', trigger_path, timeout_time, required_children, parent_state=self)
                         self.context.transition_to(new_state)
                         return

            clicked_in_sequence = self.context._process_matches_as_sequence(all_matches, current_time, last_match_time_map)
            
            if clicked_in_sequence:
                if self.mode_type == 'timer':
                    folder_settings = self.context.config_manager.load_item_setting(Path(self.folder_path))
                    interval_seconds = folder_settings.get('priority_interval', 10) * 60
                    self.context.priority_timers[self.folder_path] = time.time() + interval_seconds
                
                elif self.mode_type == 'image' and self.context._last_clicked_path:
                    self.clicked_children.add(self.context._last_clicked_path)
                    # 必要な画像をすべてクリックしたら完了
                    if self.clicked_children.issuperset(self.required_children):
                        self.context.logger.log("log_priority_image_completed", Path(self.folder_path).name)
                        self._return_to_parent_or_idle()
                        return

class SequencePriorityState(State):
    """
    ★ 順序優先ステート (再帰対応・直接インスタンス化版)
    """
    def __init__(self, context, ordered_paths, interval_sec, start_index=0, parent_state=None):
        super().__init__(context, parent_state)
        self.ordered_paths = ordered_paths
        self.interval_sec = interval_sec
        
        self.current_index = start_index
        self.step_end_time = time.time() + self.interval_sec
        self.has_clicked_current_step = False
        
        if self.current_index == 0:
            self.context.logger.log("log_sequence_started", len(ordered_paths))

    def on_child_finished(self):
        """子フォルダの処理が完了して戻ってきた時に呼ばれる"""
        # 子フォルダの処理が終わったので、次のアイテムへ進む
        self.current_index += 1
        self.step_end_time = time.time() + self.interval_sec
        self.has_clicked_current_step = False
        
        if self.current_index < len(self.ordered_paths):
            next_name = Path(self.ordered_paths[self.current_index]).name
            self.context.logger.log("log_sequence_next_step", next_name)
    
    def handle(self, current_time, screen_data, last_match_time_map, pre_matches=None):
        # 完了チェック
        if self.current_index >= len(self.ordered_paths):
            self.context.logger.log("log_sequence_completed")
            self._return_to_parent_or_idle()
            return

        # ステップ時間切れチェック
        if current_time >= self.step_end_time:
            self.current_index += 1
            if self.current_index >= len(self.ordered_paths):
                self.context.logger.log("log_sequence_completed")
                self._return_to_parent_or_idle()
                return
            
            next_name = Path(self.ordered_paths[self.current_index]).name
            self.context.logger.log("log_sequence_next_step", next_name)
            self.step_end_time = current_time + self.interval_sec
            self.has_clicked_current_step = False
            return

        target_path = self.ordered_paths[self.current_index]
        
        # ★★★ ターゲットが「フォルダ」の場合の分岐 ★★★
        if Path(target_path).is_dir():
            # ConfigManager経由でフォルダ設定をロード
            folder_settings = self.context.config_manager.load_item_setting(Path(target_path))
            mode = folder_settings.get('mode', 'normal')
            
            # 子ステートを作成して遷移 (自分を parent_state に指定)
            if mode == 'priority_sequence':
                child_order_names = self.context.config_manager.load_image_order(Path(target_path))
                child_ordered_paths = []
                for name in child_order_names:
                     full_path = Path(target_path) / name
                     c_set = self.context.config_manager.load_item_setting(full_path)
                     if c_set.get('mode') != 'excluded':
                         child_ordered_paths.append(str(full_path))
                
                step_interval = folder_settings.get('sequence_interval', 3)
                # ★ 修正: 直接インスタンス化
                new_state = SequencePriorityState(self.context, child_ordered_paths, step_interval, start_index=0, parent_state=self)
                self.context.transition_to(new_state)
                return

            elif mode == 'priority_image':
                timeout_sec = folder_settings.get('priority_image_timeout', 10)
                timeout_time = time.time() + timeout_sec
                # ★ 修正: 直接インスタンス化
                new_state = PriorityState(self.context, 'image', target_path, timeout_time, parent_state=self)
                self.context.transition_to(new_state)
                return
            
            else:
                # 通常フォルダ -> 順序シーケンスとして扱う
                child_order_names = self.context.config_manager.load_image_order(Path(target_path))
                child_ordered_paths = []
                for name in child_order_names:
                     full_path = Path(target_path) / name
                     c_set = self.context.config_manager.load_item_setting(full_path)
                     if c_set.get('mode') != 'excluded':
                         child_ordered_paths.append(str(full_path))
                
                # ★ 修正: 直接インスタンス化
                new_state = SequencePriorityState(self.context, child_ordered_paths, 3.0, start_index=0, parent_state=self)
                self.context.transition_to(new_state)
                return

        # ★★★ ターゲットが「画像」の場合 ★★★
        target_cache = {}
        if target_path in self.context.normal_template_cache:
            target_cache[target_path] = self.context.normal_template_cache[target_path]
        elif target_path in self.context.backup_template_cache:
            target_cache[target_path] = self.context.backup_template_cache[target_path]
        
        if not target_cache:
            return

        if not self.has_clicked_current_step:
            matches = self.context._find_best_match(*screen_data, target_cache)
            if matches:
                best_match = max(matches, key=lambda m: m['confidence'])
                self.context._execute_click(best_match)
                self.has_clicked_current_step = True

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
            context._cooldown_until = time.time() + 1.0
            return
            
    def get_remaining_time(self):
        elapsed = time.time() - self.start_time
        return max(0, self.duration - elapsed)
