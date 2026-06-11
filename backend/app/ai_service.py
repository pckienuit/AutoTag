import base64
import asyncio
import json
import mimetypes
from json import JSONDecodeError
from typing import Any
from urllib.parse import unquote

import httpx

from .caption import build_caption
from .image_cache import cached_image_path
from .models import RuntimeSettings, Stage2Annotation, SubjectAnnotation
from .text_cleanup import cleanup_desc_change_text, remove_comma_before_connectors
from .uit_client import uit_client


TRANSIENT_MODEL_STATUSES = {429, 502, 503, 504}
MODEL_RETRY_ATTEMPTS = 4
MODEL_RETRY_BASE_DELAY_SECONDS = 2.0


SYSTEM_PROMPT = """
You are annotating CPR image-pair tasks.
Return only a valid JSON object that matches the Stage 2 annotation schema. Do not add markdown, commentary, or extra keys.

Use only visible evidence from the images. Do not infer emotions, jobs, family relations, intent, or any hidden facts.
Use the query image to identify the subject(s). Use the target image to describe what those subject(s) should be like in the target.

Choose exactly one caseType based on the target image:
- SINGLE: one subject with one target description.
- MULTI: two different subjects, each with its own description and change.
- RELATIONAL: two different subjects, where the main signal is the ordered relation between them.

Follow these field rules:
- DESC fields identify who the subject is in the query image, specific enough to distinguish the correct person or group.
- CHANGE fields describe what that subject is doing or what visible attribute/state they have in the target image.
- PAIR_CHANGE describes the relation from Subject 1 to Subject 2, and the order must be correct.
- Never mention the literal label "Subject", "Subject 1", or "Subject 2" inside DESC or CHANGE field values. Write only the visual description fragment, such as "the woman in a yellow shirt" or "is sitting on a bench".
- For RELATIONAL, individual subject change fields are optional and should be filled only when they are clearly visible.
- Subject 1 and Subject 2 must be different. A subject may be a group if the query image shows them as a group.
- Select queryGroupIds from the query image only. Include targetGroupIds only when they clearly match the same subject(s) in the target image.
- Do not use IDs, technical terms, or overly generic labels like "the person" when the image allows a more specific description.
- Write concise, natural English fragments, not full sentences inside DESC/CHANGE/PAIR_CHANGE.
- If multiple people are best treated as one unit, use SINGLE.
- When Subject 1 is a group, write CHANGE with plural grammar and name the group naturally, such as "the two people are wearing white shirts and sitting in the stands".
- If a field is not supported by visible evidence, leave it empty rather than guessing.
- When choosing DESC details, prioritize visible uniqueness in this order: clothing -> accessories -> hair -> environment.
- If clothing evidence is sparse or absent, strengthen the description with accessories, then hair, then environment/background only as needed to disambiguate.
- Prefer the most distinctive visible attributes available, so the subject can still be uniquely identified even when earlier levels in the hierarchy are weak.
- Each DESC should include at least 3 visible distinguishing details whenever the image provides them.
- Do not stop at a short phrase like "the man wearing a gray shirt"; expand it with more visible details, such as "the man wearing a gray shirt with white text, standing near the monument".
- Count separate visible cues such as clothing color/type, text or pattern on clothing, accessories, hairstyle, pose, relative position, nearby object, and immediate background/environment.
- Use simple English vocabulary, ideally below B1 level. Prefer plain, common words over advanced or academic wording.
- All DESC, CHANGE, and PAIR_CHANGE text must be English. Translate any non-English user wording into simple English before using it.
- Keep phrasing natural and clear, but avoid rare adjectives or complex sentence structures when a simpler phrase says the same thing.
- Avoid overusing commas. Prefer natural connector words where possible.
- Use commas only when they make the grammar clearer, such as separating a list of action fragments: "is wearing blue jeans, not wearing a hat, and placing his hands behind his back".
- Do not stack fragments without connectors, such as "is wearing blue jeans not wearing a hat hands placed behind back".
- Separate adjacent clothing, accessory, action, and pose fragments with connectors or clear commas. Do not write fragments like "a red jacket grey helmet", "wearing glasses a white shirt", or "is wearing red pants a pink backpack".
- Do not leave dangling or unfinished fragments such as "holding a", "with a", or "standing near". If the object or context is unclear, omit that unfinished part.
- Avoid a comma before simple connector words when the sentence already reads clearly without it.
- For extra context about another visible person or object, use a connected phrase such as "with a man between them holding a yellow drink" instead of attaching a loose clause.
- When the subject is a group, use plural grammar in CHANGE and refer to the group naturally, such as "the two people are wearing white shirts".
- For left/right hands or looking direction, use the subject's own left/right, not the viewer's left/right. If uncertain, use neutral wording such as "one hand" or "looking to one side".
- If the target context helps distinguish the correct image, name the concrete visible object or place, such as "standing in front of a memorial wall", instead of using only a vague setting.
- If the key target signal is a spatial or interaction relation between two subjects, choose RELATIONAL instead of MULTI.
- Read the final caption pattern before responding. It must sound grammatical after "Subject 1" or "Subject 2" is inserted.
- Vary the wording a little when the images allow it, so similar tasks do not always produce the exact same phrasing.
- Prefer human-like, direct descriptions grounded in the image over rigid template-heavy wording, while still keeping the final caption valid and easy to read.

The final caption built from your fields should follow these patterns:
- SINGLE: In the query image, Subject 1 refers to [DESC]. Retrieve target images where Subject 1 [CHANGE].
- MULTI: In the query image, Subject 1 refers to [DESC 1] and Subject 2 refers to [DESC 2]. Retrieve target images where Subject 1 [CHANGE 1] and Subject 2 [CHANGE 2].
- RELATIONAL: In the query image, Subject 1 refers to [DESC 1] and Subject 2 refers to [DESC 2]. Retrieve target images where Subject 1 [PAIR_CHANGE] Subject 2.

Before responding, verify that the JSON is valid, the case is consistent with the target image, and the text is short, natural, and grounded in visible evidence.
"""


REVIEW_PROMPT = """
You are a strict reviewer for CPR image-pair annotations.
Review the proposed annotation against the images, boxes, and rules. Do not rewrite everything.

Return only a valid JSON object with this shape:
{
  "approved": true | false,
  "issues": ["short concrete issue"],
  "patch": {
    "caseType": "SINGLE | MULTI | RELATIONAL",
    "relationalSubject1ChangeEnabled": true | false,
    "relationalSubject2ChangeEnabled": true | false,
    "pairChangeRaw": "string|null",
    "pairChangeFinal": "string|null",
    "subjects": [
      {
        "subjectId": 1,
        "queryGroupIds": ["id"],
        "targetGroupIds": ["id"],
        "descQueryRaw": "English fragment",
        "descQueryFinal": "English fragment",
        "changeTargetRaw": "English fragment",
        "changeTargetFinal": "English fragment"
      }
    ]
  }
}

Use an empty patch object when the annotation is already good.
Patch only fields that are clearly wrong or incomplete. Keep good fields unchanged.
Never mention the literal label "Subject", "Subject 1", or "Subject 2" inside DESC or CHANGE field values.
Do not add unsupported details. Prefer leaving a field unchanged over guessing.
"""


def image_by_side(task: dict[str, Any], side: str) -> dict[str, Any] | None:
    wanted = {"QUERY", "A"} if side == "QUERY" else {"TARGET", "B"}
    for image in task.get("images", []):
        if str(image.get("side", "")).upper() in wanted:
            return image
    return None


def box_summary(image: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not image:
        return []
    summary: list[dict[str, Any]] = []
    for box in image.get("boxes", []):
        summary.append(
            {
                "id": box.get("id"),
                "groupUid": box.get("groupUid"),
                "label": box.get("label") or box.get("rawLabel"),
                "x": box.get("x"),
                "y": box.get("y"),
                "width": box.get("width"),
                "height": box.get("height"),
            }
        )
    return summary


async def task_image_as_data_url(
    image_url: str,
    boxes: list[dict[str, Any]] | None = None,
) -> str:
    if image_url.startswith("/api/local/images/"):
        filename = unquote(image_url.rsplit("/", 1)[-1])
        path = cached_image_path(filename)
        image_bytes = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        if content_type.startswith("image/"):
            image_bytes = uit_client._prepare_image(image_bytes, 512, 60, boxes)
            content_type = "image/jpeg"
        encoded = base64.b64encode(image_bytes).decode("ascii")
        return f"data:{content_type};base64,{encoded}"
    return await uit_client.image_as_data_url(image_url, boxes=boxes)


async def append_task_images(
    content: list[dict[str, Any]],
    query: dict[str, Any] | None,
    target: dict[str, Any] | None,
) -> None:
    for label, image in (("Query image", query), ("Target image", target)):
        if image and image.get("imageUrl"):
            image_url = str(image["imageUrl"])
            content.append({"type": "text", "text": label})
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": await task_image_as_data_url(image_url)},
                }
            )
            content.append({"type": "text", "text": f"{label} with bounding boxes"})
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": await task_image_as_data_url(
                            image_url,
                            boxes=box_summary(image),
                        )
                    },
                }
            )


async def generate_annotation(
    task: dict[str, Any], settings: RuntimeSettings, notes: str = ""
) -> Stage2Annotation:
    if not settings.openai_compat_api_key:
        raise ValueError("OpenAI-compatible API key is missing.")
    query = image_by_side(task, "QUERY")
    target = image_by_side(task, "TARGET")
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": json.dumps(
                {
                    "instruction": "Create the best annotation for this CPR pair.",
                    "queryBoxes": box_summary(query),
                    "targetBoxes": box_summary(target),
                    "userNotes": notes,
                    "schema": {
                        "schemaVersion": "1.0",
                        "caseType": "SINGLE | MULTI | RELATIONAL",
                        "relationalSubject1ChangeEnabled": "boolean",
                        "relationalSubject2ChangeEnabled": "boolean",
                        "pairChangeRaw": "string|null",
                        "pairChangeFinal": "string|null",
                        "subjects": [
                            {
                                "subjectId": 1,
                                "targetConstraintEnabled": True,
                                "queryGroupIds": ["query group id"],
                                "targetGroupIds": ["target group id"],
                                "descQueryRaw": "English fragment",
                                "descQueryFinal": "English fragment",
                                "changeTargetRaw": "English fragment",
                                "changeTargetFinal": "English fragment",
                            }
                        ],
                    },
                },
                ensure_ascii=True,
            ),
        }
    ]
    await append_task_images(content, query, target)

    payload = chat_payload(
        settings.openai_compat_model,
        temperature=0.1,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT.strip()},
            {"role": "user", "content": content},
        ],
        json_object=True,
    )
    url = settings.openai_compat_base_url.rstrip("/") + "/chat/completions"
    raw = await request_completion(url, settings.openai_compat_api_key, payload, 120.0)
    data = extract_json(raw)
    annotation = cleanup_stage2_annotation(coerce_annotation(data))
    annotation.captionFinal = build_caption(annotation)
    if settings.ai_double_check_enabled:
        annotation = await review_annotation(task, annotation, settings, notes)
    return annotation


async def review_annotation(
    task: dict[str, Any],
    annotation: Stage2Annotation,
    settings: RuntimeSettings,
    notes: str = "",
) -> Stage2Annotation:
    query = image_by_side(task, "QUERY")
    target = image_by_side(task, "TARGET")
    proposed = cleanup_stage2_annotation(annotation)
    proposed.captionFinal = build_caption(proposed)
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": json.dumps(
                {
                    "instruction": "Review this proposed CPR annotation and return a minimal JSON patch.",
                    "queryBoxes": box_summary(query),
                    "targetBoxes": box_summary(target),
                    "userNotes": notes,
                    "proposedAnnotation": proposed.model_dump(),
                    "proposedCaption": proposed.captionFinal,
                },
                ensure_ascii=True,
            ),
        }
    ]
    await append_task_images(content, query, target)
    payload = chat_payload(
        settings.openai_compat_review_model,
        temperature=0.0,
        messages=[
            {"role": "system", "content": f"{SYSTEM_PROMPT.strip()}\n\n{REVIEW_PROMPT.strip()}"},
            {"role": "user", "content": content},
        ],
        json_object=True,
    )
    url = settings.openai_compat_base_url.rstrip("/") + "/chat/completions"
    raw = await request_completion(url, settings.openai_compat_api_key, payload, 120.0)
    review = extract_json(raw)
    checked = apply_review_patch(proposed, review)
    checked.captionFinal = build_caption(checked)
    return checked


def apply_review_patch(annotation: Stage2Annotation, review: dict[str, Any]) -> Stage2Annotation:
    patch = review.get("patch")
    if not isinstance(patch, dict):
        patch = {}
    if not patch:
        cleaned = cleanup_stage2_annotation(annotation)
        cleaned.captionFinal = build_caption(cleaned)
        add_review_edit(cleaned, review)
        return cleaned

    merged = annotation.model_dump()
    for key in (
        "caseType",
        "relationalSubject1ChangeEnabled",
        "relationalSubject2ChangeEnabled",
        "pairChangeRaw",
        "pairChangeFinal",
    ):
        if key in patch:
            merged[key] = patch[key]

    subject_patches = patch.get("subjects")
    if isinstance(subject_patches, list):
        subjects_by_id: dict[int, dict[str, Any]] = {}
        for subject in merged.get("subjects", []):
            if isinstance(subject, dict):
                try:
                    subjects_by_id[int(subject.get("subjectId", len(subjects_by_id) + 1))] = dict(subject)
                except (TypeError, ValueError):
                    continue
        for subject_patch in subject_patches:
            if not isinstance(subject_patch, dict):
                continue
            try:
                subject_id = int(subject_patch.get("subjectId", 1))
            except (TypeError, ValueError):
                subject_id = 1
            current = subjects_by_id.get(subject_id, {"subjectId": subject_id, "targetConstraintEnabled": True})
            for key in (
                "queryGroupIds",
                "targetGroupIds",
                "descQueryRaw",
                "descQueryFinal",
                "changeTargetRaw",
                "changeTargetFinal",
            ):
                if key in subject_patch:
                    current[key] = subject_patch[key]
            subjects_by_id[subject_id] = current
        merged["subjects"] = [subjects_by_id[key] for key in sorted(subjects_by_id)]

    checked = cleanup_stage2_annotation(coerce_annotation(merged))
    checked.llmEdits = list(annotation.llmEdits)
    add_review_edit(checked, review)
    checked.captionFinal = build_caption(checked)
    return checked


def add_review_edit(annotation: Stage2Annotation, review: dict[str, Any]) -> None:
    issues = review.get("issues")
    if not isinstance(issues, list):
        issues = []
    annotation.llmEdits.append(
        {
            "type": "double_check",
            "approved": bool(review.get("approved", not issues)),
            "issues": [str(issue) for issue in issues],
        }
    )


async def fix_annotation_text(text: str, field: str, settings: RuntimeSettings) -> str:
    if not settings.openai_compat_api_key:
        raise ValueError("OpenAI-compatible API key is missing.")
    source = text.strip()
    if not source:
        return ""
    field_name = field.strip() or "annotation"
    prompt = f"""
Rewrite the user's intent as one concise natural English fragment for CPR annotation field: {field_name}.

Rules:
- Translate to English if needed.
- Keep only the meaning the user provided. Do not add new visible details.
- Follow the CPR annotation system rules: simple B1-level English, natural, concise, image-grounded wording.
- Avoid overusing commas. Prefer natural connector words where possible, and do not put a comma before connector words.
- Return a fragment, not a full sentence.
- Do not add markdown, quotes, labels, JSON, or commentary.
- Do not mention the literal label "Subject", "Subject 1", or "Subject 2" in DESC or CHANGE output.
- For DESC, describe who the subject is in the query image.
- For CHANGE, describe the visible target action, attribute, state, or condition.
- For PAIR_CHANGE, describe the ordered relation from Subject 1 to Subject 2.

User text:
{source}
""".strip()
    payload = chat_payload(
        settings.openai_compat_model,
        temperature=0.1,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT.strip()},
            {"role": "user", "content": prompt},
        ],
    )
    url = settings.openai_compat_base_url.rstrip("/") + "/chat/completions"
    fixed = (await request_completion(url, settings.openai_compat_api_key, payload, 60.0)).strip()
    if fixed.startswith("```"):
        fixed = fixed.strip("`").strip()
    fixed = fixed.strip().strip('"').strip("'").strip()
    if "PAIR" in field_name.upper():
        return remove_comma_before_connectors(fixed)
    return cleanup_desc_change_text(fixed)


def chat_payload(
    model: str,
    *,
    temperature: float,
    messages: list[dict[str, Any]],
    json_object: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "messages": messages,
        "stream": False,
        "max_tokens": 4096 if json_object else 512,
    }
    if "gemini" in model.lower():
        payload["max_completion_tokens"] = payload["max_tokens"]
    if json_object and "gemini" not in model.lower():
        payload["response_format"] = {"type": "json_object"}
    return payload


async def request_completion(url: str, api_key: str, payload: dict[str, Any], timeout: float) -> str:
    response = await post_completion(url, api_key, payload, timeout)
    try:
        content = completion_content(response)
        if content.strip():
            return content
    except ValueError as exc:
        if "no text output" not in str(exc):
            raise
    retry_payload = retry_chat_payload(payload)
    retry_response = await post_completion(url, api_key, retry_payload, timeout)
    return completion_content(retry_response)


def retry_delay_seconds(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("retry-after")
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            pass
    return MODEL_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))


async def post_completion(url: str, api_key: str, payload: dict[str, Any], timeout: float) -> httpx.Response:
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(1, MODEL_RETRY_ATTEMPTS + 1):
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
            if (
                response.status_code in TRANSIENT_MODEL_STATUSES
                and attempt < MODEL_RETRY_ATTEMPTS
            ):
                await asyncio.sleep(retry_delay_seconds(response, attempt))
                continue
            response.raise_for_status()
            return response
    raise RuntimeError("Model request retry loop exited unexpectedly.")


def retry_chat_payload(payload: dict[str, Any]) -> dict[str, Any]:
    retry_payload = {**payload}
    max_tokens = int(retry_payload.get("max_tokens") or 4096)
    retry_payload["max_tokens"] = max(max_tokens, 8192)
    if "gemini" in str(retry_payload.get("model", "")).lower():
        retry_payload["max_completion_tokens"] = retry_payload["max_tokens"]
    messages = retry_payload.get("messages")
    if isinstance(messages, list) and len(messages) >= 2 and messages[0].get("role") == "system":
        system_text = str(messages[0].get("content") or "")
        user_message = {**messages[1]}
        user_content = user_message.get("content")
        if isinstance(user_content, list):
            user_message["content"] = [{"type": "text", "text": system_text}, *user_content]
        elif isinstance(user_content, str):
            user_message["content"] = f"{system_text}\n\n{user_content}"
        retry_payload["messages"] = [user_message, *messages[2:]]
    return retry_payload


def completion_content(response: httpx.Response) -> str:
    try:
        response_data = response.json()
    except JSONDecodeError:
        return streamed_completion_content(response.text)
    if not isinstance(response_data, dict):
        raise ValueError(f"Model API returned unexpected JSON: {str(response_data)[:500]}")
    if response_data.get("error"):
        raise ValueError(f"Model API returned an error: {str(response_data['error'])[:500]}")

    choices = response_data.get("choices")
    if isinstance(choices, list) and choices:
        return choice_content(choices[0])

    choice = response_data.get("choice")
    if isinstance(choice, dict):
        return choice_content(choice)

    for key in ("content", "text", "output_text", "response"):
        value = response_data.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            nested = nested_response_content(value)
            if nested:
                return nested
            if key == "response":
                usage = value.get("usageMetadata")
                raise ValueError(
                    "Model API returned no text output in response. "
                    f"Usage metadata: {usage}. Body: {str(response_data)[:500]}"
                )

    raise ValueError(
        "Model API response did not include choices/message content. "
        f"Response keys: {', '.join(response_data.keys())}. Body: {str(response_data)[:500]}"
    )


def choice_content(choice: dict[str, Any]) -> str:
    message = choice.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, list):
            return "".join(str(item.get("text") or "") if isinstance(item, dict) else str(item) for item in content)
        return str(content or "")
    if isinstance(choice.get("delta"), dict):
        return str(choice["delta"].get("content") or "")
    if isinstance(choice.get("text"), str):
        return str(choice["text"])
    if isinstance(choice.get("content"), str):
        return str(choice["content"])
    raise ValueError(f"Model API choice did not include message content: {str(choice)[:500]}")


def nested_response_content(response_data: dict[str, Any]) -> str:
    candidates = response_data.get("candidates")
    if isinstance(candidates, list) and candidates:
        candidate = candidates[0]
        if isinstance(candidate, dict):
            content = candidate.get("content")
            if isinstance(content, dict):
                parts = content.get("parts")
                if isinstance(parts, list):
                    return "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict))
                if isinstance(content.get("text"), str):
                    return str(content["text"])
            if isinstance(candidate.get("text"), str):
                return str(candidate["text"])
    for key in ("text", "outputText", "output_text"):
        value = response_data.get(key)
        if isinstance(value, str):
            return value
    return ""


def streamed_completion_content(text: str) -> str:
    chunks: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if not data or data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except JSONDecodeError as exc:
            raise ValueError(f"Model API returned invalid JSON event: {data[:500]}") from exc
        for choice in event.get("choices", []):
            delta = choice.get("delta") or {}
            chunks.append(str(delta.get("content") or ""))
    if not chunks:
        raise ValueError(f"Model API returned invalid JSON: {text[:500]}")
    return "".join(chunks)


def extract_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if not text:
        raise ValueError("Model returned an empty response body.")
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end >= start:
        text = text[start : end + 1]
    if not text:
        raise ValueError("Model response did not contain JSON content.")
    return json.loads(text)


def coerce_annotation(data: dict[str, Any]) -> Stage2Annotation:
    subjects = data.get("subjects") or []
    normalized_subjects = []
    for index, item in enumerate(subjects):
        subject_id = 2 if int(item.get("subjectId", index + 1)) == 2 else 1
        normalized_subjects.append(
            SubjectAnnotation(
                subjectId=subject_id,
                targetConstraintEnabled=True,
                queryGroupIds=[str(value) for value in item.get("queryGroupIds", []) if str(value).strip()],
                targetGroupIds=[str(value) for value in item.get("targetGroupIds", []) if str(value).strip()],
                descQueryRaw=str(item.get("descQueryRaw") or item.get("descQueryFinal") or ""),
                descQueryFinal=str(item.get("descQueryFinal") or item.get("descQueryRaw") or ""),
                changeTargetRaw=str(item.get("changeTargetRaw") or item.get("changeTargetFinal") or ""),
                changeTargetFinal=str(item.get("changeTargetFinal") or item.get("changeTargetRaw") or ""),
            )
        )
    case_type = str(data.get("caseType") or "SINGLE").upper()
    if case_type not in {"SINGLE", "MULTI", "RELATIONAL"}:
        case_type = "SINGLE"
    expected = 1 if case_type == "SINGLE" else 2
    normalized_subjects = sorted(normalized_subjects, key=lambda item: item.subjectId)[:expected]
    return Stage2Annotation(
        caseType=case_type,
        relationalSubject1ChangeEnabled=bool(data.get("relationalSubject1ChangeEnabled", False)),
        relationalSubject2ChangeEnabled=bool(data.get("relationalSubject2ChangeEnabled", False)),
        pairChangeRaw=data.get("pairChangeRaw"),
        pairChangeFinal=data.get("pairChangeFinal") or data.get("pairChangeRaw"),
        subjects=normalized_subjects,
    )


def cleanup_stage2_annotation(annotation: Stage2Annotation) -> Stage2Annotation:
    annotation.pairChangeRaw = remove_comma_before_connectors(annotation.pairChangeRaw)
    annotation.pairChangeFinal = remove_comma_before_connectors(annotation.pairChangeFinal)
    for subject in annotation.subjects:
        subject.descQueryRaw = cleanup_desc_change_text(subject.descQueryRaw)
        subject.descQueryFinal = cleanup_desc_change_text(subject.descQueryFinal)
        subject.changeTargetRaw = cleanup_desc_change_text(subject.changeTargetRaw)
        subject.changeTargetFinal = cleanup_desc_change_text(subject.changeTargetFinal)
    return annotation
