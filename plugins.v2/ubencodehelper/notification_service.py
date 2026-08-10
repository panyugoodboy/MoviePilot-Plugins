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

    @staticmethod
    def client_update_text(release: dict, current_version: str) -> str:
        release = dict(release or {})
        latest_version = str(release.get("version") or "未知")
        lines = [
            f"发现 UBencode 客户端新版本：{latest_version}",
            f"当前版本：{str(current_version or '未知')}",
            "",
            "更新内容：",
        ]
        changelog = release.get("changelog") or []
        if isinstance(changelog, list):
            for item in changelog:
                if isinstance(item, dict):
                    version = str(item.get("version") or "").strip()
                    content = str(
                        item.get("content") or item.get("description") or item.get("text") or ""
                    ).strip()
                    line = f"{version}：{content}" if version and content else content or version
                else:
                    line = str(item or "").strip()
                if line:
                    lines.append(f"• {line}")
        if len(lines) == 4:
            lines.append("• 服务端未提供更新说明")
        return "\n".join(lines)

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
                f"视频码率：{cls.bitrate_text(payload)}",
                f"目标区间：{cls._target_range(payload)}",
                f"结果：{'命中目标' if payload.get('hit_target') else '继续调整'}",
                f"测压时长：{cls._duration(payload.get('sample_duration'))}",
            ])
        elif event_type in {"quick_test_completed", "precise_test_completed"}:
            lines.extend([
                f"最终 CRF：{cls._number(payload.get('crf'), 2)}",
                f"最终视频码率：{cls.bitrate_text(payload)}",
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
                f"视频码率：{cls.bitrate_text(payload)}",
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
                f"{cls.bitrate_text(payload)} · {payload.get('denoise_preset') or '关闭'}"
            )
        elif event_type in {"quick_test_completed", "precise_test_completed", "encode_completed"}:
            summary = f"CRF {cls._number(payload.get('crf'), 2)} · {cls.bitrate_text(payload)} · 已完成"
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

    @classmethod
    def bitrate_mbps(cls, payload: dict):
        data = payload if isinstance(payload, dict) else {}
        for key in ("bitrate_mbps", "video_bitrate_mbps"):
            value = cls._positive_number(data.get(key))
            if value is not None:
                return value
        for key in ("sample_bitrate", "video_bitrate", "encoded_video_bitrate"):
            value = cls._positive_number(data.get(key))
            if value is not None:
                return value / 1_000_000.0
        for key in ("estimated_video_bitrate_kbps", "encoded_video_bitrate_kbps"):
            value = cls._positive_number(data.get(key))
            if value is not None:
                return value / 1000.0
        source_mbps = cls._source_bitrate_mbps(data)
        percent = cls._positive_number(data.get("source_bitrate_percent"))
        if percent is None:
            percent = cls._positive_number(data.get("bitrate_percent"))
        if source_mbps is not None and percent is not None:
            return source_mbps * percent / 100.0
        return None

    @classmethod
    def bitrate_text(cls, payload: dict) -> str:
        value = cls.bitrate_mbps(payload)
        if value is not None:
            percent = cls.source_bitrate_percent(payload, value)
            percent_text = f"源码率的 {percent:.1f}%" if percent is not None else "源码率占比未知"
            return f"{value:.2f} Mbps（{percent_text}）"
        percent = cls.source_bitrate_percent(payload)
        if percent is not None:
            return f"源码率的 {percent:.1f}%（缺少源码率，无法换算 Mbps）"
        return "未提供"

    @classmethod
    def source_bitrate_percent(cls, payload: dict, bitrate_mbps=None):
        data = payload if isinstance(payload, dict) else {}
        for key in ("source_bitrate_percent", "bitrate_percent"):
            value = cls._positive_number(data.get(key))
            if value is not None:
                return value
        source_mbps = cls._source_bitrate_mbps(data)
        actual = bitrate_mbps if bitrate_mbps is not None else cls.bitrate_mbps(data)
        if actual is not None and source_mbps is not None and source_mbps > 0:
            return actual * 100.0 / source_mbps
        return None

    @classmethod
    def _target_range(cls, payload: dict) -> str:
        data = payload or {}
        target_min = cls._positive_number(data.get("target_min_mbps"))
        target_max = cls._positive_number(data.get("target_max_mbps"))
        source_mbps = cls._source_bitrate_mbps(data)
        if target_min is None or target_max is None:
            min_percent = cls._positive_number(data.get("target_min"))
            max_percent = cls._positive_number(data.get("target_max"))
            if min_percent is None or max_percent is None:
                return "未提供"
            if source_mbps is None:
                return f"源码率的 {min_percent:.1f}% - {max_percent:.1f}%（缺少源码率，无法换算 Mbps）"
            target_min = source_mbps * min_percent / 100.0
            target_max = source_mbps * max_percent / 100.0
        if source_mbps is not None:
            percent_text = f"源码率的 {target_min * 100.0 / source_mbps:.1f}% - {target_max * 100.0 / source_mbps:.1f}%"
        else:
            percent_text = "源码率占比未知"
        return f"{target_min:.1f} - {target_max:.1f} Mbps（{percent_text}）"

    @classmethod
    def _source_bitrate_mbps(cls, payload: dict):
        data = payload if isinstance(payload, dict) else {}
        for key in ("source_bitrate_mbps", "source_video_bitrate_mbps"):
            value = cls._positive_number(data.get(key))
            if value is not None:
                return value
        for key in ("source_bitrate", "source_video_bitrate"):
            value = cls._positive_number(data.get(key))
            if value is not None:
                return value / 1_000_000.0
        return None

    @staticmethod
    def _positive_number(value):
        try:
            number = float(value)
            return number if number > 0 else None
        except (TypeError, ValueError):
            return None

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
