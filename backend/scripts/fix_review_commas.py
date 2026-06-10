import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.caption import build_caption
from backend.app.models import Stage2Annotation
from backend.app.settings import DB_FILE
from backend.app.text_cleanup import cleanup_annotation_text, remove_comma_before_connectors


def cleanup_review_row(row: sqlite3.Row) -> dict[str, str] | None:
    payload = json.loads(row["payload"])
    annotation = json.loads(row["annotation"]) if row["annotation"] else None

    cleaned_payload = cleanup_annotation_text(payload)
    cleaned_annotation = cleanup_annotation_text(annotation) if annotation is not None else None
    cleaned_caption = remove_comma_before_connectors(row["caption"])

    if isinstance(cleaned_annotation, dict):
        try:
            parsed = Stage2Annotation.model_validate(cleaned_annotation)
        except Exception:
            parsed = None
        if parsed is not None:
            parsed.captionFinal = build_caption(parsed)
            cleaned_annotation = parsed.model_dump()
            cleaned_caption = parsed.captionFinal or cleaned_caption

    if isinstance(cleaned_payload, dict):
        if cleaned_caption:
            cleaned_payload["caption"] = cleaned_caption
        blocks = cleaned_payload.get("blocks")
        if isinstance(blocks, dict) and isinstance(cleaned_annotation, dict):
            cleaned_payload["blocks"] = {**blocks, **cleaned_annotation}

    next_values = {
        "payload": json.dumps(cleaned_payload),
        "annotation": json.dumps(cleaned_annotation) if cleaned_annotation is not None else None,
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
    backup = db_file.with_suffix(f".{timestamp}.bak")
    shutil.copy2(db_file, backup)
    return backup


def fix_review_commas(apply: bool, db_file: Path = DB_FILE) -> dict[str, Any]:
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT task_id, payload, annotation, caption
            FROM review_tasks
            WHERE reviewed = 0 AND lower(status) != 'reviewed'
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
        "backup": str(backup_path) if backup_path else None,
        "sampleTaskIds": [task_id for task_id, _values in changes[:10]],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove commas before connector words in unreviewed review tasks."
    )
    parser.add_argument("--apply", action="store_true", help="Write changes to the local SQLite DB.")
    parser.add_argument("--db", type=Path, default=DB_FILE, help="Path to autotag.sqlite3.")
    args = parser.parse_args()
    result = fix_review_commas(args.apply, args.db)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
