import sys
import types

import pytest

from app import chat, enrich, tracing
from test_chat import _FakeMessage, _FakeResponse, _FakeToolCall, _install_fake_openai, _photo


class _FakeLiveSpan:
    def __init__(self, log, name, span_type, parent, inputs, tags):
        self.log = log
        self.name = name
        self.span_type = span_type
        self.parent = parent
        self.inputs = inputs
        self.tags = tags
        self.attributes = {}
        self.error = None
        self.ended = 0

    def set_attributes(self, attributes):
        self.attributes.update(attributes)

    def record_exception(self, exc):
        self.error = exc

    def end(self, outputs=None):
        self.ended += 1
        self.outputs = outputs


def _fake_mlflow(set_experiment=lambda name: None):
    """A stand-in for the mlflow module: records every span started."""
    spans = []

    def start_span_no_context(name, span_type, parent_span=None, inputs=None, tags=None):
        span = _FakeLiveSpan(spans, name, span_type, parent_span, inputs, tags)
        spans.append(span)
        return span

    module = types.ModuleType("mlflow")
    module.set_experiment = set_experiment
    module.start_span_no_context = start_span_no_context
    module.spans = spans
    return module


@pytest.fixture
def traced(monkeypatch):
    """Tracing switched on, against a fake mlflow."""
    module = _fake_mlflow()
    monkeypatch.setattr(tracing, "_mlflow", module)
    return module.spans


@pytest.fixture(autouse=True)
def _not_started(monkeypatch):
    monkeypatch.setattr(tracing, "_started", False)
    monkeypatch.setattr(tracing, "_mlflow", None)


def test_tracing_is_off_without_a_tracking_uri(monkeypatch):
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    monkeypatch.setattr(tracing.threading, "Thread", lambda **kwargs: pytest.fail("must not start a thread"))

    tracing.configure_tracing()

    # Spans handed out while it's off do nothing, and don't raise.
    span = tracing.start_trace("chat", "AGENT", inputs={"message": "hi"})
    child = span.child("tool", "TOOL")
    child.set_attributes({"a": 1})
    child.end(outputs="x")
    span.end(error=RuntimeError("boom"))


def test_configure_tracing_does_not_connect_on_the_calling_thread(monkeypatch):
    # Startup must not wait on (or fail because of) the tracking server.
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://mlflow.example")
    started = []

    class FakeThread:
        def __init__(self, target, args, name, daemon):
            assert target is tracing._connect and daemon is True
            self.args = args

        def start(self):
            started.append(self.args)

    monkeypatch.setattr(tracing.threading, "Thread", FakeThread)

    tracing.configure_tracing()
    tracing.configure_tracing()  # a second call doesn't start a second thread

    assert started == [("http://mlflow.example",)]


def test_a_failed_connection_is_logged_and_retried_not_raised(monkeypatch, caplog):
    attempts = []

    def set_experiment(name):
        attempts.append(name)
        if len(attempts) < 3:
            raise RuntimeError("connection refused")

    module = _fake_mlflow(set_experiment)
    monkeypatch.setitem(sys.modules, "mlflow", module)
    monkeypatch.setenv("MLFLOW_EXPERIMENT_NAME", "photos")
    waits = []

    def wait(seconds):
        # Still off between attempts.
        assert tracing._mlflow is None
        assert tracing.start_trace("chat", "AGENT")._live is None
        waits.append(seconds)

    with caplog.at_level("ERROR", logger="app.tracing"):
        tracing._connect("http://user:secret@mlflow.example", wait=wait)

    assert attempts == ["photos", "photos", "photos"]
    assert waits == [30, 60]
    errors = [record.getMessage() for record in caplog.records if record.levelname == "ERROR"]
    assert len(errors) == 2
    assert "connection refused" in errors[0]
    assert "secret" not in errors[0]
    assert tracing._mlflow is module


def test_a_missing_mlflow_package_is_a_failed_connection(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "mlflow", None)  # makes `import mlflow` raise ImportError

    with caplog.at_level("ERROR", logger="app.tracing"):
        assert tracing._try_connect("http://mlflow.example", 30) is False

    assert tracing._mlflow is None
    assert "Could not connect to MLflow" in caplog.text


def test_errors_from_mlflow_are_logged_never_raised(monkeypatch, caplog):
    module = _fake_mlflow()

    def broken(**kwargs):
        raise RuntimeError("tracking server went away")

    module.start_span_no_context = broken
    monkeypatch.setattr(tracing, "_mlflow", module)

    with caplog.at_level("ERROR", logger="app.tracing"):
        span = tracing.start_trace("chat", "AGENT")
        span.child("tool", "TOOL").end()
        span.end()
    assert "tracking server went away" in caplog.text

    class BrokenLive:
        def set_attributes(self, attributes):
            raise RuntimeError("bad attribute")

        def end(self, outputs=None):
            raise RuntimeError("bad end")

    span = tracing.Span(BrokenLive())
    span.set_attributes({"a": 1})
    span.end(outputs="x")
    span.end(outputs="x")  # already ended: nothing more is attempted


def test_llm_attributes_include_token_usage_only_when_reported():
    class Usage:
        prompt_tokens = 11
        completion_tokens = 7
        total_tokens = 18

    assert tracing.llm_attributes("gemma", Usage()) == {
        "mlflow.message.format": "openai",
        "mlflow.llm.model": "gemma",
        "mlflow.chat.tokenUsage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
    }
    assert "mlflow.chat.tokenUsage" not in tracing.llm_attributes("gemma", None)
    assert "mlflow.chat.tokenUsage" not in tracing.llm_attributes("gemma", object())


def test_a_chat_turn_is_one_trace_with_a_span_per_round_and_tool_call(monkeypatch, traced):
    from app import storage

    monkeypatch.setattr(storage, "search_photos_by_text", lambda text: [_photo("p1", "a.jpg")])
    monkeypatch.setattr(chat, "_downscale_for_chat", lambda photo_bytes, content_type: (photo_bytes, content_type))
    _install_fake_openai(
        monkeypatch,
        [
            _FakeResponse(_FakeMessage(tool_calls=[_FakeToolCall("c1", "search_photos_by_text", '{"text": "a"}')])),
            _FakeResponse(_FakeMessage(content="Found one.")),
        ],
    )

    reply = chat.ask(b"x" * 5000, "image/jpeg", "find a", [])

    assert reply.text == "Found one."
    root, first_round, tool, second_round = traced
    assert (root.name, root.span_type, root.parent) == ("chat", "AGENT", None)
    assert root.inputs == {"message": "find a", "history": [], "photo_open": True, "search_list_size": 0}
    assert root.outputs == {"reply": "Found one.", "tool_calls": 1, "boxes": 0, "search_results": 1}
    assert [(span.name, span.span_type, span.parent) for span in traced[1:]] == [
        ("chat.completions", "CHAT_MODEL", root),
        ("search_photos_by_text", "TOOL", root),
        ("chat.completions", "CHAT_MODEL", root),
    ]
    assert all(span.ended == 1 and span.error is None for span in traced)
    assert tool.inputs == {"text": "a"}
    assert tool.outputs.startswith("Found 1 photo")
    assert first_round.outputs["tool_calls"][0]["function"] == {"name": "search_photos_by_text", "arguments": '{"text": "a"}'}
    assert second_round.outputs == {"role": "assistant", "content": "Found one."}
    assert first_round.attributes["mlflow.llm.model"] == "my-vision-model"
    # The photo's bytes stay out of the trace.
    image_url = first_round.inputs["messages"][0]["content"][1]["image_url"]["url"]
    assert image_url.startswith("data:image/jpeg;base64,<") and len(image_url) < 100


def test_a_failed_model_call_is_recorded_on_the_trace_and_still_raised(monkeypatch, traced):
    _install_fake_openai(monkeypatch, [])  # create() has nothing to return: IndexError

    with pytest.raises(IndexError):
        chat.ask(None, None, "hi", [])

    root, model_call = traced
    assert isinstance(root.error, IndexError) and isinstance(model_call.error, IndexError)
    assert root.ended == 1 and model_call.ended == 1


def test_an_abandoned_streamed_turn_still_ends_its_spans(monkeypatch, traced):
    class Delta:
        content = "Hel"
        tool_calls = None

    class Chunk:
        def __init__(self):
            choice = types.SimpleNamespace(delta=Delta())
            self.choices = [choice]

    _install_fake_openai(monkeypatch, [iter([Chunk(), Chunk()])])

    events = chat.ask_stream(None, None, "hi", [])
    assert next(events) == {"text": "Hel"}
    events.close()  # what a dropped connection does to the generator

    assert [span.ended for span in traced] == [1, 1]


def test_a_description_rewrite_is_traced(monkeypatch, traced):
    _install_fake_openai(monkeypatch, [_FakeResponse(_FakeMessage(content=" A beach. "))])
    monkeypatch.setattr(enrich, "_build_prompt", lambda facts: "Describe it.")

    assert enrich._compose("p1", {}) == "A beach."

    (span,) = traced
    assert (span.name, span.span_type, span.tags) == ("enrich_description", "CHAT_MODEL", {"photo.id": "p1"})
    assert span.inputs == {"messages": [{"role": "user", "content": "Describe it."}]}
    assert span.outputs == {"role": "assistant", "content": "A beach."}
    assert span.ended == 1
