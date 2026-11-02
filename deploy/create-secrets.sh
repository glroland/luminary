#!/usr/bin/env bash
# Creates/updates the Secrets the luminary Helm chart expects for the
# Postgres database, the photos backend (SMB/CIFS, etc.), and the
# OpenAI-compatible chat endpoint, in the "luminary" namespace of whatever
# OpenShift cluster `oc` is currently logged into.
#
# The secret names and key names here (DB_SECRET_NAME/username+password,
# PHOTOS_SECRET_NAME/username+password, OPENAI_SECRET_NAME/apiKey) must
# match deploy/helm/luminary/values.yaml's api.env.databaseCredentialsSecret
# / api.env.photosCredentialsSecret / api.env.openaiApiKeySecret and the
# secretKeyRef keys in charts/api/templates/deployment.yaml -- if you rename
# one, rename the other. The database role itself (matching username/
# password) is provisioned separately via `make db`/`db/` -- see the
# README's "Database" section.
#
# The database secret is always required. The photos-backend and OpenAI
# secrets are skipped (left blank, nothing created/touched) if you leave
# their first prompt empty -- e.g. a local/NFS photos backend or S3 via IAM
# role needs no credentials, and you may not be using the openai chat
# backend at all.
set -euo pipefail

NAMESPACE="${NAMESPACE:-luminary}"
DB_SECRET_NAME="luminary-db-credentials"
PHOTOS_SECRET_NAME="luminary-photos-credentials"
OPENAI_SECRET_NAME="luminary-openai-credentials"

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

prompt_required() {
  # $1=prompt label $2=var name $3=silent(1/0)
  local label="$1" var_name="$2" silent="$3" value=""
  while [ -z "${value}" ]; do
    if [ "${silent}" = "1" ]; then
      read -r -s -p "${label}: " value
      echo
    else
      read -r -p "${label}: " value
    fi
    if [ -z "${value}" ]; then
      echo "  (value cannot be empty)"
    fi
  done
  printf -v "${var_name}" '%s' "${value}"
}

prompt_optional() {
  # $1=prompt label $2=var name $3=silent(1/0) -- allows an empty value
  local label="$1" var_name="$2" silent="$3" value=""
  if [ "${silent}" = "1" ]; then
    read -r -s -p "${label}: " value
    echo
  else
    read -r -p "${label}: " value
  fi
  printf -v "${var_name}" '%s' "${value}"
}

echo "--- Database credentials (${DB_SECRET_NAME}, always required) ---"
prompt_required "Database username" DB_USERNAME 0
prompt_required "Database password" DB_PASSWORD 1
echo

echo "--- Photos backend credentials (${PHOTOS_SECRET_NAME}, blank to skip) ---"
prompt_optional "Photos backend username (blank to skip)" PHOTOS_USERNAME 0
if [ -n "${PHOTOS_USERNAME}" ]; then
  prompt_required "Photos backend password" PHOTOS_PASSWORD 1
fi
echo

echo "--- OpenAI-compatible chat endpoint credentials (${OPENAI_SECRET_NAME}, blank to skip) ---"
prompt_optional "OpenAI-compatible endpoint API key (blank to skip)" OPENAI_API_KEY 1
echo

oc create secret generic "${DB_SECRET_NAME}" \
  --namespace "${NAMESPACE}" \
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

unset DB_USERNAME DB_PASSWORD PHOTOS_USERNAME PHOTOS_PASSWORD OPENAI_API_KEY

echo
echo "Done. These secret names match deploy/helm/luminary/values.yaml's"
echo "api.env.databaseCredentialsSecret, api.env.photosCredentialsSecret, and"
echo "api.env.openaiApiKeySecret -- no further values.yaml changes needed."
echo "Note the database username/password must also match whatever role"
echo "'make db'/db/create_db_and_user.sql actually provisioned."
