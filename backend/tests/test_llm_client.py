import asyncio
import json

import httpx
import pytest

from backend.app.llm_client import (
    completion_content,
    post_completion,
    request_completion,
)




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



def test_post_completion_retries_transient_status_with_backoff(monkeypatch) -> None:
    calls: list[str] = []
    sleeps: list[float] = []

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

        async def post(self, url: str, headers: dict, json: dict) -> httpx.Response:
            calls.append(url)
            request = httpx.Request("POST", url)
            if len(calls) == 1:
                return httpx.Response(
                    429,
                    headers={"retry-after": "0.25"},
                    request=request,
                )
            return httpx.Response(
                200,
                content=b'{"choices":[{"message":{"content":"ok"}}]}',
                request=request,
            )

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("backend.app.llm_client.httpx.AsyncClient", FakeClient)
    monkeypatch.setattr("backend.app.llm_client.asyncio.sleep", fake_sleep)

    response = asyncio.run(
        post_completion(
            "https://example.test/v1/chat/completions",
            "test-key",
            {"model": "review-model"},
            30.0,
        )
    )

    assert response.status_code == 200
    assert len(calls) == 2
    assert sleeps == [0.25]


def test_post_completion_retries_transport_error(monkeypatch) -> None:
    calls: list[str] = []
    sleeps: list[float] = []

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

        async def post(self, url: str, headers: dict, json: dict) -> httpx.Response:
            calls.append(url)
            request = httpx.Request("POST", url)
            if len(calls) == 1:
                raise httpx.ReadTimeout("slow network", request=request)
            return httpx.Response(
                200,
                content=b'{"choices":[{"message":{"content":"ok"}}]}',
                request=request,
            )

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("backend.app.llm_client.httpx.AsyncClient", FakeClient)
    monkeypatch.setattr("backend.app.llm_client.asyncio.sleep", fake_sleep)

    response = asyncio.run(
        post_completion(
            "https://example.test/v1/chat/completions",
            "test-key",
            {"model": "review-model"},
            30.0,
        )
    )

    assert response.status_code == 200
    assert len(calls) == 2
    assert sleeps == [2.0]


def test_request_completion_retries_empty_model_output(monkeypatch) -> None:
    calls: list[dict] = []
    sleeps: list[float] = []
    empty_response = {
        "response": {
            "usageMetadata": {
                "promptTokenCount": 1450,
                "totalTokenCount": 1450,
            }
        }
    }

    async def fake_post_completion(
        url: str,
        api_key: str,
        payload: dict,
        timeout: float,
    ) -> httpx.Response:
        calls.append(payload)
        if len(calls) < 3:
            return response_json(empty_response)
        return response_json({"choices": [{"message": {"content": "ok"}}]})

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("backend.app.llm_client.post_completion", fake_post_completion)
    monkeypatch.setattr("backend.app.llm_client.asyncio.sleep", fake_sleep)

    content = asyncio.run(
        request_completion(
            "https://example.test/v1/chat/completions",
            "test-key",
            {
                "model": "gemini-3-flash",
                "messages": [
                    {"role": "system", "content": "system rules"},
                    {"role": "user", "content": "task"},
                ],
                "max_tokens": 4096,
            },
            30.0,
        )
    )

    assert content == "ok"
    assert len(calls) == 3
    assert calls[1]["messages"][0]["role"] == "user"
    assert sleeps == [2.0]


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
