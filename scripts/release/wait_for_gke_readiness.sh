#!/usr/bin/env bash
# Connects to GKE cluster and verifies that required deployments reach Ready state.
#
# Waits; it does not install. Anything this script needs on the cluster is put
# there by an earlier step — alert ingress by install_pubsub_platform.sh, which
# runs before it so that the gateway re-template the adapter causes is already
# in flight by the time the rollout waits below start.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/release/common.sh
source "${SCRIPT_DIR}/common.sh"

readonly READINESS_TIMEOUT="300s" # 5 minutes timeout for GKE pod readiness

release_resolve_target

COMMIT_SHA="${1:-${COMMIT_SHA:-}}"

echo "======================================================================"
echo "⏳ CONNECTING TO GKE & WAITING FOR POD READINESS"
echo "Project ID:        ${PROJECT_ID}"
echo "Region:            ${REGION}"
echo "Cluster Name:      ${CLUSTER_NAME}"
echo "Namespace:         ${AGENT_NAMESPACE}"
echo "Target Commit SHA: ${COMMIT_SHA:-(not specified)}"
echo "Readiness Timeout: ${READINESS_TIMEOUT} (5 minutes)"
echo "======================================================================"

release_connect_kubectl

echo "🔑 Configuring Docker authentication for Artifact Registry (${REGION}-docker.pkg.dev)..."
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet || true

if [ -n "${COMMIT_SHA}" ]; then
  echo "🔍 Verifying platform-agent-gateway deployment container image matches commit ${COMMIT_SHA}..."
  start_time=$(date +%s)
  until kubectl get deploy/platform-agent-gateway -n "${AGENT_NAMESPACE}" -o jsonpath='{.spec.template.spec.containers[*].image}' 2>/dev/null | grep -q ":${COMMIT_SHA}"; do
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
kubectl rollout status deployment/litellm -n "${AGENT_NAMESPACE}" --timeout="${READINESS_TIMEOUT}"
kubectl wait --for=condition=Available deployment/litellm -n "${AGENT_NAMESPACE}" --timeout="${READINESS_TIMEOUT}"

echo "Waiting for platform-agent-gateway deployment readiness..."
kubectl rollout status deployment/platform-agent-gateway -n "${AGENT_NAMESPACE}" --timeout="${READINESS_TIMEOUT}"
kubectl wait --for=condition=Available deployment/platform-agent-gateway -n "${AGENT_NAMESPACE}" --timeout="${READINESS_TIMEOUT}"
