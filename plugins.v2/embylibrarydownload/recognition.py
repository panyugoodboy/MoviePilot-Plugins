"""Recognition attempts for scanned torrent candidates."""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional


YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")


def build_recognition_attempts(
    candidate: Mapping[str, Any], target: Optional[Mapping[str, Any]] = None
) -> list[dict]:
    """Prefer target identity and title, then retry with raw and Chinese alias titles."""

    target = target if isinstance(target, Mapping) else {}
    year = target.get("year") or candidate.get("year")
    target_title = str(target.get("title") or "").strip()
    source = str(target.get("media_source") or "").strip().lower()
    media_id = str(target.get("media_id") or "").strip()
    media_type = str(target.get("media_type") or "movie").strip().lower()
    attempts = []
    if target_title and source and media_id:
        attempts.append({
            "title": _with_year(target_title, year),
            "source": source,
            "media_id": media_id,
            "media_type": media_type,
        })

    if target_title:
        attempts.append({
            "title": _with_year(target_title, year),
            "source": "",
            "media_id": "",
            "media_type": media_type,
        })
    attempts.append({
        "title": str(candidate.get("title") or "").strip(),
        "source": "",
        "media_id": "",
        "media_type": "",
    })

    heading = re.split(r"[|｜]", str(candidate.get("description") or ""), maxsplit=1)[0]
    for alias in re.split(r"[/／]", heading):
        alias = alias.strip()
        if alias:
            attempts.append({
                "title": _with_year(alias, year),
                "source": "",
                "media_id": "",
                "media_type": media_type,
            })

    unique = []
    seen = set()
    for attempt in attempts:
        if not attempt["title"]:
            continue
        key = (
            attempt["title"].casefold(), attempt["source"],
            attempt["media_id"], attempt["media_type"],
        )
        if key not in seen:
            seen.add(key)
            unique.append(attempt)
    return unique


def _with_year(title: str, year: Any) -> str:
    title = str(title or "").strip()
    year_text = str(year or "").strip()
    if year_text and year_text not in YEAR_RE.findall(title):
        return f"{title} {year_text}"
    return title
