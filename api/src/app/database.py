import logging
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

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
                description TEXT
            )
            """
        )

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
                person_id TEXT
            )
            """
        )
        # Guards a database created before person_id existed -- see
        # db/create_tables.sql's matching ALTER TABLE.
        conn.execute("ALTER TABLE faces ADD COLUMN IF NOT EXISTS person_id TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_photo_id ON faces (photo_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_person_id ON faces (person_id)")
        conn.commit()
    logger.info("Database ready at %s:%s/%s", get_database_host(), get_database_port(), get_database_name())
