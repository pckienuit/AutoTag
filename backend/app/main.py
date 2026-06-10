from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from .ai_service import cleanup_stage2_annotation, fix_annotation_text, generate_annotation
from .automation import AutomationRunner
from .caption import build_caption, validate_annotation
from .image_cache import cache_task_images, cached_image_path
from .models import AutomationStartRequest, FixTextRequest, GenerateRequest, ReviewApproveRequest, ReviewImportRequest, RuntimeSettings, SyncRequest
from .settings import get_settings
from .store import list_review_tasks, list_tasks, upsert_review_task, upsert_task
from .uit_client import uit_client


def runtime_settings() -> RuntimeSettings:
    env = get_settings()
    return RuntimeSettings(
        openai_compat_base_url=env.openai_compat_base_url,
        openai_compat_api_key=env.openai_compat_api_key,
        openai_compat_model=env.openai_compat_model,
        auto_submit_enabled=env.auto_submit_enabled,
        ai_double_check_enabled=env.ai_double_check_enabled,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await uit_client.close()


app = FastAPI(title="AutoTag CPR Assistant", lifespan=lifespan)
automation_runner = AutomationRunner(runtime_settings)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


SAVE_REFRESH_RETRY_STATUSES = {400, 409}


def review_import_url(remote_url: str) -> str:
    url = remote_url.strip().rstrip("/")
    if not url:
        raise ValueError("Remote review queue URL is required.")
    if url.endswith("/api/review/tasks"):
        return url
    return f"{url}/api/review/tasks"


def remote_base_url(import_url: str) -> str:
    marker = "/api/review/tasks"
    if import_url.endswith(marker):
        return import_url[: -len(marker)].rstrip("/")
    return import_url.rstrip("/")


def task_has_images(task: object) -> bool:
    return isinstance(task, dict) and bool(task.get("images"))


def is_unreviewed_review_item(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    if item.get("reviewed") is True:
        return False
    return str(item.get("status") or "").lower() != "reviewed"


def importable_annotation(annotation: object) -> dict[str, object] | None:
    if not isinstance(annotation, dict):
        return None
    if not isinstance(annotation.get("subjects"), list):
        return None
    normalized = dict(annotation)
    if not isinstance(normalized.get("llmEdits"), list):
        normalized["llmEdits"] = []
    return normalized


def annotation_from_task(task: dict[str, object]) -> dict[str, object] | None:
    blocks = task.get("blocks")
    if not isinstance(blocks, dict):
        return None
    return importable_annotation(blocks)


async def hydrate_remote_review_item(
    client: httpx.AsyncClient,
    base_url: str,
    item: dict[str, object],
) -> dict[str, object]:
    task = item.get("task")
    session_id = item.get("sessionId")
    task_id = item.get("taskId") or (task.get("id") if isinstance(task, dict) else None)
    if task_has_images(task) or not session_id or not task_id:
        return item

    response = await client.get(
        f"{base_url}/api/uit/sessions/{session_id}/tasks/{task_id}"
    )
    response.raise_for_status()
    data = response.json()
    remote_task = data.get("task") if isinstance(data, dict) else None
    if not isinstance(remote_task, dict):
        return item

    hydrated = {**item, "task": remote_task}
    if importable_annotation(hydrated.get("annotation")) is None:
        task_annotation = annotation_from_task(remote_task)
        if task_annotation is not None:
            hydrated["annotation"] = task_annotation
    if not hydrated.get("caption"):
        hydrated["caption"] = str(remote_task.get("caption") or "")
    return hydrated


async def import_review_item(item: dict[str, object]) -> tuple[bool, int]:
    task = item.get("task")
    session_id = item.get("sessionId")
    task_id = item.get("taskId") or (task.get("id") if isinstance(task, dict) else None)
    if not isinstance(task, dict) or not task_id or not session_id:
        return False, 0

    task_id_text = str(task_id)
    session_id_text = str(session_id)
    task, cached = await cache_task_images(task)
    annotation = importable_annotation(item.get("annotation"))
    status = str(item.get("status") or "not_reviewed")
    issues = item.get("issues") if isinstance(item.get("issues"), list) else []
    submit_result = item.get("submitResult") if isinstance(item.get("submitResult"), dict) else None
    caption = str(item.get("caption") or "")
    error = str(item.get("error")) if item.get("error") is not None else None

    upsert_task(
        task_id_text,
        session_id_text,
        task,
        annotation,
        status=status,
        needs_review=True,
        reviewed=False,
    )
    upsert_review_task(
        task_id_text,
        session_id_text,
        task,
        status,
        annotation,
        caption,
        [str(issue) for issue in issues],
        error=error,
        submit_result=submit_result,
        reviewed=False,
    )
    return True, cached


async def import_remote_review_tasks(remote_url: str) -> dict[str, object]:
    url = review_import_url(remote_url)
    base_url = remote_base_url(url)
    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()
        tasks = data.get("tasks") if isinstance(data, dict) else None
        if not isinstance(tasks, list):
            raise ValueError("Remote response must be an object with a tasks list.")

        imported = 0
        skipped = 0
        hydrated = 0
        cached = 0
        for item in tasks:
            if not is_unreviewed_review_item(item):
                skipped += 1
                continue
            if isinstance(item, dict) and not task_has_images(item.get("task")):
                item = await hydrate_remote_review_item(client, base_url, item)
                if task_has_images(item.get("task")):
                    hydrated += 1
            imported_item = False
            if isinstance(item, dict):
                imported_item, cached_count = await import_review_item(item)
                cached += cached_count
            if imported_item:
                imported += 1
            else:
                skipped += 1
    return {
        "status": "imported",
        "source": url,
        "imported": imported,
        "skipped": skipped,
        "hydrated": hydrated,
        "cached": cached,
    }


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/settings")
async def get_runtime_settings() -> dict[str, object]:
    settings = runtime_settings()
    data = settings.model_dump()
    data["openai_compat_api_key"] = bool(settings.openai_compat_api_key)
    return data


@app.post("/api/uit/login")
async def uit_login() -> dict[str, object]:
    try:
        result = await uit_client.login()
        me = await uit_client.me()
        return {"login": result, "me": me}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/uit/me")
async def uit_me() -> dict[str, object]:
    try:
        return {"me": await uit_client.me()}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/uit/sessions")
async def uit_sessions() -> dict[str, object]:
    try:
        return await uit_client.sessions()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def session_has_open_tasks(session: dict[str, object]) -> bool:
    available = session.get("availableTaskCount")
    if isinstance(available, int) and available > 0:
        return True
    completed = session.get("completed")
    total = session.get("total")
    if isinstance(completed, int) and isinstance(total, int) and completed < total:
        return True
    return bool(session.get("draftTaskId"))


@app.get("/api/uit/auto-current-task")
async def uit_auto_current_task() -> dict[str, object]:
    try:
        data = await uit_client.sessions()
        sessions = data.get("sessions") or []
        candidates = [
            session for session in sessions
            if isinstance(session, dict) and session_has_open_tasks(session)
        ]
        for session in [*candidates, *[item for item in sessions if item not in candidates]]:
            if not isinstance(session, dict) or not session.get("id"):
                continue
            session_id = str(session["id"])
            task_data = await uit_client.current_task(session_id)
            task = task_data.get("task")
            if task:
                upsert_task(str(task["id"]), session_id, task, status="fetched")
                return {"session": session, "sessionId": session_id, "task": task}
        return {"session": None, "sessionId": None, "task": None}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/uit/sessions/{session_id}/current-task")
async def uit_current_task(session_id: str) -> dict[str, object]:
    try:
        data = await uit_client.current_task(session_id)
        task = data.get("task")
        if task:
            upsert_task(str(task["id"]), session_id, task, status="fetched")
        return data
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/uit/sessions/{session_id}/submissions")
async def uit_submissions(session_id: str, sent: bool = False) -> dict[str, object]:
    try:
        return await uit_client.submissions(session_id, sent=sent)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/uit/sessions/{session_id}/tasks/{task_id}")
async def uit_task(session_id: str, task_id: str) -> dict[str, object]:
    try:
        data = await uit_client.task(task_id, session_id)
        task = data.get("task")
        if task:
            upsert_task(str(task["id"]), session_id, task, status="fetched")
        return data
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/automation/start")
async def automation_start(request: AutomationStartRequest) -> dict[str, object]:
    try:
        return automation_runner.start(request.mode, request.limit)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/automation/stop")
async def automation_stop() -> dict[str, object]:
    return automation_runner.stop()


@app.get("/api/automation/status")
async def automation_status() -> dict[str, object]:
    return automation_runner.status()


@app.get("/api/review/tasks")
async def review_tasks() -> dict[str, object]:
    return {"tasks": list_review_tasks()}


@app.post("/api/review/import-remote")
async def review_import_remote(request: ReviewImportRequest) -> dict[str, object]:
    env = get_settings()
    remote_url = request.remoteUrl or env.remote_review_queue_url
    try:
        return await import_remote_review_tasks(remote_url or "")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/review/submissions/{session_id}")
async def review_submissions(session_id: str) -> dict[str, object]:
    try:
        return await uit_client.submissions(session_id, sent=True)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/review/approve")
async def approve_review_task(request: ReviewApproveRequest) -> dict[str, object]:
    annotation = cleanup_stage2_annotation(request.annotation)
    annotation.captionFinal = build_caption(annotation)
    task_id = str(request.task["id"])
    upsert_task(
        task_id,
        request.sessionId,
        request.task,
        annotation.model_dump(),
        status="reviewed",
        needs_review=False,
        reviewed=True,
    )
    upsert_review_task(
        task_id,
        request.sessionId,
        request.task,
        "reviewed",
        annotation.model_dump(),
        annotation.captionFinal or "",
        request.issues,
        reviewed=True,
    )
    return {
        "status": "reviewed_local",
        "caption": annotation.captionFinal,
        "issues": request.issues,
    }


@app.post("/api/ai/generate")
async def ai_generate(request: GenerateRequest) -> dict[str, object]:
    try:
        annotation = await generate_annotation(request.task, runtime_settings(), request.notes)
        issues = validate_annotation(annotation)
        upsert_task(
            str(request.task["id"]),
            None,
            request.task,
            annotation.model_dump(),
            status="generated",
            needs_review=True,
        )
        return {
            "annotation": annotation.model_dump(),
            "caption": build_caption(annotation),
            "issues": issues,
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/ai/fix-text")
async def ai_fix_text(request: FixTextRequest) -> dict[str, object]:
    try:
        return {"text": await fix_annotation_text(request.text, request.field, runtime_settings())}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/uit/save")
async def save_annotation(request: SyncRequest) -> dict[str, object]:
    annotation = cleanup_stage2_annotation(request.annotation)
    annotation.captionFinal = build_caption(annotation)
    issues = validate_annotation(annotation)
    reviewed = request.reviewed
    task = request.task
    result: dict[str, object] = {}
    warnings: list[str] = []
    try:
        try:
            result = await uit_client.save(
                task,
                annotation.model_dump(),
                request.timeSpent,
                request.sessionId,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in SAVE_REFRESH_RETRY_STATUSES or not request.sessionId:
                raise
            latest = await uit_client.task(str(request.task["id"]), request.sessionId)
            task = {**task, **(latest.get("task") or {})}
            try:
                result = await uit_client.save(
                    task,
                    annotation.model_dump(),
                    request.timeSpent,
                    request.sessionId,
                )
            except httpx.HTTPStatusError as retry_exc:
                if retry_exc.response.status_code != 409 or not reviewed:
                    raise
                warnings.append("UIT rejected save because the task is already submitted or locked; marked reviewed locally.")
        task = {**task, **(result.get("task") or {})}
        upsert_task(
            str(request.task["id"]),
            request.sessionId,
            task,
            annotation.model_dump(),
            status="reviewed" if reviewed else "saved",
            needs_review=not reviewed,
            reviewed=reviewed,
        )
        if reviewed and request.sessionId:
            upsert_review_task(
                str(request.task["id"]),
                request.sessionId,
                task,
                "reviewed",
                annotation.model_dump(),
                annotation.captionFinal or "",
                issues,
                submit_result=result,
                reviewed=True,
            )
        return {
            "status": "saved" if result else "reviewed_local",
            "result": result,
            "task": task,
            "caption": annotation.captionFinal,
            "issues": issues,
            "warnings": warnings,
        }
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/uit/submit")
async def submit_annotation(request: SyncRequest) -> dict[str, object]:
    annotation = cleanup_stage2_annotation(request.annotation)
    annotation.captionFinal = build_caption(annotation)
    issues = validate_annotation(annotation)
    if issues:
        return {"status": "blocked", "issues": issues}
    try:
        result = await uit_client.submit(
            request.task,
            annotation.model_dump(),
            request.timeSpent,
            request.sessionId,
        )
        upsert_task(
            str(request.task["id"]),
            request.sessionId,
            result.get("task") or request.task,
            annotation.model_dump(),
            status="submitted",
            needs_review=True,
            reviewed=False,
        )
        if request.sessionId:
            upsert_review_task(
                str(request.task["id"]),
                request.sessionId,
                result.get("task") or request.task,
                "not_reviewed",
                annotation.model_dump(),
                annotation.captionFinal or "",
                issues,
                submit_result=result,
                reviewed=False,
            )
        return {"status": "submitted", "result": result}
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/local/tasks")
async def local_tasks() -> dict[str, object]:
    return {"tasks": list_tasks()}


@app.get("/api/local/images/{filename}")
async def local_image(filename: str) -> FileResponse:
    path = cached_image_path(filename)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Cached image not found.")
    return FileResponse(path)
