"""Background reconciliation between the configured photos backend and the
Postgres index: inserts rows for new files, updates rows whose file changed,
and deletes rows for files no longer present. This only ever reads from the
backend (fs.find/fs.info/fs.open in "rb" mode) -- "deleted" here means the
SQLite row is removed because the file is gone, never the reverse. Runs as
an asyncio task so it doesn't block the API, fanning individual files out
across a thread pool (fsspec backends -- local, SMB, S3, etc. -- are
synchronous) so slow, network-bound backends don't serialize one file at a
time.

A full reprocess of every file (re-reading, re-thumbnailing, re-captioning)
only happens for a file that's new or whose size changed. A file whose
content is unchanged but is missing a derived field -- most commonly
`vlm_description`, on a library that was indexed before Florence-2 chat was
configured -- gets that field backfilled without redoing the rest; see
_sync_one_file's docstring. This is what makes a repeat "Refresh" cheap:
there's no separate "full" vs. "incremental" sync, just this single
reconciliation, which degrades gracefully to doing only as much work as a
given file actually needs.

Each inserted/updated file also gets a `vlm_description` (chat.caption_image
-- Florence-2's raw caption, not the description shown in the UI, see
below) and its faces detected (chat.detect_faces, general object detection
filtered to "face" labels -- see chat.py's module docstring for why that,
not phrase grounding, is what finds *every* face rather than just one --
stored one-to-many in the `faces` table via storage.replace_faces) via
Florence-2. `vlm_description` doubles as the signal for "has Florence-2
already run on this photo at all", covering face detection too, not just
the caption -- see _run_florence_pipeline for the ordering that makes that
a reliable signal (faces are always written *before* `vlm_description`,
never after), and _sync_one_file's docstring for how that combines with
the backfill behavior above.

Once `vlm_description` is written, _run_florence_pipeline also calls
enrich.enrich_description(photo_id) -- `photos.description`, the text
actually shown in the UI, is a *separate*, LLM-composed rewrite of
`vlm_description` plus whatever individuals/location/filename facts are
available, not Florence-2's raw caption. See enrich.py's module docstring
for the full list of places that call it (a sync here is only one of
several -- tagging/untagging/deleting an individual elsewhere also
changes "who's in this photo" and re-triggers it).

Whenever a file's GPS coordinates are (re)extracted from EXIF (i.e.
whenever `content_changed`, same as latitude/longitude themselves --
_describe_location, geocode.py), they're also reverse-geocoded into
`location_name`, a short human-readable place description (e.g. "Boone,
North Carolina" or "Grandfather Mountain State Park, Blowing Rock, North
Carolina") stored alongside the raw coordinates. Best-effort like
vlm_description/face detection above: a lookup failure (network, timeout,
unconfigured) is logged and leaves `location_name` NULL rather than
failing the sync. There's no backfill pass for this one, unlike
vlm_description/thumbnail -- it piggybacks on latitude/longitude's own
re-extraction, which itself only happens on a genuine content change, not
on every sync of an unchanged file.

A successful sync also chains straight into recognition.py's "Refresh
Faces" job (_trigger_face_refresh, called at the end of _run_sync) --
"Refresh" on the Manage page shouldn't need a second click to also
auto-match any newly detected faces against already-tagged individuals.
recognition.start_refresh()'s own try_start() guard makes this a no-op if
a refresh is already running (e.g. someone just clicked the standalone
"Refresh Faces" button, which still exists for on-demand re-matching --
right after manually tagging someone, without waiting for/triggering a
full resync).
"""

import asyncio
import io
import logging
import mimetypes
import os.path
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional

from PIL import Image, ImageOps

from . import chat, enrich, geocode, recognition, storage
from .database import get_connection
from .storage import THUMBNAIL_SIZE, _extract_exif, _fs_and_root

logger = logging.getLogger(__name__)

MAX_CONCURRENCY = 8

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".heic",
    ".heif",
}

_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENCY)


class _SyncProgress:
    def __init__(self):
        self._lock = threading.Lock()
        self._state = {
            "status": "idle",
            "processed": 0,
            "total": 0,
            "inserted": 0,
            "updated": 0,
            "deleted": 0,
            "error": None,
        }

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)

    def try_start(self) -> bool:
        """Atomically transition to "running" if not already running, in a
        single locked step. Returns True if this call won the transition
        (the caller should start the background thread), False if a sync is
        already in flight. This has to happen synchronously, on the calling
        thread, before start_sync() returns -- otherwise a caller that polls
        status immediately after POST /sync can still read the *previous*
        run's terminal status and wrongly conclude the new sync is already
        done, while it keeps running unobserved in the background."""
        with self._lock:
            if self._state["status"] == "running":
                return False
            self._state = {
                "status": "running",
                "processed": 0,
                "total": 0,
                "inserted": 0,
                "updated": 0,
                "deleted": 0,
                "error": None,
            }
            return True

    def set_total(self, total: int) -> None:
        with self._lock:
            self._state["total"] = total

    def record(self, outcome: str) -> None:
        with self._lock:
            self._state["processed"] += 1
            if outcome in ("inserted", "updated"):
                self._state[outcome] += 1

    def finish(self, deleted: int) -> None:
        with self._lock:
            self._state["status"] = "completed"
            self._state["deleted"] = deleted

    def fail(self, error: str) -> None:
        with self._lock:
            self._state["status"] = "error"
            self._state["error"] = error

    def reset(self) -> None:
        with self._lock:
            self._state = {
                "status": "idle",
                "processed": 0,
                "total": 0,
                "inserted": 0,
                "updated": 0,
                "deleted": 0,
                "error": None,
            }


progress = _SyncProgress()
_thread: Optional[threading.Thread] = None


def start_sync() -> dict:
    """Kick off a background reconciliation in its own thread (with its own
    event loop) if one isn't already running. Deliberately decoupled from
    the API's request-handling event loop: an `asyncio.create_task()` there
    would only make progress while that loop happens to be ticking (fine
    under a real ASGI server driving continuous traffic, but not guaranteed
    in general -- e.g. it stalls indefinitely under FastAPI's TestClient
    between requests). A dedicated thread keeps the job progressing
    regardless. `progress.try_start()` flips status to "running" atomically
    and synchronously, before this function returns, so a caller that polls
    status right after this can't observe a stale terminal status left over
    from a previous run. Returns the current status snapshot either way;
    calling this while a sync is in flight just reports on it rather than
    starting a second one."""
    global _thread
    if progress.try_start():
        logger.info("Starting photo sync")
        _thread = threading.Thread(target=lambda: asyncio.run(_run_sync()), daemon=True)
        _thread.start()
    else:
        logger.info("Sync already in progress; ignoring duplicate start request")
    return progress.snapshot()


def _load_existing() -> dict:
    # has_thumbnail/has_vlm_description are booleans derived in SQL rather
    # than pulling the actual thumbnail BLOB (which can be tens of KB each)
    # into memory for every row up front just to check presence -- this
    # dict is held for the whole sync, so across a large library that blob
    # weight would add up fast for no reason.
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, filename, size_bytes,
                   (thumbnail IS NOT NULL) AS has_thumbnail,
                   (vlm_description IS NOT NULL) AS has_vlm_description
            FROM photos
            """
        ).fetchall()
        return {row["filename"]: dict(row) for row in rows}


def _sync_one_file(fs, path: str, relative_path: str, existing_row: Optional[dict]) -> str:
    """Reconcile a single discovered file against its DB row (if any).
    Returns "unchanged", "inserted", or "updated".

    "Unchanged" -- skip entirely, no file open, no thumbnail/caption/face
    work -- is the fast path a repeat sync of a large, mostly-static
    library should hit for nearly every file: it requires both that size
    matches the DB row (the file's content presumably hasn't changed)
    *and* that its thumbnail/vlm_description are already populated. A full
    reprocess (clearing and recomputing everything) only happens for a
    genuinely new or changed file. A file whose content is unchanged but
    is missing a derived field -- e.g. indexed before `vlm_description`
    existed, or before Florence-2 chat was configured, or the thumbnail
    failed on a transient read error -- gets a narrower "backfill": the
    image is opened once, but each piece of work (thumbnailing,
    captioning+face-detection) only runs for the field that's actually
    missing, so e.g. adding captions to an already-thumbnailed library
    doesn't redundantly re-thumbnail every photo, and a library sync
    without Florence-2 configured doesn't re-open every file every time
    just to confirm there's still no caption.

    `vlm_description` is also the signal for "has face detection already
    run" (there's no separate column for it) -- see _run_florence_pipeline,
    which is what this function defers to for the caption+faces work, for
    why that's safe: faces are always stored before `vlm_description` is
    set, never after.
    """
    info = fs.info(path)
    size = info.get("size") or 0

    content_changed = not existing_row or existing_row["size_bytes"] != size
    needs_thumbnail = content_changed or not existing_row["has_thumbnail"]
    needs_florence = content_changed or not existing_row["has_vlm_description"]

    if existing_row and not content_changed and not needs_thumbnail and not needs_florence:
        return "unchanged"

    fields: dict = {}
    if content_changed:
        # The file's bytes are new/different -- any derived data on record
        # was computed from the *old* content (or doesn't exist yet), so it
        # must be cleared up front and only kept if recomputing it below
        # actually succeeds, rather than left pointing at stale content.
        # `vlm_description` cleared here is what makes `needs_florence`
        # above correctly re-trigger on the *next* sync if the pipeline
        # below doesn't get to run or doesn't finish (its own
        # faces-before-vlm_description ordering is what keeps that promise
        # even when it does run) -- old faces for this photo get cleared
        # by that pipeline itself, not here. `description` (the separate,
        # LLM-composed rewrite -- see enrich.py) is cleared alongside it
        # for the same reason: it was built from the *old* vlm_description/
        # faces, which no longer apply to this changed content either.
        fields.update(
            width=None,
            height=None,
            created_at=datetime.now(timezone.utc).isoformat(),
            latitude=None,
            longitude=None,
            location_name=None,
            thumbnail=None,
            vlm_description=None,
            description=None,
        )

    rgb_image = None
    try:
        with fs.open(path, "rb") as f:
            with Image.open(f) as raw_image:
                # Camera sensors commonly store pixels in landscape with an
                # EXIF Orientation tag saying how to rotate for display (e.g.
                # a portrait shot held sideways) -- bake that rotation in
                # before reading dimensions or thumbnailing, or portrait
                # photos end up indexed and thumbnailed as landscape.
                with ImageOps.exif_transpose(raw_image) as image:
                    fields["width"], fields["height"] = image.size

                    if content_changed:
                        created_at, latitude, longitude = _extract_exif(raw_image)
                        fields["created_at"] = (created_at or datetime.now(timezone.utc)).isoformat()
                        fields["latitude"] = latitude
                        fields["longitude"] = longitude

                    if needs_thumbnail or needs_florence:
                        # .convert("RGB") fully decodes the pixel data into a
                        # new, independent in-memory image, so it stays usable
                        # below after the `with fs.open(...)`/`Image.open(...)`
                        # blocks (and the file handle they own) have closed.
                        rgb_image = image.convert("RGB")

                    if needs_thumbnail:
                        thumbnail = rgb_image.copy()
                        thumbnail.thumbnail(THUMBNAIL_SIZE)
                        buffer = io.BytesIO()
                        thumbnail.save(buffer, "JPEG")
                        fields["thumbnail"] = buffer.getvalue()
    except Exception:
        logger.warning(
            "Could not read/thumbnail %r; indexing metadata only", relative_path, exc_info=True
        )
        rgb_image = None  # don't run florence below against a half-decoded image

    # Reverse-geocoding is a network call to a third-party service (see
    # geocode.py) -- deliberately done here, after the `with fs.open(...)`
    # block above has already closed, rather than while that file handle
    # (and the photos backend connection it may represent, e.g. SMB) is
    # still open. Only attempted when this sync actually (re)extracted GPS
    # coordinates above; independent of whether the thumbnail/image-decode
    # step succeeded, since it only needs the coordinates, not the image.
    if content_changed and fields.get("latitude") is not None and fields.get("longitude") is not None:
        fields["location_name"] = _describe_location(fields["latitude"], fields["longitude"], relative_path)

    fields["content_type"] = mimetypes.guess_type(relative_path)[0] or "application/octet-stream"
    fields["size_bytes"] = size

    photo_id = existing_row["id"] if existing_row else str(uuid.uuid4())

    with get_connection() as conn:
        if existing_row:
            set_clause = ", ".join(f"{column} = %s" for column in fields)
            conn.execute(
                f"UPDATE photos SET {set_clause} WHERE id = %s",
                (*fields.values(), photo_id),
            )
        else:
            fields["id"] = photo_id
            fields["filename"] = relative_path
            columns = ", ".join(fields)
            placeholders = ", ".join("%s" for _ in fields)
            conn.execute(f"INSERT INTO photos ({columns}) VALUES ({placeholders})", tuple(fields.values()))
        conn.commit()

    # Only now, with the photo row safely in place (vlm_description still
    # NULL/stale-cleared) and a usable decoded image in hand, attempt the
    # caption+face-detection pipeline -- see its docstring for why it
    # writes `vlm_description` last.
    if rgb_image is not None and needs_florence:
        _run_florence_pipeline(photo_id, rgb_image, relative_path)

    return "updated" if existing_row else "inserted"


def _run_florence_pipeline(photo_id: str, image, relative_path: str) -> None:
    """The "quasi-transactional" Florence-2 step for one photo: caption,
    then detect faces, then store the faces, then -- only once faces are
    safely stored -- store the caption as `vlm_description`. That order,
    with `vlm_description` written last, is deliberate: it's what lets
    `vlm_description IS NOT NULL` double as "face detection has already
    run (and succeeded) for this photo" (see _sync_one_file), without a
    separate "faces done" column. If this gets interrupted or fails at any
    point, `vlm_description` is simply never written, so the next sync
    retries captioning *and* face detection from scratch -- there's no
    state where a stale caption or half-updated face set is mistaken for
    "done".

    Faces for this photo are always cleared before (re)detecting, win or
    lose: either this is a genuine content change superseding old faces
    that no longer apply, or it's a retry of a previous attempt that didn't
    reach this point last time, which might have left faces stored without
    ever setting `vlm_description` -- either way, old boxes must not
    survive alongside (or instead of) this attempt's result.

    `image` must already be RGB and EXIF-transposed, same precondition as
    chat.caption_image/detect_faces.

    Once `vlm_description` (and faces) are safely committed, also calls
    enrich.enrich_description(photo_id) -- `photos.description`, the text
    actually shown in the UI, is a separate LLM-composed rewrite built
    from vlm_description plus whatever individuals/location/filename facts
    are available (see enrich.py's module docstring). Runs synchronously,
    inline, on this same executor-thread call -- enrich_description is
    itself best-effort and never raises, so this can't turn an enrichment
    failure into a sync failure."""
    storage.replace_faces(photo_id, [])

    vlm_description = _generate_vlm_description(image, relative_path)
    if vlm_description is None:
        return  # captioning failed/unconfigured -- retry everything next sync

    boxes = _detect_faces(image, relative_path)
    if boxes is None:
        return  # face detection failed/unconfigured -- retry everything next sync

    storage.replace_faces(photo_id, boxes)

    with get_connection() as conn:
        conn.execute("UPDATE photos SET vlm_description = %s WHERE id = %s", (vlm_description, photo_id))
        conn.commit()

    enrich.enrich_description(photo_id)


def _generate_vlm_description(image, relative_path: str) -> Optional[str]:
    """Best-effort Florence-2 caption step of _run_florence_pipeline.
    `image` must already be RGB and EXIF-transposed. Mirrors the
    EXIF/thumbnail handling in _sync_one_file: missing torch/transformers
    (an optional, multi-GB dependency -- see chat.py's module docstring)
    or any other model failure is caught here rather than failing the
    sync, same reason a corrupt image doesn't fail the whole sync."""
    try:
        return chat.caption_image(image)
    except chat.ChatNotConfigured:
        # Common and expected when torch/transformers aren't installed --
        # debug, not info/warning, so it doesn't spam the log once per
        # photo across an entire library.
        logger.debug("Florence-2 not configured; skipping vlm_description for %r", relative_path)
        return None
    except Exception:
        logger.warning("Could not generate vlm_description for %r", relative_path, exc_info=True)
        return None


def _detect_faces(image, relative_path: str) -> Optional[list[dict]]:
    """Best-effort Florence-2 face-detection step of _run_florence_pipeline.
    `image` must already be RGB and EXIF-transposed. Returns the detected
    boxes (possibly an empty list -- "no faces found" is a valid,
    successful result, not a failure), or None if detection itself
    couldn't run/complete. Error handling mirrors _generate_vlm_description."""
    try:
        return chat.detect_faces(image)
    except chat.ChatNotConfigured:
        logger.debug("Florence-2 not configured; skipping face detection for %r", relative_path)
        return None
    except Exception:
        logger.warning("Could not detect faces for %r", relative_path, exc_info=True)
        return None


def _describe_location(latitude: float, longitude: float, relative_path: str) -> Optional[str]:
    """Best-effort reverse-geocode of a photo's GPS coordinates into a
    short, human-readable place description (geocode.reverse_geocode) --
    same error-handling shape as _generate_vlm_description/_detect_faces
    above: any failure (network, timeout, malformed response) is logged
    and swallowed, never fails the sync."""
    try:
        return geocode.reverse_geocode(latitude, longitude)
    except Exception:
        logger.warning("Could not reverse-geocode %r's GPS coordinates", relative_path, exc_info=True)
        return None


def _delete_missing(existing: dict, seen: set) -> int:
    missing_ids = [row["id"] for filename, row in existing.items() if filename not in seen]
    if not missing_ids:
        return 0
    with get_connection() as conn:
        # No FK cascade (see database.py) -- clean up faces/tags explicitly.
        with conn.cursor() as cursor:
            cursor.executemany("DELETE FROM faces WHERE photo_id = %s", [(photo_id,) for photo_id in missing_ids])
            cursor.executemany(
                "DELETE FROM photo_tags WHERE photo_id = %s", [(photo_id,) for photo_id in missing_ids]
            )
            cursor.executemany("DELETE FROM photos WHERE id = %s", [(photo_id,) for photo_id in missing_ids])
        conn.commit()
    return len(missing_ids)


def _trigger_face_refresh() -> None:
    """Kicks off recognition.py's "Refresh Faces" job at the end of a
    successful sync -- see this module's docstring. Isolated into its own
    function (rather than calling recognition.start_refresh() directly from
    _run_sync) purely so tests can monkeypatch *this* one call to a no-op
    without having to stub out recognition.start_refresh() itself, which
    test_recognition.py's own tests (and routers/faces.py's standalone
    "Refresh Faces" endpoint) rely on behaving normally."""
    recognition.start_refresh()


async def _run_sync() -> None:
    loop = asyncio.get_running_loop()
    try:
        fs, root = await loop.run_in_executor(_executor, _fs_and_root)
        all_paths = await loop.run_in_executor(_executor, fs.find, root)
        paths = [p for p in all_paths if os.path.splitext(p)[1].lower() in IMAGE_EXTENSIONS]
        existing = await loop.run_in_executor(_executor, _load_existing)

        logger.info("Sync found %d photo(s) under %s", len(paths), root)
        progress.set_total(len(paths))

        seen: set = set()
        semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

        async def handle(path: str) -> None:
            relative_path = path[len(root) + 1 :]
            seen.add(relative_path)
            async with semaphore:
                outcome = await loop.run_in_executor(
                    _executor, _sync_one_file, fs, path, relative_path, existing.get(relative_path)
                )
            progress.record(outcome)

        await asyncio.gather(*(handle(path) for path in paths))

        deleted = await loop.run_in_executor(_executor, _delete_missing, existing, seen)
        progress.finish(deleted=deleted)
        summary = progress.snapshot()
        logger.info(
            "Sync completed: %d inserted, %d updated, %d deleted (of %d found)",
            summary["inserted"],
            summary["updated"],
            summary["deleted"],
            summary["total"],
        )
        _trigger_face_refresh()
    except Exception as exc:
        logger.exception("Sync failed")
        progress.fail(str(exc))
