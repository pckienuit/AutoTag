import json
import io

import httpx
import pytest
from PIL import Image

from backend.app.ai_service import apply_review_patch, cleanup_stage2_annotation, coerce_annotation, completion_content
from backend.app.models import Stage2Annotation, SubjectAnnotation
from backend.app.uit_client import UitClient


def response_json(data: dict) -> httpx.Response:
    return httpx.Response(200, content=json.dumps(data).encode("utf-8"))


def test_completion_content_reads_openai_choices() -> None:
    response = response_json({"choices": [{"message": {"content": "hello"}}]})

    assert completion_content(response) == "hello"


def test_completion_content_reads_singular_choice() -> None:
    response = response_json({"choice": {"message": {"content": "hello"}}})

    assert completion_content(response) == "hello"


def test_completion_content_reports_model_error() -> None:
    response = response_json({"error": {"message": "bad model"}})

    with pytest.raises(ValueError, match="bad model"):
        completion_content(response)


def test_completion_content_reports_missing_choices() -> None:
    response = response_json({"id": "abc"})

    with pytest.raises(ValueError, match="did not include choices"):
        completion_content(response)


def test_completion_content_reads_nested_gemini_response() -> None:
    response = response_json({
        "response": {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "hello"},
                            {"text": " world"},
                        ]
                    }
                }
            ]
        }
    })

    assert completion_content(response) == "hello world"


def test_cleanup_stage2_annotation_removes_comma_before_connectors() -> None:
    annotation = cleanup_stage2_annotation(
        coerce_annotation(
            {
                "caseType": "SINGLE",
                "subjects": [
                    {
                        "subjectId": 1,
                        "queryGroupIds": ["1"],
                        "descQueryFinal": "the man in a hat, and a blue shirt",
                        "changeTargetFinal": "is sitting, and smiling",
                    }
                ],
            }
        )
    )

    subject = annotation.subjects[0]
    assert ", and" not in subject.descQueryFinal
    assert ", and" not in subject.changeTargetFinal


def test_cleanup_stage2_annotation_removes_subject_from_desc_and_change() -> None:
    annotation = cleanup_stage2_annotation(
        coerce_annotation(
            {
                "caseType": "SINGLE",
                "subjects": [
                    {
                        "subjectId": 1,
                        "queryGroupIds": ["1"],
                        "descQueryFinal": "Subject 1 refers to the woman in a yellow shirt",
                        "changeTargetFinal": "Subject 1 is sitting on a bench",
                    }
                ],
            }
        )
    )

    subject = annotation.subjects[0]
    assert subject.descQueryFinal == "the woman in a yellow shirt"
    assert subject.changeTargetFinal == "is sitting on a bench"


def test_apply_review_patch_merges_minimal_subject_changes() -> None:
    annotation = Stage2Annotation(
        caseType="SINGLE",
        subjects=[
            SubjectAnnotation(
                subjectId=1,
                queryGroupIds=["1"],
                descQueryFinal="the person in blue",
                changeTargetFinal="is walking",
            )
        ],
    )

    checked = apply_review_patch(
        annotation,
        {
            "approved": False,
            "issues": ["DESC is too generic"],
            "patch": {
                "subjects": [
                    {
                        "subjectId": 1,
                        "descQueryFinal": "Subject 1 refers to the woman in a blue jacket",
                    }
                ]
            },
        },
    )

    subject = checked.subjects[0]
    assert subject.queryGroupIds == ["1"]
    assert subject.descQueryFinal == "the woman in a blue jacket"
    assert subject.changeTargetFinal == "is walking"
    assert checked.llmEdits[-1]["type"] == "double_check"


def test_completion_content_reports_nested_empty_response() -> None:
    response = response_json({
        "response": {
            "usageMetadata": {
                "promptTokenCount": 1450,
                "totalTokenCount": 1450,
            }
        }
    })

    with pytest.raises(ValueError, match="no text output"):
        completion_content(response)


def test_box_coords_supports_normalized_boxes() -> None:
    assert UitClient._box_coords(
        {"x": 0.25, "y": 0.1, "width": 0.5, "height": 0.4},
        200,
        100,
    ) == (50, 10, 150, 50)


def test_prepare_image_draws_boxes() -> None:
    source = Image.new("RGB", (100, 100), "white")
    buffer = io.BytesIO()
    source.save(buffer, format="PNG")

    output = UitClient._prepare_image(
        buffer.getvalue(),
        max_side=100,
        quality=95,
        boxes=[{"id": "A", "x": 0.1, "y": 0.1, "width": 0.4, "height": 0.4}],
    )

    with Image.open(io.BytesIO(output)) as image:
        assert image.convert("RGB").getpixel((10, 10)) != (255, 255, 255)
