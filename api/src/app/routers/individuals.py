import logging

import psycopg
from fastapi import APIRouter, HTTPException, Response

from .. import people
from ..models import (
    CreateIndividualRequest,
    Individual,
    IndividualGroup,
    IndividualGroupList,
    IndividualGroupRequest,
    IndividualList,
    RejectedFaceList,
    TaggedFaceList,
    TaggedPhotoIds,
    UpdateIndividualRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/individuals", response_model=IndividualList)
def get_individuals() -> dict:
    return {"individuals": people.list_individuals()}


@router.get("/individuals/unmatched/photos", response_model=TaggedPhotoIds)
def get_unmatched_photo_ids() -> dict:
    """Photos with detected (human) faces but none matched to an individual."""
    return {"photo_ids": people.list_unmatched_photo_ids()}


@router.post("/individuals", response_model=Individual, status_code=201)
def create_individual(body: CreateIndividualRequest) -> dict:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name must not be empty")
    try:
        if body.group_id is not None and not people.group_exists(body.group_id):
            raise HTTPException(status_code=422, detail="Group not found")
        return people.create_individual(name, body.kind, body.group_id, body.sort_order)
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail=f"An individual named {name!r} already exists")


@router.patch("/individuals/{person_id}", response_model=Individual)
def update_individual(person_id: str, body: UpdateIndividualRequest) -> dict:
    changes = {key: getattr(body, key) for key in body.model_fields_set}
    if "name" in changes:
        changes["name"] = (changes["name"] or "").strip()
        if not changes["name"]:
            raise HTTPException(status_code=422, detail="Name must not be empty")
    if changes.get("group_id") is not None and not people.group_exists(changes["group_id"]):
        raise HTTPException(status_code=422, detail="Group not found")
    try:
        individual = people.update_individual(person_id, changes)
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail=f"An individual named {changes['name']!r} already exists")
    if not individual:
        raise HTTPException(status_code=404, detail="Individual not found")
    return individual


@router.get("/individual-groups", response_model=IndividualGroupList)
def get_groups() -> dict:
    return {"groups": people.list_groups()}


@router.post("/individual-groups", response_model=IndividualGroup, status_code=201)
def create_group(body: IndividualGroupRequest) -> dict:
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name must not be empty")
    try:
        return people.create_group(name, body.sort_order)
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail=f"A group named {name!r} already exists")


@router.patch("/individual-groups/{group_id}", response_model=IndividualGroup)
def update_group(group_id: str, body: IndividualGroupRequest) -> dict:
    changes = {key: getattr(body, key) for key in body.model_fields_set}
    if "name" in changes:
        changes["name"] = (changes["name"] or "").strip()
        if not changes["name"]:
            raise HTTPException(status_code=422, detail="Name must not be empty")
    try:
        group = people.update_group(group_id, changes)
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail=f"A group named {changes['name']!r} already exists")
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    return group


@router.delete("/individual-groups/{group_id}", status_code=204)
def remove_group(group_id: str) -> None:
    if not people.delete_group(group_id):
        raise HTTPException(status_code=404, detail="Group not found")


@router.delete("/individuals/{person_id}", status_code=204)
def remove_individual(person_id: str) -> None:
    if not people.delete_individual(person_id):
        raise HTTPException(status_code=404, detail="Individual not found")


@router.get("/individuals/{person_id}/thumbnail")
def get_individual_thumbnail(person_id: str) -> Response:
    thumbnail = people.get_representative_thumbnail(person_id)
    if not thumbnail:
        raise HTTPException(status_code=404, detail="No representative thumbnail tagged for this individual yet")
    return Response(content=thumbnail, media_type="image/jpeg")


@router.get("/individuals/{person_id}/photos", response_model=TaggedPhotoIds)
def get_individual_photo_ids(person_id: str) -> dict:
    """Backs the sidebar's "By Individual" filter (photo ids only; the
    individual's tagged faces are Manage-page data, see /faces below)."""
    if not people.get_individual(person_id):
        raise HTTPException(status_code=404, detail="Individual not found")
    return {"photo_ids": people.list_photo_ids_for_individual(person_id)}


@router.get("/individuals/{person_id}/faces", response_model=TaggedFaceList)
def get_individual_faces(person_id: str) -> dict:
    if not people.get_individual(person_id):
        raise HTTPException(status_code=404, detail="Individual not found")
    return {"faces": people.list_faces_for_individual(person_id)}


@router.get("/individuals/{person_id}/rejections", response_model=RejectedFaceList)
def get_individual_rejections(person_id: str) -> dict:
    if not people.get_individual(person_id):
        raise HTTPException(status_code=404, detail="Individual not found")
    return {"faces": people.list_rejections_for_individual(person_id)}
