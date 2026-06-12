#!/usr/bin/env bash
# ==============================================================================
# 🤖 Step 1: GCP APIs, GKE Cluster, Secret Manager & LiteLLM Gateway
# ==============================================================================
# Idempotent setup script to bootstrap the GCP/GKE infrastructure, secrets,
# and LiteLLM gateway.
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

print_warning() {
  echo -e "  ${C_YELLOW}⚠ $1${C_RESET}"
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
  print_step "Setting up Configuration State"
  
  # 1. Get active GCP Project ID
  ACTIVE_PROJECT="$(gcloud config get-value project 2>/dev/null || echo "")"
  if [ -z "$ACTIVE_PROJECT" ]; then
    DEFAULT_PROJECT_ID="$(whoami)-gkedemos"
  elif [[ "$ACTIVE_PROJECT" == *"-gkedemos" ]]; then
    DEFAULT_PROJECT_ID="$ACTIVE_PROJECT"
  else
    DEFAULT_PROJECT_ID="${ACTIVE_PROJECT}-gkedemos"
  fi
  echo -ne "  ${C_CYAN}Enter Target GCP Project ID [${C_WHITE}${DEFAULT_PROJECT_ID}${C_CYAN}]: ${C_RESET}"
  read -r INPUT_PROJECT_ID
  export PROJECT_ID="${INPUT_PROJECT_ID:-$DEFAULT_PROJECT_ID}"
  
  # 2. Dynamically resolve project number
  print_info "Resolving numeric Project Number for $PROJECT_ID..."
  PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)" 2>/dev/null || echo "")
  if [ -z "$PROJECT_NUMBER" ]; then
    echo -ne "  ${C_YELLOW}Failed to resolve project number automatically. Please enter it manually: ${C_RESET}"
    read -r PROJECT_NUMBER
  fi
  export PROJECT_NUMBER
  print_success "Project Number resolved: $PROJECT_NUMBER"

  # 3. Get Region
  DEFAULT_REGION="us-east4"
  echo -ne "  ${C_CYAN}Enter GKE GCP Region [${C_WHITE}${DEFAULT_REGION}${C_CYAN}]: ${C_RESET}"
  read -r INPUT_REGION
  export REGION="${INPUT_REGION:-$DEFAULT_REGION}"

  # 4. Get Cluster Name
  DEFAULT_CLUSTER="platform-agent-host"
  echo -ne "  ${C_CYAN}Enter GKE Cluster Name [${C_WHITE}${DEFAULT_CLUSTER}${C_CYAN}]: ${C_RESET}"
  read -r INPUT_CLUSTER
  export CLUSTER_NAME="${INPUT_CLUSTER:-$DEFAULT_CLUSTER}"

  # 5. Get Namespace
  DEFAULT_NAMESPACE="agent-system"
  echo -ne "  ${C_CYAN}Enter GKE Target Namespace [${C_WHITE}${DEFAULT_NAMESPACE}${C_CYAN}]: ${C_RESET}"
  read -r INPUT_NAMESPACE
  export NAMESPACE="${INPUT_NAMESPACE:-$DEFAULT_NAMESPACE}"

  # 6. Get Allowed User Email
  DEFAULT_USER="$(gcloud config get-value account 2>/dev/null || whoami@google.com)"
  echo -ne "  ${C_CYAN}Enter Allowed Google Chat User Email [${C_WHITE}${DEFAULT_USER}${C_CYAN}]: ${C_RESET}"
  read -r INPUT_USER
  export ALLOWED_USER="${INPUT_USER:-$DEFAULT_USER}"

  # 6.5. Generate secure random API Server auth key
  export API_SERVER_KEY=$(openssl rand -hex 16)

  # 7. Get Model Default Name
  DEFAULT_MODEL_NAME="gemini-3.1-flash-lite"
  echo -ne "  ${C_CYAN}Enter Model Default Name [${C_WHITE}${DEFAULT_MODEL_NAME}${C_CYAN}]: ${C_RESET}"
  read -r INPUT_MODEL_NAME
  export MODEL_DEFAULT_NAME="${INPUT_MODEL_NAME:-$DEFAULT_MODEL_NAME}"

  # 8. Get Model Provider
  DEFAULT_MODEL_PROVIDER="gemini"
  echo -ne "  ${C_CYAN}Enter Model Provider [${C_WHITE}${DEFAULT_MODEL_PROVIDER}${C_CYAN}]: ${C_RESET}"
  read -r INPUT_MODEL_PROVIDER
  export MODEL_PROVIDER="${INPUT_MODEL_PROVIDER:-$DEFAULT_MODEL_PROVIDER}"

  # 9. Write state file
  cat <<EOF > "$VARS_FILE"
# SRE Sourced Variables for GKE & GCP Setup
export PROJECT_ID="${PROJECT_ID}"
export PROJECT_NUMBER="${PROJECT_NUMBER}"
export REGION="${REGION}"
export CLUSTER_NAME="${CLUSTER_NAME}"
export NAMESPACE="${NAMESPACE}"
export ALLOWED_USER="${ALLOWED_USER}"
export MODEL_DEFAULT_NAME="${MODEL_DEFAULT_NAME}"
export MODEL_PROVIDER="${MODEL_PROVIDER}"
export REPO_NAME="platform-agent-repo"
export CHAT_TOPIC_NAME="platform-agent-chat-events"
export CHAT_SUB_NAME="platform-agent-chat-events-sub"
export GSA_NAME="platform-agent-bot"
export KSA_NAME="platform-agent-platform-sa"
export API_SERVER_KEY="${API_SERVER_KEY}"
EOF
  print_success "Created configuration state file at $VARS_FILE"
fi

source "$VARS_FILE"

# ─── Prerequisites Check ──────────────────────────────────────────────────────
print_step "Checking Local Prerequisites"
PREREQS=("gcloud" "kubectl" "make" "go" "openssl" "envsubst")
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

# Step 1: Enable APIs
verify_apis() {
  local out=$(gcloud services list --enabled --project="$PROJECT_ID" --format="value(config.name)" 2>/dev/null || echo "")
  echo "$out" | grep -q 'container.googleapis.com' && \
  echo "$out" | grep -q 'artifactregistry.googleapis.com' && \
  echo "$out" | grep -q 'cloudbuild.googleapis.com' && \
  echo "$out" | grep -q 'secretmanager.googleapis.com' && \
  echo "$out" | grep -q 'pubsub.googleapis.com' && \
  echo "$out" | grep -q 'chat.googleapis.com' && \
  echo "$out" | grep -q 'gsuiteaddons.googleapis.com' && \
  echo "$out" | grep -q 'aiplatform.googleapis.com' && \
  echo "$out" | grep -q 'cloudresourcemanager.googleapis.com'
}
execute_apis() {
  gcloud services enable \
      container.googleapis.com \
      artifactregistry.googleapis.com \
      cloudbuild.googleapis.com \
      secretmanager.googleapis.com \
      pubsub.googleapis.com \
      chat.googleapis.com \
      gsuiteaddons.googleapis.com \
      aiplatform.googleapis.com \
      cloudresourcemanager.googleapis.com \
      --project="$PROJECT_ID"
}

# Step 2: Create Artifact Registry Repository
verify_registry() {
  gcloud artifacts repositories describe "$REPO_NAME" --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1
}
execute_registry() {
  gcloud artifacts repositories create "$REPO_NAME" \
      --repository-format=docker \
      --location="$REGION" \
      --project="$PROJECT_ID"
}

# Step 3: GKE Cluster Provisioning
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

# Step 4: Connect kubectl & Create Namespace
verify_kubeconfig() {
  kubectl get namespace "$NAMESPACE" >/dev/null 2>&1
}
execute_kubeconfig() {
  print_info "Fetching cluster credentials..."
  gcloud container clusters get-credentials "$CLUSTER_NAME" --region "$REGION" --project "$PROJECT_ID"
  print_info "Creating namespace '$NAMESPACE'..."
  kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
}

# Step 5: Setup Secret Manager Placeholders
verify_secrets() {
  gcloud secrets describe "GEMINI_API_KEY" --project="$PROJECT_ID" >/dev/null 2>&1
}
execute_secrets() {
  for SECRET in "GEMINI_API_KEY"; do
    if ! gcloud secrets describe "$SECRET" --project="$PROJECT_ID" >/dev/null 2>&1; then
      echo -ne "  ${C_CYAN}Secret '$SECRET' not found in cloud. Enter actual key value now (or press ENTER to create empty placeholder): ${C_RESET}"
      read -s -r INPUT_KEY
      echo ""
      local VAL="${INPUT_KEY:-placeholder}"
      echo -n "$VAL" | gcloud secrets create "$SECRET" --data-file=- --replication-policy="automatic" --project="$PROJECT_ID"
      print_success "Secret '$SECRET' created in GCP Secret Manager."
    fi
  done
}

# Step 6: Sync API Keys to GKE Namespace Secrets
verify_k8s_secrets() {
  kubectl get secret platform-agent-secrets -n "$NAMESPACE" >/dev/null 2>&1
}
execute_k8s_secrets() {
  print_info "Resolving keys from GCP Secret Manager..."
  local GEMINI_KEY=$(gcloud secrets versions access latest --secret="GEMINI_API_KEY" --project="$PROJECT_ID" 2>/dev/null || echo "placeholder")
  
  if [ "$GEMINI_KEY" = "placeholder" ]; then
    print_warning "GEMINI_API_KEY is currently a placeholder in GCP Secret Manager. The platform agent will run but cannot authenticate with Gemini until updated."
  fi

  # Self-healing check: Generate API_SERVER_KEY if missing
  if [ -z "${API_SERVER_KEY:-}" ]; then
    print_info "API_SERVER_KEY not found in vars.sh state. Generating a secure random key..."
    export API_SERVER_KEY=$(openssl rand -hex 16)
    echo "export API_SERVER_KEY=\"${API_SERVER_KEY}\"" >> "$VARS_FILE"
  fi

  print_info "Writing Kubernetes Secret 'platform-agent-secrets' into '$NAMESPACE'..."
  kubectl create secret generic platform-agent-secrets \
      --namespace="$NAMESPACE" \
      --from-literal=GEMINI_API_KEY="$GEMINI_KEY" \
      --from-literal=API_SERVER_KEY="$API_SERVER_KEY" \
      --dry-run=client -o yaml | kubectl apply -f -
}

# Step 7: Deploy LiteLLM Gateway
verify_litellm() {
  "${SCRIPT_DIR}/provision_litellm/provision_litellm.sh" --verify
}
execute_litellm() {
  "${SCRIPT_DIR}/provision_litellm/provision_litellm.sh" --deploy
}

# ─── Execution Pipeline ───────────────────────────────────────────────────────
run_step "1. Enable GCP APIs" verify_apis execute_apis 30
run_step "2. Create Artifact Registry Repo" verify_registry execute_registry 0
run_step "3. Provision GKE Cluster" verify_cluster execute_cluster 10
run_step "4. Connect kubectl & Create Namespace" verify_kubeconfig execute_kubeconfig 5
run_step "5. Setup Secret Manager Placeholders" verify_secrets execute_secrets 0
run_step "6. Sync API Keys to GKE Namespace Secrets" verify_k8s_secrets execute_k8s_secrets 0
run_step "7. Deploy LiteLLM Gateway" verify_litellm execute_litellm 10
