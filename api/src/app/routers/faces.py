import logging

from fastapi import APIRouter, HTTPException, Response

from .. import people, recognition
from ..models import FaceBox, FaceRefreshStatus, TagFaceRequest

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/faces/refresh", response_model=FaceRefreshStatus)
def refresh_faces() -> dict:
    return recognition.start_refresh()


@router.get("/faces/refresh/status", response_model=FaceRefreshStatus)
def get_refresh_status() -> dict:
    return recognition.progress.snapshot()


@router.post("/faces/{face_id}/tag", response_model=FaceBox)
def tag_face(face_id: str, body: TagFaceRequest) -> dict:
    face = people.tag_face(face_id, body.person_id)
    if not face:
        raise HTTPException(status_code=404, detail="Face or individual not found")
    return face


@router.delete("/faces/{face_id}/tag", status_code=204)
def untag_face(face_id: str) -> None:
    if not people.untag_face(face_id):
        raise HTTPException(status_code=404, detail="Face not found")


@router.get("/faces/{face_id}/thumbnail")
def get_face_thumbnail(face_id: str) -> Response:
    thumbnail = people.get_face_thumbnail(face_id)
    if not thumbnail:
        raise HTTPException(status_code=404, detail="No thumbnail for this face (not tagged, or not found)")
    return Response(content=thumbnail, media_type="image/jpeg")
