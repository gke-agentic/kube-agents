# KubeAgent & DASP Installation Guide

This guide explains how to install and assemble specialized Kubernetes AI agents using the **Dynamic Assembly Specification (DASP)** and the unified **`KubeAgent` CRD**.

Under DASP, any specialized agent profile (e.g., `frugal-cost-optimizer`, `paranoid-security-auditor`, or `standard-operator`) is assembled dynamically from root building blocks (`personas/`, `skills/`, `procedures/`, and `schedules/`) using `kube-agent-cli` or declarative Kubernetes Custom Resources.

## Prerequisites

- Kubernetes CLI (`kubectl`) configured with access to your target GKE clusters.
- **cert-manager** (v1.13.0+) installed on the target Kubernetes cluster for webhook TLS certificate management:
  ```bash
  helm repo add jetstack https://charts.jetstack.io
  helm repo update
  helm install cert-manager jetstack/cert-manager \
    --namespace cert-manager \
    --create-namespace \
    --set installCRDs=true
  ```

## 1. Interactive Assembly via TUI Studio (`kube-agent-cli`)

Launch the terminal UI studio to interactively assemble and deploy a specialized `KubeAgent`:

```bash
cd cli && go run ./cmd/main.go studio
```

From the studio:
1. Select your base **Persona** archetype (e.g. `frugal-cost-optimizer`).
2. Multi-select required **Skills** (e.g. `gke-cost-analysis`).
3. Set your target operational namespaces and **Procedures** (SOPs).
4. Choose your **Workflow Mode** (`Direct`, `GitOps`, or `Hybrid`) and deploy.

## 2. Declarative Deployment via `KubeAgent` CRD

You can apply a declarative `KubeAgent` manifest directly to the cluster:

```yaml
apiVersion: kubeagents.x-k8s.io/v1alpha1
kind: KubeAgent
metadata:
  name: cluster-cost-optimizer
  namespace: agent-system
spec:
  personaRef:
    name: frugal-cost-optimizer
    ref: "./personas/frugal-cost-optimizer/SOUL.md"
  skills:
    - name: gke-cost-analysis
      ref: "./skills/gke-cost-analysis"
  namespaces:
    - "app-frontend"
    - "app-backend"
  workflowMode: "Hybrid"
```

Apply using `kubectl`:

```bash
kubectl apply -f examples/kubeagent.yaml
```

## 3. Recreating Full `agents/` Directory via DASP Templates

If you need headless transpilation or offline testing, compile the seed DASP templates (`templates/platform/agent-profile.yaml`, `templates/operator/agent-profile.yaml`, and `templates/devteam/agent-profile.yaml`) into local `agents/` folders:

```bash
make compile-agents
```
