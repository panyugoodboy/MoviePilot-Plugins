"""MP 整理纠正 MoviePilot V2 插件入口。"""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import hashlib
from threading import Lock, Thread
from typing import Any, Dict, List, Mapping, Optional, Tuple

from apscheduler.triggers.cron import CronTrigger
from fastapi import Body, HTTPException
from fastapi.responses import FileResponse, Response

from app.core.config import settings
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import NotificationType
from app.utils.http import RequestUtils

from .posters import poster_cache_suffix, poster_request_headers, safe_poster_url
from .schedule import cron_preview
from .service import OrganizeCorrectService
from .store import CorrectionStore


DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "scan_cron": "0 4 * * *",
    "auto_correct": False,
    "auto_batch_limit": 5,
    "cleanup_old_after_correct": True,
    "notify_enabled": True,
}


class MPOrganizeCorrect(_PluginBase):
    """检查英文整理结果，并按源文件中文片名和年份安全重新整理。"""

    plugin_name = "MP整理纠正"
    plugin_desc = "检查 MP 英文整理结果，按源文件片名和可选年份重新识别整理。"
    plugin_icon = "directory.png"
    plugin_version = "1.0.6"
    plugin_author = "panyugoodboy"
    author_url = "https://github.com/panyugoodboy"
    plugin_config_prefix = "mporganizecorrect_"
    plugin_order = 25
    auth_level = 2

    def __init__(self):
        super().__init__()
        self._config: Dict[str, Any] = deepcopy(DEFAULT_CONFIG)
        self._store: Optional[CorrectionStore] = None
        self._service: Optional[OrganizeCorrectService] = None
        self._task_lock = Lock()
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._poster_lock = Lock()
        self._poster_urls: OrderedDict[str, str] = OrderedDict()

    def init_plugin(self, config: dict = None) -> None:
        """加载配置并初始化插件独立数据库与业务服务。"""

        self._config = self._normalize_config(config or {})
        self._store = CorrectionStore(self.get_data_path() / "organize_correct.db")
        self._service = OrganizeCorrectService(self._store, lambda: self._config)

    def get_state(self) -> bool:
        """返回插件是否启用。"""

        return bool(self._config.get("enabled"))

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """本插件不注册消息命令。"""

        return []

    def get_service(self) -> List[Dict[str, Any]]:
        """注册每日增量扫描及可选的严格匹配自动纠正任务。"""

        if not self.get_state() or not self._config.get("scan_cron"):
            return []
        try:
            trigger = CronTrigger.from_crontab(str(self._config["scan_cron"]))
        except ValueError as error:
            logger.error(f"[MP整理纠正] Cron 无效：{error}")
            return []
        return [{
            "id": "MPOrganizeCorrect-scan",
            "name": "MP整理纠正定时扫描",
            "trigger": trigger,
            "func": self._scheduled_run,
            "kwargs": {},
        }]

    def get_api(self) -> List[Dict[str, Any]]:
        """注册管理接口及候选海报同源代理。"""

        return [
            self._route(
                "/poster/{token}", self._api_poster, ["GET"],
                "MoviePilot 搜索候选海报", allow_anonymous=True,
            ),
            self._route("/bootstrap", self._api_bootstrap, ["GET"], "插件初始化数据"),
            self._route("/overview", self._api_overview, ["GET"], "纠正状态总览"),
            self._route("/records", self._api_records, ["GET"], "待纠正记录列表"),
            self._route("/scan", self._api_scan, ["POST"], "扫描英文整理记录"),
            self._route(
                "/records/reset-scan", self._api_reset_scan, ["POST"],
                "清除插件记录并按当前 MP 历史重扫",
            ),
            self._route(
                "/records/{history_id}/search", self._api_search, ["POST"], "人工搜索媒体候选"
            ),
            self._route(
                "/records/{history_id}/preview", self._api_preview, ["POST"], "预览重新整理路径"
            ),
            self._route("/records/correct", self._api_correct, ["POST"], "批量重新整理"),
            self._route(
                "/records/correct-all", self._api_correct_all, ["POST"], "纠正全部精确匹配记录"
            ),
            self._route("/records/ignore", self._api_ignore, ["POST"], "设置忽略状态"),
            self._route("/records/cleanup", self._api_cleanup, ["POST"], "重试清理旧媒体"),
            self._route("/records/delete", self._api_delete, ["POST"], "删除旧媒体或原记录"),
            self._route("/audits", self._api_audits, ["GET"], "操作审计列表"),
            self._route(
                "/notifications/test", self._api_test_notification, ["POST"], "发送测试通知"
            ),
        ]

    @staticmethod
    def get_render_mode() -> Tuple[str, str]:
        """使用 Vue 模块联邦渲染完整插件页面。"""

        return "vue", "dist/assets"

    @staticmethod
    def get_sidebar_nav() -> List[Dict[str, Any]]:
        """在整理分区注册插件侧栏入口。"""

        return [{
            "nav_key": "main",
            "title": "MP整理纠正",
            "icon": "mdi-folder-refresh-outline",
            "section": "organize",
            "permission": "manage",
            "order": 25,
        }]

    @staticmethod
    def get_form() -> Tuple[List[dict], Dict[str, Any]]:
        """返回自定义 Vue 配置组件使用的默认配置。"""

        return [], deepcopy(DEFAULT_CONFIG)

    @staticmethod
    def get_page() -> List[dict]:
        """自定义 Vue 页面不使用 Vuetify JSON 页面配置。"""

        return []

    @staticmethod
    def get_dashboard_meta() -> List[Dict[str, str]]:
        """声明一个待纠正状态仪表盘组件。"""

        return [{"key": "status", "name": "整理纠正状态"}]

    def get_dashboard(self, key: str, **kwargs):
        """返回仪表盘统计信息。"""

        return (
            {"cols": 12, "md": 6},
            {"title": "整理纠正状态", "border": True, "stats": self._require_store().stats()},
            [],
        )

    def stop_service(self) -> None:
        """插件未维护私有调度器，无需额外停止操作。"""

    def _api_bootstrap(self) -> dict:
        return self._ok({
            "config": deepcopy(self._config),
            "stats": self._require_store().stats(),
            "tasks": deepcopy(self._tasks),
            "cron_preview": cron_preview(self._config.get("scan_cron")),
            "last_scan_at": self._require_store().get_meta("last_scan_at", ""),
        })

    def _api_overview(self) -> dict:
        return self._ok({
            "stats": self._require_store().stats(),
            "tasks": deepcopy(self._tasks),
            "last_scan_at": self._require_store().get_meta("last_scan_at", ""),
        })

    def _api_records(
        self,
        page: int = 1,
        page_size: int = 50,
        state: str = "",
        keyword: str = "",
        media_type: str = "",
    ) -> dict:
        result = self._require_store().list_records(
            page=page,
            page_size=page_size,
            state=state,
            keyword=keyword,
            media_type=media_type,
        )
        for record in result.get("items") or []:
            self._attach_poster_tokens([record.get("candidate") or {}])
            self._attach_poster_tokens(record.get("options") or [])
        return self._ok(result)

    def _api_scan(self, payload: dict = Body(default={})) -> dict:
        return self._start_task(
            "scan-full" if payload.get("full") else "scan",
            self._run_scan,
            bool(payload.get("full")),
        )

    def _api_reset_scan(self, payload: dict = Body(default={})) -> dict:
        if not payload.get("confirmed"):
            return self._error("必须确认只清除本插件记录并重新扫描")
        return self._start_task("reset-scan", self._run_reset_scan)

    def _api_search(self, history_id: int, payload: dict = Body(default={})) -> dict:
        try:
            candidates = self._require_service().search_record(
                history_id,
                title=str(payload.get("title") or "").strip(),
                year=int(payload.get("year") or 0),
                media_type=str(payload.get("media_type") or ""),
            )
            return self._ok(self._attach_poster_tokens(candidates))
        except Exception as error:
            return self._error(error)

    def _api_poster(self, token: str):
        token = str(token or "").lower()
        with self._poster_lock:
            url = self._poster_urls.get(token)
        if not url:
            raise HTTPException(status_code=404, detail="搜索候选海报不存在或已过期")

        cache_dir = self.get_data_path() / "poster_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached = next((path for path in cache_dir.glob(f"{token}.*") if path.is_file()), None)
        cache_headers = {"Cache-Control": "public, max-age=604800"}
        if cached:
            return FileResponse(cached, headers=cache_headers)

        try:
            response = RequestUtils(
                headers=poster_request_headers(url),
                proxies=getattr(settings, "PROXY", None),
                timeout=20,
            ).get_res(url)
        except Exception as error:
            logger.warning(f"[MP整理纠正] 获取搜索候选海报失败：{error}")
            raise HTTPException(status_code=502, detail="搜索候选海报获取失败") from error
        if not response or not response.ok:
            raise HTTPException(status_code=502, detail="搜索候选海报源请求失败")
        content_type = str((response.headers or {}).get("Content-Type") or "").split(";", 1)[0]
        content = response.content
        if not content or not content_type.lower().startswith("image/") or len(content) > 15 * 1024 ** 2:
            raise HTTPException(status_code=502, detail="搜索候选海报源返回无效内容")
        cache_file = cache_dir / f"{token}{poster_cache_suffix(url, content_type)}"
        cache_file.write_bytes(content)
        return Response(content=content, media_type=content_type, headers=cache_headers)

    def _api_preview(self, history_id: int, payload: dict = Body(default={})) -> dict:
        try:
            return self._ok(self._require_service().preview(history_id, payload.get("candidate") or {}))
        except Exception as error:
            return self._error(error)

    def _api_correct(self, payload: dict = Body(default={})) -> dict:
        items = payload.get("items") or []
        if not items:
            return self._error("请选择至少一条待纠正记录")
        return self._start_task(
            "correct",
            self._run_correct,
            items,
            bool(payload.get("cleanup_old", self._config.get("cleanup_old_after_correct", True))),
        )

    def _api_correct_all(self, payload: dict = Body(default={})) -> dict:
        return self._start_task(
            "correct-all",
            self._run_correct_all,
            bool(payload.get("cleanup_old", self._config.get("cleanup_old_after_correct", True))),
        )

    def _api_ignore(self, payload: dict = Body(default={})) -> dict:
        try:
            count = self._require_service().set_ignored(
                payload.get("history_ids") or [], bool(payload.get("ignored", True))
            )
            return self._ok({"updated": count})
        except Exception as error:
            return self._error(error)

    def _api_cleanup(self, payload: dict = Body(default={})) -> dict:
        return self._start_task(
            "cleanup",
            self._run_cleanup,
            payload.get("history_ids") or [],
        )

    def _api_delete(self, payload: dict = Body(default={})) -> dict:
        if not payload.get("source_safe_confirmed"):
            return self._error("必须确认源文件不会被删除")
        return self._start_task(
            "delete",
            self._run_delete,
            payload.get("history_ids") or [],
            bool(payload.get("delete_media")),
            bool(payload.get("delete_history")),
        )

    def _api_audits(self, page: int = 1, page_size: int = 50) -> dict:
        return self._ok(self._require_store().list_audits(page, page_size))

    def _api_test_notification(self) -> dict:
        self.post_message(
            mtype=NotificationType.Organize,
            title="MP整理纠正测试通知",
            text="通知通道工作正常。插件不会删除源文件。",
        )
        return self._ok(message="测试通知已发送")

    def _scheduled_run(self) -> None:
        try:
            result = self._require_service().scheduled_run()
            self._notify_summary("定时扫描", result)
        except Exception as error:
            logger.error(f"[MP整理纠正] 定时任务失败：{error}")

    def _run_scan(self, full: bool) -> dict:
        result = self._require_service().scan(
            full=full,
            progress=lambda value: self._set_task_progress("scan-full" if full else "scan", value),
        )
        self._notify_summary("全量扫描" if full else "增量扫描", result)
        return result

    def _run_reset_scan(self) -> dict:
        with self._poster_lock:
            self._poster_urls.clear()
        result = self._require_service().reset_and_scan(
            progress=lambda value: self._set_task_progress("reset-scan", value),
        )
        self._notify_summary("清除记录并重扫", result)
        return result

    def _run_correct(self, items: list, cleanup_old: bool) -> dict:
        result = self._require_service().correct_records(
            items,
            cleanup_old=cleanup_old,
            progress=lambda value: self._set_task_progress("correct", value),
        )
        self._notify_summary("重新整理", result)
        return result

    def _run_correct_all(self, cleanup_old: bool) -> dict:
        result = self._require_service().correct_all_ready(
            cleanup_old=cleanup_old,
            progress=lambda value: self._set_task_progress("correct-all", value),
        )
        self._notify_summary("全部纠正", result)
        return result

    def _run_cleanup(self, history_ids: list) -> dict:
        result = self._require_service().cleanup_records(history_ids)
        self._notify_summary("旧媒体清理", result)
        return result

    def _run_delete(
        self,
        history_ids: list,
        delete_media: bool,
        delete_history: bool,
    ) -> dict:
        result = self._require_service().delete_records(
            history_ids,
            delete_media=delete_media,
            delete_history=delete_history,
        )
        self._notify_summary("删除操作", result)
        return result

    def _start_task(self, name: str, func, *args) -> dict:
        with self._task_lock:
            running = next((
                task_name for task_name, task in self._tasks.items()
                if task.get("status") == "running"
            ), "")
            if running:
                return self._error(f"{running} 任务正在运行，请等待完成后再操作")
            self._tasks[name] = {
                "status": "running",
                "started_at": self._now(),
                "finished_at": "",
                "message": "任务已开始",
                "progress": {},
            }

        def _runner():
            try:
                result = func(*args)
                status, message = "success", "执行完成"
            except Exception as error:
                logger.error(f"[MP整理纠正] {name} 任务失败：{error}")
                result, status, message = None, "failed", str(error)
            with self._task_lock:
                progress = deepcopy((self._tasks.get(name) or {}).get("progress") or {})
                self._tasks[name] = {
                    "status": status,
                    "started_at": (self._tasks.get(name) or {}).get("started_at"),
                    "finished_at": self._now(),
                    "message": message,
                    "result": result,
                    "progress": progress,
                }

        Thread(target=_runner, name=f"MPOrganizeCorrect-{name}", daemon=True).start()
        return self._ok({"task": name}, "任务已开始")

    def _set_task_progress(self, name: str, value: Mapping[str, Any]) -> None:
        with self._task_lock:
            task = self._tasks.get(name)
            if not task or task.get("status") != "running":
                return
            task["progress"] = deepcopy(dict(value))
            task["message"] = str(value.get("message") or "")

    def _notify_summary(self, action: str, result: Mapping[str, Any]) -> None:
        if not self._config.get("notify_enabled"):
            return
        if "scan" in result:
            scan = result.get("scan") or {}
            correct = result.get("correct") or {}
            text = (
                f"扫描 {scan.get('checked', 0)} 条，列出 {scan.get('listed', 0)} 条；"
                f"自动纠正成功 {correct.get('success', 0)} 条，失败 {correct.get('failed', 0)} 条。"
            )
        elif "checked" in result:
            text = (
                f"检查 {result.get('checked', 0)} 条，列出 {result.get('listed', 0)} 条，"
                f"可自动纠正 {result.get('ready', 0)} 条。"
            )
        else:
            text = (
                f"共 {result.get('total', 0)} 条，成功 {result.get('success', 0)} 条，"
                f"失败 {result.get('failed', 0)} 条。"
            )
        self.post_message(
            mtype=NotificationType.Organize,
            title=f"MP整理纠正 · {action}",
            text=text,
        )

    def _require_store(self) -> CorrectionStore:
        if not self._store:
            raise RuntimeError("插件存储尚未初始化")
        return self._store

    def _require_service(self) -> OrganizeCorrectService:
        if not self._service:
            raise RuntimeError("插件服务尚未初始化")
        return self._service

    def _attach_poster_tokens(self, candidates: list) -> list:
        """为 MoviePilot 搜索候选注册不可伪造的同源海报代理令牌。"""

        extra_hosts = [str(getattr(settings, "TMDB_IMAGE_DOMAIN", "") or "")]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            candidate.pop("poster_token", None)
            url = safe_poster_url(candidate.get("poster_url"), extra_hosts)
            if not url:
                continue
            token = hashlib.sha256(url.encode("utf-8")).hexdigest()
            with self._poster_lock:
                self._poster_urls[token] = url
                self._poster_urls.move_to_end(token)
                while len(self._poster_urls) > 2048:
                    self._poster_urls.popitem(last=False)
            candidate["poster_token"] = token
        return candidates

    @staticmethod
    def _route(
        path: str,
        endpoint,
        methods: List[str],
        summary: str,
        allow_anonymous: bool = False,
    ) -> Dict[str, Any]:
        route = {
            "path": path,
            "endpoint": endpoint,
            "methods": methods,
            "summary": summary,
        }
        if allow_anonymous:
            route["allow_anonymous"] = True
        else:
            route["auth"] = "bear"
        return route

    @staticmethod
    def _ok(data: Any = None, message: str = "") -> dict:
        return {"success": True, "message": message, "data": data}

    @staticmethod
    def _error(error: Any) -> dict:
        return {"success": False, "message": str(error), "data": None}

    @classmethod
    def _normalize_config(cls, config: Mapping[str, Any]) -> Dict[str, Any]:
        result = deepcopy(DEFAULT_CONFIG)
        result.update(dict(config or {}))
        for key in (
            "enabled", "auto_correct", "cleanup_old_after_correct", "notify_enabled",
        ):
            result[key] = cls._to_bool(result.get(key), DEFAULT_CONFIG[key])
        result["auto_batch_limit"] = max(1, min(50, cls._to_int(result.get("auto_batch_limit"), 5)))
        result["scan_cron"] = str(result.get("scan_cron") or "").strip()
        return result

    @staticmethod
    def _to_bool(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() not in {"0", "false", "no", "off", ""}
        return default if value is None else bool(value)

    @staticmethod
    def _to_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _now() -> str:
        from datetime import datetime
        return datetime.now().astimezone().isoformat(timespec="seconds")
