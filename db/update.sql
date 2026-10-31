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
-- Current contents: adds a per-face thumbnail (people.py's management-page
-- face list) to a database that only has faces.person_id from manual face
-- tagging -- see faces.thumbnail's own comment in create_tables.sql. A
-- no-op against a database already created via a create_tables.sql that
-- includes this column.

ALTER TABLE faces ADD COLUMN IF NOT EXISTS thumbnail BYTEA;
