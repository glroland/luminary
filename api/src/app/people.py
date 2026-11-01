"""Catalog of named individuals, and manual face-tagging against that
catalog (`faces.person_id`) -- see routers/individuals.py and
routers/faces.py. Distinct from sync.py's Florence-2 face *detection*: this
module never detects anything, it only records a user's manual assignment
of an already-detected face (storage.get_faces/replace_faces) to a person.
See recognition.py for the *automatic* counterpart -- "Refresh Faces"
auto-assigns `person_id` to other faces by embedding similarity against the
faces this module tags manually, writing the same columns tag_face does
(plus `tag_source`/`match_confidence` -- see faces.tag_source's comment in
db/create_tables.sql) so both kinds of tag render the same way everywhere.

Tagging a face also crops that face's rectangle out of the photo's original
bytes, storing that crop both on the face itself (`faces.thumbnail`, so the
management page can list every face ever tagged to a person) and as the
individual's `representative_thumbnail` (the most recently tagged face
"wins" there -- it's a single quick-reference avatar, e.g. PhotoModal's
People list, not a gallery). `faces.thumbnail` is only ever non-NULL while
`person_id` is also set -- untag_face/delete_individual clear both
together, so that pairing is never violated.

Every function here that changes "who's tagged in a photo" --
tag_face/untag_face for one face, delete_individual for however many faces
were tagged to the deleted individual -- also calls
enrich.enrich_description(photo_id) for each affected photo afterward:
`photos.description` (see enrich.py's module docstring) is partly composed
from the names of individuals tagged in a photo, so a tagging change here
is one of the things that has to invalidate/recompute it, same as a sync
re-detecting faces does. That call is synchronous, inline, on the same
request this module is already handling (an LLM call, same as
POST /api/photos/{id}/chat already makes synchronously) -- and best-effort,
so a failure there never turns a successful tag/untag/delete into an error
response.
"""

import io
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from PIL import Image, ImageOps

from . import enrich, storage
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


def get_individual(person_id: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, name, (representative_thumbnail IS NOT NULL) AS has_representative_thumbnail, created_at "
            "FROM individuals WHERE id = %s",
            (person_id,),
        ).fetchone()
        return dict(row) if row else None


def update_individual(person_id: str, name: str) -> Optional[dict]:
    """Rename an individual. Returns the updated row, or None if `person_id`
    doesn't exist. Raises psycopg.errors.UniqueViolation (see
    create_individual's docstring) if `name` collides with another
    individual."""
    with get_connection() as conn:
        cursor = conn.execute("UPDATE individuals SET name = %s WHERE id = %s", (name, person_id))
        conn.commit()
        if cursor.rowcount == 0:
            return None
    return get_individual(person_id)


def delete_individual(person_id: str) -> bool:
    """Delete an individual and untag every face tagged to them, manual or
    auto (no FK cascade -- see db/create_tables.sql -- so that's done
    explicitly here, same pattern as storage.delete_photo clearing a
    photo's faces). Returns whether the individual existed.

    Regenerates photos.description (enrich.py, see this module's
    docstring) for every photo that had a face tagged to this individual --
    read *before* the UPDATE below clears person_id, since that's the only
    way to find them afterward. Once per affected photo, not once per
    face: enrich_description already reads every tagged individual for a
    photo in one query, so two faces in the same photo tagged to the
    individual being deleted only need one regeneration."""
    with get_connection() as conn:
        affected_photo_ids = [
            row["photo_id"]
            for row in conn.execute(
                "SELECT DISTINCT photo_id FROM faces WHERE person_id = %s", (person_id,)
            ).fetchall()
        ]
        conn.execute(
            "UPDATE faces SET person_id = NULL, thumbnail = NULL, tag_source = NULL, match_confidence = NULL "
            "WHERE person_id = %s",
            (person_id,),
        )
        cursor = conn.execute("DELETE FROM individuals WHERE id = %s", (person_id,))
        conn.commit()

    existed = cursor.rowcount > 0
    if existed:
        for photo_id in affected_photo_ids:
            enrich.enrich_description(photo_id)
    return existed


def get_representative_thumbnail(person_id: str) -> Optional[bytes]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT representative_thumbnail FROM individuals WHERE id = %s", (person_id,)
        ).fetchone()
        return row["representative_thumbnail"] if row else None


def list_faces_for_individual(person_id: str) -> list[dict]:
    """Every face currently tagged to `person_id` -- the data behind the
    management page's per-individual accordion. Ordered by id purely for a
    stable, deterministic response; `faces` has no timestamp column to
    order by "most recently tagged" (see this module's docstring)."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, photo_id, tag_source FROM faces WHERE person_id = %s ORDER BY id", (person_id,)
        ).fetchall()
        return [dict(row) for row in rows]


def get_face_thumbnail(face_id: str) -> Optional[bytes]:
    with get_connection() as conn:
        row = conn.execute("SELECT thumbnail FROM faces WHERE id = %s", (face_id,)).fetchone()
        return row["thumbnail"] if row else None


def untag_face(face_id: str) -> bool:
    """Clear a face's tag (person_id, thumbnail, tag_source, and
    match_confidence together -- see this module's docstring) without
    deleting the face/detection itself -- works the same whether the tag
    was manual or recognition.py's auto-match. Returns whether the face
    existed.

    Regenerates photos.description (enrich.py, see this module's
    docstring) for the face's photo -- `RETURNING photo_id` so that's
    available without a second query."""
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE faces SET person_id = NULL, thumbnail = NULL, tag_source = NULL, match_confidence = NULL "
            "WHERE id = %s RETURNING photo_id",
            (face_id,),
        )
        row = cursor.fetchone()
        conn.commit()

    if not row:
        return False
    enrich.enrich_description(row["photo_id"])
    return True


def crop_face_thumbnail(original_bytes: bytes, box: dict) -> Optional[bytes]:
    """Crop `box` (x1/y1/x2/y2, in the same EXIF-transposed pixel space
    sync.py's face detection used -- see storage.replace_faces) out of a
    photo's original bytes, downscaled to a small representative
    thumbnail. Best-effort: returns None if the image can't be decoded,
    same as sync.py's own thumbnailing. Public (not `_`-prefixed) because
    recognition.py reuses it for the same crop, both for embedding a
    candidate face and for storing its thumbnail once auto-tagged."""
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
    cropped face region both on the face itself (`thumbnail`, for the
    management page's per-individual face list) and as that individual's
    representative thumbnail. Returns the updated face row, or None if the
    face or the individual doesn't exist.

    Reads face+photo metadata in one connection, then reads the original
    off the (possibly slow/network) photos backend with no database
    connection held, then writes the update in a second connection --
    mirrors storage.py's "fresh connection per operation" pattern rather
    than holding one open across that I/O.

    `faces.thumbnail` is always overwritten with this call's result,
    including to NULL if cropping fails -- a stale thumbnail from a
    previous (possibly different) tagging of this same face must not
    survive. `individuals.representative_thumbnail` is gentler: only
    overwritten when this call's crop actually succeeds, so a transient
    failure here doesn't blank out that person's avatar elsewhere.

    Always sets `tag_source = 'manual'` (and clears `match_confidence`),
    even if the face was previously auto-tagged by recognition.py -- a
    manual tag always wins over, and replaces, an auto one.

    Regenerates photos.description (enrich.py, see this module's
    docstring) for the face's photo before returning."""
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
            thumbnail = crop_face_thumbnail(original_bytes, dict(face))

    with get_connection() as conn:
        conn.execute(
            "UPDATE faces SET person_id = %s, thumbnail = %s, tag_source = 'manual', match_confidence = NULL "
            "WHERE id = %s",
            (person_id, thumbnail, face_id),
        )
        if thumbnail:
            conn.execute(
                "UPDATE individuals SET representative_thumbnail = %s WHERE id = %s",
                (thumbnail, person_id),
            )
        conn.commit()

    enrich.enrich_description(face["photo_id"])

    return {
        "id": face["id"],
        "x1": face["x1"],
        "y1": face["y1"],
        "x2": face["x2"],
        "y2": face["y2"],
        "person_id": person_id,
        "tag_source": "manual",
    }
