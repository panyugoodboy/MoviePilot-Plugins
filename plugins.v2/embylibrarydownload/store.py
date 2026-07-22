"""SQLite persistence for the Emby library download plugin."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Iterable, Iterator, Mapping, Optional


ACTIVE_JOB_STATES = ("reserved", "queued", "downloading")
DUPLICATE_JOB_STATES = (*ACTIVE_JOB_STATES, "present")
JSON_COLUMNS = {
    "profile_json": "profile",
    "sites_json": "sites",
    "seasons_json": "seasons",
    "torrent_json": "torrent",
    "media_json": "media",
    "meta_json": "meta",
    "quality_json": "quality",
    "media_keys_json": "media_keys",
    "baseline_json": "baseline",
}


def utcnow() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


class PluginStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._schema_lock = RLock()
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self._schema_lock, self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS inventory_versions (
                    version_key TEXT PRIMARY KEY,
                    media_key TEXT NOT NULL,
                    server_name TEXT NOT NULL,
                    library_id TEXT,
                    item_id TEXT NOT NULL,
                    media_source_id TEXT,
                    item_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    original_title TEXT,
                    year INTEGER,
                    season INTEGER,
                    episode INTEGER,
                    path TEXT,
                    tmdb_id TEXT,
                    imdb_id TEXT,
                    tvdb_id TEXT,
                    quality_type TEXT,
                    quality_effect TEXT,
                    resolution TEXT,
                    video_codec TEXT,
                    audio_codec TEXT,
                    bitrate_mbps REAL DEFAULT 0,
                    size_bytes INTEGER DEFAULT 0,
                    quality_slot TEXT,
                    date_created TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_inventory_media_key
                    ON inventory_versions(media_key);
                CREATE INDEX IF NOT EXISTS ix_inventory_server_library
                    ON inventory_versions(server_name, library_id);

                CREATE TABLE IF NOT EXISTS targets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    media_type TEXT NOT NULL,
                    media_source TEXT NOT NULL DEFAULT 'themoviedb',
                    media_id TEXT,
                    title TEXT NOT NULL,
                    original_title TEXT,
                    poster_url TEXT,
                    year INTEGER,
                    seasons_json TEXT NOT NULL DEFAULT '[]',
                    desired_versions INTEGER NOT NULL DEFAULT 1,
                    sites_json TEXT NOT NULL DEFAULT '[]',
                    profile_json TEXT NOT NULL DEFAULT '{}',
                    save_path TEXT,
                    auto_download INTEGER NOT NULL DEFAULT 0,
                    prefer_scanned_pool INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS candidates (
                    candidate_key TEXT PRIMARY KEY,
                    torrent_key TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    target_id INTEGER,
                    site_id INTEGER,
                    site_name TEXT,
                    title TEXT NOT NULL,
                    description TEXT,
                    page_url TEXT,
                    enclosure TEXT,
                    size_bytes INTEGER DEFAULT 0,
                    seeders INTEGER DEFAULT 0,
                    peers INTEGER DEFAULT 0,
                    pubdate TEXT,
                    year INTEGER,
                    quality_type TEXT,
                    quality_effect TEXT,
                    resolution TEXT,
                    video_codec TEXT,
                    bitrate_mbps REAL DEFAULT 0,
                    quality_score INTEGER DEFAULT 0,
                    quality_slot TEXT,
                    eligible INTEGER NOT NULL DEFAULT 1,
                    rejection_reason TEXT,
                    torrent_json TEXT NOT NULL,
                    meta_json TEXT NOT NULL DEFAULT '{}',
                    media_json TEXT NOT NULL DEFAULT '{}',
                    media_keys_json TEXT NOT NULL DEFAULT '[]',
                    discovered_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_candidates_scope_sort
                    ON candidates(scope, year DESC, quality_score DESC, seeders DESC);
                CREATE INDEX IF NOT EXISTS ix_candidates_target
                    ON candidates(target_id);
                CREATE INDEX IF NOT EXISTS ix_candidates_torrent
                    ON candidates(torrent_key);

                CREATE TABLE IF NOT EXISTS download_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    candidate_key TEXT NOT NULL,
                    torrent_key TEXT NOT NULL,
                    target_id INTEGER,
                    title TEXT NOT NULL,
                    site_name TEXT,
                    media_keys_json TEXT NOT NULL,
                    quality_slot TEXT,
                    baseline_json TEXT NOT NULL DEFAULT '{}',
                    save_path TEXT,
                    automatic INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    download_id TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_download_jobs_status
                    ON download_jobs(status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS ix_download_jobs_candidate
                    ON download_jobs(candidate_key);

                CREATE TABLE IF NOT EXISTS plugin_state (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT NOT NULL
                );
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(inventory_versions)")}
            if "date_created" not in columns:
                conn.execute("ALTER TABLE inventory_versions ADD COLUMN date_created TEXT")
            target_columns = {row["name"] for row in conn.execute("PRAGMA table_info(targets)")}
            if "prefer_scanned_pool" not in target_columns:
                conn.execute("ALTER TABLE targets ADD COLUMN prefer_scanned_pool INTEGER NOT NULL DEFAULT 0")
            if "poster_url" not in target_columns:
                conn.execute("ALTER TABLE targets ADD COLUMN poster_url TEXT")
            if "original_title" not in target_columns:
                conn.execute("ALTER TABLE targets ADD COLUMN original_title TEXT")

    def replace_inventory(self, server_name: str, rows: list[Mapping[str, Any]]) -> int:
        now = utcnow()
        with self.connect() as conn:
            conn.execute("DELETE FROM inventory_versions WHERE server_name=?", (server_name,))
            for row in rows:
                values = dict(row)
                values.setdefault("date_created", None)
                values.setdefault("updated_at", now)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO inventory_versions (
                        version_key, media_key, server_name, library_id, item_id, media_source_id,
                        item_type, title, original_title, year, season, episode, path, tmdb_id,
                        imdb_id, tvdb_id, quality_type, quality_effect, resolution, video_codec,
                        audio_codec, bitrate_mbps, size_bytes, quality_slot, date_created, updated_at
                    ) VALUES (
                        :version_key, :media_key, :server_name, :library_id, :item_id, :media_source_id,
                        :item_type, :title, :original_title, :year, :season, :episode, :path, :tmdb_id,
                        :imdb_id, :tvdb_id, :quality_type, :quality_effect, :resolution, :video_codec,
                        :audio_codec, :bitrate_mbps, :size_bytes, :quality_slot, :date_created, :updated_at
                    )
                    """,
                    values,
                )
        self.reconcile_jobs()
        return len(rows)

    def prune_inventory_servers(self, server_names: Iterable[str]) -> None:
        names = [str(value) for value in server_names if value]
        with self.connect() as conn:
            if not names:
                conn.execute("DELETE FROM inventory_versions")
                return
            marks = ",".join("?" for _ in names)
            conn.execute(
                f"DELETE FROM inventory_versions WHERE server_name NOT IN ({marks})",
                names,
            )

    def mark_inventory_synced(self) -> None:
        now = utcnow()
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO plugin_state(key, value, updated_at) VALUES('inventory_sync', 'success', ?)",
                (now,),
            )

    def inventory_ready(self) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM plugin_state WHERE key='inventory_sync' AND value='success'"
            ).fetchone()
        return bool(row)

    def list_inventory(self, page: int = 1, page_size: int = 50, keyword: str = "", media_type: str = "") -> dict:
        page, page_size = _page_values(page, page_size)
        clauses, params = [], []
        if keyword:
            clauses.append("(title LIKE ? OR original_title LIKE ? OR path LIKE ?)")
            value = f"%{keyword}%"
            params.extend([value, value, value])
        if media_type:
            clauses.append("item_type=?")
            params.append(media_type)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM inventory_versions{where}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM inventory_versions{where} "
                "ORDER BY (date_created IS NULL OR date_created=''), date_created DESC, "
                "title, season, episode, bitrate_mbps DESC "
                "LIMIT ? OFFSET ?",
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
        return {"items": [_decode(row) for row in rows], "total": total, "page": page, "page_size": page_size}

    def inventory_version_count(self, media_key: str, conn: Optional[sqlite3.Connection] = None) -> int:
        if conn is None:
            with self.connect() as own_conn:
                return self.inventory_version_count(media_key, own_conn)
        if media_key.startswith("tv:") and not _is_tv_episode_key(media_key):
            pattern = f"{media_key}E%" if _is_tv_season_key(media_key) else f"{media_key}:%"
            row = conn.execute(
                "SELECT MAX(version_count) FROM ("
                "SELECT COUNT(DISTINCT version_key) AS version_count FROM inventory_versions "
                "WHERE media_key LIKE ? GROUP BY media_key)",
                (pattern,),
            ).fetchone()
            return int((row and row[0]) or 0)
        row = conn.execute(
            "SELECT COUNT(DISTINCT version_key) FROM inventory_versions WHERE media_key=?",
            (media_key,),
        ).fetchone()
        return int(row[0] or 0)

    def list_targets(self, with_inventory: bool = False) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM targets ORDER BY enabled DESC, year DESC, id DESC").fetchall()
            targets = [_decode(row) for row in rows]
            if with_inventory:
                ready = bool(conn.execute(
                    "SELECT 1 FROM plugin_state WHERE key='inventory_sync' AND value='success'"
                ).fetchone())
                for target in targets:
                    count = self._target_inventory_count(conn, target) if ready else 0
                    target.update({
                        "inventory_state": "present" if count else "missing" if ready else "unknown",
                        "inventory_count": count,
                        "in_library": bool(count),
                    })
        return targets

    @staticmethod
    def _target_inventory_count(conn: sqlite3.Connection, target: Mapping[str, Any]) -> int:
        media_type = str(target.get("media_type") or "movie").lower()
        source = str(target.get("media_source") or "themoviedb").lower()
        media_id = str(target.get("media_id") or "").strip()
        if media_id and source in {"themoviedb", "imdb", "tvdb"}:
            base = f"{media_type}:{source}:{media_id}"
            row = conn.execute(
                "SELECT COUNT(DISTINCT version_key) FROM inventory_versions "
                "WHERE media_key=? OR media_key LIKE ?",
                (base, f"{base}:%"),
            ).fetchone()
            return int(row[0] or 0)

        clauses, params = ["item_type=?"], [media_type]
        year = _int(target.get("year"))
        if year:
            clauses.append("(year=? OR year IS NULL)")
            params.append(year)
        rows = conn.execute(
            "SELECT version_key, title, original_title FROM inventory_versions WHERE "
            + " AND ".join(clauses),
            params,
        ).fetchall()
        titles = {
            _normalize_title(target.get("title")),
            _normalize_title(target.get("original_title")),
        } - {""}
        return len({
            row["version_key"] for row in rows
            if titles.intersection({_normalize_title(row["title"]), _normalize_title(row["original_title"])})
        })

    def get_target(self, target_id: int) -> Optional[dict]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM targets WHERE id=?", (target_id,)).fetchone()
        return _decode(row) if row else None

    def save_target(self, payload: Mapping[str, Any], target_id: Optional[int] = None) -> dict:
        now = utcnow()
        media_type = str(payload.get("media_type") or "movie").lower()
        if media_type not in {"movie", "tv"}:
            raise ValueError("媒体类型必须为 movie 或 tv")
        desired = max(1, min(3, _int(payload.get("desired_versions"), 3)))
        values = {
            "media_type": media_type,
            "media_source": str(payload.get("media_source") or "themoviedb"),
            "media_id": _none(payload.get("media_id") or payload.get("tmdb_id")),
            "title": str(payload.get("title") or "").strip(),
            "original_title": _none(payload.get("original_title")),
            "poster_url": _none(payload.get("poster_url")),
            "year": _int(payload.get("year")) or None,
            "seasons_json": dumps(payload.get("seasons") or []),
            "desired_versions": desired,
            "sites_json": dumps([_int(item) for item in payload.get("sites") or [] if _int(item)]),
            "profile_json": dumps(payload.get("profile") or {}),
            "save_path": _none(payload.get("save_path")),
            "auto_download": int(bool(payload.get("auto_download"))),
            "prefer_scanned_pool": int(bool(payload.get("prefer_scanned_pool"))),
            "enabled": int(payload.get("enabled", True) is not False),
            "updated_at": now,
        }
        if not values["title"]:
            raise ValueError("目标标题不能为空")
        with self.connect() as conn:
            if target_id:
                values["id"] = int(target_id)
                result = conn.execute(
                    """
                    UPDATE targets SET media_type=:media_type, media_source=:media_source,
                        media_id=:media_id, title=:title, original_title=:original_title,
                        poster_url=:poster_url,
                        year=:year, seasons_json=:seasons_json,
                        desired_versions=:desired_versions, sites_json=:sites_json,
                        profile_json=:profile_json, save_path=:save_path,
                        auto_download=:auto_download, prefer_scanned_pool=:prefer_scanned_pool,
                        enabled=:enabled, updated_at=:updated_at
                    WHERE id=:id
                    """,
                    values,
                )
                if not result.rowcount:
                    raise ValueError("目标不存在")
            else:
                values["created_at"] = now
                cursor = conn.execute(
                    """
                    INSERT INTO targets (
                        media_type, media_source, media_id, title, original_title,
                        poster_url, year, seasons_json,
                        desired_versions, sites_json, profile_json, save_path,
                        auto_download, prefer_scanned_pool, enabled, created_at, updated_at
                    ) VALUES (
                        :media_type, :media_source, :media_id, :title, :original_title,
                        :poster_url, :year, :seasons_json,
                        :desired_versions, :sites_json, :profile_json, :save_path,
                        :auto_download, :prefer_scanned_pool, :enabled, :created_at, :updated_at
                    )
                    """,
                    values,
                )
                target_id = cursor.lastrowid
        return self.get_target(int(target_id))

    def delete_target(self, target_id: int) -> bool:
        with self.connect() as conn:
            conn.execute("DELETE FROM candidates WHERE target_id=?", (target_id,))
            result = conn.execute("DELETE FROM targets WHERE id=?", (target_id,))
        return bool(result.rowcount)

    def replace_candidates(self, scope: str, candidates: list[Mapping[str, Any]]) -> int:
        now = utcnow()
        with self.connect() as conn:
            conn.execute("DELETE FROM candidates WHERE scope=?", (scope,))
            for candidate in candidates:
                values = dict(candidate)
                values.update({"scope": scope, "discovered_at": now, "updated_at": now})
                values.setdefault("target_id", None)
                values.setdefault("media_json", "{}")
                values.setdefault("meta_json", "{}")
                values.setdefault("media_keys_json", "[]")
                values.setdefault("rejection_reason", None)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO candidates (
                        candidate_key, torrent_key, scope, target_id, site_id, site_name, title, description,
                        page_url, enclosure, size_bytes, seeders, peers, pubdate, year,
                        quality_type, quality_effect, resolution, video_codec, bitrate_mbps,
                        quality_score, quality_slot, eligible, rejection_reason, torrent_json,
                        meta_json, media_json, media_keys_json, discovered_at, updated_at
                    ) VALUES (
                        :candidate_key, :torrent_key, :scope, :target_id, :site_id, :site_name, :title, :description,
                        :page_url, :enclosure, :size_bytes, :seeders, :peers, :pubdate, :year,
                        :quality_type, :quality_effect, :resolution, :video_codec, :bitrate_mbps,
                        :quality_score, :quality_slot, :eligible, :rejection_reason, :torrent_json,
                        :meta_json, :media_json, :media_keys_json, :discovered_at, :updated_at
                    )
                    """,
                    values,
                )
        return len(candidates)

    def update_candidate_identity(self, candidate_key: str, media: Mapping[str, Any], media_keys: list[str]) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE candidates SET media_json=?, media_keys_json=?, updated_at=? WHERE candidate_key=?",
                (dumps(media), dumps(media_keys), utcnow(), candidate_key),
            )

    def get_candidate(self, candidate_key: str) -> Optional[dict]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM candidates WHERE candidate_key=?", (candidate_key,)).fetchone()
        return _decode(row) if row else None

    def list_candidates(
        self,
        page: int = 1,
        page_size: int = 50,
        scope: str = "pool",
        keyword: str = "",
        site_id: Optional[int] = None,
        eligible_only: bool = True,
        quality_type: str = "",
    ) -> dict:
        page, page_size = _page_values(page, page_size)
        clauses, params = ["scope=?"], [scope]
        if keyword:
            clauses.append("(title LIKE ? OR description LIKE ?)")
            params.extend([f"%{keyword}%", f"%{keyword}%"])
        if site_id:
            clauses.append("site_id=?")
            params.append(int(site_id))
        if eligible_only:
            clauses.append("eligible=1")
        base_where = " WHERE " + " AND ".join(clauses)
        base_params = list(params)
        if quality_type:
            clauses.append("quality_type=?")
            params.append(str(quality_type))
        where = " WHERE " + " AND ".join(clauses)
        with self.connect() as conn:
            quality_counts = {
                row["quality_type"]: row["total"]
                for row in conn.execute(
                    f"SELECT quality_type, COUNT(*) AS total FROM candidates{base_where} GROUP BY quality_type",
                    base_params,
                ).fetchall()
            }
            total = conn.execute(f"SELECT COUNT(*) FROM candidates{where}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM candidates{where} "
                "ORDER BY COALESCE(year, 0) DESC, quality_score DESC, bitrate_mbps DESC, seeders DESC, title "
                "LIMIT ? OFFSET ?",
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
        return {
            "items": [_decode(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "quality_counts": quality_counts,
        }

    def pending_auto_candidates(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT candidate.* FROM candidates AS candidate
                WHERE scope='pool' AND eligible=1
                  AND NOT EXISTS (
                    SELECT 1 FROM download_jobs AS job
                    WHERE job.torrent_key=candidate.torrent_key
                      AND job.status IN (?,?,?,?)
                  )
                ORDER BY COALESCE(year, 0) DESC, quality_score DESC,
                         bitrate_mbps DESC, seeders DESC, title
                """,
                DUPLICATE_JOB_STATES,
            ).fetchall()
        return [_decode(row) for row in rows]

    def pending_auto_candidate_keys(self) -> list[str]:
        return [str(row["candidate_key"]) for row in self.pending_auto_candidates()]

    def reserve_download(
        self,
        candidate_key: str,
        media_keys: list[str],
        max_versions: int,
        save_path: Optional[str],
        automatic: bool,
        allow_same_slot: bool = False,
        target_id: Optional[int] = None,
    ) -> tuple[Optional[int], str]:
        if not media_keys:
            return None, "未识别到媒体版本键"
        cap = max(1, min(3, int(max_versions)))
        now = utcnow()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            candidate = conn.execute("SELECT * FROM candidates WHERE candidate_key=?", (candidate_key,)).fetchone()
            if not candidate:
                return None, "候选种子不存在或已刷新"
            if not candidate["eligible"]:
                return None, candidate["rejection_reason"] or "候选种子不符合规则"
            duplicate = conn.execute(
                "SELECT id FROM download_jobs WHERE torrent_key=? AND status IN (?,?,?,?) LIMIT 1",
                (candidate["torrent_key"], *DUPLICATE_JOB_STATES),
            ).fetchone()
            if duplicate:
                return None, "该种子已在下载队列"

            active_jobs = conn.execute(
                "SELECT media_keys_json, quality_slot FROM download_jobs WHERE status IN (?,?,?)",
                ACTIVE_JOB_STATES,
            ).fetchall()
            baseline = {}
            for media_key in media_keys:
                existing = self.inventory_version_count(media_key, conn)
                baseline[media_key] = existing
                reserved = sum(
                    1 for job in active_jobs
                    if any(_keys_overlap(media_key, key) for key in _loads(job["media_keys_json"], []))
                )
                if existing + reserved >= cap:
                    return None, f"{media_key} 已达到 {cap} 个版本上限（含下载中）"

            slot = candidate["quality_slot"]
            if slot and not allow_same_slot:
                for media_key in media_keys:
                    if _inventory_has_slot(conn, media_key, slot):
                        return None, f"{media_key} 已有相同质量槽位 {slot}"
                    for job in active_jobs:
                        if job["quality_slot"] == slot and any(
                            _keys_overlap(media_key, key) for key in _loads(job["media_keys_json"], [])
                        ):
                            return None, f"{media_key} 已有相同质量槽位在下载中"

            cursor = conn.execute(
                """
                INSERT INTO download_jobs (
                    candidate_key, torrent_key, target_id, title, site_name, media_keys_json,
                    quality_slot, baseline_json, save_path, automatic, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'reserved', ?, ?)
                """,
                (
                    candidate_key,
                    candidate["torrent_key"],
                    int(target_id) if target_id is not None else candidate["target_id"],
                    candidate["title"],
                    candidate["site_name"],
                    dumps(media_keys),
                    slot,
                    dumps(baseline),
                    save_path,
                    int(automatic),
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid), ""

    def update_job(self, job_id: int, status: str, download_id: Any = None, error: Any = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE download_jobs SET status=?, download_id=?, error=?, updated_at=? WHERE id=?",
                (status, _none(download_id), _none(error), utcnow(), job_id),
            )

    def cancel_job(self, job_id: int) -> tuple[bool, str]:
        with self.connect() as conn:
            row = conn.execute("SELECT status FROM download_jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                return False, "任务不存在"
            if row["status"] not in {"reserved", "failed"}:
                return False, "已提交下载器的任务请在下载器中取消"
            conn.execute(
                "UPDATE download_jobs SET status='cancelled', updated_at=? WHERE id=?",
                (utcnow(), job_id),
            )
        return True, ""

    def list_jobs(self, page: int = 1, page_size: int = 50) -> dict:
        page, page_size = _page_values(page, page_size)
        with self.connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM download_jobs").fetchone()[0]
            rows = conn.execute(
                "SELECT * FROM download_jobs ORDER BY id DESC LIMIT ? OFFSET ?",
                (page_size, (page - 1) * page_size),
            ).fetchall()
        return {"items": [_decode(row) for row in rows], "total": total, "page": page, "page_size": page_size}

    def reconcile_jobs(self) -> None:
        with self.connect() as conn:
            jobs = conn.execute(
                "SELECT id, media_keys_json, baseline_json, quality_slot "
                "FROM download_jobs WHERE status IN ('queued','downloading') ORDER BY id"
            ).fetchall()
            for job in jobs:
                keys = _loads(job["media_keys_json"], [])
                baseline = _loads(job["baseline_json"], {})
                slot = job["quality_slot"]
                if keys and slot and all(
                    self.inventory_version_count(key, conn) > int(baseline.get(key, 0))
                    and _inventory_has_slot(conn, key, slot)
                    for key in keys
                ):
                    conn.execute(
                        "UPDATE download_jobs SET status='present', updated_at=? WHERE id=?",
                        (utcnow(), job["id"]),
                    )

    def stats(self) -> dict:
        with self.connect() as conn:
            inventory = conn.execute("SELECT COUNT(*) FROM inventory_versions").fetchone()[0]
            media = conn.execute("SELECT COUNT(DISTINCT media_key) FROM inventory_versions").fetchone()[0]
            targets = conn.execute("SELECT COUNT(*) FROM targets WHERE enabled=1").fetchone()[0]
            candidates = conn.execute("SELECT COUNT(*) FROM candidates WHERE eligible=1").fetchone()[0]
            active_jobs = conn.execute(
                "SELECT COUNT(*) FROM download_jobs WHERE status IN (?,?,?)", ACTIVE_JOB_STATES
            ).fetchone()[0]
            sync_state = conn.execute(
                "SELECT updated_at FROM plugin_state WHERE key='inventory_sync' AND value='success'"
            ).fetchone()
            latest_inventory = sync_state[0] if sync_state else None
            latest_pool = conn.execute("SELECT MAX(updated_at) FROM candidates WHERE scope='pool'").fetchone()[0]
        return {
            "inventory_versions": inventory,
            "media_items": media,
            "active_targets": targets,
            "eligible_candidates": candidates,
            "active_jobs": active_jobs,
            "latest_inventory": latest_inventory,
            "inventory_ready": bool(latest_inventory),
            "latest_pool": latest_pool,
        }


def _decode(row: sqlite3.Row) -> dict:
    result = dict(row)
    for source, target in JSON_COLUMNS.items():
        if source in result:
            default = [] if source.endswith("keys_json") or source in {"sites_json", "seasons_json"} else {}
            result[target] = _loads(result.pop(source), default)
    for key in ("enabled", "auto_download", "prefer_scanned_pool", "eligible", "automatic"):
        if key in result:
            result[key] = bool(result[key])
    return result


def _loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


def _page_values(page: Any, page_size: Any) -> tuple[int, int]:
    return max(1, _int(page, 1)), max(1, min(50, _int(page_size, 50)))


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _none(value: Any) -> Optional[str]:
    return str(value).strip() if value not in (None, "") else None


def _normalize_title(value: Any) -> str:
    return " ".join(re.findall(r"[^\W_]+", str(value or "").casefold(), re.UNICODE))


def _keys_overlap(left: str, right: str) -> bool:
    if left == right:
        return True
    if left.startswith(f"{right}:") or right.startswith(f"{left}:"):
        return True
    if _is_tv_season_key(left) and right.startswith(f"{left}E"):
        return True
    if _is_tv_season_key(right) and left.startswith(f"{right}E"):
        return True
    return False


def _inventory_has_slot(conn: sqlite3.Connection, media_key: str, slot: str) -> bool:
    if media_key.startswith("tv:") and not _is_tv_episode_key(media_key):
        pattern = f"{media_key}E%" if _is_tv_season_key(media_key) else f"{media_key}:%"
        row = conn.execute(
            "SELECT 1 FROM inventory_versions WHERE media_key LIKE ? AND quality_slot=? LIMIT 1",
            (pattern, slot),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM inventory_versions WHERE media_key=? AND quality_slot=? LIMIT 1",
            (media_key, slot),
        ).fetchone()
    return bool(row)


def _is_tv_season_key(media_key: str) -> bool:
    tail = media_key.rsplit(":", 1)[-1]
    return len(tail) == 3 and tail.startswith("S") and tail[1:].isdigit()


def _is_tv_episode_key(media_key: str) -> bool:
    tail = media_key.rsplit(":", 1)[-1]
    if len(tail) < 6 or not tail.startswith("S") or "E" not in tail:
        return False
    season, episode = tail[1:].split("E", 1)
    return season.isdigit() and episode.isdigit()
