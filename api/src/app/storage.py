import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import fsspec
import psycopg
from PIL import Image
from PIL.ExifTags import IFD

from . import embeddings
from .config import get_photos_password, get_photos_uri, get_photos_username
from .database import get_connection

logger = logging.getLogger(__name__)

THUMBNAIL_SIZE = (320, 320)

# Columns returned for listing/metadata lookups. Excludes `thumbnail`, which
# is only fetched by get_thumbnail() -- pulling it in here would mean every
# photo's thumbnail bytes get read off disk just to list filenames.
METADATA_COLUMNS = (
    "id, filename, content_type, size_bytes, width, height, created_at, latitude, longitude, "
    "location_name, vlm_description, description, checksum"
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


def compute_checksum(fs: fsspec.AbstractFileSystem, path: str) -> str:
    """SHA-256 hex digest of the file's raw bytes, reusing _iter_file so a
    large original is streamed in chunks rather than read into memory all
    at once. Called from sync.py whenever a photo's `checksum` column is
    missing or its content has changed -- see _sync_one_file's
    `needs_checksum`."""
    hasher = hashlib.sha256()
    for chunk in _iter_file(fs, path):
        hasher.update(chunk)
    return hasher.hexdigest()


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


# Default size of the result list for find_similar_photos below -- enough
# for a small grid without pulling in most of a modest-sized library.
SIMILAR_PHOTOS_LIMIT = 12


def find_similar_photos(photo_id: str, limit: int = SIMILAR_PHOTOS_LIMIT) -> list[dict]:
    """Nearest neighbors of `photo_id`'s own `description_embedding`, by
    cosine distance (pgvector's `<=>` operator -- see embeddings.py's
    module docstring for the model/embedding behind this column).
    Excludes `photo_id` itself and any photo with no embedding yet (a NULL
    embedding has no defined distance to compare, so it's excluded from
    both sides of the comparison, not just the candidate side). Returns []
    if `photo_id` doesn't exist or has no embedding of its own yet.

    Also returns [] (rather than raising) if `description_embedding`
    doesn't exist as a column at all -- the pgvector extension was never
    installed on this Postgres server, so database.py's init_db() never
    got to create it either (logged there once at startup; logged again
    here, since this is the point where that gap actually becomes
    user-visible)."""
    try:
        with get_connection() as conn:
            row = conn.execute("SELECT description_embedding FROM photos WHERE id = %s", (photo_id,)).fetchone()
            if not row or row["description_embedding"] is None:
                return []
            rows = conn.execute(
                f"SELECT {METADATA_COLUMNS} FROM photos "
                "WHERE id != %s AND description_embedding IS NOT NULL "
                "ORDER BY description_embedding <=> %s LIMIT %s",
                (photo_id, row["description_embedding"], limit),
            ).fetchall()
            return [dict(r) for r in rows]
    except psycopg.errors.UndefinedColumn:
        # get_connection() has already logged the full traceback for this
        # (every psycopg.Error is, regardless of caller) -- just add the
        # pointer to what's actually missing and degrade to an empty
        # result instead of a 500.
        logger.warning(
            "photos.description_embedding doesn't exist -- is the pgvector extension installed? "
            'See db/create_extensions.sql and the README\'s "Database" section.'
        )
        return []


# Default cap on search_photos' result list below -- a safety net against
# returning the whole library for a very loose threshold, not a real
# pagination limit (the sidebar search narrows the grid to however many of
# these actually come back, however many that is).
SEARCH_RESULTS_LIMIT = 500


def search_photos(query: str, threshold: float, limit: int = SEARCH_RESULTS_LIMIT) -> list[dict]:
    """Free-text search over `description` via `description_embedding`
    similarity (the sidebar's "Search" section, GET /api/photos/search) --
    embeds `query` with the same local model as photos' own descriptions
    (embeddings.embed_text, is_query=True for this call since `query` is a
    search query, not a passage -- see embeddings.py's module docstring on
    why that matters for BGE specifically) and returns every photo whose
    cosine similarity to it is at least `threshold` (0-1, higher = more
    similar), ordered most-similar first. Each result dict carries all of
    METADATA_COLUMNS plus a `similarity` key.

    Unlike find_similar_photos, a missing/misconfigured embeddings backend
    is **not** swallowed here -- embeddings.embed_text raises
    EmbeddingNotConfigured (or whatever sentence-transformers/torch itself
    raises) and this lets it propagate, since a user-initiated search
    should surface a real error (the router maps it to a 503/502) rather
    than silently returning no results. A missing `description_embedding`
    column (the pgvector extension was never installed) still degrades to
    [] the same way find_similar_photos does, though -- that's a
    server-side gap, not something a 503 would help the user act on."""
    embedding = embeddings.embed_text(query, is_query=True)
    try:
        with get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM (
                    SELECT {METADATA_COLUMNS}, 1 - (description_embedding <=> %s) AS similarity
                    FROM photos
                    WHERE description_embedding IS NOT NULL
                ) scored
                WHERE similarity >= %s
                ORDER BY similarity DESC
                LIMIT %s
                """,
                (embedding, threshold, limit),
            ).fetchall()
            return [dict(r) for r in rows]
    except psycopg.errors.UndefinedColumn:
        logger.warning(
            "photos.description_embedding doesn't exist -- is the pgvector extension installed? "
            'See db/create_extensions.sql and the README\'s "Database" section.'
        )
        return []


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
        # No FK cascade (see database.py) -- clean up the photo's faces and
        # tags explicitly so they don't outlive their photo row.
        conn.execute("DELETE FROM faces WHERE photo_id = %s", (photo_id,))
        conn.execute("DELETE FROM photo_tags WHERE photo_id = %s", (photo_id,))
        cursor = conn.execute("DELETE FROM photos WHERE id = %s", (photo_id,))
        conn.commit()
        return cursor.rowcount > 0


def purge_all() -> None:
    """Delete every row from every table -- individuals, faces, tags, and
    photos. Like delete_photo, this only ever touches Postgres; the photos
    backend itself is never written to or deleted from. Originals are
    untouched and will simply be re-indexed as new photos the next time
    someone syncs."""
    with get_connection() as conn:
        conn.execute("DELETE FROM faces")
        conn.execute("DELETE FROM individuals")
        conn.execute("DELETE FROM photo_tags")
        conn.execute("DELETE FROM tags")
        conn.execute("DELETE FROM photos")
        conn.commit()


def find_duplicate_photos() -> list[dict]:
    """Groups indexed photos by `checksum` (see sync.py's `needs_checksum`/
    storage.compute_checksum) to find byte-identical files -- backs the
    management page's "Identify Duplicates" button
    (GET /api/photos/duplicates/csv). Rows with a NULL checksum (not yet
    computed, or the file couldn't be read) are excluded from every group,
    not just ignored individually -- a checksum-less photo should never be
    reported as a duplicate, or as the original anything else is a
    duplicate of, since there's nothing to confirm the match.

    Within each group of 2+ photos sharing a checksum, the one with the
    earliest (created_at, filename) is treated as "the original" and
    every *other* file in that group is reported as a duplicate of it --
    (created_at, filename) is just a stable, deterministic tie-breaker,
    not a claim that this file is somehow more authoritative. The
    original's own filename is deliberately never returned in the
    "duplicate" position of any row, including rows for other members of
    the same group: there's always exactly one original per group, by
    construction. A group of 3+ identical files produces one row per
    extra copy, all pointing back at the same original. Returns [] if no
    two indexed photos share a checksum (or no photo has one yet)."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT array_agg(filename ORDER BY created_at, filename) AS filenames
            FROM photos
            WHERE checksum IS NOT NULL
            GROUP BY checksum
            HAVING COUNT(*) > 1
            """
        ).fetchall()
    duplicates = []
    for row in rows:
        original, *copies = row["filenames"]
        duplicates.extend({"original": original, "duplicate": copy} for copy in copies)
    return duplicates


def get_faces(photo_id: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, x1, y1, x2, y2, person_id, tag_source, detection_confidence FROM faces WHERE photo_id = %s",
            (photo_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def replace_faces(photo_id: str, boxes: list[dict]) -> None:
    """Replace every face row for `photo_id` with `boxes` (each an
    {"x1","y1","x2","y2","detection_confidence"} dict in the photo's own
    pixel coordinate space -- same x1/y1/x2/y2 convention as chat.
    ChatReply.boxes, `detection_confidence` as returned by face_detection.
    detect_faces -- see its own docstring). `detection_confidence` is
    optional (`.get(...)`, defaulting to None) only so a caller that still
    passes bare x1-y2 boxes (e.g. a test building one by hand) doesn't have
    to add a field it may not have; every real caller (sync.py,
    recognition.py's full-refresh redetect) gets it from face_detection.
    detect_faces, which always includes it. Always deletes first, even
    when `boxes` is empty: this is sync.py's face-detection step, which is
    re-run from scratch (never incrementally) any time it runs at all, so
    stale boxes from a previous photo version -- or from an earlier
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
                    "INSERT INTO faces (id, photo_id, x1, y1, x2, y2, detection_confidence) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    [
                        (
                            str(uuid.uuid4()),
                            photo_id,
                            box["x1"],
                            box["y1"],
                            box["x2"],
                            box["y2"],
                            box.get("detection_confidence"),
                        )
                        for box in boxes
                    ],
                )
        conn.commit()
