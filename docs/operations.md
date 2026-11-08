# Setup and operations

No dashboard is published and no refresh is scheduled. Local commands operate on `LOCAL_DATA_DIR` (default `data`). Step 2 requires no cloud credentials. Live `run-plan` uses the same LL2 client, immutable raw response chain and quota ledger as earlier ingestion; planning, state inspection, export and rebuild are offline.

## Local setup and checks

Use the repository root with Python 3.12 and uv. Windows verification used Python 3.12.13 and uv 0.11.17. A first locked install requires registry access; use `--offline` only when dependencies are cached. Linux and Windows CI install the locked warehouse group and run the offline checks.

```powershell
uv sync --locked --group warehouse
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest -m "not integration and not live" --basetemp .pytest-tmp
uv run python -m launch_atlas doctor --offline
uv run python -m launch_atlas --help
uv build --wheel
```

These commands work in PowerShell and standard Linux shells. `.pytest-tmp` is reserved for test temporary files; keep project data elsewhere. Tests import the installed package, and default tests block external socket connections. Marked Dagster tests allow local loopback for Windows asyncio. The wheel exposes `launch_atlas` and the `launch-atlas` executable.

## Step 3 warehouse workflow

The optional `warehouse` group supplies BigQuery, dbt and Dagster. Raw JSON stays local; cloud loads send prepared Parquet directly to BigQuery. The latest source snapshot is SQLite revision 42. The production candidate remains ineligible while its historical plan is incomplete; scratch verification uses a separate, complete bounded fixture scope and never overrides production eligibility.

The integrated command runs the same dependency chain locally and in tests:

```powershell
uv sync --locked --group warehouse
uv run --locked --group warehouse --env-file .env.local python -m launch_atlas warehouse-run --data-dir data
```

This is an explicit cloud-write command: validated export → immutable snapshot → checksummed local-file loads → isolated dbt build/tests → reporting content validation. It creates invocation-specific model aliases in the seven-day scratch dataset and saves profiles, dbt logs/artifacts, candidate evidence and result/failure summaries under `data/warehouse/runs/<run-id>/`. Profiles contain environment expressions, not credential JSON. The new runner caps BigQuery/Python/dbt queries at 100,000,000 bytes each and runs dbt with one thread. Its cap is per job, not a project/monthly spending guarantee. Measure free-tier headroom before bounded cloud verification; never use trial credit beyond the authorized free limits.

Without `--publish`, no production reporting rows or pointer are created. The result reports `candidate_built`, row counts and actual release eligibility. To include live acquisition, explicitly supply `--refresh-plan-id <saved-refresh-plan>` and `--max-requests <1-8>`; an incomplete refresh stops the downstream chain. The shared LL2 ledger and raw/checkpoint rules still apply. No ingestion is performed when the flag is omitted.

After required history is complete and a candidate passes every check, first internal activation is:

```powershell
uv run --locked --group warehouse --env-file .env.local python -m launch_atlas warehouse-run --data-dir data --publish --expected-active none
```

For later activation, pass the actual previous release ID instead of `none`. The gate runs before retention. Each release retains nine marts and six dimension/bridge relations with `release_id`; stable views use one active pointer. Schema, row counts, snapshot scope, SHA-256 row-multiset digests and manifest identities are checked before activation. Candidate builds cannot overwrite retained rows. A stale writer or failed candidate preserves the current pointer. This is internal warehouse activation; Data Studio/site publication remains separate.

Rollback verifies the previously retained rows without rebuilding them:

```powershell
uv run --locked --group warehouse --env-file .env.local python -m launch_atlas warehouse-rollback --release-id <retained-release-id> --expected-active <current-release-id>
```

For recovery, inspect the run's `failure.json` and dbt logs, then fix the cause and run a new candidate. Deterministic source load jobs can be reused; new candidate aliases prevent concurrent model collisions. Do not manually move the active pointer or overwrite retained rows. Incompatible reporting schema changes require a reviewed storage/view migration; the current implementation fails closed rather than silently altering old releases.

The granular offline `state-export`, `input-snapshot` and `warehouse-prepare` commands and explicit `warehouse-load` remain available. Load-plan contract v3 records an optional source-dataset override for expiring scratch tests; normal loads still target `space_launch_atlas_source`. Prepared files use a short hash prefix plus a full-identity collision check to stay within Windows path limits. Raw manifests and bodies are verified locally, not uploaded.

Cloud integration is opt-in and uses the existing scratch dataset, local ADC, and seven-day expiration:

```powershell
$env:RUN_BIGQUERY_INTEGRATION = "1"
uv run --locked --group warehouse --env-file .env.local pytest tests/test_bigquery_releases.py tests/test_reporting_pipeline.py -m integration --basetemp .pytest-tmp -q
Remove-Item Env:RUN_BIGQUERY_INTEGRATION
```

The data-release test executes two complete, mocked-source 1957 histories through the actual load/dbt/retain/activate chain; all fixture source/model/reporting tables stay in scratch. It checks reader views, changed candidates, stale publication, failed dbt work, rollback and corrupted retained data. Its combined 15-view reader query uses a separate 250 MB cap because BigQuery's per-table billing minimum requires about 170 MiB; ordinary pipeline jobs keep the 100 MB cap. Default tests remain offline. dbt parse validates 31 models, 11 sources, 102 data tests and 6 unit tests with placeholders and no ADC.

The earlier Dagster stall was a sandbox restriction on Windows asyncio's local socket pair, not a warehouse or OAuth failure. Execution works in a normal terminal; a restricted test runner needs local-loopback access. Do not weaken Windows security settings or grant broader cloud scopes to solve it.

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
- `warehouse/prepared/<snapshot-hash-prefix>/tables/`: local Parquet copies tagged with the input snapshot.
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

Terraform installation, resource declarations, live deployment and offline mock checks are documented in [infra/README.md](../infra/README.md). Terraform was applied to the authorized project; its no-op refresh plan is clean. Two opt-in BigQuery integration tests verified release metadata and the actual load/dbt/retention/activation chain in expiring scratch, including stale/failed candidates, reader consistency, rollback and corruption rejection. The six-op Dagster job executes locally. The earlier Windows stall was caused by sandbox-blocked asyncio loopback; current execution evidence is in ROADMAP.md. No production reporting release is active.

Default tests are offline. Install `uv sync --locked --group warehouse` for all-code checks, then run Ruff lint/format, Pyright and `pytest --basetemp .pytest-tmp`. Linux and Windows CI have both passed these checks without cloud credentials. The base CLI runtime remains usable without the warehouse group.

Unattended scheduling remains unimplemented; the same local Dagster dependency chain already executes acquisition, snapshot, load, dbt/checks, retention and activation. Actual production activation remains gated by incomplete Step 2 history. Data Studio's no-cost edition requires a Google account and hosted report/data-source creation, not a server instance. The user selected their personal Google account as report owner and a weekly initial BigQuery warehouse refresh. The query credential must have only the required read access to the reporting dataset plus BigQuery Job User in the query project; verify the account's effective and inherited roles before using it for queries. A distinct Google identity is needed only if the personal account cannot meet that rule. Managed Workspace/Cloud Identity is required only for Data Studio's optional service-account mode. The inspected Data Studio home still requests API authorization; no report is connected. The console confirms an active trial; private expiry/cost observations remain in ignored evidence. No account upgrade, paid usage or GCS is authorized; continuing within free usage after the trial requires active billing or a compatible alternative. Research, measured build bytes and the weekly cadence estimate are recorded in ROADMAP.md.
