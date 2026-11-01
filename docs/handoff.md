# Current handoff

Updated 2026-11-01. Scope is Phase 0–1 local bootstrap. Project root remains orbital-pulse; Python distribution is space-launch-atlas and package launch_atlas. Original handoff preserved; PROJECT_BRIEF.md is its unchanged copy. Local Git main is initialized; no remote selected, no push/publication or cloud resource created. Other portfolio projects untouched.

Working implementation: production LL2 2.3.0 client, typed settings, immutable response bodies and manifests, shared local quota ledger/writer lock, bounded retries and Retry-After, safe pagination, budget checkpoint/resume, per-run semantic/ID dedup and source-update correction precedence, normalized Parquet and quality/coverage. CLI: doctor, smoke, backfill, import-raw, replay. Default tests block network access. No Dagster/dbt graph, global current-state database, persistent change history, complete-window watermark, publish/rollback, dashboard or schedule is implemented.

Real local runs:

- live-history-20261101: one request, three 1957 records/qualifying attempts, 2 success + 1 failure. Raw byte-identical page committed as tests/fixtures/ll2_history_1957.json; response provenance committed in docs/history_fixture_provenance.json.
- live-upcoming-20261101: three requests total (one timeout, two successful pages), 106 source records, 22 providers, 35 configurations, 19 sites, 32 pads, 27 planned hardware-stage associations. Zero completed attempts. All precision labels retained; 19 records have unknown orbital scope. Raw is local/ignored; public envelope/quality evidence is docs/upcoming_verification.json.
- demo-1957 and demo-public-sample: offline imports and replay; six-record reduced sample includes genuine recent booster/landing data. This is fixture capability, not a scheduled refresh or representative metric.

Checks run on Windows/Python 3.12.13, uv 0.11.17: uv sync --locked --offline; ruff check; ruff format --check; pyright; pytest (39 passed); CLI help and offline doctor; real API ingest + page resume; offline import/replay; uv build --wheel --offline. A standard uv build initially failed due sandbox DNS; cached offline build succeeded. Initial timezone tests exposed missing Windows IANA data; tzdata added and tests rerun successfully. GitHub CI is authored for Windows/Linux but has not run remotely. No warehouse SQL or cloud integration has been tested.

Read SETUP.md for reproduction commands. Use replay for an existing run ID, rather than importing over it. Local raw/current/coverage evidence must be inspected before presenting any number as worldwide or orbital. The source filter returned missing-mission HASTE records, so unknown scope is excluded rather than inferred. Coarse NET placeholders often use period end; current query windows do not include all intersecting periods.

Next task: Phase 2, beginning with precision-period overlap semantics and persistent source observations/reconciliation. Use cached data to avoid spending quota. A full backfill needs multiple quota windows; offset resume alone cannot prove completeness when upstream records move. Add actual Dagster/dbt/cloud adapter groups only with executable graph/models, resolving compatibility together.

Next decisions before remote/cloud/reporting work: repository name/visibility; GCP account/project, colocated region and approved monthly spend; confirm the proposed Google Data Studio product. None blocks useful Phase 2 local work. No user approval is currently needed to run offline checks or inspect local artifacts.
