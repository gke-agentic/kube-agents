#!/usr/bin/env bash
# ==============================================================================
# 🆔 Provision GCP IAM & Workload Identity for Hybrid Spike
# ==============================================================================
# Auto-detects the legacy GSA, binds it to the new sandbox KSA, and annotates it.
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
KSA_NAME="openshell-sandbox"

# 1. Try to auto-detect GSA email from legacy setup
echo "Detecting GCP Service Account (GSA) email from legacy setup..."
GSA_EMAIL=$(kubectl get serviceaccount kubeagents-platform-agent -n kubeagents-system -o jsonpath='{.metadata.annotations.iam\.gke\.io/gcp-service-account}' 2>/dev/null || echo "")

if [ -z "$GSA_EMAIL" ]; then
    # Fallback to default naming convention
    GSA_EMAIL="kubeagents-platform-gsa@${PROJECT_ID}.iam.gserviceaccount.com"
    echo "Legacy setup not found or GSA annotation missing. Defaulting to: ${GSA_EMAIL}"
else
    echo "Detected GSA: ${GSA_EMAIL}"
fi

# 2. Bind Workload Identity in GCP
echo "Binding GCP Workload Identity for ${KSA_NAME}..."
gcloud iam service-accounts add-iam-policy-binding "${GSA_EMAIL}" \
    --role="roles/iam.workloadIdentityUser" \
    --member="serviceAccount:${PROJECT_ID}.svc.id.goog[${TARGET_NAMESPACE}/${KSA_NAME}]" \
    --project="${PROJECT_ID}" \
    --quiet

# 3. Annotate the GKE ServiceAccount
echo "Annotating GKE ServiceAccount '${KSA_NAME}' in namespace '${TARGET_NAMESPACE}'..."
kubectl annotate serviceaccount "${KSA_NAME}" \
    -n "${TARGET_NAMESPACE}" \
    iam.gke.io/gcp-service-account="${GSA_EMAIL}" \
    --overwrite
