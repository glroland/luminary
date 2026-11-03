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

    from app import chat, sync
    from app.database import get_connection
    from fastapi.testclient import TestClient

    from app.enrich import progress as description_progress
    from app.geocode import progress as location_progress
    from app.main import app
    from app.recognition import progress as refresh_progress
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

    # A successful sync now also auto-triggers recognition.py's "Refresh
    # Faces" job (see sync.py's module docstring/_trigger_face_refresh).
    # Left un-mocked, every sync-exercising test below would also spin up a
    # real background face-matching run -- slow, and racy against this
    # fixture's own per-test progress reset/table truncate just below.
    # Defaulted to a no-op here; the dedicated test(s) that exercise the
    # chaining itself (test_sync.py) restore/spy on the real call.
    monkeypatch.setattr(sync, "_trigger_face_refresh", lambda: None)

    # people.tag_face/untag_face/delete_individual and sync's own Florence
    # pipeline now all call enrich.enrich_description() (see enrich.py),
    # which talks to the openai-compatible endpoint if LUMINARY_OPENAI_*
    # happen to already be set in the shell this test process inherited
    # from (e.g. a developer who also runs `make run-api` in the same
    # shell). Clear them here so every test gets a deterministic,
    # network-free "not configured" no-op by default, regardless of the
    # ambient environment; tests that specifically exercise enrichment
    # set these themselves via monkeypatch, which layers on top and
    # unwinds correctly at teardown either way -- same pattern as
    # chat._load_florence above.
    monkeypatch.delenv("LUMINARY_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LUMINARY_OPENAI_MODEL", raising=False)
    monkeypatch.delenv("LUMINARY_OPENAI_BASE_URL", raising=False)

    sync_progress.reset()
    refresh_progress.reset()
    description_progress.reset()
    location_progress.reset()

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
            conn.execute("TRUNCATE TABLE photos, faces, individuals, tags, photo_tags")
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


def refresh_and_wait(client, timeout=5.0):
    """Same polling pattern as sync_and_wait, for recognition.py's
    "Refresh Faces" job (POST /api/faces/refresh)."""
    client.post("/api/faces/refresh")
    deadline = time.monotonic() + timeout
    status = {}
    while time.monotonic() < deadline:
        status = client.get("/api/faces/refresh/status").json()
        if status["status"] in ("completed", "error"):
            return status
        time.sleep(0.05)
    raise AssertionError(f"face refresh did not finish in time: {status}")


def refresh_descriptions_and_wait(client, timeout=5.0):
    """Same polling pattern as sync_and_wait, for enrich.py's "Refresh
    Descriptions" job (POST /api/descriptions/refresh)."""
    client.post("/api/descriptions/refresh")
    deadline = time.monotonic() + timeout
    status = {}
    while time.monotonic() < deadline:
        status = client.get("/api/descriptions/refresh/status").json()
        if status["status"] in ("completed", "error"):
            return status
        time.sleep(0.05)
    raise AssertionError(f"description refresh did not finish in time: {status}")


def refresh_locations_and_wait(client, timeout=5.0):
    """Same polling pattern as sync_and_wait, for geocode.py's "Refresh
    Locations" job (POST /api/locations/refresh)."""
    client.post("/api/locations/refresh")
    deadline = time.monotonic() + timeout
    status = {}
    while time.monotonic() < deadline:
        status = client.get("/api/locations/refresh/status").json()
        if status["status"] in ("completed", "error"):
            return status
        time.sleep(0.05)
    raise AssertionError(f"location refresh did not finish in time: {status}")
