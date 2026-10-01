import json
import sqlite3
from typing import Any

from .settings import DB_FILE, STATE_DIR


def ensure_state() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rcr_tasks (
                sample_id TEXT PRIMARY KEY,
                case_type TEXT NOT NULL,
                split TEXT,
                status TEXT NOT NULL,
                revision INTEGER,
                payload TEXT NOT NULL,
                annotation TEXT,
                instruction TEXT NOT NULL DEFAULT '',
                issues TEXT NOT NULL DEFAULT '[]',
                error TEXT,
                reviewed INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def upsert_task(
    task: dict[str, Any],
    status: str,
    annotation: dict[str, Any] | None = None,
    instruction: str = "",
    issues: list[str] | None = None,
    error: str | None = None,
    reviewed: bool = False,
) -> None:
    ensure_state()
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            """
            INSERT INTO rcr_tasks(
                sample_id, case_type, split, status, revision, payload,
                annotation, instruction, issues, error, reviewed
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sample_id) DO UPDATE SET
                case_type = excluded.case_type,
                split = excluded.split,
                status = excluded.status,
                revision = excluded.revision,
                payload = excluded.payload,
                annotation = COALESCE(excluded.annotation, rcr_tasks.annotation),
                instruction = excluded.instruction,
                issues = excluded.issues,
                error = excluded.error,
                reviewed = excluded.reviewed,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                task["sample_id"],
                task["case_type"],
                task.get("split"),
                status,
                task.get("revision"),
                json.dumps(task),
                json.dumps(annotation) if annotation is not None else None,
                instruction,
                json.dumps(issues or []),
                error,
                1 if reviewed else 0,
            ),
        )


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "sampleId": row["sample_id"],
        "caseType": row["case_type"],
        "split": row["split"],
        "status": row["status"],
        "revision": row["revision"],
        "task": json.loads(row["payload"]),
        "annotation": json.loads(row["annotation"]) if row["annotation"] else None,
        "instruction": row["instruction"],
        "issues": json.loads(row["issues"]),
        "error": row["error"],
        "reviewed": bool(row["reviewed"]),
        "updatedAt": row["updated_at"],
    }


def get_task(sample_id: str) -> dict[str, Any] | None:
    ensure_state()
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM rcr_tasks WHERE sample_id = ?", (sample_id,)).fetchone()
    return row_to_dict(row) if row else None


def local_statuses() -> dict[str, str]:
    ensure_state()
    with sqlite3.connect(DB_FILE) as conn:
        return dict(conn.execute("SELECT sample_id, status FROM rcr_tasks").fetchall())


def list_tasks(
    *,
    status: str | None = None,
    case_type: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    ensure_state()
    filters: list[str] = []
    params: list[Any] = []
    if status and status != "all":
        filters.append("status = ?")
        params.append(status)
    if case_type:
        filters.append("case_type = ?")
        params.append(case_type)
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT * FROM rcr_tasks {where} ORDER BY datetime(updated_at) DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
    return [row_to_dict(row) for row in rows]
