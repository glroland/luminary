from typing import Literal, Optional

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


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    text: str


class ChatRequest(BaseModel):
    # Validated here (not left to chat.ask()'s own check) so an unknown
    # backend is rejected by FastAPI's request parsing (422) before the
    # handler runs -- that keeps the router's exception handling for
    # chat.ask() unambiguous: anything it raises is a real backend failure,
    # never a malformed request.
    backend: Literal["openai", "florence"]
    message: str
    history: list[ChatMessage] = []


class ChatResponse(BaseModel):
    reply: str
