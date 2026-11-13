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

On every successful generation, `_compose` logs the full prompt sent to
the model and its raw response at INFO, tagged with the photo id -- the
only way to see exactly what the model was asked and what it actually said
back, which matters because "the description is too short/vague/wrong"
is otherwise unverifiable without it.

Every successful generation also regenerates `photos.description_embedding`
(embeddings.py, embeddings.embed_description) -- a vector embedding of the
description text itself, for similarity search (storage.
find_similar_photos). That's a separate module since it has its own model/
dimension concerns independent of this one's chat-completion model; see
its docstring for why.

start_refresh() (routers/descriptions.py's "Refresh Descriptions" button,
POST /api/descriptions/refresh) is the bulk equivalent of the four
individually-triggered call sites above: it regenerates `description` for
every photo in the library, not just ones whose facts just changed --
e.g. after editing _build_prompt itself (wanting existing rows to pick up
the new wording/length), or after enabling LUMINARY_OPENAI_* for the first
time on a library that was synced before it was configured. Same
background-job shape as sync.py's start_sync()/recognition.py's
start_refresh() (own thread + event loop, atomic try_start() so a status
poll right after the POST can't observe a stale previous run, files fanned
out across a thread pool via asyncio.gather/run_in_executor) -- copy that
pattern, don't reinvent it, if a fourth such job ever gets added.
"""

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from . import embeddings
from .database import get_connection

logger = logging.getLogger(__name__)

MAX_CONCURRENCY = 8
_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENCY)

# A few sentences, not a one-liner, but still short enough to keep the
# UI's description paragraph (PhotoModal.jsx's .photo-modal-description)
# from growing unwieldy.
_MAX_TOKENS = 600


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
        "\nWrite a detailed, natural-language description (3-5 sentences) of this photo for a "
        "personal photo library, weaving in whichever of the facts above are useful. Describe the "
        "scene vividly -- setting, mood, notable details -- rather than a one-line summary. Don't "
        "invent details beyond what's given above, don't mention the filename literally, and don't "
        "mention that this description or the caption was computer-generated -- just describe the "
        "photo naturally, as a caption."
    )
    return "\n".join(lines)


def _compose(photo_id: str, facts: dict) -> str:
    from openai import OpenAI  # lazy: keep this an optional dependency at import time, like chat.py

    from .config import get_openai_api_key, get_openai_model

    api_key = get_openai_api_key()
    model = get_openai_model()
    if not api_key or not model:
        raise EnrichmentNotConfigured(
            "Description enrichment isn't configured: set LUMINARY_OPENAI_API_KEY and "
            "LUMINARY_OPENAI_MODEL (and LUMINARY_OPENAI_BASE_URL if this isn't api.openai.com)."
        )

    prompt = _build_prompt(facts)
    from .chat import build_openai_client

    client = build_openai_client(OpenAI, api_key)
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=_MAX_TOKENS,
        )
    except Exception as exc:
        from .chat import _log_upstream_error_body  # same diagnostic chat.py's openai calls use

        _log_upstream_error_body(exc)
        raise
    description = response.choices[0].message.content.strip()
    logger.info(
        "Description enrichment for photo %s -- prompt: %s -- response: %s", photo_id, prompt, description
    )
    return description


def enrich_description(photo_id: str) -> bool:
    """Best-effort: regenerate and store photos.description for one photo.
    Never raises -- every call site treats this as a side effect, not
    something whose failure should fail the tag/untag/sync/refresh it's
    attached to (see this module's docstring for the full list). Returns
    whether a description was actually (re)generated -- start_refresh()
    below uses this to report how many of the library it actually updated,
    the four individual call sites above just ignore it."""
    facts = _photo_facts(photo_id)
    if facts is None:
        return False

    try:
        description = _compose(photo_id, facts)
    except EnrichmentNotConfigured as exc:
        logger.debug("Skipping description enrichment for photo %s: %s", photo_id, exc)
        return False
    except Exception:
        logger.warning("Could not enrich description for photo %s", photo_id, exc_info=True)
        return False

    with get_connection() as conn:
        conn.execute("UPDATE photos SET description = %s WHERE id = %s", (description, photo_id))
        conn.commit()

    # The embedding must always describe the *current* description text --
    # regenerate it in lockstep, right here, rather than on any separate
    # schedule. Best-effort in its own right (see embeddings.py's module
    # docstring); its own success/failure is independent of the
    # description update above, which has already committed either way.
    embeddings.embed_description(photo_id)
    return True


class _RefreshProgress:
    """Same shape/locking as sync.py's _SyncProgress/recognition.py's
    _RefreshProgress -- see their docstrings for the reasoning (atomic
    try_start() so a caller polling status right after POST
    /api/descriptions/refresh can't observe a stale terminal status from a
    previous run)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = {"status": "idle", "processed": 0, "total": 0, "updated": 0, "error": None}

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)

    def try_start(self) -> bool:
        with self._lock:
            if self._state["status"] == "running":
                return False
            self._state = {"status": "running", "processed": 0, "total": 0, "updated": 0, "error": None}
            return True

    def set_total(self, total: int) -> None:
        with self._lock:
            self._state["total"] = total

    def reset(self) -> None:
        """For tests -- see conftest.py's `client` fixture, which resets
        this module-level singleton between tests the same way it already
        does for sync.progress/recognition.progress."""
        with self._lock:
            self._state = {"status": "idle", "processed": 0, "total": 0, "updated": 0, "error": None}

    def record(self, updated: bool) -> None:
        with self._lock:
            self._state["processed"] += 1
            if updated:
                self._state["updated"] += 1

    def finish(self) -> None:
        with self._lock:
            self._state["status"] = "completed"

    def fail(self, error: str) -> None:
        with self._lock:
            self._state["status"] = "error"
            self._state["error"] = error


progress = _RefreshProgress()
_thread: Optional[threading.Thread] = None


def _all_photo_ids() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute("SELECT id FROM photos").fetchall()
        return [row["id"] for row in rows]


def start_refresh() -> dict:
    """Kick off a background description refresh (every photo) in its own
    thread/event loop if one isn't already running -- same rationale and
    shape as sync.start_sync()/recognition.start_refresh() (decoupled from
    the API's own request-handling loop, try_start() flips to "running"
    synchronously before this returns). Returns the current status
    snapshot either way."""
    global _thread
    if progress.try_start():
        logger.info("Starting description refresh")
        _thread = threading.Thread(target=lambda: asyncio.run(_run_refresh()), daemon=True)
        _thread.start()
    else:
        logger.info("Description refresh already in progress; ignoring duplicate start request")
    return progress.snapshot()


async def _run_refresh() -> None:
    loop = asyncio.get_running_loop()
    try:
        photo_ids = await loop.run_in_executor(_executor, _all_photo_ids)
        progress.set_total(len(photo_ids))

        semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

        async def handle(photo_id: str) -> None:
            async with semaphore:
                updated = await loop.run_in_executor(_executor, enrich_description, photo_id)
            progress.record(updated)

        await asyncio.gather(*(handle(photo_id) for photo_id in photo_ids))

        progress.finish()
        logger.info(
            "Description refresh completed: %d updated of %d photo(s)",
            progress.snapshot()["updated"],
            len(photo_ids),
        )
    except Exception as exc:
        logger.exception("Description refresh failed")
        progress.fail(str(exc))
