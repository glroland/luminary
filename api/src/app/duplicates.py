"""The "Identify Duplicates" background job for the *similar* and *all*
modes (exact copies stay a plain synchronous query, see
`storage.find_duplicate_photos`).

Comparing perceptual hashes and then verifying each candidate's thumbnail
pixels takes minutes on a large library -- long enough that doing it inside
one HTTP request hit the proxy/route timeout. So it runs like the other
long jobs (own thread + event loop, atomic `try_start()`, a claim on the
single job slot in `jobs.py`, cooperative cancel): `POST /api/duplicates/
refresh` starts it, the Manage page polls `GET /api/jobs`, and when it has
finished the CSV is fetched with `GET /api/duplicates/csv`.

The finished result is kept **in memory** (the latest run only) -- it is a
report to download, not library state, and rebuilding it is what the job
does. It is lost on restart (the CSV endpoint then answers 404) and, like
the other job state, is per-process (see CLAUDE.md on replicas).

Read-only: Postgres only, never the photos backend.
"""

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from . import jobs, storage

logger = logging.getLogger(__name__)

MODES = ("similar", "all")

_executor = ThreadPoolExecutor(max_workers=1)

_INITIAL_STATE = {"status": "idle", "processed": 0, "total": 0, "found": 0, "mode": None, "error": None}


class _DuplicateProgress:
    """Same shape/locking as pets.py's _PetProgress (see enrich.py's
    _RefreshProgress for the atomic try_start()/release-inside-the-lock
    reasoning). The result rows live here too, set in the same locked step
    as the terminal "completed" status, so a caller that sees "completed"
    always finds them."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = dict(_INITIAL_STATE)
        self._rows: Optional[list[dict]] = None

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)

    def result(self) -> Optional[tuple[str, list[dict]]]:
        """(mode, rows) of the latest completed run, or None."""
        with self._lock:
            if self._rows is None:
                return None
            return self._state["mode"], list(self._rows)

    def try_start(self, mode: str) -> bool:
        with self._lock:
            if self._state["status"] == "running":
                return False
            self._state = {**_INITIAL_STATE, "status": "running", "mode": mode}
            return True

    def record(self, processed: int, total: int) -> None:
        with self._lock:
            self._state["processed"] = processed
            self._state["total"] = total

    def reset(self) -> None:
        """For tests -- see conftest.py's `client` fixture."""
        with self._lock:
            self._state = dict(_INITIAL_STATE)
            self._rows = None

    def finish(self, rows: list[dict]) -> None:
        with self._lock:
            self._rows = rows
            self._state["found"] = len(rows)
            self._state["processed"] = self._state["total"]
            self._state["status"] = "completed"
            jobs.release(jobs.DUPLICATES)

    def fail(self, error: str) -> None:
        with self._lock:
            self._state["status"] = "error"
            self._state["error"] = error
            jobs.release(jobs.DUPLICATES)

    def cancel(self) -> None:
        with self._lock:
            self._state["status"] = "cancelled"
            jobs.release(jobs.DUPLICATES)


progress = _DuplicateProgress()
_thread: Optional[threading.Thread] = None


def start_refresh(mode: str = "all") -> dict:
    """Kick off the duplicate search in its own thread/event loop. Claims the
    single job slot (raises jobs.JobBusy -> 409 if any job is running)."""
    global _thread
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    jobs.try_claim(jobs.DUPLICATES)
    progress.try_start(mode)
    logger.info("Starting duplicate search (mode=%s)", mode)
    _thread = jobs.run_in_thread(jobs.DUPLICATES, lambda: _run_refresh(mode))
    return progress.snapshot()


async def _run_refresh(mode: str) -> None:
    loop = asyncio.get_running_loop()
    try:
        rows = await loop.run_in_executor(
            _executor,
            lambda: storage.find_duplicate_pairs(mode, on_progress=progress.record, should_stop=jobs.cancelled),
        )
        progress.finish(rows)
        logger.info("Duplicate search completed: %d duplicate file(s)", len(rows))
    except storage.DuplicateSearchCancelled:
        logger.info("Duplicate search cancelled")
        progress.cancel()
    except Exception as exc:
        logger.exception("Duplicate search failed")
        progress.fail(str(exc))
