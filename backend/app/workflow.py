from typing import Any

from .caption import build_instruction, validate_annotation
from .models import RcrAnnotation
from .rcr_client import RcrConflictError, rcr_client
from .store import upsert_task
from .text_cleanup import cleanup_select_text, cleanup_target_condition


def canonicalize(annotation: RcrAnnotation) -> RcrAnnotation:
    return annotation.replace(
        subjects=sorted(annotation.subjects, key=lambda item: item.subject_id),
        select_texts=[cleanup_select_text(text) for text in annotation.select_texts],
        target_condition=cleanup_target_condition(annotation.target_condition),
    )


async def push_annotation(
    sample_id: str,
    annotation: RcrAnnotation,
    *,
    submit: bool,
    revision: int | None = None,
) -> dict[str, Any]:
    """Save the draft on RCR (and optionally submit it), recording the result locally.

    A stale revision (409) is refreshed once from the server and retried.
    """
    detail = await rcr_client.get_task(sample_id)
    task = detail["task"]
    annotation = canonicalize(annotation)
    issues = validate_annotation(annotation, [str(i) for i in task.get("candidate_identity_ids") or []])
    if issues:
        return {"status": "blocked", "issues": issues}
    if task["status"] == "SUBMITTED":
        return {"status": "blocked", "issues": ["Task is already submitted. Reopen it before editing."]}
    payload = annotation.to_dict()
    revision = task["revision"] if revision is None else revision
    for attempt in (1, 2):
        try:
            saved = await rcr_client.save_draft(sample_id, revision, payload)
            task = {**task, **saved["task"]}
            if submit:
                saved = await rcr_client.submit(sample_id, task["revision"], payload)
                task = {**task, **saved["task"]}
            break
        except RcrConflictError:
            if attempt == 2:
                raise
            task = (await rcr_client.get_task(sample_id))["task"]
            revision = task["revision"]
    status = "submitted" if submit else "draft_saved"
    upsert_task(
        task,
        status,
        payload,
        build_instruction(annotation),
        issues=[],
        reviewed=submit,
    )
    return {"status": status, "task": task, "instruction": build_instruction(annotation)}
