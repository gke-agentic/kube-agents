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

# ─── Prerequisites Check ──────────────────────────────────────────────────────
print_step "Checking Local Prerequisites"
check_prereqs "gcloud"

print_step "Setting up Configuration State for GKE Backup Plan"
load_state

init_var "ENABLE_GKE_BACKUP_PLAN" "true" "Enable automated Google Cloud Backup for GKE on cluster (true/false)"
if ! is_truthy "$ENABLE_GKE_BACKUP_PLAN"; then
  echo -e "  ${C_YELLOW}ℹ Skipping GKE Backup Plan setup per user request (ENABLE_GKE_BACKUP_PLAN=${ENABLE_GKE_BACKUP_PLAN}).${C_RESET}"
  exit 0
fi

ACTIVE_PROJECT="$(gcloud config get-value project 2>/dev/null || echo "")"
DEFAULT_PROJECT_ID="${ACTIVE_PROJECT:-$(whoami 2>/dev/null || echo "user")}"

init_var "PROJECT_ID" "$DEFAULT_PROJECT_ID" "Enter Target GCP Project ID"
init_var "REGION" "us-east4" "Enter GKE GCP Region"
init_var "CLUSTER_NAME" "platform-agent-host" "Enter GKE Cluster Name"
init_var "BACKUP_CRON_SCHEDULE" "0 2 * * *" "Enter GKE Backup Plan cron schedule"
init_var "BACKUP_RETAIN_DAYS" "30" "Enter backup retention in days"
BACKUP_PLAN_NAME="platform-agent-backup-plan"
init_var "BACKUP_ENCRYPTION_KEY" "" "Enter optional KMS encryption key for backups (leave empty for Google-managed)"
init_var "PRESERVE_BACKUPS" "true" "Preserve existing backup snapshots on teardown? (true/false)"


# ─── Step Implementations ─────────────────────────────────────────────────────

# Step 1: Ensure Backup for GKE API is enabled
verify_backup_api() {
  gcloud services list --enabled --project="$PROJECT_ID" --format="value(config.name)" 2>/dev/null | grep -q 'gkebackup.googleapis.com'
}
execute_backup_api() {
  print_info "Enabling Backup for GKE API (gkebackup.googleapis.com)..."
  gcloud services enable gkebackup.googleapis.com --project="$PROJECT_ID"
}

# Step 2: Ensure Backup for GKE is enabled on cluster
verify_cluster_backup_enabled() {
  gcloud container clusters describe "$CLUSTER_NAME" \
      --location="$REGION" --project="$PROJECT_ID" \
      --format="value(addonsConfig.gkeBackupAgentConfig.enabled)" 2>/dev/null | grep -iq "True"
}
execute_cluster_backup_enabled() {
  print_info "Enabling Backup for GKE on cluster '${CLUSTER_NAME}'..."
  gcloud container clusters update "$CLUSTER_NAME" \
      --location="$REGION" \
      --project="$PROJECT_ID" \
      --enable-gke-backup \
      --quiet
}

# Step 3: Ensure GKE Backup Plan
verify_backup_plan() {
  local out
  local err=0
  out=$(gcloud beta container backup-restore backup-plans describe "$BACKUP_PLAN_NAME" \
      --location="$REGION" --project="$PROJECT_ID" 2>&1) || err=$?
  if [ "$err" -eq 0 ]; then
    return 0
  elif echo "$out" | grep -iq "not found\|NOT_FOUND"; then
    return 1
  else
    echo -e "  ${C_RED}✗ Error checking GKE Backup Plan '${BACKUP_PLAN_NAME}':${C_RESET}" >&2
    echo "$out" >&2
    exit "$err"
  fi
}
execute_backup_plan() {
  local enc_flag=()
  if [ -n "${BACKUP_ENCRYPTION_KEY:-}" ]; then
    enc_flag=("--encryption-key=${BACKUP_ENCRYPTION_KEY}")
  fi

  print_info "Creating default GKE Backup Plan '${BACKUP_PLAN_NAME}'..."
  gcloud beta container backup-restore backup-plans create "$BACKUP_PLAN_NAME" \
      --project="$PROJECT_ID" \
      --location="$REGION" \
      --cluster="projects/${PROJECT_ID}/locations/${REGION}/clusters/${CLUSTER_NAME}" \
      --selected-namespaces="${NAMESPACE:-kubeagents-system}" \
      --include-secrets \
      --include-volume-data \
      --cron-schedule="$BACKUP_CRON_SCHEDULE" \
      --backup-retain-days="$BACKUP_RETAIN_DAYS" \
      "${enc_flag[@]}" \
      --quiet
}

# ─── Execution Pipeline ───────────────────────────────────────────────────────
run_step "1. Ensure Backup for GKE API enabled" verify_backup_api execute_backup_api 10
run_step "2. Ensure Backup for GKE enabled on cluster" verify_cluster_backup_enabled execute_cluster_backup_enabled 5
run_step "3. Ensure GKE Backup Plan" verify_backup_plan execute_backup_plan 0

# ─── Conclusion Checklist ─────────────────────────────────────────────────────
echo -e "\n${C_GREEN}${C_BOLD}✓ GKE Backup Plan '${BACKUP_PLAN_NAME}' provisioned successfully!${C_RESET}"
