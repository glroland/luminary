"""Composes `photos.description` -- the description actually shown in the
UI -- by having a general-purpose LLM (any OpenAI-*compatible* chat
endpoint, the same `LUMINARY_OPENAI_*` configuration chat.py's "openai"
backend already uses) rewrite whichever facts are already on hand for a
photo into a short, natural-language description: its Florence-2 caption
(`vlm_description`, chat.caption_image, run once at index time), the
individuals currently tagged in it, its reverse-geocoded location
(`location_name`, geocode.py), and its filename/path.

This is deliberately a second, separate pass over facts already computed
elsewhere, written by a (usually larger/more capable) general chat model,
not another vision-model description of the image itself --
enrich_description() never looks at the image again, only at text already
in the database. That keeps it cheap (no image download/decode/upload) and
is what makes it reasonable to call synchronously, inline, from several
different request/job code paths below, the same way chat.py's own
POST /api/photos/{id}/chat already makes a synchronous openai call as part
of handling a request.

enrich_description(photo_id) is called everywhere the facts it composes
from can change for a photo:

1. sync.py's _run_florence_pipeline, right after a fresh vlm_description
   and face set are written -- the first description for a newly indexed
   or changed photo, generated even before anyone is tagged in it
   (individuals is simply empty at that point).
2. people.py's tag_face/untag_face -- a face's identity changed, so the
   "individuals in this photo" fact changed.
3. recognition.py's auto-tagging, once per photo (not once per face) after
   a "Refresh Faces" run auto-matches at least one of its faces -- same
   reason as #2, the automatic path.
4. people.py's delete_individual, for every photo that had a face tagged
   to the individual being deleted -- same reason, the bulk version.

Best-effort throughout, same shape as every other derived field computed
in this app (geocode.py's reverse_geocode, chat.py's caption_image/
detect_faces): a failure (missing config, network error, malformed
response) is logged and leaves `description` as whatever it already was
(not cleared to NULL -- a stale-but-present description beats losing it to
a transient failure), never raised to the caller. None of the call sites
above treat this as something whose failure should fail the tag/untag/
sync/refresh it's attached to.
"""

import logging
from typing import Optional

from .database import get_connection

logger = logging.getLogger(__name__)

# Short rewrite, not a full report -- keeps this fast and keeps the UI's
# description paragraph (PhotoModal.jsx's .photo-modal-description) from
# growing unwieldy.
_MAX_TOKENS = 300


class EnrichmentNotConfigured(Exception):
    """Raised when the openai chat backend isn't configured -- mirrors
    chat.ChatNotConfigured, but kept as this module's own exception (rather
    than reusing that one) since the two are only coincidentally backed by
    the same config right now; chat.py and enrich.py otherwise don't
    depend on each other."""


def _photo_facts(photo_id: str) -> Optional[dict]:
    """Gathers the raw facts enrich_description composes a description
    from: the photo's filename/vlm_description/location_name, plus the
    distinct names of every individual currently tagged in it (a plain
    join on faces.person_id, not people.list_faces_for_individual -- this
    needs "every person in this photo", the inverse direction). None if
    the photo doesn't exist (e.g. deleted since whatever triggered this
    call was still in flight)."""
    with get_connection() as conn:
        photo = conn.execute(
            "SELECT filename, vlm_description, location_name FROM photos WHERE id = %s", (photo_id,)
        ).fetchone()
        if not photo:
            return None
        individuals = conn.execute(
            "SELECT DISTINCT i.name FROM faces f JOIN individuals i ON i.id = f.person_id "
            "WHERE f.photo_id = %s ORDER BY i.name",
            (photo_id,),
        ).fetchall()
    return {
        "filename": photo["filename"],
        "vlm_description": photo["vlm_description"],
        "location_name": photo["location_name"],
        "individuals": [row["name"] for row in individuals],
    }


def _build_prompt(facts: dict) -> str:
    lines = [f"Photo filename/path: {facts['filename']}"]
    if facts["vlm_description"]:
        lines.append(f"Automated image caption: {facts['vlm_description']}")
    if facts["location_name"]:
        lines.append(f"Location: {facts['location_name']}")
    if facts["individuals"]:
        lines.append(f"People identified in the photo: {', '.join(facts['individuals'])}")
    lines.append(
        "\nWrite one short, natural-language description (1-2 sentences) of this photo for a "
        "personal photo library, weaving in whichever of the facts above are useful. Don't invent "
        "details beyond what's given above, don't mention the filename literally, and don't mention "
        "that this description or the caption was computer-generated -- just describe the photo "
        "naturally, as a caption."
    )
    return "\n".join(lines)


def _compose(facts: dict) -> str:
    from openai import OpenAI  # lazy: keep this an optional dependency at import time, like chat.py

    from .config import get_openai_api_key, get_openai_base_url, get_openai_model

    api_key = get_openai_api_key()
    model = get_openai_model()
    if not api_key or not model:
        raise EnrichmentNotConfigured(
            "Description enrichment isn't configured: set LUMINARY_OPENAI_API_KEY and "
            "LUMINARY_OPENAI_MODEL (and LUMINARY_OPENAI_BASE_URL if this isn't api.openai.com)."
        )

    client = OpenAI(api_key=api_key, base_url=get_openai_base_url())
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": _build_prompt(facts)}],
            max_tokens=_MAX_TOKENS,
        )
    except Exception as exc:
        from .chat import _log_upstream_error_body  # same diagnostic chat.py's openai calls use

        _log_upstream_error_body(exc)
        raise
    return response.choices[0].message.content.strip()


def enrich_description(photo_id: str) -> None:
    """Best-effort: regenerate and store photos.description for one photo.
    Never raises -- every call site treats this as a side effect, not
    something whose failure should fail the tag/untag/sync/refresh it's
    attached to (see this module's docstring for the full list)."""
    facts = _photo_facts(photo_id)
    if facts is None:
        return

    try:
        description = _compose(facts)
    except EnrichmentNotConfigured as exc:
        logger.debug("Skipping description enrichment for photo %s: %s", photo_id, exc)
        return
    except Exception:
        logger.warning("Could not enrich description for photo %s", photo_id, exc_info=True)
        return

    with get_connection() as conn:
        conn.execute("UPDATE photos SET description = %s WHERE id = %s", (description, photo_id))
        conn.commit()
