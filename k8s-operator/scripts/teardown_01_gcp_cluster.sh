#!/usr/bin/env bash
# ==============================================================================
# 🧹 Step 1: Teardown GKE Cluster & Local State
# ==============================================================================
# Idempotent script to clean up the GKE Standard Cluster and local state files.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VARS_FILE="${SCRIPT_DIR}/vars.sh"

# ─── ANSI Colors ──────────────────────────────────────────────────────────────
source "${SCRIPT_DIR}/common.sh" "$@"

# ─── Configuration State Restoration ──────────────────────────────────────────
ensure_teardown_state

# ─── Confirmation Prompt ──────────────────────────────────────────────────────
confirm_action "This will permanently delete GKE cluster '$CLUSTER_NAME' and local configuration state vars.sh." \
  "GCP Project:$PROJECT_ID" \
  "Region:$REGION" \
  "GKE Cluster:$CLUSTER_NAME"

gcloud config set project "$PROJECT_ID" --quiet

# ─── Step 1: Delete GKE Cluster ───────────────────────────────────────────────
CLUSTER_EXISTS=$(cluster_exists)
if [ -n "$CLUSTER_EXISTS" ]; then
  echo -e "  ${C_CYAN}ℹ Deleting GKE Standard Cluster '$CLUSTER_NAME' in region '$REGION'...${C_RESET}"
  echo -e "    ${C_YELLOW}Note: This takes approximately 5-8 minutes in Google Cloud...${C_RESET}"
  if [ "${DRY_RUN:-0}" -eq 1 ]; then
    echo -e "  ${C_GREEN}[DRY-RUN] Would delete GKE cluster '${CLUSTER_NAME}' in region '${REGION}'.${C_RESET}"
  else
    gcloud container clusters delete "$CLUSTER_NAME" --region="$REGION" --project="${PROJECT_ID}" --quiet
    echo -e "  ${C_GREEN}✓ GKE Cluster '$CLUSTER_NAME' successfully deleted.${C_RESET}"
  fi
else
  echo -e "  ${C_GREEN}✓ GKE Cluster '$CLUSTER_NAME' does not exist.${C_RESET}"
fi

# ─── Step 1.5: Delete GCS Lock Bucket ─────────────────────────────────────────
LOCK_BUCKET="${PROJECT_ID}-kube-agents-lock"
if gcloud storage buckets describe "gs://${LOCK_BUCKET}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo -e "  ${C_CYAN}ℹ Deleting GCS lock bucket: gs://${LOCK_BUCKET}...${C_RESET}"
  if [ "${DRY_RUN:-0}" -eq 1 ]; then
    echo -e "  ${C_GREEN}[DRY-RUN] Would delete GCS bucket gs://${LOCK_BUCKET} recursively.${C_RESET}"
  else
    # Force delete all objects and the bucket itself
    gcloud storage rm -r "gs://${LOCK_BUCKET}" --project="${PROJECT_ID}" --quiet || true
    echo -e "  ${C_GREEN}✓ GCS lock bucket successfully deleted.${C_RESET}"
  fi
else
  echo -e "  ${C_GREEN}✓ GCS lock bucket 'gs://${LOCK_BUCKET}' does not exist.${C_RESET}"
fi


# ─── Step 2: Clean up Local State Files ───────────────────────────────────────
if [ -f "$VARS_FILE" ]; then
  if [ "${DRY_RUN:-0}" -eq 1 ]; then
    echo -e "  ${C_GREEN}[DRY-RUN] Would delete the local state file vars.sh.${C_RESET}"
  else
    if [ "$NO_CONFIRM" -ne 1 ]; then
      echo -ne "  ${C_CYAN}Do you want to delete the local state file vars.sh? (y/N): ${C_RESET}"
      read -r -n 1 REMOVE_VARS || true
      echo
    else
      REMOVE_VARS="y"
    fi
    if [[ ${REMOVE_VARS:-n} =~ ^[Yy]$ ]]; then
      rm -f "$VARS_FILE"
      echo -e "  ${C_GREEN}✓ Deleted ${VARS_FILE}${C_RESET}"
    else
      echo -e "  ${C_GREEN}✓ Kept ${VARS_FILE} for subsequent provisioning.${C_RESET}"
    fi
  fi
fi
