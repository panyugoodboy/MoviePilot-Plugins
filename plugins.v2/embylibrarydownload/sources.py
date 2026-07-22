"""Fixed UBits movie category sources used by the full-site pool scan."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from urllib.parse import urlsplit


_COMMON_QUERY = (
    "tag_id1=&tag_id3=&tag_id17=&tag_id4=&tag_id5=&tag_id11=&tag_id6=&tag_id21="
    "&tag_id12=&tag_id8=&tag_id10=&tag_id18=&tag_id9=&tag_id23=2&tag_id20=2"
    "&tag_id19=&tag_id24=&tag_id22=&tag_id14=&tag_id13=&incldead=1&torrentstatus=0"
    "&spstate=0&inclbookmarked=0&approval_status=&size_begin=&size_end=&seeders_begin="
    "&seeders_end=&leechers_begin=&leechers_end=&times_completed_begin="
    "&times_completed_end=&added_begin=&added_end=&search=&search_area=0&search_mode=0"
    "&page={page}"
)


@dataclass(frozen=True)
class MovieSource:
    quality_type: str
    label: str
    path: str


UBITS_MOVIE_SOURCES = (
    MovieSource("webdl", "WEB-DL", f"torrents.php?team6=1&{_COMMON_QUERY}"),
    MovieSource("remux", "Remux", f"torrents.php?medium3=1&team1=1&{_COMMON_QUERY}"),
    MovieSource("diy", "DIY 原盘", f"torrents.php?medium10=1&medium1=1&team1=1&{_COMMON_QUERY}"),
    MovieSource("encode", "Encode", f"torrents.php?medium7=1&team1=1&{_COMMON_QUERY}"),
)


def is_ubits_domain(domain: str) -> bool:
    hostname = (urlsplit(str(domain or "")).hostname or "").lower()
    return hostname in {"ubits.club", "www.ubits.club"}


def build_category_site(site: dict, source: MovieSource) -> dict:
    result = deepcopy(site)
    result["browse"] = dict(result.get("browse") or {})
    result["browse"].update({"path": source.path, "start": 0})
    return result


def with_site_proxy(site: dict, enabled: bool) -> dict:
    result = deepcopy(site)
    result["proxy"] = bool(enabled)
    return result


def should_continue_pages(*, result_count: int, page_size: int, new_count: int) -> bool:
    return new_count > 0 and result_count >= page_size
