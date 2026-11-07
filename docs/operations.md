# Local operations

Nothing is scheduled or published. Local commands operate on `LOCAL_DATA_DIR` (default `data`). Phase 2 requires no cloud credentials. Live `run-plan` uses the same LL2 client, immutable raw response chain and quota ledger as earlier ingestion; planning, state inspection, export and rebuild are offline.

## Data layout

- `raw/ll2/<run-id>/manifest.json`: query provenance, schema/parser versions, accepted pages, response attempts, request timing/status, checkpoint URL, completeness, plan/window metadata and validation report.
- `raw/ll2/<run-id>/response-*.json.gz`: immutable compressed source bytes; SHA-256 is over the decompressed response.
- `state/launch_atlas.sqlite`: acquisition/current state, semantic versions, sightings/events/conflicts, frozen plans/windows, per-window cursors, source request evidence and latest complete refresh horizon.
- `quota.json`: shared local rolling request reservations and provider retry-after state. A failed transport still consumes a reservation.
- `exports/generations/<revision-id>/`: immutable Parquet candidate, quality and manifest. `exports/active.json` points to the last validated generation.
- `normalized/<run-id>/`: legacy/per-run replay outputs; not the cross-run state export.
- `.writer.lock`: exclusive local writer lock. A stale lock must be checked against its PID before removing that file only.

Do not delete raw evidence, SQLite state or quota history as routine recovery. Raw runs can reproduce current state offline. Keep backups together if moving the data root.

## Phase 2 commands

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

Default tests are offline. Run `uv sync --locked`, `ruff check`, `ruff format --check`, `pyright` and `pytest --basetemp .pytest-tmp` before further source calls. Remote CI is configured but has not been verified on GitHub/Linux.

Production remains proposed: one Dagster dependency chain for ingest/load/dbt/check/publish, invoked by scheduled GitHub Actions; persisted warehouse state, read-only reporting identity and last-good-release rollback. No Dagster job, warehouse SQL, cloud identity, schedule, dashboard or public release is implemented. Do not provision billable resources or publish without the later project decisions and explicit authorization.
