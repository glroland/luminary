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


def test_tagged_photos_csv_lists_paths_of_tagged_photos_only(client, tmp_path):
    write_image(tmp_path / "photos" / "trips" / "b.jpg")
    write_image(tmp_path / "photos" / "a.jpg")
    write_image(tmp_path / "photos" / "untagged.jpg")
    sync_and_wait(client)
    photos = {p["filename"]: p["id"] for p in client.get("/api/photos").json()["photos"]}
    tag_id = client.post("/api/tags", json={"name": "Export"}).json()["id"]
    client.post(f"/api/photos/{photos['trips/b.jpg']}/tags/{tag_id}")
    client.post(f"/api/photos/{photos['a.jpg']}/tags/{tag_id}")

    response = client.get(f"/api/tags/{tag_id}/photos/csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert response.text.splitlines() == ["Path", "a.jpg", "trips/b.jpg"]


def test_tagged_photos_csv_for_tag_with_no_photos_is_header_only(client):
    tag_id = client.post("/api/tags", json={"name": "Empty"}).json()["id"]
    response = client.get(f"/api/tags/{tag_id}/photos/csv")
    assert response.status_code == 200
    assert response.text.splitlines() == ["Path"]


def test_tagged_photo_ids_lists_only_tagged_photos(client, tmp_path):
    write_image(tmp_path / "photos" / "a.jpg")
    write_image(tmp_path / "photos" / "untagged.jpg")
    sync_and_wait(client)
    photos = {p["filename"]: p["id"] for p in client.get("/api/photos").json()["photos"]}
    tag_id = client.post("/api/tags", json={"name": "Filter"}).json()["id"]
    client.post(f"/api/photos/{photos['a.jpg']}/tags/{tag_id}")

    response = client.get(f"/api/tags/{tag_id}/photos")
    assert response.status_code == 200
    assert response.json() == {"photo_ids": [photos["a.jpg"]]}


def test_tagged_photo_ids_for_unknown_tag_returns_404(client):
    response = client.get("/api/tags/does-not-exist/photos")
    assert response.status_code == 404


def test_tagged_photos_csv_for_unknown_tag_returns_404(client):
    response = client.get("/api/tags/does-not-exist/photos/csv")
    assert response.status_code == 404
