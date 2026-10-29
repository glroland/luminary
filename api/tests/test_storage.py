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
