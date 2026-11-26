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


# ------------------------------------------------------------------ edits


def two_trips(client):
    add_home_life()
    first = add_trip(start=datetime(2019, 5, 10, 9), days=2, per_day=4)
    second = add_trip(start=datetime(2019, 5, 20, 9), days=2, per_day=4, where=(28.5383, -81.3792))
    refresh_trips_and_wait(client)
    found = sorted(trips(client), key=lambda t: t["start_at"])
    assert len(found) == 2
    return found, first, second


def members(client, trip_id):
    return client.get(f"/api/trips/{trip_id}/photos").json()["photo_ids"]


def test_rename_cover_and_kind(client):
    add_home_life()
    ids = add_trip()
    refresh_trips_and_wait(client)
    [trip] = trips(client)

    renamed = client.patch(f"/api/trips/{trip['id']}", json={"name": "  Mountain weekend  ", "cover_photo_id": ids[1], "kind": "outing"})

    assert renamed.status_code == 200
    body = renamed.json()
    assert (body["name"], body["name_source"], body["cover_photo_id"], body["kind"], body["edited"]) == (
        "Mountain weekend", "manual", ids[1], "outing", True,
    )

    # A rebuild leaves every one of those alone.
    refresh_trips_and_wait(client)
    assert trips(client)[0]["name"] == "Mountain weekend"
    assert trips(client)[0]["cover_photo_id"] == ids[1]
    assert trips(client)[0]["kind"] == "outing"

    # An empty name and cover go back to the generated ones.
    reverted = client.patch(f"/api/trips/{trip['id']}", json={"name": "", "cover_photo_id": ""}).json()
    # (still an outing, so the generated name is the outing form)
    assert reverted["name_source"] == "auto" and reverted["name"] == "Outing, May 10, 2019"


def test_update_rejects_a_cover_outside_the_trip_and_a_bad_kind(client):
    add_home_life()
    add_trip()
    refresh_trips_and_wait(client)
    [trip] = trips(client)
    outsider = add_photo(datetime(2019, 6, 1), BOONE)

    assert client.patch(f"/api/trips/{trip['id']}", json={"cover_photo_id": outsider}).status_code == 422
    assert client.patch(f"/api/trips/{trip['id']}", json={"kind": "holiday"}).status_code == 422
    assert client.patch("/api/trips/nope", json={"name": "x"}).status_code == 404


def test_adding_photos_by_hand_keeps_them_and_moves_them_between_trips(client):
    (first, second), first_ids, second_ids = two_trips(client)
    undated = add_photo(None, None)

    added = client.post(f"/api/trips/{first['id']}/photos/add", json={"photo_ids": [undated, second_ids[0], "nope"]})

    assert added.status_code == 200
    assert added.json()["photo_count"] == len(first_ids) + 2
    assert undated in members(client, first["id"]) and second_ids[0] in members(client, first["id"])
    assert second_ids[0] not in members(client, second["id"])
    # The trip it was taken from remembers not to take it back.
    refresh_trips_and_wait(client)
    assert second_ids[0] in members(client, first["id"])
    assert second_ids[0] not in members(client, second["id"])


def test_removing_photos_is_remembered(client):
    add_home_life()
    ids = add_trip()
    refresh_trips_and_wait(client)
    [trip] = trips(client)

    removed = client.post(f"/api/trips/{trip['id']}/photos/remove", json={"photo_ids": ids[:2]})

    assert removed.json()["photo_count"] == len(ids) - 2
    refresh_trips_and_wait(client)
    assert not set(ids[:2]) & set(members(client, trip["id"]))


def test_removing_the_cover_picks_a_new_one(client):
    add_home_life()
    add_trip()
    refresh_trips_and_wait(client)
    [trip] = trips(client)

    after = client.post(f"/api/trips/{trip['id']}/photos/remove", json={"photo_ids": [trip["cover_photo_id"]]}).json()

    assert after["cover_photo_id"] not in (None, trip["cover_photo_id"])


def test_a_trip_made_by_date_range_takes_any_photo_in_it_that_is_free(client):
    add_home_life()
    inside = [add_photo(datetime(2018, 7, d, 12), None) for d in (3, 4, 5)]  # no GPS at all
    outside = add_photo(datetime(2018, 8, 1, 12), None)
    taken = add_photo(datetime(2018, 7, 4, 15), None)
    other = client.post("/api/trips", json={"name": "Other", "photo_ids": [taken]}).json()

    made = client.post("/api/trips", json={"name": "Lake house", "start_date": "2018-07-03", "end_date": "2018-07-05"})

    assert made.status_code == 201
    body = made.json()
    assert (body["name"], body["source"], body["name_source"]) == ("Lake house", "manual", "manual")
    assert set(members(client, body["id"])) == set(inside)  # not the other month's photo, not the taken one
    assert taken in members(client, other["id"])  # a free-range pick never steals


def test_a_manual_trip_needs_a_name_and_some_photos(client):
    assert client.post("/api/trips", json={"name": " ", "photo_ids": ["x"]}).status_code == 422
    assert client.post("/api/trips", json={"name": "Empty"}).status_code == 422
    assert client.post("/api/trips", json={"name": "Bad", "start_date": "05/01/2019"}).status_code == 422


def test_a_manual_trip_survives_a_rebuild(client):
    add_home_life()
    photo = add_photo(datetime(2018, 7, 3, 12), None)
    made = client.post("/api/trips", json={"name": "Keep me", "photo_ids": [photo]}).json()

    refresh_trips_and_wait(client)

    assert [t["id"] for t in trips(client)] == [made["id"]]
    assert members(client, made["id"]) == [photo]


def test_merging_keeps_the_earliest_trip_and_a_rebuild_does_not_split_it_again(client):
    (first, second), first_ids, second_ids = two_trips(client)
    client.patch(f"/api/trips/{second['id']}", json={"name": "Florida"})

    merged = client.post("/api/trips/merge", json={"trip_ids": [second["id"], first["id"]]})

    assert merged.status_code == 200
    body = merged.json()
    assert body["id"] == first["id"]
    assert body["name"] == "Florida"  # the one manual name wins over a generated one
    assert body["photo_count"] == len(first_ids) + len(second_ids)
    assert [t["id"] for t in trips(client)] == [first["id"]]
    refresh_trips_and_wait(client)
    assert [t["id"] for t in trips(client)] == [first["id"]]


def test_merge_needs_two_real_trips(client):
    (first, _), _, _ = two_trips(client)
    assert client.post("/api/trips/merge", json={"trip_ids": [first["id"]]}).status_code == 422
    assert client.post("/api/trips/merge", json={"trip_ids": [first["id"], "nope"]}).status_code == 404


def test_splitting_makes_two_trips_and_a_rebuild_does_not_join_them(client):
    add_home_life()
    ids = add_trip(days=4, per_day=3)
    refresh_trips_and_wait(client)
    [trip] = trips(client)

    split = client.post(f"/api/trips/{trip['id']}/split", json={"at": "2019-05-12"})

    assert split.status_code == 200
    original, new = split.json()["trips"]
    assert original["id"] == trip["id"] and new["id"] != trip["id"]
    assert original["photo_count"] == 6 and new["photo_count"] == 6
    assert set(members(client, new["id"])) == set(ids[6:])
    refresh_trips_and_wait(client)
    assert sorted(t["photo_count"] for t in trips(client)) == [6, 6]


def test_a_split_needs_photos_on_both_sides(client):
    add_home_life()
    add_trip()
    refresh_trips_and_wait(client)
    [trip] = trips(client)
    assert client.post(f"/api/trips/{trip['id']}/split", json={"at": "2030-01-01"}).status_code == 422
    assert client.post(f"/api/trips/{trip['id']}/split", json={"at": "2000-01-01"}).status_code == 422
    assert client.post(f"/api/trips/{trip['id']}/split", json={"at": "soon"}).status_code == 422


def test_dismissing_hides_a_trip_for_good_and_restoring_brings_it_back(client):
    add_home_life()
    ids = add_trip()
    refresh_trips_and_wait(client)
    [trip] = trips(client)

    assert client.post(f"/api/trips/{trip['id']}/dismiss").status_code == 204

    assert trips(client) == []
    assert [t["id"] for t in client.get("/api/trips", params={"status": "dismissed"}).json()["trips"]] == [trip["id"]]
    add_photo(datetime(2019, 5, 11, 15), BOONE)
    refresh_trips_and_wait(client)
    assert trips(client) == []

    restored = client.post(f"/api/trips/{trip['id']}/restore")

    assert restored.status_code == 200 and restored.json()["photo_count"] == len(ids)
    assert [t["id"] for t in trips(client)] == [trip["id"]]
    assert client.post(f"/api/trips/{trip['id']}/restore").status_code == 404  # not dismissed any more
    assert client.post("/api/trips/nope/dismiss").status_code == 404


# ------------------------------------------------------------------- homes


def wait_for_trips_job(client, timeout=30.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client.get("/api/trips/refresh/status").json()["status"] != "running":
            return
        time.sleep(0.05)
    raise AssertionError("trips job did not finish")


def homes(client):
    return client.get("/api/homes").json()["homes"]


def test_a_detected_home_is_listed_unconfirmed_and_confirming_pins_it(client):
    add_home_life()
    add_trip()
    refresh_trips_and_wait(client)

    [home] = homes(client)
    assert home["source"] == "auto" and home["confirmed"] is False
    assert home["latitude"] == pytest.approx(ATLANTA[0], abs=0.1)

    confirmed = client.post(f"/api/homes/{home['id']}/confirm")
    wait_for_trips_job(client)

    assert confirmed.status_code == 200 and confirmed.json()["rebuild_started"] is True
    assert confirmed.json()["home"]["confirmed"] is True
    [after] = homes(client)
    assert after["id"] == home["id"]  # a rebuild no longer replaces it
    assert len(trips(client)) == 1


def test_entering_a_home_replaces_the_detected_one(client):
    add_home_life()
    add_trip()
    refresh_trips_and_wait(client)

    made = client.post("/api/homes", json={"name": "Cabin", "latitude": BOONE[0], "longitude": BOONE[1], "radius_km": 20})
    wait_for_trips_job(client)

    assert made.status_code == 201
    assert made.json()["home"]["source"] == "manual" and made.json()["home"]["confirmed"] is True
    assert [h["name"] for h in homes(client)] == ["Cabin"]


def test_editing_a_home(client):
    made = client.post("/api/homes", json={"latitude": 10, "longitude": 20}).json()["home"]
    wait_for_trips_job(client)
    assert made["name"] == "Home"

    patched = client.patch(
        f"/api/homes/{made['id']}",
        json={"name": "Flat", "radius_km": 5, "start_date": "2018-01-01", "end_date": "2019-12-31"},
    )
    wait_for_trips_job(client)

    home = patched.json()["home"]
    assert (home["name"], home["radius_km"], home["start_date"], home["end_date"]) == ("Flat", 5, "2018-01-01", "2019-12-31")
    cleared = client.patch(f"/api/homes/{made['id']}", json={"end_date": None, "radius_km": None}).json()["home"]
    wait_for_trips_job(client)
    assert cleared["end_date"] is None and cleared["radius_km"] is None and cleared["start_date"] == "2018-01-01"


def test_bad_homes_are_rejected(client):
    for body in (
        {"latitude": 91, "longitude": 0},
        {"latitude": 0, "longitude": 181},
        {"latitude": 0, "longitude": 0, "radius_km": 0},
        {"latitude": 0, "longitude": 0, "start_date": "later"},
        {"latitude": 0, "longitude": 0, "start_date": "2020-01-01", "end_date": "2019-01-01"},
    ):
        assert client.post("/api/homes", json=body).status_code == 422, body
    assert client.patch("/api/homes/nope", json={"name": "x"}).status_code == 404
    assert client.post("/api/homes/nope/confirm").status_code == 404
    assert client.delete("/api/homes/nope").status_code == 404


def test_deleting_the_only_pinned_home_lets_detection_take_over_again(client):
    add_home_life()
    add_trip()
    made = client.post("/api/homes", json={"latitude": 10, "longitude": 20}).json()["home"]
    wait_for_trips_job(client)

    deleted = client.delete(f"/api/homes/{made['id']}")
    wait_for_trips_job(client)

    assert deleted.status_code == 200 and deleted.json()["home"] is None
    [home] = homes(client)
    assert home["source"] == "auto"


def test_a_home_change_while_another_job_runs_says_the_rebuild_didnt_start(client):
    from app import jobs

    jobs.try_claim(jobs.SYNC)
    try:
        made = client.post("/api/homes", json={"latitude": 10, "longitude": 20})
    finally:
        jobs.release(jobs.SYNC)
    assert made.status_code == 201 and made.json()["rebuild_started"] is False
