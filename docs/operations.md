# Local operations

Nothing is scheduled or published. Each explicit network run uses the canonical pipeline.ingest path; replay reads only accepted saved pages. Raw errors are retained but never normalized as empty successful data. Inspect the manifest, not only the final console output.

State under LOCAL_DATA_DIR (default data):

- raw/ll2/&lt;run-id&gt;/manifest.json: original URL/window, API/parser version, request/response evidence, accepted pages, source-reported counts, next URL, invocation budgets/times, completeness, errors and latest normalization report.
- raw/ll2/&lt;run-id&gt;/response-*.json.gz: unchanged JSON/error bytes, checksum-suffixed names; unknown fields preserved.
- normalized/&lt;run-id&gt;/: ten Parquet tables and quality.json. These are per-run, replaceable local outputs, not a published release.
- quota.json: conservative rolling 15/hour request reservations and source Retry-After. Shared across runs using this root, not across machines or other IP users.
- .writer.lock: exclusive local writer lock containing the process ID.

The client disables redirect following, accepts JSON only, validates same-host/version/endpoint/query pagination and increasing offsets, detects cycles, enforces one budget including retries, and rejects contradictory empty pages. Transient transport/500/502/503/504 failures receive at most three attempts within the budget. Short Retry-After waits are bounded; long waits and 429 checkpoint for manual resume, respecting persisted retry time. Source refusal overrides available local budget. Local reservations count even a request interrupted before its response, preventing accidental overspend after a crash.

On an extraction failure, accepted-page checkpoint remains at the failed URL; raw failed response stays in attempts. Fix the cause and resume the same run. On normalization/validation failure, keep raw evidence, fix the parser or mapping, then replay. An extraction marked complete means all pages of that query were fetched; it does not mean all history was fetched or that a reporting release is valid. Unknown statuses and impossible launched/future combinations fail quality. Unknown orbital scope stays excluded and visible as coverage; never infer a classification from absent payloads/counters.

Interrupted process: inspect .writer.lock and confirm that process is no longer running before removing **that file only**. Resume with the saved run ID. Do not delete raw artifacts or quota history. Atomic raw writes and checkpoint replacement allow re-fetching an uncommitted response; semantic/ID dedup handles retrieval overlap. Offset pagination can still move beneath the extraction; Phase 2 adds reconciliation and stronger complete-window watermark behavior.

Retention for this manual bootstrap: retain all raw evidence and per-run outputs until Phase 2's reviewed retention policy. No hourly snapshots, garbage collector or deletion task is enabled. Measure real artifact sizes and source change frequency before selecting cloud retention. Source-spike throttle data remains ignored because it includes the public-IP identifier; the committed verification summary omits it.

Future: a Dagster job must include ingest/load/dbt/checks/publication and be invoked by one scheduled GitHub Actions workflow, with persisted state and reporting read-only credentials. No automatic production workflow or placeholder cloud workflow is included yet. Last-good-release, rollback, stale thresholds and budget controls must be implemented and tested before the warehouse/reporting stage can publish.
