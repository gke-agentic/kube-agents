# Platform Agent Pod Network Policy Isolation — kube-agents

> **STATUS — Implemented in `feature/networkpolicy`.** The Layer 4 `NetworkPolicy` reconciliation described below is implemented in [`k8s-operator/internal/controller/platformagent_manifests.go`](file:///usr/local/google/home/shalinibhatia/src/gke-agentic/kube-agents/k8s-operator/internal/controller/platformagent_manifests.go#L1800), verified in [`platformagent_controller_test.go`](file:///usr/local/google/home/shalinibhatia/src/gke-agentic/kube-agents/k8s-operator/internal/controller/platformagent_controller_test.go#L605), and documented in [`security-and-iam.md`](file:///usr/local/google/home/shalinibhatia/src/gke-agentic/kube-agents/docs/site/src/content/docs/reference/security-and-iam.md#L154).

**Status:** Implemented (`feature/networkpolicy`)  
**Authors:** Shalini Bhatia  
**Reviewers:**  
**Last Updated:** July 29, 2026  
**Scope:** Layer 4 Kubernetes network isolation and egress boundaries for Platform Agent pods.

---

## 1. Introduction

The objective of this design is to generate and apply restrictive Kubernetes `NetworkPolicy` objects for Platform Agent pods within the `gke-labs/kube-agents` repository. By explicitly restricting ingress to necessary control-plane and observability ports and limiting egress to required operational endpoints (Cluster DNS, GCP metadata, LiteLLM Gateway, Kubernetes API Server, and external HTTPS services), we resolve three critical security findings:

- **Egress Control:** The agent previously operated without restrictive outbound network access. A compromised agent could exfiltrate sensitive cluster data or download malicious payloads from arbitrary internal or external endpoints.
- **Lateral Movement:** Unrestricted ingress allowed unauthorized internal cluster services to communicate with the agent, opening a vector for lateral movement inside the cluster.
- **MCP Exposure:** The agent's AI-context scope must be strictly bounded over the network. It should only interact with authorized endpoints (such as the LiteLLM Gateway and Google Cloud APIs) to prevent context hijacking or unintended internal modifications.

To secure the agent and adhere to the Principle of Least Privilege, we implement a Zero-Trust network architecture around Platform Agent pods.

---

## 2. Background / Motivation

In modern Kubernetes environments, AI-driven agents possess significant capabilities and contexts, making them high-value targets. The lack of default-deny network controls leaves the cluster vulnerable to threats identified in Kubernetes security audits:

1. **Exfiltration & Payload Fetching:** Unrestricted outbound access enables data exfiltration to external sinks or lateral exploration of private internal networks.
2. **Untrusted Ingress:** Without ingress filtering, any pod within the cluster could attempt to communicate with agent control-plane ports (`8642`), metrics (`8643`), or dashboards (`9119`).
3. **Internal Scan Prevention:** While the agent requires access to external HTTPS APIs (e.g., Google Cloud, GitHub), it should not be permitted to scan RFC 1918 private subnets within the cluster or cloud VPC.

---

## 3. Design / Proposal

To address these vulnerabilities while maintaining compatibility across diverse cluster environments (e.g., GKE, standard Kubernetes, Minikube), we implement a standard Layer 4 `NetworkPolicy` generated directly by the Go operator.

### 3.1 Operator Manifest Builder (`platformagent_manifests.go`)

We implement a policy builder function (`buildNetworkPolicy` in [`k8s-operator/internal/controller/platformagent_manifests.go`](file:///usr/local/google/home/shalinibhatia/src/gke-agentic/kube-agents/k8s-operator/internal/controller/platformagent_manifests.go#L1800)) that dynamically generates a standard Kubernetes `NetworkPolicy` with the following refined constraints:

#### Ingress Rules
- **Rule 1 — Internal Agent Traffic & Dashboard:** TCP ports `8642` (API Server), `8643` (Metrics), and `9119` (Dashboard), strictly restricted to connections originating from pods within the `kubeagents-system` namespace.
- **Rule 2 — Prometheus Metrics Scraping:** TCP port `8643` restricted to connections from the Google Managed Prometheus namespace (`gke-gmp-system`).

#### Egress Rules (6-Rule Policy)
1. **Rule 1 — Cluster DNS:** UDP and TCP port `53` targeting `kube-system` pods matching `k8s-app: kube-dns` and `k8s-app: node-local-dns`, plus the NodeLocal DNSCache IP block (`169.254.20.10/32`).
2. **Rule 2 — GCP Workload Identity / Metadata Server:** TCP port `80` targeting `169.254.169.254/32` for GKE Workload Identity token exchange.
3. **Rule 3 — LiteLLM Gateway:** TCP ports `4000`, `80`, and `443` targeting pods matching label `app: litellm` in the `kubeagents-system` namespace.
4. **Rule 4 — Kubernetes Control Plane API Server:** TCP ports `443` and `6443` targeting `10.96.0.1/32` (standard K8s `kubernetes.default.svc` ClusterIP; overridden for GKE Private Clusters via Kustomize patch).
5. **Rule 5 — External HTTPS (Lateral Movement Protection):** TCP port `443` targeting `0.0.0.0/0` with explicit exclusions (`except`) for RFC 1918 private subnets (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`). This blocks internal lateral scanning while permitting outbound HTTPS requests to required cloud APIs (Google Cloud, GitHub, etc.).
6. **Rule 6 — GKE Managed OpenTelemetry Collector (Trace Export):** TCP ports `4317` (OTLP gRPC) and `4318` (OTLP HTTP) targeting pods in the `gke-managed-otel` namespace for distributed trace export.

---

### 3.2 Controller Logic & Generated Specification (`platformagent_controller.go`)

The `PlatformAgentReconciler` calls `buildNetworkPolicy` when reconciling `PlatformAgent` Custom Resources. The policy is linked to the agent's lifecycle via `ctrl.SetControllerReference`, guaranteeing automatic garbage collection upon CR deletion.

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: platform-agent-gateway-netpol
  namespace: kubeagents-system
  labels:
    app: platform-agent-gateway
spec:
  # Note: Kustomize targets static 'app: platform-agent' from service.yaml.
  # The Go Operator dynamically targets 'app: <agent.Name>-gateway' when reconciling PlatformAgent CRs.
  podSelector:
    matchLabels:
      app: platform-agent-gateway
  policyTypes:
    - Ingress
    - Egress
  ingress:
    # 1. Internal agent traffic and dashboard access from within kubeagents-system
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kubeagents-system
      ports:
        - port: 8642
          protocol: TCP
        - port: 8643
          protocol: TCP
        - port: 9119
          protocol: TCP
    # 2. Prometheus metrics scraping from Google Managed Prometheus (gke-gmp-system)
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: gke-gmp-system
      ports:
        - port: 8643
          protocol: TCP
  egress:
    # 1. Cluster DNS (CoreDNS, NodeLocal DNSCache pods in kube-system, and DNSCache IP)
    - ports:
        - port: 53
          protocol: UDP
        - port: 53
          protocol: TCP
      to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
          podSelector:
            matchLabels:
              k8s-app: kube-dns
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
          podSelector:
            matchLabels:
              k8s-app: node-local-dns
        - ipBlock:
            cidr: 169.254.20.10/32
    # 2. GCP Workload Identity / Metadata Server
    - ports:
        - port: 80
          protocol: TCP
      to:
        - ipBlock:
            cidr: 169.254.169.254/32
    # 3. LiteLLM Gateway in kubeagents-system
    - ports:
        - port: 4000
          protocol: TCP
        - port: 80
          protocol: TCP
        - port: 443
          protocol: TCP
      to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kubeagents-system
          podSelector:
            matchLabels:
              app: litellm
    # 4. Kubernetes API Server (Internal Control Plane)
    - ports:
        - port: 443
          protocol: TCP
        - port: 6443
          protocol: TCP
      to:
        - ipBlock:
            cidr: 10.96.0.1/32
    # 5. External HTTPS (Google APIs, GitHub, etc.)
    # Allows external public HTTPS while strictly BLOCKING internal lateral movement.
    - ports:
        - port: 443
          protocol: TCP
      to:
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 10.0.0.0/8
              - 172.16.0.0/12
              - 192.168.0.0/16
    # 6. GKE Managed OpenTelemetry Collector (Trace Export)
    - ports:
        - port: 4317
          protocol: TCP
        - port: 4318
          protocol: TCP
      to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: gke-managed-otel
```

---



## 4. Why Generate Directly via the Go Operator?

In static applications, firewall rules are placed in static YAML manifests. However, `kube-agents` is highly dynamic:

1. **Dynamic Lifecycle:** When a cluster administrator applies a `PlatformAgent` Custom Resource, the operator dynamically provisions pods, services, and sidecars on the fly.
2. **Preventing Security Gaps:** Relying solely on static installation manifests would leave dynamically created agent pods unprotected.
3. **Zero-Touch Security:** Writing the policy builder into the Go operator ([`platformagent_manifests.go`](file:///usr/local/google/home/shalinibhatia/src/gke-agentic/kube-agents/k8s-operator/internal/controller/platformagent_manifests.go#L1800)) guarantees that the instant an agent pod is created, a customized, tightly scoped firewall is provisioned alongside it.

---

## 5. Alternatives Considered

### 5.1 CiliumNetworkPolicy (L7 / FQDN Filtering)
- **Description:** Utilizing Cilium CRDs to restrict egress traffic by Fully Qualified Domain Name (e.g., `*.googleapis.com`, `github.com`).
- **Pros:** True zero-trust egress; immune to IP rotation by cloud providers.
- **Cons:** Requires Cilium CNI; importing Cilium Go modules adds significant operator bloat; incompatible with basic development environments (Minikube/kind).
- **Verdict:** **Rejected for default installation.** The standard Layer 4 policy ensures universal portability. Users running Cilium can deploy supplementary `CiliumNetworkPolicy` or `CiliumClusterwideNetworkPolicy` resources natively.

### 5.2 Auto-Detecting Configurable Operator Pattern
- **Description:** The operator uses Kubernetes Discovery to check for `cilium.io/v2` CRDs and dynamically switches between Layer 4 and Cilium L7 policies based on a CRD `flavor` field.
- **Pros:** Full L7 security where supported without breaking local dev clusters.
- **Cons:** Increases operator binary size, requires broader ClusterRole RBAC permissions, and increases maintenance burden across multiple policy paths.
- **Verdict:** **Rejected.** Decoupling third-party CNI policy management from the core operator preserves least-privilege RBAC and codebase simplicity.

### 5.3 Service Mesh / Gateway API (Envoy/Istio `AuthorizationPolicy`)
- **Description:** Offloading network security to a dedicated service mesh to enforce deep HTTP inspection.
- **Pros:** Granular L7 method/path authorization (e.g., restricting access to specific `/v1/chat/completions` routes).
- **Cons:** Enforces a heavy end-user dependency on a specific service mesh.
- **Verdict:** **Rejected.** Out of scope for general adoption.

---

## 6. Resolved Open Questions

### 6.1 GCP Metadata Enforcement
> *Is TCP port 80 to `169.254.169.254/32` sufficient for all Workload Identity Federation flows used by the agent, or are additional internal GCP endpoints required during startup?*

**Resolution:** TCP port `80` to `169.254.169.254/32` is sufficient for GKE Workload Identity Federation token exchange within the pod. Once an identity token is acquired, subsequent requests to Google Cloud services (such as Vertex AI, Secret Manager, or Cloud Logging) are routed over HTTPS (TCP port `443`) to `*.googleapis.com`, which is permitted by Egress Rule 5 (`0.0.0.0/0 except RFC 1918`).

### 6.2 Private Google Access / VPC Service Controls
> *To further mitigate data exfiltration risks inherent in the Layer 4 policy, can we configure the cluster's VPC to route traffic to Google APIs through restricted VIPs and whitelist those specific static IP blocks instead of allowing `0.0.0.0/0`?*

**Resolution:** Yes. As documented in [`security-and-iam.md`](file:///usr/local/google/home/shalinibhatia/src/gke-agentic/kube-agents/docs/site/src/content/docs/reference/security-and-iam.md#L154), high-security deployments can configure Private Google Access via the **`private.googleapis.com`** VIP block (`199.36.153.8/30`) or **`restricted.googleapis.com`** VIP block (`199.36.153.4/30`). Administrators can replace the `0.0.0.0/0` block in Egress Rule 5 with those specific CIDR ranges or combine this baseline Layer 4 policy with an FQDN-aware egress solution (such as Cilium or Istio Egress Gateways).
