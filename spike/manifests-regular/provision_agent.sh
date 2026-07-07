#!/usr/bin/env bash
# ==============================================================================
# 🕸️ Provision Agent for Regular Spike
# ==============================================================================
# Templates config.yaml and agent.yaml, creates ConfigMap, and applies the Agent.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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
export TARGET_NAMESPACE

echo "Rendering config.yaml..."
envsubst < "${SCRIPT_DIR}/config.yaml.template" > "${SCRIPT_DIR}/config.yaml"

echo "Creating configmap 'platform-agent-config'..."
kubectl delete configmap platform-agent-config -n "${TARGET_NAMESPACE}" --ignore-not-found=true
kubectl create configmap platform-agent-config -n "${TARGET_NAMESPACE}" --from-file=config.yaml="${SCRIPT_DIR}/config.yaml" --from-file=sitecustomize.py="${SCRIPT_DIR}/sitecustomize.py"

echo "Rendering Agent manifest..."
envsubst < "${SCRIPT_DIR}/agent.yaml.template" > "${SCRIPT_DIR}/agent.yaml"

echo "Cleaning up old Agent..."
kubectl delete agent platform-agent -n "${TARGET_NAMESPACE}" --ignore-not-found=true
kubectl delete pvc platform-agent-data -n "${TARGET_NAMESPACE}" --ignore-not-found=true

echo "Applying Agent..."
kubectl apply -f "${SCRIPT_DIR}/agent.yaml"

echo "✓ Agent provisioned!"
