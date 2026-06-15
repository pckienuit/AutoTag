import base64
import asyncio
import io
import mimetypes
import time
from typing import Any
from urllib.parse import urljoin

import httpx
from PIL import Image, ImageDraw

from .settings import get_settings


class UitRateLimitError(RuntimeError):
    pass


RATE_LIMIT_STATUS = 429
TRANSIENT_UIT_STATUSES = {RATE_LIMIT_STATUS, 502, 503, 504}
UIT_RETRY_ATTEMPTS = 4
UIT_RETRY_BASE_DELAY_SECONDS = 2.0
UIT_MAX_RETRY_DELAY_SECONDS = 60.0


def retry_delay_seconds(response: httpx.Response | None, attempt: int) -> float:
    if response is not None:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return min(UIT_MAX_RETRY_DELAY_SECONDS, max(0.0, float(retry_after)))
            except ValueError:
                pass
    return min(UIT_MAX_RETRY_DELAY_SECONDS, UIT_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)))


def rate_limit_message(response: httpx.Response) -> str:
    delay = retry_delay_seconds(response, 1)
    return f"UIT rate limit reached after retries. Please wait about {int(delay)} seconds before retrying."


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
        response = await self.request_with_retry("POST", f"{self.api_prefix}/auth/login", json=payload)
        raise_for_status_with_body(response)
        self._authenticated_until = time.monotonic() + 900
        return response.json()

    async def me(self) -> dict[str, Any] | None:
        response = await self.request_with_retry("GET", f"{self.api_prefix}/me")
        if response.status_code == 401:
            return None
        if response.status_code == 429:
            raise UitRateLimitError(rate_limit_message(response))
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
        response = await self.request_with_retry("GET", path)
        if response.status_code == 401:
            self._authenticated_until = 0.0
            await self.login()
            response = await self.request_with_retry("GET", path)
        if response.status_code == 429:
            raise UitRateLimitError(rate_limit_message(response))
        raise_for_status_with_body(response)
        return response.json()

    async def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        await self.ensure_login()
        response = await self.request_with_retry("POST", path, json=payload)
        if response.status_code == 401:
            self._authenticated_until = 0.0
            await self.login()
            response = await self.request_with_retry("POST", path, json=payload)
        if response.status_code == 429:
            raise UitRateLimitError(rate_limit_message(response))
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

    async def image_as_data_url(
        self,
        image_url: str,
        max_side: int = 512,
        quality: int = 60,
        boxes: list[dict[str, Any]] | None = None,
    ) -> str:
        await self.ensure_login()
        absolute_url = urljoin(self.base_url, image_url)
        response = await self.request_with_retry("GET", absolute_url)
        if response.status_code == 401:
            self._authenticated_until = 0.0
            await self.login()
            response = await self.request_with_retry("GET", absolute_url)
        if response.status_code == 429:
            raise UitRateLimitError(rate_limit_message(response))
        raise_for_status_with_body(response)
        content_type = response.headers.get("content-type")
        image_bytes = response.content
        if content_type and content_type.startswith("image/"):
            image_bytes = self._prepare_image(response.content, max_side, quality, boxes)
            content_type = "image/jpeg"
        if not content_type:
            content_type = mimetypes.guess_type(absolute_url)[0] or "image/jpeg"
        encoded = base64.b64encode(image_bytes).decode("ascii")
        return f"data:{content_type};base64,{encoded}"

    async def request_with_retry(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        last_transport_error: httpx.TransportError | None = None
        for attempt in range(1, UIT_RETRY_ATTEMPTS + 1):
            try:
                response = await self.client.request(method, url, **kwargs)
            except httpx.TransportError as exc:
                last_transport_error = exc
                if attempt >= UIT_RETRY_ATTEMPTS:
                    raise
                await asyncio.sleep(retry_delay_seconds(None, attempt))
                continue
            if response.status_code in TRANSIENT_UIT_STATUSES and attempt < UIT_RETRY_ATTEMPTS:
                await asyncio.sleep(retry_delay_seconds(response, attempt))
                continue
            return response
        if last_transport_error is not None:
            raise last_transport_error
        raise RuntimeError("UIT request retry loop exited unexpectedly.")

    def absolute_url(self, image_url: str | None) -> str | None:
        return urljoin(self.base_url, image_url) if image_url else None

    @staticmethod
    def _prepare_image(
        content: bytes,
        max_side: int,
        quality: int,
        boxes: list[dict[str, Any]] | None = None,
    ) -> bytes:
        with Image.open(io.BytesIO(content)) as image:
            image = image.convert("RGB")
            image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            if boxes:
                UitClient._draw_boxes(image, boxes)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=quality, optimize=True)
            return output.getvalue()

    @staticmethod
    def _compress_image(content: bytes, max_side: int, quality: int) -> bytes:
        return UitClient._prepare_image(content, max_side, quality)

    @staticmethod
    def _draw_boxes(image: Image.Image, boxes: list[dict[str, Any]]) -> None:
        draw = ImageDraw.Draw(image)
        width, height = image.size
        colors = ["#14b8a6", "#f59e0b", "#3b82f6", "#ef4444", "#a855f7"]
        for index, box in enumerate(boxes):
            coords = UitClient._box_coords(box, width, height)
            if not coords:
                continue
            left, top, right, bottom = coords
            color = colors[index % len(colors)]
            for offset in range(3):
                draw.rectangle(
                    (left - offset, top - offset, right + offset, bottom + offset),
                    outline=color,
                )
            label = str(box.get("groupUid") or box.get("label") or box.get("id") or index + 1)
            text_bbox = draw.textbbox((left, top), label)
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]
            label_bottom = max(0, top)
            label_top = max(0, label_bottom - text_height - 6)
            draw.rectangle((left, label_top, left + text_width + 8, label_bottom), fill=color)
            draw.text((left + 4, label_top + 2), label, fill="#000000")

    @staticmethod
    def _box_coords(box: dict[str, Any], image_width: int, image_height: int) -> tuple[int, int, int, int] | None:
        try:
            x = float(box.get("x", 0))
            y = float(box.get("y", 0))
            width = float(box.get("width", 0))
            height = float(box.get("height", 0))
        except (TypeError, ValueError):
            return None
        if width <= 0 or height <= 0:
            return None
        if x <= 1 and y <= 1 and width <= 1 and height <= 1:
            x *= image_width
            y *= image_height
            width *= image_width
            height *= image_height
        left = max(0, min(image_width - 1, round(x)))
        top = max(0, min(image_height - 1, round(y)))
        right = max(0, min(image_width - 1, round(x + width)))
        bottom = max(0, min(image_height - 1, round(y + height)))
        if right <= left or bottom <= top:
            return None
        return left, top, right, bottom

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
