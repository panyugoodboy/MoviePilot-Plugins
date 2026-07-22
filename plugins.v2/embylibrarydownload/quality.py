"""Resource quality classification and filtering.

This module intentionally has no MoviePilot imports so its policy can be tested
without a running MoviePilot instance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any, Iterable, Mapping, Optional


YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
BITRATE_RE = re.compile(r"(?<!\d)(\d{1,3}(?:\.\d+)?)\s*(?:mbps|mb/s|m(?:bit)?/?s)(?!\w)", re.I)


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
    type_scores = {"diy": 600, "remux": 550, "bluray": 500, "webdl": 350, "encode": 300, "unknown": 0}
    effect_scores = {"dv": 80, "hdr10plus": 70, "hdr10": 60, "hdr": 50, "hlg": 40, "sdr": 10, "unknown": 0}
    resolution_scores = {"2160p": 30, "1080p": 20, "720p": 10, "unknown": 0}
    score = type_scores.get(quality_type, 0) + effect_scores.get(effect, 0) \
        + resolution_scores.get(resolution, 0) + min(int(bitrate), 99)
    slot = f"{quality_type}:{effect}:{resolution}"
    return QualityInfo(quality_type, effect, resolution, video_codec, round(bitrate, 2), year, score, slot)


def quality_matches(title: str, quality: QualityInfo, profile: Mapping[str, Any]) -> tuple[bool, str]:
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
    return True, ""


def merge_profile(base: Mapping[str, Any], override: Optional[Mapping[str, Any]]) -> dict:
    result = dict(base or {})
    for key, value in (override or {}).items():
        if value not in (None, "", []):
            result[key] = value
    return result


def profile_score(quality: QualityInfo, profile: Mapping[str, Any]) -> int:
    """Build a stable auto-selection score from the user's ordered choices."""

    types = _normal_list(profile.get("quality_types"))
    effects = _normal_list(profile.get("effects"))
    resolutions = _normal_list(profile.get("resolutions"))
    codecs = _normal_list(profile.get("video_codecs"))
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
