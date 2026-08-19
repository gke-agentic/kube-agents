#!/usr/bin/env bash
# Creates and pushes an official GA SemVer Git tag for a target commit SHA safely and idempotently.
# Releases strictly use pure numeric SemVer without 'v' prefix (e.g. 0.1.0, 0.2.0).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/release/common.sh
source "${SCRIPT_DIR}/common.sh"

RELEASE_VERSION="${1:-${RELEASE_VERSION:-${TARGET_VERSION:-${TARGET_TAG:-}}}}"
RELEASE_COMMIT="${2:-${RELEASE_COMMIT:-${TARGET_COMMIT:-}}}"

if [ -z "${RELEASE_VERSION}" ] || [ -z "${RELEASE_COMMIT}" ]; then
  echo "❌ ERROR: RELEASE_VERSION and RELEASE_COMMIT are required arguments." >&2
  echo "Usage: $0 <RELEASE_VERSION> <RELEASE_COMMIT>" >&2
  exit 1
fi

validate_pure_numeric_semver "${RELEASE_VERSION}" "Release version" || exit 1

echo "======================================================================"
echo "🏷️ CREATING AND PUSHING GA RELEASE GIT TAG"
echo "Release Version: ${RELEASE_VERSION}"
echo "Release Commit:  ${RELEASE_COMMIT}"
echo "======================================================================"

ensure_git_tag "${RELEASE_VERSION}" "${RELEASE_COMMIT}" "Release ${RELEASE_VERSION}"
