#!/usr/bin/env bash
# ==============================================================================
# 🤖 Install kagent Controller (Regular Deployment Spike)
# ==============================================================================
# This script installs the kagent Controller into the cluster.
# Substrate and OpenShell are disabled as we are running standard deployments.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_NAMESPACE="kube-agents-regular-spike"

echo "Checking pre-requisites..."
if ! command -v helm &> /dev/null; then
    echo "Error: helm CLI is required but not installed."
    exit 1
fi
if ! command -v kubectl &> /dev/null; then
    echo "Error: kubectl CLI is required but not installed."
    exit 1
fi

# Load SRE variables
VARS_SH="${SCRIPT_DIR}/vars.sh"
if [ -f "$VARS_SH" ]; then
    source "$VARS_SH"
else
    echo "Error: SRE variables file not found at ${VARS_SH}."
    exit 1
fi

echo "Connecting to cluster ${CLUSTER_NAME} in ${REGION}..."
gcloud container clusters get-credentials "${CLUSTER_NAME}" --region "${REGION}" --project "${PROJECT_ID}"

# Verify connection to cluster
echo "Active Kubernetes context:"
kubectl config current-context

echo "Ensuring target namespace '${TARGET_NAMESPACE}' exists..."
kubectl create namespace "${TARGET_NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

echo -e "\n1. Installing kagent CRDs (Cluster-wide)..."
helm upgrade --install kagent-crds oci://ghcr.io/kagent-dev/kagent/helm/kagent-crds \
  --version 0.9.11 \
  --namespace "${TARGET_NAMESPACE}" || echo "kagent CRDs might already be installed, proceeding..."

echo -e "\n2. Installing kagent Controller in '${TARGET_NAMESPACE}' (Regular configuration)..."
helm upgrade --install kagent oci://ghcr.io/kagent-dev/kagent/helm/kagent \
  --version 0.9.11 \
  --namespace "${TARGET_NAMESPACE}" \
  -f "${SCRIPT_DIR}/values-spike.yaml" \
  --set controller.openshell.enabled=false \
  --set controller.substrate.enabled=false

echo -e "\n✓ Orchestrator components (kagent) installed!"
echo "Please wait a moment for the pods to initialize, then run:"
echo "  bash spike/manifests-regular/deploy.sh"
