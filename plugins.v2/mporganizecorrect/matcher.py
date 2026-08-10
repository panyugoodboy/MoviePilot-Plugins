"""MP 整理纠正的标题识别与候选匹配工具。"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Tuple


HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
LATIN_RE = re.compile(r"[A-Za-z]")
YEAR_RE = re.compile(r"(?<!\d)(18\d{2}|19\d{2}|20\d{2}|2100)(?!\d)")
TECH_RE = re.compile(
    r"(?i)(?:\bS\d{1,2}(?:E\d{1,4})?\b|\bE\d{1,4}\b|\b(?:2160|1080|720|480)p\b|"
    r"\b(?:2160|1080|720|480)i\b|\b(?:WEB[- .]?DL|BluRay|REMUX|HDTV|UHD|x26[45]|"
    r"H[ .]?26[45]|HEVC|AVC|AAC|DTS|DDP?|TRUEHD|ATMOS|HD[ .-]?MA|FLAC|AC3|EAC3|"
    r"HDR|DV|10BIT|8BIT)\b)"
)
NOISE_RE = re.compile(r"[._]+")
RELEASE_SUFFIX_RE = re.compile(
    r"(?i)\s+(?:USA|FRA|GBR|GER|JPN|KOR|CHN|Director'?s[ .-]?Cut|Extended[ .-]?Cut|"
    r"Unrated|Proper|Repack)\b.*$"
)
GENERIC_PATH_NAMES = {"download", "downloads", "movie", "movies", "media", "video", "videos"}


def has_han(value: Any) -> bool:
    """判断文本是否含有汉字。"""

    return bool(HAN_RE.search(str(value or "")))


def is_english_label(value: Any) -> bool:
    """判断整理标题是否为无汉字且包含拉丁字母的英文标题。"""

    text = str(value or "").strip()
    return bool(text and LATIN_RE.search(text) and not HAN_RE.search(text))


def normalize_title(value: Any) -> str:
    """生成用于严格比较的 Unicode 标题键。"""

    text = unicodedata.normalize("NFKC", str(value or "").casefold())
    return "".join(re.findall(r"[^\W_]+", text, re.UNICODE))


def destination_label(destination: Any, title: Any = "", year: Any = "") -> str:
    """从整理记录和目标路径中提取媒体目录显示名。"""

    history_title = str(title or "").strip()
    parts = [part for part in str(destination or "").replace("\\", "/").split("/") if part]
    directories = parts[:-1] or parts
    year_text = str(year or "").strip()
    for part in reversed(directories):
        if (YEAR_RE.search(part) or (year_text and year_text in part)) and (
            has_han(part) or is_english_label(part)
        ):
            return part
    for part in reversed(directories):
        if has_han(part):
            return part
    if directories and is_english_label(directories[-1]):
        return directories[-1]
    return history_title


def extract_source_identity(
    source: Any,
    *,
    parsed_title: Any = "",
    parsed_year: Any = "",
    history_year: Any = "",
) -> Tuple[str, int]:
    """优先提取中文片名；没有中文时回退为清洗后的英文片名和年份。"""

    parsed = _clean_source_title(parsed_title)
    if has_han(parsed):
        return parsed, _valid_year(parsed_year) or _path_year(source) or _valid_year(history_year)
    parsed_english = _clean_english_source_title(parsed_title)

    path = str(source or "").replace("\\", "/")
    parts = [part for part in path.split("/") if part][-4:]
    chinese_candidates = []
    english_candidates = []
    for index, part in enumerate(reversed(parts)):
        stem = Path(part).stem
        cleaned = _clean_source_title(stem)
        if has_han(cleaned):
            chinese_candidates.append((len(HAN_RE.findall(cleaned)), -index, cleaned))
            continue
        english = _clean_english_source_title(stem)
        if english and normalize_title(english) not in GENERIC_PATH_NAMES:
            english_candidates.append((1 if index == 0 else 0, len(english), -index, english))
    if chinese_candidates:
        title = max(chinese_candidates, default=(0, 0, ""))[2]
    elif parsed_english and normalize_title(parsed_english) not in GENERIC_PATH_NAMES:
        title = parsed_english
    else:
        title = max(english_candidates, default=(0, 0, 0, ""))[3]
    return title, _valid_year(parsed_year) or _path_year(source) or _valid_year(history_year)


def serialize_media(media: Any) -> dict:
    """把 MoviePilot 媒体对象压缩成前端候选结构。"""

    media_type = _value(media, "type")
    media_type = getattr(media_type, "value", media_type)
    source = str(_value(media, "source") or "").strip().lower()
    source_ids = {
        "themoviedb": _value(media, "tmdb_id"),
        "douban": _value(media, "douban_id"),
        "bangumi": _value(media, "bangumi_id"),
        "anilist": _value(media, "anilist_id"),
    }
    if not source:
        source = next((key for key, value in source_ids.items() if value is not None), "")
    media_id = _value(media, "media_id") or source_ids.get(source) or _value(media, "id")
    names = [
        str(value).strip()
        for value in (_value(media, "names", []) or [])
        if str(value or "").strip()
    ]
    title = _localized_title(media) or str(_value(media, "title") or "").strip()
    poster = ""
    getter = getattr(media, "get_poster_image", None)
    if callable(getter):
        try:
            poster = str(getter() or "").strip()
        except Exception:
            poster = ""
    if not poster:
        poster = str(next((
            _value(media, key) for key in ("poster_path", "poster_url", "image", "cover_url")
            if _value(media, key)
        ), "") or "").strip()
    return {
        "media_type": str(media_type or ""),
        "media_source": source,
        "media_id": str(media_id) if media_id is not None else "",
        "title": title,
        "original_title": str(
            _value(media, "original_title") or _value(media, "original_name") or ""
        ).strip(),
        "year": _valid_year(_value(media, "year")),
        "poster_url": poster,
        "names": names[:20],
        "tmdb_id": _value(media, "tmdb_id"),
        "douban_id": _value(media, "douban_id"),
        "bangumi_id": _value(media, "bangumi_id"),
        "anilist_id": _value(media, "anilist_id"),
    }


def choose_exact_candidate(
    query_title: Any,
    query_year: Any,
    media_type: Any,
    candidates: Iterable[Mapping[str, Any]],
) -> Tuple[Optional[dict], str]:
    """只在中英文片名、年份、类型均严格匹配且结果唯一时自动命中。"""

    title_key = normalize_title(query_title)
    year = _valid_year(query_year)
    if not year:
        return None, "年份缺失，仅提供模糊搜索候选，需要人工选择"
    expected_type = _type_key(media_type)
    exact = []
    for candidate in candidates or []:
        if expected_type and _type_key(candidate.get("media_type")) != expected_type:
            continue
        if _valid_year(candidate.get("year")) != year:
            continue
        titles = [
            candidate.get("title"),
            candidate.get("original_title"),
            *(candidate.get("names") or []),
        ]
        if title_key and title_key in {normalize_title(value) for value in titles if value}:
            exact.append(dict(candidate))
    if len(exact) == 1 and has_han(exact[0].get("title")):
        return exact[0], "片名、年份和媒体类型唯一精确匹配"
    if len(exact) > 1:
        return None, f"找到 {len(exact)} 个精确候选，需要人工选择"
    return None, "未找到片名、年份和媒体类型均完全一致的唯一候选"


def safe_transfer_mode(mode: Any) -> Optional[str]:
    """返回不会删除源文件的整理方式；旧 move 记录强制改为 copy。"""

    value = str(mode or "").strip().lower()
    if value == "move":
        return "copy"
    if value in {"copy", "link", "softlink"}:
        return value
    return None


def cleanup_paths_are_safe(
    *,
    source_storage: Any,
    source_path: Any,
    old_storage: Any,
    old_path: Any,
    new_storage: Any,
    new_path: Any,
) -> Tuple[bool, str]:
    """确保旧目标删除不会命中源文件或新整理目标。"""

    old_key = _path_key(old_storage, old_path)
    source_key = _path_key(source_storage, source_path)
    new_key = _path_key(new_storage, new_path)
    if not old_key[1]:
        return False, "旧目标路径为空"
    if not source_key[1]:
        return False, "源文件路径为空"
    if old_key == source_key:
        return False, "旧目标与源文件相同"
    if new_key[1] and old_key == new_key:
        return False, "旧目标与新整理目标相同"
    if old_key[0] == source_key[0] and _paths_overlap(old_key[1], source_key[1]):
        return False, "旧目标与源文件存在包含关系"
    if new_key[1] and old_key[0] == new_key[0] and _paths_overlap(old_key[1], new_key[1]):
        return False, "旧目标与新整理目标存在包含关系"
    return True, ""


def _clean_source_title(value: Any) -> str:
    text = NOISE_RE.sub(" ", str(value or ""))
    text = YEAR_RE.sub(" ", text)
    text = TECH_RE.sub(" ", text)
    text = re.sub(r"[\[\]【】{}<>《》]", " ", text)
    text = re.sub(r"(?i)\b(?:CHS|CHT|简体|繁体|国语|粤语|中字|双语)\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -–—·:：()（）")
    if not has_han(text):
        return ""
    matches = list(HAN_RE.finditer(text))
    start, end = matches[0].start(), matches[-1].end()
    prefix = re.search(r"(?<![A-Za-z0-9])([A-Za-z0-9]{1,3}[：:·・]?)$", text[:start])
    suffix = re.match(r"([：:·・]?\d{1,3})(?!\d)", text[end:])
    if prefix:
        start = prefix.start(1)
    if suffix:
        end += suffix.end(1)
    return re.sub(r"\s+", " ", text[start:end]).strip(" -–—·:：()（）")[:120]


def _clean_english_source_title(value: Any) -> str:
    text = NOISE_RE.sub(" ", str(value or ""))
    text = YEAR_RE.sub(" ", text)
    text = TECH_RE.sub(" ", text)
    text = re.sub(r"[\[\]【】{}<>《》()]", " ", text)
    text = RELEASE_SUFFIX_RE.sub("", text)
    text = re.sub(r"\s*-\s*[A-Za-z0-9][A-Za-z0-9._-]*$", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -–—·:：")
    if not LATIN_RE.search(text) or has_han(text):
        return ""
    return text[:120]


def _localized_title(media: Any) -> str:
    values = [_value(media, "title"), *(_value(media, "names", []) or [])]
    return next((str(value).strip() for value in values if has_han(value)), "")


def _path_year(value: Any) -> int:
    match = YEAR_RE.search(str(value or ""))
    return int(match.group(1)) if match else 0


def _valid_year(value: Any) -> int:
    try:
        year = int(value)
    except (TypeError, ValueError):
        return 0
    return year if 1888 <= year <= 2100 else 0


def _type_key(value: Any) -> str:
    value = getattr(value, "value", value)
    text = str(value or "").strip().casefold()
    if text in {"movie", "电影"} or "movie" in text:
        return "movie"
    if text in {"tv", "电视剧"} or "tv" in text:
        return "tv"
    return ""


def _path_key(storage: Any, path: Any) -> Tuple[str, str]:
    normalized = str(path or "").replace("\\", "/").rstrip("/").casefold()
    return str(storage or "local").strip().casefold(), normalized


def _paths_overlap(left: str, right: str) -> bool:
    left_prefix = f"{left}/"
    right_prefix = f"{right}/"
    return left.startswith(right_prefix) or right.startswith(left_prefix)


def _value(item: Any, key: str, default: Any = None) -> Any:
    return item.get(key, default) if isinstance(item, Mapping) else getattr(item, key, default)
