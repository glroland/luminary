import pytest

from app import enrich
from app.database import get_connection


def _insert_photo(photo_id, filename="sample.jpg", vlm_description=None, location_name=None):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO photos (id, filename, content_type, size_bytes, created_at, vlm_description, location_name) "
            "VALUES (%s, %s, 'image/jpeg', 1, '2024-01-01T00:00:00', %s, %s)",
            (photo_id, filename, vlm_description, location_name),
        )
        conn.commit()


def _insert_individual(person_id, name):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO individuals (id, name, created_at) VALUES (%s, %s, '2024-01-01T00:00:00')",
            (person_id, name),
        )
        conn.commit()


def _insert_tagged_face(face_id, photo_id, person_id):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO faces (id, photo_id, x1, y1, x2, y2, person_id, tag_source) "
            "VALUES (%s, %s, 0, 0, 1, 1, %s, 'manual')",
            (face_id, photo_id, person_id),
        )
        conn.commit()


# --- _build_prompt -----------------------------------------------------


def test_build_prompt_includes_every_available_fact():
    facts = {
        "filename": "vacation/2019/beach.jpg",
        "vlm_description": "two people on a beach",
        "location_name": "Boone, North Carolina",
        "individuals": ["Alice", "Bob"],
    }
    prompt = enrich._build_prompt(facts)
    assert "vacation/2019/beach.jpg" in prompt
    assert "two people on a beach" in prompt
    assert "Boone, North Carolina" in prompt
    assert "Alice, Bob" in prompt


def test_build_prompt_omits_missing_facts():
    facts = {"filename": "sample.jpg", "vlm_description": None, "location_name": None, "individuals": []}
    prompt = enrich._build_prompt(facts)
    assert "sample.jpg" in prompt
    assert "Automated image caption" not in prompt
    assert "Location:" not in prompt
    assert "People identified" not in prompt


# --- _compose ------------------------------------------------------------


def test_compose_raises_not_configured_without_api_key_or_model(monkeypatch):
    monkeypatch.delenv("LUMINARY_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LUMINARY_OPENAI_MODEL", raising=False)

    with pytest.raises(enrich.EnrichmentNotConfigured):
        enrich._compose({"filename": "x.jpg", "vlm_description": None, "location_name": None, "individuals": []})


def test_compose_sends_the_prompt_and_returns_the_stripped_reply(monkeypatch):
    monkeypatch.setenv("LUMINARY_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("LUMINARY_OPENAI_MODEL", "my-text-model")

    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)

            class Choice:
                class Message:
                    content = "  A nice day at the beach.  "

                message = Message()

            class Response:
                choices = [Choice()]

            return Response()

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, api_key, base_url):
            captured["api_key"] = api_key
            self.chat = FakeChat()

    import openai

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)

    result = enrich._compose(
        {"filename": "beach.jpg", "vlm_description": "a beach", "location_name": None, "individuals": []}
    )

    assert result == "A nice day at the beach."
    assert captured["api_key"] == "test-key"
    assert captured["model"] == "my-text-model"
    assert captured["messages"] == [{"role": "user", "content": enrich._build_prompt(
        {"filename": "beach.jpg", "vlm_description": "a beach", "location_name": None, "individuals": []}
    )}]


# --- _photo_facts ----------------------------------------------------------


def test_photo_facts_returns_none_for_a_missing_photo(client):
    assert enrich._photo_facts("does-not-exist") is None


def test_photo_facts_gathers_distinct_sorted_individual_names(client):
    _insert_photo("photo-1", filename="party.jpg", vlm_description="a party", location_name="Boone, NC")
    _insert_individual("person-bob", "Bob")
    _insert_individual("person-alice", "Alice")
    # Two faces tagged to Bob -- his name must appear only once.
    _insert_tagged_face("face-1", "photo-1", "person-bob")
    _insert_tagged_face("face-2", "photo-1", "person-bob")
    _insert_tagged_face("face-3", "photo-1", "person-alice")

    facts = enrich._photo_facts("photo-1")

    assert facts == {
        "filename": "party.jpg",
        "vlm_description": "a party",
        "location_name": "Boone, NC",
        "individuals": ["Alice", "Bob"],
    }


def test_photo_facts_individuals_is_empty_when_nothing_is_tagged(client):
    _insert_photo("photo-1")
    assert enrich._photo_facts("photo-1")["individuals"] == []


# --- enrich_description ----------------------------------------------------


def test_enrich_description_is_a_noop_when_not_configured(client, monkeypatch):
    monkeypatch.delenv("LUMINARY_OPENAI_API_KEY", raising=False)
    _insert_photo("photo-1")

    enrich.enrich_description("photo-1")  # must not raise

    with get_connection() as conn:
        row = conn.execute("SELECT description FROM photos WHERE id = %s", ("photo-1",)).fetchone()
    assert row["description"] is None


def test_enrich_description_is_a_noop_for_a_missing_photo(client, monkeypatch):
    monkeypatch.setenv("LUMINARY_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("LUMINARY_OPENAI_MODEL", "my-text-model")
    calls = []
    monkeypatch.setattr(enrich, "_compose", lambda facts: calls.append(facts) or "unused")

    enrich.enrich_description("does-not-exist")  # must not raise

    assert calls == []


def test_enrich_description_stores_the_composed_text(client, monkeypatch):
    monkeypatch.setattr(enrich, "_compose", lambda facts: "A composed description.")
    _insert_photo("photo-1")

    enrich.enrich_description("photo-1")

    with get_connection() as conn:
        row = conn.execute("SELECT description FROM photos WHERE id = %s", ("photo-1",)).fetchone()
    assert row["description"] == "A composed description."


def test_enrich_description_leaves_a_stale_description_in_place_on_failure(client, monkeypatch, caplog):
    import logging

    def boom(facts):
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr(enrich, "_compose", boom)
    _insert_photo("photo-1")
    with get_connection() as conn:
        conn.execute("UPDATE photos SET description = 'a prior description' WHERE id = %s", ("photo-1",))
        conn.commit()

    with caplog.at_level(logging.WARNING, logger="app.enrich"):
        enrich.enrich_description("photo-1")  # must not raise

    with get_connection() as conn:
        row = conn.execute("SELECT description FROM photos WHERE id = %s", ("photo-1",)).fetchone()
    assert row["description"] == "a prior description"
    assert any(record.levelno == logging.WARNING for record in caplog.records)
