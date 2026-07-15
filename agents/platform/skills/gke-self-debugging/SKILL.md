---
name: gke-self-debugging
description: Autonomous self-debugging and closed-loop diagnostic workflows for GKE workloads, node degradation, and cluster networking failures, formalizing multi-step RCA and automated self-repair recommendations.
---

# GKE Self-Debugging Skill

Use this skill to execute closed-loop, autonomous debugging across GKE workloads, operators, and cluster networking when failures (`CrashLoopBackOff`, `FailedScheduling`, `NodeNotReady`, `ServiceUnavailable`) occur. This skill formalizes structured triage and iterative investigation before escalating to human operators.

## 🔍 Closed-Loop Autonomous Debugging Workflow

### Step 1: Automated Symptom Triage & Scope Isolation

Immediately categorize the failure domain (Pod vs. Node vs. Network vs. Control Plane) to select the appropriate diagnostic branch without wasted queries.

**Commands:**

```bash
# 1. Fetch cluster credentials and verify API server responsiveness
gcloud container clusters get-credentials <cluster_name> --region <cluster_location>
kubectl get namespaces --request-timeout=5s

# 2. Inspect cluster-wide degraded nodes and failing pods across all namespaces
kubectl get nodes --field-selector=spec.unschedulable=false -o wide | grep -v "Ready"
kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded -o wide
```

#### Scope Isolation Decision Tree:

- **Node-Level Degradation (`NodeNotReady`, `NotReady` taints):** Transition to **Step 3A (Node/Kubelet Unresponsiveness)**.
- **Workload Boot Failure (`CrashLoopBackOff`, `Error`, `CreateContainerError`, `OOMKilled`):** Transition to **Step 3B (Pod Boot & Container Crashes)**.
- **Scheduling Rejections (`Pending`, `FailedScheduling`):** Transition to **Step 3C (Scheduling & Capacity Bottlenecks)**.
- **Service & Egress Timeouts (`ServiceUnavailable`, `504 Gateway Timeout`, DNS resolution drops):** Transition to **Step 3D (Network Policy & Gateway Diagnostics)**.

---

### Step 2: Autonomous Diagnostic Iteration Loop (Loop-Until-Done)

When executing an investigation or self-repair attempt, follow the **Autonomous Recovery & Loop-Until-Done** rules defined in `SOUL.md`:

1. Continue systematically checking root cause hypotheses until the exact failure mechanism is proven with concrete log/trace evidence.
2. Treat intermediate permissions, missing secrets, or volume mount blockers as obstacles to clear rather than stopping points.
3. Proactively inspect platform-native recovery mechanisms (Config Connector reconciliation state, GKE Hub fleet membership, ArgoCD/Flux sync status) before asking for human intervention.
4. **Cap recovery attempts at 5 iterations or ~10 minutes of wall time per distinct blocker** to prevent infinite loops.

---

### Step 3: Granular Diagnostic Branches

#### Branch 3A: Node/Kubelet Unresponsiveness (`NodeNotReady`)

When nodes drop out of `Ready` state or exhibit container runtime issues (`PLEG is not healthy`, `systemd` errors):

**Commands:**

```bash
# 1. Check detailed node conditions and recent kubelet events
kubectl describe node <node_name> | grep -E "Conditions:|Ready|OutOfDisk|MemoryPressure|DiskPressure|NetworkUnavailable" -A 10

# 2. Query Cloud Logging for system-level node kernel or daemon failures within the past hour
gcloud logging read 'resource.type="k8s_node" AND resource.labels.node_name="<node_name>" AND (jsonPayload.message=~"PLEG" OR jsonPayload.message=~"kernel:" OR severity>=ERROR)' --limit=30 --format="json" --project=<project_id>
```

#### Branch 3B: Pod Boot & Container Crashes (`CrashLoopBackOff`, `OOMKilled`)

When application workloads repeatedly exit or fail during startup:

**Commands:**

```bash
# 1. Extract exact container exit code and termination reason
kubectl get pod <pod_name> -n <namespace> -o jsonpath='{.status.containerStatuses[*].lastState.terminated}'

# 2. Inspect previous container logs (crushed instance) for stack traces
kubectl logs <pod_name> -n <namespace> --all-containers -p --tail=150

# 3. Check for OOMKilled vs Application Bug
# Exit code 137 indicates OOMKilled by kernel; Exit code 1/255 indicates application logic crash.
```

#### Branch 3C: Scheduling & Capacity Bottlenecks (`FailedScheduling`)

When pods remain stuck in `Pending`:

**Commands:**

```bash
# 1. Extract exact FailedScheduling event string
kubectl get events -n <namespace> --field-selector reason=FailedScheduling --sort-by='.metadata.creationTimestamp'

# 2. If node resources are exhausted, execute the obtainability diagnostics or check Cluster Autoscaler logs
gcloud logging read 'resource.type="k8s_cluster" AND (jsonPayload.reason="ZONE_RESOURCE_POOL_EXHAUSTED" OR textPayload=~"Scale-up failed")' --limit=15 --format="json" --project=<project_id>
```

#### Branch 3D: Network Policy & Gateway Diagnostics

When pods boot successfully but fail to communicate across namespaces or reach external APIs:

**Commands:**

```bash
# 1. Check endpoints for the target Kubernetes Service
kubectl get endpoints <service_name> -n <target_namespace>

# 2. Audit active NetworkPolicies in source and destination namespaces
kubectl get networkpolicies -n <source_namespace> -o yaml
kubectl get networkpolicies -n <target_namespace> -o yaml

# 3. Check CoreDNS readiness and packet drop counters
kubectl get pods -n kube-system -l k8s-app=kube-dns -o wide
```

---

### Step 4: Self-Repair vs. GitOps Remediation

Once the root cause is established:

1. **Platform-Native Self-Repair:** If the failure is caused by a transient control-plane sync issue or a stuck management CR that can be reconciled safely via allowed platform operations (e.g., restarting a dead operator controller pod or triggering an ArgoCD hard sync), execute the self-repair step.
2. **GitOps Manifest Remediation:** If the fix requires modifying application CPU/Memory limits, adjusting `NodeSelector`/tolerations, updating `NetworkPolicy` rules, or fixing image tags, **never apply the change directly via `kubectl apply/edit`**.
3. Generate the corrected YAML manifest patch and invoke the **`submit-suggestion`** skill (`./skills/submit-suggestion/scripts/submit_suggestion.py`) to create a Git branch and Pull Request for human review.
4. Provide direct clickable Google Cloud Console links (Cloud Logging, Cloud Trace, Metrics Explorer) alongside the PR link for SRE verification.
