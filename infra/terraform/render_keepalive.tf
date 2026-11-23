variable "enable_render_keepalive" {
  description = "Create one public HTTP health-ping job only after approval and checking the billing account's three-job free allowance."
  type        = bool
  default     = false
}

resource "google_project_service" "render_keepalive_scheduler" {
  count              = var.enable_render_keepalive ? 1 : 0
  project            = var.project_id
  service            = "cloudscheduler.googleapis.com"
  disable_on_destroy = false
}

resource "google_cloud_scheduler_job" "render_keepalive" {
  count            = var.enable_render_keepalive ? 1 : 0
  project          = var.project_id
  region           = "europe-west1"
  name             = "sla-render-keepalive"
  description      = "Keep the public Render app responsive; health only, no warehouse or source work."
  schedule         = "4,9,14,19,24,29,34,39,44,49,54,59 * * * *"
  time_zone        = "Etc/UTC"
  attempt_deadline = "90s"

  http_target {
    http_method = "GET"
    uri         = "https://space-launch-atlas.onrender.com/healthz"
  }

  retry_config {
    retry_count = 1
    # A nonzero duration can force retries beyond retry_count.
    max_retry_duration   = "0s"
    min_backoff_duration = "5s"
    max_backoff_duration = "30s"
    max_doublings        = 2
  }

  depends_on = [google_project_service.render_keepalive_scheduler]
}
