#!/usr/bin/env bash
# ==============================================================================
# 🤖 Step 4: GCP Resources, IAM Policies, CRDs & Custom Resource Setup for GChat
# ==============================================================================
# Idempotent script to configure Google Chat GCP dependencies, install operator
# CRDs, and deploy the PlatformAgent custom resource.
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

cleanup() {
  tput cnorm 2>/dev/null || true
}
trap cleanup EXIT

# ─── Argument Parsing ─────────────────────────────────────────────────────────
DRY_RUN=0
while [[ "$#" -gt 0 ]]; do
  case $1 in
    --dry-run) DRY_RUN=1 ;;
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
PREREQS=("gcloud" "kubectl" "make" "envsubst")
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
    if [ -n "$wait_time" ] && [ "$wait_time" -gt 0 ]; then
      wait_for_a_bit "$wait_time" "Waiting for changes to propagate"
    fi
  else
    print_error "Failed to execute step: $name"
    exit 1
  fi
}

# ─── Step Implementations ─────────────────────────────────────────────────────

# Step 1: Manually Provision GCP Resources & IAM policy bindings
verify_agent_gcp_resources() {
  local gsa_email="${GSA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
  gcloud iam service-accounts describe "${gsa_email}" --project="${PROJECT_ID}" >/dev/null 2>&1 && \
  gcloud pubsub topics describe "${CHAT_TOPIC_NAME}" --project="${PROJECT_ID}" >/dev/null 2>&1 && \
  gcloud pubsub subscriptions describe "${CHAT_SUB_NAME}" --project="${PROJECT_ID}" >/dev/null 2>&1
}
execute_agent_gcp_resources() {
  local gsa_email="${GSA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

  # 1. Create GSA
  if ! gcloud iam service-accounts describe "${gsa_email}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    print_info "Creating GSA ${GSA_NAME}..."
    gcloud iam service-accounts create "${GSA_NAME}" \
        --display-name="Platform Agent Bot GSA" \
        --project="${PROJECT_ID}"
  fi

  # 2. Create Pub/Sub Topic
  if ! gcloud pubsub topics describe "${CHAT_TOPIC_NAME}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    print_info "Creating Pub/Sub Topic ${CHAT_TOPIC_NAME}..."
    gcloud pubsub topics create "${CHAT_TOPIC_NAME}" --project="${PROJECT_ID}"
  fi

  # 3. Create Pub/Sub Subscription
  if ! gcloud pubsub subscriptions describe "${CHAT_SUB_NAME}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    print_info "Creating Pub/Sub Subscription ${CHAT_SUB_NAME}..."
    gcloud pubsub subscriptions create "${CHAT_SUB_NAME}" \
        --topic="${CHAT_TOPIC_NAME}" \
        --ack-deadline=60 \
        --project="${PROJECT_ID}"
  fi

  # 4. IAM Bindings
  print_info "Applying IAM Policy Bindings..."
  
  # GSA Pub/Sub subscriber and viewer
  gcloud pubsub subscriptions add-iam-policy-binding "${CHAT_SUB_NAME}" \
      --member="serviceAccount:${gsa_email}" \
      --role="roles/pubsub.subscriber" \
      --project="${PROJECT_ID}" \
      --quiet >/dev/null

  gcloud pubsub subscriptions add-iam-policy-binding "${CHAT_SUB_NAME}" \
      --member="serviceAccount:${gsa_email}" \
      --role="roles/pubsub.viewer" \
      --project="${PROJECT_ID}" \
      --quiet >/dev/null

  # GSA AI Platform User on Project
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
      --member="serviceAccount:${gsa_email}" \
      --role="roles/aiplatform.user" \
      --quiet >/dev/null

  # GSA Cluster Viewer on Project
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
      --member="serviceAccount:${gsa_email}" \
      --role="roles/container.clusterViewer" \
      --quiet >/dev/null

  # GChat API Publisher on Topic
  gcloud pubsub topics add-iam-policy-binding "${CHAT_TOPIC_NAME}" \
      --member="serviceAccount:chat-api-push@system.gserviceaccount.com" \
      --role="roles/pubsub.publisher" \
      --project="${PROJECT_ID}" \
      --quiet >/dev/null

  # Workspace Add-ons Publisher on Topic
  local gsuite_sa="service-${PROJECT_NUMBER}@gcp-sa-gsuiteaddons.iam.gserviceaccount.com"
  gcloud pubsub topics add-iam-policy-binding "${CHAT_TOPIC_NAME}" \
      --member="serviceAccount:${gsuite_sa}" \
      --role="roles/pubsub.publisher" \
      --project="${PROJECT_ID}" \
      --quiet >/dev/null

  # 5. Workload Identity Binding
  print_info "Applying Workload Identity Policy Binding (GSA -> KSA)..."
  local wi_member="serviceAccount:${PROJECT_ID}.svc.id.goog[${NAMESPACE}/${KSA_NAME}]"
  gcloud iam service-accounts add-iam-policy-binding "${gsa_email}" \
      --role="roles/iam.workloadIdentityUser" \
      --member="${wi_member}" \
      --project="${PROJECT_ID}" \
      --quiet >/dev/null
}

# Step 2: Install CRDs
verify_crds() {
  kubectl get crd platformagents.kubeagents.x-k8s.io >/dev/null 2>&1 && \
  kubectl get crd operatoragents.kubeagents.x-k8s.io >/dev/null 2>&1 && \
  kubectl get crd devteamagents.kubeagents.x-k8s.io >/dev/null 2>&1
}
execute_crds() {
  print_info "Registering operator CRDs on GKE cluster..."
  (
    cd "${OPERATOR_DIR}"
    make install
  )
}

# Step 3: Apply PlatformAgent Custom Resource
verify_custom_resource() {
  kubectl get platformagents.kubeagents.x-k8s.io platform-agent -n "$NAMESPACE" >/dev/null 2>&1
}
execute_custom_resource() {
  print_info "Generating custom resource manifest 'platform-agent.yaml' from template..."
  local CR_TEMPLATE="${SCRIPT_DIR}/platform-agent.yaml.template"
  local CR_MANIFEST="${SCRIPT_DIR}/platform-agent.yaml"

  envsubst < "$CR_TEMPLATE" > "$CR_MANIFEST"
  
  print_info "Applying 'platform-agent' Custom Resource to the GKE cluster..."
  kubectl apply -f "$CR_MANIFEST"
}

# ─── Execution Pipeline ───────────────────────────────────────────────────────
run_step "1. Provision GCP Resources for Agent" verify_agent_gcp_resources execute_agent_gcp_resources 5
run_step "2. Register CRDs on cluster" verify_crds execute_crds 0
run_step "3. Apply PlatformAgent Custom Resource" verify_custom_resource execute_custom_resource 0
