import csv
import io

from fastapi import APIRouter, HTTPException, Query, Response

from .. import duplicates
from ..models import DuplicateRefreshStatus

router = APIRouter()


@router.post("/duplicates/refresh", response_model=DuplicateRefreshStatus)
def refresh_duplicates(mode: str = Query("all", pattern="^(similar|all)$")) -> dict:
    # Starts the background search for resized/re-encoded duplicates (too slow
    # for one request on a big library); `exact` stays on the synchronous
    # GET /api/photos/duplicates/csv. See duplicates.py.
    return duplicates.start_refresh(mode)


@router.get("/duplicates/refresh/status", response_model=DuplicateRefreshStatus)
def get_refresh_status() -> dict:
    return duplicates.progress.snapshot()


@router.get("/duplicates/csv")
def get_duplicates_csv() -> Response:
    """The CSV from the latest completed search (same columns as
    GET /api/photos/duplicates/csv for `similar`/`all`). 404 when no run has
    completed since the API started."""
    result = duplicates.progress.result()
    if result is None:
        raise HTTPException(status_code=404, detail="No duplicate search has completed yet -- run Identify Duplicates.")
    _mode, rows = result
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_ALL)
    writer.writerow(["Duplicate Image", "Original Image", "Match Type", "Distance", "Duplicate Size", "Original Size"])
    for row in rows:
        writer.writerow(
            [row["duplicate"], row["original"], row["match_type"], row["distance"], row["duplicate_size"], row["original_size"]]
        )
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="duplicate_photos.csv"'},
    )
