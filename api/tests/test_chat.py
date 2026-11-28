import json

import pytest

from app import chat, config, storage
from conftest import sync_and_wait, write_image


def _get_synced_photo_id(client, tmp_path):
    write_image(tmp_path / "photos" / "sample.jpg")
    sync_and_wait(client)
    return client.get("/api/photos").json()["photos"][0]["id"]


def _events(response):
    """The chat routes' response body: one JSON event per line."""
    assert response.headers["content-type"].startswith("application/x-ndjson")
    return [json.loads(line) for line in response.text.splitlines()]


def test_chat_404s_for_unknown_photo(client):
    response = client.post(
        "/api/photos/does-not-exist/chat", json={"message": "hi", "history": []}
    )
    assert response.status_code == 404


def test_chat_returns_reply_and_boxes(client, tmp_path, monkeypatch):
    photo_id = _get_synced_photo_id(client, tmp_path)

    captured = {}

    box = {"label": "square", "x1": 1.0, "y1": 2.0, "x2": 3.0, "y2": 4.0}
    tool = {"index": 0, "name": "locate_object", "arguments": {"object": "square"}}
    reply_events = [
        {"tool": {**tool, "status": "running"}},
        {"tool": {**tool, "status": "done"}},
        {"boxes": [box]},
        {"search_results": [{"id": "p1", "filename": "a.jpg", "score": 1.0}, {"id": "p2", "filename": "b.jpg", "score": 0.5}]},
        {"text": "a red square"},
    ]

    def fake_ask(photo_bytes, content_type, message, history, search_list=None, **kwargs):
        captured["args"] = (content_type, message, history)
        captured["search_list"] = search_list
        return iter(reply_events)

    monkeypatch.setattr(chat, "ask_events", fake_ask)

    response = client.post(
        f"/api/photos/{photo_id}/chat",
        json={
            "message": "what is this?",
            "history": [{"role": "user", "text": "hi"}],
            "search_list": [{"id": "p2", "filename": "b.jpg", "score": 0.5}],
        },
    )
    assert response.status_code == 200
    # Every event goes out as its own line, in order -- tool calls included.
    assert _events(response) == reply_events
    # The search list the client holds is handed to the chat, not the model.
    assert captured["search_list"] == [{"id": "p2", "filename": "b.jpg", "score": 0.5}]
    content_type, message, history = captured["args"]
    assert content_type == "image/jpeg"
    assert message == "what is this?"
    assert history == [{"role": "user", "text": "hi"}]


def test_chat_returns_503_when_backend_not_configured(client, tmp_path, monkeypatch):
    photo_id = _get_synced_photo_id(client, tmp_path)

    def fake_ask(*args, **kwargs):
        raise chat.ChatNotConfigured("set LUMINARY_OPENAI_API_KEY")

    monkeypatch.setattr(chat, "ask_events", fake_ask)

    response = client.post(f"/api/photos/{photo_id}/chat", json={"message": "hi"})
    assert response.status_code == 503
    assert "LUMINARY_OPENAI_API_KEY" in response.json()["detail"]


def test_chat_returns_502_when_backend_raises_unexpectedly(client, tmp_path, monkeypatch):
    photo_id = _get_synced_photo_id(client, tmp_path)

    def fake_ask(*args, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(chat, "ask_events", fake_ask)

    response = client.post(f"/api/photos/{photo_id}/chat", json={"message": "hi"})
    assert response.status_code == 502


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

    monkeypatch.setattr(chat, "ask_events", fake_ask)

    response = client.post(f"/api/photos/{photo_id}/chat", json={"message": "hi"})
    assert response.status_code == 502


def test_openai_backend_raises_when_not_configured(monkeypatch):
    monkeypatch.delenv("LUMINARY_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LUMINARY_OPENAI_MODEL", raising=False)

    with pytest.raises(chat.ChatNotConfigured):
        chat.ask(b"fake-bytes", "image/jpeg", "hi", [])


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
    monkeypatch.setenv("LUMINARY_CHAT_TOOLS", "false")

    reply = chat.ask(
        b"fake-bytes",
        "image/jpeg",
        "what is this?",
        [{"role": "assistant", "text": "earlier reply"}],
    )

    assert reply == chat.ChatReply(text="a red square")
    assert captured["api_key"] == "test-key"
    assert captured["base_url"] == "http://localhost:9999/v1"
    assert captured["model"] == "my-vision-model"
    # Tools switched off: `tools` is left out, not sent empty.
    assert "tools" not in captured

    messages = captured["messages"]
    assert messages[0] == {"role": "assistant", "content": "earlier reply"}
    last = messages[-1]
    assert last["role"] == "user"
    text_part = next(part for part in last["content"] if part["type"] == "text")
    image_part = next(part for part in last["content"] if part["type"] == "image_url")
    assert text_part["text"] == "what is this?"
    assert image_part["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_openai_backend_downscales_a_large_image_before_sending(monkeypatch):
    # A full-resolution camera photo (e.g. 3024x4032, ~12MP) sent as-is to a
    # self-hosted multimodal gateway is a common cause of an opaque 500 --
    # many such servers impose a much smaller max input resolution. The
    # image attached to the outgoing message must be capped, not the raw
    # original bytes.
    import io

    from PIL import Image

    monkeypatch.setenv("LUMINARY_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("LUMINARY_OPENAI_MODEL", "my-vision-model")

    buffer = io.BytesIO()
    Image.new("RGB", (3024, 4032), color="blue").save(buffer, format="JPEG")
    large_photo_bytes = buffer.getvalue()

    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)

            class Choice:
                class Message:
                    content = "a blue rectangle"

                message = Message()

            class Response:
                choices = [Choice()]

            return Response()

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, api_key, base_url):
            self.chat = FakeChat()

    import openai

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)

    chat.ask(large_photo_bytes, "image/jpeg", "what is this?", [])

    image_part = next(part for part in captured["messages"][-1]["content"] if part["type"] == "image_url")
    data_url = image_part["image_url"]["url"]
    assert data_url.startswith("data:image/jpeg;base64,")

    import base64

    sent_bytes = base64.b64decode(data_url.split(",", 1)[1])
    sent_image = Image.open(io.BytesIO(sent_bytes))
    assert max(sent_image.size) <= chat._MAX_CHAT_IMAGE_DIMENSION
    assert len(sent_bytes) < len(large_photo_bytes)


def test_openai_backend_leaves_a_small_image_unchanged(monkeypatch):
    monkeypatch.setenv("LUMINARY_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("LUMINARY_OPENAI_MODEL", "my-vision-model")

    import base64
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (100, 100), color="red").save(buffer, format="JPEG")
    small_photo_bytes = buffer.getvalue()

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
            self.chat = FakeChat()

    import openai

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)

    chat.ask(small_photo_bytes, "image/jpeg", "what is this?", [])

    image_part = next(part for part in captured["messages"][-1]["content"] if part["type"] == "image_url")
    data_url = image_part["image_url"]["url"]
    sent_bytes = base64.b64decode(data_url.split(",", 1)[1])
    assert sent_bytes == small_photo_bytes


def test_openai_backend_falls_back_to_original_bytes_if_not_decodable_as_an_image(monkeypatch):
    # test_openai_backend_sends_image_and_history_to_the_client relies on
    # this fallback too (it uses b"fake-bytes", which isn't a real image).
    monkeypatch.setenv("LUMINARY_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("LUMINARY_OPENAI_MODEL", "my-vision-model")

    photo_bytes, content_type = chat._downscale_for_chat(b"not-an-image", "image/jpeg")
    assert photo_bytes == b"not-an-image"
    assert content_type == "image/jpeg"


class _FakeFlorenceInputs(dict):
    def to(self, device, dtype):
        return self


class _FakeFlorenceProcessor:
    def __init__(self, result):
        self._result = result
        self.calls = {}

    def __call__(self, text, images, return_tensors):
        self.calls["prompt"] = text
        return _FakeFlorenceInputs({"input_ids": "ids", "pixel_values": "pixels"})

    def batch_decode(self, generated_ids, skip_special_tokens):
        return ["<raw-generated-text>"]

    def post_process_generation(self, generated_text, task, image_size):
        self.calls["task"] = task
        return {task: self._result}


class _FakeFlorenceModel:
    def generate(self, **kwargs):
        return "generated-ids"


def _make_jpeg_bytes():
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (100, 100), color="blue").save(buffer, format="JPEG")
    return buffer.getvalue()


def test_locate_object_runs_phrase_grounding(monkeypatch):
    # The one Florence-2 task that takes free text: ground a phrase to
    # image regions, returning bounding boxes -- not a sentence answer.
    processor = _FakeFlorenceProcessor({"bboxes": [[1.2, 3.4, 5.6, 7.8]], "labels": ["the dog"]})
    monkeypatch.setattr(chat, "_load_florence", lambda: (_FakeFlorenceModel(), processor))

    text, boxes = chat.locate_object(_make_jpeg_bytes(), "the dog")

    assert processor.calls["prompt"] == chat._FLORENCE_GROUNDING_TASK + "the dog"
    assert processor.calls["task"] == chat._FLORENCE_GROUNDING_TASK
    assert '"the dog"' in text
    assert "(1, 3)-(6, 8)" in text
    # The chat model needs the size the pixel coordinates are relative to.
    assert "100x100" in text
    # Full-precision coordinates (not the rounded text) travel separately
    # for the frontend to draw an overlay with.
    assert boxes == [{"label": "the dog", "x1": 1.2, "y1": 3.4, "x2": 5.6, "y2": 7.8}]


def test_locate_object_reports_no_match(monkeypatch):
    processor = _FakeFlorenceProcessor({"bboxes": [], "labels": []})
    monkeypatch.setattr(chat, "_load_florence", lambda: (_FakeFlorenceModel(), processor))

    text, boxes = chat.locate_object(_make_jpeg_bytes(), "a unicorn")

    assert "No match" in text
    assert "a unicorn" in text
    assert boxes == []


def test_locate_object_raises_chat_not_configured_without_torch(monkeypatch):
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
        chat.locate_object(b"fake-bytes", "the dog")


# --- The locate_object tool, as the chat model calls it ----------------------


class _FakeFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, call_id, name, arguments, index=0):
        self.id = call_id
        self.index = index
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeResponse:
    def __init__(self, message):
        class Choice:
            pass

        choice = Choice()
        choice.message = message
        self.choices = [choice]


def _install_fake_openai(monkeypatch, responses):
    """Patch openai.OpenAI with a client whose create() returns `responses`
    in order (each one whatever create() should return for that round),
    recording every call's kwargs. Returns that list of recorded calls."""
    monkeypatch.setenv("LUMINARY_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("LUMINARY_OPENAI_MODEL", "my-vision-model")
    calls = []
    remaining = list(responses)

    class FakeCompletions:
        def create(self, **kwargs):
            # The messages list is mutated between rounds -- snapshot it.
            calls.append({**kwargs, "messages": list(kwargs["messages"])})
            return remaining.pop(0)

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, api_key, base_url):
            self.chat = FakeChat()

    import openai

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    return calls


_DOG_BOX = {"label": "the dog", "x1": 1.0, "y1": 2.0, "x2": 3.0, "y2": 4.0}


def _offered_tools(monkeypatch, photo_bytes):
    calls = _install_fake_openai(monkeypatch, [_FakeResponse(_FakeMessage(content="ok"))])
    chat.ask(photo_bytes, "image/jpeg" if photo_bytes else None, "hi", [])
    return [tool["function"]["name"] for tool in calls[0].get("tools", [])]


_SEARCH_TOOL_NAMES = [
    "search_photos_by_filename",
    "search_photos_semantic",
    "search_photos_by_text",
    "list_individuals",
    "search_photos_by_individual",
    "list_tags",
    "search_photos_by_tag",
    "search_list_stats",
    "truncate_search_list",
]


def test_search_tools_are_offered_with_or_without_a_photo(monkeypatch):
    monkeypatch.setattr(chat, "_object_search_available", lambda: True)
    # locate_object needs a photo to look in; the search tools don't.
    assert _offered_tools(monkeypatch, None) == _SEARCH_TOOL_NAMES
    assert _offered_tools(monkeypatch, b"bytes") == _SEARCH_TOOL_NAMES + ["locate_object"]


def test_locate_object_is_not_offered_without_florence(monkeypatch):
    import importlib.util

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert _offered_tools(monkeypatch, b"bytes") == _SEARCH_TOOL_NAMES


def test_no_tools_are_offered_when_switched_off(monkeypatch):
    monkeypatch.setenv("LUMINARY_CHAT_TOOLS", "false")
    assert _offered_tools(monkeypatch, b"bytes") == []


def test_chat_without_a_photo_sends_a_plain_text_message(monkeypatch):
    calls = _install_fake_openai(monkeypatch, [_FakeResponse(_FakeMessage(content="ok"))])

    chat.ask(None, None, "find my beach photos", [])

    assert calls[0]["messages"][-1] == {"role": "user", "content": "find my beach photos"}


def _photo(photo_id, filename, **extra):
    return {"id": photo_id, "filename": filename, "location_name": None, "description": None, **extra}


def _ids(reply):
    return [entry["id"] for entry in reply.search_results]


def _ask_with_tool_calls(monkeypatch, *tool_calls, photo_bytes=None, search_list=None):
    """Run one chat turn in which the model makes `tool_calls` (one round
    each), then answers. `search_list` is the search list an earlier turn
    left. Returns (reply, the recorded create() calls)."""
    responses = [_FakeResponse(_FakeMessage(tool_calls=[call])) for call in tool_calls]
    responses.append(_FakeResponse(_FakeMessage(content="done")))
    calls = _install_fake_openai(monkeypatch, responses)
    reply = chat.ask(photo_bytes, "image/jpeg" if photo_bytes else None, "hi", [], search_list)
    return reply, calls


def test_filename_search_tool_returns_matching_ids(monkeypatch):
    searched = []

    def fake_search(text):
        searched.append(text)
        return [_photo("p1", "vacation/beach.jpg", location_name="Boone", description="A sandy beach.")]

    monkeypatch.setattr(storage, "search_photos_by_filename", fake_search)

    reply, calls = _ask_with_tool_calls(
        monkeypatch, _FakeToolCall("c1", "search_photos_by_filename", '{"text": "beach"}')
    )

    assert searched == ["beach"]
    result = calls[1]["messages"][-1]["content"]
    # The model is told what was found, and that the user can see it.
    assert reply.search_results == [{"id": "p1", "filename": "vacation/beach.jpg", "score": 1.0}]
    assert "Found 1 photo " in result
    assert "holds 1 photo" in result
    assert "- vacation/beach.jpg (100%)" in result
    assert "Search Results" in result
    # ...but not handed the photo's details: the list stays out of its context.
    assert "A sandy beach." not in result


def test_text_search_tool_returns_matching_ids(monkeypatch):
    monkeypatch.setattr(
        storage, "search_photos_by_text", lambda text: [_photo("p2", "a.jpg"), _photo("p3", "b.jpg")]
    )

    reply, _ = _ask_with_tool_calls(monkeypatch, _FakeToolCall("c1", "search_photos_by_text", '{"text": "Alice"}'))

    assert _ids(reply) == ["p2", "p3"]


@pytest.mark.parametrize(
    "arguments,threshold",
    [
        ('{"query": "a party"}', 0.3),  # loose is the default
        ('{"query": "a party", "match_level": "strict"}', 0.7),
        ('{"query": "a party", "match_level": null}', 0.3),
    ],
)
def test_semantic_search_tool_maps_match_level_to_a_threshold(monkeypatch, arguments, threshold):
    searched = []

    def fake_search(query, level_threshold):
        searched.append((query, level_threshold))
        return [_photo("p1", "party.jpg")]

    monkeypatch.setattr(storage, "search_photos", fake_search)

    reply, _ = _ask_with_tool_calls(monkeypatch, _FakeToolCall("c1", "search_photos_semantic", arguments))

    assert searched == [("a party", threshold)]
    assert _ids(reply) == ["p1"]


def test_semantic_search_tool_rejects_an_unknown_match_level(monkeypatch):
    monkeypatch.setattr(storage, "search_photos", lambda *args: pytest.fail("must not search"))

    reply, calls = _ask_with_tool_calls(
        monkeypatch, _FakeToolCall("c1", "search_photos_semantic", '{"query": "x", "match_level": "exact"}')
    )

    assert reply.search_results is None
    assert "match_level" in calls[1]["messages"][-1]["content"]


def test_semantic_search_tool_reports_a_missing_embedding_model(monkeypatch):
    from app import embeddings

    def fake_search(query, threshold):
        raise embeddings.EmbeddingNotConfigured("no torch")

    monkeypatch.setattr(storage, "search_photos", fake_search)

    reply, calls = _ask_with_tool_calls(monkeypatch, _FakeToolCall("c1", "search_photos_semantic", '{"query": "x"}'))

    assert reply.text == "done"
    assert reply.search_results is None
    assert "isn't available" in calls[1]["messages"][-1]["content"]


def test_a_search_with_no_matches_reports_an_empty_result(monkeypatch):
    monkeypatch.setattr(storage, "search_photos_by_text", lambda text: [])

    reply, calls = _ask_with_tool_calls(monkeypatch, _FakeToolCall("c1", "search_photos_by_text", '{"text": "zzz"}'))

    # [] ("searched, nothing matched") is distinct from None ("didn't search").
    assert reply.search_results == []
    assert "No photos matched" in calls[1]["messages"][-1]["content"]


def test_semantic_matches_are_scored_by_similarity_and_listed_best_first(monkeypatch):
    photos = [
        _photo("p1", "a.jpg", similarity=0.9),
        _photo("p2", "b.jpg", similarity=0.6),
        _photo("p3", "c.jpg", similarity=0.4),
    ]
    monkeypatch.setattr(storage, "search_photos", lambda query, threshold: list(reversed(photos)))

    reply, calls = _ask_with_tool_calls(monkeypatch, _FakeToolCall("c1", "search_photos_semantic", '{"query": "x"}'))

    assert [(entry["id"], entry["score"]) for entry in reply.search_results] == [("p1", 0.9), ("p2", 0.6), ("p3", 0.4)]
    result = calls[1]["messages"][-1]["content"]
    assert "highest 90%, median 60%, lowest 40%" in result


def _semantic(monkeypatch, scores):
    """Fake storage.search_photos returning photo p<n> per score, honouring
    the `photo_ids` restriction a filter-mode search passes."""
    photos = [_photo(f"p{n}", f"photo-{n}.jpg", similarity=score) for n, score in enumerate(scores, start=1)]

    def fake_search(query, threshold, photo_ids=None):
        return [photo for photo in photos if photo_ids is None or photo["id"] in photo_ids]

    monkeypatch.setattr(storage, "search_photos", fake_search)


def test_filter_mode_keeps_only_listed_photos_that_also_match(monkeypatch):
    _semantic(monkeypatch, [0.9, 0.6, 0.4])
    restricted_to = []

    def fake_individual(name, photo_ids=None):
        restricted_to.append(photo_ids)
        return ["Alice"], [_photo("p2", "photo-2.jpg"), _photo("p3", "photo-3.jpg")]

    monkeypatch.setattr(storage, "search_photos_by_individual", fake_individual)

    reply, calls = _ask_with_tool_calls(
        monkeypatch,
        _FakeToolCall("c1", "search_photos_semantic", '{"query": "beach"}'),
        _FakeToolCall("c2", "search_photos_by_individual", '{"name": "alice", "mode": "filter"}'),
    )

    # The second search only looked among the photos already in the list...
    assert restricted_to == [["p1", "p2", "p3"]]
    # ...and a photo in both keeps the weaker of its two scores.
    assert [(entry["id"], entry["score"]) for entry in reply.search_results] == [("p2", 0.6), ("p3", 0.4)]
    assert "Of the 3 photos in the search list, 2 also matched" in calls[-1]["messages"][-1]["content"]


def test_filter_mode_that_matches_nothing_leaves_the_list_unchanged(monkeypatch):
    _semantic(monkeypatch, [0.9, 0.6])
    monkeypatch.setattr(storage, "search_photos_by_tag", lambda name, photo_ids=None: (["trip"], []))

    reply, calls = _ask_with_tool_calls(
        monkeypatch,
        _FakeToolCall("c1", "search_photos_semantic", '{"query": "beach"}'),
        _FakeToolCall("c2", "search_photos_by_tag", '{"name": "trip", "mode": "filter"}'),
    )

    assert _ids(reply) == ["p1", "p2"]
    assert "left unchanged" in calls[-1]["messages"][-1]["content"]


def test_filter_mode_with_an_empty_list_does_not_search(monkeypatch):
    monkeypatch.setattr(storage, "search_photos_by_text", lambda *args, **kwargs: pytest.fail("must not search"))

    reply, calls = _ask_with_tool_calls(
        monkeypatch, _FakeToolCall("c1", "search_photos_by_text", '{"text": "x", "mode": "filter"}')
    )

    assert reply.search_results is None
    assert "nothing to filter" in calls[-1]["messages"][-1]["content"]


def test_add_mode_merges_matches_into_the_list(monkeypatch):
    _semantic(monkeypatch, [0.9, 0.4])
    monkeypatch.setattr(
        storage, "search_photos_by_filename", lambda text: [_photo("p2", "photo-2.jpg"), _photo("p9", "z.jpg")]
    )

    reply, calls = _ask_with_tool_calls(
        monkeypatch,
        _FakeToolCall("c1", "search_photos_semantic", '{"query": "beach"}'),
        _FakeToolCall("c2", "search_photos_by_filename", '{"text": "z", "mode": "add"}'),
    )

    # p2 was in both: it keeps the better score. Best score first overall.
    assert [(entry["id"], entry["score"]) for entry in reply.search_results] == [("p2", 1.0), ("p9", 1.0), ("p1", 0.9)]
    assert "1 of them were not already in the search list" in calls[-1]["messages"][-1]["content"]


def test_an_unknown_mode_is_rejected(monkeypatch):
    monkeypatch.setattr(storage, "search_photos_by_text", lambda *args, **kwargs: pytest.fail("must not search"))

    reply, calls = _ask_with_tool_calls(
        monkeypatch, _FakeToolCall("c1", "search_photos_by_text", '{"text": "x", "mode": "narrow"}')
    )

    assert reply.search_results is None
    assert '"mode" must be one of' in calls[-1]["messages"][-1]["content"]


_PREVIOUS_LIST = [
    {"id": "p3", "filename": "c.jpg", "score": 0.4},
    {"id": "p1", "filename": "a.jpg", "score": 0.9},
    {"id": "p2", "filename": "b.jpg", "score": 0.6},
    {"id": "p4", "filename": "d.jpg", "score": 0.3},
]


@pytest.mark.parametrize("score", ["50", "0.5"])  # a percentage, or the fraction a model may write instead
def test_stats_tool_describes_the_list_from_an_earlier_turn(monkeypatch, score):
    reply, calls = _ask_with_tool_calls(
        monkeypatch,
        _FakeToolCall("c1", "search_list_stats", f'{{"score": {score}}}'),
        search_list=_PREVIOUS_LIST,
    )

    result = calls[-1]["messages"][-1]["content"]
    assert "holds 4 photos" in result
    assert "highest 90%, median 50%, lowest 30%" in result
    assert "2 at or above 50%, 2 below." in result
    # Looking isn't changing: nothing new for the gallery to show.
    assert reply.search_results is None


def test_stats_tool_without_a_score_or_a_list(monkeypatch):
    _, calls = _ask_with_tool_calls(monkeypatch, _FakeToolCall("c1", "search_list_stats", "{}"))

    assert calls[-1]["messages"][-1]["content"] == "The search list is empty."


def test_truncate_tool_drops_entries_below_the_score(monkeypatch):
    reply, calls = _ask_with_tool_calls(
        monkeypatch,
        _FakeToolCall("c1", "truncate_search_list", '{"min_score": 50}'),
        search_list=_PREVIOUS_LIST,
    )

    assert [(entry["id"], entry["score"]) for entry in reply.search_results] == [("p1", 0.9), ("p2", 0.6)]
    result = calls[-1]["messages"][-1]["content"]
    assert "Removed 2 photos scoring below 50%" in result
    assert "holds 2 photos" in result


def test_truncate_tool_never_empties_the_list(monkeypatch):
    reply, calls = _ask_with_tool_calls(
        monkeypatch,
        _FakeToolCall("c1", "truncate_search_list", '{"min_score": 95}'),
        search_list=_PREVIOUS_LIST,
    )

    assert reply.search_results is None
    assert "nothing was removed" in calls[-1]["messages"][-1]["content"]


@pytest.mark.parametrize("arguments", ["{}", '{"min_score": "high"}', '{"min_score": 250}', '{"min_score": true}'])
def test_truncate_tool_rejects_a_bad_score(monkeypatch, arguments):
    reply, calls = _ask_with_tool_calls(
        monkeypatch, _FakeToolCall("c1", "truncate_search_list", arguments), search_list=_PREVIOUS_LIST
    )

    assert reply.search_results is None
    assert '"min_score" must be a number' in calls[-1]["messages"][-1]["content"]


def test_a_later_search_can_filter_the_list_from_an_earlier_turn(monkeypatch):
    monkeypatch.setattr(
        storage, "search_photos_by_text", lambda text, photo_ids=None: [_photo("p2", "b.jpg"), _photo("p3", "c.jpg")]
    )

    reply, _ = _ask_with_tool_calls(
        monkeypatch,
        _FakeToolCall("c1", "search_photos_by_text", '{"text": "boone", "mode": "filter"}'),
        search_list=_PREVIOUS_LIST,
    )

    assert [(entry["id"], entry["score"]) for entry in reply.search_results] == [("p2", 0.6), ("p3", 0.4)]


def test_a_later_empty_search_does_not_wipe_an_earlier_searchs_matches(monkeypatch):
    monkeypatch.setattr(storage, "search_photos_by_filename", lambda text: [_photo("p1", "a.jpg")])
    monkeypatch.setattr(storage, "search_photos_by_text", lambda text: [])

    reply, _ = _ask_with_tool_calls(
        monkeypatch,
        _FakeToolCall("c1", "search_photos_by_filename", '{"text": "a"}'),
        _FakeToolCall("c2", "search_photos_by_text", '{"text": "zzz"}'),
    )

    assert _ids(reply) == ["p1"]


def test_a_later_search_with_matches_replaces_an_earlier_one(monkeypatch):
    monkeypatch.setattr(storage, "search_photos_by_filename", lambda text: [_photo("p1", "a.jpg")])
    monkeypatch.setattr(storage, "search_photos_by_text", lambda text: [_photo("p2", "b.jpg")])

    reply, _ = _ask_with_tool_calls(
        monkeypatch,
        _FakeToolCall("c1", "search_photos_by_filename", '{"text": "a"}'),
        _FakeToolCall("c2", "search_photos_by_text", '{"text": "b"}'),
    )

    assert _ids(reply) == ["p2"]


def test_a_long_result_list_is_only_previewed_to_the_model(monkeypatch):
    photos = [_photo(f"p{n}", f"photo-{n}.jpg") for n in range(25)]
    monkeypatch.setattr(storage, "search_photos_by_text", lambda text: photos)

    reply, calls = _ask_with_tool_calls(monkeypatch, _FakeToolCall("c1", "search_photos_by_text", '{"text": "photo"}'))

    # Every match reaches the user; the model is told how many there are
    # and shown only the first few names.
    assert len(reply.search_results) == 25
    result = calls[1]["messages"][-1]["content"]
    assert "Found 25 photos" in result
    assert result.count("\n- ") == chat._SEARCH_PREVIEW_COUNT
    assert "photo-24.jpg" not in result


@pytest.mark.parametrize(
    "tool,storage_function,kind",
    [("list_individuals", "list_individual_names", "individuals"), ("list_tags", "list_tag_names", "tags")],
)
def test_list_tools_give_the_model_every_name_with_its_photo_count(monkeypatch, tool, storage_function, kind):
    entries = [{"name": "Alice", "photo_count": 2}, {"name": "Bob", "photo_count": 1}]
    monkeypatch.setattr(storage, storage_function, lambda: entries)

    reply, calls = _ask_with_tool_calls(monkeypatch, _FakeToolCall("c1", tool, "{}"))

    assert calls[1]["messages"][-1]["content"] == f"2 {kind}:\n- Alice (2 photos)\n- Bob (1 photo)"
    # Listing isn't a photo search: the gallery is left alone.
    assert reply.search_results is None


def test_list_tools_say_so_when_there_is_nothing_to_list(monkeypatch):
    monkeypatch.setattr(storage, "list_tag_names", lambda: [])

    _, calls = _ask_with_tool_calls(monkeypatch, _FakeToolCall("c1", "list_tags", ""))

    assert "no tags" in calls[1]["messages"][-1]["content"]


@pytest.mark.parametrize(
    "tool,storage_function",
    [
        ("search_photos_by_individual", "search_photos_by_individual"),
        ("search_photos_by_tag", "search_photos_by_tag"),
    ],
)
def test_named_search_tools_return_matching_ids(monkeypatch, tool, storage_function):
    searched = []

    def fake_search(name):
        searched.append(name)
        return ["Alice Smith"], [_photo("p1", "a.jpg"), _photo("p2", "b.jpg")]

    monkeypatch.setattr(storage, storage_function, fake_search)

    reply, calls = _ask_with_tool_calls(monkeypatch, _FakeToolCall("c1", tool, '{"name": "alice"}'))

    assert searched == ["alice"]
    assert _ids(reply) == ["p1", "p2"]
    # The model is told which name its text actually matched.
    assert "Alice Smith" in calls[1]["messages"][-1]["content"]


def test_a_named_search_for_someone_tagged_in_nothing_is_an_empty_result(monkeypatch):
    monkeypatch.setattr(storage, "search_photos_by_individual", lambda name: (["Alice"], []))

    reply, _ = _ask_with_tool_calls(monkeypatch, _FakeToolCall("c1", "search_photos_by_individual", '{"name": "alice"}'))

    assert reply.search_results == []


def test_a_named_search_for_an_unknown_name_is_not_a_search(monkeypatch):
    monkeypatch.setattr(storage, "search_photos_by_tag", lambda name: ([], []))

    reply, calls = _ask_with_tool_calls(monkeypatch, _FakeToolCall("c1", "search_photos_by_tag", '{"name": "nope"}'))

    # The gallery isn't switched to an empty result for a name that doesn't
    # exist; the model is pointed at the list instead.
    assert reply.search_results is None
    assert "list_tags" in calls[1]["messages"][-1]["content"]


def test_an_unknown_tool_is_reported_to_the_model(monkeypatch):
    reply, calls = _ask_with_tool_calls(monkeypatch, _FakeToolCall("c1", "delete_everything", "{}"))

    assert reply.text == "done"
    assert "Unknown tool" in calls[1]["messages"][-1]["content"]


# --- The search tools' queries (real database) -------------------------------


def _index(client, tmp_path, *filenames):
    for filename in filenames:
        write_image(tmp_path / "photos" / filename)
    sync_and_wait(client)
    return {photo["filename"]: photo["id"] for photo in client.get("/api/photos").json()["photos"]}


def _set(photo_id, **columns):
    from app.database import get_connection

    assignments = ", ".join(f"{column} = %s" for column in columns)
    with get_connection() as conn:
        conn.execute(f"UPDATE photos SET {assignments} WHERE id = %s", (*columns.values(), photo_id))
        conn.commit()


def _filenames(photos):
    return sorted(photo["filename"] for photo in photos)


def test_search_photos_by_filename_matches_name_and_path_case_insensitively(client, tmp_path):
    _index(client, tmp_path, "vacation/2019/Beach.jpg", "vacation/2020/hills.jpg", "IMG_1234.jpg", "IMGX1234.jpg")

    assert _filenames(storage.search_photos_by_filename("beach")) == ["vacation/2019/Beach.jpg"]
    assert _filenames(storage.search_photos_by_filename("VACATION")) == [
        "vacation/2019/Beach.jpg",
        "vacation/2020/hills.jpg",
    ]
    # Every word must match, in any order.
    assert _filenames(storage.search_photos_by_filename("hills vacation")) == ["vacation/2020/hills.jpg"]
    # "_" is a literal underscore, not LIKE's any-character wildcard.
    assert _filenames(storage.search_photos_by_filename("IMG_")) == ["IMG_1234.jpg"]
    assert storage.search_photos_by_filename("nothing-like-this") == []
    assert storage.search_photos_by_filename("   ") == []


def test_filename_and_text_searches_can_be_restricted_to_a_set_of_photos(client, tmp_path):
    ids = _index(client, tmp_path, "beach/one.jpg", "beach/two.jpg", "beach/three.jpg")
    some = [ids["beach/one.jpg"], ids["beach/three.jpg"]]

    assert _filenames(storage.search_photos_by_filename("beach", photo_ids=some)) == ["beach/one.jpg", "beach/three.jpg"]
    assert _filenames(storage.search_photos_by_text("beach", photo_ids=some)) == ["beach/one.jpg", "beach/three.jpg"]
    # An empty set is "none of them", not "no restriction".
    assert storage.search_photos_by_filename("beach", photo_ids=[]) == []
    assert storage.search_photos_by_text("beach", photo_ids=[]) == []
    assert len(storage.search_photos_by_text("beach", photo_ids=None)) == 3


def test_search_photos_by_filename_ignores_other_fields(client, tmp_path):
    ids = _index(client, tmp_path, "a.jpg")
    _set(ids["a.jpg"], description="A sandy beach.")

    assert storage.search_photos_by_filename("beach") == []
    assert _filenames(storage.search_photos_by_text("beach")) == ["a.jpg"]


def test_search_photos_by_text_looks_across_every_text_field(client, tmp_path):
    from app import people, tags

    ids = _index(client, tmp_path, "desc.jpg", "caption.jpg", "place.jpg", "trip/name.jpg", "person.jpg", "tag.jpg")
    _set(ids["desc.jpg"], description="Kids at a Lighthouse.")
    _set(ids["caption.jpg"], vlm_description="a lighthouse on a cliff")
    _set(ids["place.jpg"], location_name="Lighthouse Point, Florida")

    individual = people.create_individual("Lighthouse Larry")
    from app.database import get_connection

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO faces (id, photo_id, x1, y1, x2, y2, person_id) VALUES ('f1', %s, 0, 0, 1, 1, %s)",
            (ids["person.jpg"], individual["id"]),
        )
        conn.commit()
    tag = tags.create_tag("lighthouses")
    tags.tag_photo(ids["tag.jpg"], tag["id"])

    assert _filenames(storage.search_photos_by_text("LIGHTHOUSE")) == [
        "caption.jpg",
        "desc.jpg",
        "person.jpg",
        "place.jpg",
        "tag.jpg",
    ]
    assert _filenames(storage.search_photos_by_text("trip")) == ["trip/name.jpg"]
    # Each word may match in a different field.
    assert _filenames(storage.search_photos_by_text("florida place")) == ["place.jpg"]
    assert storage.search_photos_by_text("florida desc") == []
    assert storage.search_photos_by_text("") == []


def _tag_face(face_id, photo_id, person_id):
    from app.database import get_connection

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO faces (id, photo_id, x1, y1, x2, y2, person_id) VALUES (%s, %s, 0, 0, 1, 1, %s)",
            (face_id, photo_id, person_id),
        )
        conn.commit()


def test_individual_listing_and_search(client, tmp_path):
    from app import people

    ids = _index(client, tmp_path, "one.jpg", "two.jpg", "three.jpg")
    alice = people.create_individual("Alice")
    alice_jones = people.create_individual("Alice Jones")
    people.create_individual("Bob")
    # Two of Alice's faces in one photo still count as one photo.
    _tag_face("f1", ids["one.jpg"], alice["id"])
    _tag_face("f2", ids["one.jpg"], alice["id"])
    _tag_face("f3", ids["two.jpg"], alice["id"])
    _tag_face("f4", ids["three.jpg"], alice_jones["id"])

    assert storage.list_individual_names() == [
        {"name": "Alice", "photo_count": 2},
        {"name": "Alice Jones", "photo_count": 1},
        {"name": "Bob", "photo_count": 0},
    ]

    # An exact name (ignoring case) wins over names that merely contain it.
    names, photos = storage.search_photos_by_individual("alice")
    assert names == ["Alice"]
    assert _filenames(photos) == ["one.jpg", "two.jpg"]

    # Otherwise part of a name is enough.
    names, photos = storage.search_photos_by_individual("jones")
    assert names == ["Alice Jones"]
    assert _filenames(photos) == ["three.jpg"]

    assert storage.search_photos_by_individual("bob") == (["Bob"], [])
    assert storage.search_photos_by_individual("nobody") == ([], [])

    # Restricted to a set of photos (a filter on the chat's search list).
    names, photos = storage.search_photos_by_individual("alice", photo_ids=[ids["two.jpg"], ids["three.jpg"]])
    assert names == ["Alice"]
    assert _filenames(photos) == ["two.jpg"]


def test_tag_listing_and_search(client, tmp_path):
    from app import tags

    ids = _index(client, tmp_path, "one.jpg", "two.jpg", "three.jpg")
    favorites = tags.create_tag("Favorites")
    trips = tags.create_tag("Road trips")
    tags.create_tag("unused")
    tags.tag_photo(ids["one.jpg"], favorites["id"])
    tags.tag_photo(ids["two.jpg"], favorites["id"])
    tags.tag_photo(ids["three.jpg"], trips["id"])

    assert storage.list_tag_names() == [
        {"name": "Favorites", "photo_count": 2},
        {"name": "Road trips", "photo_count": 1},
        {"name": "unused", "photo_count": 0},
    ]

    names, photos = storage.search_photos_by_tag("FAVORITES")
    assert names == ["Favorites"]
    assert _filenames(photos) == ["one.jpg", "two.jpg"]

    names, photos = storage.search_photos_by_tag("trips")
    assert names == ["Road trips"]
    assert _filenames(photos) == ["three.jpg"]

    assert storage.search_photos_by_tag("nope") == ([], [])

    names, photos = storage.search_photos_by_tag("favorites", photo_ids=[ids["two.jpg"], ids["three.jpg"]])
    assert names == ["Favorites"]
    assert _filenames(photos) == ["two.jpg"]


# --- The chat routes ---------------------------------------------------------


def test_library_chat_needs_no_photo(client, monkeypatch):
    captured = {}

    def fake_ask(photo_bytes, content_type, message, history, search_list=None, **kwargs):
        captured["args"] = (photo_bytes, content_type, message, search_list)
        return iter([{"search_results": [{"id": "p1", "filename": "a.jpg", "score": 1.0}]}, {"text": "Found one."}])

    monkeypatch.setattr(chat, "ask_events", fake_ask)

    response = client.post("/api/chat", json={"message": "find beach photos"})

    assert response.status_code == 200
    assert _events(response) == [{"search_results": [{"id": "p1", "filename": "a.jpg", "score": 1.0}]}, {"text": "Found one."}]
    assert captured["args"] == (None, None, "find beach photos", None)


def test_library_chat_returns_503_when_not_configured(client, monkeypatch):
    def fake_ask(*args, **kwargs):
        raise chat.ChatNotConfigured("set LUMINARY_OPENAI_API_KEY")

    monkeypatch.setattr(chat, "ask_events", fake_ask)

    assert client.post("/api/chat", json={"message": "hi"}).status_code == 503


def test_chat_model_can_call_locate_object_and_then_answer(monkeypatch):
    monkeypatch.setattr(chat, "_object_search_available", lambda: True)
    searched = []

    def fake_locate(photo_bytes, phrase):
        searched.append((photo_bytes, phrase))
        return "found the dog at (1, 2)-(3, 4)", [_DOG_BOX]

    monkeypatch.setattr(chat, "locate_object", fake_locate)
    calls = _install_fake_openai(
        monkeypatch,
        [
            _FakeResponse(_FakeMessage(tool_calls=[_FakeToolCall("call-1", "locate_object", '{"object": "the dog"}')])),
            _FakeResponse(_FakeMessage(content="The dog is in the corner.")),
        ],
    )

    reply = chat.ask(b"original-bytes", "image/jpeg", "where is the dog?", [])

    assert reply == chat.ChatReply(text="The dog is in the corner.", boxes=[_DOG_BOX])
    # The search runs on the original bytes, with the model's own phrase.
    assert searched == [(b"original-bytes", "the dog")]
    assert chat._LOCATE_OBJECT_TOOL in calls[0]["tools"]
    # Round two carries the model's call and the tool's result back to it.
    assistant, tool = calls[1]["messages"][-2:]
    assert assistant["role"] == "assistant"
    assert assistant["tool_calls"][0]["id"] == "call-1"
    assert assistant["tool_calls"][0]["function"] == {"name": "locate_object", "arguments": '{"object": "the dog"}'}
    assert tool == {"role": "tool", "tool_call_id": "call-1", "content": "found the dog at (1, 2)-(3, 4)"}


def test_chat_model_answers_directly_without_calling_the_tool(monkeypatch):
    monkeypatch.setattr(chat, "_object_search_available", lambda: True)
    monkeypatch.setattr(chat, "locate_object", lambda *args: pytest.fail("the tool must not run"))
    calls = _install_fake_openai(monkeypatch, [_FakeResponse(_FakeMessage(content="A beach at sunset."))])

    reply = chat.ask(b"bytes", "image/jpeg", "what is this?", [])

    assert reply == chat.ChatReply(text="A beach at sunset.")
    assert len(calls) == 1


@pytest.mark.parametrize(
    "error,expected",
    [(chat.ChatNotConfigured("no torch"), "isn't available"), (RuntimeError("boom"), "failed")],
)
def test_a_failed_object_search_is_reported_to_the_model_not_raised(monkeypatch, error, expected):
    monkeypatch.setattr(chat, "_object_search_available", lambda: True)

    def fake_locate(photo_bytes, phrase):
        raise error

    monkeypatch.setattr(chat, "locate_object", fake_locate)
    calls = _install_fake_openai(
        monkeypatch,
        [
            _FakeResponse(_FakeMessage(tool_calls=[_FakeToolCall("call-1", "locate_object", '{"object": "x"}')])),
            _FakeResponse(_FakeMessage(content="I couldn't search, but I can see a dog.")),
        ],
    )

    reply = chat.ask(b"bytes", "image/jpeg", "where is the dog?", [])

    assert reply.text == "I couldn't search, but I can see a dog."
    assert reply.boxes == []
    assert expected in calls[1]["messages"][-1]["content"]


@pytest.mark.parametrize("arguments", ["not json", "{}", '{"object": "  "}', '{"object": 5}', "[]"])
def test_malformed_tool_arguments_are_reported_to_the_model(monkeypatch, arguments):
    monkeypatch.setattr(chat, "_object_search_available", lambda: True)
    monkeypatch.setattr(chat, "locate_object", lambda *args: pytest.fail("the tool must not run"))
    calls = _install_fake_openai(
        monkeypatch,
        [
            _FakeResponse(_FakeMessage(tool_calls=[_FakeToolCall("call-1", "locate_object", arguments)])),
            _FakeResponse(_FakeMessage(content="done")),
        ],
    )

    assert chat.ask(b"bytes", "image/jpeg", "hi", []).text == "done"
    assert "Invalid arguments" in calls[1]["messages"][-1]["content"]


def test_locate_object_without_an_open_photo_is_reported_to_the_model(monkeypatch):
    # Not offered without a photo, but a model can still ask for it.
    monkeypatch.setattr(chat, "locate_object", lambda *args: pytest.fail("the tool must not run"))

    reply, calls = _ask_with_tool_calls(monkeypatch, _FakeToolCall("c1", "locate_object", '{"object": "dog"}'))

    assert reply.boxes == []
    assert "No photo is open" in calls[1]["messages"][-1]["content"]


def test_the_last_round_is_made_without_tools_so_a_turn_cannot_loop(monkeypatch):
    monkeypatch.setattr(chat, "_object_search_available", lambda: True)
    monkeypatch.setattr(chat, "locate_object", lambda photo_bytes, phrase: ("nothing", []))

    def tool_round(n):
        return _FakeResponse(_FakeMessage(tool_calls=[_FakeToolCall(f"call-{n}", "locate_object", '{"object": "x"}')]))

    responses = [tool_round(n) for n in range(chat._MAX_CHAT_ROUNDS - 1)]
    responses.append(_FakeResponse(_FakeMessage(content="final answer")))
    calls = _install_fake_openai(monkeypatch, responses)

    assert chat.ask(b"bytes", "image/jpeg", "hi", []).text == "final answer"
    assert len(calls) == chat._MAX_CHAT_ROUNDS
    assert all("tools" in call for call in calls[:-1])
    assert "tools" not in calls[-1]


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


def test_should_stream_follows_the_setting(monkeypatch):
    monkeypatch.delenv("LUMINARY_OPENAI_STREAMING", raising=False)
    assert chat.should_stream() is False
    monkeypatch.setenv("LUMINARY_OPENAI_STREAMING", "true")
    assert chat.should_stream() is True


def test_ask_stream_raises_chat_not_configured_synchronously(monkeypatch):
    # Must raise as soon as ask_stream() is called, not only once the
    # returned generator is first iterated -- see _prepare_openai_call's
    # docstring for why (the router needs this to still produce a normal
    # HTTPException instead of a broken/empty streaming response).
    monkeypatch.delenv("LUMINARY_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LUMINARY_OPENAI_MODEL", raising=False)

    with pytest.raises(chat.ChatNotConfigured):
        chat.ask_stream(b"fake-bytes", "image/jpeg", "hi", [])


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

    chunks = list(chat.ask_stream(b"fake-bytes", "image/jpeg", "what is this?", []))

    assert chunks == [{"text": "Hello"}, {"text": ", "}, {"text": "world"}]
    assert captured["stream"] is True
    assert captured["model"] == "my-vision-model"


def test_ask_stream_runs_a_streamed_tool_call_then_streams_the_answer(monkeypatch):
    monkeypatch.setattr(chat, "_object_search_available", lambda: True)
    monkeypatch.setattr(chat, "locate_object", lambda photo_bytes, phrase: (f"found {phrase}", [_DOG_BOX]))

    class Delta:
        def __init__(self, content=None, tool_calls=None):
            self.content = content
            self.tool_calls = tool_calls

    class Chunk:
        def __init__(self, delta=None):
            class Choice:
                pass

            self.choices = []
            if delta is not None:
                choice = Choice()
                choice.delta = delta
                self.choices = [choice]

    def fragment(call_id, name, arguments):
        return _FakeToolCall(call_id, name, arguments)

    calls = _install_fake_openai(
        monkeypatch,
        [
            # The call's id/name arrive once, its arguments JSON in pieces.
            iter(
                [
                    Chunk(Delta(tool_calls=[fragment("call-1", "locate_object", "")])),
                    Chunk(Delta(tool_calls=[fragment(None, None, '{"object": ')])),
                    Chunk(Delta(tool_calls=[fragment(None, None, '"the dog"}')])),
                    Chunk(),  # e.g. a trailing usage-only chunk: no choices
                ]
            ),
            iter([Chunk(Delta("In the "), ), Chunk(Delta("corner."))]),
        ],
    )

    events = list(chat.ask_stream(b"bytes", "image/jpeg", "where is the dog?", []))

    tool = {"index": 0, "name": "locate_object", "arguments": {"object": "the dog"}}
    assert events == [
        {"tool": {**tool, "status": "running"}},
        {"tool": {**tool, "status": "done"}},
        {"boxes": [_DOG_BOX]},
        {"text": "In the "},
        {"text": "corner."},
    ]
    assert calls[1]["messages"][-1] == {"role": "tool", "tool_call_id": "call-1", "content": "found the dog"}
    assert calls[1]["messages"][-2]["tool_calls"][0]["function"]["arguments"] == '{"object": "the dog"}'


def test_ask_stream_reports_search_results_as_an_event(monkeypatch):
    monkeypatch.setattr(storage, "search_photos_by_text", lambda text: [_photo("p1", "a.jpg")])

    class Delta:
        def __init__(self, content=None, tool_calls=None):
            self.content = content
            self.tool_calls = tool_calls

    class Chunk:
        def __init__(self, delta):
            class Choice:
                pass

            choice = Choice()
            choice.delta = delta
            self.choices = [choice]

    _install_fake_openai(
        monkeypatch,
        [
            iter([Chunk(Delta(tool_calls=[_FakeToolCall("c1", "search_photos_by_text", '{"text": "a"}')]))]),
            iter([Chunk(Delta("Found one."))]),
        ],
    )

    events = list(chat.ask_stream(None, None, "find a", []))

    tool = {"index": 0, "name": "search_photos_by_text", "arguments": {"text": "a"}}
    assert events == [
        {"tool": {**tool, "status": "running"}},
        {"tool": {**tool, "status": "done"}},
        {"search_results": [{"id": "p1", "filename": "a.jpg", "score": 1.0}]},
        {"text": "Found one."},
    ]


def test_ask_events_reports_a_tool_call_before_running_it(monkeypatch):
    # The "running" event must be out before the tool starts, so the UI
    # can show a slow search while it is still going.
    ran = []

    def slow_search(text):
        ran.append(text)
        return [_photo("p1", "a.jpg")]

    monkeypatch.setattr(storage, "search_photos_by_text", slow_search)
    monkeypatch.delenv("LUMINARY_OPENAI_STREAMING", raising=False)
    _install_fake_openai(
        monkeypatch,
        [
            _FakeResponse(
                _FakeMessage(
                    tool_calls=[
                        _FakeToolCall("c1", "search_photos_by_text", '{"text": "a"}'),
                        _FakeToolCall("c2", "list_tags", "not json"),
                    ]
                )
            ),
            _FakeResponse(_FakeMessage(content="Found one.")),
        ],
    )

    events = chat.ask_events(None, None, "find a", [])

    search = {"index": 0, "name": "search_photos_by_text", "arguments": {"text": "a"}}
    assert next(events) == {"tool": {**search, "status": "running"}}
    assert ran == []
    assert next(events) == {"tool": {**search, "status": "done"}}
    assert ran == ["a"]
    # Arguments that aren't a JSON object are reported as None; the reply
    # (not streamed here) comes as one text event at the end.
    tags = {"index": 1, "name": "list_tags", "arguments": None}
    assert list(events) == [
        {"tool": {**tags, "status": "running"}},
        {"tool": {**tags, "status": "done"}},
        {"search_results": [{"id": "p1", "filename": "a.jpg", "score": 1.0}]},
        {"text": "Found one."},
    ]


def test_chat_endpoint_streams_events(client, tmp_path, monkeypatch):
    photo_id = _get_synced_photo_id(client, tmp_path)

    def fake_ask_stream(photo_bytes, content_type, message, history, search_list=None, **kwargs):
        return iter([{"text": "Hel"}, {"boxes": [_DOG_BOX]}, {"text": "lo"}])

    monkeypatch.setattr(chat, "ask_events", fake_ask_stream)

    response = client.post(f"/api/photos/{photo_id}/chat", json={"message": "hi"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events == [{"text": "Hel"}, {"boxes": [_DOG_BOX]}, {"text": "lo"}]


def test_chat_endpoint_reports_a_mid_stream_failure_in_band(client, tmp_path, monkeypatch):
    photo_id = _get_synced_photo_id(client, tmp_path)

    def failing_events():
        yield {"text": "Hel"}
        raise RuntimeError("connection dropped")

    monkeypatch.setattr(chat, "ask_events", lambda *args, **kwargs: failing_events())

    response = client.post(f"/api/photos/{photo_id}/chat", json={"message": "hi"})

    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events == [{"text": "Hel"}, {"text": "\n[chat backend failed]"}]


def test_chat_endpoint_still_503s_when_not_configured(client, tmp_path, monkeypatch):
    # The config check in ask_events() must run before the streaming
    # response starts, so a misconfigured backend is still a clean 503 --
    # not a 200 with a broken/empty stream.
    photo_id = _get_synced_photo_id(client, tmp_path)

    def fake_ask_stream(*args, **kwargs):
        raise chat.ChatNotConfigured("set LUMINARY_OPENAI_API_KEY")

    monkeypatch.setattr(chat, "ask_events", fake_ask_stream)

    response = client.post(f"/api/photos/{photo_id}/chat", json={"message": "hi"})

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")


def test_chat_endpoint_502s_when_the_first_model_call_fails(client, tmp_path, monkeypatch):
    # The first event is read before the response starts, so a failure of
    # the first call to the chat model (wrong model name, server down) is
    # still a real 502, not a 200 with "[chat backend failed]" in it.
    photo_id = _get_synced_photo_id(client, tmp_path)

    def failing_events():
        raise RuntimeError("model not found")
        yield

    monkeypatch.setattr(chat, "ask_events", lambda *args, **kwargs: failing_events())

    response = client.post(f"/api/photos/{photo_id}/chat", json={"message": "hi"})

    assert response.status_code == 502
    assert response.headers["content-type"].startswith("application/json")


def test_ask_events_sends_the_whole_reply_at_once_by_default(monkeypatch):
    monkeypatch.delenv("LUMINARY_OPENAI_STREAMING", raising=False)
    calls = _install_fake_openai(monkeypatch, [_FakeResponse(_FakeMessage(content="a plain reply"))])

    assert list(chat.ask_events(None, None, "hi", [])) == [{"text": "a plain reply"}]
    assert "stream" not in calls[0]


@pytest.mark.parametrize("raw,expected", [(None, True), ("true", True), ("false", False), ("0", False), ("off", False)])
def test_openai_verify_ssl_flag(monkeypatch, raw, expected):
    from app import chat, config

    if raw is None:
        monkeypatch.delenv("LUMINARY_OPENAI_VERIFY_SSL", raising=False)
    else:
        monkeypatch.setenv("LUMINARY_OPENAI_VERIFY_SSL", raw)
    assert config.get_openai_verify_ssl() is expected

    captured = {}

    class FakeOpenAI:
        def __init__(self, api_key, base_url, **kwargs):
            captured.update(kwargs)

    chat.build_openai_client(FakeOpenAI, "key")
    assert ("http_client" in captured) is (not expected)
