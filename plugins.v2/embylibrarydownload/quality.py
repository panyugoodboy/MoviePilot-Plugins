"""Resource quality classification and filtering.

This module intentionally has no MoviePilot imports so its policy can be tested
without a running MoviePilot instance.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from typing import Any, Iterable, Mapping, Optional


YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
BITRATE_RE = re.compile(r"(?<![A-Z0-9])(\d{1,3}(?:\.\d+)?)\s*(?:mbps|mb/s|m(?:bit)?/?s)(?!\w)", re.I)
SERIES_TITLE_RE = re.compile(
    r"(?<![A-Z0-9])S\d{1,3}(?:E\d{1,4})?(?![A-Z0-9])"
    r"|\bSEASON[ ._-]*\d+\b|\bCOMPLETE[ ._-]+SERIES\b"
    r"|第[一二三四五六七八九十百\d]+季|全集",
    re.I,
)
QUALITY_TYPE_SCORES = {"diy": 600, "remux": 550, "bluray": 500, "webdl": 350, "encode": 300, "unknown": 0}
GIB = 1024 ** 3


def _text(*values: Any) -> str:
    return " ".join(str(value or "") for value in values).upper()


def _attr(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _normal_list(values: Optional[Iterable[Any]]) -> list[str]:
    return [str(value).strip().lower() for value in values or [] if str(value).strip()]


@dataclass(frozen=True)
class QualityInfo:
    quality_type: str
    effect: str
    resolution: str
    video_codec: str
    bitrate_mbps: float
    year: Optional[int]
    score: int
    slot: str

    def to_dict(self) -> dict:
        return asdict(self)


def extract_year(title: str, fallback: Any = None) -> Optional[int]:
    try:
        year = int(fallback)
        if 1900 <= year <= 2099:
            return year
    except (TypeError, ValueError):
        pass
    match = YEAR_RE.search(title or "")
    return int(match.group(1)) if match else None


def classify_quality(
    title: str,
    meta: Any = None,
    *,
    bitrate_bps: Any = None,
    video_stream: Optional[Mapping[str, Any]] = None,
) -> QualityInfo:
    """Classify a torrent title or an Emby media source."""

    source_text = _text(
        title,
        _attr(meta, "resource_type"),
        _attr(meta, "resource_effect"),
        _attr(meta, "resource_pix"),
        _attr(meta, "video_encode"),
        _attr(meta, "video_bit"),
        _attr(meta, "customization"),
    )
    stream = video_stream or {}
    stream_text = _text(
        stream.get("Codec"),
        stream.get("VideoRange"),
        stream.get("VideoRangeType"),
        stream.get("Profile"),
        stream.get("Title"),
    )
    combined = f"{source_text} {stream_text}"

    if re.search(r"\bDIY\b", combined):
        quality_type = "diy"
    elif re.search(r"\bREMUX\b", combined):
        quality_type = "remux"
    elif re.search(r"\b(?:BDMV|BDISO|UHD[ ._-]?BD|FULL[ ._-]?(?:BLU[ ._-]?RAY|DISC)|COMPLETE[ ._-]?(?:BLU|UHD)|BLU[ ._-]?RAY[ ._-]?ISO)\b", combined):
        quality_type = "bluray"
    elif re.search(r"\bWEB[ ._-]?(?:DL|RIP)\b|\bWEB-DL\b", combined):
        quality_type = "webdl"
    elif re.search(r"\b(?:X26[45]|ENCODE)\b", combined):
        quality_type = "encode"
    elif re.search(r"\b(?:UHD[ ._-]?BLU[ ._-]?RAY|BLU[ ._-]?RAY)\b", combined):
        quality_type = "bluray"
    elif re.search(r"\b(?:H26[45]|HEVC|AVC|AV1)\b", combined):
        quality_type = "encode"
    else:
        quality_type = "unknown"

    if re.search(r"DOLBY[ ._-]?VISION|\bDOVI\b|(?<![A-Z])DV(?![A-Z])", combined):
        effect = "dv"
    elif re.search(r"HDR10\+|HDR10PLUS", combined):
        effect = "hdr10plus"
    elif re.search(r"\bHDR10\b", combined):
        effect = "hdr10"
    elif re.search(r"\bHLG\b", combined):
        effect = "hlg"
    elif re.search(r"\bHDR\b", combined):
        effect = "hdr"
    elif re.search(r"\bSDR\b", combined):
        effect = "sdr"
    else:
        effect = "unknown"

    parsed_resolution = str(_attr(meta, "resource_pix", "") or "").lower()
    height = stream.get("Height") or stream.get("height")
    width = stream.get("Width") or stream.get("width")
    if re.search(r"\b(?:2160P|4K|UHD)\b", combined) or _as_int(height) >= 2000 or _as_int(width) >= 3800:
        resolution = "2160p"
    elif re.search(r"\b1080[PI]\b", combined) or 1000 <= _as_int(height) < 2000:
        resolution = "1080p"
    elif re.search(r"\b720P\b", combined) or 700 <= _as_int(height) < 1000:
        resolution = "720p"
    elif parsed_resolution:
        resolution = parsed_resolution
    else:
        resolution = "unknown"

    raw_codec = _text(title, _attr(meta, "video_encode"), stream.get("Codec"))
    if re.search(r"\b(?:H265|X265|HEVC)\b", raw_codec):
        video_codec = "h265"
    elif re.search(r"\b(?:H264|X264|AVC)\b", raw_codec):
        video_codec = "h264"
    elif re.search(r"\bAV1\b", raw_codec):
        video_codec = "av1"
    else:
        video_codec = "unknown"

    bitrate = _as_float(bitrate_bps) / 1_000_000 if _as_float(bitrate_bps) else 0.0
    if not bitrate:
        match = BITRATE_RE.search(title or "")
        bitrate = float(match.group(1)) if match else 0.0

    year = extract_year(title, _attr(meta, "year"))
    effect_scores = {"dv": 80, "hdr10plus": 70, "hdr10": 60, "hdr": 50, "hlg": 40, "sdr": 10, "unknown": 0}
    resolution_scores = {"2160p": 30, "1080p": 20, "720p": 10, "unknown": 0}
    score = QUALITY_TYPE_SCORES.get(quality_type, 0) + effect_scores.get(effect, 0) \
        + resolution_scores.get(resolution, 0) + min(int(bitrate), 99)
    slot = f"{quality_type}:{effect}:{resolution}"
    return QualityInfo(quality_type, effect, resolution, video_codec, round(bitrate, 2), year, score, slot)


def quality_matches(
    title: str,
    quality: QualityInfo,
    profile: Mapping[str, Any],
    *,
    size_bytes: Any = 0,
) -> tuple[bool, str]:
    """Return whether a classified resource matches a configured profile."""

    include = [word.lower() for word in _split_words(profile.get("include_words"))]
    exclude = [word.lower() for word in _split_words(profile.get("exclude_words"))]
    lowered = (title or "").lower()
    if include and not all(_contains_keyword(lowered, word) for word in include):
        return False, "未包含全部必选关键词"
    if any(_contains_keyword(lowered, word) for word in exclude):
        return False, "命中排除关键词"

    type_values = _normal_list(profile.get("quality_types"))
    if type_values and quality.quality_type not in type_values:
        return False, "质量类型不符合"
    effects = _normal_list(profile.get("effects"))
    if effects and quality.effect not in effects:
        return False, "动态范围不符合"
    resolutions = _normal_list(profile.get("resolutions"))
    if resolutions and quality.resolution not in resolutions:
        return False, "分辨率不符合"
    codecs = _normal_list(profile.get("video_codecs"))
    if codecs and quality.video_codec not in codecs:
        return False, "视频编码不符合"

    minimum = _as_float(profile.get("min_bitrate_mbps"))
    maximum = _as_float(profile.get("max_bitrate_mbps"))
    if minimum and quality.bitrate_mbps and quality.bitrate_mbps < minimum:
        return False, "码率低于下限"
    if maximum and quality.bitrate_mbps and quality.bitrate_mbps > maximum:
        return False, "码率高于上限"
    if (minimum or maximum) and not quality.bitrate_mbps and profile.get("reject_unknown_bitrate"):
        return False, "无法识别码率"
    size_match, size_reason = minimum_size_matches(
        quality.resolution, size_bytes, profile
    )
    if not size_match:
        return False, size_reason
    return True, ""


def minimum_size_matches(
    resolution: Any, size_bytes: Any, profile: Mapping[str, Any]
) -> tuple[bool, str]:
    """Apply resolution-specific minimum torrent size thresholds."""

    settings = {
        "2160p": ("min_size_4k_gb", "4K"),
        "1080p": ("min_size_1080p_gb", "1080P"),
    }
    setting = settings.get(str(resolution or "").lower())
    if not setting:
        return True, ""
    minimum_gb = _as_float(profile.get(setting[0]))
    if minimum_gb <= 0:
        return True, ""
    if _as_float(size_bytes) < minimum_gb * GIB:
        return False, f"{setting[1]} 体积低于最低设置 {minimum_gb:g} GB"
    return True, ""


def estimate_bitrate_mbps(size_bytes: Any, runtime_minutes: Any) -> float:
    """Estimate overall bitrate for one movie torrent from size and runtime."""

    size = _as_float(size_bytes)
    runtime = _as_float(runtime_minutes)
    if size <= 0 or runtime <= 0:
        return 0.0
    return round(size * 8 / (runtime * 60 * 1_000_000), 2)


def plan_pool_candidates(candidates: Iterable[Mapping[str, Any]]) -> list[dict]:
    """Keep only the best WEB-DL candidate for each movie and fixed resolution."""

    rows = [dict(candidate) for candidate in candidates]
    groups: dict[tuple[str, int, str], list[int]] = {}
    for index, candidate in enumerate(rows):
        if not candidate.get("eligible"):
            continue
        if candidate.get("quality_type") != "webdl":
            continue
        resolution = str(candidate.get("resolution") or "").lower()
        if resolution not in {"2160p", "1080p"}:
            continue
        movie_key = _pool_movie_key(candidate)
        year = _as_int(candidate.get("year"))
        if not movie_key or not year:
            continue
        groups.setdefault((movie_key, year, resolution), []).append(index)

    for (_, _, resolution), indexes in groups.items():
        if len(indexes) < 2:
            continue
        all_bitrates_known = all(_as_float(rows[index].get("bitrate_mbps")) > 0 for index in indexes)
        winner = max(
            indexes,
            key=lambda index: _webdl_candidate_rank(rows[index], all_bitrates_known),
        )
        for index in indexes:
            if index == winner:
                continue
            rows[index]["eligible"] = False
            rows[index]["rejection_reason"] = (
                f"同影片 {resolution} 已保留更高码率的 WEB-DL 候选"
            )
    return rows


def merge_profile(base: Mapping[str, Any], override: Optional[Mapping[str, Any]]) -> dict:
    result = dict(base or {})
    for key, value in (override or {}).items():
        if value not in (None, "", []):
            result[key] = value
    return result


def prioritize_pool_candidates(
    candidates: Iterable[Mapping[str, Any]],
    targets: Iterable[Mapping[str, Any]],
    base_profile: Mapping[str, Any],
    default_sites: Optional[Iterable[Any]] = None,
) -> list[tuple[Mapping[str, Any], Optional[Mapping[str, Any]]]]:
    """Move scanned pool candidates matching enabled target rules to the front."""

    active_targets = [
        item
        for target in targets
        if target.get("enabled") and target.get("auto_download") and target.get("prefer_scanned_pool")
        for item in expand_target_items(target)
    ]
    preferred, regular = [], []
    for candidate in candidates:
        matched_target = next((
            target for target in active_targets
            if _pool_candidate_matches_target(
                candidate,
                target,
                merge_profile(base_profile, target.get("profile")),
                target.get("sites") or default_sites,
            )
        ), None)
        (preferred if matched_target else regular).append((candidate, matched_target))
    return preferred + regular


def expand_target_items(target: Mapping[str, Any]) -> list[dict]:
    """Expand one recommendation-list target into media targets while keeping list rules."""

    items = target.get("items") if isinstance(target.get("items"), list) else []
    if not items:
        return [dict(target)]
    expanded = []
    for position, item in enumerate(items):
        if not isinstance(item, Mapping):
            continue
        merged = dict(target)
        merged.update(item)
        merged.update({"id": target.get("id"), "items": [], "target_position": position})
        expanded.append(merged)
    return expanded


def matching_pool_candidates(
    candidates: Iterable[Mapping[str, Any]],
    target: Mapping[str, Any],
    base_profile: Mapping[str, Any],
    default_sites: Optional[Iterable[Any]] = None,
) -> list[Mapping[str, Any]]:
    """Return scanned pool candidates matching one target in target preference order."""

    profile = merge_profile(base_profile, target.get("profile"))
    matches = [
        candidate for candidate in candidates
        if _pool_candidate_matches_target(
            candidate,
            target,
            profile,
            target.get("sites") or default_sites,
        )
    ]
    return sorted(
        matches,
        key=lambda candidate: (
            profile_score(_candidate_quality(candidate), profile),
            _as_int(candidate.get("size_bytes")),
            _as_int(candidate.get("seeders")),
            str(candidate.get("title") or ""),
        ),
        reverse=True,
    )


def _pool_candidate_matches_target(
    candidate: Mapping[str, Any],
    target: Mapping[str, Any],
    profile: Mapping[str, Any],
    sites: Optional[Iterable[Any]],
) -> bool:
    if not candidate.get("eligible") or str(target.get("media_type") or "movie").lower() != "movie":
        return False
    site_ids = {_as_int(value) for value in sites or [] if _as_int(value)}
    if site_ids and _as_int(candidate.get("site_id")) not in site_ids:
        return False
    target_year = _as_int(target.get("year"))
    candidate_year = _as_int(candidate.get("year"))
    if target_year and candidate_year and target_year != candidate_year:
        return False

    quality = _candidate_quality(candidate)
    if not quality_matches(
        str(candidate.get("title") or ""),
        quality,
        profile,
        size_bytes=candidate.get("size_bytes"),
    )[0]:
        return False

    target_media_id = str(target.get("media_id") or "").strip()
    candidate_media_id = _candidate_media_id(candidate, str(target.get("media_source") or "themoviedb"))
    if target_media_id and candidate_media_id:
        return target_media_id == candidate_media_id

    target_titles = {
        _normalize_media_title(target.get("title")),
        _normalize_media_title(target.get("original_title")),
    } - {""}
    if not target_titles:
        return False
    meta = candidate.get("meta") if isinstance(candidate.get("meta"), Mapping) else {}
    media = candidate.get("media") if isinstance(candidate.get("media"), Mapping) else {}
    candidate_titles = (
        candidate.get("title"), candidate.get("description"),
        meta.get("name"), meta.get("cn_name"), meta.get("en_name"), meta.get("original_name"),
        media.get("title"), media.get("original_title"), media.get("en_title"),
    )
    normalized_candidates = [
        f" {_normalize_media_title(value)} " for value in candidate_titles if value
    ]
    return any(
        f" {target_title} " in candidate_title
        for target_title in target_titles for candidate_title in normalized_candidates
    )


def _candidate_quality(candidate: Mapping[str, Any]) -> QualityInfo:
    return QualityInfo(
        quality_type=str(candidate.get("quality_type") or "unknown"),
        effect=str(candidate.get("quality_effect") or "unknown"),
        resolution=str(candidate.get("resolution") or "unknown"),
        video_codec=str(candidate.get("video_codec") or "unknown"),
        bitrate_mbps=_as_float(candidate.get("bitrate_mbps")),
        year=_as_int(candidate.get("year")) or None,
        score=_as_int(candidate.get("quality_score")),
        slot=str(candidate.get("quality_slot") or ""),
    )


def _candidate_media_id(candidate: Mapping[str, Any], source: str) -> str:
    source = source.strip().lower()
    source_keys = {
        "themoviedb": ("tmdb_id", "tmdbid"),
        "douban": ("douban_id", "doubanid"),
        "bangumi": ("bangumi_id", "bangumiid"),
        "anilist": ("anilist_id", "anilistid"),
    }
    for data in (candidate.get("media"), candidate.get("meta")):
        if not isinstance(data, Mapping):
            continue
        data_source = str(data.get("source") or data.get("media_source") or "").lower()
        if data_source == source and data.get("media_id") not in (None, ""):
            return str(data["media_id"])
        for key in source_keys.get(source, ()):
            if data.get(key) not in (None, ""):
                return str(data[key])
    return ""


def _pool_movie_key(candidate: Mapping[str, Any]) -> str:
    media_keys = [
        str(value) for value in candidate.get("media_keys") or []
        if str(value).startswith("movie:")
    ]
    if media_keys:
        return sorted(media_keys)[0]

    for data in (candidate.get("media"), candidate.get("meta")):
        if not isinstance(data, Mapping):
            continue
        for key in ("tmdb_id", "tmdbid", "douban_id", "doubanid", "imdb_id", "imdbid"):
            if data.get(key) not in (None, ""):
                return f"{key}:{data[key]}"

    meta = candidate.get("meta") if isinstance(candidate.get("meta"), Mapping) else {}
    for value in (
        meta.get("original_name"), meta.get("en_name"), meta.get("name"), meta.get("cn_name")
    ):
        normalized = _normalize_media_title(value)
        if normalized:
            return normalized
    return ""


def _webdl_candidate_rank(candidate: Mapping[str, Any], all_bitrates_known: bool) -> tuple:
    primary = (
        _as_float(candidate.get("bitrate_mbps"))
        if all_bitrates_known else _as_int(candidate.get("size_bytes"))
    )
    return (
        primary,
        _as_int(candidate.get("size_bytes")),
        _as_int(candidate.get("seeders")),
        str(candidate.get("pubdate") or ""),
        str(candidate.get("title") or ""),
    )


def _normalize_media_title(value: Any) -> str:
    return " ".join(re.findall(r"[^\W_]+", str(value or "").casefold(), re.UNICODE))


def profile_score(quality: QualityInfo, profile: Mapping[str, Any]) -> int:
    """Build a stable auto-selection score from the user's ordered choices."""

    types = _normal_list(profile.get("quality_types"))
    effects = _normal_list(profile.get("effects"))
    resolutions = _normal_list(profile.get("resolutions"))
    codecs = _normal_list(profile.get("video_codecs"))
    if quality.quality_type == "webdl" and quality.resolution in {"2160p", "1080p"}:
        bitrate = min(max(int(quality.bitrate_mbps * 100), 0), 99_999)
        score = (
            _preference_rank(types, quality.quality_type) * 1_000_000_000
            + _preference_rank(resolutions, quality.resolution) * 100_000_000
            + bitrate * 1_000
            + (_preference_rank(effects, quality.effect) * 10 if bitrate else 0)
            + (_preference_rank(codecs, quality.video_codec) if bitrate else 0)
        )
        return score or quality.score
    score = (
        _preference_rank(types, quality.quality_type) * 1_000_000_000
        + _preference_rank(effects, quality.effect) * 10_000_000
        + _preference_rank(resolutions, quality.resolution) * 100_000
        + _preference_rank(codecs, quality.video_codec) * 1_000
    )
    bitrate = min(max(int(quality.bitrate_mbps), 0), 999)
    order = str(profile.get("bitrate_order") or "desc").lower()
    if order == "asc" and bitrate:
        score += 1000 - bitrate
    elif order == "desc":
        score += bitrate
    return score or quality.score


def apply_source_quality_type(quality: QualityInfo, quality_type: str) -> QualityInfo:
    """Use the category URL as the authoritative quality type."""

    normalized = str(quality_type or "").lower()
    if normalized not in QUALITY_TYPE_SCORES or normalized == quality.quality_type:
        return quality
    score = quality.score - QUALITY_TYPE_SCORES.get(quality.quality_type, 0) + QUALITY_TYPE_SCORES[normalized]
    return replace(
        quality,
        quality_type=normalized,
        score=score,
        slot=f"{normalized}:{quality.effect}:{quality.resolution}",
    )


def tv_exclusion_reason(*, is_tv: bool, enabled: bool) -> str:
    return "已启用排除剧集" if enabled and is_tv else ""


def is_series_title(title: str) -> bool:
    return bool(SERIES_TITLE_RE.search(str(title or "")))


def select_save_path(
    config: Mapping[str, Any],
    quality_type: str,
    media_type: str,
    target_save_path: Any = None,
) -> str:
    """Choose target, quality-specific, then media-type save path."""

    target_path = str(target_save_path or "").strip()
    if target_path:
        return target_path
    paths = config.get("quality_save_paths")
    if isinstance(paths, Mapping):
        quality_path = str(paths.get(quality_type) or "").strip()
        if quality_path:
            return quality_path
    fallback_key = "tv_save_path" if str(media_type).lower() == "tv" else "movie_save_path"
    return str(config.get(fallback_key) or "").strip()


def _split_words(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,;\n]", value) if item.strip()]
    return [str(item).strip() for item in value or [] if str(item).strip()]


def _preference_rank(options: list[str], value: str) -> int:
    if not options:
        return 0
    try:
        return len(options) - options.index(value)
    except ValueError:
        return 0


def _contains_keyword(lowered_title: str, lowered_keyword: str) -> bool:
    keyword = lowered_keyword.strip().lower()
    if not keyword:
        return False
    if keyword == "cam":
        return bool(re.search(r"(?<![a-z0-9])cam", lowered_title))
    if keyword.isalnum() and len(keyword) <= 3:
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", lowered_title))
    return keyword in lowered_title


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
