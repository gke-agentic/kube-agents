#!/usr/bin/env bash
# Calculates the next semantic version (X.Y.Z) based on Conventional Commits since the last GA release tag.
# Correctly implements SemVer 2.0 clause 4: in 0.y.z initial development, Breaking Changes bump MINOR (0.1.0 -> 0.2.0).
# Releases strictly use pure numeric SemVer without 'v' prefix (e.g. 0.1.0, 0.2.0).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/release/common.sh
source "${SCRIPT_DIR}/common.sh"

EXPLICIT_RELEASE_VERSION="${EXPLICIT_RELEASE_VERSION:-}"
BASE_TAG_PARAM="${1:-}"
TARGET_REF_PARAM="${2:-HEAD}"

# 0. Protection against Shallow Checkout in CI
if [ "$(git rev-parse --is-shallow-repository 2>/dev/null || echo "false")" = "true" ]; then
  echo "ℹ️ Shallow repository detected. Unshallowing git history for version calculation..." >&2
  git fetch --unshallow --tags >/dev/null 2>&1 || git fetch --depth=100 --tags >/dev/null 2>&1 || true
fi

# 1. Resolve baseline GA SemVer tag (pure numeric X.Y.Z, excluding rc_* tags)
if [ -n "${BASE_TAG_PARAM}" ]; then
  validate_pure_numeric_semver "${BASE_TAG_PARAM}" "Base tag" || exit 1
  if ! git rev-parse --verify "${BASE_TAG_PARAM}^{commit}" >/dev/null 2>&1; then
    echo "❌ ERROR: Base tag '${BASE_TAG_PARAM}' does not exist in git repository!" >&2
    exit 1
  fi
  LATEST_GA_TAG="${BASE_TAG_PARAM}"
else
  LATEST_GA_TAG="$(get_latest_ga_tag)"
fi

# Validate target ref exists in git repository
if ! git rev-parse --verify "${TARGET_REF_PARAM}^{commit}" >/dev/null 2>&1; then
  echo "❌ ERROR: Target ref '${TARGET_REF_PARAM}' does not exist in git repository!" >&2
  exit 1
fi

# 2. Handle explicit version override (EXPLICIT_RELEASE_VERSION) with downgrade and collision protection
if [ -n "${EXPLICIT_RELEASE_VERSION}" ]; then
  # 2.1 Validate SemVer 2.0 format
  validate_pure_numeric_semver "${EXPLICIT_RELEASE_VERSION}" "Explicit release version" || exit 1

  # 2.2 Protect against version downgrade
  if [ -n "${LATEST_GA_TAG}" ]; then
    CMP_RES="$(compare_semver "${EXPLICIT_RELEASE_VERSION}" "${LATEST_GA_TAG}")"
    if [ "${CMP_RES}" = "-1" ]; then
      echo "❌ ERROR: Explicit release version '${EXPLICIT_RELEASE_VERSION}' is lower than latest GA release '${LATEST_GA_TAG}'. Version downgrade is prohibited." >&2
      exit 1
    fi
  fi

  # 2.3 Protect against tag collisions on different commits
  if TAG_COMMIT="$(git rev-parse --verify "${EXPLICIT_RELEASE_VERSION}^{commit}" 2>/dev/null)"; then
    REQ_COMMIT="$(git rev-parse --verify "${TARGET_REF_PARAM}^{commit}" 2>/dev/null || echo "")"
    if [ -n "${REQ_COMMIT}" ] && [ "${TAG_COMMIT}" != "${REQ_COMMIT}" ]; then
      echo "❌ ERROR: Tag '${EXPLICIT_RELEASE_VERSION}' already exists in git repository on a different commit (${TAG_COMMIT:0:7}). Cannot re-assign existing release tag." >&2
      exit 1
    fi
  fi

  echo "ℹ️ Using verified explicit release version: ${EXPLICIT_RELEASE_VERSION}" >&2
  if [ -n "${GITHUB_OUTPUT:-}" ]; then
    echo "version=${EXPLICIT_RELEASE_VERSION}" >> "${GITHUB_OUTPUT}"
    echo "previous_version=${LATEST_GA_TAG}" >> "${GITHUB_OUTPUT}"
    echo "has_changes=true" >> "${GITHUB_OUTPUT}"
    echo "bump_type=manual" >> "${GITHUB_OUTPUT}"
  fi
  echo "${EXPLICIT_RELEASE_VERSION}"
  exit 0
fi

# 3. Handle baseline repository initialization when no prior tags exist
if [ -z "${LATEST_GA_TAG}" ]; then
  echo "ℹ️ No previous GA SemVer tag found. Initializing repository at baseline ${DEFAULT_INITIAL_VERSION}." >&2
  NEXT_VERSION="${DEFAULT_INITIAL_VERSION}"
  if [ -n "${GITHUB_OUTPUT:-}" ]; then
    echo "version=${NEXT_VERSION}" >> "${GITHUB_OUTPUT}"
    echo "previous_version=" >> "${GITHUB_OUTPUT}"
    echo "has_changes=true" >> "${GITHUB_OUTPUT}"
    echo "bump_type=initial" >> "${GITHUB_OUTPUT}"
  fi
  echo "${NEXT_VERSION}"
  exit 0
fi

echo "📌 Latest GA Tag: ${LATEST_GA_TAG}" >&2
IFS='.' read -r MAJOR MINOR PATCH <<< "${LATEST_GA_TAG}"

# 4. Inspect commit range for subjects (%s) and bodies (%b)
COMMITS_RANGE="${LATEST_GA_TAG}..${TARGET_REF_PARAM}"
if ! COMMITS_SUBJECTS="$(git log "${COMMITS_RANGE}" --format="%s" 2>&1)"; then
  echo "❌ ERROR: Failed to read commit log for range '${COMMITS_RANGE}': ${COMMITS_SUBJECTS}" >&2
  exit 1
fi
COMMITS_BODIES="$(git log "${COMMITS_RANGE}" --format="%b" 2>/dev/null || echo "")"

if [ -z "${COMMITS_SUBJECTS}" ]; then
  echo "ℹ️ No new commits since ${LATEST_GA_TAG}. Keeping current version." >&2
  if [ -n "${GITHUB_OUTPUT:-}" ]; then
    echo "version=${LATEST_GA_TAG}" >> "${GITHUB_OUTPUT}"
    echo "previous_version=${LATEST_GA_TAG}" >> "${GITHUB_OUTPUT}"
    echo "has_changes=false" >> "${GITHUB_OUTPUT}"
    echo "bump_type=none" >> "${GITHUB_OUTPUT}"
  fi
  echo "${LATEST_GA_TAG}"
  exit 0
fi

# 5. Analyze commits according to SemVer 2.0 and Conventional Commits rules
BUMP_TYPE="patch"
HAS_BREAKING="false"

# Check for Breaking Changes in subject (feat!:, fix!:) or footer (BREAKING CHANGE: / BREAKING-CHANGE:)
if echo "${COMMITS_SUBJECTS}" | grep -qE "^[a-z]+(\([^)]+\))?!:" || \
   echo "${COMMITS_BODIES}" | grep -qE "^[[:space:]]*BREAKING[ -]CHANGE:[[:space:]]+"; then
  HAS_BREAKING="true"
fi

if [ "${HAS_BREAKING}" = "true" ]; then
  # SemVer 2.0 Clause 4: in 0.y.z initial development, breaking changes bump MINOR (0.1.0 -> 0.2.0)
  if [ "$MAJOR" -eq 0 ]; then
    BUMP_TYPE="minor-breaking"
    MINOR=$((MINOR + 1))
    PATCH=0
  else
    BUMP_TYPE="major"
    MAJOR=$((MAJOR + 1))
    MINOR=0
    PATCH=0
  fi
# New features bump MINOR and reset PATCH
elif echo "${COMMITS_SUBJECTS}" | grep -qE "^feat(\([^)]+\))?:"; then
  BUMP_TYPE="minor"
  MINOR=$((MINOR + 1))
  PATCH=0
else
  BUMP_TYPE="patch"
  PATCH=$((PATCH + 1))
fi

NEXT_VERSION="${MAJOR}.${MINOR}.${PATCH}"

echo "📈 Calculated next version: ${NEXT_VERSION} (bump: ${BUMP_TYPE})" >&2

if [ -n "${GITHUB_OUTPUT:-}" ]; then
  echo "version=${NEXT_VERSION}" >> "${GITHUB_OUTPUT}"
  echo "previous_version=${LATEST_GA_TAG}" >> "${GITHUB_OUTPUT}"
  echo "has_changes=true" >> "${GITHUB_OUTPUT}"
  echo "bump_type=${BUMP_TYPE}" >> "${GITHUB_OUTPUT}"
fi

echo "${NEXT_VERSION}"
