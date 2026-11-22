# Setup and operations

The local LL2 worker and guarded local BigQuery refresh check are configured but currently disabled. The five-page Data Studio report remains private and has no scheduled refresh. Local commands operate on `LOCAL_DATA_DIR` (default `data`). Step 2 requires no cloud credentials. Live `run-plan` uses the same LL2 client, immutable raw response chain and quota ledger as earlier ingestion; planning, state inspection, export and rebuild are offline.

## Local setup and checks

Use the repository root with Python 3.12 and uv. Windows verification used Python 3.12.13 and uv 0.11.17. A first locked install requires registry access; use `--offline` only when dependencies are cached. Linux and Windows CI install the locked warehouse group and run the offline checks.

```powershell
uv sync --locked --group warehouse
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run python -m pytest -m "not integration and not live" --basetemp .pytest-tmp
uv run python -m launch_atlas doctor --offline
uv run python -m launch_atlas --help
uv build --wheel
```

These commands work in PowerShell and standard Linux shells. `.pytest-tmp` is reserved for test temporary files; keep project data elsewhere. Tests import the installed package, and default tests block external socket connections. Marked Dagster tests allow local loopback for Windows asyncio. The wheel exposes `launch_atlas` and the `launch-atlas` executable.

## Automatic local LL2 acquisition

Install or inspect the current-user Windows task from the checkout. When `.env.local` contains both `GCP_PROJECT_ID` and `GCP_LOCATION`, installation passes those non-secret identifiers and enables exact-response archival to the existing BigQuery table using the user's local ADC:

```powershell
.\scripts\worker.ps1 -Action Install -HistoryPlanId p2-history-1957-2026
.\scripts\worker.ps1 -Action Status
.\scripts\worker.ps1 -Action Stop
.\scripts\worker.ps1 -Action Resume -Reason "Investigated the failure and verified the cause is resolved"
.\scripts\worker.ps1 -Action Uninstall
```

The `OrbitalPulse-LL2` task launches the existing venv’s `pythonw.exe` without a console window. When enabled, it checks every five minutes and at user logon, but performs network work only when due. It uses an interactive, limited user token, stores no password and needs this PC awake with the user logged in. Missed work resumes after the next available trigger; it does not wake the computer. This is a local acquisition scheduler, not a GitHub-hosted or cloud scheduler. Both local Task Scheduler registrations were disabled on 2026-11-14 at the user’s request; no local worker process was running after the manual test.

Each tick runs at most eight requests through the existing `run_plan` dependency chain; the shared `data/quota.json` still limits all callers to 15 reservations per rolling hour, including retries. Quota and `Retry-After` waits survive restarts. Three consecutive transport/HTTP/page-envelope failures across invocations latch a persistent circuit; a valid page resets the streak. Schema failures, conflicts, differing historical passes and replay/checksum failures also block the worker for investigation. Inspect `data/worker/state.json`, its last batch and the referenced raw manifests before resuming. Resume records a reason and clears the failure circuit, preserving quota reservations and server waits. Stop is honored at the end of the current bounded invocation; uninstall removes only this checkout's task and preserves data.

The worker resumes the bound frozen history plan, then immediately creates a catch-up refresh. It verifies a full raw rebuild against all 11 active Parquet file hashes/row counts and semantic state counts before switching to weekly operation. Empty source runs are verified by the input snapshot; rebuild does not count those as semantic acquisitions. Subsequent refreshes run seven days after the prior successful refresh tick, resuming the same frozen plan after interruption. Recent 30-day launches, the upcoming 90-day horizon and updates since the last accepted correction watermark (with 48-hour overlap) are merged and validated; they do not rescan every historical year. A verified input snapshot is saved for the existing warehouse chain. With `--archive-raw-to-bigquery`, each HTTP response is archived before parsing; this only writes the separate raw-response table. The worker does not load normalized source tables, run dbt, query the warehouse or activate a reporting release.

Worker state, heartbeat, batch results, audit output and snapshot handoff remain under ignored `data/worker/`. The independent kernel lock prevents overlapping scheduled ticks and manual `run-plan` CLI commands, and releases when a process dies. A leftover `.writer.lock` is recovered only if its PID is verifiably dead; a live or inaccessible PID is left alone. Pause the worker before manual state repairs. Direct CLI controls are `worker-init`, `worker-tick`, `worker-status`, `worker-stop` and `worker-resume`; status is offline. Deleting the worker state or using another data root is not a quota-reset mechanism.

The raw archive is `space_launch_atlas_source.ll2_raw_response_pages`. The initial load contains 304 historical live response rows and was reconciled against local body checksums. Two Cloud Run tests added 11 rows; the 2026-11-14 local refresh added seven, bringing the table to 322. The seven new BYTES bodies were read back and matched local SHA-256. For a manual replay, use `uv run --locked --group warehouse --env-file .env.local python -m launch_atlas warehouse-raw-archive --all-runs --data-dir data`. The write path requires the table to exist and uses deterministic BigQuery load-job IDs; repeated uploads are idempotent. Keep local raw files and SQLite state. The current BigQuery table does not yet provide a worker-state restore path.

To repeat the hosted checkpoint proof, first confirm the BigQuery scratch region, free-tier scope and available usage headroom. The opt-in test uses recorded LL2 fixtures, capped DML, and two expiring scratch tables that it deletes on exit:

```powershell
$env:RUN_BIGQUERY_INTEGRATION = "1"
uv run --locked --group warehouse --env-file .env.local python -m pytest -m integration -s tests/test_worker_checkpoint.py --basetemp data/pc
```

The test deliberately leaves a simulated interrupted claim locked until the scratch tables are deleted. A committed checkpoint can be resumed in a new directory; a missing commit must be investigated before a replacement may contact LL2. The installed worker still uses its local data directory.

## Step 3 warehouse workflow

The optional `warehouse` group supplies BigQuery, dbt and Dagster. Cloud loads send prepared Parquet directly from local disk for the 11 typed source relations; the separate raw JSON archive is already in BigQuery. Raw archival does not normalize records or replace the Parquet load. Production release `sla-release-v1-46fb47e6c45d91744562b749` is active from the revision-220 data frozen on 2026-11-14. The original candidate passed 108 dbt tests and 15 reporting-relation checks; the 2026-11-15 publishing run re-pinned the same Parquet file hashes and dbt model sources and activated that data. The snapshot ID changed because repository-commit and generated-export metadata changed. The private report remains bound to stable production views, but its cache was not manually refreshed after activation. The old revision-95 scratch preview expires on 2026-11-16 and is not used by the report.

The integrated command runs the same dependency chain locally and in tests:

```powershell
uv sync --locked --group warehouse
uv run --locked --group warehouse --env-file .env.local python -m launch_atlas warehouse-run --data-dir data
```

This is an explicit cloud-write command: validated export → immutable snapshot → checksummed local-file loads → isolated dbt build/tests → reporting content validation. It creates invocation-specific model aliases in the seven-day scratch dataset and saves profiles, dbt logs/artifacts, candidate evidence and result/failure summaries under `data/warehouse/runs/<run-id>/`. Profiles contain environment expressions, not credential JSON. The runner caps BigQuery/Python/dbt queries at 100,000,000 bytes each and runs dbt with one thread. Its cap is per job, not a project/monthly spending guarantee. The daily local scheduler applies a project usage reserve check and requires the account-scope confirmation before a release; the user confirmed on 2026-11-11 that no other project uses this billing account. Data Studio queries are not capped by this runner.

### Guarded local warehouse schedule

`OrbitalPulse-Warehouse` was configured to run as the current interactive Windows user at 15:00 local time. Its readiness checks include worker status, accepted snapshot fingerprints, project query usage, storage and the free-tier scope setting. Both local Windows task registrations were disabled on 2026-11-14 at the user’s request, so the daily check is not currently scheduled.

**Budget scope:** Google's 1 TiB query and 10 GiB storage free allowances are per billing account, while this guard reads jobs and current table sizes for the configured project only. The user confirmed no other project uses this billing account, so this project is the current usage scope. If that changes, do not rely on the project-only check; an account-wide usage source is needed before unattended publishing. The storage threshold is a current table-size snapshot, not an account-level monthly-average calculation.

```powershell
.\scripts\warehouse-weekly.ps1 -Action Status
.\scripts\warehouse-weekly.ps1 -Action DryRun
.\scripts\warehouse-weekly.ps1 -Action Run
.\scripts\warehouse-weekly.ps1 -Action Install
```

`Install` does not run the task immediately. `Run` executes the same guard now; daily runs use the project venv's `pythonw.exe`. The task uses no stored password and requires the PC to be awake and the user signed in. Logs are under ignored `data/warehouse/schedule/`. It creates no GCP scheduler or compute resource. A successful release's actual job bytes and the project preflight are written to the local log. The report owner can trigger a report data refresh with **View → Refresh data**; the report currently has no recurring refresh schedule. Its BigQuery queries contribute to project usage and are outside the scheduler's per-job cap.

The candidate gate compares each of the 15 reporting relation row counts to the active retained release. A difference over 10% of the previous count or five rows, whichever is larger, is written to `candidate-evidence.json` and blocks publication. The first release has no active baseline, so it relies on the nonempty, schema, source-scope and dbt checks. A candidate-only run reports drift for review. A publishing run fails before retention or pointer activation when drift exceeds the tolerance.

Without `--publish`, no production reporting rows or pointer are created. The result reports `candidate_built`, row counts and actual release eligibility. To include live acquisition, explicitly supply `--refresh-plan-id <saved-refresh-plan>` and `--max-requests <1-8>`; an incomplete refresh stops the downstream chain. The shared LL2 ledger and raw/checkpoint rules still apply. No ingestion is performed when the flag is omitted.

For a new production namespace, after required history is complete and a candidate passes every check, first activation uses:

```powershell
uv run --locked --group warehouse --env-file .env.local python -m launch_atlas warehouse-run --data-dir data --publish --expected-active none
```

This first full-history production activation has already run. The current release is `sla-release-v1-46fb47e6c45d91744562b749`; do not repeat the `none` command against the existing namespace. For later activation, pass the actual previous release ID as `--expected-active`. The gate runs before retention. Each release retains nine marts and six dimension/bridge relations with `release_id`; stable views use one active pointer. Schema, row counts, snapshot scope, SHA-256 row-multiset digests and manifest identities are checked before activation. Candidate builds cannot overwrite retained rows. A stale writer or failed candidate preserves the current pointer. This is internal warehouse activation; Data Studio/site publication remains separate.

Rollback verifies the previously retained rows without rebuilding them:

```powershell
uv run --locked --group warehouse --env-file .env.local python -m launch_atlas warehouse-rollback --release-id <retained-release-id> --expected-active <current-release-id>
```

For recovery, inspect the run's `failure.json` and dbt logs, then fix the cause and run a new candidate. Deterministic source load jobs can be reused; new candidate aliases prevent concurrent model collisions. Do not manually move the active pointer or overwrite retained rows. Incompatible reporting schema changes require a reviewed storage/view migration; the current implementation fails closed rather than silently altering old releases.

### Internal preview procedure (historical)

This procedure created the separate revision-95 preview while history was incomplete. Its scratch tables expire on 2026-11-16. For a future bounded preview, first check project query and storage headroom and confirm the scratch dataset has its seven-day table expiry. Run:

```powershell
uv run --locked --group warehouse --env-file .env.local python -m launch_atlas warehouse-run --data-dir data --publish --preview --expected-active none
```

This command holds the worker lock while it verifies and pins accepted local inputs. If the lock is busy, wait for the bounded worker tick to finish. The source loads, dbt models, retained tables, views and active pointer all live in scratch. The 15 `preview_` reporting views expose `reporting_preview=true` and the coverage mart shows incomplete history. Review the pinned snapshot, load receipt, test results and release manifest in the run artifacts. The production activation command still requires a complete history snapshot.

## Step 4 weekly warehouse operation

The local worker owns LL2 acquisition and writes accepted state, raw evidence, quota ledger and Parquet exports under `data/`. The initial history backfill and raw replay audit are complete. The 2026-11-11 scheduled worker catch-up reached revision 217 with 7,683 launches, 7,718 observation events, 15,861 sightings and zero conflicts. A separate manual refresh on 2026-11-14 made seven HTTP 200 calls across recent, upcoming and correction windows; all windows committed with zero conflicts, advancing accepted SQLite state to revision 220 (7,686 launches, 7,739 observation events, 16,151 sightings). On 2026-11-15, the worker checkpoint was reconciled to that accepted state and the verified revision-220 export and snapshot. It remains `waiting_weekly`, with its next action set to 2026-11-21 11:15 UTC, seven days after the manual refresh completed. Both local Task Scheduler jobs are disabled. The local Python worker does not run dbt or refresh Data Studio.

The `OrbitalPulse-Warehouse` task was configured to check daily at 15:00 local time, but was disabled with the LL2 task on 2026-11-14. Before disabling, its script checked project monthly query usage and table size against 20 GiB and 1 GiB reserves after confirming the account-scope setting. On 2026-11-14 a read-only preflight observed 31,729,909,760 billed query bytes and 608,837,658 stored bytes before the candidate run; the user had confirmed no other project uses this billing account.

### Manual end-to-end pipeline test (2026-11-14)

The manual plan `manual-e2e-20261114` made seven LL2 requests (all HTTP 200), archived each exact response to BigQuery before parsing and completed all three windows. The subsequent candidate-only `warehouse-run` loaded the 11 typed Parquet source relations, built dbt models in scratch, passed 108 dbt tests and validated all 15 reporting relations. The dbt run results record 1,468,006,400 billed bytes across 139 jobs. The warehouse result records another 335,544,320 billed bytes across 32 validation queries. Together those artifacts record 1,803,550,720 billed bytes. A separate read-only audit retrieved the seven `BYTES` bodies and matched their SHA-256 hashes at 439,353,344 billed bytes under a one-off 500 MB cap. The current production release was read back unchanged; no `--publish` flag was used. Artifacts are in `data/warehouse/runs/4710266eddbc46ae96fcf3b647ad214d/` and the local raw manifests under `data/raw/ll2/manual-e2e-20261114-*/`.

The first warehouse attempt used `.venv` directly and stopped because the BigQuery adapter was missing from that invocation. `uv sync --offline --locked --group warehouse --inexact` synchronized the locked optional dependencies from cache; retrying with `uv run --offline --no-sync --locked --group warehouse --env-file .env.local` completed successfully. No repository code was changed for this run.

### Production activation of the 2026-11-14 data (2026-11-15)

The preflight read 43,714,084,864 project query bytes and 621,539,502 stored bytes with the single-project free-tier scope confirmed. The publishing run re-used the same revision-220 Parquet source files and unchanged dbt model files as the candidate-only run, with the frozen analysis time still 2026-11-14T11:12:19.119125Z. A new content-addressed snapshot ID, `sla-input-v1-1b061c90a13d0a376e51f50226828cdfb2112e1e584c8fc1388eac13b3c60f9c`, was created because the repository commit and generated-export metadata had changed since the original candidate snapshot `sla-input-v1-4d3caff1394ef1846b482c769c4cd60ebc32bd6756a8281e22eb64980a6ebba4`. All 108 dbt tests and 15 reporting-relation checks passed. The run activated `sla-release-v1-46fb47e6c45d91744562b749`, replacing `sla-release-v1-d4f4ae640e0934f11d7ad4c4`; the active pointer and snapshot were read back. Run artifacts are in `data/warehouse/runs/157275cce96f45e1ac681c2bb733376b/`. Billing recorded 1,468,006,400 bytes across 139 dbt jobs and 683,671,552 bytes across 94 warehouse queries, totaling 2,151,677,952 bytes. The private Looker Studio report remains bound to stable production views but was not manually refreshed after activation; it has no recurring refresh schedule.

### One-off Cloud Run worker validation (2026-11-11)

Terraform deployed `sla-ll2-worker` with one task, 1 vCPU, 4 GiB, a 600-second timeout and zero retries. The job has no schedule. Its first execution (`sla-ll2-worker-c5qsb`, 18:00:32–18:04:20 UTC) issued four LL2 requests: three HTTP 200 and one HTTP 502 with `Retry-After: 60`. Those four bodies were archived directly to BigQuery and their hashes matched. That worker checkpoint paused in `waiting_quota` and did not finish the refresh.

Later, the user requested one LL2 request. The second Cloud Run execution (`sla-ll2-worker-9hz4h`) was launched with `WORKER_MAX_REQUESTS=1`, but a hard-coded override in `tick_worker` reset the cap to eight. It actually made seven LL2 requests, all HTTP 200. A read-only BigQuery query found seven new unique response IDs and matched SHA-256 for all seven bodies; the query processed 10 MiB. The isolated cloud copy reached `waiting_weekly`, revision 220. It did not change local SQLite or the local worker state. The launcher detected `requests_this_invocation=7` instead of the requested 1 and exited with an error; its temporary checkpoint is left to its two-hour table expiry.

I corrected the local code so `tick_worker` takes an explicit per-tick request limit, and added an offline regression test. That fix is not yet in the deployed image. Do not trigger the current Cloud Run image again: it still ignores the one-request override. The seven exact request timestamps from the second run are now recorded in `data/quota.json`; at the time of reconciliation, seven requests remained in the rolling hour. The earlier four requests were already outside that window.

The two 2026-11-11 Cloud Run executions prove direct raw-response archival, not a durable hosted worker or scheduled service. At that time the remaining free-tier headroom and EU outbound LL2 transfer pricing were not verified, so zero cost was not established for those executions. The 2026-11-14 read-only project preflight subsequently confirmed query/storage usage within the user’s stated monthly BigQuery allowances; it does not resolve Cloud Run egress pricing or future billing continuity. There is no Cloud Scheduler or persistent cloud checkpoint bucket.

For each manual run, pause local acquisition for the duration of the warehouse snapshot/build so SQLite and Parquet cannot change during pinning:

```powershell
.\scripts\worker.ps1 -Action Stop
.\scripts\worker.ps1 -Action Status
uv run --locked --group warehouse --env-file .env.local python -m launch_atlas warehouse-run --data-dir data
```

Confirm the worker status is stopped before starting the warehouse command. In the resulting `data/warehouse/runs/<run-id>/`, inspect `result.json`, `candidate-evidence.json` and the recorded query job bytes. Confirm the pinned snapshot is complete, dbt tests passed, all 15 relation counts are present and candidate validation passed. If any check fails, inspect `failure.json` and `logs/dbt.log`; the current active release remains selected.

Candidate-only preview and later activation are two separate warehouse runs, so include both in the monthly query budget. The first full-history production release has already been activated. For a later release, first confirm free-tier headroom and use the previous active release ID:

```powershell
uv run --locked --group warehouse --env-file .env.local python -m launch_atlas warehouse-run --data-dir data --publish --expected-active sla-release-v1-46fb47e6c45d91744562b749
```

For a later weekly release, use the active release ID from the latest successful result as `--expected-active <release-id>`. A routine production refresh can use one publishing invocation after the monthly-headroom check because the command itself runs candidate validation before activation. Record its run directory and billed bytes. Resume acquisition after the warehouse run only when weekly local source updates should restart:

```powershell
.\scripts\worker.ps1 -Action Resume -Reason "Warehouse run finished; local state is ready to continue"
```

The worker's seven-day local refresh interval and the weekly warehouse build are separate schedules. The warehouse command does not trigger the worker's next update. The local worker appends raw response bodies to BigQuery before parsing; it does not run the warehouse build.

The granular offline `state-export`, `input-snapshot` and `warehouse-prepare` commands and explicit `warehouse-load` remain available. Load-plan contract v3 records an optional source-dataset override for expiring scratch tests; normal loads still target `space_launch_atlas_source`. Prepared files use a short hash prefix plus a full-identity collision check to stay within Windows path limits. Raw manifests and bodies are still verified from local files for replay and warehouse snapshot integrity; the separate raw-response archive contains their exact HTTP response bodies in BigQuery.

Cloud integration is opt-in and uses the existing scratch dataset, local ADC, and seven-day expiration:

```powershell
$env:RUN_BIGQUERY_INTEGRATION = "1"
uv run --locked --group warehouse --env-file .env.local python -m pytest tests/test_bigquery_releases.py tests/test_reporting_pipeline.py -m integration --basetemp .pytest-tmp -q
Remove-Item Env:RUN_BIGQUERY_INTEGRATION
```

The data-release test executes two complete, mocked-source 1957 histories through the actual load/dbt/retain/activate chain; all fixture source/model/reporting tables stay in scratch. It checks reader views, changed candidates, stale publication, failed dbt work, rollback and corrupted retained data. Its combined 15-view reader query uses a separate 250 MB cap because BigQuery's per-table billing minimum requires about 170 MiB; ordinary pipeline jobs keep the 100 MB cap. Default tests remain offline. dbt parse validates 31 models, 11 sources, 102 data tests and 6 unit tests with placeholders and no ADC.

The earlier Dagster stall was a sandbox restriction on Windows asyncio's local socket pair, not a warehouse or OAuth failure. Execution works in a normal terminal; a restricted test runner needs local-loopback access. Do not weaken Windows security settings or grant broader cloud scopes to solve it.

### Local dbt authentication with Google Cloud CLI ADC

The active local credential is Google Cloud CLI Application Default Credentials (ADC), stored outside the repository at `%APPDATA%\gcloud\application_default_credentials.json`. Google Cloud CLI 593.0.0 is installed per-user under `%LOCALAPPDATA%\GoogleCloudSDK\google-cloud-sdk`. ADC login completed on 2026-11-11 with the explicit scope `https://www.googleapis.com/auth/cloud-platform`; Cloud SQL was not selected. The user approved this broader OAuth grant after the BigQuery-only gcloud scope was rejected before login. This scope is not a grant of every IAM role: actual access still depends on the signed-in account's IAM permissions. This workflow used BigQuery only. The private dbt profile still declares BigQuery and caps billed query bytes at 1,000,000,000; this does not narrow the scope in the ADC grant. This local user credential is not an unattended runner identity.

To authenticate again, ensure `.env.local` has `GCP_PROJECT_ID`, then run in PowerShell from the repository root:

```powershell
$projectLine = Get-Content '.env.local' | Where-Object { $_ -match '^GCP_PROJECT_ID=' } | Select-Object -First 1
if (-not $projectLine) { throw 'GCP_PROJECT_ID is missing from .env.local' }
$projectId = ($projectLine -split '=', 2)[1].Trim().Trim('"')
if (-not $projectId) { throw 'GCP_PROJECT_ID is missing from .env.local' }
& (Join-Path $env:LOCALAPPDATA 'GoogleCloudSDK\google-cloud-sdk\bin\gcloud.cmd') auth application-default login --scopes=https://www.googleapis.com/auth/cloud-platform --project=$projectId --billing-project=$projectId
```

Do not set `GOOGLE_APPLICATION_CREDENTIALS`; leaving it unset lets Google libraries discover standard ADC. Do not copy ADC/client credentials into `.env.local`, the repository, logs or chat. The profile uses `method: oauth`; for connection verification, run `uv run --env-file .env.local --group warehouse dbt debug --project-dir models --profiles-dir data/dbt/profiles`. The standard ADC path was discovered automatically in the successful final check. An unattended runner needs a separate identity design.

Verified 2026-11-11: the standard ADC file exists at the documented Windows location and the guarded full-history warehouse run authenticated successfully, passed all 139 enabled dbt models/tests and published a release. The scheduler sets `GOOGLE_CLOUD_PROJECT` and `GCLOUD_PROJECT` from the nonsecret `.env.local` project setting because the user ADC itself has no default project. No credential JSON or token is stored in this repository. Google documents that gcloud user ADC is distinct from CLI command credentials and that local user ADC uses the broad `cloud-platform` scope by default; see [ADC behavior](https://docs.cloud.google.com/docs/authentication/application-default-credentials), [local ADC setup](https://docs.cloud.google.com/docs/authentication/set-up-adc-local-dev-environment?hl=en) and [gcloud ADC login options](https://docs.cloud.google.com/sdk/gcloud/reference/auth/application-default/login).

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
- `worker/superseded-replay-manifests.json`: manifest variants retained from older verification replays after their duplicate replay directories were removed. Canonical raw responses remain under `raw/`.
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

Local retention stays manual: preserve raw evidence, source request provenance, current SQLite state and observation history. Exact LL2 response bodies also have a BigQuery archive, but there is no persistent GCS archive or durable hosted worker-state store, and no pruning task is configured. Verify available disk before large backfill batches; stop safely if storage becomes insufficient.

## Checks and future stages

Terraform installation, resource declarations, live deployment and offline mock checks are documented in [infra/README.md](../infra/README.md). Terraform was applied to the authorized project; its last recorded refresh plan was clean. Two opt-in BigQuery integration tests verified release metadata and the actual load/dbt/retention/activation chain in expiring scratch, including stale/failed candidates, reader consistency, rollback and corruption rejection. The six-op Dagster job executes locally. The first full-history production release was published; its run evidence is summarized in ROADMAP.md. The earlier Windows stall was caused by sandbox-blocked asyncio loopback; see ROADMAP.md for current execution evidence.

Default tests are offline. Install `uv sync --locked --group warehouse` for all-code checks, then run Ruff lint/format, Pyright and `pytest --basetemp .pytest-tmp`. Linux and Windows CI have both passed these checks without cloud credentials. The base CLI runtime remains usable without the warehouse group.

The LL2 acquisition worker and guarded warehouse task were installed and reported Ready earlier, but both Windows Task Scheduler registrations were disabled on 2026-11-14 at the user’s request. No local worker process was running after the manual test. The same local Dagster dependency chain completed a fresh candidate-only run on 2026-11-14: 108 dbt tests and 15 reporting-relation checks passed, with the production pointer unchanged. Data Studio’s no-cost edition requires a Google account and hosted report/data-source creation, not a server instance. The user’s personal Google account owns the private report and queries BigQuery under the documented scoped IAM exception. All six report sources remain on production relations; the report remains private and has no recurring refresh schedule. Managed Workspace/Cloud Identity is required only for Data Studio’s optional service-account mode. The console last showed an active trial; no account upgrade, paid usage or GCS is authorized. Continuing within free usage after the trial requires active billing or a compatible alternative. Current run usage and local cadence are summarized in ROADMAP.md.


## Local Dash application

The application reads BigQuery directly and starts without provisioning or refreshing warehouse data. Install the locked `app` group and run from the repository root:

```powershell
uv sync --locked --group app --group warehouse
uv run --group app python -m launch_atlas.app
```

Visit `http://127.0.0.1:8050`. To use another local port, append `--port 8051`. The server binds only to loopback. Stop with Ctrl+C.

The default project is the existing Atlas project. `ATLAS_PROJECT_ID` can override it. `ATLAS_READER_SERVICE_ACCOUNT` overrides the default `sla-app-reader@<project>.iam.gserviceaccount.com`. Google user ADC is read from the standard location, honoring `CLOUDSDK_CONFIG` or an explicit `GOOGLE_APPLICATION_CREDENTIALS` path. Secrets and account configuration stay outside version control. The app never loads `.env.local` implicitly.

The reader requests short-lived `bigquery.readonly` impersonation tokens. Its IAM grants query-job creation and read access only to the reporting/source datasets. The human operator has Token Creator only on that reader. Terraform definitions are in `infra/terraform/app_reader.tf`; the local enable flag/operator member are in ignored `app-reader.auto.tfvars.json`. Enabling IAM Credentials and creating these bindings required and received explicit approval on 2026-11-21. No key, paid host, public endpoint, schedule, data write or release activation was created.

### Render deployment configuration

The 2026-11-22 implementation adds `render.yaml`, a Gunicorn WSGI entrypoint, hosted reader-auth mode and a disabled-by-default GitHub keep-alive workflow. The planned Render service uses Python 3.12 in Frankfurt, the Free plan, `/healthz` checks and one Gunicorn worker. The approved credential is stored as `bigquery-reader.json` in Render environment group `space-launch-atlas-reader`, referenced by `render.yaml`; runtime path is `/etc/secrets/bigquery-reader.json`. `ATLAS_AUTH_MODE=hosted` requests `bigquery.readonly` and rejects an unexpected reader email. Local mode remains the default and uses local user ADC to impersonate `sla-app-reader`.

Terraform created `sla-render-reader`, BigQuery Job User and Data Viewer on only reporting/source (four additions, no changes/deletions). It also manages the Organization Policy API used for inspection. The user approved one JSON key and a service-account-only exception. Their existing Organization Administrator and Project Owner roles were verified. Temporary Policy Administrator, Tag Administrator and Tag User grants expired after one hour as a fallback; all three were explicitly removed after setup. A temporary tag-conditioned project policy allowed the key only on `sla-render-reader`. The policy override and tag binding/key/value were deleted afterwards; effective `iam.disableServiceAccountKeyCreation` is again unconditionally enforced. Render group [space-launch-atlas-reader](https://dashboard.render.com/env-group/evg-dc1ge8etlv9c73c1nu90) shows one saved secret file and zero variables. The protected local key copy and clipboard were cleared; the key was never placed in Git, chat or Terraform state.

For key rotation, repeat the approved temporary scoped exception using the organization administrator, create one replacement key outside Terraform state, replace the Render secret file, deploy and verify the hosted reader, then revoke the old key. Restore policy inheritance and remove temporary grants/tags after issuance. The original local key copy is not retained. Keep `enable_render_reader=true` in ignored local tfvars while the hosted reader exists; the local `render-reader.auto.tfvars.json` records this flag.

The workflow `.github/workflows/render-keepalive.yml` runs every five minutes off the top of the hour and on manual dispatch, but the job stays skipped until repository variable `RENDER_KEEPALIVE_ENABLED` is exactly `true`. After deployment, set repository variable `RENDER_APP_URL` to the bare `https://<service>.onrender.com` URL, then enable the workflow. It calls only `/healthz`, checks for HTTP success and body `ok`, uses bounded retry/timeout and carries no credentials. GitHub scheduled runs are best effort and can be disabled after 60 days without repository activity; check workflow history periodically. A health response demonstrates process liveness only, not that a BigQuery edition loaded.

No Render web service has been created. Reader IAM, secret storage and local predeployment verification are complete. On 2026-11-22 the billing account was open/enabled, with only this project attached; month-to-date query use was 46,536,851,456 bytes and current storage 535,966,981 bytes, within the existing free-usage limits/reserves. Render showed Hobby, no card/pending charges and unused 750 instance hours / 5 GB bandwidth / 500 pipeline minutes. A Linux Gunicorn process group limited to 512 MiB (no swap) and 0.1 CPU peaked at 224,829,440 bytes while loading the actual active release and serving four concurrent clients. The cold callback took 8.265 s; the concurrent cached callbacks took 2.702–5.076 s. The live query was a zero-byte BigQuery cache hit. All five pages passed the existing browser acceptance checks at four viewport widths with 23 captures and no JS errors. A separate production WSGI import/health check passed with credentials absent and networking disabled. The probe used a one-hour read-only app-reader token; its container and token file were removed afterwards. Evidence is ignored under `data/render-setup/`. Hosted authentication/public access and resource use must still be checked on Render.


To deploy after preparation, open Render **New → Blueprint**, select `RM3006/orbital-pulse`, choose `codex/render-free-deployment` (or `main` after its PR is merged), and use the root `render.yaml` path. Keep the repository root directory; the Python package is installed from `pyproject.toml`. The root `.python-version` selects Python 3.12. Confirm one Free web service in Frankfurt and the existing secret-group reference before deployment. The start command runs the installed `.venv/bin/gunicorn` directly, matching the measured one-worker/four-thread server. After deployment, verify `/healthz`, actual BigQuery-backed pages, reload and signed-out/mobile use before setting the GitHub URL/enabled variables. GitHub cron must be on the default branch to run.

For this REST query-results path, Google documents no extraction or transfer charge for accessing query results through the BigQuery API; it does not use `EXPORT DATA`, Cloud Storage exports or the Storage Read API. Query processing still counts toward the monthly allowance. Render charges usage against its shared bandwidth/build allowances; without a card, its documented behavior on exhaustion is suspension/new-build disablement rather than a paid overage. Future traffic, restarts, newer/larger releases and billing-trial continuity require monitoring. Sources: [BigQuery pricing](https://cloud.google.com/bigquery/pricing#data_extraction_pricing), [Render Free limits](https://render.com/docs/free), [Render Python version](https://render.com/docs/python-version).

A first page load checks the active pointer and validated manifest, then reads retained rows, including configuration/family membership, with a single release/snapshot pair. It also resolves the corresponding source launch-context table for names, dimensions and qualified launch rows. A dry run precedes the SELECT, capped at 250 MB. No disk snapshot is used. The process caches up to eight editions, with no BigQuery queries for filters, comparisons, search or pagination; a reload checks for a new active release and preserves the previous edition on failure. Restart after eight distinct releases to clear the bounded cache. The expanded query billed 125,829,120 bytes on its first uncached load; a later identical load hit BigQuery's query cache and billed zero bytes.

The map uses Plotly MapLibre with attributed CARTO/OpenStreetMap tiles. Browsers need outbound access to the tile/font endpoints for the basemap; no Mapbox token, paid map account or new cloud resource was configured. World view, Florida close-up, zoom buttons, scrolling, dragging and point selection are available. World wrapping is disabled on the MapLibre instance; the responsive map is capped at 1100 px and World view fits one world and the mapped latitude range in a responsive 2:1 frame. Map/selector coverage is all 27 located sites active since January 2024 in this edition; the heatmap retains the six busiest sites. Site and Florida controls explicitly reset the camera after a user zoom/pan. Selecting a site zooms to its actual coordinates. Kennedy and Cape Canaveral overlap at world scale because their coordinates are close; the close-up separates their markers. Browser acceptance waits for tile loading and resizing before screenshots.

If the page reports a connection error, check ADC validity, permission to impersonate the reader, the reader's BigQuery roles, and outbound access to Google APIs. The app intentionally does not fall back to a broader user credential or example data. Error messages do not include auth responses or SQL. If the active snapshot's source labels have expired or disappeared, loading fails rather than mixing editions.

For browser acceptance with the local server running:

```powershell
uv run playwright install chromium
uv run --group app python scripts/verify_atlas_app.py
```

Screenshots and `acceptance.json` go to ignored `data/app-review/`. The current acceptance includes 20 page/viewport captures plus Florida, 1900 px single-world and compact B1063-context captures, comparison years, searchable/paginated outcomes, serial-only flight histories and associated vehicle context, unchanged fleet totals, ascending manifest windows and planned-provider/configuration/family bars. It also checks every mapped site has a selector option, selection beyond the first six works, all markers are inside World view, and reuse values are black above smaller grey labels. It checks that provider/configuration bar totals match the filtered manifest count and that overlapping family bars carry a non-additivity note. Default pytest remains offline. Changes to source data can legitimately change counts and chart labels relative to the approved 11 November screenshots; typography, layout and interactions remain the reference. Actual stored-byte/query headroom should be checked before sustained testing; the per-query cap does not enforce the billing account's 1 TiB monthly allowance.
