"""MoviePilot and Emby integration for the plugin."""

from __future__ import annotations

import hashlib
from threading import Lock
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import urlencode

from app.chain.download import DownloadChain
from app.chain.search import SearchChain
from app.core.context import Context, MediaInfo, TorrentInfo
from app.core.metainfo import MetaInfo
from app.db.site_oper import SiteOper
from app.helper.mediaserver import MediaServerHelper
from app.helper.sites import SitesHelper
from app.log import logger
from app.schemas.types import MediaType

from .quality import (
    apply_source_quality_type,
    classify_quality,
    is_series_title,
    merge_profile,
    profile_score,
    quality_matches,
    select_save_path,
    tv_exclusion_reason,
)
from .sources import UBITS_MOVIE_SOURCES, build_category_site, is_ubits_domain, should_continue_pages
from .store import PluginStore, dumps


class LibraryDownloadService:
    def __init__(self, store: PluginStore, config: Callable[[], Mapping[str, Any]]):
        self.store = store
        self.config = config
        self._auto_download_lock = Lock()

    def options(self) -> dict:
        sites = [
            {
                "id": site.id,
                "name": site.name,
                "domain": site.domain,
                "active": bool(site.is_active),
            }
            for site in SiteOper().list_order_by_pri() or []
            if site and site.is_active
        ]
        servers = []
        for name, service in MediaServerHelper().get_services(type_filter="emby").items():
            libraries = []
            try:
                for library in service.instance.get_librarys() or []:
                    libraries.append({
                        "id": str(_value(library, "id", "")),
                        "name": str(_value(library, "name", "")),
                        "type": str(_value(library, "type", "")),
                    })
            except Exception as error:
                logger.warning(f"[联动EMBY库筛选下载] 读取 {name} 媒体库失败：{error}")
            servers.append({"name": name, "libraries": libraries})
        return {"sites": sites, "emby_servers": servers}

    def sync_inventory(self) -> dict:
        config = self.config()
        selected_names = set(config.get("emby_servers") or [])
        selected_libraries = config.get("emby_libraries") or {}
        services = MediaServerHelper().get_services(type_filter="emby")
        if selected_names:
            services = {name: value for name, value in services.items() if name in selected_names}
        if not services:
            raise RuntimeError("未找到已启用的 Emby 服务")
        self.store.prune_inventory_servers(services.keys())

        total = 0
        server_results = []
        for name, service in services.items():
            instance = service.instance
            all_libraries = instance.get_librarys() or []
            allowed = {str(value) for value in selected_libraries.get(name, [])}
            libraries = [
                library for library in all_libraries
                if not allowed or str(_value(library, "id", "")) in allowed
            ]
            rows = []
            library_ids = []
            for library in libraries:
                library_id = str(_value(library, "id", ""))
                if not library_id:
                    continue
                library_ids.append(library_id)
                items = self._fetch_emby_library(instance, library_id)
                rows.extend(self._inventory_rows(name, library_id, items, instance))
            self.store.replace_inventory(name, rows)
            total += len(rows)
            server_results.append({"server": name, "libraries": len(library_ids), "versions": len(rows)})
        self.store.mark_inventory_synced()
        return {"versions": total, "servers": server_results}

    def refresh_pool(self, progress: Optional[Callable[[Mapping[str, Any]], None]] = None) -> dict:
        config = self.config()
        selected_sites = set(_ids(config.get("sites")))
        if not selected_sites:
            raise RuntimeError("请先选择至少一个站点")
        sites = [
            site for site in SitesHelper().get_indexers() or []
            if _int(site.get("id")) in selected_sites and is_ubits_domain(site.get("domain"))
        ]
        if not sites:
            raise RuntimeError("电影分类地址仅支持 UBits，请在搜索站点中选择 UBits")
        search = SearchChain()
        candidates: dict[str, dict] = {}
        errors = []
        profile = self._base_profile()
        total_sources = len(sites) * len(UBITS_MOVIE_SOURCES)
        completed_sources = 0
        completed_pages = 0
        for site in sites:
            site_id = _int(site.get("id"))
            site_name = str(site.get("name") or f"站点 {site_id}")
            for source in UBITS_MOVIE_SOURCES:
                category_site = build_category_site(site, source)
                page_size = search.get_search_page_size(site=category_site, keyword="") or 100
                page_index = 0
                seen = set()
                while True:
                    page = page_index + 1
                    self._report_pool_progress(
                        progress, site_id, site_name, source.label, page, completed_pages,
                        completed_sources, total_sources, candidates,
                    )
                    try:
                        torrents = search.search_torrents(
                            site=category_site,
                            keyword="",
                            mtype=MediaType.MOVIE,
                            page=page_index,
                        ) or []
                    except Exception as error:
                        errors.append(f"{site_name} {source.label} 第 {page} 页：{error}")
                        logger.error(
                            f"[联动EMBY库筛选下载] 浏览 {site_name} {source.label} 第 {page} 页失败：{error}"
                        )
                        torrents = []
                    new_count = 0
                    for torrent in torrents:
                        torrent_key = _torrent_key(torrent.to_dict())
                        if torrent_key in seen:
                            continue
                        seen.add(torrent_key)
                        new_count += 1
                        context = Context(
                            meta_info=MetaInfo(title=torrent.title, subtitle=torrent.description),
                            torrent_info=torrent,
                            resource_source="search",
                        )
                        row = self._candidate_row(
                            context,
                            target_id=None,
                            profile=profile,
                            source_quality_type=source.quality_type,
                        )
                        candidates[row["candidate_key"]] = row
                    completed_pages += 1
                    self._report_pool_progress(
                        progress, site_id, site_name, source.label, page, completed_pages,
                        completed_sources, total_sources, candidates,
                    )
                    if not should_continue_pages(
                        result_count=len(torrents),
                        page_size=page_size,
                        new_count=new_count,
                    ):
                        break
                    page_index += 1
                completed_sources += 1
        self._report_pool_progress(
            progress, 0, "UBits", "全部分类", 0, completed_pages,
            completed_sources, total_sources, candidates, finished=True,
        )
        rows = list(candidates.values())
        self.store.replace_candidates("pool", rows)

        auto_result = None
        if config.get("auto_download") and config.get("pool_auto_download"):
            auto_result = self.download_from_pool()
        return {
            "found": len(rows),
            "eligible": sum(1 for row in rows if row["eligible"]),
            "errors": errors,
            "downloads": (auto_result or {}).get("downloads", []),
            "auto_download": auto_result,
        }

    def download_from_pool(self) -> dict:
        config = self.config()
        limit = max(1, min(50, _int(config.get("auto_batch_limit"), 5)))
        if not config.get("auto_download") or not config.get("pool_auto_download"):
            return {
                "requested": limit,
                "attempted": 0,
                "submitted": 0,
                "downloads": [],
                "skipped": [],
                "message": "自动下载开关未同时开启",
            }

        with self._auto_download_lock:
            downloads = []
            skipped = []
            attempted = 0
            for candidate_key in self.store.pending_auto_candidate_keys():
                result = self._dispatch_one(candidate_key, automatic=True)
                attempted += 1
                if result.get("success"):
                    downloads.append(result)
                    if len(downloads) >= limit:
                        break
                elif len(skipped) < 50:
                    skipped.append(result)
            return {
                "requested": limit,
                "attempted": attempted,
                "submitted": len(downloads),
                "downloads": downloads,
                "skipped": skipped,
                "message": f"成功添加 {len(downloads)} / {limit} 个下载",
            }

    def search_targets(self, target_ids: Optional[Iterable[int]] = None) -> dict:
        wanted = {int(value) for value in target_ids or []}
        targets = [
            target for target in self.store.list_targets()
            if target["enabled"] and (not wanted or target["id"] in wanted)
        ]
        search = SearchChain()
        total, eligible, downloads = 0, 0, []
        remaining_auto = max(1, min(50, _int(self.config().get("auto_batch_limit"), 5)))
        for target in targets:
            profile = merge_profile(self._base_profile(), target.get("profile"))
            sites = _ids(target.get("sites")) or _ids(self.config().get("sites"))
            contexts = []
            if target.get("media_id"):
                seasons = target.get("seasons") if target["media_type"] == "tv" else [None]
                seasons = seasons or [None]
                for season in seasons:
                    contexts.extend(search.search_by_id(
                        source=target["media_source"],
                        mediaid=str(target["media_id"]),
                        mtype=MediaType.TV if target["media_type"] == "tv" else MediaType.MOVIE,
                        season=_int(season) or None,
                        sites=sites,
                    ) or [])
            else:
                query = f"{target['title']} {target.get('year') or ''}".strip()
                contexts = search.search_by_title(title=query, sites=sites) or []

            unique = {}
            for context in contexts:
                if not context.media_info:
                    context.media_info = search.recognize_media(meta=context.meta_info)
                row = self._candidate_row(context, target_id=target["id"], profile=profile)
                unique[row["candidate_key"]] = row
            rows = list(unique.values())
            self.store.replace_candidates(f"target:{target['id']}", rows)
            total += len(rows)
            eligible += sum(1 for row in rows if row["eligible"])
            if self.config().get("auto_download") and target.get("auto_download") and remaining_auto > 0:
                page = self.store.list_candidates(
                    page=1, page_size=remaining_auto, scope=f"target:{target['id']}"
                )
                keys = [item["candidate_key"] for item in page["items"]]
                downloads.extend(self.dispatch(keys, automatic=True))
                remaining_auto -= len(keys)
        return {"targets": len(targets), "found": total, "eligible": eligible, "downloads": downloads}

    def dispatch(self, candidate_keys: Iterable[str], automatic: bool = False) -> list[dict]:
        results = []
        for candidate_key in dict.fromkeys(str(value) for value in candidate_keys if value):
            try:
                results.append(self._dispatch_one(candidate_key, automatic))
            except Exception as error:
                logger.error(f"[联动EMBY库筛选下载] 下载 {candidate_key} 失败：{error}")
                results.append({"candidate_key": candidate_key, "success": False, "message": str(error)})
        return results

    def _dispatch_one(self, candidate_key: str, automatic: bool) -> dict:
        if not self.store.inventory_ready():
            return {
                "candidate_key": candidate_key,
                "success": False,
                "message": "尚未完成 Emby 版本库存同步，禁止提交下载",
            }
        candidate = self.store.get_candidate(candidate_key)
        if not candidate:
            return {"candidate_key": candidate_key, "success": False, "message": "候选种子不存在或已刷新"}
        meta = MetaInfo(title=candidate["title"], subtitle=candidate.get("description"))
        media = self._restore_media(candidate.get("media"))
        if not media:
            media = SearchChain().recognize_media(meta=meta)
        if not media:
            return {"candidate_key": candidate_key, "success": False, "message": "媒体信息识别失败，未提交下载"}
        config = self.config()
        excluded_reason = tv_exclusion_reason(
            is_tv=self._is_tv(meta, media, candidate["title"]),
            enabled=bool(config.get("exclude_tv")),
        )
        if excluded_reason:
            return {"candidate_key": candidate_key, "success": False, "message": excluded_reason}
        target = self.store.get_target(candidate["target_id"]) if candidate.get("target_id") else None
        media_keys = self._media_keys(media, meta, target)
        if not media_keys:
            return {"candidate_key": candidate_key, "success": False, "message": "无法建立媒体版本键"}
        self.store.update_candidate_identity(candidate_key, media.to_dict(), media_keys)

        cap = max(1, min(3, _int(config.get("max_versions"), 3)))
        if target:
            cap = min(cap, max(1, _int(target.get("desired_versions"), 1)))
        save_path = select_save_path(
            config,
            candidate.get("quality_type") or "unknown",
            "tv" if media.type == MediaType.TV else "movie",
            (target or {}).get("save_path"),
        )
        job_id, reason = self.store.reserve_download(
            candidate_key=candidate_key,
            media_keys=media_keys,
            max_versions=cap,
            save_path=save_path,
            automatic=automatic,
            allow_same_slot=bool(config.get("allow_same_slot")),
        )
        if not job_id:
            return {"candidate_key": candidate_key, "success": False, "message": reason}

        try:
            torrent = TorrentInfo()
            torrent.from_dict(candidate.get("torrent") or {})
            site = SiteOper().get(candidate.get("site_id")) if candidate.get("site_id") else None
            if not site:
                self.store.update_job(job_id, "failed", error="站点配置不存在")
                return {"candidate_key": candidate_key, "success": False, "message": "站点配置不存在"}
            torrent.site_cookie = site.cookie
            torrent.site_ua = site.ua
            torrent.site_proxy = bool(site.proxy)
            torrent.site_downloader = site.downloader
            context = Context(meta_info=meta, media_info=media, torrent_info=torrent, resource_source="plugin")
            download_id, error = DownloadChain().download_single(
                context=context,
                save_path=save_path or None,
                source="Plugin",
                username="联动EMBY库筛选下载",
                return_detail=True,
            )
        except Exception as error:
            self.store.update_job(job_id, "failed", error=error)
            raise
        if download_id:
            self.store.update_job(job_id, "queued", download_id=download_id)
            return {"candidate_key": candidate_key, "job_id": job_id, "success": True, "download_id": download_id}
        self.store.update_job(job_id, "failed", error=error or "添加下载任务失败")
        return {"candidate_key": candidate_key, "job_id": job_id, "success": False, "message": error or "添加下载任务失败"}

    def _candidate_row(
        self,
        context: Context,
        target_id: Optional[int],
        profile: Mapping[str, Any],
        source_quality_type: str = "",
    ) -> dict:
        torrent = context.torrent_info
        meta = context.meta_info or MetaInfo(title=torrent.title, subtitle=torrent.description)
        quality = apply_source_quality_type(classify_quality(torrent.title, meta), source_quality_type)
        eligible, reason = quality_matches(torrent.title, quality, profile)
        excluded_reason = tv_exclusion_reason(
            is_tv=self._is_tv(meta, context.media_info, torrent.title),
            enabled=bool(self.config().get("exclude_tv")),
        )
        if excluded_reason:
            eligible, reason = False, excluded_reason
        torrent_data = torrent.to_dict()
        for secret in ("site_cookie", "site_ua"):
            torrent_data[secret] = None
        torrent_key = _torrent_key(torrent_data)
        scope = f"target:{target_id}" if target_id else "pool"
        candidate_key = hashlib.sha256(f"{scope}|{torrent_key}".encode()).hexdigest()
        media_data = context.media_info.to_dict() if context.media_info else {}
        media_keys = self._media_keys(context.media_info, meta, None) if context.media_info else []
        return {
            "candidate_key": candidate_key,
            "torrent_key": torrent_key,
            "target_id": target_id,
            "site_id": torrent.site,
            "site_name": torrent.site_name,
            "title": torrent.title or "",
            "description": torrent.description or "",
            "page_url": torrent.page_url,
            "enclosure": torrent.enclosure,
            "size_bytes": _int(torrent.size),
            "seeders": _int(torrent.seeders),
            "peers": _int(torrent.peers),
            "pubdate": torrent.pubdate,
            "year": quality.year,
            "quality_type": quality.quality_type,
            "quality_effect": quality.effect,
            "resolution": quality.resolution,
            "video_codec": quality.video_codec,
            "bitrate_mbps": quality.bitrate_mbps,
            "quality_score": profile_score(quality, profile),
            "quality_slot": quality.slot,
            "eligible": int(eligible),
            "rejection_reason": reason or None,
            "torrent_json": dumps(torrent_data),
            "meta_json": dumps(meta.to_dict()),
            "media_json": dumps(media_data),
            "media_keys_json": dumps(media_keys),
        }

    def _base_profile(self) -> dict:
        config = self.config()
        return {
            "quality_types": config.get("quality_types") or [],
            "effects": config.get("effects") or [],
            "resolutions": config.get("resolutions") or [],
            "video_codecs": config.get("video_codecs") or [],
            "min_bitrate_mbps": config.get("min_bitrate_mbps") or 0,
            "max_bitrate_mbps": config.get("max_bitrate_mbps") or 0,
            "reject_unknown_bitrate": bool(config.get("reject_unknown_bitrate")),
            "bitrate_order": config.get("bitrate_order") or "desc",
            "include_words": config.get("include_words") or "",
            "exclude_words": config.get("exclude_words") or "",
        }

    @staticmethod
    def _report_pool_progress(
        callback: Optional[Callable[[Mapping[str, Any]], None]],
        site_id: int,
        site_name: str,
        category: str,
        page: int,
        completed_pages: int,
        completed_sources: int,
        total_sources: int,
        candidates: Mapping[str, Mapping[str, Any]],
        finished: bool = False,
    ) -> None:
        if not callback:
            return
        callback({
            "site_id": site_id,
            "site_name": site_name,
            "category": category,
            "page": page,
            "completed_pages": completed_pages,
            "completed_sources": completed_sources,
            "total_sources": total_sources,
            "percent": 100 if finished else round(completed_sources * 100 / total_sources, 1),
            "found": len(candidates),
            "eligible": sum(1 for row in candidates.values() if row.get("eligible")),
            "message": "四个电影分类已全部刷新" if finished else f"{site_name} · {category} · 第 {page} 页",
        })

    @staticmethod
    def _is_tv(meta: Any, media: Optional[MediaInfo], title: str = "") -> bool:
        if media and media.type == MediaType.TV:
            return True
        if getattr(meta, "type", None) == MediaType.TV:
            return True
        if getattr(meta, "season_list", None) or getattr(meta, "episode_list", None):
            return True
        if _int(getattr(meta, "begin_season", None)):
            return True
        return is_series_title(f"{title} {getattr(meta, 'title', '')}")

    @staticmethod
    def _restore_media(data: Mapping[str, Any]) -> Optional[MediaInfo]:
        if not data:
            return None
        media = MediaInfo()
        media.from_dict(dict(data))
        return media

    @staticmethod
    def _media_keys(media: MediaInfo, meta: MetaInfo, target: Optional[Mapping[str, Any]]) -> list[str]:
        if not media:
            return []
        identities = (
            (getattr(media, "source", None), getattr(media, "media_id", None)),
            ("themoviedb", getattr(media, "tmdb_id", None)),
            ("imdb", getattr(media, "imdb_id", None)),
            ("tvdb", getattr(media, "tvdb_id", None)),
            ("douban", getattr(media, "douban_id", None)),
            ("bangumi", getattr(media, "bangumi_id", None)),
            ("anilist", getattr(media, "anilist_id", None)),
        )
        source, media_id = next(((source, value) for source, value in identities if source and value), (None, None))
        if not source or media_id is None:
            return []
        media_type = "tv" if media.type == MediaType.TV else "movie"
        base = f"{media_type}:{source}:{media_id}"
        if media_type == "movie":
            return [base]
        seasons = list(getattr(meta, "season_list", None) or [])
        if not seasons and target:
            seasons = [_int(value) for value in target.get("seasons") or [] if _int(value)]
        season = _int(getattr(meta, "begin_season", None)) or (seasons[0] if len(seasons) == 1 else None)
        episodes = [_int(value) for value in getattr(meta, "episode_list", None) or [] if _int(value) >= 0]
        if season and episodes:
            return [f"{base}:S{season:02d}E{episode:02d}" for episode in episodes]
        if season:
            return [f"{base}:S{season:02d}"]
        return [base]

    @staticmethod
    def _fetch_emby_library(instance: Any, library_id: str) -> list[dict]:
        fields = (
            "ProviderIds,OriginalTitle,ProductionYear,Path,ParentId,SeriesId,"
            "ParentIndexNumber,IndexNumber,MediaSources,MediaStreams,DateCreated,PremiereDate"
        )
        result = []
        start, limit = 0, 500
        while True:
            params = urlencode({
                "ParentId": library_id,
                "Recursive": "true",
                "IncludeItemTypes": "Movie,Series,Episode",
                "Fields": fields,
                "StartIndex": start,
                "Limit": limit,
            })
            response = instance.get_data(f"[HOST]emby/Users/[USER]/Items?{params}&api_key=[APIKEY]")
            if not response or response.status_code != 200:
                raise RuntimeError(f"Emby 媒体库 {library_id} 请求失败")
            payload = response.json() or {}
            items = payload.get("Items") or []
            result.extend(items)
            start += len(items)
            total = _int(payload.get("TotalRecordCount"), len(result))
            if not items or start >= total:
                break
        return result

    def _inventory_rows(self, server: str, library_id: str, items: list[dict], instance: Any) -> list[dict]:
        series = {str(item.get("Id")): item for item in items if item.get("Type") == "Series"}
        rows = []
        for item in items:
            item_type = item.get("Type")
            if item_type not in {"Movie", "Episode"}:
                continue
            parent = None
            if item_type == "Episode":
                series_id = str(item.get("SeriesId") or "")
                parent = series.get(series_id)
                if parent is None and series_id:
                    parent = self._fetch_emby_item(instance, series_id)
                    if parent:
                        series[series_id] = parent
            identity_item = parent or item
            media_key, ids = _emby_media_key(server, item, identity_item)
            if not media_key:
                continue
            sources = item.get("MediaSources") or []
            if not sources:
                sources = [{
                    "Id": item.get("Id"),
                    "Name": item.get("Name"),
                    "Path": item.get("Path"),
                    "Bitrate": item.get("Bitrate"),
                    "Size": item.get("Size"),
                    "MediaStreams": item.get("MediaStreams") or [],
                }]
            for source in sources:
                streams = source.get("MediaStreams") or item.get("MediaStreams") or []
                video = next((stream for stream in streams if str(stream.get("Type", "")).lower() == "video"), {})
                audio = next((stream for stream in streams if str(stream.get("Type", "")).lower() == "audio"), {})
                path = source.get("Path") or item.get("Path")
                quality = classify_quality(
                    " ".join(filter(None, [str(source.get("Name") or ""), str(path or "")])),
                    bitrate_bps=source.get("Bitrate") or item.get("Bitrate"),
                    video_stream=video,
                )
                source_id = str(source.get("Id") or path or item.get("Id"))
                version_key = hashlib.sha256(f"{server}|{item.get('Id')}|{source_id}".encode()).hexdigest()
                rows.append({
                    "version_key": version_key,
                    "media_key": media_key,
                    "server_name": server,
                    "library_id": library_id,
                    "item_id": str(item.get("Id")),
                    "media_source_id": source_id,
                    "item_type": "tv" if item_type == "Episode" else "movie",
                    "title": identity_item.get("Name") or item.get("Name") or "",
                    "original_title": identity_item.get("OriginalTitle") or item.get("OriginalTitle"),
                    "year": _int(identity_item.get("ProductionYear")) or None,
                    "season": _int(item.get("ParentIndexNumber")) if item_type == "Episode" else None,
                    "episode": _int(item.get("IndexNumber")) if item_type == "Episode" else None,
                    "path": path,
                    "tmdb_id": ids.get("tmdb"),
                    "imdb_id": ids.get("imdb"),
                    "tvdb_id": ids.get("tvdb"),
                    "quality_type": quality.quality_type,
                    "quality_effect": quality.effect,
                    "resolution": quality.resolution,
                    "video_codec": quality.video_codec,
                    "audio_codec": audio.get("Codec"),
                    "bitrate_mbps": quality.bitrate_mbps,
                    "size_bytes": _int(source.get("Size")),
                    "quality_slot": quality.slot,
                    "date_created": item.get("DateCreated"),
                })
        return rows

    @staticmethod
    def _fetch_emby_item(instance: Any, item_id: str) -> Optional[dict]:
        response = instance.get_data(
            f"[HOST]emby/Users/[USER]/Items/{item_id}?Fields=ProviderIds,OriginalTitle,ProductionYear&api_key=[APIKEY]"
        )
        return response.json() if response and response.status_code == 200 else None


def _emby_media_key(server: str, item: Mapping[str, Any], identity_item: Mapping[str, Any]) -> tuple[str, dict]:
    provider_ids = {str(key).lower(): str(value) for key, value in (identity_item.get("ProviderIds") or {}).items() if value}
    if provider_ids.get("tmdb"):
        source, media_id = "themoviedb", provider_ids["tmdb"]
    elif provider_ids.get("imdb"):
        source, media_id = "imdb", provider_ids["imdb"]
    elif provider_ids.get("tvdb"):
        source, media_id = "tvdb", provider_ids["tvdb"]
    else:
        source, media_id = "emby", f"{server}:{identity_item.get('Id')}"
    if item.get("Type") == "Movie":
        return f"movie:{source}:{media_id}", provider_ids
    if item.get("ParentIndexNumber") is None or item.get("IndexNumber") is None:
        return "", provider_ids
    season = _int(item.get("ParentIndexNumber"))
    episode = _int(item.get("IndexNumber"))
    if season < 0 or episode < 0:
        return "", provider_ids
    return f"tv:{source}:{media_id}:S{season:02d}E{episode:02d}", provider_ids


def _torrent_key(torrent: Mapping[str, Any]) -> str:
    raw = "|".join(str(torrent.get(key) or "") for key in ("site", "enclosure", "page_url", "title"))
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()


def _value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _ids(values: Any) -> list[int]:
    return [_int(value) for value in values or [] if _int(value)]


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
