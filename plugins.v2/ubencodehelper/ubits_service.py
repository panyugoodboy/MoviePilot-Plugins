import re
from dataclasses import dataclass
from html import unescape
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from app.core.config import settings
from app.db.site_oper import SiteOper
from app.utils.http import RequestUtils


PASSKEY_PATTERN = re.compile(r"^[A-Za-z0-9]{16,128}$")


@dataclass
class UBitsCredentials:
    site: object
    cookie: str
    passkey: str
    username: str = ""


class UBitsService:
    @staticmethod
    def _is_ubits(site) -> bool:
        values = [str(getattr(site, name, "") or "").lower() for name in ("domain", "url")]
        return any("ubits.club" in value for value in values)

    def find_site(self):
        sites = list(SiteOper().list() or [])
        return next((site for site in sites if self._is_ubits(site)), None)

    @staticmethod
    def extract_passkey(*values: str) -> str:
        for value in values:
            text = str(value or "")
            if not text:
                continue
            parsed = urlparse(text)
            query = parse_qs(parsed.query)
            for key in ("passkey", "pass_key"):
                candidate = str((query.get(key) or [""])[0]).strip()
                if PASSKEY_PATTERN.fullmatch(candidate):
                    return candidate
            match = re.search(r"(?:passkey|pass_key)=([A-Za-z0-9]{16,128})", text, re.IGNORECASE)
            if match and PASSKEY_PATTERN.fullmatch(match.group(1)):
                return match.group(1)
        return ""

    @staticmethod
    def _extract_username(html_text: str) -> str:
        soup = BeautifulSoup(html_text or "", "html.parser")
        for anchor in soup.find_all("a", href=re.compile(r"userdetails\.php\?id=\d+", re.IGNORECASE)):
            username = re.sub(r"\s+", " ", unescape(anchor.get_text(" ", strip=True))).strip()
            if username:
                return username
        return ""

    def inspect(self) -> dict:
        try:
            credentials = self.credentials()
        except RuntimeError as exc:
            return {"ok": False, "message": str(exc)}
        return {
            "ok": True,
            "message": "UBits Cookie 与 Passkey 验证成功",
            "site_name": str(getattr(credentials.site, "name", "UBits") or "UBits"),
            "username": credentials.username,
            "cookie_ok": True,
            "passkey_ok": True,
            "passkey_tail": credentials.passkey[-4:],
        }

    def credentials(self) -> UBitsCredentials:
        site = self.find_site()
        if not site:
            raise RuntimeError("MoviePilot 未配置 UBits 站点")
        if not bool(getattr(site, "is_active", False)):
            raise RuntimeError("MoviePilot 中的 UBits 站点已禁用")
        cookie = str(getattr(site, "cookie", "") or "").strip()
        if not cookie:
            raise RuntimeError("UBits Cookie 为空")

        proxies = settings.PROXY if int(getattr(site, "proxy", 0) or 0) else None
        response = RequestUtils(
            cookies=cookie,
            ua=str(getattr(site, "ua", "") or ""),
            proxies=proxies,
            timeout=int(getattr(site, "timeout", 15) or 15),
        ).get_res(url=str(getattr(site, "url", "") or "https://ubits.club/"))
        if not response:
            raise RuntimeError("UBits 请求超时或被代理拦截")
        if not response.ok:
            raise RuntimeError(f"UBits 返回 HTTP {response.status_code}")
        final_url = str(getattr(response, "url", "") or "").lower()
        html_text = str(getattr(response, "text", "") or "")
        lower_html = html_text.lower()
        if "login.php" in final_url or ("name=\"username\"" in lower_html and "logout.php" not in lower_html):
            raise RuntimeError("UBits Cookie 已过期")

        rss = str(getattr(site, "rss", "") or "")
        passkey = self.extract_passkey(rss, html_text)
        if not passkey:
            raise RuntimeError("Cookie 有效，但未找到 UBits Passkey")
        return UBitsCredentials(
            site=site,
            cookie=cookie,
            passkey=passkey,
            username=self._extract_username(html_text),
        )
