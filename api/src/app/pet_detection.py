"""Pet *detection* for sync.py's indexing pipeline -- finding the cats and
dogs in a photo, the same job face_detection.py does for human faces.

Where face_detection.py finds a *face* (MTCNN), this finds the whole
*animal*: pets are photographed side-on, from behind, curled up asleep, and
a face-only detector would miss most of those. The model is torchvision's
COCO-trained Faster R-CNN (MobileNetV3-Large FPN backbone, the lightweight
CPU-friendly variant); only COCO's `cat` and `dog` classes are kept. torch
and torchvision are already dependencies (facenet-pytorch needs them), so
this adds no package -- only the weights, which `api/download_models.py`
caches alongside the others.

A box is the same `{"x1","y1","x2","y2","detection_confidence"}` dict
face_detection.detect_faces returns, plus `kind: "pet"` and
`species: "cat" | "dog"`, so storage.replace_faces stores both the same
way. Coordinates are in the passed image's own pixel space (full
resolution -- the image is downscaled only for inference, then the boxes
are scaled back).

Like every other ML dependency here, torch/torchvision are imported lazily
inside the loader, so the app imports cleanly without them; a missing
dependency raises PetDetectionNotConfigured, which sync.py treats as "skip
pets for this photo", never as a failure of the photo's human detection.
"""

import logging
import threading

from .config import get_pet_detection_threshold

logger = logging.getLogger(__name__)

# COCO class name -> the species stored in faces.species. Cat and dog only
# for now; adding e.g. "bird"/"horse" here is all the detector needs.
PET_SPECIES = ("cat", "dog")

# The longest side images are downscaled to before inference. Phone
# originals are 10+ megapixels; the detector resizes internally anyway
# (this model's default is ~800-1333px), so handing it the full-resolution
# tensor only costs memory.
_MAX_INFERENCE_SIDE = 1333

# A box that overlaps a higher-scoring one this much is the same animal
# labelled twice (torchvision's NMS is per class, so a cat/dog confusion
# yields both).
_CROSS_CLASS_IOU = 0.7

_detector_lock = threading.Lock()
_detector = None
_detector_threshold = None


class PetDetectionNotConfigured(Exception):
    """Raised when torch/torchvision (or the detector's weights) aren't
    available -- mirrors face_detection.FaceDetectionNotConfigured."""


def _load_detector():
    """Lazily load and cache the detector under a lock, rebuilding it if
    LUMINARY_PET_DETECTION_THRESHOLD changed since it was loaded (same
    reasoning as face_detection._load_detector). Returns (model,
    {coco label index: species})."""
    global _detector, _detector_threshold

    threshold = get_pet_detection_threshold()
    with _detector_lock:
        if _detector is not None and _detector_threshold == threshold:
            return _detector

        try:
            from torchvision.models.detection import (
                FasterRCNN_MobileNet_V3_Large_FPN_Weights,
                fasterrcnn_mobilenet_v3_large_fpn,
            )
        except ImportError as exc:
            raise PetDetectionNotConfigured(
                "Pet detection requires the torch/torchvision dependencies to be installed."
            ) from exc

        logger.info("Loading pet detection model (Faster R-CNN MobileNetV3, threshold=%s, first use only)", threshold)
        weights = FasterRCNN_MobileNet_V3_Large_FPN_Weights.DEFAULT
        try:
            model = fasterrcnn_mobilenet_v3_large_fpn(weights=weights, box_score_thresh=threshold).eval()
        except Exception as exc:
            # Weights not cached and no network (LUMINARY_USE_MODEL_CACHE
            # offline mode, a restricted cluster): "not configured", same
            # as a missing package, not a per-photo error.
            raise PetDetectionNotConfigured(f"Pet detection model could not be loaded: {exc}") from exc

        categories = weights.meta["categories"]
        labels = {index: name for index, name in enumerate(categories) if name in PET_SPECIES}
        _detector = (model, labels)
        _detector_threshold = threshold
        logger.info("Pet detection model ready")
        return _detector


def ensure_configured() -> None:
    """Forces the lazy model load without needing an image -- for a caller
    that must know the detector works *before* it touches any photo (the
    pets backfill job)."""
    _load_detector()


def _iou(a: dict, b: dict) -> float:
    width = min(a["x2"], b["x2"]) - max(a["x1"], b["x1"])
    height = min(a["y2"], b["y2"]) - max(a["y1"], b["y1"])
    if width <= 0 or height <= 0:
        return 0.0
    inter = width * height
    union = (a["x2"] - a["x1"]) * (a["y2"] - a["y1"]) + (b["x2"] - b["x1"]) * (b["y2"] - b["y1"]) - inter
    return inter / union if union > 0 else 0.0


def detect_pets(image) -> list[dict]:
    """Run the detector on an already-opened, already RGB, already
    EXIF-transposed PIL image (same precondition as
    face_detection.detect_faces) and return every cat/dog as a box in the
    image's own pixel space, clipped to its frame. An empty list means no
    pets were found, which is a valid, successful result. Raises
    PetDetectionNotConfigured if the detector can't be loaded."""
    model, labels = _load_detector()

    import torch
    from torchvision.transforms.functional import pil_to_tensor

    width, height = image.size
    scale = min(1.0, _MAX_INFERENCE_SIDE / max(width, height))
    inference_image = image
    if scale < 1.0:
        inference_image = image.resize((max(1, round(width * scale)), max(1, round(height * scale))))

    tensor = pil_to_tensor(inference_image).float() / 255.0
    with torch.no_grad():
        output = model([tensor])[0]

    candidates = []
    for box, label, score in zip(output["boxes"].tolist(), output["labels"].tolist(), output["scores"].tolist()):
        species = labels.get(int(label))
        if species is None:
            continue
        x1, y1, x2, y2 = (coordinate / scale for coordinate in box)
        candidates.append(
            {
                "x1": float(max(0.0, min(x1, width))),
                "y1": float(max(0.0, min(y1, height))),
                "x2": float(max(0.0, min(x2, width))),
                "y2": float(max(0.0, min(y2, height))),
                "detection_confidence": float(score),
                "kind": "pet",
                "species": species,
            }
        )

    # Best score first; drop a box that's mostly a better one under the
    # other label.
    candidates.sort(key=lambda candidate: candidate["detection_confidence"], reverse=True)
    kept: list[dict] = []
    for candidate in candidates:
        if all(_iou(candidate, other) < _CROSS_CLASS_IOU for other in kept):
            kept.append(candidate)
    return kept
