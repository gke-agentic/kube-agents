#!/usr/bin/env bash
# Pre-flight Screening Hook: skills/gke-cost-analysis/scripts/preflight.sh
# Protocol: Exit 0 = Silent termination (No LLM wake); Exit 1 = Wake LLM with stdout payload

set -euo pipefail

TARGET_NS="${1:-default}"

# Perform rapid, non-cognitive checks using kubectl and jq
OVERPROVISIONED_CPU=$(kubectl get pods -n "$TARGET_NS" -o json | jq -r '
  [.items[] | .spec.containers[] | select(.resources.requests.cpu != null) |
  ( .resources.requests.cpu | ltrimstr("0.") | tonumber )] | add // 0
' 2>/dev/null || echo "0")

# Threshold check (e.g., alert if requested CPU units exceed idle baseline)
if [ "${OVERPROVISIONED_CPU:-0}" -lt 5000 ]; then
  # Cluster resource allocation is within optimal parameters. Exit silently.
  exit 0
else
  # Waste detected! Emit metrics payload to stdout and exit 1 to wake cognitive agent.
  echo "OVERPROVISIONING_METRICS: Detected ${OVERPROVISIONED_CPU}m CPU requested without corresponding load."
  echo "RECOMMENDED_ACTION: Initiate cognitive evaluation of HorizontalPodAutoscaler targets in namespace ${TARGET_NS}."
  exit 1
fi
