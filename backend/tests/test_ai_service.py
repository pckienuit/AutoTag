import json

import httpx
import pytest

from backend.app.ai_service import completion_content


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
