from datetime import datetime, timedelta, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


MODULE_PATH = Path(__file__).parents[1] / "plugins.v2" / "mporganizecorrect" / "schedule.py"
SPEC = spec_from_file_location("mporganizecorrect_schedule", MODULE_PATH)
schedule_module = module_from_spec(SPEC)
sys.modules[SPEC.name] = schedule_module
SPEC.loader.exec_module(schedule_module)


class DailyTrigger:
    @classmethod
    def from_crontab(cls, expression):
        assert expression == "0 4 * * *"
        return cls()

    def get_next_fire_time(self, previous, now):
        if previous is None:
            return datetime(2026, 8, 11, 4, 0, tzinfo=now.tzinfo)
        return previous + timedelta(days=1)


def test_cron_preview_lists_three_local_run_times():
    now = datetime(2026, 8, 10, 12, 0, tzinfo=timezone(timedelta(hours=8), "CST"))

    result = schedule_module.cron_preview("0 4 * * *", now=now, trigger_class=DailyTrigger)

    assert result["valid"] is True
    assert result["times"] == [
        "2026-08-11T04:00:00+08:00",
        "2026-08-12T04:00:00+08:00",
        "2026-08-13T04:00:00+08:00",
    ]


def test_cron_preview_returns_visible_error_for_blank_expression():
    result = schedule_module.cron_preview("")

    assert result["valid"] is False
    assert "不能为空" in result["text"]
