resource "google_artifact_registry_repository" "worker" {
  project       = var.project_id
  location      = var.location
  repository_id = "space-launch-atlas"
  description   = "Private container images for the Space Launch Atlas worker."
  format        = "DOCKER"

  labels = {
    project    = "space-launch-atlas"
    managed_by = "terraform"
    component  = "worker"
  }

  depends_on = [google_project_service.required]
}

resource "google_service_account" "worker_builder" {
  project      = var.project_id
  account_id   = "sla-worker-build"
  display_name = "Space Launch Atlas worker image builder"
  description  = "Build-only identity with access to the temporary source bucket and worker image repository."

  depends_on = [google_project_service.required]
}

data "google_project" "current" {
  project_id = var.project_id
}

resource "google_service_account_iam_member" "cloudbuild_act_as_builder" {
  service_account_id = google_service_account.worker_builder.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-cloudbuild.iam.gserviceaccount.com"

  depends_on = [google_project_service.required]
}

resource "google_artifact_registry_repository_iam_member" "worker_builder" {
  project    = var.project_id
  location   = google_artifact_registry_repository.worker.location
  repository = google_artifact_registry_repository.worker.repository_id
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.worker_builder.email}"
}

resource "google_artifact_registry_repository_iam_member" "worker_runtime_reader" {
  project    = var.project_id
  location   = google_artifact_registry_repository.worker.location
  repository = google_artifact_registry_repository.worker.repository_id
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.workload["worker"].email}"
}

resource "google_project_iam_member" "worker_builder_logs" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.worker_builder.email}"
}

resource "google_cloud_run_v2_job" "worker" {
  count = var.worker_image_uri == "" ? 0 : 1

  project  = var.project_id
  name     = "sla-ll2-worker"
  location = var.location
  labels = {
    project    = "space-launch-atlas"
    managed_by = "terraform"
    component  = "worker"
  }

  template {
    task_count  = 1
    parallelism = 1

    template {
      service_account = google_service_account.workload["worker"].email
      max_retries     = 0
      timeout         = "600s"

      containers {
        name  = "worker"
        image = var.worker_image_uri

        resources {
          limits = {
            cpu    = "1"
            memory = "4Gi"
          }
        }

        env {
          name  = "GCP_PROJECT_ID"
          value = var.project_id
        }
        env {
          name  = "GCP_LOCATION"
          value = var.location
        }
        env {
          name  = "BIGQUERY_DATASET_PREFIX"
          value = var.dataset_prefix
        }
        env {
          name  = "LOCAL_DATA_DIR"
          value = "/tmp/data"
        }
      }
    }
  }

  deletion_protection = false
  depends_on = [
    google_project_service.required,
    google_project_iam_member.job_user["worker"],
    google_bigquery_dataset_iam_member.dataset_access["source_worker"],
    google_bigquery_dataset_iam_member.dataset_access["scratch_worker"],
    google_artifact_registry_repository_iam_member.worker_runtime_reader,
  ]
}
