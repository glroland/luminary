"""Background reconciliation between the configured photos backend and the
SQLite index: inserts rows for new files, updates rows whose file changed,
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
`description`, on a library that was indexed before Florence-2 chat was
configured -- gets that field backfilled without redoing the rest; see
_sync_one_file's docstring. This is what makes a repeat "Refresh" cheap:
there's no separate "full" vs. "incremental" sync, just this single
reconciliation, which degrades gracefully to doing only as much work as a
given file actually needs.

Each inserted/updated file also gets a `description` generated via
Florence-2 (chat.caption_image), best-effort like EXIF/thumbnail
extraction -- see _generate_description.
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

from . import chat
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
    # has_thumbnail/has_description are booleans derived in SQL rather than
    # pulling the actual thumbnail BLOB (which can be tens of KB each) into
    # memory for every row up front just to check presence -- this dict is
    # held for the whole sync, so across a large library that blob weight
    # would add up fast for no reason.
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, filename, size_bytes,
                   (thumbnail IS NOT NULL) AS has_thumbnail,
                   (description IS NOT NULL) AS has_description
            FROM photos
            """
        ).fetchall()
        return {row["filename"]: dict(row) for row in rows}


def _sync_one_file(fs, path: str, relative_path: str, existing_row: Optional[dict]) -> str:
    """Reconcile a single discovered file against its DB row (if any).
    Returns "unchanged", "inserted", or "updated".

    "Unchanged" -- skip entirely, no file open, no thumbnail/caption work
    -- is the fast path a repeat sync of a large, mostly-static library
    should hit for nearly every file: it requires both that size matches
    the DB row (the file's content presumably hasn't changed) *and* that
    its thumbnail/description are already populated. A full reprocess
    (clearing and recomputing everything) only happens for a genuinely new
    or changed file. A file whose content is unchanged but is missing a
    derived field -- e.g. indexed before `description` existed, or before
    Florence-2 chat was configured, or the thumbnail failed on a transient
    read error -- gets a narrower "backfill": the image is opened once,
    but each piece of work (thumbnailing, captioning) only runs for the
    field that's actually missing, so e.g. adding descriptions to an
    already-thumbnailed library doesn't redundantly re-thumbnail every
    photo, and a library sync without Florence-2 configured doesn't
    re-open every file every time just to confirm there's still no
    description.
    """
    info = fs.info(path)
    size = info.get("size") or 0

    content_changed = not existing_row or existing_row["size_bytes"] != size
    needs_thumbnail = content_changed or not existing_row["has_thumbnail"]
    needs_description = content_changed or not existing_row["has_description"]

    if existing_row and not content_changed and not needs_thumbnail and not needs_description:
        return "unchanged"

    fields: dict = {}
    if content_changed:
        # The file's bytes are new/different -- any derived data on record
        # was computed from the *old* content (or doesn't exist yet), so it
        # must be cleared up front and only kept if recomputing it below
        # actually succeeds, rather than left pointing at stale content.
        fields.update(
            width=None,
            height=None,
            created_at=datetime.now(timezone.utc).isoformat(),
            latitude=None,
            longitude=None,
            thumbnail=None,
            description=None,
        )

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

                    rgb_image = image.convert("RGB") if (needs_thumbnail or needs_description) else None

                    if needs_thumbnail:
                        thumbnail = rgb_image.copy()
                        thumbnail.thumbnail(THUMBNAIL_SIZE)
                        buffer = io.BytesIO()
                        thumbnail.save(buffer, "JPEG")
                        fields["thumbnail"] = buffer.getvalue()

                    if needs_description:
                        fields["description"] = _generate_description(rgb_image, relative_path)
    except Exception:
        logger.warning(
            "Could not read/thumbnail %r; indexing metadata only", relative_path, exc_info=True
        )

    fields["content_type"] = mimetypes.guess_type(relative_path)[0] or "application/octet-stream"
    fields["size_bytes"] = size

    with get_connection() as conn:
        if existing_row:
            set_clause = ", ".join(f"{column} = ?" for column in fields)
            conn.execute(
                f"UPDATE photos SET {set_clause} WHERE id = ?",
                (*fields.values(), existing_row["id"]),
            )
            conn.commit()
            return "updated"

        fields["id"] = str(uuid.uuid4())
        fields["filename"] = relative_path
        columns = ", ".join(fields)
        placeholders = ", ".join("?" for _ in fields)
        conn.execute(
            f"INSERT INTO photos ({columns}) VALUES ({placeholders})",
            tuple(fields.values()),
        )
        conn.commit()
        return "inserted"


def _generate_description(image, relative_path: str) -> Optional[str]:
    """Best-effort Florence-2 caption for a newly inserted/changed photo,
    stored as its `description`. `image` must already be RGB and
    EXIF-transposed (same space as the thumbnail/width/height derived
    alongside it). Mirrors the EXIF/thumbnail handling above: missing
    torch/transformers (an optional, multi-GB dependency -- see chat.py's
    module docstring) just leaves the description unset rather than
    failing the sync, and any other model failure is caught per-file for
    the same reason a corrupt image doesn't fail the whole sync."""
    try:
        return chat.caption_image(image)
    except chat.ChatNotConfigured:
        # Common and expected when torch/transformers aren't installed --
        # debug, not info/warning, so it doesn't spam the log once per
        # photo across an entire library.
        logger.debug("Florence-2 not configured; skipping description for %r", relative_path)
        return None
    except Exception:
        logger.warning("Could not generate description for %r", relative_path, exc_info=True)
        return None


def _delete_missing(existing: dict, seen: set) -> int:
    missing_ids = [row["id"] for filename, row in existing.items() if filename not in seen]
    if not missing_ids:
        return 0
    with get_connection() as conn:
        conn.executemany("DELETE FROM photos WHERE id = ?", [(photo_id,) for photo_id in missing_ids])
        conn.commit()
    return len(missing_ids)


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
    except Exception as exc:
        logger.exception("Sync failed")
        progress.fail(str(exc))
