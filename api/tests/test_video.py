"""Movie support (docs/video-support.md): video.py, indexing, Range serving."""

import io
import shutil
import subprocess
from datetime import datetime, timezone

import pytest
from PIL import Image

from app import storage, sync, video
from app.database import get_connection
from conftest import sync_and_wait, write_image

needs_ffmpeg = pytest.mark.skipif(not video.available(), reason="ffmpeg/ffprobe not installed")


def make_video(path, seconds=2, size="64x48", metadata=(), extra_args=()):
    """A tiny real MP4, written straight onto the (simulated) photos backend."""
    path.parent.mkdir(parents=True, exist_ok=True)
    args = [
        shutil.which("ffmpeg"), "-v", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size={size}:rate=10",
        "-pix_fmt", "yuv420p", *extra_args,
    ]
    for item in metadata:
        args += ["-metadata", item]
    args.append(str(path))
    subprocess.run(args, check=True)


# --- pure helpers (no ffmpeg) ------------------------------------------------


def test_parse_location_reads_iso6709():
    assert video.parse_location("+35.9132-081.5398+1000.500/") == (35.9132, -81.5398)
    assert video.parse_location("-33.8688+151.2093/") == (-33.8688, 151.2093)


@pytest.mark.parametrize("value", [None, "", "garbage", "+0.0000+000.0000/", "+95.0-081.0/", "+35.0-200.0/"])
def test_parse_location_rejects_missing_or_nonsense(value):
    assert video.parse_location(value) == (None, None)


def test_parse_creation_time_keeps_the_wall_clock():
    assert video.parse_creation_time("2019-08-10T14:22:31.000000Z") == datetime(2019, 8, 10, 14, 22, 31, tzinfo=timezone.utc)
    # Apple writes the local time with an offset; the wall clock wins.
    assert video.parse_creation_time("2019-08-10T14:22:31-0400") == datetime(2019, 8, 10, 14, 22, 31, tzinfo=timezone.utc)


@pytest.mark.parametrize("value", [None, "", "nope", "1970-01-01T00:00:00Z", "1904-01-01T00:00:00Z", "2999-01-01T00:00:00Z"])
def test_parse_creation_time_drops_unknown_epoch_and_future(value):
    assert video.parse_creation_time(value) is None


def test_rotation_swaps_reported_size():
    assert video._rotation({"side_data_list": [{"rotation": -90}]}) == -90
    assert video._rotation({"tags": {"rotate": "90"}}) == 90
    assert video._rotation({}) == 0


@pytest.mark.parametrize(
    "header,total,expected",
    [
        ("bytes=0-99", 1000, (0, 99)),
        ("bytes=900-", 1000, (900, 999)),
        ("bytes=-100", 1000, (900, 999)),
        ("bytes=990-5000", 1000, (990, 999)),  # clamped to the file
        ("bytes=-5000", 1000, (0, 999)),
        ("garbage", 1000, None),
        ("bytes=-", 1000, None),
    ],
)
def test_parse_byte_range(header, total, expected):
    assert storage.parse_byte_range(header, total) == expected


@pytest.mark.parametrize("header,total", [("bytes=1000-", 1000), ("bytes=500-100", 1000), ("bytes=-0", 1000), ("bytes=0-", 0)])
def test_parse_byte_range_unsatisfiable(header, total):
    with pytest.raises(storage.RangeNotSatisfiable):
        storage.parse_byte_range(header, total)


def test_select_media_paths(monkeypatch):
    paths = [
        "/r/a.jpg", "/r/b.heic", "/r/b.mov",  # b.mov is b.heic's Live Photo clip
        "/r/c.mp4", "/r/c.jpg",  # an mp4 is never a Live Photo clip
        "/r/d.MOV", "/r/notes.txt", "/r/sub/b.mov",  # other dir: a real video
    ]
    monkeypatch.delenv("LUMINARY_INDEX_VIDEOS", raising=False)
    monkeypatch.delenv("LUMINARY_INDEX_LIVE_PHOTO_VIDEOS", raising=False)
    assert sync.select_media_paths(paths) == [
        "/r/a.jpg", "/r/b.heic", "/r/c.mp4", "/r/c.jpg", "/r/d.MOV", "/r/sub/b.mov",
    ]

    monkeypatch.setenv("LUMINARY_INDEX_LIVE_PHOTO_VIDEOS", "1")
    assert "/r/b.mov" in sync.select_media_paths(paths)

    monkeypatch.setenv("LUMINARY_INDEX_VIDEOS", "0")
    assert sync.select_media_paths(paths) == ["/r/a.jpg", "/r/b.heic", "/r/c.jpg"]


# --- ffmpeg-backed ------------------------------------------------------------


@needs_ffmpeg
def test_probe_and_poster_of_a_real_clip(tmp_path):
    clip = tmp_path / "clip.mp4"
    make_video(
        clip, seconds=2,
        metadata=("creation_time=2019-08-10T14:22:31Z", "location=+35.9132-081.5398/"),
    )
    info = video.probe(str(clip))
    assert info.duration == pytest.approx(2.0, abs=0.3)
    assert (info.width, info.height) == (64, 48)
    assert info.created_at == datetime(2019, 8, 10, 14, 22, 31, tzinfo=timezone.utc)
    assert (info.latitude, info.longitude) == (35.9132, -81.5398)

    frame = video.poster(str(clip), info.duration)
    assert frame.mode == "RGB" and frame.size == (64, 48)


@needs_ffmpeg
def test_probe_rejects_a_file_that_is_not_a_movie(tmp_path):
    bogus = tmp_path / "bogus.mp4"
    bogus.write_bytes(b"not a movie")
    with pytest.raises(video.VideoError):
        video.probe(str(bogus))


@needs_ffmpeg
def test_sync_indexes_a_video(client, tmp_path):
    make_video(
        tmp_path / "photos" / "trip" / "beach.mp4",
        metadata=("creation_time=2019-08-10T14:22:31Z", "location=+35.9132-081.5398/"),
    )
    status = sync_and_wait(client)
    assert status["status"] == "completed" and status["inserted"] == 1

    [photo] = client.get("/api/photos").json()["photos"]
    assert photo["filename"] == "trip/beach.mp4"
    assert photo["media_kind"] == "video"
    assert photo["content_type"] == "video/mp4"
    assert photo["duration_seconds"] == pytest.approx(2.0, abs=0.3)
    assert (photo["width"], photo["height"]) == (64, 48)
    assert photo["created_at"].startswith("2019-08-10T14:22:31")
    assert (photo["latitude"], photo["longitude"]) == (35.9132, -81.5398)
    assert photo["checksum"]

    thumbnail = client.get(f"/api/photos/{photo['id']}/thumbnail")
    assert thumbnail.status_code == 200
    assert Image.open(io.BytesIO(thumbnail.content)).format == "JPEG"

    # Never face/pet scanned.
    with get_connection() as conn:
        row = conn.execute("SELECT face_detection_done, pet_detection_done FROM photos").fetchone()
    assert row["face_detection_done"] and row["pet_detection_done"]


@needs_ffmpeg
def test_resync_leaves_an_unchanged_video_alone_and_removes_a_deleted_one(client, tmp_path):
    clip = tmp_path / "photos" / "a.mp4"
    make_video(clip)
    sync_and_wait(client)
    [first] = client.get("/api/photos").json()["photos"]

    again = sync_and_wait(client)
    assert (again["inserted"], again["updated"], again["deleted"]) == (0, 0, 0)
    [second] = client.get("/api/photos").json()["photos"]
    assert second["id"] == first["id"]

    clip.unlink()
    assert sync_and_wait(client)["deleted"] == 1
    assert client.get("/api/photos").json()["photos"] == []


@needs_ffmpeg
def test_video_falls_back_to_file_date_when_it_has_none(client, tmp_path):
    make_video(tmp_path / "photos" / "undated.mp4")
    sync_and_wait(client)
    [photo] = client.get("/api/photos").json()["photos"]
    assert photo["created_at"] is not None  # the file's modification time


def test_video_is_indexed_without_ffmpeg(client, tmp_path, monkeypatch):
    monkeypatch.setattr(video, "available", lambda: False)
    clip = tmp_path / "photos" / "a.mov"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"\x00" * 2048)

    assert sync_and_wait(client)["status"] == "completed"
    [photo] = client.get("/api/photos").json()["photos"]
    assert photo["media_kind"] == "video"
    assert photo["content_type"] == "video/quicktime"
    assert photo["duration_seconds"] is None
    assert client.get(f"/api/photos/{photo['id']}/thumbnail").status_code == 404


def test_a_corrupt_video_is_indexed_with_metadata_only(client, tmp_path):
    clip = tmp_path / "photos" / "broken.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"not a movie at all")
    status = sync_and_wait(client)
    assert status["status"] == "completed" and status["failed"] == 0
    [photo] = client.get("/api/photos").json()["photos"]
    assert photo["media_kind"] == "video" and photo["duration_seconds"] is None


def test_live_photo_clip_is_not_indexed_as_its_own_video(client, tmp_path):
    write_image(tmp_path / "photos" / "IMG_1.jpg")
    (tmp_path / "photos" / "IMG_1.mov").write_bytes(b"\x00" * 64)
    sync_and_wait(client)
    assert [p["filename"] for p in client.get("/api/photos").json()["photos"]] == ["IMG_1.jpg"]


def test_videos_can_be_switched_off(client, tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINARY_INDEX_VIDEOS", "0")
    (tmp_path / "photos").mkdir()
    (tmp_path / "photos" / "a.mp4").write_bytes(b"\x00" * 64)
    write_image(tmp_path / "photos" / "b.jpg")
    sync_and_wait(client)
    assert [p["filename"] for p in client.get("/api/photos").json()["photos"]] == ["b.jpg"]


def test_a_video_is_never_scanned_for_pets(client, tmp_path, monkeypatch):
    from app import pets

    monkeypatch.setattr(video, "available", lambda: False)
    (tmp_path / "photos").mkdir()
    (tmp_path / "photos" / "a.mp4").write_bytes(b"\x00" * 64)
    write_image(tmp_path / "photos" / "b.jpg")
    sync_and_wait(client)
    assert [p["filename"] for p in pets._photos_to_scan(rescan=True)] == ["b.jpg"]


# --- serving -------------------------------------------------------------------


@pytest.fixture
def video_photo(client, tmp_path, monkeypatch):
    """A synced 'video' whose bytes are known, so ranges can be checked."""
    monkeypatch.setattr(video, "available", lambda: False)
    payload = bytes(range(256)) * 8  # 2048 bytes
    clip = tmp_path / "photos" / "clip.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(payload)
    sync_and_wait(client)
    [photo] = client.get("/api/photos").json()["photos"]
    return photo["id"], payload


def test_original_without_range_is_the_whole_file_and_advertises_ranges(client, video_photo):
    photo_id, payload = video_photo
    response = client.get(f"/api/photos/{photo_id}/original")
    assert response.status_code == 200
    assert response.content == payload
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-type"] == "video/mp4"


def test_range_request_gets_206_with_just_those_bytes(client, video_photo):
    photo_id, payload = video_photo
    response = client.get(f"/api/photos/{photo_id}/original", headers={"Range": "bytes=100-199"})
    assert response.status_code == 206
    assert response.content == payload[100:200]
    assert response.headers["content-range"] == "bytes 100-199/2048"
    assert response.headers["content-length"] == "100"


def test_open_ended_and_suffix_ranges(client, video_photo):
    photo_id, payload = video_photo
    opened = client.get(f"/api/photos/{photo_id}/original", headers={"Range": "bytes=0-"})
    assert opened.status_code == 206 and opened.content == payload
    assert opened.headers["content-range"] == "bytes 0-2047/2048"

    tail = client.get(f"/api/photos/{photo_id}/original", headers={"Range": "bytes=-48"})
    assert tail.status_code == 206 and tail.content == payload[-48:]


def test_unsatisfiable_range_is_416(client, video_photo):
    photo_id, _ = video_photo
    response = client.get(f"/api/photos/{photo_id}/original", headers={"Range": "bytes=5000-"})
    assert response.status_code == 416
    assert response.headers["content-range"] == "bytes */2048"


def test_malformed_range_is_ignored(client, video_photo):
    photo_id, payload = video_photo
    response = client.get(f"/api/photos/{photo_id}/original", headers={"Range": "lines=1-2"})
    assert response.status_code == 200 and response.content == payload


def test_chat_about_a_video_is_not_supported(client, video_photo):
    photo_id, _ = video_photo
    response = client.post(f"/api/photos/{photo_id}/chat", json={"message": "what is this?", "history": []})
    assert response.status_code == 422


# --- animated images -----------------------------------------------------------


def test_animated_gif_is_flagged_and_a_still_is_not(client, tmp_path):
    (tmp_path / "photos").mkdir()
    frames = [Image.new("RGB", (10, 10), color) for color in ("red", "blue", "green")]
    frames[0].save(tmp_path / "photos" / "moving.gif", save_all=True, append_images=frames[1:], duration=100, loop=0)
    Image.new("RGB", (10, 10), "red").save(tmp_path / "photos" / "still.gif")
    write_image(tmp_path / "photos" / "plain.jpg")
    sync_and_wait(client)

    kinds = {p["filename"]: p["media_kind"] for p in client.get("/api/photos").json()["photos"]}
    assert kinds == {"moving.gif": "animated", "still.gif": "image", "plain.jpg": "image"}
    # An animated GIF is still just an image to everything else.
    [moving] = [p for p in client.get("/api/photos").json()["photos"] if p["filename"] == "moving.gif"]
    assert moving["duration_seconds"] is None
    assert client.get(f"/api/photos/{moving['id']}/original").headers["content-type"] == "image/gif"
