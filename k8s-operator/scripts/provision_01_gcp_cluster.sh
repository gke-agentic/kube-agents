#!/usr/bin/env bash
# ==============================================================================
# 🤖 Step 1: GCP APIs & GKE Cluster Initialization
# ==============================================================================
# Idempotent setup script to bootstrap the bare GKE cluster and namespace.
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
print_step() { echo -e "\n${C_MAGENTA}${C_BOLD}>>>  $1  <<<${C_RESET}"; }
print_success() { echo -e "  ${C_GREEN}✓ $1${C_RESET}"; }
print_info() { echo -e "  ${C_CYAN}ℹ $1${C_RESET}"; }
print_warning() { echo -e "  ${C_YELLOW}⚠ $1${C_RESET}"; }
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
print_step "Setting up Configuration State"

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
if [ -z "$ACTIVE_PROJECT" ]; then
  DEFAULT_PROJECT_ID="$(whoami 2>/dev/null || echo "user")-gkedemos"
elif [[ "$ACTIVE_PROJECT" == *"-gkedemos" ]]; then
  DEFAULT_PROJECT_ID="$ACTIVE_PROJECT"
else
  DEFAULT_PROJECT_ID="${ACTIVE_PROJECT}-gkedemos"
fi

init_var "PROJECT_ID" "$DEFAULT_PROJECT_ID" "Enter Target GCP Project ID"
init_var "REGION" "us-east4" "Enter GKE GCP Region"
init_var "CLUSTER_NAME" "platform-agent-host" "Enter GKE Cluster Name"
init_var "NAMESPACE" "agent-system" "Enter GKE Target Namespace"

# ─── Prerequisites Check ──────────────────────────────────────────────────────
print_step "Checking Local Prerequisites"
PREREQS=("gcloud" "kubectl")
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
  local wait_time=$4
  
  print_step "$name"
  echo -e "  ${C_CYAN}Verifying current GCP/GKE state...${C_RESET}"
  
  if $verify_func; then print_success "Already completed: $name"; return 0; fi
  if [ "$DRY_RUN" -eq 1 ]; then print_info "[DRY-RUN] Would execute: $name"; return 0; fi

  print_info "Executing action..."
  if $execute_func; then
    print_success "Successfully executed."
    if [ -n "$wait_time" ] && [ "$wait_time" -gt 0 ]; then
      wait_for_a_bit "$wait_time" "Waiting for changes to propagate"
    fi
  else
    print_error "Failed to execute step: $name"
    exit 1
  fi
}

# ─── Step Implementations ─────────────────────────────────────────────────────

# Step 1: Enable APIs
verify_apis() {
  local out=$(gcloud services list --enabled --project="$PROJECT_ID" --format="value(config.name)" 2>/dev/null || echo "")
  echo "$out" | grep -q 'container.googleapis.com' && \
  echo "$out" | grep -q 'cloudresourcemanager.googleapis.com'
}
execute_apis() {
  gcloud services enable \
      container.googleapis.com \
      cloudresourcemanager.googleapis.com \
      --project="$PROJECT_ID"
}

# Step 2: GKE Cluster Provisioning
verify_cluster() {
  gcloud container clusters describe "$CLUSTER_NAME" --region="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1
}
execute_cluster() {
  print_info "Creating GKE Standard Cluster with Workload Identity. This takes approximately 5-8 minutes in Google Cloud..."
  gcloud beta container clusters create "$CLUSTER_NAME" \
      --region "$REGION" \
      --machine-type="e2-standard-4" \
      --num-nodes=1 \
      --workload-pool="${PROJECT_ID}.svc.id.goog" \
      --managed-otel-scope=COLLECTION_AND_INSTRUMENTATION_COMPONENTS \
      --project "$PROJECT_ID"
}

# Step 3: Connect kubectl & Create Namespace
verify_kubeconfig() {
  kubectl get namespace "$NAMESPACE" >/dev/null 2>&1
}
execute_kubeconfig() {
  print_info "Fetching cluster credentials..."
  gcloud container clusters get-credentials "$CLUSTER_NAME" --region "$REGION" --project "$PROJECT_ID"
  print_info "Creating namespace '$NAMESPACE'..."
  kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
}

# ─── Execution Pipeline ───────────────────────────────────────────────────────
run_step "1. Enable GCP Cluster APIs" verify_apis execute_apis 30
run_step "2. Provision GKE Cluster" verify_cluster execute_cluster 10
run_step "3. Connect kubectl & Create Namespace" verify_kubeconfig execute_kubeconfig 5

echo -e "\n${C_MAGENTA}${C_BOLD}>>>  GKE Infrastructure Provisioned Successfully!  <<<${C_RESET}"
