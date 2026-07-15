---
name: platform-agent-debugging
description: Diagnostic workflows for troubleshooting the kube-agents platform control plane, k8s-operator lifecycle, minty token broker, and inference proxy connectivity.
---

# Platform Agent Debugging Skill

Use this skill to systematically diagnose control-plane failures and operational bottlenecks across the core components of the `kube-agents` architecture: the `k8s-operator`, `PlatformAgent` gateway pod, `minty` GitHub token broker, and `LiteLLM`/`vLLM` inference proxy endpoints.

## 🔍 Control-Plane Diagnostic Workflow

### Step 1: Audit Operator & Controller Reconciliation States

Inspect the `k8s-operator` (written in Go using Kubebuilder) to ensure it is running and successfully reconciling `PlatformAgent` Custom Resources without panics or RBAC blocks.

**Commands:**

```bash
# 1. Check operator pod readiness and restarts
kubectl get pods -n kubeagents-system -l app.kubernetes.io/name=k8s-operator -o wide

# 2. Inspect operator controller logs for reconciliation errors or RBAC denials
kubectl logs -n kubeagents-system -l app.kubernetes.io/name=k8s-operator --tail=100 | grep -E "ERROR|panic|Reconciler error|Forbidden|unauthorized"

# 3. Inspect active PlatformAgent custom resources status
kubectl get platformagents -A -o yaml | grep -E "name:|namespace:|phase:|conditions:" -A 6
```

#### Diagnostic Decision Tree:

- **Phase: Error / Failed:** Inspect `conditions.message` on the `PlatformAgent` CR. If the error reports missing ConfigMaps, Secrets, or ServiceAccounts, verify namespace resources.
- **Operator Pod CrashLooping:** Check logs for Go panics or invalid CRD definitions (`kubectl describe crd platformagents.agent.gke.io`).

---

### Step 2: Validate Minty GitHub Token Broker & KMS Encryption

Verify that `minty` (the secure GitHub App token broker) can decrypt credentials using GCP KMS and serve short-lived tokens to the `PlatformAgent`.

**Commands:**

```bash
# 1. Check minty broker pod status and endpoints
kubectl get pods -n kubeagents-system -l app.kubernetes.io/name=minty -o wide
kubectl get endpoints -n kubeagents-system minty

# 2. Inspect minty logs for KMS decryption or Workload Identity errors
kubectl logs -n kubeagents-system -l app.kubernetes.io/name=minty --tail=50 | grep -E "KMS|decrypt|token|403|401|error"

# 3. Verify KMS key ring access via gcloud (requires project visibility)
gcloud kms keys list --keyring=<keyring_name> --location=<cluster_location> --project=<project_id>
```

#### Diagnostic Checks:

- **KMS Decryption Denied (`403 Permission Denied`):** Ensure the `minty` ServiceAccount has the `roles/cloudkms.cryptoKeyDecrypter` binding on the configured Cloud KMS key.
- **Token Exchange Failures:** Verify that the GitHub App ID, Installation ID, and private key secret (`minty-github-app-secret`) are correctly mounted in the `minty` pod.

---

### Step 3: Verify Inference Service Proxy & Telemetry Pipelines

Troubleshoot connectivity between the `PlatformAgent` gateway pod and the `LiteLLM` (hosted models) or `vLLM` (local GPU models) inference proxy endpoints, along with OTel/Prometheus telemetry exporting.

**Commands:**

```bash
# 1. Check inference proxy deployment health (`litellm` or `vllm`)
kubectl get pods -n kubeagents-system -l 'app.kubernetes.io/name in (litellm, vllm)' -o wide

# 2. Test HTTP response from the completions endpoint from inside the namespace
kubectl run curl-test -n kubeagents-system --rm -it --image=curlimages/curl -- curl -s -o /dev/null -w "%{http_code}\n" http://litellm.kubeagents-system.svc.cluster.local:4000/health

# 3. Check OTel collector export status (`gke-managed-otel` namespace)
kubectl get pods -n gke-managed-otel -o wide
```

#### Diagnostic Checks:

- **`502 Bad Gateway` / `Connection Refused` on Inference Endpoint:** Check API key validity in `litellm-config` ConfigMap or inspect `vLLM` GPU allocation (`nvidia.com/gpu`) and CUDA out-of-memory errors in container logs.
- **Missing Telemetry in GCP Console:** Verify that `gke-managed-otel` pods are running and ServiceAccount holds `roles/cloudtrace.agent` and `roles/monitoring.metricWriter`.

---

### Step 4: Construct Clickable GCP Console Telemetry Links

Whenever debugging `kube-agents` control plane issues, construct and present direct, clickable Google Cloud Console links for the active project ID (`<project_id>`) as mandated by `SOUL.md`:

- **Cloud Logging (Operator & Agent Logs):**
  `https://console.cloud.google.com/logs/query;query=resource.labels.namespace_name%3D%22kubeagents-system%22%0Aresource.type%3D%22k8s_container%22?project=<project_id>`
- **Cloud Trace (LiteLLM/vLLM Inference Traces):**
  `https://console.cloud.google.com/traces/list?project=<project_id>`
- **Cloud Monitoring (Prometheus Metrics Explorer):**
  `https://console.cloud.google.com/monitoring/metrics-explorer?project=<project_id>`
- **GKE Workloads Console:**
  `https://console.cloud.google.com/kubernetes/workload/overview?project=<project_id>`

---

### Step 5: GitOps Remediation Boundary

1. **Never mutate live control-plane deployments manually (`kubectl edit deployment/k8s-operator`).**
2. If `k8s-operator` manifests, `minty` secrets, or `LiteLLM` configurations require adjustments, synthesize the exact root cause in your status report.
3. Generate the corrected manifest YAML patch and submit a Git branch/Pull Request via the **`submit-suggestion`** skill (`./skills/submit-suggestion/scripts/submit_suggestion.py`).
