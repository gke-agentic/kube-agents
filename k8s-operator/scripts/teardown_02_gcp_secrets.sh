#!/usr/bin/env bash
# ==============================================================================
# 🧹 Step 2: Teardown Secret Manager & GKE Secrets
# ==============================================================================
# Idempotent script to clean up Kubernetes secrets and Google Secret Manager keys.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VARS_FILE="${SCRIPT_DIR}/vars.sh"

# ─── ANSI Colors ──────────────────────────────────────────────────────────────
source "${SCRIPT_DIR}/common.sh" "$@"

# ─── Configuration State Restoration ──────────────────────────────────────────
ensure_teardown_state

# ─── Confirmation Prompt ──────────────────────────────────────────────────────
confirm_action "This will permanently delete GKE platform-agent-secrets and GCP Secret Manager GEMINI_API_KEY secret." \
  "GCP Project:$PROJECT_ID" \
  "Namespace:$NAMESPACE"

gcloud config set project "$PROJECT_ID" --quiet

# ─── Step 1: Connect to GKE Cluster & Delete K8s Secret ───────────────────────
CLUSTER_EXISTS=$(cluster_exists)
if [ -n "$CLUSTER_EXISTS" ]; then
  connect_cluster || true

  # Check if Namespace exists
  NS_EXISTS=$(kubectl get namespace "${NAMESPACE}" --ignore-not-found 2>/dev/null || echo "")
  if [ -n "$NS_EXISTS" ]; then
    SECRET_EXISTS=$(kubectl get secret platform-agent-secrets -n "${NAMESPACE}" --ignore-not-found 2>/dev/null || echo "")
    if [ -n "$SECRET_EXISTS" ]; then
      echo -e "  ${C_CYAN}ℹ Deleting GKE Secret 'platform-agent-secrets' from namespace '${NAMESPACE}'...${C_RESET}"
      kubectl delete secret platform-agent-secrets -n "${NAMESPACE}" --ignore-not-found || true
      echo -e "  ${C_GREEN}✓ GKE Secret successfully deleted.${C_RESET}"
    else
      echo -e "  ${C_GREEN}✓ GKE Secret 'platform-agent-secrets' does not exist in namespace '${NAMESPACE}'.${C_RESET}"
    fi
  else
    echo -e "  ${C_GREEN}✓ Namespace '${NAMESPACE}' does not exist.${C_RESET}"
  fi
else
  echo -e "  ${C_GREEN}✓ GKE cluster '${CLUSTER_NAME}' does not exist. Skipping K8s secret deletion.${C_RESET}"
fi

# ─── Step 2: Delete Secret Manager Secret ─────────────────────────────────────
SECRET_EXISTS=$(gcloud secrets list --filter="name:GEMINI_API_KEY" --format="value(name)" --project="${PROJECT_ID}" 2>/dev/null || echo "")
if [ -n "$SECRET_EXISTS" ]; then
  echo -e "  ${C_CYAN}ℹ Deleting Secret 'GEMINI_API_KEY' from Google Secret Manager...${C_RESET}"
  gcloud secrets delete "GEMINI_API_KEY" --project="${PROJECT_ID}" --quiet || true
  echo -e "  ${C_GREEN}✓ GCP Secret 'GEMINI_API_KEY' successfully deleted.${C_RESET}"
else
  echo -e "  ${C_GREEN}✓ GCP Secret 'GEMINI_API_KEY' does not exist.${C_RESET}"
fi
