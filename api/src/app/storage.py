import io
import hashlib
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterator, Optional

import fsspec
import psycopg
from PIL import Image, ImageOps
from PIL.ExifTags import IFD

from . import backend_connection, config, embeddings, perceptual
from . import rerank as rerank_module
from .database import get_connection

logger = logging.getLogger(__name__)

THUMBNAIL_SIZE = (320, 320)

# Columns returned for listing/metadata lookups. Excludes `thumbnail`, which
# is only fetched by get_thumbnail() -- pulling it in here would mean every
# photo's thumbnail bytes get read off disk just to list filenames.
METADATA_COLUMNS = (
    "id, filename, content_type, size_bytes, width, height, created_at, latitude, longitude, "
    "location_name, location_country, location_state, location_city, vlm_description, description, checksum, "
    "media_kind, duration_seconds, rotation"
)

def _fs_and_root() -> tuple[fsspec.AbstractFileSystem, str]:
    """The photos backend's fsspec filesystem and root path. Read-only: the
    app never writes to, creates in, or deletes from this backend -- it's
    treated as someone else's existing photo library (originals live
    directly under the root; there's no app-managed subdirectory). Photos
    only enter the Postgres index via sync.py scanning what's already there.

    The connection itself (credentials passed as storage_options rather
    than embedded in the URI, one cached instance, keepalive, observable
    progress) lives in backend_connection.py."""
    return backend_connection.get_fs_and_root()


def _with_reconnect(fn):
    """Run `fn(fs, root)` against the backend, retrying transient SMB errors
    and, if the connection itself has died (an idle session the server
    dropped), reconnecting once and trying again."""
    fs, root = _fs_and_root()
    try:
        return retry_transient(fn, fs, root)
    except Exception as exc:
        if not backend_connection.is_connection_error(exc):
            raise
        logger.warning("Photos backend connection failed (%s); reconnecting", exc)
        fs, root = backend_connection.reconnect(fs)
        return retry_transient(fn, fs, root)


# SMB flow control: every in-flight request spends "credits" the server
# hands back with its response. All threads share one SMB connection, and
# smbprotocol raises immediately rather than waiting when concurrent
# requests have the pool at zero -- transient by nature, it clears as soon
# as any other request completes.
_TRANSIENT_RETRY_ATTEMPTS = 5
_TRANSIENT_RETRY_INITIAL_DELAY = 0.2


def _is_transient_backend_error(exc: Exception) -> bool:
    # Matched on the message rather than the exception class so this module
    # doesn't need smbprotocol importable for non-SMB backends.
    return "credits are available" in str(exc)


def retry_transient(fn, *args, **kwargs):
    """Call `fn`, retrying with exponential backoff on a transient photos
    backend error (see _is_transient_backend_error). Anything else, or the
    last attempt's failure, propagates unchanged. `fn` must be safe to
    call more than once."""
    delay = _TRANSIENT_RETRY_INITIAL_DELAY
    for attempt in range(1, _TRANSIENT_RETRY_ATTEMPTS + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if attempt == _TRANSIENT_RETRY_ATTEMPTS or not _is_transient_backend_error(exc):
                raise
            logger.debug("Transient photos backend error (attempt %d), retrying: %s", attempt, exc)
            time.sleep(delay)
            delay *= 2


def _iter_file(
    fs: fsspec.AbstractFileSystem,
    path: str,
    chunk_size: int = 1024 * 1024,
    start: int = 0,
    length: Optional[int] = None,
):
    """The file's bytes in chunks; `start`/`length` select a byte range
    (HTTP Range requests for video playback)."""
    with fs.open(path, "rb") as f:
        if start:
            f.seek(start)
        remaining = length
        while remaining is None or remaining > 0:
            chunk = f.read(chunk_size if remaining is None else min(chunk_size, remaining))
            if not chunk:
                break
            if remaining is not None:
                remaining -= len(chunk)
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


# XMP properties that hold a capture date, most specific first. An iPhone
# screenshot (PNG) carries no EXIF at all, only an XMP packet with
# photoshop:DateCreated.
_XMP_DATE_PROPERTIES = ("exif:DateTimeOriginal", "photoshop:DateCreated", "xmp:CreateDate")


def _extract_xmp_date(image: Image.Image) -> Optional[datetime]:
    """Best-effort read of the capture date from the image's XMP packet.
    A plain regex over the packet, not an XML parse: the value is either
    an element's text or an attribute, and neither needs a new dependency."""
    packet = image.info.get("xmp") or image.info.get("XML:com.adobe.xmp")
    if isinstance(packet, bytes):
        packet = packet.decode("utf-8", errors="ignore")
    if not isinstance(packet, str):
        return None
    for name in _XMP_DATE_PROPERTIES:
        match = re.search(re.escape(name) + r'\s*(?:=\s*["\']|>)\s*([^"\'<]+)', packet)
        if not match:
            continue
        try:
            parsed = datetime.fromisoformat(match.group(1).strip())
        except ValueError:
            continue
        # Same convention as the EXIF date below: the wall-clock time as
        # written, labelled UTC, whatever offset the packet gives.
        return parsed.replace(tzinfo=timezone.utc)
    return None


def file_modified_at(info: dict) -> Optional[datetime]:
    """The file's modification time, from the `fs.info()` result sync
    already has (so no extra backend call), or None where the backend
    reports none (plain HTTP). fsspec has no single key for it: local and
    SMB give `mtime` as epoch seconds, S3 gives `LastModified` as a
    datetime."""
    value = info.get("mtime")
    if value is None:
        value = info.get("LastModified")
    if isinstance(value, (int, float)):
        modified = datetime.fromtimestamp(value, tz=timezone.utc)
    elif isinstance(value, datetime):
        modified = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    else:
        return None
    # A time in the future is a corrupt one -- no date beats a wrong one.
    if modified > datetime.now(timezone.utc):
        return None
    return modified


def _extract_exif(image: Image.Image) -> tuple[Optional[datetime], Optional[float], Optional[float]]:
    """Best-effort read of capture time and GPS position from the image's
    embedded metadata: EXIF, then (for the date only) XMP. Returns
    (created_at, latitude, longitude), any of which may be None."""
    try:
        exif = image.getexif()
    except Exception:
        exif = Image.Exif()

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
    if created_at is None:
        try:
            created_at = _extract_xmp_date(image)
        except Exception:
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
            f"SELECT {METADATA_COLUMNS} FROM photos ORDER BY created_at DESC NULLS LAST"
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


def get_thumbnail_etag(photo_id: str) -> Optional[str]:
    """md5 of the stored thumbnail plus the photo's rotation, computed in
    Postgres so the blob itself isn't transferred -- lets the thumbnail
    endpoint answer a conditional request (If-None-Match) with a 304 without
    reading the bytes into the API. The rotation is part of the tag because
    the served image is the stored one rotated by it."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT md5(thumbnail) || '-' || rotation AS etag FROM photos "
            "WHERE id = %s AND thumbnail IS NOT NULL",
            (photo_id,),
        ).fetchone()
        return row["etag"] if row else None


def get_rotated_thumbnail(photo_id: str) -> Optional[tuple[bytes, str]]:
    """The photo's thumbnail as it should be displayed -- the stored (upright)
    one turned by the photo's rotation -- as `(bytes, content_type)`."""
    with get_connection() as conn:
        row = conn.execute("SELECT thumbnail, rotation FROM photos WHERE id = %s", (photo_id,)).fetchone()
    if not row or not row["thumbnail"]:
        return None
    thumbnail = bytes(row["thumbnail"])
    if not row["rotation"]:
        return thumbnail, "image/jpeg"
    return render_rotated(thumbnail, row["rotation"])


# --- Rotation ---------------------------------------------------------------
# A user-set display rotation for a photo whose pixels are stored at the wrong
# angle (a scan, a camera with no orientation sensor). It lives only in
# photos.rotation (degrees clockwise, a multiple of ROTATION_STEP, 0-359): the
# original on the photos backend is never modified, and the stored thumbnail
# stays the upright one -- both are turned when served, so setting it back to
# 0 is lossless and a re-sync (which rebuilds the thumbnail upright) never has
# to know about it.

ROTATION_STEP = 45


def normalize_rotation(degrees: int) -> int:
    """`degrees` (any sign) as 0-359. Raises ValueError unless it's a multiple
    of ROTATION_STEP."""
    if isinstance(degrees, bool) or not isinstance(degrees, int):
        raise ValueError("rotation must be an integer number of degrees")
    if degrees % ROTATION_STEP:
        raise ValueError(f"rotation must be a multiple of {ROTATION_STEP} degrees")
    return degrees % 360


def render_rotated(data: bytes, rotation: int, *, exif_transpose: bool = False) -> tuple[bytes, str]:
    """`data` (an image) turned `rotation` degrees clockwise, as
    `(bytes, content_type)`. A quarter turn is an exact pixel transpose,
    encoded as JPEG; any other angle needs a larger canvas whose corners are
    empty, so it's encoded as WebP with transparency (the page background
    shows through) -- JPEG has no alpha. `exif_transpose` first bakes in the
    file's own orientation tag: the angle the user sees and corrected from."""
    with Image.open(io.BytesIO(data)) as raw:
        image = (ImageOps.exif_transpose(raw) or raw) if exif_transpose else raw
        has_alpha = "A" in image.getbands() or "transparency" in image.info
        quarter_turns = rotation % 90 == 0
        buffer = io.BytesIO()
        if quarter_turns:
            turned = {
                90: Image.Transpose.ROTATE_270,
                180: Image.Transpose.ROTATE_180,
                270: Image.Transpose.ROTATE_90,
            }.get(rotation % 360)
            result = image.transpose(turned) if turned is not None else image.copy()
            if not has_alpha:
                result.convert("RGB").save(buffer, "JPEG", quality=90)
                return buffer.getvalue(), "image/jpeg"
            result.convert("RGBA").save(buffer, "WEBP", quality=90)
            return buffer.getvalue(), "image/webp"
        # PIL rotates counter-clockwise; ours is clockwise.
        result = image.convert("RGBA").rotate(-rotation, resample=Image.Resampling.BICUBIC, expand=True)
        result.save(buffer, "WEBP", quality=88)
        return buffer.getvalue(), "image/webp"


def set_rotation(photo_id: str, rotation: int) -> bool:
    """Store `rotation` (already normalized) for a photo. False if it doesn't
    exist. Postgres only -- the photos backend is never touched."""
    with get_connection() as conn:
        cursor = conn.execute("UPDATE photos SET rotation = %s WHERE id = %s", (rotation, photo_id))
        conn.commit()
        return cursor.rowcount > 0


# Default size of the result list for find_similar_photos below -- enough
# for a small grid without pulling in most of a modest-sized library.
SIMILAR_PHOTOS_LIMIT = 12


def find_similar_photos(photo_id: str, limit: int = SIMILAR_PHOTOS_LIMIT) -> list[dict]:
    """Photos whose description sentences read most like `photo_id`'s, by
    cosine distance over `photo_embeddings` (pgvector's `<=>` operator --
    see embeddings.py). A candidate's score is the mean, over each of this
    photo's sentences, of that sentence's best match among the candidate's
    sentences. Excludes `photo_id` itself and photos with no embedded
    sentences. Returns [] if `photo_id` has none of its own yet.

    Also returns [] (rather than raising) if `photo_embeddings.embedding`
    doesn't exist -- the pgvector extension was never installed on this
    server, so database.py's init_db() never created it."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT {METADATA_COLUMNS} FROM photos
                JOIN (
                    SELECT photo_id, AVG(best) AS score FROM (
                        SELECT o.photo_id, MAX(1 - (s.embedding <=> o.embedding)) AS best
                        FROM photo_embeddings s
                        JOIN photo_embeddings o ON o.photo_id != s.photo_id AND o.embedding IS NOT NULL
                        WHERE s.photo_id = %s AND s.embedding IS NOT NULL
                        GROUP BY o.photo_id, s.id
                    ) per_sentence
                    GROUP BY photo_id
                ) ranked ON ranked.photo_id = photos.id
                ORDER BY ranked.score DESC
                LIMIT %s
                """,
                (photo_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]
    except psycopg.errors.UndefinedColumn:
        logger.warning(
            "photo_embeddings.embedding doesn't exist -- is the pgvector extension installed? "
            'See db/create_extensions.sql and the README\'s "Database" section.'
        )
        return []


# Default cap on search_photos' result list below -- a safety net against
# returning the whole library for a very loose threshold, not a real
# pagination limit (the gallery's "Search Results" shows however many of
# these actually come back, however many that is).
SEARCH_RESULTS_LIMIT = 500


def _search_by_embedding(
    query: str, threshold: float, limit: int = SEARCH_RESULTS_LIMIT, photo_ids: Optional[list[str]] = None
) -> list[dict]:
    """Stage 1 of search_photos: sentence-embedding similarity (a photo
    scores as its best-matching sentence). Propagates
    EmbeddingNotConfigured; a missing `embedding` column degrades to []."""
    embedding = embeddings.embed_text(query, is_query=True)
    try:
        with get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT {METADATA_COLUMNS}, scored.similarity
                FROM photos
                JOIN (
                    SELECT photo_id, MAX(1 - (embedding <=> %s)) AS similarity
                    FROM photo_embeddings
                    WHERE embedding IS NOT NULL
                      AND (%s::text[] IS NULL OR photo_id = ANY(%s::text[]))
                    GROUP BY photo_id
                ) scored ON scored.photo_id = photos.id
                WHERE scored.similarity >= %s
                ORDER BY scored.similarity DESC
                LIMIT %s
                """,
                (embedding, photo_ids, photo_ids, threshold, limit),
            ).fetchall()
            return [dict(r) for r in rows]
    except psycopg.errors.UndefinedColumn:
        logger.warning(
            "photo_embeddings.embedding doesn't exist -- is the pgvector extension installed? "
            'See db/create_extensions.sql and the README\'s "Database" section.'
        )
        return []


# Stage 1 never applies a threshold stricter than this when the rerank stage
# will re-score the candidates -- a photo the cross-encoder would rank well
# can have a mediocre best-sentence cosine similarity, so cutting at the
# user's level first would hide it from the reranker.
RERANK_RETRIEVAL_FLOOR = 0.3


def search_photos(
    query: str,
    threshold: float,
    limit: int = SEARCH_RESULTS_LIMIT,
    photo_ids: Optional[list[str]] = None,
    rerank: Optional[bool] = None,
) -> list[dict]:
    """Free-text search over `description` (chat.py's search_photos_semantic
    tool, GET /api/photos/search), in two stages:

    1. Embedding similarity over `photo_embeddings` (embeddings.embed_text
       with is_query=True -- see embeddings.py): a photo scores as its
       best-matching sentence. When reranking is on, this takes the best
       `LUMINARY_RERANK_CANDIDATES` photos above a loose floor
       (RERANK_RETRIEVAL_FLOOR) instead of applying `threshold`.
    2. rerank.py: a cross-encoder re-scores those candidates against their
       whole `description`. The final `similarity` (0-1) is that score,
       `threshold` is applied to it, and results are ordered by it.

    If reranking is off or unavailable the stage-1 result is returned as
    before: `threshold` applies to the cosine similarity. `rerank` forces
    it on/off (None = whatever LUMINARY_RERANKER_MODEL says); the eval
    script uses that to compare. Each result carries METADATA_COLUMNS plus
    `similarity` (and `embedding_similarity`, the stage-1 score, when
    reranked).

    `photo_ids` restricts the search to those photos (None = the whole
    library); it's how chat.py filters its search list by a second search,
    applied in SQL before the limit. EmbeddingNotConfigured propagates (a
    user-initiated search should surface a real error); a missing pgvector
    column degrades to []."""
    want_rerank = bool(config.get_reranker_model()) if rerank is None else rerank
    if not want_rerank:
        return _search_by_embedding(query, threshold, limit, photo_ids)

    candidates = _search_by_embedding(
        query, min(threshold, RERANK_RETRIEVAL_FLOOR), config.get_rerank_candidates(), photo_ids
    )
    scores = rerank_module.rerank_scores(query, [c.get("description") or "" for c in candidates])
    if scores is None:
        return [c for c in candidates if c["similarity"] >= threshold][:limit]

    for candidate, score in zip(candidates, scores):
        candidate["embedding_similarity"] = candidate["similarity"]
        candidate["similarity"] = score
    kept = [c for c in candidates if c["similarity"] >= threshold]
    kept.sort(key=lambda c: c["similarity"], reverse=True)
    return kept[:limit]


# Appended to a search's WHERE clause; takes the `photo_ids` argument twice
# (None = no restriction). See search_photos' docstring.
_RESTRICT_TO_IDS = "(%s::text[] IS NULL OR id = ANY(%s::text[]))"


def _like_patterns(text: str) -> list[str]:
    """One ILIKE pattern per whitespace-separated word of `text`, with
    LIKE's own wildcards escaped so a "%" or "_" in a search (a filename
    like "IMG_1234") matches literally."""
    patterns = []
    for term in text.split():
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        patterns.append(f"%{escaped}%")
    return patterns


def search_photos_by_filename(
    text: str, limit: int = SEARCH_RESULTS_LIMIT, photo_ids: Optional[list[str]] = None
) -> list[dict]:
    """Photos whose `filename` -- the path relative to the photos root, so
    folders too -- contains every word of `text`, case-insensitively,
    ordered by filename. Backs chat.py's search_photos_by_filename tool."""
    patterns = _like_patterns(text)
    if not patterns:
        return []
    where = " AND ".join(["filename ILIKE %s"] * len(patterns))
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT {METADATA_COLUMNS} FROM photos WHERE {where} AND {_RESTRICT_TO_IDS} "
            "ORDER BY filename LIMIT %s",
            (*patterns, photo_ids, photo_ids, limit),
        ).fetchall()
        return [dict(row) for row in rows]


# Everything search_photos_by_text looks through for one word: the photo's
# own text columns, the names of people tagged in it, and its tags.
_TEXT_SEARCH_TERM = """(
    p.filename ILIKE %(t{n})s
    OR p.description ILIKE %(t{n})s
    OR p.vlm_description ILIKE %(t{n})s
    OR p.location_name ILIKE %(t{n})s
    OR EXISTS (
        SELECT 1 FROM faces f JOIN individuals i ON i.id = f.person_id
        WHERE f.photo_id = p.id AND i.name ILIKE %(t{n})s
    )
    OR EXISTS (
        SELECT 1 FROM photo_tags pt JOIN tags t ON t.id = pt.tag_id
        WHERE pt.photo_id = p.id AND t.name ILIKE %(t{n})s
    )
)"""


def search_photos_by_text(
    text: str, limit: int = SEARCH_RESULTS_LIMIT, photo_ids: Optional[list[str]] = None
) -> list[dict]:
    """Plain case-insensitive text search across everything textual known
    about a photo: filename/path, description, the raw Florence-2 caption,
    location name, tagged individuals' names, and tag names. Every word of
    `text` must appear somewhere, though not all in the same field
    ("beach alice" matches a beach photo Alice is tagged in). Newest
    first. Backs chat.py's search_photos_by_text tool."""
    patterns = _like_patterns(text)
    if not patterns:
        return []
    where = " AND ".join(_TEXT_SEARCH_TERM.format(n=n) for n in range(len(patterns)))
    params = {f"t{n}": pattern for n, pattern in enumerate(patterns)}
    params["limit"] = limit
    params["ids"] = photo_ids
    columns = ", ".join(f"p.{column.strip()}" for column in METADATA_COLUMNS.split(","))
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT {columns} FROM photos p WHERE {where} "
            "AND (%(ids)s::text[] IS NULL OR p.id = ANY(%(ids)s::text[])) "
            "ORDER BY p.created_at DESC NULLS LAST LIMIT %(limit)s",
            params,
        ).fetchall()
        return [dict(row) for row in rows]


def list_individual_names() -> list[dict]:
    """Every catalogued individual as {"name", "photo_count"} (photos with
    at least one face tagged to them, manually or automatically), by name.
    Backs chat.py's list_individuals tool."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT i.name, COUNT(DISTINCT f.photo_id) AS photo_count
            FROM individuals i
            LEFT JOIN faces f ON f.person_id = i.id
            GROUP BY i.id, i.name
            ORDER BY i.name
            """
        ).fetchall()
        return [dict(row) for row in rows]


def list_tag_names() -> list[dict]:
    """Every tag as {"name", "photo_count"}, by name. Backs chat.py's
    list_tags tool."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT t.name, COUNT(pt.photo_id) AS photo_count
            FROM tags t
            LEFT JOIN photo_tags pt ON pt.tag_id = t.id
            GROUP BY t.id, t.name
            ORDER BY t.name
            """
        ).fetchall()
        return [dict(row) for row in rows]


def _match_names(conn, table: str, name: str) -> list[dict]:
    """Rows ({"id", "name"}) of `table` (individuals or tags) that `name`
    refers to: an exact match ignoring case if there is one, otherwise
    every name containing it -- so "alice" finds "Alice Smith", but
    doesn't also drag in "Alice Jones" when there's someone called just
    "Alice"."""
    exact = conn.execute(
        f"SELECT id, name FROM {table} WHERE lower(name) = lower(%s) ORDER BY name", (name.strip(),)
    ).fetchall()
    if exact:
        return [dict(row) for row in exact]
    patterns = _like_patterns(name)
    if not patterns:
        return []
    where = " AND ".join(["name ILIKE %s"] * len(patterns))
    rows = conn.execute(f"SELECT id, name FROM {table} WHERE {where} ORDER BY name", patterns).fetchall()
    return [dict(row) for row in rows]


def search_photos_by_individual(
    name: str, limit: int = SEARCH_RESULTS_LIMIT, photo_ids: Optional[list[str]] = None
) -> tuple[list[str], list[dict]]:
    """Photos with a face tagged (manually or automatically) to the
    individual(s) `name` refers to -- see _match_names. Returns (the names
    matched, the photos, newest first); both empty when nobody matches.
    Backs chat.py's search_photos_by_individual tool."""
    with get_connection() as conn:
        matched = _match_names(conn, "individuals", name)
        if not matched:
            return [], []
        rows = conn.execute(
            f"SELECT {METADATA_COLUMNS} FROM photos WHERE id IN "
            f"(SELECT photo_id FROM faces WHERE person_id = ANY(%s)) AND {_RESTRICT_TO_IDS} "
            "ORDER BY created_at DESC NULLS LAST LIMIT %s",
            ([row["id"] for row in matched], photo_ids, photo_ids, limit),
        ).fetchall()
        return [row["name"] for row in matched], [dict(row) for row in rows]


def search_photos_by_tag(
    name: str, limit: int = SEARCH_RESULTS_LIMIT, photo_ids: Optional[list[str]] = None
) -> tuple[list[str], list[dict]]:
    """Photos carrying the tag(s) `name` refers to -- same matching and
    return shape as search_photos_by_individual. Backs chat.py's
    search_photos_by_tag tool."""
    with get_connection() as conn:
        matched = _match_names(conn, "tags", name)
        if not matched:
            return [], []
        rows = conn.execute(
            f"SELECT {METADATA_COLUMNS} FROM photos WHERE id IN "
            f"(SELECT photo_id FROM photo_tags WHERE tag_id = ANY(%s)) AND {_RESTRICT_TO_IDS} "
            "ORDER BY created_at DESC NULLS LAST LIMIT %s",
            ([row["id"] for row in matched], photo_ids, photo_ids, limit),
        ).fetchall()
        return [row["name"] for row in matched], [dict(row) for row in rows]


def stream_original(metadata: dict):
    """Return a byte-chunk iterator for the original described by `metadata`
    (as returned by get_photo), or None if the file is missing."""

    def open_stream(fs, root):
        path = f"{root}/{metadata['filename']}"
        return _iter_file(fs, path) if fs.exists(path) else None

    return _with_reconnect(open_stream)


class RangeNotSatisfiable(Exception):
    """The requested byte range lies outside the file (HTTP 416)."""

    def __init__(self, total: int):
        super().__init__(f"range not satisfiable for a {total}-byte file")
        self.total = total


@dataclass
class OriginalRange:
    total: int  # size of the whole file
    start: int
    end: int  # inclusive
    chunks: Iterator[bytes]
    partial: bool = True  # False: the header wasn't a usable range, this is the whole file


_RANGE_RE = re.compile(r"^\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*$")


def parse_byte_range(header: str, total: int) -> Optional[tuple[int, int]]:
    """Resolve a single-range `Range: bytes=...` header against a file of
    `total` bytes to an inclusive (start, end). None if the header isn't a
    single byte range we understand (the caller then serves the whole file,
    which HTTP allows); raises RangeNotSatisfiable if it is one that falls
    outside the file. `a-b`, `a-` and the suffix form `-n` are supported."""
    match = _RANGE_RE.match(header or "")
    if not match:
        return None
    first, last = match.groups()
    if first == "" and last == "":
        return None
    if first == "":  # suffix: the last n bytes
        length = int(last)
        if length == 0 or total == 0:
            raise RangeNotSatisfiable(total)
        return max(0, total - length), total - 1
    start = int(first)
    end = total - 1 if last == "" else min(int(last), total - 1)
    if start >= total or end < start:
        raise RangeNotSatisfiable(total)
    return start, end


def stream_original_range(metadata: dict, range_header: str) -> Optional[OriginalRange]:
    """Like stream_original, for one `Range` header. The size comes from the
    backend itself (not the indexed `size_bytes`, which can be stale), so the
    Content-Range/Content-Length the caller sends are always true. None if
    the file is missing. A header that isn't a single byte range gives the
    whole file with `partial=False`; one outside the file raises
    RangeNotSatisfiable (HTTP 416)."""

    def open_range(fs, root):
        path = f"{root}/{metadata['filename']}"
        if not fs.exists(path):
            return None
        total = int(retry_transient(fs.info, path).get("size") or 0)
        resolved = parse_byte_range(range_header, total)
        if resolved is None:
            return OriginalRange(total, 0, total - 1, _iter_file(fs, path), partial=False)
        start, end = resolved
        return OriginalRange(total, start, end, _iter_file(fs, path, start=start, length=end - start + 1))

    return _with_reconnect(open_range)


def read_original_bytes(metadata: dict) -> Optional[bytes]:
    """Return the full original file as bytes (for photo chat, which needs
    the whole image rather than a streamed response), or None if missing."""

    def read(fs, root) -> Optional[bytes]:
        path = f"{root}/{metadata['filename']}"
        if not fs.exists(path):
            return None
        with fs.open(path, "rb") as f:
            return f.read()

    return _with_reconnect(read)


# Formats sync indexes that browsers other than Safari can't render in an
# <img> -- the original is transcoded to JPEG on its way out instead (see
# routers/photos.py's get_photo_original). In memory only: nothing is
# written back to the photos backend.
TRANSCODED_CONTENT_TYPES = {"image/heic", "image/heif"}


def transcode_to_jpeg(original_bytes: bytes) -> bytes:
    """Decode `original_bytes` and re-encode as a full-resolution JPEG,
    with any EXIF orientation baked in."""
    with Image.open(io.BytesIO(original_bytes)) as raw_image:
        image = ImageOps.exif_transpose(raw_image) or raw_image
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, "JPEG", quality=90)
        return buffer.getvalue()


def delete_photo(photo_id: str) -> bool:
    """Remove a photo from the Postgres index only. Never touches the file
    on the photos backend -- that's someone else's data, not ours to
    delete. If the file is still there, the next sync will re-index it."""
    with get_connection() as conn:
        # No FK cascade (see database.py) -- clean up the photo's faces and
        # tags explicitly so they don't outlive their photo row.
        conn.execute("DELETE FROM faces WHERE photo_id = %s", (photo_id,))
        conn.execute("DELETE FROM face_rejections WHERE photo_id = %s", (photo_id,))
        conn.execute("DELETE FROM photo_tags WHERE photo_id = %s", (photo_id,))
        conn.execute("DELETE FROM trip_photos WHERE photo_id = %s", (photo_id,))
        conn.execute("DELETE FROM trip_exclusions WHERE photo_id = %s", (photo_id,))
        conn.execute("DELETE FROM photo_embeddings WHERE photo_id = %s", (photo_id,))
        cursor = conn.execute("DELETE FROM photos WHERE id = %s", (photo_id,))
        conn.commit()
        return cursor.rowcount > 0


def purge_all() -> None:
    """Delete every row from every table -- individuals, faces, tags, trips,
    homes and photos. Like delete_photo, this only ever touches Postgres; the photos
    backend itself is never written to or deleted from. Originals are
    untouched and will simply be re-indexed as new photos the next time
    someone syncs."""
    with get_connection() as conn:
        conn.execute("DELETE FROM faces")
        conn.execute("DELETE FROM face_rejections")
        conn.execute("DELETE FROM individuals")
        conn.execute("DELETE FROM individual_groups")
        conn.execute("DELETE FROM photo_tags")
        conn.execute("DELETE FROM tags")
        conn.execute("DELETE FROM trip_photos")
        conn.execute("DELETE FROM trip_exclusions")
        conn.execute("DELETE FROM trips")
        conn.execute("DELETE FROM homes")
        conn.execute("DELETE FROM photo_embeddings")
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


# Preference when choosing which copy is "the original": a camera/lossless
# format over a re-saved JPEG over everything else.
_FORMAT_RANK = {"image/png": 0, "image/tiff": 0, "image/heic": 0, "image/heif": 0, "image/jpeg": 1}

# Same dimensions, same format, different bytes: could be a re-encode or two
# frames of a burst, so it needs a much closer pixel match than a resize does.
_REENCODE_MIN_CORRELATION = 0.99

DUPLICATE_MODES = ("exact", "similar", "all")


def _quality_key(row: dict):
    """Sort key, best copy first: most pixels, then preferred format, then
    larger file, then earliest date, then filename (deterministic)."""
    pixels = (row["width"] or 0) * (row["height"] or 0)
    created = row["created_at"]
    return (
        -pixels,
        _FORMAT_RANK.get(row["content_type"], 2),
        -(row["size_bytes"] or 0),
        created is None,
        created or 0,
        row["filename"],
    )


def _size_label(row: dict) -> str:
    return f"{row['width']}x{row['height']}" if row["width"] and row["height"] else ""


def _candidate_pairs(rows: list[dict], max_distance: int) -> dict[tuple[int, int], int]:
    """Index pairs (i < j) of rows whose phash is within `max_distance` bits,
    mapped to their distance. Pigeonhole banding: split the 64 bits into
    max_distance + 1 bands; two hashes within that distance must agree
    exactly on at least one band, so only rows sharing a band value are
    compared -- roughly linear in the library size instead of N^2."""
    bands = max_distance + 1
    bounds = [64 * k // bands for k in range(bands + 1)]
    hashes = [
        perceptual.to_unsigned(r["phash"]) if r["phash"] is not None else None for r in rows
    ]
    pairs: dict[tuple[int, int], int] = {}
    for k in range(bands):
        shift, width = bounds[k], bounds[k + 1] - bounds[k]
        mask = (1 << width) - 1
        buckets: dict[int, list[int]] = {}
        for index, value in enumerate(hashes):
            if value is not None:
                buckets.setdefault((value >> shift) & mask, []).append(index)
        for members in buckets.values():
            for a in range(len(members)):
                i = members[a]
                for b in range(a + 1, len(members)):
                    j = members[b]
                    if (i, j) in pairs:
                        continue
                    distance = (hashes[i] ^ hashes[j]).bit_count()
                    if distance <= max_distance:
                        pairs[(i, j)] = distance
    return pairs


def _match_type(original: dict, copy: dict) -> str:
    if original["checksum"] and original["checksum"] == copy["checksum"]:
        return "exact"
    same_dims = (
        abs((original["width"] or 0) - (copy["width"] or 0)) <= 1
        and abs((original["height"] or 0) - (copy["height"] or 0)) <= 1
    )
    if not same_dims:
        return "resize"
    return "reformat" if original["content_type"] != copy["content_type"] else "reencode"


def _same_aspect(a: dict, b: dict) -> bool:
    if not (a["width"] and a["height"] and b["width"] and b["height"]):
        return True  # unknown dimensions: don't reject on them
    ratio_a, ratio_b = a["width"] / a["height"], b["width"] / b["height"]
    return abs(ratio_a - ratio_b) / ratio_a <= 0.02


class DuplicateSearchCancelled(Exception):
    """`should_stop` returned true part-way through find_similar_photos."""


def find_similar_photos(
    max_distance: Optional[int] = None,
    min_correlation: Optional[float] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> list[dict]:
    """Duplicates that are not necessarily byte-identical: the same picture
    resized, re-encoded or saved in another format (docs/duplicate-
    detection.md). Candidates are byte-identical files (same `checksum`) plus
    photos whose `phash` is within `max_distance` bits; a non-identical pair
    must then also have the same aspect ratio and a thumbnail pixel
    correlation of at least `min_correlation` (stricter for a same-size,
    same-format pair, which may be two burst frames rather than one picture).

    Grouping never chains: photos are walked best-copy-first (_quality_key);
    each one not already claimed is an "original" and claims every unclaimed
    photo that matches *it* directly. Rows without a phash can still be
    reported as exact duplicates via their checksum.

    Returns dicts with original/duplicate filenames, `match_type`
    (exact/reformat/resize/reencode), `distance` and both pixel sizes.

    The two optional callbacks are for duplicates.py's background job:
    `on_progress(processed, total)` is called as the final (slow) walk
    advances, and a true `should_stop()` aborts it with
    DuplicateSearchCancelled."""
    if max_distance is None:
        max_distance = config.get_duplicate_max_distance()
    if min_correlation is None:
        min_correlation = config.get_duplicate_min_correlation()

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, filename, content_type, size_bytes, width, height, created_at, checksum, phash
            FROM photos
            WHERE checksum IS NOT NULL OR phash IS NOT NULL
            """
        ).fetchall()
    rows = sorted((dict(r) for r in rows), key=_quality_key)  # best copy first

    # neighbours[i] = {j: distance}, i < j in quality order (j is the lesser copy)
    neighbours: dict[int, dict[int, int]] = {}
    by_checksum: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        if row["checksum"]:
            by_checksum.setdefault(row["checksum"], []).append(index)
    for members in by_checksum.values():
        for j in members[1:]:
            neighbours.setdefault(members[0], {})[j] = 0
            for i in members[1:]:
                if i < j:
                    neighbours.setdefault(i, {})[j] = 0
    histogram: dict[int, int] = {}
    for (i, j), distance in _candidate_pairs(rows, max_distance).items():
        histogram[distance] = histogram.get(distance, 0) + 1
        neighbours.setdefault(i, {}).setdefault(j, distance)
    logger.info(
        "Duplicate search: %d candidate pairs by hash distance %s",
        sum(histogram.values()),
        dict(sorted(histogram.items())),
    )

    claimed: set[int] = set()
    results: list[dict] = []
    for i, row in enumerate(rows):
        if should_stop is not None and should_stop():
            raise DuplicateSearchCancelled()
        if on_progress is not None:
            on_progress(i, len(rows))
        if i in claimed or i not in neighbours:
            continue
        matches = [(j, d) for j, d in sorted(neighbours[i].items()) if j not in claimed]
        to_verify = [
            j for j, d in matches if _match_type(row, rows[j]) != "exact" and _same_aspect(row, rows[j])
        ]
        thumbnails: dict[str, bytes] = {}
        if to_verify:
            ids = [row["id"]] + [rows[j]["id"] for j in to_verify]
            with get_connection() as conn:
                for t in conn.execute("SELECT id, thumbnail FROM photos WHERE id = ANY(%s)", (ids,)).fetchall():
                    if t["thumbnail"] is not None:
                        thumbnails[t["id"]] = bytes(t["thumbnail"])
        for j, distance in matches:
            copy = rows[j]
            match_type = _match_type(row, copy)
            if match_type != "exact":
                if not _same_aspect(row, copy):
                    continue
                a, b = thumbnails.get(row["id"]), thumbnails.get(copy["id"])
                if a is None or b is None:
                    continue
                needed = _REENCODE_MIN_CORRELATION if match_type == "reencode" else min_correlation
                if perceptual.correlation(a, b) < needed:
                    continue
            claimed.add(j)
            results.append(
                {
                    "original": row["filename"],
                    "duplicate": copy["filename"],
                    "match_type": match_type,
                    "distance": distance,
                    "original_size": _size_label(row),
                    "duplicate_size": _size_label(copy),
                }
            )
    return results


def find_duplicate_pairs(
    mode: str = "exact",
    on_progress: Optional[Callable[[int, int], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> list[dict]:
    """`exact`: byte-identical only (find_duplicate_photos, the original
    behaviour). `similar`: only resized/re-encoded/reformatted matches.
    `all`: both."""
    if mode == "exact":
        return find_duplicate_photos()
    pairs = find_similar_photos(on_progress=on_progress, should_stop=should_stop)
    return pairs if mode == "all" else [p for p in pairs if p["match_type"] != "exact"]


def get_faces(photo_id: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, x1, y1, x2, y2, person_id, tag_source, detection_confidence, kind, species "
            "FROM faces WHERE photo_id = %s",
            (photo_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def replace_faces(photo_id: str, boxes: list[dict], kinds: Optional[list[str]] = None) -> None:
    """Replace the face rows for `photo_id` with `boxes` (each an
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
    behavior, not something this function tries to preserve across.

    `kinds` scopes the replacement: `None` (the default, every existing
    caller) replaces *all* of the photo's faces, humans and pets alike;
    `["person"]` or `["pet"]` replaces only rows of those kinds and leaves
    the rest -- tags included -- alone. That is what lets the Full Face
    Refresh re-detect humans without wiping pet tags, and the pets backfill
    add pets without touching human ones. Each box may carry `kind`
    (default `"person"`) and `species` (pets only: `"cat"`/`"dog"`); a
    caller that passes `kinds` should only pass boxes of those kinds."""
    with get_connection() as conn:
        if kinds is None:
            conn.execute("DELETE FROM faces WHERE photo_id = %s", (photo_id,))
        else:
            conn.execute("DELETE FROM faces WHERE photo_id = %s AND kind = ANY(%s)", (photo_id, list(kinds)))
        if boxes:
            with conn.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO faces (id, photo_id, x1, y1, x2, y2, detection_confidence, kind, species) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        (
                            str(uuid.uuid4()),
                            photo_id,
                            box["x1"],
                            box["y1"],
                            box["x2"],
                            box["y2"],
                            box.get("detection_confidence"),
                            box.get("kind", "person"),
                            box.get("species"),
                        )
                        for box in boxes
                    ],
                )
        conn.commit()
