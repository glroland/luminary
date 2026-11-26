"""Trip detection -- pure functions, no database, no clock, no I/O.

A *trip* is a run of photos, in time order, taken away from the user's
home. Only photos with BOTH a date (`created_at`) and GPS coordinates take
part: without either there is nothing to place on a timeline or a map, so
such a photo is never organized into a trip automatically (a user can still
add one by hand -- see trips.py). A photo's place names (country/state/
city) come from reverse-geocoding its GPS, so "has a location" and "has GPS"
are the same thing here.

Pipeline (see `detect`):

1. **Homes** (`detect_homes`) -- the place where the most distinct days are
   spent in each calendar year; consecutive years at the same place merge
   into one date-ranged home. Unless the caller supplies homes already
   (trips.py does, when a user has confirmed or entered some).
2. **Away or home** -- each photo is compared with the home in force on its
   date (`home_for`): farther than the home's radius means away.
3. **Segments** -- away photos in time order, split wherever more than
   `gap_days` pass with no away photo, or a whole calendar day was spent at
   home (a day with home photos and no away photos) -- the user came back.
4. **Filter** -- fewer than `min_photos` photos or a span under `min_hours`
   is dropped. Away photos on a single calendar date make an *outing*; two
   or more dates make a *trip*.

Time zones are deliberately not determined. EXIF `DateTimeOriginal` is the
wall-clock time where the photo was taken, with no zone, which is exactly
what "what day was it where I was" needs. Cross-zone elapsed times can be
off by up to ~14h, but every threshold here is in days except `min_hours`,
which compares photos from one place. The tzinfo sync.py attaches to an EXIF
time is not a real zone, so `parse_timestamp` drops it.
"""

import math
import statistics
from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

_EARTH_RADIUS_KM = 6371.0088
_US_NAMES = {"united states", "united states of america", "usa", "us"}
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@dataclass(frozen=True)
class Settings:
    home_radius_km: float = 50.0
    gap_days: int = 2
    min_photos: int = 10
    min_hours: float = 4.0
    home_min_days: int = 30
    stop_radius_km: float = 2.0


@dataclass(frozen=True)
class Point:
    """One photo. Detection only ever sees photos with a date and GPS; a
    trip member a user added by hand may have no GPS (lat/lon None) and is
    only ever used for dates and names, never for geometry."""

    id: str
    ts: datetime  # naive wall-clock time
    lat: Optional[float]
    lon: Optional[float]
    country: Optional[str] = None
    state: Optional[str] = None
    city: Optional[str] = None
    place: Optional[str] = None  # photos.location_name
    has_thumbnail: bool = True

    @property
    def day(self) -> date:
        return self.ts.date()


@dataclass
class Home:
    latitude: float
    longitude: float
    radius_km: Optional[float] = None  # None -> Settings.home_radius_km
    start_date: Optional[date] = None  # None -> unbounded
    end_date: Optional[date] = None
    id: Optional[str] = None
    country: Optional[str] = None
    state: Optional[str] = None
    city: Optional[str] = None


@dataclass
class Candidate:
    member_ids: list
    start_at: datetime
    end_at: datetime
    kind: str  # "trip" | "outing"
    name: str
    cover_id: Optional[str]


@dataclass
class Stop:
    latitude: float
    longitude: float
    count: int
    first_at: datetime
    last_at: datetime
    photo_ids: list
    place: Optional[str]
    cover_id: Optional[str]


def parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    """`photos.created_at` as a naive wall-clock datetime, or None if it is
    missing or unparseable."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    return parsed.replace(tzinfo=None)


def parse_day(value: Optional[str]) -> Optional[date]:
    parsed = parse_timestamp(value)
    return parsed.date() if parsed else None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


# ---------------------------------------------------------------- homes


def detect_homes(points: list, settings: Settings) -> list:
    """Where the user lives, per calendar year, merged across years.

    Per year, photos are bucketed into grid cells about one home-radius
    wide and a cell scores the distinct days seen in it and its eight
    neighbours. The best cell's home is the median of its days' positions
    (one position per day, so a burst of 300 photos on one day counts once,
    and the median shrugs off the away days that fall in the same cells).
    It must still have `home_min_days` distinct days within the radius to
    count. Second homes are never detected -- a long stay at a cabin would
    become a false home; users add those by hand."""
    by_year: dict = {}
    for point in points:
        by_year.setdefault(point.day.year, []).append(point)

    yearly = []  # (year, lat, lon, country, state, city)
    cell = max(settings.home_radius_km, 1.0) / 111.0
    for year in sorted(by_year):
        day_positions: dict = {}
        for point in by_year[year]:
            day_positions.setdefault(point.day, []).append(point)
        days = {
            day: (
                statistics.median(p.lat for p in pts),
                statistics.median(p.lon for p in pts),
            )
            for day, pts in day_positions.items()
        }
        cells: dict = {}
        for day, (lat, lon) in days.items():
            cells.setdefault((round(lat / cell), round(lon / cell)), set()).add(day)

        def score(key):
            found = set()
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    found |= cells.get((key[0] + dx, key[1] + dy), set())
            return found

        best_days: set = set()
        for key in cells:
            neighbourhood = score(key)
            if len(neighbourhood) > len(best_days):
                best_days = neighbourhood
        if not best_days:
            continue
        lat = statistics.median(days[d][0] for d in best_days)
        lon = statistics.median(days[d][1] for d in best_days)
        near = {d for d, (dl, dn) in days.items() if haversine_km(lat, lon, dl, dn) <= settings.home_radius_km}
        if len(near) < settings.home_min_days:
            continue
        yearly.append((year, lat, lon, by_year[year]))

    homes: list = []
    spans: list = []  # [first_year, last_year] per home
    for year, lat, lon, year_points in yearly:
        if homes and haversine_km(homes[-1].latitude, homes[-1].longitude, lat, lon) <= settings.home_radius_km:
            spans[-1][1] = year
            continue
        homes.append(Home(latitude=lat, longitude=lon))
        spans.append([year, year])

    for i, home in enumerate(homes):
        if i > 0:
            home.start_date = date(spans[i][0], 1, 1)
        if i < len(homes) - 1:
            home.end_date = date(spans[i][1], 12, 31)
        _describe_home(home, points, settings)
    return homes


def _describe_home(home: Home, points: list, settings: Settings) -> None:
    """Most common country/state/city among geocoded photos at the home --
    for display only, never used to classify anything."""
    radius = home.radius_km or settings.home_radius_km
    tally: Counter = Counter()
    for point in points:
        if point.country and haversine_km(home.latitude, home.longitude, point.lat, point.lon) <= radius:
            tally[(point.country, point.state, point.city)] += 1
    if tally:
        home.country, home.state, home.city = tally.most_common(1)[0][0]


def home_for(homes: list, day: date) -> Optional[Home]:
    """The home in force on `day`: the one whose range contains it, else the
    one whose range is nearest."""
    best: Optional[Home] = None
    best_gap: Optional[int] = None
    for home in homes:
        if home.start_date and day < home.start_date:
            gap = (home.start_date - day).days
        elif home.end_date and day > home.end_date:
            gap = (day - home.end_date).days
        else:
            return home
        if best_gap is None or gap < best_gap:
            best, best_gap = home, gap
    return best


# ------------------------------------------------------------- segments


def detect(points: list, homes: list, settings: Settings) -> list:
    """Candidates, oldest first. `homes` is whatever should count as home;
    with none, nothing can be called "away" and the result is empty."""
    if not homes or not points:
        return []
    ordered = sorted(points, key=lambda p: (p.ts, p.id))

    away: list = []
    away_days: set = set()
    home_days: set = set()
    for point in ordered:
        home = home_for(homes, point.day)
        radius = home.radius_km or settings.home_radius_km
        if haversine_km(home.latitude, home.longitude, point.lat, point.lon) > radius:
            away.append(point)
            away_days.add(point.day)
        else:
            home_days.add(point.day)
    pure_home_days = sorted(home_days - away_days)

    segments: list = []
    for point in away:
        if segments:
            previous = segments[-1][-1]
            gap = (point.day - previous.day).days
            returned = False
            if gap > 1:
                # Any whole day strictly between the two at home?
                i = bisect_right(pure_home_days, previous.day)
                returned = i < len(pure_home_days) and pure_home_days[i] < point.day
            if gap <= settings.gap_days and not returned:
                segments[-1].append(point)
                continue
        segments.append([point])

    candidates = []
    for members in segments:
        first, last = members[0], members[-1]
        if len(members) < settings.min_photos:
            continue
        if (last.ts - first.ts) < timedelta(hours=settings.min_hours):
            continue
        kind = "trip" if len({p.day for p in members}) >= 2 else "outing"
        candidates.append(
            Candidate(
                member_ids=[p.id for p in members],
                start_at=first.ts,
                end_at=last.ts,
                kind=kind,
                name=trip_name(members, kind),
                cover_id=pick_cover(members),
            )
        )
    return candidates


def classify_kind(points: list) -> str:
    return "trip" if len({p.day for p in points}) >= 2 else "outing"


# --------------------------------------------------------- naming/cover


def pick_cover(points: list) -> Optional[str]:
    """The middle photo by time among those with a thumbnail (any photo if
    none has one)."""
    ordered = sorted(points, key=lambda p: (p.ts, p.id))
    with_thumbnail = [p for p in ordered if p.has_thumbnail]
    pool = with_thumbnail or ordered
    return pool[len(pool) // 2].id if pool else None


def _join(names: list) -> str:
    if len(names) <= 3:
        if len(names) <= 1:
            return "".join(names)
        return ", ".join(names[:-1]) + " & " + names[-1]
    return f"{names[0]}, {names[1]} & more"


def _ranked(values) -> list:
    return [name for name, _ in Counter(v for v in values if v).most_common()]


def _is_us(country: Optional[str]) -> bool:
    return bool(country) and country.strip().lower() in _US_NAMES


def place_summary(points: list) -> Optional[str]:
    """The place a trip leads with, e.g. "Italy", "Orlando, Florida",
    "Boone & Blowing Rock, North Carolina", "Georgia & Florida"."""
    located = [p for p in points if p.country or p.state or p.city]
    if not located:
        return None
    countries = _ranked(p.country for p in located)
    if len(countries) <= 1 and (not countries or _is_us(countries[0])):
        states = _ranked(p.state for p in located)
        if len(states) == 1:
            cities = _ranked(p.city for p in located if p.state == states[0])
            if cities:
                return f"{_join(cities)}, {states[0]}"
            return states[0]
        if states:
            return _join(states)
        cities = _ranked(p.city for p in located)
        return _join(cities) if cities else None
    if len(countries) == 1:
        return countries[0]
    return _join(countries)


def format_dates(start: datetime, end: datetime, kind: str) -> str:
    if kind == "outing" or start.date() == end.date():
        return f"{_MONTHS[start.month - 1]} {start.day}, {start.year}"
    if (start.year, start.month) == (end.year, end.month):
        return f"{_MONTHS[start.month - 1]} {start.year}"
    if start.year == end.year:
        return f"{_MONTHS[start.month - 1]}–{_MONTHS[end.month - 1]} {start.year}"
    return f"{_MONTHS[start.month - 1]} {start.year}–{_MONTHS[end.month - 1]} {end.year}"


def trip_name(points: list, kind: str) -> str:
    ordered = sorted(points, key=lambda p: p.ts)
    place = place_summary(points) or ("Trip" if kind == "trip" else "Outing")
    return f"{place}, {format_dates(ordered[0].ts, ordered[-1].ts, kind)}"


# ----------------------------------------------------------------- stops


def build_stops(points: list, radius_km: float) -> tuple:
    """Group a trip's located photos into map *stops* and the order they
    were visited in.

    Photos within `radius_km` of an existing stop join it (a revisited
    place reuses its stop), otherwise they start a new one. Returns
    `(stops, route)`: stops sorted by first visit, and `route` the list of
    stop indexes in visit order with consecutive repeats collapsed."""
    ordered = sorted(points, key=lambda p: (p.ts, p.id))
    stops: list = []  # each: {"lat", "lon", "points"}
    visits: list = []
    for point in ordered:
        nearest, nearest_distance = None, None
        for index, stop in enumerate(stops):
            distance = haversine_km(stop["lat"], stop["lon"], point.lat, point.lon)
            if distance <= radius_km and (nearest_distance is None or distance < nearest_distance):
                nearest, nearest_distance = index, distance
        if nearest is None:
            stops.append({"lat": point.lat, "lon": point.lon, "points": [point]})
            nearest = len(stops) - 1
        else:
            stop = stops[nearest]
            stop["points"].append(point)
            n = len(stop["points"])
            stop["lat"] += (point.lat - stop["lat"]) / n
            stop["lon"] += (point.lon - stop["lon"]) / n
        if not visits or visits[-1] != nearest:
            visits.append(nearest)

    result = []
    for stop in stops:
        pts = stop["points"]
        names = _ranked(p.place for p in pts)
        result.append(
            Stop(
                latitude=stop["lat"],
                longitude=stop["lon"],
                count=len(pts),
                first_at=pts[0].ts,
                last_at=pts[-1].ts,
                photo_ids=[p.id for p in pts],
                place=names[0] if names else None,
                cover_id=pick_cover(pts),
            )
        )
    return result, visits
