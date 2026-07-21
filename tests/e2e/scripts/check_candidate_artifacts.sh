#!/usr/bin/env bash
# ==============================================================================
# 🔍 Check Candidate Artifacts Sync Status Helper Script
# ==============================================================================
# Compares candidate ':latest' image digests against ':validated-stable' digests
# in GHCR for the pair of images (platform-agent & k8s-operator).
# Exports GitHub Action outputs:
#   - needs_validation=true/false
#   - <image_prefix>_sha=<sha256:...> (e.g. platform_agent_sha, k8s_operator_sha)
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

ensure_crane

echo "======================================================================"
echo "🔍 Checking GHCR Candidate Artifact Sync Status (Pair Validation)"
echo "----------------------------------------------------------------------"
echo "Registry: ${REGISTRY}/${OWNER}"
echo "Images:   ${IMAGES}"
echo "======================================================================"

NEEDS_VALIDATION="false"
UNVALIDATED_LIST=()

for IMG in ${IMAGES}; do
  FULL_IMAGE="${REGISTRY}/${OWNER}/${IMG}"

  LATEST_DIGEST=$("${CRANE_BIN}" digest "${FULL_IMAGE}:${SOURCE_TAG}" 2>/dev/null || echo "none")
  STABLE_DIGEST=$("${CRANE_BIN}" digest "${FULL_IMAGE}:validated-stable" 2>/dev/null || echo "missing")

  VAR_PREFIX=$(to_var_prefix "$IMG")

  echo "Image: ${FULL_IMAGE}"
  echo "  - Candidate (${SOURCE_TAG})           Digest: ${LATEST_DIGEST:0:25}..."
  echo "  - Last Validated (:validated-stable) Digest: ${STABLE_DIGEST:0:25}..."

  if [ -n "${GITHUB_OUTPUT:-}" ]; then
    echo "${VAR_PREFIX}_sha=${LATEST_DIGEST}" >> "$GITHUB_OUTPUT"
  fi

  if [ "$LATEST_DIGEST" = "none" ]; then
    echo "::error:: Candidate image ${FULL_IMAGE}:${SOURCE_TAG} was not found in GHCR!" >&2
    echo "[FAIL] Missing required candidate image: ${FULL_IMAGE}:${SOURCE_TAG}. Aborting pipeline." >&2
    exit 1
  elif [ "$LATEST_DIGEST" != "$STABLE_DIGEST" ]; then
    echo "  -> [STATUS] Unvalidated or updated candidate detected for ${IMG}!"
    NEEDS_VALIDATION="true"
    UNVALIDATED_LIST+=("${IMG}")
  else
    echo "  -> [STATUS] Up to date (matches :validated-stable)."
  fi
  echo "----------------------------------------------------------------------"
done

if [ "$NEEDS_VALIDATION" = "true" ]; then
  echo "[RESULT] At least one image in the pair (${UNVALIDATED_LIST[*]}) requires validation."
  echo "[RESULT] Starting Autopush pipeline to deploy and validate BOTH images (${IMAGES}) as a pair."
  if [ -n "${GITHUB_OUTPUT:-}" ]; then
    echo "needs_validation=true" >> "$GITHUB_OUTPUT"
  fi
  exit 0
else
  echo "[RESULT] All candidate images in the pair (${IMAGES}) are up to date and marked as validated-stable. Skipping test pipeline!"
  if [ -n "${GITHUB_OUTPUT:-}" ]; then
    echo "needs_validation=false" >> "$GITHUB_OUTPUT"
  fi
  exit 0
fi
