#!/usr/bin/env bash
# Decides whether an unattended scheduled run should publish a GA release.
#
# The GA pipeline itself is unchanged by this script: it still calculates a
# version from Conventional Commits and still refuses a commit with no
# rc_*_validated tag. This only answers the question a human used to answer by
# choosing when to click "Run workflow" — is tonight the night, and is this
# candidate one we are willing to ship without anybody watching?
#
# Four things have to hold. Any one of them failing is a skip, not an error:
# nothing is published, the run stays green, and the next night asks again.
#
#   1. A candidate has passed the staging gate. staging-promote.yml pushes a
#      staging/ tag only when the full nightly E2E matrix passes against that
#      exact commit, so the tag is the evidence. No tag, no release.
#   2. There is something to release since the last GA tag.
#   3. A release is due. The cadence is anchored to a weekday rather than to
#      the age of the last release, so a week that releases late on Sunday does
#      not drag every later release to Sunday.
#   4. Nothing in the range is a breaking change. Those are a human's call —
#      see the RELEASE_ANCHOR_DOW comment below for why this is the check that
#      does the work rather than "is it a MAJOR bump".
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/release/common.sh
source "${SCRIPT_DIR}/common.sh"

# Both injectable so the tests can place "now" on a chosen weekday without
# waiting for one. Default day-of-week 5 is Friday; see the workflow comment in
# .github/workflows/release-publish.yml for why that day.
NOW_EPOCH="${NOW_EPOCH:-$(date -u +%s)}"
RELEASE_ANCHOR_DOW="${RELEASE_ANCHOR_DOW:-5}"

SHOULD_RELEASE="false"
SKIP_REASON=""
RELEASE_COMMIT=""
STAGING_TAG=""
LATEST_GA_TAG=""
# Set only by the breaking-change branch, which is the one skip that does not
# clear itself on a later night. See emit_and_exit.
HALTED_FOR_HUMAN=""

emit_and_exit() {
  if [ -n "${GITHUB_OUTPUT:-}" ]; then
    {
      echo "should_release=${SHOULD_RELEASE}"
      echo "release_commit=${RELEASE_COMMIT}"
      echo "staging_tag=${STAGING_TAG}"
      echo "skip_reason=${SKIP_REASON}"
    } >> "${GITHUB_OUTPUT}"
  fi

  echo "======================================================================"
  if [ "${SHOULD_RELEASE}" = "true" ]; then
    echo "🚀 RELEASING"
    echo "Staging-gated commit:  ${RELEASE_COMMIT}"
    echo "From staging tag:      ${STAGING_TAG}"
    echo "Previous GA tag:       ${LATEST_GA_TAG:-<none>}"
  else
    echo "⏭️  NOT RELEASING TONIGHT"
    echo "Reason:                ${SKIP_REASON}"
    echo "Newest staging tag:    ${STAGING_TAG:-<none>}"
    echo "Latest GA tag:         ${LATEST_GA_TAG:-<none>}"
  fi
  echo "======================================================================"

  # A halt is not like the other skips: it persists until somebody acts, so
  # every following night takes the same branch and GA releases stop. Raise it
  # as a workflow annotation rather than only a summary line — an annotation
  # surfaces on the run and in the Actions list without opening the job.
  #
  # This is still weaker than the situation deserves. A green scheduled run
  # notifies nobody at all, so noticing means looking. Closing that needs an
  # out-of-band signal this repository does not have yet for scheduled work;
  # `scripts/release/README.md` records it as the open gap it is.
  if [ "${SHOULD_RELEASE}" != "true" ] && [ -n "${HALTED_FOR_HUMAN:-}" ]; then
    echo "::warning title=GA release halted::${SKIP_REASON}"
  fi

  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    {
      if [ "${SHOULD_RELEASE}" = "true" ]; then
        echo "### Releasing \`${RELEASE_COMMIT:0:7}\`"
        echo ""
        echo "Staging gate passed as \`${STAGING_TAG}\`."
      else
        echo "### No release tonight"
        echo ""
        echo "${SKIP_REASON}"
      fi
    } >> "${GITHUB_STEP_SUMMARY}"
  fi

  exit 0
}

# Tags are the entire input to this decision, and a checkout that fetched none
# answers "nothing to release" for a repository full of candidates.
if is_ci_pipeline; then
  git fetch "https://github.com/$(get_target_repo).git" --tags >/dev/null 2>&1 ||
    git fetch origin --tags >/dev/null 2>&1 || true
fi

LATEST_GA_TAG="$(get_latest_ga_tag)"

# 1. Has anything passed the staging gate?
STAGING_TAG="$(get_latest_staging_tag)"
if [ -z "${STAGING_TAG}" ]; then
  SKIP_REASON="No candidate has passed the staging gate yet — nothing carries a '${STAGING_TAG_PREFIX}/' tag."
  emit_and_exit
fi

if ! RELEASE_COMMIT="$(git rev-parse --verify "${STAGING_TAG}^{commit}" 2>/dev/null)"; then
  echo "❌ ERROR: Staging tag '${STAGING_TAG}' does not resolve to a commit." >&2
  exit 1
fi

# Belt and braces over get_latest_staging_tag's shape filter. verify_release_
# eligibility.sh refuses a commit carrying no rc_*_validated tag by exiting 1,
# which on an unattended run is a red night rather than the clean skip
# everything else here produces. Checking the same condition first keeps that
# exit unreachable from the schedule: anything that got a promotion-shaped tag
# without going through the pipeline stops here quietly instead.
if ! is_commit_already_validated "${RELEASE_COMMIT}"; then
  SKIP_REASON="Commit ${RELEASE_COMMIT:0:7} carries '${STAGING_TAG}' but no *_validated tag, so it did not reach staging through the promotion pipeline."
  emit_and_exit
fi

# 2. Is there anything new in it?
if [ -n "${LATEST_GA_TAG}" ]; then
  RANGE="${LATEST_GA_TAG}..${RELEASE_COMMIT}"
  if ! COMMITS_SUBJECTS="$(git log "${RANGE}" --format="%s" 2>&1)"; then
    echo "❌ ERROR: Failed to read commit log for range '${RANGE}': ${COMMITS_SUBJECTS}" >&2
    exit 1
  fi
  COMMITS_BODIES="$(git log "${RANGE}" --format="%b" 2>/dev/null || echo "")"

  if [ -z "${COMMITS_SUBJECTS}" ]; then
    # Covers the ordinary quiet week and the awkward case where an emergency
    # release put the GA tag ahead of the newest staging-gated commit. Both are
    # "there is nothing here to ship", and neither is worth a red run.
    SKIP_REASON="No commits between ${LATEST_GA_TAG} and the staging-gated commit ${RELEASE_COMMIT:0:7}."
    emit_and_exit
  fi
else
  COMMITS_SUBJECTS="$(git log "${RELEASE_COMMIT}" --format="%s" 2>/dev/null || echo "")"
  COMMITS_BODIES="$(git log "${RELEASE_COMMIT}" --format="%b" 2>/dev/null || echo "")"
fi

# 3. Is a release due this cycle?
#
# Anchored to the most recent RELEASE_ANCHOR_DOW at 00:00 UTC rather than to
# "last release + 7 days". Both give a weekly rhythm while everything is green;
# they differ after a blocked week, when the age-based version releases late and
# then keeps releasing late for good. Arithmetic is on the epoch rather than
# through `date -d`, whose spelling differs between GNU and BSD.
#
# Epoch day 0 was a Thursday, so day % 7 == 1 is a Friday.
DAYS=$((NOW_EPOCH / 86400))
TARGET_OFFSET=$(((RELEASE_ANCHOR_DOW + 3) % 7))
DAYS_SINCE_ANCHOR=$((((DAYS - TARGET_OFFSET) % 7 + 7) % 7))
ANCHOR_EPOCH=$(((DAYS - DAYS_SINCE_ANCHOR) * 86400))

if [ -n "${LATEST_GA_TAG}" ]; then
  GA_TAG_EPOCH="$(git for-each-ref --format='%(creatordate:unix)' "refs/tags/${LATEST_GA_TAG}" 2>/dev/null || echo "")"
  if [ -n "${GA_TAG_EPOCH}" ] && [ "${GA_TAG_EPOCH}" -ge "${ANCHOR_EPOCH}" ]; then
    SKIP_REASON="${LATEST_GA_TAG} was already released this cycle; the next one is due after the coming anchor day."
    emit_and_exit
  fi
fi

# 4. Is any of it a breaking change?
#
# This is the human gate, and it is spelled as "breaking" rather than "MAJOR"
# deliberately. calculate_next_version.sh implements SemVer clause 4, so while
# the repository is on 0.y.z a breaking change bumps MINOR and the MAJOR digit
# never moves — a guard written against MAJOR would pass every breaking release
# straight through until 1.0.0. The same regexes as calculate_next_version.sh
# step 6, so the two agree on what "breaking" means.
if echo "${COMMITS_SUBJECTS}" | grep -qE "^[a-z]+(\([^)]+\))?!:" ||
  echo "${COMMITS_BODIES}" | grep -qE "^[[:space:]]*BREAKING[ -]CHANGE:[[:space:]]+"; then
  HALTED_FOR_HUMAN="true"
  SKIP_REASON="A breaking change is waiting to ship. Releases carrying one are published by a human: run release-publish.yml manually against ${RELEASE_COMMIT:0:7}."
  emit_and_exit
fi

SHOULD_RELEASE="true"
emit_and_exit
