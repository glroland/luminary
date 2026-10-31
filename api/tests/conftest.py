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

    from app import chat
    from app.database import get_connection
    from fastapi.testclient import TestClient

    from app.main import app
    from app.sync import progress as sync_progress

    # Sync (triggered by most tests that use this fixture) generates a
    # description per inserted/updated photo via chat.caption_image, which
    # calls chat._load_florence() -- left un-mocked, that downloads
    # Florence-2's real weights from HuggingFace on first use. Default it
    # to "not configured" here; tests that specifically exercise
    # description generation override this themselves via monkeypatch,
    # which layers on top and unwinds correctly at teardown either way.
    # (Tests that don't request this fixture, e.g. chat.py's own
    # _load_florence unit tests, are unaffected.)
    def _unconfigured():
        raise chat.ChatNotConfigured("Florence-2 not configured in tests")

    monkeypatch.setattr(chat, "_load_florence", _unconfigured)

    sync_progress.reset()

    with TestClient(app) as test_client:
        # There's no per-test throwaway SQLite file anymore -- tests share
        # one real Postgres database, the `luminary-unittest` one `make db`
        # provisions and `make test-api` points LUMINARY_DATABASE_* at (see
        # config.py/README/Makefile) -- distinct from the `luminary-dev`
        # database `make run-api` uses, so running tests doesn't wipe your
        # locally-indexed dev photos. The app's startup lifespan (just run
        # by entering the `with` block above) has already created the
        # tables if they didn't exist; truncate them here for isolation
        # between tests.
        with get_connection() as conn:
            conn.execute("TRUNCATE TABLE photos, faces")
            conn.commit()
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
