import asyncio
import io
import json

import pytest
from PIL import Image

from backend.app import ai_service
from backend.app.ai_service import (
    apply_generated_text,
    apply_review_patch,
    generate_annotation,
    initial_annotation,
)
from backend.app.images import render_for_model
from backend.app.models import RuntimeSettings
from backend.tests.fixtures import make_task


def settings(**overrides) -> RuntimeSettings:
    values = {"openai_compat_api_key": "key", "ai_double_check_enabled": False}
    return RuntimeSettings(**{**values, **overrides})


def jpeg_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (200, 100), "white").save(output, format="JPEG")
    return output.getvalue()


def test_initial_annotation_uses_task_subjects() -> None:
    result = initial_annotation(make_task("RELATIONAL"))

    assert result.case_type == "RELATIONAL"
    assert [item.identity_ids for item in result.subjects] == [["10"], ["20"]]
    assert result.select_texts == ["", ""]


def test_initial_annotation_group_and_fallback_assignment() -> None:
    task = make_task("INDIVIDUAL")
    task["candidate_identity_ids"] = ["10", "20"]
    task["initial_subjects"] = []

    assert initial_annotation(task).case_type == "GROUP"
    assert initial_annotation(make_task("GROUP")).subjects[0].identity_ids == ["10", "20"]
    broken = make_task("DUAL")
    broken["initial_subjects"] = []
    with pytest.raises(ValueError, match="no initial subject assignment"):
        initial_annotation(broken)


def test_apply_generated_text_cleans_and_pads() -> None:
    base = initial_annotation(make_task("DUAL"))

    result, concerns = apply_generated_text(
        base,
        {
            "select_texts": ["a man, and a hat."],
            "target_condition": "then retrieve target images where Subject 1 sits, and Subject 2 stands.",
            "subject_problem": "box 20 shows a different person",
        },
    )

    assert result.select_texts == ["a man and a hat", ""]
    assert result.target_condition == "Subject 1 sits and Subject 2 stands"
    assert concerns == ["Model flagged the subject boxes: box 20 shows a different person"]
    assert [item.identity_ids for item in result.subjects] == [["10"], ["20"]]


def test_apply_review_patch_changes_only_text_and_fails_closed() -> None:
    base = initial_annotation(make_task("DUAL")).replace(
        select_texts=["a man", "a woman"], target_condition="Subject 1 sits and Subject 2 stands"
    )
    patch = {"target_condition": "Subject 1 stands and Subject 2 sits", "case_type": "RELATIONAL"}

    approved, approved_issues = apply_review_patch(base, {"approved": True, "issues": ["minor"], "patch": patch})
    # A patch alone does not prove every rejection was resolved, so the task still needs a human.
    patched, patched_issues = apply_review_patch(base, {"approved": False, "issues": ["wrong action"], "patch": patch})
    rejected, rejected_issues = apply_review_patch(base, {"approved": False, "issues": ["bad"], "patch": {}})
    silent, silent_issues = apply_review_patch(base, {"patch": {}})
    wrong_size, _ = apply_review_patch(base, {"approved": True, "patch": {"select_texts": ["only one"]}})

    assert approved.target_condition == patched.target_condition == "Subject 1 stands and Subject 2 sits"
    assert approved.case_type == "DUAL"  # the reviewer can never change the case
    assert approved_issues == []
    assert patched_issues == ["wrong action"]
    assert rejected == base and rejected_issues == ["bad"]
    assert silent == base and silent_issues == ["Reviewer did not approve but gave no details."]
    assert wrong_size.select_texts == ["a man", "a woman"]


def test_render_for_model_returns_resized_jpeg_data_url() -> None:
    data_url = render_for_model(
        jpeg_bytes(), [{"identity_id": "10", "x": 0.1, "y": 0.1, "width": 0.3, "height": 0.5}], {"10": 1}, max_side=64
    )

    assert data_url.startswith("data:image/jpeg;base64,")


def test_generate_annotation_uses_configured_model_and_labels_boxes(monkeypatch) -> None:
    payloads: list[dict] = []

    async def fake_load(sample_id: str, side: str) -> bytes:
        return jpeg_bytes()

    async def fake_completion(url: str, api_key: str, payload: dict, timeout: float) -> str:
        payloads.append(payload)
        return json.dumps(
            {"select_texts": ["the man in red"], "target_condition": "Subject 1 is sitting", "subject_problem": None}
        )

    monkeypatch.setattr(ai_service, "load_image_bytes", fake_load)
    monkeypatch.setattr(ai_service, "request_completion", fake_completion)

    result = asyncio.run(generate_annotation(make_task("INDIVIDUAL"), settings(openai_compat_model="ag/gemini-3.8-flash-high")))

    assert result.annotation.select_texts == ["the man in red"]
    assert result.annotation.target_condition == "Subject 1 is sitting"
    assert result.concerns == []
    assert payloads[0]["model"] == "ag/gemini-3.8-flash-high"
    user_content = payloads[0]["messages"][1]["content"]
    assert sum(1 for part in user_content if part["type"] == "image_url") == 4


def test_generate_annotation_retries_with_blurred_images_when_model_returns_nothing(monkeypatch) -> None:
    calls: list[int] = []

    async def fake_load(sample_id: str, side: str) -> bytes:
        return jpeg_bytes()

    async def fake_completion(url: str, api_key: str, payload: dict, timeout: float) -> str:
        calls.append(sum(1 for part in payload["messages"][1]["content"] if part["type"] == "image_url"))
        if len(calls) == 1:
            raise ValueError("Model API returned no text output.")
        return json.dumps({"select_texts": ["a man"], "target_condition": "Subject 1 sits"})

    monkeypatch.setattr(ai_service, "load_image_bytes", fake_load)
    monkeypatch.setattr(ai_service, "request_completion", fake_completion)

    result = asyncio.run(generate_annotation(make_task("INDIVIDUAL"), settings()))

    assert calls == [4, 2]
    assert result.annotation.select_texts == ["a man"]


def test_generate_annotation_requires_api_key() -> None:
    with pytest.raises(ValueError, match="API key"):
        asyncio.run(generate_annotation(make_task(), RuntimeSettings()))


def test_double_check_uses_review_model_and_reports_rejection(monkeypatch) -> None:
    models: list[str] = []
    efforts: list[object] = []

    async def fake_load(sample_id: str, side: str) -> bytes:
        return jpeg_bytes()

    async def fake_completion(url: str, api_key: str, payload: dict, timeout: float) -> str:
        models.append(payload["model"])
        efforts.append(payload.get("reasoning_effort"))
        if len(models) == 1:
            return json.dumps({"select_texts": ["a man"], "target_condition": "Subject 1 sits"})
        return json.dumps({"approved": False, "issues": ["target shows standing"], "patch": {}})

    monkeypatch.setattr(ai_service, "load_image_bytes", fake_load)
    monkeypatch.setattr(ai_service, "request_completion", fake_completion)

    result = asyncio.run(
        generate_annotation(
            make_task(),
            settings(
                ai_double_check_enabled=True,
                openai_compat_model="gen",
                openai_compat_review_model="rev",
                openai_compat_review_effort="high",
            ),
        )
    )

    assert models == ["gen", "rev"]
    assert efforts == [None, "high"]  # effort applies to the review call only
    assert result.concerns == ["target shows standing"]


def test_image_cache_keeps_only_the_newest_files(tmp_path, monkeypatch) -> None:
    import os

    from backend.app import images

    monkeypatch.setattr(images, "IMAGE_CACHE_DIR", tmp_path)
    for index in range(5):
        path = tmp_path / f"{index}.jpg"
        path.write_bytes(b"x")
        os.utime(path, (1000 + index, 1000 + index))

    images.prune_cache(2)

    assert sorted(item.name for item in tmp_path.glob("*.jpg")) == ["3.jpg", "4.jpg"]
