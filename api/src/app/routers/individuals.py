import logging

import psycopg
from fastapi import APIRouter, HTTPException, Response

from .. import people
from ..models import CreateIndividualRequest, Individual, IndividualList

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/individuals", response_model=IndividualList)
def get_individuals() -> dict:
    return {"individuals": people.list_individuals()}


@router.post("/individuals", response_model=Individual, status_code=201)
def create_individual(body: CreateIndividualRequest) -> dict:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name must not be empty")
    try:
        return people.create_individual(name)
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail=f"An individual named {name!r} already exists")


@router.get("/individuals/{person_id}/thumbnail")
def get_individual_thumbnail(person_id: str) -> Response:
    thumbnail = people.get_representative_thumbnail(person_id)
    if not thumbnail:
        raise HTTPException(status_code=404, detail="No representative thumbnail tagged for this individual yet")
    return Response(content=thumbnail, media_type="image/jpeg")
