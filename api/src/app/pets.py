"""The "Detect Pets" background job -- finds cats and dogs in photos that
were indexed before pet detection existed (or while it was switched off).

Sync only runs pet detection on new/changed photos, and every older photo
already has `face_detection_done` TRUE, so without this they would never be
looked at. The job visits every photo whose `pet_detection_done` is FALSE
and does exactly one thing to each: re-detect its *pets*
(`storage.replace_faces(..., kinds=["pet"])`). It never touches human faces
or their tags, which is what makes it safe to run on a library that has
already been tagged by hand -- unlike the destructive Full Face Refresh.

Because only photos still owed a pet scan are visited, it is idempotent and
cheap to re-run: a second run does nothing. A photo whose original can't be
read or decoded, or whose detection errors, is skipped and left owed (logged
at WARNING), never failing the run; it is retried next time.

Same background-job shape as `enrich.py`'s/`geocode.py`'s refresh (own thread
+ event loop, atomic `try_start()`, one executor unit per photo, a claim on
the single job slot in `jobs.py`, cooperative cancel). Run it with
`POST /api/pets/refresh`.
"""

import asyncio
import io
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from PIL import Image, ImageOps

from . import jobs, pet_detection, storage
from .config import get_max_concurrency, get_pet_detection_enabled
from .database import get_connection

logger = logging.getLogger(__name__)

MAX_CONCURRENCY = get_max_concurrency()
_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENCY)

_INITIAL_STATE = {"status": "idle", "processed": 0, "total": 0, "found": 0, "error": None}


class _PetProgress:
    """Same shape/locking as enrich.py's _RefreshProgress (see its docstring
    for the atomic try_start()/release-inside-the-lock reasoning)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = dict(_INITIAL_STATE)

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)

    def try_start(self) -> bool:
        with self._lock:
            if self._state["status"] == "running":
                return False
            self._state = {**_INITIAL_STATE, "status": "running"}
            return True

    def set_total(self, total: int) -> None:
        with self._lock:
            self._state["total"] = total

    def reset(self) -> None:
        """For tests -- see conftest.py's `client` fixture."""
        with self._lock:
            self._state = dict(_INITIAL_STATE)

    def record(self, found: int) -> None:
        with self._lock:
            self._state["processed"] += 1
            self._state["found"] += found

    def finish(self) -> None:
        with self._lock:
            self._state["status"] = "completed"
            jobs.release(jobs.PETS)

    def fail(self, error: str) -> None:
        with self._lock:
            self._state["status"] = "error"
            self._state["error"] = error
            jobs.release(jobs.PETS)

    def cancel(self) -> None:
        with self._lock:
            self._state["status"] = "cancelled"
            jobs.release(jobs.PETS)


progress = _PetProgress()
_thread: Optional[threading.Thread] = None


def _photos_to_scan() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT id, filename FROM photos WHERE NOT pet_detection_done").fetchall()
        return [dict(row) for row in rows]


def _scan_photo(photo: dict) -> int:
    """Detect pets in one photo and store them. Returns how many were found
    (0 for "none", and also for "couldn't scan" -- in which case the photo is
    left owed). Runs on an executor thread."""
    try:
        original_bytes = storage.read_original_bytes(photo)
    except Exception:
        logger.warning("Could not read %r while detecting pets; skipping", photo["filename"], exc_info=True)
        return 0
    if not original_bytes:
        return 0

    try:
        with Image.open(io.BytesIO(original_bytes)) as raw_image:
            with ImageOps.exif_transpose(raw_image) as transposed:
                image = transposed.convert("RGB")
    except Exception:
        logger.warning("Could not decode %r while detecting pets; skipping", photo["filename"], exc_info=True)
        return 0

    try:
        boxes = pet_detection.detect_pets(image)
    except Exception:
        logger.warning("Pet detection failed for %r; skipping", photo["filename"], exc_info=True)
        return 0

    # Pets only: human faces and every tag on them are left alone.
    storage.replace_faces(photo["id"], boxes, kinds=["pet"])
    with get_connection() as conn:
        cursor = conn.execute("UPDATE photos SET pet_detection_done = TRUE WHERE id = %s", (photo["id"],))
        conn.commit()
    if cursor.rowcount == 0:
        # Deleted while we were scanning it: don't leave orphan boxes behind.
        storage.replace_faces(photo["id"], [], kinds=["pet"])
        return 0
    if boxes:
        logger.info("%s: found %d pet(s)", photo["filename"], len(boxes))
    return len(boxes)


def start_refresh() -> dict:
    """Kick off the pets backfill in its own thread/event loop. Claims the
    single job slot (raises jobs.JobBusy -> 409 if any job is running)."""
    global _thread
    jobs.try_claim(jobs.PETS)
    progress.try_start()
    logger.info("Starting pet detection backfill")
    _thread = jobs.run_in_thread(jobs.PETS, _run_refresh)
    return progress.snapshot()


async def _run_refresh() -> None:
    loop = asyncio.get_running_loop()
    try:
        if not get_pet_detection_enabled():
            progress.fail("Pet detection is turned off (LUMINARY_PET_DETECTION=0)")
            return
        # Before touching any photo: a missing dependency must fail the run
        # up front, not be discovered (and swallowed) once per photo.
        try:
            await loop.run_in_executor(_executor, pet_detection.ensure_configured)
        except pet_detection.PetDetectionNotConfigured as exc:
            logger.warning("Pet detection backfill not run: %s", exc)
            progress.fail(str(exc))
            return

        photos = await loop.run_in_executor(_executor, _photos_to_scan)
        progress.set_total(len(photos))

        semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

        async def handle(photo: dict) -> None:
            async with semaphore:
                if jobs.cancelled():
                    return
                found = await loop.run_in_executor(_executor, _scan_photo, photo)
            progress.record(found)

        await asyncio.gather(*(handle(photo) for photo in photos))

        if jobs.cancelled():
            logger.info(
                "Pet detection backfill cancelled after %d of %d photo(s)", progress.snapshot()["processed"], len(photos)
            )
            progress.cancel()
            return

        progress.finish()
        logger.info(
            "Pet detection backfill completed: %d pet(s) found across %d photo(s)",
            progress.snapshot()["found"],
            len(photos),
        )
    except Exception as exc:
        logger.exception("Pet detection backfill failed")
        progress.fail(str(exc))
