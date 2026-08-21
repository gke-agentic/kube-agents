#!/usr/bin/env bash
# Connects to GKE cluster and verifies that required deployments reach Ready state.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh"

readonly READINESS_TIMEOUT="300s" # 5 minutes timeout for GKE pod readiness

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
export CLOUDSDK_PYTHON_SITEPACKAGES="0"
export PYTHONNOUSERSITE="1"
export USE_GKE_GCLOUD_AUTH_PLUGIN="True"

if [ -n "${GOOGLE_APPLICATION_CREDENTIALS:-}" ] && [ -f "${GOOGLE_APPLICATION_CREDENTIALS}" ]; then
  gcloud auth activate-service-account --key-file="${GOOGLE_APPLICATION_CREDENTIALS}" --quiet || true
  unset GOOGLE_APPLICATION_CREDENTIALS
fi

gke_dns_endpoint_flag "${CLUSTER_NAME}" "${REGION}" "${PROJECT_ID}"
# Unquoted on purpose: empty must contribute no argument. See gke_dns_endpoint.sh.
gcloud container clusters get-credentials "${CLUSTER_NAME}" --location "${REGION}" --project "${PROJECT_ID}" \
  ${GKE_DNS_ENDPOINT_FLAG}

TOKEN="$(gcloud auth print-access-token 2>/dev/null || true)"
if [ -n "${TOKEN}" ]; then
  python3 -c "
import json, os, pathlib, subprocess
res = subprocess.run(['kubectl', 'config', 'view', '--raw', '-o', 'json'], capture_output=True, text=True)
if res.returncode == 0 and res.stdout.strip():
    data = json.loads(res.stdout)
    token = '''${TOKEN}'''
    for u in data.get('users', []):
        u.get('user', {}).pop('exec', None)
        u.setdefault('user', {})['token'] = token
    cfg_file = os.environ.get('KUBECONFIG') or str(pathlib.Path.home() / '.kube' / 'config')
    p = pathlib.Path(cfg_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding='utf-8')
" || true
fi

echo "🔑 Configuring Docker authentication for Artifact Registry (${REGION}-docker.pkg.dev)..."
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet || true

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
