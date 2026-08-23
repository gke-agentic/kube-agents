output "investigator_service_account_email" {
  description = "Email of the investigator's IAM service account. The loop's minty rule gates on this address, so it must match selfImprovement.github.gsaName."
  value       = google_service_account.investigator.email
}

output "minter_service_account_email" {
  description = "Email of the token minter's IAM service account, or null when create_minter is false"
  value       = var.create_minter ? google_service_account.minter[0].email : null
}

output "kms_keyring" {
  description = "Name of the KMS key ring holding the loop's App signing key, or null when create_minter is false"
  value       = var.create_minter ? google_kms_key_ring.minter[0].name : null
}

output "kms_key" {
  description = "Name of the KMS signing key to import the loop's GitHub App PEM into, or null when create_minter is false"
  value       = var.create_minter ? google_kms_crypto_key.minter[0].name : null
}
