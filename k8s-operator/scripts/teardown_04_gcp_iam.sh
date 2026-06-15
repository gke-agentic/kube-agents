#!/usr/bin/env bash
# ==============================================================================
# 🧹 Step 4: Teardown Agent GCP Workload Identity & AI Permissions
# ==============================================================================
# Idempotent script to remove Vertex AI and GKE permissions and Workload
# Identity bindings from the Agent GSA.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VARS_FILE="${SCRIPT_DIR}/vars.sh"

# ─── ANSI Colors ──────────────────────────────────────────────────────────────
source "${SCRIPT_DIR}/common.sh" "$@"

# ─── Configuration State Restoration ──────────────────────────────────────────
ensure_teardown_state

# ─── Confirmation Prompt ──────────────────────────────────────────────────────
confirm_action "This will remove GSA permissions and Workload Identity bindings for the Agent." \
  "GCP Project:$PROJECT_ID" \
  "Agent GSA:$GSA_NAME" \
  "Namespace:$NAMESPACE" \
  "K8s SA:$KSA_NAME"

gcloud config set project "$PROJECT_ID" --quiet

# ─── Step 1: Clean up Workload Identity Binding and IAM Roles ─────────────────
gsa_email="${GSA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# Check if GSA exists first
if gcloud iam service-accounts describe "${gsa_email}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo -e "  ${C_CYAN}ℹ Removing Workload Identity Policy Binding...${C_RESET}"
  wi_member="serviceAccount:${PROJECT_ID}.svc.id.goog[${NAMESPACE}/${KSA_NAME}]"
  gcloud iam service-accounts remove-iam-policy-binding "${gsa_email}" \
      --role="roles/iam.workloadIdentityUser" \
      --member="${wi_member}" \
      --project="${PROJECT_ID}" \
      --quiet || true

  echo -e "  ${C_CYAN}ℹ Removing Vertex AI User Role...${C_RESET}"
  gcloud projects remove-iam-policy-binding "${PROJECT_ID}" \
      --member="serviceAccount:${gsa_email}" \
      --role="roles/aiplatform.user" \
      --quiet || true

  echo -e "  ${C_CYAN}ℹ Removing Container Cluster Viewer Role...${C_RESET}"
  gcloud projects remove-iam-policy-binding "${PROJECT_ID}" \
      --member="serviceAccount:${gsa_email}" \
      --role="roles/container.clusterViewer" \
      --quiet || true
  
  echo -e "  ${C_GREEN}✓ Agent GCP IAM bindings successfully removed.${C_RESET}"
else
  echo -e "  ${C_GREEN}✓ GSA '${gsa_email}' does not exist. Skipping IAM policy removals.${C_RESET}"
fi
