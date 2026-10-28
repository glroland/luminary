from typing import Optional

from pydantic import BaseModel


class Photo(BaseModel):
    id: str
    filename: str
    content_type: str
    size_bytes: int
    width: Optional[int] = None
    height: Optional[int] = None
    created_at: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class PhotoList(BaseModel):
    photos: list[Photo]


class SyncStatus(BaseModel):
    status: str  # "idle" | "running" | "completed" | "error"
    processed: int = 0
    total: int = 0
    inserted: int = 0
    updated: int = 0
    deleted: int = 0
    error: Optional[str] = None
