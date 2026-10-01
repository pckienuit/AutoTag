import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Awaitable, Callable

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.staticfiles import StaticFiles

from .ai_service import generate_annotation
from .automation import AutomationRunner
from .caption import build_instruction, validate_annotation
from .errors import error_detail
from .images import SIDES, load_image_bytes
from .models import (
    AutomationStartRequest,
    RcrAnnotation,
    RuntimeSettings,
    require_bool,
    require_sample_id,
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
        openai_compat_review_effort=env.openai_compat_review_effort,
        auto_submit_enabled=RUNTIME_OVERRIDES.get("auto_submit", env.auto_submit_enabled),
        ai_double_check_enabled=env.ai_double_check_enabled,
    )


automation_runner = AutomationRunner(runtime_settings)


def http_error(exc: BaseException) -> HTTPException:
    if isinstance(exc, RcrConflictError):
        return HTTPException(status_code=409, detail=error_detail(exc))
    if isinstance(exc, RcrError) and exc.status == 422:
        return HTTPException(status_code=422, detail=exc.message)
    return HTTPException(status_code=502, detail=error_detail(exc))


async def read_json(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(status_code=422, detail="Request body must be valid JSON.") from None
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="Request body must be a JSON object.")
    return data


def parse(build: Callable[[], Any]) -> Any:
    """Run a request parser and turn its ValueError into a 422 response."""
    try:
        return build()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


async def health(request: Request) -> Response:
    return JSONResponse({"status": "ok"})


async def get_runtime_settings(request: Request) -> Response:
    settings = runtime_settings()
    return JSONResponse(
        {
            "rcrBaseUrl": get_settings().rcr_base_url,
            "model": settings.openai_compat_model,
            "reviewModel": settings.openai_compat_review_model,
            "reviewEffort": settings.openai_compat_review_effort,
            "autoSubmitEnabled": settings.auto_submit_enabled,
            "aiDoubleCheckEnabled": settings.ai_double_check_enabled,
            "apiKeyConfigured": bool(settings.openai_compat_api_key),
        }
    )


async def set_auto_submit(request: Request) -> Response:
    data = await read_json(request)
    enabled = parse(lambda: require_bool(data, "enabled"))
    RUNTIME_OVERRIDES["auto_submit"] = enabled
    return JSONResponse({"autoSubmitEnabled": enabled})


async def rcr_login(request: Request) -> Response:
    try:
        data = await rcr_client.login()
    except Exception as exc:
        raise http_error(exc) from exc
    return JSONResponse({"user": data.get("user")})


async def rcr_me(request: Request) -> Response:
    try:
        data = await rcr_client.me()
    except Exception as exc:
        raise http_error(exc) from exc
    return JSONResponse({"user": data.get("user"), "llmEnabled": data.get("llm_enabled")})


async def rcr_tasks(request: Request) -> Response:
    """The annotator's queue, each item tagged with this tool's local status."""
    try:
        tasks = await rcr_client.list_tasks()
    except Exception as exc:
        raise http_error(exc) from exc
    local = local_statuses()
    return JSONResponse({"tasks": [{**item, "localStatus": local.get(item["sample_id"])} for item in tasks]})


async def rcr_task(request: Request) -> Response:
    sample_id = request.path_params["sample_id"]
    try:
        detail = await rcr_client.get_task(sample_id)
    except Exception as exc:
        raise http_error(exc) from exc
    local = get_local_task(sample_id)
    if local and local["annotation"]:
        task = detail["task"]
        rule_issues = validate_annotation(
            RcrAnnotation.from_dict(local["annotation"]), [str(i) for i in task.get("candidate_identity_ids") or []]
        )
        # Stored issues mix rule violations with AI notes; keep only the notes the rules cannot reproduce.
        local["concerns"] = [item for item in local["issues"] if item not in rule_issues]
    return JSONResponse({**detail, "local": local})


async def rcr_image(request: Request) -> Response:
    sample_id, side = request.path_params["sample_id"], request.path_params["side"]
    if side not in SIDES:
        raise HTTPException(status_code=404, detail="Unknown image side.")
    try:
        content = await load_image_bytes(sample_id, side)
    except Exception as exc:
        raise http_error(exc) from exc
    return Response(content=content, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


async def ai_generate(request: Request) -> Response:
    data = await read_json(request)
    sample_id = parse(lambda: require_sample_id(data))
    notes = str(data.get("notes") or "")
    try:
        task = (await rcr_client.get_task(sample_id))["task"]
        generation = await generate_annotation(task, runtime_settings(), notes)
    except Exception as exc:
        raise http_error(exc) from exc
    annotation = canonicalize(generation.annotation)
    candidates = [str(i) for i in task.get("candidate_identity_ids") or []]
    rule_issues = validate_annotation(annotation, candidates)
    issues = [*rule_issues, *generation.concerns]
    instruction = build_instruction(annotation)
    upsert_task(
        task, "needs_review" if issues else "generated", annotation.to_dict(), instruction, issues
    )
    return JSONResponse(
        {
            "annotation": annotation.to_dict(),
            "instruction": instruction,
            "issues": rule_issues,
            "concerns": generation.concerns,
        }
    )


async def ai_validate(request: Request) -> Response:
    data = await read_json(request)
    annotation = canonicalize(parse(lambda: RcrAnnotation.from_dict(data.get("annotation"))))
    return JSONResponse({"instruction": build_instruction(annotation), "issues": validate_annotation(annotation)})


async def save_or_submit(request: Request, *, submit: bool) -> Response:
    data = await read_json(request)
    sample_id = parse(lambda: require_sample_id(data))
    annotation = parse(lambda: RcrAnnotation.from_dict(data.get("annotation")))
    try:
        return JSONResponse(await push_annotation(sample_id, annotation, submit=submit))
    except Exception as exc:
        raise http_error(exc) from exc


async def rcr_draft(request: Request) -> Response:
    return await save_or_submit(request, submit=False)


async def rcr_submit(request: Request) -> Response:
    return await save_or_submit(request, submit=True)


async def rcr_reopen(request: Request) -> Response:
    sample_id = request.path_params["sample_id"]
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
    return JSONResponse(result)


async def automation_start(request: Request) -> Response:
    data = await read_json(request)
    options = parse(lambda: AutomationStartRequest.from_dict(data))
    try:
        status = automation_runner.start(options.mode, options.limit, options.case_type, options.sample_id)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=error_detail(exc)) from exc
    return JSONResponse(status)


async def automation_stop(request: Request) -> Response:
    return JSONResponse(automation_runner.stop())


async def automation_status(request: Request) -> Response:
    return JSONResponse(automation_runner.status())


async def local_tasks(request: Request) -> Response:
    params = request.query_params
    try:
        limit = int(params.get("limit", "200"))
    except ValueError:
        limit = 0
    if not 1 <= limit <= 1000:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 1000.")
    tasks = list_local_tasks(status=params.get("status", "all"), case_type=params.get("case_type"), limit=limit)
    return JSONResponse({"tasks": tasks})


async def http_exception_handler(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, HTTPException)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)


@asynccontextmanager
async def lifespan(app: Starlette):
    yield
    await rcr_client.close()


app = Starlette(
    routes=[
        Route("/api/health", health),
        Route("/api/settings", get_runtime_settings),
        Route("/api/settings/auto-submit", set_auto_submit, methods=["PUT"]),
        Route("/api/rcr/login", rcr_login, methods=["POST"]),
        Route("/api/rcr/me", rcr_me),
        Route("/api/rcr/tasks", rcr_tasks),
        Route("/api/rcr/tasks/{sample_id}", rcr_task),
        Route("/api/rcr/tasks/{sample_id}/reopen", rcr_reopen, methods=["POST"]),
        Route("/api/rcr/images/{sample_id}/{side}", rcr_image),
        Route("/api/rcr/draft", rcr_draft, methods=["POST"]),
        Route("/api/rcr/submit", rcr_submit, methods=["POST"]),
        Route("/api/ai/generate", ai_generate, methods=["POST"]),
        Route("/api/ai/validate", ai_validate, methods=["POST"]),
        Route("/api/automation/start", automation_start, methods=["POST"]),
        Route("/api/automation/stop", automation_stop, methods=["POST"]),
        Route("/api/automation/status", automation_status),
        Route("/api/local/tasks", local_tasks),
    ],
    middleware=[
        Middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    ],
    exception_handlers={HTTPException: http_exception_handler},
    lifespan=lifespan,
)


def mount_frontend(directory: Path) -> bool:
    """Serve the built frontend from the same process; must run after every API route is registered."""
    if not (directory / "index.html").is_file():
        return False
    app.mount("/", StaticFiles(directory=directory, html=True), name="frontend")
    return True


mount_frontend(Path(os.environ.get("AUTOTAG_STATIC_DIR") or ROOT_DIR / "frontend" / "dist"))
