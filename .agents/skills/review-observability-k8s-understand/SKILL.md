---
name: review-observability-k8s-understand
description: Analyzes Kubernetes project architecture and resources to build context before performing specific observability reviews.
---
# Task
Analyze the Kubernetes project/repository to build comprehensive architectural and observability context for specialized review agents.

# Checks
- **Architecture**: Identify main components, workloads, cluster configuration (e.g., enable_autopilot), and deployment patterns.
- **Docs**: Read observability documentation, Prometheus configurations, or monitoring dashboards.
- **Categorization**: Explicitly categorize workloads as either *Infrastructure* (requiring system-level monitoring) or *Application* (requiring custom metrics scrape).
- **Existing Monitoring**: Identify presence of global scrape configs, PodMonitoring, ClusterPodMonitoring, or ServiceMonitor definitions.

# Output
Output a concise summary of project purpose, workload categories, and existing monitoring configurations.
