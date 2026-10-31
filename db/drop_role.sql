-- Drops the shared login role -- destructive, and must run *after* every
-- database it owns has already been dropped (see drop_database.sql):
-- Postgres refuses "DROP ROLE" while the role still owns any database.
--
-- Run with psql as a Postgres superuser, against the `postgres`
-- maintenance database:
--
--   psql "postgresql://postgres:<admin-password>@<host>:<port>/postgres" \
--     -v db_user=luminary \
--     -f db/drop_role.sql
--
-- `make drop-db`/`make recreate-db` run this once, after drop_database.sql.

DROP ROLE IF EXISTS :"db_user";
