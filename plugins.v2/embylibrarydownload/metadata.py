"""Helpers for localizing imported movie targets with scraped metadata."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable, Mapping, Optional


HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
KANA_HANGUL_RE = re.compile(r"[\u3040-\u30ff\uac00-\ud7af]")


def select_movie_metadata(
    item: Mapping[str, Any], candidates: Iterable[Any]
) -> Optional[Any]:
    """Choose the closest movie result by title and the imported ±2 year range."""

    target_title = str(item.get("title") or item.get("original_title") or "").strip()
    target_year = _int(item.get("year"))
    tolerance = max(0, min(2, _int(item.get("year_tolerance"), 2)))
    target_key = _normalize_title(target_title)
    ranked = []
    localized_fallback = None
    for index, media in enumerate(candidates or []):
        if not _is_movie(media):
            continue
        year = _int(_value(media, "year"))
        if not year or abs(year - target_year) > tolerance:
            continue
        titles = _media_titles(media)
        keys = {_normalize_title(value) for value in titles if value}
        exact = bool(target_key and target_key in keys)
        if not exact:
            if index == 0 and chinese_title(media) and poster_url(media):
                localized_fallback = media
            continue
        score = 100
        score += 30 - abs(year - target_year) * 10
        score += 10 if chinese_title(media) else 0
        score += 5 if poster_url(media) else 0
        ranked.append((score, -index, media))
    if ranked:
        return max(ranked, key=lambda row: row[:2])[2]
    return localized_fallback


def chinese_title(media: Any) -> str:
    """Return a Chinese localized title without mistaking Japanese/Korean aliases."""

    values = [_value(media, "title"), *(_value(media, "names", []) or [])]
    for value in values:
        text = str(value or "").strip()
        if HAN_RE.search(text) and not KANA_HANGUL_RE.search(text):
            return text
    return ""


def poster_url(media: Any) -> str:
    for key in ("poster_path", "poster_url", "image", "cover_url"):
        value = str(_value(media, key) or "").strip()
        if value.startswith(("http://", "https://")):
            return value
    return ""


def scraped_item(item: Mapping[str, Any], media: Any, scraped_at: str) -> dict:
    """Merge localized identity into an imported target while retaining its search title."""

    result = dict(item)
    source_title = str(item.get("original_title") or item.get("title") or "").strip()
    localized = chinese_title(media)
    source = str(_value(media, "source") or "themoviedb").strip().lower()
    media_id = str(
        _value(media, "media_id")
        or _value(media, "tmdb_id")
        or _value(media, "douban_id")
        or _value(media, "id")
        or ""
    ).strip()
    result.update({
        "media_type": "movie",
        "media_source": source,
        "media_id": media_id,
        "title": localized or str(item.get("title") or source_title).strip(),
        "original_title": str(_value(media, "original_title") or source_title).strip(),
        "year": _int(_value(media, "year"), _int(item.get("year"))),
        "year_tolerance": 2,
        "poster_url": poster_url(media),
        "metadata_state": "complete" if localized else "missing_chinese_title",
        "metadata_error": "" if localized else "未找到中文名称",
        "scraped_at": scraped_at,
    })
    return result


def has_complete_metadata(item: Mapping[str, Any]) -> bool:
    return bool(
        item.get("metadata_state") == "complete"
        and chinese_title({"title": item.get("title")})
        and item.get("poster_url")
        and item.get("media_id")
    )


def _media_titles(media: Any) -> list[str]:
    values = [
        _value(media, "original_title"),
        _value(media, "en_title"),
        _value(media, "title"),
        *(_value(media, "names", []) or []),
    ]
    return [str(value).strip() for value in values if str(value or "").strip()]


def _is_movie(media: Any) -> bool:
    value = _value(media, "type")
    value = getattr(value, "value", value)
    text = str(value or "").strip().casefold()
    return text in {"movie", "电影"} or "movie" in text


def _normalize_title(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return "".join(re.findall(r"[^\W_]+", text, re.UNICODE))


def _value(item: Any, key: str, default: Any = None) -> Any:
    return item.get(key, default) if isinstance(item, Mapping) else getattr(item, key, default)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
