#!/usr/bin/env bash
# ==============================================================================
# 🧹 Step 3: Teardown Google Chat & Pub/Sub Setup
# ==============================================================================
# Idempotent script to clean up GChat Pub/Sub Topic/Subscription and the Bot GSA.
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
  export CHAT_TOPIC_NAME="platform-agent-chat-events"
  export CHAT_SUB_NAME="platform-agent-chat-events-sub"
  export GSA_NAME="platform-agent-bot"
fi

# ─── Confirmation Prompt ──────────────────────────────────────────────────────
if [ "$NO_CONFIRM" -ne 1 ]; then
  echo ""
  echo -e "${C_RED}${C_BOLD}🚨 WARNING: This will permanently delete GChat Pub/Sub topic, subscription, and the Bot Service Account.${C_RESET}"
  echo -e "${C_YELLOW}==============================================================================${C_RESET}"
  echo -e "  ${C_BOLD}GCP Project:${C_RESET}    ${C_BOLD}${PROJECT_ID}${C_RESET}"
  echo -e "  ${C_BOLD}Pub/Sub Topic:${C_RESET}  ${C_BOLD}${CHAT_TOPIC_NAME}${C_RESET}"
  echo -e "  ${C_BOLD}Pub/Sub Sub:${C_RESET}    ${C_BOLD}${CHAT_SUB_NAME}${C_RESET}"
  echo -e "  ${C_BOLD}Agent GSA:${C_RESET}      ${C_BOLD}${GSA_NAME}${C_RESET}"
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

# ─── Step 1: Delete Pub/Sub Subscription ──────────────────────────────────────
SUB_EXISTS=$(gcloud pubsub subscriptions list --filter="name:projects/${PROJECT_ID}/subscriptions/${CHAT_SUB_NAME}" --format="value(name)" --project="${PROJECT_ID}" 2>/dev/null || echo "")
if [ -n "$SUB_EXISTS" ]; then
  echo -e "  ${C_CYAN}ℹ Deleting Pub/Sub Subscription '${CHAT_SUB_NAME}'...${C_RESET}"
  gcloud pubsub subscriptions delete "${CHAT_SUB_NAME}" --project="${PROJECT_ID}" --quiet || true
  echo -e "  ${C_GREEN}✓ Pub/Sub Subscription successfully removed.${C_RESET}"
else
  echo -e "  ${C_GREEN}✓ Pub/Sub Subscription '${CHAT_SUB_NAME}' does not exist.${C_RESET}"
fi

# ─── Step 2: Delete Pub/Sub Topic ─────────────────────────────────────────────
TOPIC_EXISTS=$(gcloud pubsub topics list --filter="name:projects/${PROJECT_ID}/topics/${CHAT_TOPIC_NAME}" --format="value(name)" --project="${PROJECT_ID}" 2>/dev/null || echo "")
if [ -n "$TOPIC_EXISTS" ]; then
  echo -e "  ${C_CYAN}ℹ Deleting Pub/Sub Topic '${CHAT_TOPIC_NAME}'...${C_RESET}"
  gcloud pubsub topics delete "${CHAT_TOPIC_NAME}" --project="${PROJECT_ID}" --quiet || true
  echo -e "  ${C_GREEN}✓ Pub/Sub Topic successfully removed.${C_RESET}"
else
  echo -e "  ${C_GREEN}✓ Pub/Sub Topic '${CHAT_TOPIC_NAME}' does not exist.${C_RESET}"
fi

# ─── Step 3: Delete Agent GSA ─────────────────────────────────────────────────
gsa_email="${GSA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
GSA_EXISTS=$(gcloud iam service-accounts list --filter="email=${gsa_email}" --format="value(email)" --project="${PROJECT_ID}" 2>/dev/null || echo "")
if [ -n "$GSA_EXISTS" ]; then
  echo -e "  ${C_CYAN}ℹ Deleting Bot GSA '${gsa_email}'...${C_RESET}"
  gcloud iam service-accounts delete "${gsa_email}" --project="${PROJECT_ID}" --quiet || true
  echo -e "  ${C_GREEN}✓ Bot GSA successfully removed.${C_RESET}"
else
  echo -e "  ${C_GREEN}✓ Bot GSA '${gsa_email}' does not exist.${C_RESET}"
fi
