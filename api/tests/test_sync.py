import logging

import pytest
from PIL import Image

from conftest import sync_and_wait, write_image


def _exif_with_gps_and_datetime():
    exif = Image.Exif()
    exif[306] = "2023:05:17 14:22:10"  # DateTime
    exif[34853] = {  # GPSInfo
        1: "N",
        2: (40.0, 26.0, 46.3),
        3: "W",
        4: (79.0, 56.0, 55.0),
    }
    return exif


def test_sync_inserts_files_found_in_storage(client, tmp_path):
    write_image(tmp_path / "photos" / "manual-1.jpg")
    write_image(tmp_path / "photos" / "nested" / "manual-2.jpg")

    status = sync_and_wait(client)
    assert status["status"] == "completed"
    assert status["inserted"] == 2
    assert status["updated"] == 0
    assert status["deleted"] == 0

    photos = client.get("/api/photos").json()["photos"]
    assert {p["filename"] for p in photos} == {"manual-1.jpg", "nested/manual-2.jpg"}


def test_sync_ignores_non_image_files(client, tmp_path):
    write_image(tmp_path / "photos" / "manual.jpg")
    (tmp_path / "photos").mkdir(parents=True, exist_ok=True)
    (tmp_path / "photos" / "notes.txt").write_text("not a photo")

    status = sync_and_wait(client)
    assert status["total"] == 1
    assert status["inserted"] == 1


def test_sync_deletes_rows_for_files_removed_from_storage(client, tmp_path):
    image_path = tmp_path / "photos" / "sample.jpg"
    write_image(image_path)
    sync_and_wait(client)
    photo_id = client.get("/api/photos").json()["photos"][0]["id"]

    image_path.unlink()

    status = sync_and_wait(client)
    assert status["status"] == "completed"
    assert status["deleted"] == 1

    assert client.get(f"/api/photos/{photo_id}").status_code == 404


def test_sync_updates_rows_for_files_changed_in_place(client, tmp_path):
    image_path = tmp_path / "photos" / "sample.jpg"
    write_image(image_path)
    sync_and_wait(client)
    photo_id = client.get("/api/photos").json()["photos"][0]["id"]

    write_image(image_path, size=(40, 40))

    status = sync_and_wait(client)
    assert status["status"] == "completed"
    assert status["updated"] == 1
    assert status["inserted"] == 0

    photo = client.get(f"/api/photos/{photo_id}").json()
    assert photo["id"] == photo_id
    assert photo["width"] == 40
    assert photo["height"] == 40


def test_sync_extracts_created_at_and_geotag_from_exif(client, tmp_path):
    write_image(tmp_path / "photos" / "geo.jpg", exif=_exif_with_gps_and_datetime())

    sync_and_wait(client)
    photo = client.get("/api/photos").json()["photos"][0]

    assert photo["created_at"].startswith("2023-05-17T14:22:10")
    assert photo["latitude"] == pytest.approx(40.446194, abs=1e-5)
    assert photo["longitude"] == pytest.approx(-79.948611, abs=1e-5)


def test_sync_logs_a_warning_for_an_unreadable_file_but_still_indexes_it(client, tmp_path, caplog):
    photos_dir = tmp_path / "photos"
    photos_dir.mkdir(parents=True, exist_ok=True)
    (photos_dir / "corrupt.jpg").write_bytes(b"not a real jpeg")

    with caplog.at_level(logging.WARNING, logger="app.sync"):
        status = sync_and_wait(client)

    assert status["status"] == "completed"
    assert status["inserted"] == 1

    photo = client.get("/api/photos").json()["photos"][0]
    assert photo["filename"] == "corrupt.jpg"
    assert photo["width"] is None
    assert photo["height"] is None

    assert any(
        record.levelno == logging.WARNING and "corrupt.jpg" in record.message
        for record in caplog.records
    )


def test_sync_logs_and_reports_an_error_on_failure(client, monkeypatch, caplog):
    from app import sync

    def boom():
        raise RuntimeError("backend unreachable")

    monkeypatch.setattr(sync, "_fs_and_root", boom)

    with caplog.at_level(logging.ERROR, logger="app.sync"):
        status = sync_and_wait(client)

    assert status["status"] == "error"
    assert "backend unreachable" in status["error"]
    assert any(record.levelno == logging.ERROR and record.exc_info for record in caplog.records)


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
