import logging
import sqlite3
from contextlib import contextmanager

from .config import get_database_path

logger = logging.getLogger(__name__)


@contextmanager
def get_connection():
    db_path = get_database_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error:
        logger.exception("Failed to open database at %s", db_path)
        raise
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    except sqlite3.Error:
        logger.exception("Database error")
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS photos (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                content_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                width INTEGER,
                height INTEGER,
                created_at TEXT NOT NULL,
                latitude REAL,
                longitude REAL,
                thumbnail BLOB,
                description TEXT
            )
            """
        )
        # No migrations framework (see module docs) -- CREATE TABLE IF NOT
        # EXISTS only covers a brand-new database. A `photos.db` from
        # before `description` existed needs this ALTER TABLE instead;
        # "duplicate column" means a previous run already added it.
        try:
            conn.execute("ALTER TABLE photos ADD COLUMN description TEXT")
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise

        # One photo -> many detected faces (sync.py's Florence-2 face
        # detection). No FK constraint -- SQLite doesn't enforce them
        # without a per-connection PRAGMA, and this app already deletes
        # rows explicitly (storage.delete_photo, sync._delete_missing)
        # rather than relying on cascades.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS faces (
                id TEXT PRIMARY KEY,
                photo_id TEXT NOT NULL,
                x1 REAL NOT NULL,
                y1 REAL NOT NULL,
                x2 REAL NOT NULL,
                y2 REAL NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_faces_photo_id ON faces (photo_id)")
        conn.commit()
    logger.info("Database ready at %s", get_database_path())
