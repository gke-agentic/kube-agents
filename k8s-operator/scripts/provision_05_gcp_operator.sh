#!/usr/bin/env bash
# ==============================================================================
# 🤖 Step 5: Deploy Kubernetes Operator (CRDs & Controller Manager)
# ==============================================================================
# Idempotent script that installs the CRDs and deploys the operator to the cluster.
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
C_RED='\033[91m'
C_RESET='\033[0m'
C_BOLD='\033[1m'
C_WHITE='\033[97m'

# ─── UI Helpers ───────────────────────────────────────────────────────────────
print_step() { echo -e "\n${C_MAGENTA}${C_BOLD}>>>  $1  <<<${C_RESET}"; }
print_success() { echo -e "  ${C_GREEN}✓ $1${C_RESET}"; }
print_info() { echo -e "  ${C_CYAN}ℹ $1${C_RESET}"; }
print_error() { echo -e "  ${C_RED}✗ $1${C_RESET}"; }

# ─── Configuration & State Restoration ────────────────────────────────────────
print_step "Setting up Configuration State for Operator Deployment"

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
init_var "REGION" "us-east4" "Enter GKE GCP Region"
init_var "CLUSTER_NAME" "platform-agent-host" "Enter GKE Cluster Name"

# ─── Prerequisites Check ──────────────────────────────────────────────────────
print_step "Checking Local Prerequisites"
PREREQS=("gcloud" "kubectl" "make")
for cmd in "${PREREQS[@]}"; do
  echo -ne "  ${C_CYAN}Checking for $cmd... ${C_RESET}"
  if command -v "$cmd" &> /dev/null; then echo -e "✅"; else echo -e "❌"; print_error "$cmd is required."; exit 1; fi
done

# ─── Step Runner Framework ────────────────────────────────────────────────────
run_step() {
  local name=$1; local verify_func=$2; local execute_func=$3; local wait_time=$4
  print_step "$name"
  echo -e "  ${C_CYAN}Verifying current GKE state...${C_RESET}"
  if $verify_func; then print_success "Already completed: $name"; return 0; fi

  print_info "Executing action..."
  if $execute_func; then
    print_success "Successfully executed."
  else
    print_error "Failed to execute step: $name"; exit 1
  fi
}

# ─── Step Implementations ─────────────────────────────────────────────────────

# Step 1: Connect kubectl
verify_kubeconfig() {
  kubectl get ns kubeagents-system >/dev/null 2>&1 || kubectl get ns default >/dev/null 2>&1
}
execute_kubeconfig() {
  print_info "Fetching cluster credentials..."
  gcloud container clusters get-credentials "$CLUSTER_NAME" --region "$REGION" --project "$PROJECT_ID"
}

# Step 2: Deploy Operator (CRDs & Controller manager)
verify_operator() {
  kubectl get deployment kubeagents-controller-manager -n kubeagents-system >/dev/null 2>&1
}
execute_operator() {
  print_info "Installing Custom Resource Definitions (CRDs)..."
  make -C "$OPERATOR_DIR" install
  print_info "Deploying Operator Controller Manager to the GKE cluster..."
  make -C "$OPERATOR_DIR" deploy
}

# ─── Execution Pipeline ───────────────────────────────────────────────────────
run_step "1. Connect kubectl" verify_kubeconfig execute_kubeconfig 0
run_step "2. Deploy Kubernetes Operator" verify_operator execute_operator 0

print_success "Kubernetes Operator deployed successfully!"
