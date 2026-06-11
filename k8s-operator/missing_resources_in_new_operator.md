# GCP Resources Excluded from the New Operator

With the migration to the cloud-agnostic `PlatformAgent` operator, the controller no longer directly provisions or manages Google Cloud (GCP) resources. These resources must now be pre-provisioned externally (e.g., via Terraform, Crossplane, or manual scripts) before applying the Custom Resource.

---

## 1. Excluded Resources List

The following GCP resources were previously created and configured by the old operator but are **not** managed by the new operator:

### 1.1 Google Service Account (GSA)

- **Resource**: `iam.googleapis.com/ServiceAccount`
- **Default Name**: `platform-agent-bot` (derived from `spec.security.workloadIdentity.gcp.gsaName`)
- **Purpose**: Provides the identity for the GChat bot to interact with Google APIs (e.g., Vertex AI/Gemini, Pub/Sub).

### 1.2 Pub/Sub Topic

- **Resource**: `pubsub.googleapis.com/Topic`
- **Default Name**: `platform-agent-chat-events` (derived from `spec.integration.googleChat.topicName`)
- **Purpose**: Receives asynchronous message events pushed from the Google Chat API.

### 1.3 Pub/Sub Subscription

- **Resource**: `pubsub.googleapis.com/Subscription`
- **Default Name**: `platform-agent-chat-events-sub` (derived from `spec.integration.googleChat.subscriptionName`)
- **Purpose**: Allows the Platform Agent gateway to pull message events from the Pub/Sub topic.

### 1.4 IAM Policy Bindings (Project & Resource Level)

The old operator dynamically assigned several IAM roles to configure permissions. These must now be mapped externally:

1.  **GSA Pub/Sub Permissions**:
    - **Roles**: `roles/pubsub.subscriber`, `roles/pubsub.viewer`
    - **Resource**: The GChat Pub/Sub subscription.
    - **Member**: `serviceAccount:platform-agent-bot@<PROJECT_ID>.iam.gserviceaccount.com`
2.  **GSA AI Platform Permissions**:
    - **Role**: `roles/aiplatform.user`
    - **Resource**: GCP Project
    - **Member**: `serviceAccount:platform-agent-bot@<PROJECT_ID>.iam.gserviceaccount.com` (required to call Gemini API if using Vertex AI backend).
3.  **GSA Cluster Viewer Permissions**:
    - **Role**: `roles/container.clusterViewer`
    - **Resource**: GCP Project
    - **Member**: `serviceAccount:platform-agent-bot@<PROJECT_ID>.iam.gserviceaccount.com` (allows agent to discover GKE cluster resources).
4.  **Google Chat API Publisher Permissions**:
    - **Role**: `roles/pubsub.publisher`
    - **Resource**: GChat Pub/Sub Topic
    - **Member**: `serviceAccount:chat-api-push@system.gserviceaccount.com` (allows GChat API to publish chat events to the topic).
5.  **Google Workspace Add-ons Publisher Permissions**:
    - **Role**: `roles/pubsub.publisher`
    - **Resource**: GChat Pub/Sub Topic
    - **Member**: `serviceAccount:service-<PROJECT_NUMBER>@gcp-sa-gsuiteaddons.iam.gserviceaccount.com` (allows Google Workspace Add-ons to publish events).
6.  **Workload Identity User Binding**:
    - **Role**: `roles/iam.workloadIdentityUser`
    - **Resource**: GSA `platform-agent-bot`
    - **Member**: `serviceAccount:<PROJECT_ID>.svc.id.goog[<NAMESPACE>/<KSA_NAME>]` (allows the Kubernetes Service Account used by the pod to impersonate the GSA).

---

## 2. Rationale: Why Are These Resources Removed?

1.  **100% Cloud Agnostic Reconciler**:
    By removing direct GCP API calls and GKE Config Connector (KCC) dependencies, the new operator can run on any Kubernetes cluster (GKE, EKS, AKS, local KinD, etc.) without changes.
2.  **Simplified Operator Permissions (Least Privilege)**:
    The operator itself no longer requires cluster-wide admin or project-level GCP IAM permissions (like `roles/iam.serviceAccountAdmin` or `roles/resourcemanager.projectIamAdmin`) to manipulate cloud resources. It only needs RBAC permissions to manage standard Kubernetes workloads (`Deployment`, `ConfigMap`, `PVC`, `ServiceAccount`, `ClusterRoleBinding`).
3.  **Infrastructure as Code (IaC) Alignment**:
    Provisioning cloud infrastructure (Pub/Sub topics, GSAs, IAM bindings) is best handled by dedicated tools like Terraform, Pulumi, or Crossplane. The operator focuses purely on reconciling the container runtime and Kubernetes configurations.
4.  **Testability**:
    The code is much easier to unit test because we do not need to mock GCP SDK clients or rely on real GCP API responses.
