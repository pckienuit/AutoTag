import asyncio

import httpx

from backend.app import automation
from backend.app.ai_service import Generation
from backend.app.automation import AutomationRunner
from backend.app.errors import error_detail
from backend.app.models import RcrAnnotation, RuntimeSettings, SubjectAssignment
from backend.app.store import get_task, upsert_task
from backend.tests.fixtures import make_task


def generated(texts=("the man in red",), condition="Subject 1 is sitting", concerns=()) -> Generation:
    return Generation(
        annotation=RcrAnnotation(
            case_type="INDIVIDUAL",
            subjects=[SubjectAssignment(subject_id=1, identity_ids=["10"])],
            select_texts=list(texts),
            target_condition=condition,
        ),
        concerns=list(concerns),
    )


class FakeRcr:
    def __init__(self, tasks: list[dict]) -> None:
        self.tasks = tasks

    async def list_tasks(self) -> list[dict]:
        return [{"sample_id": t["sample_id"], "case_type": t["case_type"], "status": t["status"]} for t in self.tasks]

    async def get_task(self, sample_id: str) -> dict:
        return {"task": next(t for t in self.tasks if t["sample_id"] == sample_id)}


def run_automation(monkeypatch, tasks, results, *, submit=False, mode="all_open", **kwargs):
    pushes: list[tuple[str, bool]] = []
    queue = iter(results)

    async def fake_generate(task, settings, notes=""):
        result = next(queue)
        if isinstance(result, Exception):
            raise result
        return result

    async def fake_push(sample_id, annotation, *, submit, revision=None):
        pushes.append((sample_id, submit))
        return {"status": "submitted" if submit else "draft_saved"}

    async def no_sleep(self) -> None:
        return None

    monkeypatch.setattr(automation, "rcr_client", FakeRcr(tasks))
    monkeypatch.setattr(automation, "generate_annotation", fake_generate)
    monkeypatch.setattr(automation, "push_annotation", fake_push)
    monkeypatch.setattr(AutomationRunner, "_sleep_between_tasks", no_sleep)
    runner = AutomationRunner(lambda: RuntimeSettings(auto_submit_enabled=submit))
    runner.state.mode = mode
    asyncio.run(runner._run(mode, kwargs.get("limit"), kwargs.get("case_type"), kwargs.get("sample_id")))
    return runner, pushes


def test_clean_task_is_saved_as_draft_by_default(monkeypatch) -> None:
    runner, pushes = run_automation(monkeypatch, [make_task(sample_id="a")], [generated()])

    assert pushes == [("a", False)]
    assert (runner.state.drafted, runner.state.submitted, runner.state.needs_review) == (1, 0, 0)
    assert runner.state.message == "Automation completed"


def test_auto_submit_setting_submits(monkeypatch) -> None:
    runner, pushes = run_automation(monkeypatch, [make_task(sample_id="a")], [generated()], submit=True)

    assert pushes == [("a", True)]
    assert runner.state.submitted == 1


def test_validation_issue_or_model_concern_goes_to_review_without_touching_rcr(monkeypatch) -> None:
    tasks = [make_task(sample_id="a"), make_task(sample_id="b")]
    runner, pushes = run_automation(
        monkeypatch,
        tasks,
        [generated(condition="the man is sitting"), generated(concerns=["Model flagged the subject boxes: wrong"])],
    )

    assert pushes == []
    assert runner.state.needs_review == 2
    assert get_task("a")["status"] == "needs_review"
    assert get_task("a")["issues"] == ["Target condition must mention Subject 1."]
    assert get_task("b")["issues"] == ["Model flagged the subject boxes: wrong"]
    assert "2 task(s) need review" in runner.state.message


def test_skips_submitted_and_already_handled_tasks(monkeypatch) -> None:
    submitted = make_task(sample_id="done")
    submitted["status"] = "SUBMITTED"
    handled = make_task(sample_id="handled")
    upsert_task(handled, "draft_saved")
    runner, pushes = run_automation(
        monkeypatch, [submitted, handled, make_task(sample_id="todo")], [generated()]
    )

    assert pushes == [("todo", False)]
    assert runner.state.total == 1


def test_case_filter_and_limit(monkeypatch) -> None:
    tasks = [make_task("DUAL", "d1"), make_task("INDIVIDUAL", "i1"), make_task("INDIVIDUAL", "i2")]
    runner, pushes = run_automation(
        monkeypatch, tasks, [generated(), generated()], mode="one_case", case_type="INDIVIDUAL"
    )
    assert pushes == [("i1", False), ("i2", False)]
    assert runner.state.total == 2

    runner, pushes = run_automation(monkeypatch, tasks, [generated()], mode="fixed_limit", limit=1)
    assert pushes == [("d1", False)]


def test_stops_after_consecutive_failures_and_keeps_case_type(monkeypatch) -> None:
    tasks = [make_task("DUAL", f"t{i}") for i in range(5)]
    errors = [RuntimeError("model down")] * 5
    runner, pushes = run_automation(monkeypatch, tasks, errors)

    assert runner.state.failed == 3 and runner.state.processed == 3
    assert runner.state.message.startswith("Stopped after 3 consecutive failures")
    assert get_task("t0")["status"] == "failed"
    assert get_task("t0")["caseType"] == "DUAL"
    assert pushes == []


def test_stop_cancels_current_run() -> None:
    async def scenario() -> AutomationRunner:
        runner = AutomationRunner(lambda: RuntimeSettings())

        async def forever(*args) -> None:
            await asyncio.sleep(60)

        runner._run = forever
        runner.start("all_open")
        await asyncio.sleep(0)
        runner.stop()
        await asyncio.sleep(0)
        return runner

    runner = asyncio.run(scenario())
    assert runner.state.stop_requested is True


def test_start_validates_mode_arguments() -> None:
    async def scenario() -> list[str]:
        runner = AutomationRunner(lambda: RuntimeSettings())
        errors = []
        for kwargs in ({"mode": "fixed_limit"}, {"mode": "one_case"}, {"mode": "single_task"}):
            try:
                runner.start(**kwargs)
            except ValueError as exc:
                errors.append(str(exc))
        return errors

    assert len(asyncio.run(scenario())) == 3


def test_error_detail_names_empty_httpx_timeout() -> None:
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    exc = httpx.ReadTimeout("", request=request)

    assert error_detail(exc) == (
        "ReadTimeout: request timed out while calling POST https://example.test/v1/chat/completions"
    )
