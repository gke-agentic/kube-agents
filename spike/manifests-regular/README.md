# Platform Agent BYO Regular Deployment Spike (`kagent` + Hermes)

This directory contains the deployment manifests, provisioning scripts, and adaptation bridge for the **BYO (Bring-Your-Own) Regular Deployment Spike**.

## What We Achieved in This Spike
Previously, our Platform Agent relied on custom operator infrastructure or experimental sandbox runtimes. In this spike, we successfully migrated our Platform Agent to run as a regular Kubernetes deployment managed declaratively by the **`kagent` (v0.9.11)** controller using its `type: BYO` (Bring-Your-Own) custom resource definition in the `kube-agents-regular-spike` namespace on GKE Standard (`kanget-with-root-agent` cluster).

This architecture eliminates legacy operator dependencies and proves that any external or custom AI container can seamlessly integrate into the `kagent` multi-agent harness without altering its core source code or Docker image on disk!

To achieve end-to-end integration without altering the upstream container image, BYO mode relies on two declarative **Runtime Wrappers**:
1. **Security & Execution Wrapper (`podSecurityContext`):** Kubernetes dynamically overrides the image's default `root` user at runtime, forcing execution as unprivileged UID `10001` and recursively granting group-writable permissions (`fsGroup: 10001`) to attached persistent storage volumes.
2. **Protocol Adapter Wrapper (`sitecustomize.py`):** An in-process Python adapter mounted via ConfigMap that wraps Hermes's native OpenAI-compatible REST server (`/v1/chat/completions`) and exposes it as an A2A-compliant streaming endpoint (`/.well-known/agent-card.json` and `POST /`).

### High-Level Conceptual Challenges of BYO Integration in KAgent
When integrating any third-party or custom AI agent into `kagent` BYO mode as a general engineering concept, architects face four universal challenges:
1. **Protocol Impedance Mismatch (A2A vs. Native APIs):** Most industry AI frameworks (OpenAI, Anthropic, LangChain, Ollama) speak standard HTTP REST or WebSocket protocols. `kagent` requires Google's A2A (Agent-to-Agent) JSON-RPC protocol over Server-Sent Events (SSE). Bridging this requires an adaptation layer (either an in-process ConfigMap wrapper or a proxy sidecar container).
2. **Non-Root Runtime Enforcement & Storage Ownership:** Enterprise Kubernetes environments enforce non-root execution. Many third-party AI containers default to `root` and write runtime state, sessions, or logs to disk. Integrating them requires configuring Kubernetes Pod Security Contexts (`fsGroup`, `runAsUser`) to override image defaults and grant volume write permissions without rebuilding images.
3. **Asynchronous Event Loop Deadlocks:** Protocol adapters often receive external A2A requests and forward them to internal AI endpoints. If the adapter and AI engine share a single-threaded asynchronous runtime (like Python `asyncio`/`aiohttp` or Node.js), synchronous or blocking network calls will freeze the event loop, causing gateway timeouts and deadlocks. All internal bridging must be non-blocking and asynchronous.
4. **Controller Selector Reconciliation:** Declarative orchestrators (like `kagent-controller`) automatically generate Deployments and Services with strict label selectors (`kagent: <name>`). Hardcoded manual pod labels in BYO templates conflict with controller reconciliation, requiring a clean separation between workload metadata and controller-managed routing labels.

> [!IMPORTANT]
> **PoC Adapter Maturity:** The `sitecustomize.py` A2A bridge is an unfinalized Proof-of-Concept (PoC) built strictly for demo and feasibility validation. While it successfully handles basic chat messages and session recording, it does not yet implement full A2A specification compliance (such as streaming token-by-token deltas, tool execution artifacts, multi-modal payloads, or task cancellation). Executing complex multi-step tool workflows will trigger protocol errors. For production adoption, this adapter must be hardened into a finalized integration module.

For the comprehensive technical breakdown, root cause analysis (RCA), and validation logs, please refer to [**`RESEARCH_NOTES.md`**](./RESEARCH_NOTES.md).

---

## Core Components
* **`kagent` (v0.9.11):** The Kubernetes agentic harness controller and web UI (`kagent-controller`, `kagent-ui`).
* **Hermes Platform Agent (`latest`):** Our stateful Python-based AI agent container deployed as a regular Kubernetes pod via the `Agent` CRD (`type: BYO`).
* **`sitecustomize.py` Adapter:** A lightweight Python startup hook injected via ConfigMap that dynamically registers A2A endpoints (`/.well-known/agent-card.json` and `POST /`) and bridges KAgent chat streams into Hermes's internal `/v1/chat/completions` engine using `aiohttp.ClientSession`.
* **LiteLLM Proxy (local):** The local gateway deployment providing Hermes structured access to Google Gemini Vertex AI models.

---

## Prerequisites
1. You must be authenticated to GCP (`gcloud auth login`).
2. You must have `gcloud`, `kubectl`, and `helm` installed locally.
3. Your local terminal must have access to the target GKE cluster (`kanget-with-root-agent` in `us-central1`).

---

## Deployment Instructions

### 1. Configure SRE Variables
Create or verify the `spike/manifests-regular/vars.sh` file with your target environment settings.

*(Note: `vars.sh` is explicitly ignored by Git to prevent committing API keys or local cluster configuration).*

**Key Isolation Feature:**
Our `vars.sh` script explicitly exports `KUBECONFIG="${HOME}/.kube/config-kagent-root-agent"` and `CLOUDSDK_ACTIVE_CONFIG_NAME="kagent-root-agent"`. This ensures all deployment scripts operate strictly in an isolated kubeconfig context without touching or mutating your system-wide default GCP/GKE configuration!

**Template for `vars.sh`:**
```bash
# GCP & GKE Target Environment
export PROJECT_ID="eleontev-kube-agents"
export CLUSTER_NAME="kanget-with-root-agent"
export REGION="us-central1"
export KUBECONFIG="${HOME}/.kube/config-kagent-root-agent"
export CLOUDSDK_ACTIVE_CONFIG_NAME="kagent-root-agent"

# LLM Configuration (LiteLLM Proxy Settings)
export MODEL_PROVIDER="gemini"
export MODEL_DEFAULT_NAME="gemini-3.5-flash"
export GEMINI_API_KEY="your-gemini-api-key"
export API_SERVER_KEY="your-secure-internal-proxy-token"

# Agent Workload
export AGENT_IMAGE="us-central1-docker.pkg.dev/eleontev-kube-agents/platform-agent-repo/platform-agent:latest"
```

### 2. Connect to the Target GKE Cluster
Load the isolated environment variables and fetch cluster credentials:
```bash
source spike/manifests-regular/vars.sh
gcloud container clusters get-credentials ${CLUSTER_NAME} --region ${REGION} --project ${PROJECT_ID}
```

### 3. Install the `kagent` Infrastructure
Run the installation script to provision the `kagent` controller, CRDs, and UI into the `kube-agents-regular-spike` namespace:
```bash
bash spike/manifests-regular/install-kagent.sh
```
*Note: This script applies `values-spike.yaml` to configure minimal resource limits and disable unneeded default sub-components.*

### 4. Deploy the BYO Platform Agent
Run the master deployment script to provision all workload resources:
```bash
bash spike/manifests-regular/deploy.sh
```
This automated orchestrator performs the following steps in sequence:
1. **IAM & Secrets:** Provisions Kubernetes ServiceAccounts, RBAC roles (`provision_iam.sh`), and API secret bundles (`provision_secrets.sh`).
2. **LLM Gateway:** Provisions the local LiteLLM proxy deployment (`provision_litellm.sh`).
3. **A2A ConfigMap & Agent CRD:** Renders `config.yaml` and `agent.yaml` from templates, bundles our `sitecustomize.py` adapter into the `platform-agent-config` ConfigMap, and applies the `Agent` custom resource (`provision_agent.sh`).

---

## Verifying the Deployment

Once deployed, verify that the pod reaches `1/1 Ready` status:
```bash
kubectl get pods -n kube-agents-regular-spike -l kagent=platform-agent
```

### Testing in KAgent UI
Port-forward the KAgent UI service to your local machine:
```bash
kubectl port-forward -n kube-agents-regular-spike svc/kagent-ui 8084:8080
```
Open your browser to **`http://localhost:8084`**. You can select `platform-agent` from the agent dropdown and send chat prompts directly into Hermes!

### Monitoring Live Sessions in Hermes Dashboard
To view live session recordings and internal tool execution logs, port-forward the Hermes Web UI Dashboard:
```bash
kubectl port-forward -n kube-agents-regular-spike svc/platform-agent 9119:9119
```
Open your browser to **`http://localhost:9119`** to monitor real-time session trajectories as KAgent interacts with the agent.
