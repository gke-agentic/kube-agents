#!/usr/bin/env bash
# ==============================================================================
# 🏷️ Label Stable Artifacts Helper Script
# ==============================================================================
# Tags GHCR candidate container images with 'validated-stable-YYYY-MM-DD-HH_MM_utc'
# timestamp tag and updates floating 'validated-stable' tag upon E2E test success.
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

RUN_URL="${RUN_URL:-${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:-${OWNER}}/actions/runs/${GITHUB_RUN_ID:-local}}"

# Generate timestamp tag format: validated-stable-YYYY-MM-DD-HH_MM_utc
TIMESTAMP=$(date -u +"%Y-%m-%d-%H_%M_utc")
STABLE_TIMESTAMP_TAG="validated-stable-${TIMESTAMP}"
FLOATING_STABLE_TAG="validated-stable"

ensure_crane

echo "======================================================================"
echo "🏷️ Labeling Stable Artifacts in GHCR"
echo "----------------------------------------------------------------------"
echo "Registry:     ${REGISTRY}/${OWNER}"
echo "Images:       ${IMAGES}"
echo "Source Tag:   ${SOURCE_TAG}"
echo "Stable Tag:   ${STABLE_TIMESTAMP_TAG}"
echo "Floating Tag: ${FLOATING_STABLE_TAG}"
echo "Pipeline URL: ${RUN_URL}"
echo "======================================================================"

for IMG in ${IMAGES}; do
  FULL_IMAGE="${REGISTRY}/${OWNER}/${IMG}"
  echo -n "[INFO] Fetching candidate digest for ${FULL_IMAGE}:${SOURCE_TAG}... "

  DIGEST=$("${CRANE_BIN}" digest "${FULL_IMAGE}:${SOURCE_TAG}" 2>/dev/null || echo "")

  if [ -z "$DIGEST" ]; then
    echo "[ERROR] Failed to fetch digest for ${FULL_IMAGE}:${SOURCE_TAG}" >&2
    exit 1
  fi

  echo "OK (${DIGEST:0:19}...)"

  echo "[INFO] Applying tag '${STABLE_TIMESTAMP_TAG}' to ${FULL_IMAGE}@${DIGEST}..."
  "${CRANE_BIN}" tag "${FULL_IMAGE}@${DIGEST}" "${STABLE_TIMESTAMP_TAG}"

  echo "[INFO] Updating floating tag '${FLOATING_STABLE_TAG}' for ${FULL_IMAGE}@${DIGEST}..."
  "${CRANE_BIN}" tag "${FULL_IMAGE}@${DIGEST}" "${FLOATING_STABLE_TAG}"

  echo "[SUCCESS] Successfully tagged ${FULL_IMAGE} as '${STABLE_TIMESTAMP_TAG}' and '${FLOATING_STABLE_TAG}'!"
done

echo "======================================================================"
echo "✅ All packages successfully labeled as validated-stable!"
echo "======================================================================"
