import logging
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

    # Registers pgvector's vector/halfvec type adapters (embeddings.py's
    # HalfVector <-> photos.description_embedding) on *this* connection --
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
                match_confidence DOUBLE PRECISION
            )
            """
        )
        # Guards a database created before person_id/thumbnail/tag_source/
        # match_confidence existed -- see db/create_tables.sql's matching
        # ALTER TABLEs.
        conn.execute("ALTER TABLE faces ADD COLUMN IF NOT EXISTS person_id TEXT")
        conn.execute("ALTER TABLE faces ADD COLUMN IF NOT EXISTS thumbnail BYTEA")
        conn.execute("ALTER TABLE faces ADD COLUMN IF NOT EXISTS tag_source TEXT")
        conn.execute("ALTER TABLE faces ADD COLUMN IF NOT EXISTS match_confidence DOUBLE PRECISION")
        # Backfill: any face tagged before tag_source existed was
        # necessarily tagged via the manual tag_face endpoint --
        # recognition.py's auto-tagging didn't exist yet to have done it.
        # Without this, such a row has person_id set but tag_source still
        # NULL, so recognition._reference_embeddings() (which filters on
        # tag_source = 'manual') finds no reference faces at all and
        # "Refresh Faces" silently reports "nothing to match against" even
        # with plenty of manually tagged faces. A no-op once backfilled --
        # safe to run on every startup.
        conn.execute("UPDATE faces SET tag_source = 'manual' WHERE person_id IS NOT NULL AND tag_source IS NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_photo_id ON faces (photo_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_person_id ON faces (person_id)")
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
        # sync with these) for the full reasoning on halfvec(3072)/HNSW.
        try:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute("ALTER TABLE photos ADD COLUMN IF NOT EXISTS description_embedding halfvec(3072)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_photos_description_embedding "
                "ON photos USING hnsw (description_embedding halfvec_cosine_ops)"
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
