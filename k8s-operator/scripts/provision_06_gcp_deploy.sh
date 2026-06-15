#!/usr/bin/env bash
# ==============================================================================
# 🤖 Step 6: Deploy Agent Custom Resource Manifest
# ==============================================================================
# Idempotent script that connects to the cluster, renders the platform-agent.yaml
# using envsubst, and applies it to the Kubernetes environment.
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
print_step "Setting up Configuration State for Custom Resource Deployment"

if [ ! -f "$VARS_FILE" ]; then
  echo "# SRE Sourced Variables for GKE & GCP Setup" > "$VARS_FILE"
fi
source "$VARS_FILE"

init_var() {
  local var_name=$1
  local default_val=$2
  local prompt_msg=$3
  if ! declare -p "$var_name" &>/dev/null; then
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
init_var "NAMESPACE" "agent-system" "Enter GKE Target Namespace"
init_var "MODEL_DEFAULT_NAME" "gemini-3.1-flash-lite" "Enter Model Default Name"
init_var "MODEL_PROVIDER" "gemini" "Enter Model Provider"

# Vars needed for the template via envsubst and the checklist
init_var "GSA_NAME" "platform-agent-bot" "Enter Service Account Name for the Agent"
init_var "KSA_NAME" "platform-agent-platform-sa" "Enter Kubernetes Service Account Name"
init_var "CHAT_SUB_NAME" "platform-agent-chat-events-sub" "Enter Pub/Sub Subscription Name"
init_var "CHAT_TOPIC_NAME" "platform-agent-chat-events" "Enter Pub/Sub Topic Name"
init_var "ALLOWED_USER" "" "Enter Allowed Google Chat User Email"
DEFAULT_AGENT_IMAGE="ghcr.io/gke-labs/kube-agents/platform-agent"
init_var "AGENT_IMAGE" "$DEFAULT_AGENT_IMAGE" "Enter Platform Agent Image Path"

# If the user did not provide a tag/digest, default to latest
if [[ "$AGENT_IMAGE" != *":"* && "$AGENT_IMAGE" != *"@"* ]]; then
  AGENT_IMAGE="${AGENT_IMAGE}:latest"
fi

# ─── Prerequisites Check ──────────────────────────────────────────────────────
print_step "Checking Local Prerequisites"
PREREQS=("gcloud" "kubectl" "envsubst")
for cmd in "${PREREQS[@]}"; do
  echo -ne "  ${C_CYAN}Checking for $cmd... ${C_RESET}"
  if command -v "$cmd" &> /dev/null; then echo -e "✅"; else echo -e "❌"; print_error "$cmd is required."; exit 1; fi
done

# ─── Step Runner Framework ────────────────────────────────────────────────────
run_step() {
  local name=$1; local verify_func=$2; local execute_func=$3; local wait_time=$4
  print_step "$name"
  echo -e "  ${C_CYAN}Verifying current K8s state...${C_RESET}"
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

# Step 1: Connect kubectl
verify_kubeconfig() {
  kubectl get namespace "$NAMESPACE" >/dev/null 2>&1
}
execute_kubeconfig() {
  print_info "Fetching cluster credentials..."
  gcloud container clusters get-credentials "$CLUSTER_NAME" --region "$REGION" --project "$PROJECT_ID"
}

# Step 2: Apply PlatformAgent Custom Resource
verify_custom_resource() {
  kubectl get platformagents.kubeagents.x-k8s.io platform-agent -n "$NAMESPACE" >/dev/null 2>&1
}
execute_custom_resource() {
  print_info "Generating custom resource manifest 'platform-agent.yaml' from template..."
  local CR_TEMPLATE="${SCRIPT_DIR}/platform-agent.yaml.template"
  local CR_MANIFEST="${SCRIPT_DIR}/platform-agent.yaml"

  if [ ! -f "$CR_TEMPLATE" ]; then
    print_error "Custom resource template '$CR_TEMPLATE' not found!"
    exit 1
  fi

  # Ensure variables are explicitly exported so envsubst can access them
  export PROJECT_ID REGION CLUSTER_NAME NAMESPACE MODEL_DEFAULT_NAME MODEL_PROVIDER GSA_NAME KSA_NAME CHAT_SUB_NAME CHAT_TOPIC_NAME ALLOWED_USER AGENT_IMAGE

  envsubst < "$CR_TEMPLATE" > "$CR_MANIFEST"
  
  print_info "Applying 'platform-agent' Custom Resource to the GKE cluster..."
  kubectl apply -f "$CR_MANIFEST"
}

# ─── Execution Pipeline ───────────────────────────────────────────────────────
run_step "1. Connect kubectl" verify_kubeconfig execute_kubeconfig 0
run_step "2. Apply PlatformAgent Custom Resource" verify_custom_resource execute_custom_resource 0

# ─── Conclusion Checklist ─────────────────────────────────────────────────────
echo -e "\n${C_GREEN}${C_BOLD}✓ PlatformAgent Custom Resource applied successfully to GKE!${C_RESET}"
