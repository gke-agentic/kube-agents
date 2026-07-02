#!/usr/bin/env bash
# ==============================================================================
# 🛠️ Provision Platform Agent Image (Build & Push to GCP Artifact Registry)
# ==============================================================================
# This script builds the platform-agent image using Google Cloud Build and pushes
# it to the project's 'kagent-hybrid' Artifact Registry repository.
# It saves the generated image URI to '.image_uri' for deployment scripts to use.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Load SRE variables
VARS_SH="${SCRIPT_DIR}/vars.sh"
if [ -f "$VARS_SH" ]; then
    source "$VARS_SH"
else
    echo "Error: SRE variables file not found at ${VARS_SH}."
    exit 1
fi

GCP_REPO_NAME="kagent-hybrid"
IMAGE_NAME="platform-agent"
DEV_TAG="dev-$(date +%Y%m%d-%H%M%S)"
IMAGE_BASE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${GCP_REPO_NAME}/${IMAGE_NAME}"
AGENT_IMAGE="${IMAGE_BASE}:${DEV_TAG}"
IMAGE_URI_LATEST="${IMAGE_BASE}:latest"

# Resolve HERMES_AGENT_TAG from tags.env
HERMES_AGENT_TAG=""
if [ -f "${REPO_ROOT}/tags.env" ]; then
    HERMES_AGENT_TAG=$(grep '^HERMES_AGENT_TAG=' "${REPO_ROOT}/tags.env" | cut -d'=' -f2 | tr -d '\r"' | tr -d "'")
fi
if [ -z "$HERMES_AGENT_TAG" ]; then
    echo "Error: Could not resolve HERMES_AGENT_TAG from tags.env"
    exit 1
fi

echo "Checking Artifact Registry repository '${GCP_REPO_NAME}'..."
gcloud artifacts repositories describe "$GCP_REPO_NAME" --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1 || {
    echo "Repository does not exist. Creating repository '${GCP_REPO_NAME}' in region '${REGION}'..."
    gcloud artifacts repositories create "$GCP_REPO_NAME" \
        --repository-format=docker \
        --location="$REGION" \
        --project="$PROJECT_ID" \
        --description="Kubernetes Agentic Harness repository"
}

echo "Submitting Cloud Build for platform-agent..."
echo "Target Image: ${AGENT_IMAGE}"
(
    cd "${REPO_ROOT}"
    gcloud builds submit \
        --config="deploy/docker/cloudbuild.yaml" \
        --substitutions="_IMAGE_URI=${AGENT_IMAGE},_IMAGE_URI_LATEST=${IMAGE_URI_LATEST},_TARGET=platform,_HERMES_AGENT_TAG=${HERMES_AGENT_TAG}" \
        --project="${PROJECT_ID}" \
        .
)

# Save image URI for deploy.sh to read
echo "${AGENT_IMAGE}" > "${SCRIPT_DIR}/.image_uri"
echo "Image URI successfully saved to ${SCRIPT_DIR}/.image_uri"
