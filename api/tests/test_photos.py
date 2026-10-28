import io

import pytest
from PIL import Image


def _sample_image_bytes(exif=None) -> bytes:
    buffer = io.BytesIO()
    image = Image.new("RGB", (10, 10), color="red")
    if exif is not None:
        image.save(buffer, format="JPEG", exif=exif)
    else:
        image.save(buffer, format="JPEG")
    return buffer.getvalue()


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


def test_upload_list_and_delete_photo(client):
    response = client.get("/api/photos")
    assert response.status_code == 200
    assert response.json() == {"photos": []}

    files = {"file": ("sample.jpg", _sample_image_bytes(), "image/jpeg")}
    upload_response = client.post("/api/photos", files=files)
    assert upload_response.status_code == 201
    photo = upload_response.json()
    assert photo["filename"] == f"originals/{photo['id']}.jpg"
    assert photo["width"] == 10
    assert photo["height"] == 10
    assert photo["created_at"]
    assert photo["latitude"] is None
    assert photo["longitude"] is None

    list_response = client.get("/api/photos")
    assert len(list_response.json()["photos"]) == 1

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

    final_list = client.get("/api/photos")
    assert final_list.json() == {"photos": []}


def test_upload_extracts_created_at_and_geotag_from_exif(client):
    files = {
        "file": ("geo.jpg", _sample_image_bytes(exif=_exif_with_gps_and_datetime()), "image/jpeg")
    }
    response = client.post("/api/photos", files=files)
    assert response.status_code == 201
    photo = response.json()
    assert photo["created_at"].startswith("2023-05-17T14:22:10")
    assert photo["latitude"] == pytest.approx(40.446194, abs=1e-5)
    assert photo["longitude"] == pytest.approx(-79.948611, abs=1e-5)


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
