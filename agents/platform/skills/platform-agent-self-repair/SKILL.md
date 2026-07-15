---
name: platform-agent-self-repair
description: Formalizes the Worker Recovery Ladder and self-repair SOP for Platform Agent workers, remote runners, GitHub App token refresh, and identity/auth failures.
---

# Platform Agent Self-Repair Skill

Use this skill to execute autonomous self-repair and perform the mandatory 5-step Worker Recovery Ladder defined in `SOUL.md` whenever Platform Agent workers, provisioning tasks, remote runners, or `submit_suggestion` workflows fail due to authentication, IAM, bootstrap, or identity blockers.

## 🚨 Worker Recovery Ladder (Mandatory SOP)

If any remote runner, background job, or git operation fails with authentication errors (`fatal: Authentication failed`, `401 Unauthorized`), Kubernetes RBAC rejections, or Workload Identity token issues, you **MUST** execute this exact recovery ladder before escalating to the user.

**Rule:** Cap the ladder at **5 total iterations or ~10 minutes of wall time per distinct blocker** to prevent infinite loops while maximizing autonomous recovery.

---

### Step 1: Re-run & Raw Failure Trace Capture

Immediately re-run or re-query the failing worker or command to capture the exact, raw failure trace and identify the precise failure signature.

**Commands:**

```bash
# 1. Re-run or inspect the failed worker logs
kubectl logs -n kubeagents-system -l app.kubernetes.io/component=platform-agent --tail=100

# 2. If a git or PR submission operation failed, capture exact stderr output
git status || true
```

#### Signature Identification:

- **Git / GitHub App Token Expiry:** `fatal: Authentication failed for 'https://github.com/...'` or `could not read Username for 'https://github.com'`. Transition immediately to **Step 3 (Token Refresh)**.
- **Workload Identity / GCP IAM Rejection:** `googleapi: Error 403: Permission 'iam.serviceAccounts.getAccessToken' denied` or `metadata server uncontactable`. Transition to **Step 2 (Identity Context Inspection)**.
- **Kubernetes RBAC Rejection:** `User "system:serviceaccount:..." cannot create resource "..." in API group`. Transition to **Step 4 (Controller & Identity Self-Healing)**.

---

### Step 2: Inspect Identity Context & Workload Identity Bindings

Inspect the worker's active Kubernetes ServiceAccount annotations and verify the expected GCP IAM Workload Identity target.

**Commands:**

```bash
# 1. Inspect ServiceAccount annotations for active GKE Workload Identity bindings
kubectl get sa -n kubeagents-system platform-agent -o yaml | grep -E "annotations:|iam.gke.io/gcp-service-account" -A 2

# 2. Check GCP IAM policy bindings on the target GCP service account
gcloud iam service-accounts get-iam-policy <gcp_service_account_email> --format="table(bindings.role,bindings.members)"
```

#### Validation Checks:

- Confirm `iam.gke.io/gcp-service-account` points to the correct GCP Service Account email.
- Confirm `roles/iam.workloadIdentityUser` binding exists for `serviceAccount:<project_id>.svc.id.goog[kubeagents-system/platform-agent]`.

---

### Step 3: Dynamic GitHub App Token & Credential Recovery

If any git operation hits an authentication or permission error, dynamically refresh and cache the 1-hour GitHub App installation token using the pre-packaged script in your terminal tool.

**Commands:**

```bash
# 1. Outside a git repository (or specifying target explicitly):
./scripts/github_token_refresh.py <owner>/<repo>

# 2. Inside the active git repository workspace:
./scripts/github_token_refresh.py

# 3. Verify git credentials cache is refreshed and test connection
git remote -v
```

#### Post-Recovery Action:

Once the script successfully refreshes the token, immediately re-try the failing git push or `submit_suggestion.py` command.

---

### Step 4: Inspect Platform Controllers & Apply Safe Self-Repair

Check active resource controllers (`k8s-operator`, Config Connector, `minty` token broker) and GKE Hub fleet membership for self-healing paths before manual intervention.

**Commands:**

```bash
# 1. Inspect status of PlatformAgent custom resources
kubectl get platformagents -A -o yaml | grep -E "name:|phase:|conditions:" -A 6

# 2. Check health of the k8s-operator and minty token broker pods
kubectl get pods -n kubeagents-system -l 'app.kubernetes.io/name in (k8s-operator, minty)' -o wide

# 3. If a controller pod is wedged in CrashLoopBackOff or stale reconciliation, execute a safe restart (if permitted by control-plane role)
kubectl delete pod -n kubeagents-system -l app.kubernetes.io/name=k8s-operator
```

---

### Step 5: Re-run & Resume Work

After applying self-repair (refreshing tokens, verifying Workload Identity annotations, or restarting a stuck controller):

1. Re-run the original worker task or CLI command.
2. Confirm the command completes successfully without authentication or bootstrap errors.
3. Resume the original user request and report the autonomous recovery outcome cleanly in your status summary.

---

### Step 6: Escalation Protocol (Last Resort)

Escalate to the user **only** if:

- The 5-iteration or 10-minute wall-time budget is exhausted.
- A hard IAM boundary or required project-level external human approval (`IAM & Admin`) prevents self-repair.
- Provide a clean, human-readable SRE summary of the exact failure signature, what self-repair paths were tested, and what exact IAM role or PR needs human approval.
