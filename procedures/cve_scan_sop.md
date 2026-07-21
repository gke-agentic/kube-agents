# Standard Operating Procedure: CVE Vulnerability Scan & Remediation (`cve_scan_sop.md`)

## Purpose

Enforces proactive container vulnerability auditing across all running Pods and container images within targeted namespaces.

## Scope

- Namespace container images
- Running Pod deployments and StatefulSets
- Container image registries (e.g., Google Container Registry / Artifact Registry)

## Procedure

1. **Inventory Workloads:** List all running container images across targeted namespaces using `kubectl get pods -o jsonpath`.
2. **Scan Vulnerabilities:** Inspect image digests against known security advisories or vulnerability databases (CVSS score >= 7.0 High/Critical).
3. **Assess Blast Radius:** Determine if affected containers run with elevated privileges (`privileged: true` or host network mounts).
4. **Remediate:**
   - If `workflowMode: GitOps`, generate a pull request updating base image tags or patching dependencies.
   - If `workflowMode: Direct`, apply updated non-vulnerable container image tags or cordon affected workloads.
   - If `workflowMode: Hybrid`, emit a remediation proposal and await human approval.
