#!/usr/bin/env bash
# ==============================================================================
# 🚀 Coordinator: Deploy Platform Agent Hybrid Spike (kagent + Hermes)
# ==============================================================================
# Runs the individual provision scripts to deploy secrets, LiteLLM,
# build the agent image, configure IAM, and apply kagent harness configs.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export KAGENT_VARS_OVERRIDE="${SCRIPT_DIR}/vars.sh"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

echo "Checking pre-requisites..."
if ! command -v kubectl &> /dev/null; then
    echo "Error: kubectl is required but not installed."
    exit 1
fi

# 1. Check if kagent CRDs are installed
if ! kubectl get crd agentharnesses.kagent.dev &> /dev/null; then
    echo "Error: kagent CRDs (AgentHarness) are not installed in the cluster."
    exit 1
fi

# Parse arguments
SKIP_BUILD=0
for arg in "$@"; do
  case $arg in
    --skip-build) SKIP_BUILD=1 ;;
    *) ;;
  esac
done

# Step 1: Provision Secrets
echo -e "\n========================================="
echo "🔑 [Step 1/5] Provisioning GKE Secrets"
echo "========================================="
bash "${SCRIPT_DIR}/provision_secrets.sh"

# Step 2: Provision LiteLLM
echo -e "\n========================================="
echo "🧠 [Step 2/5] Provisioning LiteLLM Gateway"
echo "========================================="
bash "${SCRIPT_DIR}/provision_litellm.sh"

# Step 3: Build & Push Image
if [ "$SKIP_BUILD" -eq 0 ]; then
    echo -e "\n========================================="
    echo "🛠️ [Step 3/5] Building Platform Agent Image"
    echo "========================================="
    bash "${SCRIPT_DIR}/provision_image.sh"
else
    echo -e "\n========================================="
    echo "⏩ [Step 3/5] Skipping Image Build (Using latest)"
    echo "========================================="
    # Load vars to resolve registry path
    source "${SCRIPT_DIR}/vars.sh"
    GCP_REPO_NAME="kagent-hybrid"
    IMAGE_NAME="platform-agent"
    echo "${REGION}-docker.pkg.dev/${PROJECT_ID}/${GCP_REPO_NAME}/${IMAGE_NAME}:latest" > "${SCRIPT_DIR}/.image_uri"
fi

# Step 4: Configure IAM & Workload Identity
echo -e "\n========================================="
echo "🆔 [Step 4/5] Configuring GCP Workload Identity"
echo "========================================="
bash "${SCRIPT_DIR}/provision_iam.sh"

# Step 5: Deploy Agent Harness & Config
echo -e "\n========================================="
echo "🕸️ [Step 5/5] Deploying Agent Harness"
echo "========================================="
bash "${SCRIPT_DIR}/provision_harness.sh"

echo -e "\n✓ Hybrid Spike Deployment Completed Successfully!"
echo "Monitor progress with:"
echo "  kubectl -n kube-agents-spike get agentharness platform-agent-hybrid"
echo "  kubectl -n kube-agents-spike describe agentharness platform-agent-hybrid"
