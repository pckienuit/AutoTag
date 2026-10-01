import json
from dataclasses import dataclass, field
from typing import Any

from .caption import build_instruction, expected_case_type, is_two_subject
from .images import identity_subjects, load_image_bytes, render_for_model
from .llm_client import chat_payload, extract_json, is_no_text_output_error, request_completion
from .models import RcrAnnotation, RuntimeSettings, SubjectAssignment
from .text_cleanup import cleanup_select_text, cleanup_target_condition


SYSTEM_PROMPT = """
You are annotating RCR (Referential Composition Retrieval) image-pair tasks.
Return only a valid JSON object. Do not add markdown, commentary, or extra keys.

You get a QUERY image and a TARGET image. Colored boxes mark the people involved:
- boxes labeled S1 (orange) are Subject 1, boxes labeled S2 (blue) are Subject 2, boxes labeled "other" are not part of the task.
- The same person (identity) is boxed in both images, so S1 in the query image is the same S1 in the target image.
The subject assignment and the case type are already decided. Never change them.

You write these fields:
- select_texts: one text per subject. It identifies the subject in the QUERY image, so someone reading it can find the right person or group among other people.
- target_condition: ONE sentence that says what is true about the subject(s) in the TARGET image.

The final instruction is built like this, so your text must read naturally inside it:
  Identify Subject 1 as <select_texts[0]> [and Subject 2 as <select_texts[1]>]; then retrieve target images where <target_condition>.

Case rules:
- INDIVIDUAL: one person. select_texts has 1 text. target_condition describes Subject 1, for example "Subject 1 is holding a diploma".
- GROUP: Subject 1 is a group of several people. select_texts has 1 text that identifies the whole group, for example "the group consisting of the man in black and the woman in white". target_condition uses plural grammar and names Subject 1, for example "the members of Subject 1 are standing together on the stage".
- DUAL: two subjects with independent changes. select_texts has 2 texts. target_condition states the change of both in one sentence, for example "Subject 1 is holding a diploma and Subject 2 is clapping".
- RELATIONAL: two subjects with a directed relation. select_texts has 2 texts. target_condition states who does what to whom, in the right order, for example "Subject 1 is presenting a diploma to Subject 2". The direction must match the assignment of Subject 1 and Subject 2.

Hard rules:
- select_texts never contain the words "Subject", "Subject 1" or "Subject 2". They are plain descriptions such as "the man in a dark suit standing on the left".
- target_condition MUST contain the literal text "Subject 1" (and "Subject 2" for DUAL and RELATIONAL). It must not start with "then retrieve target images where".
- Use only visible evidence. Do not guess emotions, jobs, relationships, names, or intent. If a detail is not clearly visible, leave it out.
- All text must be English. Translate any user notes written in another language into simple English.
- Use simple English, below B1 level. Plain, common words. Short natural phrases, not long sentences.
- select_texts: describe the subject in the QUERY image. Prefer in this order: clothing, accessories, hair, pose and position, nearby objects, background. Use at least 3 distinguishing details when the image allows it. Avoid vague words like "the person" when a specific description is possible.
- target_condition: describe the visible action, pose, clothing, or position of the subject(s) in the TARGET image. Name the concrete place or object when it helps, for example "standing in front of a memorial wall".
- Do not stack fragments without connectors, such as "is wearing blue jeans not wearing a hat". Use "and" or "with". Avoid commas before "and" or "or".
- Do not leave unfinished fragments such as "holding a", "with a", or "standing near".
- For left/right of the body or looking direction, use the subject's own left/right. If unsure, use neutral words such as "one hand".
- Vary the wording a little when the images allow it, so different tasks do not all sound the same.

If a box does not match the person it should mark (for example the box in one image clearly shows a different person), still write your best text and put a short reason in "subject_problem". Otherwise "subject_problem" must be null.

Return exactly:
{"select_texts": ["..."], "target_condition": "...", "subject_problem": null}
""".strip()


REVIEW_PROMPT = """
You are a strict reviewer for RCR annotations. You get the same images and rules, plus a proposed annotation.
Check it against the images. Do not rewrite everything.

Check these points:
1. Each select_text matches the subject boxed S1/S2 in the QUERY image and is specific enough to find that person or group.
2. target_condition is true for the TARGET image: the right action, pose, clothing, place, and for RELATIONAL the right direction (Subject 1 does it to Subject 2).
3. No hidden facts (emotions, jobs, relationships, names), no unfinished fragments, no wrong grammar after "Subject 1" or "Subject 2".
4. select_texts do not contain the word "Subject"; target_condition mentions Subject 1 (and Subject 2 for DUAL and RELATIONAL).
5. Simple English, no stacked fragments, no needless commas.

Return only this JSON:
{"approved": true, "issues": ["short concrete issue"], "patch": {"select_texts": ["..."], "target_condition": "..."}}

Use an empty patch {} when the annotation is already good. Patch only fields that are clearly wrong; when you patch select_texts, return all of them. Prefer leaving a field unchanged over guessing.
""".strip()


@dataclass
class Generation:
    annotation: RcrAnnotation
    concerns: list[str] = field(default_factory=list)


def initial_annotation(task: dict[str, Any]) -> RcrAnnotation:
    """Case type and subject assignment come from the task; the AI only writes text."""
    initial = [
        {"subject_id": int(item["subject_id"]), "identity_ids": [str(i) for i in item.get("identity_ids", [])]}
        for item in task.get("initial_subjects") or []
    ]
    candidates = [str(i) for i in task.get("candidate_identity_ids") or []]
    case_type = str(task["case_type"])
    two = case_type in {"DUAL", "RELATIONAL"}
    if not initial:
        if two:
            raise ValueError(f"{case_type} task has no initial subject assignment.")
        initial = [{"subject_id": 1, "identity_ids": candidates}]
    subjects = sorted(initial, key=lambda item: item["subject_id"])[: 2 if two else 1]
    case_type = expected_case_type(subjects, case_type)
    return RcrAnnotation(
        case_type=case_type,
        subjects=[SubjectAssignment(**item) for item in subjects],
        select_texts=["" for _ in subjects],
        target_condition="",
    )


def boxes_for(task: dict[str, Any], side: str) -> list[dict[str, Any]]:
    return list(task[side].get("boxes") or [])


def subject_summary(annotation: RcrAnnotation) -> list[dict[str, Any]]:
    return [item.model_dump() for item in sorted(annotation.subjects, key=lambda item: item.subject_id)]


async def append_task_images(
    content: list[dict[str, Any]],
    task: dict[str, Any],
    annotation: RcrAnnotation,
    *,
    blur_heads: bool = False,
) -> None:
    assignment = identity_subjects(subject_summary(annotation))
    for side in ("query", "target"):
        original = await load_image_bytes(str(task["sample_id"]), side)
        boxes = boxes_for(task, side)
        title = side.upper()
        if not blur_heads:
            content.append({"type": "text", "text": f"{title} image (no boxes)"})
            content.append({"type": "image_url", "image_url": {"url": render_for_model(original)}})
        suffix = "faces blurred, " if blur_heads else ""
        content.append({"type": "text", "text": f"{title} image ({suffix}boxes labeled S1 / S2 / other)"})
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": render_for_model(original, boxes, assignment, blur_heads=blur_heads)},
            }
        )


def task_brief(task: dict[str, Any], annotation: RcrAnnotation, notes: str) -> str:
    return json.dumps(
        {
            "case_type": annotation.case_type,
            "subjects": subject_summary(annotation),
            "select_texts_needed": 2 if is_two_subject(annotation.case_type) else 1,
            "query_boxes": boxes_for(task, "query"),
            "target_boxes": boxes_for(task, "target"),
            "user_notes": notes,
        },
        ensure_ascii=True,
    )


def completion_url(settings: RuntimeSettings) -> str:
    return settings.openai_compat_base_url.rstrip("/") + "/chat/completions"


def apply_generated_text(annotation: RcrAnnotation, data: dict[str, Any]) -> tuple[RcrAnnotation, list[str]]:
    expected = len(annotation.subjects)
    raw_texts = data.get("select_texts")
    if isinstance(raw_texts, str):
        raw_texts = [raw_texts]
    texts = [cleanup_select_text(item) for item in (raw_texts or [])][:expected]
    texts += [""] * (expected - len(texts))
    result = annotation.model_copy(
        update={
            "select_texts": texts,
            "target_condition": cleanup_target_condition(data.get("target_condition")),
        }
    )
    concerns: list[str] = []
    problem = data.get("subject_problem")
    if isinstance(problem, str) and problem.strip():
        concerns.append(f"Model flagged the subject boxes: {problem.strip()}")
    return result, concerns


async def generate_annotation(
    task: dict[str, Any], settings: RuntimeSettings, notes: str = ""
) -> Generation:
    if not settings.openai_compat_api_key:
        raise ValueError("OpenAI-compatible API key is missing.")
    base = initial_annotation(task)
    url = completion_url(settings)

    async def ask(blur_heads: bool) -> str:
        content: list[dict[str, Any]] = [
            {"type": "text", "text": task_brief(task, base, notes)},
        ]
        await append_task_images(content, task, base, blur_heads=blur_heads)
        payload = chat_payload(
            settings.openai_compat_model,
            temperature=0.2,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            json_object=True,
        )
        return await request_completion(url, settings.openai_compat_api_key, payload, 120.0)

    try:
        raw = await ask(blur_heads=False)
    except ValueError as exc:
        if not is_no_text_output_error(exc):
            raise
        raw = await ask(blur_heads=True)
    annotation, concerns = apply_generated_text(base, extract_json(raw))
    if settings.ai_double_check_enabled:
        try:
            annotation, review_issues = await review_annotation(task, annotation, settings, notes)
            concerns.extend(review_issues)
        except ValueError as exc:
            if not is_no_text_output_error(exc):
                raise
            concerns.append("Double-check skipped because the model returned no text output.")
    return Generation(annotation=annotation, concerns=concerns)


async def review_annotation(
    task: dict[str, Any],
    annotation: RcrAnnotation,
    settings: RuntimeSettings,
    notes: str = "",
) -> tuple[RcrAnnotation, list[str]]:
    brief = json.loads(task_brief(task, annotation, notes))
    brief["proposed_annotation"] = {
        "select_texts": annotation.select_texts,
        "target_condition": annotation.target_condition,
    }
    brief["proposed_instruction"] = build_instruction(annotation)
    content: list[dict[str, Any]] = [{"type": "text", "text": json.dumps(brief, ensure_ascii=True)}]
    await append_task_images(content, task, annotation)
    payload = chat_payload(
        settings.openai_compat_review_model,
        temperature=0.0,
        messages=[
            {"role": "system", "content": f"{SYSTEM_PROMPT}\n\n{REVIEW_PROMPT}"},
            {"role": "user", "content": content},
        ],
        json_object=True,
    )
    raw = await request_completion(completion_url(settings), settings.openai_compat_api_key, payload, 120.0)
    return apply_review_patch(annotation, extract_json(raw))


def apply_review_patch(annotation: RcrAnnotation, review: dict[str, Any]) -> tuple[RcrAnnotation, list[str]]:
    patch = review.get("patch")
    update: dict[str, Any] = {}
    if isinstance(patch, dict):
        texts = patch.get("select_texts")
        if isinstance(texts, list) and len(texts) == len(annotation.select_texts):
            cleaned = [cleanup_select_text(item) for item in texts]
            if all(cleaned):
                update["select_texts"] = cleaned
        condition = cleanup_target_condition(patch.get("target_condition"))
        if condition:
            update["target_condition"] = condition
    # Issues the patch already fixed are not blocking; only unresolved rejections are.
    issues: list[str] = []
    if review.get("approved") is False and not update:
        issues = [str(item) for item in review.get("issues") or [] if str(item).strip()]
        issues = issues or ["Reviewer did not approve but gave no details."]
    return annotation.model_copy(update=update), issues
