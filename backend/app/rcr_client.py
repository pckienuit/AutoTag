import asyncio
from typing import Any
from urllib.parse import quote

import httpx

from .http_retry import request_with_retry
from .settings import get_settings


class RcrError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"RCR HTTP {status}: {message}")
        self.status = status
        self.message = message


class RcrConflictError(RcrError):
    """The task revision on the server is newer than the one we sent."""


def response_error(response: httpx.Response) -> RcrError:
    try:
        body = response.json()
        message = str(body.get("error") or body) if isinstance(body, dict) else str(body)
    except ValueError:
        message = response.text.strip()[:500]
    error_class = RcrConflictError if response.status_code == 409 else RcrError
    return error_class(response.status_code, message or response.reason_phrase)


class RcrClient:
    def __init__(self) -> None:
        self.client: httpx.AsyncClient | None = None
        self.csrf = ""
        self.llm_enabled = False
        self._login_lock = asyncio.Lock()

    def _http(self) -> httpx.AsyncClient:
        if self.client is None:
            self.client = httpx.AsyncClient(
                base_url=get_settings().rcr_base_url.rstrip("/"),
                timeout=45.0,
                follow_redirects=True,
            )
        return self.client

    async def close(self) -> None:
        if self.client is not None:
            await self.client.aclose()
            self.client = None
        self.csrf = ""

    async def login(self) -> dict[str, Any]:
        settings = get_settings()
        if not settings.rcr_username or not settings.rcr_password:
            raise ValueError("RCR username/password are required (RCR_USERNAME / RCR_PASSWORD).")
        async with self._login_lock:
            payload = {"username": settings.rcr_username, "password": settings.rcr_password}
            response = await request_with_retry(self._http(), "POST", "/api/auth/login", json=payload)
            if not response.is_success:
                raise response_error(response)
            data = response.json()
            self.csrf = str(data.get("csrf") or "")
            if not self.csrf:
                self.csrf = str((await self._me_raw()).get("csrf") or "")
            return data

    async def _me_raw(self) -> dict[str, Any]:
        response = await request_with_retry(self._http(), "GET", "/api/me")
        if not response.is_success:
            raise response_error(response)
        data = response.json()
        self.llm_enabled = bool(data.get("llm_enabled"))
        return data

    async def me(self) -> dict[str, Any]:
        await self.ensure_login()
        return await self._me_raw()

    async def ensure_login(self) -> None:
        if not self.csrf:
            await self.login()

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        await self.ensure_login()
        extra_headers = dict(kwargs.pop("headers", None) or {})
        for attempt in (1, 2):
            headers = dict(extra_headers)
            if method != "GET":
                headers["X-CSRF-Token"] = self.csrf
            response = await request_with_retry(self._http(), method, path, headers=headers, **kwargs)
            if response.status_code == 401 and attempt == 1:
                self.csrf = ""
                await self.login()
                continue
            if not response.is_success:
                raise response_error(response)
            return response
        raise RuntimeError("RCR request retry loop exited unexpectedly.")

    async def list_tasks(self) -> list[dict[str, Any]]:
        response = await self.request("GET", "/api/work/tasks")
        return list(response.json().get("tasks") or [])

    async def get_task(self, sample_id: str) -> dict[str, Any]:
        response = await self.request("GET", f"/api/work/tasks/{quote(sample_id, safe='')}")
        return response.json()

    async def image_bytes(self, image_url: str) -> tuple[bytes, str]:
        response = await self.request("GET", image_url)
        return response.content, response.headers.get("content-type", "image/jpeg")

    async def _mutate(self, sample_id: str, action: str, body: dict[str, Any]) -> dict[str, Any]:
        path = f"/api/work/tasks/{quote(sample_id, safe='')}/{action}"
        response = await self.request("POST", path, json=body)
        return response.json()

    async def save_draft(self, sample_id: str, revision: int, annotation: dict[str, Any]) -> dict[str, Any]:
        return await self._mutate(
            sample_id, "draft", {"expected_revision": revision, "annotation": annotation}
        )

    async def submit(self, sample_id: str, revision: int, annotation: dict[str, Any]) -> dict[str, Any]:
        return await self._mutate(
            sample_id, "submit", {"expected_revision": revision, "annotation": annotation}
        )

    async def reopen(self, sample_id: str, revision: int) -> dict[str, Any]:
        return await self._mutate(sample_id, "reopen", {"expected_revision": revision})


rcr_client = RcrClient()
