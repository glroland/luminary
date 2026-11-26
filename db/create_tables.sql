-- Creates the `photos`/`faces` schema in one application database. Run
-- with psql against that database itself (as the app role created by
-- create_db_and_user.sql, or any role with CREATE privileges on it):
--
--   psql "postgresql://luminary:<password>@<host>:<port>/luminary-dev" \
--     -f db/create_tables.sql
--
-- `make db` runs this once per database (`luminary-dev`,
-- `luminary-unittest`; prod's `luminary` separately, against prod's own
-- Postgres host). Mirrors database.py's init_db(), which runs the same
-- CREATE TABLE IF NOT EXISTS statements on every API startup -- this file
-- exists so the schema can also be provisioned/inspected independently of
-- starting the app. No migrations framework: a schema change means
-- updating both this file and init_db() together.
--
-- This also attempts `CREATE EXTENSION IF NOT EXISTS vector` itself,
-- purely as a convenience no-op for the common case where
-- db/create_extensions.sql already installed it as a superuser (see that
-- file) -- IF NOT EXISTS makes a repeat attempt here harmless. If it
-- *hasn't* run yet and this file's own app-role connection lacks the
-- privilege to create the extension itself, psql prints an error for
-- this one statement and keeps going (no `-v ON_ERROR_STOP=1` here,
-- unlike db/update.sql) -- `photo_embeddings.embedding`/its index further
-- down then also fail the same way, but every other table/column in this
-- file still gets created normally. Run db/create_extensions.sql as a
-- superuser first to avoid relying on that degradation.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS photos (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    width INTEGER,
    height INTEGER,
    created_at TEXT,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    thumbnail BYTEA,
    -- Florence-2's raw <MORE_DETAILED_CAPTION> output (sync.py's
    -- _generate_vlm_description) -- a single automated vision-model
    -- caption of the image itself. Feeds into `description` below as one
    -- of its inputs; not shown directly in the UI.
    vlm_description TEXT,
    -- The description actually shown in the UI: an LLM-composed rewrite
    -- (enrich.py, any OpenAI-*compatible* chat endpoint) that weaves
    -- together vlm_description, the names of any individuals tagged in
    -- the photo, location_name, and the filename/path -- see enrich.py's
    -- module docstring for exactly when this gets (re)computed (sync, a
    -- manual tag/untag, auto-tagging, or deleting an individual -- every
    -- place the facts it's built from can change).
    description TEXT,
    -- Short, human-readable reverse-geocoded description of latitude/
    -- longitude (e.g. "Boone, North Carolina" or "Grandfather Mountain
    -- State Park, Blowing Rock, North Carolina"), via geocode.py --
    -- see sync.py's _describe_location. TEXT (unbounded), same as every
    -- other free-text column here -- there's no reasonable length cap to
    -- apply to a place description. NULL exactly when latitude/longitude
    -- are (no GPS data), or if the reverse-geocode lookup failed.
    location_name TEXT,
    -- The same lookup's structured parts (Nominatim's address.country/
    -- state/city-or-county), for the web sidebar's "By Location" tree.
    -- country is "United States" for any US result. NULL with
    -- location_name, or for photos geocoded before these columns existed
    -- (fixed by "Refresh Locations").
    location_country TEXT,
    location_state TEXT,
    location_city TEXT,
    -- Whether sync.py's caption+face-detection pipeline (_run_florence_
    -- pipeline) -- or recognition.py's full-refresh redetect
    -- (_redetect_photo_faces) -- has successfully run face detection for
    -- this photo at least once. sync.py's incremental backfill
    -- (_sync_one_file's needs_florence) checks this directly rather than
    -- inferring it from `vlm_description IS NOT NULL`, which conflated
    -- "has a caption" with "has had faces detected". Set TRUE only once
    -- faces are safely stored (never on a failed/unconfigured attempt --
    -- see sync.py's module docstring), and reset to FALSE whenever a
    -- photo's content changes, so a stale TRUE from the old content
    -- never suppresses re-detection of the new content. DEFAULT FALSE is
    -- only the starting point for a brand-new row -- see the backfill
    -- UPDATE further down (once `faces` exists) for how a database
    -- created before this column existed gets it set correctly for
    -- already-indexed photos instead of being left at this default.
    face_detection_done BOOLEAN NOT NULL DEFAULT FALSE
);

-- Added after photos already existed in deployed databases -- IF NOT
-- EXISTS makes this a no-op against a database created after the column
-- was already part of the CREATE TABLE above.
ALTER TABLE photos ADD COLUMN IF NOT EXISTS location_name TEXT;
ALTER TABLE photos ADD COLUMN IF NOT EXISTS location_country TEXT;
ALTER TABLE photos ADD COLUMN IF NOT EXISTS location_state TEXT;
ALTER TABLE photos ADD COLUMN IF NOT EXISTS location_city TEXT;
-- A photo with no date of its own is undated (NULL), never stamped with
-- the time it was indexed. No-op once the constraint is gone.
ALTER TABLE photos ALTER COLUMN created_at DROP NOT NULL;
ALTER TABLE photos ADD COLUMN IF NOT EXISTS face_detection_done BOOLEAN NOT NULL DEFAULT FALSE;
-- TRUE once pet detection has successfully run for the photo (see
-- pet_detection.py). Separate from face_detection_done so the pets backfill
-- can find photos indexed before pets existed.
ALTER TABLE photos ADD COLUMN IF NOT EXISTS pet_detection_done BOOLEAN NOT NULL DEFAULT FALSE;

-- SHA-256 hex digest of the file's raw bytes (storage.compute_checksum),
-- for reliably telling whether two files are byte-identical -- unlike
-- size_bytes, a collision here is cryptographically implausible.
-- (Re)computed by sync.py whenever content_changed, or backfilled lazily
-- whenever a sync encounters a row where this is still NULL (e.g. a
-- database indexed before this column existed) -- same "field missing,
-- backfill it" shape as vlm_description/thumbnail above, not a one-time
-- UPDATE here, since computing it requires reading the file itself.
ALTER TABLE photos ADD COLUMN IF NOT EXISTS checksum TEXT;

-- 64-bit perceptual hash (perceptual.dhash) of the stored thumbnail, a signed
-- BIGINT, for finding resized/re-encoded/reformatted duplicates -- see
-- docs/duplicate-detection.md. NULL for a photo with no thumbnail or a flat
-- image; phash_done records that hashing was attempted for the current
-- thumbnail. Backfilled by database.backfill_data() (needs Pillow, not SQL).
ALTER TABLE photos ADD COLUMN IF NOT EXISTS phash BIGINT;
ALTER TABLE photos ADD COLUMN IF NOT EXISTS phash_done BOOLEAN NOT NULL DEFAULT FALSE;

-- A face a user untagged after recognition.py auto-matched it to
-- `person_id` ("this is NOT that person"). Unlike a faces row it survives
-- re-detection (faces get fresh ids), because it carries the crop itself:
-- recognition.py embeds `thumbnail` and uses it as a negative example.
-- Only auto tags are recorded (people.untag_face). match_confidence /
-- rejected_at_threshold: the wrong match's score and the match threshold
-- then in force. last_vetoed_*: the most recent time this row blocked a
-- candidate (NULL = never has). No FK; deleted explicitly.
CREATE TABLE IF NOT EXISTS face_rejections (
    id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL,
    photo_id TEXT NOT NULL,
    x1 DOUBLE PRECISION NOT NULL,
    y1 DOUBLE PRECISION NOT NULL,
    x2 DOUBLE PRECISION NOT NULL,
    y2 DOUBLE PRECISION NOT NULL,
    thumbnail BYTEA NOT NULL,
    match_confidence DOUBLE PRECISION,
    rejected_at_threshold DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_vetoed_score DOUBLE PRECISION,
    last_vetoed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_face_rejections_person_id ON face_rejections (person_id);
CREATE INDEX IF NOT EXISTS idx_face_rejections_photo_id ON face_rejections (photo_id);

-- Per-sentence embeddings (embeddings.py) for similarity/semantic search
-- (storage.search_photos, storage.find_similar_photos): one row per
-- sentence of `photos.description` (source_type 'D'), many per photo.
-- sentence_index is the sentence's 0-based position. No FK (rows are
-- deleted explicitly, like faces/photo_tags). The `embedding` column is a
-- separate ALTER, not inline, so a missing pgvector extension only costs
-- that column/its index, never the table. vector(1024): the local
-- BAAI/bge-large-en-v1.5 model's native size (see embeddings.py).
-- Regenerated in lockstep with `description`.
CREATE TABLE IF NOT EXISTS photo_embeddings (
    id TEXT PRIMARY KEY,
    photo_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    sentence_index INTEGER NOT NULL,
    sentence_text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_photo_embeddings_photo_id ON photo_embeddings (photo_id);

-- Migration: the old single-vector photos.description_embedding column is
-- superseded by the table above (its values aren't converted; "Refresh
-- Descriptions" re-embeds every photo).
DROP INDEX IF EXISTS idx_photos_description_embedding;
ALTER TABLE photos DROP COLUMN IF EXISTS description_embedding;

ALTER TABLE photo_embeddings ADD COLUMN IF NOT EXISTS embedding vector(1024);
CREATE INDEX IF NOT EXISTS idx_photo_embeddings_embedding
    ON photo_embeddings USING hnsw (embedding vector_cosine_ops);

-- `description` used to hold Florence-2's raw caption directly; that's now
-- `vlm_description`, freeing `description` for enrich.py's LLM-composed
-- rewrite (see the CREATE TABLE's own comments above). A database created
-- before this split has a `description` column full of real captions and
-- no `vlm_description` at all -- rename the former to the latter
-- (preserving that data) before re-adding a fresh `description` below,
-- rather than silently losing every caption already indexed. The
-- IF/AND NOT EXISTS guard makes this a no-op on a database that's already
-- past this point (including one created fresh by the CREATE TABLE above,
-- which already has both columns).
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'photos' AND column_name = 'description'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'photos' AND column_name = 'vlm_description'
    ) THEN
        ALTER TABLE photos RENAME COLUMN description TO vlm_description;
    END IF;
END $$;

ALTER TABLE photos ADD COLUMN IF NOT EXISTS vlm_description TEXT;
ALTER TABLE photos ADD COLUMN IF NOT EXISTS description TEXT;

-- Catalog of named individuals faces can be manually tagged as (see
-- `faces.person_id` below) -- people.py / routers/individuals.py.
CREATE TABLE IF NOT EXISTS individuals (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    -- Small JPEG crop of the most recently tagged face for this person
    -- (people.tag_face) -- a single representative image, not a gallery of
    -- every face ever tagged to them.
    representative_thumbnail BYTEA,
    created_at TEXT NOT NULL,
    -- 'person' | 'pet'. A face can only be tagged to an individual of its
    -- own kind (people.tag_face), and recognition only compares like with
    -- like.
    kind TEXT NOT NULL DEFAULT 'person'
);
ALTER TABLE individuals ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'person';

-- Catalog of free-form tags whole photos can be tagged with (see
-- `photo_tags` below) -- tags.py / routers/tags.py, routers/photos.py's
-- photo-tag endpoints.
CREATE TABLE IF NOT EXISTS tags (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

-- Many-to-many between photos and tags. No FK constraint, same
-- explicit-delete rationale as `faces` below (storage.delete_photo/
-- purge_all and sync._delete_missing all clean up this table's rows for a
-- photo they remove).
CREATE TABLE IF NOT EXISTS photo_tags (
    photo_id TEXT NOT NULL,
    tag_id TEXT NOT NULL,
    PRIMARY KEY (photo_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_photo_tags_tag_id ON photo_tags (tag_id);

-- One photo -> many detected faces (sync.py's Florence-2 face detection).
-- No FK constraint, to match the app's own explicit-delete approach
-- (storage.delete_photo, sync._delete_missing) rather than relying on a
-- cascade.
CREATE TABLE IF NOT EXISTS faces (
    id TEXT PRIMARY KEY,
    photo_id TEXT NOT NULL,
    x1 DOUBLE PRECISION NOT NULL,
    y1 DOUBLE PRECISION NOT NULL,
    x2 DOUBLE PRECISION NOT NULL,
    y2 DOUBLE PRECISION NOT NULL,
    -- Manually assigned via people.tag_face (POST /api/faces/{id}/tag), or
    -- auto-assigned via recognition.py's "Refresh Faces" job (see
    -- tag_source below) -- NULL until tagged either way. Logically
    -- references individuals.id, same "no FK" rationale as faces.photo_id
    -- below.
    person_id TEXT,
    -- Small JPEG crop of this specific face, set alongside person_id --
    -- the management page's per-individual face list reads this (unlike
    -- individuals.representative_thumbnail above, which only ever holds
    -- the *latest* tag). Always NULL when person_id is (people.tag_face/
    -- untag_face/delete_individual/recognition's auto-tagging keep the
    -- two in lockstep).
    thumbnail BYTEA,
    -- 'manual' (people.tag_face) or 'auto' (recognition.py, matched by
    -- face-embedding similarity against that person's manually tagged
    -- faces only -- see recognition.py's module docstring for why auto
    -- matches are never themselves used as future reference faces). NULL
    -- exactly when person_id is.
    tag_source TEXT,
    -- Cosine similarity score from recognition.py's match, NULL for
    -- manual tags (and for untagged faces).
    match_confidence DOUBLE PRECISION,
    -- MTCNN's own per-candidate detection probability (face_detection.
    -- detect_faces -- the same score LUMINARY_FACE_DETECTION_THRESHOLD is
    -- compared against to decide whether a box is reported at all), set
    -- for every row since every row came from a detection pass. Unrelated
    -- to match_confidence above -- that's recognition.py's later, separate
    -- cosine-similarity score for *whose* face this is, not whether it's
    -- a face at all.
    detection_confidence DOUBLE PRECISION
);

-- Added after faces already existed in deployed databases -- IF NOT
-- EXISTS makes each a no-op against a database created after the column
-- was already part of the CREATE TABLE above.
ALTER TABLE faces ADD COLUMN IF NOT EXISTS person_id TEXT;
ALTER TABLE faces ADD COLUMN IF NOT EXISTS thumbnail BYTEA;
ALTER TABLE faces ADD COLUMN IF NOT EXISTS tag_source TEXT;
ALTER TABLE faces ADD COLUMN IF NOT EXISTS match_confidence DOUBLE PRECISION;
ALTER TABLE faces ADD COLUMN IF NOT EXISTS detection_confidence DOUBLE PRECISION;
-- 'person' (face_detection.py's MTCNN) or 'pet' (pet_detection.py's
-- whole-animal box). `species` is 'cat'/'dog' for a pet, NULL for a person.
-- A constant DEFAULT is metadata-only in Postgres: no row rewrite, and every
-- pre-existing row correctly reads 'person'.
ALTER TABLE faces ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'person';
ALTER TABLE faces ADD COLUMN IF NOT EXISTS species TEXT;

-- Backfill: any face tagged before tag_source existed was necessarily
-- tagged via the manual tag_face endpoint (recognition.py's auto-tagging
-- didn't exist yet). Without this, such a row has person_id set but
-- tag_source still NULL, so recognition._reference_embeddings() (which
-- filters on tag_source = 'manual') finds no reference faces at all and
-- "Refresh Faces" silently reports "nothing to match against" even with
-- plenty of manually tagged faces. A no-op once backfilled -- safe to
-- re-run.
UPDATE faces SET tag_source = 'manual' WHERE person_id IS NOT NULL AND tag_source IS NULL;

CREATE INDEX IF NOT EXISTS idx_faces_photo_id ON faces (photo_id);
CREATE INDEX IF NOT EXISTS idx_faces_person_id ON faces (person_id);

-- Backfill for photos.face_detection_done, now that `faces` exists: a
-- photo that already has at least one detected face on record clearly
-- already went through detection, so it's backfilled to TRUE rather than
-- left at the column's blanket FALSE default (which would otherwise
-- force every already-indexed, face-bearing photo through a pointless
-- redetect on the next sync). A photo with zero faces on record is left
-- at FALSE -- indistinguishable here from "detection genuinely found
-- nobody" vs. "detection never ran" (no stored history to tell those
-- apart) -- so it's treated the conservative way: the next sync just
-- (re)runs detection once for such a photo. A no-op once applied -- safe
-- to re-run.
UPDATE photos SET face_detection_done = TRUE
    WHERE NOT face_detection_done
      AND EXISTS (SELECT 1 FROM faces WHERE faces.photo_id = photos.id);
