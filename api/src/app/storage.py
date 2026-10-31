import uuid
from datetime import datetime, timezone
from typing import Optional

import fsspec
from PIL import Image
from PIL.ExifTags import IFD

from .config import get_photos_password, get_photos_uri, get_photos_username
from .database import get_connection

THUMBNAIL_SIZE = (320, 320)

# Columns returned for listing/metadata lookups. Excludes `thumbnail`, which
# is only fetched by get_thumbnail() -- pulling it in here would mean every
# photo's thumbnail bytes get read off disk just to list filenames.
METADATA_COLUMNS = (
    "id, filename, content_type, size_bytes, width, height, created_at, latitude, longitude, description"
)

# Protocols whose fsspec filesystem accepts plain `username`/`password`
# kwargs. Other backends authenticate differently (S3 via boto3's standard
# credential chain -- env vars/IAM role -- needs no code here at all; HTTP
# has no generic equivalent) so credentials are only forwarded for these.
_USERNAME_PASSWORD_PROTOCOLS = {"smb"}


def _fs_and_root() -> tuple[fsspec.AbstractFileSystem, str]:
    """Resolve the configured photos URI to an fsspec filesystem and root
    path. Read-only: the app never writes to, creates in, or deletes from
    this backend -- it's treated as someone else's existing photo library
    (originals live directly under the root; there's no app-managed
    subdirectory). Photos only enter the Postgres index via sync.py scanning
    what's already there.

    Credentials (LUMINARY_PHOTOS_USERNAME/_PASSWORD) are passed to fsspec
    as storage_options, not embedded in the URI -- keeps the URI itself
    safe to log/default in a Makefile/commit."""
    uri = get_photos_uri()
    protocol, _ = fsspec.core.split_protocol(uri)

    credentials = {}
    if protocol in _USERNAME_PASSWORD_PROTOCOLS:
        username = get_photos_username()
        password = get_photos_password()
        if username is not None:
            credentials["username"] = username
        if password is not None:
            credentials["password"] = password

    fs, root = fsspec.core.url_to_fs(uri, **credentials)
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
            f"SELECT {METADATA_COLUMNS} FROM photos WHERE id = %s", (photo_id,)
        ).fetchone()
        return dict(row) if row else None


def get_thumbnail(photo_id: str) -> Optional[bytes]:
    with get_connection() as conn:
        row = conn.execute("SELECT thumbnail FROM photos WHERE id = %s", (photo_id,)).fetchone()
        return row["thumbnail"] if row else None


def stream_original(metadata: dict):
    """Return a byte-chunk iterator for the original described by `metadata`
    (as returned by get_photo), or None if the file is missing."""
    fs, root = _fs_and_root()
    path = f"{root}/{metadata['filename']}"
    return _iter_file(fs, path) if fs.exists(path) else None


def read_original_bytes(metadata: dict) -> Optional[bytes]:
    """Return the full original file as bytes (for photo chat, which needs
    the whole image rather than a streamed response), or None if missing."""
    fs, root = _fs_and_root()
    path = f"{root}/{metadata['filename']}"
    if not fs.exists(path):
        return None
    with fs.open(path, "rb") as f:
        return f.read()


def delete_photo(photo_id: str) -> bool:
    """Remove a photo from the Postgres index only. Never touches the file
    on the photos backend -- that's someone else's data, not ours to
    delete. If the file is still there, the next sync will re-index it."""
    with get_connection() as conn:
        # No FK cascade (see database.py) -- clean up the photo's faces
        # explicitly so they don't outlive their photo row.
        conn.execute("DELETE FROM faces WHERE photo_id = %s", (photo_id,))
        cursor = conn.execute("DELETE FROM photos WHERE id = %s", (photo_id,))
        conn.commit()
        return cursor.rowcount > 0


def get_faces(photo_id: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, x1, y1, x2, y2, person_id FROM faces WHERE photo_id = %s", (photo_id,)
        ).fetchall()
        return [dict(row) for row in rows]


def replace_faces(photo_id: str, boxes: list[dict]) -> None:
    """Replace every face row for `photo_id` with `boxes` (each an
    {"x1","y1","x2","y2"} dict in the photo's own pixel coordinate space --
    same convention as chat.ChatReply.boxes). Always deletes first, even
    when `boxes` is empty: this is sync.py's Florence-2 face-detection step,
    which is re-run from scratch (never incrementally) any time it runs at
    all, so stale boxes from a previous photo version -- or from an earlier
    attempt that didn't finish successfully -- must not survive alongside
    (or instead of) a fresh result. See sync.py's module docstring for the
    full before/after-`description` ordering this is part of.

    Note this also discards any `person_id` tags (people.tag_face) on the
    old rows -- new face rows get fresh ids and start untagged, so a photo
    whose content changes loses its face tags along with the stale boxes.
    That's accepted as part of this already-exists wipe-and-redetect
    behavior, not something this function tries to preserve across."""
    with get_connection() as conn:
        conn.execute("DELETE FROM faces WHERE photo_id = %s", (photo_id,))
        if boxes:
            with conn.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO faces (id, photo_id, x1, y1, x2, y2) VALUES (%s, %s, %s, %s, %s, %s)",
                    [(str(uuid.uuid4()), photo_id, box["x1"], box["y1"], box["x2"], box["y2"]) for box in boxes],
                )
        conn.commit()
