import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from .ai_service import generate_annotation
from .automation import AutomationRunner
from .caption import build_instruction, validate_annotation
from .errors import error_detail
from .images import SIDES, load_image_bytes
from .models import (
    AutoSubmitRequest,
    AutomationStartRequest,
    GenerateRequest,
    RcrAnnotation,
    RuntimeSettings,
    SaveRequest,
)
from .rcr_client import RcrConflictError, RcrError, rcr_client
from .settings import RUNTIME_OVERRIDES, ROOT_DIR, get_settings
from .store import get_task as get_local_task
from .store import list_tasks as list_local_tasks
from .store import local_statuses, upsert_task
from .workflow import canonicalize, push_annotation


def runtime_settings() -> RuntimeSettings:
    env = get_settings()
    return RuntimeSettings(
        openai_compat_base_url=env.openai_compat_base_url,
        openai_compat_api_key=env.openai_compat_api_key,
        openai_compat_model=env.openai_compat_model,
        openai_compat_review_model=env.openai_compat_review_model,
        auto_submit_enabled=RUNTIME_OVERRIDES.get("auto_submit", env.auto_submit_enabled),
        ai_double_check_enabled=env.ai_double_check_enabled,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await rcr_client.close()


app = FastAPI(title="AutoTag RCR Assistant", lifespan=lifespan)
automation_runner = AutomationRunner(runtime_settings)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def http_error(exc: BaseException) -> HTTPException:
    if isinstance(exc, RcrConflictError):
        return HTTPException(status_code=409, detail=error_detail(exc))
    if isinstance(exc, RcrError) and exc.status == 422:
        return HTTPException(status_code=422, detail=exc.message)
    return HTTPException(status_code=502, detail=error_detail(exc))


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/settings")
async def get_runtime_settings() -> dict[str, object]:
    settings = runtime_settings()
    return {
        "rcrBaseUrl": get_settings().rcr_base_url,
        "model": settings.openai_compat_model,
        "reviewModel": settings.openai_compat_review_model,
        "autoSubmitEnabled": settings.auto_submit_enabled,
        "aiDoubleCheckEnabled": settings.ai_double_check_enabled,
        "apiKeyConfigured": bool(settings.openai_compat_api_key),
    }


@app.put("/api/settings/auto-submit")
async def set_auto_submit(request: AutoSubmitRequest) -> dict[str, object]:
    RUNTIME_OVERRIDES["auto_submit"] = request.enabled
    return {"autoSubmitEnabled": request.enabled}


@app.post("/api/rcr/login")
async def rcr_login() -> dict[str, object]:
    try:
        data = await rcr_client.login()
        return {"user": data.get("user")}
    except Exception as exc:
        raise http_error(exc) from exc


@app.get("/api/rcr/me")
async def rcr_me() -> dict[str, object]:
    try:
        data = await rcr_client.me()
        return {"user": data.get("user"), "llmEnabled": data.get("llm_enabled")}
    except Exception as exc:
        raise http_error(exc) from exc


@app.get("/api/rcr/tasks")
async def rcr_tasks() -> dict[str, object]:
    """The annotator's queue, each item tagged with this tool's local status."""
    try:
        tasks = await rcr_client.list_tasks()
    except Exception as exc:
        raise http_error(exc) from exc
    local = local_statuses()
    return {"tasks": [{**item, "localStatus": local.get(item["sample_id"])} for item in tasks]}


@app.get("/api/rcr/tasks/{sample_id}")
async def rcr_task(sample_id: str) -> dict[str, object]:
    try:
        detail = await rcr_client.get_task(sample_id)
    except Exception as exc:
        raise http_error(exc) from exc
    local = get_local_task(sample_id)
    if local and local["annotation"]:
        task = detail["task"]
        rule_issues = validate_annotation(
            RcrAnnotation(**local["annotation"]), [str(i) for i in task.get("candidate_identity_ids") or []]
        )
        # Stored issues mix rule violations with AI notes; keep only the notes the rules cannot reproduce.
        local["concerns"] = [item for item in local["issues"] if item not in rule_issues]
    return {**detail, "local": local}


@app.get("/api/rcr/images/{sample_id}/{side}")
async def rcr_image(sample_id: str, side: str) -> Response:
    if side not in SIDES:
        raise HTTPException(status_code=404, detail="Unknown image side.")
    try:
        content = await load_image_bytes(sample_id, side)
    except Exception as exc:
        raise http_error(exc) from exc
    return Response(content=content, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


@app.post("/api/ai/generate")
async def ai_generate(request: GenerateRequest) -> dict[str, object]:
    try:
        task = (await rcr_client.get_task(request.sample_id))["task"]
        generation = await generate_annotation(task, runtime_settings(), request.notes)
    except Exception as exc:
        raise http_error(exc) from exc
    annotation = canonicalize(generation.annotation)
    candidates = [str(i) for i in task.get("candidate_identity_ids") or []]
    rule_issues = validate_annotation(annotation, candidates)
    issues = [*rule_issues, *generation.concerns]
    instruction = build_instruction(annotation)
    upsert_task(
        task, "needs_review" if issues else "generated", annotation.model_dump(), instruction, issues
    )
    return {
        "annotation": annotation.model_dump(),
        "instruction": instruction,
        "issues": rule_issues,
        "concerns": generation.concerns,
    }


@app.post("/api/ai/validate")
async def ai_validate(request: SaveRequest) -> dict[str, object]:
    annotation = canonicalize(request.annotation)
    return {"instruction": build_instruction(annotation), "issues": validate_annotation(annotation)}


@app.post("/api/rcr/draft")
async def rcr_draft(request: SaveRequest) -> dict[str, object]:
    return await save_or_submit(request, submit=False)


@app.post("/api/rcr/submit")
async def rcr_submit(request: SaveRequest) -> dict[str, object]:
    return await save_or_submit(request, submit=True)


async def save_or_submit(request: SaveRequest, *, submit: bool) -> dict[str, object]:
    try:
        return await push_annotation(request.sample_id, request.annotation, submit=submit)
    except Exception as exc:
        raise http_error(exc) from exc


@app.post("/api/rcr/tasks/{sample_id}/reopen")
async def rcr_reopen(sample_id: str) -> dict[str, object]:
    try:
        task = (await rcr_client.get_task(sample_id))["task"]
        result = await rcr_client.reopen(sample_id, task["revision"])
    except Exception as exc:
        raise http_error(exc) from exc
    reopened = {**task, **result.get("task", {})}
    local = get_local_task(sample_id)
    upsert_task(
        reopened,
        "draft_saved",
        result.get("annotation"),
        local["instruction"] if local else "",
    )
    return result


@app.post("/api/automation/start")
async def automation_start(request: AutomationStartRequest) -> dict[str, object]:
    try:
        return automation_runner.start(request.mode, request.limit, request.case_type, request.sample_id)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=error_detail(exc)) from exc


@app.post("/api/automation/stop")
async def automation_stop() -> dict[str, object]:
    return automation_runner.stop()


@app.get("/api/automation/status")
async def automation_status() -> dict[str, object]:
    return automation_runner.status()


@app.get("/api/local/tasks")
async def local_tasks(
    status: str = "all",
    case_type: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, object]:
    return {"tasks": list_local_tasks(status=status, case_type=case_type, limit=limit)}


def mount_frontend(directory: Path) -> bool:
    """Serve the built frontend from the same process; must run after every API route is registered."""
    if not (directory / "index.html").is_file():
        return False
    app.mount("/", StaticFiles(directory=directory, html=True), name="frontend")
    return True


mount_frontend(Path(os.environ.get("AUTOTAG_STATIC_DIR") or ROOT_DIR / "frontend" / "dist"))
