from conftest import sync_and_wait, write_image


def test_unmatched_photos_have_faces_but_none_tagged_and_ignore_pets(client, tmp_path):
    from app import people
    from app.database import get_connection

    for name in ("none.jpg", "untagged.jpg", "partly.jpg", "tagged.jpg", "pet.jpg"):
        write_image(tmp_path / "photos" / name)
    sync_and_wait(client)
    ids = {p["filename"]: p["id"] for p in client.get("/api/photos").json()["photos"]}
    person = people.create_individual("Ann")

    rows = [
        ("f1", "untagged.jpg", "person", None),
        ("f2", "partly.jpg", "person", None),
        ("f3", "partly.jpg", "person", person["id"]),
        ("f4", "tagged.jpg", "person", person["id"]),
        ("f5", "pet.jpg", "pet", None),
    ]
    with get_connection() as conn:
        for face_id, name, kind, person_id in rows:
            conn.execute(
                "INSERT INTO faces (id, photo_id, x1, y1, x2, y2, person_id, kind) VALUES (%s, %s, 0, 0, 1, 1, %s, %s)",
                (face_id, ids[name], person_id, kind),
            )
        conn.commit()

    response = client.get("/api/individuals/unmatched/photos")
    assert response.status_code == 200
    assert response.json()["photo_ids"] == [ids["untagged.jpg"]]
