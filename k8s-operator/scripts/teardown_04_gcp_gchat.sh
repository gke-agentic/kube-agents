#!/usr/bin/env bash
# ==============================================================================
# ==============================================================================
# 🧹 Step 5: Teardown Google Chat Setup
# ==============================================================================
# Idempotent script to clean up any legacy GChat Pub/Sub and notify about HTTP setup.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VARS_FILE="${SCRIPT_DIR}/vars.sh"

source "${SCRIPT_DIR}/common.sh" "$@"

ensure_teardown_state

confirm_action "This will clean up any legacy GChat Pub/Sub resources." \
  "GCP Project:$PROJECT_ID"

gcloud config set project "$PROJECT_ID" --quiet

# Clean up legacy Pub/Sub resources if they exist from prior deployments
if [ -n "${CHAT_SUB_NAME:-}" ] && gcloud pubsub subscriptions describe "${CHAT_SUB_NAME}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo -e "  ${C_CYAN}ℹ Deleting legacy Pub/Sub Subscription '${CHAT_SUB_NAME}'...${C_RESET}"
  gcloud pubsub subscriptions delete "${CHAT_SUB_NAME}" --project="${PROJECT_ID}" --quiet || true
fi

if [ -n "${CHAT_TOPIC_NAME:-}" ] && gcloud pubsub topics describe "${CHAT_TOPIC_NAME}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo -e "  ${C_CYAN}ℹ Deleting legacy Pub/Sub Topic '${CHAT_TOPIC_NAME}'...${C_RESET}"
  gcloud pubsub topics delete "${CHAT_TOPIC_NAME}" --project="${PROJECT_ID}" --quiet || true
fi

echo -e "  ${C_GREEN}✓ Google Chat HTTP Webhook requires no separate GCP infrastructure teardown (Ingress & SSL are managed by GKE).${C_RESET}"
