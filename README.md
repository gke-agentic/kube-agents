# kube-agents: The Kubernetes Agentic Harness

The k8s agentic harness fundamentally redefines the DevOps presentation layer by replacing traditional interfaces like kubectl, gcloud, and the Google Cloud console with intelligent, composable, autonomous agents powered by the **Dynamic Assembly Specification (DASP)**.

## Key Components

### 1. Unified `KubeAgent` CRD (`kubeagents.x-k8s.io/v1alpha1`)
All agent archetypes (`platform`, `operator`, `devteam`, or custom profiles) are governed by a single unified Custom Resource Definition (`KubeAgent`). Behavior, scope, and permissions are determined dynamically by attached modular building blocks:
- **Personas (`personas/`)**: Standardized identity archetypes (`standard-operator`, `paranoid-security-auditor`, `frugal-cost-optimizer`).
- **Skills (`skills/`)**: Universal composable tools and FastMCP servers.
- **Procedures (`procedures/`)**: Standard Operating Procedures (SOPs).
- **Schedules (`schedules/`)**: Recurring cron triggers.

### 2. Interactive CLI/TUI Studio (`kube-agent-cli`)
Use `kube-agent-cli studio` to interactively assemble, review, and deploy specialized agents directly to your Kubernetes clusters, or use `kube-agent-cli compile` for headless CI/CD transpilation across multi-harness runtimes (`hermes`, `antigravity`, `scion`, `cloud-agents-api`).

---

## Harness Integration & Setup

For full installation instructions, see [INSTALL.md](INSTALL.md).

### 1. Interactive Studio Assembly
```bash
cd cli && go run ./cmd/main.go studio
```

### 2. Declarative Kubernetes Manifest
```bash
kubectl apply -f examples/kubeagent.yaml
```

### 3. Recreate Full `agents/` Directory from Seed DASP Templates
```bash
make compile-agents
```

For more details on walkthroughs and demos, see the [DASP Migration Demos](docs/m2-dasp-migration-demos.md).

## Disclaimer

This is not an officially supported Google product.
This project is not eligible for the Google Open Source Software Vulnerability Rewards Program.
