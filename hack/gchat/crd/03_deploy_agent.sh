#!/bin/bash
set -euo pipefail

# Navigate to the script directory to ensure relative paths work
cd "$(dirname "$0")"

# Load environment variables
if [ -f .env ]; then
  echo " -> [OK] Loading variables from local .env file..."
  set -a
  source .env
  set +a
else
  echo " -> [ERROR] .env file not found! Please create it first."
  exit 1
fi

# Construct IMAGE_URI
export IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${IMAGE_NAME}:${IMAGE_TAG}"

# Ensure we have required variables
REQUIRED_VARS=("NAMESPACE" "PROJECT_ID" "IMAGE_URI" "CHAT_TOPIC_NAME" "CHAT_SUB_NAME" "GSA_NAME" "KSA_NAME" "GOOGLE_CHAT_ALLOWED_USERS" "GOOGLE_CHAT_HOME_CHANNEL")
for var in "${REQUIRED_VARS[@]}"; do
    if [ -z "${!var:-}" ]; then
        echo " -> [ERROR] Required variable $var is not set in .env."
        exit 1
    fi
done

# Ensure envsubst is available
if ! command -v envsubst >/dev/null 2>&1; then
    echo " -> [ERROR] 'envsubst' utility is required but not installed."
    echo "            Please install 'gettext' package (which provides envsubst)."
    exit 1
fi

# Function to generate the manifest
generate_manifest() {
    echo " -> [WAIT] Generating hermes-agent-bot.yaml from template..."
    envsubst < hermes-agent-bot.yaml.tmpl > hermes-agent-bot.yaml
    echo " -> [OK] Generated hermes-agent-bot.yaml"
}

# Check for arguments
ACTION="apply"
if [ $# -gt 0 ]; then
    if [ "$1" == "delete" ] || [ "$1" == "destroy" ] || [ "$1" == "-d" ]; then
        ACTION="delete"
    elif [ "$1" == "generate" ] || [ "$1" == "-g" ]; then
        ACTION="generate"
    fi
fi

case "$ACTION" in
    "generate")
        generate_manifest
        ;;
    "apply")
        generate_manifest
        echo " -> [WAIT] Applying manifest to GKE cluster..."
        kubectl apply -f hermes-agent-bot.yaml
        echo "✅ Agent Deployment/Update Initiated!"
        ;;
    "delete")
        generate_manifest
        echo " -> [WAIT] Deleting manifest from GKE cluster..."
        kubectl delete -f hermes-agent-bot.yaml --ignore-not-found=true
        echo "✅ Agent Deletion Initiated!"
        ;;
esac
