import asyncio
import random
from dataclasses import asdict, dataclass
from typing import Any

from .ai_service import generate_annotation
from .caption import build_caption, validate_annotation
from .models import AutomationMode, RuntimeSettings
from .store import upsert_review_task, upsert_task
from .uit_client import uit_client


DELAY_MIN_SECONDS = 120
DELAY_MAX_SECONDS = 300


@dataclass
class AutomationState:
    running: bool = False
    stop_requested: bool = False
    mode: AutomationMode | None = None
    limit: int | None = None
    processed: int = 0
    submitted: int = 0
    failed: int = 0
    needs_review: int = 0
    current_session_id: str | None = None
    current_task_id: str | None = None
    message: str = "Idle"
    next_delay_seconds: int | None = None


class AutomationRunner:
    def __init__(self, settings_factory) -> None:
        self.settings_factory = settings_factory
        self.state = AutomationState()
        self.task: asyncio.Task[None] | None = None

    def status(self) -> dict[str, Any]:
        return asdict(self.state)

    def start(self, mode: AutomationMode, limit: int | None = None) -> dict[str, Any]:
        if self.task and not self.task.done():
            raise ValueError("Automation is already running.")
        if mode == "fixed_limit" and (limit is None or limit < 1):
            raise ValueError("fixed_limit mode requires a positive limit.")
        self.state = AutomationState(running=True, mode=mode, limit=limit, message="Starting automation")
        self.task = asyncio.create_task(self._run(mode, limit))
        return self.status()

    def stop(self) -> dict[str, Any]:
        self.state.stop_requested = True
        self.state.message = "Stop requested"
        if self.task and not self.task.done():
            self.task.cancel()
        return self.status()

    async def _run(self, mode: AutomationMode, limit: int | None) -> None:
        try:
            settings = self.settings_factory()
            task_limit = limit if mode == "fixed_limit" else 1 if mode == "current_task" else None
            sessions = await self._session_candidates(mode)
            for session in sessions:
                if self._should_stop(task_limit):
                    break
                session_id = str(session["id"])
                while not self._should_stop(task_limit):
                    task_data = await uit_client.current_task(session_id)
                    task = task_data.get("task")
                    if not task:
                        break
                    submitted = await self._process_task(session_id, task, settings)
                    if self._should_stop(task_limit):
                        break
                    if submitted:
                        await self._sleep_between_submissions()
                if mode in {"one_session", "current_task"}:
                    break
            self.state.message = "Automation stopped" if self.state.stop_requested else "Automation completed"
        except asyncio.CancelledError:
            self.state.message = "Automation stopped"
        except Exception as exc:
            self.state.failed += 1
            self.state.message = str(exc)
        finally:
            self.state.running = False
            self.state.next_delay_seconds = None

    async def _session_candidates(self, mode: AutomationMode) -> list[dict[str, Any]]:
        data = await uit_client.sessions()
        sessions = [session for session in data.get("sessions", []) if isinstance(session, dict) and session.get("id")]
        candidates = [session for session in sessions if self._session_has_open_tasks(session)]
        ordered = [*candidates, *[session for session in sessions if session not in candidates]]
        if mode in {"one_session", "current_task"}:
            return ordered[:1]
        return ordered

    def _session_has_open_tasks(self, session: dict[str, Any]) -> bool:
        available = session.get("availableTaskCount")
        if isinstance(available, int) and available > 0:
            return True
        completed = session.get("completed")
        total = session.get("total")
        if isinstance(completed, int) and isinstance(total, int) and completed < total:
            return True
        return bool(session.get("draftTaskId"))

    def _should_stop(self, task_limit: int | None) -> bool:
        return self.state.stop_requested or (task_limit is not None and self.state.processed >= task_limit)

    async def _process_task(self, session_id: str, task: dict[str, Any], settings: RuntimeSettings) -> bool:
        task_id = str(task["id"])
        self.state.current_session_id = session_id
        self.state.current_task_id = task_id
        self.state.message = f"Processing {task_id}"
        upsert_task(task_id, session_id, task, status="automation_fetched")
        try:
            annotation = await generate_annotation(task, settings)
            annotation.captionFinal = build_caption(annotation)
            issues = validate_annotation(annotation)
            if issues:
                self.state.needs_review += 1
                upsert_review_task(
                    task_id,
                    session_id,
                    task,
                    "needs_review",
                    annotation.model_dump(),
                    annotation.captionFinal or "",
                    issues,
                )
                return False
            result = await uit_client.submit(task, annotation.model_dump(), 0, session_id)
            self.state.submitted += 1
            upsert_task(
                task_id,
                session_id,
                result.get("task") or task,
                annotation.model_dump(),
                "not_reviewed",
                True,
                False,
            )
            upsert_review_task(
                task_id,
                session_id,
                task,
                "not_reviewed",
                annotation.model_dump(),
                annotation.captionFinal or "",
                [],
                submit_result=result,
                reviewed=False,
            )
            return True
        except Exception as exc:
            self.state.failed += 1
            upsert_review_task(task_id, session_id, task, "failed", issues=[], error=str(exc))
            return False
        finally:
            self.state.processed += 1

    async def _sleep_between_submissions(self) -> None:
        delay = random.randint(DELAY_MIN_SECONDS, DELAY_MAX_SECONDS)
        self.state.next_delay_seconds = delay
        self.state.message = f"Waiting {delay} seconds before next task"
        await asyncio.sleep(delay)
        self.state.next_delay_seconds = None
