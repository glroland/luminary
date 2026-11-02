import logging

import pytest
from conftest import sync_and_wait, write_image


def test_list_get_and_delete_photo(client, tmp_path):
    response = client.get("/api/photos")
    assert response.status_code == 200
    assert response.json() == {"photos": []}

    write_image(tmp_path / "photos" / "sample.jpg")
    status = sync_and_wait(client)
    assert status["status"] == "completed"
    assert status["inserted"] == 1

    photo = client.get("/api/photos").json()["photos"][0]
    assert photo["filename"] == "sample.jpg"
    assert photo["width"] == 10
    assert photo["height"] == 10
    assert photo["created_at"]

    get_response = client.get(f"/api/photos/{photo['id']}")
    assert get_response.status_code == 200

    thumbnail_response = client.get(f"/api/photos/{photo['id']}/thumbnail")
    assert thumbnail_response.status_code == 200
    assert thumbnail_response.headers["content-type"] == "image/jpeg"
    assert len(thumbnail_response.content) > 0

    original_response = client.get(f"/api/photos/{photo['id']}/original")
    assert original_response.status_code == 200

    delete_response = client.delete(f"/api/photos/{photo['id']}")
    assert delete_response.status_code == 204

    assert client.get("/api/photos").json() == {"photos": []}


def test_delete_removes_index_entry_but_not_the_source_file(client, tmp_path):
    image_path = tmp_path / "photos" / "sample.jpg"
    write_image(image_path)
    sync_and_wait(client)
    photo = client.get("/api/photos").json()["photos"][0]

    client.delete(f"/api/photos/{photo['id']}")

    # The underlying file on the photos backend must be untouched.
    assert image_path.exists()

    # Since the file is still there, a re-sync indexes it again.
    status = sync_and_wait(client)
    assert status["inserted"] == 1
    assert len(client.get("/api/photos").json()["photos"]) == 1


def test_purge_clears_photos_faces_and_individuals_but_not_the_source_file(client, tmp_path):
    image_path = tmp_path / "photos" / "sample.jpg"
    write_image(image_path)
    sync_and_wait(client)
    assert len(client.get("/api/photos").json()["photos"]) == 1

    person_id = client.post("/api/individuals", json={"name": "Alice"}).json()["id"]

    response = client.delete("/api/purge")
    assert response.status_code == 204

    assert client.get("/api/photos").json() == {"photos": []}
    assert client.get("/api/individuals").json() == {"individuals": []}
    assert client.get(f"/api/individuals/{person_id}/faces").status_code == 404

    # The underlying file on the photos backend must be untouched.
    assert image_path.exists()

    # Since the file is still there, a re-sync indexes it again.
    status = sync_and_wait(client)
    assert status["inserted"] == 1
    assert len(client.get("/api/photos").json()["photos"]) == 1


def test_upload_endpoint_is_disabled(client, caplog):
    with caplog.at_level(logging.WARNING, logger="app.routers.photos"):
        response = client.post(
            "/api/photos", files={"file": ("sample.jpg", b"not-a-real-image", "image/jpeg")}
        )
    assert response.status_code == 403
    assert any("disabled" in record.message.lower() for record in caplog.records)


def test_get_missing_photo_returns_404(client):
    response = client.get("/api/photos/does-not-exist")
    assert response.status_code == 404


def test_delete_missing_photo_returns_404(client):
    response = client.delete("/api/photos/does-not-exist")
    assert response.status_code == 404


def test_get_faces_returns_stored_boxes(client, tmp_path, monkeypatch):
    from app import chat

    monkeypatch.setattr(chat, "caption_image", lambda image: "two people")
    monkeypatch.setattr(chat, "detect_faces", lambda image: [{"x1": 1.0, "y1": 2.0, "x2": 3.0, "y2": 4.0}])

    write_image(tmp_path / "photos" / "sample.jpg")
    sync_and_wait(client)
    photo_id = client.get("/api/photos").json()["photos"][0]["id"]

    response = client.get(f"/api/photos/{photo_id}/faces")
    assert response.status_code == 200
    faces = response.json()["faces"]
    assert len(faces) == 1
    face = faces[0]
    assert isinstance(face["id"], str) and face["id"]
    assert face["person_id"] is None
    assert {k: face[k] for k in ("x1", "y1", "x2", "y2")} == {"x1": 1.0, "y1": 2.0, "x2": 3.0, "y2": 4.0}


def test_get_faces_returns_empty_list_when_no_faces_were_detected(client, tmp_path):
    write_image(tmp_path / "photos" / "sample.jpg")
    sync_and_wait(client)  # florence unconfigured by default -- no faces ever ran
    photo_id = client.get("/api/photos").json()["photos"][0]["id"]

    response = client.get(f"/api/photos/{photo_id}/faces")
    assert response.status_code == 200
    assert response.json() == {"faces": []}


def test_get_faces_for_missing_photo_returns_404(client):
    response = client.get("/api/photos/does-not-exist/faces")
    assert response.status_code == 404


def test_search_photos_rejects_an_empty_query(client):
    response = client.get("/api/photos/search", params={"q": "   "})
    assert response.status_code == 400


def test_search_photos_returns_503_when_not_configured(client, monkeypatch):
    from app import embeddings, storage

    # Not an API-key check anymore (embeddings.py embeds with a local
    # model now, not an OpenAI-compatible endpoint) -- simulate the local
    # model failing to load instead, same as test_embeddings.py's own
    # "not configured" test.
    def boom(text, is_query=False):
        raise embeddings.EmbeddingNotConfigured("simulated missing dependency")

    monkeypatch.setattr(storage.embeddings, "embed_text", boom)

    response = client.get("/api/photos/search", params={"q": "a cat"})

    assert response.status_code == 503


def test_search_photos_returns_results_ordered_by_similarity(client, monkeypatch):
    from app import embeddings, storage
    from app.database import get_connection
    from pgvector import Vector

    def dims(*leading):
        return list(leading) + [0.0] * (embeddings.EMBEDDING_DIMENSIONS - len(leading))

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO photos (id, filename, content_type, size_bytes, created_at, description, "
            "description_embedding) VALUES (%s, %s, 'image/jpeg', 1, '2024-01-01T00:00:00', %s, %s)",
            ("photo-identical", "a.jpg", "a cat on a sofa", Vector(dims(1.0, 0.0))),
        )
        conn.execute(
            "INSERT INTO photos (id, filename, content_type, size_bytes, created_at, description, "
            "description_embedding) VALUES (%s, %s, 'image/jpeg', 1, '2024-01-01T00:00:00', %s, %s)",
            ("photo-orthogonal", "b.jpg", "a mountain at dusk", Vector(dims(0.0, 1.0))),
        )
        conn.commit()
    monkeypatch.setattr(storage.embeddings, "embed_text", lambda text, is_query=False: Vector(dims(1.0, 0.0)))

    response = client.get("/api/photos/search", params={"q": "a cat", "threshold": 0.5})

    assert response.status_code == 200
    results = response.json()["results"]
    assert [r["id"] for r in results] == ["photo-identical"]
    assert results[0]["similarity"] == pytest.approx(1.0, abs=1e-3)
