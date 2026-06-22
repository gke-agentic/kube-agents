#!/usr/bin/env bash
# ==============================================================================
# 🤖 Step 3: Controller & Agent GCP Workload Identity & GCP IAM Permissions
# ==============================================================================
# Idempotent script for granting GKE cluster management and Workload Identity
# permissions to the Operator Controller Manager and Agent GSAs.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VARS_FILE="${SCRIPT_DIR}/vars.sh"

# ─── ANSI Colors ──────────────────────────────────────────────────────────────
source "${SCRIPT_DIR}/common.sh" "$@"

# ─── Configuration & State Restoration ────────────────────────────────────────
print_step "Setting up Configuration State for Controller & Agent Identities"
load_state

ACTIVE_PROJECT="$(gcloud config get-value project 2>/dev/null || echo "")"
DEFAULT_PROJECT_ID="${ACTIVE_PROJECT:-$(whoami 2>/dev/null || echo "user")}"

init_var "PROJECT_ID" "$DEFAULT_PROJECT_ID" "Enter Target GCP Project ID"

# ─── Prerequisites Check ──────────────────────────────────────────────────────
print_step "Checking Local Prerequisites"
check_prereqs "gcloud" "kubectl"

# ─── Helper Functions for Agents ──────────────────────────────────────────────
verify_agent_iam() {
  local ksa_name=$1
  local gsa_name=$2
  shift 2
  local roles=("$@")
  
  local gsa_email="${gsa_name}@${PROJECT_ID}.iam.gserviceaccount.com"
  local wi_member="serviceAccount:${PROJECT_ID}.svc.id.goog[${NAMESPACE}/${ksa_name}]"
  
  # Ensure the service account exists
  gcloud iam service-accounts describe "${gsa_email}" --project="${PROJECT_ID}" >/dev/null 2>&1 || return 1
  
  # Ensure Workload Identity binding is present
  gcloud iam service-accounts get-iam-policy "${gsa_email}" --project="${PROJECT_ID}" --format="json" 2>/dev/null | grep -F -q "${wi_member}" || return 1
  
  local project_roles
  project_roles=$(gcloud projects get-iam-policy "${PROJECT_ID}" --flatten="bindings[].members" --filter="bindings.members:serviceAccount:${gsa_email}" --format="value(bindings.role)" 2>/dev/null)
  for role in "${roles[@]}"; do
    echo "$project_roles" | grep -q "${role}" || return 1
  done
  
  return 0
}

execute_agent_iam() {
  local agent_name=$1
  local ksa_name=$2
  local gsa_name=$3
  shift 3
  local roles=("$@")
  
  local gsa_email="${gsa_name}@${PROJECT_ID}.iam.gserviceaccount.com"
  
  if ! gcloud iam service-accounts describe "${gsa_email}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    print_info "Creating GSA ${gsa_name} for ${agent_name}..."
    gcloud iam service-accounts create "${gsa_name}" \
        --display-name="${agent_name} GSA" \
        --project="${PROJECT_ID}" || return 1
    sleep 15
  fi
  
  print_info "Configuring IAM roles for ${gsa_name}..."
  for role in "${roles[@]}"; do
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
        --member="serviceAccount:${gsa_email}" \
        --role="${role}" \
        --quiet >/dev/null || return 1
  done
  
  print_info "Binding Workload Identity for ${gsa_name} to ${ksa_name}..."
  local wi_member="serviceAccount:${PROJECT_ID}.svc.id.goog[${NAMESPACE}/${ksa_name}]"
  gcloud iam service-accounts add-iam-policy-binding "${gsa_email}" \
      --role="roles/iam.workloadIdentityUser" \
      --member="${wi_member}" \
      --project="${PROJECT_ID}" \
      --quiet >/dev/null || return 1
}

verify_ksa_annotation() {
  local ksa_name=$1
  local gsa_name=$2
  local gsa_email="${gsa_name}@${PROJECT_ID}.iam.gserviceaccount.com"
  local ann
  ann=$(kubectl get serviceaccount "${ksa_name}" -n "${NAMESPACE}" -o jsonpath='{.metadata.annotations.iam\.gke\.io/gcp-service-account}' 2>/dev/null || echo "")
  [ "$ann" = "$gsa_email" ]
}

annotate_ksa() {
  local ksa_name=$1
  local gsa_name=$2
  local gsa_email="${gsa_name}@${PROJECT_ID}.iam.gserviceaccount.com"
  print_info "Annotating ServiceAccount ${ksa_name} with GSA email..."
  kubectl annotate serviceaccount "${ksa_name}" \
      --namespace "${NAMESPACE}" \
      iam.gke.io/gcp-service-account="${gsa_email}" \
      --overwrite || return 1
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
      --project="$PROJECT_ID" || return 1
}

# Step 2: Configure Controller IAM
verify_controller() {
  verify_agent_iam "${CONTROLLER_KSA_NAME}" "${CONTROLLER_GSA_NAME}" \
      "roles/container.clusterViewer" \
      "roles/container.admin"
}
execute_controller() {
  execute_agent_iam "Kubeagents Controller Manager" "${CONTROLLER_KSA_NAME}" "${CONTROLLER_GSA_NAME}" \
      "roles/container.clusterViewer" \
      "roles/container.admin"
}

# Step 3: Configure Platform Agent IAM
verify_platform_agent() {
  verify_agent_iam "${PLATFORM_AGENT_KSA_NAME}" "${PLATFORM_AGENT_GSA_NAME}" \
      "roles/container.clusterAdmin" \
      "roles/container.admin" \
      "roles/monitoring.admin" \
      "roles/logging.admin" \
      "roles/iam.serviceAccountUser"
}
execute_platform_agent() {
  execute_agent_iam "Platform Agent" "${PLATFORM_AGENT_KSA_NAME}" "${PLATFORM_AGENT_GSA_NAME}" \
      "roles/container.clusterAdmin" \
      "roles/container.admin" \
      "roles/monitoring.admin" \
      "roles/logging.admin" \
      "roles/iam.serviceAccountUser" 
}

# Step 4: Configure Operator Agent IAM
verify_operator_agent() {
  verify_agent_iam "${OPERATOR_AGENT_KSA_NAME}" "${OPERATOR_AGENT_GSA_NAME}" \
      "roles/container.clusterViewer" \
      "roles/monitoring.viewer" \
      "roles/logging.viewer" \
      "roles/iam.serviceAccountUser"
}
execute_operator_agent() {
  execute_agent_iam "Operator Agent" "${OPERATOR_AGENT_KSA_NAME}" "${OPERATOR_AGENT_GSA_NAME}" \
      "roles/container.clusterViewer" \
      "roles/monitoring.viewer" \
      "roles/logging.viewer" \
      "roles/iam.serviceAccountUser"
}

# Step 5: Configure DevTeam Agent IAM
verify_devteam_agent() {
  verify_agent_iam "${DEVTEAM_AGENT_KSA_NAME}" "${DEVTEAM_AGENT_GSA_NAME}" \
      "roles/container.clusterViewer" \
      "roles/monitoring.viewer" \
      "roles/logging.viewer" \
      "roles/iam.serviceAccountUser"
}
execute_devteam_agent() {
  execute_agent_iam "DevTeam Agent" "${DEVTEAM_AGENT_KSA_NAME}" "${DEVTEAM_AGENT_GSA_NAME}" \
      "roles/container.clusterViewer" \
      "roles/monitoring.viewer" \
      "roles/logging.viewer" \
      "roles/iam.serviceAccountUser"
}

# Step 6: Configure GitHub Token Minter IAM
verify_github_minter_iam() {
  # If GitHub integration is not configured, skip
  if [ -z "${GITHUB_ORG:-}" ] || [ -z "${GITHUB_REPO:-}" ] || [ -z "${GITHUB_APP_ID:-}" ]; then
    print_info "GitHub integration not configured. Skipping Minter IAM setup."
    return 0
  fi

  local gsa_email="${GITHUB_MINTER_GSA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
  local wi_member="serviceAccount:${PROJECT_ID}.svc.id.goog[${NAMESPACE}/${GITHUB_MINTER_KSA_NAME}]"
  
  # 1. Ensure the GKE KSA exists
  kubectl get serviceaccount "${GITHUB_MINTER_KSA_NAME}" -n "${NAMESPACE}" >/dev/null 2>&1 || return 1

  # 2. Ensure the GCP GSA exists
  gcloud iam service-accounts describe "${gsa_email}" --project="${PROJECT_ID}" >/dev/null 2>&1 || return 1
  
  # 3. Ensure Workload Identity binding is present
  gcloud iam service-accounts get-iam-policy "${gsa_email}" --project="${PROJECT_ID}" --format="json" 2>/dev/null | grep -F -q "${wi_member}" || return 1
  
  return 0
}

execute_github_minter_iam() {
  if [ -z "${GITHUB_ORG:-}" ] || [ -z "${GITHUB_REPO:-}" ] || [ -z "${GITHUB_APP_ID:-}" ]; then
    return 0
  fi

  local gsa_email="${GITHUB_MINTER_GSA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
  
  if ! gcloud iam service-accounts describe "${gsa_email}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    print_info "Creating GSA ${GITHUB_MINTER_GSA_NAME} for GitHub Token Minter..."
    gcloud iam service-accounts create "${GITHUB_MINTER_GSA_NAME}" \
        --display-name="GitHub Token Minter GSA" \
        --project="${PROJECT_ID}" || return 1
    sleep 15
  fi

  print_info "Ensuring GKE namespace '${NAMESPACE}' exists..."
  kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

  print_info "Creating GKE ServiceAccount '${GITHUB_MINTER_KSA_NAME}'..."
  local GITHUB_INTEGRATION_DIR="${SCRIPT_DIR}/../config/integrations/github"
  if [ -f "${GITHUB_INTEGRATION_DIR}/serviceaccount.yaml" ]; then
    # Export variables for envsubst
    export NAMESPACE GITHUB_MINTER_KSA_NAME GITHUB_MINTER_GSA_NAME PROJECT_ID
    envsubst < "${GITHUB_INTEGRATION_DIR}/serviceaccount.yaml" | kubectl apply -f -
  else
    print_error "Minter serviceaccount.yaml template not found at ${GITHUB_INTEGRATION_DIR}/serviceaccount.yaml"
    return 1
  fi
  
  print_info "Binding Workload Identity for ${GITHUB_MINTER_GSA_NAME} to ${GITHUB_MINTER_KSA_NAME}..."
  local wi_member="serviceAccount:${PROJECT_ID}.svc.id.goog[${NAMESPACE}/${GITHUB_MINTER_KSA_NAME}]"
  gcloud iam service-accounts add-iam-policy-binding "${gsa_email}" \
      --role="roles/iam.workloadIdentityUser" \
      --member="${wi_member}" \
      --project="${PROJECT_ID}" \
      --quiet >/dev/null || return 1
}

# Step 7: Annotate GKE ServiceAccounts & Restart Controller Manager Deployment
verify_annotations() {
  if [ "${DRY_RUN:-0}" -eq 1 ]; then
    return 1
  fi
  connect_cluster

  verify_ksa_annotation "${CONTROLLER_KSA_NAME}" "${CONTROLLER_GSA_NAME}" || return 1
  verify_ksa_annotation "${PLATFORM_AGENT_KSA_NAME}" "${PLATFORM_AGENT_GSA_NAME}" || return 1
  verify_ksa_annotation "${OPERATOR_AGENT_KSA_NAME}" "${OPERATOR_AGENT_GSA_NAME}" || return 1
  verify_ksa_annotation "${DEVTEAM_AGENT_KSA_NAME}" "${DEVTEAM_AGENT_GSA_NAME}" || return 1
  
  if [ -n "${GITHUB_ORG:-}" ] && [ -n "${GITHUB_REPO:-}" ] && [ -n "${GITHUB_APP_ID:-}" ]; then
    verify_ksa_annotation "${GITHUB_MINTER_KSA_NAME}" "${GITHUB_MINTER_GSA_NAME}" || return 1
  fi
  
  return 0
}
execute_annotations() {
  annotate_ksa "${CONTROLLER_KSA_NAME}" "${CONTROLLER_GSA_NAME}" || return 1
  annotate_ksa "${PLATFORM_AGENT_KSA_NAME}" "${PLATFORM_AGENT_GSA_NAME}" || return 1
  annotate_ksa "${OPERATOR_AGENT_KSA_NAME}" "${OPERATOR_AGENT_GSA_NAME}" || return 1
  annotate_ksa "${DEVTEAM_AGENT_KSA_NAME}" "${DEVTEAM_AGENT_GSA_NAME}" || return 1
  
  if [ -n "${GITHUB_ORG:-}" ] && [ -n "${GITHUB_REPO:-}" ] && [ -n "${GITHUB_APP_ID:-}" ]; then
    annotate_ksa "${GITHUB_MINTER_KSA_NAME}" "${GITHUB_MINTER_GSA_NAME}" || return 1
  fi

  print_info "Restarting Controller Manager Deployment to apply changes..."
  kubectl rollout restart deployment/kubeagents-controller-manager -n "${NAMESPACE}" || return 1

  if kubectl get deployment/platform-agent-gateway -n "${NAMESPACE}" >/dev/null 2>&1; then
    print_info "Restarting Platform Agent Gateway Deployment to apply changes..."
    kubectl rollout restart deployment/platform-agent-gateway -n "${NAMESPACE}" || return 1
  fi
}

# ─── Execution Pipeline ───────────────────────────────────────────────────────
run_step "1. Enable APIs" verify_apis execute_apis 10
run_step "2. Configure Controller Workload Identity & GCP IAM" verify_controller execute_controller 5
run_step "3. Configure Platform Agent Workload Identity & GCP IAM" verify_platform_agent execute_platform_agent 5
run_step "4. Configure Operator Agent Workload Identity & GCP IAM" verify_operator_agent execute_operator_agent 5
run_step "5. Configure DevTeam Agent Workload Identity & GCP IAM" verify_devteam_agent execute_devteam_agent 5
run_step "6. Configure GitHub Token Minter Workload Identity" verify_github_minter_iam execute_github_minter_iam 5
run_step "7. Annotate GKE ServiceAccounts & Restart Deployment" verify_annotations execute_annotations 5

echo -e "\n${C_MAGENTA}${C_BOLD}>>>  Controller & Agent GCP Permissions Configured Successfully!  <<<${C_RESET}"
