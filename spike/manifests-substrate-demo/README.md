# Substrate & Hermes AgentHarness Research Spike (`kagent` v0.9.9 + Substrate v0.0.6)

This directory contains the independent, isolated research spike evaluating stateful AI agent orchestration on Kubernetes using **`kagent` (v0.9.9)** and **Agent Substrate (v0.0.6)** with the default built-in **Hermes** harness.

Unlike hybrid or BYO regular deployment models that rely on custom-built agent container images, this research strictly validates standard upstream runtime capabilities and declarative `AgentHarness` orchestration on GKE Standard (`us-central1`).

---

## 1. Executive Summary & Key Findings

* **Stateless vs. Stateful Scope Limitation:** Initial investigation followed the official [Agent Substrate documentation](https://www.kagent.dev/docs/kagent/examples/agent-substrate). While that guide cleanly provisions the core `kagent` and `ate-system` control plane, it explicitly notes that it focuses exclusively on stateless, ephemeral `SandboxAgent` execution environments. It does not cover stateful `AgentHarness` deployments required for persistent AI agent runtimes that maintain memory checkpoints and long-running tool loops.
* **Hermes Runtime Incompatibility:** To evaluate stateful agent harnesses, validation transitioned to the official [Hermes AgentHarness guide](https://www.kagent.dev/docs/kagent/examples/agent-harness#create-a-hermes-harness) using the built-in demo image (`ghcr.io/kagent-dev/hermes/sandbox-base`). Upon deployment, `AgentHarness/hermes-shell` immediately entered an error state (`Reconciler error: while creating workload from spec: rpc error: code = InvalidArgument desc = invalid actor template`). Code inspection confirmed that in `v0.9.9`/`v0.0.6`, **Hermes is not supported as a Substrate runtime target**; the controller omits mandatory `spec.runsc` (gVisor sandboxing) binary mappings from the generated `ActorTemplate`, causing the Substrate API server to reject the resource.
* **Version Matrix Constraints:** Exhaustive matrix testing confirmed that mixing early-beta component releases across repositories introduces breaking package version incompatibilities and CLI flag crashes (e.g., Substrate nightly `v0.0.7` chart templates inject `--router-service-name` flags that crash stable `v0.0.6` pods with `unknown flag`). Strict version locking to **`kagent v0.9.9`** and **`Substrate v0.0.6`** is mandatory for cluster stability.
* **Follow-up OpenClaw Validation (High-Level Overview):** As a follow-up activity, validation was performed using the built-in [OpenClaw AgentHarness guide](https://www.kagent.dev/docs/kagent/examples/agent-harness#create-an-openclaw-harness). While the unsupported runtime error disappeared (confirming native OpenClaw support in `v0.9.9`), achieving end-to-end readiness uncovered additional infrastructure hurdles—including GKE Workload Identity OIDC discovery routing, WorkerPool scaling resets, HDD storage write limits, and remote image pull timeouts.

---

## 2. Commit Scope & Repository Inventory

This commit scope contains strictly the files required to provision the locked `v0.9.9` / `v0.0.6` control plane and reproduce the built-in Hermes harness research. All OIDC proxy workarounds and temporary diagnostic scripts are excluded.

| File | Purpose |
| :--- | :--- |
| `README.md` | This research overview, findings summary, and step-by-step reproduction guide. |
| `setup_step1_substrate_env.sh` | Automated bootstrapping script installing Substrate (`ate-system`) and kagent (`kagent`) enforcing locked releases (`v0.0.6` / `v0.9.9`). |
| `values-demo.yaml` | Locked Substrate Helm chart configuration disabling unneeded default agents and configuring `ateomImage: "...v0.0.6"`. |
| `model-config.yaml` | Declarative `ModelConfig` resource wiring Gemini models (`gemini-3.5-flash`) to local LiteLLM gateways. |
| `hermes-harness.yaml` | Manifest for the built-in Hermes harness (demonstrating the `Invalid actor template` runtime incompatibility in `v0.9.9`/`v0.0.6`). |
| `deploy_step2_hermes_harness.sh` | Automated deployment script to apply and test the built-in Hermes harness. |

---

## 3. Step-by-Step Reproduction Guide

The research environment is configured for GKE cluster **`kagent-substrate-demo`** (`us-central1`, project `eleontev-kube-agents`).

### Prerequisites
Authenticate and fetch target cluster credentials:
```bash
gcloud container clusters get-credentials kagent-substrate-demo \
  --region us-central1 \
  --project eleontev-kube-agents
```

### Step 1: Bootstrap Substrate & kagent Control Plane
Execute the bootstrapping script to install the Substrate data plane (`ate-system`) and the kagent controller (`kagent`) with Substrate support enabled:
```bash
bash spike/manifests-substrate-demo/setup_step1_substrate_env.sh
```

### Step 2: Deploy Built-In Hermes Harness
To observe the runtime incompatibility between Hermes and Substrate in `v0.9.9`/`v0.0.6`, deploy the built-in Hermes harness:
```bash
bash spike/manifests-substrate-demo/deploy_step2_hermes_harness.sh
```

### Step 3: Verify Controller Rejection
Check the controller status on the deployed harness to witness the validation error:
```bash
kubectl -n kagent get agentharnesses
kubectl -n kagent describe agentharness hermes-shell | grep -iE "status|reason|message|error" -A 5
```
*Expected Output:* `Reconciler error: while creating workload from spec: rpc error: code = InvalidArgument desc = invalid actor template`.
