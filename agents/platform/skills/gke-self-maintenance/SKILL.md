---
name: gke-self-maintenance
description: Proactive self-maintenance workflows for monitoring GKE cluster health, detecting configuration drift, inspecting auto-repair/auto-upgrade states, and verifying certificate and secret lifecycle health.
---

# GKE Self-Maintenance Skill

Use this skill to perform proactive self-maintenance, continuous health checks, and lifecycle auditing across the GKE fleet and cluster resources before failures occur. This skill enforces read-only observation and drift detection, directing all corrective actions through the secure GitOps Pull Request workflow.

## 🔍 Proactive Maintenance Workflow

### Step 1: Audit Cluster & Node Pool Auto-Repair and Auto-Upgrade States

Verify that GKE node pools across the cluster have automated management features enabled (`autoRepair` and `autoUpgrade`) to ensure self-healing and continuous patching against node degradation and CVEs.

**Commands:**

```bash
# 1. Inspect cluster-level and node-pool-level automated management settings
gcloud container node-pools list --cluster=<cluster_name> --region=<cluster_location> --format="table(name,config.machineType,management.autoRepair,management.autoUpgrade,status)"

# 2. Check for recent node pool repair or upgrade operations
gcloud container operations list --cluster=<cluster_name> --region=<cluster_location> --filter="TYPE : (REPAIR_CLUSTER UPGRADE_NODES)" --sort-by="~START_TIME" --limit=10
```

#### Diagnostic Checks:

- **`autoRepair: False` or `autoUpgrade: False`:** Flag as a reliability and security compliance violation. Propose a declarative manifest or Config Connector/Terraform update setting `management.autoRepair: true` and `management.autoUpgrade: true`.
- **Stuck or Degraded Node Pools:** If `status` is `RECONCILING` or `ERROR` for >30 minutes, inspect node pool condition events and inspect underlying GCE instance group errors.

---

### Step 2: Configuration & GitOps Drift Detection

Compare active running cluster configurations against the authoritative GitOps repository baseline defined in `/opt/data/SETTINGS.md`.

**Commands:**

```bash
# 1. Check for untracked or manual resource mutations not managed by GitOps labels
kubectl get deployments,statefulsets,daemonsets -A -l '!app.kubernetes.io/managed-by, !argocd.argoproj.io/instance' --no-headers

# 2. Audit RBAC ClusterRoleBindings for unauthorized or ad-hoc admin privileges
kubectl get clusterrolebindings -o json | jq -r '.items[] | select(.roleRef.name=="cluster-admin") | .metadata.name + " -> " + ([.subjects[]? | .kind + "/" + .name] | join(", "))'
```

#### Diagnostic Checks:

- **Orphaned / Non-Declarative Resources:** Any workload outside `kube-system` or `gke-managed-*` lacking GitOps management labels (`app.kubernetes.io/managed-by: argocd` or `fluxcd`) represents configuration drift. Flag and recommend importing into the GitOps baseline or decommissioning.
- **Unauthorized `cluster-admin` Bindings:** Flag any non-system user or service account bound directly to `cluster-admin`. Propose scoping permissions down to namespace-restricted `Roles` via PR.

---

### Step 3: Certificate & Secret Lifecycle Audit

Audit TLS secrets, webhook certificates, `cert-manager` readiness, and ServiceAccount token expiration across namespaces to prevent sudden outage due to expired credentials.

**Commands:**

```bash
# 1. Check cert-manager Certificate resources status and expiration times (if cert-manager is installed)
kubectl get certificates -A -o wide

# 2. Find TLS secrets with expiration dates within the next 30 days
kubectl get secrets -A --field-selector type=kubernetes.io/tls -o jsonpath='{range .items[*]}{.metadata.namespace}{"/"}{.metadata.name}{"\n"}{end}' | while read -r secret; do ns=${secret%/*}; name=${secret#*/}; echo -n "$ns/$name: "; kubectl get secret "$name" -n "$ns" -o jsonpath='{.data.tls\.crt}' | base64 -d | openssl x509 -enddate -noout; done

# 3. Audit Validating/Mutating Webhook Configurations for failing CA bundles or endpoints
kubectl get validatingwebhookconfigurations,mutatingwebhookconfigurations -o wide
```

#### Diagnostic Checks:

- **Expiring Certificates (<30 days):** Verify whether `cert-manager` renewal controllers are actively trying and failing to renew (check `kubectl describe certificate <name> -n <namespace>`). If manual renewal is required, generate a alert and PR patch.
- **Broken Webhook CA Bundles:** Ensure webhook services are healthy; an uncontactable validating webhook will block all API server mutations across the cluster.

---

### Step 4: Resource Headroom & Eviction Warning Monitoring

Identify nodes operating near capacity or exhibiting early eviction signals (`DiskPressure`, `MemoryPressure`, `PIDPressure`) before workloads are evicted.

**Commands:**

```bash
# 1. Check nodes for active pressure conditions
kubectl get nodes -o json | jq -r '.items[] | select(.status.conditions[] | select(.type in ("DiskPressure", "MemoryPressure", "PIDPressure") and .status=="True")) | .metadata.name + " (" + ([.status.conditions[] | select(.status=="True") | .type] | join(", ")) + ")"'

# 2. Audit ephemeral storage (`/var/lib/docker` or container rootfs) utilization across nodes
kubectl describe nodes | grep -E "Name:|DiskPressure|Resource|Memory|cpu" -A 4 | grep -E "Name:|Allocated resources:|cpu |memory |ephemeral-storage"
```

#### Diagnostic Checks:

- **`DiskPressure: True`:** Inspect node container logs and dead pod garbage collection. Propose configuring `kubelet` log rotation thresholds or increasing root volume sizes in the `ComputeClass` / node pool spec.
- **High Ephemeral Storage Allocation (>85%):** Check for workloads dumping local cache or unrotated logs to `emptyDir` or container root filesystems without size limits (`limits.ephemeral-storage`).

---

### Step 5: GitOps Remediation Boundary

Following the inviolable GitOps boundary of `kube-agents`:

1. **Never execute manual cluster mutations (`kubectl edit`, `kubectl delete`, `gcloud container node-pools update`) directly against live environments.**
2. Synthesize maintenance audit findings into a clear, evidence-backed SRE status report.
3. If structural changes (such as enabling `autoRepair`/`autoUpgrade`, tightening RBAC, or setting resource limits) are required, generate the corrected declarative YAML manifest or Config Connector resource.
4. Invoke the **`submit-suggestion`** skill (`./skills/submit-suggestion/scripts/submit_suggestion.py`) to create a clean Git branch and submit a GitHub Pull Request for human review and merge.
