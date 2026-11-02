"""Generates and stores `photos.description_embedding` -- a vector
embedding of `photos.description` (enrich.py's LLM-composed rewrite, never
the raw Florence-2 `vlm_description`) used for similarity search: "find
photos whose description reads most like this one", via pgvector's
nearest-neighbor operators (storage.find_similar_photos,
GET /api/photos/{id}/similar), and for the sidebar's free-text search
(storage.search_photos, GET /api/photos/search).

--- Model and dimension choice ---

Embeds with a local Hugging Face sentence-embedding model
(LUMINARY_EMBEDDING_MODEL, config.get_embedding_model -- default
BAAI/bge-large-en-v1.5), loaded once per process and run in-process via
sentence-transformers -- deliberately *not* a call to an OpenAI-
*compatible* embeddings endpoint. LUMINARY_OPENAI_* is configured for
*chat completions* (chat.py's "openai" backend, enrich.py's description
rewrite) against whatever server LUMINARY_OPENAI_BASE_URL points at -- a
self-hosted OpenAI-compatible gateway (vLLM, Ollama, a local proxy, a
small sandboxed model server) commonly exposes chat completions only,
with no `/embeddings` endpoint at all, and even one that does expose it
may back it with a different, incompatible model than whatever generated
the embeddings already stored in this database -- there's no guarantee an
arbitrary OpenAI-compatible server's embedding model matches the one this
column was indexed with, or that it has one at all. Running the embedding
model ourselves removes that dependency entirely: the exact same weights,
loaded once (see _load_embedder below) and cached for the life of the
process, generate both a photo's stored embedding and every search
query's, so the two are always comparable regardless of how (or whether)
LUMINARY_OPENAI_* is even configured.

BAAI/bge-large-en-v1.5 is a strong, widely-benchmarked open embedding
model (a consistent top scorer on the MTEB retrieval leaderboard among
similarly-sized models) at 1,024 dimensions. That comfortably fits under
pgvector's plain `vector` type's HNSW/IVFFlat index cap of 2,000
dimensions (see pgvector's own README), so `photos.description_embedding`
is declared `vector(1024)` in db/create_tables.sql, not `halfvec` -- at
this dimension count there's no need to trade precision (halfvec's 16-bit
floats) for indexability the way the previous OpenAI
text-embedding-3-large choice (3,072 dimensions) required. A model with a
different native dimension count needs a schema change (the column/index
are declared at a fixed width), not just an env var change -- update
db/create_tables.sql, database.py's init_db(), and db/update.sql
together, same as any other schema change (see database.py's module docs
on why there's no migrations framework).

BGE's retrieval models are trained *asymmetrically*: a short instruction
is prepended to a *query* before embedding it, but never to the *passage*
being searched over (here, a photo's description) -- see the model
card's own usage example. `embed_text(text, is_query=...)` takes that
explicitly rather than guessing it from the caller: embed_description
below always embeds a passage (is_query=False, its default),
storage.search_photos always embeds a user's free-text query
(is_query=True). Getting this backwards doesn't raise -- it just quietly
degrades match quality, since both are still valid 1,024-dimensional
vectors -- so don't drop the parameter at either call site.

Embeddings are L2-normalized (sentence-transformers'
`normalize_embeddings=True`) so cosine distance (pgvector's `<=>`
operator, used throughout storage.py) is a meaningful metric for them --
BGE's own usage recommendations assume normalized embeddings, same
reasoning recognition.py already documents for facenet-pytorch's face
embeddings.

The model's weights must already be present in this container's Hugging
Face cache (HF_HOME, api/Containerfile's pre-download RUN step) before
_load_embedder ever runs -- HF_HUB_OFFLINE=1 (also set there) makes a
cache miss fail loudly (surfaces as a bare exception, not swallowed into
EmbeddingNotConfigured -- see _load_embedder below) instead of silently
reaching out to huggingface.co from a deployed container. Changing
LUMINARY_EMBEDDING_MODEL means rebuilding the image with a matching
`--build-arg` (see the Containerfile/Makefile) or this will always fail
at runtime.

--- When this runs ---

embed_description(photo_id) is called from enrich.enrich_description()
immediately after a new `description` is stored, for every one of that
function's own call sites (a sync's first description, tag/untag,
"Refresh Faces" auto-tagging, deleting an individual, and the bulk
"Refresh Descriptions" job -- see enrich.py's module docstring for the
full list). The embedding always describes the *current* description
text, so it's regenerated in lockstep with it rather than on any separate
schedule -- there's no case where `description` changes without
`description_embedding` being refreshed alongside it, and no case where
this runs without `description` having just changed. This is also how an
already-populated library picks up a *new* embedding model after one is
swapped in (changing LUMINARY_EMBEDDING_MODEL, or this module's default):
running "Refresh Descriptions" re-embeds every photo's existing
`description` text under the new model, with no separate backfill job
needed.

Best-effort, same shape as every other derived field in this app: a
failure (missing torch/sentence-transformers, a cache miss under
HF_HUB_OFFLINE=1, pgvector not installed -- see database.py's init_db())
is logged and leaves description_embedding as whatever it already was,
never raised to enrich_description.
"""

import logging
import threading

from .database import get_connection

logger = logging.getLogger(__name__)

# BAAI/bge-large-en-v1.5's native output size -- see this module's
# docstring for why this is never shortened, and why the column is
# declared `vector`, not `halfvec`, at this dimension count.
EMBEDDING_DIMENSIONS = 1024

# BGE's own usage instructions: prepend this to a *query* before embedding
# it, never to a passage/document -- see this module's docstring.
_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


class EmbeddingNotConfigured(Exception):
    """Raised when the local embedding model's dependencies aren't
    installed -- mirrors chat.ChatNotConfigured/
    recognition.FaceRecognitionNotConfigured exactly, including the same
    "only ImportError, nothing else" rule (see _load_embedder below): a
    real load failure (e.g. a Hugging Face cache miss under
    HF_HUB_OFFLINE=1) must propagate as a bare exception, not get
    misreported as "not configured"."""


_embedder_lock = threading.Lock()
_embedder = None


def _load_embedder():
    """Loads and caches LUMINARY_EMBEDDING_MODEL for the life of this
    process -- same lazy-import/module-global-cache/lock shape as
    chat._load_florence()/recognition._load_embedder(). Only ImportError
    (missing torch/sentence-transformers) becomes EmbeddingNotConfigured;
    any other exception from SentenceTransformer(...) (e.g. the offline
    cache-miss OSError HF_HUB_OFFLINE=1 raises for a model that was never
    pre-downloaded into the image) is left to propagate as-is, same
    reasoning chat._load_florence() documents for Florence-2."""
    global _embedder

    with _embedder_lock:
        if _embedder is not None:
            return _embedder

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingNotConfigured(
                "Description embeddings require the torch/sentence-transformers dependencies to be installed."
            ) from exc

        from .config import get_embedding_device, get_embedding_model

        model_id = get_embedding_model()
        device = get_embedding_device()

        logger.info("Loading embedding model %s on %s (first use only)", model_id, device)
        _embedder = SentenceTransformer(model_id, device=device)
        logger.info("Embedding model ready")
        return _embedder


def embed_text(text: str, is_query: bool = False):
    """Embeds arbitrary text with the local model _load_embedder loads --
    shared by embed_description below (embedding a photo's `description`,
    is_query=False) and storage.search_photos (embedding a user's
    free-text search query, is_query=True), since both need the exact
    same embedding space to be comparable via pgvector's `<=>` operator.
    `is_query` controls BGE's asymmetric query instruction -- see this
    module's docstring. Raises EmbeddingNotConfigured, or whatever
    sentence-transformers/torch itself raises; unlike embed_description,
    this is not best-effort -- both callers want a real error to surface
    (a 503/502 to the API client), not a silently skipped embedding."""
    from pgvector import Vector

    model = _load_embedder()
    input_text = _QUERY_INSTRUCTION + text if is_query else text
    embedding = model.encode(input_text, normalize_embeddings=True)
    return Vector(embedding.tolist())


def embed_description(photo_id: str) -> bool:
    """Best-effort: (re)generate and store photos.description_embedding
    from the photo's current `description`. Never raises. Returns whether
    an embedding was actually (re)generated -- mirrors
    enrich.enrich_description()'s own return value, though
    enrich_description doesn't currently use it (an embedding failure
    shouldn't flip "was the description itself updated" to false -- the
    two are independent outcomes)."""
    with get_connection() as conn:
        photo = conn.execute("SELECT description FROM photos WHERE id = %s", (photo_id,)).fetchone()
    if not photo or not photo["description"]:
        return False

    try:
        embedding = embed_text(photo["description"])
    except EmbeddingNotConfigured as exc:
        logger.debug("Skipping description embedding for photo %s: %s", photo_id, exc)
        return False
    except Exception:
        logger.warning("Could not embed description for photo %s", photo_id, exc_info=True)
        return False

    # The write-back needs its own try/except, same as embed_text() above
    # -- a database-side failure here (most commonly description_embedding
    # not existing as a real pgvector column at all, e.g. the extension
    # was never installed on this Postgres server) must degrade the same
    # best-effort way a failure to *compute* the embedding already does,
    # not propagate out of this "never raises" function and, in turn, out
    # of enrich.enrich_description's own best-effort call to it.
    try:
        with get_connection() as conn:
            conn.execute("UPDATE photos SET description_embedding = %s WHERE id = %s", (embedding, photo_id))
            conn.commit()
    except Exception:
        logger.warning("Could not store description embedding for photo %s", photo_id, exc_info=True)
        return False
    return True
