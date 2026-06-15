# Provisioning & Teardown Scripts Reference

This directory contains the automation scripts for provisioning and tearing down the GCP and GKE infrastructure required by the `kube-agents` platform agent and operator.

## Architecture & Configuration Flow

All scripts are modular and idempotent. They share a single configuration state stored in a local [vars.sh](vars.sh) file (which is git-ignored).

When any script is run:

1. It checks if [vars.sh](vars.sh) exists.
2. If any required variables are missing, the script prompts the user for them, exports them, and appends them to [vars.sh](vars.sh).
3. If they are already defined in [vars.sh](vars.sh), the script sources them and runs non-interactively.

---

## File Directory

### Orchestration Scripts

- **[provision.sh](provision.sh)**: Master script that coordinates the execution of all provisioning steps (01 to 06).
- **[teardown.sh](teardown.sh)**: Master script that coordinates the teardown steps in reverse order (06 down to 01).

### Provisioning Steps

1. **[provision_01_gcp_cluster.sh](provision_01_gcp_cluster.sh)**
   - Sets up initial project configs.
   - Enables GCP Service APIs (`container.googleapis.com`, `secretmanager.googleapis.com`, `pubsub.googleapis.com`, etc.).
   - Provisions a GKE Standard Cluster with Workload Identity enabled.
   - Points `kubectl` credentials to the new cluster and creates the target namespace.
2. **[provision_02_gcp_secrets.sh](provision_02_gcp_secrets.sh)**
   - Creates required placeholders in GCP Secret Manager (e.g. `GEMINI_API_KEY`) if they do not exist.
   - Creates a Kubernetes Secret (`platform-agent-secrets`) in the target GKE namespace and maps the secret values from Secret Manager.
3. **[provision_03_gcp_gchat.sh](provision_03_gcp_gchat.sh)**
   - Creates a GCP Google Service Account (GSA) for the agent.
   - Sets up the Pub/Sub Topic and Subscription for Google Chat events.
4. **[provision_04_gcp_iam.sh](provision_04_gcp_iam.sh)**
   - Assigns IAM permissions to the Google Service Account (Vertex AI User, Pub/Sub Subscriber).
   - Configures IAM bindings to tie the Kubernetes Service Account (KSA) to the GCP Google Service Account (GSA) using Workload Identity.
5. **[provision_05_gcp_operator.sh](provision_05_gcp_operator.sh)**
   - Installs Custom Resource Definitions (CRDs) for `PlatformAgent`, `DevTeamAgent`, and `OperatorAgent`.
   - Deploys the Operator controller manager into the GKE cluster.
6. **[provision_06_gcp_deploy.sh](provision_06_gcp_deploy.sh)**
   - Uses `envsubst` to render `platform-agent.yaml` from its template.
   - Applies the resulting `PlatformAgent` Custom Resource (CR) to deploy the platform agent instance.

### Teardown Steps

- **[teardown_06_gcp_deploy.sh](teardown_06_gcp_deploy.sh)**: Safely deletes the `PlatformAgent` Custom Resource and cleans up local manifests.
- **[teardown_05_gcp_operator.sh](teardown_05_gcp_operator.sh)**: Removes the Operator manager deployment and unregisters CRDs.
- **[teardown_04_gcp_iam.sh](teardown_04_gcp_iam.sh)**: Removes IAM policy bindings and Workload Identity associations.
- **[teardown_03_gcp_gchat.sh](teardown_03_gcp_gchat.sh)**: Deletes the Pub/Sub topic, subscription, and the bot GSA.
- **[teardown_02_gcp_secrets.sh](teardown_02_gcp_secrets.sh)**: Deletes GKE Secrets and Secret Manager assets.
- **[teardown_01_gcp_cluster.sh](teardown_01_gcp_cluster.sh)**: Deletes the GKE Standard cluster and removes the local state file `vars.sh`.

---

## Direct Usage Examples

Normally, these scripts are run via the parent Makefile targets. However, they can also be run directly.

### Run Provision Pipeline

Execute the master script from this directory:

```bash
./provision.sh
```

To run a dry-run check (simulates commands without modifying cloud resources):

```bash
./provision.sh --dry-run
```

### Run Teardown Pipeline

Clean up the provisioned environment:

```bash
./teardown.sh
```

### Run Specific Step

For example, if you want to update IAM configurations:

```bash
./provision_04_gcp_iam.sh
```
