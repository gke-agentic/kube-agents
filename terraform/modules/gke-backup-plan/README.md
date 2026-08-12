# GKE Backup Plan Module

Reusable Terraform module for provisioning a [Backup for GKE](https://cloud.google.com/kubernetes-engine/docs/add-on/backup-for-gke) `BackupPlan` that snapshots the namespace kube-agents runs in, on a schedule.

The plan is only half of the feature: the Backup for GKE **agent** must also be enabled on the target cluster. The `gke-cluster` module does that by default (`enable_backup_agent = true`), and the project needs `gkebackup.googleapis.com`.

> **Backups include Kubernetes Secrets and persistent volume data** by default, matching the provisioning script. That means the agent's credentials Secret is inside every backup — restrict backup and restore IAM to administrators who are already allowed to read those credentials, and consider `encryption_key` for CMEK.

> **Cost.** Backup for GKE bills per backed-up pod and per gigabyte of volume snapshot storage. Nothing is charged until a plan exists, which is why both install paths leave it opt-in.

## Relationship to the provisioning scripts

This module and `k8s-operator/scripts/provision_12_gke_backup_plan.sh` create the **same** BackupPlan — use one or the other for a given cluster, never both. The script does a check-then-create, so it will happily adopt and reconcile a Terraform-managed plan without Terraform noticing.

The defaults mirror the script's: the name `<cluster_name>-backup-plan`, a `0 2 * * *` schedule, 30-day retention, the `kubeagents-system` namespace, secrets and volume data included, and the schedule un-paused.

## Usage

```hcl
module "gke_backup_plan" {
  source       = "git::https://github.com/gke-labs/kube-agents.git//terraform/modules/gke-backup-plan?ref=vX.Y.Z"
  project_id   = "my-gcp-project"
  cluster_name = "platform-agent-host"
  location     = "us-central1"
}
```

See the [Release versioning & promotion guide](../../../docs/site/src/content/docs/deploy/release-versioning.md) for SemVer pinning instructions.
