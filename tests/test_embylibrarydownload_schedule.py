from datetime import datetime, timedelta, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


MODULE_PATH = (
    Path(__file__).parents[1]
    / "plugins.v2"
    / "embylibrarydownload"
    / "schedule.py"
)
SPEC = spec_from_file_location("embylibrarydownload_schedule", MODULE_PATH)
schedule_module = module_from_spec(SPEC)
sys.modules[SPEC.name] = schedule_module
SPEC.loader.exec_module(schedule_module)

cron_preview = schedule_module.cron_preview


class TwoHourlyTrigger:
    @classmethod
    def from_crontab(cls, expression):
        assert expression == "30 */2 * * *"
        return cls()

    def get_next_fire_time(self, previous, now):
        if previous is None:
            return datetime(2026, 7, 22, 20, 30, tzinfo=now.tzinfo)
        return previous + timedelta(hours=2)


class InvalidTrigger:
    @classmethod
    def from_crontab(cls, expression):
        raise ValueError("wrong number of fields")


def test_cron_preview_lists_three_concrete_local_times():
    now = datetime(2026, 7, 22, 19, 23, tzinfo=timezone(timedelta(hours=8), "CST"))

    result = cron_preview("30 */2 * * *", now=now, trigger_class=TwoHourlyTrigger)

    assert result["valid"] is True
    assert result["expression"] == "30 */2 * * *"
    assert result["times"] == [
        "2026-07-22T20:30:00+08:00",
        "2026-07-22T22:30:00+08:00",
        "2026-07-23T00:30:00+08:00",
    ]
    assert result["text"] == "未来三次：07-22 20:30、07-22 22:30、07-23 00:30（CST）"


def test_cron_preview_returns_visible_error_for_invalid_expression():
    result = cron_preview("bad cron", trigger_class=InvalidTrigger)

    assert result["valid"] is False
    assert result["text"] == "表达式无效：wrong number of fields"


def test_optional_cron_preview_explains_that_blank_disables_schedule():
    result = cron_preview("", empty_text="未设置，不会定时自动下载")

    assert result == {
        "valid": None,
        "expression": "",
        "times": [],
        "text": "未设置，不会定时自动下载",
    }
