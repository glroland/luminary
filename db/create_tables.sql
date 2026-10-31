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
    description TEXT
);

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
