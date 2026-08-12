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

    # A CMEK key cannot be removed from an existing plan once set (the script
    # warns and preserves it rather than trying); clearing this forces
    # replacement of the plan.
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
