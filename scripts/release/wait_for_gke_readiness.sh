#!/usr/bin/env bash
# Connects to GKE cluster and verifies that required deployments reach Ready state.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

readonly READINESS_TIMEOUT="300s" # 5 minutes timeout for GKE pod readiness
# Seconds, not a kubectl duration: the adapter wait below is a shell loop, because
# the AgentPlugin CRD carries no condition for `kubectl wait --for=condition` to read.
readonly PLUGIN_READY_TIMEOUT=300

CLUSTER_NAME="${GKE_CLUSTER_NAME:-${CLUSTER_NAME:-platform-agent-host}}"
REGION="${GCP_REGION:-${REGION:-us-central1}}"
PROJECT_ID="${GCP_PROJECT_ID:-${PROJECT_ID:-kube-agents-rc}}"

COMMIT_SHA="${1:-${COMMIT_SHA:-}}"

echo "======================================================================"
echo "⏳ CONNECTING TO GKE & WAITING FOR POD READINESS"
echo "Project ID:        ${PROJECT_ID}"
echo "Region:            ${REGION}"
echo "Cluster Name:      ${CLUSTER_NAME}"
echo "Target Commit SHA: ${COMMIT_SHA:-(not specified)}"
echo "Readiness Timeout: ${READINESS_TIMEOUT} (5 minutes)"
echo "======================================================================"

unset CLOUDSDK_PYTHON || true
unset CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE || true
export CLOUDSDK_PYTHON_SITEPACKAGES="0"
export PYTHONNOUSERSITE="1"
export USE_GKE_GCLOUD_AUTH_PLUGIN="True"
export CLOUDSDK_CONTAINER_USE_APPLICATION_DEFAULT_CREDENTIALS="false"
gcloud config set container/use_application_default_credentials false --quiet || true

if [ -n "${GOOGLE_APPLICATION_CREDENTIALS:-}" ] && [ -f "${GOOGLE_APPLICATION_CREDENTIALS}" ]; then
  gcloud auth activate-service-account --key-file="${GOOGLE_APPLICATION_CREDENTIALS}" --quiet || true
fi

CURRENT_CTX="$(kubectl config current-context 2>/dev/null || echo "")"
if ! kubectl cluster-info >/dev/null 2>&1 || [[ "${CURRENT_CTX}" != *"${CLUSTER_NAME}"* || "${CURRENT_CTX}" != *"${PROJECT_ID}"* ]]; then
  echo "Connecting kubectl to target cluster '${CLUSTER_NAME}' in project '${PROJECT_ID}'..."
  gke_dns_endpoint_flag "${CLUSTER_NAME}" "${REGION}" "${PROJECT_ID}"
  # Unquoted on purpose: empty must contribute no argument. See gke_dns_endpoint.sh.
  gcloud container clusters get-credentials "${CLUSTER_NAME}" --location "${REGION}" --project "${PROJECT_ID}" \
    ${GKE_DNS_ENDPOINT_FLAG}
fi

echo "🔑 Configuring Docker authentication for Artifact Registry (${REGION}-docker.pkg.dev)..."
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet || true

# ─── Pub/Sub alert ingress ────────────────────────────────────────────────────
# The Pub/Sub adapter is a separate AgentPlugin and is not in the agent image.
# deploy/docker/Dockerfile bakes the google_chat, slack and chat platforms and
# installs only the google-cloud-pubsub library; the adapter itself ships solely as
# agentplugins/pubsub-platform, and nothing in the install engine — Terraform, the
# chart, or install.sh — puts it on a cluster.
#
# A consumer such as gke-stockout-investigator contributes only route config under
# platforms.pubsub.extra.subscriptions, which the operator files on the default
# profile (gatewayScopedPluginConfigSubtrees, platformagent_manifests.go). With no
# adapter to read it the gateway opens no listener at all, and the failure is
# silence: verify.sh reports "no sign the adapter saw the message at all" and each
# stockout scenario then burns its full 360s watch reporting that the agent never
# started an investigation. Runs 32986207520, 33018980784, 33031877720 and
# 33061389550 each failed exactly that way, in both tests, deterministically.
#
# Installed here rather than by a test fixture because the adapter is a gateway
# singleton shared by every alert-producing plugin, not a fixture of one suite —
# and because the two things its installer needs already exist at this point: the
# kubectl context resolved above and the Artifact Registry credentials configured
# on the line before. The plugin's own install.sh is the canonical installer and is
# idempotent (`helm upgrade --install`, and plugin_image_resolve skips the build
# when the content tag is already published), so a re-run costs a no-op.
#
# Warned about rather than fatal, in both halves below. This script runs once, before
# BOTH suites in step 3 of rc-release-pipeline.yml: the mandatory Google Chat gate and
# then the optional cluster/audit one that #980 marked `continue-on-error`. Alert
# ingress is a dependency of the optional suite alone, so exiting non-zero here would
# let a stockout-only problem block the release gate that was deliberately made the
# blocking one — and would do it before the Chat gate had run at all. A warning leaves
# that structure intact and puts the reason directly above the failure it explains.
AGENT_NAMESPACE="${AGENT_NAMESPACE:-kubeagents-system}"
if is_truthy "${SKIP_PUBSUB_PLATFORM:-false}"; then
  echo "⏭️  SKIP_PUBSUB_PLATFORM is set: leaving Pub/Sub alert ingress uninstalled."
else
  echo "📡 Installing the Pub/Sub platform adapter (alert ingress)..."
  pubsub_installed="true"
  KUBECTL_CONTEXT="$(kubectl config current-context)" \
    GCP_PROJECT_ID="${PROJECT_ID}" \
    HERMES_NAMESPACE="${AGENT_NAMESPACE}" \
    "${REPO_ROOT}/agentplugins/pubsub-platform/install.sh" || pubsub_installed="false"
fi

if [ "${pubsub_installed:-skipped}" = "false" ]; then
  echo "⚠️  WARNING: the Pub/Sub platform adapter failed to install. Alert ingress is dead," \
    "so any alert-driven test below will report that the agent never saw its alert." >&2
elif [ "${pubsub_installed:-skipped}" = "true" ]; then
  # The rollout waits further down are what absorb the re-template this install
  # provokes, but only if the operator has written the workload before they run.
  # Waiting for the plugin's status to catch up to its spec is what orders the two:
  # the operator writes the AgentPlugin status and the gateway workload inside one
  # reconcile (platformagent_controller.go), so a plugin whose observedGeneration
  # has caught up is one whose gateway has already been re-templated. Phase alone
  # would not do it — a Ready left from an earlier reconcile is what a stale plugin
  # looks like.
  echo "Waiting for the pubsubplatform AgentPlugin to reconcile..."
  plugin_deadline=$(($(date +%s) + PLUGIN_READY_TIMEOUT))
  while :; do
    plugin_phase=""
    plugin_observed=""
    plugin_generation=""
    read -r plugin_phase plugin_observed plugin_generation <<<"$(
      kubectl get agentplugin pubsubplatform -n "${AGENT_NAMESPACE}" \
        -o jsonpath='{.status.phase} {.status.observedGeneration} {.metadata.generation}' 2>/dev/null || true
    )" || true
    if [ "${plugin_phase}" = "Ready" ] && [ -n "${plugin_observed}" ] &&
      [ "${plugin_observed}" = "${plugin_generation}" ]; then
      echo "✅ pubsubplatform AgentPlugin is Ready at generation ${plugin_generation}."
      break
    fi
    if [ "$(date +%s)" -ge "${plugin_deadline}" ]; then
      echo "⚠️  WARNING: the pubsubplatform AgentPlugin did not reconcile within" \
        "${PLUGIN_READY_TIMEOUT}s (phase='${plugin_phase:-<none>}'," \
        "observedGeneration='${plugin_observed:-<none>}', generation='${plugin_generation:-<none>}')." \
        "Alert ingress may not be serving when the suites below run." >&2
      break
    fi
    sleep 5
  done
fi

if [ -n "${COMMIT_SHA}" ]; then
  echo "🔍 Verifying platform-agent-gateway deployment container image matches commit ${COMMIT_SHA}..."
  start_time=$(date +%s)
  until kubectl get deploy/platform-agent-gateway -n kubeagents-system -o jsonpath='{.spec.template.spec.containers[*].image}' 2>/dev/null | grep -q ":${COMMIT_SHA}"; do
    if [ $(($(date +%s) - start_time)) -gt 300 ]; then
      echo "❌ ERROR: Deployment platform-agent-gateway did not update to image tag :${COMMIT_SHA} within timeout!" >&2
      exit 1
    fi
    echo "Waiting for deployment platform-agent-gateway image to be updated to :${COMMIT_SHA}..."
    sleep 5
  done
  echo "✅ platform-agent-gateway deployment image matches candidate commit ${COMMIT_SHA}."
fi

echo "Waiting for litellm deployment readiness..."
kubectl rollout status deployment/litellm -n kubeagents-system --timeout="${READINESS_TIMEOUT}"
kubectl wait --for=condition=Available deployment/litellm -n kubeagents-system --timeout="${READINESS_TIMEOUT}"

echo "Waiting for platform-agent-gateway deployment readiness..."
kubectl rollout status deployment/platform-agent-gateway -n kubeagents-system --timeout="${READINESS_TIMEOUT}"
kubectl wait --for=condition=Available deployment/platform-agent-gateway -n kubeagents-system --timeout="${READINESS_TIMEOUT}"
