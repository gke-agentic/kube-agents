#!/usr/bin/env bash
# ==============================================================================
# 🤖 Step 6: Deploy PlatformAgent Custom Resource Manifest
# ==============================================================================
# Idempotent script that connects to GKE, renders the platform-agent.yaml 
# template, and deploys it to the cluster.
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
source "${SCRIPT_DIR}/common.sh" "$@"

# ─── Prerequisites Check ──────────────────────────────────────────────────────
print_step "Checking Local Prerequisites"
check_prereqs "gcloud" "kubectl" "envsubst"

# ─── Configuration & State Restoration ────────────────────────────────────────
print_step "Setting up Configuration State for Agent Deployment"
load_state

ACTIVE_PROJECT="$(gcloud config get-value project 2>/dev/null || echo "")"
DEFAULT_PROJECT_ID="${ACTIVE_PROJECT:-$(whoami 2>/dev/null || echo "user")}"

init_var "PROJECT_ID" "$DEFAULT_PROJECT_ID" "Enter Target GCP Project ID"
init_var "REGION" "us-east4" "Enter GKE GCP Region"
init_var "CLUSTER_NAME" "platform-agent-host" "Enter GKE Cluster Name"
init_var "MODEL_PROVIDER" "gemini" "Enter Model Provider (gemini, openai, anthropic)"

case "$MODEL_PROVIDER" in
  openai)
    DEFAULT_MODEL="gpt-4o"
    ;;
  anthropic)
    DEFAULT_MODEL="claude-3-5-sonnet"
    ;;
  *)
    DEFAULT_MODEL="gemini-3.5-flash"
    ;;
esac

init_var "MODEL_DEFAULT_NAME" "$DEFAULT_MODEL" "Enter Model Default Name"

# Map global state variables to expected template variables
export GSA_NAME="${PLATFORM_AGENT_GSA_NAME}"
export KSA_NAME="${PLATFORM_AGENT_KSA_NAME}"

init_var "CHAT_SUB_NAME" "platform-agent-chat-events-sub" "Enter Pub/Sub Subscription Name"
init_var "CHAT_TOPIC_NAME" "platform-agent-chat-events" "Enter Pub/Sub Topic Name"
init_var "ALLOWED_USERS" "" "Enter Allowed Google Chat Users Emails (comma separated). Leaving it empty will allow all users."
DEFAULT_AGENT_IMAGE="ghcr.io/gke-labs/kube-agents/platform-agent"
init_var "AGENT_IMAGE" "$DEFAULT_AGENT_IMAGE" "Enter Platform Agent Image Path"

# ─── Step Implementations ─────────────────────────────────────────────────────

# Step 1: Connect kubectl
verify_kubeconfig() {
  local current_ctx
  current_ctx=$(kubectl config current-context 2>/dev/null || echo "")
  [[ "$current_ctx" == *"${PROJECT_ID}"* && "$current_ctx" == *"${CLUSTER_NAME}"* ]] && \
  kubectl get namespace "$NAMESPACE" >/dev/null 2>&1
}
execute_kubeconfig() {
  connect_cluster
}

# Step 2: Deploy LiteLLM Gateway
verify_litellm() {
  kubectl get configmap litellm-config -n "${NAMESPACE}" >/dev/null 2>&1 && \
  kubectl get deployment litellm -n "${NAMESPACE}" >/dev/null 2>&1 && \
  kubectl get service litellm -n "${NAMESPACE}" >/dev/null 2>&1
}
execute_litellm() {
  print_info "Deploying LiteLLM Gateway into GKE..."
  export NAMESPACE MODEL_PROVIDER MODEL_DEFAULT_NAME
  make -C "${OPERATOR_DIR}" deploy-litellm || return 1
}

# Step 3: Deploy GitHub Token Minter
verify_github_minter() {
  if [ -z "${GITHUB_ORG:-}" ] || [ -z "${GITHUB_REPO:-}" ] || [ -z "${GITHUB_APP_ID:-}" ]; then
    print_info "GitHub integration not configured. Skipping Minter deployment."
    return 0
  fi

  # Always return false to ensure configuration updates (like KMS key changes)
  # are applied to the Deployment workloads.
  return 1
}

execute_github_minter() {
  if [ -z "${GITHUB_ORG:-}" ] || [ -z "${GITHUB_REPO:-}" ] || [ -z "${GITHUB_APP_ID:-}" ]; then
    return 0
  fi

  # 1. Create KMS Keyring and Key if they don't exist
  # We do this here because it's part of the deployment setup, although IAM was done in step 3.
  # Wait, if GSA was created in step 3, we should have also created the KMS key there?
  # Actually, step 3 is "IAM", but KMS key creation is resource provisioning.
  # Let's ensure keyring and key exist.
  print_info "Ensuring KMS Keyring '${KMS_KEYRING}' exists..."
  if ! gcloud kms keyrings describe "${KMS_KEYRING}" --location="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud kms keyrings create "${KMS_KEYRING}" --location="${REGION}" --project="${PROJECT_ID}"
  fi

  print_info "Ensuring KMS Key '${KMS_KEY}' exists..."
  if ! gcloud kms keys describe "${KMS_KEY}" --location="${REGION}" --keyring="${KMS_KEYRING}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud kms keys create "${KMS_KEY}" \
        --location="${REGION}" \
        --keyring="${KMS_KEYRING}" \
        --purpose=asymmetric-signing \
        --default-algorithm=rsa-sign-pkcs1-2048-sha256 \
        --import-only \
        --skip-initial-version-creation \
        --project="${PROJECT_ID}"
  fi

  # Grant roles/cloudkms.signerVerifier to GSA on KMS key (in case it wasn't done, but it should be in step 3 if key existed)
  # Since key might not have existed in step 3 if we didn't create it there, we must ensure it's bound now.
  local gsa_email="${GITHUB_MINTER_GSA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
  print_info "Ensuring GSA has signer permissions on KMS key..."
  gcloud kms keys add-iam-policy-binding "${KMS_KEY}" \
      --location="${REGION}" \
      --keyring="${KMS_KEYRING}" \
      --member="serviceAccount:${gsa_email}" \
      --role="roles/cloudkms.signerVerifier" \
      --project="${PROJECT_ID}" \
      --quiet >/dev/null

  # Import PEM if provided and no version exists
  local versions=$(gcloud kms keys versions list --key="${KMS_KEY}" --keyring="${KMS_KEYRING}" --location="${REGION}" --project="${PROJECT_ID}" --filter="state=ENABLED" --format="value(name)" 2>/dev/null)
  if [ -z "$versions" ]; then
    if [ -n "${GITHUB_PEM_PATH}" ] && [ -f "${GITHUB_PEM_PATH}" ]; then
      print_info "Importing GitHub Private Key PEM into KMS..."
      
      local tmp_dir=$(mktemp -d)
      print_info "Cloning github-token-minter CLI tool (v2.7.1) for secure cryptographic wrapping..."
      if git clone --depth 1 --branch v2.7.1 https://github.com/abcxyz/github-token-minter.git "$tmp_dir" >/dev/null 2>&1; then
        local abs_pem=$(realpath "${GITHUB_PEM_PATH}")
        local import_success=0
        (
          cd "$tmp_dir"
          if go run ./cmd/minty tools import-pk \
              -project-id="${PROJECT_ID}" \
              -location="${REGION}" \
              -key-ring="${KMS_KEYRING}" \
              -key="${KMS_KEY}" \
              -private-key="@${abs_pem}"; then
            exit 0
          else
            exit 1
          fi
        ) && import_success=1
        rm -rf "$tmp_dir"
        
        if [ "$import_success" -eq 1 ]; then
          print_success "Successfully imported GitHub Private Key to KMS via Minty CLI."
        else
          print_error "Failed to import GitHub Private Key to KMS. You must import it manually."
          exit 1
        fi
      else
        rm -rf "$tmp_dir"
        print_error "Failed to clone github-token-minter repo for CLI tools."
        exit 1
      fi
    else
      print_warning "No GitHub Private Key PEM path provided or file not found."
      print_warning "KMS Key '${KMS_KEY}' has no active version. Minter will fail to start until you import the key."
      print_warning "You can import it later manually using Minty CLI:"
      print_warning "  git clone --depth 1 --branch v2.7.1 https://github.com/abcxyz/github-token-minter.git /tmp/minty && cd /tmp/minty && go run ./cmd/minty tools import-pk -project-id=${PROJECT_ID} -location=${REGION} -key-ring=${KMS_KEYRING} -key=${KMS_KEY} -private-key=@/path/to/pem"
    fi
  fi

  # Resolve the latest active (ENABLED) version number dynamically
  print_info "Resolving active KMS key version number..."
  local active_version
  active_version=$(gcloud kms keys versions list --key="${KMS_KEY}" --keyring="${KMS_KEYRING}" --location="${REGION}" --project="${PROJECT_ID}" --filter="state=ENABLED" --format="value(name)" 2>/dev/null | awk -F'/' '{print $NF}' | sort -n | tail -n 1)
  
  if [ -n "$active_version" ]; then
    export KMS_KEY_VERSION="${active_version}"
    print_success "Resolved active KMS key version: ${KMS_KEY_VERSION}"
  else
    print_error "No active (ENABLED) version found for KMS Key '${KMS_KEY}'!"
    print_error "The Token Minter deployment will fail to sign tokens."
    exit 1
  fi

  print_info "Deploying GitHub Token Minter workloads..."
  local GITHUB_INTEGRATION_DIR="${OPERATOR_DIR}/config/integrations/github"
  
  if [ -d "$GITHUB_INTEGRATION_DIR" ]; then
    # Ensure all variables are exported for envsubst
    export PROJECT_ID REGION CLUSTER_NAME NAMESPACE GITHUB_MINTER_KSA_NAME GITHUB_MINTER_GSA_NAME KMS_KEYRING KMS_KEY KMS_KEY_VERSION GITHUB_ORG GITHUB_REPO KSA_NAME GITHUB_REF PLATFORM_AGENT_GSA_NAME
    
    print_info "Applying configmap.yaml..."
    envsubst < "${GITHUB_INTEGRATION_DIR}/configmap.yaml" | kubectl apply -f -
    
    print_info "Applying deployment.yaml..."
    envsubst < "${GITHUB_INTEGRATION_DIR}/deployment.yaml" | kubectl apply -f -
  else
    print_error "GitHub integration directory not found at ${GITHUB_INTEGRATION_DIR}"
    exit 1
  fi
}

# Step 4: Apply PlatformAgent Custom Resource
verify_custom_resource() {
  # Always return false to ensure configuration updates are applied to the Custom Resource
  return 1
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
  export PROJECT_ID REGION CLUSTER_NAME MODEL_DEFAULT_NAME MODEL_PROVIDER GSA_NAME CHAT_SUB_NAME CHAT_TOPIC_NAME ALLOWED_USERS AGENT_IMAGE NAMESPACE KSA_NAME

  envsubst < "$CR_TEMPLATE" > "$CR_MANIFEST"
  
  print_info "Applying 'platform-agent' Custom Resource to the GKE cluster..."
  kubectl apply -f "$CR_MANIFEST"
}

# ─── Execution Pipeline ───────────────────────────────────────────────────────
run_step "1. Connect kubectl" verify_kubeconfig execute_kubeconfig 0
run_step "2. Deploy LiteLLM Gateway" verify_litellm execute_litellm 0
run_step "3. Deploy GitHub Token Minter" verify_github_minter execute_github_minter 10
run_step "4. Apply PlatformAgent Custom Resource" verify_custom_resource execute_custom_resource 0

# ─── Conclusion Checklist ─────────────────────────────────────────────────────
echo -e "\n${C_GREEN}${C_BOLD}✓ PlatformAgent Custom Resource applied successfully to GKE!${C_RESET}"
