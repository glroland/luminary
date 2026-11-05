"""Automatic face recognition ("Refresh Faces", POST /api/faces/refresh):
matches already-detected-but-untagged faces (sync.py's face_detection.py
step -- this module never detects anything, only recognizes) against
the individuals a user has already manually tagged at least one face for
(people.tag_face), auto-assigning `faces.person_id` wherever the match is
confident enough. The goal is to need exactly one manual tag per person,
not one per photo they appear in.

start_refresh() has two callers: the standalone "Refresh Faces" button
(routers/faces.py, for on-demand re-matching -- e.g. right after manually
tagging someone, without waiting for/triggering a full resync) and, as of
sync.py's _trigger_face_refresh, every successful "Refresh" (sync) too, so
newly detected faces get matched without a second click. try_start()'s
"already running" guard (below) makes the two mutually harmless -- whichever
gets there first just gets polled to completion by the other.

The two callers ask for different scopes via start_refresh(full=...).
sync.py's post-sync trigger always passes the default, `full=False`:
incremental, only ever matching still-untagged faces (candidate_faces()'s
`person_id IS NULL`), cheap/idempotent to run after every single sync.

routers/faces.py's button passes `full=True`, which is a fundamentally
bigger operation: the premise (per the person clicking it) is that
LUMINARY_FACE_DETECTION_THRESHOLD/LUMINARY_FACE_MIN_SIZE were just
changed, which means every existing face *box* in the library -- not just
the person_id tags on them -- was detected under settings that no longer
apply, and is itself stale. So `full=True` doesn't just re-match; it
re-runs face_detection.detect_faces for every already-indexed photo
against the *current* settings and replaces that photo's faces
(storage.replace_faces) before matching starts at all (_redetect_photo_
faces/_redetect_all_faces). storage.replace_faces deletes-then-reinserts
with fresh ids -- the exact same "re-detection also wipes tags" behavior
sync.py's own content-change path already has (see its module docstring),
just triggered by a settings change instead of a file change. That means
a full refresh deletes *every* tag in the library, manual or auto, not
only auto ones -- a box detected under the old sensitivity may not even
be the same face (or exist at all) under the new one, so a tag pointing
at it is exactly as stale as the box itself. This is a deliberate,
confirmed trade-off (not a bug): after a full refresh, every individual
needs at least one face manually re-tagged before _reference_embeddings
has anything to match future candidates against again -- the matching
phase that follows will legitimately report 0 matches until that happens.

Embeddings come from facenet-pytorch's `InceptionResnetV1(pretrained=
"vggface2")` -- a torch-native model, deliberately not DeepFace (which
would additionally require TensorFlow, a second multi-GB ML framework,
alongside the torch/transformers stack Florence-2 already requires -- see
requirements.txt's comment). This module only ever uses that embedding
half, applied to crops of boxes that already exist -- the boxes themselves
come from sync.py's indexing pipeline, via face_detection.py (which *does*
use facenet-pytorch's own MTCNN, as of switching face detection off of
Florence-2's <OD> task -- see that module's docstring for why). This
module still doesn't use MTCNN's alignment step (eye/landmark-based
rotation/cropping before embedding) -- see below.

Two safety invariants, both load-bearing for correctness -- both are
about the *matching* step specifically, and deliberately don't apply to
full=True's redetect step above, which is a different, explicitly
destructive operation the person triggering it has already opted into:

1. Auto-tagging (_process_photo_faces) only ever writes to faces where
   `person_id IS NULL`. It never overwrites an existing tag, manual or
   auto -- a manual correction can never be clobbered by matching itself,
   and an incremental (`full=False`) run is cheap/idempotent after the
   first pass (only genuinely new untagged faces, e.g. from a later sync,
   do any work).
2. Reference embeddings (_reference_embeddings) are built *only* from
   `tag_source = 'manual'` faces, never from previously auto-matched ones.
   An incorrect auto-match must never become a future reference that
   reinforces itself -- that compounding-error path is exactly what this
   restriction closes off.

No embeddings are persisted -- every refresh run recomputes reference
embeddings (cheap: usually few manually-tagged faces per person, and their
crops are already sitting in `faces.thumbnail`) and re-embeds/re-compares
every still-untagged face (simplest correct behavior: a face that didn't
match last run might match this run if someone tagged more reference faces
for that person since). Revisit only if a real library size makes that
full rescan too slow.

Matching is cosine similarity against `config.get_face_match_threshold()`
(`LUMINARY_FACE_MATCH_THRESHOLD`). Confirmed empirically (not just assumed)
that `InceptionResnetV1`'s output is L2-normalized -- `model(tensor)[0]
.norm()` comes back ~1.0 -- so cosine similarity is a mathematically valid
choice of metric here, as opposed to e.g. raw (non-normalized) Euclidean
distance. That said, there's no reliable published "same person" cosine
cutoff for this exact model/weights combination (unlike the original
FaceNet paper's well-known L2-distance threshold on *its* embeddings) --
the right value is something to tune against your own library's real
photos, not a number to trust blindly. If a run matches nothing,
`_run_refresh` logs the single closest unmatched candidate's actual score
at INFO (so it's visible without changing `LUMINARY_LOG_LEVEL`) -- that's
the number to look at before assuming the threshold, not the code, is
wrong. Setting `LUMINARY_LOG_LEVEL=DEBUG` additionally logs every
candidate's best-match score in `_process_photo_faces`. Both log lines
name the photo's *filename* and the individual's *name* (via
`_individual_names()`), not just `face_id`/`person_id` UUIDs -- an
operator debugging "why didn't this match" needs to go look at the actual
photo and person, not resolve two ids by hand first.

Candidate crops come straight from face_detection.py's MTCNN box
(`sync.py`/`face_detection.detect_faces`), with no face *alignment* step
(no eye/landmark-based rotation/scaling) before embedding -- this module
deliberately doesn't call MTCNN's own alignment-aware detect-and-crop path
(see above), even though MTCNN is now in the pipeline for detection
itself. A loose or off-center box, or the lack of alignment, can suppress
genuine same-person similarity scores across the board, not just for one
unlucky photo -- if real same-person pairs are consistently scoring lower
than expected, this is as likely an explanation as the threshold itself
being wrong.

Whenever _process_photo_faces auto-tags at least one face in a photo, it
also calls enrich.enrich_description(photo_id) once for that photo (not
once per face matched) -- `photos.description` (see enrich.py's module
docstring) is partly composed from the names of individuals tagged in a
photo, so an auto-match here is one of the things that invalidates/
recomputes it, same as people.py's manual tag_face/untag_face.
"""

import asyncio
import io
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from PIL import Image, ImageOps

from . import enrich, face_detection, people, storage
from .config import get_face_match_threshold, get_max_concurrency
from .database import get_connection

logger = logging.getLogger(__name__)

# Read once at import time -- see config.get_max_concurrency()'s docstring
# for why this isn't re-read per refresh run the way other LUMINARY_*
# settings are read lazily elsewhere in this app.
MAX_CONCURRENCY = get_max_concurrency()
_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENCY)

_embedder_lock = threading.Lock()
_embedder = None


class FaceRecognitionNotConfigured(Exception):
    """Raised when the torch/facenet-pytorch dependencies aren't
    installed -- mirrors chat.ChatNotConfigured."""


def _load_embedder():
    """Lazily load and cache facenet-pytorch's embedding model, under a
    lock, on first use -- mirrors chat.py's _load_florence() exactly (see
    its docstring/comments for the rationale: heavy optional dependency,
    deferred import, cached for the life of the process)."""
    global _embedder
    with _embedder_lock:
        if _embedder is not None:
            return _embedder

        try:
            from facenet_pytorch import InceptionResnetV1
        except ImportError as exc:
            raise FaceRecognitionNotConfigured(
                "Face recognition requires the torch/facenet-pytorch dependencies to be installed."
            ) from exc

        logger.info("Loading face recognition model (first use only)")
        _embedder = InceptionResnetV1(pretrained="vggface2").eval()
        logger.info("Face recognition model ready")
        return _embedder


def _embed(image: Image.Image) -> list[float]:
    """Embed a face crop (any size/aspect ratio -- resized here to the
    160x160 InceptionResnetV1 expects) into a 512-d vector. `_load_embedder`
    is called first so a missing dependency surfaces as
    FaceRecognitionNotConfigured before the bare imports below, which
    assume it already succeeded -- same ordering chat.py's
    _run_florence_task uses relative to _load_florence."""
    model = _load_embedder()
    import torch
    from facenet_pytorch import fixed_image_standardization
    from torchvision.transforms.functional import pil_to_tensor

    resized = image.convert("RGB").resize((160, 160))
    tensor = fixed_image_standardization(pil_to_tensor(resized).float()).unsqueeze(0)
    with torch.no_grad():
        embedding = model(tensor)[0]
    return embedding.tolist()


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _mean_embedding(embeddings: list[list[float]]) -> list[float]:
    length = len(embeddings[0])
    return [sum(embedding[i] for embedding in embeddings) / len(embeddings) for i in range(length)]


def _backfill_missing_reference_thumbnails() -> None:
    """A face manually tagged *before* `faces.thumbnail` existed has
    `person_id`/`tag_source = 'manual'` set but `thumbnail` still NULL
    forever -- a plain `ALTER TABLE ... ADD COLUMN` never backfills
    existing rows (same gap `database.init_db()`'s `tag_source` backfill
    closes for that column), but unlike that one, fixing this needs real
    I/O (re-reading the photo's original and re-cropping it), not just a
    SQL `UPDATE`, so it can't live in `init_db()` without that module
    importing `storage`/`people` and creating a circular import. Run once
    per refresh, lazily, right before `_reference_embeddings()` needs
    those thumbnails -- a no-op (one cheap query, zero rows) once
    everything's backfilled, so there's no reason to gate this behind a
    separate one-time migration step.

    Without this, `_reference_embeddings()`'s `thumbnail IS NOT NULL`
    filter silently excludes every face tagged this way, so that
    individual has *no* reference embedding at all and can never be
    auto-matched against, no matter what `LUMINARY_FACE_MATCH_THRESHOLD`
    is set to."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, photo_id, x1, y1, x2, y2 FROM faces "
            "WHERE tag_source = 'manual' AND person_id IS NOT NULL AND thumbnail IS NULL"
        ).fetchall()
    if not rows:
        return

    by_photo: dict[str, list[dict]] = {}
    for row in rows:
        by_photo.setdefault(row["photo_id"], []).append(dict(row))

    backfilled = 0
    for photo_id, faces in by_photo.items():
        with get_connection() as conn:
            photo = conn.execute("SELECT filename FROM photos WHERE id = %s", (photo_id,)).fetchone()
        if not photo:
            continue
        try:
            original_bytes = storage.read_original_bytes(dict(photo))
        except Exception:
            logger.warning(
                "Could not read original for photo %s while backfilling face thumbnails", photo_id, exc_info=True
            )
            continue
        if not original_bytes:
            continue
        for face in faces:
            thumbnail = people.crop_face_thumbnail(original_bytes, face)
            if not thumbnail:
                continue
            with get_connection() as conn:
                conn.execute("UPDATE faces SET thumbnail = %s WHERE id = %s", (thumbnail, face["id"]))
                conn.commit()
            backfilled += 1

    if backfilled:
        logger.info("Backfilled missing thumbnails for %d pre-existing manually tagged face(s)", backfilled)


def _reference_embeddings() -> dict[str, list[float]]:
    """One mean-pooled embedding per individual, from their manually
    tagged faces' already-stored thumbnails only (see this module's
    docstring for why never auto-matched ones). Individuals with no
    manually tagged face are absent from the result -- nothing to match
    candidates against yet."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT person_id, thumbnail FROM faces "
            "WHERE tag_source = 'manual' AND person_id IS NOT NULL AND thumbnail IS NOT NULL"
        ).fetchall()

    by_person: dict[str, list[list[float]]] = {}
    for row in rows:
        try:
            image = Image.open(io.BytesIO(row["thumbnail"]))
        except Exception:
            logger.warning("Could not decode a manually tagged face's thumbnail; skipping it", exc_info=True)
            continue
        by_person.setdefault(row["person_id"], []).append(_embed(image))

    return {person_id: _mean_embedding(embeddings) for person_id, embeddings in by_person.items()}


def _photos_with_tagged_faces() -> list[str]:
    """Distinct ids of every photo with at least one tagged face (manual
    or auto) -- read *before* _redetect_all_faces's storage.replace_faces
    calls wipe every photo's faces (tags included), the only way to know
    afterward which photos' "who's in this photo" facts just changed, so
    enrich.enrich_description can be regenerated for them. Mirrors
    people.delete_individual's own "read affected photo ids before the
    bulk clear" pattern, just scoped to the whole library instead of one
    individual."""
    with get_connection() as conn:
        rows = conn.execute("SELECT DISTINCT photo_id FROM faces WHERE person_id IS NOT NULL").fetchall()
        return [row["photo_id"] for row in rows]


def _list_photos() -> list[dict]:
    with get_connection() as conn:
        return [dict(row) for row in conn.execute("SELECT id, filename FROM photos").fetchall()]


def _redetect_photo_faces(photo: dict) -> Optional[int]:
    """full=True's per-photo redetect step: re-runs face_detection.
    detect_faces against the *current* LUMINARY_FACE_DETECTION_THRESHOLD/
    LUMINARY_FACE_MIN_SIZE for one already-indexed photo and replaces its
    stored faces with the result (storage.replace_faces). Same EXIF-
    transpose-then-RGB decode as people.crop_face_thumbnail/sync.py's own
    detection step -- detect_faces's box coordinates are only meaningful
    in that same pixel space.

    storage.replace_faces deletes every one of this photo's existing face
    rows first, manual or auto tags included, before inserting the fresh
    ones -- same already-accepted "re-detection also wipes tags" behavior
    sync.py's own content-change path has (see its module docstring). This
    is the entire point of a full refresh: a box detected under the old
    sensitivity is stale, and a tag pointing at it is exactly as stale.

    Returns how many faces were (re)detected, or None if the original
    couldn't be read/decoded this time -- in that case this photo's
    *existing* faces are left completely alone (storage.replace_faces is
    never called) rather than wiped with nothing to replace them, since
    "detection didn't run" must not look the same as "detection ran and
    found zero faces". Best-effort, same shape as sync.py's own per-file
    error handling: logged, never fails the whole refresh."""
    try:
        original_bytes = storage.read_original_bytes(photo)
    except Exception:
        logger.warning("Could not read original for photo %s while redetecting faces", photo["id"], exc_info=True)
        return None
    if not original_bytes:
        return None

    try:
        with Image.open(io.BytesIO(original_bytes)) as raw_image:
            with ImageOps.exif_transpose(raw_image) as transposed:
                image = transposed.convert("RGB")
    except Exception:
        logger.warning("Could not decode original for photo %s while redetecting faces", photo["id"], exc_info=True)
        return None

    boxes = face_detection.detect_faces(image)
    storage.replace_faces(photo["id"], boxes)
    return len(boxes)


def _candidate_faces() -> list[dict]:
    """Every currently-untagged detected face, library-wide -- what this
    run will try to match. See storage.replace_faces for why a photo whose
    content changes produces fresh untagged candidates here too (its old
    tags, manual or auto, don't survive re-detection)."""
    with get_connection() as conn:
        rows = conn.execute("SELECT id, photo_id, x1, y1, x2, y2 FROM faces WHERE person_id IS NULL").fetchall()
        return [dict(row) for row in rows]


def _individual_names() -> dict[str, str]:
    """id -> name for every catalogued individual -- purely for making log
    lines human-readable (filenames/names, not UUIDs an operator would
    have to go look up by hand to debug a "why didn't this match"
    question). Never used for matching itself, only logging."""
    with get_connection() as conn:
        rows = conn.execute("SELECT id, name FROM individuals").fetchall()
        return {row["id"]: row["name"] for row in rows}


def _process_photo_faces(
    photo_id: str,
    faces: list[dict],
    references: dict[str, list[float]],
    threshold: float,
    individual_names: dict[str, str],
) -> tuple[int, Optional[tuple[float, str, str, str]]]:
    """Runs on the executor thread: reads `photo_id`'s original once (even
    if it has several candidate faces), then embeds+matches each face in
    `faces` against `references`. Returns (how many got auto-tagged, the
    best-scoring *unmatched* candidate seen here as (score, person_id,
    face_id, photo_filename), or None if every face here matched or had no
    reference to compare against) -- _run_refresh aggregates the latter
    across every photo so it can log the single closest near-miss when a
    whole run matches nothing, which is the only real diagnostic for "is
    the threshold wrong, or is nothing even close". `photo_filename` (and
    `individual_names`, used only for the debug line below) are there so
    that log line names things an operator can actually go look at --
    filenames and individual names, not photo_id/person_id/face_id UUIDs.

    Best-effort like sync.py's own thumbnailing/captioning: a photo whose
    original can't be read, or a face whose crop can't be decoded, is
    skipped (logged), never fails the whole run."""
    with get_connection() as conn:
        photo = conn.execute("SELECT filename FROM photos WHERE id = %s", (photo_id,)).fetchone()
    if not photo:
        return 0, None  # deleted since the candidate list was built
    photo_filename = photo["filename"]

    try:
        original_bytes = storage.read_original_bytes(dict(photo))
    except Exception:
        logger.warning("Could not read original for photo %s from the photos backend", photo_id, exc_info=True)
        return 0, None
    if not original_bytes:
        return 0, None

    matched = 0
    best_unmatched: Optional[tuple[float, str, str, str]] = None
    for face in faces:
        thumbnail = people.crop_face_thumbnail(original_bytes, face)
        if not thumbnail:
            continue
        try:
            image = Image.open(io.BytesIO(thumbnail))
        except Exception:
            logger.warning("Could not decode a cropped candidate face; skipping it", exc_info=True)
            continue
        embedding = _embed(image)

        best_person_id: Optional[str] = None
        best_score = 0.0
        for person_id, reference in references.items():
            score = _cosine_similarity(embedding, reference)
            if score > best_score:
                best_person_id, best_score = person_id, score

        best_name = individual_names.get(best_person_id, best_person_id) if best_person_id else None
        logger.debug(
            "Candidate face %s in %s: best match %s scored %.4f (threshold %.4f)",
            face["id"],
            photo_filename,
            best_name,
            best_score,
            threshold,
        )

        if best_person_id is None or best_score < threshold:
            if best_person_id is not None and (best_unmatched is None or best_score > best_unmatched[0]):
                best_unmatched = (best_score, best_person_id, face["id"], photo_filename)
            continue

        # The `AND person_id IS NULL` guard (and checking rowcount rather
        # than assuming success) means a face tagged by something else
        # between _candidate_faces() building the list and now is left
        # alone -- invariant 1 in this module's docstring, enforced at the
        # write itself rather than trusted from the earlier read.
        with get_connection() as conn:
            cursor = conn.execute(
                "UPDATE faces SET person_id = %s, thumbnail = %s, tag_source = 'auto', match_confidence = %s "
                "WHERE id = %s AND person_id IS NULL",
                (best_person_id, thumbnail, best_score, face["id"]),
            )
            if cursor.rowcount > 0:
                conn.execute(
                    "UPDATE individuals SET representative_thumbnail = %s WHERE id = %s",
                    (thumbnail, best_person_id),
                )
                matched += 1
            conn.commit()

    if matched:
        # "Who's in this photo" changed -- regenerate photos.description
        # (enrich.py), same as a manual tag_face does, once per photo
        # rather than once per face matched in it (enrich_description
        # already reads every tagged individual for a photo in one query).
        enrich.enrich_description(photo_id)

    return matched, best_unmatched


class _RefreshProgress:
    """Same shape/locking as sync.py's _SyncProgress -- see its docstrings
    for the reasoning (atomic try_start() so a caller polling status right
    after POST /api/faces/refresh can't observe a stale terminal status
    from a previous run)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = {
            "status": "idle",
            "processed": 0,
            "total": 0,
            "matched": 0,
            "error": None,
        }

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)

    def try_start(self) -> bool:
        with self._lock:
            if self._state["status"] == "running":
                return False
            self._state = {
                "status": "running",
                "processed": 0,
                "total": 0,
                "matched": 0,
                "error": None,
            }
            return True

    def set_total(self, total: int) -> None:
        with self._lock:
            self._state["total"] = total

    def reset(self) -> None:
        """For tests -- see conftest.py's `client` fixture, which resets
        this module-level singleton between tests the same way it already
        does for sync.progress (DB truncation alone doesn't touch
        in-memory state)."""
        with self._lock:
            self._state = {
                "status": "idle",
                "processed": 0,
                "total": 0,
                "matched": 0,
                "error": None,
            }

    def record(self, count: int) -> None:
        """Increments `processed` by `count` candidate faces at once -- a
        whole photo's worth, since a photo (not a single face) is this
        module's unit of concurrency (see _run_refresh), unlike sync.py's
        per-item record()."""
        with self._lock:
            self._state["processed"] += count

    def finish(self, matched: int) -> None:
        with self._lock:
            self._state["status"] = "completed"
            self._state["matched"] = matched

    def fail(self, error: str) -> None:
        with self._lock:
            self._state["status"] = "error"
            self._state["error"] = error


progress = _RefreshProgress()
_thread: Optional[threading.Thread] = None


def start_refresh(full: bool = False) -> dict:
    """Kick off a background refresh in its own thread/event loop if one
    isn't already running -- same rationale and shape as sync.start_sync()
    (decoupled from the API's own request-handling loop, try_start() flips
    to "running" synchronously before this returns). Returns the current
    status snapshot either way.

    `full` picks which of this module's two callers is asking -- see the
    module docstring's "two callers ask for different scopes" section.
    Default `False` (sync.py's post-sync trigger: incremental, only
    still-untagged faces); routers/faces.py's button passes `True`
    (re-detect every photo's faces against the current detection
    settings first -- discarding every existing tag, manual or auto,
    along with the stale boxes -- then re-match the whole library)."""
    global _thread
    if progress.try_start():
        logger.info("Starting face refresh (full=%s)", full)
        _thread = threading.Thread(target=lambda: asyncio.run(_run_refresh(full=full)), daemon=True)
        _thread.start()
    else:
        logger.info("Face refresh already in progress; ignoring duplicate start request")
    return progress.snapshot()


async def _run_refresh(full: bool = False) -> None:
    loop = asyncio.get_running_loop()

    if full:
        # Fail before touching any photo, not partway through after some
        # faces have already been wiped with nothing to replace them --
        # see face_detection.ensure_configured()'s docstring.
        try:
            await loop.run_in_executor(_executor, face_detection.ensure_configured)
        except face_detection.FaceDetectionNotConfigured as exc:
            progress.fail(str(exc))
            return
        except Exception as exc:
            logger.exception("Refresh faces failed loading the face detection model for a full refresh")
            progress.fail(str(exc))
            return

        try:
            affected_photo_ids = await loop.run_in_executor(_executor, _photos_with_tagged_faces)
            photos = await loop.run_in_executor(_executor, _list_photos)
        except Exception as exc:
            logger.exception("Refresh faces failed listing photos for a full refresh")
            progress.fail(str(exc))
            return

        redetect_semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
        redetected_photos = 0
        redetected_faces = 0

        async def redetect(photo: dict) -> None:
            nonlocal redetected_photos, redetected_faces
            async with redetect_semaphore:
                count = await loop.run_in_executor(_executor, _redetect_photo_faces, photo)
            if count is not None:
                redetected_photos += 1
                redetected_faces += count

        try:
            await asyncio.gather(*(redetect(photo) for photo in photos))
        except Exception as exc:
            logger.exception("Refresh faces failed redetecting faces for a full refresh")
            progress.fail(str(exc))
            return

        logger.info(
            "Full refresh: re-detected faces for %d of %d photo(s) (%d face(s) total) against the current "
            "detection settings -- every tag in the library, manual or auto, was discarded along with the "
            "old boxes; individuals need at least one face re-tagged before matching can find anything",
            redetected_photos,
            len(photos),
            redetected_faces,
        )
        for photo_id in affected_photo_ids:
            await loop.run_in_executor(_executor, enrich.enrich_description, photo_id)

    try:
        await loop.run_in_executor(_executor, _backfill_missing_reference_thumbnails)
        references = await loop.run_in_executor(_executor, _reference_embeddings)
        candidates = await loop.run_in_executor(_executor, _candidate_faces)
        individual_names = await loop.run_in_executor(_executor, _individual_names)
        progress.set_total(len(candidates))
    except FaceRecognitionNotConfigured as exc:
        progress.fail(str(exc))
        return
    except Exception as exc:
        logger.exception("Refresh faces failed building reference embeddings")
        progress.fail(str(exc))
        return

    if not references:
        # total is already set above (e.g. a full refresh may have just
        # redetected plenty of faces) even though there's nothing yet to
        # match them against -- matched=0 here is a real, accurate result,
        # not "returned before doing anything".
        logger.info("Refresh faces: no individuals have a manually tagged face yet -- nothing to match against")
        progress.finish(matched=0)
        return

    try:
        by_photo: dict[str, list[dict]] = {}
        for face in candidates:
            by_photo.setdefault(face["photo_id"], []).append(face)

        threshold = get_face_match_threshold()
        total_matched = 0
        global_best_unmatched: Optional[tuple[float, str, str, str]] = None
        semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

        async def handle(photo_id: str, faces: list[dict]) -> None:
            nonlocal total_matched, global_best_unmatched
            async with semaphore:
                matched, best_unmatched = await loop.run_in_executor(
                    _executor, _process_photo_faces, photo_id, faces, references, threshold, individual_names
                )
            total_matched += matched
            is_new_best = global_best_unmatched is None or (
                best_unmatched is not None and best_unmatched[0] > global_best_unmatched[0]
            )
            if best_unmatched is not None and is_new_best:
                global_best_unmatched = best_unmatched
            progress.record(len(faces))

        await asyncio.gather(*(handle(photo_id, faces) for photo_id, faces in by_photo.items()))

        progress.finish(total_matched)
        logger.info("Refresh faces completed: %d matched of %d candidate face(s)", total_matched, len(candidates))
        # The single most actionable diagnostic when a run matches
        # nothing: whether the closest candidate was "close but just under
        # threshold" (tune LUMINARY_FACE_MATCH_THRESHOLD down) or nowhere
        # close (a real non-match, or something else is wrong) -- logged
        # at INFO, same level as the completion line above, so it's visible
        # without needing to flip LUMINARY_LOG_LEVEL to DEBUG for the
        # per-face scores logged inside _process_photo_faces. Names the
        # photo filename and individual *name*, not just UUIDs -- an
        # operator debugging "why didn't this match" needs to actually go
        # look at the photo and the person, not resolve two ids by hand
        # first.
        if total_matched == 0 and global_best_unmatched is not None:
            score, person_id, face_id, photo_filename = global_best_unmatched
            individual_name = individual_names.get(person_id, person_id)
            logger.info(
                "Refresh faces: closest unmatched candidate (face %s in %s) scored %.4f against individual "
                "%s (threshold %.4f) -- if that's really the same person, lower LUMINARY_FACE_MATCH_THRESHOLD",
                face_id,
                photo_filename,
                score,
                individual_name,
                threshold,
            )
    except Exception as exc:
        logger.exception("Refresh faces failed")
        progress.fail(str(exc))
