import json
import io

import httpx
import pytest
from PIL import Image

from backend.app.ai_service import completion_content
from backend.app.uit_client import UitClient


def response_json(data: dict) -> httpx.Response:
    return httpx.Response(200, content=json.dumps(data).encode("utf-8"))


def test_completion_content_reads_openai_choices() -> None:
    response = response_json({"choices": [{"message": {"content": "hello"}}]})

    assert completion_content(response) == "hello"


def test_completion_content_reads_singular_choice() -> None:
    response = response_json({"choice": {"message": {"content": "hello"}}})

    assert completion_content(response) == "hello"


def test_completion_content_reports_model_error() -> None:
    response = response_json({"error": {"message": "bad model"}})

    with pytest.raises(ValueError, match="bad model"):
        completion_content(response)


def test_completion_content_reports_missing_choices() -> None:
    response = response_json({"id": "abc"})

    with pytest.raises(ValueError, match="did not include choices"):
        completion_content(response)


def test_completion_content_reads_nested_gemini_response() -> None:
    response = response_json({
        "response": {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "hello"},
                            {"text": " world"},
                        ]
                    }
                }
            ]
        }
    })

    assert completion_content(response) == "hello world"


def test_completion_content_reports_nested_empty_response() -> None:
    response = response_json({
        "response": {
            "usageMetadata": {
                "promptTokenCount": 1450,
                "totalTokenCount": 1450,
            }
        }
    })

    with pytest.raises(ValueError, match="no text output"):
        completion_content(response)


def test_box_coords_supports_normalized_boxes() -> None:
    assert UitClient._box_coords(
        {"x": 0.25, "y": 0.1, "width": 0.5, "height": 0.4},
        200,
        100,
    ) == (50, 10, 150, 50)


def test_prepare_image_draws_boxes() -> None:
    source = Image.new("RGB", (100, 100), "white")
    buffer = io.BytesIO()
    source.save(buffer, format="PNG")

    output = UitClient._prepare_image(
        buffer.getvalue(),
        max_side=100,
        quality=95,
        boxes=[{"id": "A", "x": 0.1, "y": 0.1, "width": 0.4, "height": 0.4}],
    )

    with Image.open(io.BytesIO(output)) as image:
        assert image.convert("RGB").getpixel((10, 10)) != (255, 255, 255)
