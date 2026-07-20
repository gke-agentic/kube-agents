---
name: gke-quota-and-limits-checker
description: Audits namespace ResourceQuotas, LimitRanges, and unconstrained container CPU/memory limits.
---

# GKE Resource Quotas and Limits Checker Skill

This skill provides diagnostic capabilities to audit GKE cluster resources, ensuring containers have proper resource limits set, tracking ResourceQuota consumption, and validating namespace LimitRanges.

## Workflows

### 1. Audit Unconstrained Containers

Identify containers running in the cluster that lack explicit CPU/memory limits or requests, which can lead to node starvation or noisy neighbor issues.

**Command:**
```bash
kubectl get pods --all-namespaces -o jsonpath='{range .items[*]}{.metadata.namespace}{"\t"}{.metadata.name}{"\t"}{range .spec.containers[*]}{.name}{"\tLimits-CPU="}{.resources.limits.cpu}{"\tLimits-Mem="}{.resources.limits.memory}{"\n"}{end}{end}'
```

### 2. Inspect Namespace ResourceQuotas

List and verify the status of ResourceQuota specifications across all active namespaces.

**Command:**
```bash
kubectl get resourcequotas --all-namespaces -o wide
```

### 3. Check Namespace LimitRanges

Audit the default LimitRange configuration configured on namespaces to auto-inject limits when pods do not specify them.

**Command:**
```bash
kubectl get limitranges --all-namespaces
```
