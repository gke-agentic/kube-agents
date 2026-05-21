---
name: review-security-k8s-agents-main
description: Orchestrates comprehensive Kubernetes security audits specifically tailored for AI agent workloads.
---
# Task
Coordinate AI agent security review sub-agents, gather findings, and produce a summarized JSON report.

# Workflow
## 1. Context Ingestion
Pass project context (from `review-security-k8s-understand`) to sub-agents.

## 2. Parallel Reviews
Launch in parallel:
- `review-security-k8s-agents-sandbox`
- `review-security-k8s-agents-firewall`
- `review-security-k8s-agents-credentials`
- `review-security-k8s-agents-prompt-injection`
- `review-security-k8s-agents-data-exfil`
- `review-security-k8s-agents-audit-logs`

**CRITICAL**: Instruct each to output JSON:
```json
[{"agent": "<skill-name>", "findings": [{"message": "<desc>", "file": "<name>", "line": "<num>"}]}]
```
(Return empty list if no findings). Wait for completion.

## 3. Aggregation
Merge outputs into a single JSON array. Output MUST be valid JSON (markdown blocks okay). Omit agents with no findings or return empty `findings`.
