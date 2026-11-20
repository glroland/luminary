from fastapi import APIRouter, HTTPException, Response

from .. import people

router = APIRouter()


@router.get("/rejections/{rejection_id}/thumbnail")
def get_rejection_thumbnail(rejection_id: str) -> Response:
    thumbnail = people.get_rejection_thumbnail(rejection_id)
    if not thumbnail:
        raise HTTPException(status_code=404, detail="Rejection not found")
    return Response(content=bytes(thumbnail), media_type="image/jpeg")


@router.delete("/rejections/{rejection_id}", status_code=204)
def delete_rejection(rejection_id: str) -> None:
    if not people.delete_rejection(rejection_id):
        raise HTTPException(status_code=404, detail="Rejection not found")
