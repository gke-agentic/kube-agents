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
# Three conditions:
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
# What this is actually buying, since the publishing path is less fragile than it
# looks. An ordinary quiet week already ends green without any of this:
# calculate_next_version.sh exits 0 with has_changes=false, and
# verify_release_eligibility.sh recognises the GA tag as the stamped child of the
# candidate and takes its idempotent-skip branch. Three narrower things are left,
# and they are the reason this exists:
#
#   - The halt. Nothing in the publishing path stops for a breaking change.
#   - Two shapes that do go red on a run with nothing to ship. No rc_*_validated
#     tag anywhere trips the exit 1 in verify_release_eligibility.sh; and a GA tag
#     sitting on a commit that is not this candidate's stamped child — what an
#     emergency release leaves behind — trips its "tag already exists on a
#     different commit" collision. Condition 2 covers the second.
#   - Deciding in one place, on the tag graph, rather than depending on a
#     `gh release view` call and a commit-shape heuristic several scripts deep.
#
# A red run should mean the machinery is broken, not that this week had nothing
# to ship, or nobody reads the red ones.
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
#
# With no GA tag there is no range and nothing to check: the repository has never
# released, so everything reachable is new. Both remaining conditions are skipped
# rather than evaluated against all of history — see condition 3 for why that
# matters — which is also what calculate_next_version.sh does in this state,
# publishing DEFAULT_INITIAL_VERSION without scanning.
if [ -z "${LATEST_GA_TAG}" ]; then
  SHOULD_RELEASE="true"
  emit_and_exit
fi

RANGE="${LATEST_GA_TAG}..${RELEASE_COMMIT}"
if ! COMMITS_SUBJECTS="$(git log "${RANGE}" --format="%s" 2>&1)"; then
  echo "❌ ERROR: Failed to read commit log for range '${RANGE}': ${COMMITS_SUBJECTS}" >&2
  exit 1
fi
COMMITS_BODIES="$(git log "${RANGE}" --format="%b" 2>/dev/null || echo "")"

if [ -z "${COMMITS_SUBJECTS}" ]; then
  # Two shapes reach here and both are "nothing to ship". The ordinary quiet week
  # would be handled without this — verify_release_eligibility.sh recognises the
  # GA tag as the stamped child of this candidate and skips green on its own — so
  # what this saves there is a wasted checkout, version calculation and four
  # registry inspections rather than a red run.
  #
  # The other shape is the one that goes red today: an emergency release put the
  # GA tag on a commit that is not this candidate's stamped child, so the
  # eligibility check reports "tag already exists on a different commit" and exits
  # 1. On a schedule that is a red run with nothing wrong.
  SKIP_REASON="No commits between ${LATEST_GA_TAG} and the gate-passing commit ${RELEASE_COMMIT:0:7}."
  emit_and_exit
fi

# ── 3. Is any of it a breaking change? ───────────────────────────────────────
#
# Spelled as "breaking" rather than "MAJOR" deliberately. calculate_next_version.sh
# implements SemVer clause 4, so while the repository is on 0.y.z a breaking
# change bumps MINOR and the MAJOR digit never moves — a guard written against
# MAJOR would pass every breaking release straight through until 1.0.0.
#
# The definition is common.sh's, shared with calculate_next_version.sh, because a
# second copy here is how the bump and the halt come to disagree about what
# "breaking" means — and the direction that fails silently is the gate waving one
# through into an unattended release.
#
# Reached only with a GA tag in hand, which is what keeps this bounded. Against
# all of history it would match some long-shipped `feat!:` and then never stop
# matching it, since there is no range to shrink: one permanent halt, every run.
if commit_messages_have_breaking_change "${COMMITS_SUBJECTS}" "${COMMITS_BODIES}"; then
  HALTED_FOR_HUMAN="true"
  SKIP_REASON="A breaking change is waiting to ship. Releases carrying one are published by a human: run release-publish.yml manually against ${RELEASE_COMMIT:0:7}."
  emit_and_exit
fi

SHOULD_RELEASE="true"
emit_and_exit
