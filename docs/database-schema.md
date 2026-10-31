# Database schema

Luminary's API stores photo metadata, generated thumbnails, and detected
faces in Postgres — a remote server managed independently of this repo's
Helm chart/ArgoCD app (see the README's "Database" section and `db/`).
Original photo files themselves are never stored here; they stay on the
configured photos backend (`LUMINARY_PHOTOS_URI`) and are only read, never
written (see CLAUDE.md's hard invariant).

There are two tables, `photos` and `faces`, both created by
[`db/create_tables.sql`](../db/create_tables.sql) and mirrored by
`database.py`'s `init_db()` (run on every API startup). **There is no
migrations framework** — a schema change means updating both of those
together, by hand.

## `photos`

One row per indexed photo. Rows are inserted/updated/deleted exclusively by
`sync.py`'s reconciliation (`POST /api/sync`) and `storage.delete_photo`
(`DELETE /api/photos/{id}`) — there's no other write path.

| Column         | Type               | Nullable | Description |
|-----------------|--------------------|----------|--------------|
| `id`            | `TEXT`             | no       | Primary key. A `uuid4` string, generated when a file is first indexed (`sync._sync_one_file`); stable across re-syncs as long as the file's path doesn't change. |
| `filename`      | `TEXT`             | no       | Path of the file *relative to the photos root*, exactly as discovered (e.g. `vacation/2019/beach.jpg`). This is the correlation key sync uses to match a discovered file back to its row — not `id`. |
| `content_type`  | `TEXT`             | no       | Guessed from the filename via `mimetypes.guess_type` (e.g. `image/jpeg`), not sniffed from file contents. |
| `size_bytes`    | `BIGINT`           | no       | File size as reported by the photos backend (`fs.info`). Sync's primary "has this file changed?" signal — a size mismatch against this column triggers a full reprocess. |
| `width`         | `INTEGER`          | yes      | Pixel width *after* EXIF orientation is applied (`ImageOps.exif_transpose`), so a portrait photo shot on a sideways-held sensor is recorded as portrait. `NULL` if the file couldn't be opened/decoded (see `description`). |
| `height`        | `INTEGER`          | yes      | Same as `width`, post-transpose. |
| `created_at`    | `TEXT`             | no       | ISO 8601 timestamp string, **not** a native `TIMESTAMP` column. Prefers the photo's EXIF `DateTimeOriginal`/`DateTimeDigitized`/`DateTime` (treated as UTC — EXIF carries no timezone); falls back to the time it was indexed if EXIF is absent or unparseable. Stored as text so the API can pass it straight through to JSON without a conversion step. |
| `latitude`      | `DOUBLE PRECISION` | yes      | Decimal degrees, decoded from the EXIF GPS IFD (`_extract_exif`). `NULL` if the photo has no GPS tags. |
| `longitude`     | `DOUBLE PRECISION` | yes      | Same as `latitude`. |
| `thumbnail`     | `BYTEA`            | yes      | A 320×320 (max) JPEG, generated at index time by `storage.THUMBNAIL_SIZE`/`sync._sync_one_file`. `NULL` until generated, or permanently if the source file couldn't be decoded — thumbnailing is best-effort and never fails the whole sync. Deliberately excluded from `storage.METADATA_COLUMNS`/`list_photos`/`get_photo` so listing photos never pulls thumbnail bytes off disk; only `get_thumbnail` (`GET /api/photos/{id}/thumbnail`) reads this column. |
| `description`   | `TEXT`             | yes      | Florence-2-generated caption (`chat.caption_image`, run from `sync._run_florence_pipeline`). `NULL` if Florence-2 isn't configured, hasn't run yet, or failed. Doubles as the signal for "face detection has already run for this photo too" (see `faces` below) — there's no separate column for that. |

No indexes beyond the primary key — the API's only queries are by `id` or
an unfiltered `ORDER BY created_at DESC` scan (`list_photos`), which doesn't
currently need one.

## `faces`

Zero or more rows per photo, one per face Florence-2's general object
detection found (filtered to "face" labels — see `chat.py`'s module
docstring for why general detection, not phrase grounding, is what finds
*every* face in a photo rather than just one matching a text prompt).
Written exclusively by `storage.replace_faces`, called from
`sync._run_florence_pipeline`.

| Column     | Type               | Nullable | Description |
|-------------|--------------------|----------|--------------|
| `id`        | `TEXT`             | no       | Primary key. A `uuid4` string, generated fresh every time faces are (re)detected for a photo — not stable across re-syncs the way `photos.id` is. |
| `photo_id`  | `TEXT`             | no       | Logically references `photos.id` — see "No foreign key" below. |
| `x1`        | `DOUBLE PRECISION` | no       | Left edge of the detected face's bounding box, in the *original* photo's own pixel coordinate space (matching `photos.width`/`height`, i.e. post-EXIF-transpose) — not normalized to 0–1, and not relative to any resized/thumbnail version. |
| `y1`        | `DOUBLE PRECISION` | no       | Top edge, same coordinate space. |
| `x2`        | `DOUBLE PRECISION` | no       | Right edge. |
| `y2`        | `DOUBLE PRECISION` | no       | Bottom edge. |

### Index

- `idx_faces_photo_id` on `(photo_id)` — every read of this table
  (`storage.get_faces`, `GET /api/photos/{id}/faces`) filters by
  `photo_id`, and every sync deletes by it too (`sync._delete_missing`).

### No foreign key

`faces.photo_id` has no `REFERENCES photos(id)` constraint and no cascade.
This is deliberate: Postgres can enforce the relationship, but this app
already deletes dependent face rows explicitly and in the right order
wherever a photo row is removed or replaced —
`storage.delete_photo` and `sync._delete_missing` delete from `faces`
before `photos`, and `storage.replace_faces` always deletes a photo's old
faces before inserting new ones, even when the new set is empty. Adding a
constraint wouldn't change application behavior; keep it in mind as
*documentation* of the relationship rather than database-enforced.

### Write ordering: faces before `description`

`sync._run_florence_pipeline` always clears and (re)writes a photo's faces
*before* writing its `description`, never after. That ordering is what
lets `photos.description IS NOT NULL` double as "face detection has already
run (and succeeded) for this photo", without a separate boolean column. If
the pipeline is interrupted or fails partway, `description` stays `NULL`
and the next sync retries captioning *and* face detection from scratch —
there's no state where a half-written face set is mistaken for "done".
Don't reorder those two writes.
