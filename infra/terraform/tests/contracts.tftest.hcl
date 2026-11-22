mock_provider "google" {}

run "render_reader_is_separate_and_read_only" {
  command = plan
  variables {
    enable_app_reader    = false
    enable_render_reader = true
  }
  assert {
    condition = (
      google_service_account.render_reader[0].account_id == "sla-render-reader" &&
      google_project_iam_member.render_reader_jobs[0].role == "roles/bigquery.jobUser" &&
      toset(keys(google_bigquery_dataset_iam_member.render_reader_data)) == toset(["reporting", "source"]) &&
      alltrue([for binding in google_bigquery_dataset_iam_member.render_reader_data :
        binding.role == "roles/bigquery.dataViewer"
      ]) &&
      length(google_service_account.app_reader) == 0
    )
    error_message = "The hosted reader must be separate, have only reporting/source reads and query-job creation, and leave the local reader disabled unless requested."
  }
}

run "app_reader_is_keyless_and_read_only" {
  command = plan
  variables {
    enable_app_reader   = true
    app_reader_operator = "user:reader@example.com"
  }
  assert {
    condition = (
      google_project_iam_member.app_reader_jobs[0].role == "roles/bigquery.jobUser" &&
      length(google_bigquery_dataset_iam_member.app_reader_data) == 2 &&
      alltrue([for binding in google_bigquery_dataset_iam_member.app_reader_data :
        binding.role == "roles/bigquery.dataViewer"
      ]) &&
      google_service_account_iam_member.app_reader_operator[0].role == "roles/iam.serviceAccountTokenCreator"
    )
    error_message = "The app identity must only read datasets and create query jobs, with keyless operator access."
  }
}

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
      google_bigquery_dataset_iam_member.dataset_access["source_worker"].role == "roles/bigquery.dataEditor" &&
      google_bigquery_dataset_iam_member.dataset_access["source_worker"].dataset_id == output.dataset_ids.source &&
      google_bigquery_dataset_iam_member.dataset_access["scratch_worker"].role == "roles/bigquery.dataEditor" &&
      google_bigquery_dataset_iam_member.dataset_access["scratch_worker"].dataset_id == output.dataset_ids.scratch &&
      length(google_project_iam_member.job_user) == 3 &&
      length(google_service_account.workload) == 3 &&
      !contains(keys(google_service_account.workload), "reporting") &&
      !contains(keys(google_bigquery_dataset_iam_member.dataset_access), "reporting_reader")
    )
    error_message = "Ingest, transform and reporting identities must have separate, scoped access."
  }

  assert {
    condition = (
      google_artifact_registry_repository_iam_member.worker_runtime_reader.role == "roles/artifactregistry.reader" &&
      google_artifact_registry_repository_iam_member.worker_runtime_reader.repository == "space-launch-atlas" &&
      google_bigquery_table.ll2_raw_response_pages.dataset_id == output.dataset_ids.source &&
      google_bigquery_table.ll2_raw_response_pages.time_partitioning[0].field == "request_started_at_utc" &&
      toset(google_bigquery_table.ll2_raw_response_pages.clustering) == toset(["run_id", "source"]) &&
      google_bigquery_table.ll2_raw_response_pages.deletion_protection &&
      one([
        for field in jsondecode(google_bigquery_table.ll2_raw_response_pages.schema) :
        field.mode if field.name == "request_started_at_utc"
      ]) == "NULLABLE"
    )
    error_message = "The exact-response archive must be partitioned, clustered, and protected from deletion."
  }

  assert {
    condition = toset(keys(google_project_service.required)) == toset([
      "bigquery.googleapis.com",
      "iam.googleapis.com",
      "cloudresourcemanager.googleapis.com",
      "serviceusage.googleapis.com",
      "artifactregistry.googleapis.com",
      "cloudbuild.googleapis.com",
      "run.googleapis.com",
    ])
    error_message = "Only required BigQuery, IAM, Resource Manager, Service Usage, Cloud Build, Cloud Run and Artifact Registry APIs should be enabled."
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
      length(google_project_iam_member.job_user) == 4
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
