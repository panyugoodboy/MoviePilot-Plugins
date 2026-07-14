import json
import time
from datetime import datetime
from typing import Any, Dict, List, Tuple

from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType

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
    plugin_version = "1.1.0"
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
        return []

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
        pass

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
