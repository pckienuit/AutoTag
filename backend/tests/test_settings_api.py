from starlette.applications import Starlette
from starlette.testclient import TestClient

from backend.app.main import app
from backend.app.models import AutomationStartRequest, RcrAnnotation
from backend.app.settings import load_settings


def test_load_settings_reads_flags_and_defaults() -> None:
    settings = load_settings({"AUTO_SUBMIT_ENABLED": "True", "AI_DOUBLE_CHECK_ENABLED": "0", "RCR_USERNAME": "u"})

    assert settings.auto_submit_enabled is True
    assert settings.ai_double_check_enabled is False
    assert settings.rcr_username == "u"
    assert settings.openai_compat_model == "ag/gemini-3.8-flash-high"
    assert load_settings({}).auto_submit_enabled is False
    assert load_settings({"AUTO_SUBMIT_ENABLED": ""}).ai_double_check_enabled is True


def test_auto_submit_can_be_toggled_from_the_ui(monkeypatch) -> None:
    monkeypatch.setattr("backend.app.main.RUNTIME_OVERRIDES", {})
    monkeypatch.setattr("backend.app.main.get_settings", lambda: load_settings({}))
    client = TestClient(app)

    assert client.get("/api/settings").json()["autoSubmitEnabled"] is False
    assert client.put("/api/settings/auto-submit", json={"enabled": True}).json() == {"autoSubmitEnabled": True}
    assert client.get("/api/settings").json()["autoSubmitEnabled"] is True
    client.put("/api/settings/auto-submit", json={"enabled": False})
    assert client.get("/api/settings").json()["autoSubmitEnabled"] is False


def test_bad_requests_get_a_json_detail() -> None:
    client = TestClient(app)

    for response in (
        client.put("/api/settings/auto-submit", json={"enabled": "yes"}),
        client.put("/api/settings/auto-submit", content=b"not json"),
        client.post("/api/ai/validate", json={"annotation": {"case_type": "NOPE"}}),
        client.post("/api/rcr/draft", json={"annotation": {}}),
        client.post("/api/automation/start", json={"mode": "everything"}),
        client.get("/api/local/tasks?limit=0"),
        client.get("/api/rcr/images/abc/side"),
    ):
        assert response.status_code in (404, 422)
        assert isinstance(response.json()["detail"], str)


def test_validate_endpoint_reports_rule_issues() -> None:
    client = TestClient(app)
    annotation = {
        "case_type": "INDIVIDUAL",
        "subjects": [{"subject_id": 1, "identity_ids": ["1"]}],
        "select_texts": ["a man"],
        "target_condition": "the man sits",
    }

    result = client.post("/api/ai/validate", json={"annotation": annotation}).json()

    assert result["issues"] == ["Target condition must mention Subject 1."]
    assert result["instruction"].startswith("Identify Subject 1 as a man;")


def test_annotation_from_dict_validates_untrusted_input() -> None:
    parsed = RcrAnnotation.from_dict(
        {"case_type": "DUAL", "subjects": [{"subject_id": 1, "identity_ids": [10]}], "select_texts": ["a", "b"]}
    )

    assert parsed.subjects[0].identity_ids == ["10"]
    assert parsed.to_dict()["target_condition"] == ""
    for bad in (None, {"case_type": "x"}, {"case_type": "DUAL", "select_texts": [1]}, {"case_type": "DUAL", "subjects": [{}]}):
        try:
            RcrAnnotation.from_dict(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")
    assert AutomationStartRequest.from_dict({"mode": "fixed_limit", "limit": 3}).limit == 3


def test_frontend_is_served_without_shadowing_the_api(tmp_path) -> None:
    from backend.app import main

    (tmp_path / "index.html").write_text("<div id=root></div>")
    fresh = Starlette()
    original, main.app = main.app, fresh
    try:
        assert main.mount_frontend(tmp_path) is True
        assert main.mount_frontend(tmp_path / "missing") is False
    finally:
        main.app = original

    assert TestClient(fresh).get("/").text == "<div id=root></div>"
    assert TestClient(app).get("/api/health").json() == {"status": "ok"}
