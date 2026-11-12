"""Catalog of free-form tags that can be attached to whole photos -- see
routers/tags.py (tag catalog CRUD: create/rename/delete, list with counts)
and routers/photos.py's photo-tag endpoints (attaching/detaching a tag on
one specific photo). Distinct from people.py's individuals/faces: a tag
describes the photo as a whole (e.g. "vacation", "favorites"), not a
person detected within it, so there's no bounding box or thumbnail
involved here -- just a many-to-many relationship (`photo_tags`) between
`photos` and `tags`.

The management page only ever shows a tag's *photo count*, never the
photos themselves (unlike individuals, whose per-person accordion lists
every tagged face) -- see ManagementPage.jsx. Tagging/untagging a photo
happens elsewhere in the UI: a right-click menu on the gallery thumbnail,
and an editable panel in PhotoModal -- both call the same
tag_photo/untag_photo below.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from .database import get_connection

logger = logging.getLogger(__name__)


def list_tags() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT t.id, t.name, t.created_at, COUNT(pt.photo_id) AS photo_count
            FROM tags t
            LEFT JOIN photo_tags pt ON pt.tag_id = t.id
            GROUP BY t.id, t.name, t.created_at
            ORDER BY t.name
            """
        ).fetchall()
        return [dict(row) for row in rows]


def create_tag(name: str) -> dict:
    """Raises psycopg.errors.UniqueViolation (via get_connection, which logs
    and re-raises any psycopg.Error) if `name` is already catalogued --
    left for the router to map to a 409, same pattern as
    people.create_individual."""
    tag_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO tags (id, name, created_at) VALUES (%s, %s, %s)",
            (tag_id, name, created_at),
        )
        conn.commit()
    return {"id": tag_id, "name": name, "created_at": created_at, "photo_count": 0}


def _get_tag_with_count(conn, tag_id: str) -> Optional[dict]:
    row = conn.execute(
        """
        SELECT t.id, t.name, t.created_at, COUNT(pt.photo_id) AS photo_count
        FROM tags t
        LEFT JOIN photo_tags pt ON pt.tag_id = t.id
        WHERE t.id = %s
        GROUP BY t.id, t.name, t.created_at
        """,
        (tag_id,),
    ).fetchone()
    return dict(row) if row else None


def update_tag(tag_id: str, name: str) -> Optional[dict]:
    """Rename a tag. Returns the updated row (with its current photo
    count), or None if `tag_id` doesn't exist. Raises
    psycopg.errors.UniqueViolation (see create_tag's docstring) if `name`
    collides with another tag."""
    with get_connection() as conn:
        cursor = conn.execute("UPDATE tags SET name = %s WHERE id = %s", (name, tag_id))
        if cursor.rowcount == 0:
            conn.commit()
            return None
        updated = _get_tag_with_count(conn, tag_id)
        conn.commit()
        return updated


def delete_tag(tag_id: str) -> bool:
    """Delete a tag and every photo_tags row referencing it (no FK cascade
    -- see db/create_tables.sql -- same explicit-cleanup pattern
    people.delete_individual uses for faces). Returns whether the tag
    existed."""
    with get_connection() as conn:
        conn.execute("DELETE FROM photo_tags WHERE tag_id = %s", (tag_id,))
        cursor = conn.execute("DELETE FROM tags WHERE id = %s", (tag_id,))
        conn.commit()
        return cursor.rowcount > 0


def list_tagged_filenames(tag_id: str) -> Optional[list[str]]:
    """Every photo's filename (its path relative to the photos root) with
    this tag attached, sorted -- backs the management page's per-tag "List"
    CSV download. Returns None if the tag doesn't exist, so the router can
    tell "unknown tag" (404) apart from "tag with no photos" (empty CSV)."""
    with get_connection() as conn:
        if not conn.execute("SELECT 1 FROM tags WHERE id = %s", (tag_id,)).fetchone():
            return None
        rows = conn.execute(
            """
            SELECT p.filename FROM photo_tags pt
            JOIN photos p ON p.id = pt.photo_id
            WHERE pt.tag_id = %s
            ORDER BY p.filename
            """,
            (tag_id,),
        ).fetchall()
        return [row["filename"] for row in rows]


def list_tagged_photo_ids(tag_id: str) -> Optional[list[str]]:
    """Ids of every photo with this tag attached -- backs the sidebar's
    "By Tag" filter, which narrows the already-loaded photo list client-side
    (same shape as "By Individual" using a person's faces' photo_ids).
    Returns None if the tag doesn't exist, same as list_tagged_filenames."""
    with get_connection() as conn:
        if not conn.execute("SELECT 1 FROM tags WHERE id = %s", (tag_id,)).fetchone():
            return None
        rows = conn.execute(
            "SELECT photo_id FROM photo_tags WHERE tag_id = %s", (tag_id,)
        ).fetchall()
        return [row["photo_id"] for row in rows]


def list_tags_for_photo(photo_id: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT t.id, t.name FROM tags t
            JOIN photo_tags pt ON pt.tag_id = t.id
            WHERE pt.photo_id = %s
            ORDER BY t.name
            """,
            (photo_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def tag_photo(photo_id: str, tag_id: str) -> Optional[dict]:
    """Attach `tag_id` to `photo_id`, idempotently (ON CONFLICT DO NOTHING
    -- re-tagging an already-tagged photo is a no-op, not an error).
    Returns the tag's {id, name}, or None if the tag doesn't exist.
    Doesn't check that the photo exists -- routers/photos.py's caller
    already has that row in hand, same split people.tag_face makes for
    data its caller already has."""
    with get_connection() as conn:
        tag = conn.execute("SELECT id, name FROM tags WHERE id = %s", (tag_id,)).fetchone()
        if not tag:
            return None
        conn.execute(
            "INSERT INTO photo_tags (photo_id, tag_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (photo_id, tag_id),
        )
        conn.commit()
        return dict(tag)


def untag_photo(photo_id: str, tag_id: str) -> bool:
    """Detach `tag_id` from `photo_id`. Returns whether that pairing
    existed -- not whether the photo/tag themselves do, so untagging
    something that was never tagged is a no-op 404, same shape as
    people.untag_face."""
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM photo_tags WHERE photo_id = %s AND tag_id = %s", (photo_id, tag_id)
        )
        conn.commit()
        return cursor.rowcount > 0
