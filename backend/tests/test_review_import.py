import asyncio

from backend.app import main


def test_review_import_url_accepts_base_or_endpoint() -> None:
    assert main.review_import_url("http://vps:8000") == "http://vps:8000/api/review/tasks"
    assert main.review_import_url("http://vps:8000/api/review/tasks") == "http://vps:8000/api/review/tasks"


def test_is_unreviewed_review_item_filters_reviewed_tasks() -> None:
    assert main.is_unreviewed_review_item({"status": "not_reviewed", "reviewed": False})
    assert not main.is_unreviewed_review_item({"status": "reviewed", "reviewed": False})
    assert not main.is_unreviewed_review_item({"status": "not_reviewed", "reviewed": True})


def test_importable_annotation_rejects_malformed_subjects() -> None:
    assert main.importable_annotation({"subjects": ""}) is None
    assert main.importable_annotation({"subjects": [], "llmEdits": ""}) == {"subjects": [], "llmEdits": []}


def test_import_remote_review_tasks_upserts_unreviewed_items(monkeypatch) -> None:
    task = {"id": "task-1", "images": []}
    remote_data = {
        "tasks": [
            {
                "taskId": "task-1",
                "sessionId": "session-1",
                "task": task,
                "annotation": {"caseType": "SINGLE"},
                "caption": "caption",
                "status": "not_reviewed",
                "reviewed": False,
                "issues": ["needs check"],
            },
            {
                "taskId": "task-2",
                "sessionId": "session-1",
                "task": {"id": "task-2"},
                "status": "reviewed",
                "reviewed": True,
            },
        ]
    }
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return remote_data

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

        async def get(self, url: str) -> FakeResponse:
            calls.append(("get", (url,), {}))
            return FakeResponse()

    monkeypatch.setattr(main.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(main, "upsert_task", lambda *args, **kwargs: calls.append(("task", args, kwargs)))
    monkeypatch.setattr(main, "upsert_review_task", lambda *args, **kwargs: calls.append(("review", args, kwargs)))

    result = asyncio.run(main.import_remote_review_tasks("http://vps:8000"))

    assert result["imported"] == 1
    assert result["skipped"] == 1
    assert ("get", ("http://vps:8000/api/review/tasks",), {}) in calls
    assert len([call for call in calls if call[0] == "task"]) == 1
    assert len([call for call in calls if call[0] == "review"]) == 1
