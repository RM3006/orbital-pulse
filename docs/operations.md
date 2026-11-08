# Setup and operations

Nothing is scheduled or published. Local commands operate on `LOCAL_DATA_DIR` (default `data`). Step 2 requires no cloud credentials. Live `run-plan` uses the same LL2 client, immutable raw response chain and quota ledger as earlier ingestion; planning, state inspection, export and rebuild are offline.

## Local setup and checks

Use the repository root with Python 3.12 and uv. Windows verification used Python 3.12.13 and uv 0.11.17. A first locked install requires registry access; use `--offline` only when dependencies are cached. Linux CI is authored but has not run remotely.

```powershell
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest -m "not integration and not live" --basetemp .pytest-tmp
uv run python -m launch_atlas doctor --offline
uv run python -m launch_atlas --help
uv build --wheel
```

These commands work in PowerShell and standard Linux shells. `.pytest-tmp` is reserved for test temporary files; keep project data elsewhere. Tests import the installed package, and default tests block socket connections. The wheel exposes `launch_atlas` and the `launch-atlas` executable.

## Step 3 warehouse workflow

The optional warehouse dependency group contains BigQuery, dbt and Dagster tooling. GCS is deliberately not part of the current design: raw JSON, manifests and prepared Parquet stay on the local filesystem. Terraform manages the existing project's BigQuery datasets/APIs/IAM; its state remains local.

The snapshot loaded into BigQuery was `sla-input-v1-0f7ad0d77cbf3fa2b34a410da0b274205128c643b5c8c1f3359b3ed04bfca9b5` at SQLite revision 27. Local Step 2 has since advanced to revision 35 and its Parquet export passes quality; that newer revision has not yet been pinned or loaded. To prepare a current snapshot and, if separately intended, load it from local files, use:

```powershell
uv sync --locked --group warehouse
uv run python -m launch_atlas state-export --data-dir data
uv run python -m launch_atlas input-snapshot --data-dir data
$snapshotId = Read-Host "Paste the input_snapshot_id printed by input-snapshot"
$projectLine = Get-Content .env.local | Where-Object { $_ -match '^GCP_PROJECT_ID=' } | Select-Object -First 1
$env:GCP_PROJECT_ID = ($projectLine -split '=', 2)[1].Trim().Trim('"')
$locationLine = Get-Content .env.local | Where-Object { $_ -match '^GCP_LOCATION=' } | Select-Object -First 1
$env:GCP_LOCATION = ($locationLine -split '=', 2)[1].Trim().Trim('"')
uv run --env-file .env.local python -m launch_atlas warehouse-prepare --snapshot-id $snapshotId --project-id $env:GCP_PROJECT_ID --location $env:GCP_LOCATION
uv run --env-file .env.local python -m launch_atlas warehouse-load --snapshot-id $snapshotId --project-id $env:GCP_PROJECT_ID --location $env:GCP_LOCATION
```

The prepare command is offline; `warehouse-load` is the explicit cloud-write boundary. It rechecks source evidence and local Parquet, uses deterministic BigQuery file-load jobs and explicit schemas, verifies row counts, then writes a receipt under `data/warehouse/plans/`. It sends no raw files to a cloud object store. The stored receipt and job verification for this snapshot are under `data/warehouse/` (ignored by Git).

Local dbt authentication uses Google Cloud CLI ADC in the Windows user profile. `.env.local` contains only the project/location identifiers; do not put tokens, passwords, OAuth JSON or service-account keys there. The private dbt profile is under ignored `data/dbt/profiles/`. For a scratch build, source data remains in `space_launch_atlas_source`, while all model layers target the expiring scratch dataset:

```powershell
$env:DBT_SOURCE_DATASET = "space_launch_atlas_source"
$env:DBT_TARGET_DATASET = "space_launch_atlas_scratch"
$env:DBT_STAGING_DATASET = "space_launch_atlas_scratch"
$env:DBT_CORE_DATASET = "space_launch_atlas_scratch"
$env:DBT_MARTS_DATASET = "space_launch_atlas_scratch"
$tablePrefix = "sla_0f7ad0d77cbf3fa2"
$dbtVars = "{input_snapshot_id: $snapshotId, source_table_prefix: $tablePrefix}"
uv run --env-file .env.local dbt debug --project-dir models --profiles-dir data/dbt/profiles
uv run --env-file .env.local dbt build --project-dir models --profiles-dir data/dbt/profiles --target-path data/warehouse/dbt/0f7ad0d77cbf3fa2/target --log-path data/warehouse/dbt/0f7ad0d77cbf3fa2/logs --vars $dbtVars --no-version-check
```

For the transactional release backend integration check, set `RUN_BIGQUERY_INTEGRATION=1` only when intentionally running the cloud test. It creates two uniquely named tables in `space_launch_atlas_scratch`, verifies publish/stale-writer/rollback behavior, and requires the configured scratch dataset's seven-day expiration. The test is skipped by default:

```powershell
$env:RUN_BIGQUERY_INTEGRATION = "1"
uv run --locked --offline --group warehouse --env-file .env.local pytest tests/test_bigquery_releases.py -m integration --basetemp=.pytest-tmp/release-test -q
Remove-Item Env:RUN_BIGQUERY_INTEGRATION
```

The latest verified run built 26 models and passed 80 dbt data tests plus one dbt unit test. A per-job `maximum_bytes_billed=1,000,000,000` cap is configured, but this is not a monthly/project cost ceiling. No reporting release was published: the snapshot still has `release_eligible=false` while Step 2 history is incomplete. Data Studio is not connected because the user requires a free-only project and there is no demonstrated hard per-query connector cap.

For offline-only dbt parsing with placeholders, copy `models/profiles.yml.example` into a temporary profiles directory and run `dbt parse`; this does not validate cloud authentication or execute SQL. Parse discovers 26 models, 11 sources and 80 data tests. Cloud model/test commands require the local ADC and the explicit real target/dataset routing shown above.

### Local dbt authentication with Google Cloud CLI ADC

The active local credential is Google Cloud CLI Application Default Credentials (ADC), stored outside the repository at `%APPDATA%\gcloud\application_default_credentials.json`. gcloud 592.0.0 login completed with the explicit scope `https://www.googleapis.com/auth/cloud-platform`; Cloud SQL was not selected. The user approved this broader OAuth grant after the BigQuery-only gcloud scope was rejected before login. This is an OAuth authorization scope, not a grant of every IAM role: actual access still depends on the signed-in account's IAM permissions. The project used BigQuery only in this workflow. The private dbt profile still declares BigQuery and caps billed query bytes at 1,000,000,000; this does not narrow the scope already present in the ADC grant.

The downloaded Google Cloud CLI archive was checksum-verified and extracted under ignored `.pytest-tmp\gcloud\`; it is not installed globally or on `PATH`. To authenticate again with this checkout's cached CLI, first ensure `.env.local` has `GCP_PROJECT_ID`, then run in PowerShell from the repository root:

```powershell
$env:CLOUDSDK_PYTHON = (Resolve-Path '.venv\Scripts\python.exe').Path
$env:CLOUDSDK_CONFIG = Join-Path $env:APPDATA 'gcloud'
$projectLine = Get-Content '.env.local' | Where-Object { $_ -match '^GCP_PROJECT_ID=' } | Select-Object -First 1
if (-not $projectLine) { throw 'GCP_PROJECT_ID is missing from .env.local' }
$projectId = ($projectLine -split '=', 2)[1].Trim().Trim('"')
if (-not $projectId) { throw 'GCP_PROJECT_ID is missing from .env.local' }
& '.pytest-tmp\gcloud\unpacked\google-cloud-sdk\bin\gcloud.cmd' auth application-default login --scopes=https://www.googleapis.com/auth/cloud-platform --project=$projectId
```

Do not set `GOOGLE_APPLICATION_CREDENTIALS`; leaving it unset lets Google libraries discover standard ADC. Do not copy ADC/client credentials into `.env.local`, the repository, logs or chat. The profile uses `method: oauth`; for connection verification, run `uv run --env-file .env.local --group warehouse dbt debug --project-dir models --profiles-dir data/dbt/profiles`. The standard ADC path was discovered automatically in the successful final check. An unattended runner needs a separate identity design.

Verified 2026-11-08: login succeeded in an isolated gcloud configuration, the staged credential passed `dbt debug`, and the promoted standard ADC passed `dbt debug` again without a credential-path override. No dbt model SQL, warehouse quality test or non-BigQuery GCP API was run as part of ADC setup. The setup created no cloud resource. Google documents that gcloud user ADC is distinct from CLI command credentials and that local user ADC uses the broad `cloud-platform` scope by default; see [ADC behavior](https://docs.cloud.google.com/docs/authentication/application-default-credentials), [local ADC setup](https://docs.cloud.google.com/docs/authentication/set-up-adc-local-dev-environment?hl=en) and [gcloud ADC login options](https://docs.cloud.google.com/sdk/gcloud/reference/auth/application-default/login).

The earlier dedicated Desktop OAuth client/helper is a narrower but inactive fallback. Its app is still in Testing and its BigQuery-only refresh token is subject to the seven-day Testing limit; those rules do not describe the current gcloud ADC. Public branding/domain setup remains deferred because it is unnecessary for this personal local workflow. See [Desktop OAuth](https://developers.google.com/identity/protocols/oauth2/native-app) and [refresh-token rules](https://developers.google.com/identity/protocols/oauth2/#expiration).

### First bounded BigQuery Console verification (2026-11-07)

Using the signed-in BigQuery Console in the configured project, four datasets (`space_launch_atlas_source`, `space_launch_atlas_staging`, `space_launch_atlas_core`, `space_launch_atlas_marts`) were created in `europe-west1`. Eleven Parquet files from snapshot `sla-input-v1-dea2e86fe3ca85b9918b6587e3658325f7084646426ac84b1dbdb87e8a90a04a` were uploaded into the source dataset, with names prefixed `sla_dea2e86fe3ca85b9918b6587_`. The local input files total 224,815 bytes. A BigQuery `COUNT(*)` query matched local row counts: bridge configuration/family 138; launch site 28; pad 77; provider 42; vehicle configuration 117; vehicle family 50; launch attempts 290; upcoming schedule 179; observations 598; current launch records 577; stage flights 73. These rows have different grains and must not be added as a launch total.

The dataset DDL and initial scripted count job showed 0 bytes processed; the final single-SELECT result was successful, but its byte figure and individual load job IDs were not captured. The UI used Parquet schemas and bypassed the Python load adapter, deterministic job IDs and receipt checks. No GCS archive, dbt run/test, scratch quality suite or release was created. At that point local ADC was unresolved; the subsequent CLI ADC setup is recorded above.

`.env.example` lists actual nonsecret settings, but the CLI does not automatically load `.env` files. Set environment variables explicitly, e.g. `$env:LOCAL_DATA_DIR = './data'` in PowerShell. Only local mode and the verified LL2 production source are accepted; no token is read. Supply a descriptive User-Agent. Credentials, raw/state and caches stay out of Git.

## Offline demo and bounded live access

```powershell
uv run python -m launch_atlas import-raw --input tests/fixtures/ll2_history_1957.json --observed-at 2026-11-01T17:04:49.556708+00:00 --orbital-only --run-id demo-1957
uv run python -m launch_atlas replay --run-id demo-1957
```

The fixture has three 1957 attempts (two successes, one failure). `--orbital-only` asserts source-query provenance; it does not make unknown scope a qualifying attempt. Import refuses an existing ID: replay it instead. Raw is under `data/raw/ll2/demo-1957/`; ten per-run Parquet tables and quality are under `data/normalized/demo-1957/`. This small slice is not a worldwide reliability estimate.

Explicit live examples, with retries consuming the same request budget:

```powershell
uv run python -m launch_atlas smoke --source ll2 --max-requests 2
uv run python -m launch_atlas backfill --start-year 1957 --end-year 1957 --page-size 100 --max-requests 2 --run-id history-example
uv run python -m launch_atlas backfill --resume --run-id history-example --max-requests 2
```

Smoke uses five detailed records per page by default. History/recent/upcoming source windows and page size are frozen in each run; resume does not replace them with new arguments. Completed resume uses no network. General explicit runs are capped at eight requests; the shared ledger is capped at 15/hour and provider refusal overrides it. Large detailed pages can time out. Exit codes: 0 completed/successful inspection; 2 safely incomplete/checkpointed; 1 failure. A partial run passing row checks is still incomplete.

## Data layout

- `raw/ll2/<run-id>/manifest.json`: query provenance, schema/parser versions, accepted pages, response attempts, request timing/status, checkpoint URL, completeness, plan/window metadata and validation report.
- `raw/ll2/<run-id>/response-*.json.gz`: immutable compressed source bytes; SHA-256 is over the decompressed response.
- `state/launch_atlas.sqlite`: acquisition/current state, semantic versions, sightings/events/conflicts, frozen plans/windows, per-window cursors, source request evidence and latest complete refresh horizon.
- `quota.json`: shared local rolling request reservations and provider retry-after state. A failed transport still consumes a reservation.
- `exports/generations/<revision-id>/`: immutable Parquet candidate, quality and manifest. `exports/active.json` points to the last validated generation.
- `normalized/<run-id>/`: legacy/per-run replay outputs; not the cross-run state export.
- `.writer.lock`: exclusive local writer lock. A stale lock must be checked against its PID before removing that file only.
- `warehouse/prepared/<snapshot-id>/tables/`: local Parquet copies tagged with the input snapshot.
- `warehouse/plans/<plan-hash-prefix>/load-plan.json`: deterministic, offline local-file-to-BigQuery load plan. No GCS upload is performed. A load receipt is written only after the explicit BigQuery loader confirms destination row counts.

Do not delete raw evidence, SQLite state or quota history as routine recovery. Raw runs can reproduce current state offline. Keep backups together if moving the data root.

## Step 2 commands

```powershell
# Create immutable plans before the first request
uv run python -m launch_atlas plan-refresh --plan-id refresh-2026-11-07
uv run python -m launch_atlas plan-backfill --plan-id history-1957-2026 --start-year 1957 --end-year 2026

# Execute/resume within this invocation's ceiling and shared rolling ledger
uv run python -m launch_atlas run-plan --plan-id refresh-2026-11-07 --max-requests 8
uv run python -m launch_atlas run-plan --plan-id history-1957-2026 --max-requests 8

# Inspect local progress and materialize a validated generation
uv run python -m launch_atlas state-status
uv run python -m launch_atlas state-export

# Offline replay/reconstruction to an empty, separate output directory
uv run python -m launch_atlas rebuild-state --output-dir data/rebuild-check
```

Use new unique plan IDs for new frozen windows; a saved plan's bounds, filters, parser/policy versions and page size do not change on resume. `plan-refresh` freezes recent, upcoming and broad correction windows with their 30-day/90-day/48-hour policy. `plan-backfill` creates two annual passes. Dense historical windows can be replaced with monthly children using the configured source-count threshold. `plan-reconcile` creates another pass for selected unstable windows. Do not report parent completion while children remain pending or pass content hashes disagree.

Run at most the available shared quota allowance. LL2 documents a shared free request limit; this local ledger cannot see other consumers on the same IP. A `budget_stopped`, `retry_wait`, `throttled` or `quota_stopped` window remains incomplete and safely resumable. Respect `Retry-After`; do not create a second data root to bypass the shared quota ledger. Calls/retries are bounded by the invocation budget and the provider response.

`state-merge --run-id ...` explicitly applies one complete validated run to SQLite. `state-export` writes a new generation and validates file checksums before replacing the active pointer. If a candidate fails, the prior active generation remains selected. `rebuild-state` uses completed `kind=live` runs only; add `--include-run-id <id>` to explicitly include an offline import. It refuses to replace a nonempty target and does not call the network. Rebuild raw artifact provenance and run manifests remain local.

## Data and quality rules

The launch attempt fact is launch grain; upcoming schedule is one row per launch ID per frozen horizon. Stages, payloads and family bridge rows must never multiply launch counts. Upcoming records retain source precision; supported precision maps to half-open UTC intervals and is selected by overlap with the frozen 90-day horizon. Unsupported intervals and unknown orbital scope remain visible in coverage. Unknown mission/orbit scope is excluded from orbital attempt metrics, not inferred from names or counters.

Each complete source window means its saved query finished, not that LL2 is globally complete. Offset pagination can move under the extraction; two-pass comparison and bounded corrections reduce the risk but do not establish a provider snapshot. Track pending, split, unreconciled and conflicting windows separately. Never merge launch success, landing success, schedule movement or data coverage into a composite score.

Transient transport/500/502/503/504 failures receive at most three attempts within quota. Redirects and unsafe pagination are rejected. Raw errors are retained but never normalized as empty successful data. Fix validation or parser issues and replay from saved raw. A request accepted before a crash still counts against the reservation ledger.

Retention stays manual: retain raw evidence, source request provenance, current SQLite state and observation history. No pruning task or cloud copy exists. Verify available disk before large backfill batches; stop safely if storage becomes insufficient.

## Checks and future stages

Terraform installation, resource declarations, live deployment and offline mock checks are documented in [infra/README.md](../infra/README.md). Terraform was applied to the authorized project; its no-op refresh plan is clean. The BigQuery release backend has passed an opt-in scratch integration test for publish, compare-and-swap, stale-writer rejection and rollback. No production reporting store or active release exists. A local Dagster graph composes snapshot and load-plan preparation; definition checks pass, but execution did not progress to its first step on Windows, including for a trivial job, so no Dagster execution is claimed.

Default tests are offline. Run `uv sync --locked`, `ruff check`, `ruff format --check`, `pyright` and `pytest --basetemp .pytest-tmp` before further source calls. Remote CI is configured but has not been verified on GitHub/Linux.

Production orchestration remains proposed: one Dagster chain for ingest/load/dbt/check/publish, invoked by scheduled GitHub Actions. The local snapshot→plan graph is authored but not executable in this Windows environment. Terraform and the BigQuery source load have been applied; dbt models/tests and the transactional release backend have been verified in scratch. Actual release publication remains gated because the snapshot is not eligible while Step 2 history is incomplete. Data Studio remains disconnected pending a free-only query-cost plan. Do not upgrade to a paid account, exceed the free tier or create GCS resources. The no-billing sandbox still omits DML; post-trial architecture remains unresolved.
