import io

from PIL import Image

from conftest import sync_and_wait


def _write_heic(path, size=(40, 20)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color="red").save(path, "HEIF")


def test_sync_indexes_heic_with_dimensions_and_thumbnail(client, tmp_path):
    _write_heic(tmp_path / "photos" / "IMG_0486.HEIC")
    sync_and_wait(client)

    [photo] = client.get("/api/photos").json()["photos"]
    assert photo["content_type"] == "image/heic"
    assert (photo["width"], photo["height"]) == (40, 20)

    thumbnail = client.get(f"/api/photos/{photo['id']}/thumbnail")
    assert thumbnail.status_code == 200
    assert Image.open(io.BytesIO(thumbnail.content)).format == "JPEG"


def test_heic_original_is_served_as_jpeg(client, tmp_path):
    _write_heic(tmp_path / "photos" / "IMG_0486.HEIC")
    sync_and_wait(client)
    [photo] = client.get("/api/photos").json()["photos"]

    response = client.get(f"/api/photos/{photo['id']}/original")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    image = Image.open(io.BytesIO(response.content))
    assert image.format == "JPEG"
    assert image.size == (40, 20)


def test_small_heic_is_reencoded_for_chat():
    from app import chat

    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), color="red").save(buffer, "HEIF")

    photo_bytes, content_type = chat._downscale_for_chat(buffer.getvalue(), "image/heic")
    assert content_type == "image/jpeg"
    assert Image.open(io.BytesIO(photo_bytes)).format == "JPEG"


def test_thumbnail_etag_revalidation(client, tmp_path):
    from conftest import sync_and_wait, write_image

    write_image(tmp_path / "a.jpg")
    sync_and_wait(client)
    photo = client.get("/api/photos").json()["photos"][0]
    url = f"/api/photos/{photo['id']}/thumbnail"
    first = client.get(url)
    assert first.status_code == 200
    assert "max-age" in first.headers["cache-control"]
    etag = first.headers["etag"]
    second = client.get(url, headers={"If-None-Match": etag})
    assert second.status_code == 304
    assert client.get(url, headers={"If-None-Match": '"other"'}).status_code == 200
