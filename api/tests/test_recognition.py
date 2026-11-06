import io
import time
import uuid

import pytest
from PIL import Image

from app import recognition
from app.database import get_connection
from conftest import refresh_and_wait, sync_and_wait, write_image

FACE_BOX = {"x1": 1.0, "y1": 1.0, "x2": 9.0, "y2": 9.0}


def _incremental_refresh_and_wait(timeout=5.0):
    """Same polling shape as conftest.refresh_and_wait, but calls
    recognition.start_refresh() directly with full=False (the default) --
    the incremental, non-destructive path sync.py's own post-sync trigger,
    and the management page's own "Refresh Faces" button, both use (the
    latter via POST /api/faces/refresh with no `full` param, or
    `full=false` explicitly -- see routers/faces.py). This helper bypasses
    the HTTP layer entirely so these tests don't depend on that routing;
    conftest.refresh_and_wait is the HTTP-level equivalent for the
    destructive `full=true` scope specifically."""
    recognition.start_refresh()
    deadline = time.monotonic() + timeout
    status = {}
    while time.monotonic() < deadline:
        status = recognition.progress.snapshot()
        if status["status"] in ("completed", "error"):
            return status
        time.sleep(0.05)
    raise AssertionError(f"incremental face refresh did not finish in time: {status}")


def _solid_color_embed(image):
    """Deterministic stand-in for the real facenet-pytorch model: the
    average color of the (solid-color, in these tests) image, as a 3-d
    vector. Two faces cropped from same-colored test photos embed
    identically; different colors embed as different/orthogonal vectors --
    enough to exercise the real matching/threshold/mean-pooling logic
    without downloading or running any real model."""
    r, g, b = image.convert("RGB").resize((1, 1)).getpixel((0, 0))
    return [float(r), float(g), float(b)]


def _solid_jpeg_bytes(color):
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), color=color).save(buffer, "JPEG")
    return buffer.getvalue()


def _sync_two_photos(client, tmp_path, monkeypatch, color_a, color_b):
    from app import chat, face_detection

    monkeypatch.setattr(chat, "caption_image", lambda image: "a person")
    monkeypatch.setattr(face_detection, "detect_faces", lambda image: [FACE_BOX])
    monkeypatch.setattr(recognition, "_embed", _solid_color_embed)

    write_image(tmp_path / "photos" / "a.jpg", size=(20, 20), color=color_a)
    write_image(tmp_path / "photos" / "b.jpg", size=(20, 20), color=color_b)
    sync_and_wait(client)

    photos = {p["filename"]: p["id"] for p in client.get("/api/photos").json()["photos"]}
    face_a = client.get(f"/api/photos/{photos['a.jpg']}/faces").json()["faces"][0]
    face_b = client.get(f"/api/photos/{photos['b.jpg']}/faces").json()["faces"][0]
    return photos, face_a, face_b


def test_init_db_backfills_tag_source_for_pre_existing_tags(client):
    """A face tagged before tag_source existed has person_id set but
    tag_source NULL -- database.init_db() (already called once by
    TestClient's lifespan via the `client` fixture) must backfill
    tag_source='manual' for it on every startup, or
    recognition._reference_embeddings() (which only considers
    tag_source='manual' faces) would never see it, silently leaving
    "Refresh Faces" with nothing to match against."""
    from app.database import get_connection, init_db

    person_id = client.post("/api/individuals", json={"name": "Rita"}).json()["id"]
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO photos (id, filename, content_type, size_bytes, created_at) VALUES (%s, %s, %s, %s, %s)",
            ("photo-y", "y.jpg", "image/jpeg", 1, "2024-01-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO faces (id, photo_id, x1, y1, x2, y2, person_id) VALUES (%s, %s, 0, 0, 1, 1, %s)",
            ("face-y", "photo-y", person_id),
        )
        conn.commit()

    faces_before = client.get("/api/photos/photo-y/faces").json()["faces"]
    assert faces_before[0]["tag_source"] is None

    init_db()

    faces_after = client.get("/api/photos/photo-y/faces").json()["faces"]
    assert faces_after[0]["tag_source"] == "manual"


def test_refresh_status_defaults_to_idle(client):
    status = client.get("/api/faces/refresh/status").json()
    assert status == {"status": "idle", "processed": 0, "total": 0, "matched": 0, "error": None, "full": False}


def test_refresh_with_no_manual_tags_does_nothing(client, tmp_path, monkeypatch):
    _sync_two_photos(client, tmp_path, monkeypatch, "red", "red")

    status = _incremental_refresh_and_wait()
    assert status["status"] == "completed"
    assert status["matched"] == 0
    assert status["total"] == 2  # both faces are candidates; nothing to match them against yet


def test_refresh_auto_tags_a_similar_untagged_face(client, tmp_path, monkeypatch):
    photos, face_a, face_b = _sync_two_photos(client, tmp_path, monkeypatch, "red", "red")

    person_id = client.post("/api/individuals", json={"name": "Alice"}).json()["id"]
    client.post(f"/api/faces/{face_a['id']}/tag", json={"person_id": person_id})

    status = _incremental_refresh_and_wait()
    assert status["status"] == "completed"
    assert status["total"] == 1  # face_a is no longer a candidate; only face_b is
    assert status["matched"] == 1

    updated_b = client.get(f"/api/photos/{photos['b.jpg']}/faces").json()["faces"][0]
    assert updated_b["id"] == face_b["id"]
    assert updated_b["person_id"] == person_id
    assert updated_b["tag_source"] == "auto"

    tagged_faces = client.get(f"/api/individuals/{person_id}/faces").json()["faces"]
    assert {(f["id"], f["tag_source"]) for f in tagged_faces} == {
        (face_a["id"], "manual"),
        (face_b["id"], "auto"),
    }

    # The auto match also gets a thumbnail, same as a manual tag.
    assert client.get(f"/api/faces/{face_b['id']}/thumbnail").status_code == 200


def test_refresh_triggers_description_enrichment_only_for_photos_it_auto_tagged(client, tmp_path, monkeypatch):
    from app import enrich

    photos, face_a, face_b = _sync_two_photos(client, tmp_path, monkeypatch, "red", "red")
    person_id = client.post("/api/individuals", json={"name": "Alice"}).json()["id"]
    client.post(f"/api/faces/{face_a['id']}/tag", json={"person_id": person_id})

    calls = []
    monkeypatch.setattr(enrich, "enrich_description", lambda pid: calls.append(pid))

    status = _incremental_refresh_and_wait()
    assert status["matched"] == 1

    # Only b.jpg had a face auto-tagged by this run -- a.jpg's manual tag
    # (above, before the spy was installed) must not show up here.
    assert calls == [photos["b.jpg"]]


def test_refresh_backfills_a_missing_thumbnail_on_a_pre_existing_manual_tag(client, tmp_path, monkeypatch):
    """A face tagged before faces.thumbnail existed has person_id/
    tag_source='manual' set but thumbnail still NULL -- simulated here by
    inserting that row directly rather than going through tag_face (which
    always sets thumbnail today). Refresh must backfill it before it can
    be used as a reference, or this individual could never be auto-matched
    against no matter the threshold."""
    monkeypatch.setattr(recognition, "_embed", _solid_color_embed)

    write_image(tmp_path / "photos" / "a.jpg", size=(20, 20), color="red")
    write_image(tmp_path / "photos" / "b.jpg", size=(20, 20), color="red")
    sync_and_wait(client)  # metadata only -- florence unconfigured by default, no faces detected via sync

    photos = {p["filename"]: p["id"] for p in client.get("/api/photos").json()["photos"]}
    person_id = client.post("/api/individuals", json={"name": "Alice"}).json()["id"]

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO faces (id, photo_id, x1, y1, x2, y2, person_id, tag_source) "
            "VALUES (%s, %s, 1, 1, 9, 9, %s, 'manual')",
            ("face-a", photos["a.jpg"], person_id),
        )
        conn.execute(
            "INSERT INTO faces (id, photo_id, x1, y1, x2, y2) VALUES (%s, %s, 1, 1, 9, 9)",
            ("face-b", photos["b.jpg"]),
        )
        conn.commit()

    assert client.get("/api/faces/face-a/thumbnail").status_code == 404  # pre-existing-tag style: no thumbnail yet

    status = _incremental_refresh_and_wait()
    assert status["matched"] == 1

    assert client.get("/api/faces/face-a/thumbnail").status_code == 200  # backfilled
    updated_b = client.get(f"/api/photos/{photos['b.jpg']}/faces").json()["faces"][0]
    assert updated_b["person_id"] == person_id
    assert updated_b["tag_source"] == "auto"


def test_refresh_logs_the_closest_unmatched_candidate_when_nothing_matches(client, tmp_path, monkeypatch, caplog):
    import logging

    # Not orthogonal (unlike the "does not match a dissimilar face" test
    # below) -- a middling cosine similarity, deliberately below the
    # default threshold but not near zero, so there's an actual near-miss
    # to report rather than a clean non-match.
    photos, face_a, face_b = _sync_two_photos(client, tmp_path, monkeypatch, "red", (50, 240, 10))
    person_id = client.post("/api/individuals", json={"name": "Alice"}).json()["id"]
    client.post(f"/api/faces/{face_a['id']}/tag", json={"person_id": person_id})

    with caplog.at_level(logging.INFO, logger="app.recognition"):
        status = _incremental_refresh_and_wait()

    assert status["matched"] == 0
    # Names the photo *filename* and individual *name* -- not just
    # face_id/person_id UUIDs an operator would otherwise have to go
    # resolve by hand to know what to actually go look at.
    assert any(
        "closest unmatched candidate" in record.message
        and face_b["id"] in record.message
        and "b.jpg" in record.message
        and "Alice" in record.message
        for record in caplog.records
    )


def test_refresh_does_not_match_a_dissimilar_face(client, tmp_path, monkeypatch):
    photos, face_a, face_b = _sync_two_photos(client, tmp_path, monkeypatch, "red", "blue")

    person_id = client.post("/api/individuals", json={"name": "Alice"}).json()["id"]
    client.post(f"/api/faces/{face_a['id']}/tag", json={"person_id": person_id})

    status = _incremental_refresh_and_wait()
    assert status["matched"] == 0

    still_untagged = client.get(f"/api/photos/{photos['b.jpg']}/faces").json()["faces"][0]
    assert still_untagged["person_id"] is None
    assert still_untagged["tag_source"] is None


def test_refresh_never_overwrites_an_existing_tag(client, tmp_path, monkeypatch):
    # Both faces are the same color (would otherwise clearly match), but
    # face_b is already manually tagged to a *different* person before
    # refresh runs. Incremental (full=False) on purpose -- this tests the
    # matching algorithm's own invariant 1, which the button's full=True
    # redetect/purge would make moot (it discards both tags outright
    # before matching even starts -- see the full-refresh tests below).
    photos, face_a, face_b = _sync_two_photos(client, tmp_path, monkeypatch, "red", "red")

    alice = client.post("/api/individuals", json={"name": "Alice"}).json()["id"]
    bob = client.post("/api/individuals", json={"name": "Bob"}).json()["id"]
    client.post(f"/api/faces/{face_a['id']}/tag", json={"person_id": alice})
    client.post(f"/api/faces/{face_b['id']}/tag", json={"person_id": bob})

    status = _incremental_refresh_and_wait()
    assert status["total"] == 0  # neither face is a candidate -- both already tagged
    assert status["matched"] == 0

    unchanged = client.get(f"/api/photos/{photos['b.jpg']}/faces").json()["faces"][0]
    assert unchanged["person_id"] == bob
    assert unchanged["tag_source"] == "manual"


def test_refresh_button_redetects_every_photo_and_purges_every_tag(client, tmp_path, monkeypatch):
    # The button (routers/faces.py -> start_refresh(full=True)) re-detects
    # faces for every indexed photo against the current detection settings
    # first, which -- via storage.replace_faces -- discards *every* tag on
    # the old faces, manual or auto, along with the stale boxes themselves
    # (fresh ids). This is the explicitly-requested, destructive behavior:
    # a face box detected under the old sensitivity is stale, and a tag
    # pointing at it is exactly as stale.
    photos, face_a, face_b = _sync_two_photos(client, tmp_path, monkeypatch, "red", "red")
    alice = client.post("/api/individuals", json={"name": "Alice"}).json()["id"]
    client.post(f"/api/faces/{face_a['id']}/tag", json={"person_id": alice})

    status = refresh_and_wait(client)
    assert status["status"] == "completed"
    # Both photos' faces were redetected (mocked to always find exactly
    # one face each -- see FACE_BOX/_sync_two_photos) and are now fresh,
    # untagged candidates; nothing matched because the manual tag that
    # would have served as a reference was purged along with its box
    # before matching ever ran.
    assert status["total"] == 2
    assert status["matched"] == 0

    redetected_a = client.get(f"/api/photos/{photos['a.jpg']}/faces").json()["faces"][0]
    redetected_b = client.get(f"/api/photos/{photos['b.jpg']}/faces").json()["faces"][0]
    assert redetected_a["id"] != face_a["id"]  # fresh id -- the old box/tag is gone
    assert redetected_a["person_id"] is None
    assert redetected_a["tag_source"] is None
    assert redetected_b["id"] != face_b["id"]
    assert redetected_b["person_id"] is None


def test_refresh_button_regenerates_descriptions_for_every_photo_that_lost_a_tag(client, tmp_path, monkeypatch):
    from app import enrich

    photos, face_a, face_b = _sync_two_photos(client, tmp_path, monkeypatch, "red", "red")
    alice = client.post("/api/individuals", json={"name": "Alice"}).json()["id"]
    client.post(f"/api/faces/{face_a['id']}/tag", json={"person_id": alice})

    calls = []
    monkeypatch.setattr(enrich, "enrich_description", lambda pid: calls.append(pid))

    refresh_and_wait(client)

    # Only a.jpg had a tagged face before the purge -- b.jpg never had one,
    # so there's nothing there for "who's in this photo" to invalidate.
    assert calls == [photos["a.jpg"]]


def test_refresh_button_fails_without_touching_any_data_when_detection_not_configured(client, tmp_path, monkeypatch):
    from app import face_detection

    photos, face_a, face_b = _sync_two_photos(client, tmp_path, monkeypatch, "red", "red")
    alice = client.post("/api/individuals", json={"name": "Alice"}).json()["id"]
    client.post(f"/api/faces/{face_a['id']}/tag", json={"person_id": alice})

    def _raise():
        raise face_detection.FaceDetectionNotConfigured("simulated missing dependency")

    monkeypatch.setattr(face_detection, "ensure_configured", _raise)

    status = refresh_and_wait(client)
    assert status["status"] == "error"

    # Nothing was touched -- the preflight check must fail before any
    # photo's faces are wiped, not discover the missing dependency
    # photo-by-photo after some have already been replaced with nothing.
    unchanged = client.get(f"/api/photos/{photos['a.jpg']}/faces").json()["faces"][0]
    assert unchanged["id"] == face_a["id"]
    assert unchanged["person_id"] == alice
    assert unchanged["tag_source"] == "manual"


def test_sync_triggered_refresh_does_not_redetect_or_purge(client, tmp_path, monkeypatch):
    # sync.py's own post-sync trigger calls recognition.start_refresh()
    # with no args (full=False) -- unlike the button, it must leave
    # existing faces/tags completely alone, only matching still-untagged
    # candidates.
    photos, face_a, face_b = _sync_two_photos(client, tmp_path, monkeypatch, "red", "red")
    alice = client.post("/api/individuals", json={"name": "Alice"}).json()["id"]
    client.post(f"/api/faces/{face_a['id']}/tag", json={"person_id": alice})

    status = _incremental_refresh_and_wait()
    assert status["status"] == "completed"
    assert status["matched"] == 1  # face_b auto-tagged against face_a's reference

    unchanged_a = client.get(f"/api/photos/{photos['a.jpg']}/faces").json()["faces"][0]
    assert unchanged_a["id"] == face_a["id"]  # same id -- never redetected
    assert unchanged_a["person_id"] == alice
    assert unchanged_a["tag_source"] == "manual"


def test_redetect_photo_faces_leaves_existing_faces_alone_when_original_cannot_be_read(client, monkeypatch):
    from app import storage

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO photos (id, filename, content_type, size_bytes, created_at) VALUES (%s, %s, %s, %s, %s)",
            ("photo-missing", "missing.jpg", "image/jpeg", 1, "2024-01-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO faces (id, photo_id, x1, y1, x2, y2) VALUES (%s, %s, 1, 1, 9, 9)",
            ("face-missing", "photo-missing"),
        )
        conn.commit()

    monkeypatch.setattr(storage, "read_original_bytes", lambda metadata: None)

    result = recognition._redetect_photo_faces({"id": "photo-missing", "filename": "missing.jpg"})

    assert result is None
    still_there = client.get("/api/photos/photo-missing/faces").json()["faces"]
    assert [f["id"] for f in still_there] == ["face-missing"]


def test_reference_embeddings_only_use_manually_tagged_faces(client, monkeypatch):
    monkeypatch.setattr(recognition, "_embed", _solid_color_embed)
    person_id = client.post("/api/individuals", json={"name": "Quinn"}).json()["id"]

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO photos (id, filename, content_type, size_bytes, created_at) VALUES (%s, %s, %s, %s, %s)",
            ("photo-x", "x.jpg", "image/jpeg", 1, "2024-01-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO faces (id, photo_id, x1, y1, x2, y2, person_id, thumbnail, tag_source) "
            "VALUES (%s, %s, 0, 0, 1, 1, %s, %s, 'manual')",
            (str(uuid.uuid4()), "photo-x", person_id, _solid_jpeg_bytes((255, 0, 0))),
        )
        # An auto-matched face for the same person, a different color --
        # must NOT be averaged into the reference embedding below.
        conn.execute(
            "INSERT INTO faces (id, photo_id, x1, y1, x2, y2, person_id, thumbnail, tag_source) "
            "VALUES (%s, %s, 0, 0, 1, 1, %s, %s, 'auto')",
            (str(uuid.uuid4()), "photo-x", person_id, _solid_jpeg_bytes((0, 0, 255))),
        )
        conn.commit()

    # JPEG is lossy -- round-tripping a pure (255, 0, 0) 4x4 swatch through
    # it can shift a channel by a shade (e.g. to 254), so compare
    # approximately rather than exactly.
    embeddings = recognition._reference_embeddings()
    assert embeddings.keys() == {person_id}
    r, g, b = embeddings[person_id]
    assert r == pytest.approx(255.0, abs=2.0)
    assert g == pytest.approx(0.0, abs=2.0)
    assert b == pytest.approx(0.0, abs=2.0)


def test_mean_embedding_pools_multiple_manually_tagged_faces():
    assert recognition._mean_embedding([[1.0, 0.0], [3.0, 2.0]]) == [2.0, 1.0]


def test_cosine_similarity():
    assert recognition._cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert recognition._cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert recognition._cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0
