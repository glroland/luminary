from fastapi import APIRouter

from .. import geocode
from ..models import LocationRefreshStatus

router = APIRouter()


@router.post("/locations/refresh", response_model=LocationRefreshStatus)
def refresh_locations() -> dict:
    return geocode.start_refresh()


@router.get("/locations/refresh/status", response_model=LocationRefreshStatus)
def get_refresh_status() -> dict:
    return geocode.progress.snapshot()
