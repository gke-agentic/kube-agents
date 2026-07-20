# SRE Triage & Architectural Evaluation Report

## Issue #135: Rename agent-facing service URLs from `litellm` to an inference-gateway-agnostic name

### 1. Executive Summary
- **The Issue:** The Kubernetes Agentic Harness (`kube-agents`) currently couples agent-facing configuration to a specific product name by hardcoding `litellm` (and `litellm-gateway` for upstream) as the default service name and DNS URL.
- **The Risk:** If LiteLLM is replaced or augmented with alternative inference gateways (such as vLLM, Triton, Ollama, or a raw OpenAI proxy), all existing agent configurations, manifests, Helm values, and internal controllers referencing `http://litellm` will break.
- **SRE Ruling Request:** standardize a platform-agnostic service URL and determine the deprecation/backwards-compatibility path for running GKE clusters.

---

### 2. Systematic Technical Analysis

An audit of the codebase reveals that the name `litellm` is highly coupled across multiple functional areas, including:

1. **Go Operator Controller (`platformagent_manifests.go`):**
   The Go controller dynamically builds the agent configuration and injects the hardcoded `litellm` service URL:
   ```go
   cfg.Model.BaseURL = fmt.Sprintf("http://litellm.%s.svc.cluster.local/v1", agent.Namespace)
   ```
   *Impact:* Any change to this URL requires compiling and redeploying the GKE Operator manager.

2. **Kustomize Integration Base Manifests (`k8s-operator/config/integrations/litellm`):**
   The base deployment, service, network policy, and pod monitoring configurations are all hardcoded to resource name `litellm` and label `app: litellm`.

3. **Staging Workload Helm Charts (`workload-bundle`):**
   Staging simulator and values configurations define variables such as `targetLiteLLM` and reference `http://litellm-gateway:4000/health/readiness`.

4. **Example Workloads & Docs:**
   Examples under `examples/inference-replay/` and `examples/litellm-gemini/` reference and document `litellm` and `litellm-gateway` explicitly.

---

### 3. Proposed Solutions & Evaluation

We evaluate the following options for standardizing an agnostic name:

#### Option A: Rename to `inference-gateway`
- **Pros:** Highly descriptive, standard industry terminology, completely decoupled from specific vendors or products.
- **Cons:** Longest service URL string, requires sweeping edits in Go controllers and Helm charts.

#### Option B: Rename to `llm-gateway`
- **Pros:** Concise, clear, and universally understood across AI/ML platform engineering.
- **Cons:** Slightly specific to LLMs (might not cover vision or audio-only future models as cleanly as "inference-gateway").

#### Option C: Keep `litellm` as a Virtual Alias (Backwards-Compatible Path)
- **Pros:** Zero-risk migration. Existing agents continue to work without modification.
- **Cons:** Perpetuates the stale branding and does not clean up the underlying architectural coupling.

---

### 4. SRE Recommendations & Next Steps

We recommend the SRE Team review and approve the following resolution path:

1. **Select Option A (`inference-gateway`):** Establish `inference-gateway` as the new standard service name for GKE integrations.
2. **Implement Aliasing (Gradual Rollout):** Deploy a virtual service or CNAME alias for `litellm` pointing to `inference-gateway` to allow legacy workloads to migrate gradually.
3. **Update Go Operator:** Update `platformagent_manifests.go` to use the new agnostic URL while supporting a fallback/override configuration in the `PlatformAgent` CRD spec.

This issue has been escalated to `status:escalation-needed` pending final architectural selection of the standardized service name.

**Grounding Source:**
- **Go Controller:** `k8s-operator/internal/controller/platformagent_manifests.go`
- **Integration Configs:** `k8s-operator/config/integrations/litellm/`
- **Upstream Issue:** gke-labs/kube-agents #135