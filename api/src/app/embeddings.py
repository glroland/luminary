"""Generates and stores `photos.description_embedding` -- a vector
embedding of `photos.description` (enrich.py's LLM-composed rewrite, never
the raw Florence-2 `vlm_description`) used for similarity search: "find
photos whose description reads most like this one", via pgvector's
nearest-neighbor operators (storage.find_similar_photos,
GET /api/photos/{id}/similar).

--- Model and dimension choice ---

Embeds with OpenAI's text-embedding-3-large (LUMINARY_EMBEDDING_MODEL,
config.get_embedding_model) at its full native 3072 dimensions --
deliberately *not* shortened via the OpenAI embeddings API's own
`dimensions` parameter (which supports shrinking this model's output via
Matryoshka representation learning, e.g. to 1024 or 1536 -- OpenAI's own
docs show this degrades gracefully, but it still always costs some
retrieval quality versus the untruncated embedding; "highest fidelity"
here means not throwing any of it away).

Keeping the full, untruncated embedding while still being able to index it
is why `photos.description_embedding` is declared `halfvec(3072)` in
db/create_tables.sql, not `vector(3072)`: pgvector's `vector` type only
supports HNSW/IVFFlat indexes up to 2,000 dimensions (see pgvector's own
README) -- a plain `vector(3072)` column could still *store* the value
(vector's storage cap is far higher, 16,000 dimensions) but could never be
indexed, so every similarity query would silently become a full table
scan as the library grows. `halfvec` (16-bit floats instead of `vector`'s
32-bit) raises that indexable cap to 4,000 dimensions, comfortably
covering this model's 3,072 with no truncation needed. The halved storage
per row is an incidental side effect of that choice, not the reason for
it.

text-embedding-3-large is OpenAI's highest-fidelity general-purpose text
embedding model as of this writing. If LUMINARY_OPENAI_BASE_URL points at
a different OpenAI-*compatible* server instead, set LUMINARY_EMBEDDING_
MODEL to whatever that server calls its own best embedding model -- but
note EMBEDDING_DIMENSIONS below is specific to text-embedding-3-large's
own native size. A model with a different native dimension count needs a
schema change (the column/index are declared at a fixed width), not just
an env var change: there's no migrations framework in this app (see
database.py's module docs) -- update db/create_tables.sql, database.py's
init_db(), and db/update.sql together, same as any other schema change.

--- When this runs ---

embed_description(photo_id) is called from enrich.enrich_description()
immediately after a new `description` is stored, for every one of that
function's own call sites (a sync's first description, tag/untag, "Refresh
Faces" auto-tagging, deleting an individual, and the bulk "Refresh
Descriptions" job -- see enrich.py's module docstring for the full list).
The embedding always describes the *current* description text, so it's
regenerated in lockstep with it rather than on any separate schedule --
there's no case where `description` changes without `description_embedding`
being refreshed alongside it, and no case where this runs without
`description` having just changed.

Best-effort, same shape as every other derived field in this app: a
failure (missing config, network error, pgvector not installed -- see
database.py's init_db()) is logged and leaves description_embedding as
whatever it already was, never raised to enrich_description.
"""

import logging

from .database import get_connection

logger = logging.getLogger(__name__)

# text-embedding-3-large's native output size -- see this module's
# docstring for why this is never truncated via the OpenAI API's own
# `dimensions` parameter, and why the column is declared `halfvec`, not
# `vector`, to stay indexable at this size.
EMBEDDING_DIMENSIONS = 3072


class EmbeddingNotConfigured(Exception):
    """Raised when the openai embeddings backend isn't configured --
    mirrors enrich.EnrichmentNotConfigured/chat.ChatNotConfigured."""


def embed_text(text: str):
    """Embeds arbitrary text with the same model/endpoint as
    embed_description below -- shared by that function (embedding a
    photo's `description`) and storage.search_photos (embedding a user's
    free-text search query), since both need the exact same embedding
    space to be comparable via pgvector's `<=>` operator. Raises
    EmbeddingNotConfigured/whatever the openai SDK itself raises; unlike
    embed_description, this is not best-effort -- both callers want a real
    error to surface (a 503/502 to the API client), not a silently
    skipped embedding."""
    from openai import OpenAI  # lazy: keep this an optional dependency at import time, like chat.py/enrich.py
    from pgvector import HalfVector

    from .config import get_embedding_model, get_openai_api_key, get_openai_base_url

    api_key = get_openai_api_key()
    if not api_key:
        raise EmbeddingNotConfigured(
            "Description embeddings aren't configured: set LUMINARY_OPENAI_API_KEY "
            "(and LUMINARY_OPENAI_BASE_URL if this isn't api.openai.com)."
        )

    model = get_embedding_model()
    client = OpenAI(api_key=api_key, base_url=get_openai_base_url())
    try:
        response = client.embeddings.create(model=model, input=text)
    except Exception as exc:
        from .chat import _log_upstream_error_body  # same diagnostic chat.py/enrich.py's openai calls use

        _log_upstream_error_body(exc)
        raise
    return HalfVector(response.data[0].embedding)


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

    with get_connection() as conn:
        conn.execute("UPDATE photos SET description_embedding = %s WHERE id = %s", (embedding, photo_id))
        conn.commit()
    return True
