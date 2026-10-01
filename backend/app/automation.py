import asyncio
import math
import random
import time
from dataclasses import asdict, dataclass
from typing import Any

from .ai_service import generate_annotation
from .caption import build_instruction, validate_annotation
from .errors import error_detail
from .models import AutomationMode, CaseType, RuntimeSettings
from .rcr_client import RcrError, rcr_client
from .store import local_statuses, upsert_task
from .workflow import canonicalize, push_annotation


DELAY_MIN_SECONDS = 3
DELAY_MAX_SECONDS = 8
MAX_CONSECUTIVE_FAILURES = 3
SKIP_LOCAL_STATUSES = {"generated", "draft_saved", "needs_review", "reviewed", "submitted"}


@dataclass
class AutomationState:
    running: bool = False
    stop_requested: bool = False
    mode: AutomationMode | None = None
    limit: int | None = None
    case_type: str | None = None
    total: int = 0
    processed: int = 0
    drafted: int = 0
    submitted: int = 0
    failed: int = 0
    needs_review: int = 0
    current_task_id: str | None = None
    message: str = "Idle"
    next_delay_seconds: int | None = None
    next_delay_until: float | None = None


class AutomationRunner:
    def __init__(self, settings_factory) -> None:
        self.settings_factory = settings_factory
        self.state = AutomationState()
        self.task: asyncio.Task[None] | None = None

    def status(self) -> dict[str, Any]:
        data = asdict(self.state)
        data["next_delay_seconds"] = self._next_delay_seconds()
        return data

    def _next_delay_seconds(self) -> int | None:
        if self.state.next_delay_until is None:
            return None
        return max(0, math.ceil(self.state.next_delay_until - time.time()))

    def start(
        self,
        mode: AutomationMode,
        limit: int | None = None,
        case_type: CaseType | None = None,
        sample_id: str | None = None,
    ) -> dict[str, Any]:
        if self.task and not self.task.done():
            raise ValueError("Automation is already running.")
        if mode == "fixed_limit" and (limit is None or limit < 1):
            raise ValueError("fixed_limit mode requires a positive limit.")
        if mode == "one_case" and not case_type:
            raise ValueError("one_case mode requires a case_type.")
        if mode == "single_task" and not sample_id:
            raise ValueError("single_task mode requires a sample_id.")
        self.state = AutomationState(
            running=True, mode=mode, limit=limit, case_type=case_type, message="Starting automation"
        )
        self.task = asyncio.create_task(self._run(mode, limit, case_type, sample_id))
        return self.status()

    def stop(self) -> dict[str, Any]:
        self.state.stop_requested = True
        self.state.message = "Stop requested"
        self.state.next_delay_seconds = None
        self.state.next_delay_until = None
        if self.task and not self.task.done():
            self.task.cancel()
        return self.status()

    async def pending_samples(
        self,
        mode: AutomationMode,
        limit: int | None,
        case_type: str | None,
        sample_id: str | None,
    ) -> list[dict[str, Any]]:
        tasks = await rcr_client.list_tasks()
        if mode == "single_task":
            wanted = [item for item in tasks if item["sample_id"] == sample_id]
            if not wanted:
                raise ValueError(f"Task {sample_id} is not in your RCR queue.")
            return wanted
        done = local_statuses()
        pending = [
            item
            for item in tasks
            if item["status"] != "SUBMITTED"
            and done.get(item["sample_id"]) not in SKIP_LOCAL_STATUSES
            and (mode != "one_case" or item["case_type"] == case_type)
        ]
        return pending[:limit] if mode == "fixed_limit" and limit else pending

    async def _run(
        self,
        mode: AutomationMode,
        limit: int | None,
        case_type: str | None,
        sample_id: str | None,
    ) -> None:
        try:
            samples = await self.pending_samples(mode, limit, case_type, sample_id)
            self.state.total = len(samples)
            consecutive_failures = 0
            for index, current in enumerate(samples):
                if self.state.stop_requested:
                    break
                outcome = await self._process_task(current, self.settings_factory())
                consecutive_failures = consecutive_failures + 1 if outcome == "failed" else 0
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    self.state.message = f"Stopped after {consecutive_failures} consecutive failures: {self.state.message}"
                    break
                if index < len(samples) - 1 and not self.state.stop_requested:
                    await self._sleep_between_tasks()
            if self.state.stop_requested:
                self.state.message = "Automation stopped"
            elif not self.state.message.startswith("Stopped after"):
                self.state.message = (
                    "Automation completed"
                    if not self.state.needs_review
                    else f"Automation completed, {self.state.needs_review} task(s) need review"
                )
        except asyncio.CancelledError:
            self.state.message = "Automation stopped"
        except Exception as exc:
            self.state.failed += 1
            self.state.message = error_detail(exc)
        finally:
            self.state.running = False
            self.state.next_delay_seconds = None
            self.state.next_delay_until = None

    async def _process_task(self, queued: dict[str, Any], settings: RuntimeSettings) -> str:
        sample_id = queued["sample_id"]
        self.state.current_task_id = sample_id
        self.state.message = f"Processing {sample_id}"
        task = queued
        try:
            task = (await rcr_client.get_task(sample_id))["task"]
            if task["status"] == "SUBMITTED":
                return "skipped"
            generation = await generate_annotation(task, settings)
            annotation = canonicalize(generation.annotation)
            issues = [
                *validate_annotation(annotation, [str(i) for i in task.get("candidate_identity_ids") or []]),
                *generation.concerns,
            ]
            instruction = build_instruction(annotation)
            if issues:
                self.state.needs_review += 1
                upsert_task(task, "needs_review", annotation.to_dict(), instruction, issues)
                return "needs_review"
            submit = settings.auto_submit_enabled
            result = await push_annotation(sample_id, annotation, submit=submit)
            if result["status"] == "blocked":
                self.state.needs_review += 1
                upsert_task(task, "needs_review", annotation.to_dict(), instruction, result["issues"])
                return "needs_review"
            if submit:
                self.state.submitted += 1
            else:
                self.state.drafted += 1
            return result["status"]
        except RcrError as exc:
            if exc.status == 422:
                self.state.needs_review += 1
                upsert_task(task, "needs_review", issues=[f"RCR rejected the annotation: {exc.message}"])
                return "needs_review"
            return self._record_failure(task, exc)
        except Exception as exc:
            return self._record_failure(task, exc)
        finally:
            self.state.processed += 1

    def _record_failure(self, task: dict[str, Any], exc: BaseException) -> str:
        detail = error_detail(exc)
        self.state.failed += 1
        self.state.message = f"Failed {task['sample_id']}: {detail}"
        upsert_task(task, "failed", error=detail)
        return "failed"

    async def _sleep_between_tasks(self) -> None:
        delay = random.randint(DELAY_MIN_SECONDS, DELAY_MAX_SECONDS)
        self.state.next_delay_until = time.time() + delay
        self.state.message = f"Waiting {delay} seconds before next task"
        await asyncio.sleep(delay)
        self.state.next_delay_seconds = None
        self.state.next_delay_until = None
