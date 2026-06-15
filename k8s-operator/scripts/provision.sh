#!/usr/bin/env bash
# ==============================================================================
# 🤖 Master GKE Standard & Cloud-Agnostic Operator E2E Provisioner
# ==============================================================================
# Orchestrates GCP/GKE bootstrapping, operator and agent container builds,
# manual GSA/PubSub setup, IAM configuration, and CR application.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ─── ANSI Colors ──────────────────────────────────────────────────────────────
C_MAGENTA='\033[95m'
C_BOLD='\033[1m'
C_RESET='\033[0m'

# ─── Argument Parsing ─────────────────────────────────────────────────────────
DRY_RUN_ARG=""
FORCE_BUILD_ARG=""
while [[ "$#" -gt 0 ]]; do
  case $1 in
    --dry-run) DRY_RUN_ARG="--dry-run" ;;
    --force-build) FORCE_BUILD_ARG="--force-build" ;;
  esac
  shift
done

echo -e "${C_MAGENTA}${C_BOLD}🚀 Starting GKE Platform Agent provisioning pipeline...${C_RESET}"

"${SCRIPT_DIR}/provision_01_gcp_cluster.sh" $DRY_RUN_ARG
"${SCRIPT_DIR}/provision_02_gcp_secrets.sh" $DRY_RUN_ARG
"${SCRIPT_DIR}/provision_03_gcp_gchat.sh" $DRY_RUN_ARG
"${SCRIPT_DIR}/provision_04_gcp_iam.sh" $DRY_RUN_ARG
"${SCRIPT_DIR}/provision_05_gcp_deploy.sh" $DRY_RUN_ARG

echo -e "\n${C_MAGENTA}${C_BOLD}>>>  Infrastructure & Cloud Resources Provisioned Successfully!  <<<${C_RESET}"