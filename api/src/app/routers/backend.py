from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from .. import backend_connection

router = APIRouter()


class ConnectionStep(BaseModel):
    name: str
    label: str
    # running | done | failed
    status: str
    seconds: Optional[float]


class BackendStatus(BaseModel):
    # idle (nothing attempted yet) | connecting | ready | error
    status: str
    message: str
    steps: list[ConnectionStep]
    error: Optional[str]
    # Seconds the current connection attempt has been running; only while
    # `connecting`.
    elapsed_seconds: Optional[float]


@router.get("/backend/status", response_model=BackendStatus)
def get_backend_status() -> dict:
    """Where the connection to the photos backend stands. Read-only: it
    reports, it never starts a connection (see backend_connection.py)."""
    return backend_connection.snapshot()
