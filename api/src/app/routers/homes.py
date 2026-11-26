from fastapi import APIRouter, Response

from .. import trips
from ..models import HomeChange, HomeCreate, HomeList, HomeUpdate

router = APIRouter()


@router.get("/homes", response_model=HomeList)
def list_homes() -> dict:
    """The home locations trip detection compares photos with. Auto-detected
    ones are unconfirmed until the user confirms or edits them."""
    return {"homes": trips.list_homes()}


@router.post("/homes", response_model=HomeChange, status_code=201)
def create_home(body: HomeCreate) -> dict:
    home = trips.create_home(body.model_dump())
    return {"home": home, "rebuild_started": trips.rebuild_after_home_change()}


@router.patch("/homes/{home_id}", response_model=HomeChange)
def update_home(home_id: str, body: HomeUpdate) -> dict:
    home = trips.update_home(home_id, body.model_dump(exclude_unset=True))
    return {"home": home, "rebuild_started": trips.rebuild_after_home_change()}


@router.post("/homes/{home_id}/confirm", response_model=HomeChange)
def confirm_home(home_id: str) -> dict:
    """Mark a detected home as right. Confirms every detected home, since a
    move gives several dated ones and they only make sense together."""
    home = trips.confirm_home(home_id)
    return {"home": home, "rebuild_started": trips.rebuild_after_home_change()}


@router.delete("/homes/{home_id}", response_model=HomeChange)
def delete_home(home_id: str) -> dict:
    trips.delete_home(home_id)
    return {"home": None, "rebuild_started": trips.rebuild_after_home_change()}
