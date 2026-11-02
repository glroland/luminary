#!/usr/bin/env bash
# Creates/updates the Secrets the luminary Helm chart expects for the
# Postgres database, the photos backend (SMB/CIFS, etc.), and the
# OpenAI-compatible chat endpoint, in the "luminary" namespace of whatever
# OpenShift cluster `oc` is currently logged into.
#
# The secret names and key names here (DB_SECRET_NAME/database+username+
# password, PHOTOS_SECRET_NAME/username+password, OPENAI_SECRET_NAME/
# apiKey) must match deploy/helm/luminary/values.yaml's
# api.env.databaseCredentialsSecret / api.env.photosCredentialsSecret /
# api.env.openaiApiKeySecret and the secretKeyRef keys in
# charts/api/templates/deployment.yaml -- if you rename one, rename the
# other. The database+role themselves are provisioned separately via
# `make db`/`make db-create`/`db/` -- see the README's "Database" section.
#
# The database secret is always required. Its name/username/password
# prompts default to LUMINARY_DATABASE_NAME/_USER/_PASSWORD from the
# environment if set, else the "luminary" placeholder Makefile's
# LUMINARY_DATABASE_USER/_PASSWORD themselves default to (the database
# *name* has no single Makefile default worth reusing here -- make db
# creates luminary-dev/luminary-unittest, make db-create takes a name
# explicitly -- so it falls back to the same "luminary" placeholder
# instead) -- press enter to accept.
#
# The photos-backend and OpenAI secrets are skipped (left blank, nothing
# created/touched) if you leave their first prompt empty -- e.g. a
# local/NFS photos backend or S3 via IAM role needs no credentials, and you
# may not be using the openai chat backend at all. Their prompts default to
# LUMINARY_PHOTOS_USERNAME/_PASSWORD/LUMINARY_OPENAI_API_KEY from the
# calling shell's environment, if set (the same env vars `make run-api`
# reads) -- press enter to accept. Unlike the database defaults above,
# these may be real secrets, so the password/API key prompts never echo
# the actual default value, only that one is available.
set -euo pipefail

NAMESPACE="${NAMESPACE:-luminary}"
DB_SECRET_NAME="luminary-db-credentials"
PHOTOS_SECRET_NAME="luminary-photos-credentials"
OPENAI_SECRET_NAME="luminary-openai-credentials"

# Name/username aren't secrets, so these fall back to LUMINARY_DATABASE_
# NAME/_USER from the environment (e.g. already exported for `make
# db-create`) and are always echoed literally at the prompt. Both default
# to "luminary-prod" rather than the generic "luminary" placeholder
# LUMINARY_DATABASE_USER/_PASSWORD themselves default to in the Makefile --
# this script is normally run against a real prod database+role created
# via `make db-create`, and that's the name used in the examples here/in
# the README's "Database" section.
DB_DEFAULT_NAME="${LUMINARY_DATABASE_NAME:-luminary-prod}"
DB_DEFAULT_USERNAME="${LUMINARY_DATABASE_USER:-luminary-prod}"
# The password default is handled separately below: LUMINARY_DATABASE_
# PASSWORD from the environment *is* a real secret, so unlike the two
# above it must never be echoed, only the hardcoded "luminary" placeholder
# (when there's no environment override) is safe to show literally.
DB_PASSWORD_DEFAULT="${LUMINARY_DATABASE_PASSWORD:-}"

# Defaults for the photos-backend/OpenAI prompts below, pulled from the
# environment if the caller already has them set (e.g. sourced the same
# profile `make run-api` uses) -- empty if not set, which just means
# "blank to skip" at the prompt.
PHOTOS_USERNAME_DEFAULT="${LUMINARY_PHOTOS_USERNAME:-}"
PHOTOS_PASSWORD_DEFAULT="${LUMINARY_PHOTOS_PASSWORD:-}"
OPENAI_API_KEY_DEFAULT="${LUMINARY_OPENAI_API_KEY:-}"

if ! command -v oc >/dev/null 2>&1; then
  echo "error: 'oc' (OpenShift CLI) not found on PATH" >&2
  exit 1
fi

if ! oc whoami >/dev/null 2>&1; then
  echo "error: not logged into an OpenShift cluster (run 'oc login' first)" >&2
  exit 1
fi

echo "Target cluster: $(oc whoami --show-server 2>/dev/null || echo unknown)"
echo "Target namespace: ${NAMESPACE}"
echo

if ! oc get namespace "${NAMESPACE}" >/dev/null 2>&1; then
  echo "Namespace '${NAMESPACE}' does not exist, creating it."
  oc create namespace "${NAMESPACE}"
fi

prompt_with_default() {
  # $1=prompt label $2=var name $3=default $4=silent(1/0) -- enter accepts
  # the default, always echoed literally in the bracket (only for defaults
  # that are not real secrets, e.g. the DB dev placeholder below).
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

prompt_optional_visible() {
  # $1=prompt label $2=var name $3=default (may be empty) -- enter accepts
  # the default; not silent, so only use for non-secret values.
  local label="$1" var_name="$2" default="$3" value="" bracket="blank to skip"
  if [ -n "${default}" ]; then
    bracket="${default}"
  fi
  read -r -p "${label} [${bracket}]: " value
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

echo "--- Database credentials (${DB_SECRET_NAME}, always required) ---"
prompt_with_default "Database name" DB_NAME "${DB_DEFAULT_NAME}" 0
prompt_with_default "Database username" DB_USERNAME "${DB_DEFAULT_USERNAME}" 0
if [ -n "${DB_PASSWORD_DEFAULT}" ]; then
  prompt_secret "Database password" DB_PASSWORD "${DB_PASSWORD_DEFAULT}" 1
else
  prompt_with_default "Database password" DB_PASSWORD "luminary" 1
fi
echo

echo "--- Photos backend credentials (${PHOTOS_SECRET_NAME}, blank to skip) ---"
prompt_optional_visible "Photos backend username" PHOTOS_USERNAME "${PHOTOS_USERNAME_DEFAULT}"
if [ -n "${PHOTOS_USERNAME}" ]; then
  prompt_secret "Photos backend password" PHOTOS_PASSWORD "${PHOTOS_PASSWORD_DEFAULT}" 1
fi
echo

echo "--- OpenAI-compatible chat endpoint credentials (${OPENAI_SECRET_NAME}, blank to skip) ---"
prompt_secret "OpenAI-compatible endpoint API key" OPENAI_API_KEY "${OPENAI_API_KEY_DEFAULT}" 0
echo

oc create secret generic "${DB_SECRET_NAME}" \
  --namespace "${NAMESPACE}" \
  --from-literal=database="${DB_NAME}" \
  --from-literal=username="${DB_USERNAME}" \
  --from-literal=password="${DB_PASSWORD}" \
  --dry-run=client -o yaml | oc apply -f -
echo "Created/updated ${DB_SECRET_NAME}"

if [ -n "${PHOTOS_USERNAME}" ]; then
  oc create secret generic "${PHOTOS_SECRET_NAME}" \
    --namespace "${NAMESPACE}" \
    --from-literal=username="${PHOTOS_USERNAME}" \
    --from-literal=password="${PHOTOS_PASSWORD}" \
    --dry-run=client -o yaml | oc apply -f -
  echo "Created/updated ${PHOTOS_SECRET_NAME}"
else
  echo "Skipped ${PHOTOS_SECRET_NAME} (no username given)"
fi

if [ -n "${OPENAI_API_KEY}" ]; then
  oc create secret generic "${OPENAI_SECRET_NAME}" \
    --namespace "${NAMESPACE}" \
    --from-literal=apiKey="${OPENAI_API_KEY}" \
    --dry-run=client -o yaml | oc apply -f -
  echo "Created/updated ${OPENAI_SECRET_NAME}"
else
  echo "Skipped ${OPENAI_SECRET_NAME} (no API key given)"
fi

unset DB_NAME DB_USERNAME DB_PASSWORD PHOTOS_USERNAME PHOTOS_PASSWORD OPENAI_API_KEY

echo
echo "Done. These secret names match deploy/helm/luminary/values.yaml's"
echo "api.env.databaseCredentialsSecret, api.env.photosCredentialsSecret, and"
echo "api.env.openaiApiKeySecret -- no further values.yaml changes needed."
echo "Note the database name/username/password must also match whatever"
echo "'make db'/'make db-create'/db/create_db_and_user.sql actually provisioned."
