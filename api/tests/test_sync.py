import io
import time

from PIL import Image


def _write_image(path, size=(10, 10), color="red"):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color=color).save(path, "JPEG")


def _run_sync_and_wait(client, timeout=5.0):
    client.post("/api/sync")
    deadline = time.monotonic() + timeout
    status = {}
    while time.monotonic() < deadline:
        status = client.get("/api/sync/status").json()
        # "idle" means the background thread hasn't started yet (or hasn't
        # called progress.start() yet) -- keep polling past it rather than
        # treating it as done, or a check that lands before the thread gets
        # going would report a false completion.
        if status["status"] in ("completed", "error"):
            return status
        time.sleep(0.05)
    raise AssertionError(f"sync did not finish in time: {status}")


def test_sync_inserts_files_found_in_storage(client, tmp_path):
    _write_image(tmp_path / "photos" / "originals" / "manual-1.jpg")
    _write_image(tmp_path / "photos" / "originals" / "manual-2.jpg")

    status = _run_sync_and_wait(client)
    assert status["status"] == "completed"
    assert status["inserted"] == 2
    assert status["updated"] == 0
    assert status["deleted"] == 0

    photos = client.get("/api/photos").json()["photos"]
    assert {p["filename"] for p in photos} == {
        "originals/manual-1.jpg",
        "originals/manual-2.jpg",
    }


def test_sync_deletes_rows_for_files_removed_from_storage(client, tmp_path):
    buffer = io.BytesIO()
    Image.new("RGB", (10, 10), color="blue").save(buffer, format="JPEG")
    upload = client.post(
        "/api/photos", files={"file": ("sample.jpg", buffer.getvalue(), "image/jpeg")}
    ).json()

    (tmp_path / "photos" / upload["filename"]).unlink()

    status = _run_sync_and_wait(client)
    assert status["status"] == "completed"
    assert status["deleted"] == 1

    assert client.get(f"/api/photos/{upload['id']}").status_code == 404


def test_sync_updates_rows_for_files_changed_in_place(client, tmp_path):
    buffer = io.BytesIO()
    Image.new("RGB", (10, 10), color="green").save(buffer, format="JPEG")
    upload = client.post(
        "/api/photos", files={"file": ("sample.jpg", buffer.getvalue(), "image/jpeg")}
    ).json()

    _write_image(tmp_path / "photos" / upload["filename"], size=(40, 40))

    status = _run_sync_and_wait(client)
    assert status["status"] == "completed"
    assert status["updated"] == 1
    assert status["inserted"] == 0

    photo = client.get(f"/api/photos/{upload['id']}").json()
    assert photo["id"] == upload["id"]
    assert photo["width"] == 40
    assert photo["height"] == 40


def test_sync_status_defaults_to_idle(client):
    status = client.get("/api/sync/status").json()
    assert status == {
        "status": "idle",
        "processed": 0,
        "total": 0,
        "inserted": 0,
        "updated": 0,
        "deleted": 0,
        "error": None,
    }
