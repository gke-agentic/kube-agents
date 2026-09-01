#!/usr/bin/env bash
# Generates SPDX 2.3 and CycloneDX 1.5 JSON Software Bill of Materials (SBOM) using Syft for the release bundle and OCI images.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# shellcheck source=scripts/release/common.sh
source "${SCRIPT_DIR}/common.sh"

TAG_NAME="${1:-${TAG_NAME:-${GITHUB_REF_NAME:-}}}"
TARGET_DIR="${2:-${TARGET_DIR:-${REPO_ROOT}}}"
DIST_DIR="${DIST_DIR:-${REPO_ROOT}/build/dist}"

if [ -z "${TAG_NAME}" ]; then
  echo "❌ ERROR: TAG_NAME must be specified as first argument or environment variable." >&2
  exit 1
fi

validate_pure_numeric_semver "${TAG_NAME}" "Release tag" || exit 1

if [ ! -d "${TARGET_DIR}" ]; then
  echo "❌ ERROR: Target directory '${TARGET_DIR}' does not exist!" >&2
  exit 1
fi

if ! command -v syft >/dev/null 2>&1; then
  if is_ci_pipeline; then
    echo "❌ ERROR: 'syft' CLI is mandatory in CI for SBOM generation but not found in PATH." >&2
    exit 1
  else
    echo "⚠️ WARNING: 'syft' CLI is not found in PATH. Skipping local SBOM generation." >&2
    exit 0
  fi
fi

BUNDLE_PREFIX="kube-agents-${TAG_NAME}"
REGISTRY_PREFIX="$(get_registry_prefix)"

echo "======================================================================"
echo "🛡️ GENERATING RELEASE SBOMs (SPDX 2.3 & CycloneDX 1.5 via Syft)"
echo "Tag Name:     ${TAG_NAME}"
echo "Target Dir:   ${TARGET_DIR}"
echo "Destination:  ${DIST_DIR}"
echo "======================================================================"

# Isolated staging directory for all-or-nothing generation (guarantees idempotency and clean crash recovery)
TMP_SBOM_DIR="$(mktemp -d -t kube-agents-sbom-XXXXXX)"
trap 'rm -rf "${TMP_SBOM_DIR}"' EXIT

# 1. Staging filesystem SBOMs
echo "  • Generating SPDX 2.3 JSON SBOM for ${BUNDLE_PREFIX} filesystem..."
syft "dir:${TARGET_DIR}" -o spdx-json > "${TMP_SBOM_DIR}/${BUNDLE_PREFIX}.spdx.json"

echo "  • Generating CycloneDX 1.5 JSON SBOM for ${BUNDLE_PREFIX} filesystem..."
syft "dir:${TARGET_DIR}" -o cyclonedx-json > "${TMP_SBOM_DIR}/${BUNDLE_PREFIX}.cdx.json"

# 2. Staging container image SBOMs with explicit error reporting
for img in "${REQUIRED_RELEASE_IMAGES[@]}"; do
  img_ref="${REGISTRY_PREFIX}/${img}:${TAG_NAME}"
  echo "  • Generating SPDX SBOM for container image ${img_ref}..."
  err_file="${TMP_SBOM_DIR}/${img}.err"
  set +e
  syft "${img_ref}" -o spdx-json > "${TMP_SBOM_DIR}/${img}-${TAG_NAME}.spdx.json" 2>"${err_file}"
  exit_code=$?
  set -e

  if [ "${exit_code}" -ne 0 ]; then
    err_msg="$(cat "${err_file}" 2>/dev/null || echo "Unknown error")"
    if is_ci_pipeline; then
      echo "❌ ERROR: Failed to generate SBOM for container image ${img_ref} in CI (exit code ${exit_code}): ${err_msg}" >&2
      exit "${exit_code}"
    else
      echo "  ⚠️ Warning: Could not generate remote image SBOM for ${img_ref} locally (exit code ${exit_code}): ${err_msg}" >&2
      rm -f "${TMP_SBOM_DIR}/${img}-${TAG_NAME}.spdx.json"
    fi
  fi
done

# 3. All-or-nothing publication to DIST_DIR (atomic and idempotent promotion)
mkdir -p "${DIST_DIR}"
find "${TMP_SBOM_DIR}" -maxdepth 1 -name "*.json" -size +0c | while read -r staged_file; do
  mv -f "${staged_file}" "${DIST_DIR}/"
done

echo "✅ Generated SPDX & CycloneDX SBOM artifacts in ${DIST_DIR}:"
find "${DIST_DIR}" -maxdepth 1 -name "*.json" | sort | while read -r fname; do
  echo "  • $(basename "$fname")"
done
