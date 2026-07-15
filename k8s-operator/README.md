# Kubernetes Agentic Harness Operator

This directory contains the Kubernetes Operator for the `kube-agents` harness. The operator defines and manages the lifecycle of agent custom resources:

- **PlatformAgent**: Manages platform-level configuration and capabilities.

The operator is built using the Kubebuilder framework and is written in Go.

---

## Prerequisites

Before building or deploying the operator, ensure you have the following installed:

- [Go](https://go.dev/doc/install) (version 1.24+)
- [Docker](https://docs.docker.com/get-docker/) or Podman (for building container images)
- [kubectl](https://kubernetes.io/docs/tasks/tools/) (configured to access your Kubernetes/GKE cluster)
- Access to a running Kubernetes/GKE cluster
- [gcloud](https://cloud.google.com/sdk/docs/install) (for GKE cluster access)

---

## Deployment & Operations

For instructions on bootstrapping GCP/GKE infrastructure, capacity planning, cluster sizing, API key configuration, and deploying integrations (LiteLLM, GitHub), please refer to the [Kube-Agents Deployment & Operations Guide](../docs/deployment.md).

---

## Local Development (Fast Iteration)

For local development and testing, you can run the operator controller as a local Go process on your machine, while pointing it to a remote GKE or local Kubernetes cluster. This bypasses the need to build and push container images on every code change.

### Step 1: Set Active Kubernetes Context

Ensure your `kubectl` is pointed to the correct cluster:

```bash
# Check the active context
kubectl config current-context

# If needed, authenticate and switch to your GKE cluster
gcloud container clusters get-credentials <CLUSTER_NAME> --zone <ZONE> --project <PROJECT_ID>
```

### Step 2: Install the Custom Resource Definitions (CRDs)

Register the operator's Custom Resource Definitions (CRDs) with the cluster:

```bash
make install
```

> [!NOTE]
> This command uses `controller-gen` to generate the CRD manifests from Go structs and applies them to the cluster via `kustomize`.

### Step 3: Run the Operator Locally

Run the operator controller as a local process:

```bash
make run
```

---

## Building and Deploying to GKE

If you need to build the operator manager container image and deploy the controller onto GKE:

### Step 1: Build and Push the Container Image

Build the container image and push it to your registry (e.g., Google Artifact Registry):

```bash
# 1. Define the image tag (using Artifact Registry format):
export IMG=us-central1-docker.pkg.dev/your-gcp-project-id/kubeagents-registry/kubeagents-operator:v1.0.0

# 2. Build and push image:
make docker-build docker-push
```

### Step 2: Deploy the Controller Manager

Deploy the operator to GKE using the built image:

```bash
make deploy
```

Verify that the operator pod is running:

```bash
kubectl get pods -n kubeagents-system
```

---

## Makefile Reference

The [Makefile](Makefile) provides several targets to automate development workflows:

| Target              | Description                                                                      |
| :------------------ | :------------------------------------------------------------------------------- |
| `make manifests`    | Generate WebhookConfiguration, ClusterRole and CustomResourceDefinition objects. |
| `make generate`     | Generate code containing DeepCopy, DeepCopyInto, and DeepCopyTab methods.        |
| `make fmt`          | Run `go fmt` against code.                                                       |
| `make vet`          | Run `go vet` against code.                                                       |
| `make test`         | Run tests.                                                                       |
| `make install`      | Install CRDs into the K8s cluster.                                               |
| `make uninstall`    | Uninstall CRDs from the K8s cluster.                                             |
| `make deploy`       | Deploy controller to the K8s cluster.                                            |
| `make undeploy`     | Undeploy controller from the K8s cluster.                                        |
| `make docker-build` | Build docker image with the manager.                                             |
| `make docker-push`  | Push docker image with the manager.                                              |
