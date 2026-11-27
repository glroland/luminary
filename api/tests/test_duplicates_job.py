import time

from conftest import sync_and_wait


def _scene(size):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", size, (30, 60, 120))
    d = ImageDraw.Draw(img)
    w, h = size
    d.rectangle([w * 0.1, h * 0.1, w * 0.5, h * 0.6], fill=(220, 200, 40))
    d.ellipse([w * 0.5, h * 0.4, w * 0.9, h * 0.9], fill=(200, 30, 30))
    return img


def _wait(client, timeout=30.0):
    deadline = time.monotonic() + timeout
    status = {}
    while time.monotonic() < deadline:
        status = client.get("/api/duplicates/refresh/status").json()
        if status["status"] in ("completed", "error", "cancelled"):
            return status
        time.sleep(0.05)
    raise AssertionError(f"duplicate search did not finish in time: {status}")


def _index_pair(client, tmp_path):
    photos_dir = tmp_path / "photos"
    photos_dir.mkdir(exist_ok=True)
    _scene((800, 600)).save(photos_dir / "big.jpg", "JPEG")
    _scene((200, 150)).save(photos_dir / "small.png", "PNG")
    sync_and_wait(client)


def test_duplicate_job_finds_similar_photos_and_serves_the_csv(client, tmp_path):
    _index_pair(client, tmp_path)

    started = client.post("/api/duplicates/refresh?mode=similar")
    assert started.status_code == 200
    assert started.json()["status"] == "running"
    status = _wait(client)

    assert status["status"] == "completed"
    assert status["found"] == 1
    assert status["mode"] == "similar"
    assert client.get("/api/jobs").json()["active"] is None

    response = client.get("/api/duplicates/csv")
    assert response.status_code == 200
    rows = response.text.strip().splitlines()
    assert len(rows) == 2
    assert rows[1].startswith('"small.png","big.jpg","resize",')


def test_duplicates_csv_is_404_before_any_run(client):
    assert client.get("/api/duplicates/csv").status_code == 404


def test_duplicate_job_rejects_exact_and_unknown_modes(client):
    assert client.post("/api/duplicates/refresh?mode=exact").status_code == 422
    assert client.post("/api/duplicates/refresh?mode=bogus").status_code == 422


def test_duplicate_job_is_reported_in_jobs_and_blocks_other_jobs(client):
    from app import jobs

    jobs.try_claim(jobs.DUPLICATES)
    assert client.get("/api/jobs").json()["active"] == "duplicates"
    assert client.post("/api/duplicates/refresh").status_code == 409


def test_duplicate_job_can_be_cancelled_and_keeps_no_result(client, tmp_path, monkeypatch):
    from app import storage

    _index_pair(client, tmp_path)

    def slow_search(mode, on_progress=None, should_stop=None):
        while not should_stop():
            time.sleep(0.01)
        raise storage.DuplicateSearchCancelled()

    monkeypatch.setattr(storage, "find_duplicate_pairs", slow_search)
    client.post("/api/duplicates/refresh")
    client.post("/api/jobs/cancel")
    status = _wait(client)

    assert status["status"] == "cancelled"
    assert client.get("/api/jobs").json()["active"] is None
    assert client.get("/api/duplicates/csv").status_code == 404


def test_duplicate_job_failure_surfaces_in_status(client, monkeypatch):
    from app import storage

    def boom(*args, **kwargs):
        raise RuntimeError("database went away")

    monkeypatch.setattr(storage, "find_duplicate_pairs", boom)
    client.post("/api/duplicates/refresh")
    status = _wait(client)

    assert status["status"] == "error"
    assert "database went away" in status["error"]
    assert client.get("/api/jobs").json()["active"] is None
