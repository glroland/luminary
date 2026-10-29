import sys
import time
from pathlib import Path

import pytest
from PIL import Image

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINARY_PHOTOS_URI", str(tmp_path / "photos"))
    monkeypatch.setenv("LUMINARY_DATABASE_PATH", str(tmp_path / "photos.db"))

    from fastapi.testclient import TestClient

    from app.main import app
    from app.sync import progress as sync_progress

    sync_progress.reset()

    with TestClient(app) as test_client:
        yield test_client


def write_image(path, size=(10, 10), color="red", exif=None):
    """Write a JPEG directly onto the (simulated) photos backend, as if it
    had always been there -- the API has no write path of its own to put a
    photo there."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, color=color)
    if exif is not None:
        image.save(path, "JPEG", exif=exif)
    else:
        image.save(path, "JPEG")


def sync_and_wait(client, timeout=5.0):
    client.post("/api/sync")
    deadline = time.monotonic() + timeout
    status = {}
    while time.monotonic() < deadline:
        status = client.get("/api/sync/status").json()
        # start_sync() flips status to "running" synchronously before the
        # POST even returns (see sync.py's try_start()), so only a genuine
        # terminal status here means *this* sync is actually done.
        if status["status"] in ("completed", "error"):
            return status
        time.sleep(0.05)
    raise AssertionError(f"sync did not finish in time: {status}")
