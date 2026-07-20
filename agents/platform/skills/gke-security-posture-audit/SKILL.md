---
name: gke-security-posture-audit
description: Audits root execution, privileged containers, hostPath mounts, and Pod Security Admission standards.
---

# GKE Security Posture Audit Skill

This skill provides workflows to perform static and dynamic security posture audits on GKE clusters, identifying privilege escalations, root execution, unsafe hostPath mounts, and verifying namespace PSA standards.

## Workflows

### 1. Identify Privileged and Root Containers

Search across all namespaces for pods running in privileged mode or executing as root.

**Command:**
```bash
kubectl get pods --all-namespaces -o jsonpath='{range .items[*]}{.metadata.namespace}{"\t"}{.metadata.name}{"\t"}{range .spec.containers[*]}{.name}{"\tPrivileged="}{.securityContext.privileged}{"\tRunAsNonRoot="}{.securityContext.runAsNonRoot}{"\n"}{end}{end}'
```

### 2. Check for hostPath Mounts

Find pods that mount local host volumes (`hostPath`), which can bypass container isolation boundaries.

**Command:**
```bash
kubectl get pods --all-namespaces -o jsonpath='{range .items[*]}{.metadata.namespace}{"\t"}{.metadata.name}{"\t"}{range .spec.volumes[?(@.hostPath)]}{"hostPath="}{.hostPath.path}{"\n"}{end}{end}'
```

### 3. Verify Pod Security Admission (PSA) Labels

Audit GKE namespace labels to ensure proper security standards (`enforce=privileged`, `baseline`, or `restricted`) are applied.

**Command:**
```bash
kubectl get namespaces -o custom-columns=NAME:.metadata.name,PSA-ENFORCE:.metadata.labels.pod-security\.kubernetes\.io/enforce,PSA-WARN:.metadata.labels.pod-security\.kubernetes\.io/warn
```
