"""The chat assistant: an OpenAI-*compatible* chat endpoint (self-hosted or
not -- LUMINARY_OPENAI_BASE_URL is never assumed to be api.openai.com)
that can call tools of ours. If a photo is open it's attached to the
message, so the model can see it; with none open the chat is about the
library as a whole.

The tools (see _TOOLS below; the model decides when to call one):

- search_photos_by_filename / search_photos_semantic /
  search_photos_by_text / search_photos_by_individual /
  search_photos_by_tag search the library. This is the app's only search
  UI -- there is no search box. The photos a search finds travel back
  alongside the reply (ChatReply.search_results, ids only) and the web UI
  shows them as the gallery's "Search Results".
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

The reply can optionally be streamed back token-by-token
(LUMINARY_OPENAI_STREAMING, off by default) -- see should_stream()/
ask_stream() vs. the always-available, full-reply-at-once ask().

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

from . import embeddings, storage
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

    `search_results` is None unless the model searched the library this
    turn; then it's the ids of the photos found, best match first ([] if
    nothing matched)."""

    text: str
    boxes: list[dict] = field(default_factory=list)
    search_results: Optional[list[str]] = None


def ask(photo_bytes: Optional[bytes], content_type: Optional[str], message: str, history: list[dict]) -> ChatReply:
    """Send one chat message, returning the full reply at once.
    `photo_bytes`/`content_type` are the open photo, or None when none is.
    `history` is a list of {"role": "user"|"assistant", "text": str}
    dicts, oldest first."""
    return _ask_openai(photo_bytes, content_type, message, history)


def should_stream() -> bool:
    """Whether a chat request should use the streaming response path
    (ask_stream) instead of ask() -- LUMINARY_OPENAI_STREAMING, default
    off."""
    return get_openai_streaming()


def ask_stream(photo_bytes: Optional[bytes], content_type: Optional[str], message: str, history: list[dict]):
    """Streaming counterpart to ask(), for when should_stream() is True.
    Returns an iterator of events, each a dict with exactly one key:
    {"text": str} for the next piece of the reply, {"boxes": [...]} or
    {"search_results": [...]} (same shapes as ChatReply's, the turn's
    current value) once a tool call has produced them."""
    return _ask_openai_stream(photo_bytes, content_type, message, history)


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


# The model may call a tool, read the result, and call another (e.g. a
# looser search after the first found nothing). The last round is always
# made without tools, so a turn can't loop: it has to end in an answer.
_MAX_CHAT_ROUNDS = 4

# search_photos_semantic's `match_level`: the minimum cosine similarity
# between the query and a photo's description. Starting points, not
# verified-correct values -- the same spread the old sidebar search box
# offered.
_MATCH_LEVELS = {"loose": 0.3, "moderate": 0.5, "strict": 0.7, "very_strict": 0.85}
_DEFAULT_MATCH_LEVEL = "loose"

# How many of a search's matches are described to the model. All of them
# reach the user (as ids, in ChatReply.search_results); the model only
# needs enough to say something sensible about what was found.
_SEARCH_PREVIEW_COUNT = 10

_SEARCH_NOTE = (
    "The matching photos are shown to the user in the gallery, under \"Search Results\" -- "
    "summarise what was found rather than listing every file."
)


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
    {"text": {"type": "string", "description": "Text to look for in the file name or path."}},
    ["text"],
)

_SEMANTIC_SEARCH_TOOL = _tool(
    "search_photos_semantic",
    "Find photos in the user's library by meaning: matches photos whose description is similar to "
    "the query even when the words differ. Use for what a photo shows or is about, e.g. "
    '"kids playing in snow" or "a birthday party". ' + _SEARCH_NOTE,
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
    },
    ["query"],
)

_TEXT_SEARCH_TOOL = _tool(
    "search_photos_by_text",
    "Find photos in the user's library by plain text match (case-insensitive) across everything "
    "known about them: file name and path, description, location, names of people tagged in the "
    "photo, and tags. Every word given must appear somewhere. Use for specific names, places or "
    'words, e.g. "Alice", "Boone" or "Grandfather Mountain". ' + _SEARCH_NOTE,
    {"text": {"type": "string", "description": "The word(s) to look for."}},
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
    {"name": {"type": "string", "description": "The person's name, or part of it."}},
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
    {"name": {"type": "string", "description": "The tag's name, or part of it."}},
    ["name"],
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
]


class _Turn:
    """What one chat turn's tool calls work on and produce."""

    def __init__(self, photo_bytes: Optional[bytes]):
        self.photo_bytes = photo_bytes  # the open photo's original bytes, or None
        self.boxes: list[dict] = []
        self.search_results: Optional[list[str]] = None

    def record_search(self, photos: list[dict]) -> None:
        # When the model searches more than once in a turn, the last search
        # that found something is what the user is shown -- a follow-up that
        # comes back empty doesn't wipe out an earlier search's matches.
        if photos or self.search_results is None:
            self.search_results = [photo["id"] for photo in photos]


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


def _run_tool_calls(messages: list, content: str, calls: list[dict], turn: _Turn) -> None:
    """Record the model's tool calls (each {"id", "name", "arguments"},
    `arguments` still the raw JSON string) and their results on `messages`,
    ready for the next round; what they found lands on `turn`. A tool
    failure never fails the chat: it's reported back to the model as the
    tool's result, so the model can still answer without it."""
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
    for call in calls:
        messages.append({"role": "tool", "tool_call_id": call["id"], "content": _run_tool_call(call, turn)})


def _run_tool_call(call: dict, turn: _Turn) -> str:
    handler = _TOOL_HANDLERS.get(call["name"])
    if handler is None:
        return f"Unknown tool: {call['name']}"
    try:
        arguments = json.loads(call["arguments"] or "{}")
    except ValueError:
        arguments = None
    if not isinstance(arguments, dict):
        return "Invalid arguments: expected a JSON object."
    try:
        return handler(arguments, turn)
    except Exception:
        logger.exception("Chat tool %s(%r) failed", call["name"], arguments)
        return "The tool failed."


def _text_argument(arguments: dict, name: str) -> Optional[str]:
    value = arguments.get(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _search_result_text(photos: list[dict], what: str, turn: _Turn) -> str:
    """Record a search's matches on the turn and describe them to the model."""
    logger.info("Chat model searched the library for %s: %d match(es)", what, len(photos))
    turn.record_search(photos)
    if not photos:
        return f"No photos matched {what}."

    lines = []
    for photo in photos[:_SEARCH_PREVIEW_COUNT]:
        details = [photo["filename"]]
        if photo.get("location_name"):
            details.append(photo["location_name"])
        if photo.get("description"):
            details.append(photo["description"][:200])
        lines.append("- " + " | ".join(details))
    more = len(photos) - len(lines)
    if more > 0:
        lines.append(f"(and {more} more)")
    count = f"{len(photos)} photo{'' if len(photos) == 1 else 's'}"
    if len(photos) >= storage.SEARCH_RESULTS_LIMIT:
        count = f"at least {count} (the search stops there)"
    return (
        f"Found {count} matching {what}. They are now shown to the user in the gallery "
        'under "Search Results".\n' + "\n".join(lines)
    )


def _tool_search_by_filename(arguments: dict, turn: _Turn) -> str:
    text = _text_argument(arguments, "text")
    if text is None:
        return 'Invalid arguments: "text" must be a non-empty string.'
    return _search_result_text(storage.search_photos_by_filename(text), f'the file name/path "{text}"', turn)


def _tool_search_semantic(arguments: dict, turn: _Turn) -> str:
    query = _text_argument(arguments, "query")
    if query is None:
        return 'Invalid arguments: "query" must be a non-empty string.'
    level = arguments.get("match_level") or _DEFAULT_MATCH_LEVEL
    if level not in _MATCH_LEVELS:
        return f'Invalid arguments: "match_level" must be one of {", ".join(_MATCH_LEVELS)}.'
    try:
        photos = storage.search_photos(query, _MATCH_LEVELS[level])
    except embeddings.EmbeddingNotConfigured:
        logger.warning("Chat model called search_photos_semantic, but the embedding model isn't available")
        return "Semantic search isn't available on this server. Try search_photos_by_text instead."
    return _search_result_text(photos, f'"{query}" ({level} semantic match)', turn)


def _tool_search_by_text(arguments: dict, turn: _Turn) -> str:
    text = _text_argument(arguments, "text")
    if text is None:
        return 'Invalid arguments: "text" must be a non-empty string.'
    return _search_result_text(storage.search_photos_by_text(text), f'the text "{text}"', turn)


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
    matched, photos = search(name)
    if not matched:
        # Not recorded as a search: nothing was looked for, so the user's
        # gallery shouldn't switch to an empty result.
        return f'There is no {kind} matching "{name}". Call {list_tool} to see the ones that exist.'
    return _search_result_text(photos, f"the {kind} {', '.join(matched)}", turn)


def _tool_search_by_individual(arguments: dict, turn: _Turn) -> str:
    return _named_search(arguments, turn, storage.search_photos_by_individual, "individual", "list_individuals")


def _tool_search_by_tag(arguments: dict, turn: _Turn) -> str:
    return _named_search(arguments, turn, storage.search_photos_by_tag, "tag", "list_tags")


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
    "locate_object": _tool_locate_object,
}


def _ask_openai(
    photo_bytes: Optional[bytes], content_type: Optional[str], message: str, history: list[dict]
) -> ChatReply:
    client, model, messages = _prepare_openai_call(photo_bytes, content_type, message, history)
    turn = _Turn(photo_bytes)
    for round_index in range(_MAX_CHAT_ROUNDS):
        try:
            response = client.chat.completions.create(
                model=model, messages=messages, max_tokens=500, **_chat_tools(round_index, turn)
            )
        except Exception as exc:
            _log_upstream_error_body(exc)
            raise
        reply = response.choices[0].message
        # getattr: not every OpenAI-compatible server includes the field.
        tool_calls = getattr(reply, "tool_calls", None)
        if not tool_calls:
            break
        calls = [
            {"id": call.id, "name": call.function.name, "arguments": call.function.arguments}
            for call in tool_calls
        ]
        _run_tool_calls(messages, reply.content, calls, turn)
    return ChatReply(text=reply.content or "", boxes=turn.boxes, search_results=turn.search_results)


def _ask_openai_stream(photo_bytes: Optional[bytes], content_type: Optional[str], message: str, history: list[dict]):
    # _prepare_openai_call() runs here, eagerly -- not inside generate()
    # below -- specifically so ChatNotConfigured still raises synchronously
    # when this is called, rather than only once the returned generator is
    # first iterated (which, for an HTTP caller, would be after the
    # streaming response has already started and the status code can no
    # longer be changed).
    client, model, messages = _prepare_openai_call(photo_bytes, content_type, message, history)

    def generate():
        turn = _Turn(photo_bytes)
        try:
            for round_index in range(_MAX_CHAT_ROUNDS):
                stream = client.chat.completions.create(
                    model=model, messages=messages, max_tokens=500, stream=True, **_chat_tools(round_index, turn)
                )
                content = ""
                # A tool call arrives in fragments across chunks, keyed by
                # its index: id and name once, the arguments JSON in pieces.
                calls: dict[int, dict] = {}
                for chunk in stream:
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
                if not calls:
                    return
                boxes_before = len(turn.boxes)
                search_before = turn.search_results
                _run_tool_calls(messages, content, [calls[index] for index in sorted(calls)], turn)
                if len(turn.boxes) > boxes_before:
                    yield {"boxes": list(turn.boxes)}
                if turn.search_results is not search_before:
                    yield {"search_results": turn.search_results}
        except Exception as exc:
            _log_upstream_error_body(exc)
            raise

    return generate()


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

    inputs = processor(text=prompt, images=image, return_tensors="pt").to(device, dtype)
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
