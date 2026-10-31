from conftest import sync_and_wait, write_image


def test_create_and_list_individuals(client):
    response = client.post("/api/individuals", json={"name": "Alice"})
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Alice"
    assert body["has_representative_thumbnail"] is False
    assert isinstance(body["id"], str) and body["id"]

    listed = client.get("/api/individuals").json()["individuals"]
    assert [individual["name"] for individual in listed] == ["Alice"]


def test_create_individual_rejects_empty_name(client):
    response = client.post("/api/individuals", json={"name": "   "})
    assert response.status_code == 422


def test_create_individual_duplicate_name_conflicts(client):
    assert client.post("/api/individuals", json={"name": "Bob"}).status_code == 201
    response = client.post("/api/individuals", json={"name": "Bob"})
    assert response.status_code == 409


def test_individual_thumbnail_404_before_any_tag(client):
    person_id = client.post("/api/individuals", json={"name": "Carol"}).json()["id"]
    response = client.get(f"/api/individuals/{person_id}/thumbnail")
    assert response.status_code == 404


def _sync_one_detected_face(client, tmp_path, monkeypatch, box=None):
    from app import chat

    box = box or {"x1": 1.0, "y1": 1.0, "x2": 5.0, "y2": 5.0}
    monkeypatch.setattr(chat, "caption_image", lambda image: "a person")
    monkeypatch.setattr(chat, "detect_faces", lambda image: [box])

    write_image(tmp_path / "photos" / "sample.jpg", size=(20, 20))
    sync_and_wait(client)

    photo_id = client.get("/api/photos").json()["photos"][0]["id"]
    face = client.get(f"/api/photos/{photo_id}/faces").json()["faces"][0]
    return photo_id, face


def test_tag_face_for_unknown_face_returns_404(client):
    person_id = client.post("/api/individuals", json={"name": "Dave"}).json()["id"]
    response = client.post("/api/faces/does-not-exist/tag", json={"person_id": person_id})
    assert response.status_code == 404


def test_tag_face_for_unknown_individual_returns_404(client, tmp_path, monkeypatch):
    _, face = _sync_one_detected_face(client, tmp_path, monkeypatch)
    response = client.post(f"/api/faces/{face['id']}/tag", json={"person_id": "does-not-exist"})
    assert response.status_code == 404


def test_tag_face_assigns_person_and_stores_representative_thumbnail(client, tmp_path, monkeypatch):
    photo_id, face = _sync_one_detected_face(client, tmp_path, monkeypatch)
    person_id = client.post("/api/individuals", json={"name": "Erin"}).json()["id"]

    response = client.post(f"/api/faces/{face['id']}/tag", json={"person_id": person_id})
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == face["id"]
    assert body["person_id"] == person_id

    # The face row itself now carries the tag.
    faces = client.get(f"/api/photos/{photo_id}/faces").json()["faces"]
    assert faces[0]["person_id"] == person_id

    # Tagging crops the face rectangle out of the original and stores it as
    # the individual's representative thumbnail.
    individuals = client.get("/api/individuals").json()["individuals"]
    tagged = next(i for i in individuals if i["id"] == person_id)
    assert tagged["has_representative_thumbnail"] is True

    thumbnail_response = client.get(f"/api/individuals/{person_id}/thumbnail")
    assert thumbnail_response.status_code == 200
    assert thumbnail_response.headers["content-type"] == "image/jpeg"
    assert len(thumbnail_response.content) > 0


def test_retagging_a_face_to_a_different_person_updates_both(client, tmp_path, monkeypatch):
    _, face = _sync_one_detected_face(client, tmp_path, monkeypatch)
    first_person = client.post("/api/individuals", json={"name": "Frank"}).json()["id"]
    second_person = client.post("/api/individuals", json={"name": "Grace"}).json()["id"]

    client.post(f"/api/faces/{face['id']}/tag", json={"person_id": first_person})
    response = client.post(f"/api/faces/{face['id']}/tag", json={"person_id": second_person})
    assert response.status_code == 200
    assert response.json()["person_id"] == second_person
