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


def test_sync_stores_checksum_for_new_photos(client, tmp_path):
    import hashlib

    image_path = tmp_path / "photos" / "sample.jpg"
    write_image(image_path)
    sync_and_wait(client)

    photo = client.get("/api/photos").json()["photos"][0]
    assert photo["checksum"] == hashlib.sha256(image_path.read_bytes()).hexdigest()


def test_sync_recomputes_checksum_when_a_file_changes_in_place(client, tmp_path):
    import hashlib

    image_path = tmp_path / "photos" / "sample.jpg"
    write_image(image_path)
    sync_and_wait(client)
    photo_id = client.get("/api/photos").json()["photos"][0]["id"]
    checksum_before = client.get(f"/api/photos/{photo_id}").json()["checksum"]

    write_image(image_path, size=(40, 40))
    sync_and_wait(client)

    photo = client.get(f"/api/photos/{photo_id}").json()
    assert photo["checksum"] == hashlib.sha256(image_path.read_bytes()).hexdigest()
    assert photo["checksum"] != checksum_before


def test_sync_backfills_missing_checksum_without_touching_an_existing_thumbnail(client, tmp_path):
    # Simulates a library indexed before the checksum column existed: the
    # thumbnail is already there, only the checksum is missing. The repeat
    # sync below must add it without burning the cost of re-thumbnailing.
    import hashlib

    from app.database import get_connection

    image_path = tmp_path / "photos" / "sample.jpg"
    write_image(image_path)
    sync_and_wait(client)
    photo_id = client.get("/api/photos").json()["photos"][0]["id"]
    thumbnail_before = client.get(f"/api/photos/{photo_id}/thumbnail").content

    with get_connection() as conn:
        conn.execute("UPDATE photos SET checksum = NULL WHERE id = %s", (photo_id,))
        conn.commit()
    assert client.get(f"/api/photos/{photo_id}").json()["checksum"] is None

    status = sync_and_wait(client)
    assert status["status"] == "completed"
    assert status["updated"] == 1
    assert status["inserted"] == 0

    photo = client.get(f"/api/photos/{photo_id}").json()
    assert photo["checksum"] == hashlib.sha256(image_path.read_bytes()).hexdigest()
    assert photo["id"] == photo_id  # same row, not a new insert
    assert client.get(f"/api/photos/{photo_id}/thumbnail").content == thumbnail_before


def test_sync_logs_but_does_not_fail_when_checksum_computation_raises(client, tmp_path, monkeypatch, caplog):
    from app import storage

    def boom(fs, path):
        raise RuntimeError("backend unreachable mid-read")

    monkeypatch.setattr(storage, "compute_checksum", boom)
    write_image(tmp_path / "photos" / "sample.jpg")

    with caplog.at_level(logging.WARNING, logger="app.sync"):
        status = sync_and_wait(client)

    assert status["status"] == "completed"
    photo = client.get("/api/photos").json()["photos"][0]
    assert photo["checksum"] is None
    assert any(
        record.levelno == logging.WARNING and "sample.jpg" in record.message
        for record in caplog.records
    )


def test_sync_extracts_created_at_and_geotag_from_exif(client, tmp_path, monkeypatch):
    from app import geocode

    monkeypatch.setattr(geocode, "reverse_geocode", lambda lat, lon: "Pittsburgh, Pennsylvania")
    write_image(tmp_path / "photos" / "geo.jpg", exif=_exif_with_gps_and_datetime())

    sync_and_wait(client)
    photo = client.get("/api/photos").json()["photos"][0]

    assert photo["created_at"].startswith("2023-05-17T14:22:10")
    assert photo["latitude"] == pytest.approx(40.446194, abs=1e-5)
    assert photo["longitude"] == pytest.approx(-79.948611, abs=1e-5)
    assert photo["location_name"] == "Pittsburgh, Pennsylvania"


def test_sync_leaves_location_name_null_without_gps_data(client, tmp_path, monkeypatch):
    from app import geocode

    calls = []
    monkeypatch.setattr(geocode, "reverse_geocode", lambda lat, lon: calls.append((lat, lon)))
    write_image(tmp_path / "photos" / "no-gps.jpg")

    sync_and_wait(client)
    photo = client.get("/api/photos").json()["photos"][0]

    assert photo["location_name"] is None
    assert calls == []


def test_sync_logs_but_does_not_fail_when_reverse_geocoding_raises(client, tmp_path, monkeypatch, caplog):
    from app import geocode

    def boom(lat, lon):
        raise RuntimeError("Nominatim unreachable")

    monkeypatch.setattr(geocode, "reverse_geocode", boom)
    write_image(tmp_path / "photos" / "geo.jpg", exif=_exif_with_gps_and_datetime())

    with caplog.at_level(logging.WARNING, logger="app.sync"):
        status = sync_and_wait(client)

    assert status["status"] == "completed"
    photo = client.get("/api/photos").json()["photos"][0]
    assert photo["latitude"] is not None  # the rest of the row still indexed fine
    assert photo["location_name"] is None
    assert any("reverse-geocode" in record.message for record in caplog.records)


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


def test_sync_triggers_face_refresh_once_it_completes(client, tmp_path, monkeypatch):
    # The `client` fixture stubs sync._trigger_face_refresh to a no-op by
    # default (see conftest.py) so the rest of this file's tests don't also
    # spin up a real background face-matching run. Override it here with a
    # spy, just for this test, to confirm _run_sync actually calls it.
    from app import sync

    calls = []
    monkeypatch.setattr(sync, "_trigger_face_refresh", lambda: calls.append(1))

    write_image(tmp_path / "photos" / "sample.jpg")
    status = sync_and_wait(client)

    assert status["status"] == "completed"
    assert calls == [1]


def test_sync_does_not_trigger_face_refresh_on_failure(client, monkeypatch):
    from app import sync

    def boom():
        raise RuntimeError("backend unreachable")

    calls = []
    monkeypatch.setattr(sync, "_trigger_face_refresh", lambda: calls.append(1))
    monkeypatch.setattr(sync, "_fs_and_root", boom)

    status = sync_and_wait(client)

    assert status["status"] == "error"
    assert calls == []


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


def test_sync_generates_vlm_description_via_florence_for_new_photos(client, tmp_path, monkeypatch):
    from app import chat, face_detection

    monkeypatch.setattr(chat, "caption_image", lambda image: "a red square on a plain background")
    monkeypatch.setattr(face_detection, "detect_faces", lambda image: [])

    write_image(tmp_path / "photos" / "sample.jpg")
    sync_and_wait(client)

    photo = client.get("/api/photos").json()["photos"][0]
    assert photo["vlm_description"] == "a red square on a plain background"


def test_sync_triggers_description_enrichment_once_vlm_description_is_written(client, tmp_path, monkeypatch):
    from app import chat, enrich, face_detection

    monkeypatch.setattr(chat, "caption_image", lambda image: "a red square on a plain background")
    monkeypatch.setattr(face_detection, "detect_faces", lambda image: [])
    calls = []
    monkeypatch.setattr(enrich, "enrich_description", lambda photo_id: calls.append(photo_id))

    write_image(tmp_path / "photos" / "sample.jpg")
    sync_and_wait(client)

    photo_id = client.get("/api/photos").json()["photos"][0]["id"]
    assert calls == [photo_id]


def test_sync_does_not_trigger_description_enrichment_when_captioning_is_not_configured(client, tmp_path, monkeypatch):
    # Default fixture behavior: Florence unconfigured, so _run_florence_pipeline
    # returns before ever writing vlm_description -- enrich_description must
    # not be called over a photo that still has nothing to enrich from.
    from app import enrich

    calls = []
    monkeypatch.setattr(enrich, "enrich_description", lambda photo_id: calls.append(photo_id))

    write_image(tmp_path / "photos" / "sample.jpg")
    sync_and_wait(client)

    assert calls == []


def test_sync_leaves_vlm_description_null_when_florence_isnt_configured(client, tmp_path):
    # Default fixture behavior (see conftest.py's `client` fixture): sync
    # must still complete normally, just without a description.
    write_image(tmp_path / "photos" / "sample.jpg")

    status = sync_and_wait(client)
    assert status["status"] == "completed"

    photo = client.get("/api/photos").json()["photos"][0]
    assert photo["vlm_description"] is None


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
    assert photo["vlm_description"] is None
    assert any(
        record.levelno == logging.WARNING and "sample.jpg" in record.message
        for record in caplog.records
    )


def test_sync_backfills_missing_vlm_description_without_touching_an_existing_thumbnail(client, tmp_path, monkeypatch):
    # Simulates a library indexed before Florence-2 chat was configured:
    # the thumbnail is already there, only the description is missing.
    # The repeat sync below must add the description without burning the
    # cost of re-thumbnailing every photo.
    write_image(tmp_path / "photos" / "sample.jpg")
    sync_and_wait(client)  # florence unconfigured by default -- no description yet
    photo_id = client.get("/api/photos").json()["photos"][0]["id"]
    thumbnail_before = client.get(f"/api/photos/{photo_id}/thumbnail").content
    assert client.get(f"/api/photos/{photo_id}").json()["vlm_description"] is None

    from app import chat, face_detection

    monkeypatch.setattr(chat, "caption_image", lambda image: "a backfilled description")
    monkeypatch.setattr(face_detection, "detect_faces", lambda image: [])

    status = sync_and_wait(client)
    assert status["status"] == "completed"
    assert status["updated"] == 1
    assert status["inserted"] == 0

    photo = client.get(f"/api/photos/{photo_id}").json()
    assert photo["vlm_description"] == "a backfilled description"
    assert photo["id"] == photo_id  # same row, not a new insert
    assert client.get(f"/api/photos/{photo_id}/thumbnail").content == thumbnail_before


def test_sync_skips_reprocessing_a_fully_populated_unchanged_file(client, tmp_path, monkeypatch):
    from app import chat, face_detection

    caption_calls = []
    face_calls = []
    monkeypatch.setattr(chat, "caption_image", lambda image: caption_calls.append(1) or "a description")
    monkeypatch.setattr(face_detection, "detect_faces", lambda image: face_calls.append(1) or [])

    write_image(tmp_path / "photos" / "sample.jpg")
    sync_and_wait(client)
    assert len(caption_calls) == 1
    assert len(face_calls) == 1

    # Nothing changed and every field is already populated -- the second
    # sync should be a pure no-op for this file: "unchanged", no second
    # call into Florence-2 for either the caption or face detection.
    status = sync_and_wait(client)
    assert status["status"] == "completed"
    assert status["inserted"] == 0
    assert status["updated"] == 0
    assert len(caption_calls) == 1
    assert len(face_calls) == 1


def test_sync_backfills_missing_thumbnail_without_recapturing_an_existing_vlm_description(client, tmp_path, monkeypatch):
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
            "UPDATE photos SET thumbnail = NULL, vlm_description = 'a pre-existing description', "
            "face_detection_done = TRUE WHERE id = %s",
            (photo_id,),
        )
        conn.commit()
    assert client.get(f"/api/photos/{photo_id}/thumbnail").status_code == 404
    assert client.get(f"/api/photos/{photo_id}").json()["vlm_description"] == "a pre-existing description"

    status = sync_and_wait(client)
    assert status["status"] == "completed"
    assert status["updated"] == 1

    photo = client.get(f"/api/photos/{photo_id}").json()
    assert photo["vlm_description"] == "a pre-existing description"
    thumbnail_response = client.get(f"/api/photos/{photo_id}/thumbnail")
    assert len(thumbnail_response.content) > 0


def test_sync_regenerates_vlm_description_when_a_file_changes_in_place(client, tmp_path, monkeypatch):
    from app import chat, face_detection

    captions = iter(["first caption", "second caption"])
    monkeypatch.setattr(chat, "caption_image", lambda image: next(captions))
    monkeypatch.setattr(face_detection, "detect_faces", lambda image: [])

    image_path = tmp_path / "photos" / "sample.jpg"
    write_image(image_path)
    sync_and_wait(client)
    photo_id = client.get("/api/photos").json()["photos"][0]["id"]
    assert client.get(f"/api/photos/{photo_id}").json()["vlm_description"] == "first caption"

    write_image(image_path, size=(40, 40))
    sync_and_wait(client)
    assert client.get(f"/api/photos/{photo_id}").json()["vlm_description"] == "second caption"


def test_sync_stores_detected_faces_for_new_photos(client, tmp_path, monkeypatch):
    from app import chat, face_detection
    from app.storage import get_faces

    monkeypatch.setattr(chat, "caption_image", lambda image: "two people on a beach")
    monkeypatch.setattr(
        face_detection,
        "detect_faces",
        lambda image: [{"x1": 1.0, "y1": 2.0, "x2": 3.0, "y2": 4.0}, {"x1": 5.0, "y1": 6.0, "x2": 7.0, "y2": 8.0}],
    )

    write_image(tmp_path / "photos" / "sample.jpg")
    sync_and_wait(client)

    photo_id = client.get("/api/photos").json()["photos"][0]["id"]
    faces = get_faces(photo_id)
    assert {(face["x1"], face["y1"], face["x2"], face["y2"]) for face in faces} == {
        (1.0, 2.0, 3.0, 4.0),
        (5.0, 6.0, 7.0, 8.0),
    }
    assert all(face["person_id"] is None for face in faces)


def test_sync_treats_zero_detected_faces_as_a_successful_result(client, tmp_path, monkeypatch):
    from app import chat, face_detection
    from app.storage import get_faces

    monkeypatch.setattr(chat, "caption_image", lambda image: "an empty room")
    monkeypatch.setattr(face_detection, "detect_faces", lambda image: [])

    write_image(tmp_path / "photos" / "sample.jpg")
    status = sync_and_wait(client)

    assert status["status"] == "completed"
    photo = client.get("/api/photos").json()["photos"][0]
    assert photo["vlm_description"] == "an empty room"  # not left null just because there were no faces
    assert get_faces(photo["id"]) == []


def test_sync_does_not_store_vlm_description_or_faces_when_face_detection_fails(client, tmp_path, monkeypatch, caplog):
    from app import chat, face_detection
    from app.storage import get_faces

    monkeypatch.setattr(chat, "caption_image", lambda image: "a description that should not be saved")

    def boom(image):
        raise RuntimeError("face model inference failed")

    monkeypatch.setattr(face_detection, "detect_faces", boom)

    write_image(tmp_path / "photos" / "sample.jpg")

    with caplog.at_level(logging.WARNING, logger="app.sync"):
        status = sync_and_wait(client)

    # The "quasi-transactional" ordering: captioning succeeded, but since
    # face detection (the next step) failed, description must NOT be
    # persisted either -- otherwise a later sync would see description
    # IS NOT NULL and wrongly skip retrying face detection.
    assert status["status"] == "completed"
    photo = client.get("/api/photos").json()["photos"][0]
    assert photo["vlm_description"] is None
    assert get_faces(photo["id"]) == []
    assert any(
        record.levelno == logging.WARNING and "sample.jpg" in record.message
        for record in caplog.records
    )


def test_sync_replaces_faces_rather_than_accumulating_when_a_file_changes_in_place(client, tmp_path, monkeypatch):
    from app import chat, face_detection
    from app.storage import get_faces

    monkeypatch.setattr(chat, "caption_image", lambda image: "a photo")
    face_results = iter(
        [
            [{"x1": 1.0, "y1": 1.0, "x2": 2.0, "y2": 2.0}],
            [{"x1": 9.0, "y1": 9.0, "x2": 10.0, "y2": 10.0}, {"x1": 11.0, "y1": 11.0, "x2": 12.0, "y2": 12.0}],
        ]
    )
    monkeypatch.setattr(face_detection, "detect_faces", lambda image: next(face_results))

    image_path = tmp_path / "photos" / "sample.jpg"
    write_image(image_path)
    sync_and_wait(client)
    photo_id = client.get("/api/photos").json()["photos"][0]["id"]
    assert len(get_faces(photo_id)) == 1

    write_image(image_path, size=(40, 40))
    sync_and_wait(client)

    faces = get_faces(photo_id)
    assert len(faces) == 2
    assert {(face["x1"], face["y1"], face["x2"], face["y2"]) for face in faces} == {
        (9.0, 9.0, 10.0, 10.0),
        (11.0, 11.0, 12.0, 12.0),
    }


def test_sync_backfills_faces_alongside_a_missing_vlm_description(client, tmp_path, monkeypatch):
    # Same scenario as the description-only backfill test, but checking
    # that faces -- which never got a chance to run at all the first time,
    # since florence was unconfigured -- get populated by the backfill too.
    from app import chat, face_detection
    from app.storage import get_faces

    write_image(tmp_path / "photos" / "sample.jpg")
    sync_and_wait(client)  # florence unconfigured by default
    photo_id = client.get("/api/photos").json()["photos"][0]["id"]
    assert get_faces(photo_id) == []

    monkeypatch.setattr(chat, "caption_image", lambda image: "a backfilled description")
    monkeypatch.setattr(face_detection, "detect_faces", lambda image: [{"x1": 1.0, "y1": 2.0, "x2": 3.0, "y2": 4.0}])

    sync_and_wait(client)

    faces = get_faces(photo_id)
    assert len(faces) == 1
    assert (faces[0]["x1"], faces[0]["y1"], faces[0]["x2"], faces[0]["y2"]) == (1.0, 2.0, 3.0, 4.0)


def test_sync_deletes_faces_for_photos_removed_from_storage(client, tmp_path, monkeypatch):
    from app import chat, face_detection
    from app.storage import get_faces

    monkeypatch.setattr(chat, "caption_image", lambda image: "a photo")
    monkeypatch.setattr(face_detection, "detect_faces", lambda image: [{"x1": 1.0, "y1": 2.0, "x2": 3.0, "y2": 4.0}])

    image_path = tmp_path / "photos" / "sample.jpg"
    write_image(image_path)
    sync_and_wait(client)
    photo_id = client.get("/api/photos").json()["photos"][0]["id"]
    assert get_faces(photo_id) != []

    image_path.unlink()
    sync_and_wait(client)

    assert get_faces(photo_id) == []


def test_sync_status_defaults_to_idle(client):
    status = client.get("/api/sync/status").json()
    assert status == {
        "status": "idle",
        "processed": 0,
        "total": 0,
        "inserted": 0,
        "updated": 0,
        "deleted": 0,
        "failed": 0,
        "error": None,
    }


def test_sync_backfills_exif_for_a_file_that_was_never_decoded(client, tmp_path, monkeypatch):
    # A file indexed while it couldn't be decoded (e.g. HEIC before a
    # decoder existed) has no thumbnail, an index-time created_at, and no
    # GPS -- once it *can* be decoded, a plain re-sync of the unchanged
    # file must fill all of those in, not just the thumbnail.
    from app import geocode
    from app.database import get_connection

    monkeypatch.setattr(geocode, "reverse_geocode", lambda lat, lon: "Pittsburgh, Pennsylvania")
    write_image(tmp_path / "photos" / "geo.jpg", exif=_exif_with_gps_and_datetime())
    sync_and_wait(client)
    photo_id = client.get("/api/photos").json()["photos"][0]["id"]

    with get_connection() as conn:
        conn.execute(
            "UPDATE photos SET thumbnail = NULL, width = NULL, height = NULL, latitude = NULL, "
            "longitude = NULL, location_name = NULL, created_at = '2026-01-01T00:00:00+00:00' WHERE id = %s",
            (photo_id,),
        )
        conn.commit()

    sync_and_wait(client)
    photo = client.get(f"/api/photos/{photo_id}").json()

    assert photo["created_at"].startswith("2023-05-17T14:22:10")
    assert photo["latitude"] == pytest.approx(40.446194, abs=1e-5)
    assert photo["longitude"] == pytest.approx(-79.948611, abs=1e-5)
    assert photo["location_name"] == "Pittsburgh, Pennsylvania"
    assert photo["width"] is not None


def test_sync_thumbnail_backfill_keeps_created_at_when_the_file_has_no_exif_date(client, tmp_path):
    from app.database import get_connection

    write_image(tmp_path / "photos" / "plain.jpg")
    sync_and_wait(client)
    photo_id = client.get("/api/photos").json()["photos"][0]["id"]

    with get_connection() as conn:
        conn.execute(
            "UPDATE photos SET thumbnail = NULL, created_at = '2020-02-02T00:00:00+00:00' WHERE id = %s",
            (photo_id,),
        )
        conn.commit()

    sync_and_wait(client)
    assert client.get(f"/api/photos/{photo_id}").json()["created_at"].startswith("2020-02-02")


def test_sync_skips_a_file_whose_processing_fails_without_failing_the_run_or_deleting_its_row(
    client, tmp_path, monkeypatch
):
    from app import sync

    write_image(tmp_path / "photos" / "good.jpg")
    write_image(tmp_path / "photos" / "bad.jpg")
    sync_and_wait(client)
    before = {p["filename"]: p["id"] for p in client.get("/api/photos").json()["photos"]}
    assert set(before) == {"good.jpg", "bad.jpg"}

    real_sync_one_file = sync._sync_one_file

    def flaky(fs, path, relative_path, existing_row):
        if relative_path == "bad.jpg":
            raise RuntimeError("backend hiccup")
        return real_sync_one_file(fs, path, relative_path, existing_row)

    monkeypatch.setattr(sync, "_sync_one_file", flaky)

    status = sync_and_wait(client)
    assert status["status"] == "completed"
    assert status["failed"] == 1
    assert status["deleted"] == 0
    assert status["processed"] == 2

    after = {p["filename"]: p["id"] for p in client.get("/api/photos").json()["photos"]}
    assert after == before


def test_sync_retries_a_transient_smb_credit_error(client, tmp_path, monkeypatch):
    from app import storage

    monkeypatch.setattr(storage, "_TRANSIENT_RETRY_INITIAL_DELAY", 0)
    write_image(tmp_path / "photos" / "sample.jpg")

    fs, _ = storage._fs_and_root()
    real_info = type(fs).info
    calls = {"count": 0}

    def flaky_info(self, path, **kwargs):
        # Only the per-file lookup, not the directory scan's own calls.
        if str(path).endswith("sample.jpg"):
            calls["count"] += 1
        if str(path).endswith("sample.jpg") and calls["count"] <= 2:
            raise OSError("Request requires 1 credits but only 0 credits are available")
        return real_info(self, path, **kwargs)

    monkeypatch.setattr(type(fs), "info", flaky_info)

    status = sync_and_wait(client)
    assert status["status"] == "completed"
    assert status["failed"] == 0
    assert status["inserted"] == 1
    assert calls["count"] >= 3
