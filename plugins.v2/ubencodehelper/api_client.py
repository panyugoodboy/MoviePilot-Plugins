import secrets
from typing import Any, Dict, List, Optional

import requests


_CENTER_URL = "https://encode.wuzf.top:53501"
_CLIENT_NAME = "UBencode"
_CLIENT_VERSION = "1.5.3"


class ApiClientError(RuntimeError):
    pass


class UBencodeApiClient:
    def __init__(self, device_id: str, timeout: tuple[int, int] = (10, 30)):
        self.device_id = device_id
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str = "",
        payload: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> Dict[str, Any]:
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = requests.request(
                method=method,
                url=f"{_CENTER_URL}{path}",
                headers=headers,
                json=payload,
                params=params,
                timeout=self.timeout,
            )
        except requests.Timeout as exc:
            raise ApiClientError("压制中心请求超时") from exc
        except requests.RequestException as exc:
            raise ApiClientError("无法连接压制中心") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise ApiClientError("压制中心返回了无法识别的数据") from exc

        if not response.ok:
            detail = str(data.get("detail") or "请求失败") if isinstance(data, dict) else "请求失败"
            detail_map = {
                "captcha expired": "验证码已过期，请重新获取",
                "captcha incorrect": "验证码错误",
                "invalid username or password": "账号或密码错误",
                "invalid refresh token": "绑定已失效，请重新绑定",
                "user disabled": "账号授权已停用",
                "user expired": "账号授权已过期",
                "task not found": "任务不存在",
            }
            raise ApiClientError(detail_map.get(detail, detail[:200]))
        if not isinstance(data, dict):
            raise ApiClientError("压制中心返回格式错误")
        return data

    def captcha(self) -> Dict[str, Any]:
        return self._request("GET", "/api/ubencode/captcha")

    def login(
        self,
        username: str,
        password: str,
        captcha_id: str,
        captcha_answer: str,
    ) -> Dict[str, Any]:
        return self._request(
            "POST",
            "/api/ubencode/login",
            payload={
                "username": username,
                "password": password,
                "client": _CLIENT_NAME,
                "version": _CLIENT_VERSION,
                "nonce": secrets.token_urlsafe(24),
                "device_id": self.device_id,
                "captcha_id": captcha_id,
                "captcha_answer": captcha_answer,
            },
        )

    def me(self, token: str) -> Dict[str, Any]:
        return self._request("GET", "/api/ubencode/me", token=token)

    def logout(self, token: str) -> Dict[str, Any]:
        return self._request(
            "POST",
            "/api/ubencode/logout",
            token=token,
            payload=self._refresh_payload(token),
        )

    def tasks(self, token: str, status: str = "") -> List[dict]:
        data = self._request("GET", "/api/tasks", token=token, params={"status": status} if status else None)
        return list(data.get("tasks") or [])

    def events(self, token: str, after_id: int = 0, limit: int = 100) -> Dict[str, Any]:
        return self._request(
            "GET",
            "/api/ubencode/events",
            token=token,
            params={
                "after_id": max(0, int(after_id or 0)),
                "limit": min(200, max(1, int(limit or 100))),
                "version": _CLIENT_VERSION,
                "device_id": self.device_id,
            },
        )

    def runtime_clients(self, token: str) -> List[dict]:
        data = self._request(
            "GET",
            "/api/ubencode/runtime-clients",
            token=token,
            params={"version": _CLIENT_VERSION, "device_id": self.device_id},
        )
        return list(data.get("clients") or [])

    def runtime_status(self, token: str, target_device_id: str = "") -> Dict[str, Any]:
        return self._request(
            "GET",
            "/api/ubencode/runtime-status",
            token=token,
            params={
                "target_device_id": str(target_device_id or ""),
                "version": _CLIENT_VERSION,
                "device_id": self.device_id,
            },
        )

    def runtime_queues(
        self,
        token: str,
        queue_type: str = "all",
        page: int = 1,
        page_size: int = 8,
        target_device_id: str = "",
    ) -> Dict[str, Any]:
        return self._request(
            "GET",
            "/api/ubencode/runtime-queues",
            token=token,
            params={
                "target_device_id": str(target_device_id or ""),
                "queue_type": str(queue_type or "all"),
                "page": max(1, int(page or 1)),
                "page_size": min(20, max(1, int(page_size or 8))),
                "version": _CLIENT_VERSION,
                "device_id": self.device_id,
            },
        )

    def create_remote_command(
        self,
        token: str,
        target_device_id: str,
        action: str,
        item_id: str,
        parameters: Optional[dict] = None,
        idempotency_key: str = "",
    ) -> Dict[str, Any]:
        return self._request(
            "POST",
            "/api/ubencode/remote-commands",
            token=token,
            payload={
                **self._refresh_payload(token),
                "target_device_id": str(target_device_id or ""),
                "action": str(action or ""),
                "item_id": str(item_id or ""),
                "parameters": dict(parameters or {}),
                "idempotency_key": str(idempotency_key or ""),
                "expires_in": 120,
            },
        )

    def remote_command(self, token: str, command_id: str) -> Dict[str, Any]:
        return self._request(
            "GET",
            f"/api/ubencode/remote-commands/{command_id}",
            token=token,
            params={"version": _CLIENT_VERSION, "device_id": self.device_id},
        )

    def task_payload(self, token: str, task_id: int) -> Dict[str, Any]:
        return self._request(
            "POST",
            f"/api/tasks/{task_id}/payload",
            token=token,
            payload=self._refresh_payload(token),
        )

    def claim(self, token: str, task_id: int, kind: str) -> Dict[str, Any]:
        if kind not in {"test", "encode"}:
            raise ValueError("unsupported claim kind")
        return self._request(
            "POST",
            f"/api/tasks/{task_id}/claim-{kind}",
            token=token,
            payload=self._refresh_payload(token),
        )

    def cancel(self, token: str, task_id: int) -> Dict[str, Any]:
        return self._request(
            "POST",
            f"/api/tasks/{task_id}/cancel",
            token=token,
            payload=self._refresh_payload(token),
        )

    def _refresh_payload(self, token: str) -> dict:
        return {
            "refresh_token": token,
            "client": _CLIENT_NAME,
            "version": _CLIENT_VERSION,
            "device_id": self.device_id,
        }
