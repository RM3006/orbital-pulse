output "dataset_ids" {
  description = "Warehouse layer names; dbt will own modeled tables within these datasets."
  value       = { for layer, dataset in google_bigquery_dataset.layer : layer => dataset.dataset_id }
}

output "workload_service_accounts" {
  description = "Non-secret workload identities. No keys are created or exported."
  value       = { for name, account in google_service_account.workload : name => account.email }
}

output "worker_image_repository" {
  description = "Private Artifact Registry repository used for the Cloud Run worker image."
  value       = google_artifact_registry_repository.worker.name
}
