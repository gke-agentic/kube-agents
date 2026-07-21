# Standard Operating Procedure: Deployment Failure Resolution (`deployment_failure_resolver_sop.md`)

## Purpose

Standardizes automated triage and remediation of failed Kubernetes deployments (`CrashLoopBackOff`, `ImagePullBackOff`, `OOMKilled`, rollout timeouts).

## Scope

- Pod lifecycle events and termination reasons
- Container logs and previous crash logs (`kubectl logs --previous`)
- Kubernetes Deployment rollout statuses

## Procedure

1. **Detect Rollout Anomaly:** Identify deployments with unavailable replicas or pods in crash/pull error loops.
2. **Root Cause Diagnosis:**
   - For `OOMKilled`: Inspect memory usage limits and container exit status `137`.
   - For `ImagePullBackOff`: Verify image digest existence and ImagePullSecrets.
   - For `CrashLoopBackOff`: Extract stack traces from `kubectl logs --previous`.
3. **Remediation & Escalation:**
   - Apply safe resource bump or config rollback if self-healing is permitted under the active workflow mode.
   - Document root cause analysis with full evidence trail.
