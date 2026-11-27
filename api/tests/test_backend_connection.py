import fsspec
import pytest

from app import backend_connection as bc


def _fake_url_to_fs(monkeypatch, fail_times=0):
    calls = []

    def fake(uri, **kwargs):
        calls.append(uri)
        if len(calls) <= fail_times:
            raise ConnectionError("server unreachable")
        return fsspec.filesystem("memory"), "/photos/"

    monkeypatch.setattr(fsspec.core, "url_to_fs", fake)
    return calls


def test_idle_before_any_connection(client):
    body = client.get("/api/backend/status").json()
    assert body["status"] == "idle"
    assert body["steps"] == []


def test_smb_connect_records_each_step(client, monkeypatch):
    _fake_url_to_fs(monkeypatch)
    monkeypatch.setenv("LUMINARY_PHOTOS_URI", "smb://server/share/photos")
    bc.get_fs_and_root()

    body = client.get("/api/backend/status").json()
    assert body["status"] == "ready"
    assert [s["name"] for s in body["steps"]] == ["authenticate", "verify"]
    assert all(s["status"] == "done" for s in body["steps"])


def test_probe_steps_are_recorded_for_smb(client, monkeypatch):
    _fake_url_to_fs(monkeypatch)
    monkeypatch.setenv("LUMINARY_PHOTOS_URI", "smb://server/share/photos")

    def probe(host, port):
        with bc._step("resolve"):
            pass
        with bc._step("connect"):
            pass

    monkeypatch.setattr(bc, "_probe_host", probe)
    bc.get_fs_and_root()
    names = [s["name"] for s in bc.snapshot()["steps"]]
    assert names == ["resolve", "connect", "authenticate", "verify"]


def test_connection_is_cached(monkeypatch):
    calls = _fake_url_to_fs(monkeypatch)
    monkeypatch.setenv("LUMINARY_PHOTOS_URI", "smb://server/share/photos")
    first = bc.get_fs_and_root()
    second = bc.get_fs_and_root()
    assert first == second
    assert first[1] == "/photos"
    assert len(calls) == 1


def test_failure_is_reported_and_next_call_retries(client, monkeypatch):
    _fake_url_to_fs(monkeypatch, fail_times=1)
    monkeypatch.setenv("LUMINARY_PHOTOS_URI", "smb://server/share/photos")
    with pytest.raises(ConnectionError):
        bc.get_fs_and_root()

    body = client.get("/api/backend/status").json()
    assert body["status"] == "error"
    assert "unreachable" in body["error"]
    assert body["steps"][-1]["status"] == "failed"

    bc.get_fs_and_root()
    assert client.get("/api/backend/status").json()["status"] == "ready"


def test_reconnect_replaces_connection(monkeypatch):
    calls = _fake_url_to_fs(monkeypatch)
    monkeypatch.setenv("LUMINARY_PHOTOS_URI", "smb://server/share/photos")
    fs, _ = bc.get_fs_and_root()
    bc.reconnect(fs)
    assert len(calls) == 2


def test_reconnect_does_not_redo_a_connection_another_thread_replaced(monkeypatch):
    calls = _fake_url_to_fs(monkeypatch)
    monkeypatch.setenv("LUMINARY_PHOTOS_URI", "smb://server/share/photos")
    bc.get_fs_and_root()
    stale = object()
    bc.reconnect(stale)
    assert len(calls) == 1


def test_snapshot_says_reconnecting(monkeypatch):
    _fake_url_to_fs(monkeypatch)
    monkeypatch.setenv("LUMINARY_PHOTOS_URI", "smb://server/share/photos")
    bc.get_fs_and_root()
    bc._reconnecting = True
    bc._status = "connecting"
    assert bc.snapshot()["message"].startswith("Reconnecting")


def test_keepalive_reconnects_when_session_is_dead(monkeypatch):
    calls = _fake_url_to_fs(monkeypatch)
    monkeypatch.setenv("LUMINARY_PHOTOS_URI", "smb://server/share/photos")
    fs, _ = bc.get_fs_and_root()

    def dead(path, **kwargs):
        raise ConnectionError("session gone")

    monkeypatch.setattr(fs, "info", dead)
    bc._keepalive_once()
    assert len(calls) == 2
    assert bc.snapshot()["status"] == "ready"


def test_keepalive_leaves_a_healthy_connection_alone(monkeypatch):
    calls = _fake_url_to_fs(monkeypatch)
    monkeypatch.setenv("LUMINARY_PHOTOS_URI", "smb://server/share/photos")
    fs, _ = bc.get_fs_and_root()
    fs.makedirs("/photos", exist_ok=True)
    bc._keepalive_once()
    assert len(calls) == 1


def test_is_connection_error():
    assert bc.is_connection_error(ConnectionResetError())
    assert bc.is_connection_error(TimeoutError())
    assert not bc.is_connection_error(FileNotFoundError())
    assert not bc.is_connection_error(ValueError("nope"))

    class SMBConnectionClosed(Exception):
        pass

    assert bc.is_connection_error(SMBConnectionClosed())


def test_storage_reconnects_once_when_connection_dies(client, monkeypatch, tmp_path):
    from app import storage

    monkeypatch.setenv("LUMINARY_PHOTOS_URI", "smb://server/share")
    memory = fsspec.filesystem("memory")
    memory.pipe("/photos/a.jpg", b"bytes")
    state = {"calls": 0}

    def fake(uri, **kwargs):
        state["calls"] += 1
        fs = fsspec.filesystem("memory")
        return fs, "/photos"

    monkeypatch.setattr(fsspec.core, "url_to_fs", fake)
    fs, _ = bc.get_fs_and_root()

    real_exists = type(fs).exists
    attempts = {"n": 0}

    def flaky(self, path, **kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ConnectionError("dropped")
        return real_exists(self, path, **kwargs)

    monkeypatch.setattr(type(fs), "exists", flaky)
    assert storage.read_original_bytes({"filename": "a.jpg"}) == b"bytes"
    assert state["calls"] == 2


def test_storage_does_not_reconnect_for_a_missing_file(client, monkeypatch):
    from app import storage

    calls = _fake_url_to_fs(monkeypatch)
    monkeypatch.setenv("LUMINARY_PHOTOS_URI", "smb://server/share")
    assert storage.read_original_bytes({"filename": "nope.jpg"}) is None
    assert len(calls) == 1
