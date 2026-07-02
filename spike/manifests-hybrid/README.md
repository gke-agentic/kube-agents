# Platform Agent Hybrid Spike (kagent + Hermes)

This directory contains the deployment scripts and configurations for the "Hybrid Spike" Proof of Concept (PoC). 

## What We Achieved in This Spike
Previously, we ran our agents on GCP using a custom `kubebuilder` orchestrator and the `hermes` stateful workload. In this spike, we successfully migrated to a new architecture using the declarative `kagent` controller while keeping our legacy `hermes` payload as the "brain".

The biggest architectural shift is that our agent (via the `AgentHarness` resource) now runs inside a strict **OpenShell sandbox**. This transition proved that the hybrid approach works, but it also highlighted significant friction points:
1. **Strict Isolation Boundary:** All external integrations (network access, API authentication) must now be explicitly tunneled through the OpenShell sandbox.
2. **Workload Modifications:** We had to adapt the `hermes` container image to support running as a non-root user.
3. **Complex Configuration Injection:** Because the `AgentHarness` CRD does not support direct volume mounts, and OpenShell aggressively drops `valueFrom` secrets, we had to build a custom Python patcher to forcefully inject API keys into the underlying `Sandbox` manifest on the fly.
4. **Hardcoded Workarounds (Hacks):** OpenShell's nested network namespaces conflict heavily with standard GKE security policies. We had to apply `AppArmor: Unconfined` patches just to allow the pod to start, and use `iptables` IP masquerading and `/etc/hosts` hacks to restore DNS resolution for Pub/Sub and Google Chat integrations.

These friction points clearly demonstrate that the OpenShell runtime is brittle and conflicts with GKE policies.

## Core Components
*   **kagent (v0.9.11)**: The Kubernetes orchestrator controller (via OCI).
*   **OpenShell (v0.0.51)**: The GKE sandbox runtime provider used by kagent to isolate the agent pod.

*   **Hermes Agent (latest)**: The legacy Python-based agent workload running inside the OpenShell sandbox.
*   **LiteLLM (local)**: The LLM proxy providing the agent access to Gemini Vertex AI.

---

## Prerequisites
1. You must be authenticated to GCP (`gcloud auth login`).
2. You must have `gcloud`, `kubectl`, and `helm` installed on your machine.
3. You must have a configured Google Chat App and a Pub/Sub topic for your agent to pull events from.

---

## Deployment Instructions

### 1. Configure SRE Variables
Create a `spike/manifests-hybrid/vars.sh` file and populate it with your environment-specific values. 

*(Note: `vars.sh` is explicitly ignored by Git to prevent API key leakage).*

**Template for `vars.sh`:**
```bash
# GCP & GKE Target Environment
export PROJECT_ID="your-gcp-project-id"
export PROJECT_NUMBER="your-gcp-project-number"
export CLUSTER_NAME="your-gke-cluster-name"
export REGION="us-east4"

# LLM Configuration (LiteLLM Proxy Settings)
export MODEL_PROVIDER="gemini"
export MODEL_DEFAULT_NAME="gemini-3.5-flash"
export GEMINI_API_KEY="your-gemini-api-key"
export API_SERVER_KEY="your-secure-internal-proxy-token"
export OPENAI_API_KEY="placeholder"
export ANTHROPIC_API_KEY="placeholder"

# Google Chat Pub/Sub Integrations
export ALLOWED_USERS="your-ldap"
export CHAT_TOPIC_NAME="platform-agent-chat-events"
export CHAT_SUB_NAME="platform-agent-chat-events-sub"

# Agent Workload
export AGENT_IMAGE="ghcr.io/gke-labs/kube-agents/platform-agent"
```

### 2. Connect to your GKE Cluster
Point your local `kubectl` context to your target cluster:
```bash
source spike/manifests-hybrid/vars.sh
gcloud config set project ${PROJECT_ID}
gcloud container clusters get-credentials ${CLUSTER_NAME} --region ${REGION} --project ${PROJECT_ID}
```

### 3. Install the `kagent` Infrastructure
Run the installation script to deploy OpenShell and the `kagent` controller into the isolated `kube-agents-spike` namespace. 

```bash
bash spike/manifests-hybrid/install-kagent.sh
```
*Note: This script automatically uses `values-spike.yaml` to disable kagent's default system agents (Istio, Cilium, Helm, etc.) to keep the cluster resource footprint minimal.*

### 4. Deploy the Agent Workload
Run the deployment script to provision the remaining agent resources in the `kube-agents-spike` namespace:

```bash
bash spike/manifests-hybrid/deploy.sh
```
This script handles several automated steps:
1. Reuses the project's native SRE scripts (`provision_04_gcp_k8s_secrets.sh` & `provision_07_deploy_litellm.sh`) to provision `platform-agent-secrets` and the local `litellm` proxy.
2. Applies the `ModelConfig` and `AgentHarness` CRDs.
3. **AppArmor Patcher**: Runs an inline Python script to bypass GKE v1.35 AppArmor nested namespace restrictions and dynamically injects the API key secret mounts into the generated OpenShell `Sandbox` CR.

---

## Important Note: Ephemeral Network Fixes

Due to GKE Standard's Datapath V2 (Cilium) interacting aggressively with OpenShell's nested network namespaces, outbound DNS queries (Port 53) are currently blocked from inside the sandbox. 

If your Hermes agent pod starts crashing or reports it cannot reach `pubsub.googleapis.com` or `chat.googleapis.com`, you must manually establish an IP Masquerading rule and inject static DNS mappings into the pod's root namespace via `kubectl exec`. 

Please refer to [**`RESEARCH_NOTES.md`**](./RESEARCH_NOTES.md) for the exact root cause analysis and the temporary commands required to establish the Pub/Sub connection.
