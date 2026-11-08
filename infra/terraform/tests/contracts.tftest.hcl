mock_provider "google" {}

variables {
  project_id = "atlas-test-project"
  location   = "EU"
}

run "warehouse_contract" {
  command = plan

  assert {
    condition = alltrue([
      for dataset in google_bigquery_dataset.layer :
      dataset.location == var.location &&
      dataset.project == var.project_id &&
      !dataset.delete_contents_on_destroy && dataset.deletion_policy == "PREVENT"
    ])
    error_message = "Warehouse layers must share the approved project/location and protect accepted data."
  }

  assert {
    condition = (
      length(output.dataset_ids) == 6 &&
      output.dataset_ids.reporting == "space_launch_atlas_reporting" &&
      output.dataset_ids.source != output.dataset_ids.reporting
    )
    error_message = "Source and published reporting data must have separate dataset namespaces."
  }

  assert {
    condition     = google_bigquery_dataset.layer["scratch"].default_table_expiration_ms == 604800000
    error_message = "Scratch tables expire after seven days."
  }

  assert {
    condition = (
      google_bigquery_dataset_iam_member.dataset_access["reporting_transform"].role == "roles/bigquery.dataEditor" &&
      google_bigquery_dataset_iam_member.dataset_access["reporting_transform"].dataset_id == output.dataset_ids.reporting &&
      google_bigquery_dataset_iam_member.dataset_access["source_ingest"].role == "roles/bigquery.dataEditor" &&
      length(google_project_iam_member.job_user) == 2 &&
      length(google_service_account.workload) == 2 &&
      !contains(keys(google_service_account.workload), "reporting") &&
      !contains(keys(google_bigquery_dataset_iam_member.dataset_access), "reporting_reader")
    )
    error_message = "Ingest, transform and reporting identities must have separate, scoped access."
  }

  assert {
    condition = toset(keys(google_project_service.required)) == toset([
      "bigquery.googleapis.com",
      "iam.googleapis.com",
      "cloudresourcemanager.googleapis.com",
      "serviceusage.googleapis.com",
    ])
    error_message = "Only BigQuery, IAM, Resource Manager and Service Usage APIs should be enabled."
  }
}

run "enable_optional_data_studio_service_account" {
  command = plan

  variables {
    enable_data_studio_service_account = true
  }

  assert {
    condition = (
      google_bigquery_dataset_iam_member.dataset_access["reporting_reader"].role == "roles/bigquery.dataViewer" &&
      google_bigquery_dataset_iam_member.dataset_access["reporting_reader"].dataset_id == output.dataset_ids.reporting &&
      contains(keys(google_service_account.workload), "reporting") &&
      length(google_project_iam_member.job_user) == 3
    )
    error_message = "The optional Data Studio identity must only read reporting and create query jobs."
  }
}

run "reject_invalid_project" {
  command = plan

  variables {
    project_id = "invalid project"
  }

  expect_failures = [var.project_id]
}

run "reject_invalid_dataset_prefix" {
  command = plan

  variables {
    dataset_prefix = "invalid-prefix"
  }

  expect_failures = [var.dataset_prefix]
}
