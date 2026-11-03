from app import geocode
from app.database import get_connection
from conftest import refresh_locations_and_wait


def _insert_photo(photo_id, filename="sample.jpg", latitude=None, longitude=None, location_name=None):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO photos (id, filename, content_type, size_bytes, created_at, latitude, longitude, "
            "location_name) VALUES (%s, %s, 'image/jpeg', 1, '2024-01-01T00:00:00', %s, %s, %s)",
            (photo_id, filename, latitude, longitude, location_name),
        )
        conn.commit()


def test_refresh_status_defaults_to_idle(client):
    status = client.get("/api/locations/refresh/status").json()
    assert status == {"status": "idle", "processed": 0, "total": 0, "updated": 0, "error": None}


def test_refresh_with_no_geotagged_photos_does_nothing(client):
    _insert_photo("photo-1")  # no latitude/longitude

    status = refresh_locations_and_wait(client)
    assert status == {"status": "completed", "processed": 0, "total": 0, "updated": 0, "error": None}


def test_refresh_re_geocodes_every_photo_with_gps_coordinates(client, monkeypatch):
    _insert_photo("photo-1", "a.jpg", latitude=34.0754, longitude=-84.2941, location_name="Alpharetta, Georgia")
    _insert_photo("photo-2", "b.jpg", latitude=36.2168, longitude=-81.6746, location_name="Boone, North Carolina")

    monkeypatch.setattr(geocode, "reverse_geocode", lambda lat, lon, **kwargs: f"({lat}, {lon})")

    status = refresh_locations_and_wait(client)
    assert status["status"] == "completed"
    assert status["total"] == 2
    assert status["processed"] == 2
    assert status["updated"] == 2

    with get_connection() as conn:
        rows = conn.execute("SELECT filename, location_name FROM photos ORDER BY filename").fetchall()
    assert [row["location_name"] for row in rows] == ["(34.0754, -84.2941)", "(36.2168, -81.6746)"]


def test_refresh_overwrites_with_none_on_a_resultless_lookup(client, monkeypatch):
    # Mirrors sync.py's own _describe_location: a successful Nominatim
    # call that found nothing usable (open ocean, no OSM address data)
    # still unconditionally overwrites location_name, same as a fresh
    # sync of the same coordinates would.
    _insert_photo("photo-1", latitude=0.0, longitude=0.0, location_name="stale value")

    monkeypatch.setattr(geocode, "reverse_geocode", lambda lat, lon, **kwargs: None)

    status = refresh_locations_and_wait(client)
    assert status["updated"] == 1

    with get_connection() as conn:
        row = conn.execute("SELECT location_name FROM photos WHERE id = %s", ("photo-1",)).fetchone()
    assert row["location_name"] is None


def test_refresh_leaves_location_name_untouched_on_failure(client, monkeypatch):
    _insert_photo("photo-1", latitude=34.0754, longitude=-84.2941, location_name="Alpharetta, Georgia")

    def boom(lat, lon, **kwargs):
        raise RuntimeError("Nominatim unreachable")

    monkeypatch.setattr(geocode, "reverse_geocode", boom)

    status = refresh_locations_and_wait(client)
    assert status["status"] == "completed"
    assert status["total"] == 1
    assert status["processed"] == 1
    assert status["updated"] == 0

    with get_connection() as conn:
        row = conn.execute("SELECT location_name FROM photos WHERE id = %s", ("photo-1",)).fetchone()
    assert row["location_name"] == "Alpharetta, Georgia"


def test_refresh_ignores_a_duplicate_start_while_already_running(client, monkeypatch):
    assert geocode.progress.try_start() is True  # simulate a run already in flight

    response = client.post("/api/locations/refresh")
    assert response.json()["status"] == "running"
