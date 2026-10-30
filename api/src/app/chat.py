"""Chat about a photo, via one of two interchangeable backends:

- "openai": any OpenAI-*compatible* multimodal chat endpoint (self-hosted or
  not -- LUMINARY_OPENAI_BASE_URL is never assumed to be api.openai.com).
- "florence": a local Florence-2 vision-language model (see
  https://huggingface.co/microsoft/Florence-2-base), run in-process via
  transformers/torch.

Both are heavy, optional dependencies (openai is light; torch/transformers
are not) -- imported lazily inside the functions that need them, not at
module import time, so the API starts and the rest of the test suite runs
without requiring either to be installed or configured.

Florence-2 is not a conversational VQA model: its API is a fixed set of task
tokens (captioning, OCR, object detection, phrase grounding), not free-form
question answering -- there's no task token for "answer this arbitrary
question about the image". Exactly one task accepts free text alongside the
task token: <CAPTION_TO_PHRASE_GROUNDING>, which takes a caption/phrase and
returns the bounding box(es) where it appears in the image (see
run_example() in the model card) -- not a natural-language answer. The
Florence-2 path uses that task, with the user's message as the phrase to
ground, whenever the message is non-empty; an empty message falls back to
<MORE_DETAILED_CAPTION>. The frontend should make this limitation visible
rather than implying it's a general-purpose chat model: a user's question
("where was this taken?") won't be answered, but a phrase ("the dog") will
be located. ask() returns a ChatReply (not a bare string) so that
grounding's bounding boxes can travel alongside the reply text -- see its
docstring -- for the frontend to draw as an overlay on the full-size image.

The openai backend can optionally stream its reply back token-by-token
(LUMINARY_OPENAI_STREAMING, off by default) -- see should_stream()/
ask_stream() vs. the always-available, full-reply-at-once ask().

caption_image() and detect_faces() are the two Florence-2 entry points
sync.py uses directly (outside the chat.ask()/ChatReply flow above) to
auto-describe and face-detect a photo at index time: caption_image() is
<MORE_DETAILED_CAPTION>, and detect_faces() is the same grounding task
_ask_florence uses for a user's chat message, but grounding the fixed
phrase "face" instead. Both raise ChatNotConfigured exactly like ask()
does, which is how sync.py knows to leave that work for a later sync
rather than fail it.
"""

import base64
import io
import logging
import threading
from dataclasses import dataclass, field

from .config import (
    get_florence_device,
    get_florence_model,
    get_openai_api_key,
    get_openai_base_url,
    get_openai_model,
    get_openai_streaming,
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
    `boxes` is non-empty only for a florence grounding result (never for
    openai, which has no concept of image regions) -- each is
    {"label": str, "x1"/"y1"/"x2"/"y2": float} in the *original* photo's
    pixel coordinate space (matching Photo.width/height from the API, not
    whatever resolution the model processed internally), so the frontend
    can position an overlay directly against the full-size image without
    knowing anything about Florence-2's internals."""

    text: str
    boxes: list[dict] = field(default_factory=list)


def ask(backend: str, photo_bytes: bytes, content_type: str, message: str, history: list[dict]) -> ChatReply:
    """Ask a question about a photo, returning the full reply at once.
    `history` is a list of {"role": "user"|"assistant", "text": str}
    dicts, oldest first."""
    if backend == "openai":
        return ChatReply(text=_ask_openai(photo_bytes, content_type, message, history))
    if backend == "florence":
        return _ask_florence(photo_bytes, message)
    raise ValueError(f"Unknown chat backend: {backend!r}")


def should_stream(backend: str) -> bool:
    """Whether a chat request for this backend should use the streaming
    response path (ask_stream) instead of ask(). Only "openai" supports
    streaming -- Florence-2's API is a single blocking generate() call with
    no token-by-token output -- and only when LUMINARY_OPENAI_STREAMING is
    enabled (default off)."""
    return backend == "openai" and get_openai_streaming()


def ask_stream(backend: str, photo_bytes: bytes, content_type: str, message: str, history: list[dict]):
    """Streaming counterpart to ask(), for when should_stream(backend) is
    True. Returns an iterator of reply text chunks. Only implemented for
    "openai" -- callers must check should_stream() first."""
    if backend != "openai":
        raise ValueError(f"Streaming isn't supported for backend {backend!r}")
    return _ask_openai_stream(photo_bytes, content_type, message, history)


# --- Path 1: OpenAI-compatible multimodal endpoint -------------------------


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
        image = ImageOps.exif_transpose(image) or image
        if max(image.size) <= _MAX_CHAT_IMAGE_DIMENSION:
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


def _prepare_openai_call(photo_bytes: bytes, content_type: str, message: str, history: list[dict]):
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

    client = OpenAI(api_key=api_key, base_url=get_openai_base_url())

    photo_bytes, content_type = _downscale_for_chat(photo_bytes, content_type)
    image_data_url = f"data:{content_type};base64,{base64.b64encode(photo_bytes).decode('ascii')}"

    messages = [{"role": turn["role"], "content": turn["text"]} for turn in history]
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


def _ask_openai(photo_bytes: bytes, content_type: str, message: str, history: list[dict]) -> str:
    client, model, messages = _prepare_openai_call(photo_bytes, content_type, message, history)
    try:
        response = client.chat.completions.create(model=model, messages=messages, max_tokens=500)
    except Exception as exc:
        _log_upstream_error_body(exc)
        raise
    return response.choices[0].message.content


def _ask_openai_stream(photo_bytes: bytes, content_type: str, message: str, history: list[dict]):
    # _prepare_openai_call() runs here, eagerly -- not inside generate()
    # below -- specifically so ChatNotConfigured still raises synchronously
    # when this is called, rather than only once the returned generator is
    # first iterated (which, for an HTTP caller, would be after the
    # streaming response has already started and the status code can no
    # longer be changed).
    client, model, messages = _prepare_openai_call(photo_bytes, content_type, message, history)

    def generate():
        try:
            stream = client.chat.completions.create(model=model, messages=messages, max_tokens=500, stream=True)
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except Exception as exc:
            _log_upstream_error_body(exc)
            raise

    return generate()


# --- Path 2: local Florence-2 vision-language model -------------------------

# Used when the chat message is empty -- a plain description, no text input.
_FLORENCE_CAPTION_TASK = "<MORE_DETAILED_CAPTION>"
# The one Florence-2 task that takes free text alongside the task token: a
# caption/phrase to locate in the image, returned as bounding box(es) -- not
# a natural-language answer. Used whenever the chat message is non-empty.
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
    must already be RGB and EXIF-transposed (see caption_image/_ask_florence
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
    per-file) and return the plain-text description. Shared between
    _ask_florence's empty-message case and sync.py's auto-description-on-
    index path, so both describe the image the same way. Raises
    ChatNotConfigured if torch/transformers aren't installed, same as
    ask()."""
    return _run_florence_task(image, _FLORENCE_CAPTION_TASK)


# Fixed phrase sync.py grounds for face detection -- not user-provided text,
# so there's no "empty message" case to handle the way _ask_florence does.
_FLORENCE_FACE_PHRASE = "face"


def detect_faces(image) -> list[dict]:
    """Run Florence-2's phrase-grounding task (_FLORENCE_GROUNDING_TASK)
    with "face" as the phrase, on an already-opened, already RGB, already
    EXIF-transposed image (same precondition as caption_image). Returns
    every detected face as a {"x1","y1","x2","y2"} box in the image's own
    pixel coordinate space -- same convention as ChatReply.boxes, i.e. the
    full-resolution photo's space, not whatever resolution Florence-2
    processed internally. An empty list means no faces were found, which
    is a valid, successful result -- not a failure. Raises
    ChatNotConfigured if torch/transformers aren't installed, same as
    ask()/caption_image()."""
    result = _run_florence_task(image, _FLORENCE_GROUNDING_TASK, _FLORENCE_FACE_PHRASE)
    bboxes = result.get("bboxes", [])
    return [{"x1": x1, "y1": y1, "x2": x2, "y2": y2} for x1, y1, x2, y2 in bboxes]


def _ask_florence(photo_bytes: bytes, message: str) -> ChatReply:
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

    phrase = message.strip()
    if not phrase:
        return ChatReply(text=caption_image(image))

    result = _run_florence_task(image, _FLORENCE_GROUNDING_TASK, phrase)
    text, boxes = _format_grounding_result(result, phrase)
    return ChatReply(text=text, boxes=boxes)


def _format_grounding_result(result: dict, phrase: str) -> tuple[str, list[dict]]:
    """<CAPTION_TO_PHRASE_GROUNDING> returns {"bboxes": [...], "labels":
    [...]} -- box coordinates (in the image_size passed to
    post_process_generation, i.e. the original photo's pixel space), not a
    sentence. Render readable text for the chat transcript, and return the
    boxes themselves (full precision, not rounded like the text) for the
    frontend to draw."""
    bboxes = result.get("bboxes", [])
    labels = result.get("labels", [])
    if not bboxes:
        return f'Florence-2 couldn\'t locate "{phrase}" in this image.', []

    lines = []
    boxes = []
    for label, (x1, y1, x2, y2) in zip(labels, bboxes):
        lines.append(f"- \"{label}\" at approximately ({x1:.0f}, {y1:.0f})-({x2:.0f}, {y2:.0f})")
        boxes.append({"label": label, "x1": x1, "y1": y1, "x2": x2, "y2": y2})

    text = f'Location(s) found for "{phrase}":\n' + "\n".join(lines)
    return text, boxes
