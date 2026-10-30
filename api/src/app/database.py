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
        conn.commit()
    logger.info("Database ready at %s", get_database_path())
