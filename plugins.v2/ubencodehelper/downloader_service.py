import hashlib
import uuid
from typing import Dict, List
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bencode import bdecode, bencode

from app.core.config import settings
from app.helper.downloader import DownloaderHelper
from app.utils.http import RequestUtils


class DownloaderService:
    def __init__(self, plugin):
        self.plugin = plugin

    @staticmethod
    def options() -> List[dict]:
        result = []
        for config in DownloaderHelper().get_configs().values():
            result.append({
                "title": f"{config.name} ({config.type})",
                "value": config.name,
                "type": config.type,
            })
        return result

    @staticmethod
    def inspect(name: str) -> dict:
        service = DownloaderHelper().get_service(name=name)
        if not service:
            return {"ok": False, "message": "下载器不存在或未启用"}
        if service.instance.is_inactive():
            return {"ok": False, "message": f"下载器 {service.name} 当前无法连接"}
        return {
            "ok": True,
            "message": f"下载器 {service.name} 连接正常",
            "name": service.name,
            "type": service.type,
        }

    @staticmethod
    def build_download_url(base_url: str, source_url: str, passkey: str) -> str:
        absolute = urljoin(base_url, source_url)
        parsed = urlparse(absolute)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        if passkey and not query.get("passkey") and not query.get("pass_key"):
            query["passkey"] = passkey
        return urlunparse(parsed._replace(query=urlencode(query)))

    @staticmethod
    def torrent_hash(content: bytes) -> str:
        try:
            decoded = bdecode(content)
            info = None
            if isinstance(decoded, dict):
                info = decoded.get("info") or decoded.get(b"info")
            if not info:
                raise ValueError
            return hashlib.sha1(bencode(info)).hexdigest()
        except Exception as exc:
            raise RuntimeError("下载内容不是有效种子文件，请检查 UBits Cookie") from exc

    def push(
        self,
        *,
        task_id: int,
        source_url: str,
        credentials,
        downloader_name: str,
        download_dir: str = "",
        category: str = "UBencode",
        tags: str = "UBencode,待压制",
    ) -> dict:
        mapping: Dict[str, dict] = dict(self.plugin.get_data("download_map") or {})
        existing = mapping.get(str(task_id))
        if existing:
            return {
                "ok": True,
                "duplicate": True,
                "torrent_hash": str(existing.get("torrent_hash") or ""),
                "message": "该任务已经推送到下载器",
            }

        service = DownloaderHelper().get_service(name=downloader_name)
        if not service or service.instance.is_inactive():
            raise RuntimeError("所选下载器不存在或无法连接")
        url = self.build_download_url(
            str(getattr(credentials.site, "url", "") or "https://ubits.club/"),
            source_url,
            credentials.passkey,
        )
        proxies = settings.PROXY if int(getattr(credentials.site, "proxy", 0) or 0) else None
        response = RequestUtils(
            cookies=credentials.cookie,
            ua=str(getattr(credentials.site, "ua", "") or ""),
            proxies=proxies,
            timeout=int(getattr(credentials.site, "timeout", 15) or 15),
        ).get_res(url=url)
        if not response or not response.ok:
            raise RuntimeError("源种下载失败，请检查 UBits Cookie")
        content = bytes(response.content or b"")
        info_hash = self.torrent_hash(content)
        torrents, _ = service.instance.get_torrents(ids=[info_hash])
        if torrents:
            mapping[str(task_id)] = {
                "torrent_hash": info_hash,
                "downloader": service.name,
                "duplicate": True,
            }
            self.plugin.save_data("download_map", mapping)
            return {
                "ok": True,
                "duplicate": True,
                "torrent_hash": info_hash,
                "message": "下载器中已存在该种子",
            }

        tag_items = [item.strip() for item in str(tags or "").split(",") if item.strip()]
        marker = f"UBH-{uuid.uuid4().hex[:10]}"
        if service.type == "qbittorrent":
            state = service.instance.add_torrent(
                content=content,
                download_dir=download_dir or None,
                category=category or None,
                tag=tag_items + [marker],
            )
            if not state:
                raise RuntimeError("qBittorrent 拒绝添加种子")
            added_hash = service.instance.get_torrent_id_by_tag(tags=marker) or info_hash
        elif service.type == "transmission":
            torrent = service.instance.add_torrent(
                content=content,
                download_dir=download_dir or None,
                labels=tag_items,
            )
            if not torrent:
                raise RuntimeError("Transmission 拒绝添加种子")
            added_hash = str(getattr(torrent, "hashString", "") or info_hash)
        elif service.type == "rtorrent":
            state = service.instance.add_torrent(
                content=content,
                download_dir=download_dir or None,
                tags=tag_items,
            )
            if not state:
                raise RuntimeError("rTorrent 拒绝添加种子")
            added_hash = info_hash
        else:
            raise RuntimeError(f"暂不支持下载器类型 {service.type}")

        mapping[str(task_id)] = {
            "torrent_hash": added_hash,
            "downloader": service.name,
            "duplicate": False,
        }
        self.plugin.save_data("download_map", mapping)
        return {
            "ok": True,
            "duplicate": False,
            "torrent_hash": added_hash,
            "message": f"源种已推送到 {service.name}",
        }
