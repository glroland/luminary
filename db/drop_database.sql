-- Drops a single application database -- destructive. Separate from
-- dropping the role (see drop_role.sql): the same `luminary` role owns
-- more than one database (luminary-dev, luminary-unittest, and prod's
-- luminary -- see the README's "Database" section), and Postgres refuses
-- to drop a role that still owns any database, so each database using a
-- shared role must be dropped before that role is.
--
-- Run with psql as a Postgres superuser, against the `postgres`
-- maintenance database (you can't drop a database you're connected to):
--
--   psql "postgresql://postgres:<admin-password>@<host>:<port>/postgres" \
--     -v db_name=luminary-dev \
--     -f db/drop_database.sql
--
-- `make drop-db`/`make recreate-db` run this once per database.

-- DROP DATABASE fails if anything still holds a connection to it (e.g. a
-- stale `make run-api`/`make test-api` from an earlier run) -- terminate
-- those first.
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = :'db_name' AND pid <> pg_backend_pid();

DROP DATABASE IF EXISTS :"db_name";
