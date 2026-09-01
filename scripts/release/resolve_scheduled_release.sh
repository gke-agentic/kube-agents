#!/usr/bin/env bash
# Decides whether an unattended run of release-publish.yml should publish a GA
# release.
#
# The publishing pipeline is unchanged by this script. It still calculates a
# version from Conventional Commits and still refuses a commit with no
# rc_*_validated tag. This answers only the question a human used to answer by
# choosing when to click "Run workflow": is this candidate one we are willing to
# ship with nobody watching?
#
# Three conditions, and each is checked here rather than left to the publishing
# steps because of how those steps fail. verify_release_eligibility.sh exits 1
# when its gate is not satisfied, and calculate_next_version.sh has nothing to
# compute when no commits have landed since the last GA tag. Triggered by hand
# those are correct, visible errors — a human asked, and got an answer. On a
# schedule they are a red run every week that happened to have nothing to ship,
# and a workflow that is red most weeks is one nobody reads.
#
#   1. A candidate has passed the gate — the newest rc_*_validated tag. Skip.
#   2. There is something to release: commits exist between the newest GA tag
#      and that candidate's commit. Skip.
#   3. Nothing in the range is a breaking change. HALT.
#
# Conditions 1 and 2 are green skips: nothing is published, the run stays green,
# and the next run asks again. Condition 3 is not, and the difference matters. A
# breaking change does not clear itself — every following run takes the same
# branch and GA releases stop until somebody publishes by hand — so it fails the
# job. A skip means "nothing to do this week"; red means "something needs you".
#
# There is deliberately no weekday or elapsed-time check in here. The cron is
# the cadence, so no wall-clock arithmetic exists anywhere in the decision, and
# "has this candidate already been released?" needs no condition of its own:
# if the newest GA tag points at the gated commit, condition 2's range is empty
# and the skip already covers it. The state lives in the tags.
#
# Gate selection is the one part expected to move. Today it reads the
# rc_*_validated family; once the nightly pipeline is producing staging_<ts>_<sha>
# tags reliably it reads those instead, and nothing else in this file changes.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/release/common.sh
source "${SCRIPT_DIR}/common.sh"

SHOULD_RELEASE="false"
SKIP_REASON=""
RELEASE_COMMIT=""
GATE_TAG=""
LATEST_GA_TAG=""

# Set only by condition 3. See the halt handling in emit_and_exit.
HALTED_FOR_HUMAN=""

emit_and_exit() {
  if [ -n "${GITHUB_OUTPUT:-}" ]; then
    {
      echo "should_release=${SHOULD_RELEASE}"
      echo "release_commit=${RELEASE_COMMIT}"
      echo "gate_tag=${GATE_TAG}"
      echo "skip_reason=${SKIP_REASON}"
    } >> "${GITHUB_OUTPUT}"
  fi

  echo "======================================================================"
  if [ "${SHOULD_RELEASE}" = "true" ]; then
    echo "🚀 RELEASING"
    echo "Gate-passing commit:   ${RELEASE_COMMIT}"
    echo "From gate tag:         ${GATE_TAG}"
    echo "Previous GA tag:       ${LATEST_GA_TAG:-<none>}"
  elif [ -n "${HALTED_FOR_HUMAN}" ]; then
    echo "🛑 HALTED — A HUMAN HAS TO PUBLISH THIS ONE"
    echo "Reason:                ${SKIP_REASON}"
    echo "Gate-passing commit:   ${RELEASE_COMMIT:-<none>}"
    echo "Latest GA tag:         ${LATEST_GA_TAG:-<none>}"
  else
    echo "⏭️  NOT RELEASING"
    echo "Reason:                ${SKIP_REASON}"
    echo "Newest gate tag:       ${GATE_TAG:-<none>}"
    echo "Latest GA tag:         ${LATEST_GA_TAG:-<none>}"
  fi
  echo "======================================================================"

  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    {
      if [ "${SHOULD_RELEASE}" = "true" ]; then
        echo "### Releasing \`${RELEASE_COMMIT:0:7}\`"
        echo ""
        echo "Gate passed as \`${GATE_TAG}\`."
      elif [ -n "${HALTED_FOR_HUMAN}" ]; then
        echo "### Release halted"
        echo ""
        echo "${SKIP_REASON}"
      else
        echo "### No release this run"
        echo ""
        echo "${SKIP_REASON}"
      fi
    } >> "${GITHUB_STEP_SUMMARY}"
  fi

  # A halt is not like the other two outcomes: it persists until somebody acts.
  # Failing the job is what makes it visible — a green run notifies nobody, so a
  # halt reported as a skip would stop GA releases silently for as long as the
  # breaking change sits unshipped.
  if [ -n "${HALTED_FOR_HUMAN}" ]; then
    echo "::error title=GA release halted::${SKIP_REASON}"
    exit 1
  fi

  exit 0
}

# Tags are the entire input to this decision, and a checkout that fetched none
# answers "nothing to release" for a repository full of candidates. No-op
# outside CI, where the local tag graph is already whatever the caller intended.
release_fetch_tags

LATEST_GA_TAG="$(get_latest_ga_tag)"

# ── 1. Has anything passed the gate? ─────────────────────────────────────────
#
# Reusing common.sh's lookup rather than re-implementing it: a second answer to
# "which candidate is validated" is how this gate and verify_release_eligibility.sh
# drift apart, and they have to agree or the resolver waves through a commit the
# publish job then refuses with exit 1.
GATE_TAG="$(get_latest_validated_rc_tag)"
if [ -z "${GATE_TAG}" ]; then
  SKIP_REASON="No candidate has passed the gate — no 'rc_*_validated' tag exists."
  emit_and_exit
fi

if ! RELEASE_COMMIT="$(git rev-parse --verify "${GATE_TAG}^{commit}" 2>/dev/null)"; then
  echo "❌ ERROR: Gate tag '${GATE_TAG}' does not resolve to a commit." >&2
  exit 1
fi

# ── 2. Is there anything new in it? ──────────────────────────────────────────
if [ -n "${LATEST_GA_TAG}" ]; then
  RANGE="${LATEST_GA_TAG}..${RELEASE_COMMIT}"
  if ! COMMITS_SUBJECTS="$(git log "${RANGE}" --format="%s" 2>&1)"; then
    echo "❌ ERROR: Failed to read commit log for range '${RANGE}': ${COMMITS_SUBJECTS}" >&2
    exit 1
  fi
  COMMITS_BODIES="$(git log "${RANGE}" --format="%b" 2>/dev/null || echo "")"

  if [ -z "${COMMITS_SUBJECTS}" ]; then
    # Two shapes reach here and both are "nothing to ship": the ordinary quiet
    # week, and the case where an emergency release already put the GA tag on or
    # ahead of the gated commit. Neither is worth a red run.
    SKIP_REASON="No commits between ${LATEST_GA_TAG} and the gate-passing commit ${RELEASE_COMMIT:0:7}."
    emit_and_exit
  fi
else
  COMMITS_SUBJECTS="$(git log "${RELEASE_COMMIT}" --format="%s" 2>/dev/null || echo "")"
  COMMITS_BODIES="$(git log "${RELEASE_COMMIT}" --format="%b" 2>/dev/null || echo "")"
fi

# ── 3. Is any of it a breaking change? ───────────────────────────────────────
#
# Spelled as "breaking" rather than "MAJOR" deliberately. calculate_next_version.sh
# implements SemVer clause 4, so while the repository is on 0.y.z a breaking
# change bumps MINOR and the MAJOR digit never moves — a guard written against
# MAJOR would pass every breaking release straight through until 1.0.0. The
# regexes are the ones calculate_next_version.sh step 6 uses, so the two agree on
# what "breaking" means.
if echo "${COMMITS_SUBJECTS}" | grep -qE "^[a-z]+(\([^)]+\))?!:" ||
  echo "${COMMITS_BODIES}" | grep -qE "^[[:space:]]*BREAKING[ -]CHANGE:[[:space:]]+"; then
  HALTED_FOR_HUMAN="true"
  SKIP_REASON="A breaking change is waiting to ship. Releases carrying one are published by a human: run release-publish.yml manually against ${RELEASE_COMMIT:0:7}."
  emit_and_exit
fi

SHOULD_RELEASE="true"
emit_and_exit
