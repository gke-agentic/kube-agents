#!/usr/bin/env bash
# ==============================================================================
# 🔑 Provision GKE Secrets for Hybrid Spike
# ==============================================================================
# Creates the spike namespace if missing and executes the SRE secrets provisioner.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Load SRE variables
VARS_SH="${SCRIPT_DIR}/vars.sh"
if [ -f "$VARS_SH" ]; then
    source "$VARS_SH"
else
    echo "Error: SRE variables file not found at ${VARS_SH}."
    exit 1
fi

TARGET_NAMESPACE="kube-agents-spike"
export NAMESPACE="${TARGET_NAMESPACE}"

echo "Ensuring namespace '${TARGET_NAMESPACE}' exists..."
kubectl create namespace "${TARGET_NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

echo "Cleaning up any existing platform secrets to avoid stale merged keys..."
kubectl delete secret platform-agent-secrets -n "${TARGET_NAMESPACE}" --ignore-not-found=true

echo "Running SRE Secrets Provisioner..."
bash "${REPO_ROOT}/k8s-operator/scripts/provision_04_gcp_k8s_secrets.sh" --no-confirm

echo "Patching secret with configuration variables for OpenShell environment preservation..."
# ==============================================================================
# OpenShell Environment Variable Wiping Workaround:
#
# Because GKE Standard's OpenShell supervisor clears out all container-level env 
# variables (configured via k8s pod spec env / valueFrom) when entering the sandbox,
# dynamic non-secret settings (like project ID, allowed users, GKE location) are lost.
# 
# To bypass this limitation, we append these dynamic configurations into the 
# 'platform-agent-secrets' Secret itself. Our container wrapper script will then 
# read them from the mounted secrets directory (/etc/secrets/...) and restore them 
# into the agent's workload environment on boot.
# ==============================================================================
B64_PROJECT_ID=$(echo -n "${PROJECT_ID}" | base64 | tr -d '\n')
B64_SUB_NAME=$(echo -n "projects/${PROJECT_ID}/subscriptions/${CHAT_SUB_NAME}" | base64 | tr -d '\n')
B64_ALLOWED_USERS=$(echo -n "${ALLOWED_USERS}" | base64 | tr -d '\n')
B64_CLUSTER_NAME=$(echo -n "${CLUSTER_NAME}" | base64 | tr -d '\n')
B64_LOCATION=$(echo -n "${REGION}" | base64 | tr -d '\n')

kubectl patch secret platform-agent-secrets -n "${TARGET_NAMESPACE}" -p "{\"data\": {\"GOOGLE_CHAT_PROJECT_ID\": \"${B64_PROJECT_ID}\", \"GOOGLE_CHAT_SUBSCRIPTION_NAME\": \"${B64_SUB_NAME}\", \"GOOGLE_CHAT_ALLOWED_USERS\": \"${B64_ALLOWED_USERS}\", \"GKE_CLUSTER_NAME\": \"${B64_CLUSTER_NAME}\", \"GKE_LOCATION\": \"${B64_LOCATION}\", \"HERMES_DASHBOARD\": \"$(echo -n "1" | base64 | tr -d '\n')\", \"PLATFORM_AGENT_DASHBOARD\": \"$(echo -n "1" | base64 | tr -d '\n')\", \"PLATFORM_AGENT_PLUGINS_DEBUG\": \"$(echo -n "0" | base64 | tr -d '\n')\", \"GRPC_VERBOSITY\": \"$(echo -n "DEBUG" | base64 | tr -d '\n')\", \"GRPC_TRACE\": \"$(echo -n "http,connectivity_state,client_channel" | base64 | tr -d '\n')\", \"HERMES_GATEWAY_PLATFORM_CONNECT_TIMEOUT\": \"$(echo -n "120" | base64 | tr -d '\n')\", \"GRPC_DNS_RESOLVER\": \"$(echo -n "native" | base64 | tr -d '\n')\"}}"
