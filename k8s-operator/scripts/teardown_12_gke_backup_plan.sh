#!/usr/bin/env bash
# ==============================================================================
# 12. Teardown GKE Backup & Disaster Recovery Plan
# ==============================================================================
# Idempotent script to delete the Google Cloud Backup for GKE BackupPlan.
# Safe to run even if the backup plan was never created.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh" "$@"

ensure_teardown_state

# ─── Confirmation Prompt ──────────────────────────────────────────────────────
confirm_action "This will delete the GKE Backup Plan 'platform-agent-backup-plan'." \
  "GCP Project:$PROJECT_ID" \
  "GCP Region:$REGION"

print_step "Checking and removing GKE Backup Plan"

if gcloud beta container backup-restore backup-plans describe platform-agent-backup-plan \
    --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
  if [ "${DRY_RUN:-0}" -eq 1 ]; then
    echo -e "  ${C_GREEN}[DRY-RUN] Would delete GKE Backup Plan 'platform-agent-backup-plan'.${C_RESET}"
  else
    echo -e "  ${C_CYAN}ℹ Deleting existing backup snapshots in 'platform-agent-backup-plan'...${C_RESET}"
    backups=$(gcloud beta container backup-restore backups list \
        --backup-plan=platform-agent-backup-plan \
        --location="$REGION" \
        --project="$PROJECT_ID" \
        --format="value(name)" || echo "")
    for backup in $backups; do
      if [ -n "$backup" ]; then
        echo -e "    ${C_CYAN}ℹ Deleting snapshot '${backup}'...${C_RESET}"
        gcloud beta container backup-restore backups delete "$backup" \
            --backup-plan=platform-agent-backup-plan \
            --location="$REGION" \
            --project="$PROJECT_ID" \
            --quiet
      fi
    done

    echo -e "  ${C_CYAN}ℹ Deleting GKE Backup Plan 'platform-agent-backup-plan'...${C_RESET}"
    gcloud beta container backup-restore backup-plans delete platform-agent-backup-plan \
        --location="$REGION" \
        --project="$PROJECT_ID" \
        --quiet
    echo -e "  ${C_GREEN}✓ Deleted GKE Backup Plan 'platform-agent-backup-plan'.${C_RESET}"
  fi
else
  echo -e "  ${C_GREEN}✓ GKE Backup Plan 'platform-agent-backup-plan' does not exist. Skipping.${C_RESET}"
fi

echo -e "\n${C_GREEN}${C_BOLD}✓ GKE Backup Plan cleanup completed successfully!${C_RESET}"
