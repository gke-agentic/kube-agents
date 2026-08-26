#!/usr/bin/env bash
# Resolves the validated release candidate that the staging promotion gate should test.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

RC_TAG="${1:-${RC_TAG:-}}"

SKIP_PROMOTION="false"

# Tags are the whole input to this script, and a checkout that fetched none
# answers "no validated candidate exists" for a repository full of them. The
# sibling copies of this in ensure_git_tag and resolve_rc_tag.sh guard
# get_target_repo against being empty; it ends in `echo "$DEFAULT_RELEASE_REPO"`
# and cannot be.
if is_ci_pipeline; then
  git fetch "https://github.com/$(get_target_repo).git" --tags >/dev/null 2>&1 ||
    git fetch origin --tags >/dev/null 2>&1 || true
fi

if [ -z "${RC_TAG}" ]; then
  RC_TAG="$(get_latest_validated_rc_tag)"
fi

if [ -z "${RC_TAG}" ]; then
  echo "❌ ERROR: No validated release candidate tag (rc_*_validated) found. Nothing to promote." >&2
  exit 1
fi

if ! COMMIT_SHA=$(git rev-parse --verify "${RC_TAG}^{commit}" 2>/dev/null); then
  echo "❌ ERROR: Cannot resolve valid Git commit SHA from release candidate tag '${RC_TAG}'!" >&2
  exit 1
fi

# A manually supplied tag is the one input that can name an unvalidated candidate.
# Promotion is a validated-only gate, so check the commit rather than the spelling
# of the tag: an rc_* tag and its rc_*_validated sibling point at the same commit.
if ! is_commit_already_validated "${COMMIT_SHA}"; then
  echo "❌ ERROR: Commit ${COMMIT_SHA} (from '${RC_TAG}') carries no *_validated tag. Only validated candidates are promoted." >&2
  exit 1
fi

STAGING_TAG="$(staging_tag_for_rc "${RC_TAG}")"

existing_staging_tag="$(get_existing_staging_tag "${COMMIT_SHA}")"
if [ -n "${existing_staging_tag}" ]; then
  echo "ℹ️ Commit ${COMMIT_SHA:0:7} is already promoted to staging as '${existing_staging_tag}'. Skipping redundant promotion run." >&2
  SKIP_PROMOTION="true"
fi

if [ -n "${GITHUB_OUTPUT:-}" ]; then
  {
    echo "commit_sha=${COMMIT_SHA}"
    echo "rc_tag=${RC_TAG}"
    echo "staging_tag=${STAGING_TAG}"
    echo "skip_promotion=${SKIP_PROMOTION}"
  } >> "$GITHUB_OUTPUT"
fi

echo "======================================================================"
echo "🎯 RESOLVED STAGING PROMOTION CANDIDATE"
echo "Validated RC Tag:          ${RC_TAG}"
echo "Target Commit SHA:         ${COMMIT_SHA}"
echo "Staging Promotion Tag:     ${STAGING_TAG}"
echo "Skip (Already Promoted):   ${SKIP_PROMOTION}"
echo "======================================================================"
