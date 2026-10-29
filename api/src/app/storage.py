from datetime import datetime, timezone
from typing import Optional

import fsspec
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
    path. Read-only: the app never writes to, creates in, or deletes from
    this backend -- it's treated as someone else's existing photo library
    (originals live directly under the root; there's no app-managed
    subdirectory). Photos only enter the SQLite index via sync.py scanning
    what's already there."""
    fs, root = fsspec.core.url_to_fs(get_photos_uri())
    return fs, root.rstrip("/") or "/"


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
    """Remove a photo from the SQLite index only. Never touches the file on
    the photos backend -- that's someone else's data, not ours to delete.
    If the file is still there, the next sync will re-index it."""
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
        conn.commit()
        return cursor.rowcount > 0
