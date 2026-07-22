from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "plugins.v2" / "embylibrarydownload" / "notifications.py"
SPEC = spec_from_file_location("embylibrarydownload_notifications", MODULE_PATH)
MODULE = module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_inventory_summary_contains_only_aggregate_data():
    summary = MODULE.build_task_summary(
        "inventory", "success",
        {
            "versions": 400,
            "servers": [{"server": "Emby"}],
            "targets": {"lists": 2, "items": 300, "present": 80, "missing": 220},
            "private_detail": "Movie.Name.2026.2160p",
        },
        finished_at="2026-07-23T02:15:00+08:00",
    )

    assert summary["config_key"] == "notify_inventory"
    assert summary["title"].startswith("📚")
    assert "库存版本：400" in summary["text"]
    assert "已入库：80" in summary["text"]
    assert "Movie.Name" not in summary["text"]
    assert "完成时间：2026-07-23 02:15" in summary["text"]


def test_target_summary_uses_counts_without_movie_details():
    summary = MODULE.build_task_summary(
        "target-pool:9", "success",
        {"matched": 234, "submitted": 5, "skipped": [{"message": "secret"}]},
        target={
            "title": "豆瓣电影 Top250", "item_count": 250,
            "in_library_count": 66, "missing_count": 184,
            "items": [{"title": "肖申克的救赎"}],
        },
    )

    assert summary["config_key"] == "notify_targets"
    assert summary["title"] == "🎯 豆瓣电影 Top250 · 目标汇总"
    assert "清单影片：250" in summary["text"]
    assert "匹配种子：234" in summary["text"]
    assert "肖申克的救赎" not in summary["text"]
    assert "secret" not in summary["text"]


def test_failure_summary_does_not_expose_error_detail():
    summary = MODULE.build_task_summary(
        "pool", "failed", {"error": "cookie and stack trace must stay private"}
    )

    assert summary["config_key"] == "notify_failures"
    assert summary["title"].startswith("🌐")
    assert "失败任务：1" in summary["text"]
    assert "cookie" not in summary["text"]


def test_all_success_summaries_have_emoji_and_data_layout():
    cases = [
        ("targets", {"targets": 1, "found": 2, "eligible": 2, "downloads": []}),
        ("pool", {"found": 2, "eligible": 2, "errors": [], "downloads": []}),
        ("auto-download", {"requested": 5, "attempted": 7, "submitted": 5, "skipped": []}),
        ("downloads", {"submitted": 1, "failed": 0, "results": [{"success": True}]}),
    ]

    for task_name, result in cases:
        summary = MODULE.build_task_summary(task_name, "success", result)
        assert summary["title"][0] in "🔎🌐⬇️🧾"
        assert summary["text"].startswith("📊 数据汇总\n────────────")
        assert "\n🕒 完成时间：" in summary["text"]
