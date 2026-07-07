#!/usr/bin/env bash
# ==============================================================================
# 🚀 Full Orchestrated Deployment for Regular Spike
# ==============================================================================
# Sequence: Secrets -> LiteLLM -> IAM (KSA) -> Agent
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export VARS_FILE="${SCRIPT_DIR}/vars.sh"

echo "=== 🚀 Starting Orchestrated Deployment for Regular Spike ==="

# 1. Provision Secrets
echo -e "\n=== [1/4] Provisioning GKE Secrets ==="
bash "${SCRIPT_DIR}/provision_secrets.sh"

# 2. Deploy LiteLLM
echo -e "\n=== [2/4] Deploying LiteLLM Gateway ==="
bash "${SCRIPT_DIR}/provision_litellm.sh"

# 3. Provision IAM (Creates KSA and binds to GSA)
echo -e "\n=== [3/4] Provisioning GCP IAM & Workload Identity ==="
bash "${SCRIPT_DIR}/provision_iam.sh"

# 4. Provision Agent (Creates ConfigMap and applies Agent CRD)
echo -e "\n=== [4/4] Provisioning Agent ==="
bash "${SCRIPT_DIR}/provision_agent.sh"

echo -e "\n=============================================================================="
echo "✓ 🚀 Deployment complete! The Platform Agent is initializing."
echo "   Monitor status: kubectl get agent platform-agent -n kube-agents-regular-spike"
echo "   View logs:      kubectl logs -n kube-agents-regular-spike -l app=platform-agent"
echo "=============================================================================="
