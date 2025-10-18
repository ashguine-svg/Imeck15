# monitoring_states.py (最終修正版)

import time
from pathlib import Path

class State:
    """監視状態の基底クラス"""
    def __init__(self, context):
        self.context = context

    def handle(self, current_time, screen_data, last_match_time_map, pre_matches=None):
        """この状態で実行されるべき処理"""
        raise NotImplementedError

    def get_name(self):
        return self.__class__.__name__

class IdleState(State):
    """通常監視の状態"""
    def handle(self, current_time, screen_data, last_match_time_map, pre_matches=None):
        context = self.context
        
        # core.py でマッチング済みのため、pre_matches を使用する
        all_matches = pre_matches if pre_matches is not None else []
        
        # pre_matches は既に Eco Modeの除外対象外のフィルタリングがされている
        normal_matches = [m for m in all_matches if m['path'] in context.normal_template_cache]
        backup_trigger_matches = [m for m in all_matches if m['path'] in context.backup_template_cache]

        if normal_matches:
            for match in normal_matches:
                path = match['path']
                cache_item = context.normal_template_cache.get(path)
                # 優先画像検出による状態遷移 (Eco Mode解除後に実行される)
                if cache_item and cache_item.get('folder_mode') == 'priority_image':
                    context.transition_to_image_priority(cache_item['folder_path'])
                    return

        was_clicked = context._process_matches_as_sequence(normal_matches, current_time, last_match_time_map)
        if was_clicked:
            return

        # バックアップトリガー検出による状態遷移 (Eco Mode解除後に実行される)
        if backup_trigger_matches:
            best_backup_trigger = max(backup_trigger_matches, key=lambda m: m['confidence'])
            context.transition_to_countdown(best_backup_trigger)
            return

class PriorityState(State):
    """優先モードの状態"""
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
            self.context.logger.log(f"フォルダ '{folder_name}' のタイマー優先監視を開始しました。(解除時間: {timeout_min:.1f}分)")
        else:
            self.context.logger.log(f"フォルダ '{folder_name}' の画像認識型優先監視を開始しました。")

    def handle(self, current_time, screen_data, last_match_time_map, pre_matches=None):
        if current_time >= self.timeout_time:
            self.context.logger.log(f"フォルダ '{Path(self.folder_path).name}' の優先監視を終了しました。(タイムアウト)")
            self.context.transition_to(IdleState(self.context))
            return

        def filter_by_folder(cache):
            # 現在の優先フォルダ内のアイテムのみを抽出
            return {p: d for p, d in cache.items() if d.get('folder_path') == self.folder_path}

        priority_normal_cache = filter_by_folder(self.context.normal_template_cache)
        priority_backup_cache = filter_by_folder(self.context.backup_template_cache)
        
        # pre_matchesではなく、優先キャッシュに対してマッチングを再実行
        # (core._monitoring_loopでpre_matchesはIdleStateの除外対象外のみが返されるため)
        all_matches = self.context._find_best_match(*screen_data, priority_normal_cache)
        all_matches.extend(self.context._find_best_match(*screen_data, priority_backup_cache))
        
        if not all_matches and self.mode_type == 'image':
            if current_time - self.no_match_since_time > 10: # タイムアウトは10秒固定とする
                self.context.logger.log(f"フォルダ '{Path(self.folder_path).name}' の優先監視を終了しました。(タイムアウト)")
                self.context.transition_to(IdleState(self.context))
                return
        
        if all_matches:
            if self.mode_type == 'image': self.no_match_since_time = current_time

            clicked_in_sequence = self.context._process_matches_as_sequence(all_matches, current_time, last_match_time_map)
            
            if clicked_in_sequence:
                if self.mode_type == 'timer':
                    # タイマーをリセット
                    folder_settings = self.context.config_manager.load_item_setting(Path(self.folder_path))
                    interval_seconds = folder_settings.get('priority_interval', 10) * 60
                    self.context.priority_timers[self.folder_path] = time.time() + interval_seconds
                
                elif self.mode_type == 'image' and self.context._last_clicked_path:
                    self.clicked_children.add(self.context._last_clicked_path)
                    if self.clicked_children.issuperset(self.required_children):
                        self.context.logger.log(f"フォルダ '{Path(self.folder_path).name}' の優先監視を終了しました。(完了)")
                        self.context.transition_to(IdleState(self.context))
                        return

class CountdownState(State):
    """バックアップカウントダウンの状態"""
    def __init__(self, context, trigger_match):
        super().__init__(context)
        self.trigger_match = trigger_match
        self.start_time = time.time()
        self.duration = trigger_match['settings'].get('backup_time', 300.0)
        
        path = trigger_match['path']
        self.context.logger.log(f"バックアップ画像 '{Path(path).name}' を検出。{self.duration:.1f}秒のカウントダウンを開始します。")

    def handle(self, current_time, screen_data, last_match_time_map, pre_matches=None):
        context = self.context
        
        # core.py でマッチング済みのため、pre_matches を使用する
        normal_matches = [m for m in (pre_matches if pre_matches is not None else []) if m['path'] in context.normal_template_cache]
        
        if normal_matches:
            context._process_matches_as_sequence(normal_matches, current_time, last_match_time_map)
            context.logger.log("通常画像を検出したため、バックアップカウントダウンをキャンセルします。")
            context.transition_to(IdleState(context))
            return
        
        elapsed_time = current_time - self.start_time
        if elapsed_time >= self.duration:
            context.logger.log(f"{self.duration:.1f}秒が経過。バックアップクリックを実行します。")
            context._execute_final_backup_click(self.trigger_match['path'])
            context.transition_to(IdleState(context))
            context._cooldown_until = time.time() + 1.0
            return
            
    def get_remaining_time(self):
        elapsed = time.time() - self.start_time
        return max(0, self.duration - elapsed)
