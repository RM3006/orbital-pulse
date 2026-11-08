variable "project_id" {
  description = "Existing, approved GCP project ID. This configuration does not create a project or enable billing."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "Use a GCP project ID of 6-30 lowercase letters, digits or hyphens."
  }
}

variable "location" {
  description = "Approved BigQuery dataset location, for example EU or europe-west1."
  type        = string

  validation {
    condition     = length(trimspace(var.location)) > 0 && trimspace(var.location) == var.location
    error_message = "An explicit storage/warehouse location without surrounding whitespace is required."
  }
}

variable "dataset_prefix" {
  description = "Prefix for source, staging, core, marts and reporting datasets."
  type        = string
  default     = "space_launch_atlas"

  validation {
    condition     = can(regex("^[a-z][a-z0-9_]{0,49}$", var.dataset_prefix))
    error_message = "Use a lowercase dataset prefix starting with a letter, up to 50 characters."
  }
}

variable "enable_data_studio_service_account" {
  description = "Create the optional Data Studio service-account identity; requires a managed Workspace or Cloud Identity organization."
  type        = bool
  default     = false
}
