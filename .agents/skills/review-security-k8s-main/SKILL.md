---
name: review-security-k8s-main
description: Orchestrates comprehensive Kubernetes security reviews.
---
# Task
Coordinate Kubernetes security review sub-agents, gather findings, and produce a summarized JSON report.

# Workflow
## 1. Context
Invoke `review-security-k8s-understand`. Wait for summary.

## 2. Parallel Reviews
Pass context and launch in parallel:
- `review-security-k8s-rbac`
- `review-security-k8s-nodes`
- `review-security-k8s-network`
- `review-security-k8s-gateway`
- `review-security-k8s-namespaces`
- `review-security-k8s-service-accounts`
- `review-security-k8s-storage`
- `review-security-k8s-admission`
- `review-security-k8s-pod`
- `review-security-k8s-agents-main`

**CRITICAL**: Instruct each to output JSON:
```json
[{"agent": "<skill>", "findings": [{"message": "<desc>", "file": "<name>", "line": "<num>"}]}]
```
(Return empty list if no findings). Wait for completion.

## 3. Aggregation
Merge into a single JSON array. Output MUST be valid JSON string (markdown blocks okay). Omit agents with no findings or return empty `findings`.
