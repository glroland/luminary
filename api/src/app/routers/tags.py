import logging

import psycopg
from fastapi import APIRouter, HTTPException

from .. import tags
from ..models import CreateTagRequest, Tag, TagList

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


@router.delete("/tags/{tag_id}", status_code=204)
def remove_tag(tag_id: str) -> None:
    if not tags.delete_tag(tag_id):
        raise HTTPException(status_code=404, detail="Tag not found")
