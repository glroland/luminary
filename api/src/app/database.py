import sqlite3
from contextlib import contextmanager

from .config import get_database_path


@contextmanager
def get_connection():
    db_path = get_database_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
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
                thumbnail BLOB
            )
            """
        )
        conn.commit()
