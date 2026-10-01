import base64
import hashlib
import io
from pathlib import Path
from typing import Any
from urllib.parse import quote

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .rcr_client import rcr_client
from .settings import STATE_DIR


IMAGE_CACHE_DIR = STATE_DIR / "images"
SIDES = ("query", "target")
SUBJECT_COLORS = {1: "#f97316", 2: "#2563eb"}
OTHER_COLOR = "#6b7280"
MODEL_MAX_SIDE = 768
MODEL_JPEG_QUALITY = 70


def cache_path(sample_id: str, side: str) -> Path:
    digest = hashlib.sha256(f"{sample_id}/{side}".encode("utf-8")).hexdigest()
    return IMAGE_CACHE_DIR / f"{digest}.jpg"


def task_image_url(sample_id: str, side: str) -> str:
    return f"/api/image/{quote(sample_id, safe='')}/{side}"


async def load_image_bytes(sample_id: str, side: str) -> bytes:
    """Return the original image, downloading it from RCR on first use."""
    if side not in SIDES:
        raise ValueError(f"Unknown image side: {side}")
    path = cache_path(sample_id, side)
    if path.is_file():
        return path.read_bytes()
    content, _ = await rcr_client.image_bytes(task_image_url(sample_id, side))
    IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return content


def identity_subjects(subjects: list[dict[str, Any]]) -> dict[str, int]:
    return {
        str(identity_id): int(subject["subject_id"])
        for subject in subjects
        for identity_id in subject.get("identity_ids", [])
    }


def box_pixels(box: dict[str, Any], width: int, height: int) -> tuple[int, int, int, int] | None:
    try:
        x, y = float(box["x"]), float(box["y"])
        w, h = float(box["width"]), float(box["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    left = max(0, min(width - 1, round(x * width)))
    top = max(0, min(height - 1, round(y * height)))
    right = max(0, min(width - 1, round((x + w) * width)))
    bottom = max(0, min(height - 1, round((y + h) * height)))
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def render_for_model(
    content: bytes,
    boxes: list[dict[str, Any]] | None = None,
    assignment: dict[str, int] | None = None,
    *,
    blur_heads: bool = False,
    max_side: int = MODEL_MAX_SIDE,
) -> str:
    """Resize the image and draw boxes labelled S1/S2 by assigned subject; return a data URL."""
    assignment = assignment or {}
    with Image.open(io.BytesIO(content)) as source:
        image = source.convert("RGB")
    image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=max(14, image.width // 40))
    for box in boxes or []:
        coords = box_pixels(box, image.width, image.height)
        if not coords:
            continue
        left, top, right, bottom = coords
        if blur_heads:
            box_height = bottom - top
            blur_bottom = bottom if box_height <= image.height * 0.45 else top + max(1, round(box_height * 0.35))
            region = image.crop((left, top, right, blur_bottom)).filter(ImageFilter.GaussianBlur(radius=12))
            image.paste(region, (left, top))
        subject = assignment.get(str(box.get("identity_id")))
        color = SUBJECT_COLORS.get(subject, OTHER_COLOR) if subject else OTHER_COLOR
        label = f"S{subject}" if subject else "other"
        for offset in range(3):
            draw.rectangle((left - offset, top - offset, right + offset, bottom + offset), outline=color)
        text_box = draw.textbbox((0, 0), label, font=font)
        text_width, text_height = text_box[2] - text_box[0], text_box[3] - text_box[1]
        label_top = max(0, top - text_height - 8)
        draw.rectangle((left, label_top, left + text_width + 10, label_top + text_height + 8), fill=color)
        draw.text((left + 5, label_top + 2), label, fill="#ffffff", font=font)
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=MODEL_JPEG_QUALITY, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")
