#!/usr/bin/env bash
# ==============================================================================
# 🤖 Step 12: GKE Backup Plan (optional)
# ==============================================================================
# Sets up Google Cloud Backup for GKE BackupPlan for automated cluster
# and persistent volume snapshots. Skipped unless ENABLE_GKE_BACKUP_PLAN=true.
#
# Note: If BACKUP_CRON_SCHEDULE or BACKUP_RETAIN_DAYS are modified after initial
# provisioning, re-running this script automatically reconciles the existing
# backup plan in-place using 'gcloud beta container backup-restore backup-plans update'.
#
# Cost: Incurs charges based on the number of GKE pods backed up and persistent
# volume snapshot storage used. Defaults to ENABLE_GKE_BACKUP_PLAN=false.
#
# Security: Backups include Kubernetes Secrets and persistent volume data, so
# GCP IAM policies should restrict backup/restore permissions to authorized admins.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh" "$@"

# ─── Prerequisites Check ──────────────────────────────────────────────────────
print_step "Checking Local Prerequisites"
check_prereqs "gcloud"

print_step "Setting up Configuration State for GKE Backup Plan"
load_state

init_var "ENABLE_GKE_BACKUP_PLAN" "false" "Enable automated Google Cloud Backup for GKE on cluster (true/false)"
if ! is_truthy "$ENABLE_GKE_BACKUP_PLAN"; then
  echo -e "  ${C_YELLOW}ℹ Skipping GKE Backup Plan setup per user request (ENABLE_GKE_BACKUP_PLAN=${ENABLE_GKE_BACKUP_PLAN}).${C_RESET}"
  exit 0
fi

if [ "${DRY_RUN:-0}" -ne 1 ] && ! gcloud beta --help &>/dev/null; then
  print_error "The 'gcloud beta' component is required for Backup for GKE commands. Please install it (e.g., 'gcloud components install beta' or 'apt-get install google-cloud-cli-beta') and rerun."
  exit 1
fi

ACTIVE_PROJECT="$(gcloud config get-value project 2>/dev/null || echo "")"
DEFAULT_PROJECT_ID="${ACTIVE_PROJECT:-$(whoami 2>/dev/null || echo "user")}"

init_var "PROJECT_ID" "$DEFAULT_PROJECT_ID" "Enter Target GCP Project ID"
init_var "REGION" "us-east4" "Enter GKE GCP Region"
init_var "CLUSTER_NAME" "platform-agent-host" "Enter GKE Cluster Name"
init_var "BACKUP_CRON_SCHEDULE" "#!/usr/bin/env bash
# ==============================================================================
# 🤖 Step 12: GKE Backup Plan (optional)
# ==============================================================================
# Sets up Google Cloud Backup for GKE BackupPlan for automated cluster
# and persistent volume snapshots. Skipped unless ENABLE_GKE_BACKUP_PLAN=true.
#
# Note: If BACKUP_CRON_SCHEDULE or BACKUP_RETAIN_DAYS are modified after initial
# provisioning, re-running this script automatically reconciles the existing
# backup plan in-place using 'gcloud beta container backup-restore backup-plans update'.
#
# Cost: Incurs charges based on the number of GKE pods backed up and persistent
# volume snapshot storage used. Defaults to ENABLE_GKE_BACKUP_PLAN=false.
#
# Security: Backups include Kubernetes Secrets and persistent volume data, so
# GCP IAM policies should restrict backup/restore permissions to authorized admins.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/common.sh" "$@"

# ─── Prerequisites Check ──────────────────────────────────────────────────────
print_step "Checking Local Prerequisites"
check_prereqs "gcloud"

print_step "Setting up Configuration State for GKE Backup Plan"
load_state

init_var "ENABLE_GKE_BACKUP_PLAN" "false" "Enable automated Google Cloud Backup for GKE on cluster (true/false)"
if ! is_truthy "$ENABLE_GKE_BACKUP_PLAN"; then
  echo -e "  ${C_YELLOW}ℹ Skipping GKE Backup Plan setup per user request (ENABLE_GKE_BACKUP_PLAN=${ENABLE_GKE_BACKUP_PLAN}).${C_RESET}"
  exit 0
fi

if [ "${DRY_RUN:-0}" -ne 1 ] && ! gcloud beta --help < /dev/null &>/dev/null; then
  print_error "The 'gcloud beta' component is required for Backup for GKE commands. Please install it (e.g., 'gcloud components install beta' or 'apt-get install google-cloud-cli-beta') and rerun."
  exit 1
fi

ACTIVE_PROJECT="$(gcloud config get-value project 2>/dev/null || echo "")"
DEFAULT_PROJECT_ID="${ACTIVE_PROJECT:-$(whoami 2>/dev/null || echo "user")}"

init_var "PROJECT_ID" "$DEFAULT_PROJECT_ID" "Enter Target GCP Project ID"
init_var "REGION" "us-east4" "Enter GKE GCP Region"
init_var "CLUSTER_NAME" "platform-agent-host" "Enter GKE Cluster Name"

# ─── Backup Plan Configuration Validation & Prompts ───────────────────────────

calculate_cron_interval_minutes() {
  local cron="$1"
  local min hr dom mon dow
  read -r min hr dom mon dow <<< "$cron"

  # If minute is */N, interval is N minutes
  if [[ "$min" =~ ^\*/([0-9]+)$ ]]; then
    echo "${BASH_REMATCH[1]}"
    return 0
  elif [ "$min" = "*" ]; then
    echo "1"
    return 0
  elif [[ "$min" =~ ^[0-9]+$ ]]; then
    # Minute is a fixed number (e.g. 0)
    if [[ "$hr" =~ ^\*/([0-9]+)$ ]]; then
      echo "$(( BASH_REMATCH[1] * 60 ))"
      return 0
    elif [ "$hr" = "*" ]; then
      echo "60"
      return 0
    elif [[ "$hr" =~ ^[0-9]+$ ]]; then
      # Specific hour and minute (e.g. 0 2 * * *) -> Daily interval (1440 mins)
      echo "1440"
      return 0
    fi
  fi
  # Default to daily (1440 mins) for other standard schedules
  echo "1440"
}

validate_cron_schedule() {
  local cron="$1"
  if [ -z "$cron" ]; then
    echo "BACKUP_CRON_SCHEDULE cannot be empty."
    return 1
  fi
  local field_count
  field_count=$(echo "$cron" | awk '{print NF}')
  if [ "$field_count" -ne 5 ]; then
    echo "BACKUP_CRON_SCHEDULE must be a valid 5-field cron expression (e.g., '0 2 * * *'). Got ${field_count} fields: '${cron}'."
    return 1
  fi
  local min hr dom mon dow
  read -r min hr dom mon dow <<< "$cron"
  if [ "$min" = "*" ] && [ "$hr" != "*" ]; then
    echo "Minute field is '*' with specific hour '${hr}', which triggers every minute during that hour. Did you mean '0 ${hr} * * *'?"
    return 1
  fi
  local interval_mins
  interval_mins=$(calculate_cron_interval_minutes "$cron")
  if [ "$interval_mins" -lt 10 ]; then
    echo "BACKUP_CRON_SCHEDULE interval (${interval_mins}m) is invalid: Backup for GKE requires a minimum interval of 10 minutes between scheduled backups (e.g., '*/15 * * * *', '0 * * * *', or '0 2 * * *')."
    return 1
  fi
  return 0
}

validate_backup_retention() {
  local retain="$1"
  local cron="$2"
  if [[ ! "$retain" =~ ^[1-9][0-9]*$ ]]; then
    echo "BACKUP_RETAIN_DAYS must be a positive integer >= 1 (got '${retain}')."
    return 1
  fi
  local interval_mins
  interval_mins=$(calculate_cron_interval_minutes "$cron")
  local max_retain_days=$(( (interval_mins * 360) / 1440 ))
  if [ "$max_retain_days" -lt 1 ]; then
    max_retain_days=1
  fi
  if [ "$retain" -gt "$max_retain_days" ]; then
    echo "BACKUP_RETAIN_DAYS (${retain}) exceeds Backup for GKE limit: retention must be <= 360 * creation interval (max ${max_retain_days} days for interval of ${interval_mins}m with cron '${cron}')."
    return 1
  fi
  return 0
}

validate_encryption_key() {
  local key="$1"
  if [ -n "$key" ]; then
    if [[ ! "$key" =~ ^projects/[^/]+/locations/[^/]+/keyRings/[^/]+/cryptoKeys/[^/]+$ ]]; then
      echo "BACKUP_ENCRYPTION_KEY must be a valid Cloud KMS cryptoKey resource path (e.g., projects/PROJECT_ID/locations/REGION/keyRings/KEY_RING/cryptoKeys/KEY_NAME) or empty."
      return 1
    fi
  fi
  return 0
}

# BACKUP_CRON_SCHEDULE prompt & validation
DEFAULT_CRON="0 2 * * *"
if [ -n "${BACKUP_CRON_SCHEDULE:-}" ]; then
  if err_msg=$(validate_cron_schedule "$BACKUP_CRON_SCHEDULE"); then
    : # valid
  else
    if [ "${DRY_RUN:-0}" -eq 1 ] || is_ci_pipeline; then
      print_error "Invalid BACKUP_CRON_SCHEDULE in ${VARS_FILE} (or env): ${err_msg}"
      exit 1
    else
      print_warning "Invalid BACKUP_CRON_SCHEDULE in ${VARS_FILE}: ${err_msg}"
      unset BACKUP_CRON_SCHEDULE
    fi
  fi
fi

while [ -z "${BACKUP_CRON_SCHEDULE:-}" ]; do
  if [ "${DRY_RUN:-0}" -eq 1 ] || is_ci_pipeline; then
    BACKUP_CRON_SCHEDULE="$DEFAULT_CRON"
  else
    echo -ne "  ${C_CYAN}Enter GKE Backup Plan cron schedule [${C_WHITE}${DEFAULT_CRON}${C_CYAN}]: ${C_RESET}"
    read -r input_val
    BACKUP_CRON_SCHEDULE="${input_val:-$DEFAULT_CRON}"
  fi
  if err_msg=$(validate_cron_schedule "$BACKUP_CRON_SCHEDULE"); then
    save_var "BACKUP_CRON_SCHEDULE" "$BACKUP_CRON_SCHEDULE"
  else
    print_error "$err_msg"
    unset BACKUP_CRON_SCHEDULE
    if [ "${DRY_RUN:-0}" -eq 1 ] || is_ci_pipeline; then
      exit 1
    fi
  fi
done

# BACKUP_RETAIN_DAYS prompt & validation
DEFAULT_RETAIN="30"
if [ -n "${BACKUP_RETAIN_DAYS:-}" ]; then
  if err_msg=$(validate_backup_retention "$BACKUP_RETAIN_DAYS" "$BACKUP_CRON_SCHEDULE"); then
    : # valid
  else
    if [ "${DRY_RUN:-0}" -eq 1 ] || is_ci_pipeline; then
      print_error "Invalid BACKUP_RETAIN_DAYS in ${VARS_FILE} (or env): ${err_msg}"
      exit 1
    else
      print_warning "Invalid BACKUP_RETAIN_DAYS in ${VARS_FILE}: ${err_msg}"
      unset BACKUP_RETAIN_DAYS
    fi
  fi
fi

while [ -z "${BACKUP_RETAIN_DAYS:-}" ]; do
  if [ "${DRY_RUN:-0}" -eq 1 ] || is_ci_pipeline; then
    BACKUP_RETAIN_DAYS="$DEFAULT_RETAIN"
  else
    echo -ne "  ${C_CYAN}Enter backup retention in days [${C_WHITE}${DEFAULT_RETAIN}${C_CYAN}]: ${C_RESET}"
    read -r input_val
    BACKUP_RETAIN_DAYS="${input_val:-$DEFAULT_RETAIN}"
  fi
  if err_msg=$(validate_backup_retention "$BACKUP_RETAIN_DAYS" "$BACKUP_CRON_SCHEDULE"); then
    save_var "BACKUP_RETAIN_DAYS" "$BACKUP_RETAIN_DAYS"
  else
    print_error "$err_msg"
    unset BACKUP_RETAIN_DAYS
    if [ "${DRY_RUN:-0}" -eq 1 ] || is_ci_pipeline; then
      exit 1
    fi
  fi
done

BACKUP_PLAN_NAME="${CLUSTER_NAME}-backup-plan"

# BACKUP_ENCRYPTION_KEY prompt & validation
if [ "${BACKUP_ENCRYPTION_KEY+defined}" = "defined" ]; then
  if err_msg=$(validate_encryption_key "$BACKUP_ENCRYPTION_KEY"); then
    : # valid
  else
    if [ "${DRY_RUN:-0}" -eq 1 ] || is_ci_pipeline; then
      print_error "Invalid BACKUP_ENCRYPTION_KEY in ${VARS_FILE} (or env): ${err_msg}"
      exit 1
    else
      print_warning "Invalid BACKUP_ENCRYPTION_KEY in ${VARS_FILE}: ${err_msg}"
      unset BACKUP_ENCRYPTION_KEY
    fi
  fi
fi

if [ -z "${BACKUP_ENCRYPTION_KEY+defined}" ]; then
  while true; do
    if [ "${DRY_RUN:-0}" -eq 1 ] || is_ci_pipeline; then
      BACKUP_ENCRYPTION_KEY=""
    else
      echo -ne "  ${C_CYAN}Enter optional KMS encryption key for backups (leave empty for Google-managed) [${C_WHITE}${C_CYAN}]: ${C_RESET}"
      read -r input_val
      BACKUP_ENCRYPTION_KEY="${input_val:-}"
    fi
    if err_msg=$(validate_encryption_key "$BACKUP_ENCRYPTION_KEY"); then
      save_var "BACKUP_ENCRYPTION_KEY" "$BACKUP_ENCRYPTION_KEY"
      break
    else
      print_error "$err_msg"
      if [ "${DRY_RUN:-0}" -eq 1 ] || is_ci_pipeline; then
        exit 1
      fi
    fi
  done
fi

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
      --update-addons=BackupRestore=ENABLED \
      --quiet
}

# Step 3: Ensure GKE Backup Plan
# Contract: Sets BACKUP_PLAN_EXISTS="true"|"false" as a global side-effect read by execute_backup_plan().
verify_backup_plan() {
  local out
  local err=0
  out=$(gcloud beta container backup-restore backup-plans describe "$BACKUP_PLAN_NAME" \
      --location="$REGION" --project="$PROJECT_ID" 2>&1) || err=$?
  if [ "$err" -eq 0 ]; then
    BACKUP_PLAN_EXISTS="true"
    local curr_retain curr_cron curr_enc curr_paused
    curr_retain=$(echo "$out" | grep -E "^[[:space:]]*backupRetainDays:" | head -n1 | sed -E 's/^[[:space:]]*backupRetainDays:[[:space:]]*//' | tr -d "'\"[:space:]")
    curr_cron=$(echo "$out" | grep -E "^[[:space:]]*cronSchedule:" | head -n1 | sed -E 's/^[[:space:]]*cronSchedule:[[:space:]]*//' | sed -E "s/^['\"]//;s/['\"]$//")
    curr_enc=$(echo "$out" | grep -E "^[[:space:]]*gcpKmsEncryptionKey:" | head -n1 | sed -E 's/^[[:space:]]*gcpKmsEncryptionKey:[[:space:]]*//' | tr -d "'\"[:space:]")
    curr_paused=$(echo "$out" | grep -iE "^[[:space:]]*(paused|deactivated):" | head -n1 | awk '{print tolower($2)}' | tr -d "'\"[:space:]")

    local enc_matches="true"
    if [ -n "${BACKUP_ENCRYPTION_KEY:-}" ]; then
      if [ "$curr_enc" != "$BACKUP_ENCRYPTION_KEY" ]; then
        enc_matches="false"
      fi
    elif [ -n "$curr_enc" ]; then
      echo -e "  ${C_YELLOW}⚠ Existing BackupPlan '${BACKUP_PLAN_NAME}' has CMEK encryption enabled. Clearing CMEK key via empty BACKUP_ENCRYPTION_KEY is unsupported; preserving existing key.${C_RESET}"
    fi

    if [ "$curr_paused" = "true" ]; then
      echo -e "  ${C_YELLOW}ℹ Existing BackupPlan '${BACKUP_PLAN_NAME}' is paused/deactivated. Will unpause.${C_RESET}"
      return 1
    fi

    if [ "$curr_retain" = "$BACKUP_RETAIN_DAYS" ] && [ "$curr_cron" = "$BACKUP_CRON_SCHEDULE" ] && [ "$enc_matches" = "true" ]; then
      return 0
    else
      echo -e "  ${C_YELLOW}ℹ Configuration drift detected on existing BackupPlan '${BACKUP_PLAN_NAME}'. Will update.${C_RESET}"
      return 1
    fi
  elif [ "${DRY_RUN:-0}" -eq 1 ] || echo "$out" | grep -iq "not found"; then
    BACKUP_PLAN_EXISTS="false"
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

  if [ "${BACKUP_PLAN_EXISTS:-false}" = "true" ]; then
    print_info "Updating GKE Backup Plan '${BACKUP_PLAN_NAME}' to reconcile configuration drift..."
    gcloud beta container backup-restore backup-plans update "$BACKUP_PLAN_NAME" \
        --project="$PROJECT_ID" \
        --location="$REGION" \
        --cron-schedule="$BACKUP_CRON_SCHEDULE" \
        --backup-retain-days="$BACKUP_RETAIN_DAYS" \
        --no-paused \
        "${enc_flag[@]}" \
        --no-async \
        --quiet
  else
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
        --no-paused \
        "${enc_flag[@]}" \
        --no-async \
        --quiet
  fi
}

# ─── Execution Pipeline ───────────────────────────────────────────────────────
run_step "1. Ensure Backup for GKE API enabled" verify_backup_api execute_backup_api 10
run_step "2. Ensure Backup for GKE enabled on cluster" verify_cluster_backup_enabled execute_cluster_backup_enabled 5
run_step "3. Ensure GKE Backup Plan" verify_backup_plan execute_backup_plan 0

# ─── Conclusion Checklist ─────────────────────────────────────────────────────
echo -e "\n${C_GREEN}${C_BOLD}✓ GKE Backup Plan '${BACKUP_PLAN_NAME}' provisioned successfully!${C_RESET}"
" "Enter GKE Backup Plan cron schedule"
if [ -n "${BACKUP_CRON_SCHEDULE:-}" ]; then
  field_count=$(echo "$BACKUP_CRON_SCHEDULE" | awk '{print NF}')
  if [ "$field_count" -ne 5 ]; then
    print_error "BACKUP_CRON_SCHEDULE must be a valid 5-field cron expression (e.g., '0 2 * * *'). Got: '${BACKUP_CRON_SCHEDULE}'"
    exit 1
  fi
fi
init_var "BACKUP_RETAIN_DAYS" "30" "Enter backup retention in days"
BACKUP_PLAN_NAME="${CLUSTER_NAME}-backup-plan"
init_var "BACKUP_ENCRYPTION_KEY" "" "Enter optional KMS encryption key for backups (leave empty for Google-managed)"
if [ -n "${BACKUP_ENCRYPTION_KEY:-}" ]; then
  if [[ ! "${BACKUP_ENCRYPTION_KEY}" =~ ^projects/[^/]+/locations/[^/]+/keyRings/[^/]+/cryptoKeys/[^/]+$ ]]; then
    print_error "BACKUP_ENCRYPTION_KEY must be a valid Cloud KMS cryptoKey resource path (e.g., projects/PROJECT_ID/locations/REGION/keyRings/KEY_RING/cryptoKeys/KEY_NAME) or empty."
    exit 1
  fi
fi

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
      --update-addons=BackupRestore=ENABLED \
      --quiet
}

# Step 3: Ensure GKE Backup Plan
# Contract: Sets BACKUP_PLAN_EXISTS="true"|"false" as a global side-effect read by execute_backup_plan().
verify_backup_plan() {
  local out
  local err=0
  out=$(gcloud beta container backup-restore backup-plans describe "$BACKUP_PLAN_NAME" \
      --location="$REGION" --project="$PROJECT_ID" 2>&1) || err=$?
  if [ "$err" -eq 0 ]; then
    BACKUP_PLAN_EXISTS="true"
    local curr_retain curr_cron curr_enc
    curr_retain=$(echo "$out" | grep -E "^[[:space:]]*backupRetainDays:" | head -n1 | sed -E 's/^[[:space:]]*backupRetainDays:[[:space:]]*//' | tr -d "'\"[:space:]")
    curr_cron=$(echo "$out" | grep -E "^[[:space:]]*cronSchedule:" | head -n1 | sed -E 's/^[[:space:]]*cronSchedule:[[:space:]]*//' | sed -E "s/^['\"]//;s/['\"]$//")
    curr_enc=$(echo "$out" | grep -E "^[[:space:]]*gcpKmsEncryptionKey:" | head -n1 | sed -E 's/^[[:space:]]*gcpKmsEncryptionKey:[[:space:]]*//' | tr -d "'\"[:space:]")

    local enc_matches="true"
    if [ -n "${BACKUP_ENCRYPTION_KEY:-}" ]; then
      if [ "$curr_enc" != "$BACKUP_ENCRYPTION_KEY" ]; then
        enc_matches="false"
      fi
    elif [ -n "$curr_enc" ]; then
      echo -e "  ${C_YELLOW}⚠ Existing BackupPlan '${BACKUP_PLAN_NAME}' has CMEK encryption enabled. Clearing CMEK key via empty BACKUP_ENCRYPTION_KEY is unsupported; preserving existing key.${C_RESET}"
    fi

    if [ "$curr_retain" = "$BACKUP_RETAIN_DAYS" ] && [ "$curr_cron" = "$BACKUP_CRON_SCHEDULE" ] && [ "$enc_matches" = "true" ]; then
      return 0
    else
      echo -e "  ${C_YELLOW}ℹ Configuration drift detected on existing BackupPlan '${BACKUP_PLAN_NAME}'. Will update.${C_RESET}"
      return 1
    fi
  elif [ "${DRY_RUN:-0}" -eq 1 ] || echo "$out" | grep -iq "not found"; then
    BACKUP_PLAN_EXISTS="false"
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

  if [ "${BACKUP_PLAN_EXISTS:-false}" = "true" ]; then
    print_info "Updating GKE Backup Plan '${BACKUP_PLAN_NAME}' to reconcile configuration drift..."
    gcloud beta container backup-restore backup-plans update "$BACKUP_PLAN_NAME" \
        --project="$PROJECT_ID" \
        --location="$REGION" \
        --cron-schedule="$BACKUP_CRON_SCHEDULE" \
        --backup-retain-days="$BACKUP_RETAIN_DAYS" \
        --no-paused \
        "${enc_flag[@]}" \
        --no-async \
        --quiet
  else
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
        --no-paused \
        "${enc_flag[@]}" \
        --no-async \
        --quiet
  fi
}

# ─── Execution Pipeline ───────────────────────────────────────────────────────
run_step "1. Ensure Backup for GKE API enabled" verify_backup_api execute_backup_api 10
run_step "2. Ensure Backup for GKE enabled on cluster" verify_cluster_backup_enabled execute_cluster_backup_enabled 5
run_step "3. Ensure GKE Backup Plan" verify_backup_plan execute_backup_plan 0

# ─── Conclusion Checklist ─────────────────────────────────────────────────────
echo -e "\n${C_GREEN}${C_BOLD}✓ GKE Backup Plan '${BACKUP_PLAN_NAME}' provisioned successfully!${C_RESET}"
