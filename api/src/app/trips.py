"""Trips: storing, reconciling and serving what `trip_detection.py` finds.

Everything here touches Postgres only -- never the photos backend (see
CLAUDE.md's hard invariant). A trip is a run of dated, GPS-tagged photos
taken away from home; `trip_detection.py` is the pure algorithm, this module
loads its input, merges its output into the stored trips, and runs it as a
background job.

**Reconciliation rule.** Detection may *create* trips and *extend* them with
photos not in any trip yet; it never moves a photo from one trip to another,
merges, splits, or recreates a trip the user dismissed. That is what lets a
user's edits (rename, add/remove photos, merge, split, dismiss -- phase 2)
survive every later run:

- a candidate sharing no photo with a stored trip becomes a new trip, unless
  it overlaps a dismissed trip's dates (a tombstone), in which case it is
  dropped whole;
- a candidate sharing photos with exactly one trip extends it with its free,
  non-excluded photos; one sharing photos with several only places a free
  photo into the trip containing (or within `gap_days` of) its time;
- an `auto` member no longer in any candidate (it became home, lost its GPS,
  was deleted) is released; a `manual` member never is;
- an unedited auto trip left under `min_photos` is deleted; an edited or
  manual one is kept, even empty;
- names/kinds/covers/bounds are recomputed, except the ones a user set.

The whole run is one transaction under a Postgres advisory lock (the edit
endpoints take the same lock), so a cancelled or failed run writes nothing.
It has no per-file I/O, so unlike the other jobs it needs no thread pool.

It runs as the manual "Rebuild trips" job (`start_refresh`), and inline at
the end of a sync and of Refresh Locations (`rebuild_quietly`, while that
job's own claim on the job slot is still held, so the face-matching job the
sync chains into afterwards never finds the slot busy).
"""

import asyncio
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import jobs
from . import trip_detection as td
from .config import (
    get_trip_gap_days,
    get_trip_home_min_days,
    get_trip_home_radius_km,
    get_trip_min_hours,
    get_trip_min_photos,
    get_trip_stop_radius_km,
)
from .database import get_connection

logger = logging.getLogger(__name__)

# Advisory lock shared by the rebuild and every trip-editing endpoint.
LOCK_KEY = 7_360_001


def get_settings() -> td.Settings:
    return td.Settings(
        home_radius_km=get_trip_home_radius_km(),
        gap_days=get_trip_gap_days(),
        min_photos=get_trip_min_photos(),
        min_hours=get_trip_min_hours(),
        home_min_days=get_trip_home_min_days(),
        stop_radius_km=get_trip_stop_radius_km(),
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------------------- progress


def _fresh_state(status: str = "idle") -> dict:
    return {
        "status": status,
        "processed": 0,
        "total": 0,
        "created": 0,
        "updated": 0,
        "removed": 0,
        "homes": 0,
        "error": None,
    }


class _TripProgress:
    """Same shape/locking as geocode.py's _RefreshProgress (see its
    docstring): try_start() flips to "running" synchronously so a caller
    polling right after POST /api/trips/refresh can't read a stale terminal
    status, and every terminal transition releases the job slot in the same
    locked step."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = _fresh_state()

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)

    def try_start(self) -> bool:
        with self._lock:
            if self._state["status"] == "running":
                return False
            self._state = _fresh_state("running")
            return True

    def reset(self) -> None:
        """For tests -- see conftest.py's `client` fixture."""
        with self._lock:
            self._state = _fresh_state()

    def set_total(self, total: int) -> None:
        with self._lock:
            self._state["total"] = total

    def finish(self, summary: dict) -> None:
        with self._lock:
            self._state.update(summary)
            self._state["processed"] = self._state["total"]
            self._state["status"] = "completed"
            jobs.release(jobs.TRIPS)

    def fail(self, error: str) -> None:
        with self._lock:
            self._state["status"] = "error"
            self._state["error"] = error
            jobs.release(jobs.TRIPS)

    def cancel(self) -> None:
        with self._lock:
            self._state["status"] = "cancelled"
            jobs.release(jobs.TRIPS)


progress = _TripProgress()
_thread: Optional[threading.Thread] = None


# ------------------------------------------------------------------ loading

_POINT_COLUMNS = (
    "id, created_at, latitude, longitude, location_country, location_state, location_city, "
    "location_name, (thumbnail IS NOT NULL) AS has_thumbnail"
)


def _point(row: dict) -> Optional[td.Point]:
    ts = td.parse_timestamp(row["created_at"])
    if ts is None:
        return None
    return td.Point(
        id=row["id"],
        ts=ts,
        lat=row["latitude"],
        lon=row["longitude"],
        country=row.get("location_country"),
        state=row.get("location_state"),
        city=row.get("location_city"),
        place=row.get("location_name"),
        has_thumbnail=bool(row.get("has_thumbnail")),
    )


def _eligible_points(conn) -> list:
    """Photos with a date AND GPS -- the only ones detection considers."""
    rows = conn.execute(
        f"SELECT {_POINT_COLUMNS} FROM photos "
        "WHERE created_at IS NOT NULL AND latitude IS NOT NULL AND longitude IS NOT NULL"
    ).fetchall()
    points = [_point(row) for row in rows]
    return [p for p in points if p is not None]


def _row_to_home(row: dict) -> td.Home:
    return td.Home(
        id=row["id"],
        latitude=row["latitude"],
        longitude=row["longitude"],
        radius_km=row["radius_km"],
        start_date=td.parse_day(row["start_date"]),
        end_date=td.parse_day(row["end_date"]),
        country=row["country"],
        state=row["state"],
        city=row["city"],
    )


def load_homes(conn) -> list:
    rows = conn.execute("SELECT * FROM homes ORDER BY start_date NULLS FIRST").fetchall()
    return [_row_to_home(row) for row in rows]


def _resolve_homes(conn, points: list, settings: td.Settings) -> list:
    """Homes a user entered or confirmed win outright (they have taken
    control of the answer); otherwise the unconfirmed auto-detected ones are
    replaced with a fresh detection."""
    pinned = conn.execute("SELECT * FROM homes WHERE source = 'manual' OR confirmed").fetchall()
    if pinned:
        return [_row_to_home(row) for row in pinned]

    detected = td.detect_homes(points, settings)
    conn.execute("DELETE FROM homes WHERE source = 'auto' AND NOT confirmed")
    for home in detected:
        home.id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO homes (id, name, latitude, longitude, radius_km, start_date, end_date, "
            "country, state, city, source, confirmed) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'auto', FALSE)",
            (
                home.id,
                home.city or home.state or home.country or "Home",
                home.latitude,
                home.longitude,
                None,
                home.start_date.isoformat() if home.start_date else None,
                home.end_date.isoformat() if home.end_date else None,
                home.country,
                home.state,
                home.city,
            ),
        )
    return detected


# ---------------------------------------------------------------- rebuild


def _bounds(start: Optional[str], end: Optional[str]) -> tuple:
    return td.parse_timestamp(start), td.parse_timestamp(end)


def _gap_days(ts: datetime, start: Optional[datetime], end: Optional[datetime]) -> float:
    """Days from `ts` to the span [start, end] (0 inside it)."""
    if start is None or end is None:
        return float("inf")
    if ts < start:
        return (start - ts) / timedelta(days=1)
    if ts > end:
        return (ts - end) / timedelta(days=1)
    return 0.0


def _reconcile(conn, candidates: list, settings: td.Settings) -> dict:
    trips = {row["id"]: row for row in conn.execute("SELECT * FROM trips WHERE status = 'active'").fetchall()}
    tombstones = [
        _bounds(row["start_at"], row["end_at"])
        for row in conn.execute("SELECT start_at, end_at FROM trips WHERE status = 'dismissed'").fetchall()
    ]
    membership = {
        row["photo_id"]: (row["trip_id"], row["membership"])
        for row in conn.execute("SELECT photo_id, trip_id, membership FROM trip_photos").fetchall()
    }
    exclusions = {
        (row["trip_id"], row["photo_id"])
        for row in conn.execute("SELECT trip_id, photo_id FROM trip_exclusions").fetchall()
    }

    created: set = set()
    claimed: set = set()
    inserts: list = []  # (photo_id, trip_id)

    for candidate in candidates:
        if any(
            start is not None and end is not None and candidate.start_at <= end and candidate.end_at >= start
            for start, end in tombstones
        ):
            continue
        claimed.update(candidate.member_ids)

        matched = []
        for photo_id in candidate.member_ids:
            owner = membership.get(photo_id)
            if owner and owner[0] in trips and owner[0] not in matched:
                matched.append(owner[0])

        if not matched:
            trip_id = str(uuid.uuid4())
            now = _now()
            conn.execute(
                "INSERT INTO trips (id, name, kind, start_at, end_at, cover_photo_id, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    trip_id,
                    candidate.name,
                    candidate.kind,
                    candidate.start_at.isoformat(),
                    candidate.end_at.isoformat(),
                    candidate.cover_id,
                    now,
                    now,
                ),
            )
            created.add(trip_id)
            trips[trip_id] = {"id": trip_id}
            for photo_id in candidate.member_ids:
                membership[photo_id] = (trip_id, "auto")
                inserts.append((photo_id, trip_id))
            continue

        free = [photo_id for photo_id in candidate.member_ids if photo_id not in membership]
        if len(matched) == 1:
            for photo_id in free:
                if (matched[0], photo_id) not in exclusions:
                    membership[photo_id] = (matched[0], "auto")
                    inserts.append((photo_id, matched[0]))
            continue

        # Several stored trips share this candidate (e.g. after a manual
        # split): never merge them -- put each free photo into the trip it
        # falls in, or within gap_days of, and leave the rest alone.
        point_times = {}
        for photo_id in free:
            row = conn.execute("SELECT created_at FROM photos WHERE id = %s", (photo_id,)).fetchone()
            point_times[photo_id] = td.parse_timestamp(row["created_at"]) if row else None
        for photo_id in free:
            ts = point_times[photo_id]
            if ts is None:
                continue
            best, best_gap = None, None
            for trip_id in matched:
                start, end = _bounds(trips[trip_id].get("start_at"), trips[trip_id].get("end_at"))
                gap = _gap_days(ts, start, end)
                if gap <= settings.gap_days and (best_gap is None or gap < best_gap):
                    best, best_gap = trip_id, gap
            if best is not None and (best, photo_id) not in exclusions:
                membership[photo_id] = (best, "auto")
                inserts.append((photo_id, best))

    if inserts:
        with conn.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO trip_photos (photo_id, trip_id, membership) VALUES (%s, %s, 'auto') "
                "ON CONFLICT (photo_id) DO NOTHING",
                inserts,
            )

    # Release auto members detection no longer supports.
    stale = [
        photo_id
        for photo_id, (trip_id, kind) in membership.items()
        if kind == "auto" and photo_id not in claimed
    ]
    if stale:
        with conn.cursor() as cursor:
            cursor.executemany("DELETE FROM trip_photos WHERE photo_id = %s AND membership = 'auto'", [(p,) for p in stale])

    return _refresh_trip_rows(conn, created, settings)


def _refresh_trip_rows(conn, created: set, settings: td.Settings) -> dict:
    """Recompute every active trip's derived fields from its members, and
    delete the unedited auto ones that fell below the minimum."""
    trips = conn.execute("SELECT * FROM trips WHERE status = 'active'").fetchall()
    members: dict = {}
    rows = conn.execute(
        f"""
        SELECT tp.trip_id, p.id, p.created_at, p.latitude, p.longitude, p.location_country,
               p.location_state, p.location_city, p.location_name, (p.thumbnail IS NOT NULL) AS has_thumbnail
        FROM trip_photos tp JOIN photos p ON p.id = tp.photo_id
        """
    ).fetchall()
    for row in rows:
        members.setdefault(row["trip_id"], []).append(row)

    updated = removed = 0
    for trip in trips:
        trip_id = trip["id"]
        member_rows = members.get(trip_id, [])
        if trip["source"] == "auto" and not trip["edited"] and len(member_rows) < settings.min_photos:
            conn.execute("DELETE FROM trip_photos WHERE trip_id = %s", (trip_id,))
            conn.execute("DELETE FROM trip_exclusions WHERE trip_id = %s", (trip_id,))
            conn.execute("DELETE FROM trips WHERE id = %s", (trip_id,))
            if trip_id not in created:
                removed += 1
            continue

        dated = [p for p in (_point(row) for row in member_rows) if p is not None]
        fields = {}
        if dated:
            ordered = sorted(dated, key=lambda p: (p.ts, p.id))
            fields["start_at"] = ordered[0].ts.isoformat()
            fields["end_at"] = ordered[-1].ts.isoformat()
            kind = trip["kind"] if trip["edited"] else td.classify_kind(dated)
            fields["kind"] = kind
            if trip["name_source"] == "auto":
                fields["name"] = td.trip_name(dated, kind)
        member_ids = {row["id"] for row in member_rows}
        if trip["cover_source"] == "auto" or trip["cover_photo_id"] not in member_ids:
            cover = td.pick_cover(dated) if dated else (next(iter(member_ids)) if member_ids else None)
            fields["cover_photo_id"] = cover
        changed = {key: value for key, value in fields.items() if trip.get(key) != value}
        if changed:
            changed["updated_at"] = _now()
            set_clause = ", ".join(f"{column} = %s" for column in changed)
            conn.execute(f"UPDATE trips SET {set_clause} WHERE id = %s", (*changed.values(), trip_id))
            if trip_id not in created:
                updated += 1

    return {"created": len(created), "updated": updated, "removed": removed}


def rebuild(track: bool = False) -> Optional[dict]:
    """Detect trips from the library and reconcile them into the database.
    Returns `{created, updated, removed, homes, total}`, or None if `track`
    (the manual job) was cancelled before anything was written."""
    settings = get_settings()
    with get_connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
        points = _eligible_points(conn)
        if track:
            progress.set_total(len(points))
        homes = _resolve_homes(conn, points, settings)
        if track and jobs.cancelled():
            return None
        candidates = td.detect(points, homes, settings)
        if track and jobs.cancelled():
            return None
        summary = _reconcile(conn, candidates, settings)
        conn.commit()
    summary.update({"homes": len(homes), "total": len(points)})
    logger.info(
        "Trips rebuilt: %d created, %d updated, %d removed (%d located photo(s), %d home(s))",
        summary["created"],
        summary["updated"],
        summary["removed"],
        len(points),
        len(homes),
    )
    return summary


def rebuild_quietly() -> None:
    """For sync/Refresh Locations: trips are derived data, so a failure here
    is logged and never fails the job it rides along with."""
    try:
        rebuild()
    except Exception:
        logger.exception("Could not rebuild trips")


def start_refresh() -> dict:
    """Kick off a background rebuild in its own thread -- same shape as
    geocode.start_refresh()."""
    global _thread
    jobs.try_claim(jobs.TRIPS)  # raises jobs.JobBusy if any job is running
    progress.try_start()
    logger.info("Starting trips rebuild")
    _thread = jobs.run_in_thread(jobs.TRIPS, _run_refresh)
    return progress.snapshot()


async def _run_refresh() -> None:
    try:
        summary = await asyncio.get_running_loop().run_in_executor(None, rebuild, True)
        if summary is None:
            logger.info("Trips rebuild cancelled")
            progress.cancel()
            return
        progress.finish({k: summary[k] for k in ("created", "updated", "removed", "homes")})
    except Exception as exc:
        logger.exception("Trips rebuild failed")
        progress.fail(str(exc))


# ------------------------------------------------------------------ queries

_TRIP_SELECT = """
    SELECT t.id, t.name, t.kind, t.status, t.source, t.start_at, t.end_at, t.cover_photo_id,
           t.edited, COUNT(tp.photo_id) AS photo_count
    FROM trips t LEFT JOIN trip_photos tp ON tp.trip_id = t.id
"""
_TRIP_GROUP = " GROUP BY t.id"


def list_trips() -> list:
    with get_connection() as conn:
        rows = conn.execute(
            _TRIP_SELECT + " WHERE t.status = 'active'" + _TRIP_GROUP + " ORDER BY t.start_at DESC NULLS LAST, t.id"
        ).fetchall()
        return [dict(row) for row in rows]


def get_trip(trip_id: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(_TRIP_SELECT + " WHERE t.id = %s" + _TRIP_GROUP, (trip_id,)).fetchone()
        return dict(row) if row else None


def list_trip_photo_ids(trip_id: str) -> Optional[list]:
    """Member ids, oldest first (undated last). None if there is no such trip."""
    with get_connection() as conn:
        if not conn.execute("SELECT 1 FROM trips WHERE id = %s", (trip_id,)).fetchone():
            return None
        rows = conn.execute(
            "SELECT p.id FROM trip_photos tp JOIN photos p ON p.id = tp.photo_id WHERE tp.trip_id = %s "
            "ORDER BY p.created_at ASC NULLS LAST, p.filename",
            (trip_id,),
        ).fetchall()
        return [row["id"] for row in rows]


def get_trip_locations(trip_id: str) -> Optional[dict]:
    """What the trip map draws: stops (photos within the stop radius of each
    other), the order they were visited in, the home in force when the trip
    began, and how many members have no GPS (so aren't on the map)."""
    settings = get_settings()
    with get_connection() as conn:
        trip = conn.execute("SELECT id, start_at FROM trips WHERE id = %s", (trip_id,)).fetchone()
        if not trip:
            return None
        rows = conn.execute(
            """
            SELECT p.id, p.created_at, p.latitude, p.longitude, p.location_country, p.location_state,
                   p.location_city, p.location_name, (p.thumbnail IS NOT NULL) AS has_thumbnail
            FROM trip_photos tp JOIN photos p ON p.id = tp.photo_id WHERE tp.trip_id = %s
            """,
            (trip_id,),
        ).fetchall()
        homes = load_homes(conn)

    located, unlocated = [], 0
    for row in rows:
        point = _point(row)
        if row["latitude"] is None or row["longitude"] is None:
            unlocated += 1
        elif point is not None:
            located.append(point)
        else:
            unlocated += 1  # GPS but no date: still not orderable on a route

    stops, route = td.build_stops(located, settings.stop_radius_km)
    start = td.parse_timestamp(trip["start_at"])
    home = td.home_for(homes, start.date()) if start and homes else None
    return {
        "stops": [
            {
                "latitude": stop.latitude,
                "longitude": stop.longitude,
                "count": stop.count,
                "first_at": stop.first_at.isoformat(),
                "last_at": stop.last_at.isoformat(),
                "place": stop.place,
                "cover_photo_id": stop.cover_id,
                "photo_ids": stop.photo_ids,
            }
            for stop in stops
        ],
        "route": route,
        "home": (
            {
                "latitude": home.latitude,
                "longitude": home.longitude,
                "name": home.city or home.state or home.country or "Home",
            }
            if home
            else None
        ),
        "unlocated_count": unlocated,
    }
