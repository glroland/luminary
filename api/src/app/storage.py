import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import fsspec
from fastapi import UploadFile
from PIL import Image

from .config import get_photos_uri
from .database import get_connection

THUMBNAIL_SIZE = (320, 320)


def _storage() -> tuple[fsspec.AbstractFileSystem, str, str]:
    """Resolve the configured photos URI to an fsspec filesystem plus the
    originals/thumbnails directories under it, creating them if the backend
    supports it (read-only backends like http/https don't, and don't need
    to)."""
    fs, root = fsspec.core.url_to_fs(get_photos_uri())
    root = root.rstrip("/") or "/"
    originals = f"{root}/originals"
    thumbnails = f"{root}/thumbnails"
    for directory in (originals, thumbnails):
        try:
            fs.makedirs(directory, exist_ok=True)
        except NotImplementedError:
            pass
    return fs, originals, thumbnails


def _iter_file(fs: fsspec.AbstractFileSystem, path: str, chunk_size: int = 1024 * 1024):
    with fs.open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            yield chunk


def save_photo(upload: UploadFile) -> dict:
    photo_id = str(uuid.uuid4())
    suffix = Path(upload.filename or "").suffix or ".jpg"
    fs, originals_dir, thumbnails_dir = _storage()
    original_path = f"{originals_dir}/{photo_id}{suffix}"

    contents = upload.file.read()
    with fs.open(original_path, "wb") as f:
        f.write(contents)

    width = height = None
    try:
        with fs.open(original_path, "rb") as f:
            with Image.open(f) as image:
                width, height = image.size
                thumbnail = image.convert("RGB")
                thumbnail.thumbnail(THUMBNAIL_SIZE)
                with fs.open(f"{thumbnails_dir}/{photo_id}.jpg", "wb") as thumb_file:
                    thumbnail.save(thumb_file, "JPEG")
    except Exception:
        # Not a readable image format (e.g. no thumbnail support) -- the
        # original is still stored and metadata is still recorded.
        pass

    metadata = {
        "id": photo_id,
        "filename": upload.filename or f"{photo_id}{suffix}",
        "content_type": upload.content_type or "application/octet-stream",
        "size_bytes": len(contents),
        "width": width,
        "height": height,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO photos
                (id, filename, content_type, size_bytes, width, height, uploaded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                metadata["id"],
                metadata["filename"],
                metadata["content_type"],
                metadata["size_bytes"],
                metadata["width"],
                metadata["height"],
                metadata["uploaded_at"],
            ),
        )
        conn.commit()

    return metadata


def list_photos() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM photos ORDER BY uploaded_at DESC").fetchall()
        return [dict(row) for row in rows]


def get_photo(photo_id: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
        return dict(row) if row else None


def _find_original(fs: fsspec.AbstractFileSystem, originals_dir: str, photo_id: str) -> Optional[str]:
    matches = fs.glob(f"{originals_dir}/{photo_id}.*")
    return matches[0] if matches else None


def stream_original(photo_id: str):
    """Return a byte-chunk iterator for the original file, or None if missing."""
    fs, originals_dir, _ = _storage()
    path = _find_original(fs, originals_dir, photo_id)
    return _iter_file(fs, path) if path else None


def stream_thumbnail(photo_id: str):
    """Return a byte-chunk iterator for the thumbnail, or None if missing."""
    fs, _, thumbnails_dir = _storage()
    path = f"{thumbnails_dir}/{photo_id}.jpg"
    return _iter_file(fs, path) if fs.exists(path) else None


def delete_photo(photo_id: str) -> bool:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
        conn.commit()
        deleted = cursor.rowcount > 0

    if deleted:
        fs, originals_dir, thumbnails_dir = _storage()
        original = _find_original(fs, originals_dir, photo_id)
        if original:
            fs.rm(original)
        thumbnail = f"{thumbnails_dir}/{photo_id}.jpg"
        if fs.exists(thumbnail):
            fs.rm(thumbnail)

    return deleted
