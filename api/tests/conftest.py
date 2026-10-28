import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINARY_PHOTOS_URI", str(tmp_path / "photos"))
    monkeypatch.setenv("LUMINARY_DATABASE_PATH", str(tmp_path / "photos.db"))

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
