"""One background job at a time, across the whole API.

The five long-running jobs -- photo sync (`sync.py`), face matching
(`recognition.py`), description refresh (`enrich.py`), location refresh
(`geocode.py`) and the pets backfill (`pets.py`) -- each keep their own progress object, but they all
contend for the single claim held here. A second `POST` while any of them
is running raises `JobBusy` (mapped to `409` in `main.py`), whichever
browser sent it. `GET /api/jobs` (`routers/jobs.py`) reports which job holds
the claim, so every open Manage page sees the same status, not just the one
that clicked.

Cancellation is cooperative: `request_cancel()` sets a flag, the running job
checks `cancelled()` between units of work (per photo / per file), stops
picking up new ones, and finishes with status `"cancelled"`. The claim is
held until then, so the UI shows "Cancelling" until the system really is
ready to run another job. A cancelled job never runs its destructive tail
(sync's delete-missing-rows step), since it hasn't seen every file.

`release()` is called by each progress object's terminal transition
(`finish`/`fail`/`cancel`) *before* it flips the status, so any caller that
sees a terminal status can immediately start the next job without racing a
still-held claim. The worker thread also releases in a `finally`
(`run_in_thread`) as a safety net; `release` is idempotent and only
releases the kind that holds it.
"""

import asyncio
import threading
from typing import Awaitable, Callable, Optional

SYNC = "sync"
FACES = "faces"
DESCRIPTIONS = "descriptions"
LOCATIONS = "locations"
PETS = "pets"


class JobBusy(Exception):
    """Another job holds the claim."""

    def __init__(self, active: Optional[str]):
        self.active = active
        super().__init__(f"Another job is already running: {active}")


_lock = threading.Lock()
_active: Optional[str] = None
_cancel = threading.Event()


def try_claim(kind: str) -> None:
    """Atomically take the claim or raise `JobBusy`."""
    global _active
    with _lock:
        if _active is not None:
            raise JobBusy(_active)
        _active = kind
        _cancel.clear()


def release(kind: str) -> None:
    global _active
    with _lock:
        if _active == kind:
            _active = None
            _cancel.clear()


def request_cancel() -> bool:
    """Ask the running job to stop. False if nothing is running."""
    with _lock:
        if _active is None:
            return False
        _cancel.set()
        return True


def cancelled() -> bool:
    """For the running job to poll between units of work."""
    return _cancel.is_set()


def snapshot() -> dict:
    with _lock:
        return {"active": _active, "cancelling": _active is not None and _cancel.is_set()}


def reset() -> None:
    """For tests -- see conftest.py's `client` fixture."""
    global _active
    with _lock:
        _active = None
        _cancel.clear()


def run_in_thread(kind: str, job: Callable[[], Awaitable[None]]) -> threading.Thread:
    """Run `job()` in its own thread with its own event loop (see sync.py
    for why not the API's loop), releasing `kind`'s claim when it ends."""

    def target() -> None:
        try:
            asyncio.run(job())
        finally:
            release(kind)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread
