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
-- unlike db/update.sql) -- `photos.description_embedding`/its index further
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
    created_at TEXT NOT NULL,
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
    location_name TEXT
);

-- Added after photos already existed in deployed databases -- IF NOT
-- EXISTS makes this a no-op against a database created after the column
-- was already part of the CREATE TABLE above.
ALTER TABLE photos ADD COLUMN IF NOT EXISTS location_name TEXT;

-- Embedding of `description` (not `vlm_description`), for similarity
-- search (GET /api/photos/{id}/similar, storage.find_similar_photos) --
-- see embeddings.py's module docstring for the full reasoning.
-- Deliberately NOT inline on the CREATE TABLE above, unlike location_name
-- -- halfvec is a type the vector extension provides, so if that
-- extension isn't actually installed (CREATE EXTENSION above failed/was
-- skipped), referencing `halfvec` inside the CREATE TABLE itself would
-- fail the *entire* statement, taking every other column in `photos`
-- down with it. Isolating it in its own ALTER TABLE means a missing
-- extension only costs this one column/its index below, never the rest
-- of the table. halfvec(3072), not vector(3072): pgvector's plain
-- `vector` type only supports HNSW/IVFFlat indexes up to 2,000
-- dimensions, but `halfvec` (16-bit floats) raises that cap to 4,000,
-- comfortably fitting OpenAI text-embedding-3-large's full native 3,072
-- dimensions without truncating it down to fit -- the highest embedding
-- fidelity pgvector can actually index. NULL until
-- enrich.enrich_description() successfully generates a `description` for
-- this photo and embeddings.embed_description() then embeds it;
-- regenerated in lockstep with `description` every time that changes
-- (same trigger list as `description` itself above), never on its own
-- separate schedule.
ALTER TABLE photos ADD COLUMN IF NOT EXISTS description_embedding halfvec(3072);

-- HNSW over cosine distance -- the operator find_similar_photos' `<=>`
-- queries use. Built after the column exists rather than inline on the
-- CREATE TABLE, same as idx_faces_* further down -- an index needs its
-- target column to already exist. A database where the vector extension
-- (and thus the halfvec type/column above) never got created has nothing
-- here for this to index; this statement then errors the same way the
-- CREATE EXTENSION/ALTER TABLE above do, for the same reason (see this
-- file's header comment).
CREATE INDEX IF NOT EXISTS idx_photos_description_embedding
    ON photos USING hnsw (description_embedding halfvec_cosine_ops);

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
    created_at TEXT NOT NULL
);

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
    match_confidence DOUBLE PRECISION
);

-- Added after faces already existed in deployed databases -- IF NOT
-- EXISTS makes each a no-op against a database created after the column
-- was already part of the CREATE TABLE above.
ALTER TABLE faces ADD COLUMN IF NOT EXISTS person_id TEXT;
ALTER TABLE faces ADD COLUMN IF NOT EXISTS thumbnail BYTEA;
ALTER TABLE faces ADD COLUMN IF NOT EXISTS tag_source TEXT;
ALTER TABLE faces ADD COLUMN IF NOT EXISTS match_confidence DOUBLE PRECISION;

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
