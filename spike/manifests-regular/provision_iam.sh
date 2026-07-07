#!/usr/bin/env bash
# ==============================================================================
# 🆔 Provision GCP IAM & Workload Identity for Regular Spike
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_NAMESPACE="kube-agents-regular-spike"
KSA_NAME="platform-agent-sa"

# Load SRE variables
VARS_SH="${SCRIPT_DIR}/vars.sh"
if [ -f "$VARS_SH" ]; then
    source "$VARS_SH"
    export VARS_FILE="${VARS_SH}"
else
    echo "Error: SRE variables file not found at ${VARS_SH}."
    exit 1
fi

# 1. Create the KSA if it doesn't exist
echo "Creating GKE ServiceAccount '${KSA_NAME}' in namespace '${TARGET_NAMESPACE}'..."
kubectl create serviceaccount "${KSA_NAME}" -n "${TARGET_NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

# 2. GSA email
GSA_EMAIL="kubeagents-platform-gsa@${PROJECT_ID}.iam.gserviceaccount.com"
echo "Using GSA: ${GSA_EMAIL}"

# 3. Bind Workload Identity in GCP
echo "Binding GCP Workload Identity for ${KSA_NAME}..."
gcloud iam service-accounts add-iam-policy-binding "${GSA_EMAIL}" \
    --role="roles/iam.workloadIdentityUser" \
    --member="serviceAccount:${PROJECT_ID}.svc.id.goog[${TARGET_NAMESPACE}/${KSA_NAME}]" \
    --project="${PROJECT_ID}" \
    --quiet

# 4. Annotate the GKE ServiceAccount
echo "Annotating GKE ServiceAccount '${KSA_NAME}'..."
kubectl annotate serviceaccount "${KSA_NAME}" \
    -n "${TARGET_NAMESPACE}" \
    iam.gke.io/gcp-service-account="${GSA_EMAIL}" \
    --overwrite
