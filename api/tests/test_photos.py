import io

from PIL import Image


def _sample_image_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (10, 10), color="red").save(buffer, format="JPEG")
    return buffer.getvalue()


def test_upload_list_and_delete_photo(client):
    response = client.get("/api/photos")
    assert response.status_code == 200
    assert response.json() == {"photos": []}

    files = {"file": ("sample.jpg", _sample_image_bytes(), "image/jpeg")}
    upload_response = client.post("/api/photos", files=files)
    assert upload_response.status_code == 201
    photo = upload_response.json()
    assert photo["filename"] == "sample.jpg"
    assert photo["width"] == 10
    assert photo["height"] == 10

    list_response = client.get("/api/photos")
    assert len(list_response.json()["photos"]) == 1

    get_response = client.get(f"/api/photos/{photo['id']}")
    assert get_response.status_code == 200

    thumbnail_response = client.get(f"/api/photos/{photo['id']}/thumbnail")
    assert thumbnail_response.status_code == 200

    original_response = client.get(f"/api/photos/{photo['id']}/original")
    assert original_response.status_code == 200

    delete_response = client.delete(f"/api/photos/{photo['id']}")
    assert delete_response.status_code == 204

    final_list = client.get("/api/photos")
    assert final_list.json() == {"photos": []}


def test_upload_rejects_non_image(client):
    files = {"file": ("notes.txt", b"hello", "text/plain")}
    response = client.post("/api/photos", files=files)
    assert response.status_code == 400


def test_get_missing_photo_returns_404(client):
    response = client.get("/api/photos/does-not-exist")
    assert response.status_code == 404


def test_delete_missing_photo_returns_404(client):
    response = client.delete("/api/photos/does-not-exist")
    assert response.status_code == 404
