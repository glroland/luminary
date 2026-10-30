import io
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


def test_sync_applies_exif_orientation_so_portrait_photos_arent_indexed_as_landscape(client, tmp_path):
    # A phone held sideways: the sensor captures landscape pixels (wide x
    # short) and the EXIF Orientation tag says "rotate 90 CW to display" --
    # i.e. this is actually a portrait photo.
    exif = Image.Exif()
    exif[274] = 6  # Orientation: rotate 90 CW
    image = Image.new("RGB", (400, 300), color="red")  # landscape pixel data
    path = tmp_path / "photos" / "portrait.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "JPEG", exif=exif)

    sync_and_wait(client)
    photo = client.get("/api/photos").json()["photos"][0]

    # Width/height in the index should reflect the corrected, as-displayed
    # orientation, not the raw sensor pixel grid.
    assert photo["width"] == 300
    assert photo["height"] == 400

    thumbnail_response = client.get(f"/api/photos/{photo['id']}/thumbnail")
    thumbnail = Image.open(io.BytesIO(thumbnail_response.content))
    assert thumbnail.width < thumbnail.height


def test_sync_generates_description_via_florence_for_new_photos(client, tmp_path, monkeypatch):
    from app import chat

    monkeypatch.setattr(chat, "caption_image", lambda image: "a red square on a plain background")

    write_image(tmp_path / "photos" / "sample.jpg")
    sync_and_wait(client)

    photo = client.get("/api/photos").json()["photos"][0]
    assert photo["description"] == "a red square on a plain background"


def test_sync_leaves_description_null_when_florence_isnt_configured(client, tmp_path):
    # Default fixture behavior (see conftest.py's `client` fixture): sync
    # must still complete normally, just without a description.
    write_image(tmp_path / "photos" / "sample.jpg")

    status = sync_and_wait(client)
    assert status["status"] == "completed"

    photo = client.get("/api/photos").json()["photos"][0]
    assert photo["description"] is None


def test_sync_logs_but_does_not_fail_when_florence_raises_unexpectedly(client, tmp_path, monkeypatch, caplog):
    from app import chat

    def boom(image):
        raise RuntimeError("model inference failed")

    monkeypatch.setattr(chat, "caption_image", boom)

    write_image(tmp_path / "photos" / "sample.jpg")

    with caplog.at_level(logging.WARNING, logger="app.sync"):
        status = sync_and_wait(client)

    assert status["status"] == "completed"
    photo = client.get("/api/photos").json()["photos"][0]
    assert photo["description"] is None
    assert any(
        record.levelno == logging.WARNING and "sample.jpg" in record.message
        for record in caplog.records
    )


def test_sync_backfills_missing_description_without_touching_an_existing_thumbnail(client, tmp_path, monkeypatch):
    # Simulates a library indexed before Florence-2 chat was configured:
    # the thumbnail is already there, only the description is missing.
    # The repeat sync below must add the description without burning the
    # cost of re-thumbnailing every photo.
    write_image(tmp_path / "photos" / "sample.jpg")
    sync_and_wait(client)  # florence unconfigured by default -- no description yet
    photo_id = client.get("/api/photos").json()["photos"][0]["id"]
    thumbnail_before = client.get(f"/api/photos/{photo_id}/thumbnail").content
    assert client.get(f"/api/photos/{photo_id}").json()["description"] is None

    from app import chat

    monkeypatch.setattr(chat, "caption_image", lambda image: "a backfilled description")

    status = sync_and_wait(client)
    assert status["status"] == "completed"
    assert status["updated"] == 1
    assert status["inserted"] == 0

    photo = client.get(f"/api/photos/{photo_id}").json()
    assert photo["description"] == "a backfilled description"
    assert photo["id"] == photo_id  # same row, not a new insert
    assert client.get(f"/api/photos/{photo_id}/thumbnail").content == thumbnail_before


def test_sync_skips_reprocessing_a_fully_populated_unchanged_file(client, tmp_path, monkeypatch):
    from app import chat

    calls = []
    monkeypatch.setattr(chat, "caption_image", lambda image: calls.append(1) or "a description")

    write_image(tmp_path / "photos" / "sample.jpg")
    sync_and_wait(client)
    assert len(calls) == 1

    # Nothing changed and every field is already populated -- the second
    # sync should be a pure no-op for this file: "unchanged", no second
    # call into Florence-2.
    status = sync_and_wait(client)
    assert status["status"] == "completed"
    assert status["inserted"] == 0
    assert status["updated"] == 0
    assert len(calls) == 1


def test_sync_backfills_missing_thumbnail_without_recapturing_an_existing_description(client, tmp_path, monkeypatch):
    # Simulates a thumbnail that failed to generate on a previous sync (a
    # transient read error) while a description somehow already exists --
    # the backfill must regenerate just the thumbnail and leave the
    # existing description alone (in particular: not invoke Florence-2
    # again for it).
    from app import chat
    from app.database import get_connection

    # If this gets called, it means the backfill wrongly re-ran captioning
    # even though a description already existed -- the final assertion
    # below would then see this sentinel instead of the original text.
    monkeypatch.setattr(chat, "caption_image", lambda image: "SHOULD NOT BE CALLED")

    image_path = tmp_path / "photos" / "sample.jpg"
    write_image(image_path)
    sync_and_wait(client)
    photo_id = client.get("/api/photos").json()["photos"][0]["id"]

    with get_connection() as conn:
        conn.execute(
            "UPDATE photos SET thumbnail = NULL, description = 'a pre-existing description' WHERE id = ?",
            (photo_id,),
        )
        conn.commit()
    assert client.get(f"/api/photos/{photo_id}/thumbnail").status_code == 404
    assert client.get(f"/api/photos/{photo_id}").json()["description"] == "a pre-existing description"

    status = sync_and_wait(client)
    assert status["status"] == "completed"
    assert status["updated"] == 1

    photo = client.get(f"/api/photos/{photo_id}").json()
    assert photo["description"] == "a pre-existing description"
    thumbnail_response = client.get(f"/api/photos/{photo_id}/thumbnail")
    assert len(thumbnail_response.content) > 0


def test_sync_regenerates_description_when_a_file_changes_in_place(client, tmp_path, monkeypatch):
    from app import chat

    captions = iter(["first caption", "second caption"])
    monkeypatch.setattr(chat, "caption_image", lambda image: next(captions))

    image_path = tmp_path / "photos" / "sample.jpg"
    write_image(image_path)
    sync_and_wait(client)
    photo_id = client.get("/api/photos").json()["photos"][0]["id"]
    assert client.get(f"/api/photos/{photo_id}").json()["description"] == "first caption"

    write_image(image_path, size=(40, 40))
    sync_and_wait(client)
    assert client.get(f"/api/photos/{photo_id}").json()["description"] == "second caption"


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
