#!/usr/bin/env bash
# Creates/updates the Secrets the luminary Helm chart expects for the
# photos backend (SMB/CIFS, etc.) and the OpenAI-compatible chat endpoint,
# in the "luminary" namespace of whatever OpenShift cluster `oc` is
# currently logged into.
#
# The secret names and key names here (PHOTOS_SECRET_NAME/username+password,
# OPENAI_SECRET_NAME/apiKey) must match deploy/helm/luminary/values.yaml's
# api.env.photosCredentialsSecret / api.env.openaiApiKeySecret and the
# secretKeyRef keys in charts/api/templates/deployment.yaml -- if you rename
# one, rename the other.
set -euo pipefail

NAMESPACE="${NAMESPACE:-luminary}"
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

echo "--- Photos backend credentials (${PHOTOS_SECRET_NAME}) ---"
prompt_required "Photos backend username" PHOTOS_USERNAME 0
prompt_required "Photos backend password" PHOTOS_PASSWORD 1
echo

echo "--- OpenAI-compatible chat endpoint credentials (${OPENAI_SECRET_NAME}) ---"
prompt_required "OpenAI-compatible endpoint API key" OPENAI_API_KEY 1
echo

oc create secret generic "${PHOTOS_SECRET_NAME}" \
  --namespace "${NAMESPACE}" \
  --from-literal=username="${PHOTOS_USERNAME}" \
  --from-literal=password="${PHOTOS_PASSWORD}" \
  --dry-run=client -o yaml | oc apply -f -

oc create secret generic "${OPENAI_SECRET_NAME}" \
  --namespace "${NAMESPACE}" \
  --from-literal=apiKey="${OPENAI_API_KEY}" \
  --dry-run=client -o yaml | oc apply -f -

unset PHOTOS_USERNAME PHOTOS_PASSWORD OPENAI_API_KEY

echo
echo "Done. Created/updated in namespace '${NAMESPACE}':"
echo "  ${PHOTOS_SECRET_NAME}  (keys: username, password)"
echo "  ${OPENAI_SECRET_NAME}  (key: apiKey)"
echo
echo "These match deploy/helm/luminary/values.yaml's api.env.photosCredentialsSecret"
echo "and api.env.openaiApiKeySecret -- no further values.yaml changes needed."
