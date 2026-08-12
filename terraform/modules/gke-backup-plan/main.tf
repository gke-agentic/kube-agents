locals {
  backup_plan_name = coalesce(var.name, "${var.cluster_name}-backup-plan")
}

# Mirrors the BackupPlan k8s-operator/scripts/provision_12_gke_backup_plan.sh
# creates: the same default name, schedule, retention, namespace scope, and
# include-secrets/include-volume-data choices. Pick one path per cluster —
# both create the same resource, and the script's check-then-create would
# adopt a Terraform-managed plan without Terraform knowing.
resource "google_gke_backup_backup_plan" "this" {
  name     = local.backup_plan_name
  project  = var.project_id
  location = var.location
  cluster  = "projects/${var.project_id}/locations/${var.location}/clusters/${var.cluster_name}"

  backup_config {
    include_secrets     = var.include_secrets
    include_volume_data = var.include_volume_data

    selected_namespaces {
      namespaces = var.selected_namespaces
    }

    # Set this once or not at all. Clearing it on a plan that already holds
    # backups does not quietly swap the encryption over: the delete half of the
    # replacement is refused while backups exist (see the README's Teardown
    # section), so the apply stops partway. provision_12 sidesteps the same
    # problem by warning and preserving the existing key rather than trying.
    dynamic "encryption_key" {
      for_each = var.encryption_key == "" ? [] : [var.encryption_key]
      content {
        gcp_kms_encryption_key = encryption_key.value
      }
    }
  }

  backup_schedule {
    cron_schedule = var.cron_schedule
    paused        = var.paused
  }

  retention_policy {
    backup_retain_days = var.backup_retain_days
  }
}
