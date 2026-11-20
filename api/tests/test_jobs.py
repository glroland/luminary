import threading
import time

import pytest

from app import enrich, jobs
from app.database import get_connection


def _insert_photos(count):
    with get_connection() as conn:
        for i in range(count):
            conn.execute(
                "INSERT INTO photos (id, filename, content_type, size_bytes, created_at) "
                "VALUES (%s, %s, 'image/jpeg', 1, '2024-01-01T00:00:00')",
                (f"photo-{i}", f"{i}.jpg"),
            )
        conn.commit()


def _wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("condition not met in time")


@pytest.fixture
def held_description_job(client, monkeypatch):
    """A description refresh that blocks inside its first photos until the
    test opens the gate, so the job can be observed (and cancelled) while
    it is genuinely in flight."""
    _insert_photos(60)
    gate = threading.Event()

    def blocked(photo_id):
        gate.wait(10)
        return True

    monkeypatch.setattr(enrich, "enrich_description", blocked)
    yield gate
    gate.set()
    _wait_for(lambda: client.get("/api/jobs").json()["active"] is None)


def test_jobs_status_is_idle_by_default(client):
    body = client.get("/api/jobs").json()
    assert body["active"] is None
    assert body["cancelling"] is False
    assert body["sync"]["status"] == "idle"
    assert body["faces"]["status"] == "idle"
    assert body["descriptions"]["status"] == "idle"
    assert body["locations"]["status"] == "idle"


def test_running_job_is_visible_to_any_client_and_blocks_every_other_job(client, held_description_job):
    assert client.post("/api/descriptions/refresh").status_code == 200

    body = client.get("/api/jobs").json()
    assert body["active"] == "descriptions"
    assert body["descriptions"]["status"] == "running"

    # Same job again, and every other kind, are refused.
    for path in (
        "/api/descriptions/refresh",
        "/api/locations/refresh",
        "/api/sync",
        "/api/faces/refresh",
        "/api/faces/refresh?full=true",
    ):
        response = client.post(path)
        assert response.status_code == 409, path
        assert "descriptions" in response.json()["detail"]


def test_cancel_stops_the_job_and_frees_the_claim(client, held_description_job):
    client.post("/api/descriptions/refresh")

    body = client.post("/api/jobs/cancel").json()
    assert body["active"] == "descriptions"
    assert body["cancelling"] is True

    # Still held (and still refusing others) until the job has really stopped.
    assert client.post("/api/locations/refresh").status_code == 409

    held_description_job.set()
    _wait_for(lambda: client.get("/api/jobs").json()["active"] is None)

    body = client.get("/api/jobs").json()
    assert body["cancelling"] is False
    assert body["descriptions"]["status"] == "cancelled"
    assert body["descriptions"]["processed"] < 60

    # Ready to run something else again.
    assert client.post("/api/locations/refresh").status_code == 200


def test_cancel_with_nothing_running_is_harmless(client):
    body = client.post("/api/jobs/cancel").json()
    assert body["active"] is None
    assert body["cancelling"] is False
    # ...and a flag left over from that doesn't cancel the next job.
    assert client.post("/api/locations/refresh").status_code == 200
    _wait_for(lambda: client.get("/api/jobs").json()["locations"]["status"] == "completed")


def test_cancelled_sync_skips_the_delete_missing_step(client, tmp_path):
    import asyncio

    from app import sync
    from conftest import sync_and_wait, write_image

    photos = tmp_path / "photos"
    write_image(photos / "keep.jpg")
    write_image(photos / "gone.jpg")
    sync_and_wait(client)

    (photos / "gone.jpg").unlink()

    # Run the sync body directly with a cancel already requested.
    jobs.try_claim(jobs.SYNC)
    jobs.request_cancel()
    sync.progress.try_start()
    asyncio.run(sync._run_sync())

    # Cancelled runs finish without the destructive reconcile...
    status = client.get("/api/sync/status").json()
    assert status["status"] == "cancelled"
    with get_connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM photos").fetchone()["n"] == 2
    # ...and release the claim.
    assert client.get("/api/jobs").json()["active"] is None

    # A normal sync afterwards does remove it.
    sync_and_wait(client)
    with get_connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM photos").fetchone()["n"] == 1
