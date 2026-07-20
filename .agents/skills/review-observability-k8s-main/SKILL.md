---
name: review-observability-k8s-main
description: Orchestrates comprehensive Kubernetes observability reviews.
---
# Task
Coordinate Kubernetes observability review sub-agents, gather metrics and logging coverage findings, and produce a summarized JSON report.

# Workflow
## 1. Context
Invoke `review-observability-k8s-understand`. Wait for summary.

## 2. Parallel Reviews
Pass context and launch in parallel sub-agents:
- `review-observability-k8s-metrics-coverage`

**CRITICAL**: Instruct each to output JSON:
```json
[{"agent": "<skill>", "findings": [{"message": "<desc>", "file": "<name>", "line": "<num>"}]}]
```
(Return empty list if no findings). Wait for completion.

## 3. Triage & Filtering
Evaluate raw findings against the project context to determine real monitoring gaps. Filter out findings that are adequately covered by default platform postures (such as GKE Autopilot default Prometheus scraper or namespace-wide automatic scrapers).

## 4. Aggregation
Merge filtered findings into a single JSON array. Output MUST be valid JSON string (markdown blocks okay). Omit agents with no findings or return empty `findings`.
