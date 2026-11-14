"""MLflow tracing of the app's LLM work: one trace per chat turn (the
model rounds and tool calls inside it as child spans, see chat.py) and one
per description rewrite (enrich.py).

Off unless MLFLOW_TRACKING_URI is set. mlflow reads that variable itself,
along with MLFLOW_TRACKING_AUTH, MLFLOW_WORKSPACE, MLFLOW_TRACKING_TOKEN
and friends -- nothing here passes them on. The experiment is
MLFLOW_EXPERIMENT_NAME, default "luminary".

**Tracing must never fail, or slow down, what it traces.** Three things
follow from that:

- Nothing is traced until `_connect()` has reached the tracking server.
  mlflow's client raises when it can't (`set_experiment`), and while it
  can't, every span start would block the traced request on HTTP retries.
  So the connection is made on a background thread -- startup doesn't wait
  for it -- a failure is logged at ERROR and retried with backoff, and
  until one succeeds `start_trace()` hands out spans that do nothing.
- Every call into mlflow after that is wrapped (`Span._safely`): whatever
  it raises is logged at ERROR and dropped. Spans are exported from
  mlflow's own background queue, so a server that goes away later costs
  log lines, not requests.
- `mlflow` is imported lazily, on that thread, and only when tracing is
  configured: the import alone takes seconds, and the app must keep
  importing and running without the package installed (an ImportError is
  logged like any other failure to connect).

Spans are started with an explicit parent (`Span.child`), never through
mlflow's "current span" context (`mlflow.start_span`, `@mlflow.trace`,
`mlflow.openai.autolog()`): a chat turn is a generator that FastAPI's
StreamingResponse advances from a different worker thread on each step,
and a context variable set on one step isn't there on the next. That is
also why the openai calls aren't autologged -- an autologged span would
start a trace of its own rather than join the turn's -- and doing it by
hand lets chat.py keep the photo's bytes out of the trace.
"""

import logging
import threading
from typing import Any, Optional

from .config import get_mlflow_experiment_name, get_mlflow_tracking_uri, redact_uri

logger = logging.getLogger(__name__)

# How long to wait before trying the tracking server again, doubling after
# each failure up to the maximum.
_RETRY_SECONDS = 30
_MAX_RETRY_SECONDS = 600

_lock = threading.Lock()
_started = False
# The mlflow module, once the tracking server has been reached; None until
# then (and for good if tracing isn't configured).
_mlflow = None


def configure_tracing() -> None:
    """Start connecting to MLflow, if it's configured. Returns at once;
    call it once, at startup."""
    global _started
    uri = get_mlflow_tracking_uri()
    if not uri:
        logger.info("MLflow tracing is off: MLFLOW_TRACKING_URI is not set")
        return
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_connect, args=(uri,), name="mlflow-tracing", daemon=True).start()


def _connect(uri: str, wait=threading.Event().wait) -> None:
    """Keep trying until the tracking server answers, then switch tracing
    on. Runs on its own thread; never raises."""
    delay = _RETRY_SECONDS
    while not _try_connect(uri, delay):
        wait(delay)
        delay = min(delay * 2, _MAX_RETRY_SECONDS)


def _try_connect(uri: str, retry_in: int) -> bool:
    global _mlflow
    experiment = get_mlflow_experiment_name()
    try:
        import mlflow

        # The one call that needs the server: it looks the experiment up,
        # creating it if it's missing, and raises if it can't.
        mlflow.set_experiment(experiment)
    except Exception as exc:  # ImportError included: mlflow isn't installed
        logger.error(
            "Could not connect to MLflow at %s; tracing stays off, trying again in %ds: %s",
            redact_uri(uri),
            retry_in,
            exc,
        )
        return False
    _mlflow = mlflow
    logger.info("MLflow tracing enabled: uri=%s experiment=%s", redact_uri(uri), experiment)
    return True


class Span:
    """A span, or a stand-in that does nothing when tracing is off or the
    span couldn't be started. No method here raises."""

    def __init__(self, live=None):
        self._live = live

    def child(self, name: str, span_type: str, inputs: Any = None) -> "Span":
        if self._live is None:
            return _NO_SPAN
        return _start(name, span_type, inputs, parent=self._live)

    def set_attributes(self, attributes: dict) -> None:
        self._safely(lambda live: live.set_attributes(attributes))

    def end(self, outputs: Any = None, error: Optional[BaseException] = None) -> None:
        """Finish the span; only the first call counts. `error` is what the
        traced work raised, if it did."""

        def finish(live):
            if error is not None:
                live.record_exception(error if isinstance(error, Exception) else repr(error))
            live.end(outputs=outputs)

        self._safely(finish)
        self._live = None

    def _safely(self, call) -> None:
        if self._live is None:
            return
        try:
            call(self._live)
        except Exception as exc:
            logger.error("MLflow tracing failed, span dropped: %s", exc)


_NO_SPAN = Span()


def start_trace(name: str, span_type: str, inputs: Any = None, tags: Optional[dict] = None) -> Span:
    """Start a new trace's root span. Always returns a Span: one that does
    nothing if tracing is off."""
    return _start(name, span_type, inputs, tags=tags)


def _start(name: str, span_type: str, inputs: Any, parent=None, tags: Optional[dict] = None) -> Span:
    mlflow = _mlflow
    if mlflow is None:
        return _NO_SPAN
    try:
        return Span(
            mlflow.start_span_no_context(name=name, span_type=span_type, parent_span=parent, inputs=inputs, tags=tags)
        )
    except Exception as exc:
        logger.error("MLflow tracing failed, span %s not started: %s", name, exc)
        return _NO_SPAN


def llm_attributes(model: str, usage: Any = None) -> dict:
    """Span attributes for one chat-completions call: the conversation is
    in OpenAI's message format (so MLflow renders it as a chat), the model,
    and the token counts if the server reported them (`usage` is the
    response's usage object, or None)."""
    attributes = {"mlflow.message.format": "openai", "mlflow.llm.model": model}
    counts = {
        ours: getattr(usage, theirs, None)
        for ours, theirs in (
            ("input_tokens", "prompt_tokens"),
            ("output_tokens", "completion_tokens"),
            ("total_tokens", "total_tokens"),
        )
    }
    if all(isinstance(count, int) for count in counts.values()):
        attributes["mlflow.chat.tokenUsage"] = counts
    return attributes
