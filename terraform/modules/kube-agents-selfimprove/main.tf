# The self-improvement loop's Google identity.
#
# Two accounts, and the split is the point. The investigator reads telemetry and
# nothing else; the minter signs GitHub App assertions and reads nothing. Giving
# one account both jobs would mean the identity that can open a pull request on
# the repository is the same one that decides a pull request is warranted.
#
# Kept out of kube-agents-iam deliberately. That module grants the Platform
# Agent what it needs to manage the fleet -- up to container.admin, under the
# full-install composition's gke-admin permission set -- and the loop must not
# inherit any of it: an agent that can modify the cluster it is
# investigating cannot honestly report on it. A separate module also means an
# install can destroy this one alone and leave the product running.

locals {
  minter_account_id = "${var.service_account_id}-minter"

  # Cloud KMS has no zonal locations, so a zonal cluster location maps to its
  # region. Same derivation as the chart's KMS_KEY_NAME and the installer's
  # derive_kms_location; they have to agree or the minter looks up a key that
  # is not there.
  kms_location = replace(var.location, "/-[a-z]$/", "")
}

# ---------------------------------------------------------------------------
# The investigator
# ---------------------------------------------------------------------------

resource "google_service_account" "investigator" {
  project      = var.project_id
  account_id   = var.service_account_id
  display_name = "Kube-Agents Self-Improvement Investigator"
  description  = "Read-only telemetry access for the self-improvement CronJob. Holds no GKE roles by design."
}

resource "google_service_account_iam_member" "investigator_workload_identity" {
  service_account_id = google_service_account.investigator.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[${var.namespace}/${var.ksa_name}]"
}

# The complete grant. Three viewer roles, matching the three things
# selfimprove_evidence.py can query, and nothing else.
#
# Notably absent: container.viewer. Kubernetes reads go through the pod's
# Kubernetes service account, which the chart binds to `view` in one namespace
# -- so the blast radius of the cluster half is a namespace rather than a
# project, and it is enforced by RBAC rather than by IAM. Adding
# container.viewer here would silently widen that to every cluster in the
# project.
resource "google_project_iam_member" "investigator" {
  for_each = toset([
    "roles/logging.viewer",
    "roles/cloudtrace.viewer",
    "roles/monitoring.viewer",
  ])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.investigator.email}"
}

# ---------------------------------------------------------------------------
# The minter, for fork and upstream modes only
# ---------------------------------------------------------------------------

resource "google_service_account" "minter" {
  count = var.create_minter ? 1 : 0

  project      = var.project_id
  account_id   = local.minter_account_id
  display_name = "Kube-Agents Self-Improvement Token Minter"
  description  = "Signs GitHub App assertions for the self-improvement loop. A different App from the Platform Agent's."
}

resource "google_service_account_iam_member" "minter_workload_identity" {
  count = var.create_minter ? 1 : 0

  service_account_id = google_service_account.minter[0].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[${var.namespace}/${var.minter_ksa_name}]"
}

# Its own key ring, not a second key on github-token-minter-keyring. Rotating or
# destroying the loop's App key must not be able to touch the one the agent's
# GitOps writes depend on, and a shared ring makes that a matter of care rather
# than of structure.
resource "google_kms_key_ring" "minter" {
  count = var.create_minter ? 1 : 0

  project  = var.project_id
  name     = var.kms_keyring_name
  location = local.kms_location
}

# Import-only and with no initial version: the GitHub App private key PEM is
# imported afterwards. Until it is, the minter pod fails readiness and the loop
# files nothing -- which is the correct failure, since the alternative is a loop
# that believes it can open pull requests and discovers otherwise at the end of
# an hour's investigation.
resource "google_kms_crypto_key" "minter" {
  #checkov:skip=CKV_GCP_82:Import-only asymmetric signing key lifecycle is managed via Minty/KMS
  count = var.create_minter ? 1 : 0

  name     = var.kms_key_name
  key_ring = google_kms_key_ring.minter[0].id
  purpose  = "ASYMMETRIC_SIGN"

  version_template {
    algorithm        = "RSA_SIGN_PKCS1_2048_SHA256"
    protection_level = "SOFTWARE"
  }

  import_only                   = true
  skip_initial_version_creation = true
}

resource "google_kms_crypto_key_iam_member" "minter_signer_verifier" {
  count = var.create_minter ? 1 : 0

  crypto_key_id = google_kms_crypto_key.minter[0].id
  role          = "roles/cloudkms.signerVerifier"
  member        = "serviceAccount:${google_service_account.minter[0].email}"
}
