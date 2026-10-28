"""Background reconciliation between the configured photos backend and the
SQLite index: inserts rows for new files, updates rows whose file changed,
and deletes rows for files no longer present. Runs as an asyncio task so it
doesn't block the API, fanning individual files out across a thread pool
(fsspec backends -- local, SMB, S3, etc. -- are synchronous) so slow,
network-bound backends don't serialize one file at a time.
"""

import asyncio
import io
import mimetypes
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional

from PIL import Image

from .database import get_connection
from .storage import THUMBNAIL_SIZE, _extract_exif, _fs_and_root

MAX_CONCURRENCY = 8

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

    def start(self, total: int) -> None:
        with self._lock:
            self._state = {
                "status": "running",
                "processed": 0,
                "total": total,
                "inserted": 0,
                "updated": 0,
                "deleted": 0,
                "error": None,
            }

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

    def is_running(self) -> bool:
        with self._lock:
            return self._state["status"] == "running"

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
    regardless. Returns the current status snapshot either way; calling
    this while a sync is in flight just reports on it rather than starting
    a second one."""
    global _thread
    if not progress.is_running():
        _thread = threading.Thread(target=lambda: asyncio.run(_run_sync()), daemon=True)
        _thread.start()
    return progress.snapshot()


def _load_existing() -> dict:
    with get_connection() as conn:
        rows = conn.execute("SELECT id, filename, size_bytes FROM photos").fetchall()
        return {row["filename"]: dict(row) for row in rows}


def _sync_one_file(fs, path: str, relative_path: str, existing_row: Optional[dict]) -> str:
    """Reconcile a single discovered file against its DB row (if any).
    Returns "unchanged", "inserted", or "updated"."""
    info = fs.info(path)
    size = info.get("size") or 0

    if existing_row and existing_row["size_bytes"] == size:
        return "unchanged"

    width = height = None
    created_at = latitude = longitude = None
    thumbnail_bytes = None
    try:
        with fs.open(path, "rb") as f:
            with Image.open(f) as image:
                width, height = image.size
                created_at, latitude, longitude = _extract_exif(image)

                thumbnail = image.convert("RGB")
                thumbnail.thumbnail(THUMBNAIL_SIZE)
                buffer = io.BytesIO()
                thumbnail.save(buffer, "JPEG")
                thumbnail_bytes = buffer.getvalue()
    except Exception:
        pass

    content_type = mimetypes.guess_type(relative_path)[0] or "application/octet-stream"
    created_at_value = (created_at or datetime.now(timezone.utc)).isoformat()

    with get_connection() as conn:
        if existing_row:
            conn.execute(
                """
                UPDATE photos
                SET content_type = ?, size_bytes = ?, width = ?, height = ?,
                    created_at = ?, latitude = ?, longitude = ?, thumbnail = ?
                WHERE id = ?
                """,
                (
                    content_type,
                    size,
                    width,
                    height,
                    created_at_value,
                    latitude,
                    longitude,
                    thumbnail_bytes,
                    existing_row["id"],
                ),
            )
            conn.commit()
            return "updated"

        conn.execute(
            """
            INSERT INTO photos
                (id, filename, content_type, size_bytes, width, height, created_at,
                 latitude, longitude, thumbnail)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                relative_path,
                content_type,
                size,
                width,
                height,
                created_at_value,
                latitude,
                longitude,
                thumbnail_bytes,
            ),
        )
        conn.commit()
        return "inserted"


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
        originals_dir = f"{root}/originals"
        paths = await loop.run_in_executor(_executor, fs.find, originals_dir)
        existing = await loop.run_in_executor(_executor, _load_existing)

        progress.start(total=len(paths))

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
    except Exception as exc:
        progress.fail(str(exc))
