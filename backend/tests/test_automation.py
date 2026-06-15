import asyncio

import httpx

from backend.app import automation
from backend.app.automation import AutomationRunner
from backend.app.errors import error_detail


def test_delay_continues_until_timer_finishes(monkeypatch) -> None:
    monkeypatch.setattr(automation.random, "randint", lambda start, end: 1)
    runner = AutomationRunner(lambda: None)

    asyncio.run(runner._sleep_between_submissions())

    assert runner.state.next_delay_seconds is None
    assert runner.state.message == "Waiting 1 seconds before next task"


def test_error_detail_names_empty_httpx_timeout() -> None:
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    exc = httpx.ReadTimeout("", request=request)

    assert error_detail(exc) == (
        "ReadTimeout: request timed out while calling "
        "POST https://example.test/v1/chat/completions"
    )
