from fastapi import APIRouter

from .. import pets
from ..models import PetRefreshStatus

router = APIRouter()


@router.post("/pets/refresh", response_model=PetRefreshStatus)
def refresh_pets(rescan: bool = False) -> dict:
    # Non-destructive: only adds pet boxes to photos not yet scanned for
    # them (or, with ?rescan=true, every photo); human faces and tags are
    # never touched (see pets.py).
    return pets.start_refresh(rescan=rescan)


@router.get("/pets/refresh/status", response_model=PetRefreshStatus)
def get_refresh_status() -> dict:
    return pets.progress.snapshot()
