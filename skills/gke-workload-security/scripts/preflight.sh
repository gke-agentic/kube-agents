#!/usr/bin/env bash
# Pre-flight Screening Hook: skills/gke-workload-security/scripts/preflight.sh
# Protocol: Exit 0 = Silent termination (No LLM wake); Exit 1 = Wake LLM with stdout payload

set -euo pipefail

TARGET_NS="${1:-default}"

PRIVILEGED_COUNT=$(kubectl get pods -n "$TARGET_NS" -o json | jq -r '
  [.items[] | .spec.containers[]? | select(.securityContext.privileged == true)] | length
' 2>/dev/null || echo "0")

if [ "${PRIVILEGED_COUNT:-0}" -eq 0 ]; then
  exit 0
else
  echo "SECURITY_ANOMALY_METRICS: Detected ${PRIVILEGED_COUNT} privileged containers in namespace ${TARGET_NS}."
  echo "RECOMMENDED_ACTION: Perform deep security audit of Pod security contexts and RBAC grants."
  exit 1
fi
