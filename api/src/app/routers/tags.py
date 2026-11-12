import csv
import io
import logging

import psycopg
from fastapi import APIRouter, HTTPException, Response

from .. import tags
from ..models import CreateTagRequest, Tag, TaggedPhotoIds, TagList

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/tags", response_model=TagList)
def get_tags() -> dict:
    return {"tags": tags.list_tags()}


@router.post("/tags", response_model=Tag, status_code=201)
def create_tag(body: CreateTagRequest) -> dict:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name must not be empty")
    try:
        return tags.create_tag(name)
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail=f"A tag named {name!r} already exists")


@router.patch("/tags/{tag_id}", response_model=Tag)
def update_tag(tag_id: str, body: CreateTagRequest) -> dict:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name must not be empty")
    try:
        tag = tags.update_tag(tag_id, name)
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail=f"A tag named {name!r} already exists")
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    return tag


@router.get("/tags/{tag_id}/photos", response_model=TaggedPhotoIds)
def get_tagged_photo_ids(tag_id: str) -> dict:
    """Backs the sidebar's "By Tag" filter: ids of every photo with this tag."""
    photo_ids = tags.list_tagged_photo_ids(tag_id)
    if photo_ids is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    return {"photo_ids": photo_ids}


@router.get("/tags/{tag_id}/photos/csv")
def get_tagged_photos_csv(tag_id: str) -> Response:
    """Backs the management page's per-tag "List" button: a one-column CSV
    of every tagged photo's path+filename relative to the photos root."""
    filenames = tags.list_tagged_filenames(tag_id)
    if filenames is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Path"])
    for filename in filenames:
        writer.writerow([filename])
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="tagged_photos.csv"'},
    )


@router.delete("/tags/{tag_id}", status_code=204)
def remove_tag(tag_id: str) -> None:
    if not tags.delete_tag(tag_id):
        raise HTTPException(status_code=404, detail="Tag not found")
