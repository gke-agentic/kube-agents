# Standard Operating Procedure: Weekly Cluster Cost & FinOps Report (`weekly_cost_report_sop.md`)

## Purpose

Generates a recurring weekly FinOps analysis of cluster compute utilization, overprovisioned requests, and idle workloads.

## Scope

- CPU/Memory Requests vs. Actual P95 Usage
- HorizontalPodAutoscaler (HPA) targets
- Idle or abandoned environments

## Procedure

1. **Collect Utilization Metrics:** Aggregate CPU and memory utilization over the preceding 7 days across all target namespaces.
2. **Identify Idle Waste:** Flag deployments whose average utilization is below 15% of requested resources.
3. **Generate Actionable Recommendations:**
   - Recommend HPA minReplicas/maxReplicas right-sizing.
   - Calculate monthly dollar savings for each recommended right-size operation.
4. **Publish Findings:** Output structured Markdown summary to Slack/Google Chat integration channels or commit report artifact to the target Git repository.
