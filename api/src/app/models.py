from typing import Literal, Optional

from pydantic import BaseModel, Field


class Photo(BaseModel):
    id: str
    filename: str
    content_type: str
    size_bytes: int
    width: Optional[int] = None
    height: Optional[int] = None
    # Null when neither the image's metadata nor the backend gives a date.
    created_at: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    # Short, human-readable reverse-geocoded description of latitude/
    # longitude (e.g. "Boone, North Carolina"), via geocode.py -- see
    # sync.py's _describe_location. Null exactly when latitude/longitude
    # are, or if the reverse-geocode lookup failed/wasn't configured.
    location_name: Optional[str] = None
    # The same lookup's structured parts (Nominatim's country/state/city),
    # which the web sidebar's "By Location" tree is built from. Null with
    # location_name, or for a photo geocoded before these columns existed
    # (until "Refresh Locations" is run).
    location_country: Optional[str] = None
    location_state: Optional[str] = None
    location_city: Optional[str] = None
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
    # SHA-256 hex digest of the file's raw bytes (storage.compute_checksum)
    # -- lets two files be compared for byte-for-byte equality reliably,
    # unlike size_bytes alone. Null until a sync has computed it (see
    # sync.py's `needs_checksum`), which happens for every new/changed file
    # and is lazily backfilled for any already-indexed photo missing it.
    checksum: Optional[str] = None


class PhotoList(BaseModel):
    photos: list[Photo]


class PhotoSearchResult(Photo):
    # Cosine similarity (0-1, higher = more similar) between the search
    # query's embedding and this photo's best-matching description sentence (photo_embeddings) -- see
    # storage.search_photos. Absent from the plain Photo model above since
    # it's only meaningful in the context of one particular search.
    similarity: float


class PhotoSearchResults(BaseModel):
    results: list[PhotoSearchResult]


class SyncStatus(BaseModel):
    status: str  # "idle" | "running" | "completed" | "error" | "cancelled"
    processed: int = 0
    total: int = 0
    inserted: int = 0
    updated: int = 0
    deleted: int = 0
    failed: int = 0  # files skipped this run after a backend/database error
    error: Optional[str] = None


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    text: str


class SearchListEntry(BaseModel):
    # One photo in the chat's search list -- see chat._Turn. `filename` is
    # the path relative to the photos root; `score` is 0-1, higher = a
    # better match (cosine similarity for a semantic search, 1 for an
    # exact file name/text/person/tag match).
    id: str
    filename: str
    score: float = Field(ge=0, le=1)


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []
    # The search list as an earlier reply's `search_results` left it, sent
    # back so this message's tools can filter/truncate it. The list is
    # never part of the conversation the model reads.
    search_list: Optional[list[SearchListEntry]] = None


class FaceBox(BaseModel):
    # Pixel coordinates in the *original* photo's own coordinate space
    # (matching Photo.width/height), the same convention as the boxes the
    # chat's object search reports (chat.ChatReply.boxes), but no `label` --
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
    # MTCNN's own per-candidate detection probability (face_detection.
    # detect_faces) -- unrelated to recognition.py's match_confidence,
    # which isn't exposed here (it's an internal matching score, not
    # something PhotoModal.jsx's box overlay shows). Null for a face
    # detected before this column existed (see database.py's ALTER TABLE).
    detection_confidence: Optional[float] = None
    # "person" (a human face, MTCNN) | "pet" (a whole cat/dog, pet_detection.
    # py). `species` is "cat"/"dog" for a pet and null for a person. Only an
    # individual of the same kind can be tagged to the box.
    kind: Literal["person", "pet"] = "person"
    species: Optional[str] = None


class FaceList(BaseModel):
    faces: list[FaceBox]


class Individual(BaseModel):
    id: str
    name: str
    # "person" | "pet" -- which kind of detected box can be tagged to them.
    kind: Literal["person", "pet"] = "person"
    # Whether people.tag_face has stored a representative crop for this
    # person yet -- the actual bytes are fetched separately via
    # GET /api/individuals/{id}/thumbnail, same split as Photo/thumbnail.
    has_representative_thumbnail: bool
    created_at: str


class IndividualList(BaseModel):
    individuals: list[Individual]


class CreateIndividualRequest(BaseModel):
    name: str
    # Ignored by PATCH (rename) -- an individual's kind is fixed at creation.
    kind: Literal["person", "pet"] = "person"


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
    # Cosine similarity of an auto match (0-1); null for manual tags.
    match_confidence: Optional[float] = None


class TaggedFaceList(BaseModel):
    faces: list[TaggedFace]


class RejectedFace(BaseModel):
    # A face the user untagged after it was auto-matched to an individual
    # (a face_rejections row) -- enough to render its crop
    # (GET /api/rejections/{id}/thumbnail) and navigate to its photo.
    id: str
    photo_id: str


class RejectedFaceList(BaseModel):
    faces: list[RejectedFace]


class FaceRefreshStatus(BaseModel):
    # Mirrors SyncStatus's shape -- see recognition.py's _RefreshProgress.
    status: str  # "idle" | "running" | "completed" | "error" | "cancelled"
    processed: int = 0  # candidate (untagged) faces considered so far
    total: int = 0  # candidate faces found at the start of this run
    matched: int = 0  # of those, how many got auto-tagged
    error: Optional[str] = None
    # Which of the two differently-scoped operations this run is/was --
    # False: incremental match-only (sync's post-sync trigger, and the
    # management page's non-destructive "Refresh Faces" button). True: the
    # destructive full redetect-then-match (the management page's "Full
    # Face Refresh" button). Lets the frontend tell them apart when polling
    # status it didn't itself just start (e.g. resuming after a page
    # reload, or a sync-triggered run already in flight).
    full: bool = False


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


class TaggedPhotoIds(BaseModel):
    # The sidebar's "By Tag" filter -- just ids, since the frontend already
    # has every photo's metadata loaded and filters that list itself.
    photo_ids: list[str]


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
    status: str  # "idle" | "running" | "completed" | "error" | "cancelled"
    processed: int = 0  # photos considered so far
    total: int = 0  # photos in the library at the start of this run
    updated: int = 0  # of those, how many actually got a new description
    error: Optional[str] = None


class LocationRefreshStatus(BaseModel):
    # Mirrors DescriptionRefreshStatus's shape -- see geocode.py's
    # _RefreshProgress.
    status: str  # "idle" | "running" | "completed" | "error" | "cancelled"
    processed: int = 0  # photos with GPS coordinates considered so far
    total: int = 0  # such photos in the library at the start of this run
    updated: int = 0  # of those, how many were successfully re-geocoded
    error: Optional[str] = None


class PetRefreshStatus(BaseModel):
    # See pets.py's _PetProgress.
    status: str  # "idle" | "running" | "completed" | "error" | "cancelled"
    processed: int = 0  # photos scanned so far
    total: int = 0  # photos still owed a pet scan at the start of this run
    found: int = 0  # pets (cat/dog boxes) found across them
    error: Optional[str] = None


class TripRefreshStatus(BaseModel):
    # See trips.py's _TripProgress.
    status: str  # "idle" | "running" | "completed" | "error" | "cancelled"
    processed: int = 0  # located, dated photos considered
    total: int = 0
    created: int = 0  # trips newly created by this run
    updated: int = 0  # existing trips whose name/dates/photos changed
    removed: int = 0  # unedited auto trips that no longer qualified
    homes: int = 0  # home locations in force (0 = none found, so no trips)
    error: Optional[str] = None


class Trip(BaseModel):
    id: str
    name: str
    kind: str  # "trip" (2+ calendar days) | "outing" (one day)
    status: str  # "active" | "dismissed"
    source: str  # "auto" | "manual"
    start_at: Optional[str] = None
    end_at: Optional[str] = None
    cover_photo_id: Optional[str] = None
    edited: bool = False
    photo_count: int = 0


class TripList(BaseModel):
    trips: list[Trip]


class TripPhotoIds(BaseModel):
    # Member ids, oldest first -- the frontend already has every photo's
    # metadata loaded and filters/orders that list itself.
    photo_ids: list[str]


class TripStop(BaseModel):
    latitude: float
    longitude: float
    count: int
    first_at: str
    last_at: str
    place: Optional[str] = None
    cover_photo_id: Optional[str] = None
    photo_ids: list[str]


class TripHome(BaseModel):
    latitude: float
    longitude: float
    name: str


class TripLocations(BaseModel):
    stops: list[TripStop]
    route: list[int]  # indexes into `stops`, in visit order
    home: Optional[TripHome] = None
    unlocated_count: int = 0  # members with no GPS (not on the map)


class JobsStatus(BaseModel):
    """Every background job in one response -- see jobs.py. `active` is the
    one job holding the single claim ("sync" | "faces" | "descriptions" |
    "locations" | "pets" | "trips"), or null when idle; `cancelling` is true once a cancel
    was requested and the job hasn't stopped yet."""

    active: Optional[str] = None
    cancelling: bool = False
    sync: SyncStatus
    faces: FaceRefreshStatus
    descriptions: DescriptionRefreshStatus
    locations: LocationRefreshStatus
    pets: PetRefreshStatus
    trips: TripRefreshStatus
