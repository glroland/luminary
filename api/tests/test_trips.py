"""trips.py against the real test database, through the HTTP API."""

import uuid
from datetime import datetime, timedelta

import pytest
from conftest import refresh_trips_and_wait

from app.database import get_connection

ATLANTA = (33.749, -84.388)
BOONE = (36.2168, -81.6746)


@pytest.fixture(autouse=True)
def small_thresholds(monkeypatch):
    monkeypatch.setenv("LUMINARY_TRIP_HOME_MIN_DAYS", "5")
    monkeypatch.setenv("LUMINARY_TRIP_MIN_PHOTOS", "3")
    monkeypatch.setenv("LUMINARY_TRIP_MIN_HOURS", "1")


def add_photo(when, where=None, **columns):
    photo_id = str(uuid.uuid4())
    values = {
        "id": photo_id,
        "filename": f"{photo_id}.jpg",
        "content_type": "image/jpeg",
        "size_bytes": 1,
        "created_at": when.isoformat() if when else None,
        "latitude": where[0] if where else None,
        "longitude": where[1] if where else None,
        "thumbnail": b"x",
    }
    values.update(columns)
    with get_connection() as conn:
        conn.execute(
            f"INSERT INTO photos ({', '.join(values)}) VALUES ({', '.join(['%s'] * len(values))})",
            tuple(values.values()),
        )
        conn.commit()
    return photo_id


def add_home_life(days=8, start=datetime(2019, 1, 1, 9)):
    return [add_photo(start + timedelta(days=d), ATLANTA) for d in range(days)]


def add_trip(start=datetime(2019, 5, 10, 9), days=3, per_day=4, where=BOONE, **columns):
    return [
        add_photo(start + timedelta(days=d, hours=2 * i), where, **columns)
        for d in range(days)
        for i in range(per_day)
    ]


def trips(client):
    return client.get("/api/trips").json()["trips"]


def test_rebuild_creates_a_trip_from_dated_gps_photos(client):
    add_home_life()
    members = add_trip(location_country="United States", location_state="North Carolina", location_city="Boone")

    status = refresh_trips_and_wait(client)

    assert status["status"] == "completed"
    assert status["created"] == 1 and status["homes"] == 1
    [trip] = trips(client)
    assert trip["name"] == "Boone, North Carolina, May 2019"
    assert trip["kind"] == "trip" and trip["source"] == "auto" and trip["edited"] is False
    assert trip["photo_count"] == len(members)
    assert trip["cover_photo_id"] in members

    ids = client.get(f"/api/trips/{trip['id']}/photos").json()["photo_ids"]
    assert ids == members  # oldest first
    assert client.get(f"/api/trips/{trip['id']}").json()["id"] == trip["id"]


def test_photos_without_a_date_or_without_gps_are_never_organized(client):
    add_home_life()
    add_trip()
    stray = [add_photo(None, BOONE), add_photo(datetime(2019, 5, 11, 12), None)]

    refresh_trips_and_wait(client)

    [trip] = trips(client)
    ids = client.get(f"/api/trips/{trip['id']}/photos").json()["photo_ids"]
    assert not set(stray) & set(ids)


def test_a_second_rebuild_changes_nothing(client):
    add_home_life()
    add_trip()
    refresh_trips_and_wait(client)
    [before] = trips(client)

    status = refresh_trips_and_wait(client)

    assert (status["created"], status["updated"], status["removed"]) == (0, 0, 0)
    assert trips(client) == [before]


def test_a_new_photo_extends_the_existing_trip(client):
    add_home_life()
    add_trip()
    refresh_trips_and_wait(client)
    [before] = trips(client)

    extra = add_photo(datetime(2019, 5, 13, 10), BOONE)
    status = refresh_trips_and_wait(client)

    [after] = trips(client)
    assert after["id"] == before["id"]
    assert after["photo_count"] == before["photo_count"] + 1
    assert status["updated"] == 1
    assert extra in client.get(f"/api/trips/{after['id']}/photos").json()["photo_ids"]


def test_trips_that_no_longer_qualify_are_removed(client):
    add_home_life()
    members = add_trip(days=1, per_day=4)
    refresh_trips_and_wait(client)
    assert len(trips(client)) == 1

    for photo_id in members[:2]:
        assert client.delete(f"/api/photos/{photo_id}").status_code == 204
    status = refresh_trips_and_wait(client)

    assert trips(client) == []
    assert status["removed"] == 1
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM trip_photos").fetchone()["n"] == 0


def test_deleting_a_photo_drops_its_membership(client):
    add_home_life()
    members = add_trip()
    refresh_trips_and_wait(client)

    client.delete(f"/api/photos/{members[0]}")

    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM trip_photos WHERE photo_id = %s", (members[0],)).fetchone()["n"] == 0


def test_an_edited_trip_keeps_its_name_and_survives_losing_photos(client):
    add_home_life()
    members = add_trip()
    refresh_trips_and_wait(client)
    [trip] = trips(client)
    with get_connection() as conn:
        conn.execute("UPDATE trips SET edited = TRUE, name_source = 'manual', name = 'Mountains' WHERE id = %s", (trip["id"],))
        conn.commit()

    for photo_id in members:
        client.delete(f"/api/photos/{photo_id}")
    refresh_trips_and_wait(client)

    [kept] = trips(client)
    assert kept["id"] == trip["id"] and kept["name"] == "Mountains" and kept["photo_count"] == 0


def test_a_manually_added_photo_stays_even_without_gps_or_date(client):
    add_home_life()
    add_trip()
    refresh_trips_and_wait(client)
    [trip] = trips(client)
    manual = add_photo(None, None)
    with get_connection() as conn:
        conn.execute("INSERT INTO trip_photos (photo_id, trip_id, membership) VALUES (%s, %s, 'manual')", (manual, trip["id"]))
        conn.commit()

    refresh_trips_and_wait(client)

    ids = client.get(f"/api/trips/{trip['id']}/photos").json()["photo_ids"]
    assert ids[-1] == manual  # undated sorts last
    locations = client.get(f"/api/trips/{trip['id']}/locations").json()
    assert locations["unlocated_count"] == 1


def test_an_excluded_photo_is_not_put_back(client):
    add_home_life()
    members = add_trip()
    refresh_trips_and_wait(client)
    [trip] = trips(client)
    with get_connection() as conn:
        conn.execute("DELETE FROM trip_photos WHERE photo_id = %s", (members[0],))
        conn.execute("INSERT INTO trip_exclusions (trip_id, photo_id) VALUES (%s, %s)", (trip["id"], members[0]))
        conn.commit()

    refresh_trips_and_wait(client)

    assert members[0] not in client.get(f"/api/trips/{trip['id']}/photos").json()["photo_ids"]


def test_a_dismissed_trip_is_not_recreated(client):
    add_home_life()
    add_trip()
    refresh_trips_and_wait(client)
    [trip] = trips(client)
    with get_connection() as conn:
        conn.execute("UPDATE trips SET status = 'dismissed' WHERE id = %s", (trip["id"],))
        conn.execute("DELETE FROM trip_photos WHERE trip_id = %s", (trip["id"],))
        conn.commit()

    refresh_trips_and_wait(client)
    add_photo(datetime(2019, 5, 11, 15), BOONE)
    refresh_trips_and_wait(client)

    assert trips(client) == []


def test_homes_a_user_entered_win_over_detection(client):
    add_home_life()  # would detect Atlanta
    add_trip()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO homes (id, name, latitude, longitude, source, confirmed) VALUES ('h', 'Cabin', %s, %s, 'manual', TRUE)",
            BOONE,
        )
        conn.commit()

    refresh_trips_and_wait(client)

    # With Boone as home, the Boone photos are home and the Atlanta ones are away (too few to qualify).
    assert all("Boone" not in t["name"] for t in trips(client))
    with get_connection() as conn:
        assert [r["name"] for r in conn.execute("SELECT name FROM homes").fetchall()] == ["Cabin"]


def test_locations_give_stops_route_and_home(client):
    add_home_life()
    add_trip(days=2, per_day=3, location_name="Boone Cafe")
    refresh_trips_and_wait(client)
    [trip] = trips(client)

    locations = client.get(f"/api/trips/{trip['id']}/locations").json()

    assert len(locations["stops"]) == 1
    stop = locations["stops"][0]
    assert stop["count"] == 6 and stop["place"] == "Boone Cafe" and len(stop["photo_ids"]) == 6
    assert locations["route"] == [0]
    assert locations["unlocated_count"] == 0
    assert locations["home"]["latitude"] == pytest.approx(ATLANTA[0], abs=0.1)


def test_unknown_trip_is_404(client):
    for path in ("", "/photos", "/locations"):
        assert client.get(f"/api/trips/nope{path}").status_code == 404


def test_the_jobs_snapshot_includes_trips_and_the_slot_is_shared(client):
    assert client.get("/api/jobs").json()["trips"]["status"] == "idle"
    from app import jobs

    jobs.try_claim(jobs.SYNC)
    try:
        assert client.post("/api/trips/refresh").status_code == 409
    finally:
        jobs.release(jobs.SYNC)


def test_no_home_found_means_no_trips(client):
    add_trip()  # no home-life photos, so nothing to call "away" from
    status = refresh_trips_and_wait(client)
    assert status["homes"] == 0 and trips(client) == []
