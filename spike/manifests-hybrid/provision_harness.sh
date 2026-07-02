#!/usr/bin/env bash
# ==============================================================================
# 🕸️ Provision Agent Harness for Hybrid Spike
# ==============================================================================
# Resolves the image path, templates agentharness.yaml, and applies kagent manifests.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Load SRE variables
VARS_SH="${SCRIPT_DIR}/vars.sh"
if [ -f "$VARS_SH" ]; then
    source "$VARS_SH"
else
    echo "Error: SRE variables file not found at ${VARS_SH}."
    exit 1
fi

TARGET_NAMESPACE="kube-agents-spike"

# Read the image URI from the state file
IMAGE_URI_FILE="${SCRIPT_DIR}/.image_uri"
if [ -f "$IMAGE_URI_FILE" ]; then
    export AGENT_IMAGE="$(cat "$IMAGE_URI_FILE")"
else
    # Fallback to latest if no image URI file exists
    GCP_REPO_NAME="kagent-hybrid"
    IMAGE_NAME="platform-agent"
    export AGENT_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${GCP_REPO_NAME}/${IMAGE_NAME}:latest"
fi

echo "Using image for harness: ${AGENT_IMAGE}"

echo "Rendering AgentHarness manifest..."
envsubst < "${SCRIPT_DIR}/agentharness.yaml.template" > "${SCRIPT_DIR}/agentharness.yaml"

echo "Applying ModelConfig..."
kubectl apply -n "${TARGET_NAMESPACE}" -f "${SCRIPT_DIR}/modelconfig.yaml"

echo "Cleaning up old AgentHarness to reset controller status..."
kubectl delete agentharness platform-agent-hybrid -n "${TARGET_NAMESPACE}" --ignore-not-found=true

echo "Applying AgentHarness..."
kubectl apply -n "${TARGET_NAMESPACE}" -f "${SCRIPT_DIR}/agentharness.yaml"

# ==============================================================================
# 🩹 AppArmor Patch Loop (GKE Standard Workaround)
# ==============================================================================
# OpenShell supervisor requires netns operations which GKE baseline AppArmor blocks.
# Since the kagent controller creates the Sandbox CR without relaxing AppArmor,
# we must capture the Sandbox CR immediately after creation and patch it.
# ==============================================================================
echo "Starting AppArmor and secrets patcher..."
set +e
SANDBOX_NAME="kube-agents-spike-platform-agent-hybrid"
patched=false
for i in {1..30}; do
  if kubectl get sandbox "${SANDBOX_NAME}" -n "${TARGET_NAMESPACE}" >/dev/null 2>&1; then
    echo "Patcher: Sandbox CR found."
    
    # Check if the controller already created the pod (annotation exists)
    HAS_POD=$(kubectl get sandbox "${SANDBOX_NAME}" -n "${TARGET_NAMESPACE}" -o yaml | grep "agents.x-k8s.io/pod-name" || echo "")
    
    # ============================================================================
    # Workaround: Inject AppArmor Unconfined & Secrets Volume (Robust & Idempotent)
    # ============================================================================
    echo "Patcher: Applying security updates and volume mounts to Sandbox CR..."
    for attempt in {1..5}; do
      py_out=$(python3 -c '
import json, subprocess
name = "'"${SANDBOX_NAME}"'"
ns = "'"${TARGET_NAMESPACE}"'"
cmd = ["kubectl", "get", "sandbox", name, "-n", ns, "-o", "json"]
res = subprocess.run(cmd, capture_output=True, text=True)
if res.returncode != 0:
    exit(1)
cr = json.loads(res.stdout)
pod_spec = cr.get("spec", {}).get("podTemplate", {}).get("spec", {})
patches = []

# 1. Patch Volumes
volumes = pod_spec.get("volumes", [])
if not volumes:
    patches.append({"op": "add", "path": "/spec/podTemplate/spec/volumes", "value": [{"name": "platform-secrets", "secret": {"secretName": "platform-agent-secrets"}}]})
elif not any(v.get("name") == "platform-secrets" for v in volumes):
    patches.append({"op": "add", "path": "/spec/podTemplate/spec/volumes/-", "value": {"name": "platform-secrets", "secret": {"secretName": "platform-agent-secrets"}}})

containers = pod_spec.get("containers", [{}])
if containers:
    container = containers[0]
    
    # 2. Patch AppArmor Profile (Native v1.30+ configuration)
    sec_ctx = container.get("securityContext", {})
    if not sec_ctx:
        patches.append({"op": "add", "path": "/spec/podTemplate/spec/containers/0/securityContext", "value": {"appArmorProfile": {"type": "Unconfined"}}})
    elif "appArmorProfile" not in sec_ctx:
        patches.append({"op": "add", "path": "/spec/podTemplate/spec/containers/0/securityContext/appArmorProfile", "value": {"type": "Unconfined"}})

    # 3. Patch Volume Mounts
    mounts = container.get("volumeMounts", [])
    if not mounts:
        patches.append({"op": "add", "path": "/spec/podTemplate/spec/containers/0/volumeMounts", "value": [{"name": "platform-secrets", "mountPath": "/etc/secrets/platform-agent-secrets", "readOnly": True}]})
    elif not any(m.get("name") == "platform-secrets" for m in mounts):
        patches.append({"op": "add", "path": "/spec/podTemplate/spec/containers/0/volumeMounts/-", "value": {"name": "platform-secrets", "mountPath": "/etc/secrets/platform-agent-secrets", "readOnly": True}})

if patches:
    patch_str = json.dumps(patches)
    res_p = subprocess.run(["kubectl", "patch", "sandbox", name, "-n", ns, "--type=json", "--patch", patch_str], capture_output=True, text=True)
    if res_p.returncode != 0:
        print(res_p.stderr)
        exit(1)
' 2>&1)
      if [ $? -eq 0 ]; then
        echo "Patcher: Security and volume patches applied successfully."
        break
      else
        echo "Patcher: Patch failed: ${py_out}, retrying... (attempt ${attempt}/5)"
        sleep 1
      fi
    done
    
    if [ -n "${HAS_POD}" ]; then
      echo "Patcher: Pod was already created before patch. Forcing recreation..."
      # Delete the old pod first (waits for deletion to complete)
      kubectl delete pod "${SANDBOX_NAME}" -n "${TARGET_NAMESPACE}" --ignore-not-found=true
      # Remove the pod-name annotation to force the controller to recreate it
      kubectl patch sandbox "${SANDBOX_NAME}" -n "${TARGET_NAMESPACE}" --type=json --patch '[{"op": "remove", "path": "/metadata/annotations/agents.x-k8s.io~1pod-name"}]'
    fi
    
    echo "Patcher: Patch process completed successfully!"
    patched=true
    break
  fi
  sleep 2
done
if [ "$patched" = false ]; then
  echo "Patcher: Warning: Sandbox CR not found after 60s, patch not applied."
fi
set -e


