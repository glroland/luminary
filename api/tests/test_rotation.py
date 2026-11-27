import io

import pytest
from conftest import sync_and_wait, write_image
from PIL import Image


def _indexed(client, tmp_path, size=(40, 20)):
    write_image(tmp_path / "photos" / "wide.jpg", size=size)
    sync_and_wait(client)
    return client.get("/api/photos").json()["photos"][0]


def _size(response):
    with Image.open(io.BytesIO(response.content)) as image:
        return image.size


def test_new_photo_is_unrotated(client, tmp_path):
    assert _indexed(client, tmp_path)["rotation"] == 0


@pytest.mark.parametrize("sent,stored", [(45, 45), (-45, 315), (-90, 270), (360, 0), (405, 45), (0, 0)])
def test_rotation_is_normalized_and_stored(client, tmp_path, sent, stored):
    photo = _indexed(client, tmp_path)
    response = client.put(f"/api/photos/{photo['id']}/rotation", json={"rotation": sent})
    assert response.status_code == 200
    assert response.json()["rotation"] == stored
    assert client.get(f"/api/photos/{photo['id']}").json()["rotation"] == stored


@pytest.mark.parametrize("bad", [30, 1, -50])
def test_rotation_must_be_a_multiple_of_45(client, tmp_path, bad):
    photo = _indexed(client, tmp_path)
    assert client.put(f"/api/photos/{photo['id']}/rotation", json={"rotation": bad}).status_code == 422


def test_rotating_a_missing_photo_is_404(client):
    assert client.put("/api/photos/nope/rotation", json={"rotation": 90}).status_code == 404


def test_quarter_turn_swaps_thumbnail_and_original_dimensions(client, tmp_path):
    photo = _indexed(client, tmp_path)
    upright = _size(client.get(f"/api/photos/{photo['id']}/thumbnail"))
    client.put(f"/api/photos/{photo['id']}/rotation", json={"rotation": 90})
    thumb = client.get(f"/api/photos/{photo['id']}/thumbnail")
    assert thumb.headers["content-type"] == "image/jpeg"
    assert _size(thumb) == upright[::-1]
    original = client.get(f"/api/photos/{photo['id']}/original")
    assert original.headers["content-type"] == "image/jpeg"
    assert _size(original) == (20, 40)


def test_quarter_turn_direction_is_clockwise(client, tmp_path):
    # Left half red, right half blue; turned clockwise, red ends up on top.
    path = tmp_path / "photos" / "halves.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (40, 20), "blue")
    image.paste(Image.new("RGB", (20, 20), "red"), (0, 0))
    image.save(path)
    sync_and_wait(client)
    photo = client.get("/api/photos").json()["photos"][0]
    client.put(f"/api/photos/{photo['id']}/rotation", json={"rotation": 90})
    with Image.open(io.BytesIO(client.get(f"/api/photos/{photo['id']}/original").content)) as turned:
        top, bottom = turned.getpixel((10, 5)), turned.getpixel((10, 35))
    assert top[0] > 200 and top[2] < 60
    assert bottom[2] > 200 and bottom[0] < 60


def test_45_degrees_grows_the_canvas_with_a_transparent_corner(client, tmp_path):
    photo = _indexed(client, tmp_path)
    client.put(f"/api/photos/{photo['id']}/rotation", json={"rotation": 45})
    for kind in ("thumbnail", "original"):
        response = client.get(f"/api/photos/{photo['id']}/{kind}")
        assert response.headers["content-type"] == "image/webp"
        with Image.open(io.BytesIO(response.content)) as image:
            assert image.width > 40 and image.height > 20
            assert image.convert("RGBA").getpixel((0, 0))[3] == 0


def test_negative_and_positive_are_opposite_directions(client, tmp_path):
    photo = _indexed(client, tmp_path)
    corner = {}
    for rotation in (45, -45):
        client.put(f"/api/photos/{photo['id']}/rotation", json={"rotation": rotation})
        with Image.open(io.BytesIO(client.get(f"/api/photos/{photo['id']}/original").content)) as image:
            corner[rotation] = image.convert("RGBA").getpixel((image.width // 2, image.height // 2))
    assert corner[45][3] == 255 and corner[-45][3] == 255


def test_thumbnail_etag_changes_with_rotation(client, tmp_path):
    photo = _indexed(client, tmp_path)
    url = f"/api/photos/{photo['id']}/thumbnail"
    before = client.get(url).headers["etag"]
    assert client.get(url, headers={"If-None-Match": before}).status_code == 304
    client.put(f"/api/photos/{photo['id']}/rotation", json={"rotation": 45})
    assert client.get(url, headers={"If-None-Match": before}).status_code == 200
    assert client.get(url).headers["etag"] != before


def test_rotating_back_to_zero_restores_the_original(client, tmp_path):
    photo = _indexed(client, tmp_path)
    url = f"/api/photos/{photo['id']}/thumbnail"
    before = client.get(url).content
    client.put(f"/api/photos/{photo['id']}/rotation", json={"rotation": 135})
    client.put(f"/api/photos/{photo['id']}/rotation", json={"rotation": 0})
    assert client.get(url).content == before


def test_rotation_survives_a_resync_and_never_touches_the_file(client, tmp_path):
    photo = _indexed(client, tmp_path)
    source = tmp_path / "photos" / "wide.jpg"
    before = source.read_bytes()
    client.put(f"/api/photos/{photo['id']}/rotation", json={"rotation": 270})
    sync_and_wait(client)
    assert client.get(f"/api/photos/{photo['id']}").json()["rotation"] == 270
    assert source.read_bytes() == before


def test_animated_photos_cannot_be_rotated(client, tmp_path):
    photo = _indexed(client, tmp_path)
    from app.database import get_connection

    with get_connection() as conn:
        conn.execute("UPDATE photos SET media_kind = 'animated' WHERE id = %s", (photo["id"],))
        conn.commit()
    assert client.put(f"/api/photos/{photo['id']}/rotation", json={"rotation": 90}).status_code == 422
