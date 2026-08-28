#!/usr/bin/env bash
# Executes environment teardown and provisioning for release candidate deployment.
set -euo pipefail

export CLOUDSDK_CORE_DISABLE_PROMPTS="${CLOUDSDK_CORE_DISABLE_PROMPTS:-1}"

# rc_teardown_is_strict, rc_teardown_run and rc_teardown_report_failure are
# shared with teardown_rc_environment.sh, which removes the environment again
# once a run has passed. Both read the same three outcomes out of uninstall.sh.
# shellcheck source=scripts/release/rc_teardown_common.sh
. "$(dirname "${BASH_SOURCE[0]}")/rc_teardown_common.sh"

# Before the temp file, so a missing coordinate aborts without leaving one
# behind — this script deliberately carries no EXIT trap to clean it up. A trap
# that ends on a successful command hands ITS status to the shell, which would
# turn a `set -u` abort on a missing input into a green step.
rc_teardown_require_inputs
TEARDOWN_LOG="$(mktemp)"

echo "==> Tearing down existing RC environment (${RC_TEARDOWN_TARGET}) via canonical uninstall.sh..."
TEARDOWN_STATUS=0
rc_teardown_run "${TEARDOWN_LOG}" || TEARDOWN_STATUS=$?

# uninstall.sh exits 0 when it tore the environment down, 3 when there was no
# Terraform state to tear down (not a failure), and anything else when the
# teardown could not start or did not finish; `./uninstall.sh --help` is the
# contract. Collapsing the three into one warning is how a teardown that tore
# nothing down went unremarked: the RC environment survived from run to run
# while the pipeline reported provisioning a fresh one, and the AgentPlugins
# and Secrets it left behind were read as E2E flakes.
case "${TEARDOWN_STATUS}" in
  0)
    echo "==> Teardown complete (uninstall.sh exit 0)."
    ;;
  3)
    echo "==> Nothing to tear down: no Terraform state for '${GKE_CLUSTER_NAME}' (uninstall.sh exit 3), so there is no RC environment to remove."
    ;;
  *)
    rc_teardown_report_failure \
      "${TEARDOWN_STATUS}" "${TEARDOWN_LOG}" \
      "uninstall.sh exited ${TEARDOWN_STATUS}; the RC environment was NOT torn down and this run reinstalls over whatever survived." \
      "⚠️ RC teardown failed" \
      "\`uninstall.sh\` did not tear the RC environment down. The install below runs" \
      "on top of the previous run's cluster, CRs, Secrets and pods, so any E2E" \
      "failure may be stale state rather than a regression in the candidate."
    # Whether this is fatal is the caller's choice, because the two answers
    # trade different things. Stopping keeps a candidate from being validated
    # against stale state; continuing keeps a teardown problem from blocking
    # every release. RC_TEARDOWN_STRICT picks, and the pipeline sets it from a
    # variable on the `rc` environment so the choice is a setting, not a commit.
    if rc_teardown_is_strict; then
      echo "RC_TEARDOWN_STRICT is set: refusing to provision on top of a failed teardown." >&2
      rm -f "${TEARDOWN_LOG}"
      exit "${TEARDOWN_STATUS}"
    fi
    echo "==> Proceeding with provisioning anyway (RC_TEARDOWN_STRICT is not set); the environment is NOT fresh." >&2
    ;;
esac

rm -f "${TEARDOWN_LOG}"

INSTALL_ARGS=(
  --non-interactive -y
  --project-id="${GCP_PROJECT_ID}"
  --region="${GCP_REGION}"
  --cluster-name="${GKE_CLUSTER_NAME}"
  --image-tag="${IMAGE_TAG}"
)

if [ "${GOOGLE_CHAT_ENABLED:-false}" = "true" ]; then
  INSTALL_ARGS+=(--enable-google-chat)
fi

if [ -n "${GOOGLE_CHAT_MODE:-}" ]; then
  INSTALL_ARGS+=(--google-chat-mode="${GOOGLE_CHAT_MODE}")
fi

if [ -n "${CHAT_TOPIC_NAME:-}" ]; then
  INSTALL_ARGS+=(--chat-topic-name="${CHAT_TOPIC_NAME}")
fi

if [ -n "${MODEL_PROVIDER:-}" ]; then
  INSTALL_ARGS+=(--model-provider="${MODEL_PROVIDER}")
fi

if [ -n "${MODEL_DEFAULT_NAME:-}" ]; then
  INSTALL_ARGS+=(--model-default-name="${MODEL_DEFAULT_NAME}")
fi

if [ -n "${ENABLE_GVISOR:-}" ]; then
  INSTALL_ARGS+=(--gvisor="${ENABLE_GVISOR}")
fi

if [ -n "${PLATFORM_AGENT_PERMISSION_SET:-}" ]; then
  INSTALL_ARGS+=(--permission-set="${PLATFORM_AGENT_PERMISSION_SET}")
fi

if [ -n "${REGISTRY_PREFIX:-}" ]; then
  INSTALL_ARGS+=(--registry-prefix="${REGISTRY_PREFIX}")
fi

if [ -n "${USER_PROFILE_ENABLED:-}" ]; then
  INSTALL_ARGS+=(--user-profile-enabled="${USER_PROFILE_ENABLED}")
fi

# GitOps repository, and with it the GitHub token minter.
#
# Passed only when set, because --gitops-repo with an empty value is not the same
# as omitting it: install.sh defaults PARAM_GITOPS_REPO to 'gke-fleet-iac', so an
# empty flag would name a repository this project does not own. Omitted, the
# install is byte-identical to one that never had these inputs — GITHUB_ORG stays
# empty and installer_common.sh leaves enable_github_minter false.
if [ -n "${GITHUB_ORG:-}" ]; then
  INSTALL_ARGS+=(--gitops-org="${GITHUB_ORG}")
fi

if [ -n "${GITHUB_REPO:-}" ]; then
  INSTALL_ARGS+=(--gitops-repo="${GITHUB_REPO}")
fi

# Memory mode mapping: kube_agents_memory/hindsight -> hindsight, none/off -> off, else -> file
if [ "${MEMORY_PROVIDER:-}" = "kube_agents_memory" ] || [ "${MEMORY_PROVIDER:-}" = "hindsight" ]; then
  INSTALL_ARGS+=(--memory=hindsight)
elif [ "${MEMORY_PROVIDER:-}" = "none" ] || [ "${MEMORY_PROVIDER:-}" = "off" ]; then
  INSTALL_ARGS+=(--memory=off)
else
  INSTALL_ARGS+=(--memory=file)
fi

# install.sh imports the GitHub App private key into the minter's KMS signing key
# (import_github_pem), and it takes a path rather than a value — GITHUB_PEM_PATH.
# A secret only exists here as a variable, so it has to be materialised.
#
# The import is skipped when the key already has an ENABLED version, so on the RC
# this only does work on the first install after the key is created: lifecycle.sh's
# adopt-kms re-adopts the key ring on every subsequent apply and restores the key
# version that terraform destroy left disabled, and uninstall.sh:452 records that
# GCP cannot delete key rings at all.
#
# Written with a restrictive umask rather than chmod after the fact, so the key is
# never briefly world-readable, and removed after install.sh rather than in an EXIT
# trap — see the note at the top of this file for why this script has none.
RC_PEM_TMP=""
if [ -n "${GH_APP_PRIVATE_KEY:-}" ] && [ -z "${GITHUB_PEM_PATH:-}" ]; then
  RC_PEM_TMP="$(umask 077 && mktemp)"
  printf '%s\n' "${GH_APP_PRIVATE_KEY}" >"${RC_PEM_TMP}"
  export GITHUB_PEM_PATH="${RC_PEM_TMP}"
  echo "==> GitHub App private key staged for KMS import (imported only if the minter's key has no enabled version)."
fi

echo "==> Provisioning RC environment at the candidate commit via canonical install.sh..."
INSTALL_STATUS=0
./install.sh "${INSTALL_ARGS[@]}" || INSTALL_STATUS=$?

if [ -n "${RC_PEM_TMP}" ]; then
  rm -f "${RC_PEM_TMP}"
fi

exit "${INSTALL_STATUS}"
