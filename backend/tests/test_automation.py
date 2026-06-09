import asyncio

from backend.app import automation
from backend.app.automation import AutomationRunner


def test_interrupt_delay_skips_current_sleep(monkeypatch) -> None:
    monkeypatch.setattr(automation.random, "randint", lambda start, end: 30)
    runner = AutomationRunner(lambda: None)

    async def run_delay() -> None:
        delay_task = asyncio.create_task(runner._sleep_between_submissions())
        while runner.state.next_delay_seconds is None:
            await asyncio.sleep(0)

        assert runner.interrupt_delay() is True
        await asyncio.wait_for(delay_task, timeout=0.5)

    asyncio.run(run_delay())

    assert runner.state.next_delay_seconds is None
    assert runner.state.message == "Delay skipped after review approval"
