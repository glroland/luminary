"""Pet detection (pet_detection.py) and how pets coexist with human faces:
kind-scoped storage.replace_faces, sync's pet step, kind-checked tagging,
and recognition.py staying humans-only."""

import pytest
from PIL import Image

from conftest import sync_and_wait, write_image

from app import pet_detection

CAT = {"x1": 2.0, "y1": 2.0, "x2": 12.0, "y2": 12.0, "detection_confidence": 0.9, "kind": "pet", "species": "cat"}
FACE = {"x1": 1.0, "y1": 1.0, "x2": 5.0, "y2": 5.0, "detection_confidence": 0.99}


# --- pet_detection.detect_pets ------------------------------------------


class _FakeModel:
    def __init__(self, boxes, labels, scores):
        import torch

        self._output = {
            "boxes": torch.tensor(boxes, dtype=torch.float32).reshape(-1, 4),
            "labels": torch.tensor(labels, dtype=torch.int64),
            "scores": torch.tensor(scores, dtype=torch.float32),
        }

    def __call__(self, tensors):
        return [self._output]


@pytest.fixture
def fake_detector(monkeypatch):
    def install(boxes, labels, scores):
        monkeypatch.setattr(
            pet_detection, "_load_detector", lambda: (_FakeModel(boxes, labels, scores), {17: "cat", 18: "dog"})
        )

    return install


def test_detect_pets_keeps_only_cats_and_dogs(fake_detector):
    # label 1 ("person" in COCO) must be ignored; 17 cat, 18 dog kept.
    fake_detector(
        [[1, 1, 10, 10], [20, 20, 40, 40], [50, 50, 90, 90]],
        [17, 1, 18],
        [0.9, 0.95, 0.8],
    )

    boxes = pet_detection.detect_pets(Image.new("RGB", (100, 100)))

    assert [(b["species"], b["kind"]) for b in boxes] == [("cat", "pet"), ("dog", "pet")]
    assert boxes[0]["detection_confidence"] == pytest.approx(0.9)
    assert (boxes[1]["x1"], boxes[1]["y2"]) == (50.0, 90.0)


def test_detect_pets_clips_boxes_to_the_frame(fake_detector):
    fake_detector([[-5, -5, 130, 120]], [18], [0.9])

    (box,) = pet_detection.detect_pets(Image.new("RGB", (100, 100)))

    assert (box["x1"], box["y1"], box["x2"], box["y2"]) == (0.0, 0.0, 100.0, 100.0)


def test_detect_pets_scales_boxes_back_for_a_downscaled_image(fake_detector):
    # 2666x2666 is downscaled by 0.5 for inference; boxes come back in the
    # original image's pixel space.
    fake_detector([[100, 100, 400, 400]], [17], [0.9])

    (box,) = pet_detection.detect_pets(Image.new("RGB", (2666, 2666)))

    assert (box["x1"], box["x2"]) == pytest.approx((200.0, 800.0), abs=1.0)


def test_detect_pets_drops_a_lower_scored_duplicate_under_the_other_label(fake_detector):
    fake_detector([[10, 10, 50, 50], [11, 11, 50, 50]], [17, 18], [0.9, 0.7])

    boxes = pet_detection.detect_pets(Image.new("RGB", (100, 100)))

    assert [b["species"] for b in boxes] == ["cat"]


def test_detect_pets_returns_empty_when_nothing_found(fake_detector):
    fake_detector([], [], [])

    assert pet_detection.detect_pets(Image.new("RGB", (100, 100))) == []


# --- storage.replace_faces kinds ----------------------------------------


def _photo(client, tmp_path, monkeypatch):
    from app import chat, face_detection

    monkeypatch.setattr(chat, "caption_image", lambda image: "a photo")
    monkeypatch.setattr(face_detection, "detect_faces", lambda image: [dict(FACE)])
    write_image(tmp_path / "photos" / "sample.jpg", size=(20, 20))
    sync_and_wait(client)
    return client.get("/api/photos").json()["photos"][0]["id"]


def test_replace_faces_with_kinds_leaves_other_kinds_alone(client, tmp_path, monkeypatch):
    from app import storage

    photo_id = _photo(client, tmp_path, monkeypatch)
    storage.replace_faces(photo_id, [CAT], kinds=["pet"])

    faces = storage.get_faces(photo_id)
    assert sorted((f["kind"], f["species"]) for f in faces) == [("person", None), ("pet", "cat")]

    # Re-detecting only pets replaces the pet and keeps the person.
    storage.replace_faces(photo_id, [], kinds=["pet"])
    assert [f["kind"] for f in storage.get_faces(photo_id)] == ["person"]

    # No `kinds` still wipes everything.
    storage.replace_faces(photo_id, [])
    assert storage.get_faces(photo_id) == []


# --- sync ---------------------------------------------------------------


def test_sync_stores_pets_alongside_faces_when_enabled(client, tmp_path, monkeypatch):
    from app import chat, face_detection
    from app.database import get_connection
    from app.storage import get_faces

    monkeypatch.setenv("LUMINARY_PET_DETECTION", "1")
    monkeypatch.setattr(chat, "caption_image", lambda image: "a cat and a person")
    monkeypatch.setattr(face_detection, "detect_faces", lambda image: [dict(FACE)])
    monkeypatch.setattr(pet_detection, "detect_pets", lambda image: [dict(CAT)])

    write_image(tmp_path / "photos" / "sample.jpg", size=(20, 20))
    sync_and_wait(client)

    photo_id = client.get("/api/photos").json()["photos"][0]["id"]
    assert sorted((f["kind"], f["species"]) for f in get_faces(photo_id)) == [("person", None), ("pet", "cat")]
    with get_connection() as conn:
        row = conn.execute("SELECT pet_detection_done FROM photos WHERE id = %s", (photo_id,)).fetchone()
    assert row["pet_detection_done"] is True

    # The API exposes the kind.
    faces = client.get(f"/api/photos/{photo_id}/faces").json()["faces"]
    assert {(f["kind"], f["species"]) for f in faces} == {("person", None), ("pet", "cat")}


def test_sync_skips_pets_when_disabled(client, tmp_path, monkeypatch):
    from app import chat, face_detection
    from app.database import get_connection
    from app.storage import get_faces

    calls = []
    monkeypatch.setenv("LUMINARY_PET_DETECTION", "0")
    monkeypatch.setattr(chat, "caption_image", lambda image: "a person")
    monkeypatch.setattr(face_detection, "detect_faces", lambda image: [dict(FACE)])
    monkeypatch.setattr(pet_detection, "detect_pets", lambda image: calls.append(1) or [dict(CAT)])

    write_image(tmp_path / "photos" / "sample.jpg", size=(20, 20))
    sync_and_wait(client)

    photo_id = client.get("/api/photos").json()["photos"][0]["id"]
    assert calls == []
    assert [f["kind"] for f in get_faces(photo_id)] == ["person"]
    with get_connection() as conn:
        row = conn.execute(
            "SELECT face_detection_done, pet_detection_done FROM photos WHERE id = %s", (photo_id,)
        ).fetchone()
    # Humans are done; pets are still owed (so the backfill can find it).
    assert (row["face_detection_done"], row["pet_detection_done"]) == (True, False)


def test_a_pet_detector_failure_does_not_cost_the_photo_its_faces(client, tmp_path, monkeypatch):
    from app import chat, face_detection
    from app.database import get_connection
    from app.storage import get_faces

    def boom(image):
        raise RuntimeError("detector exploded")

    monkeypatch.setenv("LUMINARY_PET_DETECTION", "1")
    monkeypatch.setattr(chat, "caption_image", lambda image: "a person")
    monkeypatch.setattr(face_detection, "detect_faces", lambda image: [dict(FACE)])
    monkeypatch.setattr(pet_detection, "detect_pets", boom)

    write_image(tmp_path / "photos" / "sample.jpg", size=(20, 20))
    sync_and_wait(client)

    photo_id = client.get("/api/photos").json()["photos"][0]["id"]
    assert [f["kind"] for f in get_faces(photo_id)] == ["person"]
    with get_connection() as conn:
        row = conn.execute(
            "SELECT face_detection_done, pet_detection_done FROM photos WHERE id = %s", (photo_id,)
        ).fetchone()
    assert (row["face_detection_done"], row["pet_detection_done"]) == (True, False)


# --- individuals and tagging --------------------------------------------


def test_individuals_default_to_person_and_can_be_pets(client):
    person = client.post("/api/individuals", json={"name": "Alice"}).json()
    pet = client.post("/api/individuals", json={"name": "Luna", "kind": "pet"}).json()

    assert person["kind"] == "person"
    assert pet["kind"] == "pet"
    kinds = {i["name"]: i["kind"] for i in client.get("/api/individuals").json()["individuals"]}
    assert kinds == {"Alice": "person", "Luna": "pet"}


def test_create_individual_rejects_an_unknown_kind(client):
    assert client.post("/api/individuals", json={"name": "X", "kind": "robot"}).status_code == 422


def test_rename_keeps_the_kind(client):
    pet = client.post("/api/individuals", json={"name": "Luna", "kind": "pet"}).json()

    renamed = client.patch(f"/api/individuals/{pet['id']}", json={"name": "Luna II"}).json()

    assert renamed["kind"] == "pet"


def _tag_setup(client, tmp_path, monkeypatch):
    from app import storage

    photo_id = _photo(client, tmp_path, monkeypatch)
    storage.replace_faces(photo_id, [CAT], kinds=["pet"])
    faces = {f["kind"]: f for f in client.get(f"/api/photos/{photo_id}/faces").json()["faces"]}
    return photo_id, faces["person"], faces["pet"]


def test_a_pet_box_can_be_tagged_to_a_pet_individual(client, tmp_path, monkeypatch):
    _, _, pet_face = _tag_setup(client, tmp_path, monkeypatch)
    luna = client.post("/api/individuals", json={"name": "Luna", "kind": "pet"}).json()["id"]

    response = client.post(f"/api/faces/{pet_face['id']}/tag", json={"person_id": luna})

    assert response.status_code == 200
    assert (response.json()["kind"], response.json()["species"]) == ("pet", "cat")
    # The representative thumbnail was cropped (with the pet margin).
    assert client.get(f"/api/individuals/{luna}/thumbnail").status_code == 200


def test_tagging_across_kinds_is_a_409_and_changes_nothing(client, tmp_path, monkeypatch):
    photo_id, person_face, pet_face = _tag_setup(client, tmp_path, monkeypatch)
    alice = client.post("/api/individuals", json={"name": "Alice"}).json()["id"]
    luna = client.post("/api/individuals", json={"name": "Luna", "kind": "pet"}).json()["id"]

    assert client.post(f"/api/faces/{pet_face['id']}/tag", json={"person_id": alice}).status_code == 409
    assert client.post(f"/api/faces/{person_face['id']}/tag", json={"person_id": luna}).status_code == 409

    faces = client.get(f"/api/photos/{photo_id}/faces").json()["faces"]
    assert all(f["person_id"] is None for f in faces)
    assert client.get(f"/api/individuals/{luna}/thumbnail").status_code == 404


def test_enrichment_names_pets_separately_from_people(client, tmp_path, monkeypatch):
    from app import enrich

    photo_id, person_face, pet_face = _tag_setup(client, tmp_path, monkeypatch)
    alice = client.post("/api/individuals", json={"name": "Alice"}).json()["id"]
    luna = client.post("/api/individuals", json={"name": "Luna", "kind": "pet"}).json()["id"]
    client.post(f"/api/faces/{person_face['id']}/tag", json={"person_id": alice})
    client.post(f"/api/faces/{pet_face['id']}/tag", json={"person_id": luna})

    facts = enrich._photo_facts(photo_id)
    prompt = enrich._build_prompt(facts)

    assert facts["individuals"] == ["Alice"] and facts["pets"] == ["Luna"]
    assert "People identified in the photo: Alice" in prompt
    assert "Pets identified in the photo (animals, not people): Luna" in prompt


# --- recognition stays humans-only --------------------------------------


def test_recognition_ignores_pet_boxes_and_pet_references(client, tmp_path, monkeypatch):
    from app import recognition

    photo_id, person_face, pet_face = _tag_setup(client, tmp_path, monkeypatch)
    # The untagged pet is never a candidate (the human box still is) ...
    assert [c["id"] for c in recognition._candidate_faces()] == [person_face["id"]]

    # ... and a manually tagged pet never becomes a human reference.
    luna = client.post("/api/individuals", json={"name": "Luna", "kind": "pet"}).json()["id"]
    client.post(f"/api/faces/{pet_face['id']}/tag", json={"person_id": luna})
    assert [c["id"] for c in recognition._candidate_faces()] == [person_face["id"]]
    monkeypatch.setattr(recognition, "_embed", lambda image: [1.0, 0.0])
    assert recognition._reference_embeddings() == {}


# --- the "Detect Pets" backfill job (pets.py) -----------------------------


def _index_without_pets(client, tmp_path, monkeypatch, names=("a.jpg",)):
    """Photos indexed while pet detection was off -- exactly what a library
    from before pets existed looks like: faces done, pets still owed."""
    import os

    from app import chat, face_detection

    # Whatever the test had set up (the `pets_ready` fixture enables it,
    # conftest otherwise leaves it off) is restored after indexing.
    before = os.environ.get("LUMINARY_PET_DETECTION", "0")
    monkeypatch.setenv("LUMINARY_PET_DETECTION", "0")
    monkeypatch.setattr(chat, "caption_image", lambda image: "a photo")
    monkeypatch.setattr(face_detection, "detect_faces", lambda image: [dict(FACE)])
    for name in names:
        write_image(tmp_path / "photos" / name, size=(20, 20))
    sync_and_wait(client)
    monkeypatch.setenv("LUMINARY_PET_DETECTION", before)
    return {p["filename"]: p["id"] for p in client.get("/api/photos").json()["photos"]}


@pytest.fixture
def pets_ready(monkeypatch):
    monkeypatch.setenv("LUMINARY_PET_DETECTION", "1")
    monkeypatch.setattr(pet_detection, "ensure_configured", lambda: None)
    calls = []
    monkeypatch.setattr(pet_detection, "detect_pets", lambda image: calls.append(1) or [dict(CAT)])
    return calls


def test_backfill_adds_pets_to_owed_photos_and_leaves_human_faces_and_tags_alone(
    client, tmp_path, monkeypatch, pets_ready
):
    from conftest import refresh_pets_and_wait

    from app import storage
    from app.database import get_connection

    ids = _index_without_pets(client, tmp_path, monkeypatch)
    photo_id = ids["a.jpg"]
    person_face = storage.get_faces(photo_id)[0]
    alice = client.post("/api/individuals", json={"name": "Alice"}).json()["id"]
    client.post(f"/api/faces/{person_face['id']}/tag", json={"person_id": alice})

    status = refresh_pets_and_wait(client)

    assert status["status"] == "completed"
    assert (status["processed"], status["total"], status["found"]) == (1, 1, 1)
    faces = {f["kind"]: f for f in storage.get_faces(photo_id)}
    assert faces["pet"]["species"] == "cat"
    # The human face is the very same row, still tagged.
    assert faces["person"]["id"] == person_face["id"]
    assert faces["person"]["person_id"] == alice
    with get_connection() as conn:
        done = conn.execute("SELECT pet_detection_done FROM photos WHERE id = %s", (photo_id,)).fetchone()
    assert done["pet_detection_done"] is True


def test_backfill_is_idempotent_and_skips_already_scanned_photos(client, tmp_path, monkeypatch, pets_ready):
    from conftest import refresh_pets_and_wait

    _index_without_pets(client, tmp_path, monkeypatch, names=("a.jpg", "b.jpg"))

    first = refresh_pets_and_wait(client)
    second = refresh_pets_and_wait(client)

    assert (first["total"], first["found"]) == (2, 2)
    assert (second["status"], second["total"], second["found"]) == ("completed", 0, 0)
    assert len(pets_ready) == 2  # the detector never ran again


def test_backfill_does_not_re_scan_photos_sync_already_scanned(client, tmp_path, monkeypatch, pets_ready):
    from conftest import refresh_pets_and_wait

    from app import chat, face_detection

    monkeypatch.setattr(chat, "caption_image", lambda image: "a photo")
    monkeypatch.setattr(face_detection, "detect_faces", lambda image: [dict(FACE)])
    write_image(tmp_path / "photos" / "new.jpg", size=(20, 20))
    sync_and_wait(client)  # pets enabled -> scanned during sync
    calls_after_sync = len(pets_ready)

    status = refresh_pets_and_wait(client)

    assert status["total"] == 0
    assert len(pets_ready) == calls_after_sync


def test_backfill_fails_up_front_when_pet_detection_is_disabled(client, tmp_path, monkeypatch):
    from conftest import refresh_pets_and_wait

    from app.storage import get_faces

    ids = _index_without_pets(client, tmp_path, monkeypatch)  # leaves LUMINARY_PET_DETECTION=0

    status = refresh_pets_and_wait(client)

    assert status["status"] == "error"
    assert "turned off" in status["error"]
    assert [f["kind"] for f in get_faces(ids["a.jpg"])] == ["person"]


def test_backfill_fails_up_front_when_the_detector_is_not_installed(client, tmp_path, monkeypatch):
    from conftest import refresh_pets_and_wait

    def not_installed():
        raise pet_detection.PetDetectionNotConfigured("no torchvision")

    ids = _index_without_pets(client, tmp_path, monkeypatch)
    monkeypatch.setenv("LUMINARY_PET_DETECTION", "1")
    monkeypatch.setattr(pet_detection, "ensure_configured", not_installed)

    status = refresh_pets_and_wait(client)

    assert (status["status"], status["error"]) == ("error", "no torchvision")
    # Nothing was touched, and the job slot was released.
    assert client.get("/api/jobs").json()["active"] is None
    from app.database import get_connection

    with get_connection() as conn:
        row = conn.execute("SELECT pet_detection_done FROM photos WHERE id = %s", (ids["a.jpg"],)).fetchone()
    assert row["pet_detection_done"] is False


def test_backfill_leaves_a_photo_owed_when_its_detection_errors(client, tmp_path, monkeypatch, pets_ready):
    from conftest import refresh_pets_and_wait

    from app.database import get_connection

    ids = _index_without_pets(client, tmp_path, monkeypatch, names=("good.jpg", "bad.jpg"))

    def flaky(image):
        flaky.n += 1
        if flaky.n == 1:
            raise RuntimeError("boom")
        return [dict(CAT)]

    flaky.n = 0
    monkeypatch.setattr(pet_detection, "detect_pets", flaky)

    status = refresh_pets_and_wait(client)

    assert status["status"] == "completed"  # one bad photo doesn't fail the run
    assert (status["processed"], status["found"]) == (2, 1)
    with get_connection() as conn:
        done = [r["pet_detection_done"] for r in conn.execute("SELECT pet_detection_done FROM photos").fetchall()]
    assert sorted(done) == [False, True]  # the failed one is retried next run
    assert set(ids) == {"good.jpg", "bad.jpg"}


def test_backfill_is_rejected_while_another_job_runs(client):
    from app import jobs

    jobs.try_claim(jobs.SYNC)

    assert client.post("/api/pets/refresh").status_code == 409


def test_jobs_snapshot_includes_the_pets_job(client):
    body = client.get("/api/jobs").json()

    assert body["pets"] == {"status": "idle", "processed": 0, "total": 0, "found": 0, "error": None}


# --- automatic pet recognition (recognition.py + pet_embedding.py) --------


def _color_vector(image):
    r, g, b = image.convert("RGB").resize((1, 1)).getpixel((0, 0))
    return [float(r), float(g), float(b)]


def _refresh_and_wait(timeout=10.0):
    """recognition's incremental refresh (what sync's post-sync trigger and
    the Manage page use), called directly like test_recognition does."""
    import time

    from app import recognition

    recognition.start_refresh()
    deadline = time.monotonic() + timeout
    status = {}
    while time.monotonic() < deadline:
        status = recognition.progress.snapshot()
        if status["status"] in ("completed", "error", "cancelled"):
            return status
        time.sleep(0.05)
    raise AssertionError(f"face refresh did not finish in time: {status}")


@pytest.fixture
def two_photos_with_a_person_and_a_cat(client, tmp_path, monkeypatch):
    """a.jpg and b.jpg, same colour, each with one human box and one cat box
    -- the fakes embed by colour, so a.* and b.* embed identically."""
    from app import chat, face_detection, pet_embedding, recognition

    calls = {"person": 0, "pet": 0}

    def person_embed(image):
        calls["person"] += 1
        return _color_vector(image)

    def pet_embed(image):
        calls["pet"] += 1
        return _color_vector(image)

    monkeypatch.setenv("LUMINARY_PET_DETECTION", "1")
    monkeypatch.setattr(chat, "caption_image", lambda image: "a person and a cat")
    monkeypatch.setattr(face_detection, "detect_faces", lambda image: [dict(FACE)])
    monkeypatch.setattr(pet_detection, "detect_pets", lambda image: [dict(CAT)])
    monkeypatch.setattr(recognition, "_embed", person_embed)
    monkeypatch.setattr(pet_embedding, "embed", pet_embed)

    write_image(tmp_path / "photos" / "a.jpg", size=(20, 20), color="red")
    write_image(tmp_path / "photos" / "b.jpg", size=(20, 20), color="red")
    sync_and_wait(client)

    photos = {p["filename"]: p["id"] for p in client.get("/api/photos").json()["photos"]}
    faces = {}
    for name in ("a.jpg", "b.jpg"):
        for face in client.get(f"/api/photos/{photos[name]}/faces").json()["faces"]:
            faces[(name, face["kind"])] = face
    return photos, faces, calls


def test_a_pet_is_auto_matched_to_a_pet_tagged_by_hand(client, two_photos_with_a_person_and_a_cat):
    _, faces, calls = two_photos_with_a_person_and_a_cat
    luna = client.post("/api/individuals", json={"name": "Luna", "kind": "pet"}).json()["id"]
    client.post(f"/api/faces/{faces[('a.jpg', 'pet')]['id']}/tag", json={"person_id": luna})

    status = _refresh_and_wait()

    assert status["status"] == "completed" and status["matched"] == 1
    luna_faces = client.get(f"/api/individuals/{luna}/faces").json()["faces"]
    assert sorted((f["tag_source"]) for f in luna_faces) == ["auto", "manual"]
    assert calls["pet"] == 2  # the manual reference + the one candidate
    # The human box in b.jpg stays untagged: nobody human was tagged.
    assert calls["person"] == 0


def test_people_and_pets_are_matched_independently_in_one_run(client, two_photos_with_a_person_and_a_cat):
    photos, faces, calls = two_photos_with_a_person_and_a_cat
    alice = client.post("/api/individuals", json={"name": "Alice"}).json()["id"]
    luna = client.post("/api/individuals", json={"name": "Luna", "kind": "pet"}).json()["id"]
    client.post(f"/api/faces/{faces[('a.jpg', 'person')]['id']}/tag", json={"person_id": alice})
    client.post(f"/api/faces/{faces[('a.jpg', 'pet')]['id']}/tag", json={"person_id": luna})

    status = _refresh_and_wait()

    assert status["matched"] == 2
    assert status["total"] == 2  # one human + one pet candidate
    by_kind = {f["kind"]: f for f in client.get(f"/api/photos/{photos['b.jpg']}/faces").json()["faces"]}
    assert by_kind["person"]["person_id"] == alice
    assert by_kind["pet"]["person_id"] == luna
    assert calls == {"person": 2, "pet": 2}  # each kind went through its own embedder only


def test_a_pet_is_not_matched_when_only_a_person_was_tagged(client, two_photos_with_a_person_and_a_cat):
    photos, faces, calls = two_photos_with_a_person_and_a_cat
    alice = client.post("/api/individuals", json={"name": "Alice"}).json()["id"]
    client.post(f"/api/faces/{faces[('a.jpg', 'person')]['id']}/tag", json={"person_id": alice})

    status = _refresh_and_wait()

    assert status["matched"] == 1
    by_kind = {f["kind"]: f for f in client.get(f"/api/photos/{photos['b.jpg']}/faces").json()["faces"]}
    assert by_kind["person"]["person_id"] == alice
    assert by_kind["pet"]["person_id"] is None
    assert calls["pet"] == 0


def test_a_missing_pet_model_skips_pets_but_still_matches_people(
    client, two_photos_with_a_person_and_a_cat, monkeypatch
):
    from app import pet_embedding

    photos, faces, _ = two_photos_with_a_person_and_a_cat
    alice = client.post("/api/individuals", json={"name": "Alice"}).json()["id"]
    luna = client.post("/api/individuals", json={"name": "Luna", "kind": "pet"}).json()["id"]
    client.post(f"/api/faces/{faces[('a.jpg', 'person')]['id']}/tag", json={"person_id": alice})
    client.post(f"/api/faces/{faces[('a.jpg', 'pet')]['id']}/tag", json={"person_id": luna})

    def no_model(image):
        raise pet_embedding.PetRecognitionNotConfigured("no dinov2")

    monkeypatch.setattr(pet_embedding, "embed", no_model)

    status = _refresh_and_wait()

    assert status["status"] == "completed"
    assert status["matched"] == 1  # the person; the pet pass was skipped
    by_kind = {f["kind"]: f for f in client.get(f"/api/photos/{photos['b.jpg']}/faces").json()["faces"]}
    assert by_kind["person"]["person_id"] == alice
    assert by_kind["pet"]["person_id"] is None


def test_the_pet_threshold_is_its_own_setting_and_is_named_in_the_log(
    client, two_photos_with_a_person_and_a_cat, monkeypatch, caplog
):
    import logging

    from app import pet_embedding

    _, faces, _ = two_photos_with_a_person_and_a_cat
    luna = client.post("/api/individuals", json={"name": "Luna", "kind": "pet"}).json()["id"]
    client.post(f"/api/faces/{faces[('a.jpg', 'pet')]['id']}/tag", json={"person_id": luna})
    # Reference [1, 0]; candidate [0.9, 0.436] -> cosine 0.9.
    vectors = iter([[1.0, 0.0], [0.9, 0.43589]])
    monkeypatch.setattr(pet_embedding, "embed", lambda image: next(vectors))
    monkeypatch.setenv("LUMINARY_PET_MATCH_THRESHOLD", "0.95")
    # A face threshold this high/low must not matter to pets.
    monkeypatch.setenv("LUMINARY_FACE_MATCH_THRESHOLD", "0.0")

    with caplog.at_level(logging.INFO, logger="app.recognition"):
        status = _refresh_and_wait()

    assert status["matched"] == 0
    assert any(
        "closest unmatched candidate" in r.message and "LUMINARY_PET_MATCH_THRESHOLD" in r.message and "Luna" in r.message
        for r in caplog.records
    )


def test_unmatching_an_auto_tagged_pet_vetoes_it_and_records_the_pet_threshold(
    client, two_photos_with_a_person_and_a_cat, monkeypatch
):
    from app.database import get_connection

    photos, faces, _ = two_photos_with_a_person_and_a_cat
    luna = client.post("/api/individuals", json={"name": "Luna", "kind": "pet"}).json()["id"]
    client.post(f"/api/faces/{faces[('a.jpg', 'pet')]['id']}/tag", json={"person_id": luna})
    assert _refresh_and_wait()["matched"] == 1
    b_pet = next(
        f for f in client.get(f"/api/photos/{photos['b.jpg']}/faces").json()["faces"] if f["kind"] == "pet"
    )
    assert b_pet["person_id"] == luna

    assert client.delete(f"/api/faces/{b_pet['id']}/tag").status_code == 204

    with get_connection() as conn:
        rejection = conn.execute("SELECT rejected_at_threshold FROM face_rejections WHERE person_id = %s", (luna,)).fetchone()
    assert rejection["rejected_at_threshold"] == 0.8  # the pet default, not the face one (0.4)

    # And a later run does not tag it as Luna again.
    assert _refresh_and_wait()["matched"] == 0


def test_a_full_face_refresh_leaves_pet_boxes_and_tags_alone(client, two_photos_with_a_person_and_a_cat, monkeypatch):
    from conftest import refresh_and_wait

    from app import face_detection

    photos, faces, _ = two_photos_with_a_person_and_a_cat
    luna = client.post("/api/individuals", json={"name": "Luna", "kind": "pet"}).json()["id"]
    cat_a = faces[("a.jpg", "pet")]
    client.post(f"/api/faces/{cat_a['id']}/tag", json={"person_id": luna})
    monkeypatch.setattr(face_detection, "ensure_configured", lambda: None)

    refresh_and_wait(client)

    a_faces = client.get(f"/api/photos/{photos['a.jpg']}/faces").json()["faces"]
    pet = next(f for f in a_faces if f["kind"] == "pet")
    person = next(f for f in a_faces if f["kind"] == "person")
    assert pet["id"] == cat_a["id"] and pet["person_id"] == luna  # same row, still tagged
    assert person["id"] != faces[("a.jpg", "person")]["id"]  # humans were re-detected
