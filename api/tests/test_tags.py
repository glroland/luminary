from conftest import sync_and_wait, write_image


def test_create_and_list_tags(client):
    response = client.post("/api/tags", json={"name": "Vacation"})
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Vacation"
    assert body["photo_count"] == 0
    assert isinstance(body["id"], str) and body["id"]

    listed = client.get("/api/tags").json()["tags"]
    assert [tag["name"] for tag in listed] == ["Vacation"]


def test_create_tag_rejects_empty_name(client):
    response = client.post("/api/tags", json={"name": "   "})
    assert response.status_code == 422


def test_create_tag_duplicate_name_conflicts(client):
    assert client.post("/api/tags", json={"name": "Favorites"}).status_code == 201
    response = client.post("/api/tags", json={"name": "Favorites"})
    assert response.status_code == 409


def test_update_tag_renames(client):
    tag_id = client.post("/api/tags", json={"name": "Trip"}).json()["id"]

    response = client.patch(f"/api/tags/{tag_id}", json={"name": "Road Trip"})
    assert response.status_code == 200
    assert response.json()["name"] == "Road Trip"

    listed = client.get("/api/tags").json()["tags"]
    assert [tag["name"] for tag in listed] == ["Road Trip"]


def test_update_tag_rejects_empty_name(client):
    tag_id = client.post("/api/tags", json={"name": "Pets"}).json()["id"]
    response = client.patch(f"/api/tags/{tag_id}", json={"name": "  "})
    assert response.status_code == 422


def test_update_tag_duplicate_name_conflicts(client):
    client.post("/api/tags", json={"name": "Work"})
    tag_id = client.post("/api/tags", json={"name": "Home"}).json()["id"]

    response = client.patch(f"/api/tags/{tag_id}", json={"name": "Work"})
    assert response.status_code == 409


def test_update_unknown_tag_returns_404(client):
    response = client.patch("/api/tags/does-not-exist", json={"name": "Anything"})
    assert response.status_code == 404


def test_delete_unknown_tag_returns_404(client):
    response = client.delete("/api/tags/does-not-exist")
    assert response.status_code == 404


def _sync_one_photo(client, tmp_path):
    write_image(tmp_path / "photos" / "sample.jpg")
    sync_and_wait(client)
    return client.get("/api/photos").json()["photos"][0]["id"]


def test_get_photo_tags_for_unknown_photo_returns_404(client):
    response = client.get("/api/photos/does-not-exist/tags")
    assert response.status_code == 404


def test_tag_photo_for_unknown_photo_returns_404(client):
    tag_id = client.post("/api/tags", json={"name": "Nature"}).json()["id"]
    response = client.post(f"/api/photos/does-not-exist/tags/{tag_id}")
    assert response.status_code == 404


def test_tag_photo_for_unknown_tag_returns_404(client, tmp_path):
    photo_id = _sync_one_photo(client, tmp_path)
    response = client.post(f"/api/photos/{photo_id}/tags/does-not-exist")
    assert response.status_code == 404


def test_tag_and_untag_photo(client, tmp_path):
    photo_id = _sync_one_photo(client, tmp_path)
    tag_id = client.post("/api/tags", json={"name": "Sunset"}).json()["id"]

    assert client.get(f"/api/photos/{photo_id}/tags").json() == {"tags": []}

    response = client.post(f"/api/photos/{photo_id}/tags/{tag_id}")
    assert response.status_code == 204

    listed = client.get(f"/api/photos/{photo_id}/tags").json()["tags"]
    assert listed == [{"id": tag_id, "name": "Sunset"}]

    # The catalog's photo_count reflects the tag's actual usage.
    catalog = client.get("/api/tags").json()["tags"]
    assert catalog[0]["photo_count"] == 1

    untag_response = client.delete(f"/api/photos/{photo_id}/tags/{tag_id}")
    assert untag_response.status_code == 204
    assert client.get(f"/api/photos/{photo_id}/tags").json() == {"tags": []}
    assert client.get("/api/tags").json()["tags"][0]["photo_count"] == 0


def test_tagging_a_photo_twice_with_the_same_tag_is_idempotent(client, tmp_path):
    photo_id = _sync_one_photo(client, tmp_path)
    tag_id = client.post("/api/tags", json={"name": "Family"}).json()["id"]

    assert client.post(f"/api/photos/{photo_id}/tags/{tag_id}").status_code == 204
    assert client.post(f"/api/photos/{photo_id}/tags/{tag_id}").status_code == 204

    listed = client.get(f"/api/photos/{photo_id}/tags").json()["tags"]
    assert listed == [{"id": tag_id, "name": "Family"}]


def test_untagging_a_photo_that_was_never_tagged_is_a_no_op(client, tmp_path):
    photo_id = _sync_one_photo(client, tmp_path)
    tag_id = client.post("/api/tags", json={"name": "Unused"}).json()["id"]

    response = client.delete(f"/api/photos/{photo_id}/tags/{tag_id}")
    assert response.status_code == 204


def test_deleting_a_tag_removes_it_from_tagged_photos(client, tmp_path):
    photo_id = _sync_one_photo(client, tmp_path)
    tag_id = client.post("/api/tags", json={"name": "Temporary"}).json()["id"]
    client.post(f"/api/photos/{photo_id}/tags/{tag_id}")

    response = client.delete(f"/api/tags/{tag_id}")
    assert response.status_code == 204

    assert client.get("/api/tags").json()["tags"] == []
    assert client.get(f"/api/photos/{photo_id}/tags").json() == {"tags": []}


def test_deleting_a_photo_removes_its_tags(client, tmp_path):
    photo_id = _sync_one_photo(client, tmp_path)
    tag_id = client.post("/api/tags", json={"name": "Deleted Photo Tag"}).json()["id"]
    client.post(f"/api/photos/{photo_id}/tags/{tag_id}")

    response = client.delete(f"/api/photos/{photo_id}")
    assert response.status_code == 204

    # The tag itself survives; only its link to the now-deleted photo is
    # gone (same as how deleting an individual doesn't delete faces, only
    # their person_id -- here it's the join row that's cleaned up).
    assert client.get("/api/tags").json()["tags"][0]["photo_count"] == 0
