"""Data-only notification summaries for the plugin."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional


TASK_TITLES = {
    "inventory": "📚 Emby 库存",
    "targets": "🔎 目标搜索",
    "pool": "🌐 全站种子池",
    "auto-download": "⬇️ 自动下载",
    "downloads": "🧾 手动下载",
}


def build_task_summary(
    task_name: str,
    status: str,
    result: Optional[Mapping[str, Any]] = None,
    *,
    target: Optional[Mapping[str, Any]] = None,
    progress: Optional[Mapping[str, Any]] = None,
    finished_at: Optional[str] = None,
) -> Optional[dict]:
    """Build a compact summary containing aggregate data and no item details."""

    result = result if isinstance(result, Mapping) else {}
    progress = progress if isinstance(progress, Mapping) else {}
    target = target if isinstance(target, Mapping) else {}
    base_name = task_name.split(":", 1)[0]
    if base_name == "target-pool":
        label = str(target.get("title") or "目标清单")
        title = f"🎯 {label} · 目标汇总"
        config_key = "notify_targets"
    else:
        title = f"{TASK_TITLES.get(base_name, '📊 插件任务')}汇总"
        config_key = {
            "inventory": "notify_inventory",
            "targets": "notify_targets",
            "pool": "notify_pool",
            "auto-download": "notify_download",
            "downloads": "notify_download",
        }.get(base_name)
    if not config_key:
        return None

    if status != "success":
        return {
            "config_key": "notify_failures",
            "title": title.replace("汇总", "异常"),
            "text": _format_rows([
                ("✅", "完成任务", 0),
                ("❌", "失败任务", 1),
            ], finished_at),
        }

    if base_name == "inventory":
        target_stats = result.get("targets") if isinstance(result.get("targets"), Mapping) else {}
        webdl_stats = result.get("webdl_slots") if isinstance(result.get("webdl_slots"), Mapping) else {}
        rows = [
            ("🖥️", "Emby 服务", len(result.get("servers") or [])),
            ("🎞️", "库存版本", result.get("versions")),
            ("📋", "目标清单", target_stats.get("lists")),
            ("🎬", "目标影片", target_stats.get("items")),
            ("✅", "已入库", target_stats.get("present")),
            ("⏳", "待补影片", target_stats.get("missing")),
            ("🔁", "WEB-DL升级", webdl_stats.get("completed")),
            ("🧹", "清理旧版本", webdl_stats.get("cleaned_versions")),
            ("⚠️", "升级待处理", webdl_stats.get("failed")),
        ]
    elif base_name == "pool":
        rows = [
            ("🏷️", "分类任务", progress.get("total_sources") or result.get("total_sources")),
            ("📄", "扫描页数", progress.get("completed_pages") or result.get("completed_pages")),
            ("📦", "种子总数", result.get("found")),
            ("✅", "合格种子", result.get("eligible")),
            ("⚠️", "扫描错误", len(result.get("errors") or [])),
            ("⬇️", "提交下载", len(result.get("downloads") or [])),
        ]
    elif base_name == "target-pool":
        rows = [
            ("🎬", "清单影片", target.get("item_count") or len(target.get("items") or [])),
            ("✅", "已入库", target.get("in_library_count")),
            ("⏳", "待补影片", target.get("missing_count")),
            ("🔎", "匹配种子", result.get("matched")),
            ("⬇️", "提交下载", result.get("submitted")),
            ("⏭️", "跳过种子", len(result.get("skipped") or [])),
        ]
    elif base_name == "targets":
        rows = [
            ("📋", "目标清单", result.get("targets")),
            ("🔎", "发现种子", result.get("found")),
            ("✅", "合格种子", result.get("eligible")),
            ("⬇️", "提交下载", len(result.get("downloads") or [])),
        ]
    elif base_name == "auto-download":
        submitted = result.get("submitted")
        if submitted is None:
            submitted = sum(1 for item in result.get("results") or [] if item.get("success"))
        requested = result.get("requested")
        if requested is None:
            requested = len(result.get("results") or [])
        skipped = result.get("skipped") or []
        rows = [
            ("🎯", "计划数量", requested),
            ("🔍", "检查种子", result.get("attempted", requested)),
            ("✅", "提交成功", submitted),
            ("⏭️", "检查后跳过", len(skipped)),
        ]
        rows.extend(_skip_reason_rows(skipped))
    else:
        submitted = result.get("submitted")
        if submitted is None:
            submitted = sum(1 for item in result.get("results") or [] if item.get("success"))
        failed = result.get("failed")
        if failed is None:
            failed = len(result.get("skipped") or [])
        requested = result.get("requested")
        if requested is None:
            requested = len(result.get("results") or [])
        rows = [
            ("🎯", "计划数量", requested),
            ("🔍", "检查种子", result.get("attempted", requested)),
            ("✅", "提交成功", submitted),
            ("❌", "提交失败", failed),
        ]
    return {
        "config_key": config_key,
        "title": title,
        "text": _format_rows(rows, finished_at),
    }


def build_test_summary(finished_at: Optional[str] = None) -> dict:
    return {
        "title": "🔔 插件通知测试",
        "text": _format_rows([
            ("📡", "通知链路", 1),
            ("📊", "汇总模式", 1),
            ("✅", "测试结果", 1),
        ], finished_at),
    }


def _skip_reason_rows(skipped: list[Any]) -> list[tuple[str, str, int]]:
    counts = {
        "媒体识别失败": 0,
        "码率没有提升": 0,
        "相同槽位下载中": 0,
        "达到版本上限": 0,
        "其他原因": 0,
    }
    for item in skipped:
        message = str(item.get("message") or "") if isinstance(item, Mapping) else str(item or "")
        if "媒体信息识别失败" in message or "媒体识别失败" in message \
                or "无法建立媒体版本键" in message:
            label = "媒体识别失败"
        elif "码率" in message and ("未提升" in message or "没有提升" in message):
            label = "码率没有提升"
        elif "下载中" in message and ("槽位" in message or "相同质量" in message):
            label = "相同槽位下载中"
        elif "版本上限" in message:
            label = "达到版本上限"
        else:
            label = "其他原因"
        counts[label] += 1

    reasons = [(label, count) for label, count in counts.items() if count]
    return [
        ("  └─" if index == len(reasons) - 1 else "  ├─", label, count)
        for index, (label, count) in enumerate(reasons)
    ]


def _format_rows(rows: list[tuple[str, str, Any]], finished_at: Optional[str]) -> str:
    lines = ["📊 数据汇总", "────────────"]
    for emoji, label, value in rows:
        lines.append(f"{emoji} {label}：{_number(value)}")
    lines.extend(("────────────", f"🕒 完成时间：{_time_text(finished_at)}"))
    return "\n".join(lines)


def _number(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _time_text(value: Optional[str]) -> str:
    if value:
        try:
            return datetime.fromisoformat(str(value)).astimezone().strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
