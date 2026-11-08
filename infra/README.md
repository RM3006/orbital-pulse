# Terraform harness

Terraform 1.13.1 and the Google provider 7.46.2 are installed and locked under `infra/terraform/`. On 2026-11-08 the user-authorized plan was applied to the existing project and a refresh plan reported no drift. The configuration manages six BigQuery datasets, four required APIs, two workload service accounts and scoped BigQuery IAM. Raw evidence remains local: there is no GCS bucket, Storage API enablement, storage role or remote Terraform backend.

## What the configuration changes

- Enables BigQuery, IAM, Cloud Resource Manager and Service Usage APIs.
- Manages `source`, `staging`, `core`, `marts`, `reporting` and `scratch` datasets in one location. Datasets are protected from Terraform deletion; new scratch tables default to seven-day expiry.
- Creates `sla-ingest` and `sla-transform` service accounts, without keys.
- Grants both identities BigQuery job creation at project scope. Ingest can edit only `source`. Transform can read `source` and edit the other five datasets. The optional reporting identity is off; if later enabled, it can read only `reporting` and create query jobs.
- Does not create or link a billing account, run queries, load data, enable GCS, or publish a report.

The existing four datasets (`source`, `staging`, `core`, `marts`) were imported into local state before applying. The plan adopted them, then created `reporting` and `scratch`; their tables are not Terraform-managed. The initial plan was 17 additions, 4 in-place metadata/protection updates, and 0 deletions. The post-apply plan had no changes.

## Authentication and IAM are separate

The Google provider uses Application Default Credentials (ADC), so Terraform and local dbt can use the same gcloud user login. ADC answers “which identity is calling?” The OAuth `cloud-platform` scope permits API request types, but does not itself grant project permissions. Google Cloud IAM answers “what can that identity do?” Effective permissions also depend on inherited project/folder/organization roles and policies.

The human ADC identity running Terraform must already be allowed to:

- enable services (`serviceusage.services.enable`), commonly through `roles/serviceusage.serviceUsageAdmin`;
- create/manage project service accounts, commonly through `roles/iam.serviceAccountAdmin`;
- create/update BigQuery datasets and their dataset IAM, commonly through `roles/bigquery.admin`;
- grant project-level `roles/bigquery.jobUser`, requiring project IAM policy update permission, commonly through `roles/resourcemanager.projectIamAdmin`.

These roles are Terraform operator prerequisites, not roles that this configuration grants to itself. They are broad; inherited access or a custom role with only the required permissions is preferable. Do not create service-account keys. If the current ADC user lacks a prerequisite, Terraform reports the denied API/permission; grant only the missing permission to the human operator through the project IAM console, then retry.

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

For future changes, review the saved plan before applying. The initial plan contained no GCS/Storage resources, no replacement and no deletion. The user authorized and this exact plan was applied. State remains local at `terraform.tfstate` (ignored by Git) and must be backed up; do not configure a GCS backend while raw files are staying local.

## Offline checks

From the repository root:

```powershell
terraform -chdir=infra/terraform fmt -check -recursive
terraform -chdir=infra/terraform validate
terraform -chdir=infra/terraform test
```

The native tests use a mocked provider and `command = plan`; they check policy without ADC or cloud writes. They do not prove the live user IAM, organization policy or API availability.
