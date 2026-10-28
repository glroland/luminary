from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import storage
from ..models import Photo, PhotoList

router = APIRouter()


@router.get("/photos", response_model=PhotoList)
def get_photos() -> dict:
    return {"photos": storage.list_photos()}


@router.post("/photos", response_model=Photo, status_code=201)
def upload_photo(file: UploadFile = File(...)) -> dict:
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image uploads are supported")
    return storage.save_photo(file)


@router.get("/photos/{photo_id}", response_model=Photo)
def get_photo(photo_id: str) -> dict:
    photo = storage.get_photo(photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")
    return photo


@router.get("/photos/{photo_id}/original")
def get_photo_original(photo_id: str) -> FileResponse:
    path = storage.find_original_path(photo_id)
    if not path:
        raise HTTPException(status_code=404, detail="Photo not found")
    return FileResponse(path)


@router.get("/photos/{photo_id}/thumbnail")
def get_photo_thumbnail(photo_id: str) -> FileResponse:
    path = storage.find_thumbnail_path(photo_id)
    if not path:
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(path, media_type="image/jpeg")


@router.delete("/photos/{photo_id}", status_code=204)
def remove_photo(photo_id: str) -> None:
    if not storage.delete_photo(photo_id):
        raise HTTPException(status_code=404, detail="Photo not found")
