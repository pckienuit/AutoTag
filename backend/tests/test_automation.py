import asyncio

import httpx

from backend.app import automation
from backend.app.automation import AutomationRunner
from backend.app.errors import error_detail


def test_delay_sleeps_once_and_reports_deadline(monkeypatch) -> None:
    sleeps: list[int] = []

    monkeypatch.setattr(automation.random, "randint", lambda start, end: 3)
    monkeypatch.setattr(automation.time, "time", lambda: 1000.0)

    async def fake_sleep(delay: int) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(automation.asyncio, "sleep", fake_sleep)
    runner = AutomationRunner(lambda: None)

    asyncio.run(runner._sleep_between_submissions())

    assert sleeps == [3]
    assert runner.state.next_delay_seconds is None
    assert runner.state.next_delay_until is None
    assert runner.state.message == "Waiting 3 seconds before next task"


def test_error_detail_names_empty_httpx_timeout() -> None:
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    exc = httpx.ReadTimeout("", request=request)

    assert error_detail(exc) == (
        "ReadTimeout: request timed out while calling "
        "POST https://example.test/v1/chat/completions"
    )


def test_run_preserves_task_failure_message(monkeypatch) -> None:
    runner = AutomationRunner(lambda: object())

    async def fake_session_candidates(mode: str) -> list[dict]:
        return [{"id": "session-1"}]

    class FakeUitClient:
        async def current_task(self, session_id: str) -> dict:
            return {"task": {"id": "task-1"}}

    async def fake_process_task(session_id: str, task: dict, settings: object) -> bool:
        runner.state.failed += 1
        runner.state.processed += 1
        runner.state.message = "Failed task-1: model returned no text"
        return False

    monkeypatch.setattr(runner, "_session_candidates", fake_session_candidates)
    monkeypatch.setattr(automation, "uit_client", FakeUitClient())
    monkeypatch.setattr(runner, "_process_task", fake_process_task)

    asyncio.run(runner._run("all_open", None))

    assert runner.state.running is False
    assert runner.state.message == "Failed task-1: model returned no text"
