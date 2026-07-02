#!/usr/bin/env bash
# ==============================================================================
# 🧠 Provision LiteLLM Gateway for Hybrid Spike
# ==============================================================================
# Executes SRE LiteLLM provisioner and waits for rollout to complete.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Load SRE variables
VARS_SH="${REPO_ROOT}/k8s-operator/scripts/vars.sh"
if [ -f "$VARS_SH" ]; then
    source "$VARS_SH"
else
    echo "Error: SRE variables file not found at ${VARS_SH}."
    exit 1
fi

TARGET_NAMESPACE="kube-agents-spike"
export NAMESPACE="${TARGET_NAMESPACE}"

echo "Running SRE LiteLLM Provisioner..."
bash "${REPO_ROOT}/k8s-operator/scripts/provision_07_deploy_litellm.sh" --no-confirm

echo "Waiting for LiteLLM deployment to be ready in '${TARGET_NAMESPACE}'..."
kubectl rollout status deployment/litellm -n "${TARGET_NAMESPACE}" --timeout=60s
