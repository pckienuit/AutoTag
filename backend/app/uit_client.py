import base64
import mimetypes
from typing import Any
from urllib.parse import urljoin

import httpx

from .settings import get_settings


class UitClient:
    def __init__(self) -> None:
        self.base_url = "https://aiclub.uit.edu.vn"
        self.api_prefix = "/label_cpr/api"
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=45.0, follow_redirects=True)

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
        response.raise_for_status()
        return response.json()

    async def me(self) -> dict[str, Any] | None:
        response = await self.client.get(f"{self.api_prefix}/me")
        if response.status_code == 401:
            return None
        response.raise_for_status()
        return response.json()

    async def ensure_login(self) -> None:
        if await self.me() is None:
            await self.login()

    async def get_json(self, path: str) -> dict[str, Any]:
        await self.ensure_login()
        response = await self.client.get(path)
        response.raise_for_status()
        return response.json()

    async def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        await self.ensure_login()
        response = await self.client.post(path, json=payload)
        response.raise_for_status()
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

    async def image_as_data_url(self, image_url: str) -> str:
        await self.ensure_login()
        absolute_url = urljoin(self.base_url, image_url)
        response = await self.client.get(absolute_url)
        response.raise_for_status()
        content_type = response.headers.get("content-type")
        if not content_type:
            content_type = mimetypes.guess_type(absolute_url)[0] or "image/jpeg"
        encoded = base64.b64encode(response.content).decode("ascii")
        return f"data:{content_type};base64,{encoded}"

    def absolute_url(self, image_url: str | None) -> str | None:
        return urljoin(self.base_url, image_url) if image_url else None

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
