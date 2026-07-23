---
name: kube-agents-maintain-and-debug
description: >-
  Audits, diagnoses, and manages health anomalies, pod freezes, auth drift,
  state corruption, and admission lockouts across the internal Kube-Agents
  platform harness (agent-system). Formulates interactive Google Chat / Slack
  remediation proposals, executes verified fixes upon human approval, and
  escalates declarative code or infra bugs to the GitOps repository.
---

# Task

Diagnose, triage, and manage the operational health of the internal Kube-Agents Platform Agent harness in `kubeagents-system`, `agent-system`, and `kube-agents-operator-system`.

# SRE Workflow: Interactive Approvals & GitOps Escalation

When this skill is invoked or triggered via background cron ([jobs.json](../../cron/jobs.json)), follow this 4-step dynamic investigative procedure:

```mermaid
graph TD
    A[Trigger / Cron Turn] --> B[1. Triage: Run Telemetry Collector]
    B --> C{Cluster Healthy?}
    C -->|Yes: Status HEALTHY| D[Healthy System Override: Return SILENT]
    C -->|No: Status DEGRADED| E[2. Inspect Telemetry & active_incidents]
    E --> F{Fixable via Runtime Command?}
    F -->|Yes: Runtime Fix| G[Post Interactive Proposal Card to Google Chat]
    F -->|No: Code / Image Bug| H[File Structured Issue in GitOps Repo]
    G --> I{Human Approves?}
    I -->|Yes: "Approve"| J[Execute Fix & Run 60s Health Verification]
    I -->|No / Inactive| K[ZERO ACTION TAKEN: Maintain Cluster State]
    J -->|Verified 200 OK| L[Post ✅ Self-Healing Receipt & Mark RESOLVED]
    J -->|Failed / Timeout| M[Auto-Revert Command & File GitOps Issue]
```

### Step 1: Cluster Health Triage
Execute the telemetry collector to gather structured facts across pods, quotas, events, probes, and active incident states:
```bash
python3 /opt/data/skills/kube-agents-maintain-and-debug/scripts/maintain.py diagnose --json || python3 scripts/maintain.py diagnose --json
```

### Step 2: Dynamic Root-Cause Analysis & Routing
- **Healthy System Override**: If `status == "HEALTHY"` and all pods are `Running`, return **`[SILENT]`** immediately to suppress chat noise.
- **Incident & Issue Deduplication (Single Source of Truth)**: Inspect `open_prs` and `open_issues` in the telemetry. If an open PR or Issue on GitHub already exists related to the component (e.g. `github-token-minter`) OR matching the specific diagnosed failure symptom/root cause (e.g. `ImagePullBackOff`), return **`[SILENT]`** immediately to prevent creating duplicate tickets/PRs on GitHub.
- **Declarative Failure Routing (`ImagePullBackOff` / Non-existent Image Tag / Manifest Drift)**: Skip Step 3 (Interactive Proposal Card). Proceed directly to **Step 5 (Declarative GitOps PR Escalation)** to invoke `maintain.py create-gitops-pr`.

---

### Step 3: Interactive Approval Proposal Card (Google Chat / Slack)

If a new runtime-recoverable anomaly is detected (e.g. `CrashLoopBackOff`, Secret key drift, deadlocked pod, or stale webhook), **NEVER execute terminal mutations autonomously**. (Note: For `ImagePullBackOff`, skip this step and proceed to Step 5).

Synthesize the forensic evidence and post the **Interactive Proposal Card** for Google Chat / Slack:

```markdown
⚠️ **[P0 SRE PROPOSAL] Actionable Cluster Incident Detected**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 **Component:** `deployment/<name>` (`kubeagents-system`)
🔍 **Diagnosed Root Cause:** `<diagnosed failure layer>`
📋 **Forensic Log Proof:** `<extracted error line from container logs>`
🛠️ **Proposed Fix:** `<exact kubectl or patch command>`
🛡️ **Safety Guardrail:** 60s Verification + Auto-Revert on failure

👉 **Reply `Approve` to execute.**
👉 **Reply `Reject` to escalate to GitOps.**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Record the pending state to disk:
```bash
python3 scripts/maintain.py record-incident --component "<component>" --state "AWAITING_APPROVAL" --action "<proposed kubectl cmd>"
```

---

### Step 4: Human Action & 60-Second Verification Protocol

#### Case A: User Does Not Respond / Inactive
- **Inviolable SRE Inaction Rule:** **Take ZERO action and make ZERO cluster changes.** The cluster state remains unchanged until human confirmation is received.

#### Case B: User Replies `"Approve"`
1. **Execution:** Execute the proposed `kubectl` command.
2. **60-Second Health Probe Loop:**
   ```bash
   kubectl rollout status deployment/<deployment-name> -n kubeagents-system --timeout=60s
   ```
3. **Verification Branching:**
   - **If Verified (`1/1 Running` & HTTP 200 OK):**
     Update incident state to `RESOLVED` and post the **✅ Self-Healing Receipt**:
     ```markdown
     ✅ **Platform Agent Self-Healing — Component Recovered:**
     - **Component:** `<healed-component>` (`<namespace>`)
     - **Diagnosed Root Cause:** `<root cause>`
     - **Remediation Applied:** `<executed command>`
     - **Verification Status:** `Running` (HTTP 200 OK)
     ```
   - **If Verification Fails / Times Out:**
     1. Automatically execute the rollback command to revert the cluster to the prior state.
     2. Escalate directly to the **GitOps Repository**.

---

### Step 5: Declarative GitOps Issue & Fallback PR Escalation

When an issue requires code/manifest investigation, new Docker images (`ImagePullBackOff`), quota expansions, or when the user replies `Reject` (or if 60s health verification fails):

1. **Target Repository Resolution:** Dynamically extract the GitOps repository URL from `/opt/data/SETTINGS.md`.
2. **Issue-First Escalation & Fallback PR (Zero Code Lines Changed):**
   - If Issues are enabled, open a **GitHub Issue** ticket.
   - If Issues are disabled, open a **Pull Request** as a fallback ticket.
   - **Zero Code Lines Changed:** The PR acts purely as an SRE diagnostic report card detailing the symptom, root cause, forensic logs, and step-by-step resolution instructions. No application source code or manifest lines are mutated automatically.
   ```bash
   python3 /opt/data/skills/kube-agents-maintain-and-debug/scripts/maintain.py create-gitops-pr \
     --component "<component>" \
     --root-cause "<diagnosed root cause>" \
     --logs "<error logs>" \
     --action "<proposed resolution instructions>"
   ```

---

# Execution Guardrails & Circuit Breakers

### ⚡ Anti-Flapping Circuit Breakers
If a container is stuck in a chronic crash loop where previous rollbacks/restarts failed to stabilize the pod:
1. Pause posting interactive chat cards to prevent human alert fatigue.
2. Mark the incident state as `"FLAPPING_CIRCUIT_BREAKER_TRIPPED"` in `incidents.json`.
3. Escalate the chronic failure directly to the GitOps Repository as an infrastructure bug.

### 🛡️ Negative Safety Red Lines (What NEVER to Touch)
- **Declarative Scope Guardrail (No Source Code Modifications)**: Automated GitOps Pull Requests must **ONLY modify declarative manifest files** (`.yaml`, `.yml`, `.template`). NEVER attempt to modify application source code files (`.go`, `.py`, `.js`, etc.) in automated remediation PRs.
- **No Storage Mutations**: NEVER delete `PersistentVolumeClaims` (PVCs), `PersistentVolumes` (PVs), `StatefulSets`, or persistent volume storage.
- **Autonomous Exclusion Boundaries**: All mutations are strictly restricted to `kubeagents-system`, `agent-system`, and `kube-agents-operator-system`. NEVER modify or restart resources in `kube-system`, `gmp-system`, or customer tenant application namespaces. NEVER run `kubectl delete namespace`.
