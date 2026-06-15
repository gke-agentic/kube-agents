#!/usr/bin/env bash
# ==============================================================================
# 🤖 Step 5: Agent GCP Workload Identity & AI Permissions
# ==============================================================================
# Idempotent script for granting AI and Workload Identity permissions to the 
# Agent GSA, allowing the Kubernetes Pods to authenticate and call Gemini.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VARS_FILE="${SCRIPT_DIR}/vars.sh"

# ─── ANSI Colors ──────────────────────────────────────────────────────────────
C_CYAN='\033[96m'
C_GREEN='\033[92m'
C_YELLOW='\033[93m'
C_MAGENTA='\033[95m'
C_RED='\033[91m'
C_RESET='\033[0m'
C_BOLD='\033[1m'
C_WHITE='\033[97m'

# ─── UI Helpers ───────────────────────────────────────────────────────────────
print_step() { echo -e "\n${C_MAGENTA}${C_BOLD}>>>  $1  <<<${C_RESET}"; }
print_success() { echo -e "  ${C_GREEN}✓ $1${C_RESET}"; }
print_info() { echo -e "  ${C_CYAN}ℹ $1${C_RESET}"; }
print_error() { echo -e "  ${C_RED}✗ $1${C_RESET}"; }

wait_for_a_bit() {
  local seconds=$1
  local msg=$2
  local spinner=( "⠋" "⠙" "⠹" "⠸" "⠼" "⠴" "⠦" "⠧" "⠇" "⠏" )
  echo -ne "  ${C_YELLOW}${msg} (${seconds}s)...  "
  tput civis 2>/dev/null || true
  for (( i=0; i<seconds*10; i++ )); do
    local idx=$(( i % 10 ))
    echo -ne "\b${spinner[$idx]}"
    sleep 0.1
  done
  echo -ne "\b ${C_RESET}\n"
  tput cnorm 2>/dev/null || true
}

cleanup() { tput cnorm 2>/dev/null || true; }
trap cleanup EXIT

# ─── Argument Parsing ─────────────────────────────────────────────────────────
DRY_RUN=0
while [[ "$#" -gt 0 ]]; do
  case $1 in --dry-run) DRY_RUN=1 ;; esac
  shift
done

# ─── Configuration & State Restoration ────────────────────────────────────────
print_step "Setting up Configuration State for Agent Identity"

if [ ! -f "$VARS_FILE" ]; then
  echo "# SRE Sourced Variables for GKE & GCP Setup" > "$VARS_FILE"
fi
source "$VARS_FILE"

init_var() {
  local var_name=$1
  local default_val=$2
  local prompt_msg=$3
  if [ -z "${!var_name}" ]; then
    echo -ne "  ${C_CYAN}${prompt_msg} [${C_WHITE}${default_val}${C_CYAN}]: ${C_RESET}"
    read -r input_val
    local final_val="${input_val:-$default_val}"
    export "${var_name}=${final_val}"
    echo "export ${var_name}=\"${final_val}\"" >> "$VARS_FILE"
  fi
}

ACTIVE_PROJECT="$(gcloud config get-value project 2>/dev/null || echo "")"
DEFAULT_PROJECT_ID="${ACTIVE_PROJECT:-$(whoami 2>/dev/null || echo "user")}"

init_var "PROJECT_ID" "$DEFAULT_PROJECT_ID" "Enter Target GCP Project ID"
init_var "NAMESPACE" "agent-system" "Enter GKE Target Namespace"
init_var "GSA_NAME" "platform-agent-bot" "Enter Service Account Name for the Agent"
init_var "KSA_NAME" "platform-agent-platform-sa" "Enter Kubernetes Service Account Name"

# ─── Prerequisites Check ──────────────────────────────────────────────────────
print_step "Checking Local Prerequisites"
PREREQS=("gcloud")
for cmd in "${PREREQS[@]}"; do
  echo -ne "  ${C_CYAN}Checking for $cmd... ${C_RESET}"
  if command -v "$cmd" &> /dev/null; then echo -e "✅"; else echo -e "❌"; print_error "$cmd is required."; exit 1; fi
done

# ─── Step Runner Framework ────────────────────────────────────────────────────
run_step() {
  local name=$1; local verify_func=$2; local execute_func=$3; local wait_time=$4
  print_step "$name"
  echo -e "  ${C_CYAN}Verifying current state...${C_RESET}"
  if $verify_func; then print_success "Already completed: $name"; return 0; fi
  if [ "$DRY_RUN" -eq 1 ]; then print_info "[DRY-RUN] Would execute: $name"; return 0; fi

  print_info "Executing action..."
  if $execute_func; then
    print_success "Successfully executed."
    if [ -n "$wait_time" ] && [ "$wait_time" -gt 0 ]; then wait_for_a_bit "$wait_time" "Waiting for changes to propagate"; fi
  else
    print_error "Failed to execute step: $name"; exit 1
  fi
}

# ─── Step Implementations ─────────────────────────────────────────────────────

# Step 1: Enable AI Platform API
verify_apis() {
  local out=$(gcloud services list --enabled --project="$PROJECT_ID" --format="value(config.name)" 2>/dev/null || echo "")
  echo "$out" | grep -q 'aiplatform.googleapis.com'
}
execute_apis() {
  gcloud services enable aiplatform.googleapis.com --project="$PROJECT_ID"
}

# Step 2: Bind Agent GSA AI & Workload Identity Permissions
verify_agent_iam() {
  local gsa_email="${GSA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
  local wi_member="serviceAccount:${PROJECT_ID}.svc.id.goog[${NAMESPACE}/${KSA_NAME}]"
  gcloud iam service-accounts get-iam-policy "${gsa_email}" --project="${PROJECT_ID}" --format="json" 2>/dev/null | grep -F -q "${wi_member}"
}
execute_agent_iam() {
  local gsa_email="${GSA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

  # Ensure the GSA exists in case this script is run out of sequence
  if ! gcloud iam service-accounts describe "${gsa_email}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    print_info "Creating GSA ${GSA_NAME}..."
    gcloud iam service-accounts create "${GSA_NAME}" \
        --display-name="Platform Agent Bot GSA" \
        --project="${PROJECT_ID}"
  fi

  print_info "Applying Workload Identity and AI IAM Policies..."

  # 1. Allow bot to call Gemini (Vertex AI)
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
      --member="serviceAccount:${gsa_email}" \
      --role="roles/aiplatform.user" \
      --quiet >/dev/null

  # 2. Allow operator/bot to view cluster info
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
      --member="serviceAccount:${gsa_email}" \
      --role="roles/container.clusterViewer" \
      --quiet >/dev/null

  # 3. Workload Identity Binding (maps Kubernetes SA to Google SA)
  local wi_member="serviceAccount:${PROJECT_ID}.svc.id.goog[${NAMESPACE}/${KSA_NAME}]"
  gcloud iam service-accounts add-iam-policy-binding "${gsa_email}" \
      --role="roles/iam.workloadIdentityUser" \
      --member="${wi_member}" \
      --project="${PROJECT_ID}" \
      --quiet >/dev/null
}

# ─── Execution Pipeline ───────────────────────────────────────────────────────
run_step "1. Enable AI Platform API" verify_apis execute_apis 10
run_step "2. Configure Agent Workload Identity & AI Permissions" verify_agent_iam execute_agent_iam 5

echo -e "\n${C_MAGENTA}${C_BOLD}>>>  Agent GCP Permissions Configured Successfully!  <<<${C_RESET}"
