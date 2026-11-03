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
    # Short, human-readable reverse-geocoded description of latitude/
    # longitude (e.g. "Boone, North Carolina"), via geocode.py -- see
    # sync.py's _describe_location. Null exactly when latitude/longitude
    # are, or if the reverse-geocode lookup failed/wasn't configured.
    location_name: Optional[str] = None
    # Florence-2's raw <MORE_DETAILED_CAPTION> output, generated during
    # sync (see sync.py's _generate_vlm_description) -- null until a sync
    # has run with Florence-2 available, or if captioning failed for this
    # photo. Feeds into `description` below as one of its inputs; not
    # shown directly in the UI.
    vlm_description: Optional[str] = None
    # The description actually shown in the UI: an LLM-composed rewrite
    # (enrich.py, any OpenAI-*compatible* chat endpoint) weaving together
    # vlm_description, the names of any individuals tagged in the photo,
    # location_name, and the filename/path. Null until that's run
    # successfully at least once for this photo (see enrich.py's module
    # docstring for every point that (re)triggers it) -- a stale-but-
    # present value is kept on a later failure rather than cleared.
    description: Optional[str] = None


class PhotoList(BaseModel):
    photos: list[Photo]


class PhotoSearchResult(Photo):
    # Cosine similarity (0-1, higher = more similar) between the search
    # query's embedding and this photo's description_embedding -- see
    # storage.search_photos. Absent from the plain Photo model above since
    # it's only meaningful in the context of one particular search.
    similarity: float


class PhotoSearchResults(BaseModel):
    results: list[PhotoSearchResult]


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
    # Set by POST /api/faces/{id}/tag (manual) or recognition.py's
    # "Refresh Faces" (auto); null until either tags this face.
    person_id: Optional[str] = None
    # "manual" | "auto" | null (null exactly when person_id is). See
    # faces.tag_source's comment in db/create_tables.sql.
    tag_source: Optional[str] = None


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


class TaggedFace(BaseModel):
    # The management page's per-individual face list -- deliberately
    # minimal (no x1-y2 box, no photo filename): the frontend already has
    # the full photo list loaded and only needs enough to render a
    # thumbnail (GET /api/faces/{id}/thumbnail) and navigate back to the
    # source photo (`photo_id`).
    id: str
    photo_id: str
    # "manual" | "auto" -- drives the per-face "auto" badge in the
    # management page's accordion. Every row here has person_id set (by
    # construction -- see people.list_faces_for_individual), so this is
    # never null in practice, unlike FaceBox.tag_source.
    tag_source: Optional[str] = None


class TaggedFaceList(BaseModel):
    faces: list[TaggedFace]


class FaceRefreshStatus(BaseModel):
    # Mirrors SyncStatus's shape -- see recognition.py's _RefreshProgress.
    status: str  # "idle" | "running" | "completed" | "error"
    processed: int = 0  # candidate (untagged) faces considered so far
    total: int = 0  # candidate faces found at the start of this run
    matched: int = 0  # of those, how many got auto-tagged
    error: Optional[str] = None


class Tag(BaseModel):
    id: str
    name: str
    created_at: str
    # Number of photos currently tagged with this -- the management page
    # shows this instead of the photos themselves (unlike Individual,
    # whose per-person accordion lists every tagged face).
    photo_count: int


class TagList(BaseModel):
    tags: list[Tag]


class CreateTagRequest(BaseModel):
    name: str


class PhotoTag(BaseModel):
    # The tags attached to one specific photo (GET /api/photos/{id}/tags)
    # -- deliberately minimal, same reasoning as TaggedFace: the frontend
    # only needs enough to render/toggle a tag, not the full catalog row's
    # created_at/photo_count.
    id: str
    name: str


class PhotoTagList(BaseModel):
    tags: list[PhotoTag]


class DescriptionRefreshStatus(BaseModel):
    # Mirrors FaceRefreshStatus's shape -- see enrich.py's _RefreshProgress.
    status: str  # "idle" | "running" | "completed" | "error"
    processed: int = 0  # photos considered so far
    total: int = 0  # photos in the library at the start of this run
    updated: int = 0  # of those, how many actually got a new description
    error: Optional[str] = None


class LocationRefreshStatus(BaseModel):
    # Mirrors DescriptionRefreshStatus's shape -- see geocode.py's
    # _RefreshProgress.
    status: str  # "idle" | "running" | "completed" | "error"
    processed: int = 0  # photos with GPS coordinates considered so far
    total: int = 0  # such photos in the library at the start of this run
    updated: int = 0  # of those, how many were successfully re-geocoded
    error: Optional[str] = None
