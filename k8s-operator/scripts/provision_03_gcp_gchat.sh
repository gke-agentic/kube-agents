#!/usr/bin/env bash
# ==============================================================================
# 🤖 Step 3: Google Chat & Pub/Sub Setup
# ==============================================================================
# Configures the Google Chat backend: Pub/Sub routing, the Agent's Service Account,
# and grants the Service Account permission to read incoming chat messages.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VARS_FILE="${SCRIPT_DIR}/vars.sh"

# ─── ANSI Colors ──────────────────────────────────────────────────────────────
C_CYAN='\033[96m'
C_GREEN='\033[92m'
C_YELLOW='\033[93m'
C_MAGENTA='\033[95m'
C_RED='\033[91m'
C_RESET='\033[0m'
C_BOLD='\033[1m'
C_WHITE='\033[97m'

# ─── UI Helpers ───────────────────────────────────────────────────────────────
print_step() { echo -e "\n${C_MAGENTA}${C_BOLD}>>>  $1  <<<${C_RESET}"; }
print_success() { echo -e "  ${C_GREEN}✓ $1${C_RESET}"; }
print_info() { echo -e "  ${C_CYAN}ℹ $1${C_RESET}"; }
print_warning() { echo -e "  ${C_YELLOW}⚠ $1${C_RESET}"; }
print_error() { echo -e "  ${C_RED}✗ $1${C_RESET}"; }

wait_for_a_bit() {
  local seconds=$1
  local msg=$2
  local spinner=( "⠋" "⠙" "⠹" "⠸" "⠼" "⠴" "⠦" "⠧" "⠇" "⠏" )
  echo -ne "  ${C_YELLOW}${msg} (${seconds}s)...  "
  tput civis 2>/dev/null || true
  for (( i=0; i<seconds*10; i++ )); do
    local idx=$(( i % 10 ))
    echo -ne "\b${spinner[$idx]}"
    sleep 0.1
  done
  echo -ne "\b ${C_RESET}\n"
  tput cnorm 2>/dev/null || true
}

cleanup() { tput cnorm 2>/dev/null || true; }
trap cleanup EXIT

# ─── Argument Parsing ─────────────────────────────────────────────────────────
DRY_RUN=0
while [[ "$#" -gt 0 ]]; do
  case $1 in --dry-run) DRY_RUN=1 ;; esac
  shift
done

# ─── Configuration & State Restoration ────────────────────────────────────────
print_step "Setting up Configuration State for GChat Setup"

if [ ! -f "$VARS_FILE" ]; then
  echo "# SRE Sourced Variables for GKE & GCP Setup" > "$VARS_FILE"
fi
source "$VARS_FILE"

init_var() {
  local var_name=$1
  local default_val=$2
  local prompt_msg=$3
  if [ -z "${!var_name}" ]; then
    echo -ne "  ${C_CYAN}${prompt_msg} [${C_WHITE}${default_val}${C_CYAN}]: ${C_RESET}"
    read -r input_val
    local final_val="${input_val:-$default_val}"
    export "${var_name}=${final_val}"
    echo "export ${var_name}=\"${final_val}\"" >> "$VARS_FILE"
  fi
}

ACTIVE_PROJECT="$(gcloud config get-value project 2>/dev/null || echo "")"
DEFAULT_PROJECT_ID="${ACTIVE_PROJECT:-$(whoami 2>/dev/null || echo "user")}"

init_var "PROJECT_ID" "$DEFAULT_PROJECT_ID" "Enter Target GCP Project ID"

if [ -z "$PROJECT_NUMBER" ]; then
  print_info "Resolving numeric Project Number for $PROJECT_ID..."
  PROJECT_NUMBER_VAL=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)" 2>/dev/null || echo "")
  if [ -z "$PROJECT_NUMBER_VAL" ]; then
    echo -ne "  ${C_YELLOW}Failed to resolve project number automatically. Please enter it manually: ${C_RESET}"
    read -r PROJECT_NUMBER_VAL
  fi
  export PROJECT_NUMBER="$PROJECT_NUMBER_VAL"
  echo "export PROJECT_NUMBER=\"${PROJECT_NUMBER}\"" >> "$VARS_FILE"
  print_success "Project Number resolved: $PROJECT_NUMBER"
fi

DEFAULT_USER=""
init_var "ALLOWED_USER" "$DEFAULT_USER" "Enter Allowed Google Chat User Email"
init_var "CHAT_TOPIC_NAME" "platform-agent-chat-events" "Enter Pub/Sub Topic Name"
init_var "CHAT_SUB_NAME" "platform-agent-chat-events-sub" "Enter Pub/Sub Subscription Name"
init_var "GSA_NAME" "platform-agent-bot" "Enter Service Account Name for the Agent"

# ─── Prerequisites Check ──────────────────────────────────────────────────────
print_step "Checking Local Prerequisites"
PREREQS=("gcloud")
for cmd in "${PREREQS[@]}"; do
  echo -ne "  ${C_CYAN}Checking for $cmd... ${C_RESET}"
  if command -v "$cmd" &> /dev/null; then echo -e "✅"; else echo -e "❌"; print_error "$cmd is required."; exit 1; fi
done

# ─── Step Runner Framework ────────────────────────────────────────────────────
run_step() {
  local name=$1; local verify_func=$2; local execute_func=$3; local wait_time=$4
  print_step "$name"
  echo -e "  ${C_CYAN}Verifying current GCP state...${C_RESET}"
  if $verify_func; then print_success "Already completed: $name"; return 0; fi
  if [ "$DRY_RUN" -eq 1 ]; then print_info "[DRY-RUN] Would execute: $name"; return 0; fi

  print_info "Executing action..."
  if $execute_func; then
    print_success "Successfully executed."
    if [ -n "$wait_time" ] && [ "$wait_time" -gt 0 ]; then wait_for_a_bit "$wait_time" "Waiting for changes to propagate"; fi
  else
    print_error "Failed to execute step: $name"; exit 1
  fi
}

# ─── Step Implementations ─────────────────────────────────────────────────────

# Step 1: Enable Chat & PubSub APIs
verify_apis() {
  local out=$(gcloud services list --enabled --project="$PROJECT_ID" --format="value(config.name)" 2>/dev/null || echo "")
  echo "$out" | grep -q 'pubsub.googleapis.com' && \
  echo "$out" | grep -q 'chat.googleapis.com' && \
  echo "$out" | grep -q 'gsuiteaddons.googleapis.com'
}
execute_apis() {
  gcloud services enable \
      pubsub.googleapis.com \
      chat.googleapis.com \
      gsuiteaddons.googleapis.com \
      --project="$PROJECT_ID"
}

# Step 2: Pub/Sub Setup (Inbound routing from GChat)
verify_pubsub_setup() {
  gcloud pubsub topics describe "${CHAT_TOPIC_NAME}" --project="${PROJECT_ID}" >/dev/null 2>&1 && \
  gcloud pubsub subscriptions describe "${CHAT_SUB_NAME}" --project="${PROJECT_ID}" >/dev/null 2>&1
}
execute_pubsub_setup() {
  if ! gcloud pubsub topics describe "${CHAT_TOPIC_NAME}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    print_info "Creating Pub/Sub Topic ${CHAT_TOPIC_NAME}..."
    gcloud pubsub topics create "${CHAT_TOPIC_NAME}" --project="${PROJECT_ID}"
  fi

  if ! gcloud pubsub subscriptions describe "${CHAT_SUB_NAME}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    print_info "Creating Pub/Sub Subscription ${CHAT_SUB_NAME}..."
    gcloud pubsub subscriptions create "${CHAT_SUB_NAME}" \
        --topic="${CHAT_TOPIC_NAME}" \
        --ack-deadline=60 \
        --project="${PROJECT_ID}"
  fi

  print_info "Granting Google Chat systems Publisher roles to the Topic..."
  gcloud pubsub topics add-iam-policy-binding "${CHAT_TOPIC_NAME}" \
      --member="serviceAccount:chat-api-push@system.gserviceaccount.com" \
      --role="roles/pubsub.publisher" \
      --project="${PROJECT_ID}" \
      --quiet >/dev/null

  local gsuite_sa="service-${PROJECT_NUMBER}@gcp-sa-gsuiteaddons.iam.gserviceaccount.com"
  gcloud pubsub topics add-iam-policy-binding "${CHAT_TOPIC_NAME}" \
      --member="serviceAccount:${gsuite_sa}" \
      --role="roles/pubsub.publisher" \
      --project="${PROJECT_ID}" \
      --quiet >/dev/null
}

# Step 3: Agent GSA Creation & PubSub Message Read Access
verify_agent_gcp() {
  local gsa_email="${GSA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
  gcloud iam service-accounts describe "${gsa_email}" --project="${PROJECT_ID}" >/dev/null 2>&1 && \
  gcloud pubsub subscriptions get-iam-policy "${CHAT_SUB_NAME}" --project="${PROJECT_ID}" --format="json" 2>/dev/null | grep -F -q "${gsa_email}"
}
execute_agent_gcp() {
  local gsa_email="${GSA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

  # 1. Create the Bot's Service Account
  if ! gcloud iam service-accounts describe "${gsa_email}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    print_info "Creating GSA ${GSA_NAME}..."
    gcloud iam service-accounts create "${GSA_NAME}" \
        --display-name="Platform Agent Bot GSA" \
        --project="${PROJECT_ID}"
  fi

  print_info "Applying Pub/Sub Subscriber Role for Agent GSA..."
  
  # 2. Allow bot to read from Pub/Sub Queue
  gcloud pubsub subscriptions add-iam-policy-binding "${CHAT_SUB_NAME}" \
      --member="serviceAccount:${gsa_email}" \
      --role="roles/pubsub.subscriber" \
      --project="${PROJECT_ID}" \
      --quiet >/dev/null

  gcloud pubsub subscriptions add-iam-policy-binding "${CHAT_SUB_NAME}" \
      --member="serviceAccount:${gsa_email}" \
      --role="roles/pubsub.viewer" \
      --project="${PROJECT_ID}" \
      --quiet >/dev/null
}

# ─── Execution Pipeline ───────────────────────────────────────────────────────
run_step "1. Enable GCP APIs for Chat & PubSub" verify_apis execute_apis 15
run_step "2. Provision Pub/Sub Routing (Inbound)" verify_pubsub_setup execute_pubsub_setup 5
run_step "3. Setup Agent Identity & Message Read Permissions" verify_agent_gcp execute_agent_gcp 5

# ─── Conclusion Checklist ─────────────────────────────────────────────────────
echo -e "\n${C_MAGENTA}${C_BOLD}>>>  GCP Backend for Google Chat Configured!  <<<${C_RESET}"
echo -e "${C_YELLOW}${C_BOLD}======================= START COPY&PASTE =======================${C_RESET}"
echo -e "${C_YELLOW}Your Google Cloud Chat infrastructure is initialized!${C_RESET}"
echo -e "Follow these steps in the UI to finish setting up the bot:\n"
echo -e "[ ] Configure GChat bot connection in GCP Console:"
echo -e "       ${C_WHITE}https://console.cloud.google.com/apis/api/chat.googleapis.com/hangouts-chat?project=${PROJECT_ID}${C_RESET}"
echo -e "       - Name: ${C_GREEN}GKE Platform Agent Bot${C_RESET}"
echo -e "       - Avatar: ${C_GREEN}https://platform-agent.nousresearch.com/docs/img/logo.png${C_RESET}"
echo -e "       - Connection Settings: Select ${C_BOLD}Cloud Pub/Sub${C_RESET}"
echo -e "       - Pub/Sub Topic Name: ${C_GREEN}projects/${PROJECT_ID}/topics/${CHAT_TOPIC_NAME}${C_RESET}"
echo -e "       - Under Visibility, check: ${C_GREEN}Only specific people (add your email ${ALLOWED_USER})${C_RESET}"
echo -e "======================== END COPY&PASTE ========================\n"
