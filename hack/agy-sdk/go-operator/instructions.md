# E2E Implementation Plan: Custom Go Operator for GKE (GCP)

This plan outlines how to build a custom Kubernetes Operator in Go using **Kubebuilder**. The operator will watch a Custom Resource (CR) named `MyOwnCronJob`, parse its custom `text` field, and map it into a standard native Kubernetes `CronJob` (`batch/v1`) with a custom command.

---

## 📋 High-Level Architecture Overview

1. **User Manifest (CR):** You apply a minimalist YAML (`kind: MyOwnCronJob`) containing only the dynamic payload (`text`).
2. **Go Operator (Controller):** Watches your CR, triggers the `Reconcile` loop, processes the text, and constructs a full-scale native `batch/v1` `CronJob` struct.
3. **Target Resource (GCP/GKE):** GKE runs the generated CronJob. You trigger it manually to view the output inside **GCP Cloud Logging**.

---

## 🛠️ Step-by-Step Execution Guide

### Step 1: Environment Prerequisites
Ensure your local machine has the following tools installed and configured:
* **Go** (v1.20 or higher)
* **Kubebuilder CLI** (The official framework for scaffolding Go operators)
* **gcloud CLI** (Authenticated to your GCP Project and targeted at your GKE cluster)

---

### Step 2: Initialize the Go Project
Create a fresh project directory and scaffold the Go operator backbone using Kubebuilder.

```bash
mkdir my-gcp-operator
cd my-gcp-operator

# Initialize the operator structure (Replace with your own domain/repository)
kubebuilder init --domain mycompany.com --repo mycompany.com/gcp-operator
```

---

### Step 3: Create the API and CRD Schema
Generate the Custom Resource Definition (CRD) configurations and the Go controller files.

```bash
kubebuilder create api --group batch --version v1 --kind MyOwnCronJob
```
*Note: When prompted to "Create Resource [y/n]" and "Create Controller [y/n]", press `y` for both.*

Modify the Go Struct to include your custom field:
Open `api/v1/myowncronjob_types.go` and add the `Text` field to the `MyOwnCronJobSpec` struct:

```go
// api/v1/myowncronjob_types.go
type MyOwnCronJobSpec struct {
    // Text represents the custom string that will be passed down to the CronJob logger container
    Text string `json:"text"`
}
```

Generate the final YAML manifests for Kubernetes based on your Go code updates:

```bash
make manifests
```

---

### Step 4: Implement Mapping Logic in Go (The Reconciler)
Open `internal/controller/myowncronjob_controller.go`. Modify the `Reconcile` function to fetch your Custom Resource and map it directly into a standard native `batch/v1` `CronJob`.

> [!IMPORTANT]
> Do not replace the entire file with the snippet below, as it only contains the imports and the `Reconcile` function. Keep the scaffolded `MyOwnCronJobReconciler` struct and `SetupWithManager` function.

```go
// internal/controller/myowncronjob_controller.go

package controller

import (
	"context"
	"k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"

	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	batchv1v1 "mycompany.com/gcp-operator/api/v1"
)

func (r *MyOwnCronJobReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
    log := log.FromContext(ctx)

    // 1. Fetch the Custom Resource (CR) instance from etcd
    var myCR batchv1v1.MyOwnCronJob
    if err := r.Get(ctx, req.NamespacedName, &myCR); err != nil {
        return ctrl.Result{}, client.IgnoreNotFound(err)
    }

    // 2. Define and map properties into the native batch/v1 CronJob struct
    cronJob := &batchv1.CronJob{
        ObjectMeta: metav1.ObjectMeta{
            Name:      myCR.Name + "-built-by-operator",
            Namespace: myCR.Namespace,
        },
        Spec: batchv1.CronJobSpec{
            Schedule: "0 0 1 1 *", // Runs once a year (effectively manual-only)
            JobTemplate: batchv1.JobTemplateSpec{
                Spec: batchv1.JobSpec{
                    Template: corev1.PodTemplateSpec{
                        Spec: corev1.PodSpec{
                            RestartPolicy: corev1.RestartPolicyNever,
                            Containers: []corev1.Container{
                                {
                                    Name:  "logger",
                                    Image: "busybox:latest",
                                    // DYNAMIC MAPPING: Injects custom text directly into the command payload
                                    Command: []string{"echo", "CronJob Trigger: " + myCR.Spec.Text},
                                },
                            },
                        },
                    },
                },
            },
        },
    }

    // 3. Set OwnerReference (Garbage collection: If CR is deleted, this CronJob terminates too)
    if err := ctrl.SetControllerReference(&myCR, cronJob, r.Scheme); err != nil {
        return ctrl.Result{}, err
    }

    // 4. Server-side Validation: Create or update the CronJob via Kubernetes API Client
    foundCronJob := &batchv1.CronJob{}
    err := r.Get(ctx, types.NamespacedName{Name: cronJob.Name, Namespace: cronJob.Namespace}, foundCronJob)
    if err != nil && errors.IsNotFound(err) {
        log.Info("Mapping CR to Native CronJob", "TextToLog", myCR.Spec.Text)
        err = r.Create(ctx, cronJob)
        if err != nil {
            return ctrl.Result{}, err // If invalid type or param, API server returns 404/422 and we retry
        }
    }

    return ctrl.Result{}, nil
}
```

---

### Step 5: Build and Run the Operator
Instead of baking docker images immediately, you can register the schema and run your local Go binary against your remote GKE cluster.

Install CRD schemas into your GKE cluster:

```bash
make install
```

Verify that the Custom Resource Definition (CRD) is successfully registered in your cluster:
```bash
kubectl get crds | grep myowncronjobs
# Output should display: myowncronjobs.batch.mycompany.com
```

Run the Go Operator locally on your machine (it uses your local kubeconfig to communicate with GCP GKE):

```bash
make run
```

---

### Step 6: End-to-End Validation (Testing)
While the operator loop is active, open a new terminal window and apply your source Custom Resource manifest.

#### 1. Apply the Custom Resource Manifest (`my-cr.yaml`)

```yaml
apiVersion: batch.mycompany.com/v1
kind: MyOwnCronJob
metadata:
  name: test-log-cronjob
spec:
  text: "Hello from Custom Go Operator inside GCP!"
```

```bash
kubectl apply -f my-cr.yaml
```

Verify that your Custom Resource (CR) instance was successfully created in the cluster:
```bash
kubectl get myowncronjobs
# Output should display: test-log-cronjob
```

#### 2. Confirm the Operator Mapped it to a Native CronJob

```bash
kubectl get cronjobs
# Output will display: test-log-cronjob-built-by-operator
```

#### 3. Trigger the CronJob Manually via CLI
Since the schedule runs only once a year, trigger a manual evaluation job instantly:

```bash
kubectl create job --from=cronjob/test-log-cronjob-built-by-operator manual-debug-run
```

#### 4. View logs in GKE & GCP Cloud Logging
Verify that the underlying pod executed the echo command with your custom injected text:

```bash
kubectl logs job/manual-debug-run
# Logs will output: CronJob Trigger: Hello from Custom Go Operator inside GCP!
```

Head over to the GCP Cloud Logging Console in your browser. Under the GKE container resource filters, you will see this exact stdout message indexed and tracked seamlessly.

---

## 🚀 Step 7: Deploy the Operator to GKE (Production Workload)

Once you have validated the operator locally, you should deploy it directly into your GKE cluster as a permanent workload (a Kubernetes `Deployment`) so it runs continuously without needing your local machine.

### Prerequisites
1. **Google Artifact Registry (GAR):** A Docker repository to store your operator's container image.
2. **IAM Permissions:** Ensure your gcloud user has permissions to push to GAR and deploy to GKE.

---

### Sub-Step 7.1: Create a Google Artifact Registry Repository
If you don't have a repository yet, create one using the `gcloud` CLI:

```bash
# Set your GCP variables
export GCP_PROJECT_ID="your-gcp-project-id"
export REGION="us-central1" # or your preferred region
export REPO_NAME="my-operators"

# Create a Docker repository in GAR
gcloud artifacts repositories create $REPO_NAME \
    --repository-format=docker \
    --location=$REGION \
    --description="Docker repository for custom operators"
```

Configure your local Docker CLI to authenticate with the registry:
```bash
gcloud auth configure-docker $REGION-docker.pkg.dev
```

---

### Sub-Step 7.2: Build and Push the Operator Image
Kubebuilder provides built-in Makefile targets to build and push the container image.

Define your target image tag:
```bash
export IMG="$REGION-docker.pkg.dev/$GCP_PROJECT_ID/$REPO_NAME/my-gcp-operator:v1.0.0"
```

Build the Docker image locally (ensure Docker is running on your machine):
```bash
make docker-build IMG=$IMG
```

Push the image to your Google Artifact Registry:
```bash
make docker-push IMG=$IMG
```

---

### Sub-Step 7.3: Deploy to the GKE Cluster
Now, deploy the operator resources (Deployment, RBAC roles, ServiceAccounts) to your GKE cluster. This uses `kustomize` under the hood to configure the deployment to use your newly pushed image.

```bash
make deploy IMG=$IMG
```

---

### Sub-Step 7.4: Verify the In-Cluster Deployment
Confirm that the operator is running successfully inside your GKE cluster.

Kubebuilder deploys the operator into a dedicated namespace named `<project-name>-system` (in this case, `my-gcp-operator-system`):

```bash
# List the pods in the operator namespace
kubectl get pods -n my-gcp-operator-system
```

You should see a pod named `my-gcp-operator-controller-manager-xxxxx` in the `Running` state.

View the in-cluster logs of the operator to ensure it started successfully:
```bash
kubectl logs -n my-gcp-operator-system deployment/my-gcp-operator-controller-manager -c manager
```

---

### Sub-Step 7.5: Test the In-Cluster Operator
To prove the operator is working independently of your local machine:
1. **Stop your local operator process** (press `Ctrl+C` in the terminal where `make run` was running).
2. Create a new Custom Resource or modify an existing one:
   ```bash
   kubectl apply -f my-cr.yaml
   ```
3. Verify that the native `CronJob` is still created/updated on GKE:
   ```bash
   kubectl get cronjobs
   ```
   *Since the operator is now running inside GKE, it will reconcile the changes automatically!*