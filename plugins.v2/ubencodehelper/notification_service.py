import time

from app.schemas import NotificationType


class NotificationService:
    EVENT_SWITCHES = {
        "quick_test_round_completed": "notify_quick_round",
        "quick_test_completed": "notify_quick_completed",
        "quick_test_failed": "notify_quick_failed",
        "precise_test_round_completed": "notify_precise_round",
        "precise_test_completed": "notify_precise_completed",
        "precise_test_failed": "notify_precise_failed",
        "encode_progress_checkpoint": "notify_encode_progress",
        "encode_completed": "notify_encode_completed",
        "encode_failed": "notify_encode_failed",
        "test_queue_completed": "notify_test_queue_completed",
        "encode_queue_completed": "notify_encode_queue_completed",
    }

    EVENT_TITLES = {
        "quick_test_started": "UBencode 快速测压开始",
        "quick_test_round_completed": "UBencode 快速测压结果",
        "quick_test_completed": "UBencode 快速测压完成",
        "quick_test_failed": "UBencode 快速测压失败",
        "precise_test_started": "UBencode 精准测压开始",
        "precise_test_round_completed": "UBencode 精准测压结果",
        "precise_test_completed": "UBencode 精准测压完成",
        "precise_test_failed": "UBencode 精准测压失败",
        "encode_started": "UBencode 正压开始",
        "encode_progress_checkpoint": "UBencode 正压进度",
        "encode_completed": "UBencode 正压完成",
        "encode_failed": "UBencode 正压失败",
        "test_queue_completed": "UBencode 测压队列完成",
        "encode_queue_completed": "UBencode 正压队列完成",
    }

    def __init__(self, plugin):
        self.plugin = plugin

    @classmethod
    def should_notify(cls, event: dict, config: dict) -> bool:
        switch = cls.EVENT_SWITCHES.get(str(event.get("event_type") or ""))
        return bool(switch and config.get(switch, False))

    def send_event(self, event: dict):
        event_type = str(event.get("event_type") or "")
        self.plugin.post_message(
            mtype=NotificationType.Plugin,
            title=self.EVENT_TITLES.get(event_type, "UBencode 状态更新"),
            text=self.event_text(event),
        )

    @classmethod
    def event_text(cls, event: dict) -> str:
        event_type = str(event.get("event_type") or "")
        payload = dict(event.get("payload") or {})
        title = str(payload.get("title") or "未命名影片")[:300]
        lines = [f"任务：{title}"]
        encoder = str(payload.get("encoder") or "").strip()
        if encoder:
            lines.append(f"编码器：{encoder}")
        if event_type.endswith("_round_completed"):
            lines.extend([
                f"轮次：{int(payload.get('round') or 0)}",
                f"CRF：{cls._number(payload.get('crf'), 2)}",
                f"滤镜：{payload.get('denoise_preset') or '关闭'}",
                f"码率比例：{cls._number(payload.get('bitrate_percent'), 1)}%",
                f"目标区间：{cls._number(payload.get('target_min'), 1)}% - {cls._number(payload.get('target_max'), 1)}%",
                f"结果：{'命中目标' if payload.get('hit_target') else '继续调整'}",
                f"测压时长：{cls._duration(payload.get('sample_duration'))}",
            ])
        elif event_type in {"quick_test_completed", "precise_test_completed"}:
            lines.extend([
                f"最终 CRF：{cls._number(payload.get('crf'), 2)}",
                f"最终码率比例：{cls._number(payload.get('bitrate_percent'), 1)}%",
                f"滤镜：{payload.get('denoise_preset') or '关闭'}",
                f"总耗时：{cls._duration(payload.get('elapsed_seconds'))}",
            ])
        elif event_type == "encode_progress_checkpoint":
            lines.extend([
                f"进度：{int(payload.get('progress') or 0)}%",
                f"速度：{cls._number(payload.get('fps'), 2)} fps",
                f"已用：{cls._duration(payload.get('elapsed_seconds'))}",
                f"预计剩余：{cls._duration(payload.get('eta_seconds'))}",
            ])
        elif event_type == "encode_completed":
            lines.extend([
                f"CRF：{cls._number(payload.get('crf'), 2)}",
                f"码率比例：{cls._number(payload.get('bitrate_percent'), 1)}%",
                f"平均速度：{cls._number(payload.get('avg_fps'), 2)} fps",
                f"总耗时：{cls._duration(payload.get('elapsed_seconds'))}",
            ])
        elif event_type.endswith("_failed"):
            lines.extend([
                f"错误：{str(payload.get('error') or '运行失败')[:300]}",
                f"已用：{cls._duration(payload.get('elapsed_seconds'))}",
            ])
        elif event_type.endswith("_queue_completed"):
            lines.extend([
                f"项目：{int(payload.get('item_count') or 0)}",
                f"完成：{int(payload.get('completed') or 0)}",
                f"失败：{int(payload.get('failed') or 0)}",
                f"总耗时：{cls._duration(payload.get('elapsed_seconds'))}",
            ])
        return "\n".join(lines)

    @classmethod
    def timeline_item(cls, event: dict, notified: bool) -> dict:
        payload = dict(event.get("payload") or {})
        event_type = str(event.get("event_type") or "")
        if event_type.endswith("_round_completed"):
            summary = (
                f"第 {int(payload.get('round') or 0)} 轮 · CRF {cls._number(payload.get('crf'), 2)} · "
                f"{cls._number(payload.get('bitrate_percent'), 1)}% · {payload.get('denoise_preset') or '关闭'}"
            )
        elif event_type in {"quick_test_completed", "precise_test_completed", "encode_completed"}:
            summary = f"CRF {cls._number(payload.get('crf'), 2)} · {cls._number(payload.get('bitrate_percent'), 1)}% · 已完成"
        elif event_type.endswith("_failed"):
            summary = str(payload.get("error") or "运行失败")[:160]
        elif event_type == "encode_progress_checkpoint":
            summary = f"进度 {int(payload.get('progress') or 0)}% · {cls._number(payload.get('fps'), 2)} fps"
        elif event_type.endswith("_queue_completed"):
            summary = f"完成 {int(payload.get('completed') or 0)} · 失败 {int(payload.get('failed') or 0)}"
        else:
            summary = "已开始"
        level = "error" if event_type.endswith("_failed") else "success" if event_type.endswith("_completed") else "info"
        icon = "mdi-alert-circle-outline" if level == "error" else "mdi-check-circle-outline" if level == "success" else "mdi-progress-clock"
        return {
            "id": int(event.get("id") or 0),
            "event_id": str(event.get("event_id") or ""),
            "event_type": event_type,
            "title": str(payload.get("title") or "未命名影片")[:300],
            "label": cls.EVENT_TITLES.get(event_type, "UBencode 状态更新").replace("UBencode ", ""),
            "summary": summary,
            "occurred_at": int(event.get("occurred_at") or event.get("created_at") or time.time()),
            "notified": bool(notified),
            "level": level,
            "icon": icon,
        }

    def sync(self, tasks: list[dict], username: str, config: dict) -> dict:
        previous = dict(self.plugin.get_data("task_snapshot") or {})
        current = {}
        username_lower = str(username or "").lower()
        for task in tasks:
            task_id = str(task.get("id") or "")
            if not task_id:
                continue
            assignees = {
                str(task.get("test_assignee") or "").lower(),
                str(task.get("encode_assignee") or "").lower(),
            }
            if username_lower and username_lower not in assignees:
                continue
            current[task_id] = str(task.get("status") or "")
        self.plugin.save_data("task_snapshot", current)
        self.plugin.save_data("last_sync", {"at": int(time.time()), "task_count": len(current)})
        return {"ok": True, "task_count": len(current), "changed": current != previous}

    @staticmethod
    def _number(value, digits: int) -> str:
        try:
            return f"{float(value):.{digits}f}"
        except (TypeError, ValueError):
            return "-"

    @staticmethod
    def _duration(value) -> str:
        try:
            seconds = max(0, int(float(value or 0)))
        except (TypeError, ValueError):
            return "-"
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours} 小时 {minutes} 分"
        if minutes:
            return f"{minutes} 分 {seconds} 秒"
        return f"{seconds} 秒"
