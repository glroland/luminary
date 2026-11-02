-- Incremental schema change, for an already-provisioned database that
-- predates it -- an alternative to `make db-recreate`'s full drop/rebuild
-- when all you need is to alter the existing one in place. Run via
-- `make db-update` (see the Makefile), which applies this to both
-- luminary-dev and luminary-unittest, then truncates this file to zero
-- bytes once it succeeds against both -- write the next schema change's
-- DDL here before running it again. For prod's own `luminary` database,
-- run the same file by hand instead (`make db-update` never touches it --
-- see the README's "Database" section for why):
--
--   psql "postgresql://luminary:<password>@<host>:<port>/luminary" -v ON_ERROR_STOP=1 -f db/update.sql
--
-- Current contents: four independent, idempotent changes, safe to apply
-- together even if one or more were already applied via a current
-- create_tables.sql:
--
-- 1. Backfills faces.tag_source = 'manual' for any face that already had
--    person_id set before tag_source existed (necessarily tagged via the
--    manual tag_face endpoint -- recognition.py's "Refresh Faces"
--    auto-tagging didn't exist yet to have done it). Without this,
--    recognition._reference_embeddings() (which only considers
--    tag_source = 'manual' faces) finds nothing to build reference
--    embeddings from, so "Refresh Faces" silently reports "no individuals
--    have a manually tagged face yet -- nothing to match against" even on
--    a database with plenty of manually tagged faces.
-- 2. Adds photos.location_name: a short, human-readable reverse-geocoded
--    description of a photo's GPS coordinates (e.g. "Boone, North
--    Carolina"), alongside the existing raw latitude/longitude -- see
--    geocode.py and sync.py's _describe_location. Only populated going
--    forward, on the next sync of each photo that has GPS data (same as
--    latitude/longitude themselves -- there's no backfill pass for
--    photos already indexed before this column existed).
-- 3. Renames photos.description (Florence-2's raw caption) to
--    vlm_description, preserving its data, then re-adds a fresh
--    `description` column -- now the LLM-composed rewrite enrich.py
--    builds from vlm_description + tagged individuals + location_name +
--    filename. The rename only fires if `description` exists and
--    `vlm_description` doesn't (a database that's already past this
--    point is untouched); see enrich.py's module docstring for when
--    `description` gets (re)computed going forward.
-- 4. Adds photos.description_embedding (halfvec(3072)) plus its HNSW
--    index, for similarity search -- see embeddings.py's module
--    docstring and create_tables.sql's own comment on this column for
--    the full reasoning. Unlike the first three, this one has a real
--    prerequisite `make db-update` itself can't satisfy: the pgvector
--    extension must already be installed in this database (run
--    db/create_extensions.sql as a superuser first -- db-update connects
--    as the app role, which typically can't CREATE EXTENSION itself). If
--    that hasn't happened, the ALTER TABLE below fails with "type
--    halfvec does not exist", and -v ON_ERROR_STOP=1 (see above) aborts
--    the whole run *without* clearing this file -- install the
--    extension, then just re-run `make db-update`.

UPDATE faces SET tag_source = 'manual' WHERE person_id IS NOT NULL AND tag_source IS NULL;

ALTER TABLE photos ADD COLUMN IF NOT EXISTS location_name TEXT;

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

ALTER TABLE photos ADD COLUMN IF NOT EXISTS description_embedding halfvec(3072);

CREATE INDEX IF NOT EXISTS idx_photos_description_embedding
    ON photos USING hnsw (description_embedding halfvec_cosine_ops);
