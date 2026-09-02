#!/usr/bin/env bash
# Renders an install.env from a GitHub environment's variables and secrets.
#
# install.env is the installer's configuration input (see install.env.example).
# On a workstation it is hand-authored and lives beside install.sh. A GitHub
# runner is ephemeral and has no such file, so every job that drives the
# installer renders one here first and points KUBE_AGENTS_INSTALL_ENV at it.
#
# This is the single mapping from `vars.*`/`secrets.*` to install configuration.
# Before it there were two: deploy-environment.yml built a flag list for
# install.sh, and nothing at all existed for the long-lived environments, which
# is how autopush and staging came to run last month's composition with today's
# images (#1117). One mapping means one answer to "what is this environment
# configured as", and it is the same answer whether the caller is provisioning
# from nothing, reconciling in place, or planning a drift report.
#
# Usage:
#   render_install_env.sh <output-path> [--strict]
#
# --strict additionally requires every setting whose absence would REMOVE
# something from an install that already exists. See REQUIRED_STRICT below for
# why that list is not the same as the one an ephemeral environment needs.
#
# Reads its inputs from the environment, so the calling workflow step decides
# what a variable resolves to and this script never reaches for `vars.` itself.
set -euo pipefail

OUT_PATH="${1:-}"
STRICT="false"
if [ "${2:-}" = "--strict" ]; then
  STRICT="true"
fi

if [ -z "$OUT_PATH" ]; then
  echo "usage: render_install_env.sh <output-path> [--strict]" >&2
  exit 2
fi

# ---------------------------------------------------------------------------
# The variable contract
# ---------------------------------------------------------------------------
# Left of the colon: the install.env key. Right: the GitHub variable or secret
# name the workflow exports it under. They differ because the GitHub side was
# named for CI ("GCP_PROJECT_ID", the project CI deploys to) and the installer
# side for the install ("PROJECT_ID", the project the agent runs in), and
# renaming either now would break every environment at once.
#
# A key with no value is omitted from the file entirely rather than written
# empty. install.defaults.env then supplies the default, which is the whole
# point of #1081's precedence chain — an empty assignment would beat it and
# mean "explicitly nothing", which for MEMORY or PERMISSION_SET is a different
# install from the default one.
MAPPING="
PROJECT_ID:GCP_PROJECT_ID
REGION:GCP_REGION
CLUSTER_NAME:GKE_CLUSTER_NAME
CLUSTER_MODE:CLUSTER_MODE
MODEL_PROVIDER:MODEL_PROVIDER
MODEL_DEFAULT_NAME:MODEL_DEFAULT_NAME
VERTEX_PROJECT_ID:VERTEX_PROJECT_ID
VERTEX_LOCATION:VERTEX_LOCATION
GEMINI_API_KEY:GEMINI_API_KEY
ANTHROPIC_API_KEY:ANTHROPIC_API_KEY
OPENAI_API_KEY:OPENAI_API_KEY
GOOGLE_CHAT_ENABLED:GOOGLE_CHAT_ENABLED
GOOGLE_CHAT_MODE:GOOGLE_CHAT_MODE
CHAT_TOPIC_NAME:CHAT_TOPIC_NAME
CHAT_SUB_NAME:CHAT_SUB_NAME
ALLOWED_USERS:ALLOWED_USERS
SLACK_ENABLED:SLACK_ENABLED
SLACK_BOT_TOKEN:SLACK_BOT_TOKEN
SLACK_APP_TOKEN:SLACK_APP_TOKEN
SLACK_ALLOWED_USERS:SLACK_ALLOWED_USERS
SLACK_HOME_CHANNEL:SLACK_HOME_CHANNEL
SLACK_HOME_CHANNEL_NAME:SLACK_HOME_CHANNEL_NAME
GITOPS_ORG:GITOPS_ORG
GITOPS_REPO:GITOPS_REPO
GITHUB_APP_ID:GITHUB_APP_ID
KMS_KEYRING:KMS_KEYRING
KMS_KEY:KMS_KEY
PLATFORM_AGENT_PERMISSION_SET:PLATFORM_AGENT_PERMISSION_SET
PLATFORM_AGENT_CUSTOM_ROLES:PLATFORM_AGENT_CUSTOM_ROLES
ENABLE_GVISOR:ENABLE_GVISOR
USER_PROFILE_ENABLED:USER_PROFILE_ENABLED
HERMES_DASHBOARD_ENABLED:HERMES_DASHBOARD_ENABLED
ENABLE_GKE_BACKUP_PLAN:ENABLE_GKE_BACKUP_PLAN
REGISTRY_PREFIX:REGISTRY_PREFIX
THIRD_PARTY_REGISTRY_PREFIX:THIRD_PARTY_REGISTRY_PREFIX
NAMESPACE:AGENT_NAMESPACE
"

# Always required: without these the script cannot name an install at all, so
# there is nothing for a plan or an apply to be about.
REQUIRED_ALWAYS="GCP_PROJECT_ID GCP_REGION GKE_CLUSTER_NAME"

# Required in --strict mode, which is the mode every job that touches a
# LONG-LIVED environment runs in.
#
# Each of these names something the composition provisions and an omitted value
# un-provisions: the gVisor node pool, the Hindsight API and its Postgres, the
# agent's custom IAM roles, the backup plan, the Pub/Sub topic behind Google
# Chat. On an environment that is torn down and rebuilt every run — `rc` and
# `nightly` — an omitted value costs a feature the tests may not exercise. On
# one that has been running for a month, the same omission is a `terraform
# apply` that plans a DESTROY, which is #1060's failure and the reason #1117
# could not simply be wired up. #1081 closed it for the flag path; this list is
# the same guarantee for the CI path, where the "previous value" the installer
# would inherit lives in a GitHub environment rather than on a disk.
#
# So: an unconfigured long-lived environment fails here, loudly, naming what to
# set — rather than converging on a default and taking the difference out of
# the running install.
REQUIRED_STRICT="
GOOGLE_CHAT_ENABLED
MODEL_PROVIDER
PLATFORM_AGENT_PERMISSION_SET
ENABLE_GVISOR
MEMORY_PROVIDER
USER_PROFILE_ENABLED
ENABLE_GKE_BACKUP_PLAN
"

missing=""
for var in $REQUIRED_ALWAYS; do
  [ -n "${!var:-}" ] || missing="${missing} ${var}"
done
if [ "$STRICT" = "true" ]; then
  for var in $REQUIRED_STRICT; do
    [ -n "${!var:-}" ] || missing="${missing} ${var}"
  done
fi

if [ -n "$missing" ]; then
  # One annotation naming every missing variable at once. Failing on the first
  # one costs a full run per variable, and there are eleven of them on an
  # environment that has never been configured.
  echo "::error title=Install configuration is incomplete::Set these on the GitHub environment this job binds to:${missing}. Each one is a setting the composition provisions; running without it would apply a default over the value this environment is already installed with, and terraform would plan to destroy the difference. docs/site/src/content/docs/deploy/environment-reconcile.md lists what each one should be."
  echo "==> Missing install configuration:${missing}" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Settings that need translating rather than copying
# ---------------------------------------------------------------------------
# MEMORY_PROVIDER is the CI-side name and carries CI-side values; install.env
# records MEMORY, whose vocabulary is file/hindsight/off. The same three-way
# mapping deploy-environment.yml's provisioning step used to do inline.
case "${MEMORY_PROVIDER:-}" in
  kube_agents_memory|hindsight) MEMORY="hindsight" ;;
  none|off)                     MEMORY="off" ;;
  "")                           MEMORY="" ;;
  *)                            MEMORY="file" ;;
esac
export MEMORY

# NAMESPACE has three spellings in play: the installer's own NAMESPACE, rc and
# nightly's AGENT_NAMESPACE, and staging's bare NAMESPACE. The mapping above
# reads AGENT_NAMESPACE; this fills in from the other before it, so an
# environment carrying either one is understood and neither has to be renamed
# in the GitHub UI while installs are running against it.
: "${AGENT_NAMESPACE:=${NAMESPACE:-}}"
export AGENT_NAMESPACE

# ---------------------------------------------------------------------------
# Write it
# ---------------------------------------------------------------------------
# umask first, so the file is never briefly world-readable: it carries the model
# provider's API key and the Slack tokens.
umask 077
: >"$OUT_PATH"

{
  echo "# Generated by scripts/release/render_install_env.sh — do not edit."
  echo "# Rendered from the GitHub environment's variables and secrets."
  echo "# Every value here comes from a GitHub environment setting; change it there."
  echo
} >>"$OUT_PATH"

emit() {
  local key="$1" value="$2"
  [ -n "$value" ] || return 0
  # %q, because these are read with `set -a; . install.env; set +a` and a value
  # with a space, a quote or a `$` in it — an allowed-users list, a Slack
  # channel name — would otherwise be re-interpreted as shell.
  printf '%s=%q\n' "$key" "$value" >>"$OUT_PATH"
}

for pair in $MAPPING; do
  key="${pair%%:*}"
  src="${pair##*:}"
  emit "$key" "${!src:-}"
done

# MEMORY is derived above rather than mapped, so it is emitted on its own.
emit MEMORY "${MEMORY}"

chmod 600 "$OUT_PATH"

# The listing is keys only. Every value is either a GitHub variable the reader
# can look up or a secret they must not be able to.
echo "==> Rendered install configuration to ${OUT_PATH}:"
sed -n 's/^\([A-Z_][A-Z0-9_]*\)=.*/    \1/p' "$OUT_PATH"
