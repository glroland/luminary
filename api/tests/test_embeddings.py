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


# --- embed_text ----------------------------------------------------------


def test_embed_raises_not_configured_without_api_key(monkeypatch):
    monkeypatch.delenv("LUMINARY_OPENAI_API_KEY", raising=False)

    with pytest.raises(embeddings.EmbeddingNotConfigured):
        embeddings.embed_text("a description")


def test_embed_sends_the_configured_model_and_text(monkeypatch):
    monkeypatch.setenv("LUMINARY_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("LUMINARY_EMBEDDING_MODEL", "my-embedding-model")

    captured = {}

    class FakeEmbeddings:
        def create(self, **kwargs):
            captured.update(kwargs)

            class Datum:
                embedding = [0.1, 0.2, 0.3]

            class Response:
                data = [Datum()]

            return Response()

    class FakeOpenAI:
        def __init__(self, api_key, base_url):
            captured["api_key"] = api_key
            self.embeddings = FakeEmbeddings()

    import openai

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)

    result = embeddings.embed_text("a description")

    assert result.to_list() == pytest.approx([0.1, 0.2, 0.3], abs=1e-3)
    assert captured["api_key"] == "test-key"
    assert captured["model"] == "my-embedding-model"
    assert captured["input"] == "a description"


# --- embed_description -------------------------------------------------


def test_embed_description_is_a_noop_when_not_configured(client, monkeypatch):
    monkeypatch.delenv("LUMINARY_OPENAI_API_KEY", raising=False)
    _insert_photo("photo-1", description="a cat on a sofa")

    assert embeddings.embed_description("photo-1") is False


def test_embed_description_is_a_noop_for_a_missing_photo(client):
    assert embeddings.embed_description("does-not-exist") is False


def test_embed_description_is_a_noop_when_description_is_null(client, monkeypatch):
    monkeypatch.setenv("LUMINARY_OPENAI_API_KEY", "test-key")
    _insert_photo("photo-1", description=None)

    calls = []
    monkeypatch.setattr(embeddings, "embed_text", lambda text: calls.append(text))

    assert embeddings.embed_description("photo-1") is False
    assert calls == []


def test_embed_description_stores_the_embedding(client, monkeypatch):
    from pgvector import HalfVector

    monkeypatch.setenv("LUMINARY_OPENAI_API_KEY", "test-key")
    _insert_photo("photo-1", description="a cat on a sofa")
    monkeypatch.setattr(embeddings, "embed_text", lambda text: HalfVector([0.1, 0.2, 0.3]))

    assert embeddings.embed_description("photo-1") is True

    with get_connection() as conn:
        row = conn.execute(
            "SELECT description_embedding FROM photos WHERE id = %s", ("photo-1",)
        ).fetchone()
    assert row["description_embedding"].to_list() == pytest.approx([0.1, 0.2, 0.3], abs=1e-3)


def test_embed_description_leaves_embedding_untouched_on_failure(client, monkeypatch, caplog):
    import logging

    monkeypatch.setenv("LUMINARY_OPENAI_API_KEY", "test-key")
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
