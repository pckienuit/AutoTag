from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.settings import Settings


def test_auto_submit_can_be_toggled_from_the_ui(monkeypatch) -> None:
    monkeypatch.setattr("backend.app.main.RUNTIME_OVERRIDES", {})
    monkeypatch.setattr("backend.app.main.get_settings", lambda: Settings(_env_file=None, AUTO_SUBMIT_ENABLED=False))
    client = TestClient(app)

    assert client.get("/api/settings").json()["autoSubmitEnabled"] is False
    assert client.put("/api/settings/auto-submit", json={"enabled": True}).json() == {"autoSubmitEnabled": True}
    assert client.get("/api/settings").json()["autoSubmitEnabled"] is True
    client.put("/api/settings/auto-submit", json={"enabled": False})
    assert client.get("/api/settings").json()["autoSubmitEnabled"] is False


def test_frontend_is_served_without_shadowing_the_api(tmp_path) -> None:
    from fastapi import FastAPI

    from backend.app import main

    (tmp_path / "index.html").write_text("<div id=root></div>")
    fresh = FastAPI()
    fresh.get("/api/health")(lambda: {"status": "ok"})
    original, main.app = main.app, fresh
    try:
        assert main.mount_frontend(tmp_path) is True
        assert main.mount_frontend(tmp_path / "missing") is False
    finally:
        main.app = original
    client = TestClient(fresh)

    assert client.get("/").text == "<div id=root></div>"
    assert client.get("/api/health").json() == {"status": "ok"}
