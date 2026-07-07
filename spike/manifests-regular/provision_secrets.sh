#!/usr/bin/env bash
# ==============================================================================
# 🔑 Provision GKE Secrets for Regular Spike
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Load SRE variables
VARS_SH="${SCRIPT_DIR}/vars.sh"
if [ -f "$VARS_SH" ]; then
    source "$VARS_SH"
    export VARS_FILE="${VARS_SH}"
else
    echo "Error: SRE variables file not found at ${VARS_SH}."
    exit 1
fi

TARGET_NAMESPACE="kube-agents-regular-spike"
export NAMESPACE="${TARGET_NAMESPACE}"

echo "Ensuring namespace '${TARGET_NAMESPACE}' exists..."
kubectl create namespace "${TARGET_NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

echo "Cleaning up any existing platform secrets..."
kubectl delete secret platform-agent-secrets -n "${TARGET_NAMESPACE}" --ignore-not-found=true

echo "Running SRE Secrets Provisioner..."
bash "${REPO_ROOT}/k8s-operator/scripts/provision_04_gcp_k8s_secrets.sh" --no-confirm
