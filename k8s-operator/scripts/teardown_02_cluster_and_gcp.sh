#!/usr/bin/env bash
# ==============================================================================
# 🧹 Step 2: Teardown LiteLLM, Secret Manager, Registry, Cluster & Local State
# ==============================================================================
# Idempotent script to clean up LiteLLM, Secret Manager secrets, Artifact
# Registry repo, the GKE cluster itself, and local generated state files.
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

# ─── Argument Parsing ─────────────────────────────────────────────────────────
NO_CONFIRM=0
while [[ "$#" -gt 0 ]]; do
  case $1 in
    --no-confirm|-y) NO_CONFIRM=1 ;;
  esac
  shift
done

# ─── Configuration State Restoration ──────────────────────────────────────────
echo -e "${C_BOLD}=== Restoring Configuration State ===${C_RESET}"
if [ -f "$VARS_FILE" ]; then
  echo -e "  ${C_GREEN}✓ Loading state variables from ${VARS_FILE}...${C_RESET}"
  source "$VARS_FILE"
else
  echo -e "  ${C_YELLOW}⚠ State file ${VARS_FILE} not found. Prompting for target values...${C_RESET}"
  
  # 1. Get active GCP Project ID
  ACTIVE_PROJECT="$(gcloud config get-value project 2>/dev/null || echo "")"
  echo -ne "  ${C_CYAN}Enter Target GCP Project ID [${C_WHITE}${ACTIVE_PROJECT}${C_CYAN}]: ${C_RESET}"
  read -r INPUT_PROJECT_ID
  export PROJECT_ID="${INPUT_PROJECT_ID:-$ACTIVE_PROJECT}"
  if [ -z "$PROJECT_ID" ]; then
    echo -e "  ${C_RED}✗ Project ID is required.${C_RESET}"
    exit 1
  fi

  # 2. Get Region
  DEFAULT_REGION="us-east4"
  echo -ne "  ${C_CYAN}Enter GKE GCP Region [${C_WHITE}${DEFAULT_REGION}${C_CYAN}]: ${C_RESET}"
  read -r INPUT_REGION
  export REGION="${INPUT_REGION:-$DEFAULT_REGION}"

  # 3. Get Cluster Name
  DEFAULT_CLUSTER="platform-agent-host"
  echo -ne "  ${C_CYAN}Enter GKE Cluster Name [${C_WHITE}${DEFAULT_CLUSTER}${C_CYAN}]: ${C_RESET}"
  read -r INPUT_CLUSTER
  export CLUSTER_NAME="${INPUT_CLUSTER:-$DEFAULT_CLUSTER}"

  # 4. Get Namespace
  DEFAULT_NAMESPACE="agent-system"
  echo -ne "  ${C_CYAN}Enter GKE Target Namespace [${C_WHITE}${DEFAULT_NAMESPACE}${C_CYAN}]: ${C_RESET}"
  read -r INPUT_NAMESPACE
  export NAMESPACE="${INPUT_NAMESPACE:-$DEFAULT_NAMESPACE}"

  export REPO_NAME="platform-agent-repo"
fi

# ─── Confirmation Prompt ──────────────────────────────────────────────────────
if [ "$NO_CONFIRM" -ne 1 ]; then
  echo ""
  echo -e "${C_RED}${C_BOLD}🚨 WARNING: This will permanently delete GKE cluster, LiteLLM gateway, GCP Secret Manager keys, and Artifact Registry repository.${C_RESET}"
  echo -e "${C_YELLOW}==============================================================================${C_RESET}"
  echo -e "  ${C_BOLD}GCP Project:${C_RESET}    ${C_BOLD}${PROJECT_ID}${C_RESET}"
  echo -e "  ${C_BOLD}Region:${C_RESET}         ${C_BOLD}${REGION}${C_RESET}"
  echo -e "  ${C_BOLD}GKE Cluster:${C_RESET}    ${C_BOLD}${CLUSTER_NAME}${C_RESET}"
  echo -e "  ${C_BOLD}Artifact Repo:${C_RESET}  ${C_BOLD}${REPO_NAME}${C_RESET}"
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

# ─── Connect to GKE Cluster ───────────────────────────────────────────
echo -e "\n${C_BOLD}=== Connecting to GKE Cluster ===${C_RESET}"
CLUSTER_EXISTS=$(gcloud container clusters list --filter="name=${CLUSTER_NAME} AND zone:${REGION}*" --format="value(name)" --project="${PROJECT_ID}" 2>/dev/null || echo "")
if [ -n "$CLUSTER_EXISTS" ]; then
  echo -e "  ${C_CYAN}ℹ Fetching cluster credentials...${C_RESET}"
  gcloud container clusters get-credentials "$CLUSTER_NAME" --region "$REGION" --project "$PROJECT_ID" --quiet || true
else
  echo -e "  ${C_GREEN}✓ GKE cluster '${CLUSTER_NAME}' does not exist.${C_RESET}"
fi

# ─── Step 1: Undeploy LiteLLM Gateway ───────────────────────────────────────
if [ -n "$CLUSTER_EXISTS" ]; then
  echo -e "\n${C_BOLD}=== 1. Tearing Down LiteLLM Gateway ===${C_RESET}"
  echo -e "  ${C_CYAN}ℹ Deleting LiteLLM service, deployment, and configmap...${C_RESET}"
  kubectl delete service litellm -n "$NAMESPACE" --ignore-not-found || true
  kubectl delete deployment litellm -n "$NAMESPACE" --ignore-not-found || true
  kubectl delete configmap litellm-config -n "$NAMESPACE" --ignore-not-found || true
  echo -e "  ${C_GREEN}✓ LiteLLM Gateway successfully torn down.${C_RESET}"
fi

# ─── Step 2: Delete Secret Manager Placeholders ───────────────────────────────
echo -e "\n${C_BOLD}=== 2. Tearing Down Secret Manager Placeholders ===${C_RESET}"
SECRETS_TO_DELETE=("GEMINI_API_KEY")
for SECRET in "${SECRETS_TO_DELETE[@]}"; do
  SECRET_EXISTS=$(gcloud secrets list --filter="name:${SECRET}" --format="value(name)" --project="${PROJECT_ID}" 2>/dev/null || echo "")
  if [ -n "$SECRET_EXISTS" ]; then
    echo -e "  ${C_CYAN}ℹ Deleting Secret '$SECRET'...${C_RESET}"
    gcloud secrets delete "$SECRET" --project="${PROJECT_ID}" --quiet || true
    echo -e "  ${C_GREEN}✓ Secret '$SECRET' successfully deleted.${C_RESET}"
  else
    echo -e "  ${C_GREEN}✓ Secret '$SECRET' already deleted or does not exist.${C_RESET}"
  fi
done

# ─── Step 3: Delete Artifact Registry Repository ──────────────────────────────
echo -e "\n${C_BOLD}=== 3. Tearing Down Artifact Registry Repo ===${C_RESET}"
if [ -n "${REPO_NAME:-}" ]; then
  if gcloud artifacts repositories describe "$REPO_NAME" --location="$REGION" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    echo -e "  ${C_CYAN}ℹ Deleting Artifact Registry repository '$REPO_NAME'...${C_RESET}"
    gcloud artifacts repositories delete "$REPO_NAME" --location="$REGION" --project="${PROJECT_ID}" --quiet || true
    echo -e "  ${C_GREEN}✓ Artifact Registry repository '$REPO_NAME' successfully deleted.${C_RESET}"
  else
    echo -e "  ${C_GREEN}✓ Repository '$REPO_NAME' already deleted or does not exist.${C_RESET}"
  fi
fi

# ─── Step 4: Delete GKE Cluster ───────────────────────────────────────────────
echo -e "\n${C_BOLD}=== 4. Tearing Down GKE Cluster ===${C_RESET}"
if [ -n "$CLUSTER_EXISTS" ]; then
  echo -e "  ${C_CYAN}ℹ Deleting GKE Standard Cluster '$CLUSTER_NAME' in region '$REGION'...${C_RESET}"
  echo -e "    ${C_YELLOW}Note: This takes approximately 5-8 minutes in Google Cloud...${C_RESET}"
  gcloud container clusters delete "$CLUSTER_NAME" --region="$REGION" --project="${PROJECT_ID}" --quiet
  echo -e "  ${C_GREEN}✓ GKE Cluster '$CLUSTER_NAME' successfully deleted.${C_RESET}"
else
  echo -e "  ${C_GREEN}✓ Cluster '$CLUSTER_NAME' already deleted or does not exist.${C_RESET}"
fi

# ─── Step 5: Clean up Local State Files ───────────────────────────────────────
echo -e "\n${C_BOLD}=== 5. Cleaning up Local Generated Files ===${C_RESET}"
if [ -f "$VARS_FILE" ]; then
  if [ "$NO_CONFIRM" -ne 1 ]; then
    echo -ne "  ${C_CYAN}Do you want to delete the local state file vars.sh? (keeps settings for next provision if kept) (y/N): ${C_RESET}"
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

local_yaml="${SCRIPT_DIR}/platform-agent.yaml"
if [ -f "$local_yaml" ]; then
  rm -f "$local_yaml"
  echo -e "  ${C_GREEN}✓ Deleted platform-agent.yaml${C_RESET}"
fi
