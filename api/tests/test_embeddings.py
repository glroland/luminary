import pytest

from app import embeddings
from app.database import get_connection


def _insert_photo(photo_id, filename="sample.jpg", description=None):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO photos (id, filename, content_type, size_bytes, created_at, description) "
            "VALUES (%s, %s, 'image/jpeg', 1, '2024-01-01T00:00:00', %s)",
            (photo_id, filename, description),
        )
        conn.commit()


class _FakeEmbedder:
    """Stands in for a loaded sentence-transformers model -- records the
    exact text it was asked to encode (so tests can assert the query
    instruction prefix is/isn't applied) and returns a fixed-size vector."""

    def __init__(self):
        self.calls = []

    def encode(self, text, normalize_embeddings=True):
        self.calls.append(text)
        import numpy as np

        return np.array([0.1, 0.2, 0.3])


# --- _load_embedder --------------------------------------------------------


def test_load_embedder_raises_not_configured_without_sentence_transformers(monkeypatch):
    # sentence-transformers/torch are an optional, multi-GB dependency --
    # this must hold regardless of whether they happen to be installed in
    # whatever environment the suite runs in, so the "not installed" case
    # is simulated at the import boundary (same technique test_chat.py uses
    # for torch/transformers), not relied on as ambient environment state.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sentence_transformers":
            raise ImportError("simulated missing sentence_transformers")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(embeddings, "_embedder", None)

    with pytest.raises(embeddings.EmbeddingNotConfigured):
        embeddings.embed_text("a description")


def test_load_embedder_caches_across_calls(monkeypatch):
    fake = _FakeEmbedder()
    loads = []

    class FakeSentenceTransformer:
        def __init__(self, model_id, device):
            loads.append((model_id, device))

        def encode(self, text, normalize_embeddings=True):
            return fake.encode(text, normalize_embeddings=normalize_embeddings)

    import sentence_transformers

    monkeypatch.setattr(sentence_transformers, "SentenceTransformer", FakeSentenceTransformer)
    monkeypatch.setattr(embeddings, "_embedder", None)

    embeddings.embed_text("first")
    embeddings.embed_text("second")

    assert len(loads) == 1  # loaded once, reused on the second call


# --- embed_text --------------------------------------------------------


def test_embed_text_uses_the_configured_model_and_device(monkeypatch):
    monkeypatch.setenv("LUMINARY_EMBEDDING_MODEL", "my-embedding-model")
    monkeypatch.setenv("LUMINARY_EMBEDDING_DEVICE", "cpu")
    monkeypatch.setattr(embeddings, "_embedder", None)

    captured = {}

    class FakeSentenceTransformer:
        def __init__(self, model_id, device):
            captured["model_id"] = model_id
            captured["device"] = device

        def encode(self, text, normalize_embeddings=True):
            captured["text"] = text
            captured["normalize_embeddings"] = normalize_embeddings
            import numpy as np

            return np.array([0.1, 0.2, 0.3])

    import sentence_transformers

    monkeypatch.setattr(sentence_transformers, "SentenceTransformer", FakeSentenceTransformer)

    result = embeddings.embed_text("a description")

    assert result.to_list() == pytest.approx([0.1, 0.2, 0.3], abs=1e-3)
    assert captured["model_id"] == "my-embedding-model"
    assert captured["device"] == "cpu"
    assert captured["text"] == "a description"
    assert captured["normalize_embeddings"] is True


def test_embed_text_prepends_the_query_instruction_only_for_queries(monkeypatch):
    fake = _FakeEmbedder()
    monkeypatch.setattr(embeddings, "_embedder", fake)

    embeddings.embed_text("a passage of text", is_query=False)
    embeddings.embed_text("a search query", is_query=True)

    assert fake.calls[0] == "a passage of text"
    assert fake.calls[1] == embeddings._QUERY_INSTRUCTION + "a search query"


# --- embed_description -------------------------------------------------


def test_embed_description_is_a_noop_for_a_missing_photo(client):
    assert embeddings.embed_description("does-not-exist") is False


def test_embed_description_is_a_noop_when_description_is_null(client, monkeypatch):
    _insert_photo("photo-1", description=None)

    calls = []
    monkeypatch.setattr(embeddings, "embed_text", lambda text: calls.append(text))

    assert embeddings.embed_description("photo-1") is False
    assert calls == []


def test_embed_description_is_a_noop_when_not_configured(client, monkeypatch):
    _insert_photo("photo-1", description="a cat on a sofa")

    def boom(text):
        raise embeddings.EmbeddingNotConfigured("not configured")

    monkeypatch.setattr(embeddings, "embed_text", boom)

    assert embeddings.embed_description("photo-1") is False


def test_embed_description_stores_the_embedding(client, monkeypatch):
    from pgvector import Vector

    _insert_photo("photo-1", description="a cat on a sofa")
    monkeypatch.setattr(embeddings, "embed_text", lambda text: Vector([0.1, 0.2, 0.3]))

    assert embeddings.embed_description("photo-1") is True

    with get_connection() as conn:
        row = conn.execute(
            "SELECT description_embedding FROM photos WHERE id = %s", ("photo-1",)
        ).fetchone()
    assert row["description_embedding"].to_list() == pytest.approx([0.1, 0.2, 0.3], abs=1e-3)


def test_embed_description_leaves_embedding_untouched_on_failure(client, monkeypatch, caplog):
    import logging

    _insert_photo("photo-1", description="a cat on a sofa")

    def boom(text):
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr(embeddings, "embed_text", boom)

    with caplog.at_level(logging.WARNING, logger="app.embeddings"):
        assert embeddings.embed_description("photo-1") is False

    with get_connection() as conn:
        row = conn.execute(
            "SELECT description_embedding FROM photos WHERE id = %s", ("photo-1",)
        ).fetchone()
    assert row["description_embedding"] is None
    assert any(record.levelno == logging.WARNING for record in caplog.records)
