from app import enrich
from app.database import get_connection
from conftest import refresh_descriptions_and_wait


def _insert_photo(photo_id, filename="sample.jpg"):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO photos (id, filename, content_type, size_bytes, created_at) "
            "VALUES (%s, %s, 'image/jpeg', 1, '2024-01-01T00:00:00')",
            (photo_id, filename),
        )
        conn.commit()


def test_refresh_status_defaults_to_idle(client):
    status = client.get("/api/descriptions/refresh/status").json()
    assert status == {"status": "idle", "processed": 0, "total": 0, "updated": 0, "error": None}


def test_refresh_with_no_photos_does_nothing(client):
    status = refresh_descriptions_and_wait(client)
    assert status == {"status": "completed", "processed": 0, "total": 0, "updated": 0, "error": None}


def test_refresh_updates_the_description_for_every_photo(client, monkeypatch):
    _insert_photo("photo-1", "a.jpg")
    _insert_photo("photo-2", "b.jpg")

    monkeypatch.setattr(
        enrich, "_compose", lambda photo_id, facts: f"A description for {facts['filename']}."
    )

    status = refresh_descriptions_and_wait(client)
    assert status["status"] == "completed"
    assert status["total"] == 2
    assert status["processed"] == 2
    assert status["updated"] == 2

    with get_connection() as conn:
        rows = conn.execute("SELECT filename, description FROM photos ORDER BY filename").fetchall()
    assert [row["description"] for row in rows] == [
        "A description for a.jpg.",
        "A description for b.jpg.",
    ]


def test_refresh_counts_unconfigured_photos_as_processed_but_not_updated(client, monkeypatch):
    monkeypatch.delenv("LUMINARY_OPENAI_API_KEY", raising=False)
    _insert_photo("photo-1")

    status = refresh_descriptions_and_wait(client)
    assert status["status"] == "completed"
    assert status["total"] == 1
    assert status["processed"] == 1
    assert status["updated"] == 0

    with get_connection() as conn:
        row = conn.execute("SELECT description FROM photos WHERE id = %s", ("photo-1",)).fetchone()
    assert row["description"] is None
