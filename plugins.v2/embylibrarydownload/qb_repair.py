"""Plan safe qBittorrent path repairs for this plugin's active jobs."""

from __future__ import annotations

from typing import Any, Iterable, Mapping


def build_qb_path_repair_plan(
    torrents: Iterable[Mapping[str, Any]],
    active_download_ids: Iterable[str],
    temp_path: str,
) -> list[dict]:
    active_ids = {str(value).lower() for value in active_download_ids if value}
    temp_root = _normalize_path(temp_path)
    if not active_ids or not temp_root or temp_root == "/":
        return []

    groups: dict[str, dict[str, list[str]]] = {}
    for torrent in torrents:
        torrent_hash = str(torrent.get("hash") or "").lower()
        content_path = _normalize_path(torrent.get("content_path"))
        save_path = _normalize_path(torrent.get("save_path"))
        if torrent_hash not in active_ids or not _inside(content_path, temp_root):
            continue
        if not save_path or _inside(save_path, temp_root):
            continue
        group = groups.setdefault(save_path, {"hashes": [], "titles": []})
        group["hashes"].append(torrent_hash)
        group["titles"].append(str(torrent.get("name") or torrent_hash))

    return [
        {
            "save_path": save_path,
            "hashes": group["hashes"],
            "titles": group["titles"],
        }
        for save_path, group in groups.items()
    ]


def _normalize_path(value: Any) -> str:
    path = str(value or "").strip().replace("\\", "/")
    return path.rstrip("/") or ("/" if path.startswith("/") else "")


def _inside(path: str, root: str) -> bool:
    return bool(path and root and (path == root or path.startswith(f"{root}/")))
