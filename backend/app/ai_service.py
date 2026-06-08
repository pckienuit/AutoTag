import json
from json import JSONDecodeError
from typing import Any

import httpx

from .caption import build_caption
from .models import RuntimeSettings, Stage2Annotation, SubjectAnnotation
from .uit_client import uit_client


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
- For RELATIONAL, individual subject change fields are optional and should be filled only when they are clearly visible.
- Subject 1 and Subject 2 must be different. A subject may be a group if the query image shows them as a group.
- Select queryGroupIds from the query image only. Include targetGroupIds only when they clearly match the same subject(s) in the target image.
- Do not use IDs, technical terms, or overly generic labels like "the person" when the image allows a more specific description.
- Write concise, natural English fragments, not full sentences inside DESC/CHANGE/PAIR_CHANGE.
- If multiple people are best treated as one unit, use SINGLE.
- If a field is not supported by visible evidence, leave it empty rather than guessing.
- When choosing DESC details, prioritize visible uniqueness in this order: clothing -> accessories -> hair -> environment.
- If clothing evidence is sparse or absent, strengthen the description with accessories, then hair, then environment/background only as needed to disambiguate.
- Prefer the most distinctive visible attributes available, so the subject can still be uniquely identified even when earlier levels in the hierarchy are weak.
- Each DESC should include at least 3 visible distinguishing details whenever the image provides them.
- Do not stop at a short phrase like "the man wearing a gray shirt"; expand it with more visible details, such as "the man wearing a gray shirt with white text, standing near the monument".
- Count separate visible cues such as clothing color/type, text or pattern on clothing, accessories, hairstyle, pose, relative position, nearby object, and immediate background/environment.
- Use simple English vocabulary, ideally below B1 level. Prefer plain, common words over advanced or academic wording.
- Keep phrasing natural and clear, but avoid rare adjectives or complex sentence structures when a simpler phrase says the same thing.
- Vary the wording a little when the images allow it, so similar tasks do not always produce the exact same phrasing.
- Prefer human-like, direct descriptions grounded in the image over rigid template-heavy wording, while still keeping the final caption valid and easy to read.

The final caption built from your fields should follow these patterns:
- SINGLE: In the query image, Subject 1 refers to [DESC]. Retrieve target images where Subject 1 [CHANGE].
- MULTI: In the query image, Subject 1 refers to [DESC 1], and Subject 2 refers to [DESC 2]. Retrieve target images where Subject 1 [CHANGE 1] and Subject 2 [CHANGE 2].
- RELATIONAL: In the query image, Subject 1 refers to [DESC 1], and Subject 2 refers to [DESC 2]. Retrieve target images where Subject 1 [PAIR_CHANGE] Subject 2.

Before responding, verify that the JSON is valid, the case is consistent with the target image, and the text is short, natural, and grounded in visible evidence.
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
    for label, image in (("Query image", query), ("Target image", target)):
        if image and image.get("imageUrl"):
            data_url = await uit_client.image_as_data_url(str(image["imageUrl"]))
            content.append({"type": "text", "text": label})
            content.append({"type": "image_url", "image_url": {"url": data_url}})

    payload = {
        "model": settings.openai_compat_model,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT.strip()},
            {"role": "user", "content": content},
        ],
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    url = settings.openai_compat_base_url.rstrip("/") + "/chat/completions"
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {settings.openai_compat_api_key}"},
            json=payload,
        )
        response.raise_for_status()
    raw = completion_content(response)
    data = extract_json(raw)
    annotation = coerce_annotation(data)
    annotation.captionFinal = build_caption(annotation)
    return annotation


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
- Return a fragment, not a full sentence.
- Do not add markdown, quotes, labels, JSON, or commentary.
- For DESC, describe who the subject is in the query image.
- For CHANGE, describe the visible target action, attribute, state, or condition.
- For PAIR_CHANGE, describe the ordered relation from Subject 1 to Subject 2.

User text:
{source}
""".strip()
    payload = {
        "model": settings.openai_compat_model,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT.strip()},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
    }
    url = settings.openai_compat_base_url.rstrip("/") + "/chat/completions"
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {settings.openai_compat_api_key}"},
            json=payload,
        )
        response.raise_for_status()
    fixed = completion_content(response).strip()
    if fixed.startswith("```"):
        fixed = fixed.strip("`").strip()
    return fixed.strip().strip('"').strip("'").strip()


def completion_content(response: httpx.Response) -> str:
    try:
        response_data = response.json()
    except JSONDecodeError:
        return streamed_completion_content(response.text)
    return str(response_data["choices"][0]["message"]["content"] or "")


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
