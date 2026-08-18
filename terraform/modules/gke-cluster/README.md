# GKE Autopilot Cluster Module

Reusable Terraform module for provisioning a GKE Autopilot cluster configured for Kube-Agents workloads. Autopilot clusters are regional: `location` must be a region (a zone is rejected at plan time). The full-install composition passes `kube-agents-host=true` through `resource_labels` so the admin portal can discover the deployed host; standalone callers can use the same input when they install kube-agents on the cluster.

By default (`enable_database_encryption = true`), the module provisions a Cloud KMS Keyring and CryptoKey, binds `roles/cloudkms.cryptoKeyEncrypterDecrypter` to the GKE Service Agent, and enables etcd database encryption (CMEK). FQDN NetworkPolicy is also on by default (`enable_fqdn_network_policy`), matching the cluster `provision_01_gcp_cluster.sh` creates — the operator's opt-in `FQDNNetworkPolicy` companion objects only enforce on clusters that have it.

Set `allow_external_dns_traffic = true` for a cluster the Platform Agent has to reach from outside the VPC. It drives `control_plane_endpoints_config.dns_endpoint_config.allow_external_traffic`, the field the agent's endpoint detection reads before it passes `get-credentials --dns-endpoint` (see [`k8s-operator/scripts/gke_dns_endpoint.sh`](../../../k8s-operator/scripts/gke_dns_endpoint.sh)); a cluster whose IP endpoint the agent cannot route to and whose DNS endpoint serves no external traffic is unreachable. This block is why the module requires provider `>= 6.11` — it does not exist in 5.x.

The default is `false`, which is GKE's own default and therefore the value every cluster this module already manages is sitting at: the module set no `control_plane_endpoints_config` before, so upgrading to a version that does leaves an existing cluster's plan empty rather than publishing a control-plane endpoint its operator never asked for. That matters more than an ordinary default because this endpoint is governed by IAM alone — no private-endpoint or master-authorized-networks setting is holding it shut. The field is Terraform-managed either way once the module renders it, so change it here rather than with `gcloud container clusters update --enable-dns-access`: out-of-band it is drift that the next apply reverts. That cuts both ways on upgrade — a cluster whose endpoint was opened with `gcloud` while the module did not manage the field needs `allow_external_dns_traffic = true` before the next apply, which would otherwise close it and leave the agent on the IP endpoint.

> **KMS resources cannot be deleted.** Cloud KMS key rings and keys are never actually
> destroyed — `terraform destroy` only removes them from state, and a subsequent apply
> with the same names fails with a 409 (the provisioning scripts sidestep this by
> check-then-create). Recover by importing the existing resources back into state
> (`terraform import module.<name>.google_kms_key_ring.gke_keyring ...`) or by choosing new
> `kms_keyring_name`/`kms_key_name` values.

## Usage

```hcl
module "gke_cluster" {
  source          = "git::https://github.com/gke-labs/kube-agents.git//terraform/modules/gke-cluster?ref=1.2.0"
  project_id      = "my-gcp-project"
  cluster_name    = "production-host-01"
  location        = "us-central1"
  resource_labels = {
    "kube-agents-host" = "true"
  }

  # Reachable by a Platform Agent that does not sit in this VPC. Omit for a
  # cluster that should stay VPC-only.
  allow_external_dns_traffic = true
}
```

See the [Release versioning & promotion guide](../../../docs/site/src/content/docs/deploy/release-versioning.md) for SemVer pinning instructions.
