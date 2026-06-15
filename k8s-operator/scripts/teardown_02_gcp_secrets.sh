#!/usr/bin/env bash
# ==============================================================================
# 🧹 Step 2: Teardown Secret Manager & GKE Secrets
# ==============================================================================
# Idempotent script to clean up Kubernetes secrets and Google Secret Manager keys.
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
  export NAMESPACE="agent-system"
fi

# ─── Confirmation Prompt ──────────────────────────────────────────────────────
if [ "$NO_CONFIRM" -ne 1 ]; then
  echo ""
  echo -e "${C_RED}${C_BOLD}🚨 WARNING: This will permanently delete GKE platform-agent-secrets and GCP Secret Manager GEMINI_API_KEY secret.${C_RESET}"
  echo -e "${C_YELLOW}==============================================================================${C_RESET}"
  echo -e "  ${C_BOLD}GCP Project:${C_RESET}    ${C_BOLD}${PROJECT_ID}${C_RESET}"
  echo -e "  ${C_BOLD}Namespace:${C_RESET}      ${C_BOLD}${NAMESPACE}${C_RESET}"
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

# ─── Step 1: Connect to GKE Cluster & Delete K8s Secret ───────────────────────
CLUSTER_EXISTS=$(gcloud container clusters list --filter="name=${CLUSTER_NAME} AND zone:${REGION}*" --format="value(name)" --project="${PROJECT_ID}" 2>/dev/null || echo "")
if [ -n "$CLUSTER_EXISTS" ]; then
  echo -e "  ${C_CYAN}ℹ Fetching cluster credentials...${C_RESET}"
  gcloud container clusters get-credentials "$CLUSTER_NAME" --region "$REGION" --project "$PROJECT_ID" --quiet || true

  # Check if Namespace exists
  NS_EXISTS=$(kubectl get namespace "${NAMESPACE}" --ignore-not-found 2>/dev/null || echo "")
  if [ -n "$NS_EXISTS" ]; then
    SECRET_EXISTS=$(kubectl get secret platform-agent-secrets -n "${NAMESPACE}" --ignore-not-found 2>/dev/null || echo "")
    if [ -n "$SECRET_EXISTS" ]; then
      echo -e "  ${C_CYAN}ℹ Deleting GKE Secret 'platform-agent-secrets' from namespace '${NAMESPACE}'...${C_RESET}"
      kubectl delete secret platform-agent-secrets -n "${NAMESPACE}" --ignore-not-found || true
      echo -e "  ${C_GREEN}✓ GKE Secret successfully deleted.${C_RESET}"
    else
      echo -e "  ${C_GREEN}✓ GKE Secret 'platform-agent-secrets' does not exist in namespace '${NAMESPACE}'.${C_RESET}"
    fi
  else
    echo -e "  ${C_GREEN}✓ Namespace '${NAMESPACE}' does not exist.${C_RESET}"
  fi
else
  echo -e "  ${C_GREEN}✓ GKE cluster '${CLUSTER_NAME}' does not exist. Skipping K8s secret deletion.${C_RESET}"
fi

# ─── Step 2: Delete Secret Manager Secret ─────────────────────────────────────
SECRET_EXISTS=$(gcloud secrets list --filter="name:GEMINI_API_KEY" --format="value(name)" --project="${PROJECT_ID}" 2>/dev/null || echo "")
if [ -n "$SECRET_EXISTS" ]; then
  echo -e "  ${C_CYAN}ℹ Deleting Secret 'GEMINI_API_KEY' from Google Secret Manager...${C_RESET}"
  gcloud secrets delete "GEMINI_API_KEY" --project="${PROJECT_ID}" --quiet || true
  echo -e "  ${C_GREEN}✓ GCP Secret 'GEMINI_API_KEY' successfully deleted.${C_RESET}"
else
  echo -e "  ${C_GREEN}✓ GCP Secret 'GEMINI_API_KEY' does not exist.${C_RESET}"
fi
