"""Best-effort reverse geocoding of a photo's GPS coordinates into a short,
human-readable place description (e.g. "Boone, North Carolina",
"Grandfather Mountain State Park, Blowing Rock, North Carolina", or
"Target, 123 North Point Pkwy, Alpharetta, Georgia"), stored as
`photos.location_name` alongside the raw latitude/longitude. Only ever
called from sync.py's `_sync_one_file`, when a photo's GPS coordinates are
(re)extracted from EXIF -- i.e. only on insert/update, same as latitude/
longitude themselves (see sync.py's module docstring for why there's no
separate backfill pass for photos already indexed before this column
existed).

Uses OpenStreetMap's Nominatim reverse-geocoding API -- free, no API key,
consistent with PhotoMap.jsx's own use of OpenStreetMap map tiles elsewhere
in this app. `LUMINARY_GEOCODE_BASE_URL` (config.get_geocode_base_url) can
point this at a self-hosted Nominatim instance instead, e.g. to avoid
depending on/rate-limiting against the public instance for a large
library. Nominatim's usage policy
(https://operations.osmfoundation.org/policies/nominatim/) requires a
descriptive User-Agent identifying the application
(`LUMINARY_GEOCODE_USER_AGENT`, config.get_geocode_user_agent) and caps the
public instance at one request/second -- `_rate_limit()` below enforces
that globally across sync's concurrent worker threads (see sync.py's
MAX_CONCURRENCY), so this becomes the long pole for a sync of a GPS-heavy
library against the public instance; point `LUMINARY_GEOCODE_BASE_URL` at a
self-hosted instance to remove that cap.

Uses only the standard library (`urllib`) rather than adding a new HTTP
client dependency for what's a small, optional, best-effort feature --
every error (network failure, timeout, non-200, malformed JSON) is the
caller's (sync.py's) responsibility to catch; this module only raises.

start_refresh() (routers/locations.py's "Refresh Locations" button,
POST /api/locations/refresh) is the bulk equivalent of sync.py's
per-photo call above: it re-geocodes every photo that already has GPS
coordinates, not just ones just (re)extracted during a sync -- e.g.
after changing LUMINARY_GEOCODE_ZOOM or _PLACE_KEYS (such as the
shop/street-address additions above) and wanting already-indexed photos
to pick up the new result, since there's otherwise no backfill path for
this field (same as latitude/longitude themselves -- see sync.py's
module docstring). Same background-job shape as sync.py's start_sync()/
recognition.py's/enrich.py's own start_refresh() (own thread + event
loop, atomic try_start(), files fanned out across a thread pool) --
copy that pattern, don't reinvent it. _rate_limit() above still caps
this at one Nominatim request/second against the public instance, same
as during a sync, so this is the long pole for a GPS-heavy library here
too.
"""

import asyncio
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from .config import get_geocode_base_url, get_geocode_user_agent, get_geocode_zoom
from .database import get_connection

logger = logging.getLogger(__name__)

# Mirrors enrich.py's MAX_CONCURRENCY -- _rate_limit() below still caps
# actual Nominatim requests to one/second regardless, so this only lets
# the DB read/write around each geocode call overlap across photos rather
# than serializing unnecessarily.
MAX_CONCURRENCY = 8
_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENCY)

# Nominatim's public-instance usage policy caps requests at one per second,
# enforced here (not per-thread) since sync.py fans work out across several
# concurrent worker threads that would otherwise all hit this at once.
_MIN_REQUEST_INTERVAL_SECONDS = 1.0
_rate_limit_lock = threading.Lock()
_last_request_at = 0.0

# The single most notable "named place" tag to lead with, if the
# coordinates fall within one -- most to least specific. Nominatim's
# `address` object only ever populates whichever of these actually
# matched, never more than one at a time, so this just picks the best one
# present. `national_park`/`protected_area` (confirmed empirically against
# real Nominatim responses, e.g. for Grandfather Mountain State Park)
# cover the "<name> State/National Park" case the module docstring's
# example is built on -- but reverse geocoding a bare point only resolves
# to the park's *own* polygon when no smaller named feature (a trail, a
# building, a road) also overlaps that exact point, which real GPS-tagged
# photos often do -- don't expect this to reliably outrank a trail/road
# name for a point that happens to sit on one, that's a genuine Nominatim
# limitation, not a bug here.
#
# `shop`/`mall`/`office` cover named businesses (e.g. a Target or other
# big-box retailer mapped in OSM as a point/building with its own name) --
# Nominatim's address breakdown puts the business's *name* under the key
# matching whatever OSM tag the matched feature carries (`shop=*` for most
# retail, `shop=mall` for a shopping center, `office=*` for a workplace),
# the same way it puts a park's name under `national_park` above. Like the
# park case, this only resolves to the business if GPS precision plus
# LUMINARY_GEOCODE_ZOOM (needs to be high -- 18, building-level -- for
# this) land the point on that business's own mapped feature rather than
# just its containing street/neighborhood; whether it's mapped at all
# depends entirely on OSM's crowd-sourced coverage for that location.
_PLACE_KEYS = (
    "national_park",
    "protected_area",
    "attraction",
    "tourism",
    "leisure",
    "natural",
    "historic",
    "shop",
    "mall",
    "office",
    "amenity",
)
# Locality, most to least specific.
_LOCALITY_KEYS = ("city", "town", "village", "hamlet", "suburb")


def _rate_limit() -> None:
    global _last_request_at
    with _rate_limit_lock:
        wait = _last_request_at + _MIN_REQUEST_INTERVAL_SECONDS - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


def _describe(address: dict, display_name: Optional[str]) -> Optional[str]:
    """Builds a short "<notable place>, <street>, <locality>, <state>"-style
    string from Nominatim's structured `address` object (preferred -- it's
    what produces "Grandfather Mountain State Park, Blowing Rock, North
    Carolina"-style results), falling back to Nominatim's own full
    `display_name` (which includes a postcode and country, more cluttered
    but always present when the lookup matched *something*) only if none
    of the curated fields below are present.

    The street slot (`house_number` + `road`) sits right after the notable
    place, before locality -- e.g. "Target, 123 Main St, Alpharetta,
    Georgia" -- since it's the next level down in specificity from a named
    business/place, same as `house_number`+`road` is one level down from
    `city` in Nominatim's own address hierarchy. `house_number` only gets
    used when `road` is also present (a bare number with no street name
    isn't meaningful on its own); `road` alone (no `house_number`) is
    still included, since Nominatim often has the street but not a
    specific number for a GPS-derived point.

    `county` is deliberately only a fallback for the locality slot (used
    when none of _LOCALITY_KEYS are present), not folded into the
    state/country slot below -- confirmed against a real response (a rural
    road with `county`+`state` but no `city`/`town`) that dropping it
    there produced a bare "North Carolina" with no county context at all,
    which is worse than "Watauga County, North Carolina"."""
    parts = []
    for key in _PLACE_KEYS:
        if address.get(key):
            parts.append(address[key])
            break
    if address.get("road"):
        street = f"{address['house_number']} {address['road']}" if address.get("house_number") else address["road"]
        parts.append(street)
    for key in _LOCALITY_KEYS:
        if address.get(key):
            parts.append(address[key])
            break
    else:
        if address.get("county"):
            parts.append(address["county"])
    if address.get("state"):
        parts.append(address["state"])
    elif not parts and address.get("country"):
        parts.append(address["country"])
    return ", ".join(parts) if parts else (display_name or None)


def reverse_geocode(latitude: float, longitude: float, timeout: float = 10.0) -> Optional[str]:
    """Reverse-geocodes one (latitude, longitude) pair into a short place
    description, or None if Nominatim returned nothing usable for it (open
    ocean, an area with no OSM address data, etc). Synchronous and
    rate-limited (see _rate_limit) -- callers (sync.py's _sync_one_file)
    always run this on a worker thread, never the API's own event loop.
    Raises on any transport/parsing failure; the caller (sync.py's
    _describe_location) is responsible for treating that as best-effort,
    same as every other derived field computed during sync."""
    _rate_limit()

    params = urllib.parse.urlencode(
        {
            "format": "jsonv2",
            "lat": latitude,
            "lon": longitude,
            "zoom": get_geocode_zoom(),
            "addressdetails": 1,
        }
    )
    url = f"{get_geocode_base_url()}?{params}"
    request = urllib.request.Request(url, headers={"User-Agent": get_geocode_user_agent()})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read())

    if "error" in payload:
        # Nominatim's own "nothing here" response -- a 200 with an `error`
        # field (e.g. {"error": "Unable to geocode"}), not an HTTP error.
        logger.debug("Nominatim had no address for (%s, %s): %s", latitude, longitude, payload["error"])
        return None

    return _describe(payload.get("address") or {}, payload.get("display_name"))


class _RefreshProgress:
    """Same shape/locking as sync.py's _SyncProgress/recognition.py's and
    enrich.py's own _RefreshProgress -- see their docstrings for the
    reasoning (atomic try_start() so a caller polling status right after
    POST /api/locations/refresh can't observe a stale terminal status
    from a previous run)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = {"status": "idle", "processed": 0, "total": 0, "updated": 0, "error": None}

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)

    def try_start(self) -> bool:
        with self._lock:
            if self._state["status"] == "running":
                return False
            self._state = {"status": "running", "processed": 0, "total": 0, "updated": 0, "error": None}
            return True

    def set_total(self, total: int) -> None:
        with self._lock:
            self._state["total"] = total

    def reset(self) -> None:
        """For tests -- see conftest.py's `client` fixture, which resets
        this module-level singleton between tests the same way it already
        does for sync.progress/recognition.progress/enrich.progress."""
        with self._lock:
            self._state = {"status": "idle", "processed": 0, "total": 0, "updated": 0, "error": None}

    def record(self, updated: bool) -> None:
        with self._lock:
            self._state["processed"] += 1
            if updated:
                self._state["updated"] += 1

    def finish(self) -> None:
        with self._lock:
            self._state["status"] = "completed"

    def fail(self, error: str) -> None:
        with self._lock:
            self._state["status"] = "error"
            self._state["error"] = error


progress = _RefreshProgress()
_thread: Optional[threading.Thread] = None


def _geocoded_photo_ids() -> list[str]:
    """Every photo with GPS coordinates to re-geocode. Deliberately not
    gated on sync.py's content_changed the way the original geocode on
    insert/update is -- the whole point of this bulk job is to pick up a
    _describe()/LUMINARY_GEOCODE_ZOOM change (e.g. the shop/street-address
    additions above) for photos that were already indexed before it
    existed, with no file change of their own to trigger a re-sync."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id FROM photos WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
        ).fetchall()
        return [row["id"] for row in rows]


def _refresh_one(photo_id: str) -> bool:
    """Best-effort: re-geocode one photo's existing latitude/longitude and
    overwrite location_name with the result -- including a None result,
    the same way sync.py's _describe_location assigns its own result
    unconditionally, so this job reproduces exactly what a fresh sync
    would have written for the same coordinates. Only an exception
    (network/timeout/malformed response) leaves location_name untouched
    and is reported as not updated -- same "never raises" shape as
    embeddings.embed_description/enrich.enrich_description."""
    with get_connection() as conn:
        photo = conn.execute("SELECT latitude, longitude FROM photos WHERE id = %s", (photo_id,)).fetchone()
    if not photo:
        return False

    try:
        location_name = reverse_geocode(photo["latitude"], photo["longitude"])
    except Exception:
        logger.warning("Could not re-geocode photo %s", photo_id, exc_info=True)
        return False

    with get_connection() as conn:
        conn.execute("UPDATE photos SET location_name = %s WHERE id = %s", (location_name, photo_id))
        conn.commit()
    return True


def start_refresh() -> dict:
    """Kick off a background location refresh (every photo with GPS
    coordinates) in its own thread/event loop if one isn't already
    running -- same rationale/shape as sync.start_sync()/recognition.
    start_refresh()/enrich.start_refresh() (decoupled from the API's own
    request-handling loop, try_start() flips to "running" synchronously
    before this returns). Returns the current status snapshot either
    way."""
    global _thread
    if progress.try_start():
        logger.info("Starting location refresh")
        _thread = threading.Thread(target=lambda: asyncio.run(_run_refresh()), daemon=True)
        _thread.start()
    else:
        logger.info("Location refresh already in progress; ignoring duplicate start request")
    return progress.snapshot()


async def _run_refresh() -> None:
    loop = asyncio.get_running_loop()
    try:
        photo_ids = await loop.run_in_executor(_executor, _geocoded_photo_ids)
        progress.set_total(len(photo_ids))

        semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

        async def handle(photo_id: str) -> None:
            async with semaphore:
                updated = await loop.run_in_executor(_executor, _refresh_one, photo_id)
            progress.record(updated)

        await asyncio.gather(*(handle(photo_id) for photo_id in photo_ids))

        progress.finish()
        logger.info(
            "Location refresh completed: %d updated of %d photo(s)",
            progress.snapshot()["updated"],
            len(photo_ids),
        )
    except Exception as exc:
        logger.exception("Location refresh failed")
        progress.fail(str(exc))
