import base64
import io
import mimetypes
import time
from typing import Any
from urllib.parse import urljoin

import httpx
from PIL import Image

from .settings import get_settings


class UitRateLimitError(RuntimeError):
    pass


def raise_for_status_with_body(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = response.text.strip()
        if detail:
            message = f"{exc} Response body: {detail[:1000]}"
            raise httpx.HTTPStatusError(message, request=exc.request, response=exc.response) from exc
        raise


class UitClient:
    def __init__(self) -> None:
        self.base_url = "https://aiclub.uit.edu.vn"
        self.api_prefix = "/label_cpr/api"
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=45.0, follow_redirects=True)
        self._authenticated_until = 0.0

    async def close(self) -> None:
        await self.client.aclose()

    async def login(self, email: str | None = None, password: str | None = None) -> dict[str, Any]:
        settings = get_settings()
        payload = {
            "email": email or settings.uit_email,
            "password": password or settings.uit_password,
        }
        if not payload["email"] or not payload["password"]:
            raise ValueError("UIT email/password are required.")
        response = await self.client.post(f"{self.api_prefix}/auth/login", json=payload)
        raise_for_status_with_body(response)
        self._authenticated_until = time.monotonic() + 900
        return response.json()

    async def me(self) -> dict[str, Any] | None:
        response = await self.client.get(f"{self.api_prefix}/me")
        if response.status_code == 401:
            return None
        if response.status_code == 429:
            raise UitRateLimitError("UIT rate limit reached. Please wait before retrying.")
        raise_for_status_with_body(response)
        return response.json()

    async def ensure_login(self) -> None:
        if time.monotonic() < self._authenticated_until:
            return
        if await self.me() is None:
            await self.login()
        else:
            self._authenticated_until = time.monotonic() + 900

    async def get_json(self, path: str) -> dict[str, Any]:
        await self.ensure_login()
        response = await self.client.get(path)
        if response.status_code == 401:
            self._authenticated_until = 0.0
            await self.login()
            response = await self.client.get(path)
        if response.status_code == 429:
            raise UitRateLimitError("UIT rate limit reached. Please wait before retrying.")
        raise_for_status_with_body(response)
        return response.json()

    async def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        await self.ensure_login()
        response = await self.client.post(path, json=payload)
        if response.status_code == 401:
            self._authenticated_until = 0.0
            await self.login()
            response = await self.client.post(path, json=payload)
        if response.status_code == 429:
            raise UitRateLimitError("UIT rate limit reached. Please wait before retrying.")
        raise_for_status_with_body(response)
        return response.json()

    async def sessions(self) -> dict[str, Any]:
        return await self.get_json(f"{self.api_prefix}/annotator/sessions")

    async def current_task(self, session_id: str) -> dict[str, Any]:
        return await self.get_json(f"{self.api_prefix}/annotator/sessions/{session_id}/current-task")

    async def task(self, task_id: str, session_id: str | None = None) -> dict[str, Any]:
        if session_id:
            return await self.get_json(f"{self.api_prefix}/annotator/sessions/{session_id}/tasks/{task_id}")
        return await self.get_json(f"{self.api_prefix}/annotator/tasks/{task_id}")

    async def submissions(self, session_id: str, sent: bool = False) -> dict[str, Any]:
        value = "yes" if sent else "no"
        return await self.get_json(
            f"{self.api_prefix}/annotator/sessions/{session_id}/my-submissions?sent={value}"
        )

    async def save(
        self,
        task: dict[str, Any],
        annotation: dict[str, Any],
        time_spent: int,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        task_id = str(task["id"])
        path = (
            f"{self.api_prefix}/annotator/sessions/{session_id}/tasks/{task_id}/save"
            if session_id
            else f"{self.api_prefix}/annotator/tasks/{task_id}/save"
        )
        payload = self._mutation_payload(task, annotation, time_spent)
        return await self.post_json(path, payload)

    async def submit(
        self,
        task: dict[str, Any],
        annotation: dict[str, Any],
        time_spent: int,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        task_id = str(task["id"])
        path = (
            f"{self.api_prefix}/annotator/sessions/{session_id}/tasks/{task_id}/submit"
            if session_id
            else f"{self.api_prefix}/annotator/tasks/{task_id}/submit"
        )
        payload = self._mutation_payload(task, annotation, time_spent)
        return await self.post_json(path, payload)

    async def image_as_data_url(self, image_url: str, max_side: int = 512, quality: int = 60) -> str:
        await self.ensure_login()
        absolute_url = urljoin(self.base_url, image_url)
        response = await self.client.get(absolute_url)
        if response.status_code == 401:
            self._authenticated_until = 0.0
            await self.login()
            response = await self.client.get(absolute_url)
        if response.status_code == 429:
            raise UitRateLimitError("UIT rate limit reached. Please wait before retrying.")
        raise_for_status_with_body(response)
        content_type = response.headers.get("content-type")
        image_bytes = response.content
        if content_type and content_type.startswith("image/"):
            image_bytes = self._compress_image(response.content, max_side, quality)
            content_type = "image/jpeg"
        if not content_type:
            content_type = mimetypes.guess_type(absolute_url)[0] or "image/jpeg"
        encoded = base64.b64encode(image_bytes).decode("ascii")
        return f"data:{content_type};base64,{encoded}"

    def absolute_url(self, image_url: str | None) -> str | None:
        return urljoin(self.base_url, image_url) if image_url else None

    @staticmethod
    def _compress_image(content: bytes, max_side: int, quality: int) -> bytes:
        with Image.open(io.BytesIO(content)) as image:
            image = image.convert("RGB")
            image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=quality, optimize=True)
            return output.getvalue()

    @staticmethod
    def _mutation_payload(
        task: dict[str, Any], annotation: dict[str, Any], time_spent: int
    ) -> dict[str, Any]:
        def version(value: Any) -> int | None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        return {
            "annotation": annotation,
            "timeSpent": max(0, int(time_spent or 0)),
            "claimToken": task.get("claimToken"),
            "expectedReservationVersion": version(task.get("reservationVersion")),
            "expectedDraftVersion": version(task.get("draftVersion")) or 0,
        }


uit_client = UitClient()
