import json
from typing import Any

import httpx

from .caption import build_caption
from .models import RuntimeSettings, Stage2Annotation, SubjectAnnotation
from .uit_client import uit_client


SYSTEM_PROMPT = """
You label CPR image-pair tasks. Return only strict JSON for the Stage 2 annotation schema.
Use only visible evidence. Do not infer emotions, jobs, family relations, or hidden facts.
Choose exactly one caseType: SINGLE, MULTI, RELATIONAL.
DESC describes who the subject is in the query image. CHANGE describes what the same subject does or has in the target image.
For RELATIONAL, pairChangeFinal must be a relation phrase between Subject 1 and Subject 2, for example "is standing next to".
Use natural, concise English fragments, not full captions inside DESC/CHANGE.
Select queryGroupIds from query boxes. If target group IDs match the same people, include targetGroupIds.
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
    }
    url = settings.openai_compat_base_url.rstrip("/") + "/chat/completions"
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {settings.openai_compat_api_key}"},
            json=payload,
        )
        response.raise_for_status()
    raw = response.json()["choices"][0]["message"]["content"]
    data = extract_json(raw)
    annotation = coerce_annotation(data)
    annotation.captionFinal = build_caption(annotation)
    return annotation


def extract_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end >= start:
        text = text[start : end + 1]
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

