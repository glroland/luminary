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
tag_face for one face, delete_individual for however many faces
were tagged to the deleted individual -- also calls
enrich.enrich_description(photo_id) for each affected photo afterward:
`photos.description` (see enrich.py's module docstring) is partly composed
from the names of individuals tagged in a photo, so a tagging change here
is one of the things that has to invalidate/recompute it, same as a sync
re-detecting faces does. That call is synchronous, inline, on the same
request this module is already handling (an LLM call, same as
POST /api/photos/{id}/chat already makes synchronously) -- and best-effort,
so a failure there never turns a successful tag/delete into an error
response. untag_face is the exception: it only clears the photo's
description and description embeddings (a cheap SQL write) and leaves
regenerating them to the next "Refresh Descriptions" run.

Untagging a face that recognition.py *auto*-tagged also records a
`face_rejections` row (the crop, the wrong match's score and the threshold
then in force), which recognition.py uses as a negative example so the
mistake doesn't come back. Manual untags aren't recorded: they're usually
a misclick or a correction to a reference, and removing a manual tag
already removes it from the references. Manually tagging a face removes an
overlapping rejection for that person; deleting an individual removes
theirs.
"""

import io
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from PIL import Image, ImageOps

from . import embeddings, enrich, storage
from .config import get_face_match_threshold, get_pet_match_threshold
from .database import get_connection

logger = logging.getLogger(__name__)

# Two boxes in the same photo count as the same face at or above this IoU.
REJECTION_MATCH_IOU = 0.5

# Smaller than storage.THUMBNAIL_SIZE -- this is a cropped face, not a
# whole-photo preview.
REPRESENTATIVE_THUMBNAIL_SIZE = (160, 160)


def list_individuals() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, kind, (representative_thumbnail IS NOT NULL) AS has_representative_thumbnail, "
            "created_at FROM individuals ORDER BY name"
        ).fetchall()
        return [dict(row) for row in rows]


INDIVIDUAL_KINDS = ("person", "pet")


class KindMismatch(Exception):
    """A face can only be tagged to an individual of its own kind -- a
    detected dog is never a catalogued person, or the other way round.
    Mapped to a 409 by routers/faces.py."""


def create_individual(name: str, kind: str = "person") -> dict:
    """Raises psycopg.errors.UniqueViolation (via get_connection, which logs
    and re-raises any psycopg.Error) if `name` is already catalogued --
    left for the router to map to a 409, rather than checked here first and
    racing a concurrent insert of the same name."""
    individual_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO individuals (id, name, kind, created_at) VALUES (%s, %s, %s, %s)",
            (individual_id, name, kind, created_at),
        )
        conn.commit()
    return {
        "id": individual_id,
        "name": name,
        "kind": kind,
        "has_representative_thumbnail": False,
        "created_at": created_at,
    }


def get_individual(person_id: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, name, kind, (representative_thumbnail IS NOT NULL) AS has_representative_thumbnail, "
            "created_at FROM individuals WHERE id = %s",
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
        conn.execute("DELETE FROM face_rejections WHERE person_id = %s", (person_id,))
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
    management page's per-individual accordion. Newest photo first (undated
    photos last), id as a stable tiebreak."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT f.id, f.photo_id, f.tag_source, f.match_confidence FROM faces f "
            "JOIN photos p ON p.id = f.photo_id WHERE f.person_id = %s "
            "ORDER BY p.created_at DESC NULLS LAST, f.id",
            (person_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def list_rejections_for_individual(person_id: str) -> list[dict]:
    """Every face untagged after being auto-matched to `person_id` (see
    this module's docstring) -- id and photo only, never the crop bytes;
    those are served per rejection by get_rejection_thumbnail."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT r.id, r.photo_id FROM face_rejections r LEFT JOIN photos p ON p.id = r.photo_id "
            "WHERE r.person_id = %s ORDER BY p.created_at DESC NULLS LAST, r.id",
            (person_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_rejection_thumbnail(rejection_id: str) -> Optional[bytes]:
    with get_connection() as conn:
        row = conn.execute("SELECT thumbnail FROM face_rejections WHERE id = %s", (rejection_id,)).fetchone()
        return row["thumbnail"] if row else None


def delete_rejection(rejection_id: str) -> bool:
    """Forget one rejection, so recognition.py may match that face to the
    individual again on a later run. Returns whether it existed."""
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM face_rejections WHERE id = %s", (rejection_id,))
        conn.commit()
        return cursor.rowcount > 0


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

    Does *not* regenerate photos.description inline -- that is an LLM call
    plus one embedding call per sentence, which made unmatching take
    seconds. Instead the photo's description (which named the person) and
    its description embeddings are cleared, in the same transaction, so
    nothing stale is shown or searched; "Refresh Descriptions" writes a
    new one. `RETURNING photo_id` gives the photo without a second
    query."""
    with get_connection() as conn:
        # The match threshold in force when the (wrong) auto-match was made
        # is the one for the face's own kind -- pets use a different scale.
        kind_row = conn.execute("SELECT kind FROM faces WHERE id = %s", (face_id,)).fetchone()
        match_threshold = (
            get_pet_match_threshold() if kind_row and kind_row["kind"] == "pet" else get_face_match_threshold()
        )
        # An *auto* tag being removed means recognition.py got it wrong:
        # keep the crop and score as a face_rejections row (recognition.py
        # uses it as a negative example) before the UPDATE below discards
        # them. A manual untag isn't recorded -- see this module's
        # docstring on why only auto tags count as a "that's not them".
        conn.execute(
            "INSERT INTO face_rejections (id, person_id, photo_id, x1, y1, x2, y2, thumbnail, "
            "match_confidence, rejected_at_threshold) "
            "SELECT %s, person_id, photo_id, x1, y1, x2, y2, thumbnail, match_confidence, %s "
            "FROM faces WHERE id = %s AND tag_source = 'auto' AND person_id IS NOT NULL "
            "AND thumbnail IS NOT NULL",
            (str(uuid.uuid4()), match_threshold, face_id),
        )
        cursor = conn.execute(
            "UPDATE faces SET person_id = NULL, thumbnail = NULL, tag_source = NULL, match_confidence = NULL "
            "WHERE id = %s RETURNING photo_id",
            (face_id,),
        )
        row = cursor.fetchone()
        if row:
            conn.execute("UPDATE photos SET description = NULL WHERE id = %s", (row["photo_id"],))
            conn.execute(
                "DELETE FROM photo_embeddings WHERE photo_id = %s AND source_type = %s",
                (row["photo_id"], embeddings.SOURCE_DESCRIPTION),
            )
        conn.commit()

    return row is not None


def box_iou(a: dict, b: dict) -> float:
    """Intersection-over-union of two x1/y1/x2/y2 boxes."""
    width = min(a["x2"], b["x2"]) - max(a["x1"], b["x1"])
    height = min(a["y2"], b["y2"]) - max(a["y1"], b["y1"])
    if width <= 0 or height <= 0:
        return 0.0
    inter = width * height
    union = (a["x2"] - a["x1"]) * (a["y2"] - a["y1"]) + (b["x2"] - b["x1"]) * (b["y2"] - b["y1"]) - inter
    return inter / union if union > 0 else 0.0


# A pet's box is the whole animal, tight to the body; a small margin keeps
# ears and tail tips in its crop. A human face box is used as detected.
PET_CROP_MARGIN = 0.1


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

    # A stored box can be degenerate (zero/negative area) or lie outside the
    # image (e.g. detected in a different pixel space than the one decoded
    # here); PIL then yields an empty crop and JPEG save raises "cannot write
    # empty image". Clamp to the image, and treat an empty result as "no crop".
    width, height = rgb_image.size
    left, right = min(box["x1"], box["x2"]), max(box["x1"], box["x2"])
    top, bottom = min(box["y1"], box["y2"]), max(box["y1"], box["y2"])
    if box.get("kind") == "pet":
        margin_x, margin_y = (right - left) * PET_CROP_MARGIN, (bottom - top) * PET_CROP_MARGIN
        left, right, top, bottom = left - margin_x, right + margin_x, top - margin_y, bottom + margin_y
    x1 = max(0, int(left))
    y1 = max(0, int(top))
    x2 = min(width, int(right))
    y2 = min(height, int(bottom))
    if x2 <= x1 or y2 <= y1:
        logger.warning("Face box %s is empty or outside the %dx%d image; skipping crop", box, width, height)
        return None

    crop = rgb_image.crop((x1, y1, x2, y2))
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
    docstring) for the face's photo before returning.

    Raises KindMismatch if the face's kind (person/pet) differs from the
    individual's."""
    with get_connection() as conn:
        face = conn.execute(
            "SELECT id, photo_id, x1, y1, x2, y2, kind, species FROM faces WHERE id = %s", (face_id,)
        ).fetchone()
        if not face:
            return None
        individual = conn.execute("SELECT id, kind FROM individuals WHERE id = %s", (person_id,)).fetchone()
        if not individual:
            return None
        # "person" (a human face) <-> individual.kind "person"; a pet box
        # <-> "pet". Checked before any I/O or write.
        if face["kind"] != individual["kind"]:
            raise KindMismatch(f"A {face['kind']} face can't be tagged to a {individual['kind']}")
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
        # Manually tagging a face to someone overrides an earlier "not
        # them" rejection of the same face (same photo, overlapping box --
        # its id may differ if the photo was re-detected since).
        for rejection in conn.execute(
            "SELECT id, x1, y1, x2, y2 FROM face_rejections WHERE person_id = %s AND photo_id = %s",
            (person_id, face["photo_id"]),
        ).fetchall():
            if box_iou(dict(face), dict(rejection)) >= REJECTION_MATCH_IOU:
                conn.execute("DELETE FROM face_rejections WHERE id = %s", (rejection["id"],))
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
        "kind": face["kind"],
        "species": face["species"],
    }
