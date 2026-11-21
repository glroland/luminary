import csv
import io
import json
import logging

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import StreamingResponse

from .. import chat, embeddings, storage, tags
from ..models import ChatRequest, FaceList, Photo, PhotoList, PhotoSearchResults, PhotoTagList

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


@router.get("/photos/duplicates/csv")
def get_duplicate_photos_csv(mode: str = Query("all", pattern="^(exact|similar|all)$")) -> Response:
    """Backs the management page's "Identify Duplicates" button. One row per
    duplicate file, never for the original it's a duplicate of. Column 1 is
    the duplicate and column 2 its original, so bin/move-images.sh (which
    reads column 1 by default) moves the duplicates. `filename`
    already holds the path relative to the photos root, so no separate
    "path" column is needed for either side.

    `mode=all` (the default) reports both kinds; `similar` only the
    resized/re-encoded/reformatted ones (via the perceptual hash --
    docs/duplicate-detection.md); both add match type, hash distance and
    both pixel sizes. `exact` is the original byte-identical check via
    `photos.checksum`, with just the two columns. Registered before "/photos/{photo_id}"
    below, same collision-avoidance reasoning as "/photos/search" above --
    a literal path registered after a dynamic "{photo_id}" segment would
    never be reached."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_ALL)
    rows = storage.find_duplicate_pairs(mode)
    if mode == "exact":
        writer.writerow(["Duplicate Image", "Original Image"])
        for row in rows:
            writer.writerow([row["duplicate"], row["original"]])
    else:
        writer.writerow(
            ["Duplicate Image", "Original Image", "Match Type", "Distance", "Duplicate Size", "Original Size"]
        )
        for row in rows:
            writer.writerow(
                [
                    row["duplicate"],
                    row["original"],
                    row["match_type"],
                    row["distance"],
                    row["duplicate_size"],
                    row["original_size"],
                ]
            )
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="duplicate_photos.csv"'},
    )


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
def get_photo_original(photo_id: str) -> Response:
    photo = storage.get_photo(photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")

    try:
        if photo["content_type"] in storage.TRANSCODED_CONTENT_TYPES:
            # HEIC/HEIF: only Safari can display it, so serve a JPEG instead.
            original_bytes = storage.read_original_bytes(photo)
            if original_bytes is None:
                raise HTTPException(status_code=404, detail="Photo not found")
            return Response(storage.transcode_to_jpeg(original_bytes), media_type="image/jpeg")
        stream = storage.stream_original(photo)
    except HTTPException:
        raise
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


@router.post("/chat")
def chat_about_library(body: ChatRequest):
    """Chat with no photo open: the model has its photo-search tools, but
    no image to look at."""
    return _chat(None, None, body, "the library")


@router.post("/photos/{photo_id}/chat")
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

    return _chat(original_bytes, photo["content_type"], body, f"photo {photo_id}")


def _chat(photo_bytes, content_type, body: ChatRequest, subject: str):
    """Shared by both chat routes. `photo_bytes`/`content_type` are None
    when no photo is open; `subject` only labels log lines."""
    history = [turn.model_dump() for turn in body.history]
    # The search list as the web UI last received it -- the server keeps no
    # conversation state, so this is how a follow-up message can narrow or
    # truncate an earlier search. See chat._Turn.
    search_list = None if body.search_list is None else [entry.model_dump() for entry in body.search_list]

    # chat.ask_events() validates config and builds the request *before*
    # returning its iterator (see chat._ask_openai_events), so
    # ChatNotConfigured/other setup failures become a normal HTTPException
    # here. Reading the first event before the response is constructed
    # extends that to the first call to the chat model, which is where a
    # wrong model name or an unreachable server shows up. Deliberately
    # broad `except Exception` (never a ValueError -> 400 branch): this
    # must also catch things like pydantic.ValidationError, a ValueError
    # subclass the openai SDK raises when parsing a non-conforming response
    # from a self-hosted server -- a backend failure, not a bad request.
    try:
        events = iter(chat.ask_events(photo_bytes, content_type, body.message, history, search_list))
        first = next(events, None)
    except chat.ChatNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception:
        logger.exception("Chat failed for %s", subject)
        raise HTTPException(status_code=502, detail="Chat backend failed")

    # Always a stream, one JSON object per line, so the web UI can show a
    # tool call while it is still running: {"tool": {...}} when the model
    # calls a tool and again when it finishes, {"text": ...} reply pieces
    # (one piece unless LUMINARY_OPENAI_STREAMING is on), {"boxes": [...]}
    # when the model located something in the photo and
    # {"search_results": [...]} when it changed the search list. Once the
    # response has started the status code can't change any more, so a
    # later failure is reported in-band, logged server-side either way.
    def generate():
        try:
            if first is not None:
                yield json.dumps(first) + "\n"
            for event in events:
                yield json.dumps(event) + "\n"
        except Exception:
            logger.exception("Chat failed mid-stream for %s", subject)
            yield json.dumps({"text": "\n[chat backend failed]"}) + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")
