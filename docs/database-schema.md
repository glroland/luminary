# Database schema

Luminary's API stores photo metadata, generated thumbnails, and detected
faces in Postgres — a remote server managed independently of this repo's
Helm chart/ArgoCD app (see the README's "Database" section and `db/`).
Original photo files themselves are never stored here; they stay on the
configured photos backend (`LUMINARY_PHOTOS_URI`) and are only read, never
written (see CLAUDE.md's hard invariant).

There are three tables, `photos`, `faces`, and `individuals`, all created by
[`db/create_tables.sql`](../db/create_tables.sql) and mirrored by
`database.py`'s `init_db()` (run on every API startup). **There is no
migrations framework** — a schema change means updating both of those
together, by hand (see `faces.person_id` below for the one example of that
so far: a column added after `faces` already existed, via `ALTER TABLE ...
ADD COLUMN IF NOT EXISTS` in both places), plus staging the same statements
in [`db/update.sql`](../db/update.sql) so an already-provisioned database
can be patched in place via `make db-update` instead of a full
`make db-recreate` drop/rebuild — see the README's "Database" section.

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
| `person_id` | `TEXT`             | yes      | Assigned via `people.tag_face` (`POST /api/faces/{id}/tag`, manual) or `recognition.py`'s "Refresh Faces" (`POST /api/faces/refresh`, automatic) — logically references `individuals.id` (see "No foreign key" below). `NULL` until either tags this face; Florence-2's detection pass never sets this itself. See `tag_source` for which of the two set it. |
| `thumbnail` | `BYTEA`            | yes      | A small (160×160 max) JPEG crop of *this* face, set alongside `person_id` whether the tag is manual or automatic — the management page's per-individual face list (`GET /api/individuals/{id}/faces`, `GET /api/faces/{id}/thumbnail`) reads this, one row per tagged face, unlike `individuals.representative_thumbnail` below which only ever holds the latest. Always `NULL` exactly when `person_id` is — `people.tag_face`/`untag_face`/`delete_individual`/`recognition.py`'s auto-tagging all keep the two in lockstep, clearing/setting both together. |
| `tag_source` | `TEXT`            | yes      | `'manual'` (`people.tag_face`) or `'auto'` (`recognition.py`'s "Refresh Faces", matched by face-embedding similarity against that person's *manually* tagged faces only — see `recognition.py`'s module docstring for why auto matches never themselves become future reference faces). `NULL` exactly when `person_id` is. Drives the "auto-detected"/"auto" badges in `PhotoModal.jsx`'s "People in this photo" panel and `ManagementPage.jsx`'s per-face thumbnails. |
| `match_confidence` | `DOUBLE PRECISION` | yes | Cosine similarity score from `recognition.py`'s match (0–1-ish, higher is more confident). `NULL` for manual tags and untagged faces. Stored for troubleshooting/future use; not currently surfaced in any API response or UI. |

**Backfill for faces tagged before `tag_source` existed**: a face tagged
via `people.tag_face` *before* this column was added has `person_id` set
but `tag_source` left `NULL` by the bare `ALTER TABLE ... ADD COLUMN`
(adding a column never backfills existing rows). Since
`recognition._reference_embeddings()` only considers `tag_source =
'manual'` rows, that left every pre-existing manual tag invisible to
"Refresh Faces" — it would report `"no individuals have a manually tagged
face yet"` even on a database with plenty of them. Fixed by a one-time
`UPDATE faces SET tag_source = 'manual' WHERE person_id IS NOT NULL AND
tag_source IS NULL` backfill, now in both `database.py`'s `init_db()`
(so it self-heals on every API startup) and `db/create_tables.sql`; a
no-op once applied, so it's left in permanently rather than removed after
one use.

### Indexes

- `idx_faces_photo_id` on `(photo_id)` — every read of this table
  (`storage.get_faces`, `GET /api/photos/{id}/faces`) filters by
  `photo_id`, and every sync deletes by it too (`sync._delete_missing`).
- `idx_faces_person_id` on `(person_id)` — `people.list_faces_for_individual`
  (`GET /api/individuals/{id}/faces`, the management page's per-individual
  face list) filters by it.

### No foreign key

Neither `faces.photo_id` nor `faces.person_id` has a `REFERENCES ...`
constraint or cascade. For `photo_id` this is deliberate: Postgres can
enforce the relationship, but this app already deletes dependent face rows
explicitly and in the right order wherever a photo row is removed or
replaced — `storage.delete_photo` and `sync._delete_missing` delete from
`faces` before `photos`, and `storage.replace_faces` always deletes a
photo's old faces before inserting new ones, even when the new set is
empty. For `person_id`, `people.tag_face` checks both the face and the
individual exist before writing the tag, so the same "app-enforced, not
database-enforced" rationale applies — adding either constraint wouldn't
change application behavior; keep both in mind as *documentation* of the
relationship rather than database-enforced.

### Write ordering: faces before `description`

`sync._run_florence_pipeline` always clears and (re)writes a photo's faces
*before* writing its `description`, never after. That ordering is what
lets `photos.description IS NOT NULL` double as "face detection has already
run (and succeeded) for this photo", without a separate boolean column. If
the pipeline is interrupted or fails partway, `description` stays `NULL`
and the next sync retries captioning *and* face detection from scratch —
there's no state where a half-written face set is mistaken for "done".
Don't reorder those two writes.

**Re-detection also wipes tags**: because `replace_faces` deletes and
reinserts with fresh `id`s, a photo whose content changes (triggering a
full Florence-2 re-detection) loses any `person_id`/`thumbnail`/
`tag_source`/`match_confidence` tags its old faces had — manual or auto,
there's no attempt to match an old face row to its replacement by
position/overlap. Accepted as a consequence of the existing
wipe-and-redetect design, not something tagging tries to preserve across
— a later "Refresh Faces" run will simply re-match it if it's still a
confident match.

## `individuals`

The catalog of named people faces can be manually tagged as — see
`people.py` / `routers/individuals.py` / `routers/faces.py`. Added to and
tagged from `PhotoModal.jsx`'s right-click-and-hold tag menu (press "f" in
the photo viewer to show face boxes, then right-click-drag one onto a
name); added/renamed/removed, and every tagged face browsed/untagged, from
the management page (`ManagementPage.jsx`, linked from the sidebar).

| Column                      | Type    | Nullable | Description |
|-------------------------------|---------|----------|--------------|
| `id`                          | `TEXT`  | no       | Primary key. A `uuid4` string, generated when the individual is first catalogued (`POST /api/individuals`). |
| `name`                        | `TEXT`  | no       | Unique (enforced by a `UNIQUE` constraint — `POST`/`PATCH /api/individuals` map a violation to `409`). Freeform, whatever the user typed. |
| `representative_thumbnail`    | `BYTEA` | yes      | A small (160×160 max) JPEG crop of the *most recently* tagged face for this person, manual or auto (`people.tag_face`/`people.crop_face_thumbnail`, also reused by `recognition.py`), cropped from the photo's original bytes using the tagged face's `x1`/`y1`/`x2`/`y2` box. `NULL` until this person has been tagged on at least one face. A quick-reference avatar only (e.g. `PhotoModal.jsx`'s People list) — tagging a second face just overwrites this with the new crop; the *gallery* of every face ever tagged to this person is `faces.thumbnail` rows via `GET /api/individuals/{id}/faces`, not this column. Excluded from `GET /api/individuals`'s list response the same way `photos.thumbnail` is excluded from `list_photos` — only `GET /api/individuals/{id}/thumbnail` reads this column; the list response exposes a `has_representative_thumbnail` boolean instead. |
| `created_at`                  | `TEXT`  | no       | ISO 8601 timestamp string, same convention as `photos.created_at` — the time the individual was catalogued, not tied to any particular photo. |

### Deleting an individual also untags their faces

`people.delete_individual` clears `person_id`/`thumbnail`/`tag_source`/
`match_confidence` on every face tagged to the individual being deleted
(manual or auto) *before* deleting the `individuals` row — same "no FK, so
clean up explicitly" rationale as `faces.photo_id` above. Those faces
aren't deleted, just untagged back to plain (undetected-identity)
Florence-2 detections.

## Automatic face recognition ("Refresh Faces")

`recognition.py` (`POST /api/faces/refresh`, `GET /api/faces/refresh/status`)
auto-assigns `faces.person_id` to other faces by embedding similarity
against the faces a user has already manually tagged for that individual
— the goal is one manual tag per person, not one per photo. It never
detects faces itself (Florence-2/`sync.py` already did that); it only
decides *whose* face an already-detected, still-untagged box is. Uses
facenet-pytorch's `InceptionResnetV1(pretrained="vggface2")` for
embeddings — a torch-native model, deliberately not DeepFace (which would
require TensorFlow, a second multi-GB ML framework, alongside the torch/
transformers stack Florence-2 already needs — see `requirements.txt`'s
comment on the `facenet-pytorch` line).

Two invariants worth knowing if you're touching this code:

- **Never overwrites an existing tag.** Only faces with `person_id IS
  NULL` are candidates; a manual correction can never be clobbered by a
  later run, and re-running is cheap/idempotent after the first pass.
- **Reference embeddings come only from `tag_source = 'manual'` faces.**
  An auto-matched face is never itself used as a reference for future
  matches — otherwise one bad match could compound into more bad matches.

No embeddings are persisted anywhere — every run recomputes reference
embeddings (from `faces.thumbnail` rows already in the database, no need
to re-fetch originals for those) and re-embeds/re-compares every
currently-untagged face from scratch. The match threshold
(`LUMINARY_FACE_MATCH_THRESHOLD`, default `0.6`, a cosine similarity) is
read via `config.get_face_match_threshold()`.
