import io
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import fsspec
from fastapi import UploadFile
from PIL import Image
from PIL.ExifTags import IFD

from .config import get_photos_uri
from .database import get_connection

THUMBNAIL_SIZE = (320, 320)

# Columns returned for listing/metadata lookups. Excludes `thumbnail`, which
# is only fetched by get_thumbnail() -- pulling it in here would mean every
# photo's thumbnail bytes get read off disk just to list filenames.
METADATA_COLUMNS = "id, filename, content_type, size_bytes, width, height, created_at, latitude, longitude"


def _fs_and_root() -> tuple[fsspec.AbstractFileSystem, str]:
    """Resolve the configured photos URI to an fsspec filesystem and root
    path, creating the `originals` directory if the backend supports it
    (read-only backends like http/https don't, and don't need to)."""
    fs, root = fsspec.core.url_to_fs(get_photos_uri())
    root = root.rstrip("/") or "/"
    try:
        fs.makedirs(f"{root}/originals", exist_ok=True)
    except NotImplementedError:
        pass
    return fs, root


def _iter_file(fs: fsspec.AbstractFileSystem, path: str, chunk_size: int = 1024 * 1024):
    with fs.open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            yield chunk


def _dms_to_decimal(dms, ref) -> Optional[float]:
    if not dms or not ref:
        return None
    degrees, minutes, seconds = (float(part) for part in dms)
    decimal = degrees + minutes / 60 + seconds / 3600
    return -decimal if ref in ("S", "W") else decimal


def _extract_exif(image: Image.Image) -> tuple[Optional[datetime], Optional[float], Optional[float]]:
    """Best-effort read of capture time and GPS position from EXIF. Returns
    (created_at, latitude, longitude), any of which may be None."""
    try:
        exif = image.getexif()
    except Exception:
        return None, None, None

    exif_ifd = {}
    try:
        exif_ifd = exif.get_ifd(IFD.Exif)
    except Exception:
        pass

    raw_datetime = exif_ifd.get(36867) or exif_ifd.get(36868) or exif.get(306)
    created_at = None
    if raw_datetime:
        try:
            # EXIF timestamps carry no timezone; treated as UTC for a
            # single consistent, sortable representation in the database.
            created_at = datetime.strptime(raw_datetime, "%Y:%m:%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            created_at = None

    try:
        gps_ifd = exif.get_ifd(IFD.GPSInfo)
    except Exception:
        gps_ifd = {}

    latitude = _dms_to_decimal(gps_ifd.get(2), gps_ifd.get(1)) if gps_ifd else None
    longitude = _dms_to_decimal(gps_ifd.get(4), gps_ifd.get(3)) if gps_ifd else None

    return created_at, latitude, longitude


def save_photo(upload: UploadFile) -> dict:
    photo_id = str(uuid.uuid4())
    suffix = Path(upload.filename or "").suffix or ".jpg"
    relative_path = f"originals/{photo_id}{suffix}"

    fs, root = _fs_and_root()
    original_path = f"{root}/{relative_path}"

    contents = upload.file.read()
    with fs.open(original_path, "wb") as f:
        f.write(contents)

    width = height = None
    created_at = latitude = longitude = None
    thumbnail_bytes = None
    try:
        with fs.open(original_path, "rb") as f:
            with Image.open(f) as image:
                width, height = image.size
                created_at, latitude, longitude = _extract_exif(image)

                thumbnail = image.convert("RGB")
                thumbnail.thumbnail(THUMBNAIL_SIZE)
                buffer = io.BytesIO()
                thumbnail.save(buffer, "JPEG")
                thumbnail_bytes = buffer.getvalue()
    except Exception:
        # Not a readable image format (e.g. no thumbnail support) -- the
        # original is still stored and metadata is still recorded.
        pass

    metadata = {
        "id": photo_id,
        "filename": relative_path,
        "content_type": upload.content_type or "application/octet-stream",
        "size_bytes": len(contents),
        "width": width,
        "height": height,
        "created_at": (created_at or datetime.now(timezone.utc)).isoformat(),
        "latitude": latitude,
        "longitude": longitude,
    }

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO photos
                (id, filename, content_type, size_bytes, width, height, created_at,
                 latitude, longitude, thumbnail)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                metadata["id"],
                metadata["filename"],
                metadata["content_type"],
                metadata["size_bytes"],
                metadata["width"],
                metadata["height"],
                metadata["created_at"],
                metadata["latitude"],
                metadata["longitude"],
                thumbnail_bytes,
            ),
        )
        conn.commit()

    return metadata


def list_photos() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT {METADATA_COLUMNS} FROM photos ORDER BY created_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]


def get_photo(photo_id: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            f"SELECT {METADATA_COLUMNS} FROM photos WHERE id = ?", (photo_id,)
        ).fetchone()
        return dict(row) if row else None


def get_thumbnail(photo_id: str) -> Optional[bytes]:
    with get_connection() as conn:
        row = conn.execute("SELECT thumbnail FROM photos WHERE id = ?", (photo_id,)).fetchone()
        return row["thumbnail"] if row else None


def stream_original(metadata: dict):
    """Return a byte-chunk iterator for the original described by `metadata`
    (as returned by get_photo), or None if the file is missing."""
    fs, root = _fs_and_root()
    path = f"{root}/{metadata['filename']}"
    return _iter_file(fs, path) if fs.exists(path) else None


def delete_photo(photo_id: str) -> bool:
    with get_connection() as conn:
        row = conn.execute("SELECT filename FROM photos WHERE id = ?", (photo_id,)).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
        conn.commit()

    fs, root = _fs_and_root()
    try:
        fs.rm(f"{root}/{row['filename']}")
    except FileNotFoundError:
        pass

    return True
