from fastapi import APIRouter

from .. import sync
from ..models import SyncStatus

router = APIRouter()


@router.post("/sync", response_model=SyncStatus)
def trigger_sync() -> dict:
    return sync.start_sync()


@router.get("/sync/status", response_model=SyncStatus)
def get_sync_status() -> dict:
    return sync.progress.snapshot()
