"""Runtime configuration read from environment variables.

Values are read lazily (per call) rather than cached at import time so tests
can override them per-case with monkeypatched environment variables.
"""

import os
import re
from pathlib import Path
from typing import Optional


def get_photos_uri() -> str:
    """Where original/thumbnail files live, as an fsspec URI.

    Accepts anything fsspec understands: a bare local path (default),
    ``file:///...``, ``s3://bucket/prefix``, ``smb://server/share/path``,
    ``https://host/path`` (read-only), etc.
    """
    return os.environ.get("LUMINARY_PHOTOS_URI", "/data/photos")


def get_photos_username() -> Optional[str]:
    """Credentials for LUMINARY_PHOTOS_URI, kept separate from the URI
    itself (never e.g. ``smb://user:pass@host/...``) so the URI stays safe
    to log, default in a Makefile, or commit, while the secret only ever
    lives in the environment."""
    return os.environ.get("LUMINARY_PHOTOS_USERNAME") or None


def get_photos_password() -> Optional[str]:
    return os.environ.get("LUMINARY_PHOTOS_PASSWORD") or None


def get_database_path() -> Path:
    return Path(os.environ.get("LUMINARY_DATABASE_PATH", "/data/photos.db"))


def get_cors_origins() -> list[str]:
    raw = os.environ.get("LUMINARY_CORS_ORIGINS", "*")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def get_log_level() -> str:
    return os.environ.get("LUMINARY_LOG_LEVEL", "INFO").upper()


def redact_uri(uri: str) -> str:
    """Strip embedded credentials (e.g. ``smb://user:pass@host/...``) from a
    URI so it's safe to put in logs."""
    return re.sub(r"//[^/@\s]+@", "//***@", uri)


# --- Photo chat: path 1, any OpenAI-compatible multimodal endpoint --------


def get_openai_api_key() -> Optional[str]:
    return os.environ.get("LUMINARY_OPENAI_API_KEY") or None


def get_openai_base_url() -> Optional[str]:
    """Base URL of an OpenAI-*compatible* endpoint (vLLM, Ollama, LM Studio,
    a self-hosted gateway, etc). Deliberately has no default -- the openai
    SDK defaults to api.openai.com if this is left unset, which is only
    correct if that's genuinely the intended backend; never assume it."""
    return os.environ.get("LUMINARY_OPENAI_BASE_URL") or None


def get_openai_model() -> Optional[str]:
    """No sensible default -- a self-hosted OpenAI-compatible server's model
    name is whatever that deployment calls it, so this must be set
    explicitly rather than guessed."""
    return os.environ.get("LUMINARY_OPENAI_MODEL") or None


# --- Photo chat: path 2, local Florence-2 vision-language model -----------


def get_florence_model() -> str:
    return os.environ.get("LUMINARY_FLORENCE_MODEL", "microsoft/Florence-2-base")


def get_florence_device() -> str:
    return os.environ.get("LUMINARY_FLORENCE_DEVICE", "cpu")
