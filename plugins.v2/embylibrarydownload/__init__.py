from __future__ import annotations

from copy import deepcopy
from threading import Lock, Thread
from typing import Any, Dict, List, Mapping, Optional, Tuple

from apscheduler.triggers.cron import CronTrigger
from fastapi import Body

from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import NotificationType

from .notifications import build_task_summary, build_test_summary
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
    "proxy_enabled": True,
    "movie_save_path": "",
    "tv_save_path": "",
    "quality_save_paths": {},
    "quality_types": ["bluray", "diy", "remux", "encode", "webdl"],
    "effects": ["dv", "hdr10plus", "hdr10", "hdr", "hlg", "sdr", "unknown"],
    "resolutions": ["2160p", "1080p"],
    "video_codecs": [],
    "min_bitrate_mbps": 0,
    "max_bitrate_mbps": 0,
    "min_size_4k_gb": 0,
    "min_size_1080p_gb": 0,
    "bitrate_order": "desc",
    "reject_unknown_bitrate": False,
    "include_words": "",
    "exclude_words": "CAM,TS,TC,HDTS",
    "exclude_tv": True,
    "notify_enabled": True,
    "notify_inventory": True,
    "notify_targets": True,
    "notify_pool": True,
    "notify_download": True,
    "notify_failures": True,
}


class EmbyLibraryDownload(_PluginBase):
    plugin_name = "联动EMBY库筛选下载"
    plugin_desc = "以 Emby 实际媒体版本为准，按站点和质量规则搜索、限量并下载资源。"
    plugin_icon = "emby.png"
    plugin_version = "0.3.6"
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
        self._store.apply_minimum_size_filters(
            int(self._config["min_size_4k_gb"] * 1024 ** 3),
            int(self._config["min_size_1080p_gb"] * 1024 ** 3),
        )

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
            self._route("/jobs/delete", self._api_delete_jobs, ["POST"], "批量删除下载任务"),
            self._route("/jobs/retry", self._api_retry_jobs, ["POST"], "批量重试下载任务"),
            self._route("/jobs/retry-failed", self._api_retry_failed_jobs, ["POST"], "重试全部失败任务"),
            self._route("/notifications/test", self._api_test_notification, ["POST"], "发送测试通知"),
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
        return self._ok(self._require_store().list_targets(with_inventory=True))

    def _api_create_target(self, payload: Dict[str, Any] = Body(default={})) -> dict:
        try:
            target = self._require_store().save_target(payload)
            task_name = f"target-pool:{target['id']}"
            task = self._start_task(
                task_name, self._process_target_from_pool, target["id"]
            )
            target["pool_task"] = task_name if task.get("success") else None
            return self._ok(target, "目标已新增，正在匹配已扫描种子池")
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
        return self._start_task("targets", self._search_targets, target_ids, False)

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
        cleaned = len(self._require_service().cleanup_obsolete_failed_jobs())
        result = self._require_store().list_jobs(page, page_size)
        result["cleaned_count"] += cleaned
        return self._ok(result)

    def _api_cancel_job(self, job_id: int) -> dict:
        success, message = self._require_store().cancel_job(job_id)
        return self._ok(None, "任务已取消") if success else self._error(message)

    def _api_delete_jobs(self, payload: Dict[str, Any] = Body(default={})) -> dict:
        job_ids = payload.get("job_ids") or []
        if not job_ids:
            return self._error("请至少选择一个下载任务")
        if len(job_ids) > 50:
            return self._error("单次最多删除当前页 50 个任务")
        result = self._require_store().delete_jobs(job_ids)
        return self._ok(
            result,
            f"已删除 {result['deleted']} 个任务，跳过 {result['blocked'] + result['missing']} 个",
        )

    def _api_retry_jobs(self, payload: Dict[str, Any] = Body(default={})) -> dict:
        job_ids = payload.get("job_ids") or []
        if not job_ids:
            return self._error("请至少选择一个下载任务")
        if len(job_ids) > 50:
            return self._error("单次最多重试当前页 50 个任务")
        return self._start_task("job-retry", self._retry_jobs, job_ids, False)

    def _api_retry_failed_jobs(self) -> dict:
        self._require_service().cleanup_obsolete_failed_jobs()
        if not self._require_store().retryable_jobs(all_failed=True):
            return self._error("当前没有失败任务")
        return self._start_task("job-retry", self._retry_jobs, [], True)

    def _api_test_notification(self) -> dict:
        summary = build_test_summary(self._now())
        self.post_message(
            mtype=NotificationType.Plugin,
            title=summary["title"],
            text=summary["text"],
        )
        return self._ok(None, "测试通知已发送")

    def _sync_inventory(self) -> dict:
        return self._run_notified("inventory", self._require_service().sync_inventory)

    def _search_targets(
        self, target_ids: Optional[List[int]] = None, allow_auto_download: bool = True
    ) -> dict:
        return self._run_notified(
            "targets", self._require_service().search_targets, target_ids, allow_auto_download
        )

    def _refresh_pool(self) -> dict:
        return self._run_notified(
            "pool",
            self._require_service().refresh_pool,
            lambda value: self._set_task_progress("pool", value),
        )

    def _auto_download_pool(self) -> dict:
        return self._run_notified("auto-download", self._require_service().download_from_pool)

    def _process_target_from_pool(self, target_id: int) -> dict:
        return self._run_notified(
            f"target-pool:{target_id}",
            self._require_service().process_target_from_pool,
            target_id,
        )

    def _dispatch_downloads(self, candidate_keys: List[str]) -> dict:
        return self._run_notified("downloads", self._dispatch_downloads_result, candidate_keys)

    def _dispatch_downloads_result(self, candidate_keys: List[str]) -> dict:
        results = self._require_service().dispatch(candidate_keys, automatic=False)
        return {
            "submitted": sum(1 for item in results if item.get("success")),
            "failed": sum(1 for item in results if not item.get("success")),
            "results": results,
        }

    def _retry_jobs(self, job_ids: List[int], all_failed: bool) -> dict:
        return self._run_notified(
            "downloads", self._require_service().retry_jobs, job_ids, all_failed
        )

    def _run_notified(self, task_name: str, func, *args) -> dict:
        try:
            result = func(*args)
        except Exception:
            self._safe_send_task_summary(task_name, "failed", None)
            raise
        self._safe_send_task_summary(task_name, "success", result)
        return result

    def _safe_send_task_summary(
        self, task_name: str, status: str, result: Optional[Mapping[str, Any]]
    ) -> None:
        try:
            self._send_task_summary(task_name, status, result)
        except Exception as error:
            logger.warning(f"[联动EMBY库筛选下载] 汇总通知生成失败：{error}")

    def _send_task_summary(
        self, task_name: str, status: str, result: Optional[Mapping[str, Any]]
    ) -> None:
        if not self._config.get("notify_enabled"):
            return
        target = None
        if task_name.startswith("target-pool:"):
            try:
                target_id = int(task_name.split(":", 1)[1])
                target = next((
                    item for item in self._require_store().list_targets(with_inventory=True)
                    if item.get("id") == target_id
                ), None)
            except (TypeError, ValueError):
                target = None
        progress = deepcopy(self._tasks.get(task_name, {}).get("progress") or {})
        summary = build_task_summary(
            task_name,
            status,
            result,
            target=target,
            progress=progress,
            finished_at=self._now(),
        )
        if not summary or not self._config.get(summary["config_key"]):
            return
        try:
            self.post_message(
                mtype=NotificationType.Plugin,
                title=summary["title"],
                text=summary["text"],
            )
        except Exception as error:
            logger.warning(f"[联动EMBY库筛选下载] 汇总通知发送失败：{error}")

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
        result["min_size_4k_gb"] = max(0, cls._to_float(result.get("min_size_4k_gb"), 0))
        result["min_size_1080p_gb"] = max(0, cls._to_float(result.get("min_size_1080p_gb"), 0))
        result["exclude_tv"] = cls._to_bool(result.get("exclude_tv"), True)
        result["proxy_enabled"] = cls._to_bool(result.get("proxy_enabled"), True)
        for key in (
            "notify_enabled", "notify_inventory", "notify_targets",
            "notify_pool", "notify_download", "notify_failures",
        ):
            result[key] = cls._to_bool(result.get(key), True)
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
    def _to_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_bool(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() not in {"0", "false", "no", "off", ""}
        return default if value is None else bool(value)
