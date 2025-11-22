# monitoring_states.py

import time
from pathlib import Path

class State:
    def __init__(self, context):
        self.context = context
    def handle(self, current_time, screen_data, last_match_time_map, pre_matches=None):
        raise NotImplementedError
    def get_name(self):
        return self.__class__.__name__

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
                        context.transition_to_image_priority(target_path)
                        return
                    
                    # --- ★ 順序優先モードへの遷移 ---
                    if folder_mode == 'priority_sequence':
                        seq_info = cache_item.get('sequence_info')
                        if seq_info:
                            ordered_paths = seq_info.get('ordered_paths', [])
                            # リストの先頭画像がマッチした場合のみトリガー
                            if ordered_paths and ordered_paths[0] == path:
                                # 通常のクリック処理を行ってから遷移する
                                context._process_matches_as_sequence([match], current_time, last_match_time_map)
                                
                                # 次の画像（インデックス1）から開始するステートへ遷移
                                # インターバル設定を取得
                                step_interval = seq_info.get('interval', 3)
                                context.transition_to_sequence_priority(ordered_paths, step_interval)
                                return

        was_clicked = context._process_matches_as_sequence(normal_matches, current_time, last_match_time_map)
        if was_clicked:
            return

        if backup_trigger_matches:
            best_backup_trigger = max(backup_trigger_matches, key=lambda m: m['confidence'])
            context.transition_to_countdown(best_backup_trigger)
            return

class PriorityState(State):
    def __init__(self, context, mode_type, folder_path, timeout_time, required_children=None):
        super().__init__(context)
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
        if current_time >= self.timeout_time:
            self.context.logger.log("log_priority_timeout", Path(self.folder_path).name)
            self.context.transition_to(IdleState(self.context))
            return

        def filter_by_folder(cache):
            return {p: d for p, d in cache.items() if d.get('folder_path') == self.folder_path}

        priority_normal_cache = filter_by_folder(self.context.normal_template_cache)
        priority_backup_cache = filter_by_folder(self.context.backup_template_cache)
        
        all_matches = self.context._find_best_match(*screen_data, priority_normal_cache)
        all_matches.extend(self.context._find_best_match(*screen_data, priority_backup_cache))
        
        if not all_matches and self.mode_type == 'image':
            if current_time - self.no_match_since_time > 10:
                self.context.logger.log("log_priority_image_timeout", Path(self.folder_path).name)
                self.context.transition_to(IdleState(self.context))
                return
        
        if all_matches:
            if self.mode_type == 'image': self.no_match_since_time = current_time

            for match in all_matches:
                path = match['path']
                cache_item = self.context.normal_template_cache.get(path) or self.context.backup_template_cache.get(path)
                
                if cache_item:
                    trigger_path = cache_item.get('priority_trigger_path')
                    if trigger_path and trigger_path != self.folder_path:
                         self.context.transition_to_image_priority(trigger_path)
                         return

            clicked_in_sequence = self.context._process_matches_as_sequence(all_matches, current_time, last_match_time_map)
            
            if clicked_in_sequence:
                if self.mode_type == 'timer':
                    folder_settings = self.context.config_manager.load_item_setting(Path(self.folder_path))
                    interval_seconds = folder_settings.get('priority_interval', 10) * 60
                    self.context.priority_timers[self.folder_path] = time.time() + interval_seconds
                
                elif self.mode_type == 'image' and self.context._last_clicked_path:
                    self.clicked_children.add(self.context._last_clicked_path)
                    if self.clicked_children.issuperset(self.required_children):
                        self.context.logger.log("log_priority_image_completed", Path(self.folder_path).name)
                        self.context.transition_to(IdleState(self.context))
                        return

class SequencePriorityState(State):
    """
    ★ 新規追加: 順序優先ステート
    A(トリガー済み) -> B -> C -> D と順番に検索し、各ステップでインターバル時間だけ待機・検索を行う。
    """
    def __init__(self, context, ordered_paths, interval_sec):
        super().__init__(context)
        self.ordered_paths = ordered_paths # 全画像のパスリスト [A, B, C, D]
        self.interval_sec = interval_sec
        
        # 現在のターゲットインデックス (A=0はトリガー済みなので、B=1から開始)
        self.current_index = 1
        
        # 次のステップに進む時刻
        self.step_end_time = time.time() + self.interval_sec
        
        # 現在のステップでクリック済みかどうか
        self.has_clicked_current_step = False
        
        self.context.logger.log("log_sequence_started", len(ordered_paths) - 1)

    def handle(self, current_time, screen_data, last_match_time_map, pre_matches=None):
        # すべての画像を巡回し終えたら終了
        if self.current_index >= len(self.ordered_paths):
            self.context.logger.log("log_sequence_completed")
            self.context.transition_to(IdleState(self.context))
            return

        # ステップの制限時間を超えたか？
        if current_time >= self.step_end_time:
            # 次の画像へ
            self.current_index += 1
            if self.current_index >= len(self.ordered_paths):
                self.context.logger.log("log_sequence_completed")
                self.context.transition_to(IdleState(self.context))
                return
            
            # 新しいステップの開始
            target_name = Path(self.ordered_paths[self.current_index]).name
            self.context.logger.log("log_sequence_next_step", target_name)
            self.step_end_time = current_time + self.interval_sec
            self.has_clicked_current_step = False
            return

        # 現在のターゲット画像のみを検索対象とするキャッシュを作成
        target_path = self.ordered_paths[self.current_index]
        
        # ターゲットがディレクトリ(孫フォルダ等)の場合はスキップして次へ
        # (現在の仕様では画像のみ対象とするのが安全)
        if Path(target_path).is_dir():
             # 即座に次へ
             self.step_end_time = current_time # 次のループでindexが増える
             return

        # キャッシュからターゲット画像データを抽出
        target_cache = {}
        if target_path in self.context.normal_template_cache:
            target_cache[target_path] = self.context.normal_template_cache[target_path]
        elif target_path in self.context.backup_template_cache:
            target_cache[target_path] = self.context.backup_template_cache[target_path]
        
        if not target_cache:
            # キャッシュにない（設定除外など）場合は待機時間を消化するだけ
            return

        # まだクリックしていない場合のみ検索＆クリック試行
        if not self.has_clicked_current_step:
            matches = self.context._find_best_match(*screen_data, target_cache)
            if matches:
                # マッチしたらクリック実行
                # (インターバル制御などは無視して即座にクリックする。
                #  なぜならこのステート自体がシーケンス制御を行っているため)
                best_match = max(matches, key=lambda m: m['confidence'])
                self.context._execute_click(best_match)
                
                # クリック済みフラグを立てる (このステップではもうクリックしない)
                self.has_clicked_current_step = True
                # ユーザー要件: "マッチしてもインターバル待機" なので、step_end_time は短縮しない

class CountdownState(State):
    def __init__(self, context, trigger_match):
        super().__init__(context)
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
            context.transition_to(IdleState(context))
            return
        
        elapsed_time = current_time - self.start_time
        if elapsed_time >= self.duration:
            context.logger.log("log_countdown_executing", f"{self.duration:.1f}")
            context._execute_click(self.trigger_match)
            context.transition_to(IdleState(context))
            context._cooldown_until = time.time() + 1.0
            return
            
    def get_remaining_time(self):
        elapsed = time.time() - self.start_time
        return max(0, self.duration - elapsed)
