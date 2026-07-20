---
name: review-observability-k8s-metrics-coverage
description: Reviews Kubernetes manifests for Prometheus scrape configurations and matching monitoring coverage resources.
---
# Task
Review Kubernetes manifests to ensure any workload declaring scrape intent is covered by appropriate monitoring resources (e.g. PodMonitoring).

# Checks
- **Scrape Intent Identification**: Scan manifests for workloads (Deployments, StatefulSets, DaemonSets, Pods) containing the annotation `prometheus.io/scrape: "true"`.
- **Monitoring Resource Search**: Check if corresponding `PodMonitoring`, `ClusterPodMonitoring`, or `ServiceMonitor` resources selecting the workload exist anywhere in the repository.
- **Coverage Validation**:
  - **Fire finding when**: A workload declares `prometheus.io/scrape: "true"` but no selecting monitoring resource (`PodMonitoring`, `ClusterPodMonitoring`, `ServiceMonitor`) is declared in the repository.
  - **Suppress finding when**: Global GKE Autopilot Managed Prometheus is enabled (`enable_autopilot = true` in cluster configuration) AND the default Managed Prometheus posture covers the workload (e.g., standard namespace metrics are gathered automatically without custom resources).
- **Format Findings**: Output findings in the canonical JSON format:
  ```json
  [{"agent": "review-observability-k8s-metrics-coverage", "findings": [{"message": "Workload declares 'prometheus.io/scrape: true' but is missing selecting PodMonitoring, ClusterPodMonitoring, or ServiceMonitor.", "file": "<manifest-file-path>", "line": "<line-number>"}]}]
  ```
