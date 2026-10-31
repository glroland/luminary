import logging

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
