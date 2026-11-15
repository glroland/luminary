"""Second-stage reranking for semantic search (storage.search_photos).

The first stage (embeddings.py + pgvector) is a bi-encoder: the query and
each description sentence are embedded *separately* and compared by cosine
similarity. That is fast and index-friendly but coarse -- it misses
negation, word order and "X next to Y", and scoring a photo by its single
best sentence rewards matching one concept of a multi-concept query.

This stage re-scores the top candidates with a cross-encoder
(LUMINARY_RERANKER_MODEL, default BAAI/bge-reranker-base), which reads the
query and a photo's whole `description` *together*. Its raw logit goes
through a sigmoid so the result is a 0-1 score like the embedding
similarity it replaces -- the search tools' match levels
(chat._MATCH_LEVELS) and the search list's scores keep their meaning.
Those levels were chosen for cosine similarity though; re-tune them
against real queries with `make eval-search`.

Fail-soft, like every other derived feature here: if
sentence-transformers is missing, the model can't be loaded (e.g. a cache
miss under HF_HUB_OFFLINE=1), reranking is switched off, or scoring
raises, `rerank_scores` returns None and the caller keeps the embedding
order. A load failure is remembered so a broken setup costs one warning,
not a retry on every search. LUMINARY_RERANKER_MODEL="" turns the stage
off entirely.
"""

import logging
import math
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_reranker = None
_load_failed = False


def _load_reranker():
    """Loads and caches the cross-encoder. Returns None (and remembers it)
    when it can't be loaded."""
    global _reranker, _load_failed

    with _lock:
        if _reranker is not None:
            return _reranker
        if _load_failed:
            return None

        from .config import get_reranker_device, get_reranker_model

        model_id = get_reranker_model()
        try:
            from sentence_transformers import CrossEncoder

            logger.info("Loading reranker model %s (first use only)", model_id)
            _reranker = CrossEncoder(model_id, device=get_reranker_device())
        except Exception:
            _load_failed = True
            logger.warning(
                "Could not load reranker model %s -- search falls back to embedding order", model_id, exc_info=True
            )
            return None
        logger.info("Reranker model ready")
        return _reranker


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def rerank_scores(query: str, documents: list[str]) -> Optional[list[float]]:
    """0-1 relevance of each document to `query`, in input order, or None if
    reranking is off or unavailable (the caller then keeps its own order)."""
    from .config import get_reranker_model

    if not documents or not get_reranker_model():
        return None
    model = _load_reranker()
    if model is None:
        return None
    try:
        raw = model.predict([(query, document) for document in documents])
        return [_sigmoid(float(score)) for score in raw]
    except Exception:
        logger.warning("Reranking failed -- search falls back to embedding order", exc_info=True)
        return None
