#!/usr/bin/env bash
# ==============================================================================
# 🧹 Step 5: Teardown Google Chat Setup
# ==============================================================================
# Idempotent script to clean up any GChat Pub/Sub and HTTP resources
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VARS_FILE="${SCRIPT_DIR}/vars.sh"

# ─── ANSI Colors ──────────────────────────────────────────────────────────────
source "${SCRIPT_DIR}/common.sh" "$@"

# ─── Configuration State Restoration ──────────────────────────────────────────
ensure_teardown_state

# ─── Confirmation Prompt ──────────────────────────────────────────────────────
confirm_action "This will clean up any legacy GChat Pub/Sub resources." \
  "GCP Project:$PROJECT_ID"

gcloud config set project "$PROJECT_ID" --quiet

# ─── Step 1: Clean Up Legacy Pub/Sub Subscription ─────────────────────────────
if [ -n "${CHAT_SUB_NAME:-}" ] && gcloud pubsub subscriptions describe "${CHAT_SUB_NAME}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo -e "  ${C_CYAN}ℹ Deleting legacy Pub/Sub Subscription '${CHAT_SUB_NAME}'...${C_RESET}"
  if [ "${DRY_RUN:-0}" -eq 1 ]; then
    echo -e "  ${C_GREEN}[DRY-RUN] Would delete legacy Pub/Sub Subscription '${CHAT_SUB_NAME}'.${C_RESET}"
  else
    gcloud pubsub subscriptions delete "${CHAT_SUB_NAME}" --project="${PROJECT_ID}" --quiet || true
    echo -e "  ${C_GREEN}✓ Legacy Pub/Sub Subscription successfully removed.${C_RESET}"
  fi
elif [ -n "${CHAT_SUB_NAME:-}" ]; then
  echo -e "  ${C_GREEN}✓ Legacy Pub/Sub Subscription '${CHAT_SUB_NAME}' does not exist.${C_RESET}"
fi

# ─── Step 2: Clean Up Legacy Pub/Sub Topic ────────────────────────────────────
if [ -n "${CHAT_TOPIC_NAME:-}" ] && gcloud pubsub topics describe "${CHAT_TOPIC_NAME}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo -e "  ${C_CYAN}ℹ Deleting legacy Pub/Sub Topic '${CHAT_TOPIC_NAME}'...${C_RESET}"
  if [ "${DRY_RUN:-0}" -eq 1 ]; then
    echo -e "  ${C_GREEN}[DRY-RUN] Would delete legacy Pub/Sub Topic '${CHAT_TOPIC_NAME}'.${C_RESET}"
  else
    gcloud pubsub topics delete "${CHAT_TOPIC_NAME}" --project="${PROJECT_ID}" --quiet || true
    echo -e "  ${C_GREEN}✓ Legacy Pub/Sub Topic successfully removed.${C_RESET}"
  fi
elif [ -n "${CHAT_TOPIC_NAME:-}" ]; then
  echo -e "  ${C_GREEN}✓ Legacy Pub/Sub Topic '${CHAT_TOPIC_NAME}' does not exist.${C_RESET}"
fi

# ─── Step 3: Verify HTTP Webhook Teardown Status ──────────────────────────────
echo -e "  ${C_GREEN}✓ Google Chat HTTP Webhook requires no separate GCP infrastructure teardown (Ingress & SSL are managed by GKE).${C_RESET}"

