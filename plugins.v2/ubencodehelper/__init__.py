import json
import hashlib
import secrets
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Tuple

from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.core.event import Event, eventmanager
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType
from app.schemas.types import EventType

from .api_client import ApiClientError
from .auth_service import AuthService
from .downloader_service import DownloaderService
from .event_service import EventService
from .notification_service import NotificationService
from .schemas import BindRequest, ConfigCheckRequest, DownloaderCheckRequest, TaskActionRequest
from .ubits_service import UBitsService


class UBencodeHelper(_PluginBase):
    plugin_name = "UBencode 助手"
    plugin_desc = "绑定压制中心、领取任务并将 UBits 源种推送到 MoviePilot 下载器。"
    plugin_icon = "ffmpeg.png"
    plugin_version = "1.3.0"
    plugin_author = "panyugoodboy"
    author_url = "https://github.com/panyugoodboy/MoviePilot-Plugins"
    plugin_config_prefix = "ubencodehelper_"
    plugin_order = 30
    auth_level = 2

    DEFAULT_CRON = "*/2 * * * *"
    DEFAULT_CONFIG = {
        "enabled": False,
        "username": "",
        "downloader": "",
        "download_dir": "",
        "category": "UBencode",
        "tags": "UBencode,待压制",
        "cron": DEFAULT_CRON,
        "interactive_user_ids": "",
        "default_runtime_device": "",
        "runtime_page_size": 8,
        "notify_quick_round": True,
        "notify_quick_completed": True,
        "notify_quick_failed": True,
        "notify_precise_round": True,
        "notify_precise_completed": True,
        "notify_precise_failed": True,
        "notify_encode_progress": False,
        "notify_encode_completed": True,
        "notify_encode_failed": True,
        "notify_test_queue_completed": True,
        "notify_encode_queue_completed": True,
        "notify_abnormal": True,
    }

    _config: Dict[str, Any] = {}

    def init_plugin(self, config: dict = None):
        self._interaction_sessions = getattr(self, "_interaction_sessions", {})
        self._command_monitors = getattr(self, "_command_monitors", set())
        self._command_monitor_lock = getattr(self, "_command_monitor_lock", threading.Lock())
        self._stopping = False
        incoming = dict(config or {})
        if "notify_quick_completed" not in incoming and "notify_test_completed" in incoming:
            incoming["notify_quick_completed"] = bool(incoming.get("notify_test_completed"))
            incoming["notify_precise_completed"] = bool(incoming.get("notify_test_completed"))
        clean = dict(self.DEFAULT_CONFIG)
        clean.update({key: value for key, value in incoming.items() if key in self.DEFAULT_CONFIG})
        clean["username"] = str(clean.get("username") or "").strip()[:80]
        clean["downloader"] = str(clean.get("downloader") or "").strip()[:128]
        clean["download_dir"] = str(clean.get("download_dir") or "").strip()[:500]
        clean["category"] = str(clean.get("category") or "UBencode").strip()[:100]
        clean["tags"] = str(clean.get("tags") or "UBencode,待压制").strip()[:300]
        clean["cron"] = self._valid_cron(str(clean.get("cron") or ""))
        clean["interactive_user_ids"] = str(clean.get("interactive_user_ids") or "").strip()[:500]
        clean["default_runtime_device"] = str(clean.get("default_runtime_device") or "").strip()[:120]
        try:
            clean["runtime_page_size"] = min(10, max(3, int(clean.get("runtime_page_size") or 8)))
        except (TypeError, ValueError):
            clean["runtime_page_size"] = 8
        self._config = clean
        if incoming != clean:
            self.update_config(clean)

    @classmethod
    def _valid_cron(cls, value: str) -> str:
        try:
            CronTrigger.from_crontab(value)
            return value
        except (TypeError, ValueError):
            return cls.DEFAULT_CRON

    def get_state(self) -> bool:
        auth = AuthService(self)
        return bool(self._config.get("enabled") and auth.public_status(auth.auth_data()).get("ok"))

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return [
            {"cmd": "/控制", "event": EventType.PluginAction, "desc": "🎛️ 打开客户端互动控制菜单", "category": "UBencode", "data": {"action": "ub_control"}},
            {"cmd": "/帮助", "event": EventType.PluginAction, "desc": "📖 显示中文命令列表", "category": "UBencode", "data": {"action": "ub_help"}},
            {"cmd": "/压制状态", "event": EventType.PluginAction, "desc": "📊 查看客户端和队列状态", "category": "UBencode", "data": {"action": "ub_status"}},
            {"cmd": "/当前任务", "event": EventType.PluginAction, "desc": "🎬 查看正在执行的任务", "category": "UBencode", "data": {"action": "ub_current"}},
            {"cmd": "/查看队列", "event": EventType.PluginAction, "desc": "📋 查看测压、正压、混流或制种队列", "category": "UBencode", "data": {"action": "ub_queue"}},
            {"cmd": "/客户端", "event": EventType.PluginAction, "desc": "💻 查看同账号客户端", "category": "UBencode", "data": {"action": "ub_clients"}},
            {"cmd": "/最近记录", "event": EventType.PluginAction, "desc": "🕘 查看最近测压和正压记录", "category": "UBencode", "data": {"action": "ub_recent"}},
            {"cmd": "/任务中心", "event": EventType.PluginAction, "desc": "🗂️ 查看任务中心统计", "category": "UBencode", "data": {"action": "ub_tasks"}},
            {"cmd": "/随机截图", "event": EventType.PluginAction, "desc": "🖼️ 选择客户端项目并生成随机截图", "category": "UBencode", "data": {"action": "ub_screenshot"}},
            {"cmd": "/发布", "event": EventType.PluginAction, "desc": "📤 执行发布检查并确认发布", "category": "UBencode", "data": {"action": "ub_publish"}},
        ]

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {"path": "/captcha", "endpoint": self.api_captcha, "methods": ["GET"], "auth": "apikey", "summary": "获取验证码"},
            {"path": "/bind", "endpoint": self.api_bind, "methods": ["POST"], "auth": "apikey", "summary": "绑定压制中心账号"},
            {"path": "/logout", "endpoint": self.api_logout, "methods": ["POST"], "auth": "apikey", "summary": "解除绑定"},
            {"path": "/check-auth", "endpoint": self.api_check_auth, "methods": ["GET"], "auth": "apikey", "summary": "检查账号授权"},
            {"path": "/check-ubits", "endpoint": self.api_check_ubits, "methods": ["GET"], "auth": "apikey", "summary": "检查 UBits 配置"},
            {"path": "/check-downloader", "endpoint": self.api_check_downloader, "methods": ["POST"], "auth": "apikey", "summary": "检查下载器"},
            {"path": "/preflight", "endpoint": self.api_preflight, "methods": ["POST"], "auth": "apikey", "summary": "完整配置预检"},
            {"path": "/test-notification", "endpoint": self.api_test_notification, "methods": ["POST"], "auth": "apikey", "summary": "发送测试通知"},
            {"path": "/task-action", "endpoint": self.api_task_action, "methods": ["POST"], "auth": "apikey", "summary": "任务操作"},
        ]

    def get_service(self) -> List[Dict[str, Any]]:
        if not self.get_state():
            return []
        return [{
            "id": "UBencodeHelper.Sync",
            "name": "UBencode 助手状态同步",
            "trigger": CronTrigger.from_crontab(self._config.get("cron") or self.DEFAULT_CRON),
            "func": self.sync_task_status,
            "kwargs": {},
        }]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        auth = AuthService(self).auth_data()
        token = json.dumps(settings.API_TOKEN)
        endpoint = "/api/v1/plugin/UBencodeHelper"
        captcha_js = f"""async function() {{
            captcha_status = '正在获取验证码...';
            try {{
                const r = await fetch('{endpoint}/captcha?apikey=' + encodeURIComponent({token}));
                const d = await r.json();
                captcha_id = d.captcha_id || '';
                captcha_question = d.question || '验证码获取失败';
                captcha_status = d.message || '';
            }} catch (_) {{ captcha_status = '验证码获取失败'; }}
        }}"""
        bind_js = f"""async function() {{
            bind_status = '正在验证账号...'; bind_ok = false;
            const password = window.__ubencodeHelperPassword || '';
            const answer = window.__ubencodeHelperCaptchaAnswer || '';
            try {{
                const r = await fetch('{endpoint}/bind?apikey=' + encodeURIComponent({token}), {{
                    method: 'POST', headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{username: username || '', password, captcha_id: captcha_id || '', captcha_answer: answer}})
                }});
                const d = await r.json(); bind_ok = !!d.ok; bind_status = d.message || '绑定失败';
                if (d.ok) bound_username = d.username || username || '';
            }} catch (_) {{ bind_status = '绑定请求失败'; }}
            window.__ubencodeHelperPassword = ''; window.__ubencodeHelperCaptchaAnswer = '';
            captcha_id = ''; captcha_question = '请重新获取验证码';
        }}"""
        logout_js = f"""async function() {{
            try {{
                const r = await fetch('{endpoint}/logout?apikey=' + encodeURIComponent({token}), {{method: 'POST'}});
                const d = await r.json(); bind_ok = false; bind_status = d.message || '已解除绑定'; bound_username = '';
            }} catch (_) {{ bind_status = '解除绑定失败'; }}
        }}"""
        ubits_js = f"""async function() {{
            ubits_status = '正在检测 UBits...'; ubits_ok = false;
            try {{
                const r = await fetch('{endpoint}/check-ubits?apikey=' + encodeURIComponent({token}));
                const d = await r.json(); ubits_ok = !!d.ok; ubits_status = d.message || '检测失败';
                if (d.passkey_tail) ubits_status += '，Passkey 尾号 ••••' + d.passkey_tail;
            }} catch (_) {{ ubits_status = 'UBits 检测请求失败'; }}
        }}"""
        downloader_js = f"""async function() {{
            downloader_status = '正在检测下载器...'; downloader_ok = false;
            try {{
                const r = await fetch('{endpoint}/check-downloader?apikey=' + encodeURIComponent({token}), {{
                    method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{downloader: downloader || ''}})
                }});
                const d = await r.json(); downloader_ok = !!d.ok; downloader_status = d.message || '检测失败';
            }} catch (_) {{ downloader_status = '下载器检测请求失败'; }}
        }}"""
        preflight_js = f"""async function() {{
            preflight_status = '正在执行完整预检...'; preflight_ok = false;
            try {{
                const r = await fetch('{endpoint}/preflight?apikey=' + encodeURIComponent({token}), {{
                    method: 'POST', headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{downloader: downloader || '', cron: cron || ''}})
                }});
                const d = await r.json(); preflight_ok = !!d.ok; preflight_status = (d.results || []).join('；') || d.message || '预检失败';
            }} catch (_) {{ preflight_status = '完整预检请求失败'; }}
        }}"""
        notify_js = f"""async function() {{
            notification_status = '正在发送测试通知...';
            try {{
                const r = await fetch('{endpoint}/test-notification?apikey=' + encodeURIComponent({token}), {{method: 'POST'}});
                const d = await r.json(); notification_status = d.message || '测试通知已发送';
            }} catch (_) {{ notification_status = '测试通知发送失败'; }}
        }}"""

        form = [{
            "component": "VForm",
            "content": [
                {"component": "VAlert", "props": {
                    "type": "{{ bind_ok ? 'success' : 'info' }}",
                    "variant": "tonal",
                    "title": "{{ bind_ok ? '账号已绑定 · ' + bound_username : '压制中心账号未绑定' }}",
                    "text": "{{ bind_status }}",
                    "class": "mb-4",
                }},
                {"component": "VSheet", "props": {"class": "border rounded pa-4 mb-4"}, "content": [
                    self._section_title("账号绑定", "验证压制中心账号，密码只用于本次绑定", "mdi-account-key-outline"),
                    {"component": "VRow", "props": {"align": "center"}, "content": [
                        self._col(self._switch("enabled", "启用 UBencode 助手"), 3),
                        self._col(self._field("username", "压制中心账号"), 4),
                        self._col({"component": "VTextField", "props": {
                            "label": "压制中心密码", "type": "password", "autocomplete": "new-password",
                            "clearable": True, "hide-details": "auto",
                            "onUpdate:modelValue": "function(value){ window.__ubencodeHelperPassword = String(value || ''); }",
                        }}, 5),
                    ]},
                    {"component": "VRow", "props": {"align": "center"}, "content": [
                        self._col(self._button("获取验证码", "mdi-shield-refresh", captcha_js), 3),
                        self._col({"component": "VTextField", "props": {
                            "model": "captcha_question", "label": "验证码问题", "readonly": True, "hide-details": "auto",
                        }}, 4),
                        self._col({"component": "VTextField", "props": {
                            "label": "验证码答案", "autocomplete": "off", "hide-details": "auto",
                            "onUpdate:modelValue": "function(value){ window.__ubencodeHelperCaptchaAnswer = String(value || ''); }",
                        }}, 2),
                        self._col(self._button("验证并绑定", "mdi-account-check", bind_js, "primary"), 3),
                    ]},
                    self._alert("{{ captcha_status }}", "info"),
                ]},
                {"component": "VSheet", "props": {"class": "border rounded pa-4 mb-4"}, "content": [
                    self._section_title("站点与下载器", "自动读取 UBits 凭据，并使用 MoviePilot 已配置的下载器", "mdi-download-network-outline"),
                    {"component": "VRow", "props": {"align": "center"}, "content": [
                        self._col(self._button("检测 UBits", "mdi-cookie-check", ubits_js), 3),
                        self._col({"component": "VSelect", "props": {
                            "model": "downloader", "label": "下载器", "clearable": True,
                            "items": DownloaderService.options(), "hide-details": "auto",
                        }}, 6),
                        self._col(self._button("检测下载器", "mdi-lan-check", downloader_js), 3),
                    ]},
                    {"component": "VRow", "content": [
                        self._col(self._field("download_dir", "下载目录（留空使用下载器默认）"), 6),
                        self._col(self._field("category", "分类"), 3),
                        self._col(self._field("tags", "标签（逗号分隔）"), 3),
                    ]},
                    self._alert("{{ ubits_status }}", "{{ ubits_ok ? 'success' : 'info' }}"),
                    self._alert("{{ downloader_status }}", "{{ downloader_ok ? 'success' : 'info' }}"),
                ]},
                {"component": "VSheet", "props": {"class": "border rounded pa-4 mb-4"}, "content": [
                    self._section_title("通知配置", "按 CRON 拉取事件，并通过 MoviePilot 插件通知渠道发送", "mdi-bell-outline"),
                    {"component": "VRow", "content": [
                        self._col({"component": "VCronField", "props": {"model": "cron", "label": "事件同步 CRON"}}, 4),
                        self._col({"component": "div", "content": [
                            self._group_title("快速测压", "mdi-speedometer"),
                            self._switch("notify_quick_round", "每轮结果"),
                            self._switch("notify_quick_completed", "测压完成"),
                            self._switch("notify_quick_failed", "测压失败"),
                        ]}, 4),
                        self._col({"component": "div", "content": [
                            self._group_title("精准测压", "mdi-crosshairs-gps"),
                            self._switch("notify_precise_round", "每轮结果"),
                            self._switch("notify_precise_completed", "测压完成"),
                            self._switch("notify_precise_failed", "测压失败"),
                        ]}, 4),
                    ]},
                    {"component": "VDivider", "props": {"class": "my-3"}},
                    {"component": "VRow", "content": [
                        self._col({"component": "div", "content": [
                            self._group_title("正压通知", "mdi-movie-open-cog-outline"),
                            self._switch("notify_encode_progress", "每 10% 进度（默认关闭）"),
                            self._switch("notify_encode_completed", "正压完成"),
                            self._switch("notify_encode_failed", "正压失败"),
                        ]}, 4),
                        self._col({"component": "div", "content": [
                            self._group_title("队列摘要", "mdi-format-list-checks"),
                            self._switch("notify_test_queue_completed", "测压队列完成"),
                            self._switch("notify_encode_queue_completed", "正压队列完成"),
                        ]}, 4),
                        self._col({"component": "div", "content": [
                            self._group_title("运行异常", "mdi-alert-circle-outline"),
                            self._switch("notify_abnormal", "账号或同步异常"),
                        ]}, 4),
                    ]},
                ]},
                {"component": "VSheet", "props": {"class": "border rounded pa-4 mb-4"}, "content": [
                    self._section_title("通知互动", "使用中文命令查询 UBencode 客户端和队列状态", "mdi-message-text-outline"),
                    {"component": "VRow", "content": [
                        self._col(self._field("interactive_user_ids", "允许互动的通知用户 ID（逗号分隔，留空不限制）"), 5),
                        self._col(self._field("default_runtime_device", "默认客户端名称或设备 ID（留空取最近在线）"), 4),
                        self._col({"component": "VTextField", "props": {
                            "model": "runtime_page_size", "label": "每页任务数", "type": "number",
                            "min": 3, "max": 10, "hide-details": "auto",
                        }}, 3),
                    ]},
                    {"component": "VAlert", "props": {
                        "type": "info", "variant": "tonal", "density": "compact",
                        "text": "输入 /帮助 或直接发送“帮助”可查看完整中文命令列表。",
                    }},
                ]},
                {"component": "VRow", "props": {"align": "center"}, "content": [
                    self._col(self._button("发送测试通知", "mdi-bell-check-outline", notify_js), 3),
                    self._col(self._button("完整验证", "mdi-clipboard-check-outline", preflight_js, "primary"), 6),
                    self._col(self._button("解除绑定", "mdi-logout", logout_js, "error", "outlined"), 3),
                ]},
                self._alert("{{ notification_status }}", "info"),
                self._alert("{{ preflight_status }}", "{{ preflight_ok ? 'success' : 'warning' }}"),
            ],
        }]
        model = dict(self.DEFAULT_CONFIG)
        model.update(self._config)
        model.update({
            "captcha_id": "",
            "captcha_question": "请先获取验证码",
            "captcha_status": "",
            "bound_username": str(auth.get("username") or ""),
            "bind_ok": bool(auth.get("refresh_token")),
            "bind_status": AuthService.public_status(auth).get("message"),
            "ubits_ok": False,
            "ubits_status": "尚未检测 UBits",
            "downloader_ok": False,
            "downloader_status": "尚未检测下载器",
            "notification_status": "",
            "preflight_ok": False,
            "preflight_status": "保存配置前建议执行完整验证",
        })
        return form, model

    def get_page(self) -> List[dict]:
        auth_service = AuthService(self)
        try:
            auth_status = auth_service.verify()
            if not auth_status.get("ok"):
                return [self._alert(str(auth_status.get("message") or "账号授权无效"), "warning")]
            client = auth_service.client()
            token = auth_service.token()
            EventService(self).sync(client, token, self._config)
            tasks = client.tasks(token)
        except ApiClientError as exc:
            return [self._alert(str(exc), "error")]

        ubits_status = UBitsService().inspect()
        downloader_status = DownloaderService.inspect(str(self._config.get("downloader") or ""))
        last_sync = dict(self.get_data("last_sync") or {})
        recent_events = [dict(item) for item in list(self.get_data("recent_events") or []) if isinstance(item, dict)]
        contents: List[dict] = [
            {"component": "VSheet", "props": {"class": "border rounded pa-4 mb-4"}, "content": [
                {"component": "div", "props": {"class": "d-flex flex-wrap align-center justify-space-between ga-3 mb-3"}, "content": [
                    {"component": "div", "content": [
                        {"component": "div", "props": {"class": "text-h6 font-weight-bold"}, "text": "UBencode 助手"},
                        {"component": "div", "props": {"class": "text-body-2 text-medium-emphasis"}, "text": f"{len(tasks)} 个任务 · 事件同步与源种下载"},
                    ]},
                    {"component": "VChip", "props": {
                        "color": "success" if self.get_state() else "warning", "variant": "tonal", "prepend-icon": "mdi-link-variant",
                    }, "text": "运行中" if self.get_state() else "待启用"},
                ]},
                {"component": "VRow", "content": [
                    self._status_col("压制中心", auth_status.get("message"), auth_status.get("ok"), "mdi-account-check-outline"),
                    self._status_col("UBits", ubits_status.get("message"), ubits_status.get("ok"), "mdi-cookie-check-outline"),
                    self._status_col("下载器", downloader_status.get("message"), downloader_status.get("ok"), "mdi-download-network-outline"),
                    self._status_col("最近同步", self._format_ts(last_sync.get("at")), bool(last_sync.get("at")), "mdi-sync"),
                ]},
            ]},
            self._recent_events_panel(recent_events),
        ]
        page_message = dict(self.get_data("page_message") or {})
        if page_message.get("message"):
            contents.append(self._alert(str(page_message.get("message")), "success" if page_message.get("ok") else "warning"))

        username = str(auth_status.get("username") or "")
        contents.append(self._task_groups(self._task_categories(tasks, username), username))
        return contents

    def api_captcha(self) -> dict:
        try:
            data = AuthService(self).client().captcha()
            return {"ok": True, "captcha_id": data.get("captcha_id"), "question": data.get("question"), "message": "验证码已获取"}
        except ApiClientError as exc:
            return {"ok": False, "message": str(exc)}

    def api_bind(self, body: BindRequest) -> dict:
        try:
            result = AuthService(self).bind(body.username, body.password, body.captcha_id, body.captcha_answer)
            self._config["username"] = result.get("username") or body.username
            self.update_config(self._config)
            return result
        except ApiClientError as exc:
            return {"ok": False, "message": str(exc)}

    def api_logout(self) -> dict:
        return AuthService(self).logout()

    def api_check_auth(self) -> dict:
        try:
            return AuthService(self).verify()
        except ApiClientError as exc:
            return {"ok": False, "message": str(exc)}

    def api_check_ubits(self) -> dict:
        result = UBitsService().inspect()
        self.save_data("ubits_status", {key: value for key, value in result.items() if key not in {"cookie", "passkey"}})
        return result

    @staticmethod
    def api_check_downloader(body: DownloaderCheckRequest) -> dict:
        return DownloaderService.inspect(body.downloader)

    def api_preflight(self, body: ConfigCheckRequest) -> dict:
        results = []
        ok = True
        try:
            CronTrigger.from_crontab(body.cron)
            results.append("CRON 有效")
        except (TypeError, ValueError):
            ok = False
            results.append("CRON 无效")
        for checker in (self.api_check_auth, self.api_check_ubits):
            result = checker()
            ok = ok and bool(result.get("ok"))
            results.append(str(result.get("message") or "检查失败"))
        downloader = DownloaderService.inspect(body.downloader)
        ok = ok and bool(downloader.get("ok"))
        results.append(str(downloader.get("message") or "下载器检查失败"))
        return {"ok": ok, "results": results, "message": "完整预检通过" if ok else "完整预检未通过"}

    def api_test_notification(self) -> dict:
        self.post_message(
            mtype=NotificationType.Plugin,
            title="UBencode 助手测试通知",
            text="MoviePilot 通知链路工作正常。",
        )
        return {"ok": True, "message": "测试通知已提交到 MoviePilot"}

    def api_task_action(self, body: TaskActionRequest) -> dict:
        auth = AuthService(self)
        token = auth.token()
        if not token:
            return self._remember_result(False, "尚未绑定压制中心账号")
        try:
            if body.action in {"claim_test", "claim_test_download", "claim_encode", "claim_encode_download"}:
                kind = "test" if "test" in body.action else "encode"
                result = auth.client().claim(token, body.task_id, kind)
                if not result.get("ok"):
                    return self._remember_result(False, "任务领取失败，可能已被其他用户领取")
                if body.action.endswith("_download"):
                    return self._push_source(body.task_id, claimed=True)
                return self._remember_result(True, "任务领取成功")
            if body.action == "push_source":
                return self._push_source(body.task_id)
            if body.action == "cancel":
                result = auth.client().cancel(token, body.task_id)
                return self._remember_result(bool(result.get("ok")), "任务已取消领取" if result.get("ok") else "取消领取失败")
            if body.action == "show_result":
                payload = auth.client().task_payload(token, body.task_id)
                return self._remember_result(True, self._test_result_text(payload))
        except (ApiClientError, RuntimeError, ValueError) as exc:
            prefix = "任务已领取，但" if body.action.endswith("_download") else ""
            return self._remember_result(False, f"{prefix}{str(exc)}")
        except Exception as exc:
            logger.warning(f"UBencode 助手任务操作异常：{exc.__class__.__name__}")
            prefix = "任务已领取，但" if body.action.endswith("_download") else ""
            return self._remember_result(False, f"{prefix}操作失败，请检查插件配置后重试")
        return self._remember_result(False, "不支持的任务操作")

    def _push_source(self, task_id: int, claimed: bool = False) -> dict:
        auth = AuthService(self)
        payload = auth.client().task_payload(auth.token(), task_id)
        client_payload = dict(payload.get("client_payload") or {})
        source_url = str(client_payload.get("source_download_url") or "")
        if not source_url:
            raise RuntimeError("任务缺少源种下载地址")
        credentials = UBitsService().credentials()
        downloader = str(self._config.get("downloader") or "")
        if not downloader:
            raise RuntimeError("尚未选择下载器")
        result = DownloaderService(self).push(
            task_id=task_id,
            source_url=source_url,
            credentials=credentials,
            downloader_name=downloader,
            download_dir=str(self._config.get("download_dir") or ""),
            category=str(self._config.get("category") or "UBencode"),
            tags=str(self._config.get("tags") or "UBencode,待压制"),
        )
        message = str(result.get("message") or "源种推送完成")
        if claimed:
            message = f"任务领取成功，{message}"
        return self._remember_result(True, message)

    def sync_task_status(self):
        auth = AuthService(self)
        try:
            auth_status = auth.verify()
            if not auth_status.get("ok"):
                raise ApiClientError(str(auth_status.get("message") or "账号授权无效"))
            client = auth.client()
            token = auth.token()
            event_result = EventService(self).sync(client, token, self._config)
            tasks = client.tasks(token)
            task_result = NotificationService(self).sync(tasks, str(auth_status.get("username") or ""), self._config)
            return {"ok": True, "events": event_result, "tasks": task_result}
        except (ApiClientError, RuntimeError) as exc:
            logger.warning(f"UBencode 助手状态同步失败：{str(exc)[:200]}")
            if self._config.get("notify_abnormal", True):
                self.post_message(
                    mtype=NotificationType.Plugin,
                    title="UBencode 助手同步异常",
                    text=str(exc)[:200],
                )
            return {"ok": False, "message": str(exc)}

    def stop_service(self):
        self._stopping = True

    @staticmethod
    def _help_text() -> str:
        return "\n".join([
            "🎬 UBencode 助手",
            "",
            "🎛️ 互动控制",
            "/控制",
            "/随机截图",
            "/发布",
            "  多客户端下先选择目标客户端",
            "",
            "📊 运行状态",
            "/压制状态 [客户端]",
            "  在线状态、当前进度和队列数量",
            "/当前任务 [客户端]",
            "  当前测压、正压、混流或制种详情",
            "/客户端",
            "  同账号客户端和最后在线时间",
            "",
            "📋 队列与记录",
            "/查看队列 [全部|测压|正压|混流|制种] [页码] [客户端]",
            "/最近记录",
            "/任务中心",
            "",
            "💡 使用示例",
            "/查看队列 正压 2",
            "/压制状态 工作机",
            "",
            "输入 /帮助 或“帮助”可再次查看。",
        ])

    def _interaction_allowed(self, event_data: dict) -> bool:
        configured = {
            item.strip().lower()
            for item in str(self._config.get("interactive_user_ids") or "").replace("，", ",").split(",")
            if item.strip()
        }
        if not configured:
            return True
        user_id = str(event_data.get("userid") or event_data.get("user") or "").strip().lower()
        return bool(user_id and user_id in configured)

    def _reply_interaction(self, event_data: dict, title: str, text: str, buttons=None):
        channel = event_data.get("channel")
        user_id = event_data.get("userid") or event_data.get("user")
        if channel:
            kwargs = {
                "channel": channel,
                "title": title,
                "text": text,
                "userid": user_id,
            }
            if buttons:
                kwargs["buttons"] = buttons
            if event_data.get("original_message_id"):
                kwargs["original_message_id"] = event_data.get("original_message_id")
            if event_data.get("original_chat_id"):
                kwargs["original_chat_id"] = event_data.get("original_chat_id")
            self.post_message(**kwargs)
        else:
            self.post_message(mtype=NotificationType.Plugin, title=title, text=text)

    @classmethod
    def _callback_button(cls, text: str, action: str) -> dict:
        return {"text": text, "callback_data": f"[PLUGIN]{cls.__name__}|{action}"}

    @staticmethod
    def _interaction_key(event_data: dict) -> str:
        channel = str(event_data.get("channel") or "default")
        user_id = str(event_data.get("userid") or event_data.get("user") or "default")
        return f"{channel}:{user_id}"

    def _interaction_session(self, event_data: dict) -> dict:
        now = int(time.time())
        key = self._interaction_key(event_data)
        self._interaction_sessions = {
            name: value for name, value in dict(getattr(self, "_interaction_sessions", {}) or {}).items()
            if now - int((value or {}).get("updated_at") or 0) <= 1800
        }
        session = dict(self._interaction_sessions.get(key) or {})
        session["updated_at"] = now
        self._interaction_sessions[key] = session
        return session

    def _save_interaction_session(self, event_data: dict, session: dict):
        session = dict(session or {})
        session["updated_at"] = int(time.time())
        self._interaction_sessions[self._interaction_key(event_data)] = session

    def _interactive_api(self):
        auth = AuthService(self)
        status = auth.verify()
        if not status.get("ok"):
            raise ApiClientError(str(status.get("message") or "压制中心账号未绑定"))
        return auth.client(), auth.token()

    @staticmethod
    def _age_text(seconds: Any) -> str:
        try:
            value = max(0, int(seconds or 0))
        except (TypeError, ValueError):
            value = 0
        if value < 60:
            return f"{value} 秒前"
        if value < 3600:
            return f"{value // 60} 分钟前"
        if value < 86400:
            return f"{value // 3600} 小时前"
        return f"{value // 86400} 天前"

    @staticmethod
    def _duration_text(seconds: Any) -> str:
        try:
            value = max(0, int(float(seconds or 0)))
        except (TypeError, ValueError):
            value = 0
        hours, remainder = divmod(value, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours}小时{minutes}分"
        if minutes:
            return f"{minutes}分{secs}秒"
        return f"{secs}秒"

    @staticmethod
    def _runtime_status_label(status: str) -> str:
        return {
            "idle": "空闲",
            "paused": "已暂停",
            "test": "测压中",
            "encode": "正压中",
            "mix": "混流中",
            "torrent": "制种中",
        }.get(str(status or ""), str(status or "未知"))

    @staticmethod
    def _queue_label(queue_type: str) -> str:
        return {"test": "测压", "encode": "正压", "mix": "混流", "torrent": "制种"}.get(queue_type, queue_type)

    @staticmethod
    def _queue_status_label(status: str) -> str:
        return {
            "pending": "等待",
            "ready": "待压制",
            "testing": "测压中",
            "encoding": "正压中",
            "mixing": "混流中",
            "torrenting": "制种中",
            "done": "完成",
            "failed": "失败",
        }.get(str(status or ""), str(status or "未知"))

    def _resolve_runtime_device(self, client, token: str, requested: str = "") -> str:
        query = str(requested or self._config.get("default_runtime_device") or "").strip()
        if not query:
            return ""
        clients = client.runtime_clients(token)
        exact = [item for item in clients if query.lower() in {
            str(item.get("device_id") or "").lower(), str(item.get("device_name") or "").lower()
        }]
        if len(exact) == 1:
            return str(exact[0].get("device_id") or "")
        partial = [item for item in clients if query.lower() in str(item.get("device_name") or "").lower()]
        if len(partial) == 1:
            return str(partial[0].get("device_id") or "")
        if not exact and not partial:
            raise ApiClientError(f"未找到客户端：{query}")
        raise ApiClientError(f"客户端名称不唯一：{query}，请使用 /客户端 查看设备 ID")

    def _format_runtime_status(self, client: dict | None, current_only: bool = False) -> str:
        if not client:
            return "尚未收到 UBencode 客户端状态。请先登录并打开客户端。"
        snapshot = client.get("snapshot") if isinstance(client.get("snapshot"), dict) else {}
        current = snapshot.get("current") if isinstance(snapshot.get("current"), dict) else {}
        device_name = str(client.get("device_name") or client.get("device_id") or "未命名客户端")
        online = bool(client.get("online"))
        lines = [
            f"客户端：{device_name}",
            f"连接：{'在线' if online else '离线'}，状态更新于 {self._age_text(client.get('stale_seconds'))}",
            f"状态：{self._runtime_status_label(snapshot.get('status'))}",
        ]
        if current:
            queue_type = str(current.get("queue_type") or "")
            stage = str(current.get("stage") or "")
            stage_text = "快速测压" if stage == "quick" else "精准测压" if stage == "precise" else self._queue_label(queue_type)
            lines.extend([
                f"当前：{current.get('title') or '未命名影片'}",
                f"阶段：{stage_text}，进度 {int(current.get('progress') or 0)}%",
            ])
            metrics = []
            if current.get("fps") is not None:
                metrics.append(f"{current.get('fps')} fps")
            if current.get("elapsed_seconds") is not None:
                metrics.append(f"已用 {self._duration_text(current.get('elapsed_seconds'))}")
            if current.get("eta"):
                metrics.append(f"预计剩余 {current.get('eta')}")
            if metrics:
                lines.append("，".join(metrics))
            if current.get("detail"):
                lines.append(f"详情：{str(current.get('detail'))[:220]}")
        else:
            lines.append("当前：没有正在执行的任务")
        if not current_only:
            counts = snapshot.get("queue_counts") if isinstance(snapshot.get("queue_counts"), dict) else {}
            lines.append(
                "队列："
                f"测压 {int(counts.get('test') or 0)}，正压 {int(counts.get('encode') or 0)}，"
                f"混流 {int(counts.get('mix') or 0)}，制种 {int(counts.get('torrent') or 0)}"
            )
        return "\n".join(lines)

    def _parse_queue_args(self, arg_str: str) -> tuple[str, int, str]:
        aliases = {
            "全部": "all", "测压": "test", "正压": "encode", "混流": "mix", "制种": "torrent",
            "all": "all", "test": "test", "encode": "encode", "mix": "mix", "torrent": "torrent",
        }
        tokens = [item for item in str(arg_str or "").strip().split() if item]
        queue_type = "all"
        page = 1
        device_tokens = []
        for token in tokens:
            lowered = token.lower()
            if lowered in aliases and queue_type == "all":
                queue_type = aliases[lowered]
            elif token.isdigit() and page == 1:
                page = max(1, int(token))
            else:
                device_tokens.append(token.removeprefix("设备=").removeprefix("客户端="))
        return queue_type, page, " ".join(device_tokens).strip()

    def _format_runtime_queues(self, result: dict, queue_type: str, page: int, page_size: int) -> str:
        client = result.get("client") if isinstance(result.get("client"), dict) else None
        if not client:
            return "尚未收到 UBencode 客户端队列。"
        queues = result.get("queues") if isinstance(result.get("queues"), dict) else {}
        counts = result.get("queue_counts") if isinstance(result.get("queue_counts"), dict) else {}
        device_name = str(client.get("device_name") or client.get("device_id") or "未命名客户端")
        lines = [f"客户端：{device_name}"]
        if queue_type == "all":
            lines.append(
                f"队列总数：测压 {int(counts.get('test') or 0)}，正压 {int(counts.get('encode') or 0)}，"
                f"混流 {int(counts.get('mix') or 0)}，制种 {int(counts.get('torrent') or 0)}"
            )
        shown = 0
        for key in ("test", "encode", "mix", "torrent"):
            if queue_type != "all" and key != queue_type:
                continue
            items = list(queues.get(key) or [])
            if not items:
                if queue_type != "all":
                    lines.extend([f"\n{self._queue_label(key)}队列：", "暂无任务"])
                continue
            lines.append(f"\n{self._queue_label(key)}队列：")
            for item in items:
                if queue_type == "all" and shown >= page_size:
                    break
                number = (page - 1) * page_size + shown + 1
                status = self._queue_status_label(item.get("status"))
                progress = f" {int(item.get('progress') or 0)}%" if item.get("progress") is not None else ""
                detail = []
                if item.get("crf") is not None:
                    detail.append(f"CRF {item.get('crf')}")
                if item.get("denoise_preset") and item.get("denoise_preset") != "关闭":
                    detail.append(f"滤镜 {item.get('denoise_preset')}")
                if item.get("bitrate_percent") is not None:
                    detail.append(f"码率 {item.get('bitrate_percent')}%")
                suffix = f" | {'，'.join(detail)}" if detail else ""
                lines.append(f"{number}. [{status}{progress}] {item.get('title') or '未命名影片'}{suffix}")
                shown += 1
            if queue_type == "all" and shown >= page_size:
                break
        total = (
            sum(int(counts.get(key) or 0) for key in ("test", "encode", "mix", "torrent"))
            if queue_type == "all"
            else int(counts.get(queue_type) or 0)
        )
        total_pages = max(1, (total + page_size - 1) // page_size)
        lines.append(f"\n第 {page}/{total_pages} 页，共 {total} 项")
        return "\n".join(lines)

    def _format_runtime_clients(self, clients: list[dict]) -> str:
        if not clients:
            return "尚未发现 UBencode 客户端。"
        lines = ["UBencode 客户端"]
        for index, item in enumerate(clients, 1):
            name = str(item.get("device_name") or item.get("device_id") or "未命名客户端")
            state = "在线" if item.get("online") else "离线"
            status = self._runtime_status_label(item.get("status"))
            counts = item.get("queue_counts") if isinstance(item.get("queue_counts"), dict) else {}
            lines.append(
                f"{index}. {name} | {state}，{status}，{self._age_text(item.get('stale_seconds'))}\n"
                f"   设备 ID：{item.get('device_id') or '-'}\n"
                f"   队列：测压 {int(counts.get('test') or 0)} / 正压 {int(counts.get('encode') or 0)} / "
                f"混流 {int(counts.get('mix') or 0)} / 制种 {int(counts.get('torrent') or 0)}"
            )
        return "\n".join(lines)

    @staticmethod
    def _control_client_label(item: dict) -> str:
        name = str(item.get("device_name") or "未命名客户端")
        suffix = str(item.get("device_id") or "")[-6:].upper()
        return f"{name} · {suffix}" if suffix else name

    def _resolve_control_client(self, event_data: dict, client, token: str):
        clients = list(client.runtime_clients(token) or [])
        online = [item for item in clients if item.get("online")]
        session = self._interaction_session(event_data)
        selected_id = str(session.get("target_device_id") or "")
        selected = next((item for item in online if str(item.get("device_id") or "") == selected_id), None)
        if selected:
            return selected, clients
        configured = str(self._config.get("default_runtime_device") or "").strip().lower()
        if configured:
            hits = [
                item for item in online
                if configured in {
                    str(item.get("device_id") or "").lower(),
                    str(item.get("device_name") or "").lower(),
                }
            ]
            if len(hits) == 1:
                selected = hits[0]
        if not selected and len(online) == 1:
            selected = online[0]
        if selected:
            session["target_device_id"] = str(selected.get("device_id") or "")
            self._save_interaction_session(event_data, session)
        return selected, clients

    def _send_client_picker(self, event_data: dict, client, token: str):
        clients = list(client.runtime_clients(token) or [])
        session = self._interaction_session(event_data)
        session["client_choices"] = [str(item.get("device_id") or "") for item in clients]
        self._save_interaction_session(event_data, session)
        lines = ["请选择要操作的 UBencode 客户端："]
        buttons = []
        for index, item in enumerate(clients):
            label = self._control_client_label(item)
            state = "🟢 在线" if item.get("online") else "⚫ 离线"
            lines.append(f"{index + 1}. {label} | {state} | {self._runtime_status_label(item.get('status'))}")
            if item.get("online"):
                buttons.append([self._callback_button(f"💻 {label}", f"c:{index}")])
        if not clients:
            lines.append("尚未发现客户端，请先打开并登录 UBencode。")
        buttons.append([self._callback_button("🔙 返回控制中心", "m")])
        self._reply_interaction(event_data, "💻 选择客户端", "\n".join(lines), buttons)

    def _send_control_menu(self, event_data: dict, client, token: str):
        selected, clients = self._resolve_control_client(event_data, client, token)
        if not selected and len([item for item in clients if item.get("online")]) > 1:
            self._send_client_picker(event_data, client, token)
            return
        if not selected:
            self._send_client_picker(event_data, client, token)
            return
        status = client.runtime_status(token, str(selected.get("device_id") or ""))
        runtime = status.get("client") if isinstance(status.get("client"), dict) else {}
        snapshot = runtime.get("snapshot") if isinstance(runtime.get("snapshot"), dict) else {}
        capabilities = set(snapshot.get("capabilities") or [])
        text = "\n".join([
            f"当前客户端：{self._control_client_label(selected)}",
            f"状态：{self._runtime_status_label(snapshot.get('status'))}",
            "请选择操作：",
        ])
        buttons = [
            [
                self._callback_button("📊 查看状态", "st"),
                self._callback_button("📋 查看队列", "q"),
            ],
        ]
        operation_row = []
        if "generate_screenshots" in capabilities:
            operation_row.append(self._callback_button("🖼️ 随机截图", "s:1"))
        if "prepare_publish" in capabilities:
            operation_row.append(self._callback_button("📤 发布准备", "p:1"))
        if operation_row:
            buttons.append(operation_row)
        buttons.append([
            self._callback_button("💻 切换客户端", "clients"),
            self._callback_button("🕘 最近记录", "recent"),
        ])
        if not capabilities:
            text += "\n远程操作未启用，请在客户端截图发布页开启。"
        self._reply_interaction(event_data, "🎛️ UBencode 控制中心", text, buttons)

    def _send_operation_items(self, event_data: dict, client, token: str, mode: str, page: int = 1):
        selected, clients = self._resolve_control_client(event_data, client, token)
        if not selected:
            self._send_client_picker(event_data, client, token)
            return
        status = client.runtime_status(token, str(selected.get("device_id") or ""))
        runtime = status.get("client") if isinstance(status.get("client"), dict) else {}
        snapshot = runtime.get("snapshot") if isinstance(runtime.get("snapshot"), dict) else {}
        action = "generate_screenshots" if mode == "screenshot" else "prepare_publish"
        items = [item for item in list(snapshot.get("publish_items") or []) if action in set(item.get("actions") or [])]
        page_size = 6
        total_pages = max(1, (len(items) + page_size - 1) // page_size)
        page = min(total_pages, max(1, int(page or 1)))
        session = self._interaction_session(event_data)
        session["operation_items"] = items
        session["operation_mode"] = mode
        session["target_device_id"] = str(selected.get("device_id") or "")
        self._save_interaction_session(event_data, session)
        start = (page - 1) * page_size
        buttons = []
        for index in range(start, min(len(items), start + page_size)):
            item = items[index]
            title = str(item.get("title") or "未命名影片")
            compact = title if len(title) <= 34 else title[:33] + "…"
            icon = "🖼️" if mode == "screenshot" else "📤"
            callback = f"si:{index}" if mode == "screenshot" else f"pi:{index}"
            buttons.append([self._callback_button(f"{icon} {compact}", callback)])
        pager = []
        prefix = "s" if mode == "screenshot" else "p"
        if page > 1:
            pager.append(self._callback_button("⬅️ 上一页", f"{prefix}:{page - 1}"))
        if page < total_pages:
            pager.append(self._callback_button("下一页 ➡️", f"{prefix}:{page + 1}"))
        if pager:
            buttons.append(pager)
        buttons.append([self._callback_button("🔙 返回控制中心", "m")])
        title = "🖼️ 选择随机截图项目" if mode == "screenshot" else "📤 选择发布项目"
        text = (
            f"客户端：{self._control_client_label(selected)}\n"
            f"第 {page}/{total_pages} 页，共 {len(items)} 个可操作项目"
        )
        if not items:
            text += "\n当前客户端没有符合条件的已完成混流项目。"
        self._reply_interaction(event_data, title, text, buttons)

    def _remote_idempotency_key(self, event_data: dict, action: str, item_id: str, parameters: dict, attempt: int) -> str:
        raw = "|".join([
            self._interaction_key(event_data),
            str(event_data.get("original_message_id") or event_data.get("text") or ""),
            action,
            item_id,
            str(max(1, int(attempt or 1))),
            json.dumps(parameters or {}, ensure_ascii=False, sort_keys=True),
        ])
        return "mp-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]

    def _create_remote_action(
        self,
        event_data: dict,
        client,
        token: str,
        action: str,
        item: dict,
        parameters: dict = None,
    ):
        session = self._interaction_session(event_data)
        target_device_id = str(session.get("target_device_id") or "")
        item_id = str(item.get("item_id") or "")
        parameters = dict(parameters or {})
        if not target_device_id or not item_id:
            raise ApiClientError("客户端或项目选择已失效，请重新选择")
        active_command_id = str(session.get("active_command_id") or "")
        if active_command_id:
            current = client.remote_command(token, active_command_id).get("command") or {}
            if str(current.get("status") or "") not in {"success", "failed", "cancelled", "expired"}:
                self._reply_interaction(event_data, "⏳ 指令执行中", "当前操作仍在执行，请等待完成。")
                self._monitor_remote_command(dict(event_data), active_command_id)
                return
            session.pop("active_command_id", None)
        attempt = int(session.get("command_attempt") or 0) + 1
        session["command_attempt"] = attempt
        result = client.create_remote_command(
            token,
            target_device_id,
            action,
            item_id,
            parameters,
            self._remote_idempotency_key(event_data, action, item_id, parameters, attempt),
        )
        command = result.get("command") if isinstance(result.get("command"), dict) else {}
        command_id = str(command.get("command_id") or "")
        if not command_id:
            raise ApiClientError("压制中心未返回远程指令编号")
        session["active_item"] = item
        session["active_action"] = action
        session["active_command_id"] = command_id
        self._save_interaction_session(event_data, session)
        self._reply_interaction(
            event_data,
            "✅ 指令已发送",
            f"客户端已收到操作请求。\n影片：{item.get('title') or '未命名影片'}\n指令：{command_id[-8:]}",
            [[self._callback_button("🔙 返回控制中心", "m")]],
        )
        self._monitor_remote_command(dict(event_data), command_id)

    def _monitor_remote_command(self, event_data: dict, command_id: str):
        with self._command_monitor_lock:
            if command_id in self._command_monitors:
                return
            self._command_monitors.add(command_id)

        def run():
            last_bucket = -1
            try:
                client, token = self._interactive_api()
                deadline = time.time() + 1800
                while time.time() < deadline and not self._stopping:
                    data = client.remote_command(token, command_id)
                    command = data.get("command") if isinstance(data.get("command"), dict) else {}
                    status = str(command.get("status") or "")
                    progress = int(command.get("progress") or 0)
                    bucket = min(3, progress // 25)
                    if status == "running" and bucket > last_bucket and bucket > 0:
                        last_bucket = bucket
                        self._reply_interaction(
                            event_data,
                            "⏳ UBencode 正在执行",
                            f"进度：{progress}%\n{command.get('message') or '客户端处理中'}",
                        )
                    if status in {"success", "failed", "cancelled", "expired"}:
                        self._render_remote_command_result(event_data, command)
                        return
                    time.sleep(3)
                self._reply_interaction(event_data, "⚠️ 远程操作超时", "长时间没有收到完成结果，请查看客户端状态。")
            except Exception as exc:
                self._reply_interaction(event_data, "⚠️ 远程操作查询失败", str(exc)[:500])
            finally:
                with self._command_monitor_lock:
                    self._command_monitors.discard(command_id)

        threading.Thread(target=run, name=f"UBencodeRemote-{command_id[-8:]}", daemon=True).start()

    def _render_remote_command_result(self, event_data: dict, command: dict):
        action = str(command.get("action") or "")
        status = str(command.get("status") or "")
        result = command.get("result") if isinstance(command.get("result"), dict) else {}
        session = self._interaction_session(event_data)
        session.pop("active_command_id", None)
        self._save_interaction_session(event_data, session)
        item = dict(session.get("active_item") or {})
        if status != "success":
            buttons = []
            if result.get("error_code") == "output_conflict" and item:
                buttons.append([
                    self._callback_button("🧹 清理后继续", "sc:c"),
                    self._callback_button("➕ 保留继续", "sc:k"),
                ])
            buttons.append([self._callback_button("🔙 返回控制中心", "m")])
            self._reply_interaction(
                event_data,
                "❌ 远程操作失败",
                str(command.get("message") or status or "执行失败"),
                buttons,
            )
            return
        if action == "generate_screenshots":
            text = f"已生成 {int(result.get('pair_count') or 0)} 组随机对比截图。"
            buttons = [[
                self._callback_button("☁️ 上传图床", "u"),
                self._callback_button("📤 发布准备", "prep"),
            ]]
        elif action == "upload_screenshots":
            text = "截图已上传图床，可以继续执行发布检查。"
            buttons = [[self._callback_button("📤 发布准备", "prep")]]
        elif action == "prepare_publish":
            issues = [str(value) for value in list(result.get("issues") or []) if str(value)]
            text = "\n".join([
                f"标题：{result.get('title') or '-'}",
                f"截图：{int(result.get('screenshot_count') or 0)} 组",
                f"种子：{'已准备' if result.get('torrent_ready') else '未准备'}",
                f"MediaInfo：{'已准备' if result.get('mediainfo_ready') else '未准备'}",
            ])
            buttons = []
            if result.get("ready") and result.get("confirm_token"):
                session["confirm_token"] = str(result.get("confirm_token") or "")
                self._save_interaction_session(event_data, session)
                text += "\n\n发布检查已通过，请确认是否发布。"
                buttons.append([self._callback_button("✅ 确认发布", "pc")])
            else:
                text += "\n\n缺失项：" + ("；".join(issues) if issues else "未知")
                buttons.append([
                    self._callback_button("🖼️ 随机截图", "shot"),
                    self._callback_button("☁️ 上传图床", "u"),
                ])
        elif action == "confirm_publish":
            url = str(result.get("detail_url") or "")
            text = "UBits 发布完成。" + (f"\n{url}" if url else "")
            buttons = []
        else:
            text = str(command.get("message") or "远程操作完成")
            buttons = []
        buttons.append([self._callback_button("🔙 返回控制中心", "m")])
        self._reply_interaction(event_data, "✅ 远程操作完成", text, buttons)

    @eventmanager.register(EventType.MessageAction)
    def handle_remote_message_action(self, event: Event):
        event_data = dict(event.event_data or {})
        if str(event_data.get("plugin_id") or "") != self.__class__.__name__:
            return
        action = str(event_data.get("text") or "")
        if not self._interaction_allowed(event_data):
            self._reply_interaction(event_data, "UBencode 助手", "当前通知用户没有操作权限。")
            return
        if not self._config.get("enabled"):
            self._reply_interaction(event_data, "UBencode 助手", "插件尚未启用。")
            return
        try:
            client, token = self._interactive_api()
            session = self._interaction_session(event_data)
            if action == "m":
                self._send_control_menu(event_data, client, token)
            elif action == "clients":
                self._send_client_picker(event_data, client, token)
            elif action.startswith("c:"):
                index = int(action.split(":", 1)[1])
                choices = list(session.get("client_choices") or [])
                if index < 0 or index >= len(choices):
                    raise ValueError("客户端选择已过期")
                session["target_device_id"] = str(choices[index])
                self._save_interaction_session(event_data, session)
                self._send_control_menu(event_data, client, token)
            elif action == "st":
                target = str(session.get("target_device_id") or "")
                result = client.runtime_status(token, target)
                self._reply_interaction(
                    event_data, "📊 压制状态", self._format_runtime_status(result.get("client")),
                    [[self._callback_button("🔙 返回控制中心", "m")]],
                )
            elif action == "q":
                target = str(session.get("target_device_id") or "")
                page_size = int(self._config.get("runtime_page_size") or 8)
                result = client.runtime_queues(token, "all", 1, page_size, target)
                self._reply_interaction(
                    event_data, "📋 UBencode 队列", self._format_runtime_queues(result, "all", 1, page_size),
                    [[self._callback_button("🔙 返回控制中心", "m")]],
                )
            elif action == "recent":
                EventService(self).sync(client, token, self._config)
                self._reply_interaction(event_data, "🕘 最近记录", "已刷新最近记录，请使用 /最近记录 查看。")
            elif action.startswith("s:"):
                self._send_operation_items(event_data, client, token, "screenshot", int(action.split(":", 1)[1]))
            elif action.startswith("p:"):
                self._send_operation_items(event_data, client, token, "publish", int(action.split(":", 1)[1]))
            elif action.startswith(("si:", "pi:")):
                index = int(action.split(":", 1)[1])
                items = list(session.get("operation_items") or [])
                if index < 0 or index >= len(items):
                    raise ValueError("项目选择已过期")
                item = dict(items[index])
                session["active_item"] = item
                self._save_interaction_session(event_data, session)
                if action.startswith("si:"):
                    if int(item.get("existing_screenshot_files") or 0) > 0:
                        self._reply_interaction(
                            event_data,
                            "🖼️ 截图输出确认",
                            "检测到截图目录里已有同影片文件，请选择处理方式。",
                            [[
                                self._callback_button("🧹 清理后继续", "sc:c"),
                                self._callback_button("➕ 保留继续", "sc:k"),
                            ], [self._callback_button("❌ 取消", "m")]],
                        )
                    else:
                        self._create_remote_action(event_data, client, token, "generate_screenshots", item, {"conflict_policy": "keep"})
                else:
                    self._create_remote_action(event_data, client, token, "prepare_publish", item)
            elif action in {"sc:c", "sc:k", "shot"}:
                item = dict(session.get("active_item") or {})
                policy = "clear" if action == "sc:c" else "keep"
                self._create_remote_action(event_data, client, token, "generate_screenshots", item, {"conflict_policy": policy})
            elif action == "u":
                self._create_remote_action(event_data, client, token, "upload_screenshots", dict(session.get("active_item") or {}))
            elif action == "prep":
                self._create_remote_action(event_data, client, token, "prepare_publish", dict(session.get("active_item") or {}))
            elif action == "pc":
                confirm_token = str(session.get("confirm_token") or "")
                if not confirm_token:
                    raise ValueError("发布确认已过期，请重新执行发布准备")
                self._create_remote_action(
                    event_data, client, token, "confirm_publish", dict(session.get("active_item") or {}),
                    {"confirm_token": confirm_token},
                )
            else:
                return
        except (ApiClientError, RuntimeError, ValueError, IndexError) as exc:
            self._reply_interaction(
                event_data,
                "⚠️ UBencode 操作失败",
                str(exc)[:500],
                [[self._callback_button("🔙 返回控制中心", "m")]],
            )

    @eventmanager.register(EventType.PluginAction)
    def handle_interactive_command(self, event: Event):
        event_data = dict(event.event_data or {})
        action = str(event_data.get("action") or "")
        if not action.startswith("ub_"):
            return
        if action == "ub_help":
            self._reply_interaction(event_data, "📖 UBencode 帮助", self._help_text())
            return
        if not self._interaction_allowed(event_data):
            self._reply_interaction(event_data, "UBencode 助手", "当前通知用户没有查询权限。")
            return
        if not self._config.get("enabled"):
            self._reply_interaction(event_data, "UBencode 助手", "插件尚未启用。")
            return
        try:
            client, token = self._interactive_api()
            arg_str = str(event_data.get("arg_str") or "").strip()
            if action == "ub_control":
                self._send_control_menu(event_data, client, token)
                return
            if action == "ub_screenshot":
                self._send_operation_items(event_data, client, token, "screenshot", 1)
                return
            if action == "ub_publish":
                self._send_operation_items(event_data, client, token, "publish", 1)
                return
            if action in {"ub_status", "ub_current"}:
                target = self._resolve_runtime_device(client, token, arg_str)
                result = client.runtime_status(token, target)
                text = self._format_runtime_status(result.get("client"), current_only=action == "ub_current")
                title = "🎬 当前任务" if action == "ub_current" else "📊 压制状态"
            elif action == "ub_queue":
                queue_type, page, requested = self._parse_queue_args(arg_str)
                target = self._resolve_runtime_device(client, token, requested)
                page_size = int(self._config.get("runtime_page_size") or 8)
                result = client.runtime_queues(token, queue_type, page, page_size, target)
                text = self._format_runtime_queues(result, queue_type, page, page_size)
                title = "📋 UBencode 队列"
            elif action == "ub_clients":
                text = self._format_runtime_clients(client.runtime_clients(token))
                title = "💻 UBencode 客户端"
            elif action == "ub_recent":
                EventService(self).sync(client, token, self._config)
                events = [dict(item) for item in list(self.get_data("recent_events") or []) if isinstance(item, dict)][:10]
                if events:
                    lines = ["最近记录"]
                    for index, item in enumerate(events, 1):
                        lines.append(
                            f"{index}. {item.get('label') or '状态更新'} | {item.get('title') or '未命名影片'}\n"
                            f"   {item.get('summary') or '-'}，{self._format_ts(item.get('occurred_at'))}"
                        )
                    text = "\n".join(lines)
                else:
                    text = "暂无客户端事件记录。"
                title = "🕘 最近记录"
            elif action == "ub_tasks":
                tasks = client.tasks(token)
                groups = self._task_categories(tasks, str(self._config.get("username") or ""))
                text = "任务中心\n" + "\n".join(f"{name}：{len(items)}" for name, _icon, items in groups)
                title = "🗂️ 任务中心"
            else:
                return
            self._reply_interaction(event_data, title, text)
        except (ApiClientError, RuntimeError, ValueError) as exc:
            self._reply_interaction(event_data, "⚠️ UBencode 查询失败", str(exc)[:500])

    @eventmanager.register(EventType.UserMessage)
    def handle_plain_help(self, event: Event):
        event_data = dict(event.event_data or {})
        text = str(event_data.get("text") or "").strip()
        if text not in {"帮助", "压制帮助", "UBencode帮助", "ubencode帮助"}:
            return
        self._reply_interaction(event_data, "📖 UBencode 帮助", self._help_text())

    @staticmethod
    def _task_categories(tasks: list[dict], username: str) -> list[tuple[str, str, list[dict]]]:
        user_key = str(username or "").lower()
        mine: list[dict] = []
        uhd_pending: list[dict] = []
        uhd_tested: list[dict] = []
        bd: list[dict] = []
        claimed_pending: list[dict] = []
        claimed_done: list[dict] = []
        completed: list[dict] = []

        for task in tasks:
            status = str(task.get("status") or "")
            profile = str(task.get("source_profile") or task.get("target_profile") or "").upper()
            test_assignee = str(task.get("test_assignee") or "").lower()
            encode_assignee = str(task.get("encode_assignee") or "").lower()
            has_test_owner = bool(test_assignee)
            reserved_for_encode_user = bool(encode_assignee)
            is_mine = bool(user_key) and (
                (status in {"testing_claimed", "test_uploaded", "pending_encode"} and test_assignee == user_key)
                or (
                    status in {"testing_claimed", "test_uploaded", "pending_encode", "encoding_claimed", "published", "completed"}
                    and encode_assignee == user_key
                )
            )

            if is_mine and status in {"testing_claimed", "test_uploaded", "pending_encode", "encoding_claimed", "published", "completed"}:
                mine.append(task)
            if status == "completed":
                completed.append(task)
            if status == "testing_claimed" and (has_test_owner or reserved_for_encode_user):
                claimed_pending.append(task)
            elif (status in {"test_uploaded", "pending_encode"} and reserved_for_encode_user) or status == "encoding_claimed":
                claimed_done.append(task)
            if status in {"pending_test", "test_uploaded", "pending_encode"} and not reserved_for_encode_user:
                if profile == "UHD":
                    if status == "pending_test":
                        uhd_pending.append(task)
                    else:
                        uhd_tested.append(task)
                elif profile == "BD":
                    bd.append(task)

        return [
            ("我的任务", "mdi-account-check-outline", mine),
            ("UHD待测压", "mdi-timer-sand", uhd_pending),
            ("UHD已测压", "mdi-check-decagram-outline", uhd_tested),
            ("BD任务", "mdi-disc", bd),
            ("已领取任务", "mdi-account-lock-outline", claimed_pending + claimed_done),
            ("已完成任务", "mdi-check-circle-outline", completed),
        ]

    def _task_groups(self, groups: list[tuple[str, str, list[dict]]], username: str) -> dict:
        panels: List[dict] = []
        for title, icon, tasks in groups:
            task_content: List[dict]
            if tasks:
                task_content = [self._task_card(task, username) for task in tasks[:100]]
            else:
                task_content = [{"component": "VAlert", "props": {
                    "type": "info", "variant": "tonal", "density": "compact", "text": "暂无任务",
                }}]
            panels.append({
                "component": "VExpansionPanel",
                "content": [
                    {"component": "VExpansionPanelTitle", "content": [{
                        "component": "div",
                        "props": {"class": "d-flex align-center ga-2 w-100"},
                        "content": [
                            {"component": "VIcon", "props": {"icon": icon, "color": "primary", "size": "small"}},
                            {"component": "span", "props": {"class": "font-weight-medium"}, "text": title},
                            {"component": "VSpacer"},
                            {"component": "VChip", "props": {"size": "small", "variant": "tonal"}, "text": str(len(tasks))},
                        ],
                    }]},
                    {"component": "VExpansionPanelText", "content": task_content},
                ],
            })
        return {"component": "VExpansionPanels", "props": {"multiple": False, "class": "mb-5"}, "content": panels}

    def _task_card(self, task: dict, username: str) -> dict:
        status = str(task.get("status") or "")
        task_id = int(task.get("id") or 0)
        title = str(task.get("source_title") or "未命名任务")
        profile = str(task.get("source_profile") or task.get("target_profile") or "-")
        codec = str(task.get("target_codec") or "-")
        hdr = str(task.get("hdr_type") or "SDR")
        buttons: List[dict] = []
        if status == "pending_test":
            buttons.extend([
                self._task_button("领取测压", "claim_test", task_id),
                self._task_button("领取测压并下载", "claim_test_download", task_id, "primary"),
                self._task_button("领取正压", "claim_encode", task_id),
                self._task_button("领取正压并下载", "claim_encode_download", task_id, "primary"),
            ])
        elif status in {"pending_encode", "test_uploaded"}:
            buttons.extend([
                self._task_button("领取正压", "claim_encode", task_id),
                self._task_button("领取正压并下载", "claim_encode_download", task_id, "primary"),
            ])
        assignees = {
            str(task.get("test_assignee") or "").lower(),
            str(task.get("encode_assignee") or "").lower(),
        }
        if str(username or "").lower() in assignees and status not in {"completed", "deleted"}:
            buttons.append(self._task_button("推送源种", "push_source", task_id))
            buttons.append(self._task_button("取消领取", "cancel", task_id, "error"))
        if int(task.get("test_result_id") or 0) > 0:
            buttons.append(self._task_button("查看测压参数", "show_result", task_id))
        detail_url = str(task.get("source_detail_url") or "")
        if detail_url:
            buttons.append({"component": "VBtn", "props": {"href": detail_url, "target": "_blank", "variant": "text", "size": "small", "prepend-icon": "mdi-open-in-new"}, "text": "REMUX"})
        return {
            "component": "VCard",
            "props": {"variant": "outlined", "class": "mb-2", "rounded": "sm"},
            "content": [
                {"component": "VCardText", "props": {"class": "py-3"}, "content": [
                    {"component": "div", "props": {"class": "d-flex flex-wrap align-start justify-space-between ga-2"}, "content": [
                        {"component": "div", "content": [
                            {"component": "div", "props": {"class": "font-weight-bold text-body-1"}, "text": title},
                            {"component": "div", "props": {"class": "text-body-2 text-medium-emphasis mt-1"}, "text": f"{profile} · {codec} · {hdr}"},
                        ]},
                        {"component": "VChip", "props": {
                            "size": "small", "variant": "tonal", "color": self._task_status_color(status),
                        }, "text": self._task_status_label(status)},
                    ]},
                    {"component": "div", "props": {"class": "d-flex flex-wrap ga-2 mt-3"}, "content": buttons},
                ]},
            ],
        }

    @staticmethod
    def _test_result_text(payload: dict) -> str:
        results = dict(payload.get("test_results") or {})
        if not results and payload.get("test_result"):
            results = {"result": payload.get("test_result")}
        if not results:
            return "该任务暂无测压结果"
        blocks = []
        for codec, result in results.items():
            item = dict(result or {})
            template = str(item.get("template_text") or "").strip()
            summary = template or f"CRF {item.get('crf') or '-'}，码率 {item.get('bitrate_percent') or '-'}%"
            blocks.append(f"[{codec}]\n{summary[:4000]}")
        return "\n\n".join(blocks)

    def _remember_result(self, ok: bool, message: str) -> dict:
        result = {"ok": bool(ok), "message": str(message)[:8000], "at": int(time.time())}
        self.save_data("page_message", result)
        return result

    @staticmethod
    def _section_title(title: str, subtitle: str, icon: str = "mdi-tune-variant") -> dict:
        return {"component": "div", "props": {"class": "d-flex align-start ga-3 mb-4"}, "content": [
            {"component": "VIcon", "props": {"icon": icon, "color": "primary", "size": "large"}},
            {"component": "div", "content": [
                {"component": "div", "props": {"class": "text-h6 font-weight-bold"}, "text": title},
                {"component": "div", "props": {"class": "text-body-2 text-medium-emphasis"}, "text": subtitle},
            ]},
        ]}

    @staticmethod
    def _group_title(title: str, icon: str) -> dict:
        return {"component": "div", "props": {"class": "d-flex align-center ga-2 mb-1"}, "content": [
            {"component": "VIcon", "props": {"icon": icon, "size": "small", "color": "primary"}},
            {"component": "div", "props": {"class": "text-subtitle-2 font-weight-bold"}, "text": title},
        ]}

    @staticmethod
    def _col(item: dict, md: int) -> dict:
        return {"component": "VCol", "props": {"cols": 12, "md": md}, "content": [item]}

    @staticmethod
    def _field(model: str, label: str) -> dict:
        return {"component": "VTextField", "props": {"model": model, "label": label, "clearable": True, "hide-details": "auto"}}

    @staticmethod
    def _switch(model: str, label: str) -> dict:
        return {"component": "VSwitch", "props": {"model": model, "label": label, "color": "primary", "density": "compact", "hide-details": True}}

    @staticmethod
    def _button(text: str, icon: str, handler: str, color: str = "secondary", variant: str = "tonal") -> dict:
        return {"component": "VBtn", "props": {
            "variant": variant, "color": color, "prepend-icon": icon, "block": True, "height": 44, "onClick": handler,
        }, "text": text}

    @staticmethod
    def _alert(text: str, alert_type: str) -> dict:
        return {"component": "VAlert", "props": {"type": alert_type, "variant": "tonal", "density": "compact", "text": text, "class": "mb-2"}}

    @staticmethod
    def _status_col(title: str, text: str, ok: bool, icon: str) -> dict:
        return {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [{
            "component": "VAlert",
            "props": {
                "type": "success" if ok else "warning", "variant": "tonal", "title": title,
                "text": str(text or "-"), "icon": icon, "density": "compact",
            },
        }]}

    def _recent_events_panel(self, events: list[dict]) -> dict:
        content: List[dict] = [{"component": "div", "props": {
            "class": "d-flex flex-wrap align-center justify-space-between ga-2 mb-3",
        }, "content": [
            {"component": "div", "props": {"class": "d-flex align-center ga-2"}, "content": [
                {"component": "VIcon", "props": {"icon": "mdi-bell-badge-outline", "color": "primary"}},
                {"component": "div", "props": {"class": "text-subtitle-1 font-weight-bold"}, "text": "最近事件"},
            ]},
            {"component": "VChip", "props": {"size": "small", "variant": "tonal"}, "text": f"最近 {min(12, len(events))} 条"},
        ]}]
        if not events:
            content.append({"component": "VAlert", "props": {
                "type": "info", "variant": "tonal", "density": "compact", "text": "暂无客户端事件，完成一次测压或正压后会显示在这里。",
            }})
        else:
            items = []
            for event in events[:12]:
                items.append({"component": "VListItem", "props": {
                    "prepend-icon": str(event.get("icon") or "mdi-progress-clock"),
                    "title": f"{event.get('label') or '状态更新'} · {event.get('title') or '未命名影片'}",
                    "subtitle": f"{event.get('summary') or '-'} · {self._format_ts(event.get('occurred_at'))}",
                    "class": "px-0",
                }})
            content.append({"component": "VList", "props": {"density": "compact", "lines": "two"}, "content": items})
        return {"component": "VSheet", "props": {"class": "border rounded pa-4 mb-5"}, "content": content}

    @staticmethod
    def _task_status_label(status: str) -> str:
        return {
            "pending_test": "待测压",
            "testing_claimed": "已领取测压",
            "pending_encode": "待正压",
            "test_uploaded": "测压完成",
            "encoding_claimed": "已领取正压",
            "published": "已发布",
            "completed": "已完成",
        }.get(str(status or ""), str(status or "未知"))

    @staticmethod
    def _task_status_color(status: str) -> str:
        if status in {"completed", "published", "test_uploaded"}:
            return "success"
        if status in {"testing_claimed", "encoding_claimed"}:
            return "warning"
        return "info"

    @staticmethod
    def _format_ts(value: Any) -> str:
        try:
            return datetime.fromtimestamp(int(value)).strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError, OSError):
            return "尚未同步"

    @staticmethod
    def _task_button(text: str, action: str, task_id: int, color: str = "secondary") -> dict:
        return {
            "component": "VBtn",
            "props": {"variant": "tonal", "color": color, "height": 44},
            "text": text,
            "events": {"click": {
                "api": f"plugin/UBencodeHelper/task-action?apikey={settings.API_TOKEN}",
                "method": "post",
                "params": {"task_id": task_id, "action": action},
            }},
        }
