"""Catalog of named individuals, and manual face-tagging against that
catalog (`faces.person_id`) -- see routers/individuals.py and
routers/faces.py. Distinct from sync.py's Florence-2 face *detection*: this
module never detects anything, it only records a user's manual assignment
of an already-detected face (storage.get_faces/replace_faces) to a person.

Tagging a face also crops that face's rectangle out of the photo's original
bytes and stores it as the individual's `representative_thumbnail` -- the
most recently tagged face "wins"; there's no gallery of every face ever
tagged to a person, just the one representative crop.
"""

import io
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from PIL import Image, ImageOps

from . import storage
from .database import get_connection

logger = logging.getLogger(__name__)

# Smaller than storage.THUMBNAIL_SIZE -- this is a cropped face, not a
# whole-photo preview.
REPRESENTATIVE_THUMBNAIL_SIZE = (160, 160)


def list_individuals() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, (representative_thumbnail IS NOT NULL) AS has_representative_thumbnail, created_at "
            "FROM individuals ORDER BY name"
        ).fetchall()
        return [dict(row) for row in rows]


def create_individual(name: str) -> dict:
    """Raises psycopg.errors.UniqueViolation (via get_connection, which logs
    and re-raises any psycopg.Error) if `name` is already catalogued --
    left for the router to map to a 409, rather than checked here first and
    racing a concurrent insert of the same name."""
    individual_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO individuals (id, name, created_at) VALUES (%s, %s, %s)",
            (individual_id, name, created_at),
        )
        conn.commit()
    return {"id": individual_id, "name": name, "has_representative_thumbnail": False, "created_at": created_at}


def get_representative_thumbnail(person_id: str) -> Optional[bytes]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT representative_thumbnail FROM individuals WHERE id = %s", (person_id,)
        ).fetchone()
        return row["representative_thumbnail"] if row else None


def _crop_face(original_bytes: bytes, box: dict) -> Optional[bytes]:
    """Crop `box` (x1/y1/x2/y2, in the same EXIF-transposed pixel space
    sync.py's face detection used -- see storage.replace_faces) out of a
    photo's original bytes, downscaled to a small representative
    thumbnail. Best-effort: returns None if the image can't be decoded,
    same as sync.py's own thumbnailing."""
    try:
        with Image.open(io.BytesIO(original_bytes)) as raw_image:
            with ImageOps.exif_transpose(raw_image) as image:
                rgb_image = image.convert("RGB")
    except Exception:
        logger.warning("Could not decode original image to crop a tagged face", exc_info=True)
        return None

    crop = rgb_image.crop((box["x1"], box["y1"], box["x2"], box["y2"]))
    crop.thumbnail(REPRESENTATIVE_THUMBNAIL_SIZE)
    buffer = io.BytesIO()
    crop.save(buffer, "JPEG")
    return buffer.getvalue()


def tag_face(face_id: str, person_id: str) -> Optional[dict]:
    """Assign `person_id` to face `face_id`, and best-effort store the
    cropped face region as that individual's representative thumbnail.
    Returns the updated face row, or None if the face or the individual
    doesn't exist.

    Reads face+photo metadata in one connection, then reads the original
    off the (possibly slow/network) photos backend with no database
    connection held, then writes the update in a second connection --
    mirrors storage.py's "fresh connection per operation" pattern rather
    than holding one open across that I/O."""
    with get_connection() as conn:
        face = conn.execute(
            "SELECT id, photo_id, x1, y1, x2, y2 FROM faces WHERE id = %s", (face_id,)
        ).fetchone()
        if not face:
            return None
        individual = conn.execute("SELECT id FROM individuals WHERE id = %s", (person_id,)).fetchone()
        if not individual:
            return None
        photo = conn.execute("SELECT filename FROM photos WHERE id = %s", (face["photo_id"],)).fetchone()

    thumbnail = None
    if photo:
        try:
            original_bytes = storage.read_original_bytes(dict(photo))
        except Exception:
            logger.warning("Could not read original for face %s from the photos backend", face_id, exc_info=True)
            original_bytes = None
        if original_bytes:
            thumbnail = _crop_face(original_bytes, dict(face))

    with get_connection() as conn:
        conn.execute("UPDATE faces SET person_id = %s WHERE id = %s", (person_id, face_id))
        if thumbnail:
            conn.execute(
                "UPDATE individuals SET representative_thumbnail = %s WHERE id = %s",
                (thumbnail, person_id),
            )
        conn.commit()

    return {
        "id": face["id"],
        "x1": face["x1"],
        "y1": face["y1"],
        "x2": face["x2"],
        "y2": face["y2"],
        "person_id": person_id,
    }
