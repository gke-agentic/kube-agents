#!/usr/bin/env bash
# ==============================================================================
# 🤖 Install kagent, OpenShell Gateway, & Sandbox Controller (Isolated Spike)
# ==============================================================================
# This script installs the required orchestrator components (Agent Sandbox Controller,
# OpenShell Gateway, and kagent Controller) into the cluster, disabling default
# agents to keep it minimal.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_NAMESPACE="kube-agents-spike"
SANDBOX_VERSION="v0.3.10"

echo "Checking pre-requisites..."
if ! command -v helm &> /dev/null; then
    echo "Error: helm CLI is required but not installed."
    exit 1
fi
if ! command -v kubectl &> /dev/null; then
    echo "Error: kubectl CLI is required but not installed."
    exit 1
fi

# Verify connection to cluster
echo "Active Kubernetes context:"
kubectl config current-context

# ==============================================================================
# 1. Installing SIG-Node Agent Sandbox Controller (v0.3.10)
# ==============================================================================
# WHY THIS IS NEEDED:
# OpenShell gateway (installed in step 2) does not manage pod lifecycles itself.
# Instead, it delegates pod provisioning to the upstream SIG-Node 'Agent Sandbox'
# controller via the 'agents.x-k8s.io' API group.
#
# These CRDs (Sandbox, SandboxClaim, etc.) are not default GKE resources. Without
# this controller and its CRDs, OpenShell will fail with K8s API 404 errors when
# trying to list or create sandboxes.
#
# We pin this to v0.3.10 to ensure we use the 'v1alpha1' API version expected
# by OpenShell v0.0.51 (newer versions graduated to v1beta1).
# ==============================================================================
echo -e "\n1. Installing SIG-Node Agent Sandbox Controller (v0.3.10)..."
kubectl apply -f "https://github.com/kubernetes-sigs/agent-sandbox/releases/download/v0.3.10/manifest.yaml"
kubectl apply -f "https://github.com/kubernetes-sigs/agent-sandbox/releases/download/v0.3.10/extensions.yaml"


echo "Ensuring target namespace '${TARGET_NAMESPACE}' exists..."
kubectl create namespace "${TARGET_NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

echo -e "\n2. Installing OpenShell Gateway in '${TARGET_NAMESPACE}'..."
helm upgrade --install openshell oci://ghcr.io/nvidia/openshell/helm-chart \
  --version 0.0.51 \
  --namespace "${TARGET_NAMESPACE}" \
  --values - <<'EOF'
server:
  disableTls: true
  auth:
    allowUnauthenticatedUsers: true
service:
  metricsPort: 0
EOF

echo -e "\n3. Installing kagent CRDs (Cluster-wide)..."
helm upgrade --install kagent-crds oci://ghcr.io/kagent-dev/kagent/helm/kagent-crds \
  --version 0.9.11 \
  --namespace "${TARGET_NAMESPACE}" || echo "kagent CRDs might already be installed, proceeding..."

echo -e "\n4. Installing kagent Controller in '${TARGET_NAMESPACE}' (Minimal configuration)..."
helm upgrade --install kagent oci://ghcr.io/kagent-dev/kagent/helm/kagent \
  --version 0.9.11 \
  --namespace "${TARGET_NAMESPACE}" \
  -f "${SCRIPT_DIR}/values-spike.yaml" \
  --set controller.openshell.enabled=true \
  --set 'controller.env[0].name=OPENSHELL_GATEWAY_URL' \
  --set-string "controller.env[0].value=dns:///openshell.${TARGET_NAMESPACE}.svc:8080" \
  --set 'controller.env[1].name=OPENSHELL_INSECURE' \
  --set-string 'controller.env[1].value=true' \
  --set controller.substrate.enabled=true

echo -e "\n✓ Orchestrator components (Sandbox Controller, OpenShell & kagent) installed!"
echo "Please wait a moment for the pods to initialize, then run:"
echo "  bash spike/manifests-hybrid/deploy.sh"
