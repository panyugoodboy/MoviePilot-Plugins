"""Safe deletion helpers for completed WEB-DL upgrades."""

from __future__ import annotations

from typing import Any, Callable, Mapping


def verify_new_webdl_version(
    version: Mapping[str, Any],
    transfer_history: Any,
    storage: Any,
    fileitem_factory: Callable[..., Any],
    expected_download_id: Any = None,
) -> tuple[bool, str]:
    """Require both an Emby inventory row and an existing transferred file."""

    path = str(version.get("path") or "").strip()
    if not path:
        return False, "新 WEB-DL 版本没有可核对的 Emby 路径"
    history = transfer_history.get_by_dest(path)
    if not history or not bool(getattr(history, "status", False)):
        return False, f"新 WEB-DL 版本尚无成功转移记录：{path}"
    expected = str(expected_download_id or "").strip().lower()
    actual = str(getattr(history, "download_hash", "") or "").strip().lower()
    if expected and actual != expected:
        return False, f"新 WEB-DL 转移记录不属于当前下载任务：{path}"
    item = _fileitem(
        getattr(history, "dest_fileitem", None),
        getattr(history, "dest_storage", None),
        getattr(history, "dest", None),
        fileitem_factory,
    )
    if not item or not storage.exists(item):
        return False, f"新 WEB-DL 入库文件不存在：{path}"
    return True, ""


def cleanup_old_webdl_version(
    version: Mapping[str, Any],
    transfer_history: Any,
    storage: Any,
    fileitem_factory: Callable[..., Any],
) -> tuple[bool, str]:
    """Delete one exact old destination, its source, then its transfer record."""

    path = str(version.get("path") or "").strip()
    if not path:
        return False, "旧版本没有可核对的 Emby 路径"
    history = transfer_history.get_by_dest(path)
    if not history:
        return False, f"未找到旧版本对应的转移记录：{path}"
    if str(getattr(history, "dest", "") or "") != path:
        return False, f"转移记录目标路径与旧版本不一致：{path}"
    if not bool(getattr(history, "status", False)):
        return False, f"旧版本对应的转移记录不是成功状态：{path}"

    dest = _fileitem(
        getattr(history, "dest_fileitem", None),
        getattr(history, "dest_storage", None),
        getattr(history, "dest", None),
        fileitem_factory,
    )
    source = _fileitem(
        getattr(history, "src_fileitem", None),
        getattr(history, "src_storage", None),
        getattr(history, "src", None),
        fileitem_factory,
    )
    deleted_paths = set()
    for label, item in (("媒体库链接", dest), ("下载源文件", source)):
        if not item or not getattr(item, "path", None) or item.path in deleted_paths:
            continue
        if storage.exists(item):
            if not storage.delete_media_file(item):
                return False, f"{label}删除失败：{item.path}"
        deleted_paths.add(item.path)

    transfer_history.delete(history.id)
    return True, ""


def _fileitem(data: Any, storage: Any, path: Any, factory: Callable[..., Any]) -> Any:
    if isinstance(data, Mapping) and data.get("path"):
        return factory(**dict(data))
    if path:
        return factory(storage=str(storage or "local"), path=str(path), type="file")
    return None
