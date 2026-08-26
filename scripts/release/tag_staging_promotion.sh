#!/usr/bin/env bash
# Promotes a validated Release Candidate commit to staging by pushing its staging/** tag.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

COMMIT_SHA="${1:-${COMMIT_SHA:-}}"
STAGING_TAG="${2:-${STAGING_TAG:-}}"
RC_TAG="${3:-${RC_TAG:-}}"

if [ -z "${COMMIT_SHA}" ] || [ -z "${STAGING_TAG}" ]; then
  echo "❌ ERROR: COMMIT_SHA and STAGING_TAG are required." >&2
  exit 1
fi

if [[ "${STAGING_TAG}" != "${STAGING_TAG_PREFIX}/"* ]]; then
  echo "❌ ERROR: Staging tag '${STAGING_TAG}' must live under '${STAGING_TAG_PREFIX}/'. The staging-redeploy-*.yml workflows trigger on nothing else." >&2
  exit 1
fi

echo "======================================================================"
echo "🚀 PROMOTING VALIDATED RELEASE CANDIDATE TO STAGING"
echo "Commit SHA:    ${COMMIT_SHA}"
echo "Validated Tag: ${RC_TAG:-(not specified)}"
echo "Staging Tag:   ${STAGING_TAG}"
echo "======================================================================"

# The push is the deploy trigger: staging-redeploy-{agent,controller,integrations}
# all start on `push: tags: staging/**`. A tag pushed with the default GITHUB_TOKEN
# starts no workflow at all, so the calling job must check out with a PAT
# (RELEASE_BOT_TOKEN) or this succeeds and staging never moves.
ensure_git_tag "${STAGING_TAG}" "${COMMIT_SHA}" "Promoted validated RC ${RC_TAG:-${COMMIT_SHA}} to staging"
