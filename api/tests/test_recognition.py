import io
import uuid

import pytest
from PIL import Image

from app import recognition
from app.database import get_connection
from conftest import refresh_and_wait, sync_and_wait, write_image

FACE_BOX = {"x1": 1.0, "y1": 1.0, "x2": 9.0, "y2": 9.0}


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
    from app import chat

    monkeypatch.setattr(chat, "caption_image", lambda image: "a person")
    monkeypatch.setattr(chat, "detect_faces", lambda image: [FACE_BOX])
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
    assert status == {"status": "idle", "processed": 0, "total": 0, "matched": 0, "error": None}


def test_refresh_with_no_manual_tags_does_nothing(client, tmp_path, monkeypatch):
    _sync_two_photos(client, tmp_path, monkeypatch, "red", "red")

    status = refresh_and_wait(client)
    assert status["status"] == "completed"
    assert status["matched"] == 0
    assert status["total"] == 0  # returns before _candidate_faces()/set_total() ever runs


def test_refresh_auto_tags_a_similar_untagged_face(client, tmp_path, monkeypatch):
    photos, face_a, face_b = _sync_two_photos(client, tmp_path, monkeypatch, "red", "red")

    person_id = client.post("/api/individuals", json={"name": "Alice"}).json()["id"]
    client.post(f"/api/faces/{face_a['id']}/tag", json={"person_id": person_id})

    status = refresh_and_wait(client)
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


def test_refresh_does_not_match_a_dissimilar_face(client, tmp_path, monkeypatch):
    photos, face_a, face_b = _sync_two_photos(client, tmp_path, monkeypatch, "red", "blue")

    person_id = client.post("/api/individuals", json={"name": "Alice"}).json()["id"]
    client.post(f"/api/faces/{face_a['id']}/tag", json={"person_id": person_id})

    status = refresh_and_wait(client)
    assert status["matched"] == 0

    still_untagged = client.get(f"/api/photos/{photos['b.jpg']}/faces").json()["faces"][0]
    assert still_untagged["person_id"] is None
    assert still_untagged["tag_source"] is None


def test_refresh_never_overwrites_an_existing_tag(client, tmp_path, monkeypatch):
    # Both faces are the same color (would otherwise clearly match), but
    # face_b is already manually tagged to a *different* person before
    # refresh runs.
    photos, face_a, face_b = _sync_two_photos(client, tmp_path, monkeypatch, "red", "red")

    alice = client.post("/api/individuals", json={"name": "Alice"}).json()["id"]
    bob = client.post("/api/individuals", json={"name": "Bob"}).json()["id"]
    client.post(f"/api/faces/{face_a['id']}/tag", json={"person_id": alice})
    client.post(f"/api/faces/{face_b['id']}/tag", json={"person_id": bob})

    status = refresh_and_wait(client)
    assert status["total"] == 0  # neither face is a candidate -- both already tagged
    assert status["matched"] == 0

    unchanged = client.get(f"/api/photos/{photos['b.jpg']}/faces").json()["faces"][0]
    assert unchanged["person_id"] == bob
    assert unchanged["tag_source"] == "manual"


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
