import asyncio

import httpx

from backend.app import main
from backend.app.models import Stage2Annotation, SubjectAnnotation, SyncRequest
from backend.app.uit_client import UitClient, raise_for_status_with_body


def status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://aiclub.uit.edu.vn/save")
    response = httpx.Response(status_code, request=request, text="stale draft version")
    return httpx.HTTPStatusError("failed", request=request, response=response)


def valid_annotation() -> Stage2Annotation:
    return Stage2Annotation(
        caseType="SINGLE",
        subjects=[
            SubjectAnnotation(
                subjectId=1,
                queryGroupIds=["270"],
                descQueryFinal="the person in the black jacket",
                changeTargetFinal="is standing near the train",
            )
        ],
    )


def test_raise_for_status_includes_response_body() -> None:
    request = httpx.Request("POST", "https://aiclub.uit.edu.vn/save")
    response = httpx.Response(400, request=request, text='{"message":"invalid draft"}')

    try:
        raise_for_status_with_body(response)
    except httpx.HTTPStatusError as exc:
        assert "Response body:" in str(exc)
        assert "invalid draft" in str(exc)
    else:
        raise AssertionError("Expected HTTPStatusError")


def test_save_annotation_retries_400_with_latest_task(monkeypatch) -> None:
    initial_task = {"id": "task-1", "draftVersion": 0, "reservationVersion": 0, "claimToken": "old"}
    latest_task = {"id": "task-1", "draftVersion": 3, "reservationVersion": 2, "claimToken": "new"}
    calls: list[dict[str, object]] = []

    class FakeUitClient:
        async def save(self, task, annotation, time_spent, session_id):
            calls.append(task)
            if len(calls) == 1:
                raise status_error(400)
            return {"task": task, "ok": True}

        async def task(self, task_id, session_id):
            return {"task": latest_task}

    monkeypatch.setattr(main, "uit_client", FakeUitClient())
    monkeypatch.setattr(main, "upsert_task", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "upsert_review_task", lambda *args, **kwargs: None)

    result = asyncio.run(
        main.save_annotation(
            SyncRequest(
                task=initial_task,
                annotation=valid_annotation(),
                sessionId="session-1",
                reviewed=False,
            )
        )
    )

    assert result["status"] == "saved"
    assert calls == [initial_task, latest_task]


def test_uit_client_retries_connect_error(monkeypatch) -> None:
    calls: list[str] = []
    sleeps: list[float] = []

    class FakeAsyncClient:
        async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
            calls.append(url)
            request = httpx.Request(method, url)
            if len(calls) == 1:
                raise httpx.ConnectError("network down", request=request)
            return httpx.Response(200, request=request, json={"ok": True})

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    client = UitClient()
    client.client = FakeAsyncClient()
    monkeypatch.setattr("backend.app.uit_client.asyncio.sleep", fake_sleep)

    response = asyncio.run(client.request_with_retry("GET", "/label_cpr/api/me"))

    assert response.status_code == 200
    assert len(calls) == 2
    assert sleeps == [2.0]


def test_uit_client_retries_rate_limit_with_retry_after(monkeypatch) -> None:
    calls: list[str] = []
    sleeps: list[float] = []

    class FakeAsyncClient:
        async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
            calls.append(url)
            request = httpx.Request(method, url)
            if len(calls) == 1:
                return httpx.Response(429, request=request, headers={"retry-after": "3"})
            return httpx.Response(200, request=request, json={"ok": True})

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    client = UitClient()
    client.client = FakeAsyncClient()
    monkeypatch.setattr("backend.app.uit_client.asyncio.sleep", fake_sleep)

    response = asyncio.run(client.request_with_retry("GET", "/label_cpr/api/me"))

    assert response.status_code == 200
    assert len(calls) == 2
    assert sleeps == [3.0]
