"""Runtime configuration read from environment variables.

Values are read lazily (per call) rather than cached at import time so tests
can override them per-case with monkeypatched environment variables.
"""

import os
from pathlib import Path


def get_photos_uri() -> str:
    """Where original/thumbnail files live, as an fsspec URI.

    Accepts anything fsspec understands: a bare local path (default),
    ``file:///...``, ``s3://bucket/prefix``, ``smb://server/share/path``,
    ``https://host/path`` (read-only), etc.
    """
    return os.environ.get("LUMINARY_PHOTOS_URI", "/data/photos")


def get_database_path() -> Path:
    return Path(os.environ.get("LUMINARY_DATABASE_PATH", "/data/photos.db"))


def get_cors_origins() -> list[str]:
    raw = os.environ.get("LUMINARY_CORS_ORIGINS", "*")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]
