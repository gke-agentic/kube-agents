# M2 DASP Migration & Parallel Co-existence Walkthroughs

This document provides step-by-step SRE verification guides for running unified `KubeAgent` resources concurrently with legacy `PlatformAgent` workloads during Phases 1–4 of the DASP Migration Roadmap.

---

## 1. Architecture Overview: Dual Reconciler Co-existence

During migration, the `k8s-operator` runs both legacy controllers (`PlatformAgentReconciler`) and the new unified `KubeAgentReconciler` within the same manager process. Resource collisions are prevented through:

- **ConfigMap Namespacing:** `KubeAgentReconciler` creates ConfigMaps suffixed with `-workspace-config` and `-schedule-config`.
- **Distinct Deployment Names:** Autonomous `KubeAgent` profiles use unique `metadata.name` identifiers (e.g., `cluster-cost-optimizer` or `devteam-security-auditor`).
- **Zero Admission Cardinality Enforcement:** Unlike legacy webhooks which restricted instances to 1 per project or namespace, `KubeAgent` allows unlimited specialized instances.

---

## 2. Walkthrough 1: Deploying Parallel Agent Workloads

### Step 1: Apply the CRDs

```bash
kubectl apply -f k8s-operator/config/crd/bases/kubeagents.x-k8s.io_platformagents.yaml
kubectl apply -f k8s-operator/config/crd/bases/kubeagents.x-k8s.io_kubeagents.yaml
```

### Step 2: Deploy the Legacy Platform Agent

```yaml
apiVersion: kubeagents.x-k8s.io/v1alpha1
kind: PlatformAgent
metadata:
  name: platform-agent
  namespace: kubeagents-system
spec:
  harness:
    clusterName: "gke-production-us-central1"
    location: "us-central1"
    projectId: "enterprise-gke-ops"
```

```bash
kubectl apply -f legacy-platform-agent.yaml
```

### Step 3: Deploy a Composable KubeAgent (`cluster-cost-optimizer`)

```yaml
apiVersion: kubeagents.x-k8s.io/v1alpha1
kind: KubeAgent
metadata:
  name: cluster-cost-optimizer
  namespace: kubeagents-system
spec:
  personaRef:
    name: frugal-cost-optimizer
    ref: "./personas/frugal-cost-optimizer/SOUL.md"
  skills:
    - name: gke-cost-analysis
      ref: "./skills/gke-cost-analysis"
  procedures:
    - name: weekly_cost_report_sop.md
      ref: "./procedures/weekly_cost_report_sop.md"
  namespaces:
    - "app-frontend"
    - "app-backend"
  workflowMode: "Hybrid"
```

```bash
kubectl apply -f templates/operator/agent-profile.yaml
```

### Step 4: Verify Multi-Namespace RBAC Injection

Verify that `KubeAgentReconciler` automatically provisioned least-privilege `RoleBinding` resources across targeted namespaces:

```bash
kubectl get rolebinding -n app-frontend
kubectl get rolebinding -n app-backend
```

---

## 3. Walkthrough 2: Interactive Studio & Approval Flow (`kube-agent-cli`)

### Step 1: Launch Interactive Studio TUI

```bash
kube-agent-cli studio
```

Navigate through the Persona, Skills, Procedures, and WorkflowMode sections using arrow keys and spacebar. Press `[Enter]` to compile and apply directly to the active cluster.

### Step 2: Approve Gated Mutations in Hybrid Mode

When an agent proposes cluster mutations in `Hybrid` workflow mode:

```bash
kube-agent-cli approve cluster-cost-optimizer
```

Review the terminal diff preview and approve or reject the mutation interactively.
