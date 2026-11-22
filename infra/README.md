# Terraform harness

Terraform 1.13.1 and the Google provider 7.46.2 are installed and locked under `infra/terraform/`. The user-authorized dataset/IAM plan and protected, partitioned `source.ll2_raw_response_pages` table were applied. The table has 304 historical local responses plus 11 responses from two Cloud Run executions; all 11 cloud body hashes were verified. A private image repository, Cloud Build identity, Cloud Run worker identity and one-task `sla-ll2-worker` job are deployed. A second image was built and applied for a one-request setting, but a hard-coded worker-tick limit overrode it; the corrected code is local and not yet deployed. There is no persistent GCS bucket, remote Terraform backend or Cloud Scheduler.

## What the configuration changes

- Enables BigQuery, IAM, Cloud Resource Manager, Service Usage, Artifact Registry, Cloud Build and Cloud Run APIs.
- Manages `source`, `staging`, `core`, `marts`, `reporting` and `scratch` datasets in one location. Datasets are protected from Terraform deletion; new scratch tables default to seven-day expiry.
- Defines `source.ll2_raw_response_pages` with an explicit schema, day partitioning by request time, clustering by run/source and deletion protection. The table stores exact API bodies as `BYTES`; it does not store a parsed/canonicalized JSON value.
- Creates `sla-ingest`, `sla-transform`, `sla-worker` and `sla-worker-build` service accounts, without keys. The worker can edit only `source` and `scratch`; the builder can write only to the private image repository, and the runtime worker can read images only from that repository.
- Defines separate optional local and hosted Dash readers. Both have BigQuery Job User and Data Viewer on only reporting/source. The local reader uses operator impersonation; the user-approved Render reader has one key stored only in Render's secret group, outside Terraform state. Their approved IAM changes and required inspection/token APIs are applied. See [operations](../docs/operations.md#render-deployment-configuration) for flags, rotation and key-policy cleanup.
- Grants workload accounts BigQuery job creation at project scope. Ingest can edit only `source`. Transform can read `source` and edit the other five datasets. The optional reporting identity is off; if later enabled, it can read only `reporting` and create query jobs.
- Defines a Cloud Run Job with one task, 1 vCPU, 4 GiB, a 600-second timeout and zero retries. It has no Cloud Scheduler trigger. The first execution made four LL2 requests (three HTTP 200, one HTTP 502 with a 60-second retry delay) and paused. The second was intended to make one request, but made seven HTTP 200 calls because the worker code reset the cap to eight. All 11 response rows are in the permanent raw table and hashes match. The tested local fix is not in the deployed image; do not execute this image again until updated.
- Terraform does not create or link a billing account, persist worker state in GCS, publish a report or create a recurring schedule. The container build used a private temporary source bucket, which was removed after the build.

The existing four datasets (`source`, `staging`, `core`, `marts`) were imported into local state before applying. The plan adopted them, then created `reporting` and `scratch`; ordinary pipeline tables remain outside Terraform, while the raw archive table is explicitly managed. The initial plan was 17 additions, 4 in-place metadata/protection updates, and 0 deletions. The raw-table plan added one protected BigQuery table; a follow-up schema relaxation made the historical request-start timestamp nullable to preserve missing source metadata. The request-cap image update was applied in place (0 additions, 1 change, 0 deletions). A further image update is needed to deploy the local cap fix.

## Authentication and IAM are separate

The Google provider uses Application Default Credentials (ADC), so Terraform and local dbt can use the same gcloud user login. ADC answers “which identity is calling?” The OAuth `cloud-platform` scope permits API request types, but does not itself grant project permissions. Google Cloud IAM answers “what can that identity do?” Effective permissions also depend on inherited project/folder/organization roles and policies.

The human ADC identity running Terraform must already be allowed to:

- enable services (`serviceusage.services.enable`), commonly through `roles/serviceusage.serviceUsageAdmin`;
- create/manage project service accounts, commonly through `roles/iam.serviceAccountAdmin`;
- create/update BigQuery datasets and their dataset IAM, commonly through `roles/bigquery.admin`;
- grant project-level `roles/bigquery.jobUser`, requiring project IAM policy update permission, commonly through `roles/resourcemanager.projectIamAdmin`.

These roles are Terraform operator prerequisites, not roles that this configuration grants to itself. They are broad; inherited access or a custom role with only the required permissions is preferable. Keep workload/local-reader access keyless. The separately approved Render reader key is managed outside Terraform and stored only in Render. If the current ADC user lacks a prerequisite, Terraform reports the denied API/permission; grant only the missing permission to the human operator through the project IAM console, then retry.

## Initialize, import, plan, apply

Copy `terraform.tfvars.example` to an ignored `terraform.tfvars` and fill the existing project ID and BigQuery location, or set `TF_VAR_project_id` and `TF_VAR_location` in the current PowerShell session. These are identifiers, not secrets. Do not put ADC credentials or service-account keys in this directory.

```powershell
terraform -chdir=infra/terraform init -backend=false -input=false
terraform -chdir=infra/terraform validate
terraform -chdir=infra/terraform import 'google_bigquery_dataset.layer["source"]' "projects/$env:TF_VAR_project_id/datasets/space_launch_atlas_source"
terraform -chdir=infra/terraform import 'google_bigquery_dataset.layer["staging"]' "projects/$env:TF_VAR_project_id/datasets/space_launch_atlas_staging"
terraform -chdir=infra/terraform import 'google_bigquery_dataset.layer["core"]' "projects/$env:TF_VAR_project_id/datasets/space_launch_atlas_core"
terraform -chdir=infra/terraform import 'google_bigquery_dataset.layer["marts"]' "projects/$env:TF_VAR_project_id/datasets/space_launch_atlas_marts"
terraform -chdir=infra/terraform plan -out step3.tfplan
terraform -chdir=infra/terraform show step3.tfplan
terraform -chdir=infra/terraform apply -input=false step3.tfplan
```

For future changes, review the saved plan before applying. The original plan contained no GCS/Storage resources, no replacement and no deletion. The user authorized and this exact plan was applied. State remains local at `terraform.tfstate` (ignored by Git) and must be backed up; do not configure a GCS backend as part of the raw-archive work.

The Python command `warehouse-raw-archive --all-runs` uploads local response evidence to the managed table using ADC and deterministic BigQuery load jobs. It archived 304 historical live responses from 221 run manifests; response IDs and SHA-256 values matched the local files. The Cloud Run worker uses its attached service account and archives each HTTP response before parsing. Its one-off test added four more unique raw rows; SQL confirmed the SHA-256 of each stored `BYTES` body. The job used a temporary BigQuery checkpoint that is not a durable weekly state store. The local worker installer still adds the raw-archive flag when both GCP identifiers exist in `.env.local`. These paths require the raw table to exist and use `CREATE_NEVER`, so they fail rather than silently creating an unmanaged table. Local SQLite/raw data remain authoritative.

## Offline checks

From the repository root:

```powershell
terraform -chdir=infra/terraform fmt -check -recursive
terraform -chdir=infra/terraform validate
terraform -chdir=infra/terraform test
```

The native tests use a mocked provider and `command = plan`; they check policy without ADC or cloud writes. They do not prove the live user IAM, organization policy or API availability.
