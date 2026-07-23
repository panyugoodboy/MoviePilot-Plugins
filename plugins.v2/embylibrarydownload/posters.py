"""Poster URL validation and upstream request headers."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse


POSTER_HOSTS = {
    "image.tmdb.org",
    "m.media-amazon.com",
    "lain.bgm.tv",
}


def safe_poster_url(value: object) -> str:
    url = str(value or "").strip()
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    host = str(parsed.hostname or "").lower()
    allowed = host in POSTER_HOSTS or host.endswith(".doubanio.com")
    return url if parsed.scheme in {"http", "https"} and allowed else ""


def poster_request_headers(url: str) -> dict[str, str]:
    headers = {
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
    }
    if str(urlparse(url).hostname or "").lower().endswith(".doubanio.com"):
        headers["Referer"] = "https://movie.douban.com/"
    return headers


def poster_cache_suffix(url: str, content_type: str = "") -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif"}:
        return suffix
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/avif": ".avif",
        "image/gif": ".gif",
    }.get(str(content_type or "").split(";", 1)[0].lower(), ".img")
