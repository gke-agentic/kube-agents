#!/usr/bin/env bash
# Executes environment teardown and provisioning for release candidate deployment.
set -euo pipefail

export CLOUDSDK_CORE_DISABLE_PROMPTS="${CLOUDSDK_CORE_DISABLE_PROMPTS:-1}"

# RC_TEARDOWN_STRICT is typed into a GitHub web form, so it accepts what
# installer_common.sh's is_truthy accepts rather than the literal "true" alone —
# a maintainer who types `1` must not get a pipeline that keeps installing over
# a surviving environment while logging that strict mode is off. Inlined
# because this script does not source installer_common.sh; keep the two in step
# (the accepted set is pinned by tests/testing/common.py's TRUTHY_BOOLEAN_INPUTS).
# A value that is neither truthy nor an obvious "off" is a typo, and a typo in a
# safety switch is worth a line of output.
rc_teardown_is_strict() {
  local val="${RC_TEARDOWN_STRICT:-}"
  val="${val//[[:space:]]/}"
  case "$val" in
    [Tt][Rr][Uu][Ee] | [Yy][Ee][Ss] | [Yy] | 1 | [Oo][Nn]) return 0 ;;
    "" | [Ff][Aa][Ll][Ss][Ee] | [Nn][Oo] | [Nn] | 0 | [Oo][Ff][Ff]) return 1 ;;
    *)
      echo "::warning title=RC_TEARDOWN_STRICT not understood::'${RC_TEARDOWN_STRICT}' is neither truthy nor falsy; treating it as off." >&2
      return 1
      ;;
  esac
}

# Surfaces on the run's annotations and in the job summary, not just in the
# scrolled-past middle of a step log.
annotate_teardown_failure() {
  local status="$1"
  echo "::error title=RC teardown failed::uninstall.sh exited ${status}; the RC environment was NOT torn down and this run reinstalls over whatever survived." >&2
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    {
      echo "### ⚠️ RC teardown failed (exit ${status})"
      echo ""
      echo "\`uninstall.sh\` did not tear the RC environment down. The install below runs"
      echo "on top of the previous run's cluster, CRs, Secrets and pods, so any E2E"
      echo "failure may be stale state rather than a regression in the candidate."
      echo ""
      echo "<details><summary>uninstall.sh output</summary>"
      echo ""
      echo '```'
      # Backticks stripped so a triple-backtick in the teardown output cannot
      # close this fence and render the rest as markdown and HTML; nothing in a
      # log excerpt depends on them. `awk 1` guarantees the trailing newline the
      # closing fence needs — without it a final line with no newline swallows
      # the ``` and the block never closes.
      tail -n 40 "${TEARDOWN_LOG}" | tr -d '`' | awk 1
      echo '```'
      echo ""
      echo "</details>"
    } >> "${GITHUB_STEP_SUMMARY}"
  fi
}

# Expanded in the main shell, not inside the pipeline below: a `set -u` abort
# on a missing variable has to kill this script, and inside a pipeline it would
# only kill that stage's subshell.
UNINSTALL_ARGS=(
  --non-interactive -y
  --project-id="${GCP_PROJECT_ID}"
  --region="${GCP_REGION}"
  --cluster-name="${GKE_CLUSTER_NAME}"
)

# Created after the inputs are read, and removed by hand below, because this
# script must not carry an EXIT trap: a trap that ends on a successful command
# hands ITS status to the shell, which turns a `set -u` abort on a missing
# input into a green step.
TEARDOWN_LOG="$(mktemp)"

echo "==> Tearing down existing RC environment via canonical uninstall.sh..."
set +e
./uninstall.sh "${UNINSTALL_ARGS[@]}" 2>&1 | tee "${TEARDOWN_LOG}"
TEARDOWN_STATUS="${PIPESTATUS[0]}"
set -e

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
    annotate_teardown_failure "${TEARDOWN_STATUS}"
    # Deliberately not fatal by default. The teardown does not run in this
    # pipeline today — the deploy job installs no terraform before this step,
    # and install.sh auto-installs it only afterwards — so failing the job here
    # would turn a silent problem into a permanently red one. Making the
    # teardown succeed instead means rebuilding a GKE cluster on every
    # scheduled run, which is a cost decision for a human, not for this script.
    # Set RC_TEARDOWN_STRICT=true to make it a hard stop once that is decided.
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

# Memory mode mapping: kube_agents_memory/hindsight -> hindsight, none/off -> off, else -> file
if [ "${MEMORY_PROVIDER:-}" = "kube_agents_memory" ] || [ "${MEMORY_PROVIDER:-}" = "hindsight" ]; then
  INSTALL_ARGS+=(--memory=hindsight)
elif [ "${MEMORY_PROVIDER:-}" = "none" ] || [ "${MEMORY_PROVIDER:-}" = "off" ]; then
  INSTALL_ARGS+=(--memory=off)
else
  INSTALL_ARGS+=(--memory=file)
fi

echo "==> Provisioning RC environment at the candidate commit via canonical install.sh..."
./install.sh "${INSTALL_ARGS[@]}"
