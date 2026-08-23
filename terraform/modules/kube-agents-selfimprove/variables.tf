variable "project_id" {
  description = "GCP Project ID"
  type        = string
}

variable "location" {
  description = "GCP region, or the zonal cluster location it is derived from, for the KMS key ring"
  type        = string
}

variable "namespace" {
  description = "Kubernetes namespace the self-improvement CronJob runs in"
  type        = string
  default     = "kubeagents-system"
}

variable "service_account_id" {
  description = "IAM service account for the investigator. Must match selfImprovement.github.gsaName in the chart."
  type        = string
  default     = "kubeagents-selfimprove"

  validation {
    # 23, not 30. The minter account below is this plus `-minter`, and GCP caps
    # a service account id at 30 characters -- so the real ceiling on this one
    # is 30 minus the seven characters that get appended. Checked here as well
    # as in the chart because either can be applied without the other.
    condition     = can(regex("^[a-z]([-a-z0-9]{4,21}[a-z0-9])$", var.service_account_id))
    error_message = "service_account_id must be 6-23 characters, start with a lowercase letter, and contain only lowercase letters, digits, and hyphens. The derived `-minter` account must fit in 30."
  }
}

variable "ksa_name" {
  description = "Kubernetes service account the CronJob runs as. Must match selfImprovement.github.ksaName."
  type        = string
  default     = "kubeagents-selfimprove"
}

variable "create_minter" {
  description = "Create the loop's own GitHub token minter identity and KMS key. Only needed when selfImprovement.mode is fork or upstream; report-only has no GitHub identity at all."
  type        = bool
  default     = false
}

variable "minter_ksa_name" {
  description = "Kubernetes service account the loop's minter Deployment runs as"
  type        = string
  default     = "kube-agents-selfimprove-token-minter"
}

variable "kms_keyring_name" {
  description = "KMS key ring holding the loop's GitHub App signing key"
  type        = string
  default     = "selfimprove-token-minter-keyring"
}

variable "kms_key_name" {
  description = "KMS asymmetric signing key the loop's GitHub App private key is imported into"
  type        = string
  default     = "selfimprove-token-minter-key"
}
