# Test Verification Plan - Platform Agent Operator

This plan outlines how to verify the new cloud-agnostic operator on a real GKE/GCP environment, comparing its behavior with the old operator.

---

## Part 1: Provision the GCP/GKE Infrastructure

The easiest way to bootstrap the GCP resources (Pub/Sub, Workload Identity, Secrets, GKE cluster) is to run the existing provisioning script.

1. Run the old provisioning script. This will set up the entire cluster, Secret Manager, GSA, KSA, and also deploy the old operator and resource:

   ```bash
   ./integrations/gchat/crd/provision.sh
   ```

   > [!NOTE]
   > Make note of the values configured in `integrations/gchat/crd/vars.sh` as they will be needed to build the new Custom Resource.

2. Verify the old deployment is running and functional by sending a test message to the GChat bot as described in the script's final instructions.

---

## Part 2: Clean Up the Old Operator & Custom Resource (Without Destroying Cloud Infra)

To prevent the old operator from deleting GCP service accounts, Pub/Sub topics, and IAM bindings when deleting the Custom Resource, we must scale it down first and clear its finalizers.

1. Scale down the old operator so it cannot react to resource changes or run deletion hooks:

   ```bash
   kubectl scale deployment --all -n platform-agent-operator-system --replicas=0
   ```

2. Patch the old `PlatformAgent` Custom Resource to clear its finalizers, allowing Kubernetes to drop the resource immediately from etcd without invoking cleanup actions:

   ```bash
   kubectl patch platformagent platform-agent -n agent-system -p '{"metadata":{"finalizers":[]}}' --type=merge
   ```

3. Delete the Custom Resource (which should disappear instantly):

   ```bash
   kubectl delete platformagent platform-agent -n agent-system
   ```

4. Uninstall the old operator controller and delete its namespace:
   ```bash
   cd integrations/gchat/crd/platform-agent-operator
   make undeploy
   kubectl delete namespace platform-agent-operator-system
   ```

---

## Part 3: Deploy the New Operator

We will register the new CRDs and run the new controller. For faster testing/debugging, we can run the new controller manager locally from your terminal while targeting the GKE cluster context.

1. Register the new CRD on the GKE cluster:

   ```bash
   cd k8s-operator
   make install
   ```

2. Run the new controller manager locally:
   ```bash
   make run
   ```
   Leave this terminal window open to monitor the manager logs.

---

## Part 4: Apply the New PlatformAgent Custom Resource

Create a new Custom Resource manifest mapping the parameters from `integrations/gchat/crd/vars.sh` to the new cloud-agnostic spec schema.

1. Create a file named `platform-agent-new.yaml` in the workspace:

   ```yaml
   apiVersion: kubeagents.x-k8s.io/v1alpha1
   kind: PlatformAgent
   metadata:
     name: platform-agent
     namespace: agent-system # Use namespace from vars.sh
   spec:
     harness:
       clusterName: "platform-agent-host" # Use CLUSTER_NAME from vars.sh
       location: "us-central1" # Use REGION from vars.sh
       hermes:
         dashboardEnabled: true
         pluginsDebug: false
         platformAgentHome: "/opt/data"
         apiServerSecretRef:
           name: platform-agent-secrets
           key: API_SERVER_KEY
     deployment:
       image: "us-central1-docker.pkg.dev/<YOUR_PROJECT_ID>/platform-agent-repo/platform-agent" # Use image from vars.sh
       tag: "latest"
       imagePullPolicy: Always
     security:
       serviceAccountName: "platform-agent-platform-sa" # Use KSA_NAME from vars.sh
       workloadIdentity:
         gcp:
           gsaName: "platform-agent-bot" # Use GSA_NAME from vars.sh
           projectId: "<YOUR_PROJECT_ID>" # Use PROJECT_ID from vars.sh
     model:
       provider: "gemini" # Use MODEL_PROVIDER from vars.sh
       default: "gemini-3.1-flash-lite" # Use MODEL_DEFAULT_NAME from vars.sh
       gemini:
         apiKeySecretRef:
           name: platform-agent-secrets
           key: GEMINI_API_KEY
     integration:
       googleChat:
         enabled: true
         projectId: "<YOUR_PROJECT_ID>"
         topicName: "platform-agent-chat-events" # Use CHAT_TOPIC_NAME from vars.sh
         subscriptionName: "platform-agent-chat-events-sub" # Use CHAT_SUB_NAME from vars.sh
         allowedUsers:
           - "<YOUR_EMAIL>" # Use ALLOWED_USER from vars.sh
         homeChannel: ""
   ```

2. In a separate terminal, apply the new Custom Resource:
   ```bash
   kubectl apply -f platform-agent-new.yaml
   ```

---

## Part 5: Verification Checklist

Verify that the new operator successfully reconciles all native Kubernetes resources to match the configuration of the old operator:

### 1. Reconciled Resources Verification

- [ ] **ServiceAccount**:

  ```bash
  kubectl get serviceaccount platform-agent-platform-sa -n agent-system -o yaml
  ```

  Ensure it contains the Workload Identity annotation:
  `iam.gke.io/gcp-service-account: platform-agent-bot@<YOUR_PROJECT_ID>.iam.gserviceaccount.com`

- [ ] **ConfigMap**:

  ```bash
  kubectl get configmap platform-agent-config -n agent-system -o yaml
  ```

  Ensure it contains `config.yaml` with the correct model and Google Chat settings.

- [ ] **PVC**:

  ```bash
  kubectl get pvc platform-agent-data -n agent-system
  ```

  Ensure it is bound.

- [ ] **Deployment**:

  ```bash
  kubectl get deployment platform-agent-gateway -n agent-system -o yaml
  ```

  Ensure:
  - It references the ServiceAccount `platform-agent-platform-sa`.
  - The Pod Template has the `kubeagents.x-k8s.io/config-hash` annotation.
  - The strategy type is `Recreate`.
  - Environment variables (GOOGLE*CHAT*\*, GEMINI_API_KEY, API_SERVER_KEY) are set correctly.

- [ ] **RBAC**:
  ```bash
  kubectl get clusterrolebinding kubeagents:viewer:agent-system:platform-agent -o yaml
  kubectl get clusterrolebinding kubeagents:explorer:agent-system:platform-agent -o yaml
  ```
  Ensure they bind the service account to the `view` and `kubeagents:explorer:agent-system:platform-agent` roles respectively.

### 2. Functional Verification

- [ ] Run `kubectl get platformagent platform-agent -n agent-system -o yaml` and check the status:
  - `status.phase` should be `Ready`.
  - `status.deploymentStatus.readyReplicas` should be `1`.
  - `status.storageStatus.bound` should be `true`.
- [ ] Check deployment logs:
  ```bash
  kubectl logs -l app=platform-agent-gateway -n agent-system -c platform-agent
  ```
  Ensure the agent starts successfully without authentication or connection errors.
- [ ] Send a Google Chat message to the bot to confirm it responds.

---

## Part 6: Teardown / Finalizer Verification

Verify that when deleting the new Custom Resource, the finalizers gracefully clean up the unowned cluster-scoped resources without hanging.

1. Delete the new Custom Resource:

   ```bash
   kubectl delete platformagent platform-agent -n agent-system
   ```

   Ensure the CR disappears cleanly and does not hang indefinitely.

2. Verify the cluster-scoped bindings and clusterroles were successfully cleaned up:
   ```bash
   kubectl get clusterrolebinding kubeagents:viewer:agent-system:platform-agent
   kubectl get clusterrolebinding kubeagents:explorer:agent-system:platform-agent
   kubectl get clusterrole kubeagents:explorer:agent-system:platform-agent
   ```
   All of the above should return `NotFound`.
