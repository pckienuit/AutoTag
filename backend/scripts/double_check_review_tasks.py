import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.ai_service import cleanup_stage2_annotation, review_annotation
from backend.app.caption import build_caption, validate_annotation
from backend.app.main import runtime_settings
from backend.app.models import Stage2Annotation
from backend.app.settings import DB_FILE, STATE_DIR
from backend.app.store import upsert_review_task, upsert_task
from backend.app.uit_client import uit_client


DEFAULT_LOG_FILE = STATE_DIR / "double_check_review_tasks.jsonl"


def unreviewed_items(limit: int | None = None) -> list[dict[str, Any]]:
    query = """
        SELECT *
        FROM review_tasks
        WHERE reviewed = 0 AND lower(status) != 'reviewed'
        ORDER BY datetime(updated_at) DESC
    """
    params: tuple[Any, ...] = ()
    if limit is not None:
        query += " LIMIT ?"
        params = (limit,)
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
    return [
        {
            "taskId": row["task_id"],
            "sessionId": row["session_id"],
            "task": json.loads(row["payload"]),
            "annotation": json.loads(row["annotation"]) if row["annotation"] else None,
            "caption": row["caption"],
            "status": row["status"],
            "issues": json.loads(row["issues"]),
            "error": row["error"],
            "submitResult": json.loads(row["submit_result"]) if row["submit_result"] else None,
        }
        for row in rows
    ]


def processed_task_ids(log_file: Path) -> set[str]:
    if not log_file.exists():
        return set()
    processed: set[str] = set()
    for line in log_file.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        task_id = item.get("taskId")
        if isinstance(task_id, str) and item.get("status") in {"checked", "skipped"}:
            processed.add(task_id)
    return processed


def comparable_annotation(annotation: Stage2Annotation) -> dict[str, Any]:
    data = annotation.model_dump()
    data["llmEdits"] = []
    return data


def append_log(log_file: Path, item: dict[str, Any]) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=True) + "\n")


async def process_item(
    item: dict[str, Any],
    *,
    apply: bool,
    settings,
) -> dict[str, Any]:
    if not isinstance(item.get("annotation"), dict):
        return {"taskId": item["taskId"], "status": "skipped", "reason": "missing annotation"}
    try:
        original = cleanup_stage2_annotation(Stage2Annotation.model_validate(item["annotation"]))
    except Exception as exc:
        return {"taskId": item["taskId"], "status": "skipped", "reason": f"invalid annotation: {exc}"}
    original.captionFinal = build_caption(original)

    checked = await review_annotation(item["task"], original, settings)
    checked.captionFinal = build_caption(checked)
    issues = validate_annotation(checked)
    changed = comparable_annotation(checked) != comparable_annotation(original)

    if apply and changed:
        upsert_task(
            item["taskId"],
            item["sessionId"],
            item["task"],
            checked.model_dump(),
            status=item["status"],
            needs_review=True,
            reviewed=False,
        )
        upsert_review_task(
            item["taskId"],
            item["sessionId"],
            item["task"],
            item["status"],
            checked.model_dump(),
            checked.captionFinal or "",
            issues,
            error=item["error"],
            submit_result=item["submitResult"],
            reviewed=False,
        )

    edit = checked.llmEdits[-1] if checked.llmEdits else {}
    return {
        "taskId": item["taskId"],
        "status": "checked",
        "changed": changed,
        "approved": edit.get("approved"),
        "reviewIssues": edit.get("issues", []),
        "validationIssues": issues,
        "applied": apply and changed,
    }


async def run_batch(args: argparse.Namespace) -> dict[str, Any]:
    settings = runtime_settings()
    if not settings.openai_compat_api_key:
        raise ValueError("OpenAI-compatible API key is missing.")

    log_file = args.log_file
    seen = processed_task_ids(log_file) if args.resume else set()
    items = [item for item in unreviewed_items(args.limit) if item["taskId"] not in seen]

    summary = {"total": len(items), "checked": 0, "changed": 0, "skipped": 0, "failed": 0}
    lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(max(1, args.concurrency))

    async def run_one(index: int, item: dict[str, Any]) -> None:
        async with semaphore:
            if args.delay_seconds > 0:
                await asyncio.sleep(args.delay_seconds * ((index - 1) % max(1, args.concurrency)))
            try:
                result = await process_item(item, apply=args.apply, settings=settings)
            except Exception as exc:
                result = {"taskId": item["taskId"], "status": "failed", "reason": repr(exc)[:500]}

        async with lock:
            append_log(log_file, result)

            if result["status"] == "checked":
                summary["checked"] += 1
                if result.get("changed"):
                    summary["changed"] += 1
            elif result["status"] == "skipped":
                summary["skipped"] += 1
            else:
                summary["failed"] += 1

            processed = summary["checked"] + summary["skipped"] + summary["failed"]
            if processed % args.progress_every == 0 or result["status"] != "checked":
                print({"processed": processed, **summary, "last": result}, flush=True)

    await asyncio.gather(*(run_one(index, item) for index, item in enumerate(items, 1)))

    await uit_client.close()
    return {**summary, "logFile": str(log_file), "apply": args.apply, "resumeSkipped": len(seen)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run AI double-check over unreviewed review tasks.")
    parser.add_argument("--apply", action="store_true", help="Write reviewer patches to local DB.")
    parser.add_argument("--no-resume", dest="resume", action="store_false", help="Ignore existing JSONL log.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of tasks to process.")
    parser.add_argument("--delay-seconds", type=float, default=1.0, help="Delay between AI review calls.")
    parser.add_argument("--concurrency", type=int, default=1, help="Number of concurrent AI review calls.")
    parser.add_argument("--progress-every", type=int, default=10, help="Print progress every N tasks.")
    parser.add_argument("--log-file", type=Path, default=DEFAULT_LOG_FILE, help="JSONL result log path.")
    args = parser.parse_args()
    result = asyncio.run(run_batch(args))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
