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
    # Florence-2's <MORE_DETAILED_CAPTION> description, generated during
    # sync (see sync.py) -- null until a sync has run with Florence-2
    # available, or if it couldn't be generated for this photo.
    description: Optional[str] = None


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


class BoundingBox(BaseModel):
    # Pixel coordinates in the *original* photo's own coordinate space
    # (matching Photo.width/height) -- never a resized/internal coordinate
    # space some model happened to process at. See chat.ChatReply.
    label: str
    x1: float
    y1: float
    x2: float
    y2: float


class ChatResponse(BaseModel):
    reply: str
    # Only ever non-empty for a florence grounding result -- the openai
    # backend has no concept of image regions. Always [] for a streamed
    # reply too (streaming is openai-only, see chat.should_stream).
    boxes: list[BoundingBox] = []


class FaceBox(BaseModel):
    # Same pixel-coordinate convention as BoundingBox, but no `label` --
    # the `faces` table doesn't store one; every row is a face by
    # construction (see sync.py's Florence-2 face-detection pipeline).
    id: str
    x1: float
    y1: float
    x2: float
    y2: float
    # Set by POST /api/faces/{id}/tag once a user has manually tagged this
    # face as a known individual; null otherwise.
    person_id: Optional[str] = None


class FaceList(BaseModel):
    faces: list[FaceBox]


class Individual(BaseModel):
    id: str
    name: str
    # Whether people.tag_face has stored a representative crop for this
    # person yet -- the actual bytes are fetched separately via
    # GET /api/individuals/{id}/thumbnail, same split as Photo/thumbnail.
    has_representative_thumbnail: bool
    created_at: str


class IndividualList(BaseModel):
    individuals: list[Individual]


class CreateIndividualRequest(BaseModel):
    name: str


class TagFaceRequest(BaseModel):
    person_id: str
