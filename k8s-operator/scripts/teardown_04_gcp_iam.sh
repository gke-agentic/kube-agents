#!/usr/bin/env bash
# ==============================================================================
# 🧹 Step 4: Teardown Agent GCP Workload Identity & AI Permissions
# ==============================================================================
# Idempotent script to remove Vertex AI and GKE permissions and Workload
# Identity bindings from the Agent GSA.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VARS_FILE="${SCRIPT_DIR}/vars.sh"

# ─── ANSI Colors ──────────────────────────────────────────────────────────────
C_CYAN='\033[96m'
C_GREEN='\033[92m'
C_YELLOW='\033[93m'
C_RED='\033[91m'
C_RESET='\033[0m'
C_BOLD='\033[1m'
C_WHITE='\033[97m'

# ─── Argument Parsing ─────────────────────────────────────────────────────────
NO_CONFIRM=0
while [[ "$#" -gt 0 ]]; do
  case $1 in
    --no-confirm|-y) NO_CONFIRM=1 ;;
  esac
  shift
done

# ─── Configuration State Restoration ──────────────────────────────────────────
if [ -f "$VARS_FILE" ]; then
  source "$VARS_FILE"
else
  echo -e "  ${C_YELLOW}⚠ State file ${VARS_FILE} not found. Prompting for target values...${C_RESET}"
  ACTIVE_PROJECT="$(gcloud config get-value project 2>/dev/null || echo "")"
  echo -ne "  ${C_CYAN}Enter Target GCP Project ID [${C_WHITE}${ACTIVE_PROJECT}${C_CYAN}]: ${C_RESET}"
  read -r INPUT_PROJECT_ID
  export PROJECT_ID="${INPUT_PROJECT_ID:-$ACTIVE_PROJECT}"
  if [ -z "$PROJECT_ID" ]; then
    echo -e "  ${C_RED}✗ Project ID is required.${C_RESET}"
    exit 1
  fi
  export NAMESPACE="agent-system"
  export GSA_NAME="platform-agent-bot"
  export KSA_NAME="platform-agent-platform-sa"
fi

# ─── Confirmation Prompt ──────────────────────────────────────────────────────
if [ "$NO_CONFIRM" -ne 1 ]; then
  echo ""
  echo -e "${C_RED}${C_BOLD}🚨 WARNING: This will remove GSA permissions and Workload Identity bindings for the Agent.${C_RESET}"
  echo -e "${C_YELLOW}==============================================================================${C_RESET}"
  echo -e "  ${C_BOLD}GCP Project:${C_RESET}    ${C_BOLD}${PROJECT_ID}${C_RESET}"
  echo -e "  ${C_BOLD}Agent GSA:${C_RESET}      ${C_BOLD}${GSA_NAME}${C_RESET}"
  echo -e "  ${C_BOLD}Namespace:${C_RESET}      ${C_BOLD}${NAMESPACE}${C_RESET}"
  echo -e "  ${C_BOLD}K8s SA:${C_RESET}         ${C_BOLD}${KSA_NAME}${C_RESET}"
  echo -e "${C_YELLOW}==============================================================================${C_RESET}"
  echo ""
  echo -ne "  ${C_CYAN}Are you sure you want to proceed? (y/N): ${C_RESET}"
  read -r -n 1 REPLY
  echo
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
      echo -e "  ${C_YELLOW}ℹ Aborted.${C_RESET}"
      exit 0
  fi
fi

gcloud config set project "$PROJECT_ID" --quiet

# ─── Step 1: Clean up Workload Identity Binding and IAM Roles ─────────────────
gsa_email="${GSA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# Check if GSA exists first
if gcloud iam service-accounts describe "${gsa_email}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo -e "  ${C_CYAN}ℹ Removing Workload Identity Policy Binding...${C_RESET}"
  wi_member="serviceAccount:${PROJECT_ID}.svc.id.goog[${NAMESPACE}/${KSA_NAME}]"
  gcloud iam service-accounts remove-iam-policy-binding "${gsa_email}" \
      --role="roles/iam.workloadIdentityUser" \
      --member="${wi_member}" \
      --project="${PROJECT_ID}" \
      --quiet || true

  echo -e "  ${C_CYAN}ℹ Removing Vertex AI User Role...${C_RESET}"
  gcloud projects remove-iam-policy-binding "${PROJECT_ID}" \
      --member="serviceAccount:${gsa_email}" \
      --role="roles/aiplatform.user" \
      --quiet || true

  echo -e "  ${C_CYAN}ℹ Removing Container Cluster Viewer Role...${C_RESET}"
  gcloud projects remove-iam-policy-binding "${PROJECT_ID}" \
      --member="serviceAccount:${gsa_email}" \
      --role="roles/container.clusterViewer" \
      --quiet || true
  
  echo -e "  ${C_GREEN}✓ Agent GCP IAM bindings successfully removed.${C_RESET}"
else
  echo -e "  ${C_GREEN}✓ GSA '${gsa_email}' does not exist. Skipping IAM policy removals.${C_RESET}"
fi
