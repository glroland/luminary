from typing import Optional

from pydantic import BaseModel


class Photo(BaseModel):
    id: str
    filename: str
    content_type: str
    size_bytes: int
    width: Optional[int] = None
    height: Optional[int] = None
    uploaded_at: str


class PhotoList(BaseModel):
    photos: list[Photo]
