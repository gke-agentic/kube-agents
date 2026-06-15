#!/usr/bin/env bash
# ==============================================================================
# 🤖 Master GKE Standard & Cloud-Agnostic Operator E2E Provisioner
# ==============================================================================
# Orchestrates GCP/GKE bootstrapping, operator and agent container builds,
# manual GSA/PubSub setup, and CR application.
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

# ─── Argument Parsing ─────────────────────────────────────────────────────────
DRY_RUN=0
FORCE_BUILD=0
DRY_RUN_ARG=""
FORCE_BUILD_ARG=""
while [[ "$#" -gt 0 ]]; do
  case $1 in
    --dry-run)
      DRY_RUN=1
      DRY_RUN_ARG="--dry-run"
      ;;
    --force-build)
      FORCE_BUILD=1
      FORCE_BUILD_ARG="--force-build"
      ;;
  esac
  shift
done

# Execute the modular provisioning scripts
echo -e "${C_MAGENTA}${C_BOLD}🚀 Starting GKE Platform Agent provisioning pipeline...${C_RESET}"

"${SCRIPT_DIR}/provision_01_cluster_and_gcp.sh" $DRY_RUN_ARG
"${SCRIPT_DIR}/provision_02_build_push_operator.sh" $DRY_RUN_ARG $FORCE_BUILD_ARG
"${SCRIPT_DIR}/provision_03_build_push_agents.sh" $DRY_RUN_ARG $FORCE_BUILD_ARG
"${SCRIPT_DIR}/provision_04_setup_gchat.sh" $DRY_RUN_ARG

# Sourcing variables for the checklist
if [ -f "$VARS_FILE" ]; then
  source "$VARS_FILE"
fi

# ─── Conclusion Checklist ─────────────────────────────────────────────────────
echo -e "\n${C_MAGENTA}${C_BOLD}>>>  Infrastructure & Cloud Resources Provisioned Successfully!  <<<${C_RESET}"

echo -e "${C_YELLOW}${C_BOLD}======================= START COPY&PASTE =======================${C_RESET}"
echo -e "${C_YELLOW}Your GKE Platform Agent resources have been successfully initialized!${C_RESET}"
echo -e "Recommend you copy-paste this final step checklist to complete setup:\n"

echo -e "[ ] 1. Configure GChat bot connection in GCP Console:"
echo -e "       ${C_WHITE}https://console.cloud.google.com/apis/api/chat.googleapis.com/hangouts-chat?project=${PROJECT_ID:-<PROJECT_ID>}${C_RESET}"
echo -e "       - Name: ${C_GREEN}GKE Platform Agent Bot${C_RESET}"
echo -e "       - Avatar: ${C_GREEN}https://platform-agent.nousresearch.com/docs/img/logo.png${C_RESET}"
echo -e "       - Connection Settings: Select ${C_BOLD}Cloud Pub/Sub${C_RESET}"
echo -e "       - Pub/Sub Topic Name: ${C_GREEN}projects/${PROJECT_ID:-<PROJECT_ID>}/topics/${CHAT_TOPIC_NAME:-platform-agent-chat-events}${C_RESET}"
echo -e "       - Under Visibility, check: ${C_GREEN}Only specific people (add your email ${ALLOWED_USER:-<EMAIL>})${C_RESET}"

echo -e ""
echo -e "[ ] 2. Run the new Operator manager locally or deploy it:"
echo -e "       To run locally: ${C_WHITE}ENABLE_WEBHOOKS=false make run${C_RESET} (from k8s-operator directory)"
echo -e "       To deploy to cluster: ${C_WHITE}make deploy IMG=${REGION:-us-east4}-docker.pkg.dev/${PROJECT_ID:-<PROJECT_ID>}/${REPO_NAME:-platform-agent-repo}/kube-agents-operator:latest${C_RESET}"

echo -e ""
echo -e "[ ] 3. Monitor Gateway pod rollout progress:"
echo -e "       ${C_WHITE}kubectl get pods -n ${NAMESPACE:-agent-system}${C_RESET}"

echo -e ""
echo -e "[ ] 4. Send a DM to the Bot on Google Chat:"
echo -e "       Type: ${C_WHITE}\"Hi Hermes\"${C_RESET}"

echo -e ""
echo -e "[ ] 5. ${C_YELLOW}[Optional]${C_RESET} Approve pairing code in GKE container:"
echo -e "       ${C_CYAN}(Only required for first-time bot deployments. If the bot responds instantly, skip this!)${C_RESET}"
echo -e "       ${C_WHITE}kubectl exec -it deploy/platform-agent-gateway -n ${NAMESPACE:-agent-system} -- hermes pairing approve google_chat <PAIRING_CODE>${C_RESET}"

echo -e ""
echo -e "======================== END COPY&PASTE ========================\n"
