"""trip_detection.py is pure, so these need no database or fixtures."""

from datetime import date, datetime, timedelta

from app import trip_detection as td

ATLANTA = (33.749, -84.388)
BOONE = (36.2168, -81.6746)
BLOWING_ROCK = (36.1335, -81.6768)
ORLANDO = (28.5383, -81.3792)
ROME = (41.9028, 12.4964)

SETTINGS = td.Settings(home_radius_km=50, gap_days=2, min_photos=3, min_hours=1, home_min_days=5, stop_radius_km=2)


def pt(photo_id, when, where, **kwargs):
    return td.Point(id=photo_id, ts=datetime.fromisoformat(when), lat=where[0], lon=where[1], **kwargs)


def run(start, where, days=1, per_day=4, prefix="p", **kwargs):
    """`per_day` photos a day for `days` days from `start`, 2 hours apart."""
    first = datetime.fromisoformat(start)
    return [
        td.Point(
            id=f"{prefix}-{day}-{i}",
            ts=first + timedelta(days=day, hours=2 * i),
            lat=where[0],
            lon=where[1],
            **kwargs,
        )
        for day in range(days)
        for i in range(per_day)
    ]


HOME = td.Home(latitude=ATLANTA[0], longitude=ATLANTA[1])


def test_parse_timestamp_drops_the_fake_utc_zone():
    parsed = td.parse_timestamp("2019-05-01T21:30:00+00:00")
    assert parsed == datetime(2019, 5, 1, 21, 30)
    assert parsed.tzinfo is None
    assert td.parse_timestamp(None) is None
    assert td.parse_timestamp("not a date") is None


def test_detect_homes_picks_the_place_with_the_most_days():
    points = run("2019-01-01T09:00:00", ATLANTA, days=20) + run("2019-03-01T09:00:00", BOONE, days=3)
    homes = td.detect_homes(points, SETTINGS)
    assert len(homes) == 1
    assert td.haversine_km(homes[0].latitude, homes[0].longitude, *ATLANTA) < 5
    assert homes[0].start_date is None and homes[0].end_date is None


def test_detect_homes_needs_enough_days():
    assert td.detect_homes(run("2019-01-01T09:00:00", ATLANTA, days=3), SETTINGS) == []


def test_a_burst_of_photos_on_one_day_counts_once():
    burst = [pt(f"b{i}", f"2019-01-01T{i // 20 + 6:02d}:{(i % 20) * 3:02d}:00", ATLANTA) for i in range(200)]
    assert td.detect_homes(burst, SETTINGS) == []


def test_a_move_gives_two_dated_homes():
    points = run("2018-01-01T09:00:00", ATLANTA, days=10) + run("2019-01-01T09:00:00", ORLANDO, days=10)
    homes = td.detect_homes(points, SETTINGS)
    assert len(homes) == 2
    assert homes[0].start_date is None and homes[0].end_date == date(2018, 12, 31)
    assert homes[1].start_date == date(2019, 1, 1) and homes[1].end_date is None
    assert td.home_for(homes, date(2018, 6, 1)) is homes[0]
    assert td.home_for(homes, date(2020, 6, 1)) is homes[1]


def test_home_for_falls_back_to_the_nearest_range():
    early = td.Home(1, 1, end_date=date(2016, 12, 31))
    late = td.Home(2, 2, start_date=date(2019, 1, 1))
    assert td.home_for([early, late], date(2017, 3, 1)) is early
    assert td.home_for([early, late], date(2018, 11, 1)) is late


def test_a_multi_day_trip():
    points = run("2019-05-01T09:00:00", ATLANTA, days=1) + run("2019-05-10T09:00:00", BOONE, days=3, per_day=5)
    candidates = td.detect(points, [HOME], SETTINGS)
    assert len(candidates) == 1
    trip = candidates[0]
    assert trip.kind == "trip"
    assert len(trip.member_ids) == 15
    assert trip.start_at == datetime(2019, 5, 10, 9) and trip.end_at.date() == date(2019, 5, 12)


def test_a_one_day_outing():
    candidates = td.detect(run("2019-05-10T09:00:00", BOONE, days=1, per_day=6), [HOME], SETTINGS)
    assert [c.kind for c in candidates] == ["outing"]


def test_too_few_photos_or_too_short_is_dropped():
    assert td.detect(run("2019-05-10T09:00:00", BOONE, days=1, per_day=2), [HOME], SETTINGS) == []
    quick = [pt(f"q{i}", f"2019-05-10T09:{i:02d}:00", BOONE) for i in range(5)]
    assert td.detect(quick, [HOME], SETTINGS) == []


def test_a_long_gap_splits_trips():
    points = run("2019-05-01T09:00:00", BOONE, days=2, prefix="a") + run("2019-05-08T09:00:00", BOONE, days=2, prefix="b")
    assert len(td.detect(points, [HOME], SETTINGS)) == 2


def test_a_short_quiet_stretch_does_not_split_a_trip():
    # Two days with no photos (a long drive or flight) between away photos.
    points = run("2019-05-01T09:00:00", BOONE, days=1, prefix="a") + run("2019-05-03T09:00:00", ORLANDO, days=1, prefix="b")
    candidates = td.detect(points, [HOME], SETTINGS)
    assert len(candidates) == 1 and len(candidates[0].member_ids) == 8


def test_a_whole_day_at_home_ends_the_trip():
    points = (
        run("2019-05-01T09:00:00", BOONE, days=1, prefix="a")
        + run("2019-05-02T09:00:00", ATLANTA, days=1, prefix="home")
        + run("2019-05-03T09:00:00", BOONE, days=1, prefix="b")
    )
    assert len(td.detect(points, [HOME], SETTINGS)) == 2


def test_leaving_home_the_same_day_does_not_split_a_trip():
    points = [pt("h", "2019-05-01T07:00:00", ATLANTA)] + run("2019-05-01T10:00:00", BOONE, days=2, prefix="a")
    candidates = td.detect(points, [HOME], SETTINGS)
    assert len(candidates) == 1
    assert "h" not in candidates[0].member_ids  # home photos are never members


def test_no_home_means_no_trips():
    assert td.detect(run("2019-05-10T09:00:00", BOONE, days=2), [], SETTINGS) == []


def test_place_summary():
    us = lambda city, state: dict(country="United States", state=state, city=city)
    assert td.place_summary([pt("a", "2019-05-01T10:00:00", BOONE, **us("Boone", "North Carolina"))]) == "Boone, North Carolina"
    both = [
        pt("a", "2019-05-01T10:00:00", BOONE, **us("Boone", "North Carolina")),
        pt("b", "2019-05-01T11:00:00", BOONE, **us("Boone", "North Carolina")),
        pt("c", "2019-05-01T12:00:00", BLOWING_ROCK, **us("Blowing Rock", "North Carolina")),
    ]
    assert td.place_summary(both) == "Boone & Blowing Rock, North Carolina"
    states = [
        pt("a", "2019-05-01T10:00:00", BOONE, **us("Boone", "North Carolina")),
        pt("b", "2019-05-01T11:00:00", ORLANDO, **us("Orlando", "Florida")),
    ]
    assert td.place_summary(states) in ("North Carolina & Florida", "Florida & North Carolina")
    rome = [pt("a", "2019-05-01T10:00:00", ROME, country="Italy", state="Lazio", city="Rome")]
    assert td.place_summary(rome) == "Italy"
    assert td.place_summary([pt("a", "2019-05-01T10:00:00", ROME)]) is None


def test_names():
    trip = [pt("a", "2019-05-10T09:00:00", ROME, country="Italy"), pt("b", "2019-05-17T09:00:00", ROME, country="Italy")]
    assert td.trip_name(trip, "trip") == "Italy, May 2019"
    assert td.trip_name([trip[0]], "outing") == "Italy, May 10, 2019"
    assert td.trip_name([pt("a", "2019-05-10T09:00:00", ROME)], "outing") == "Outing, May 10, 2019"
    assert td.format_dates(datetime(2020, 12, 28), datetime(2021, 1, 3), "trip") == "Dec 2020–Jan 2021"
    assert td.format_dates(datetime(2019, 7, 28), datetime(2019, 8, 3), "trip") == "Jul–Aug 2019"


def test_cover_is_the_middle_photo_with_a_thumbnail():
    points = [pt(f"p{i}", f"2019-05-10T0{i}:00:00", BOONE, has_thumbnail=(i != 2)) for i in range(5)]
    assert td.pick_cover(points) == "p3"
    assert td.pick_cover([pt("only", "2019-05-10T01:00:00", BOONE, has_thumbnail=False)]) == "only"
    assert td.pick_cover([]) is None


def test_stops_reuse_a_revisited_place_and_record_the_route():
    points = (
        run("2019-05-01T09:00:00", BOONE, per_day=3, prefix="a")
        + run("2019-05-02T09:00:00", BLOWING_ROCK, per_day=2, prefix="b")
        + run("2019-05-03T09:00:00", BOONE, per_day=1, prefix="c")
    )
    stops, route = td.build_stops(points, 2)
    assert [s.count for s in stops] == [4, 2]
    assert route == [0, 1, 0]
    assert stops[0].first_at < stops[1].first_at


def test_stops_name_themselves_after_their_commonest_place():
    points = [pt("a", "2019-05-01T09:00:00", BOONE, place="Cafe"), pt("b", "2019-05-01T10:00:00", BOONE, place="Cafe")]
    stops, _ = td.build_stops(points, 2)
    assert stops[0].place == "Cafe"
