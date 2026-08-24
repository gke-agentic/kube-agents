output "investigator_service_account_email" {
  description = "Email of the investigator's IAM service account. The chart annotates the loop's KSA with it, so it must match selfImprovement.github.gsaName."
  value       = google_service_account.investigator.email
}
