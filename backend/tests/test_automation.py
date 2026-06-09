import asyncio

from backend.app import automation
from backend.app.automation import AutomationRunner


def test_delay_continues_until_timer_finishes(monkeypatch) -> None:
    monkeypatch.setattr(automation.random, "randint", lambda start, end: 1)
    runner = AutomationRunner(lambda: None)

    asyncio.run(runner._sleep_between_submissions())

    assert runner.state.next_delay_seconds is None
    assert runner.state.message == "Waiting 1 seconds before next task"
