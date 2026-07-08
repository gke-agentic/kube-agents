#!/usr/bin/env bash
# ==============================================================================
# 🚀 Step 2: Deploy Demo Hermes Harness on kagent Substrate
# ==============================================================================
# Applies the official demo ModelConfig (Gemini 3.5 Flash) and AgentHarness
# manifests without specifying a custom image, using default upstream Hermes images.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAMESPACE="kagent"

echo "=== 1. Checking if kagent orchestrator is running in '${NAMESPACE}' ==="
if ! kubectl get crd agentharnesses.kagent.dev >/dev/null 2>&1; then
  echo "Error: AgentHarness CRD not found. Please run Step 1 first:"
  echo "  bash spike/manifests-substrate-demo/setup_step1_substrate_env.sh"
  exit 1
fi

echo ""
echo "=== 2. Ensuring API Key Secret exists ==="
if ! kubectl get secret platform-agent-secrets -n "${NAMESPACE}" >/dev/null 2>&1; then
  echo "Notice: Secret 'platform-agent-secrets' not found in namespace '${NAMESPACE}'."
  echo "Creating a placeholder secret for validation purposes..."
  kubectl create secret generic platform-agent-secrets -n "${NAMESPACE}" \
    --from-literal=GEMINI_API_KEY="placeholder-gemini-3.5-key" \
    --from-literal=OPENAI_API_KEY="placeholder-openai-key" \
    --dry-run=client -o yaml | kubectl apply -f -
fi

echo ""
echo "=== 3. Applying ModelConfig (Gemini 3.5 Flash) and AgentHarness Manifests ==="
kubectl apply -f "${SCRIPT_DIR}/model-config.yaml"
kubectl apply -f "${SCRIPT_DIR}/hermes-harness.yaml"

echo ""
echo "=== 4. Inspecting Deployed AgentHarness Status ==="
sleep 5
kubectl -n "${NAMESPACE}" get agentharnesses -o wide || true
kubectl -n "${NAMESPACE}" describe agentharness hermes-shell || true

echo -e "\n✅ Step 2 Complete! Official demo Hermes harness deployed."
echo "To check actor/worker status:"
echo "  kubectl -n ${NAMESPACE} get pods -l app.kubernetes.io/name=kagent"
echo "To port-forward UI and chat:"
echo "  kubectl port-forward -n ${NAMESPACE} svc/kagent-ui 8080:8080"
