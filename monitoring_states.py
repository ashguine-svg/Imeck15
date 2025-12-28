# monitoring_states.py
# 状態遷移ロジック
# ★★★ (拡張) タイマー連動クリック機能 (TimerStandbyState) を実装 ★★★
# ★★★ (修正) ロック時間内は通常監視から対象を除外し、タイマーによる独占制御を行うロジックを追加 ★★★
# ★★★ (修正) 画像認識のROI設定がある場合、タイマー座標にROIのオフセットを適用する ★★★

import time
import pyautogui
from pathlib import Path
import cv2
import numpy as np

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

        # --- 0. クイックタイマー（Shift+右クリック予約）判定 ---
        # 1分前からROIテンプレでマッチ監視し、見つかったら通常監視を一時停止して指定時刻でクリック
        if hasattr(context, "quick_timers") and context.quick_timers:
            # もっとも近い予定を優先
            entries = list(context.quick_timers.values())
            entries.sort(key=lambda e: float(e.get("trigger_time", 0)))
            for e in entries:
                trigger_time = float(e.get("trigger_time", 0))
                match_start = float(e.get("match_start_time", trigger_time - 60.0))
                if current_time < match_start:
                    continue
                # 期限切れは削除
                if current_time > trigger_time + 5.0:
                    try:
                        slot = int(e.get("slot"))
                        del context.quick_timers[slot]
                        context.quickTimersChanged.emit()
                    except Exception:
                        pass
                    continue

                template_gray = e.get("template_gray", None)
                if template_gray is None:
                    continue

                frame = getattr(context, "latest_high_res_frame", None)
                if frame is None or frame.size == 0:
                    continue

                screen_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                if screen_gray.shape[0] < template_gray.shape[0] or screen_gray.shape[1] < template_gray.shape[1]:
                    continue

                res = cv2.matchTemplate(screen_gray, template_gray, cv2.TM_CCOEFF_NORMED)
                _minv, maxv, _minl, maxl = cv2.minMaxLoc(res)
                if maxv >= 0.85:
                    context.transition_to(QuickTimerStandbyState(context, e, maxl))
                    return
                # マッチしていない場合も通常監視は継続（1分前から探索を続ける）
                break
        
        # --- 1. タイマーアプローチ (ロック) 判定 ---
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
        
        # --- 2. 通常監視ロジック ---
        all_matches = pre_matches if pre_matches is not None else []
        
        # タイマーロック中の画像を除外
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
        all_matches = candidates

        normal_matches = [m for m in all_matches if m['path'] in context.normal_template_cache]
        backup_trigger_matches = [m for m in all_matches if m['path'] in context.backup_template_cache]

        # 混合リストの作成（通常画像 + 各シーケンスの1番目）
        filtered_matches = []
        sequence_trigger_map = {} # path -> seq_info
        priority_image_map = {}   # path -> cache_item

        for match in normal_matches:
            path = match['path']
            cache_item = context.normal_template_cache.get(path)
            if not cache_item: continue
            
            folder_mode = cache_item.get('folder_mode')
            
            # 順序優先(シーケンス)モード
            if folder_mode == 'priority_sequence':
                seq_info = cache_item.get('sequence_info')
                if seq_info:
                    ordered_paths = seq_info.get('ordered_paths', [])
                    if ordered_paths and ordered_paths[0] == path:
                        filtered_matches.append(match)
                        sequence_trigger_map[path] = seq_info
                    continue
            
            # 画像優先モード
            if folder_mode == 'priority_image':
                filtered_matches.append(match)
                priority_image_map[path] = cache_item
                continue

            # 通常画像
            filtered_matches.append(match)

        # インターバル比較を実行し、実際にクリックされた画像を取得
        clicked_match = context._process_matches_as_sequence(filtered_matches, current_time, last_match_time_map)
        
        if clicked_match:
            clicked_path = clicked_match['path']
            
            # --- クリック後の状態遷移処理 ---
            
            # A. シーケンスモードへの遷移
            if clicked_path in sequence_trigger_map:
                seq_info = sequence_trigger_map[clicked_path]
                ordered_paths = seq_info.get('ordered_paths', [])
                step_interval = seq_info.get('interval', 3)
                new_state = SequencePriorityState(context, ordered_paths, step_interval, start_index=0)
                context.transition_to(new_state)
                return

            # B. 画像優先モードへの遷移
            if clicked_path in priority_image_map:
                cache_item = priority_image_map[clicked_path]
                trigger_path = cache_item.get('priority_trigger_path')
                target_path = trigger_path if trigger_path else cache_item['folder_path']
                timeout_time = time.time() + 300
                required_children = context.folder_children_map.get(target_path, set())
                new_state = PriorityState(context, 'image', target_path, timeout_time, required_children)
                context.transition_to(new_state)
                return
            
            return

        # 3. バックアップトリガー
        if backup_trigger_matches:
            best_backup_trigger = max(backup_trigger_matches, key=lambda m: m['confidence'])
            new_state = CountdownState(context, best_backup_trigger)
            context.transition_to(new_state)
            return

class TimerStandbyState(State):
    """
    タイマー待機・実行ステート
    対象画像が存在する限りロックし、時間が来たら座標をクリックする。
    """
    def __init__(self, context, target_path, schedule, parent_state=None):
        super().__init__(context, parent_state)
        self.target_path = target_path
        self.schedule = schedule
        
        # 画像ロスト時の猶予期間用変数
        self.last_seen_time = time.time()
        
        self.context.logger.log(f"[INFO] Timer Standby: Locked on {Path(target_path).name}")

    def handle(self, current_time, screen_data, last_match_time_map, pre_matches=None):
        context = self.context

        # 1. 存在確認 (Safety)
        # 現在の画面に対象画像があるか確認。
        cache_item = context.normal_template_cache.get(self.target_path) or \
                     context.backup_template_cache.get(self.target_path)
        
        target_match = None
        if cache_item:
            matches = context._find_best_match(*screen_data, {self.target_path: cache_item})
            if matches:
                target_match = matches[0] # 最も信頼度が高いもの
        
        if target_match:
            self.last_seen_time = current_time # 画像が見えたら最終確認時刻を更新
        else:
            # 画像が見つからない場合: 猶予期間 (Grace Period)
            if current_time - self.last_seen_time > 5.0:
                context.logger.log("[INFO] Timer Target lost (timeout). Returning to Idle.")
                self._return_to_parent_or_idle()
                return

        # 2. 時刻判定
        pending_actions = [a for a in self.schedule['actions'] if not a['executed']]
        if not pending_actions:
            context.logger.log("[INFO] All timer actions completed.")
            self._return_to_parent_or_idle()
            return

        next_action = pending_actions[0]
        
        # ターゲット時刻になったか？
        if current_time >= next_action['target_time']:
            # 時間が来た時点で画像が見えている場合のみクリック
            if target_match:
                self.execute_action(next_action, target_match)
            else:
                # ターゲット時刻+1.0秒を過ぎたら諦める (Strict Timeout)
                if current_time > next_action['target_time'] + 1.0:
                     context.logger.log(f"[WARN] Timer action failed: Target image not visible at trigger time. (ID:{next_action['id']})")
                     next_action['executed'] = True

    def execute_action(self, action, match_info):
        """アクション実行ロジック (座標計算 -> クリック -> 待機)"""
        context = self.context
        
        # --- 座標計算 ---
        match_rect = match_info['rect'] # (x, y, w, h) in Capture coords
        match_x, match_y = match_rect[0], match_rect[1]
        
        # テンプレートマッチング時のスケール (Original -> Capture)
        current_scale = match_info.get('scale', 1.0)
        
        # 設定された相対座標 (Original coords)
        target_x_orig = action['x']
        target_y_orig = action['y']
        
        # --- ▼▼▼ 修正: ROIオフセットの補正 ▼▼▼ ---
        # ROI設定がある場合、マッチングに使われたテンプレートは「元の画像から切り抜かれた一部」です。
        # action['x']は「元の画像」の左上からの座標ですが、
        # match_x は「切り抜かれた画像」の左上がマッチした場所です。
        # そのため、action['x'] から「切り抜きの開始位置(ROI X)」を引いて、
        # 「切り抜き画像内での相対座標」に変換してからスケールする必要があります。
        
        settings = match_info.get('settings', {})
        roi_offset_x = 0
        roi_offset_y = 0
        
        if settings.get('roi_enabled', False):
            roi_mode = settings.get('roi_mode', 'fixed')
            roi_rect = settings.get('roi_rect_variable') if roi_mode == 'variable' else settings.get('roi_rect')
            
            if roi_rect:
                roi_offset_x = roi_rect[0]
                roi_offset_y = roi_rect[1]
        
        # 切り抜き画像(テンプレート)内での相対位置に変換
        rel_x_in_template = target_x_orig - roi_offset_x
        rel_y_in_template = target_y_orig - roi_offset_y
        
        # スケール適用 (Capture coords)
        rel_x_scaled = rel_x_in_template * current_scale
        rel_y_scaled = rel_y_in_template * current_scale
        
        # --- ▲▲▲ 修正完了 ▲▲▲ ---
        
        # 認識エリアのオフセット
        area_offset_x = context.recognition_area[0]
        area_offset_y = context.recognition_area[1]
        
        # 実効キャプチャスケールの逆補正 (Capture -> Screen)
        eff_scale = context.effective_capture_scale
        
        final_click_x = area_offset_x + ((match_x + rel_x_scaled) / eff_scale)
        final_click_y = area_offset_y + ((match_y + rel_y_scaled) / eff_scale)
        
        # --- 安全装置: ウィンドウアクティブ化 ---
        if context.target_hwnd:
            context.action_manager._activate_window(context.target_hwnd)
            
        # --- クリック実行 ---
        try:
            cx, cy = int(final_click_x), int(final_click_y)
            
            from action import block_input
            
            block_input(True)
            # 画像ごとの設定に従う（デフォルトは左クリック）
            settings = match_info.get('settings', {}) if isinstance(match_info, dict) else {}
            btn = 'right' if bool(settings.get('right_click', False)) else 'left'
            pyautogui.click(cx, cy, button=btn)
            block_input(False)
            
            context.logger.log(f"[INFO] Timer Action Executed: ID {action['id']} @ ({cx}, {cy})")
            
            action['executed'] = True
            
            interval = self.schedule.get('sequence_interval', 1.0)
            if interval > 0:
                time.sleep(interval)
                
        except Exception as e:
            context.logger.log(f"[ERROR] Timer click failed: {e}")
            action['executed'] = True


class QuickTimerStandbyState(State):
    """
    クイックタイマー待機・実行ステート（1回限り）
    - 予約ROIテンプレが見つかった位置を基準に、指定分後にクリック
    """
    def __init__(self, context, entry: dict, first_match_top_left: tuple, parent_state=None):
        super().__init__(context, parent_state)
        self.entry = entry
        self.slot = int(entry.get("slot", 0))
        self.trigger_time = float(entry.get("trigger_time", 0))
        self.template_gray = entry.get("template_gray", None)
        self.click_offset = tuple(entry.get("click_offset", (0, 0)))
        self.last_seen_time = time.time()
        self.last_match_top_left = tuple(first_match_top_left)  # (x, y) in capture coords (high-res)
        self.context.logger.log(f"[INFO] QuickTimer Standby: Locked (slot={self.slot})")

    def handle(self, current_time, screen_data, last_match_time_map, pre_matches=None):
        context = self.context

        # 1) 位置更新（見えなくなっても少し猶予）
        if self.template_gray is not None:
            frame = getattr(context, "latest_high_res_frame", None)
            if frame is not None and frame.size != 0:
                try:
                    screen_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    if screen_gray.shape[0] >= self.template_gray.shape[0] and screen_gray.shape[1] >= self.template_gray.shape[1]:
                        res = cv2.matchTemplate(screen_gray, self.template_gray, cv2.TM_CCOEFF_NORMED)
                        _minv, maxv, _minl, maxl = cv2.minMaxLoc(res)
                        if maxv >= 0.85:
                            self.last_seen_time = current_time
                            self.last_match_top_left = tuple(maxl)
                except Exception:
                    pass

        # 2) 実行時刻判定
        if current_time >= self.trigger_time:
            # 見失いが長い場合は中止（誤クリック防止）
            if current_time - self.last_seen_time > 5.0:
                context.logger.log(f"[WARN] QuickTimer: target lost at trigger time (slot={self.slot}). Cancelling.")
                self._consume_and_exit()
                return

            try:
                rx0, ry0 = int(context.recognition_area[0]), int(context.recognition_area[1])
                mx, my = int(self.last_match_top_left[0]), int(self.last_match_top_left[1])
                dx, dy = int(self.click_offset[0]), int(self.click_offset[1])
                cx = rx0 + mx + dx
                cy = ry0 + my + dy

                if context.target_hwnd:
                    context.action_manager._activate_window(context.target_hwnd)

                from action import block_input
                block_input(True)
                # 予約ごとの設定に従う（デフォルトは左クリック）
                btn = 'right' if bool(self.entry.get('right_click', False)) else 'left'
                pyautogui.click(int(cx), int(cy), button=btn)
                block_input(False)
                context.logger.log(f"[INFO] QuickTimer Executed: slot={self.slot} @ ({int(cx)}, {int(cy)})")
            except Exception as e:
                context.logger.log(f"[ERROR] QuickTimer click failed: {e}")
            finally:
                self._consume_and_exit()

    def _consume_and_exit(self):
        # 1回限りなので消す
        try:
            if hasattr(self.context, "quick_timers") and self.slot in self.context.quick_timers:
                del self.context.quick_timers[self.slot]
                self.context.quickTimersChanged.emit()
        except Exception:
            pass
        self._return_to_parent_or_idle()

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
    def __init__(self, context, ordered_paths, interval_sec, start_index=0, parent_state=None):
        super().__init__(context, parent_state)
        self.ordered_paths = ordered_paths
        self.interval_sec = interval_sec
        
        self.current_index = start_index
        # 全ての画像（1番目も2番目以降も）でインターバル後にクリックするため、
        # step_end_timeを現在時刻 + インターバル時間に設定
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
        
        # ターゲットが「フォルダ」の場合
        if Path(target_path).is_dir():
            folder_settings = self.context.config_manager.load_item_setting(Path(target_path))
            mode = folder_settings.get('mode', 'normal')
            
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

            elif mode == 'priority_image':
                timeout_sec = folder_settings.get('priority_image_timeout', 10)
                timeout_time = time.time() + timeout_sec
                new_state = PriorityState(self.context, 'image', target_path, timeout_time, parent_state=self)
                self.context.transition_to(new_state)
                return
            
            else:
                # 通常フォルダ -> 順序シーケンス
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

        # ターゲットが「画像」の場合
        target_cache = {}
        if target_path in self.context.normal_template_cache:
            target_cache[target_path] = self.context.normal_template_cache[target_path]
        elif target_path in self.context.backup_template_cache:
            target_cache[target_path] = self.context.backup_template_cache[target_path]
        
        if not target_cache:
            return

        if not self.has_clicked_current_step:
            # インターバル時間が経過しているかチェック
            if current_time < self.step_end_time:
                # インターバル時間が経過していない場合は待機
                return
            
            matches = self.context._find_best_match(*screen_data, target_cache)
            if matches:
                best_match = max(matches, key=lambda m: m['confidence'])
                self.context._execute_click(best_match)
                self.has_clicked_current_step = True
                # クリック後、インターバル時間を守るため、step_end_timeを更新
                # これにより、次のステップに進む前にインターバル時間が経過するまで待機する
                self.step_end_time = current_time + self.interval_sec
            else:
                # 画像が検出されない場合でも、インターバル時間を設定して待機
                # これにより、画像が検出されなくても次のステップに進むことができる
                self.step_end_time = current_time + self.interval_sec

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
