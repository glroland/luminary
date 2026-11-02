#!/usr/bin/env bash
# Interactive, destructive rebuild of one ad-hoc application database+role
# -- the script equivalent of `make db-recreate` (which only ever targets
# luminary-dev/luminary-unittest), for a separately-provisioned database
# like prod's. Prompts for every value instead of requiring them all as
# Makefile variables up front, defaulting the database name/username to
# "luminary-prod" (the name used throughout the README's "Database"
# section/deploy/create-secrets.sh's own defaults) -- press enter to
# accept.
#
# Drops the target database and role (if they already exist), then
# recreates both and the schema from scratch via db/create_db_and_user.sql,
# db/create_extensions.sql, and db/create_tables.sql -- the same three
# files `make db`/`make db-create` run, just driven directly with psql so
# this works without a Makefile checkout/admin env vars already exported.
#
# DESTRUCTIVE: every row in the target database is gone once this runs.
# There is no separate "just the schema" option here -- if you only need
# an in-place schema change on data you want to keep, use
# `db/update.sql`/`make db-update` instead (see the README).
set -euo pipefail

if ! command -v psql >/dev/null 2>&1; then
  echo "error: 'psql' not found on PATH" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB_DIR="${SCRIPT_DIR}/../db"

prompt_with_default() {
  # $1=prompt label $2=var name $3=default $4=silent(1/0) -- enter accepts
  # the default, always echoed literally in the bracket (only for defaults
  # that are not real secrets).
  local label="$1" var_name="$2" default="$3" silent="$4" value=""
  if [ "${silent}" = "1" ]; then
    read -r -s -p "${label} [${default}]: " value
    echo
  else
    read -r -p "${label} [${default}]: " value
  fi
  if [ -z "${value}" ]; then
    value="${default}"
  fi
  printf -v "${var_name}" '%s' "${value}"
}

prompt_secret() {
  # $1=prompt label $2=var name $3=default (may be empty) $4=required(1/0)
  # Enter accepts the default. The bracket never echoes the actual default
  # value (it may be a real secret pulled from the environment) -- only
  # whether one is available.
  local label="$1" var_name="$2" default="$3" required="$4" value="" bracket
  if [ -n "${default}" ]; then
    bracket="press enter to use value from environment"
  elif [ "${required}" = "1" ]; then
    bracket="required"
  else
    bracket="blank to skip"
  fi
  while :; do
    read -r -s -p "${label} [${bracket}]: " value
    echo
    if [ -z "${value}" ]; then
      value="${default}"
    fi
    if [ -n "${value}" ] || [ "${required}" != "1" ]; then
      break
    fi
    echo "  (value cannot be empty)"
  done
  printf -v "${var_name}" '%s' "${value}"
}

echo "--- Postgres server ---"
prompt_with_default "Database host" DB_HOST "${LUMINARY_DATABASE_HOST:-db.home.glroland.com}" 0
prompt_with_default "Database port" DB_PORT "${LUMINARY_DATABASE_PORT:-5432}" 0
prompt_with_default "Admin (superuser) username" ADMIN_USER "${LUMINARY_DATABASE_ADMIN_USER:-postgres}" 0
prompt_secret "Admin (superuser) password" ADMIN_PASSWORD "${LUMINARY_DATABASE_ADMIN_PASSWORD:-}" 1
echo

echo "--- Database + role to rebuild ---"
prompt_with_default "Database name" DB_NAME "${LUMINARY_CREATE_DB_NAME:-luminary-prod}" 0
prompt_with_default "Database username" DB_USER "${LUMINARY_CREATE_DB_USER:-luminary-prod}" 0
prompt_secret "Database password" DB_PASSWORD "${LUMINARY_CREATE_DB_PASSWORD:-}" 1
echo

echo "This will DROP and recreate database '${DB_NAME}' and role '${DB_USER}'"
echo "on ${DB_HOST}:${DB_PORT} -- ALL DATA IN '${DB_NAME}' WILL BE LOST."
read -r -p "Type the database name (${DB_NAME}) to confirm: " CONFIRM
if [ "${CONFIRM}" != "${DB_NAME}" ]; then
  echo "Confirmation did not match -- aborting, nothing was touched."
  exit 1
fi
echo

echo "Dropping database '${DB_NAME}' (if it exists)..."
PGPASSWORD="${ADMIN_PASSWORD}" psql \
  -h "${DB_HOST}" -p "${DB_PORT}" -U "${ADMIN_USER}" -d postgres \
  -v db_name="${DB_NAME}" \
  -f "${DB_DIR}/drop_database.sql"

echo "Dropping role '${DB_USER}' (if it exists)..."
PGPASSWORD="${ADMIN_PASSWORD}" psql \
  -h "${DB_HOST}" -p "${DB_PORT}" -U "${ADMIN_USER}" -d postgres \
  -v db_user="${DB_USER}" \
  -f "${DB_DIR}/drop_role.sql"

echo "Creating database '${DB_NAME}' and role '${DB_USER}'..."
PGPASSWORD="${ADMIN_PASSWORD}" psql \
  -h "${DB_HOST}" -p "${DB_PORT}" -U "${ADMIN_USER}" -d postgres \
  -v db_name="${DB_NAME}" -v db_user="${DB_USER}" -v db_password="${DB_PASSWORD}" \
  -f "${DB_DIR}/create_db_and_user.sql"

echo "Installing the pgvector extension..."
PGPASSWORD="${ADMIN_PASSWORD}" psql \
  -h "${DB_HOST}" -p "${DB_PORT}" -U "${ADMIN_USER}" -d "${DB_NAME}" \
  -f "${DB_DIR}/create_extensions.sql"

echo "Creating schema..."
PGPASSWORD="${DB_PASSWORD}" psql \
  -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" \
  -f "${DB_DIR}/create_tables.sql"

unset ADMIN_PASSWORD DB_PASSWORD

echo
echo "Done. '${DB_NAME}' (role '${DB_USER}') has been rebuilt from scratch."
echo "Update luminary-db-credentials (deploy/create-secrets.sh) if the"
echo "name/username/password changed from what's currently deployed."
