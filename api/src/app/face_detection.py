"""Face *detection* for sync.py's indexing pipeline -- finding where in a
photo the faces are, as opposed to recognition.py's job of deciding *whose*
face an already-found box is.

This used to be built on Florence-2's <OD> (general object detection) task,
filtering to detections whose label mentioned "face" (see chat.py's module
docstring for that history). Confirmed in practice to miss real faces --
partial/angled/small, or in a crowded group photo -- regardless of how that
task was tuned (more beam search width didn't fix it): <OD> is a general
object detector that happens to label some boxes "human face," not a model
trained to find faces, and its output carries no per-box confidence score
to loosen in the first place.

Replaced with facenet-pytorch's MTCNN -- a purpose-built, multi-scale face
detector (an image pyramid across a 3-stage cascade, so it looks for faces
at many sizes, not just whatever scale the input happens to be resized to)
with a real per-candidate confidence score. Already a dependency of this
app for recognition.py's embedding step, so this adds no new heavy
dependency -- just a second use of facenet-pytorch alongside the
InceptionResnetV1 embedder. recognition.py still deliberately uses only
facenet-pytorch's embedding half for *that* step (see its own module
docstring): no face-alignment step happens there, or here -- detect_faces()
below returns MTCNN's raw boxes with no eye/landmark-based rotation or
cropping. Lazily loaded and cached at module scope under a lock, same shape
as chat.py's _load_florence()/recognition.py's _load_embedder(): heavy
optional dependency, deferred import, raises FaceDetectionNotConfigured
(mirroring ChatNotConfigured/FaceRecognitionNotConfigured) if torch/
facenet-pytorch aren't installed, so sync.py can treat a missing dependency
as "leave detection for a later sync," exactly as it already does for
Florence-2 captioning.

Two knobs, both config.py-backed and read fresh (rebuilding the cached
MTCNN if either changes -- see _load_detector()): LUMINARY_FACE_DETECTION_
THRESHOLD (confidence to report a candidate; see
config.get_face_detection_threshold()'s own docstring) and
LUMINARY_FACE_MIN_SIZE (the smallest face, in pixels of MTCNN's own
internally-resized copy of the image -- not the original photo's
resolution -- that its pyramid will scan down to; see
config.get_face_min_size()'s docstring). Smaller catches more distant/small
faces at the cost of more false positives and slower detection (more
pyramid scales to scan); not independently verified against a specific
pixel threshold in this app the way LUMINARY_FACE_DETECTION_THRESHOLD's
0.6->0.65 change was -- tune both against your own library's real photos.
"""

import logging
import threading

from .config import get_face_detection_threshold, get_face_min_size

logger = logging.getLogger(__name__)

_detector_lock = threading.Lock()
_detector = None
_detector_threshold = None
_detector_min_size = None


class FaceDetectionNotConfigured(Exception):
    """Raised when the torch/facenet-pytorch dependencies aren't
    installed -- mirrors chat.ChatNotConfigured/
    recognition.FaceRecognitionNotConfigured."""


def _load_detector():
    """Lazily load and cache MTCNN, under a lock, on first use -- mirrors
    chat.py's _load_florence()/recognition.py's _load_embedder(). Re-reads
    LUMINARY_FACE_DETECTION_THRESHOLD/LUMINARY_FACE_MIN_SIZE and rebuilds if
    either has changed since the model was last loaded, so a test (or a
    config change in a long-lived process) isn't stuck with whatever
    values happened to be set the first time this ran."""
    global _detector, _detector_threshold, _detector_min_size

    threshold = get_face_detection_threshold()
    min_size = get_face_min_size()
    with _detector_lock:
        if _detector is not None and _detector_threshold == threshold and _detector_min_size == min_size:
            return _detector

        try:
            from facenet_pytorch import MTCNN
        except ImportError as exc:
            raise FaceDetectionNotConfigured(
                "Face detection requires the torch/facenet-pytorch dependencies to be installed."
            ) from exc

        logger.info(
            "Loading face detection model (MTCNN, threshold=%s, min_size=%s, first use only)", threshold, min_size
        )
        # Same threshold applied to all 3 cascade stages (P-Net/R-Net/
        # O-Net) rather than tuned independently -- MTCNN's own default is
        # [0.6, 0.7, 0.7], a reasonable starting point to deviate from
        # uniformly rather than a precise target to preserve. min_face_size
        # is MTCNN's own knob for the minimum face size (in pixels of its
        # internally-resized copy of the image) its pyramid will scan down
        # to -- its own default, 20, is also ours; see
        # config.get_face_min_size()'s docstring.
        _detector = MTCNN(
            keep_all=True,
            thresholds=[threshold, threshold, threshold],
            min_face_size=min_size,
            device="cpu",
        )
        _detector_threshold = threshold
        _detector_min_size = min_size
        logger.info("Face detection model ready")
        return _detector


def ensure_configured() -> None:
    """Forces the lazy model load (_load_detector) without needing an
    image, raising FaceDetectionNotConfigured if torch/facenet-pytorch
    aren't installed. For a caller (recognition.py's full "Refresh Faces"
    redetect) that's about to take an action it can't undo if detection
    doesn't actually work -- replacing a photo's existing faces with
    nothing -- and so needs to know *before* touching any photo, not
    discover it photo-by-photo after already wiping some of them."""
    _load_detector()


def detect_faces(image) -> list[dict]:
    """Run MTCNN on an already-opened, already RGB, already EXIF-transposed
    image (same precondition chat.caption_image has), and return every
    detected face as a {"x1","y1","x2","y2","detection_confidence"} box in
    the image's own pixel coordinate space -- same x1/y1/x2/y2 convention
    chat.ChatReply.boxes uses, i.e. the full-resolution photo's space.
    `detection_confidence` is MTCNN's own per-candidate probability (the
    final cascade stage's score, same value LUMINARY_FACE_DETECTION_
    THRESHOLD is compared against to decide whether a candidate is
    reported at all -- see config.get_face_detection_threshold()'s
    docstring) -- distinct from faces.match_confidence, which is
    recognition.py's unrelated cosine-similarity score from matching an
    already-detected face against a reference embedding. An empty list
    means no faces were found, which is a valid, successful result -- not
    a failure. Raises FaceDetectionNotConfigured if torch/facenet-pytorch
    aren't installed, same as recognition.py's _embed()."""
    detector = _load_detector()
    boxes, probs = detector.detect(image)
    if boxes is None:
        return []
    width, height = image.size
    results = []
    for (x1, y1, x2, y2), prob in zip(boxes, probs):
        # MTCNN.detect() doesn't clip a candidate to the image frame the
        # way Florence-2's <OD> (the detector this replaced) did -- a face
        # near an edge/corner routinely comes back with a negative x1/y1 or
        # an x2/y2 past the image's own width/height. Nothing downstream
        # (storage, the crop people.py takes for a representative
        # thumbnail, PhotoModal.jsx's overlay math, which assumes
        # 0 <= x1 < x2 <= photo.width and the same for y) expects or
        # tolerates that -- an out-of-bounds box there doesn't error, it
        # just renders in the wrong place relative to the photo, which is
        # what an uncapped box here was causing. Clip to the frame rather
        # than drop the detection: the face is still real and still
        # (mostly) in the photo.
        results.append(
            {
                "x1": float(max(0.0, min(x1, width))),
                "y1": float(max(0.0, min(y1, height))),
                "x2": float(max(0.0, min(x2, width))),
                "y2": float(max(0.0, min(y2, height))),
                "detection_confidence": float(prob),
            }
        )
    return results
