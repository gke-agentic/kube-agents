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
python3 scripts/maintain.py diagnose --json
```

### Step 2: Dynamic Root-Cause Analysis & Deduplication
- **Healthy System Override**: If `status == "HEALTHY"` and all pods are `Running`, return **`[SILENT]`** immediately to suppress chat noise.
- **Incident Deduplication**: If `status == "DEGRADED"` but `active_incidents` in the telemetry indicates the incident is already `AWAITING_APPROVAL` (and no new human message has arrived), return **`[SILENT]`** to prevent chat spam.

---

### Step 3: Interactive Approval Proposal Card (Google Chat / Slack)

If a new runtime-recoverable anomaly is detected (e.g. `CrashLoopBackOff`, Secret key drift, deadlocked pod, or stale webhook), **NEVER execute terminal mutations autonomously**.

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

### Step 5: Declarative GitOps Issue Escalation

When an issue requires code changes, new Docker images (`ImagePullBackOff`), or Terraform quota expansions (or if verification fails):

1. **Target Repository Resolution:** Dynamically extract the GitOps repository from `/opt/data/SETTINGS.md`.
2. **Deterministic Issue Creation:**
   ```bash
   REPO_URL=$(grep "Git Repo:" /opt/data/SETTINGS.md | awk '{print $NF}' | sed -E 's|^https?://(www\.)?github\.com/||; s|\.git$||')

   gh issue create -R "${REPO_URL}" \
     --title "🚨 [Automated SRE Incident] ${COMPONENT} ${FAILURE_REASON}" \
     --body "### Incident Summary\n\n- **Component:** \`${COMPONENT}\`\n- **Forensic Logs:**\n\`\`\`text\n${ERROR_LOGS}\n\`\`\`" \
     --label "status:escalation-needed"
   ```

---

# Execution Guardrails & Circuit Breakers

### ⚡ Anti-Flapping Circuit Breakers
If a container is stuck in a chronic crash loop where previous rollbacks/restarts failed to stabilize the pod:
1. Pause posting interactive chat cards to prevent human alert fatigue.
2. Mark the incident state as `"FLAPPING_CIRCUIT_BREAKER_TRIPPED"` in `incidents.json`.
3. Escalate the chronic failure directly to the GitOps Repository as an infrastructure bug.

### 🛡️ Negative Safety Red Lines (What NEVER to Touch)
- **No Storage Mutations**: NEVER delete `PersistentVolumeClaims` (PVCs), `PersistentVolumes` (PVs), `StatefulSets`, or persistent volume storage.
- **Autonomous Exclusion Boundaries**: All mutations are strictly restricted to `kubeagents-system`, `agent-system`, and `kube-agents-operator-system`. NEVER modify or restart resources in `kube-system`, `gmp-system`, or customer tenant application namespaces. NEVER run `kubectl delete namespace`.
