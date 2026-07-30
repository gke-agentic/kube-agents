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

BACKUP_PLAN_NAME="platform-agent-backup-plan"
init_var "PRESERVE_BACKUPS" "true" "Preserve existing backup snapshots on teardown? (true/false)"

# ─── Confirmation Prompt ──────────────────────────────────────────────────────
confirm_action "This will delete the GKE Backup Plan '${BACKUP_PLAN_NAME}'." \
  "GCP Project:$PROJECT_ID" \
  "GCP Region:$REGION"

print_step "Checking and removing GKE Backup Plan"

describe_out=""
describe_err=0
describe_out=$(gcloud beta container backup-restore backup-plans describe "$BACKUP_PLAN_NAME" \
    --location="$REGION" --project="$PROJECT_ID" 2>&1) || describe_err=$?

if [ "$describe_err" -eq 0 ]; then
  backups=$(gcloud beta container backup-restore backups list \
      --backup-plan="$BACKUP_PLAN_NAME" \
      --location="$REGION" \
      --project="$PROJECT_ID" \
      --format="value(name.basename())" 2>/dev/null || echo "")

  if [ -n "$backups" ] && is_truthy "${PRESERVE_BACKUPS}"; then
    echo -e "  ${C_YELLOW}ℹ PRESERVE_BACKUPS=true (default): Preserving GKE Backup Plan '${BACKUP_PLAN_NAME}' and its existing backup snapshots.${C_RESET}"
    echo -e "  ${C_CYAN}ℹ To delete all backup snapshots and the plan, run with PRESERVE_BACKUPS=false.${C_RESET}"
  else
    if [ "${DRY_RUN:-0}" -eq 1 ]; then
      echo -e "  ${C_GREEN}[DRY-RUN] Would delete GKE Backup Plan '${BACKUP_PLAN_NAME}'.${C_RESET}"
    else
      if [ -n "$backups" ]; then
        echo -e "  ${C_CYAN}ℹ Deleting existing backup snapshots in '${BACKUP_PLAN_NAME}'...${C_RESET}"
        count=0
        for backup in $backups; do
          if [ -n "$backup" ]; then
            echo -e "    ${C_CYAN}ℹ Triggering deletion for snapshot '${backup}' (in background)...${C_RESET}"
            (
              gcloud beta container backup-restore backups delete "$backup" \
                  --backup-plan="$BACKUP_PLAN_NAME" \
                  --location="$REGION" \
                  --project="$PROJECT_ID" \
                  --quiet || echo -e "    ${C_YELLOW}⚠ Failed to delete snapshot '${backup}'; continuing...${C_RESET}"
            ) &
            count=$((count + 1))
            if [ $((count % 5)) -eq 0 ]; then
              wait
            fi
          fi
        done
        wait
      fi

      remaining_backups=$(gcloud beta container backup-restore backups list \
          --backup-plan="$BACKUP_PLAN_NAME" \
          --location="$REGION" \
          --project="$PROJECT_ID" \
          --format="value(name.basename())" 2>/dev/null || echo "")
      if [ -n "$remaining_backups" ]; then
        echo -e "  ${C_RED}✗ Some backup snapshots could not be deleted. Remaining snapshots:${C_RESET}" >&2
        echo "$remaining_backups" | while read -r rem; do
          if [ -n "$rem" ]; then
            echo -e "    - ${rem}" >&2
          fi
        done
        echo -e "  ${C_RED}✗ Cannot delete BackupPlan '${BACKUP_PLAN_NAME}' until all snapshots are removed.${C_RESET}" >&2
        exit 1
      fi

      echo -e "  ${C_CYAN}ℹ Deleting GKE Backup Plan '${BACKUP_PLAN_NAME}'...${C_RESET}"
      gcloud beta container backup-restore backup-plans delete "$BACKUP_PLAN_NAME" \
          --location="$REGION" \
          --project="$PROJECT_ID" \
          --quiet
      echo -e "  ${C_GREEN}✓ Deleted GKE Backup Plan '${BACKUP_PLAN_NAME}'.${C_RESET}"
    fi
  fi
elif echo "$describe_out" | grep -iq "not found\|NOT_FOUND"; then
  echo -e "  ${C_GREEN}✓ GKE Backup Plan '${BACKUP_PLAN_NAME}' does not exist. Skipping.${C_RESET}"
else
  echo -e "  ${C_RED}✗ Error describing GKE Backup Plan '${BACKUP_PLAN_NAME}':${C_RESET}" >&2
  echo "$describe_out" >&2
  exit "$describe_err"
fi

echo -e "\n${C_GREEN}${C_BOLD}✓ GKE Backup Plan cleanup completed successfully!${C_RESET}"
