-- Creates one application database and the login role the API connects as.
-- Run with psql as a Postgres superuser, against the `postgres` maintenance
-- database (the target database doesn't exist yet on a first run):
--
--   psql "postgresql://postgres:<admin-password>@<host>:<port>/postgres" \
--     -v db_name=luminary-dev -v db_user=luminary -v db_password=luminary \
--     -f db/create_db_and_user.sql
--
-- `make db` runs this once per database -- `luminary-dev` (local
-- development) and `luminary-unittest` (`make test-api`), both owned by the
-- same `luminary` role; prod's `luminary` database (used by the Helm
-- deployment) is provisioned the same way, separately, against prod's own
-- Postgres host. Role creation is guarded so re-running this for a second
-- database with the same db_user doesn't error (see the Makefile's
-- LUMINARY_DATABASE_ADMIN_USER/_PASSWORD and LUMINARY_DATABASE_HOST/_PORT/
-- _NAME/_USER/_PASSWORD/LUMINARY_TEST_DATABASE_NAME variables). Idempotent
-- either way: safe to re-run against an already-provisioned server --
-- CREATE ROLE/CREATE DATABASE have no IF NOT EXISTS of their own in
-- Postgres, so both are guarded explicitly below instead.

SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'db_user', :'db_password')
WHERE NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = :'db_user')
\gexec

SELECT format('CREATE DATABASE %I OWNER %I', :'db_name', :'db_user')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'db_name')
\gexec

GRANT ALL PRIVILEGES ON DATABASE :"db_name" TO :"db_user";
