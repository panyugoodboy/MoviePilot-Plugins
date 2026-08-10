"""MP 整理纠正的扫描、预览和安全重整服务。"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import re
from threading import Lock
from typing import Any, Callable, Iterable, Mapping, Optional

from app.chain.media import MediaChain
from app.chain.storage import StorageChain
from app.chain.transfer import TransferChain
from app.core.config import settings
from app.core.metainfo import MetaInfoPath
from app.db.transferhistory_oper import TransferHistoryOper
from app.helper.directory import DirectoryHelper
from app.log import logger
from app.schemas import FileItem
from app.schemas.types import MediaType

from .matcher import (
    choose_exact_candidate,
    cleanup_paths_are_safe,
    destination_label,
    extract_source_identity,
    has_han,
    is_english_label,
    safe_transfer_mode,
    serialize_media,
)
from .store import CorrectionStore


HISTORY_FIELDS = (
    "id", "src", "src_storage", "src_fileitem", "dest", "dest_storage",
    "dest_fileitem", "mode", "type", "category", "title", "year", "tmdbid",
    "imdbid", "tvdbid", "doubanid", "bangumiid", "anilistid", "media_source",
    "media_id", "seasons", "episodes", "image", "downloader", "download_hash",
    "status", "errmsg", "date", "files", "episode_group",
)


class OrganizeCorrectService:
    """协调 MoviePilot 现有识别、整理、存储和历史记录能力。"""

    def __init__(
        self,
        store: CorrectionStore,
        config_getter: Callable[[], Mapping[str, Any]],
    ):
        """初始化服务并建立跨入口互斥锁。"""

        self.store = store
        self.config_getter = config_getter
        self._operation_lock = Lock()

    def scan(
        self,
        *,
        full: bool = False,
        progress: Optional[Callable[[dict], None]] = None,
    ) -> dict:
        """扫描新增或全部成功整理记录并搜索中文候选。"""

        if not self._operation_lock.acquire(blocking=False):
            raise RuntimeError("已有扫描、纠正或清理任务正在运行")
        try:
            return self._scan(full=full, progress=progress)
        finally:
            self._operation_lock.release()

    def scheduled_run(
        self,
        progress: Optional[Callable[[dict], None]] = None,
    ) -> dict:
        """执行 Cron 增量扫描，并按配置限量自动纠正精确电影结果。"""

        if not self._operation_lock.acquire(blocking=False):
            raise RuntimeError("已有扫描、纠正或清理任务正在运行")
        try:
            scan_result = self._scan(full=False, progress=progress)
            config = dict(self.config_getter() or {})
            correct_result = {"total": 0, "success": 0, "failed": 0, "items": []}
            if config.get("auto_correct"):
                ready = self.store.list_ready(int(config.get("auto_batch_limit") or 5))
                items = [
                    {"history_id": item["history_id"], "candidate": item.get("candidate") or {}}
                    for item in ready
                ]
                correct_result = self._correct_records(
                    items,
                    cleanup_old=bool(config.get("cleanup_old_after_correct", True)),
                    automatic=True,
                    progress=progress,
                )
            return {"scan": scan_result, "correct": correct_result}
        finally:
            self._operation_lock.release()

    def search_record(
        self,
        history_id: int,
        *,
        title: str,
        year: int,
        media_type: str,
    ) -> list[dict]:
        """按中英文片名、可选年份和类型返回模糊搜索候选。"""

        if not self.store.get_record(history_id):
            raise ValueError("待纠正记录不存在")
        title = str(title or "").strip()
        if not title:
            raise ValueError("搜索片名不能为空")
        year = self._int(year)
        if year and not 1888 <= year <= 2100:
            raise ValueError("年份必须是 1888–2100 的四位数字，或留空")
        return self._search_candidates(title, year, media_type)

    def preview(self, history_id: int, candidate: Mapping[str, Any]) -> dict:
        """使用 MoviePilot 底层整理模块预览路径，全程不改记录也不动文件。"""

        record = self._require_record(history_id)
        history = self._require_current_history(record)
        return self._preview_record(record, history, candidate)

    def correct_records(
        self,
        items: Iterable[Mapping[str, Any]],
        *,
        cleanup_old: bool,
        automatic: bool = False,
        progress: Optional[Callable[[dict], None]] = None,
    ) -> dict:
        """批量执行安全重整；任何失败都不会删除旧媒体文件。"""

        if not self._operation_lock.acquire(blocking=False):
            raise RuntimeError("已有扫描、纠正或清理任务正在运行")
        try:
            return self._correct_records(
                items,
                cleanup_old=cleanup_old,
                automatic=automatic,
                progress=progress,
            )
        finally:
            self._operation_lock.release()

    def correct_all_ready(
        self,
        *,
        cleanup_old: bool,
        progress: Optional[Callable[[dict], None]] = None,
    ) -> dict:
        """纠正全部唯一精确匹配电影，不受分页或批次数量限制。"""

        if not self._operation_lock.acquire(blocking=False):
            raise RuntimeError("已有扫描、纠正或清理任务正在运行")
        try:
            items = [
                {"history_id": record["history_id"], "candidate": record.get("candidate") or {}}
                for record in self.store.list_ready()
            ]
            return self._correct_records(
                items,
                cleanup_old=cleanup_old,
                automatic=True,
                progress=progress,
            )
        finally:
            self._operation_lock.release()

    def set_ignored(self, history_ids: Iterable[int], ignored: bool) -> int:
        """批量永久忽略或恢复待纠正记录。"""

        return self.store.set_ignored(history_ids, ignored, self._now())

    def cleanup_records(self, history_ids: Iterable[int]) -> dict:
        """重试删除已经纠正成功但尚未清理的旧目标媒体。"""

        if not self._operation_lock.acquire(blocking=False):
            raise RuntimeError("已有扫描、纠正或清理任务正在运行")
        try:
            results = []
            for history_id in sorted({int(value) for value in history_ids}):
                record = self._require_record(history_id)
                current = TransferHistoryOper().get_by_src(
                    record["src"], storage=(record.get("snapshot") or {}).get("src_storage")
                )
                if not current or not current.status:
                    results.append({"history_id": history_id, "success": False, "message": "新整理记录不存在"})
                    continue
                success, message = self._delete_old_destination(record, current)
                state = "corrected" if success else "cleanup_pending"
                self.store.set_state(history_id, state, message, updated_at=self._now())
                results.append({"history_id": history_id, "success": success, "message": message})
            return self._summarize(results)
        finally:
            self._operation_lock.release()

    def delete_records(
        self,
        history_ids: Iterable[int],
        *,
        delete_media: bool,
        delete_history: bool,
    ) -> dict:
        """按旧目标快照删除媒体和/或原整理记录，永不触碰源文件。"""

        if not delete_media and not delete_history:
            raise ValueError("至少选择删除旧媒体或原整理记录中的一项")
        if not self._operation_lock.acquire(blocking=False):
            raise RuntimeError("已有扫描、纠正或清理任务正在运行")
        try:
            results = []
            for history_id in sorted({int(value) for value in history_ids}):
                record = self._require_record(history_id)
                snapshot = record.get("snapshot") or {}
                messages = []
                success = True
                if delete_media:
                    safe, reason = cleanup_paths_are_safe(
                        source_storage=snapshot.get("src_storage"),
                        source_path=record.get("src"),
                        old_storage=snapshot.get("dest_storage"),
                        old_path=record.get("old_dest"),
                        new_storage="",
                        new_path="",
                    )
                    if not safe:
                        success, messages = False, [reason]
                    else:
                        old_item = self._destination_fileitem(record)
                        if self._file_exists(old_item) and not StorageChain().delete_media_file(old_item):
                            success, messages = False, ["旧媒体删除失败"]
                        else:
                            messages.append("旧媒体已删除或原本不存在")
                if success and delete_history:
                    current = TransferHistoryOper().get(history_id)
                    if current and self._history_matches_record(current, record):
                        TransferHistoryOper().delete(history_id)
                        messages.append("原整理记录已删除")
                    elif current:
                        success = False
                        messages.append("整理记录已变化，拒绝删除")
                    else:
                        messages.append("原整理记录已不存在")
                state = "deleted" if success else "failed"
                message = "；".join(messages)
                self.store.set_state(history_id, state, message, updated_at=self._now())
                self._audit(record, {}, action="delete", status="success" if success else "failed", message=message)
                results.append({"history_id": history_id, "success": success, "message": message})
            return self._summarize(results)
        finally:
            self._operation_lock.release()

    def _scan(
        self,
        *,
        full: bool,
        progress: Optional[Callable[[dict], None]],
    ) -> dict:
        scan_started = datetime.now()
        since = "1970-01-01 00:00:00" if full else self._incremental_since()
        histories = list(reversed(TransferHistoryOper().list_by_date(since) or []))
        result = {"checked": len(histories), "listed": 0, "ready": 0, "manual": 0, "failed": 0}
        for index, history in enumerate(histories, start=1):
            if progress:
                progress({
                    "current": index,
                    "total": len(histories),
                    "message": (
                        f"正在检查 {index}/{len(histories)} · "
                        f"{self._source_name(getattr(history, 'src', ''))}"
                    ),
                })
            try:
                record = self._analyze_history(history)
                if not record:
                    continue
                self.store.upsert_record(record)
                result["listed"] += 1
                if record["state"] in result:
                    result[record["state"]] += 1
            except Exception as error:
                result["failed"] += 1
                logger.error(f"[MP整理纠正] 检查整理记录 #{getattr(history, 'id', 0)} 失败：{error}")
        self.store.set_meta("last_scan_date", scan_started.strftime("%Y-%m-%d %H:%M:%S"))
        self.store.set_meta("last_scan_at", self._now())
        return result

    def _analyze_history(self, history: Any) -> Optional[dict]:
        if not history or not history.status or not history.src or not history.dest:
            return None
        old_title = destination_label(history.dest, history.title, history.year)
        if not is_english_label(old_title):
            return None
        snapshot = self._history_snapshot(history)
        source_item = self._source_fileitem(snapshot)
        parsed_title, parsed_year = "", ""
        try:
            meta = MetaInfoPath(Path(history.src))
            parsed_title = getattr(meta, "cn_name", None) or getattr(meta, "name", "")
            parsed_year = getattr(meta, "year", "")
        except Exception as error:
            logger.debug(f"[MP整理纠正] 源路径元数据解析失败，使用文本回退：{error}")
        query_title, query_year = extract_source_identity(
            history.src,
            parsed_title=parsed_title,
            parsed_year=parsed_year,
            history_year=history.year,
        )
        state, reason, candidate, options = "manual", "", {}, []
        transfer_mode = safe_transfer_mode(history.mode)
        if not self._file_exists(source_item):
            state, reason = "missing_source", "源文件不存在，禁止重新整理"
        elif not transfer_mode:
            state, reason = "manual", f"整理方式 {history.mode or '未知'} 无法确认不会删除源文件"
        elif not query_title:
            state, reason = "manual", "源路径中未提取到可搜索片名"
        else:
            try:
                options = self._search_candidates(query_title, query_year, history.type)
                if query_year:
                    candidate, reason = choose_exact_candidate(
                        query_title, query_year, history.type, options
                    )
                else:
                    candidate = None
                    reason = "年份未提取，已按片名模糊搜索，需要人工选择"
                if candidate:
                    state = "ready" if self._media_type(history.type) == MediaType.MOVIE else "manual"
                    if state == "manual":
                        reason = "电视剧已精确命中，首版需人工确认季集后整理"
                else:
                    state = "manual"
            except Exception as error:
                state, reason = "failed", f"媒体搜索失败：{error}"
        now = self._now()
        return {
            "history_id": history.id,
            "media_type": history.type or "",
            "old_title": old_title,
            "old_year": self._int(history.year),
            "src": history.src,
            "old_dest": history.dest,
            "query_title": query_title,
            "query_year": query_year,
            "mode": history.mode or "",
            "state": state,
            "reason": reason,
            "candidate": candidate or {},
            "options": options,
            "snapshot": snapshot,
            "created_at": now,
            "updated_at": now,
        }

    def _search_candidates(self, title: str, year: int, media_type: str) -> list[dict]:
        query = f"{title} {year}" if year else title
        _, medias = MediaChain().search(query)
        if not medias and year:
            _, medias = MediaChain().search(title)
        expected = self._media_type(media_type)
        results = []
        seen = set()
        for media in medias or []:
            item = serialize_media(media)
            if expected and self._media_type(item.get("media_type")) != expected:
                continue
            key = (item.get("media_source"), item.get("media_id"), item.get("media_type"))
            if not item.get("media_id") or key in seen:
                continue
            seen.add(key)
            results.append(item)
        results.sort(
            key=lambda item: (
                abs(int(item.get("year") or 0) - int(year)) if year else 0,
                0 if has_han(item.get("title")) else 1,
                str(item.get("title") or ""),
            )
        )
        return results[:30]

    def _preview_record(
        self,
        record: Mapping[str, Any],
        history: Any,
        candidate: Mapping[str, Any],
    ) -> dict:
        media = self._recognize_candidate(candidate, history.type)
        fileitem = self._source_fileitem(record.get("snapshot") or {})
        if not self._file_exists(fileitem):
            raise RuntimeError("源文件不存在，已阻止预览和整理")
        transfer_mode = safe_transfer_mode(history.mode)
        if not transfer_mode:
            raise RuntimeError(f"整理方式 {history.mode or '未知'} 无法确认安全")
        meta = MetaInfoPath(Path(fileitem.path))
        season = self._season_number(history.seasons)
        if season is not None:
            meta.begin_season = season
        target_directory = DirectoryHelper().get_dir(
            media=media,
            storage=fileitem.storage,
            src_path=Path(fileitem.path),
            target_storage=history.dest_storage,
        )
        transfer_info = TransferChain().transfer(
            fileitem=fileitem,
            meta=meta,
            mediainfo=media,
            target_directory=target_directory,
            target_storage=history.dest_storage,
            transfer_type=transfer_mode,
            preview=True,
        )
        if not transfer_info or not transfer_info.success or not transfer_info.target_item:
            message = getattr(transfer_info, "message", "") if transfer_info else "整理预览失败"
            raise RuntimeError(message or "整理预览失败")
        target = transfer_info.target_item
        if not target.path or str(target.path).replace("\\", "/") == str(history.dest).replace("\\", "/"):
            raise RuntimeError("预览目标仍是旧英文路径，请重新选择中文候选")
        return {
            "history_id": record["history_id"],
            "source": {"storage": fileitem.storage, "path": fileitem.path, "locked": True},
            "old_target": {"storage": history.dest_storage, "path": history.dest},
            "new_target": {"storage": target.storage, "path": target.path},
            "transfer_mode": transfer_mode,
            "candidate": dict(candidate),
            "message": transfer_info.message or "预览成功",
        }

    def _correct_records(
        self,
        items: Iterable[Mapping[str, Any]],
        *,
        cleanup_old: bool,
        automatic: bool,
        progress: Optional[Callable[[dict], None]],
    ) -> dict:
        values = list(items or [])
        results = []
        for index, value in enumerate(values, start=1):
            history_id = int(value.get("history_id") or 0)
            record = None
            candidate = value.get("candidate") or {}
            try:
                record = self._require_record(history_id)
                if progress:
                    progress({
                        "current": index,
                        "total": len(values),
                        "message": (
                            f"正在纠正 {index}/{len(values)} · "
                            f"{self._source_name(record.get('src'))}"
                        ),
                    })
                if automatic and (record.get("state") != "ready" or self._media_type(record.get("media_type")) != MediaType.MOVIE):
                    raise RuntimeError("该记录不符合自动纠正条件")
                candidate = candidate or record.get("candidate") or {}
                results.append(self._correct_one(record, candidate, cleanup_old=cleanup_old))
            except Exception as error:
                if history_id:
                    self.store.set_state(history_id, "failed", str(error), updated_at=self._now())
                if record:
                    self._audit(record, candidate, action="correct", status="failed", message=str(error))
                results.append({"history_id": history_id, "success": False, "message": str(error)})
        return self._summarize(results)

    def _correct_one(
        self,
        record: Mapping[str, Any],
        candidate: Mapping[str, Any],
        *,
        cleanup_old: bool,
    ) -> dict:
        history = self._require_current_history(record)
        preview = self._preview_record(record, history, candidate)
        media = self._recognize_candidate(candidate, history.type)
        fileitem = self._source_fileitem(record.get("snapshot") or {})
        transfer_mode = safe_transfer_mode(history.mode)
        snapshot = self._history_snapshot(history)
        TransferHistoryOper().delete(history.id)
        transfer_succeeded = False
        try:
            original_follow_title = settings.SCRAP_FOLLOW_TMDB
            settings.SCRAP_FOLLOW_TMDB = True
            try:
                state, message = TransferChain().do_transfer(
                    fileitem=fileitem,
                    mediainfo=media,
                    target_storage=history.dest_storage,
                    transfer_type=transfer_mode,
                    season=self._season_number(history.seasons),
                    force=True,
                    background=False,
                    manual=True,
                    downloader=history.downloader,
                    download_hash=history.download_hash,
                    sync_extra_files=True,
                )
            finally:
                settings.SCRAP_FOLLOW_TMDB = original_follow_title
            if not state:
                raise RuntimeError(str(message or "重新整理失败"))
            transfer_succeeded = True
        except Exception:
            self._restore_history(snapshot)
            raise

        current = TransferHistoryOper().get_by_src(fileitem.path, storage=fileitem.storage)
        if not current or not current.status or not current.dest:
            raise RuntimeError("重新整理已返回成功，但未找到新的成功整理记录")
        if not self._candidate_matches_history(candidate, current):
            raise RuntimeError("新整理记录的媒体 ID 与所选候选不一致")
        if not self._file_exists(self._source_fileitem(snapshot)):
            raise RuntimeError("安全校验失败：重新整理后源文件不存在")
        new_item = self._history_destination_fileitem(current)
        if not self._file_exists(new_item):
            raise RuntimeError("新整理目标尚未确认存在，旧媒体保持不动")

        cleanup_message = "旧媒体按本次设置保留"
        success_state = "corrected"
        if cleanup_old:
            cleanup_success, cleanup_message = self._delete_old_destination(record, current)
            if not cleanup_success:
                success_state = "cleanup_pending"
        self.store.set_state(
            record["history_id"],
            success_state,
            cleanup_message,
            candidate=candidate,
            updated_at=self._now(),
        )
        self._audit(
            record,
            candidate,
            action="correct",
            status="success" if success_state == "corrected" else "cleanup_pending",
            message=cleanup_message,
            new_dest=current.dest,
        )
        return {
            "history_id": record["history_id"],
            "success": True,
            "state": success_state,
            "message": cleanup_message,
            "old_dest": record.get("old_dest"),
            "new_dest": current.dest,
            "preview": preview,
            "transfer_succeeded": transfer_succeeded,
        }

    def _delete_old_destination(self, record: Mapping[str, Any], current: Any) -> tuple[bool, str]:
        snapshot = record.get("snapshot") or {}
        safe, reason = cleanup_paths_are_safe(
            source_storage=snapshot.get("src_storage"),
            source_path=record.get("src"),
            old_storage=snapshot.get("dest_storage"),
            old_path=record.get("old_dest"),
            new_storage=getattr(current, "dest_storage", ""),
            new_path=getattr(current, "dest", ""),
        )
        if not safe:
            return False, f"旧媒体未删除：{reason}"
        old_item = self._destination_fileitem(record)
        if not self._file_exists(old_item):
            return True, "旧媒体原本不存在，无需清理"
        if not StorageChain().delete_media_file(old_item):
            return False, "新整理已完成，但旧媒体删除失败，可在待清理列表重试"
        return True, "新整理已验证，旧英文媒体已安全删除"

    def _recognize_candidate(self, candidate: Mapping[str, Any], fallback_type: str):
        source = str(candidate.get("media_source") or "").strip()
        media_id = str(candidate.get("media_id") or "").strip()
        media_type = self._media_type(candidate.get("media_type") or fallback_type)
        if not source or not media_id or not media_type:
            raise ValueError("候选缺少媒体来源、媒体 ID 或类型")
        media = MediaChain().recognize_media(source=source, mediaid=media_id, mtype=media_type)
        if not media:
            raise RuntimeError("无法按所选媒体 ID 获取详情")
        selected_title = str(candidate.get("title") or "").strip()
        if not has_han(selected_title):
            raise ValueError("所选候选没有中文标题")
        media.title = selected_title
        if candidate.get("year"):
            media.year = str(candidate["year"])
        return media

    def _require_record(self, history_id: int) -> dict:
        record = self.store.get_record(int(history_id))
        if not record:
            raise ValueError("待纠正记录不存在")
        return record

    def _require_current_history(self, record: Mapping[str, Any]):
        history = TransferHistoryOper().get(int(record["history_id"]))
        if not history:
            raise RuntimeError("原整理记录已不存在，请重新扫描")
        if not self._history_matches_record(history, record):
            raise RuntimeError("原整理记录的源路径或目标路径已经变化，请重新扫描")
        return history

    @staticmethod
    def _history_matches_record(history: Any, record: Mapping[str, Any]) -> bool:
        return (
            str(getattr(history, "src", "") or "") == str(record.get("src") or "")
            and str(getattr(history, "dest", "") or "") == str(record.get("old_dest") or "")
        )

    @classmethod
    def _history_snapshot(cls, history: Any) -> dict:
        return {
            field: getattr(history, field)
            for field in HISTORY_FIELDS
            if hasattr(history, field)
        }

    @staticmethod
    def _restore_history(snapshot: Mapping[str, Any]) -> None:
        current = TransferHistoryOper().get_by_src(
            str(snapshot.get("src") or ""), storage=snapshot.get("src_storage")
        )
        if current:
            TransferHistoryOper().delete(current.id)
        TransferHistoryOper().add(**dict(snapshot))

    @classmethod
    def _source_fileitem(cls, snapshot: Mapping[str, Any]) -> FileItem:
        data = dict(snapshot.get("src_fileitem") or {})
        data.setdefault("path", snapshot.get("src") or "")
        data.setdefault("storage", snapshot.get("src_storage") or "local")
        return cls._complete_fileitem(data)

    @classmethod
    def _destination_fileitem(cls, record: Mapping[str, Any]) -> FileItem:
        snapshot = record.get("snapshot") or {}
        data = dict(snapshot.get("dest_fileitem") or {})
        data.setdefault("path", record.get("old_dest") or snapshot.get("dest") or "")
        data.setdefault("storage", snapshot.get("dest_storage") or "local")
        return cls._complete_fileitem(data)

    @classmethod
    def _history_destination_fileitem(cls, history: Any) -> FileItem:
        data = dict(getattr(history, "dest_fileitem", None) or {})
        data.setdefault("path", getattr(history, "dest", "") or "")
        data.setdefault("storage", getattr(history, "dest_storage", "") or "local")
        return cls._complete_fileitem(data)

    @staticmethod
    def _complete_fileitem(data: Mapping[str, Any]) -> FileItem:
        value = dict(data)
        path = Path(str(value.get("path") or ""))
        value.setdefault("name", path.name)
        value.setdefault("basename", path.stem)
        value.setdefault("extension", path.suffix.lstrip("."))
        value.setdefault("type", "file" if path.suffix else "dir")
        return FileItem(**value)

    @staticmethod
    def _file_exists(fileitem: FileItem) -> bool:
        if not fileitem or not fileitem.path:
            return False
        if (fileitem.storage or "local") == "local":
            return Path(fileitem.path).exists()
        return bool(StorageChain().get_item(fileitem))

    @staticmethod
    def _candidate_matches_history(candidate: Mapping[str, Any], history: Any) -> bool:
        source = str(candidate.get("media_source") or "")
        media_id = str(candidate.get("media_id") or "")
        history_ids = {
            "themoviedb": getattr(history, "tmdbid", None),
            "douban": getattr(history, "doubanid", None),
            "bangumi": getattr(history, "bangumiid", None),
            "anilist": getattr(history, "anilistid", None),
        }
        actual = getattr(history, "media_id", None) if getattr(history, "media_source", None) == source else history_ids.get(source)
        return bool(media_id and actual is not None and str(actual) == media_id)

    def _audit(
        self,
        record: Mapping[str, Any],
        candidate: Mapping[str, Any],
        *,
        action: str,
        status: str,
        message: str,
        new_dest: str = "",
    ) -> None:
        self.store.add_audit({
            "action": action,
            "history_id": record.get("history_id"),
            "old_title": record.get("old_title"),
            "new_title": candidate.get("title"),
            "src": record.get("src"),
            "old_dest": record.get("old_dest"),
            "new_dest": new_dest,
            "media_source": candidate.get("media_source"),
            "media_id": candidate.get("media_id"),
            "status": status,
            "message": message,
            "created_at": self._now(),
        })

    def _incremental_since(self) -> str:
        value = self.store.get_meta("last_scan_date", "1970-01-01 00:00:00")
        try:
            return (datetime.strptime(value, "%Y-%m-%d %H:%M:%S") - timedelta(seconds=2)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        except ValueError:
            return "1970-01-01 00:00:00"

    @staticmethod
    def _season_number(value: Any) -> Optional[int]:
        match = re.search(r"\d+", str(value or ""))
        return int(match.group()) if match else None

    @staticmethod
    def _media_type(value: Any) -> Optional[MediaType]:
        value = getattr(value, "value", value)
        text = str(value or "").strip().casefold()
        if text in {"movie", "电影"} or "movie" in text:
            return MediaType.MOVIE
        if text in {"tv", "电视剧"} or text == "tv":
            return MediaType.TV
        return None

    @staticmethod
    def _summarize(items: list[dict]) -> dict:
        return {
            "total": len(items),
            "success": sum(1 for item in items if item.get("success")),
            "failed": sum(1 for item in items if not item.get("success")),
            "items": items,
        }

    @staticmethod
    def _int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _source_name(value: Any) -> str:
        return str(value or "未知源文件").replace("\\", "/").rsplit("/", 1)[-1]

    @staticmethod
    def _now() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")
