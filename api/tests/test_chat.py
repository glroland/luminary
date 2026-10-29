import pytest

from app import chat, config
from conftest import sync_and_wait, write_image


def _get_synced_photo_id(client, tmp_path):
    write_image(tmp_path / "photos" / "sample.jpg")
    sync_and_wait(client)
    return client.get("/api/photos").json()["photos"][0]["id"]


def test_chat_404s_for_unknown_photo(client):
    response = client.post(
        "/api/photos/does-not-exist/chat", json={"backend": "openai", "message": "hi", "history": []}
    )
    assert response.status_code == 404


def test_chat_returns_reply_from_selected_backend(client, tmp_path, monkeypatch):
    photo_id = _get_synced_photo_id(client, tmp_path)

    captured = {}

    def fake_ask(backend, photo_bytes, content_type, message, history):
        captured["args"] = (backend, content_type, message, history)
        return "a red square"

    monkeypatch.setattr(chat, "ask", fake_ask)

    response = client.post(
        f"/api/photos/{photo_id}/chat",
        json={"backend": "florence", "message": "what is this?", "history": [{"role": "user", "text": "hi"}]},
    )
    assert response.status_code == 200
    assert response.json() == {"reply": "a red square"}
    backend, content_type, message, history = captured["args"]
    assert backend == "florence"
    assert content_type == "image/jpeg"
    assert message == "what is this?"
    assert history == [{"role": "user", "text": "hi"}]


def test_chat_returns_503_when_backend_not_configured(client, tmp_path, monkeypatch):
    photo_id = _get_synced_photo_id(client, tmp_path)

    def fake_ask(*args, **kwargs):
        raise chat.ChatNotConfigured("set LUMINARY_OPENAI_API_KEY")

    monkeypatch.setattr(chat, "ask", fake_ask)

    response = client.post(f"/api/photos/{photo_id}/chat", json={"backend": "openai", "message": "hi"})
    assert response.status_code == 503
    assert "LUMINARY_OPENAI_API_KEY" in response.json()["detail"]


def test_chat_returns_502_when_backend_raises_unexpectedly(client, tmp_path, monkeypatch):
    photo_id = _get_synced_photo_id(client, tmp_path)

    def fake_ask(*args, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(chat, "ask", fake_ask)

    response = client.post(f"/api/photos/{photo_id}/chat", json={"backend": "openai", "message": "hi"})
    assert response.status_code == 502


def test_chat_rejects_unknown_backend_as_a_validation_error(client, tmp_path):
    # Validated by the ChatRequest schema (Literal["openai", "florence"]),
    # not by chat.ask()'s own check -- FastAPI's own 422 means an invalid
    # backend never reaches the except-clauses in chat_about_photo, which
    # matters for the next test: see its comment.
    photo_id = _get_synced_photo_id(client, tmp_path)

    response = client.post(f"/api/photos/{photo_id}/chat", json={"backend": "not-a-real-backend", "message": "hi"})
    assert response.status_code == 422


def test_chat_returns_502_not_400_for_a_valueerror_from_the_backend(client, tmp_path, monkeypatch):
    # Regression test: pydantic.ValidationError (which the openai SDK can
    # raise internally, e.g. parsing a non-conforming response from a
    # self-hosted OpenAI-compatible server) is a ValueError subclass. An
    # earlier version of chat_about_photo caught bare ValueError and mapped
    # it to 400 "bad request", which was wrong here -- a backend/response
    # problem, not a malformed request -- and misled debugging of a real
    # failure. Must come back as 502, with the real error only in the logs.
    photo_id = _get_synced_photo_id(client, tmp_path)

    def fake_ask(*args, **kwargs):
        raise ValueError("1 validation error for ChatCompletion")

    monkeypatch.setattr(chat, "ask", fake_ask)

    response = client.post(f"/api/photos/{photo_id}/chat", json={"backend": "openai", "message": "hi"})
    assert response.status_code == 502


def test_ask_rejects_unknown_backend():
    with pytest.raises(ValueError):
        chat.ask("not-a-real-backend", b"", "image/jpeg", "hi", [])


def test_openai_backend_raises_when_not_configured(monkeypatch):
    monkeypatch.delenv("LUMINARY_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LUMINARY_OPENAI_MODEL", raising=False)

    with pytest.raises(chat.ChatNotConfigured):
        chat.ask("openai", b"fake-bytes", "image/jpeg", "hi", [])


def test_openai_backend_sends_image_and_history_to_the_client(monkeypatch):
    monkeypatch.setenv("LUMINARY_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("LUMINARY_OPENAI_MODEL", "my-vision-model")
    monkeypatch.setenv("LUMINARY_OPENAI_BASE_URL", "http://localhost:9999/v1")

    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)

            class Choice:
                class Message:
                    content = "a red square"

                message = Message()

            class Response:
                choices = [Choice()]

            return Response()

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, api_key, base_url):
            captured["api_key"] = api_key
            captured["base_url"] = base_url
            self.chat = FakeChat()

    import openai

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)

    reply = chat.ask(
        "openai",
        b"fake-bytes",
        "image/jpeg",
        "what is this?",
        [{"role": "assistant", "text": "earlier reply"}],
    )

    assert reply == "a red square"
    assert captured["api_key"] == "test-key"
    assert captured["base_url"] == "http://localhost:9999/v1"
    assert captured["model"] == "my-vision-model"

    messages = captured["messages"]
    assert messages[0] == {"role": "assistant", "content": "earlier reply"}
    last = messages[-1]
    assert last["role"] == "user"
    text_part = next(part for part in last["content"] if part["type"] == "text")
    image_part = next(part for part in last["content"] if part["type"] == "image_url")
    assert text_part["text"] == "what is this?"
    assert image_part["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_florence_backend_raises_chat_not_configured_without_torch(monkeypatch):
    # torch/transformers are an optional, multi-GB dependency -- this must
    # hold regardless of whether they happen to be installed in whatever
    # environment the suite runs in, so the "not installed" case is
    # simulated at the import boundary rather than relied on as ambient
    # environment state.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in ("torch", "transformers"):
            raise ImportError(f"simulated missing {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(chat, "_florence_model", None)
    monkeypatch.setattr(chat, "_florence_processor", None)

    with pytest.raises(chat.ChatNotConfigured):
        chat.ask("florence", b"fake-bytes", "image/jpeg", "what is this?", [])


# --- Streaming -------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [(None, False), ("", False), ("false", False), ("0", False)])
def test_openai_streaming_defaults_to_off(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("LUMINARY_OPENAI_STREAMING", raising=False)
    else:
        monkeypatch.setenv("LUMINARY_OPENAI_STREAMING", raw)
    assert config.get_openai_streaming() is expected


@pytest.mark.parametrize("raw", ["true", "1", "yes", "on", "True", "ON"])
def test_openai_streaming_recognizes_truthy_values(monkeypatch, raw):
    monkeypatch.setenv("LUMINARY_OPENAI_STREAMING", raw)
    assert config.get_openai_streaming() is True


def test_should_stream_is_false_for_openai_when_disabled(monkeypatch):
    monkeypatch.delenv("LUMINARY_OPENAI_STREAMING", raising=False)
    assert chat.should_stream("openai") is False


def test_should_stream_is_true_for_openai_when_enabled(monkeypatch):
    monkeypatch.setenv("LUMINARY_OPENAI_STREAMING", "true")
    assert chat.should_stream("openai") is True


def test_should_stream_is_always_false_for_florence(monkeypatch):
    # Florence-2 has no streaming API to use even if the flag is set.
    monkeypatch.setenv("LUMINARY_OPENAI_STREAMING", "true")
    assert chat.should_stream("florence") is False


def test_ask_stream_rejects_florence():
    with pytest.raises(ValueError):
        chat.ask_stream("florence", b"", "image/jpeg", "hi", [])


def test_ask_stream_raises_chat_not_configured_synchronously(monkeypatch):
    # Must raise as soon as ask_stream() is called, not only once the
    # returned generator is first iterated -- see _prepare_openai_call's
    # docstring for why (the router needs this to still produce a normal
    # HTTPException instead of a broken/empty streaming response).
    monkeypatch.delenv("LUMINARY_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LUMINARY_OPENAI_MODEL", raising=False)

    with pytest.raises(chat.ChatNotConfigured):
        chat.ask_stream("openai", b"fake-bytes", "image/jpeg", "hi", [])


def test_ask_stream_yields_chunks_from_the_client(monkeypatch):
    monkeypatch.setenv("LUMINARY_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("LUMINARY_OPENAI_MODEL", "my-vision-model")

    class FakeDelta:
        def __init__(self, content):
            self.content = content

    class FakeChoice:
        def __init__(self, content):
            self.delta = FakeDelta(content)

    class FakeChunk:
        def __init__(self, content):
            self.choices = [FakeChoice(content)]

    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            # A None content delta (e.g. the final chunk carrying only a
            # finish_reason) must be skipped, not yielded as "None".
            return iter([FakeChunk("Hello"), FakeChunk(", "), FakeChunk("world"), FakeChunk(None)])

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, api_key, base_url):
            self.chat = FakeChat()

    import openai

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)

    chunks = list(chat.ask_stream("openai", b"fake-bytes", "image/jpeg", "what is this?", []))

    assert chunks == ["Hello", ", ", "world"]
    assert captured["stream"] is True
    assert captured["model"] == "my-vision-model"


def test_chat_endpoint_streams_when_enabled(client, tmp_path, monkeypatch):
    photo_id = _get_synced_photo_id(client, tmp_path)

    def fake_ask_stream(backend, photo_bytes, content_type, message, history):
        assert backend == "openai"
        return iter(["Hel", "lo, ", "world"])

    monkeypatch.setattr(chat, "should_stream", lambda backend: True)
    monkeypatch.setattr(chat, "ask_stream", fake_ask_stream)

    response = client.post(f"/api/photos/{photo_id}/chat", json={"backend": "openai", "message": "hi"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == "Hello, world"


def test_chat_endpoint_streaming_still_503s_when_not_configured(client, tmp_path, monkeypatch):
    # The config check in ask_stream() must run before the streaming
    # response starts, so a misconfigured backend is still a clean 503 --
    # not a 200 with a broken/empty stream.
    photo_id = _get_synced_photo_id(client, tmp_path)

    def fake_ask_stream(*args, **kwargs):
        raise chat.ChatNotConfigured("set LUMINARY_OPENAI_API_KEY")

    monkeypatch.setattr(chat, "should_stream", lambda backend: True)
    monkeypatch.setattr(chat, "ask_stream", fake_ask_stream)

    response = client.post(f"/api/photos/{photo_id}/chat", json={"backend": "openai", "message": "hi"})

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")


def test_chat_endpoint_does_not_stream_by_default(client, tmp_path, monkeypatch):
    photo_id = _get_synced_photo_id(client, tmp_path)
    monkeypatch.delenv("LUMINARY_OPENAI_STREAMING", raising=False)

    monkeypatch.setattr(chat, "ask", lambda *args, **kwargs: "a plain reply")

    response = client.post(f"/api/photos/{photo_id}/chat", json={"backend": "openai", "message": "hi"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"reply": "a plain reply"}
