"""The chat assistant: an OpenAI-*compatible* chat endpoint (self-hosted or
not -- LUMINARY_OPENAI_BASE_URL is never assumed to be api.openai.com)
that can call tools of ours. If a photo is open it's attached to the
message, so the model can see it; with none open the chat is about the
library as a whole.

The tools (see _TOOLS below; the model decides when to call one):

- search_photos_by_filename / search_photos_semantic /
  search_photos_by_text / search_photos_by_individual /
  search_photos_by_tag search the library. This is the app's only search
  UI -- there is no search box. Their matches go into the *search list*,
  a scored list of photos kept outside the model's context (see _Turn):
  a search replaces it, filters it or adds to it, and the model is told
  only how many photos it holds and their score statistics. The list
  travels back alongside the reply (ChatReply.search_results) and the web
  UI shows it as the gallery's "Search Results", best score first.
- search_list_stats / truncate_search_list report on that list (count,
  highest/median/lowest score, how many are above or below a given
  score) and drop its entries below a score.
- list_individuals / list_tags tell the model which people and tags
  exist. Their result is only text for the model to answer from.
- locate_object, only offered while a photo is open, finds an object in
  that photo with a local Florence-2 vision-language model (see
  https://huggingface.co/microsoft/Florence-2-base), in-process via
  transformers/torch. The boxes found travel back the same way
  (ChatReply.boxes) for the frontend to draw on the full-size image.

openai and torch/transformers are optional dependencies (openai is light;
the others are not) -- imported lazily inside the functions that need
them, not at module import time, so the API starts and the rest of the
test suite runs without requiring either to be installed or configured.

Florence-2 is not a conversational VQA model: its API is a fixed set of task
tokens (captioning, OCR, object detection, phrase grounding), not free-form
question answering. Exactly one task accepts free text alongside the task
token: <CAPTION_TO_PHRASE_GROUNDING>, which takes a phrase and returns the
bounding box(es) where it appears in the image (see run_example() in the
model card) -- not a natural-language answer. That's why it's a *tool* of
the chat model rather than a chat backend of its own (it used to be one,
picked with a checkbox in the UI).

LUMINARY_CHAT_TOOLS (default on) switches every tool off, for an
OpenAI-compatible server that rejects requests carrying `tools` (e.g. vLLM
started without tool calling enabled); chat then works as plain question
answering, and there is no search.

A turn is reported as a stream of events (ask_events(), what the chat
routes call): each tool call as it starts and finishes, the reply, and
what the tools found. The reply text is one event unless
LUMINARY_OPENAI_STREAMING (off by default, should_stream()) asks the model
for it token by token. ask() is the same loop collected into a ChatReply.
Each turn is also an MLflow trace when tracing is configured (tracing.py).

caption_image() is the one Florence-2 entry point sync.py uses directly
(outside the chat.ask()/ChatReply flow above) to auto-describe a photo at
index time, via <MORE_DETAILED_CAPTION>. It raises ChatNotConfigured
exactly like ask() does, which is how sync.py knows to leave that work for
a later sync rather than fail it.

Face *detection* (as opposed to the chat/captioning above) used to live
here too, built on Florence-2's <OD> (general object detection) task,
filtering to detections whose label mentioned "face". That was confirmed
in practice to miss real faces (partial/angled/small, or in a crowded
photo) regardless of how the task was tuned -- <OD> is a general object
detector that happens to label some boxes "human face," not a model
trained for face detection, and (unlike a real detector) its output has no
per-box confidence score to loosen. It's been replaced by face_detection.py,
which uses facenet-pytorch's MTCNN (a purpose-built, multi-scale face
detector with a real tunable confidence threshold) instead -- see that
module's docstring. sync.py now calls face_detection.detect_faces(), not
anything in this module, for the face-boxes step of its indexing pipeline.
"""

import base64
import importlib.util
import io
import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Optional

from . import embeddings, storage, tracing
from .config import (
    get_chat_tools,
    get_florence_device,
    get_florence_model,
    get_openai_api_key,
    get_openai_base_url,
    get_openai_model,
    get_openai_streaming,
    get_openai_verify_ssl,
)

logger = logging.getLogger(__name__)

# Cap the longest side of the image sent to the openai-compatible endpoint.
# We index full-resolution camera originals (a modern phone photo is
# commonly 10+ megapixels), but self-hosted multimodal servers (vLLM and
# similar) often impose a much smaller max input resolution and fail --
# frequently with an opaque 500, not a clear 400 -- on a photo this large.
_MAX_CHAT_IMAGE_DIMENSION = 2048


class ChatNotConfigured(Exception):
    """Raised when the selected backend is missing required configuration."""


@dataclass
class ChatReply:
    """Return shape of ask(): `text` is always the human-readable reply.

    `boxes` is non-empty only when the model called the locate_object tool
    this turn and it found something -- each is
    {"label": str, "x1"/"y1"/"x2"/"y2": float} in the *original* photo's
    pixel coordinate space (matching Photo.width/height from the API, not
    whatever resolution either model processed internally), so the frontend
    can position an overlay directly against the full-size image without
    knowing anything about Florence-2's internals.

    `search_results` is None unless a tool set the search list this turn
    (a search, or a truncation); then it's the whole list as it now
    stands -- {"id", "filename", "score"} entries, score 0-1, best score
    first ([] if a first search matched nothing). See _Turn."""

    text: str
    boxes: list[dict] = field(default_factory=list)
    search_results: Optional[list[dict]] = None


def ask(
    photo_bytes: Optional[bytes],
    content_type: Optional[str],
    message: str,
    history: list[dict],
    search_list: Optional[list[dict]] = None,
) -> ChatReply:
    """Send one chat message, returning the full reply at once.
    `photo_bytes`/`content_type` are the open photo, or None when none is.
    `history` is a list of {"role": "user"|"assistant", "text": str}
    dicts, oldest first. `search_list` is the search list as the previous
    turn left it ({"id", "filename", "score"} entries; see _Turn), which
    this turn's tools can filter, add to or truncate."""
    reply = ChatReply(text="")
    for event in _ask_openai_events(photo_bytes, content_type, message, history, search_list, stream=False):
        if "text" in event:
            reply.text += event["text"]
        elif "boxes" in event:
            reply.boxes = event["boxes"]
        elif "search_results" in event:
            reply.search_results = event["search_results"]
    return reply


def should_stream() -> bool:
    """Whether the chat model's reply is requested token by token
    (LUMINARY_OPENAI_STREAMING, default off). Either way ask_events()
    reports tool calls as they happen; this only decides whether the
    reply text arrives in pieces or all at once."""
    return get_openai_streaming()


def ask_events(
    photo_bytes: Optional[bytes],
    content_type: Optional[str],
    message: str,
    history: list[dict],
    search_list: Optional[list[dict]] = None,
):
    """What the chat routes call: one chat message as an iterator of
    events, each a dict with exactly one key --

    {"tool": {...}} when the model calls a tool, and again when that call
      has finished: {"index", "name", "arguments", "status"}, `index`
      counting the turn's calls from 0 (the same on both events),
      `arguments` the parsed JSON object (None if it wasn't one) and
      `status` "running" then "done";
    {"text": str} for the next piece of the reply -- the whole reply as
      one event unless should_stream();
    {"boxes": [...]} / {"search_results": [...]} (same shapes as
      ChatReply's, the turn's current value) once a tool call has
      produced them.

    Config is validated before this returns, not when the iterator is
    first read -- see _ask_openai_events."""
    return _ask_openai_events(photo_bytes, content_type, message, history, search_list, stream=should_stream())


def ask_stream(
    photo_bytes: Optional[bytes],
    content_type: Optional[str],
    message: str,
    history: list[dict],
    search_list: Optional[list[dict]] = None,
):
    """ask_events() with the reply always streamed token by token,
    whatever LUMINARY_OPENAI_STREAMING says."""
    return _ask_openai_events(photo_bytes, content_type, message, history, search_list, stream=True)


# --- The chat model: an OpenAI-compatible multimodal endpoint ---------------


def _downscale_for_chat(photo_bytes: bytes, content_type: str) -> tuple[bytes, str]:
    """Downscale the image to _MAX_CHAT_IMAGE_DIMENSION before it's sent to
    the openai-compatible endpoint -- see that constant's comment for why.
    Capping the longest side keeps the payload well within what any vision
    model actually uses internally anyway (most downsample on their own
    end regardless). Best-effort: if the bytes can't be decoded as an image
    for any reason, fall back to the original bytes unchanged rather than
    failing the whole chat request over this."""
    from PIL import Image, ImageOps

    try:
        image = Image.open(io.BytesIO(photo_bytes))
        # HEIC/HEIF is always re-encoded, whatever its size: vision
        # endpoints generally accept JPEG/PNG/WebP/GIF only.
        needs_reencode = image.format == "HEIF"
        image = ImageOps.exif_transpose(image) or image
        if max(image.size) <= _MAX_CHAT_IMAGE_DIMENSION and not needs_reencode:
            return photo_bytes, content_type
        image.thumbnail((_MAX_CHAT_IMAGE_DIMENSION, _MAX_CHAT_IMAGE_DIMENSION), Image.LANCZOS)
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=85)
        return buffer.getvalue(), "image/jpeg"
    except Exception:
        logger.warning("Could not downscale image for chat; sending original bytes", exc_info=True)
        return photo_bytes, content_type


def _log_upstream_error_body(exc: Exception) -> None:
    """The openai SDK's own exception message is sometimes just a bare
    status code with no detail, when the upstream's error response body
    doesn't match the shape the client expects to parse -- common with
    self-hosted gateways. Log the raw response body too, since that's
    usually where the actual diagnostic from the server ends up."""
    response = getattr(exc, "response", None)
    if response is None:
        return
    try:
        logger.error("openai-compatible backend returned: %s", response.text)
    except Exception:
        pass


def build_openai_client(openai_cls, api_key: str):
    """Construct the openai client, honoring LUMINARY_OPENAI_VERIFY_SSL.
    Shared with enrich.py. `openai_cls` is passed in (rather than imported
    here) so callers keep their own lazy `from openai import OpenAI`.
    `http_client` is only passed when verification is off, using the SDK's
    own DefaultHttpxClient so its default timeouts/limits are kept."""
    kwargs = {}
    if not get_openai_verify_ssl():
        from openai import DefaultHttpxClient

        logger.warning("TLS certificate verification is disabled for the openai-compatible endpoint")
        kwargs["http_client"] = DefaultHttpxClient(verify=False)
    return openai_cls(api_key=api_key, base_url=get_openai_base_url(), **kwargs)


def _prepare_openai_call(photo_bytes: Optional[bytes], content_type: Optional[str], message: str, history: list[dict]):
    """Shared setup for both the blocking and streaming openai calls: validate
    config, build the client and the messages payload. Raises
    ChatNotConfigured synchronously (not deferred into a generator) so a
    caller building a streaming HTTP response can still turn it into a
    normal error response *before* the stream has actually started."""
    from openai import OpenAI  # lazy: keep this an optional dependency at import time

    api_key = get_openai_api_key()
    model = get_openai_model()
    if not api_key or not model:
        raise ChatNotConfigured(
            "OpenAI chat isn't configured: set LUMINARY_OPENAI_API_KEY and "
            "LUMINARY_OPENAI_MODEL (and LUMINARY_OPENAI_BASE_URL if this "
            "isn't api.openai.com)."
        )

    client = build_openai_client(OpenAI, api_key)

    messages = [{"role": turn["role"], "content": turn["text"]} for turn in history]
    if photo_bytes is None:
        messages.append({"role": "user", "content": message})
        return client, model, messages

    photo_bytes, content_type = _downscale_for_chat(photo_bytes, content_type)
    image_data_url = f"data:{content_type};base64,{base64.b64encode(photo_bytes).decode('ascii')}"

    # The image is attached to the current message on every call (rather
    # than once at the start of the conversation) so it works the same way
    # regardless of whether a given OpenAI-compatible server retains image
    # content referenced from earlier turns.
    messages.append(
        {
            "role": "user",
            "content": [
                {"type": "text", "text": message},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ],
        }
    )

    return client, model, messages


# The model may call a tool, read the result, and call another (search,
# filter that by a second search, check the scores, truncate). The last
# round is always made without tools, so a turn can't loop: it has to end
# in an answer. Tool results are a few lines each (see _search_list_text),
# so several rounds stay cheap even for a small context window.
_MAX_CHAT_ROUNDS = 6

# search_photos_semantic's `match_level`: the minimum cosine similarity
# between the query and a photo's description. Starting points, not
# verified-correct values -- the same spread the old sidebar search box
# offered.
_MATCH_LEVELS = {"loose": 0.3, "moderate": 0.5, "strict": 0.7, "very_strict": 0.85}
_DEFAULT_MATCH_LEVEL = "loose"

# How many of the search list's entries (the best-scoring ones) are named
# to the model. The list itself never enters the model's context -- it
# lives on the _Turn, the user sees it in the gallery, and the model works
# on it through counts and score statistics. A few names are just enough
# for it to say something concrete about what was found.
_SEARCH_PREVIEW_COUNT = 3

# What a search does with the search list (the tools' `mode` argument).
_SEARCH_MODES = ("new", "filter", "add")
_DEFAULT_SEARCH_MODE = "new"

# The score of a match that has no degree to it: a file name, text, person
# or tag either matches or doesn't. Only the semantic search grades its
# matches (cosine similarity).
_EXACT_MATCH_SCORE = 1.0

_SEARCH_NOTE = (
    "Matches go into the search list, which the user sees in the gallery under \"Search Results\", "
    "best score first. You are told how many matched and their scores, not every file."
)

_MODE_PROPERTY = {
    "type": "string",
    "enum": list(_SEARCH_MODES),
    "description": (
        'What to do with the search list. "new" (default): replace it with these matches. '
        '"filter": keep only the photos already in the list that also match (to narrow a previous '
        'search). "add": add these matches to the list.'
    ),
}


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


_FILENAME_SEARCH_TOOL = _tool(
    "search_photos_by_filename",
    "Find photos in the user's library by file name or folder path (case-insensitive; every word "
    "given must appear in the path). Use when the user refers to a file or folder name, e.g. "
    '"IMG_1234" or "the vacation/2019 folder". ' + _SEARCH_NOTE,
    {
        "text": {"type": "string", "description": "Text to look for in the file name or path."},
        "mode": _MODE_PROPERTY,
    },
    ["text"],
)

_SEMANTIC_SEARCH_TOOL = _tool(
    "search_photos_semantic",
    "Find photos in the user's library by meaning: matches photos whose description is similar to "
    "the query even when the words differ. Use for what a photo shows or is about, e.g. "
    '"kids playing in snow" or "a birthday party". Each match is scored by how similar it is. '
    + _SEARCH_NOTE,
    {
        "query": {"type": "string", "description": "A description of the photos to find."},
        "match_level": {
            "type": "string",
            "enum": list(_MATCH_LEVELS),
            "description": (
                f'How close a match has to be. Defaults to "{_DEFAULT_MATCH_LEVEL}". Use a stricter '
                "level if the user wants only close matches or a search returned too many unrelated photos."
            ),
        },
        "mode": _MODE_PROPERTY,
    },
    ["query"],
)

_TEXT_SEARCH_TOOL = _tool(
    "search_photos_by_text",
    "Find photos in the user's library by plain text match (case-insensitive) across everything "
    "known about them: file name and path, description, location, names of people tagged in the "
    "photo, and tags. Every word given must appear somewhere. Use for specific names, places or "
    'words, e.g. "Alice", "Boone" or "Grandfather Mountain". ' + _SEARCH_NOTE,
    {"text": {"type": "string", "description": "The word(s) to look for."}, "mode": _MODE_PROPERTY},
    ["text"],
)

_LOCATE_OBJECT_TOOL = _tool(
    "locate_object",
    "Search the open photo for an object with a visual grounding model and get back the bounding "
    "box of each match. The matches are also highlighted on the photo for the user. Call this when "
    "the user asks to find, locate, point out or show where something is in this photo, or when "
    "you need to confirm whether a specific object is present. Don't call it for questions you can "
    "answer just by looking at the photo.",
    {"object": {"type": "string", "description": 'What to search for, as a short noun phrase, e.g. "the dog" or "red car".'}},
    ["object"],
)

_LIST_INDIVIDUALS_TOOL = _tool(
    "list_individuals",
    "List the people (individuals) catalogued in the user's library, with how many photos each is "
    "tagged in. Use to answer who is in the library, or to find the right name before calling "
    "search_photos_by_individual.",
    {},
    [],
)

_INDIVIDUAL_SEARCH_TOOL = _tool(
    "search_photos_by_individual",
    "Find the photos a person is tagged in. Use when the user asks for photos of someone by name, "
    'e.g. "photos of Alice". ' + _SEARCH_NOTE,
    {"name": {"type": "string", "description": "The person's name, or part of it."}, "mode": _MODE_PROPERTY},
    ["name"],
)

_LIST_TAGS_TOOL = _tool(
    "list_tags",
    "List the tags used in the user's library, with how many photos carry each. Use to answer what "
    "tags exist, or to find the right tag before calling search_photos_by_tag.",
    {},
    [],
)

_TAG_SEARCH_TOOL = _tool(
    "search_photos_by_tag",
    "Find the photos carrying a tag. Use when the user asks for photos by tag, e.g. "
    '"photos tagged favorites". ' + _SEARCH_NOTE,
    {"name": {"type": "string", "description": "The tag's name, or part of it."}, "mode": _MODE_PROPERTY},
    ["name"],
)

_SCORE_DESCRIPTION = "A match score as a percentage, 0 to 100."

_SEARCH_LIST_STATS_TOOL = _tool(
    "search_list_stats",
    "Describe the current search list (the photos shown to the user under \"Search Results\", kept "
    "from earlier messages too) without changing it: how many photos it holds and their highest, "
    "median and lowest match score. Give a score to also learn how many photos are at or above it "
    "and how many below -- useful before truncate_search_list.",
    {"score": {"type": "number", "description": _SCORE_DESCRIPTION + " Optional."}},
    [],
)

_TRUNCATE_SEARCH_LIST_TOOL = _tool(
    "truncate_search_list",
    "Remove every photo scoring below min_score from the search list, keeping the better matches. "
    "Use when a search returned too many weak matches or the user asks for only the best ones.",
    {"min_score": {"type": "number", "description": _SCORE_DESCRIPTION + " Photos scoring below it are removed."}},
    ["min_score"],
)

# Everything offered whether or not a photo is open.
_SEARCH_TOOLS = [
    _FILENAME_SEARCH_TOOL,
    _SEMANTIC_SEARCH_TOOL,
    _TEXT_SEARCH_TOOL,
    _LIST_INDIVIDUALS_TOOL,
    _INDIVIDUAL_SEARCH_TOOL,
    _LIST_TAGS_TOOL,
    _TAG_SEARCH_TOOL,
    _SEARCH_LIST_STATS_TOOL,
    _TRUNCATE_SEARCH_LIST_TOOL,
]


class _Turn:
    """What one chat turn's tool calls work on and produce.

    `search_list` is the search list: {"id", "filename", "score"} entries
    (score 0-1), best score first. It is deliberately kept here, outside
    the conversation sent to the model -- a model with a small context
    window can't carry hundreds of file names back and forth -- and the
    tools populate, filter and truncate it, telling the model only its
    size and score statistics. It starts as whatever the caller passed in
    (the list a previous turn left, which the web UI holds and sends back
    with each message; there is no server-side conversation state).

    `search_results` stays None until a tool sets the list this turn, and
    is then that list -- the part of the reply that tells the web UI what
    to show."""

    def __init__(
        self,
        photo_bytes: Optional[bytes],
        search_list: Optional[list[dict]] = None,
        span: Optional[tracing.Span] = None,
    ):
        self.photo_bytes = photo_bytes  # the open photo's original bytes, or None
        # The turn's trace (tracing.py); each tool call is a child span.
        self.span = span or tracing.Span()
        self.boxes: list[dict] = []
        self.search_list: list[dict] = _by_score(search_list or [])
        self.search_results: Optional[list[dict]] = None
        self.tool_calls = 0  # how many tool calls this turn has started

    def set_search_list(self, entries: list[dict]) -> None:
        self.search_list = _by_score(entries)
        self.search_results = self.search_list


def _by_score(entries: list[dict]) -> list[dict]:
    # sorted() is stable, so equally-scored entries keep the order the
    # search returned them in (newest first, or by file name).
    return sorted(entries, key=lambda entry: entry["score"], reverse=True)


def _object_search_available() -> bool:
    """Whether Florence-2's dependencies are installed, i.e. whether
    locate_object can be offered. find_spec() only looks the packages up --
    it doesn't import them, so this stays cheap and keeps torch a lazy
    import."""
    try:
        return all(importlib.util.find_spec(name) is not None for name in ("torch", "transformers"))
    except (ImportError, ValueError):
        return False


def _chat_tools(round_index: int, turn: _Turn) -> dict:
    """Extra kwargs for one chat.completions.create() call: the tool list,
    or nothing -- `tools` is left out entirely rather than sent empty, since
    some OpenAI-compatible servers reject an empty list."""
    if round_index >= _MAX_CHAT_ROUNDS - 1 or not get_chat_tools():
        return {}
    tools = list(_SEARCH_TOOLS)
    if turn.photo_bytes is not None and _object_search_available():
        tools.append(_LOCATE_OBJECT_TOOL)
    return {"tools": tools}


def _run_tool_calls(messages: list, content: str, calls: list[dict], turn: _Turn):
    """Record the model's tool calls (each {"id", "name", "arguments"},
    `arguments` still the raw JSON string) and their results on `messages`,
    ready for the next round; what they found lands on `turn`. A tool
    failure never fails the chat: it's reported back to the model as the
    tool's result, so the model can still answer without it.

    A generator: it yields ask_events()'s events as it goes -- a "running"
    then a "done" {"tool": ...} event around each call (so a caller that
    forwards them shows the call *before* a slow tool has finished), and
    {"boxes": ...}/{"search_results": ...} once the calls have changed
    them. Nothing runs unless it is iterated."""
    messages.append(
        {
            "role": "assistant",
            "content": content or "",
            "tool_calls": [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {"name": call["name"], "arguments": call["arguments"]},
                }
                for call in calls
            ],
        }
    )
    boxes_before = len(turn.boxes)
    search_before = turn.search_results
    for call in calls:
        event = {"index": turn.tool_calls, "name": call["name"], "arguments": _parsed_arguments(call)}
        turn.tool_calls += 1
        yield {"tool": {**event, "status": "running"}}
        span = turn.span.child(call["name"], "TOOL", inputs=event["arguments"])
        result = _run_tool_call(call, turn)
        span.end(outputs=result)
        messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
        yield {"tool": {**event, "status": "done"}}
    if len(turn.boxes) > boxes_before:
        yield {"boxes": list(turn.boxes)}
    if turn.search_results is not search_before:
        yield {"search_results": turn.search_results}


def _parsed_arguments(call: dict) -> Optional[dict]:
    """A tool call's arguments as the JSON object they should be, or None
    if the model wrote something else."""
    try:
        arguments = json.loads(call["arguments"] or "{}")
    except ValueError:
        return None
    return arguments if isinstance(arguments, dict) else None


def _run_tool_call(call: dict, turn: _Turn) -> str:
    handler = _TOOL_HANDLERS.get(call["name"])
    if handler is None:
        return f"Unknown tool: {call['name']}"
    arguments = _parsed_arguments(call)
    if arguments is None:
        return "Invalid arguments: expected a JSON object."
    try:
        return handler(arguments, turn)
    except Exception:
        logger.exception("Chat tool %s(%r) failed", call["name"], arguments)
        return "The tool failed."


def _text_argument(arguments: dict, name: str) -> Optional[str]:
    value = arguments.get(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _score_argument(arguments: dict, name: str) -> Optional[float]:
    """A score argument as a 0-1 fraction, or None if it isn't a usable
    number. The tools ask for a percentage, matching what the model is
    shown (and what the user sees on each tile) -- but a value of 1 or
    less is taken as a fraction, since a model that writes 0.6 means 60%,
    not "under one percent"."""
    value = arguments.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
        return None
    return float(value) if value <= 1 else value / 100


def _percent(score: float) -> str:
    return f"{round(score * 100)}%"


def _count(n: int) -> str:
    return f"{n} photo{'' if n == 1 else 's'}"


def _median(scores: list[float]) -> float:
    ordered = sorted(scores)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _search_list_text(turn: _Turn, preview: bool = True) -> str:
    """The search list as the model sees it: its size and score statistics,
    plus the names of the few best matches -- never the list itself."""
    entries = turn.search_list
    if not entries:
        return "The search list is empty."
    scores = [entry["score"] for entry in entries]
    text = (
        f"The search list holds {_count(len(entries))}, shown to the user in the gallery under "
        f'"Search Results", best score first. Scores: highest {_percent(scores[0])}, '
        f"median {_percent(_median(scores))}, lowest {_percent(scores[-1])}."
    )
    if preview:
        best = [f"- {entry['filename']} ({_percent(entry['score'])})" for entry in entries[:_SEARCH_PREVIEW_COUNT]]
        text += "\nBest matches:\n" + "\n".join(best)
    return text


def _search_mode(arguments: dict) -> Optional[str]:
    mode = arguments.get("mode") or _DEFAULT_SEARCH_MODE
    return mode if mode in _SEARCH_MODES else None


_INVALID_MODE = f'Invalid arguments: "mode" must be one of {", ".join(_SEARCH_MODES)}.'
_NOTHING_TO_FILTER = 'The search list is empty, so there is nothing to filter. Search with mode "new" first.'


def _restriction(turn: _Turn, mode: str) -> dict:
    """Extra kwargs for a storage search: in "filter" mode it only looks
    among the photos already in the search list."""
    return {"photo_ids": [entry["id"] for entry in turn.search_list]} if mode == "filter" else {}


def _apply_search(turn: _Turn, mode: str, photos: list[dict], what: str) -> str:
    """Apply one search's matches to the turn's search list per `mode` and
    describe the outcome to the model. A photo's score is its `similarity`
    when the search graded it (semantic), else _EXACT_MATCH_SCORE.

    A search that would leave nothing never empties a list that has
    photos in it -- the list is left as it was and the model is told so,
    so one over-narrow follow-up doesn't throw away what the user is
    looking at."""
    logger.info("Chat model searched the library for %s (mode %s): %d match(es)", what, mode, len(photos))
    found = [
        {
            "id": photo["id"],
            "filename": photo["filename"],
            "score": round(min(1.0, max(0.0, float(photo.get("similarity", _EXACT_MATCH_SCORE)))), 4),
        }
        for photo in photos
    ]
    current = {entry["id"]: entry for entry in turn.search_list}

    if not found:
        if not current:
            # Still a search, with an empty result: the gallery shows it.
            turn.set_search_list([])
            return f"No photos matched {what}. The search list is empty."
        among = f"None of the {_count(len(current))} in the search list matched" if mode == "filter" else "No photos matched"
        return f"{among} {what}. The search list was left unchanged.\n" + _search_list_text(turn, preview=False)

    if mode == "filter":
        # In both searches' results: as good a match as the weaker of the two.
        entries = [{**entry, "score": min(entry["score"], current[entry["id"]]["score"])} for entry in found if entry["id"] in current]
        outcome = f"Of the {_count(len(current))} in the search list, {len(entries)} also matched {what}; the rest were removed."
    elif mode == "add":
        merged = dict(current)
        for entry in found:
            known = merged.get(entry["id"])
            merged[entry["id"]] = entry if known is None else {**known, "score": max(known["score"], entry["score"])}
        entries = list(merged.values())
        outcome = f"Found {_count(len(found))} matching {what}; {len(entries) - len(current)} of them were not already in the search list and were added."
    else:
        entries = found
        outcome = f"Found {_count(len(found))} matching {what}."
    if mode != "filter" and len(found) >= storage.SEARCH_RESULTS_LIMIT:
        outcome += f" (A search stops at {storage.SEARCH_RESULTS_LIMIT} matches; there may be more.)"

    turn.set_search_list(entries)
    return outcome + "\n" + _search_list_text(turn)


def _tool_search_by_filename(arguments: dict, turn: _Turn) -> str:
    text = _text_argument(arguments, "text")
    if text is None:
        return 'Invalid arguments: "text" must be a non-empty string.'
    mode = _search_mode(arguments)
    if mode is None:
        return _INVALID_MODE
    if mode == "filter" and not turn.search_list:
        return _NOTHING_TO_FILTER
    photos = storage.search_photos_by_filename(text, **_restriction(turn, mode))
    return _apply_search(turn, mode, photos, f'the file name/path "{text}"')


def _tool_search_semantic(arguments: dict, turn: _Turn) -> str:
    query = _text_argument(arguments, "query")
    if query is None:
        return 'Invalid arguments: "query" must be a non-empty string.'
    level = arguments.get("match_level") or _DEFAULT_MATCH_LEVEL
    if level not in _MATCH_LEVELS:
        return f'Invalid arguments: "match_level" must be one of {", ".join(_MATCH_LEVELS)}.'
    mode = _search_mode(arguments)
    if mode is None:
        return _INVALID_MODE
    if mode == "filter" and not turn.search_list:
        return _NOTHING_TO_FILTER
    try:
        photos = storage.search_photos(query, _MATCH_LEVELS[level], **_restriction(turn, mode))
    except embeddings.EmbeddingNotConfigured:
        logger.warning("Chat model called search_photos_semantic, but the embedding model isn't available")
        return "Semantic search isn't available on this server. Try search_photos_by_text instead."
    return _apply_search(turn, mode, photos, f'"{query}" ({level} semantic match)')


def _tool_search_by_text(arguments: dict, turn: _Turn) -> str:
    text = _text_argument(arguments, "text")
    if text is None:
        return 'Invalid arguments: "text" must be a non-empty string.'
    mode = _search_mode(arguments)
    if mode is None:
        return _INVALID_MODE
    if mode == "filter" and not turn.search_list:
        return _NOTHING_TO_FILTER
    photos = storage.search_photos_by_text(text, **_restriction(turn, mode))
    return _apply_search(turn, mode, photos, f'the text "{text}"')


def _name_list_text(entries: list[dict], kind: str) -> str:
    """Text for the list_individuals/list_tags tools. The whole list goes to
    the model -- unlike a photo search there's no gallery view of it, so
    the model's answer is the only way the user sees it."""
    if not entries:
        return f"There are no {kind} in the library yet."
    lines = [f"- {entry['name']} ({entry['photo_count']} photo{'' if entry['photo_count'] == 1 else 's'})" for entry in entries]
    return f"{len(entries)} {kind}:\n" + "\n".join(lines)


def _tool_list_individuals(arguments: dict, turn: _Turn) -> str:
    return _name_list_text(storage.list_individual_names(), "individuals")


def _tool_list_tags(arguments: dict, turn: _Turn) -> str:
    return _name_list_text(storage.list_tag_names(), "tags")


def _named_search(arguments: dict, turn: _Turn, search, kind: str, list_tool: str) -> str:
    """Shared by search_photos_by_individual/_by_tag: `search` is the
    storage function, returning (names matched, photos)."""
    name = _text_argument(arguments, "name")
    if name is None:
        return 'Invalid arguments: "name" must be a non-empty string.'
    mode = _search_mode(arguments)
    if mode is None:
        return _INVALID_MODE
    if mode == "filter" and not turn.search_list:
        return _NOTHING_TO_FILTER
    matched, photos = search(name, **_restriction(turn, mode))
    if not matched:
        # Not recorded as a search: nothing was looked for, so the search
        # list (and the user's gallery) is left alone.
        return f'There is no {kind} matching "{name}". Call {list_tool} to see the ones that exist.'
    return _apply_search(turn, mode, photos, f"the {kind} {', '.join(matched)}")


def _tool_search_by_individual(arguments: dict, turn: _Turn) -> str:
    return _named_search(arguments, turn, storage.search_photos_by_individual, "individual", "list_individuals")


def _tool_search_by_tag(arguments: dict, turn: _Turn) -> str:
    return _named_search(arguments, turn, storage.search_photos_by_tag, "tag", "list_tags")


def _tool_search_list_stats(arguments: dict, turn: _Turn) -> str:
    text = _search_list_text(turn)
    if "score" not in arguments or arguments["score"] is None or not turn.search_list:
        return text
    score = _score_argument(arguments, "score")
    if score is None:
        return 'Invalid arguments: "score" must be a number from 0 to 100.'
    at_or_above = sum(1 for entry in turn.search_list if entry["score"] >= score)
    return (
        f"{text}\n{at_or_above} at or above {_percent(score)}, "
        f"{len(turn.search_list) - at_or_above} below."
    )


def _tool_truncate_search_list(arguments: dict, turn: _Turn) -> str:
    min_score = _score_argument(arguments, "min_score")
    if min_score is None:
        return 'Invalid arguments: "min_score" must be a number from 0 to 100.'
    if not turn.search_list:
        return "The search list is empty, so there is nothing to truncate."
    kept = [entry for entry in turn.search_list if entry["score"] >= min_score]
    removed = len(turn.search_list) - len(kept)
    logger.info("Chat model truncated the search list at %s: %d removed, %d kept", _percent(min_score), removed, len(kept))
    if not kept:
        # Same rule as _apply_search: never empty a list that has photos.
        return (
            f"No photo scores {_percent(min_score)} or higher, so nothing was removed.\n"
            + _search_list_text(turn, preview=False)
        )
    if removed:
        turn.set_search_list(kept)
    return f"Removed {_count(removed)} scoring below {_percent(min_score)}.\n" + _search_list_text(turn, preview=False)


def _tool_locate_object(arguments: dict, turn: _Turn) -> str:
    phrase = _text_argument(arguments, "object")
    if phrase is None:
        return 'Invalid arguments: "object" must be a non-empty string.'
    if turn.photo_bytes is None:
        return "No photo is open, so there is nothing to search in."
    try:
        text, found = locate_object(turn.photo_bytes, phrase)
    except ChatNotConfigured:
        logger.warning("Chat model called locate_object, but Florence-2 isn't available")
        return "Object search isn't available on this server."
    logger.info("Chat model searched the photo for %r: %d match(es)", phrase, len(found))
    turn.boxes.extend(found)
    return text


_TOOL_HANDLERS = {
    "search_photos_by_filename": _tool_search_by_filename,
    "search_photos_semantic": _tool_search_semantic,
    "search_photos_by_text": _tool_search_by_text,
    "list_individuals": _tool_list_individuals,
    "search_photos_by_individual": _tool_search_by_individual,
    "list_tags": _tool_list_tags,
    "search_photos_by_tag": _tool_search_by_tag,
    "search_list_stats": _tool_search_list_stats,
    "truncate_search_list": _tool_truncate_search_list,
    "locate_object": _tool_locate_object,
}


def _ask_openai_events(
    photo_bytes: Optional[bytes],
    content_type: Optional[str],
    message: str,
    history: list[dict],
    search_list: Optional[list[dict]],
    stream: bool,
):
    """The chat loop behind ask(), ask_events() and ask_stream(): returns
    an iterator of ask_events()'s events. `stream` is whether the model's
    reply is requested (and so reported) token by token."""
    # _prepare_openai_call() runs here, eagerly -- not inside generate()
    # below -- specifically so ChatNotConfigured still raises synchronously
    # when this is called, rather than only once the returned generator is
    # first iterated (which, for an HTTP caller, would be after the
    # streaming response has already started and the status code can no
    # longer be changed).
    client, model, messages = _prepare_openai_call(photo_bytes, content_type, message, history)

    def generate():
        # One trace per turn: this span, a child per model round and a
        # child per tool call. Ended in the `finally`, so a turn the client
        # walked away from (GeneratorExit) is still recorded.
        span = tracing.start_trace(
            "chat",
            "AGENT",
            inputs={
                "message": message,
                "history": history,
                "photo_open": photo_bytes is not None,
                "search_list_size": len(search_list or []),
            },
        )
        turn = _Turn(photo_bytes, search_list, span)
        reply = ""
        error = None
        try:
            for round_index in range(_MAX_CHAT_ROUNDS):
                request = dict(model=model, messages=messages, max_tokens=500, **_chat_tools(round_index, turn))
                content, calls = yield from _traced_round(client, request, stream, span)
                if stream:
                    reply += content
                if not calls:
                    break
                yield from _run_tool_calls(messages, content, calls, turn)
            # Not streamed: only the last round's text is the reply (text
            # a model writes alongside a tool call is not shown).
            if not stream and content:
                reply = content
                yield {"text": content}
        except Exception as exc:
            error = exc
            _log_upstream_error_body(exc)
            raise
        finally:
            span.end(
                outputs={
                    "reply": reply,
                    "tool_calls": turn.tool_calls,
                    "boxes": len(turn.boxes),
                    "search_results": None if turn.search_results is None else len(turn.search_results),
                },
                error=error,
            )

    return generate()


def _traced_round(client, request: dict, stream: bool, parent: tracing.Span):
    """One model round as a child span of the turn's trace. A generator
    either way -- it yields the round's {"text": ...} events when streamed
    -- returning (text, tool calls)."""
    span = parent.child(
        "chat.completions",
        "CHAT_MODEL",
        inputs={
            "messages": _messages_for_trace(request["messages"]),
            "tools": [tool["function"]["name"] for tool in request.get("tools", [])],
        },
    )
    usage = None
    try:
        if stream:
            content, calls, usage = yield from _streamed_round(client, request)
        else:
            content, calls, usage = _plain_round(client, request)
    except BaseException as exc:  # GeneratorExit too: the client went away mid-reply
        span.end(error=exc)
        raise
    span.set_attributes(tracing.llm_attributes(request["model"], usage))
    output = {"role": "assistant", "content": content}
    if calls:
        output["tool_calls"] = [
            {"id": call["id"], "type": "function", "function": {"name": call["name"], "arguments": call["arguments"]}}
            for call in calls
        ]
    span.end(outputs=output)
    return content, calls


def _messages_for_trace(messages: list) -> list:
    """The conversation as it's recorded on a trace: the open photo's
    base64 data URL -- megabytes, on every round of every turn -- swapped
    for a note of its size."""

    def part_for_trace(part):
        url = part.get("image_url", {}).get("url", "") if isinstance(part, dict) else ""
        if not url.startswith("data:"):
            return part
        return {"type": "image_url", "image_url": {"url": f"{url.split(',', 1)[0]},<{len(url)} characters omitted>"}}

    return [
        {**message, "content": [part_for_trace(part) for part in message["content"]]}
        if isinstance(message.get("content"), list)
        else message
        for message in messages
    ]


def _plain_round(client, request: dict) -> tuple[str, list[dict], object]:
    """One model round, the reply taken whole: (its text, its tool calls,
    the token usage the server reported or None)."""
    response = client.chat.completions.create(**request)
    reply = response.choices[0].message
    # getattr: not every OpenAI-compatible server includes the field.
    calls = [
        {"id": call.id, "name": call.function.name, "arguments": call.function.arguments}
        for call in getattr(reply, "tool_calls", None) or []
    ]
    return reply.content or "", calls, getattr(response, "usage", None)


def _streamed_round(client, request: dict):
    """One model round, streamed: a generator yielding a {"text": ...}
    event per piece of the reply and *returning* the same
    (text, tool calls, usage) as _plain_round."""
    content = ""
    usage = None
    # A tool call arrives in fragments across chunks, keyed by its index:
    # id and name once, the arguments JSON in pieces.
    calls: dict[int, dict] = {}
    for chunk in client.chat.completions.create(stream=True, **request):
        # Only servers asked for it send usage, on a final chunk of its own.
        usage = getattr(chunk, "usage", None) or usage
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            content += delta.content
            yield {"text": delta.content}
        for fragment in getattr(delta, "tool_calls", None) or []:
            call = calls.setdefault(fragment.index, {"id": "", "name": "", "arguments": ""})
            if fragment.id:
                call["id"] = fragment.id
            if fragment.function is not None:
                if fragment.function.name:
                    call["name"] = fragment.function.name
                if fragment.function.arguments:
                    call["arguments"] += fragment.function.arguments
    return content, [calls[index] for index in sorted(calls)], usage


# --- Florence-2: the locate_object tool, and sync.py's captions -------------

# A plain description, no text input -- caption_image(), for sync.py.
_FLORENCE_CAPTION_TASK = "<MORE_DETAILED_CAPTION>"
# The one Florence-2 task that takes free text alongside the task token: a
# caption/phrase to locate in the image, returned as bounding box(es) -- not
# a natural-language answer. This is what the locate_object tool runs.
_FLORENCE_GROUNDING_TASK = "<CAPTION_TO_PHRASE_GROUNDING>"

_florence_lock = threading.Lock()
_florence_model = None
_florence_processor = None


def _load_florence():
    global _florence_model, _florence_processor

    with _florence_lock:
        if _florence_model is not None:
            return _florence_model, _florence_processor

        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoProcessor
        except ImportError as exc:
            raise ChatNotConfigured(
                "Florence-2 chat requires the torch/transformers/timm/einops "
                "dependencies to be installed."
            ) from exc

        model_id = get_florence_model()
        device = get_florence_device()
        dtype = torch.float16 if device.startswith("cuda") else torch.float32

        logger.info("Loading Florence-2 model %s on %s (first use only)", model_id, device)
        model = AutoModelForCausalLM.from_pretrained(
            model_id, torch_dtype=dtype, trust_remote_code=True
        ).to(device)
        processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
        logger.info("Florence-2 model ready")

        _florence_model, _florence_processor = model, processor
        return _florence_model, _florence_processor


_FLORENCE_MIN_SIDE = 32


def _run_florence_task(image, task: str, phrase: str = "") -> dict:
    """Shared Florence-2 inference step: load the model, run `task` (with
    `phrase` appended for the one task that takes free text -- see module
    docstring), and return the parsed result for that task key. `image`
    must already be RGB and EXIF-transposed (see caption_image/locate_object
    for why that matters)."""
    model, processor = _load_florence()
    import torch

    device = get_florence_device()
    dtype = torch.float16 if device.startswith("cuda") else torch.float32

    # run_example() in the model card concatenates task token and text input
    # with no separator -- the model was trained on exactly that format.
    prompt = task + phrase if phrase else task

    # The HF image processor guesses channel layout from the array shape; a
    # side of 1-3 px (a 1x1 tracking pixel, say) looks like channels-first
    # and fails with "mean must have 1 elements". Upscale such tiny images
    # for the model only -- boxes are still reported in the original size.
    model_image = image
    if min(image.size) < _FLORENCE_MIN_SIDE:
        scale = _FLORENCE_MIN_SIDE / min(image.size)
        model_image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        )
    inputs = processor(text=prompt, images=model_image, return_tensors="pt").to(device, dtype)
    generated_ids = model.generate(
        input_ids=inputs["input_ids"],
        pixel_values=inputs["pixel_values"],
        max_new_tokens=1024,
        num_beams=3,
    )
    generated_text = processor.batch_decode(generated_ids, skip_special_tokens=False)[0]
    parsed = processor.post_process_generation(generated_text, task=task, image_size=(image.width, image.height))
    return parsed[task]


def caption_image(image) -> str:
    """Run Florence-2's <MORE_DETAILED_CAPTION> task on an already-opened,
    already RGB, already EXIF-transposed image (e.g. the one sync.py opens
    per-file) and return the plain-text description, for sync.py's
    auto-description-on-index path. Raises ChatNotConfigured if
    torch/transformers aren't installed."""
    return _run_florence_task(image, _FLORENCE_CAPTION_TASK)


def locate_object(photo_bytes: bytes, phrase: str) -> tuple[str, list[dict]]:
    """The locate_object tool: find `phrase` in the photo with Florence-2's
    phrase grounding. Returns (a text result for the chat model to read,
    the boxes for the frontend to draw). Raises ChatNotConfigured if
    torch/transformers aren't installed."""
    from PIL import Image, ImageOps  # Pillow is a core dependency, always safe to import

    # Raise ChatNotConfigured (missing torch/transformers) before doing
    # anything else, including decoding photo_bytes as an image -- a
    # genuinely malformed image shouldn't mask a "not configured" error.
    _load_florence()

    image = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
    # Match sync.py's indexing: the raw sensor pixel grid ignores EXIF
    # Orientation, but the browser (and Photo.width/height, recorded from
    # sync.py post-transpose) both honor it. Grounding box coordinates must
    # be computed in that same post-transpose space, or they'll be rotated
    # relative to what the frontend overlays them on.
    image = ImageOps.exif_transpose(image) or image

    result = _run_florence_task(image, _FLORENCE_GROUNDING_TASK, phrase)
    text, boxes = _format_grounding_result(result, phrase)
    if boxes:
        # The chat model was sent a downscaled copy, so pixel coordinates
        # mean nothing to it without the size they're relative to.
        text += (
            f"\nCoordinates are (left, top)-(right, bottom) in pixels; the photo is "
            f"{image.width}x{image.height}. These regions are now highlighted on the photo for the user."
        )
    return text, boxes


def _format_grounding_result(result: dict, phrase: str) -> tuple[str, list[dict]]:
    """<CAPTION_TO_PHRASE_GROUNDING> returns {"bboxes": [...], "labels":
    [...]} -- box coordinates (in the image_size passed to
    post_process_generation, i.e. the original photo's pixel space), not a
    sentence. Render readable text for the chat model, and return the
    boxes themselves (full precision, not rounded like the text) for the
    frontend to draw."""
    bboxes = result.get("bboxes", [])
    labels = result.get("labels", [])
    if not bboxes:
        return f'No match for "{phrase}" was found in this photo.', []

    lines = []
    boxes = []
    for label, (x1, y1, x2, y2) in zip(labels, bboxes):
        lines.append(f"- \"{label}\" at approximately ({x1:.0f}, {y1:.0f})-({x2:.0f}, {y2:.0f})")
        boxes.append({"label": label, "x1": x1, "y1": y1, "x2": x2, "y2": y2})

    text = f'Location(s) found for "{phrase}":\n' + "\n".join(lines)
    return text, boxes
