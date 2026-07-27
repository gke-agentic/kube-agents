#!/usr/bin/env bash
# ==============================================================================
# 12. GKE Backup & Disaster Recovery Provisioning
# ==============================================================================
# Sets up Google Cloud Backup for GKE BackupPlan for automated cluster
# and persistent volume snapshots.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh" "$@"

print_step "Setting up Configuration State for GKE Backup Plan"
load_state

ACTIVE_PROJECT="$(gcloud config get-value project 2>/dev/null || echo "")"
DEFAULT_PROJECT_ID="${ACTIVE_PROJECT:-$(whoami 2>/dev/null || echo "user")}"

init_var "PROJECT_ID" "$DEFAULT_PROJECT_ID" "Enter Target GCP Project ID"
init_var "REGION" "us-east4" "Enter GKE GCP Region"
init_var "CLUSTER_NAME" "platform-agent-host" "Enter GKE Cluster Name"
init_var "ENABLE_GKE_BACKUP_PLAN" "true" "Enable automated Google Cloud Backup for GKE on cluster (true/false)"

# ─── Interactive Confirmation ─────────────────────────────────────────────────
if ! is_truthy "$ENABLE_GKE_BACKUP_PLAN"; then
  echo -e "  ${C_YELLOW}ℹ Skipping GKE Backup Plan setup per user request (ENABLE_GKE_BACKUP_PLAN=${ENABLE_GKE_BACKUP_PLAN}).${C_RESET}"
  exit 0
fi

# ─── Step Implementations ─────────────────────────────────────────────────────

# Step 1: Connect kubectl
verify_kubeconfig() {
  local current_ctx
  current_ctx=$(kubectl config current-context 2>/dev/null || echo "")
  [[ "$current_ctx" == *"${PROJECT_ID}"* && "$current_ctx" == *"${CLUSTER_NAME}"* ]]
}
execute_kubeconfig() {
  connect_cluster
}

# Step 2: Ensure GKE Backup Plan
verify_backup_plan() {
  # Always return false so that schedule or retention updates are applied idempotently on every run
  return 1
}
execute_backup_plan() {
  if gcloud beta container backup-restore backup-plans describe platform-agent-backup-plan \
      --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
    print_info "Updating existing GKE Backup Plan 'platform-agent-backup-plan'..."
    gcloud beta container backup-restore backup-plans update platform-agent-backup-plan \
        --project="$PROJECT_ID" \
        --location="$REGION" \
        --cron-schedule="0 2 * * *" \
        --backup-retain-days=30 \
        --quiet
  else
    print_info "Creating default GKE Backup Plan 'platform-agent-backup-plan'..."
    gcloud beta container backup-restore backup-plans create platform-agent-backup-plan \
        --project="$PROJECT_ID" \
        --location="$REGION" \
        --cluster="projects/${PROJECT_ID}/locations/${REGION}/clusters/${CLUSTER_NAME}" \
        --all-namespaces \
        --include-volume-data \
        --cron-schedule="0 2 * * *" \
        --backup-retain-days=30 \
        --quiet
  fi
}

# ─── Execution Pipeline ───────────────────────────────────────────────────────
run_step "1. Connect kubectl" verify_kubeconfig execute_kubeconfig 0
run_step "2. Ensure GKE Backup Plan" verify_backup_plan execute_backup_plan 0

# ─── Conclusion Checklist ─────────────────────────────────────────────────────
echo -e "\n${C_GREEN}${C_BOLD}✓ GKE Backup Plan 'platform-agent-backup-plan' provisioned successfully!${C_RESET}"
