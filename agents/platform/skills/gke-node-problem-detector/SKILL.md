---
name: gke-node-problem-detector
description: Diagnoses node-level issues, kernel deadlocks, OOM kills, and hardware/system degradation on GKE clusters.
---

# GKE Node Problem Detector Skill

This skill provides diagnostic workflows for inspecting GKE node status, detecting kernel deadlocks, finding read-only filesystems, and investigating Out-Of-Memory (OOM) kills.

## Workflows

### 1. Audit GKE Node Conditions

Inspect cluster nodes to identify standard and custom conditions reported by the Node Problem Detector.

**Command:**
```bash
kubectl get nodes -o custom-columns=NAME:.metadata.name,READY:.status.conditions[?(@.type=="Ready")].status,KERNEL_DEADLOCK:.status.conditions[?(@.type=="KernelDeadlock")].status,READONLY_FS:.status.conditions[?(@.type=="ReadonlyFilesystem")].status
```

### 2. Retrieve Node Problem Events

Query recent events matching typical system errors such as disk pressure, PID pressure, or kernel issues.

**Command:**
```bash
kubectl get events --all-namespaces --field-selector reason=NodeNotReady,reason=OOMKilling -o wide
```

### 3. Check Node Problem Detector Pods

Verify if the node-problem-detector daemonset is active and healthy in the cluster (typically running in `kube-system`).

**Command:**
```bash
kubectl get daemonset node-problem-detector -n kube-system
kubectl get pods -n kube-system -l app=node-problem-detector
```
