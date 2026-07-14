import time
import uuid
from datetime import datetime
from typing import Any, Dict

from .api_client import ApiClientError, UBencodeApiClient


class AuthService:
    def __init__(self, plugin):
        self.plugin = plugin

    def device_id(self) -> str:
        value = str((self.plugin.get_data("device") or {}).get("device_id") or "")
        if value:
            return value
        value = f"moviepilot-{uuid.uuid4().hex}"
        self.plugin.save_data("device", {"device_id": value})
        return value

    def client(self) -> UBencodeApiClient:
        return UBencodeApiClient(self.device_id())

    def auth_data(self) -> Dict[str, Any]:
        return dict(self.plugin.get_data("auth") or {})

    def token(self) -> str:
        return str(self.auth_data().get("refresh_token") or "")

    def bind(self, username: str, password: str, captcha_id: str, captcha_answer: str) -> dict:
        result = self.client().login(username, password, captcha_id, captcha_answer)
        token = str(result.get("refresh_token") or "")
        if not token:
            raise ApiClientError("压制中心未返回登录令牌")
        profile = self.client().me(token)
        auth = {
            "refresh_token": token,
            "username": str(profile.get("username") or result.get("username") or username),
            "role": str(profile.get("role") or result.get("role") or ""),
            "enabled": bool(profile.get("enabled", True)),
            "user_expires_at": int(profile.get("user_expires_at") or 0),
            "refresh_expires_at": int(profile.get("refresh_expires_at") or result.get("refresh_expires_at") or 0),
            "verified_at": int(time.time()),
        }
        self.plugin.save_data("auth", auth)
        return self.public_status(auth)

    def verify(self) -> dict:
        token = self.token()
        if not token:
            return {"ok": False, "message": "尚未绑定压制中心账号"}
        profile = self.client().me(token)
        auth = self.auth_data()
        auth.update({
            "username": str(profile.get("username") or auth.get("username") or ""),
            "role": str(profile.get("role") or auth.get("role") or ""),
            "enabled": bool(profile.get("enabled", True)),
            "user_expires_at": int(profile.get("user_expires_at") or 0),
            "refresh_expires_at": int(profile.get("refresh_expires_at") or 0),
            "verified_at": int(time.time()),
        })
        self.plugin.save_data("auth", auth)
        return self.public_status(auth)

    def logout(self) -> dict:
        token = self.token()
        if token:
            try:
                self.client().logout(token)
            except ApiClientError:
                pass
        self.plugin.del_data("auth")
        return {"ok": True, "message": "已解除压制中心绑定"}

    @staticmethod
    def public_status(auth: Dict[str, Any]) -> dict:
        username = str(auth.get("username") or "")
        role = str(auth.get("role") or "")
        token = str(auth.get("refresh_token") or "")
        enabled = bool(auth.get("enabled", True))
        user_expires_at = int(auth.get("user_expires_at") or 0)
        expires_at = int(auth.get("refresh_expires_at") or 0)
        now = int(time.time())
        role_text = {"full": "完整版", "basic": "工具版"}.get(role, role or "未知")
        expires_text = datetime.fromtimestamp(expires_at).strftime("%Y-%m-%d %H:%M") if expires_at else "未知"
        ok = bool(username and token and enabled and (not user_expires_at or user_expires_at > now) and expires_at > now)
        if not username or not token:
            message = "尚未绑定压制中心账号"
        elif not enabled:
            message = "压制中心账号授权已停用，请联系管理员"
        elif user_expires_at and user_expires_at <= now:
            message = "压制中心账号授权已过期，请联系管理员"
        elif expires_at <= now:
            message = "压制中心登录已过期，请重新绑定账号"
        else:
            message = f"已绑定账号 {username}，权限：{role_text}，令牌有效期：{expires_text}"
        return {
            "ok": ok,
            "username": username,
            "role": role,
            "refresh_expires_at": expires_at,
            "message": message,
        }
