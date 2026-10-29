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
question answering. There's no task token in its public API for "answer this
arbitrary question about the image", so the Florence-2 path always returns a
detailed caption (<MORE_DETAILED_CAPTION>) regardless of what the user typed
-- see run_example() in the model card. The frontend should make this
limitation visible rather than implying it's a general-purpose chat model.

The openai backend can optionally stream its reply back token-by-token
(LUMINARY_OPENAI_STREAMING, off by default) -- see should_stream()/
ask_stream() vs. the always-available, full-reply-at-once ask().
"""

import base64
import io
import logging
import threading

from .config import (
    get_florence_device,
    get_florence_model,
    get_openai_api_key,
    get_openai_base_url,
    get_openai_model,
    get_openai_streaming,
)

logger = logging.getLogger(__name__)


class ChatNotConfigured(Exception):
    """Raised when the selected backend is missing required configuration."""


def ask(backend: str, photo_bytes: bytes, content_type: str, message: str, history: list[dict]) -> str:
    """Ask a question about a photo, returning the full reply at once.
    `history` is a list of {"role": "user"|"assistant", "text": str}
    dicts, oldest first."""
    if backend == "openai":
        return _ask_openai(photo_bytes, content_type, message, history)
    if backend == "florence":
        return _ask_florence(photo_bytes)
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
    response = client.chat.completions.create(model=model, messages=messages, max_tokens=500)
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
        stream = client.chat.completions.create(model=model, messages=messages, max_tokens=500, stream=True)
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    return generate()


# --- Path 2: local Florence-2 vision-language model -------------------------

_FLORENCE_TASK = "<MORE_DETAILED_CAPTION>"

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


def _ask_florence(photo_bytes: bytes) -> str:
    from PIL import Image  # Pillow is a core dependency, always safe to import

    # _load_florence() raises ChatNotConfigured (not ImportError) if
    # torch/transformers aren't installed -- only import torch here, after
    # that's already succeeded, so this function never raises a raw
    # ImportError itself.
    model, processor = _load_florence()
    import torch

    device = get_florence_device()
    dtype = torch.float16 if device.startswith("cuda") else torch.float32

    image = Image.open(io.BytesIO(photo_bytes)).convert("RGB")

    inputs = processor(text=_FLORENCE_TASK, images=image, return_tensors="pt").to(device, dtype)
    generated_ids = model.generate(
        input_ids=inputs["input_ids"],
        pixel_values=inputs["pixel_values"],
        max_new_tokens=1024,
        num_beams=3,
    )
    generated_text = processor.batch_decode(generated_ids, skip_special_tokens=False)[0]
    parsed = processor.post_process_generation(
        generated_text, task=_FLORENCE_TASK, image_size=(image.width, image.height)
    )
    return parsed[_FLORENCE_TASK]
