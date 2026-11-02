import fsspec
import pytest

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
            "INSERT INTO photos (id, filename, content_type, size_bytes, created_at) VALUES (%s, %s, %s, %s, %s)",
            (photo_id, "a.jpg", "image/jpeg", 1, "2024-01-01T00:00:00"),
        )
        conn.commit()


def _without_id(faces: list[dict]) -> list[dict]:
    """get_faces now also returns `id`/`person_id`/`tag_source` (see
    people.py's manual face tagging and recognition.py's auto-matching) --
    these tests only care about the box coordinates, so strip the
    generated id and assert the tag fields separately/implicitly via this
    helper always expecting them untagged (None)."""
    for face in faces:
        assert face["person_id"] is None
        assert face["tag_source"] is None
    return [{k: v for k, v in face.items() if k not in ("id", "person_id", "tag_source")} for face in faces]


def test_replace_faces_stores_and_fully_replaces_boxes(client):
    _insert_bare_photo_row("photo-1")

    storage.replace_faces("photo-1", [{"x1": 1.0, "y1": 2.0, "x2": 3.0, "y2": 4.0}])
    assert _without_id(storage.get_faces("photo-1")) == [{"x1": 1.0, "y1": 2.0, "x2": 3.0, "y2": 4.0}]

    # A second call replaces, rather than appending to, the first set.
    storage.replace_faces("photo-1", [{"x1": 5.0, "y1": 6.0, "x2": 7.0, "y2": 8.0}])
    assert _without_id(storage.get_faces("photo-1")) == [{"x1": 5.0, "y1": 6.0, "x2": 7.0, "y2": 8.0}]

    storage.replace_faces("photo-1", [])
    assert storage.get_faces("photo-1") == []


def test_get_faces_is_scoped_to_its_own_photo(client):
    _insert_bare_photo_row("photo-1")
    _insert_bare_photo_row("photo-2")

    storage.replace_faces("photo-1", [{"x1": 1.0, "y1": 1.0, "x2": 2.0, "y2": 2.0}])
    storage.replace_faces("photo-2", [{"x1": 9.0, "y1": 9.0, "x2": 10.0, "y2": 10.0}])

    assert _without_id(storage.get_faces("photo-1")) == [{"x1": 1.0, "y1": 1.0, "x2": 2.0, "y2": 2.0}]
    assert _without_id(storage.get_faces("photo-2")) == [{"x1": 9.0, "y1": 9.0, "x2": 10.0, "y2": 10.0}]


def test_delete_photo_also_deletes_its_faces(client):
    _insert_bare_photo_row("photo-1")
    storage.replace_faces("photo-1", [{"x1": 1.0, "y1": 2.0, "x2": 3.0, "y2": 4.0}])

    storage.delete_photo("photo-1")

    assert storage.get_faces("photo-1") == []


def _embedding_vector(*leading_values, dims=1024):
    """A full-width vector(1024) value, as the column itself requires --
    `leading_values` set the first few components (which, padded with
    zeros, are what actually determines cosine similarity in these
    tests), the rest filled with zeros."""
    from pgvector import Vector

    return Vector(list(leading_values) + [0.0] * (dims - len(leading_values)))


def _insert_photo_with_embedding(photo_id: str, description: str, *leading_values) -> None:
    from app.database import get_connection

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO photos (id, filename, content_type, size_bytes, created_at, description, "
            "description_embedding) VALUES (%s, %s, 'image/jpeg', 1, '2024-01-01T00:00:00', %s, %s)",
            (photo_id, f"{photo_id}.jpg", description, _embedding_vector(*leading_values)),
        )
        conn.commit()


def test_search_photos_orders_by_similarity_descending(client, monkeypatch):
    # Identical direction -> similarity 1.0; orthogonal -> 0.0; a 45-degree
    # angle -> ~0.707 -- controllable, exact cosine similarities via a
    # couple of nonzero leading components, zero-padded out to the
    # column's full 1024 width.
    _insert_photo_with_embedding("photo-orthogonal", "a mountain at dusk", 0.0, 1.0)
    _insert_photo_with_embedding("photo-identical", "a cat on a sofa", 1.0, 0.0)
    _insert_photo_with_embedding("photo-diagonal", "a cat sleeping on a couch", 1.0, 1.0)
    monkeypatch.setattr(storage.embeddings, "embed_text", lambda text, is_query=False: _embedding_vector(1.0, 0.0))

    results = storage.search_photos("a cat", threshold=0.0)

    assert [r["id"] for r in results] == ["photo-identical", "photo-diagonal", "photo-orthogonal"]
    assert results[0]["similarity"] == pytest.approx(1.0, abs=1e-3)
    assert results[1]["similarity"] == pytest.approx(0.70710678, abs=1e-3)
    assert results[2]["similarity"] == pytest.approx(0.0, abs=1e-3)


def test_search_photos_filters_out_results_below_threshold(client, monkeypatch):
    _insert_photo_with_embedding("photo-orthogonal", "a mountain at dusk", 0.0, 1.0)
    _insert_photo_with_embedding("photo-identical", "a cat on a sofa", 1.0, 0.0)
    monkeypatch.setattr(storage.embeddings, "embed_text", lambda text, is_query=False: _embedding_vector(1.0, 0.0))

    results = storage.search_photos("a cat", threshold=0.5)

    assert [r["id"] for r in results] == ["photo-identical"]


def test_search_photos_excludes_photos_with_no_embedding_yet(client, monkeypatch):
    _insert_bare_photo_row("photo-no-embedding")
    _insert_photo_with_embedding("photo-identical", "a cat on a sofa", 1.0, 0.0)
    monkeypatch.setattr(storage.embeddings, "embed_text", lambda text, is_query=False: _embedding_vector(1.0, 0.0))

    results = storage.search_photos("a cat", threshold=0.0)

    assert [r["id"] for r in results] == ["photo-identical"]


def test_search_photos_propagates_a_misconfigured_embeddings_backend(client, monkeypatch):
    from app import embeddings

    def boom(text, is_query=False):
        raise embeddings.EmbeddingNotConfigured("not configured")

    monkeypatch.setattr(storage.embeddings, "embed_text", boom)

    with pytest.raises(embeddings.EmbeddingNotConfigured):
        storage.search_photos("a cat", threshold=0.5)
