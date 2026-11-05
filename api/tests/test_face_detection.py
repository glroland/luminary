import io

import numpy as np
import pytest
from PIL import Image

from app import face_detection


def _make_image():
    # face_detection.detect_faces takes an already-decoded, already-RGB PIL
    # Image (same precondition sync.py's rgb_image satisfies).
    buffer = io.BytesIO()
    Image.new("RGB", (100, 100), color="blue").save(buffer, "JPEG")
    return Image.open(io.BytesIO(buffer.getvalue())).convert("RGB")


class _FakeMTCNN:
    """Stands in for facenet_pytorch.MTCNN -- .detect() returns (boxes,
    probs), boxes either an Nx4 numpy array or None ("no faces found")."""

    def __init__(self, boxes, probs=None):
        self._boxes = boxes
        self._probs = probs

    def detect(self, image):
        if self._boxes is None:
            return None, [None]
        return np.array(self._boxes), self._probs if self._probs is not None else [0.99] * len(self._boxes)


@pytest.fixture(autouse=True)
def _reset_detector(monkeypatch):
    # _load_detector caches at module scope -- make sure each test starts
    # from a clean slate instead of reusing whatever a previous test left
    # cached there.
    monkeypatch.setattr(face_detection, "_detector", None)
    monkeypatch.setattr(face_detection, "_detector_threshold", None)
    monkeypatch.setattr(face_detection, "_detector_min_size", None)


def test_detect_faces_returns_boxes_from_mtcnn(monkeypatch):
    monkeypatch.setattr(
        face_detection, "_load_detector", lambda: _FakeMTCNN([[1.0, 2.0, 3.0, 4.0], [5.0, 6.0, 7.0, 8.0]])
    )

    boxes = face_detection.detect_faces(_make_image())

    assert boxes == [
        {"x1": 1.0, "y1": 2.0, "x2": 3.0, "y2": 4.0, "detection_confidence": 0.99},
        {"x1": 5.0, "y1": 6.0, "x2": 7.0, "y2": 8.0, "detection_confidence": 0.99},
    ]


def test_detect_faces_returns_each_box_s_own_detection_confidence(monkeypatch):
    # Regression coverage: detect_faces must pair each box with its own
    # probability (zip, in order) rather than e.g. reusing the first/last
    # one for every box -- the two are reported by MTCNN as parallel
    # sequences, not nested together.
    monkeypatch.setattr(
        face_detection,
        "_load_detector",
        lambda: _FakeMTCNN([[1.0, 2.0, 3.0, 4.0], [5.0, 6.0, 7.0, 8.0]], probs=[0.91, 0.62]),
    )

    boxes = face_detection.detect_faces(_make_image())

    assert [box["detection_confidence"] for box in boxes] == [0.91, 0.62]


def test_detect_faces_returns_an_empty_list_when_no_faces_are_found(monkeypatch):
    monkeypatch.setattr(face_detection, "_load_detector", lambda: _FakeMTCNN(None))

    assert face_detection.detect_faces(_make_image()) == []


def test_detect_faces_clips_boxes_to_the_image_frame(monkeypatch):
    # Regression coverage: unlike Florence-2's <OD> (the detector this
    # replaced), MTCNN.detect() doesn't clip a candidate to the image it
    # ran against -- a face near an edge/corner routinely comes back with
    # a negative x1/y1 or an x2/y2 past the image's own width/height.
    # Confirmed against the real model (not just this fake): a face pasted
    # near a canvas corner produces exactly this shape of out-of-bounds
    # box. Nothing downstream tolerates that (storage, people.py's crop,
    # PhotoModal.jsx's overlay math all assume 0 <= x1 < x2 <= photo.width,
    # same for y) -- an unclipped box renders in the wrong place relative
    # to the photo instead of erroring, which is what this clips against.
    monkeypatch.setattr(
        face_detection,
        "_load_detector",
        lambda: _FakeMTCNN([[-10.0, -5.0, 50.0, 60.0], [70.0, 80.0, 130.0, 140.0]]),
    )

    boxes = face_detection.detect_faces(_make_image())  # 100x100

    assert boxes == [
        {"x1": 0.0, "y1": 0.0, "x2": 50.0, "y2": 60.0, "detection_confidence": 0.99},
        {"x1": 70.0, "y1": 80.0, "x2": 100.0, "y2": 100.0, "detection_confidence": 0.99},
    ]


def test_detect_faces_raises_not_configured_without_torch(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in ("facenet_pytorch",):
            raise ImportError(f"simulated missing {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(face_detection.FaceDetectionNotConfigured):
        face_detection.detect_faces(_make_image())


def test_load_detector_rebuilds_when_threshold_changes(monkeypatch):
    # A changed LUMINARY_FACE_DETECTION_THRESHOLD must take effect on the
    # next detection rather than being stuck with whatever threshold MTCNN
    # happened to be built with the first time -- see _load_detector's
    # docstring.
    built_thresholds = []

    class _FakeMTCNNClass:
        def __init__(self, keep_all, thresholds, min_face_size, device):
            built_thresholds.append(thresholds)

    import facenet_pytorch

    monkeypatch.setattr(facenet_pytorch, "MTCNN", _FakeMTCNNClass)

    monkeypatch.setenv("LUMINARY_FACE_DETECTION_THRESHOLD", "0.5")
    face_detection._load_detector()
    monkeypatch.setenv("LUMINARY_FACE_DETECTION_THRESHOLD", "0.3")
    face_detection._load_detector()

    assert built_thresholds == [[0.5, 0.5, 0.5], [0.3, 0.3, 0.3]]


def test_load_detector_rebuilds_when_min_size_changes(monkeypatch):
    # Same rebuild-on-change behavior as the threshold above, but for
    # LUMINARY_FACE_MIN_SIZE.
    built_min_sizes = []

    class _FakeMTCNNClass:
        def __init__(self, keep_all, thresholds, min_face_size, device):
            built_min_sizes.append(min_face_size)

    import facenet_pytorch

    monkeypatch.setattr(facenet_pytorch, "MTCNN", _FakeMTCNNClass)

    monkeypatch.setenv("LUMINARY_FACE_MIN_SIZE", "20")
    face_detection._load_detector()
    monkeypatch.setenv("LUMINARY_FACE_MIN_SIZE", "40")
    face_detection._load_detector()

    assert built_min_sizes == [20, 40]


def test_load_detector_defaults_min_size_to_150(monkeypatch):
    # facenet-pytorch's own MTCNN default (20) was confirmed too permissive
    # in practice -- see config.get_face_min_size()'s docstring.
    built_min_sizes = []

    class _FakeMTCNNClass:
        def __init__(self, keep_all, thresholds, min_face_size, device):
            built_min_sizes.append(min_face_size)

    import facenet_pytorch

    monkeypatch.setattr(facenet_pytorch, "MTCNN", _FakeMTCNNClass)
    monkeypatch.delenv("LUMINARY_FACE_MIN_SIZE", raising=False)

    face_detection._load_detector()

    assert built_min_sizes == [150]
