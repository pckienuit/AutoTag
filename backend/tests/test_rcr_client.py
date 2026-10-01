import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from backend.app import rcr_client as rcr_module
from backend.app import workflow
from backend.app.models import RcrAnnotation, SubjectAssignment
from backend.app.rcr_client import RcrClient, RcrConflictError, RcrError
from backend.app.store import get_task
from backend.tests.fixtures import make_task


def make_client(handler) -> RcrClient:
    client = RcrClient()
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://rcr.test")
    return client


@pytest.fixture(autouse=True)
def rcr_credentials(monkeypatch):
    monkeypatch.setattr(
        rcr_module,
        "get_settings",
        lambda: SimpleNamespace(rcr_username="user", rcr_password="pw", rcr_base_url="http://rcr.test"),
    )


def login_response(csrf: str = "tok") -> httpx.Response:
    return httpx.Response(200, json={"user": {"id": 1}, "csrf": csrf})


def test_login_then_mutation_sends_csrf_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/auth/login":
            assert json.loads(request.content) == {"username": "user", "password": "pw"}
            return login_response("csrf-1")
        return httpx.Response(200, json={"task": {"revision": 2}})

    client = make_client(handler)
    asyncio.run(client.save_draft("sample", 1, {"case_type": "INDIVIDUAL"}))

    draft = seen[-1]
    assert draft.url.path == "/api/work/tasks/sample/draft"
    assert draft.headers["X-CSRF-Token"] == "csrf-1"
    assert json.loads(draft.content) == {"expected_revision": 1, "annotation": {"case_type": "INDIVIDUAL"}}


def test_get_does_not_need_csrf_and_relogs_in_after_401() -> None:
    calls: list[str] = []
    state = {"expired": True}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/auth/login":
            state["expired"] = False
            return login_response()
        if state["expired"]:
            return httpx.Response(401, json={"error": "login required"})
        return httpx.Response(200, json={"tasks": [{"sample_id": "a"}]})

    client = make_client(handler)
    client.csrf = "stale"

    assert asyncio.run(client.list_tasks()) == [{"sample_id": "a"}]
    assert calls == ["/api/work/tasks", "/api/auth/login", "/api/work/tasks"]


def test_errors_carry_server_message_and_conflict_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return login_response()
        if request.url.path.endswith("/draft"):
            return httpx.Response(409, json={"error": "task changed in another tab"})
        return httpx.Response(422, json={"error": "DUAL requires 2 subject(s)"})

    client = make_client(handler)

    with pytest.raises(RcrConflictError, match="task changed"):
        asyncio.run(client.save_draft("s", 1, {}))
    with pytest.raises(RcrError, match="DUAL requires 2") as excinfo:
        asyncio.run(client.submit("s", 1, {}))
    assert excinfo.value.status == 422


def test_login_requires_credentials(monkeypatch) -> None:
    monkeypatch.setattr(
        rcr_module, "get_settings", lambda: SimpleNamespace(rcr_username="", rcr_password="", rcr_base_url="x")
    )

    with pytest.raises(ValueError, match="RCR username/password"):
        asyncio.run(RcrClient().login())


class FakeRcr:
    def __init__(self, revisions: list[int], status: str = "ASSIGNED") -> None:
        self.revisions = revisions
        self.status = status
        self.drafts: list[int] = []
        self.submitted: list[int] = []

    async def get_task(self, sample_id: str) -> dict:
        task = make_task(sample_id=sample_id, revision=self.revisions[0])
        task["status"] = self.status
        return {"task": task, "annotation": None}

    async def save_draft(self, sample_id: str, revision: int, annotation: dict) -> dict:
        self.drafts.append(revision)
        if revision != self.revisions[-1]:
            raise RcrConflictError(409, "task changed in another tab")
        return {"task": {"revision": revision + 1, "status": "IN_PROGRESS"}}

    async def submit(self, sample_id: str, revision: int, annotation: dict) -> dict:
        self.submitted.append(revision)
        return {"task": {"revision": revision + 1, "status": "SUBMITTED"}}


def annotation() -> RcrAnnotation:
    return RcrAnnotation(
        case_type="INDIVIDUAL",
        subjects=[SubjectAssignment(subject_id=1, identity_ids=["10"])],
        select_texts=["the man in a red shirt"],
        target_condition="Subject 1 is sitting",
    )


def test_push_annotation_refreshes_revision_on_conflict(monkeypatch) -> None:
    fake = FakeRcr(revisions=[1])
    # The caller still holds revision 0; the retry must pick up the server's revision 1.
    monkeypatch.setattr(workflow, "rcr_client", fake)

    result = asyncio.run(workflow.push_annotation("s1", annotation(), submit=False, revision=0))

    assert fake.drafts == [0, 1]
    assert result["status"] == "draft_saved"
    assert get_task("s1")["status"] == "draft_saved"


def test_push_annotation_submit_uses_revision_returned_by_draft(monkeypatch) -> None:
    fake = FakeRcr(revisions=[3])
    monkeypatch.setattr(workflow, "rcr_client", fake)

    result = asyncio.run(workflow.push_annotation("s1", annotation(), submit=True))

    assert fake.drafts == [3] and fake.submitted == [4]
    assert result["status"] == "submitted"
    assert get_task("s1")["reviewed"] is True


def test_push_annotation_blocks_invalid_and_submitted(monkeypatch) -> None:
    fake = FakeRcr(revisions=[1])
    monkeypatch.setattr(workflow, "rcr_client", fake)
    bad = annotation().replace(**{"target_condition": "the man is sitting"})

    blocked = asyncio.run(workflow.push_annotation("s1", bad, submit=False))
    fake.status = "SUBMITTED"
    done = asyncio.run(workflow.push_annotation("s1", annotation(), submit=False))

    assert blocked == {"status": "blocked", "issues": ["Target condition must mention Subject 1."]}
    assert done["status"] == "blocked" and "already submitted" in done["issues"][0]
    assert fake.drafts == []
