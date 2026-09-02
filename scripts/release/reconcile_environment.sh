#!/usr/bin/env bash
# Reconciles a long-lived environment against the composition in this checkout,
# or reports what a reconcile would change.
#
# `rc` and `nightly` are destroyed and rebuilt every run, so they always run
# today's `terraform/examples/full-install`. `autopush` and `staging` are not:
# they receive image tags and nothing else, so every infrastructure change that
# lands on main — IAM, Pub/Sub, node pools, and the chart values the composition
# renders — was invisible there until somebody re-applied by hand. #1117 found
# both of them a month behind while reporting themselves as "main".
#
# This is the in-place half of the answer (#1117's Option A). The other half,
# destroying and rebuilding from nothing, is deploy-environment.yml, which the
# same issue keeps as a dispatch-only button because it takes the cluster with
# it.
#
# Usage:
#   RECONCILE_MODE=plan|apply reconcile_environment.sh
#
# Inputs, all from the environment because the calling workflow is what resolves
# `vars.*` and `secrets.*`:
#
#   RECONCILE_MODE       plan (read-only) or apply
#   GITHUB_ENVIRONMENT   the environment's name, for messages and the lease
#   IMAGE_TAG            optional. Omitted, a plan reads the tag the install is
#                        already running and an apply keeps it where it is.
#   LEASE_POLICY         defer (default) | fail | ignore — what an apply does
#                        when somebody is live-testing against this install
#   plus every install setting render_install_env.sh maps.
set -euo pipefail

MODE="${RECONCILE_MODE:-plan}"
ENV_NAME="${GITHUB_ENVIRONMENT:-unknown}"
LEASE_POLICY="${LEASE_POLICY:-defer}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

case "$MODE" in
  plan|apply) ;;
  *) echo "RECONCILE_MODE must be plan or apply, not '${MODE}'." >&2; exit 2 ;;
esac

summary() {
  [ -n "${GITHUB_STEP_SUMMARY:-}" ] || return 0
  printf '%s\n' "$*" >>"${GITHUB_STEP_SUMMARY}"
}

output() {
  [ -n "${GITHUB_OUTPUT:-}" ] || return 0
  printf '%s=%s\n' "$1" "$2" >>"${GITHUB_OUTPUT}"
}

# ---------------------------------------------------------------------------
# 1. Configuration
# ---------------------------------------------------------------------------
# At the repository root, which is where every front door looks for it and
# where scripts/live_test_lease.py discovers which install this checkout is
# pointed at. .gitignore already excludes it.
INSTALL_ENV="${REPO_ROOT}/install.env"
export KUBE_AGENTS_INSTALL_ENV="${INSTALL_ENV}"

echo "==> Rendering the install configuration for '${ENV_NAME}'."
# --strict: this environment already exists, so a setting that arrives empty
# would apply a default over a running install and plan the destruction of the
# difference. render_install_env.sh names every missing one at once and stops.
"${REPO_ROOT}/scripts/release/render_install_env.sh" "${INSTALL_ENV}" --strict

# ---------------------------------------------------------------------------
# 2. Wait out any image redeploy already in flight
# ---------------------------------------------------------------------------
# The redeploy workflows run `helm upgrade` on the `kube-agents` release, and
# the composition's helm_release.kube_agents owns that same release. Both at
# once is either a failed apply or a lost deploy, depending on which one gets
# the release lock. They are not scheduled against each other — autopush's
# redeploys start from a GHCR publish, which is every push to main — so the
# overlap is real and this waits it out rather than racing it.
#
# Bounded, and a timeout is a deferral rather than a failure: the reconcile runs
# again tomorrow, and blocking a nightly on a stuck deploy helps nobody.
await_redeploys() {
  command -v gh >/dev/null 2>&1 || { echo "==> gh not available; skipping the redeploy check."; return 0; }
  [ -n "${GH_TOKEN:-${GITHUB_TOKEN:-}}" ] || { echo "==> No token for the redeploy check; skipping."; return 0; }

  local deadline=$((SECONDS + 900)) running
  while [ "$SECONDS" -lt "$deadline" ]; do
    running=""
    for component in agent controller integrations; do
      local wf="${ENV_NAME}-redeploy-${component}.yml"
      # `gh run list` on a workflow this repository does not have exits
      # non-zero; an environment with no redeploy workflows simply has nothing
      # to wait for.
      local n
      n="$(gh run list --repo "${GITHUB_REPOSITORY:-gke-labs/kube-agents}" \
        --workflow "$wf" --status in_progress --json databaseId \
        --jq 'length' 2>/dev/null || echo 0)"
      [ "$n" = "0" ] || running="${running} ${wf}(${n})"
    done
    [ -n "$running" ] || return 0
    echo "==> Waiting for in-flight redeploys:${running}"
    sleep 30
  done

  echo "::warning title=Redeploy still running::A redeploy of '${ENV_NAME}' was still in flight after 15 minutes; skipping this reconcile rather than running a terraform apply concurrently with a helm upgrade on the same release."
  return 1
}

if [ "$MODE" = "apply" ]; then
  if ! await_redeploys; then
    summary "### Reconcile deferred — \`${ENV_NAME}\`"
    summary ""
    summary "An image redeploy was still running. Nothing was applied."
    output "result" "deferred"
    exit 0
  fi
fi

# ---------------------------------------------------------------------------
# 3. The live-test lease
# ---------------------------------------------------------------------------
# AGENTS.md requires every pull request to be validated against a running
# install, and autopush is that install for most of this repository's agents.
# An unattended `terraform apply` landing in the middle of somebody's live
# validation rewrites what they are in the process of observing, and they have
# no way to tell that is what happened -- their evidence simply stops matching
# the cluster.
#
# So the reconcile takes the same lease an agent would. `acquire` fails when
# somebody else holds it, which is the check and the claim in one step: a
# separate "is it free?" read would leave a window between the answer and the
# apply. A plan takes nothing, because it changes nothing.
LEASE_HELD="false"
release_lease() {
  [ "$LEASE_HELD" = "true" ] || return 0
  python3 "${REPO_ROOT}/scripts/live_test_lease.py" release >/dev/null 2>&1 || true
  LEASE_HELD="false"
}
trap release_lease EXIT

if [ "$MODE" = "apply" ] && [ "$LEASE_POLICY" != "ignore" ]; then
  # A run id rather than a pid: the lease keys holder identity on the session,
  # and every step of a workflow run is a different shell.
  export KUBE_AGENTS_LEASE_SESSION="gha-${GITHUB_RUN_ID:-manual}"
  if python3 "${REPO_ROOT}/scripts/live_test_lease.py" acquire \
    --note "scheduled reconcile of ${ENV_NAME}" --ttl 90; then
    LEASE_HELD="true"
  else
    holder="$(python3 "${REPO_ROOT}/scripts/live_test_lease.py" status --json 2>/dev/null || echo '[]')"
    if [ "$LEASE_POLICY" = "fail" ]; then
      echo "::error title=Live-test lease is held::Somebody is live-testing against '${ENV_NAME}'. Refusing to apply. ${holder}"
      exit 1
    fi
    echo "::warning title=Live-test lease is held::Somebody is live-testing against '${ENV_NAME}'; skipping this reconcile. It will run again on the next schedule. ${holder}"
    summary "### Reconcile deferred — \`${ENV_NAME}\`"
    summary ""
    summary "The live-test lease was held, so nothing was applied."
    summary ""
    summary '```json'
    summary "${holder}"
    summary '```'
    output "result" "deferred"
    exit 0
  fi
fi

# ---------------------------------------------------------------------------
# 4. Plan, or apply
# ---------------------------------------------------------------------------
UPGRADE_ARGS=(--non-interactive)
if [ -n "${IMAGE_TAG:-}" ]; then
  UPGRADE_ARGS+=(--image-tag="${IMAGE_TAG}")
elif [ "$MODE" = "apply" ]; then
  # An apply with no tag has to say so. Empty otherwise means "the caller's
  # IMAGE_TAG did not resolve", which upgrade.sh refuses on purpose.
  UPGRADE_ARGS+=(--keep-image-tag)
fi

# Somewhere the calling workflow can read afterwards, so the drift report can
# quote the plan rather than telling a reader to open the job log. Named as an
# output rather than assumed, because a caller running two environments in one
# job would otherwise have them overwrite each other.
PLAN_LOG="${RUNNER_TEMP:-/tmp}/reconcile-${ENV_NAME}.log"
output "plan_log" "${PLAN_LOG}"
status=0

if [ "$MODE" = "plan" ]; then
  echo "==> Planning '${ENV_NAME}' (read-only)."
  # `tee`, so the plan is in the job log and in a file at once. `|| status=` is
  # what keeps `set -e` from killing the script on the exit code this whole
  # path exists to read: with pipefail the pipeline reports upgrade.sh's 2, and
  # PIPESTATUS[0] is where the unambiguous copy of it lives (tee's own 0 is
  # PIPESTATUS[1]).
  "${REPO_ROOT}/upgrade.sh" --plan "${UPGRADE_ARGS[@]}" 2>&1 | tee "${PLAN_LOG}" \
    || status="${PIPESTATUS[0]}"

  case "$status" in
    0)
      summary "### \`${ENV_NAME}\` is in sync"
      summary ""
      summary "A full upgrade would change nothing."
      output "drift" "false"
      ;;
    2)
      summary "### \`${ENV_NAME}\` has drifted from the composition"
      summary ""
      summary "See the plan in the job log."
      output "drift" "true"
      # 2 is this script's report, not its failure. The caller decides what a
      # drifted environment means; here it means the plan succeeded.
      status=0
      ;;
    *)
      summary "### The plan for \`${ENV_NAME}\` failed"
      output "drift" "unknown"
      ;;
  esac
  output "result" "planned"
else
  echo "==> Reconciling '${ENV_NAME}' in place."
  "${REPO_ROOT}/upgrade.sh" --upgrade-mode=full "${UPGRADE_ARGS[@]}" 2>&1 | tee "${PLAN_LOG}" \
    || status="${PIPESTATUS[0]}"
  if [ "$status" = "0" ]; then
    summary "### Reconciled \`${ENV_NAME}\`"
    summary ""
    summary "The composition in this checkout is now applied to the environment."
    output "result" "applied"
  else
    summary "### Reconciling \`${ENV_NAME}\` failed"
    output "result" "failed"
  fi
fi

release_lease
exit "$status"
