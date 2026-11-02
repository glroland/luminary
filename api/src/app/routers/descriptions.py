from fastapi import APIRouter

from .. import enrich
from ..models import DescriptionRefreshStatus

router = APIRouter()


@router.post("/descriptions/refresh", response_model=DescriptionRefreshStatus)
def refresh_descriptions() -> dict:
    return enrich.start_refresh()


@router.get("/descriptions/refresh/status", response_model=DescriptionRefreshStatus)
def get_refresh_status() -> dict:
    return enrich.progress.snapshot()
