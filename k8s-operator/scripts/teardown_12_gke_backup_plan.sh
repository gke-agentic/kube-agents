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

describe_out=""
describe_err=0
describe_out=$(gcloud beta container backup-restore backup-plans describe platform-agent-backup-plan \
    --location="$REGION" --project="$PROJECT_ID" 2>&1) || describe_err=$?

if [ "$describe_err" -eq 0 ]; then
  if [ "${DRY_RUN:-0}" -eq 1 ]; then
    echo -e "  ${C_GREEN}[DRY-RUN] Would delete GKE Backup Plan 'platform-agent-backup-plan'.${C_RESET}"
  else
    echo -e "  ${C_CYAN}ℹ Deleting existing backup snapshots in 'platform-agent-backup-plan'...${C_RESET}"
    backups=$(gcloud beta container backup-restore backups list \
        --backup-plan=platform-agent-backup-plan \
        --location="$REGION" \
        --project="$PROJECT_ID" \
        --format="value(name.basename())")
    for backup in $backups; do
      if [ -n "$backup" ]; then
        echo -e "    ${C_CYAN}ℹ Deleting snapshot '${backup}'...${C_RESET}"
        gcloud beta container backup-restore backups delete "$backup" \
            --backup-plan=platform-agent-backup-plan \
            --location="$REGION" \
            --project="$PROJECT_ID" \
            --quiet || echo -e "    ${C_YELLOW}⚠ Failed to delete snapshot '${backup}'; continuing...${C_RESET}"
      fi
    done

    echo -e "  ${C_CYAN}ℹ Deleting GKE Backup Plan 'platform-agent-backup-plan'...${C_RESET}"
    gcloud beta container backup-restore backup-plans delete platform-agent-backup-plan \
        --location="$REGION" \
        --project="$PROJECT_ID" \
        --quiet
    echo -e "  ${C_GREEN}✓ Deleted GKE Backup Plan 'platform-agent-backup-plan'.${C_RESET}"
  fi
elif echo "$describe_out" | grep -iq "not found\|NOT_FOUND"; then
  echo -e "  ${C_GREEN}✓ GKE Backup Plan 'platform-agent-backup-plan' does not exist. Skipping.${C_RESET}"
else
  echo -e "  ${C_RED}✗ Error describing GKE Backup Plan 'platform-agent-backup-plan':${C_RESET}" >&2
  echo "$describe_out" >&2
  exit "$describe_err"
fi

echo -e "\n${C_GREEN}${C_BOLD}✓ GKE Backup Plan cleanup completed successfully!${C_RESET}"
