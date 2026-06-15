#!/usr/bin/env bash
# ==============================================================================
# 🧹 Master GKE Standard & Cloud-Agnostic Operator E2E Teardown Script
# ==============================================================================
# Master script to orchestrate the clean up and deletion of all GCP and GKE
# resources provisioned by the provisioning scripts.
# ==============================================================================

set -euo pipefail

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
C_RED='\033[91m'
C_RESET='\033[0m'
C_BOLD='\033[1m'
C_WHITE='\033[97m'

# ─── Configuration State Restoration ──────────────────────────────────────────
if [ -f "$VARS_FILE" ]; then
  # shellcheck disable=SC1090
  source "$VARS_FILE"
else
  # Prompt for project ID if state not found
  ACTIVE_PROJECT="$(gcloud config get-value project 2>/dev/null || echo "")"
  echo -ne "  ${C_CYAN}Enter Target GCP Project ID [${C_WHITE}${ACTIVE_PROJECT}${C_CYAN}]: ${C_RESET}"
  read -r INPUT_PROJECT_ID
  export PROJECT_ID="${INPUT_PROJECT_ID:-$ACTIVE_PROJECT}"
  if [ -z "$PROJECT_ID" ]; then
    echo -e "  ${C_RED}✗ Project ID is required.${C_RESET}"
    exit 1
  fi
  export CLUSTER_NAME="platform-agent-host"
fi

# ─── Confirmation Prompt ──────────────────────────────────────────────────────
echo ""
echo -e "${C_RED}${C_BOLD}🚨 WARNING: This will permanently delete all GChat integration resources, GKE cluster, GCP resources, Secret Manager keys, and docker images.${C_RESET}"
echo -e "${C_YELLOW}==============================================================================${C_RESET}"
echo -e "  ${C_BOLD}GCP Project:${C_RESET}    ${C_BOLD}${PROJECT_ID}${C_RESET}"
echo -e "  ${C_BOLD}GKE Cluster:${C_RESET}    ${C_BOLD}${CLUSTER_NAME:-platform-agent-host}${C_RESET}"
echo -e "${C_YELLOW}==============================================================================${C_RESET}"
echo ""
echo -ne "  ${C_CYAN}Are you sure you want to proceed with teardown? (y/N): ${C_RESET}"
read -r -n 1 REPLY
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo -e "  ${C_YELLOW}ℹ Teardown aborted.${C_RESET}"
    exit 0
fi

# Execute teardown steps with --no-confirm to avoid sub-script prompts
echo -e "\n${C_RED}${C_BOLD}🧹 Running Teardown Steps...${C_RESET}"
"${SCRIPT_DIR}/teardown_01_gchat.sh" --no-confirm
"${SCRIPT_DIR}/teardown_02_cluster_and_gcp.sh" --no-confirm

echo -e "\n${C_GREEN}${C_BOLD}====================================================${C_RESET}"
echo -e "${C_GREEN}${C_BOLD}✅ Teardown Complete! All GChat GKE & GCP resources clean.${C_RESET}"
echo -e "${C_GREEN}${C_BOLD}====================================================${C_RESET}"
