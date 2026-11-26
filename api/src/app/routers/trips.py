from fastapi import APIRouter, HTTPException

from .. import trips
from ..models import Trip, TripList, TripLocations, TripPhotoIds, TripRefreshStatus

router = APIRouter()


@router.get("/trips", response_model=TripList)
def list_trips() -> dict:
    """Every active trip and day outing, newest first."""
    return {"trips": trips.list_trips()}


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
