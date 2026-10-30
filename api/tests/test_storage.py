import fsspec

from app import storage


def _fake_url_to_fs(monkeypatch):
    calls = []

    def fake(uri, **kwargs):
        calls.append((uri, kwargs))
        return fsspec.filesystem("memory"), "/whatever"

    monkeypatch.setattr(fsspec.core, "url_to_fs", fake)
    return calls


def test_smb_uri_forwards_username_and_password(monkeypatch):
    calls = _fake_url_to_fs(monkeypatch)
    monkeypatch.setenv("LUMINARY_PHOTOS_URI", "smb://server/share/photos")
    monkeypatch.setenv("LUMINARY_PHOTOS_USERNAME", "alice")
    monkeypatch.setenv("LUMINARY_PHOTOS_PASSWORD", "hunter2")

    storage._fs_and_root()

    assert len(calls) == 1
    uri, kwargs = calls[0]
    assert uri == "smb://server/share/photos"
    assert kwargs == {"username": "alice", "password": "hunter2"}


def test_local_path_does_not_forward_credentials(monkeypatch):
    calls = _fake_url_to_fs(monkeypatch)
    monkeypatch.setenv("LUMINARY_PHOTOS_URI", "/data/photos")
    monkeypatch.setenv("LUMINARY_PHOTOS_USERNAME", "alice")
    monkeypatch.setenv("LUMINARY_PHOTOS_PASSWORD", "hunter2")

    storage._fs_and_root()

    assert len(calls) == 1
    _, kwargs = calls[0]
    assert kwargs == {}


def test_s3_uri_does_not_forward_username_password(monkeypatch):
    calls = _fake_url_to_fs(monkeypatch)
    monkeypatch.setenv("LUMINARY_PHOTOS_URI", "s3://my-bucket/photos")
    monkeypatch.setenv("LUMINARY_PHOTOS_USERNAME", "alice")
    monkeypatch.setenv("LUMINARY_PHOTOS_PASSWORD", "hunter2")

    storage._fs_and_root()

    assert len(calls) == 1
    _, kwargs = calls[0]
    assert kwargs == {}


def test_no_credentials_set_forwards_nothing(monkeypatch):
    calls = _fake_url_to_fs(monkeypatch)
    monkeypatch.setenv("LUMINARY_PHOTOS_URI", "smb://server/share/photos")
    monkeypatch.delenv("LUMINARY_PHOTOS_USERNAME", raising=False)
    monkeypatch.delenv("LUMINARY_PHOTOS_PASSWORD", raising=False)

    storage._fs_and_root()

    assert len(calls) == 1
    _, kwargs = calls[0]
    assert kwargs == {}


def _insert_bare_photo_row(photo_id: str) -> None:
    from app.database import get_connection

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO photos (id, filename, content_type, size_bytes, created_at) VALUES (?, ?, ?, ?, ?)",
            (photo_id, "a.jpg", "image/jpeg", 1, "2024-01-01T00:00:00"),
        )
        conn.commit()


def test_replace_faces_stores_and_fully_replaces_boxes(client):
    _insert_bare_photo_row("photo-1")

    storage.replace_faces("photo-1", [{"x1": 1.0, "y1": 2.0, "x2": 3.0, "y2": 4.0}])
    assert storage.get_faces("photo-1") == [{"x1": 1.0, "y1": 2.0, "x2": 3.0, "y2": 4.0}]

    # A second call replaces, rather than appending to, the first set.
    storage.replace_faces("photo-1", [{"x1": 5.0, "y1": 6.0, "x2": 7.0, "y2": 8.0}])
    assert storage.get_faces("photo-1") == [{"x1": 5.0, "y1": 6.0, "x2": 7.0, "y2": 8.0}]

    storage.replace_faces("photo-1", [])
    assert storage.get_faces("photo-1") == []


def test_get_faces_is_scoped_to_its_own_photo(client):
    _insert_bare_photo_row("photo-1")
    _insert_bare_photo_row("photo-2")

    storage.replace_faces("photo-1", [{"x1": 1.0, "y1": 1.0, "x2": 2.0, "y2": 2.0}])
    storage.replace_faces("photo-2", [{"x1": 9.0, "y1": 9.0, "x2": 10.0, "y2": 10.0}])

    assert storage.get_faces("photo-1") == [{"x1": 1.0, "y1": 1.0, "x2": 2.0, "y2": 2.0}]
    assert storage.get_faces("photo-2") == [{"x1": 9.0, "y1": 9.0, "x2": 10.0, "y2": 10.0}]


def test_delete_photo_also_deletes_its_faces(client):
    _insert_bare_photo_row("photo-1")
    storage.replace_faces("photo-1", [{"x1": 1.0, "y1": 2.0, "x2": 3.0, "y2": 4.0}])

    storage.delete_photo("photo-1")

    assert storage.get_faces("photo-1") == []
