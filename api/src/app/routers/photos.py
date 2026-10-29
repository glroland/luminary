import logging

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import StreamingResponse

from .. import chat, storage
from ..models import ChatRequest, ChatResponse, Photo, PhotoList

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/photos", response_model=PhotoList)
def get_photos() -> dict:
    return {"photos": storage.list_photos()}


@router.post("/photos")
def upload_photo() -> None:
    # The API never writes to the configured photos backend -- add files
    # directly under LUMINARY_PHOTOS_URI and call POST /api/sync to index
    # them instead.
    logger.warning("Rejected upload attempt: uploads are disabled")
    raise HTTPException(
        status_code=403,
        detail=(
            "Uploads are disabled: this API never writes to the configured "
            "photos storage. Add the file directly under LUMINARY_PHOTOS_URI "
            "and call POST /api/sync to index it."
        ),
    )


@router.get("/photos/{photo_id}", response_model=Photo)
def get_photo(photo_id: str) -> dict:
    photo = storage.get_photo(photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")
    return photo


@router.get("/photos/{photo_id}/original")
def get_photo_original(photo_id: str) -> StreamingResponse:
    photo = storage.get_photo(photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")

    try:
        stream = storage.stream_original(photo)
    except Exception:
        logger.exception(
            "Failed to read original for photo %s (%s) from the photos backend",
            photo_id,
            photo["filename"],
        )
        raise HTTPException(status_code=502, detail="Could not read the original from the photos backend")

    if not stream:
        raise HTTPException(status_code=404, detail="Photo not found")
    return StreamingResponse(stream, media_type=photo["content_type"])


@router.get("/photos/{photo_id}/thumbnail")
def get_photo_thumbnail(photo_id: str) -> Response:
    thumbnail = storage.get_thumbnail(photo_id)
    if not thumbnail:
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return Response(content=thumbnail, media_type="image/jpeg")


@router.delete("/photos/{photo_id}", status_code=204)
def remove_photo(photo_id: str) -> None:
    # Removes the index entry only -- the source file on the photos backend
    # is never touched. If it's still there, the next sync re-indexes it.
    if not storage.delete_photo(photo_id):
        raise HTTPException(status_code=404, detail="Photo not found")


@router.post("/photos/{photo_id}/chat", response_model=ChatResponse)
def chat_about_photo(photo_id: str, body: ChatRequest) -> dict:
    photo = storage.get_photo(photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")

    try:
        original_bytes = storage.read_original_bytes(photo)
    except Exception:
        logger.exception(
            "Failed to read original for photo %s (%s) from the photos backend", photo_id, photo["filename"]
        )
        raise HTTPException(status_code=502, detail="Could not read the original from the photos backend")

    if original_bytes is None:
        raise HTTPException(status_code=404, detail="Photo not found")

    try:
        reply = chat.ask(
            body.backend,
            original_bytes,
            photo["content_type"],
            body.message,
            [turn.model_dump() for turn in body.history],
        )
    except chat.ChatNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception:
        # Deliberately broad: this must also catch things like
        # pydantic.ValidationError (a ValueError subclass) raised from
        # inside the openai SDK when parsing a non-conforming response from
        # a self-hosted OpenAI-compatible server -- that's a backend
        # failure, not a malformed request, and must not come back as a
        # misleading 400.
        logger.exception("Chat backend %r failed for photo %s", body.backend, photo_id)
        raise HTTPException(status_code=502, detail="Chat backend failed")

    return {"reply": reply}
