"""
timer_schedule.py

CoreEngine のタイマースケジュール構築ロジックを切り出す（リファクタ: core.py分割）。
挙動は変えず、壊れた設定データが混ざっても全体を落とさないようガードする。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any


def build_timer_schedule_cache(
    *,
    normal_template_cache: dict,
    backup_template_cache: dict,
    logger,
) -> Dict[str, Dict[str, Any]]:
    schedule: Dict[str, Dict[str, Any]] = {}
    all_caches = list((normal_template_cache or {}).items()) + list((backup_template_cache or {}).items())
    now = datetime.now()

    for path, data in all_caches:
        # 壊れた設定（None/想定外型）を安全にスキップして、キャッシュ再構築全体を落とさない
        if not isinstance(data, dict):
            try:
                logger.log(f"[WARN] Timer schedule: invalid cache entry for {Path(path).name} (data is {type(data).__name__})")
            except Exception:
                pass
            continue

        settings = data.get('settings') or {}
        if not isinstance(settings, dict):
            try:
                logger.log(f"[WARN] Timer schedule: invalid settings for {Path(path).name} (settings is {type(settings).__name__})")
            except Exception:
                pass
            continue

        timer_conf = settings.get('timer_mode') or {}
        if not isinstance(timer_conf, dict):
            continue

        if not timer_conf.get('enabled', False):
            continue

        actions = []
        saved_actions = timer_conf.get('actions', [])
        if not isinstance(saved_actions, list):
            saved_actions = []

        for act in saved_actions:
            if not isinstance(act, dict):
                continue
            if not act.get('enabled', False):
                continue

            time_str = act.get('display_time', "20:00:00")
            try:
                t_time = datetime.strptime(time_str, "%H:%M:%S").time()
                target_dt = datetime.combine(now.date(), t_time)
                if target_dt < now:
                    target_dt += timedelta(days=1)
                act_copy = act.copy()
                act_copy['target_time'] = target_dt.timestamp()
                act_copy['executed'] = False
                actions.append(act_copy)
            except ValueError:
                try:
                    logger.log(f"[WARN] Invalid time format for {Path(path).name}: {time_str}")
                except Exception:
                    pass
                continue

        actions.sort(key=lambda x: x['target_time'])
        if actions:
            schedule[path] = {
                "approach_time": timer_conf.get('approach_time', 5) * 60,
                "sequence_interval": timer_conf.get('sequence_interval', 1.0),
                "actions": actions,
            }

    if schedule:
        try:
            logger.log(f"[INFO] Timer schedule built for {len(schedule)} items.")
        except Exception:
            pass

    return schedule


