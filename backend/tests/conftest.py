import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Keep tests away from the real SQLite file and image cache."""
    from backend.app import images, store

    monkeypatch.setattr(store, "STATE_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_FILE", tmp_path / "test.sqlite3")
    monkeypatch.setattr(images, "IMAGE_CACHE_DIR", tmp_path / "images")
