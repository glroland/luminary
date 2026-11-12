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
    # photos.description_embedding) on *this* connection --
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
                created_at TEXT NOT NULL,
                latitude DOUBLE PRECISION,
                longitude DOUBLE PRECISION,
                thumbnail BYTEA,
                vlm_description TEXT,
                description TEXT,
                location_name TEXT
            )
            """
        )
        # Guards a database created before location_name existed -- see
        # db/create_tables.sql's matching ALTER TABLE.
        conn.execute("ALTER TABLE photos ADD COLUMN IF NOT EXISTS location_name TEXT")

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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_photo_id ON faces (photo_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_person_id ON faces (person_id)")
        # Row-level data backfills (tag_source, face_detection_done) are
        # deliberately *not* here -- see backfill_data() below for why they
        # must stay off the startup path.
        conn.commit()

        # photos.description_embedding (embeddings.py) -- for similarity
        # search. Best-effort and isolated in its own commit/rollback,
        # unlike everything above: it needs the pgvector extension
        # installed in this database first, which this app role may not
        # have privilege to do itself (see db/create_extensions.sql's own
        # comment on why CREATE EXTENSION commonly needs superuser), and a
        # Postgres server that never got pgvector installed at the OS
        # level at all is a real, expected case this must degrade
        # gracefully from -- every other table/column above must keep
        # working on every startup regardless. A failure here is logged
        # once per startup, not raised; it never takes down init_db()/the
        # app. See db/create_tables.sql's matching statements (kept in
        # sync with these) for the full reasoning on vector(1024)/HNSW.
        #
        # The DO block below is a migration, not just a column add: an
        # earlier version of this app stored a halfvec(3072) OpenAI
        # embedding here (see embeddings.py's module docstring for why
        # that switched to a local vector(1024) model) -- this runs on
        # every startup (init_db() always has, there's no migrations
        # framework), so a database that already has the old column type
        # needs it dropped before the new one can be added; the old
        # embeddings are meaningless under the new model regardless, there
        # is nothing worth preserving by converting in place instead of
        # dropping. A fresh/already-migrated database (udt_name already
        # 'vector') skips the drop and the ADD COLUMN/CREATE INDEX below
        # are no-ops, same self-healing shape as the vlm_description
        # rename above.
        try:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute(
                """
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'photos' AND column_name = 'description_embedding'
                          AND udt_name <> 'vector'
                    ) THEN
                        ALTER TABLE photos DROP COLUMN description_embedding;
                    END IF;
                END $$
                """
            )
            conn.execute("ALTER TABLE photos ADD COLUMN IF NOT EXISTS description_embedding vector(1024)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_photos_description_embedding "
                "ON photos USING hnsw (description_embedding vector_cosine_ops)"
            )
            conn.commit()
        except psycopg.Error as exc:
            conn.rollback()
            logger.warning(
                "Could not set up pgvector (extension/column/index) for photos.description_embedding -- "
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
