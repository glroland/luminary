from datetime import datetime


def test_about_in_development(client, monkeypatch):
    monkeypatch.delenv("LUMINARY_VERSION", raising=False)
    monkeypatch.setenv("LUMINARY_PHOTOS_URI", "smb://user:secret@nas/photos")
    monkeypatch.setenv("LUMINARY_OPENAI_MODEL", "my-model")
    monkeypatch.delenv("LUMINARY_OPENAI_BASE_URL", raising=False)

    body = client.get("/api/about").json()

    assert body["version"] is None
    assert body["photos_source"] == "smb://***@nas/photos"
    assert "secret" not in str(body)
    assert body["chat_model"] == "my-model"
    assert body["chat_endpoint"] is None
    assert body["database_name"]
    assert body["year"] == datetime.now().year


def test_about_reports_build_version(client, monkeypatch):
    monkeypatch.setenv("LUMINARY_VERSION", "42")
    assert client.get("/api/about").json()["version"] == "42"
