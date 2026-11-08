locals {
  labels = {
    project    = "space-launch-atlas"
    managed_by = "terraform"
  }
  datasets = {
    source    = "Source-aligned ingestion tables with provenance."
    staging   = "Typed and standardized source records."
    core      = "Launch, hardware, site and observation models at declared grains."
    marts     = "Validated analytical metrics and release candidates."
    reporting = "Validated reporting releases consumed by Data Studio."
    scratch   = "Short-lived isolated candidate builds; tables expire after seven days."
  }
  service_account_catalog = {
    ingest    = "Loads accepted local Parquet inputs into source tables."
    transform = "Runs dbt transformations and publishes reporting tables."
    reporting = "Read-only Data Studio query identity."
  }
  service_accounts = {
    for name, description in local.service_account_catalog : name => description
    if name != "reporting" || var.enable_data_studio_service_account
  }
  dataset_roles = {
    source_ingest       = { dataset = "source", account = "ingest", role = "roles/bigquery.dataEditor" }
    source_transform    = { dataset = "source", account = "transform", role = "roles/bigquery.dataViewer" }
    staging_transform   = { dataset = "staging", account = "transform", role = "roles/bigquery.dataEditor" }
    core_transform      = { dataset = "core", account = "transform", role = "roles/bigquery.dataEditor" }
    marts_transform     = { dataset = "marts", account = "transform", role = "roles/bigquery.dataEditor" }
    reporting_transform = { dataset = "reporting", account = "transform", role = "roles/bigquery.dataEditor" }
    scratch_transform   = { dataset = "scratch", account = "transform", role = "roles/bigquery.dataEditor" }
    reporting_reader = {
      dataset = "reporting"
      account = "reporting"
      role    = "roles/bigquery.dataViewer"
    }
  }
  dataset_roles_enabled = {
    for name, binding in local.dataset_roles : name => binding
    if name != "reporting_reader" || var.enable_data_studio_service_account
  }
}

resource "google_project_service" "required" {
  for_each = toset([
    "bigquery.googleapis.com",
    "iam.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "serviceusage.googleapis.com",
  ])

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_bigquery_dataset" "layer" {
  for_each = local.datasets

  project                     = var.project_id
  dataset_id                  = "${var.dataset_prefix}_${each.key}"
  location                    = var.location
  description                 = each.value
  delete_contents_on_destroy  = false
  deletion_policy             = "PREVENT"
  default_table_expiration_ms = each.key == "scratch" ? 604800000 : null
  labels                      = merge(local.labels, { layer = each.key })

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.required]
}

resource "google_service_account" "workload" {
  for_each = local.service_accounts

  project      = var.project_id
  account_id   = "sla-${each.key}"
  display_name = "Space Launch Atlas ${each.key}"
  description  = each.value

  depends_on = [google_project_service.required]
}

resource "google_bigquery_dataset_iam_member" "dataset_access" {
  for_each = local.dataset_roles_enabled

  project    = var.project_id
  dataset_id = google_bigquery_dataset.layer[each.value.dataset].dataset_id
  role       = each.value.role
  member     = "serviceAccount:${google_service_account.workload[each.value.account].email}"
}

resource "google_project_iam_member" "job_user" {
  for_each = local.service_accounts

  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.workload[each.key].email}"
}
