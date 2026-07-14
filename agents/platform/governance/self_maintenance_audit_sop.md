# SOP: Self-Maintenance & Skill Audit (Daily Governance)

**Purpose:** Performs daily self-maintenance checks across GKE cluster node pools (auto-repair/auto-upgrade), inspects certificate and secret lifecycles, and audits active agent `SKILL.md` definitions across the fleet to detect configuration drift and instructional degradation.

---

## Execution Checklist

### 1. Auditing Target Fleet

- Retrieve the active GKE clusters list and repository paths directly using native GKE monitoring and read-only tools.

### 2. GKE Self-Maintenance Rules

For each active cluster, execute these auditing checks directly using native GKE monitoring and read-only tools (`gke-self-maintenance` skill):

1.  **Node Pool Automated Management Audits:**
    - Query: `"gcloud container node-pools list --cluster=<cluster_name> --region=<cluster_location> --format='json(name,management)'"`
    - 🚨 **Maintenance Violation:** Any node pool running with `management.autoRepair: false` or `management.autoUpgrade: false` is flagged immediately as a reliability and security vulnerability.
2.  **Certificate & Secret Expiration Audits:**
    - Query: `"kubectl get secrets -A --field-selector type=kubernetes.io/tls -o json"`
    - 🚨 **Lifecycle Warning:** Any TLS secret or `cert-manager` certificate expiring within the next 30 days is logged for immediate rotation verification.
3.  **Node Resource Pressure Audits:**
    - Query: `"kubectl get nodes -o json"`
    - 🚨 **Pressure Condition:** Any node exhibiting active `DiskPressure`, `MemoryPressure`, or `PIDPressure` (`status=="True"`) is flagged for container rootfs / ephemeral storage cleanup (`limits.ephemeral-storage`).

### 3. Agent Skill Quality & Precision Audit

Inspect the local `kube-agents` skill repository (`agents/platform/skills/`) to verify instruction integrity (`skill-maintenance` skill):

1.  **Frontmatter & Trigger Precision Audits:**
    - Verify that every `SKILL.md` file possesses required `name` and `description` YAML frontmatter keys without semantic overlap or conversational filler.
2.  **Command & Script Path Verification:**
    - Inspect embedded `kubectl`, `gcloud`, and helper script references (e.g., `./skills/submit-suggestion/scripts/submit_suggestion.py`) to confirm target scripts exist and syntax is valid.
3.  **GitOps & Least Privilege Verification:**
    - Flag any skill instructions attempting direct live mutations (`kubectl apply`, `kubectl edit`) instead of delegating changes to the secure Pull Request workflow (`submit-suggestion`).

### 4. GitOps Remediation & Reporting

If maintenance violations, expiring certificates, or skill instruction drift are identified:

1.  **Synthesize YAML & Skill Patches:** Dynamically generate the recommended K8s YAML patches (enabling `autoRepair`/`autoUpgrade`) or optimized `SKILL.md` corrections.
2.  **Submit GitOps Pull Request:** Invoke the **`submit-suggestion`** skill to open a clean Pull Request containing the proposed self-maintenance fixes for human review.
3.  **Daily Maintenance Report:** Document all checked clusters, verified skills, and generated pull requests in the daily Self-Maintenance Governance Report.
