import hashlib
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from .settings import STATE_DIR
from .uit_client import raise_for_status_with_body, uit_client


IMAGE_CACHE_DIR = STATE_DIR / "images"


def cached_image_url(filename: str) -> str:
    return f"/api/local/images/{filename}"


def cached_image_path(filename: str) -> Path:
    return IMAGE_CACHE_DIR / filename


def is_cached_image_url(image_url: str) -> bool:
    return image_url.startswith("/api/local/images/")


def image_cache_filename(image_url: str, content_type: str | None = None) -> str:
    suffix = image_url_suffix(image_url)
    if not suffix and content_type:
        suffix = mimetypes.guess_extension(content_type.split(";")[0].strip())
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        suffix = ".jpg"
    digest = hashlib.sha256(image_url.encode("utf-8")).hexdigest()
    return f"{digest}{suffix}"


def image_url_suffix(image_url: str) -> str:
    parsed = urlparse(image_url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix:
        return suffix
    source_paths = parse_qs(parsed.query).get("d", [])
    if source_paths:
        return Path(source_paths[0]).suffix.lower()
    return ""


async def cache_image(image_url: str) -> str:
    if not image_url or is_cached_image_url(image_url):
        return image_url

    filename = image_cache_filename(image_url)
    path = cached_image_path(filename)
    if path.exists():
        return cached_image_url(filename)

    await uit_client.ensure_login()
    absolute_url = urljoin(uit_client.base_url, image_url)
    response = await uit_client.request_with_retry("GET", absolute_url)
    if response.status_code == 401:
        uit_client._authenticated_until = 0.0
        await uit_client.login()
        response = await uit_client.request_with_retry("GET", absolute_url)
    raise_for_status_with_body(response)

    content_type = response.headers.get("content-type")
    filename = image_cache_filename(image_url, content_type)
    path = cached_image_path(filename)
    if not path.exists():
        IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
    return cached_image_url(filename)


async def cache_task_images(task: dict[str, Any]) -> tuple[dict[str, Any], int]:
    images = task.get("images")
    if not isinstance(images, list):
        return task, 0

    cached_count = 0
    next_images: list[object] = []
    for image in images:
        if not isinstance(image, dict):
            next_images.append(image)
            continue
        image_url = image.get("imageUrl")
        if not isinstance(image_url, str) or not image_url:
            next_images.append(image)
            continue
        local_url = await cache_image(image_url)
        if local_url != image_url:
            cached_count += 1
        next_images.append({**image, "imageUrl": local_url})

    return {**task, "images": next_images}, cached_count
