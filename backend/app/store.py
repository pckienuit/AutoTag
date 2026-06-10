import json
import sqlite3
from typing import Any

from .settings import DB_FILE, STATE_DIR


def ensure_state() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                session_id TEXT,
                payload TEXT NOT NULL,
                annotation TEXT,
                status TEXT NOT NULL DEFAULT 'local',
                needs_review INTEGER NOT NULL DEFAULT 1,
                reviewed INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS review_tasks (
                task_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                annotation TEXT,
                caption TEXT,
                status TEXT NOT NULL,
                issues TEXT NOT NULL DEFAULT '[]',
                error TEXT,
                submit_result TEXT,
                reviewed INTEGER NOT NULL DEFAULT 0,
                submissions_url TEXT NOT NULL,
                work_url TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        _ensure_column(conn, "tasks", "reviewed", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "review_tasks", "reviewed", "INTEGER NOT NULL DEFAULT 0")


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def upsert_task(
    task_id: str,
    session_id: str | None,
    payload: dict[str, Any],
    annotation: dict[str, Any] | None = None,
    status: str = "local",
    needs_review: bool = True,
    reviewed: bool = False,
) -> None:
    ensure_state()
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            """
            INSERT INTO tasks(task_id, session_id, payload, annotation, status, needs_review, reviewed)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                session_id = excluded.session_id,
                payload = excluded.payload,
                annotation = COALESCE(excluded.annotation, tasks.annotation),
                status = excluded.status,
                needs_review = excluded.needs_review,
                reviewed = excluded.reviewed,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                task_id,
                session_id,
                json.dumps(payload),
                json.dumps(annotation) if annotation is not None else None,
                status,
                1 if needs_review else 0,
                1 if reviewed else 0,
            ),
        )


def upsert_review_task(
    task_id: str,
    session_id: str,
    payload: dict[str, Any],
    status: str,
    annotation: dict[str, Any] | None = None,
    caption: str = "",
    issues: list[str] | None = None,
    error: str | None = None,
    submit_result: dict[str, Any] | None = None,
    reviewed: bool = False,
) -> None:
    ensure_state()
    submissions_url = f"https://aiclub.uit.edu.vn/label_cpr/annotator/sessions/{session_id}/submissions"
    work_url = f"https://aiclub.uit.edu.vn/label_cpr/annotator/sessions/{session_id}/work?taskId={task_id}"
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            """
            INSERT INTO review_tasks(
                task_id, session_id, payload, annotation, caption, status, issues,
                error, submit_result, reviewed, submissions_url, work_url
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                session_id = excluded.session_id,
                payload = excluded.payload,
                annotation = COALESCE(excluded.annotation, review_tasks.annotation),
                caption = excluded.caption,
                status = excluded.status,
                issues = excluded.issues,
                error = excluded.error,
                submit_result = excluded.submit_result,
                reviewed = excluded.reviewed,
                submissions_url = excluded.submissions_url,
                work_url = excluded.work_url,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                task_id,
                session_id,
                json.dumps(payload),
                json.dumps(annotation) if annotation is not None else None,
                caption,
                status,
                json.dumps(issues or []),
                error,
                json.dumps(submit_result) if submit_result is not None else None,
                1 if reviewed else 0,
                submissions_url,
                work_url,
            ),
        )


def list_tasks() -> list[dict[str, Any]]:
    ensure_state()
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY datetime(updated_at) DESC LIMIT 200"
        ).fetchall()
    return [
        {
            "taskId": row["task_id"],
            "sessionId": row["session_id"],
            "task": json.loads(row["payload"]),
            "annotation": json.loads(row["annotation"]) if row["annotation"] else None,
            "status": row["status"],
            "needsReview": bool(row["needs_review"]),
            "reviewed": bool(row["reviewed"]),
            "updatedAt": row["updated_at"],
        }
        for row in rows
    ]


def list_review_tasks(limit: int | None = None) -> list[dict[str, Any]]:
    ensure_state()
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        if limit is None:
            rows = conn.execute(
                "SELECT * FROM review_tasks ORDER BY datetime(updated_at) DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM review_tasks ORDER BY datetime(updated_at) DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [
        {
            "taskId": row["task_id"],
            "sessionId": row["session_id"],
            "task": json.loads(row["payload"]),
            "annotation": json.loads(row["annotation"]) if row["annotation"] else None,
            "caption": row["caption"],
            "status": row["status"],
            "reviewed": bool(row["reviewed"]),
            "issues": json.loads(row["issues"]),
            "error": row["error"],
            "submitResult": json.loads(row["submit_result"]) if row["submit_result"] else None,
            "submissionsUrl": row["submissions_url"],
            "workUrl": row["work_url"],
            "updatedAt": row["updated_at"],
        }
        for row in rows
    ]
