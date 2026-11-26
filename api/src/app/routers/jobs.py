from fastapi import APIRouter

from .. import enrich, geocode, jobs, pets, recognition, sync, trips
from ..models import JobsStatus

router = APIRouter()


def _status() -> dict:
    # One snapshot of everything, so every open Manage page -- not just the
    # browser that started a job -- sees what is running and how far along.
    return {
        **jobs.snapshot(),
        "sync": sync.progress.snapshot(),
        "faces": recognition.progress.snapshot(),
        "descriptions": enrich.progress.snapshot(),
        "locations": geocode.progress.snapshot(),
        "pets": pets.progress.snapshot(),
        "trips": trips.progress.snapshot(),
    }


@router.get("/jobs", response_model=JobsStatus)
def get_jobs() -> dict:
    return _status()


@router.post("/jobs/cancel", response_model=JobsStatus)
def cancel_job() -> dict:
    # Cooperative: the running job stops at its next checkpoint, and
    # `cancelling` stays true until it has (see jobs.py).
    jobs.request_cancel()
    return _status()
