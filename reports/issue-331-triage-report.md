# Executive Triage Report & Architectural Analysis

## Issue #331: LP-004: Operator ClusterRole grants unrestricted bind verb on clusterroles (cluster-admin escalation)
## Issue #338: Design: per-agent vs shared `kubeagents:explorer` ClusterRole (+ optional explicit bind whitelist)

### 1. Issue Overview and Context
- **Issue #331 Problem:** The operator controller ClusterRole historically granted the `bind` verb on all `clusterroles` and `clusterrolebindings` without any `resourceNames` scoping. This creates a full cluster privilege escalation vector if the operator is compromised.
- **Issue #334 Resolution:** The related issue ARCH-005 (#334) was resolved via PR #98 by explicitly restricting the operator's `bind` verb to `resourceNames: ["view"]`.
- **The Core Dilemma (#331 / #338):** The operator also needs to bind its own dynamically generated explorer ClusterRole (`kubeagents:explorer:<namespace>:<name>`). Since this role name is dynamic, it cannot be added to a static `resourceNames` whitelist.
- **SRE Ruling Request:** Resolve the design questions in Issue #338 (per-agent vs shared static explorer role name) to unblock the complete scoping of the `bind` verb for Issue #331.

---

### 2. Systematic Forensic Analysis and Architectural Evaluation

To resolve the security escalation vector while preserving core functionality, we analyzed the active Go controllers, RBAC definitions, and the Kubernetes permission binding rules:

#### Option A: Keep Per-Agent Dynamic Roles (Current Model)
- **Mechanics:** The operator creates `kubeagents:explorer:<ns>:<name>` dynamically per agent and deletes it on teardown.
- **Security Scoping Block:** We cannot scope the operator's `bind` verb with `resourceNames` for dynamic names, meaning the operator must keep unrestricted `bind` across all ClusterRoles, which fails least-privilege auditing (LP-004).
- **Implicit Bind Exception:** Under Kubernetes RBAC rules, a subject may bind a role whose permissions it already holds. Since the operator's own ClusterRole already fully covers all explorer permissions (read-only node/pod/namespace/CRD access), the operator can bind `kubeagents:explorer` without having explicit `bind` permissions on it. However, if explorer permissions ever expand beyond the operator's own permissions, this implicit path breaks.

#### Option B: Transition to Shared Static Singleton Role (`kubeagents:explorer`)
- **Mechanics:** Use a single, predictable name `kubeagents:explorer` across all agents.
- **Security Scoping Benefit:** Allows us to explicitly whitelist `kubeagents:explorer` alongside `view` in the operator's `bind` permission block:
  ```yaml
  resourceNames:
    - view
    - kubeagents:explorer
  ```
- **Trade-off:** Changes multi-tenancy and cleanup semantics, as the ClusterRole persists even when individual agents are deleted, and all agents share a single permission surface.

---

### 3. SRE Recommendations & Next Steps

We recommend the SRE Team review and approve the following resolution path:

1. **Approve Option B:** Transition the operator to use a static, shared `kubeagents:explorer` ClusterRole. This represents a minor semantic change but enables a highly secure, explicit whitelist defense-in-depth model.
2. **Implement Split RBAC:** Once Option B is approved, the operator's ClusterRole binding permissions should be split to explicitly scope `bind` verbs:
  ```yaml
  - apiGroups:
      - rbac.authorization.k8s.io
    resources:
      - clusterroles
    resourceNames:
      - view
      - kubeagents:explorer
    verbs:
      - bind
  ```

---

### 4. Transition State Determination
Because this security resolution is directly coupled to the high-level design decision in Issue #338, it cannot be implemented autonomously without an SRE architectural ruling.
- **Transition Target:** `status:escalation-needed` is selected to escalate this triage report for human review and final decision.

**Grounding Source:**
- **Codebase Reference:** `k8s-operator/internal/controller/platformagent_manifests.go:718`
- **RBAC Configuration:** `k8s-operator/config/rbac/role.yaml`
- **Upstream Issues:** gke-labs/kube-agents #331 and #338
