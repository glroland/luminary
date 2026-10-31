import logging

from fastapi import APIRouter, HTTPException

from .. import people
from ..models import FaceBox, TagFaceRequest

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/faces/{face_id}/tag", response_model=FaceBox)
def tag_face(face_id: str, body: TagFaceRequest) -> dict:
    face = people.tag_face(face_id, body.person_id)
    if not face:
        raise HTTPException(status_code=404, detail="Face or individual not found")
    return face
