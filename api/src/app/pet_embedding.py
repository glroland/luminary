"""Pet *recognition* embeddings -- turning a cropped cat/dog into a vector so
recognition.py can decide *which* pet it is, the way its InceptionResnetV1
does for human faces (that model only knows human identities, so it can't
tell two cats apart).

The model is DINOv2 (`LUMINARY_PET_EMBEDDING_MODEL`, default
`facebook/dinov2-small`) through `transformers`, which is already a
dependency for Florence-2 -- no new package. The embedding is the CLS token
concatenated with the mean of the patch tokens, L2-normalized, so cosine
similarity is a dot product. The crop is squashed to 224x224 rather than
centre-cropped (the model's own processor would cut off the ends of a long
animal); preprocessing is done by hand with the ImageNet mean/std DINOv2 was
trained with, which keeps this independent of the processor's resize quirks.

Same lazy-load/cached-under-a-lock/own-exception shape as
recognition._load_embedder: torch/transformers are imported inside the
loader, and a missing dependency or unavailable weights raises
`PetRecognitionNotConfigured`, which recognition.py treats as "skip pets
this run", never as a failure of the human matching.
"""

import logging
import threading

from PIL import Image

from .config import get_pet_embedding_model

logger = logging.getLogger(__name__)

_INPUT_SIZE = 224
_IMAGENET_MEAN = (0.485, 0.456, 0.406)
_IMAGENET_STD = (0.229, 0.224, 0.225)

_lock = threading.Lock()
_model = None
_model_name = None


class PetRecognitionNotConfigured(Exception):
    """Raised when torch/transformers (or the model's weights) aren't
    available -- mirrors recognition.FaceRecognitionNotConfigured."""


def _load_model():
    """Lazily load and cache the model, rebuilding it if
    LUMINARY_PET_EMBEDDING_MODEL changed since it was loaded."""
    global _model, _model_name

    name = get_pet_embedding_model()
    with _lock:
        if _model is not None and _model_name == name:
            return _model

        try:
            from transformers import AutoModel
        except ImportError as exc:
            raise PetRecognitionNotConfigured(
                "Pet recognition requires the torch/transformers dependencies to be installed."
            ) from exc

        logger.info("Loading pet recognition model (%s, first use only)", name)
        try:
            _model = AutoModel.from_pretrained(name).eval()
        except Exception as exc:
            # Weights not cached and no network (offline model cache, a
            # restricted cluster): "not configured", like a missing package.
            raise PetRecognitionNotConfigured(f"Pet recognition model {name!r} could not be loaded: {exc}") from exc
        _model_name = name
        logger.info("Pet recognition model ready")
        return _model


def ensure_configured() -> None:
    """Forces the lazy load without needing an image."""
    _load_model()


def embed(image: Image.Image) -> list[float]:
    """Embed a pet crop (any size/aspect ratio) into an L2-normalized
    vector. Raises PetRecognitionNotConfigured if the model can't be
    loaded. `_load_model` is called first so a missing dependency surfaces
    as that exception before the bare imports below, which assume it
    succeeded -- same ordering as recognition._embed."""
    model = _load_model()
    import torch
    from torchvision.transforms.functional import normalize, pil_to_tensor

    resized = image.convert("RGB").resize((_INPUT_SIZE, _INPUT_SIZE))
    tensor = normalize(pil_to_tensor(resized).float() / 255.0, list(_IMAGENET_MEAN), list(_IMAGENET_STD))
    with torch.no_grad():
        hidden = model(pixel_values=tensor.unsqueeze(0)).last_hidden_state[0]
    vector = torch.cat([hidden[0], hidden[1:].mean(dim=0)])
    vector = vector / vector.norm().clamp_min(1e-12)
    return vector.tolist()
