import logging
import threading
from contextlib import contextmanager

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg.types import TypeInfo

from .config import (
    get_database_host,
    get_database_name,
    get_database_password,
    get_database_port,
    get_database_user,
)

logger = logging.getLogger(__name__)


@contextmanager
def get_connection():
    try:
        conn = psycopg.connect(
            host=get_database_host(),
            port=get_database_port(),
            dbname=get_database_name(),
            user=get_database_user(),
            password=get_database_password(),
            row_factory=dict_row,
        )
    except psycopg.Error:
        logger.exception(
            "Failed to connect to database at %s:%s/%s",
            get_database_host(),
            get_database_port(),
            get_database_name(),
        )
        raise

    # Registers pgvector's vector type adapter (embeddings.py's Vector <->
    # photo_embeddings.embedding) on *this* connection --
    # get_connection() opens a fresh one per operation (no pooling), so
    # this has to happen every time, not once at startup. Guarded on
    # whether the `vector` type actually exists in this database: without
    # this check, register_vector() itself raises on a database where the
    # pgvector extension was never installed (see init_db()'s own
    # best-effort attempt to create it, and db/create_extensions.sql) --
    # every *other* table/column in this app must keep working either
    # way, so this never becomes a reason get_connection() itself fails.
    if TypeInfo.fetch(conn, "vector") is not None:
        register_vector(conn)

    try:
        yield conn
    except psycopg.Error:
        logger.exception("Database error")
        raise
    finally:
        conn.close()


def init_db() -> None:
    # Mirrors db/create_tables.sql, which provisions the same schema
    # independently of starting the app (see db/ and the `make db` target).
    # No migrations framework -- a schema change means updating both this
    # and that file together, plus staging the same ALTER/CREATE statements
    # in db/update.sql so an already-provisioned database can be patched in
    # place via `make db-update` instead of a full drop/rebuild.
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS photos (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                content_type TEXT NOT NULL,
                size_bytes BIGINT NOT NULL,
                width INTEGER,
                height INTEGER,
                created_at TEXT,
                latitude DOUBLE PRECISION,
                longitude DOUBLE PRECISION,
                thumbnail BYTEA,
                vlm_description TEXT,
                description TEXT,
                location_name TEXT,
                location_country TEXT,
                location_state TEXT,
                location_city TEXT
            )
            """
        )
        # Guards a database created before location_name existed -- see
        # db/create_tables.sql's matching ALTER TABLE.
        conn.execute("ALTER TABLE photos ADD COLUMN IF NOT EXISTS location_name TEXT")
        for column in ("location_country", "location_state", "location_city"):
            conn.execute(f"ALTER TABLE photos ADD COLUMN IF NOT EXISTS {column} TEXT")
        # A photo with no date of its own is undated (NULL), never stamped
        # with the time it was indexed. No-op once the constraint is gone.
        conn.execute("ALTER TABLE photos ALTER COLUMN created_at DROP NOT NULL")

        # `description` used to hold Florence-2's raw caption directly;
        # that's now `vlm_description`, freeing `description` for
        # enrich.py's LLM-composed description (built from
        # vlm_description + individuals + location + filename -- see
        # enrich.py's module docstring). A database created before this
        # split has a `description` column full of real captions and no
        # `vlm_description` at all -- rename the former to the latter
        # (preserving that data) before re-adding a fresh `description`
        # below, rather than silently losing every caption already
        # indexed. The IF/AND NOT EXISTS guard makes this a no-op on a
        # database that's already past this point, same shape as every
        # other self-healing ALTER in this function.
        conn.execute(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'photos' AND column_name = 'description'
                ) AND NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'photos' AND column_name = 'vlm_description'
                ) THEN
                    ALTER TABLE photos RENAME COLUMN description TO vlm_description;
                END IF;
            END $$
            """
        )
        conn.execute("ALTER TABLE photos ADD COLUMN IF NOT EXISTS vlm_description TEXT")
        conn.execute("ALTER TABLE photos ADD COLUMN IF NOT EXISTS description TEXT")

        # Dedicated signal for "has sync.py's caption+face-detection
        # pipeline (or recognition.py's full-refresh redetect) actually
        # run for this photo" -- see sync.py's module docstring. Replaces
        # the earlier `vlm_description IS NOT NULL` inference, which
        # conflated "has a caption" with "has had faces detected". Added
        # with DEFAULT FALSE so a brand-new row starts untrue until the
        # pipeline actually runs; a database created before this column
        # existed gets backfilled further down (once the `faces` table
        # below is guaranteed to exist) based on whether each photo
        # already has any detected faces on record, rather than being left
        # at the blanket FALSE default for every pre-existing row.
        conn.execute(
            "ALTER TABLE photos ADD COLUMN IF NOT EXISTS face_detection_done BOOLEAN NOT NULL DEFAULT FALSE"
        )

        # SHA-256 hex digest of the file's raw bytes (storage.compute_checksum),
        # for reliably telling whether two files are byte-identical --
        # unlike size_bytes, a collision here is cryptographically
        # implausible. (Re)computed by sync.py whenever content_changed, or
        # backfilled lazily whenever a sync encounters a row where this is
        # still NULL (e.g. a database indexed before this column existed) --
        # same "field missing, backfill it" shape as vlm_description/
        # thumbnail above, not a one-time UPDATE here, since computing it
        # requires reading the file itself.
        conn.execute("ALTER TABLE photos ADD COLUMN IF NOT EXISTS checksum TEXT")

        # 64-bit perceptual hash (perceptual.dhash) of the stored thumbnail,
        # as a signed BIGINT, for finding resized/re-encoded/reformatted
        # duplicates -- see docs/duplicate-detection.md. NULL when the photo
        # has no thumbnail or is a flat image; `phash_done` says whether
        # hashing has been attempted for the current thumbnail, so a flat
        # image isn't retried on every startup. Row-level backfill lives in
        # backfill_data() (it needs Pillow, not just SQL).
        conn.execute("ALTER TABLE photos ADD COLUMN IF NOT EXISTS phash BIGINT")
        conn.execute("ALTER TABLE photos ADD COLUMN IF NOT EXISTS phash_done BOOLEAN NOT NULL DEFAULT FALSE")
        # TRUE once pet detection (pet_detection.py) has successfully run for
        # this photo. Separate from face_detection_done on purpose: photos
        # indexed before pet detection existed have that one TRUE, and the
        # pets backfill must be able to tell them apart from scanned ones.
        conn.execute("ALTER TABLE photos ADD COLUMN IF NOT EXISTS pet_detection_done BOOLEAN NOT NULL DEFAULT FALSE")
        # 'image' | 'video' | 'animated' (animated GIF/WebP/APNG), and a video's
        # length -- see docs/video-support.md. A constant DEFAULT is
        # metadata-only, so existing rows read 'image' with no backfill.
        conn.execute("ALTER TABLE photos ADD COLUMN IF NOT EXISTS media_kind TEXT NOT NULL DEFAULT 'image'")
        conn.execute("ALTER TABLE photos ADD COLUMN IF NOT EXISTS duration_seconds DOUBLE PRECISION")

        # Catalog of named individuals faces can be manually tagged as --
        # see people.py / routers/individuals.py.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS individuals (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                representative_thumbnail BYTEA,
                created_at TEXT NOT NULL
            )
            """
        )
        # 'person' | 'pet' -- see db/create_tables.sql. A constant DEFAULT is
        # metadata-only in Postgres, so existing rows read 'person' with no
        # row-level backfill (which must never live in init_db -- see
        # backfill_data()).
        conn.execute("ALTER TABLE individuals ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'person'")

        # Catalog of free-form tags whole photos can be tagged with -- see
        # tags.py / routers/tags.py (tag CRUD) and routers/photos.py's
        # photo-tag endpoints (attaching/detaching a tag on a specific
        # photo). Distinct from `individuals` above: a tag describes the
        # photo as a whole, not a detected face, so there's no thumbnail
        # column here.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tags (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
            """
        )

        # Many-to-many between photos and tags. No FK constraint, same
        # explicit-delete rationale as `faces` below (see
        # storage.delete_photo/purge_all and sync._delete_missing, all of
        # which must also clean up this table's rows for a photo they
        # remove).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS photo_tags (
                photo_id TEXT NOT NULL,
                tag_id TEXT NOT NULL,
                PRIMARY KEY (photo_id, tag_id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_photo_tags_tag_id ON photo_tags (tag_id)")
        # Trips -- see db/create_tables.sql's matching tables and
        # trips.py / trip_detection.py. No FK constraints.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS homes (
                id TEXT PRIMARY KEY,
                name TEXT,
                latitude DOUBLE PRECISION NOT NULL,
                longitude DOUBLE PRECISION NOT NULL,
                radius_km DOUBLE PRECISION,
                start_date TEXT,
                end_date TEXT,
                country TEXT,
                state TEXT,
                city TEXT,
                source TEXT NOT NULL DEFAULT 'auto',
                confirmed BOOLEAN NOT NULL DEFAULT FALSE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trips (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                name_source TEXT NOT NULL DEFAULT 'auto',
                kind TEXT NOT NULL DEFAULT 'trip',
                status TEXT NOT NULL DEFAULT 'active',
                source TEXT NOT NULL DEFAULT 'auto',
                start_at TEXT,
                end_at TEXT,
                cover_photo_id TEXT,
                cover_source TEXT NOT NULL DEFAULT 'auto',
                edited BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trip_photos (
                photo_id TEXT PRIMARY KEY,
                trip_id TEXT NOT NULL,
                membership TEXT NOT NULL DEFAULT 'auto'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trip_exclusions (
                trip_id TEXT NOT NULL,
                photo_id TEXT NOT NULL,
                PRIMARY KEY (trip_id, photo_id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trip_photos_trip_id ON trip_photos (trip_id)")

        # One photo -> many detected faces (sync.py's Florence-2 face
        # detection). No FK constraint -- this app already deletes rows
        # explicitly (storage.delete_photo, sync._delete_missing) rather
        # than relying on cascades.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS faces (
                id TEXT PRIMARY KEY,
                photo_id TEXT NOT NULL,
                x1 DOUBLE PRECISION NOT NULL,
                y1 DOUBLE PRECISION NOT NULL,
                x2 DOUBLE PRECISION NOT NULL,
                y2 DOUBLE PRECISION NOT NULL,
                person_id TEXT,
                thumbnail BYTEA,
                tag_source TEXT,
                match_confidence DOUBLE PRECISION,
                detection_confidence DOUBLE PRECISION
            )
            """
        )
        # Guards a database created before person_id/thumbnail/tag_source/
        # match_confidence/detection_confidence existed -- see
        # db/create_tables.sql's matching ALTER TABLEs. A face row that
        # predates detection_confidence simply stays NULL forever (there's
        # no stored probability to backfill it from) until a future sync/
        # "Refresh Faces" redetects that photo.
        conn.execute("ALTER TABLE faces ADD COLUMN IF NOT EXISTS person_id TEXT")
        conn.execute("ALTER TABLE faces ADD COLUMN IF NOT EXISTS thumbnail BYTEA")
        conn.execute("ALTER TABLE faces ADD COLUMN IF NOT EXISTS tag_source TEXT")
        conn.execute("ALTER TABLE faces ADD COLUMN IF NOT EXISTS match_confidence DOUBLE PRECISION")
        conn.execute("ALTER TABLE faces ADD COLUMN IF NOT EXISTS detection_confidence DOUBLE PRECISION")
        # 'person' (MTCNN) | 'pet' (pet_detection.py); species is 'cat'/'dog'
        # for a pet and NULL for a person. See db/create_tables.sql.
        conn.execute("ALTER TABLE faces ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'person'")
        conn.execute("ALTER TABLE faces ADD COLUMN IF NOT EXISTS species TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_photo_id ON faces (photo_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_person_id ON faces (person_id)")
        # Faces a user untagged after an auto-match (see db/create_tables.sql
        # for the full column rationale). No FK; deleted explicitly.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS face_rejections (
                id TEXT PRIMARY KEY,
                person_id TEXT NOT NULL,
                photo_id TEXT NOT NULL,
                x1 DOUBLE PRECISION NOT NULL,
                y1 DOUBLE PRECISION NOT NULL,
                x2 DOUBLE PRECISION NOT NULL,
                y2 DOUBLE PRECISION NOT NULL,
                thumbnail BYTEA NOT NULL,
                match_confidence DOUBLE PRECISION,
                rejected_at_threshold DOUBLE PRECISION,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_vetoed_score DOUBLE PRECISION,
                last_vetoed_at TIMESTAMPTZ
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_face_rejections_person_id ON face_rejections (person_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_face_rejections_photo_id ON face_rejections (photo_id)")
        # One photo -> many embedded text snippets (embeddings.py): today one
        # row per sentence of photos.description (source_type 'D'). No FK,
        # same as faces/photo_tags -- rows are deleted explicitly
        # (storage.delete_photo, sync._delete_missing). The `embedding`
        # column itself is added in the pgvector block below, not here, so
        # a server without pgvector still gets a table the delete paths
        # can safely reference.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS photo_embeddings (
                id TEXT PRIMARY KEY,
                photo_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                sentence_index INTEGER NOT NULL,
                sentence_text TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_photo_embeddings_photo_id ON photo_embeddings (photo_id)")
        # Row-level data backfills (tag_source, face_detection_done) are
        # deliberately *not* here -- see backfill_data() below for why they
        # must stay off the startup path.
        conn.commit()

        # photo_embeddings.embedding (embeddings.py) -- for similarity
        # search. Best-effort and isolated in its own commit/rollback,
        # unlike everything above: it needs the pgvector extension
        # installed in this database first, which this app role may not
        # have privilege to do itself (see db/create_extensions.sql's own
        # comment on why CREATE EXTENSION commonly needs superuser), and a
        # Postgres server that never got pgvector installed at the OS
        # level at all is a real, expected case this must degrade
        # gracefully from -- every other table/column above must keep
        # working on every startup regardless. See db/create_tables.sql's
        # matching statements (kept in sync with these).
        #
        # The old single-vector photos.description_embedding column (and
        # its index) is dropped here: it is superseded by one
        # photo_embeddings row per description sentence. Its values are not
        # converted -- "Refresh Descriptions" re-embeds every photo.
        try:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute("DROP INDEX IF EXISTS idx_photos_description_embedding")
            conn.execute("ALTER TABLE photos DROP COLUMN IF EXISTS description_embedding")
            conn.execute("ALTER TABLE photo_embeddings ADD COLUMN IF NOT EXISTS embedding vector(1024)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_photo_embeddings_embedding "
                "ON photo_embeddings USING hnsw (embedding vector_cosine_ops)"
            )
            conn.commit()
        except psycopg.Error as exc:
            conn.rollback()
            logger.warning(
                "Could not set up pgvector (extension/column/index) for photo_embeddings.embedding -- "
                "similarity search will be unavailable until the pgvector extension is installed on this "
                "Postgres server (see db/create_extensions.sql and the README's \"Database\" section). "
                "Everything else started normally. Cause: %s",
                exc,
            )
    logger.info("Database ready at %s:%s/%s", get_database_host(), get_database_port(), get_database_name())


# Rows updated per transaction by backfill_data(). Each batch commits on its
# own, so progress survives a pod restart midway and no single transaction
# holds row locks on a large slice of `photos` for long (a concurrent sync
# writing to those same rows would otherwise wait on it).
BACKFILL_BATCH_SIZE = 500


def backfill_data() -> None:
    """Row-level data backfills for columns added after a database was first
    indexed. Kept out of init_db() and run on a background thread after the
    app is already serving (main.py's lifespan, via start_backfill()) --
    unlike init_db()'s DDL, these touch every matching row, and on a large
    library the first run can take far longer than the liveness probe allows.
    When this ran synchronously in init_db(), the pod was killed mid-UPDATE,
    the transaction rolled back, and the next start began from zero again: a
    crash loop that never converged. Each statement is idempotent (a no-op
    once applied), so running this on every startup is safe and cheap after
    the first time.

    Until this finishes, the only effect is that a sync may redo face
    detection for a photo it would otherwise have skipped -- wasteful, never
    incorrect -- so nothing waits on it."""
    # Any face tagged before tag_source existed was necessarily tagged via
    # the manual tag_face endpoint -- recognition.py's auto-tagging didn't
    # exist yet to have done it. Without this, such a row has person_id set
    # but tag_source still NULL, so recognition._reference_embeddings()
    # (which filters on tag_source = 'manual') finds no reference faces at
    # all and "Refresh Faces" silently reports "nothing to match against"
    # even with plenty of manually tagged faces.
    _backfill_in_batches(
        "faces.tag_source",
        "UPDATE faces SET tag_source = 'manual' WHERE id IN ("
        "SELECT id FROM faces WHERE person_id IS NOT NULL AND tag_source IS NULL LIMIT %s)",
    )
    # A photo that already has at least one detected face on record clearly
    # already went through detection, so it's backfilled to TRUE rather than
    # left at the column's blanket FALSE default (which would otherwise force
    # every already-indexed, face-bearing photo through a pointless redetect
    # on the next sync). A photo with zero faces on record is left at FALSE
    # -- indistinguishable here from "detection genuinely found nobody" vs.
    # "detection never ran" (there's no stored history to tell those apart),
    # so it's treated the conservative way: the next sync just (re)runs
    # detection once for such a photo.
    _backfill_in_batches(
        "photos.face_detection_done",
        "UPDATE photos SET face_detection_done = TRUE WHERE id IN ("
        "SELECT id FROM photos WHERE NOT face_detection_done AND EXISTS "
        "(SELECT 1 FROM faces WHERE faces.photo_id = photos.id) LIMIT %s)",
    )
    # An existing GIF is almost always animated, and re-reading every one to
    # check isn't worth it. Sync classifies exactly whenever it (re)reads a
    # file (a static GIF it re-reads goes back to 'image', and this puts it
    # right back -- a rare, harmless mislabel). WebP/APNG are classified
    # only when sync next reads them.
    _backfill_in_batches(
        "photos.media_kind (gif)",
        "UPDATE photos SET media_kind = 'animated' WHERE id IN ("
        "SELECT id FROM photos WHERE media_kind = 'image' AND lower(filename) LIKE '%%.gif' LIMIT %s)",
    )
    _backfill_phashes()


def _backfill_phashes() -> None:
    """Hashes every photo that has a thumbnail but hasn't been through
    perceptual.dhash yet. Reads only the stored thumbnail (no backend I/O),
    commits per batch like _backfill_in_batches."""
    from . import perceptual  # lazy: keeps database.py importable on its own

    total = 0
    while True:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, thumbnail FROM photos WHERE NOT phash_done AND thumbnail IS NOT NULL LIMIT %s",
                (BACKFILL_BATCH_SIZE,),
            ).fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE photos SET phash = %s, phash_done = TRUE WHERE id = %s",
                    (perceptual.dhash_from_bytes(bytes(row["thumbnail"])), row["id"]),
                )
            conn.commit()
        total += len(rows)
        if len(rows) < BACKFILL_BATCH_SIZE:
            break
        logger.info("Backfilling photos.phash: %d rows so far", total)
    if total:
        logger.info("Backfilled photos.phash for %d rows", total)


def _backfill_in_batches(label: str, statement: str) -> None:
    """Runs `statement` (an UPDATE limited to %s rows that stops matching
    rows once it has updated them) repeatedly, one commit per batch, until a
    batch updates nothing."""
    total = 0
    while True:
        with get_connection() as conn:
            updated = conn.execute(statement, (BACKFILL_BATCH_SIZE,)).rowcount
            conn.commit()
        total += updated
        if updated < BACKFILL_BATCH_SIZE:
            break
        logger.info("Backfilling %s: %d rows so far", label, total)
    if total:
        logger.info("Backfilled %s for %d rows", label, total)


def start_backfill() -> threading.Thread:
    """Runs backfill_data() on a daemon thread so the API can start serving
    (and pass its health checks) immediately. A failure is logged, never
    raised -- every backfilled column only makes later work cheaper, so the
    app keeps running without it and the next startup retries."""

    def run() -> None:
        try:
            backfill_data()
        except Exception:
            logger.exception("Background data backfill failed; it will be retried on the next startup")

    thread = threading.Thread(target=run, name="db-backfill", daemon=True)
    thread.start()
    return thread
