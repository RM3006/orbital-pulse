variable "enable_app_reader" {
  description = "Enable the keyless read-only reader for the locally operated Dash application."
  type        = bool
  default     = false
}

variable "enable_render_reader" {
  description = "Enable the separately scoped read-only identity for the Render-hosted Dash application."
  type        = bool
  default     = false
}

variable "app_reader_operator" {
  description = "IAM member allowed to impersonate only the app reader; set in ignored local tfvars."
  type        = string
  default     = ""
  validation {
    condition     = var.app_reader_operator == "" || can(regex("^user:[^@ ]+@[^@ ]+$", var.app_reader_operator))
    error_message = "Use a user IAM member."
  }
}

resource "google_project_service" "app_reader_tokens" {
  count              = var.enable_app_reader ? 1 : 0
  project            = var.project_id
  service            = "iamcredentials.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "render_reader_org_policy" {
  count              = var.enable_render_reader ? 1 : 0
  project            = var.project_id
  service            = "orgpolicy.googleapis.com"
  disable_on_destroy = false
}

resource "google_service_account" "app_reader" {
  count        = var.enable_app_reader ? 1 : 0
  project      = var.project_id
  account_id   = "sla-app-reader"
  display_name = "Space Launch Atlas app reader"
  description  = "Read-only reporting and snapshot labels; no keys and no warehouse writes."
}

resource "google_project_iam_member" "app_reader_jobs" {
  count   = var.enable_app_reader ? 1 : 0
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.app_reader[0].email}"
}

resource "google_bigquery_dataset_iam_member" "app_reader_data" {
  for_each   = var.enable_app_reader ? toset(["reporting", "source"]) : toset([])
  project    = var.project_id
  dataset_id = "${var.dataset_prefix}_${each.key}"
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.app_reader[0].email}"
}

resource "google_service_account_iam_member" "app_reader_operator" {
  count              = var.enable_app_reader && var.app_reader_operator != "" ? 1 : 0
  service_account_id = google_service_account.app_reader[0].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = var.app_reader_operator
}

resource "google_service_account" "render_reader" {
  count        = var.enable_render_reader ? 1 : 0
  project      = var.project_id
  account_id   = "sla-render-reader"
  display_name = "Space Launch Atlas Render reader"
  description  = "Read-only reporting and snapshot labels for the hosted Atlas; no warehouse writes."
}

resource "google_project_iam_member" "render_reader_jobs" {
  count   = var.enable_render_reader ? 1 : 0
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.render_reader[0].email}"
}

resource "google_bigquery_dataset_iam_member" "render_reader_data" {
  for_each   = var.enable_render_reader ? toset(["reporting", "source"]) : toset([])
  project    = var.project_id
  dataset_id = "${var.dataset_prefix}_${each.key}"
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.render_reader[0].email}"
}
