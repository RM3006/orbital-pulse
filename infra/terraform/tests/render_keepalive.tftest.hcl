mock_provider "google" {}

variables {
  project_id = "atlas-test-project"
  location   = "EU"
}

run "scheduler_is_disabled_by_default" {
  command = plan

  assert {
    condition = (
      length(google_project_service.render_keepalive_scheduler) == 0 &&
      length(google_cloud_scheduler_job.render_keepalive) == 0
    )
    error_message = "The fallback must create no scheduler API or job until explicitly enabled."
  }
}

run "enabled_job_only_pings_public_health" {
  command = plan

  variables {
    enable_render_keepalive = true
  }

  assert {
    condition = (
      length(google_cloud_scheduler_job.render_keepalive) == 1 &&
      google_project_service.render_keepalive_scheduler[0].service == "cloudscheduler.googleapis.com" &&
      google_cloud_scheduler_job.render_keepalive[0].project == var.project_id &&
      google_cloud_scheduler_job.render_keepalive[0].region == "europe-west1" &&
      google_cloud_scheduler_job.render_keepalive[0].http_target[0].http_method == "GET" &&
      google_cloud_scheduler_job.render_keepalive[0].http_target[0].uri == "https://space-launch-atlas.onrender.com/healthz" &&
      length(google_cloud_scheduler_job.render_keepalive[0].http_target[0].oauth_token) == 0 &&
      length(google_cloud_scheduler_job.render_keepalive[0].http_target[0].oidc_token) == 0
    )
    error_message = "The sole fallback job must request only public Render health without sending credentials."
  }

  assert {
    condition = (
      google_cloud_scheduler_job.render_keepalive[0].schedule == "4,9,14,19,24,29,34,39,44,49,54,59 * * * *" &&
      google_cloud_scheduler_job.render_keepalive[0].time_zone == "Etc/UTC" &&
      google_cloud_scheduler_job.render_keepalive[0].attempt_deadline == "90s" &&
      google_cloud_scheduler_job.render_keepalive[0].retry_config[0].retry_count == 1 &&
      google_cloud_scheduler_job.render_keepalive[0].retry_config[0].max_retry_duration == "120s"
    )
    error_message = "Keep five-minute health requests and bounded cold-start retries."
  }
}
