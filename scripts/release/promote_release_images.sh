#!/usr/bin/env bash
# Promotes verified container images from candidate commit SHA to GA release tag in GHCR without rebuilding.
# Releases strictly use pure numeric SemVer without 'v' prefix (e.g. 0.1.0, 0.2.0).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/release/common.sh
source "${SCRIPT_DIR}/common.sh"

RELEASE_COMMIT="${1:-${RELEASE_COMMIT:-${COMMIT_SHA:-${TARGET_COMMIT:-}}}}"
RELEASE_VERSION="${2:-${RELEASE_VERSION:-${TARGET_VERSION:-${TARGET_TAG:-}}}}"

if [ -z "${RELEASE_COMMIT}" ] || [ -z "${RELEASE_VERSION}" ]; then
  echo "❌ ERROR: RELEASE_COMMIT and RELEASE_VERSION are required arguments." >&2
  echo "Usage: $0 <RELEASE_COMMIT> <RELEASE_VERSION>" >&2
  exit 1
fi

validate_pure_numeric_semver "${RELEASE_VERSION}" "Release version" || exit 1

echo "======================================================================"
echo "🚀 PROMOTING RELEASE CONTAINER IMAGES (NO-REBUILD)"
echo "Release Commit:  ${RELEASE_COMMIT}"
echo "Release Version: ${RELEASE_VERSION}"
echo "======================================================================"

promote_release_images "${RELEASE_COMMIT}" "${RELEASE_VERSION}"
