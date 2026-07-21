#!/usr/bin/env bash
# Pre-flight Screening Hook: skills/gke-observability/scripts/preflight.sh
# Protocol: Exit 0 = Silent termination (No LLM wake); Exit 1 = Wake LLM with stdout payload

set -euo pipefail

TARGET_NS="${1:-default}"

# Check for Pods in CrashLoopBackOff or ImagePullBackOff without waking LLM
CRASHING_PODS=$(kubectl get pods -n "$TARGET_NS" --field-selector=status.phase!=Running,status.phase!=Succeeded -o json | jq -r '
  [.items[] | select(.status.containerStatuses[]?.state.waiting.reason // "" | test("CrashLoopBackOff|ImagePullBackOff|ErrImagePull"))] | length
' 2>/dev/null || echo "0")

if [ "${CRASHING_PODS:-0}" -eq 0 ]; then
  exit 0
else
  echo "OBSERVABILITY_ANOMALY_METRICS: Detected ${CRASHING_PODS} pods in crash or pull backoff in namespace ${TARGET_NS}."
  echo "RECOMMENDED_ACTION: Inspect pod events and previous crash logs via deployment_failure_resolver_sop.md."
  exit 1
fi
