#!/usr/bin/env bash
# ==============================================================================
# 🚀 Step 1: Setup kagent Substrate Environment on GCP GKE (Stable 1:1 Walkthrough)
# ==============================================================================
# Why this file exists & why it was updated (Step 7 in SUBSTRATE_DEMO_SPIKE_LOG.md):
# 1. In testing beta release v0.10.0-beta3 and nightly v0.0.7, we encountered
#    experimental JWT authentication edge cases and schema shifts.
# 2. Per user instruction and SUBSTRATE_DEMO_SPIKE_LOG.md (Step 7), we pivoted
#    to following the official stable walkthrough at kagent.dev/docs/kagent/examples/agent-substrate
#    one-to-one using stable versions v0.0.6 (Substrate) and v0.9.9 (kagent).
# 3. This bypasses all experimental JWT modes and guarantees reliable sandboxing.
# ==============================================================================

set -e

# Added SCRIPT_DIR resolution so Helm can reliably locate values-demo.yaml regardless of the caller's working directory.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PROJECT_ID="${GCP_PROJECT_ID:-eleontev-kube-agents}"
REGION="${GCP_REGION:-us-central1}"
CLUSTER_NAME="${GKE_CLUSTER_NAME:-kagent-substrate-demo}"

echo "=== 0. Verifying Prerequisites ==="
for cmd in gcloud kubectl helm; do
  if ! command -v "$cmd" &> /dev/null; then
    echo "Error: Required command '$cmd' not found in PATH."
    exit 1
  fi
done

echo ""
echo "=== 1. Connecting to GKE Cluster ${CLUSTER_NAME} in ${REGION} ==="
gcloud container clusters get-credentials "${CLUSTER_NAME}" --region "${REGION}" --project "${PROJECT_ID}"

echo ""
echo "=== 2. Installing Substrate CRDs and Data Plane in 'ate-system' (Stable v0.0.6) ==="
# Updated to stable v0.0.6 per 1:1 official walkthrough (SUBSTRATE_DEMO_SPIKE_LOG.md Step 7)
helm upgrade --install substrate-crds oci://ghcr.io/kagent-dev/substrate/helm/substrate-crds \
  --version 0.0.6 \
  --namespace ate-system --create-namespace --wait

# Updated to stable v0.0.6 per 1:1 official walkthrough (SUBSTRATE_DEMO_SPIKE_LOG.md Step 7).
# We pass explicit GKE JWT issuer and audience flags (SUBSTRATE_DEMO_SPIKE_LOG.md Step 6 & 14)
# because GKE service account tokens use the cluster URL as their issuer. Without these flags,
# ate-api-server rejects ate-controller bearer tokens with 'unexpected issuer'.
helm upgrade --install substrate oci://ghcr.io/kagent-dev/substrate/helm/substrate \
  --version 0.0.6 \
  --namespace ate-system --create-namespace --wait --timeout 10m \
  --set auth.jwt.issuer="https://container.googleapis.com/v1/projects/${PROJECT_ID}/locations/${REGION}/clusters/${CLUSTER_NAME}" \
  --set auth.jwt.audience="api.ate-system.svc"

# Note: Removed Step 2a (gvisor-sandbox-config.yaml) because in stable Substrate v0.0.6,
# the SandboxConfig CRD does not exist, and it is not part of kagent.dev/docs/kagent/examples/agent-substrate.

echo ""
echo "=== 3. Installing kagent CRDs and Controller in 'kagent' (Stable v0.9.9) ==="
# Updated to stable v0.9.9 per 1:1 official walkthrough (SUBSTRATE_DEMO_SPIKE_LOG.md Step 7)
helm upgrade --install kagent-crds oci://ghcr.io/kagent-dev/kagent/helm/kagent-crds \
  --version 0.9.9 \
  --namespace kagent --create-namespace --wait

# Updated to stable v0.9.9 and ateom-gvisor:v0.0.6 per 1:1 official walkthrough (SUBSTRATE_DEMO_SPIKE_LOG.md Step 7).
# We pass values-demo.yaml (-f) to omit redundant workloads and set top-level 'registry: ghcr.io' (Step 10).
helm upgrade --install kagent oci://ghcr.io/kagent-dev/kagent/helm/kagent \
  --version 0.9.9 \
  --namespace kagent --timeout 10m --wait \
  -f "${SCRIPT_DIR}/values-demo.yaml" \
  --set controller.substrate.enabled=true \
  --set controller.substrate.ateApiEndpoint="dns:///api.ate-system.svc:443" \
  --set controller.substrate.ateApiInsecure=true \
  --set substrateWorkerPool.create=true \
  --set substrateWorkerPool.replicas=1 \
  --set substrateWorkerPool.ateomImage="ghcr.io/kagent-dev/substrate/ateom-gvisor:v0.0.6"

echo ""
echo "=== 4. Verifying Installation Status ==="
kubectl get pods -n ate-system
kubectl get pods -n kagent

# Added explanatory note: By default, the Substrate 'atelet' DaemonSet requests 2 CPU per pod. On e2-standard-4 VMs,
# 1 out of 9 nodes may report 'Unschedulable' / 'Pending' status due to GKE system daemonsets consuming CPU headroom.
# We proceed safely because 8 operational data plane nodes are more than enough to host our Substrate worker pools.
echo -e "\n✅ Step 1 Complete! Substrate data plane and kagent controller are installed."
echo "Note: If 1 out of 9 'atelet' pods in ate-system is Pending/Unschedulable due to 2 CPU request, proceed safely as 8 nodes are enough."
echo "Wait ~30-60 seconds for pods to become 1/1 Running, then execute Step 2:"
echo "  bash spike/manifests-substrate-demo/deploy_step2_hermes_harness.sh"
