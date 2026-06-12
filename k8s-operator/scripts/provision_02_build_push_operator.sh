#!/usr/bin/env bash
# ==============================================================================
# 🤖 Step 2: Build and Push Operator Image via Cloud Build
# ==============================================================================
# Idempotent script to build the manager container image and push it to
# Google Artifact Registry.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "$SCRIPT_DIR" == */scripts ]]; then
  OPERATOR_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
else
  OPERATOR_DIR="${SCRIPT_DIR}"
fi
VARS_FILE="${SCRIPT_DIR}/vars.sh"

# ─── ANSI Colors ──────────────────────────────────────────────────────────────
C_CYAN='\033[96m'
C_GREEN='\033[92m'
C_YELLOW='\033[93m'
C_MAGENTA='\033[95m'
C_BLUE='\033[94m'
C_RED='\033[91m'
C_RESET='\033[0m'
C_BOLD='\033[1m'
C_WHITE='\033[97m'

# ─── UI Helpers ───────────────────────────────────────────────────────────────
print_step() {
  echo -e "\n${C_MAGENTA}${C_BOLD}>>>  $1  <<<${C_RESET}"
}

print_success() {
  echo -e "  ${C_GREEN}✓ $1${C_RESET}"
}

print_info() {
  echo -e "  ${C_CYAN}ℹ $1${C_RESET}"
}

print_error() {
  echo -e "  ${C_RED}✗ $1${C_RESET}"
}

# ─── Argument Parsing ─────────────────────────────────────────────────────────
DRY_RUN=0
FORCE_BUILD=0
while [[ "$#" -gt 0 ]]; do
  case $1 in
    --dry-run) DRY_RUN=1 ;;
    --force-build) FORCE_BUILD=1 ;;
  esac
  shift
done

# ─── Configuration & State Restoration ────────────────────────────────────────
if [ ! -f "$VARS_FILE" ]; then
  print_error "Configuration state file $VARS_FILE not found."
  print_error "Please run 01_provision_cluster_and_gcp.sh first to initialize configuration."
  exit 1
fi

source "$VARS_FILE"

# ─── Prerequisites Check ──────────────────────────────────────────────────────
print_step "Checking Local Prerequisites"
PREREQS=("gcloud" "make")
for cmd in "${PREREQS[@]}"; do
  echo -ne "  ${C_CYAN}Checking for $cmd... ${C_RESET}"
  if command -v "$cmd" &> /dev/null; then
    echo -e "✅"
  else
    echo -e "❌"
    print_error "$cmd is required but not installed. Please install it and rerun."
    exit 1
  fi
done

# ─── Step Runner Framework ────────────────────────────────────────────────────
run_step() {
  local name=$1
  local verify_func=$2
  local execute_func=$3
  
  print_step "$name"
  echo -e "  ${C_CYAN}Verifying current GCP state...${C_RESET}"
  
  if $verify_func; then
    print_success "Already completed: $name"
    return 0
  fi
  
  if [ "$DRY_RUN" -eq 1 ]; then
    print_info "[DRY-RUN] Would execute: $name"
    return 0
  fi

  print_info "Executing action..."
  if $execute_func; then
    print_success "Successfully executed."
  else
    print_error "Failed to execute step: $name"
    exit 1
  fi
}

# ─── Step Implementations ─────────────────────────────────────────────────────

# Step: Build & Push Operator Image
verify_operator_image() {
  if [ "$FORCE_BUILD" -eq 1 ]; then
    return 1
  fi
  gcloud artifacts docker images list "$REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/kube-agents-operator" --project="$PROJECT_ID" --filter="TAGS:latest" --format="value(image)" 2>/dev/null | grep -q "kube-agents-operator"
}

execute_operator_image() {
  print_info "Building operator container via Google Cloud Build..."
  gcloud builds submit \
      --tag "$REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/kube-agents-operator:latest" \
      --project "$PROJECT_ID" \
      "${OPERATOR_DIR}"
}

# ─── Execution Pipeline ───────────────────────────────────────────────────────
run_step "Build and Push Operator Image" verify_operator_image execute_operator_image
