"""搜索候选海报地址处理。"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.parse import urlparse


POSTER_HOSTS = {
    "artworks.thetvdb.com",
    "image.tmdb.org",
    "lain.bgm.tv",
    "m.media-amazon.com",
    "media.themoviedb.org",
    "s4.anilist.co",
}


def normalize_poster_url(
    value: object,
    source: object = "",
    tmdb_image_url: Optional[Callable[[str, str], str]] = None,
) -> str:
    """补全 MoviePilot 搜索结果中的协议相对地址和 TMDB 相对路径。"""

    url = str(value or "").strip()
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("/") and str(source or "").lower() == "themoviedb":
        return str(tmdb_image_url(url, "w500") or "") if callable(tmdb_image_url) else ""
    return url


def safe_poster_url(value: object, extra_hosts: Iterable[str] = ()) -> str:
    """只允许 MoviePilot 内置元数据来源使用的公网图片地址。"""

    url = str(value or "").strip()
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    host = str(parsed.hostname or "").lower()
    allowed_hosts = POSTER_HOSTS | {str(item or "").lower() for item in extra_hosts if item}
    allowed = host in allowed_hosts or host.endswith(".doubanio.com")
    return url if parsed.scheme in {"http", "https"} and allowed else ""


def poster_request_headers(url: str) -> dict[str, str]:
    """返回浏览器兼容的图片请求头。"""

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
    """根据图片地址或响应类型生成缓存扩展名。"""

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
