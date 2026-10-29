from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import StreamingResponse

from .. import storage
from ..models import Photo, PhotoList

router = APIRouter()


@router.get("/photos", response_model=PhotoList)
def get_photos() -> dict:
    return {"photos": storage.list_photos()}


@router.post("/photos")
def upload_photo() -> None:
    # The API never writes to the configured photos backend -- add files
    # directly under LUMINARY_PHOTOS_URI and call POST /api/sync to index
    # them instead.
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
    stream = storage.stream_original(photo) if photo else None
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
