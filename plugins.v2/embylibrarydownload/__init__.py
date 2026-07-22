from __future__ import annotations

from copy import deepcopy
from threading import Lock, Thread
from typing import Any, Dict, List, Mapping, Optional, Tuple

from apscheduler.triggers.cron import CronTrigger
from fastapi import Body

from app.log import logger
from app.plugins import _PluginBase

from .schedule import cron_preview
from .service import LibraryDownloadService
from .store import PluginStore


DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "emby_servers": [],
    "emby_libraries": {},
    "sites": [],
    "inventory_cron": "0 */6 * * *",
    "target_cron": "15 */2 * * *",
    "pool_cron": "30 */2 * * *",
    "auto_download_cron": "",
    "target_scan_enabled": True,
    "pool_scan_enabled": False,
    "auto_download": False,
    "pool_auto_download": False,
    "auto_batch_limit": 5,
    "max_versions": 3,
    "allow_same_slot": False,
    "movie_save_path": "",
    "tv_save_path": "",
    "quality_save_paths": {},
    "quality_types": ["bluray", "diy", "remux", "encode", "webdl"],
    "effects": ["dv", "hdr10plus", "hdr10", "hdr", "hlg", "sdr", "unknown"],
    "resolutions": ["2160p", "1080p"],
    "video_codecs": [],
    "min_bitrate_mbps": 0,
    "max_bitrate_mbps": 0,
    "bitrate_order": "desc",
    "reject_unknown_bitrate": False,
    "include_words": "",
    "exclude_words": "CAM,TS,TC,HDTS",
    "exclude_tv": True,
}


class EmbyLibraryDownload(_PluginBase):
    plugin_name = "联动EMBY库筛选下载"
    plugin_desc = "以 Emby 实际媒体版本为准，按站点和质量规则搜索、限量并下载资源。"
    plugin_icon = "emby.png"
    plugin_version = "0.2.5"
    plugin_author = "panyugoodboy"
    author_url = "https://github.com/panyugoodboy"
    plugin_config_prefix = "embylibrarydownload_"
    plugin_order = 24
    auth_level = 2

    _config: Dict[str, Any] = deepcopy(DEFAULT_CONFIG)
    _store: Optional[PluginStore] = None
    _service: Optional[LibraryDownloadService] = None
    _task_lock = Lock()
    _tasks: Dict[str, Dict[str, Any]] = {}

    def init_plugin(self, config: dict = None) -> None:
        if "_tasks" not in self.__dict__:
            self._tasks = {}
        self._config = self._normalize_config(config or {})
        self._store = PluginStore(self.get_data_path() / "library_download.db")
        self._service = LibraryDownloadService(self._store, lambda: self._config)

    def get_state(self) -> bool:
        return bool(self._config.get("enabled"))

    def get_service(self) -> List[Dict[str, Any]]:
        if not self.get_state():
            return []
        services = []
        definitions = [
            ("inventory", "Emby媒体库版本同步", self._config.get("inventory_cron"), self._sync_inventory),
            (
                "targets",
                "目标资源搜索",
                self._config.get("target_cron") if self._config.get("target_scan_enabled") else None,
                self._search_targets,
            ),
            (
                "pool",
                "自定义站点种子池刷新",
                self._config.get("pool_cron") if self._config.get("pool_scan_enabled") else None,
                self._refresh_pool,
            ),
            (
                "auto-download",
                "从已扫描种子池自动下载",
                self._config.get("auto_download_cron")
                if self._config.get("auto_download") and self._config.get("pool_auto_download") else None,
                self._auto_download_pool,
            ),
        ]
        for suffix, name, cron, func in definitions:
            if not cron:
                continue
            try:
                trigger = CronTrigger.from_crontab(str(cron))
            except ValueError as error:
                logger.error(f"[联动EMBY库筛选下载] {name} Cron 无效：{error}")
                continue
            services.append({
                "id": f"EmbyLibraryDownload-{suffix}",
                "name": name,
                "trigger": trigger,
                "func": func,
                "kwargs": {},
            })
        return services

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            self._route("/bootstrap", self._api_bootstrap, ["GET"], "插件初始化数据"),
            self._route("/overview", self._api_overview, ["GET"], "总览统计"),
            self._route("/inventory", self._api_inventory, ["GET"], "Emby版本库存"),
            self._route("/inventory/sync", self._api_sync_inventory, ["POST"], "同步Emby版本库存"),
            self._route("/targets", self._api_targets, ["GET"], "目标清单"),
            self._route("/targets", self._api_create_target, ["POST"], "新增目标"),
            self._route("/targets/{target_id}", self._api_update_target, ["PUT"], "更新目标"),
            self._route("/targets/{target_id}", self._api_delete_target, ["DELETE"], "删除目标"),
            self._route("/targets/search", self._api_search_targets, ["POST"], "搜索目标资源"),
            self._route("/candidates", self._api_candidates, ["GET"], "候选种子列表"),
            self._route("/pool/refresh", self._api_refresh_pool, ["POST"], "刷新自定义站点种子池"),
            self._route("/downloads", self._api_downloads, ["POST"], "批量提交下载"),
            self._route("/jobs", self._api_jobs, ["GET"], "下载任务"),
            self._route("/jobs/{job_id}/cancel", self._api_cancel_job, ["POST"], "取消未提交任务"),
        ]

    @staticmethod
    def get_render_mode() -> Tuple[str, str]:
        return "vue", "dist/assets"

    @staticmethod
    def get_sidebar_nav() -> List[Dict[str, Any]]:
        return [{
            "nav_key": "main",
            "title": "EMBY库筛选下载",
            "icon": "mdi-movie-filter",
            "section": "organize",
            "permission": "manage",
            "order": 24,
        }]

    @staticmethod
    def get_dashboard_meta() -> List[Dict[str, str]]:
        return [{"key": "status", "name": "Emby库存状态"}]

    def get_dashboard(self, key: str, **kwargs) -> Tuple[Dict[str, Any], Dict[str, Any], List[dict]]:
        stats = self._require_store().stats()
        return (
            {"cols": 12, "md": 6},
            {"title": "Emby库存状态", "border": True, "stats": stats},
            [],
        )

    @staticmethod
    def get_form() -> Tuple[List[dict], Dict[str, Any]]:
        return [], deepcopy(DEFAULT_CONFIG)

    @staticmethod
    def get_page() -> List[dict]:
        return []

    def stop_service(self) -> None:
        pass

    def _api_bootstrap(self) -> dict:
        try:
            cron_previews = {
                key: cron_preview(self._config.get(key), trigger_class=CronTrigger)
                for key in ("inventory_cron", "target_cron", "pool_cron")
            }
            cron_previews["auto_download_cron"] = cron_preview(
                self._config.get("auto_download_cron"),
                trigger_class=CronTrigger,
                empty_text="未设置，不会定时自动下载",
            )
            return self._ok({
                "config": deepcopy(self._config),
                "options": self._require_service().options(),
                "stats": self._require_store().stats(),
                "tasks": deepcopy(self._tasks),
                "cron_previews": cron_previews,
            })
        except Exception as error:
            return self._error(error)

    def _api_overview(self) -> dict:
        return self._ok({"stats": self._require_store().stats(), "tasks": deepcopy(self._tasks)})

    def _api_inventory(
        self, page: int = 1, page_size: int = 50, keyword: str = "", media_type: str = ""
    ) -> dict:
        return self._ok(self._require_store().list_inventory(page, page_size, keyword, media_type))

    def _api_sync_inventory(self) -> dict:
        return self._start_task("inventory", self._sync_inventory)

    def _api_targets(self) -> dict:
        return self._ok(self._require_store().list_targets())

    def _api_create_target(self, payload: Dict[str, Any] = Body(default={})) -> dict:
        try:
            return self._ok(self._require_store().save_target(payload), "目标已新增")
        except Exception as error:
            return self._error(error)

    def _api_update_target(self, target_id: int, payload: Dict[str, Any] = Body(default={})) -> dict:
        try:
            return self._ok(self._require_store().save_target(payload, target_id), "目标已更新")
        except Exception as error:
            return self._error(error)

    def _api_delete_target(self, target_id: int) -> dict:
        return self._ok(None, "目标已删除") if self._require_store().delete_target(target_id) \
            else self._error("目标不存在")

    def _api_search_targets(self, payload: Dict[str, Any] = Body(default={})) -> dict:
        target_ids = payload.get("target_ids") or []
        return self._start_task("targets", self._search_targets, target_ids)

    def _api_candidates(
        self,
        page: int = 1,
        page_size: int = 50,
        scope: str = "pool",
        keyword: str = "",
        site_id: Optional[int] = None,
        eligible_only: bool = True,
        quality_type: str = "",
    ) -> dict:
        return self._ok(self._require_store().list_candidates(
            page, page_size, scope, keyword, site_id, eligible_only, quality_type
        ))

    def _api_refresh_pool(self) -> dict:
        return self._start_task("pool", self._refresh_pool)

    def _api_downloads(self, payload: Dict[str, Any] = Body(default={})) -> dict:
        keys = payload.get("candidate_keys") or []
        if not keys:
            return self._error("请至少选择一个候选种子")
        if len(keys) > 50:
            return self._error("单次最多提交当前页 50 个种子")
        return self._start_task("downloads", self._dispatch_downloads, keys)

    def _api_jobs(self, page: int = 1, page_size: int = 50) -> dict:
        return self._ok(self._require_store().list_jobs(page, page_size))

    def _api_cancel_job(self, job_id: int) -> dict:
        success, message = self._require_store().cancel_job(job_id)
        return self._ok(None, "任务已取消") if success else self._error(message)

    def _sync_inventory(self) -> dict:
        return self._require_service().sync_inventory()

    def _search_targets(self, target_ids: Optional[List[int]] = None) -> dict:
        return self._require_service().search_targets(target_ids)

    def _refresh_pool(self) -> dict:
        return self._require_service().refresh_pool(lambda value: self._set_task_progress("pool", value))

    def _auto_download_pool(self) -> dict:
        return self._require_service().download_from_pool()

    def _dispatch_downloads(self, candidate_keys: List[str]) -> dict:
        results = self._require_service().dispatch(candidate_keys, automatic=False)
        return {
            "submitted": sum(1 for item in results if item.get("success")),
            "failed": sum(1 for item in results if not item.get("success")),
            "results": results,
        }

    def _require_store(self) -> PluginStore:
        if not self._store:
            raise RuntimeError("插件数据库尚未初始化")
        return self._store

    def _require_service(self) -> LibraryDownloadService:
        if not self._service:
            raise RuntimeError("插件服务尚未初始化")
        return self._service

    def _start_task(self, name: str, func, *args) -> dict:
        with self._task_lock:
            task = self._tasks.get(name) or {}
            if task.get("status") == "running":
                return self._error(f"{name} 任务正在运行")
            self._tasks[name] = {
                "status": "running",
                "started_at": self._now(),
                "message": "",
                "progress": {},
            }

        def runner():
            try:
                result = func(*args)
                status, message = "success", "执行完成"
            except Exception as error:
                logger.error(f"[联动EMBY库筛选下载] {name} 任务失败：{error}")
                result, status, message = None, "failed", str(error)
            with self._task_lock:
                progress = deepcopy(self._tasks.get(name, {}).get("progress") or {})
                self._tasks[name] = {
                    "status": status,
                    "started_at": self._tasks.get(name, {}).get("started_at"),
                    "finished_at": self._now(),
                    "message": message,
                    "result": result,
                    "progress": progress,
                }

        Thread(target=runner, name=f"EmbyLibraryDownload-{name}", daemon=True).start()
        return self._ok({"task": name}, "任务已开始")

    @classmethod
    def _normalize_config(cls, config: Dict[str, Any]) -> Dict[str, Any]:
        result = deepcopy(DEFAULT_CONFIG)
        result.update(config or {})
        result["max_versions"] = max(1, min(3, cls._to_int(result.get("max_versions"), 3)))
        result["auto_batch_limit"] = max(1, min(50, cls._to_int(result.get("auto_batch_limit"), 5)))
        result["exclude_tv"] = cls._to_bool(result.get("exclude_tv"), True)
        result.pop("browse_pages", None)
        result.pop("excluded_tv_titles", None)
        for key in ("sites", "emby_servers", "quality_types", "effects", "resolutions", "video_codecs"):
            if not isinstance(result.get(key), list):
                result[key] = []
        if not isinstance(result.get("emby_libraries"), dict):
            result["emby_libraries"] = {}
        if not isinstance(result.get("quality_save_paths"), dict):
            result["quality_save_paths"] = {}
        result["quality_save_paths"] = {
            key: str(result["quality_save_paths"].get(key) or "").strip()
            for key in ("bluray", "diy", "remux", "encode", "webdl", "unknown")
            if str(result["quality_save_paths"].get(key) or "").strip()
        }
        return result

    def _set_task_progress(self, name: str, value: Mapping[str, Any]) -> None:
        with self._task_lock:
            task = self._tasks.get(name)
            if not task or task.get("status") != "running":
                return
            task["progress"] = deepcopy(dict(value))
            task["message"] = str(value.get("message") or "")

    @staticmethod
    def _route(path: str, endpoint, methods: List[str], summary: str) -> Dict[str, Any]:
        return {"path": path, "endpoint": endpoint, "methods": methods, "auth": "bear", "summary": summary}

    @staticmethod
    def _ok(data: Any = None, message: str = "") -> dict:
        return {"success": True, "message": message, "data": data}

    @staticmethod
    def _error(error: Any) -> dict:
        return {"success": False, "message": str(error), "data": None}

    @staticmethod
    def _now() -> str:
        from datetime import datetime
        return datetime.now().astimezone().isoformat(timespec="seconds")

    @staticmethod
    def _to_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_bool(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() not in {"0", "false", "no", "off", ""}
        return default if value is None else bool(value)
