from typing import Literal

from fastapi import APIRouter, HTTPException, Response

from .. import trips
from ..models import (
    Trip,
    TripCreate,
    TripList,
    TripLocations,
    TripMergeRequest,
    TripPhotoIds,
    TripPhotosRequest,
    TripRefreshStatus,
    TripSplitRequest,
    TripUpdate,
)

router = APIRouter()


@router.get("/trips", response_model=TripList)
def list_trips(status: Literal["active", "dismissed"] = "active") -> dict:
    """Every active trip and day outing, newest first (or the dismissed ones)."""
    return {"trips": trips.list_trips(status)}


@router.post("/trips", response_model=Trip, status_code=201)
def create_trip(body: TripCreate) -> dict:
    """A trip made by hand, from photo ids and/or a date range."""
    return trips.create_trip(body.name, body.photo_ids, body.start_date, body.end_date)


@router.post("/trips/merge", response_model=Trip)
def merge_trips(body: TripMergeRequest) -> dict:
    """Merge into whichever of them started first, which keeps its id."""
    return trips.merge_trips(body.trip_ids)


@router.post("/trips/refresh", response_model=TripRefreshStatus)
def refresh_trips() -> dict:
    return trips.start_refresh()


@router.get("/trips/refresh/status", response_model=TripRefreshStatus)
def get_refresh_status() -> dict:
    return trips.progress.snapshot()


@router.get("/trips/{trip_id}", response_model=Trip)
def get_trip(trip_id: str) -> dict:
    trip = trips.get_trip(trip_id)
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found")
    return trip


@router.get("/trips/{trip_id}/photos", response_model=TripPhotoIds)
def get_trip_photo_ids(trip_id: str) -> dict:
    """Backs the sidebar's "By Trip" filter: ids of every photo in the trip,
    oldest first."""
    photo_ids = trips.list_trip_photo_ids(trip_id)
    if photo_ids is None:
        raise HTTPException(status_code=404, detail="Trip not found")
    return {"photo_ids": photo_ids}


@router.get("/trips/{trip_id}/locations", response_model=TripLocations)
def get_trip_locations(trip_id: str) -> dict:
    """Backs the trip map: stops, the route between them, and the home."""
    locations = trips.get_trip_locations(trip_id)
    if locations is None:
        raise HTTPException(status_code=404, detail="Trip not found")
    return locations


@router.patch("/trips/{trip_id}", response_model=Trip)
def update_trip(trip_id: str, body: TripUpdate) -> dict:
    return trips.update_trip(trip_id, body.name, body.cover_photo_id, body.kind)


@router.post("/trips/{trip_id}/photos/add", response_model=Trip)
def add_trip_photos(trip_id: str, body: TripPhotosRequest) -> dict:
    """Put photos in the trip, whatever their date or GPS, moving them out of
    any other trip."""
    return trips.add_photos(trip_id, body.photo_ids)


@router.post("/trips/{trip_id}/photos/remove", response_model=Trip)
def remove_trip_photos(trip_id: str, body: TripPhotosRequest) -> dict:
    """Take photos out of the trip; detection won't put them back."""
    return trips.remove_photos(trip_id, body.photo_ids)


@router.post("/trips/{trip_id}/split", response_model=TripList)
def split_trip(trip_id: str, body: TripSplitRequest) -> dict:
    """The original first, then the new trip holding photos from `at` on."""
    return {"trips": trips.split_trip(trip_id, body.at)}


@router.post("/trips/{trip_id}/dismiss", status_code=204)
def dismiss_trip(trip_id: str) -> Response:
    trips.dismiss_trip(trip_id)
    return Response(status_code=204)


@router.post("/trips/{trip_id}/restore", response_model=Trip)
def restore_trip(trip_id: str) -> dict:
    return trips.restore_trip(trip_id)
