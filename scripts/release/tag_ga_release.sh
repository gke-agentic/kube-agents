#!/usr/bin/env bash
# Creates and pushes an official GA SemVer Git tag for a target commit SHA safely and idempotently.
# Releases strictly use pure numeric SemVer without 'v' prefix (e.g. 0.1.0, 0.2.0).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/release/common.sh
source "${SCRIPT_DIR}/common.sh"

RELEASE_VERSION="${1:-${RELEASE_VERSION:-${TARGET_VERSION:-${TARGET_TAG:-}}}}"
RC_CANDIDATE_COMMIT="${2:-${RC_CANDIDATE_COMMIT:-${TARGET_COMMIT:-}}}"

if [ -z "${RELEASE_VERSION}" ] || [ -z "${RC_CANDIDATE_COMMIT}" ]; then
  echo "❌ ERROR: RELEASE_VERSION and RC candidate commit are required as arguments or environment variables." >&2
  echo "Usage: $0 (with RELEASE_VERSION and RC candidate commit in env) or $0 <RELEASE_VERSION> <RC_CANDIDATE_COMMIT>" >&2
  exit 1
fi

validate_pure_numeric_semver "${RELEASE_VERSION}" "Release version" || exit 1

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "${SCRIPT_DIR}/../.." && pwd))"

# Canonicalize RC candidate commit SHA
RC_CANDIDATE_COMMIT_SHA="$(git -C "${REPO_ROOT}" rev-parse --verify "${RC_CANDIDATE_COMMIT}^{commit}" 2>/dev/null || echo "${RC_CANDIDATE_COMMIT}")"

echo "======================================================================"
echo "🏷️ CREATING AND PUSHING GA RELEASE GIT TAG"
echo "Release Version:     ${RELEASE_VERSION}"
echo "RC Candidate Commit: ${RC_CANDIDATE_COMMIT_SHA:0:7}"
echo "======================================================================"

RELEASE_COMMIT="$(create_stamped_release_commit "${RELEASE_VERSION}" "${RC_CANDIDATE_COMMIT_SHA}" "${REPO_ROOT}")"

if [ "${RELEASE_COMMIT}" != "${RC_CANDIDATE_COMMIT_SHA}" ]; then
  echo "Release Commit:      ${RELEASE_COMMIT:0:7}"
fi

ensure_git_tag "${RELEASE_VERSION}" "${RELEASE_COMMIT}" "Release ${RELEASE_VERSION}"
