"""Cron schedule presentation helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional


def cron_preview(
    expression: Any,
    *,
    count: int = 3,
    now: Optional[datetime] = None,
    trigger_class: Any = None,
) -> dict:
    value = str(expression or "").strip()
    try:
        if not value:
            raise ValueError("表达式不能为空")
        if trigger_class is None:
            from apscheduler.triggers.cron import CronTrigger
            trigger_class = CronTrigger
        trigger = trigger_class.from_crontab(value)
        current = now or datetime.now().astimezone()
        previous = None
        fire_times = []
        for _ in range(max(1, count)):
            fire_time = trigger.get_next_fire_time(previous, current)
            if not fire_time:
                break
            fire_times.append(fire_time)
            previous = fire_time
            current = fire_time
        if not fire_times:
            raise ValueError("无法计算下次执行时间")
        timezone_name = fire_times[0].tzname() or current.tzname() or "本地时区"
        return {
            "valid": True,
            "expression": value,
            "times": [item.isoformat(timespec="seconds") for item in fire_times],
            "text": f"未来三次：{'、'.join(item.strftime('%m-%d %H:%M') for item in fire_times)}（{timezone_name}）",
        }
    except Exception as error:
        return {
            "valid": False,
            "expression": value,
            "times": [],
            "text": f"表达式无效：{error}",
        }
