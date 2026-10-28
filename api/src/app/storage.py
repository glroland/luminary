import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import UploadFile
from PIL import Image

from .config import get_data_dir
from .database import get_connection

THUMBNAIL_SIZE = (320, 320)


def _originals_dir() -> Path:
    directory = get_data_dir() / "originals"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _thumbnails_dir() -> Path:
    directory = get_data_dir() / "thumbnails"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def save_photo(upload: UploadFile) -> dict:
    photo_id = str(uuid.uuid4())
    suffix = Path(upload.filename or "").suffix or ".jpg"
    original_path = _originals_dir() / f"{photo_id}{suffix}"

    contents = upload.file.read()
    original_path.write_bytes(contents)

    width = height = None
    try:
        with Image.open(original_path) as image:
            width, height = image.size
            thumbnail = image.convert("RGB")
            thumbnail.thumbnail(THUMBNAIL_SIZE)
            thumbnail.save(_thumbnails_dir() / f"{photo_id}.jpg", "JPEG")
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


def find_original_path(photo_id: str) -> Optional[Path]:
    for path in _originals_dir().glob(f"{photo_id}.*"):
        return path
    return None


def find_thumbnail_path(photo_id: str) -> Optional[Path]:
    path = _thumbnails_dir() / f"{photo_id}.jpg"
    return path if path.exists() else None


def delete_photo(photo_id: str) -> bool:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
        conn.commit()
        deleted = cursor.rowcount > 0

    if deleted:
        original = find_original_path(photo_id)
        if original:
            original.unlink(missing_ok=True)
        thumbnail = find_thumbnail_path(photo_id)
        if thumbnail:
            thumbnail.unlink(missing_ok=True)

    return deleted
