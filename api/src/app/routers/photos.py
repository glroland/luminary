import logging

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import StreamingResponse

from .. import chat, embeddings, storage, tags
from ..models import ChatRequest, ChatResponse, FaceList, Photo, PhotoList, PhotoSearchResults, PhotoTagList

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/photos", response_model=PhotoList)
def get_photos() -> dict:
    return {"photos": storage.list_photos()}


@router.get("/photos/search", response_model=PhotoSearchResults)
def search_photos(q: str, threshold: float = 0.5) -> dict:
    # Registered *before* "/photos/{photo_id}" below -- Starlette matches
    # routes in registration order, and both shapes are "/photos/<one
    # segment>", so this must come first or a request for literally
    # "/photos/search" would instead match get_photo(photo_id="search")
    # (same reasoning routers/photos.py's "/purge" endpoint's own comment
    # already documents for a different collision).
    if not q.strip():
        raise HTTPException(status_code=400, detail="Query must not be empty")
    try:
        results = storage.search_photos(q, threshold)
    except embeddings.EmbeddingNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception:
        logger.exception("Description search failed for query %r", q)
        raise HTTPException(status_code=502, detail="Search backend failed")
    return {"results": results}


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


@router.get("/photos/{photo_id}/faces", response_model=FaceList)
def get_photo_faces(photo_id: str) -> dict:
    if not storage.get_photo(photo_id):
        raise HTTPException(status_code=404, detail="Photo not found")
    return {"faces": storage.get_faces(photo_id)}


@router.get("/photos/{photo_id}/tags", response_model=PhotoTagList)
def get_photo_tags(photo_id: str) -> dict:
    if not storage.get_photo(photo_id):
        raise HTTPException(status_code=404, detail="Photo not found")
    return {"tags": tags.list_tags_for_photo(photo_id)}


@router.post("/photos/{photo_id}/tags/{tag_id}", status_code=204)
def add_photo_tag(photo_id: str, tag_id: str) -> None:
    if not storage.get_photo(photo_id):
        raise HTTPException(status_code=404, detail="Photo not found")
    if not tags.tag_photo(photo_id, tag_id):
        raise HTTPException(status_code=404, detail="Tag not found")


@router.delete("/photos/{photo_id}/tags/{tag_id}", status_code=204)
def remove_photo_tag(photo_id: str, tag_id: str) -> None:
    # Untagging something that was never tagged is a no-op, same shape as
    # DELETE /api/faces/{id}/tag -- not worth a 404 over, since the end
    # state (no tag) is identical either way.
    tags.untag_photo(photo_id, tag_id)


@router.get("/photos/{photo_id}/similar", response_model=PhotoList)
def get_similar_photos(photo_id: str) -> dict:
    # storage.find_similar_photos already returns [] (rather than raising)
    # for a photo with no embedding yet, or if pgvector itself was never
    # installed -- a 404 here is reserved for the photo truly not existing.
    if not storage.get_photo(photo_id):
        raise HTTPException(status_code=404, detail="Photo not found")
    return {"photos": storage.find_similar_photos(photo_id)}


@router.delete("/photos/{photo_id}", status_code=204)
def remove_photo(photo_id: str) -> None:
    # Removes the index entry only -- the source file on the photos backend
    # is never touched. If it's still there, the next sync re-indexes it.
    if not storage.delete_photo(photo_id):
        raise HTTPException(status_code=404, detail="Photo not found")


@router.delete("/purge", status_code=204)
def purge_database() -> None:
    # Clears every photos/faces/individuals row -- the photos backend itself
    # is never touched, so a subsequent sync re-indexes everything found
    # there. Registered as a static "/purge" path, not under "/photos/...",
    # so it can never collide with the "/photos/{photo_id}" route above.
    logger.warning("Purging all photos, faces, and individuals from the database")
    storage.purge_all()


@router.post("/photos/{photo_id}/chat", response_model=ChatResponse)
def chat_about_photo(photo_id: str, body: ChatRequest):
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

    history = [turn.model_dump() for turn in body.history]

    if chat.should_stream(body.backend):
        # chat.ask_stream() validates config and builds the request
        # *before* returning its generator (see _prepare_openai_call's
        # docstring), so ChatNotConfigured/other setup failures still
        # become a normal HTTPException here, before any streaming
        # response has started -- only a failure *during* generation (after
        # the 200 and some chunks may already be out) can't change the
        # status code any more; that case is surfaced in-band in the
        # stream itself, logged server-side either way.
        try:
            chunks = chat.ask_stream(body.backend, original_bytes, photo["content_type"], body.message, history)
        except chat.ChatNotConfigured as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except Exception:
            logger.exception("Chat backend %r failed to start streaming for photo %s", body.backend, photo_id)
            raise HTTPException(status_code=502, detail="Chat backend failed")

        def generate():
            try:
                yield from chunks
            except Exception:
                logger.exception(
                    "Streaming chat backend %r failed mid-stream for photo %s", body.backend, photo_id
                )
                yield "\n[chat backend failed]"

        return StreamingResponse(generate(), media_type="text/plain")

    try:
        result = chat.ask(body.backend, original_bytes, photo["content_type"], body.message, history)
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

    return {"reply": result.text, "boxes": result.boxes}
