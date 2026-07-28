#!/bin/bash
# cluster_preflight.sh - Read-only self-check a Cluster Agent runs before working
# a kanban task, so it can report *what is wrong* instead of crashing silently.
#
# Why this exists: a dispatcher-spawned Cluster Agent whose environment is broken
# (missing/stale KUBECONFIG, unreachable cluster, missing identity) would exit
# without ever calling kanban_complete/kanban_block. Hermes then marks the card
# crashed and the user only sees a generic "the agent crashed on startup" with no
# cause. The Cluster Agent runs this first (see cluster SOUL.md §6); on FAILED it
# blocks the card with the reason below (kanban_block kind="needs_input").
#
# Deploys to /opt/data/scripts/cluster_preflight.sh via the same path as
# kanban_notify_propagate.py (agents/platform/scripts -> /opt/defaults/scripts ->
# /opt/data/scripts), so it reaches existing cluster profiles on image roll.
#
# Strictly read-only: only `kubectl cluster-info` (a GET) and local file reads.
#
# Usage:  bash cluster_preflight.sh [--json]
# Exit:   0 = PREFLIGHT OK, non-zero = PREFLIGHT FAILED (reason on stdout).

set -u

JSON=0
[ "${1:-}" = "--json" ] && JSON=1

HERMES_HOME="${HERMES_HOME:-/opt/data}"
# On the dispatch path the worker rewrites HERMES_HOME to its own profile home,
# where the scaffold pins kubeconfig.yaml and writes USER.md. Fall back to that
# pinned kubeconfig when KUBECONFIG is not already exported.
KUBECONFIG="${KUBECONFIG:-$HERMES_HOME/kubeconfig.yaml}"
USER_MD="$HERMES_HOME/USER.md"

STATUS="ok"
REASON=""
REMEDIATION=""
EVIDENCE=""

fail() {
    STATUS="failed"
    REASON="$1"
    REMEDIATION="$2"
    EVIDENCE="${3:-}"
}

# 1. Fixed cluster identity present (project/cluster/location live in USER.md).
if [ "$STATUS" = "ok" ]; then
    if [ ! -f "$USER_MD" ]; then
        fail "This Cluster Agent has no identity file (USER.md missing at $USER_MD)." \
             "Re-scaffold the profile via the Platform Agent (cluster_agent_profile.py create)." \
             "expected identity file not found: $USER_MD"
    elif ! grep -qi "cluster:" "$USER_MD"; then
        fail "This Cluster Agent's identity file is present but incomplete (no cluster identity)." \
             "Re-scaffold the profile so USER.md records its project/cluster/location." \
             "USER.md present but missing 'cluster:' at $USER_MD"
    fi
fi

# 2. Kubeconfig pinned and non-empty.
if [ "$STATUS" = "ok" ]; then
    if [ ! -f "$KUBECONFIG" ]; then
        fail "Kubeconfig is not pinned for this cluster (no file at $KUBECONFIG)." \
             "Re-scaffold the profile; scaffolding runs 'gcloud container clusters get-credentials' and pins KUBECONFIG via <home>/.env." \
             "missing kubeconfig: $KUBECONFIG"
    elif [ ! -s "$KUBECONFIG" ]; then
        fail "Kubeconfig at $KUBECONFIG is empty." \
             "Re-scaffold the profile to re-fetch cluster credentials." \
             "empty kubeconfig: $KUBECONFIG"
    fi
fi

# 3. Cluster reachable (read-only GET). Captures the real API error verbatim
#    (403 access denied, cluster not found, connection timeout, ...).
if [ "$STATUS" = "ok" ]; then
    if ! command -v kubectl >/dev/null 2>&1; then
        fail "kubectl is not available in this agent's environment." \
             "This indicates a broken image/toolset; escalate to the Platform Agent." \
             "kubectl not found on PATH"
    else
        # Hard wall-clock cap (`timeout`) in addition to --request-timeout: a
        # black-holed API endpoint can stall the TCP connect well past the
        # request timeout, and a hung preflight would itself look like a crash.
        ERR="$(timeout 15 env KUBECONFIG="$KUBECONFIG" kubectl cluster-info --request-timeout=8s 2>&1 >/dev/null)"
        rc=$?
        [ "$rc" -eq 124 ] && ERR="timed out after 15s contacting the cluster API server"
        if [ "$rc" -ne 0 ]; then
            # Collapse to a single line so it reads cleanly on the kanban card.
            ERR_ONE="$(printf '%s' "$ERR" | tr '\n' ' ' | sed 's/  */ /g' | cut -c1-500)"
            fail "Cannot reach the target cluster's API server." \
                 "The cluster may be deleted, unreachable, or the agent's credentials lack access. Verify the cluster exists and the agent's service account has GKE access; then re-scaffold if needed." \
                 "kubectl cluster-info: $ERR_ONE"
        fi
    fi
fi

# ---- Output -----------------------------------------------------------------
json_escape() { printf '%s' "$1" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))' 2>/dev/null || printf '"%s"' "$(printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g')"; }

if [ "$JSON" = "1" ]; then
    printf '{"status": %s, "reason": %s, "remediation": %s, "evidence": %s}\n' \
        "$(json_escape "$STATUS")" \
        "$(json_escape "$REASON")" \
        "$(json_escape "$REMEDIATION")" \
        "$(json_escape "$EVIDENCE")"
else
    if [ "$STATUS" = "ok" ]; then
        echo "PREFLIGHT: OK"
    else
        echo "PREFLIGHT: FAILED"
        echo "reason: $REASON"
        echo "remediation: $REMEDIATION"
        [ -n "$EVIDENCE" ] && echo "evidence: $EVIDENCE"
    fi
fi

[ "$STATUS" = "ok" ]
