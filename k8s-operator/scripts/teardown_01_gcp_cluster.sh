#!/usr/bin/env bash
# ==============================================================================
# 🧹 Step 1: Teardown GKE Cluster & Local State
# ==============================================================================
# Idempotent script to clean up the GKE Standard Cluster and local state files.
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
  export REGION="us-east4"
  export CLUSTER_NAME="platform-agent-host"
fi

# ─── Confirmation Prompt ──────────────────────────────────────────────────────
if [ "$NO_CONFIRM" -ne 1 ]; then
  echo ""
  echo -e "${C_RED}${C_BOLD}🚨 WARNING: This will permanently delete GKE cluster '$CLUSTER_NAME' and local configuration state vars.sh.${C_RESET}"
  echo -e "${C_YELLOW}==============================================================================${C_RESET}"
  echo -e "  ${C_BOLD}GCP Project:${C_RESET}    ${C_BOLD}${PROJECT_ID}${C_RESET}"
  echo -e "  ${C_BOLD}Region:${C_RESET}         ${C_BOLD}${REGION}${C_RESET}"
  echo -e "  ${C_BOLD}GKE Cluster:${C_RESET}    ${C_BOLD}${CLUSTER_NAME}${C_RESET}"
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

# ─── Step 1: Delete GKE Cluster ───────────────────────────────────────────────
CLUSTER_EXISTS=$(gcloud container clusters list --filter="name=${CLUSTER_NAME} AND zone:${REGION}*" --format="value(name)" --project="${PROJECT_ID}" 2>/dev/null || echo "")
if [ -n "$CLUSTER_EXISTS" ]; then
  echo -e "  ${C_CYAN}ℹ Deleting GKE Standard Cluster '$CLUSTER_NAME' in region '$REGION'...${C_RESET}"
  echo -e "    ${C_YELLOW}Note: This takes approximately 5-8 minutes in Google Cloud...${C_RESET}"
  gcloud container clusters delete "$CLUSTER_NAME" --region="$REGION" --project="${PROJECT_ID}" --quiet
  echo -e "  ${C_GREEN}✓ GKE Cluster '$CLUSTER_NAME' successfully deleted.${C_RESET}"
else
  echo -e "  ${C_GREEN}✓ GKE Cluster '$CLUSTER_NAME' does not exist.${C_RESET}"
fi

# ─── Step 2: Clean up Local State Files ───────────────────────────────────────
if [ -f "$VARS_FILE" ]; then
  if [ "$NO_CONFIRM" -ne 1 ]; then
    echo -ne "  ${C_CYAN}Do you want to delete the local state file vars.sh? (y/N): ${C_RESET}"
    read -r -n 1 REMOVE_VARS || true
    echo
  else
    REMOVE_VARS="y"
  fi
  if [[ ${REMOVE_VARS:-n} =~ ^[Yy]$ ]]; then
    rm -f "$VARS_FILE"
    echo -e "  ${C_GREEN}✓ Deleted ${VARS_FILE}${C_RESET}"
  else
    echo -e "  ${C_GREEN}✓ Kept ${VARS_FILE} for subsequent provisioning.${C_RESET}"
  fi
fi
