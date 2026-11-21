"""storage.find_similar_photos against an in-memory stand-in for Postgres, so
the matching/grouping logic is covered without a database."""

import io
from contextlib import contextmanager

from PIL import Image, ImageDraw

from app import perceptual, storage


def _scene(size, other=False):
    img = Image.new("RGB", size, (30, 60, 120) if not other else (240, 240, 240))
    d = ImageDraw.Draw(img)
    w, h = size
    if other:
        for i in range(0, w, w // 16):
            d.rectangle([i, 0, i + w // 40, h - i // 2], fill=(10, 90, 10))
    else:
        d.rectangle([w * 0.1, h * 0.1, w * 0.5, h * 0.6], fill=(220, 200, 40))
        d.ellipse([w * 0.5, h * 0.4, w * 0.9, h * 0.9], fill=(200, 30, 30))
    return img


def _jpeg(img):
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, photos):
        self.photos = photos

    def execute(self, sql, params=None):
        if "thumbnail" in sql:
            return _Result([{"id": p["id"], "thumbnail": p["thumbnail"]} for p in self.photos if p["id"] in params[0]])
        return _Result([{k: v for k, v in p.items() if k != "thumbnail"} for p in self.photos])


def _photo(pid, filename, img, size_bytes, content_type="image/jpeg", checksum=None):
    thumb = _jpeg(img.copy())
    return {
        "id": pid, "filename": filename, "content_type": content_type, "size_bytes": size_bytes,
        "width": img.width, "height": img.height, "created_at": None, "checksum": checksum,
        "phash": perceptual.dhash_from_bytes(thumb), "thumbnail": thumb,
    }


def _run(monkeypatch, photos):
    @contextmanager
    def fake():
        yield _FakeConn(photos)

    monkeypatch.setattr(storage, "get_connection", fake)
    return storage.find_similar_photos()


def test_resize_and_reformat_are_matched_to_the_largest_original(monkeypatch):
    big, small = _scene((800, 600)), _scene((200, 150))
    photos = [
        _photo("1", "small.jpg", small, 5_000),
        _photo("2", "big.jpg", big, 90_000),
        _photo("3", "big.png", big, 400_000, content_type="image/png"),
        _photo("4", "unrelated.jpg", _scene((800, 600), other=True), 80_000),
    ]
    rows = _run(monkeypatch, photos)
    by_dup = {r["duplicate"]: r for r in rows}
    assert set(by_dup) == {"small.jpg", "big.jpg"}  # big.png wins on pixels tie + format rank
    assert all(r["original"] == "big.png" for r in rows)
    assert by_dup["small.jpg"]["match_type"] == "resize"
    assert by_dup["big.jpg"]["match_type"] == "reformat"
    assert by_dup["small.jpg"]["original_size"] == "800x600"


def test_different_aspect_ratio_is_rejected(monkeypatch):
    photos = [
        _photo("1", "a.jpg", _scene((800, 600)), 90_000),
        _photo("2", "b.jpg", _scene((800, 300)), 40_000),
    ]
    assert _run(monkeypatch, photos) == []


def test_exact_checksum_match_needs_no_hash(monkeypatch):
    photos = [
        {**_photo("1", "a.jpg", _scene((100, 100)), 10, checksum="x"), "phash": None},
        {**_photo("2", "b.jpg", _scene((100, 100)), 10, checksum="x"), "phash": None},
    ]
    rows = _run(monkeypatch, photos)
    assert [(r["original"], r["duplicate"], r["match_type"]) for r in rows] == [("a.jpg", "b.jpg", "exact")]


def test_groups_do_not_chain(monkeypatch):
    # b matches a, c matches b only: c must not be dragged into a's group.
    a, b = _scene((800, 600)), _scene((400, 300))
    c = _scene((400, 300), other=True)
    photos = [_photo("1", "a.jpg", a, 9), _photo("2", "b.jpg", b, 5), _photo("3", "c.jpg", c, 5)]
    rows = _run(monkeypatch, photos)
    assert [(r["original"], r["duplicate"]) for r in rows] == [("a.jpg", "b.jpg")]
