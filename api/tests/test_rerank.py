import pytest

from app import rerank, storage
from test_storage import _insert_photo_with_embedding, _embedding_vector


@pytest.fixture(autouse=True)
def _reset_reranker(monkeypatch):
    monkeypatch.setattr(rerank, "_reranker", None)
    monkeypatch.setattr(rerank, "_load_failed", False)


class _FakeCrossEncoder:
    def __init__(self, logits_by_doc):
        self.logits_by_doc = logits_by_doc
        self.pairs = None

    def predict(self, pairs):
        self.pairs = pairs
        return [self.logits_by_doc[doc] for _, doc in pairs]


def test_rerank_scores_is_none_when_disabled(monkeypatch):
    monkeypatch.setenv("LUMINARY_RERANKER_MODEL", "")
    monkeypatch.setattr(rerank, "_reranker", _FakeCrossEncoder({"a": 0.0}))
    assert rerank.rerank_scores("q", ["a"]) is None


def test_rerank_scores_applies_a_sigmoid(monkeypatch):
    monkeypatch.setenv("LUMINARY_RERANKER_MODEL", "fake")
    fake = _FakeCrossEncoder({"a": 0.0, "b": 10.0, "c": -10.0})
    monkeypatch.setattr(rerank, "_reranker", fake)

    scores = rerank.rerank_scores("q", ["a", "b", "c"])

    assert scores[0] == pytest.approx(0.5)
    assert scores[1] > 0.99 and scores[2] < 0.01
    assert fake.pairs == [("q", "a"), ("q", "b"), ("q", "c")]


def test_rerank_scores_falls_back_when_the_model_cannot_load(monkeypatch):
    monkeypatch.setenv("LUMINARY_RERANKER_MODEL", "missing-model")
    calls = []

    class Boom:
        def __init__(self, *args, **kwargs):
            calls.append(1)
            raise OSError("not in the offline cache")

    import sentence_transformers

    monkeypatch.setattr(sentence_transformers, "CrossEncoder", Boom)

    assert rerank.rerank_scores("q", ["a"]) is None
    assert rerank.rerank_scores("q", ["a"]) is None
    assert len(calls) == 1  # the failure is remembered, not retried per search


def test_search_photos_reorders_by_the_reranker(client, monkeypatch):
    # Embedding order: cat (1.0) > diagonal (0.71); the cross-encoder
    # disagrees and prefers the diagonal one.
    _insert_photo_with_embedding("photo-cat", "a cat on a sofa", 1.0, 0.0)
    _insert_photo_with_embedding("photo-diagonal", "a cat sleeping on a couch", 1.0, 1.0)
    monkeypatch.setenv("LUMINARY_RERANKER_MODEL", "fake")
    monkeypatch.setattr(
        rerank, "_reranker", _FakeCrossEncoder({"a cat on a sofa": -4.0, "a cat sleeping on a couch": 4.0})
    )
    monkeypatch.setattr(storage.embeddings, "embed_text", lambda text, is_query=False: _embedding_vector(1.0, 0.0))

    results = storage.search_photos("a cat", threshold=0.5)

    assert [r["id"] for r in results] == ["photo-diagonal"]  # photo-cat: sigmoid(-4) < 0.5
    assert results[0]["similarity"] > 0.9
    assert results[0]["embedding_similarity"] == pytest.approx(0.7071, abs=1e-3)


def test_search_photos_keeps_candidates_below_the_threshold_for_the_reranker(client, monkeypatch):
    # Cosine 0.71 is under the 0.85 threshold, but the reranker rates it highly.
    _insert_photo_with_embedding("photo-diagonal", "a cat sleeping on a couch", 1.0, 1.0)
    monkeypatch.setenv("LUMINARY_RERANKER_MODEL", "fake")
    monkeypatch.setattr(rerank, "_reranker", _FakeCrossEncoder({"a cat sleeping on a couch": 6.0}))
    monkeypatch.setattr(storage.embeddings, "embed_text", lambda text, is_query=False: _embedding_vector(1.0, 0.0))

    assert [r["id"] for r in storage.search_photos("a cat", threshold=0.85)] == ["photo-diagonal"]


def test_search_photos_without_a_reranker_uses_the_embedding_threshold(client, monkeypatch):
    _insert_photo_with_embedding("photo-diagonal", "a cat sleeping on a couch", 1.0, 1.0)
    monkeypatch.setattr(storage.embeddings, "embed_text", lambda text, is_query=False: _embedding_vector(1.0, 0.0))

    assert storage.search_photos("a cat", threshold=0.85) == []


def test_search_photos_falls_back_to_embedding_order_when_reranking_fails(client, monkeypatch):
    _insert_photo_with_embedding("photo-cat", "a cat on a sofa", 1.0, 0.0)
    _insert_photo_with_embedding("photo-diagonal", "a cat sleeping on a couch", 1.0, 1.0)
    monkeypatch.setenv("LUMINARY_RERANKER_MODEL", "fake")
    monkeypatch.setattr(rerank, "rerank_scores", lambda query, docs: None)
    monkeypatch.setattr(storage.embeddings, "embed_text", lambda text, is_query=False: _embedding_vector(1.0, 0.0))

    results = storage.search_photos("a cat", threshold=0.8)

    assert [r["id"] for r in results] == ["photo-cat"]  # threshold applied to cosine
