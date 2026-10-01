import asyncio
from typing import Any

import httpx


TRANSIENT_STATUSES = {429, 502, 503, 504}
RETRY_ATTEMPTS = 4
RETRY_BASE_DELAY_SECONDS = 2.0
MAX_RETRY_DELAY_SECONDS = 60.0


def retry_delay_seconds(response: httpx.Response | None, attempt: int) -> float:
    if response is not None:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return min(MAX_RETRY_DELAY_SECONDS, max(0.0, float(retry_after)))
            except ValueError:
                pass
    return min(MAX_RETRY_DELAY_SECONDS, RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)))


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs: Any,
) -> httpx.Response:
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.TransportError:
            if attempt >= RETRY_ATTEMPTS:
                raise
            await asyncio.sleep(retry_delay_seconds(None, attempt))
            continue
        if response.status_code in TRANSIENT_STATUSES and attempt < RETRY_ATTEMPTS:
            await asyncio.sleep(retry_delay_seconds(response, attempt))
            continue
        return response
    raise RuntimeError("Request retry loop exited unexpectedly.")
