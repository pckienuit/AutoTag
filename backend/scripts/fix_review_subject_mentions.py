import argparse
import json
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.ai_service import cleanup_stage2_annotation
from backend.app.caption import build_caption
from backend.app.models import Stage2Annotation
from backend.app.settings import DB_FILE


SUBJECT_WORD_RE = re.compile(r"\bsubject\b", re.IGNORECASE)


def annotation_has_subject_mentions(annotation: dict[str, Any] | None) -> bool:
    if not isinstance(annotation, dict):
        return False
    subjects = annotation.get("subjects")
    if not isinstance(subjects, list):
        return False
    for subject in subjects:
        if not isinstance(subject, dict):
            continue
        for field in ("descQueryRaw", "descQueryFinal", "changeTargetRaw", "changeTargetFinal"):
            if SUBJECT_WORD_RE.search(str(subject.get(field) or "")):
                return True
    return False


def cleanup_review_row(row: sqlite3.Row) -> dict[str, str] | None:
    payload = json.loads(row["payload"])
    annotation = json.loads(row["annotation"]) if row["annotation"] else None
    if not annotation_has_subject_mentions(annotation):
        return None

    try:
        parsed = Stage2Annotation.model_validate(annotation)
    except Exception:
        return None

    cleaned = cleanup_stage2_annotation(parsed)
    cleaned.captionFinal = build_caption(cleaned)
    cleaned_annotation = cleaned.model_dump()
    cleaned_caption = cleaned.captionFinal or row["caption"]

    if isinstance(payload, dict):
        payload["caption"] = cleaned_caption
        blocks = payload.get("blocks")
        if isinstance(blocks, dict):
            payload["blocks"] = {**blocks, **cleaned_annotation}

    next_values = {
        "payload": json.dumps(payload),
        "annotation": json.dumps(cleaned_annotation),
        "caption": cleaned_caption,
    }
    if (
        next_values["payload"] == row["payload"]
        and next_values["annotation"] == row["annotation"]
        and next_values["caption"] == row["caption"]
    ):
        return None
    return next_values


def backup_db(db_file: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = db_file.with_suffix(f".{timestamp}.subject-mentions.bak")
    shutil.copy2(db_file, backup)
    return backup


def fix_subject_mentions(apply: bool, include_reviewed: bool, db_file: Path = DB_FILE) -> dict[str, Any]:
    where = "" if include_reviewed else "WHERE reviewed = 0 AND lower(status) != 'reviewed'"
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT task_id, payload, annotation, caption
            FROM review_tasks
            {where}
            """
        ).fetchall()

        changes: list[tuple[str, dict[str, str]]] = []
        for row in rows:
            next_values = cleanup_review_row(row)
            if next_values is not None:
                changes.append((row["task_id"], next_values))

        backup_path = None
        if apply and changes:
            backup_path = backup_db(db_file)
            for task_id, values in changes:
                conn.execute(
                    """
                    UPDATE review_tasks
                    SET payload = ?, annotation = ?, caption = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ?
                    """,
                    (values["payload"], values["annotation"], values["caption"], task_id),
                )
                conn.execute(
                    """
                    UPDATE tasks
                    SET payload = ?, annotation = COALESCE(?, annotation), updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ?
                    """,
                    (values["payload"], values["annotation"], task_id),
                )
            conn.commit()

    return {
        "scanned": len(rows),
        "changed": len(changes),
        "applied": apply,
        "includeReviewed": include_reviewed,
        "backup": str(backup_path) if backup_path else None,
        "sampleTaskIds": [task_id for task_id, _values in changes[:20]],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove literal Subject mentions from DESC/CHANGE fields in review tasks."
    )
    parser.add_argument("--apply", action="store_true", help="Write changes to the local SQLite DB.")
    parser.add_argument("--include-reviewed", action="store_true", help="Also scan reviewed tasks.")
    parser.add_argument("--db", type=Path, default=DB_FILE, help="Path to autotag.sqlite3.")
    args = parser.parse_args()
    result = fix_subject_mentions(args.apply, args.include_reviewed, args.db)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
