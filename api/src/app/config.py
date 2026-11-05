"""Runtime configuration read from environment variables.

Values are read lazily (per call) rather than cached at import time so tests
can override them per-case with monkeypatched environment variables.
"""

import os
import re
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


def get_database_host() -> str:
    """Postgres host holding photo metadata + thumbnails. Unlike
    LUMINARY_PHOTOS_URI, this is always Postgres, always remote to this
    app's own deployment -- the database is provisioned and maintained
    separately (see db/ and the `make db`/`make db-recreate` targets), not
    stood up by the Helm chart/ArgoCD app alongside the API."""
    return os.environ.get("LUMINARY_DATABASE_HOST", "localhost")


def get_database_port() -> int:
    return int(os.environ.get("LUMINARY_DATABASE_PORT", "5432"))


def get_database_name() -> str:
    return os.environ.get("LUMINARY_DATABASE_NAME", "luminary")


def get_database_user() -> str:
    return os.environ.get("LUMINARY_DATABASE_USER", "luminary")


def get_database_password() -> Optional[str]:
    """No default -- unlike the photos backend's credentials, which may
    legitimately be absent (a local path/NFS mount needs none), a Postgres
    connection always authenticates with something; leaving this unset
    is a misconfiguration, not a valid "no auth needed" state."""
    return os.environ.get("LUMINARY_DATABASE_PASSWORD") or None


def get_cors_origins() -> list[str]:
    raw = os.environ.get("LUMINARY_CORS_ORIGINS", "*")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def get_log_level() -> str:
    return os.environ.get("LUMINARY_LOG_LEVEL", "INFO").upper()


def redact_uri(uri: str) -> str:
    """Strip embedded credentials (e.g. ``smb://user:pass@host/...``) from a
    URI so it's safe to put in logs."""
    return re.sub(r"//[^/@\s]+@", "//***@", uri)


def get_max_concurrency() -> int:
    """How many files/photos sync.py's indexing pipeline and recognition.py's
    "Refresh Faces" fan out across at once (each module's own
    ThreadPoolExecutor max_workers + asyncio.Semaphore bound -- see their
    own MAX_CONCURRENCY). Read once at import time by both modules (that
    constant is set from this at module load, not re-read per sync/refresh
    run) -- unlike the lazily-read settings elsewhere in this file, there's
    no need to change this mid-process, and the executor's pool size can
    only be chosen once anyway. `8` was the original hardcoded value for
    both and remains the default so unset reproduces that; a deployment
    with less CPU to spare for background work (e.g. a local `make run-api`
    sharing the machine with everything else you're doing) can lower it
    without touching code -- see the Makefile's LUMINARY_MAX_CONCURRENCY
    and the Helm chart's api.env.maxConcurrency."""
    return int(os.environ.get("LUMINARY_MAX_CONCURRENCY", "8"))


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


def get_openai_streaming() -> bool:
    """Whether the openai chat backend streams its reply back to the client
    token-by-token instead of returning it all at once. Off by default --
    opt in explicitly."""
    return os.environ.get("LUMINARY_OPENAI_STREAMING", "false").strip().lower() in ("1", "true", "yes", "on")


# --- Photo chat: path 2, local Florence-2 vision-language model -----------


def get_florence_model() -> str:
    return os.environ.get("LUMINARY_FLORENCE_MODEL", "microsoft/Florence-2-base")


def get_florence_device() -> str:
    return os.environ.get("LUMINARY_FLORENCE_DEVICE", "cpu")


# --- Reverse geocoding (photos.location_name, geocode.py) -----------------


def get_geocode_base_url() -> str:
    """Nominatim-compatible reverse-geocoding endpoint. Defaults to the
    public OpenStreetMap instance -- free, no API key, the same service
    PhotoMap.jsx already depends on (for map tiles) elsewhere in this app.
    Point this at a self-hosted Nominatim instance instead to avoid that
    public instance's 1-request/second usage-policy cap (see geocode.py's
    module docstring) on a large/GPS-heavy library."""
    return os.environ.get("LUMINARY_GEOCODE_BASE_URL", "https://nominatim.openstreetmap.org/reverse")


def get_geocode_user_agent() -> str:
    """Nominatim's usage policy requires a descriptive User-Agent
    identifying the calling application -- see
    https://operations.osmfoundation.org/policies/nominatim/. Override this
    (e.g. to add a contact URL/email) if the public instance ever
    rejects/rate-limits the default."""
    return os.environ.get("LUMINARY_GEOCODE_USER_AGENT", "luminary-photo-browser/1.0")


def get_geocode_zoom() -> int:
    """Nominatim's `zoom` parameter picks which OSM feature *layer* reverse
    geocoding resolves to -- low values resolve to broad administrative
    areas (city/county/state), high values (up to 18) resolve to individual
    buildings/named businesses. There's no single right value across very
    different photo subjects (a building interior vs. a large state park)
    -- like LUMINARY_FACE_MATCH_THRESHOLD below, this is a starting point
    to tune against your own library's real photos, not a
    verified-correct number. Defaults to 18 (the maximum, building-level)
    rather than a coarser value specifically so a named business mapped in
    OSM (e.g. a Target -- see geocode.py's `_PLACE_KEYS`) has a chance to
    resolve over just its containing street/neighborhood; lower this if
    that ends up producing overly narrow results for a library that's
    mostly outdoor/park photos instead."""
    return int(os.environ.get("LUMINARY_GEOCODE_ZOOM", "18"))


# --- Description embeddings for similarity search (embeddings.py) -------


def get_embedding_model() -> str:
    """Hugging Face model id for photos.description_embedding -- a local
    sentence-embedding model run in-process via sentence-transformers
    (embeddings.py), deliberately *not* a call to an OpenAI-*compatible*
    embeddings endpoint -- LUMINARY_OPENAI_BASE_URL may point at a
    self-hosted chat gateway with no embeddings endpoint at all, or one
    whose embeddings endpoint produces a different, incompatible vector
    space than whatever already generated the embeddings stored in this
    database. Defaults to BAAI/bge-large-en-v1.5 -- see embeddings.py's
    module docstring for why that model, and why its weights must already
    be present in this container's Hugging Face cache (api/Containerfile)
    rather than fetched at first use."""
    return os.environ.get("LUMINARY_EMBEDDING_MODEL", "BAAI/bge-large-en-v1.5")


def get_embedding_device() -> str:
    """Mirrors get_florence_device() above -- "cpu" by default, "cuda"/
    "cuda:0" etc. if a GPU is available to the pod."""
    return os.environ.get("LUMINARY_EMBEDDING_DEVICE", "cpu")


# --- Automatic face detection (sync.py's indexing pipeline, face_detection.py) ---


def get_face_detection_threshold() -> float:
    """Confidence threshold facenet-pytorch's MTCNN must clear to report a
    detected face, applied uniformly across its 3-stage cascade (P-Net/
    R-Net/O-Net) -- see face_detection.py's module docstring for why a
    single value instead of per-stage tuning is good enough here. Lower
    catches more faces (partial/angled/small, or in a crowded photo) at the
    cost of more false positives; like LUMINARY_FACE_MATCH_THRESHOLD below,
    there's no single verified-correct value across different libraries'
    real photos. MTCNN's own published default for its final stage is 0.7;
    this started lower (0.5, when detection first switched from Florence-2's
    <OD> to MTCNN, to fix real faces going undetected) but that turned out
    too permissive in practice -- too many non-face detections -- raised to
    0.65, then 0.8, then 0.72, then 0.77, then 0.68, then 0.72, then back
    up to 0.8 as the current starting point. Tune this against your own
    library in either direction rather than assuming either number is
    right for it."""
    return float(os.environ.get("LUMINARY_FACE_DETECTION_THRESHOLD", "0.8"))


def get_face_min_size() -> int:
    """Minimum face size, in pixels, measured against MTCNN's own
    internally-resized copy of the image (not the original photo's
    resolution), for MTCNN to report a detection -- passed straight through
    as its own min_face_size constructor kwarg. Mirrors
    get_face_detection_threshold() above -- lower catches more faces
    (small/distant subjects, group photos) at the cost of more false
    positives and slower detection (MTCNN's image pyramid has to scan more
    scales). facenet-pytorch's own MTCNN default is 20; confirmed too
    permissive in practice (spurious small-box detections), raised to 100
    then to 150 as the current starting point -- same "default was too
    permissive, moved it, keep tuning from here" shape as
    get_face_detection_threshold()'s own
    0.5->0.65->0.8->0.72->0.77->0.68->0.72->0.8 move above. No
    single verified-correct value across different libraries' real
    photos -- tune against yours."""
    return int(os.environ.get("LUMINARY_FACE_MIN_SIZE", "150"))


# --- Automatic face recognition ("Refresh Faces", recognition.py) --------


def get_face_match_threshold() -> float:
    """Minimum cosine similarity between a candidate face's embedding and
    an individual's reference embedding (recognition.py's
    _reference_embeddings(), built from their manually tagged faces only)
    for recognition.py to auto-tag a match. facenet-pytorch's
    InceptionResnetV1 L2-normalizes its output (confirmed empirically,
    not just assumed -- see recognition.py's module docstring), so cosine
    similarity is a valid metric for it, but there's no reliable published
    "same person" cutoff for this exact model/weights combination the way
    there is for e.g. the original FaceNet L2-distance threshold -- 0.6
    turned out too strict in practice (real matches scored below it and
    were silently skipped). 0.4 is a looser starting point, not a
    verified-correct one either -- if a refresh run matches nothing,
    check its log line naming the closest unmatched candidate's actual
    score (or set LUMINARY_LOG_LEVEL=DEBUG for every candidate's score)
    and tune this against those real numbers rather than guessing again."""
    return float(os.environ.get("LUMINARY_FACE_MATCH_THRESHOLD", "0.4"))
