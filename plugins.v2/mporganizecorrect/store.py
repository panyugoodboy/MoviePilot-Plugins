"""MP 整理纠正插件的独立持久化存储。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any, Iterable, Mapping, Optional


class CorrectionStore:
    """保存待纠正记录、忽略项、运行元数据和操作审计。"""

    def __init__(self, path: Path):
        """初始化 SQLite 文件并创建所需表。"""

        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._initialize()

    def upsert_record(self, record: Mapping[str, Any]) -> None:
        """新增或更新一条待纠正记录，同时保留用户忽略状态。"""

        now = str(record.get("updated_at") or record.get("created_at") or "")
        values = {
            "history_id": int(record["history_id"]),
            "media_type": str(record.get("media_type") or ""),
            "old_title": str(record.get("old_title") or ""),
            "old_year": int(record.get("old_year") or 0),
            "src": str(record.get("src") or ""),
            "old_dest": str(record.get("old_dest") or ""),
            "query_title": str(record.get("query_title") or ""),
            "query_year": int(record.get("query_year") or 0),
            "mode": str(record.get("mode") or ""),
            "state": str(record.get("state") or "manual"),
            "reason": str(record.get("reason") or ""),
            "candidate_json": self._dump(record.get("candidate") or {}),
            "options_json": self._dump(record.get("options") or []),
            "snapshot_json": self._dump(record.get("snapshot") or {}),
            "created_at": str(record.get("created_at") or now),
            "updated_at": now,
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO corrections (
                    history_id, media_type, old_title, old_year, src, old_dest,
                    query_title, query_year, mode, state, reason, candidate_json,
                    options_json, snapshot_json, created_at, updated_at
                ) VALUES (
                    :history_id, :media_type, :old_title, :old_year, :src, :old_dest,
                    :query_title, :query_year, :mode, :state, :reason, :candidate_json,
                    :options_json, :snapshot_json, :created_at, :updated_at
                )
                ON CONFLICT(history_id) DO UPDATE SET
                    media_type=excluded.media_type,
                    old_title=excluded.old_title,
                    old_year=excluded.old_year,
                    src=excluded.src,
                    old_dest=excluded.old_dest,
                    query_title=excluded.query_title,
                    query_year=excluded.query_year,
                    mode=excluded.mode,
                    state=CASE WHEN corrections.ignored=1 THEN corrections.state ELSE excluded.state END,
                    reason=CASE WHEN corrections.ignored=1 THEN corrections.reason ELSE excluded.reason END,
                    candidate_json=excluded.candidate_json,
                    options_json=excluded.options_json,
                    snapshot_json=excluded.snapshot_json,
                    updated_at=excluded.updated_at
                """,
                values,
            )

    def get_record(self, history_id: int) -> Optional[dict]:
        """按整理历史 ID 获取一条插件记录。"""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM corrections WHERE history_id=?", (int(history_id),)
            ).fetchone()
        return self._record(row) if row else None

    def list_records(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        state: str = "",
        keyword: str = "",
        media_type: str = "",
    ) -> dict:
        """分页查询待纠正记录。"""

        clauses = []
        params: list[Any] = []
        if state:
            if state == "ignored":
                clauses.append("ignored=1")
            else:
                clauses.append("state=? AND ignored=0")
                params.append(state)
        else:
            clauses.append("ignored=0")
        if media_type:
            clauses.append("media_type=?")
            params.append(media_type)
        if keyword:
            clauses.append(
                "(old_title LIKE ? OR query_title LIKE ? OR src LIKE ? OR old_dest LIKE ?)"
            )
            pattern = f"%{keyword}%"
            params.extend([pattern] * 4)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        page = max(1, int(page))
        page_size = max(1, min(100, int(page_size)))
        with self._connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM corrections{where}", params
            ).fetchone()[0]
            rows = connection.execute(
                f"SELECT * FROM corrections{where} ORDER BY updated_at DESC, history_id DESC "
                "LIMIT ? OFFSET ?",
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
        return {"items": [self._record(row) for row in rows], "total": total}

    def list_ready(self, limit: Optional[int] = None) -> list[dict]:
        """获取允许自动纠正的电影精确匹配项；不传数量时返回全部。"""

        sql = """
            SELECT * FROM corrections
            WHERE state='ready' AND ignored=0 AND media_type IN ('电影', 'movie')
            ORDER BY updated_at ASC, history_id ASC
        """
        params = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (max(1, min(50, int(limit))),)
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._record(row) for row in rows]

    def set_state(
        self,
        history_id: int,
        state: str,
        reason: str = "",
        candidate: Optional[Mapping[str, Any]] = None,
        updated_at: str = "",
    ) -> None:
        """更新一条记录的处理状态。"""

        assignments = ["state=?", "reason=?", "updated_at=?"]
        params: list[Any] = [state, reason, updated_at]
        if candidate is not None:
            assignments.append("candidate_json=?")
            params.append(self._dump(candidate))
        params.append(int(history_id))
        with self._connect() as connection:
            connection.execute(
                f"UPDATE corrections SET {', '.join(assignments)} WHERE history_id=?", params
            )

    def set_ignored(self, history_ids: Iterable[int], ignored: bool, updated_at: str) -> int:
        """批量设置或取消永久忽略。"""

        ids = sorted({int(value) for value in history_ids})
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE corrections SET ignored=?, updated_at=? WHERE history_id IN ({placeholders})",
                [1 if ignored else 0, updated_at, *ids],
            )
        return cursor.rowcount

    def clear_records(self) -> int:
        """清空本插件纠正记录和扫描游标，保留操作审计。"""

        with self._connect() as connection:
            count = int(connection.execute("SELECT COUNT(*) FROM corrections").fetchone()[0])
            connection.execute("DELETE FROM corrections")
            connection.execute(
                "DELETE FROM metadata WHERE key IN ('last_scan_date', 'last_scan_at')"
            )
        return count

    def stats(self) -> dict:
        """汇总插件首页需要的状态数量。"""

        result = {
            "total": 0,
            "ready": 0,
            "manual": 0,
            "failed": 0,
            "cleanup_pending": 0,
            "corrected": 0,
            "ignored": 0,
        }
        with self._connect() as connection:
            for row in connection.execute(
                "SELECT state, ignored, COUNT(*) AS count FROM corrections GROUP BY state, ignored"
            ).fetchall():
                count = int(row["count"])
                result["total"] += count
                if row["ignored"]:
                    result["ignored"] += count
                elif row["state"] in result:
                    result[row["state"]] += count
        return result

    def add_audit(self, item: Mapping[str, Any]) -> int:
        """写入一条不可覆盖的操作审计。"""

        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO audits (
                    action, history_id, old_title, new_title, src, old_dest, new_dest,
                    media_source, media_id, status, message, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(item.get("action") or ""),
                    int(item.get("history_id") or 0),
                    str(item.get("old_title") or ""),
                    str(item.get("new_title") or ""),
                    str(item.get("src") or ""),
                    str(item.get("old_dest") or ""),
                    str(item.get("new_dest") or ""),
                    str(item.get("media_source") or ""),
                    str(item.get("media_id") or ""),
                    str(item.get("status") or ""),
                    str(item.get("message") or ""),
                    str(item.get("created_at") or ""),
                ),
            )
        return int(cursor.lastrowid)

    def list_audits(self, page: int = 1, page_size: int = 50) -> dict:
        """分页查询操作审计。"""

        page = max(1, int(page))
        page_size = max(1, min(100, int(page_size)))
        with self._connect() as connection:
            total = connection.execute("SELECT COUNT(*) FROM audits").fetchone()[0]
            rows = connection.execute(
                "SELECT * FROM audits ORDER BY id DESC LIMIT ? OFFSET ?",
                (page_size, (page - 1) * page_size),
            ).fetchall()
        return {"items": [dict(row) for row in rows], "total": total}

    def get_meta(self, key: str, default: str = "") -> str:
        """读取插件运行元数据。"""

        with self._connect() as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def set_meta(self, key: str, value: Any) -> None:
        """写入插件运行元数据。"""

        with self._connect() as connection:
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS corrections (
                    history_id INTEGER PRIMARY KEY,
                    media_type TEXT NOT NULL DEFAULT '',
                    old_title TEXT NOT NULL DEFAULT '',
                    old_year INTEGER NOT NULL DEFAULT 0,
                    src TEXT NOT NULL DEFAULT '',
                    old_dest TEXT NOT NULL DEFAULT '',
                    query_title TEXT NOT NULL DEFAULT '',
                    query_year INTEGER NOT NULL DEFAULT 0,
                    mode TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'manual',
                    reason TEXT NOT NULL DEFAULT '',
                    candidate_json TEXT NOT NULL DEFAULT '{}',
                    options_json TEXT NOT NULL DEFAULT '[]',
                    snapshot_json TEXT NOT NULL DEFAULT '{}',
                    ignored INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_corrections_state
                    ON corrections(state, ignored, updated_at);
                CREATE TABLE IF NOT EXISTS audits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT NOT NULL DEFAULT '',
                    history_id INTEGER NOT NULL DEFAULT 0,
                    old_title TEXT NOT NULL DEFAULT '',
                    new_title TEXT NOT NULL DEFAULT '',
                    src TEXT NOT NULL DEFAULT '',
                    old_dest TEXT NOT NULL DEFAULT '',
                    new_dest TEXT NOT NULL DEFAULT '',
                    media_source TEXT NOT NULL DEFAULT '',
                    media_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_audits_created_at ON audits(created_at);
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT ''
                );
                """
            )

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return _LockedConnection(connection, self._lock)

    @classmethod
    def _record(cls, row: sqlite3.Row) -> dict:
        item = dict(row)
        item["ignored"] = bool(item.get("ignored"))
        item["candidate"] = cls._load(item.pop("candidate_json", "{}"), {})
        item["options"] = cls._load(item.pop("options_json", "[]"), [])
        item["snapshot"] = cls._load(item.pop("snapshot_json", "{}"), {})
        return item

    @staticmethod
    def _dump(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)

    @staticmethod
    def _load(value: str, default: Any) -> Any:
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default


class _LockedConnection:
    """在一次 SQLite 上下文操作期间持有进程内可重入锁。"""

    def __init__(self, connection: sqlite3.Connection, lock: RLock):
        self.connection = connection
        self.lock = lock

    def __enter__(self) -> sqlite3.Connection:
        self.lock.acquire()
        return self.connection

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        try:
            if exc_type:
                self.connection.rollback()
            else:
                self.connection.commit()
        finally:
            self.connection.close()
            self.lock.release()
