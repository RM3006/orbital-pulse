# Decisions

Recorded 2026-11-01. Accepted locally unless explicitly marked proposed.

| ID | Decision | Evidence / rationale |
|---|---|---|
| 001 | Keep the supplied folder `orbital-pulse`; package/repository metadata uses `space-launch-atlas` / `launch_atlas`. | Empty folder contained only `SPACE_LAUNCH_PROJECT_BOOTSTRAP.md`. Copied it unchanged to the requested PROJECT_BRIEF.md; do not rename the user's project directory. Git initialized locally on main; no remote or visibility selected. |
| 002 | Use the production LL2 2.3.0 API, detailed orbital-filtered pages. | Live schema and responses verified; normal mode omits hardware flights and structured payloads. See source_verification.json and data_sources.md. No development host substitution. |
| 003 | Python 3.12, uv, httpx, Pydantic, Polars; ruff/pyright/pytest. | Proposed baseline retained, Python 3.12.13 installed. Locked dependency set actually installed/tested on Windows. Dagster official installation docs support Python >=3.10; selected local dependencies support 3.12. Re-resolve the eventual Dagster/dbt adapter group together before claiming adapter compatibility. |
| 004 | Add tzdata only on Windows. | Nonempty tests exposed ZoneInfoNotFoundError for UTC when Polars exported timestamp rows. Windows lacks the system IANA database; adding the runtime dependency fixes real timestamp reads. |
| 005 | argparse and per-run filesystem state; no application server. | First consumer is the reproducible ingestion CLI. One canonical path, no extra service. Explicit local mode rejects cloud mode and unverified source URLs. |
| 006 | Smoke budget 2; general explicit runs capped at 8; source-wide local ledger capped at 15/hour. | Conservative default for interactive verification, leaving shared-IP headroom. Retries consume the same budget. 429 stops with persisted Retry-After; long transient waits checkpoint rather than sleeping indefinitely. |
| 007 | Keep raw and normalized output per run; no global metric publication. | Required first deliverable is replayable public data, not an empty dashboard. Per-run correction/semantic dedup is implemented; persistent cross-run observations and watermarks belong to Phase 2. |
| 008 | Retain GCS + BigQuery + dbt and Dagster + GitHub Actions as the production proposal; defer installation/provisioning. | No billable resources authorized. Adding adapters without SQL or a warehouse consumer would not validate them. Phase 1 contains the working local dependency chain; add Dagster around it when that graph becomes useful. |
| 009 | MIT for project code; attribute The Space Devs data separately. | Brief's default retained. Public fixtures are reduced source data; images retain separate licenses and are not rendered. Recheck terms before publication. |
| 010 | CI checks Windows and Linux; pinned third-party actions and uv. | Action SHAs verified with upstream refs on 2026-11-01. Workflow is authored, not executed remotely. Local Windows results are the current evidence. |
| 011 | Exclude unknown mission/orbit scope from the orbital denominator while retaining every raw/current record. | The full upcoming page returned three HASTE entries with mission=null despite include_suborbital=false. Rocket Lab's official HASTE page identifies the vehicle as suborbital. Source filtering alone is insufficient evidence for null-orbit records. Nineteen of the 106 upcoming source records lack an orbit; flag coverage and review in Phase 2. This conservative policy can undercount legitimate unknown-scope launches; it does not invent orbital status from names or source counters. |
| 012 | Keep semantic hashes separate from response checksums. | Raw checksums cover all bytes. Within-run observation hashes use launch/schedule/status/mission identity, hardware-flight/landing fields and supplied payload structure; they exclude media, all-time provider/hardware counters and source-update-only changes. Every raw retrieval remains evidenced. |

Pending: remote repository name/visibility; actual GCP project/account, colocated region and monthly spend; confirmation of the proposed Google Data Studio reporting product. No credentials or budget are needed to continue Phase 2 locally. No selection here authorizes billing, publication or changes to the existing portfolio projects.

References: [Dagster installation](https://docs.dagster.io/getting-started/installation), [dbt BigQuery adapter](https://docs.getdbt.com/docs/core/connect-data-platform/bigquery-setup), [Python timezone data](https://docs.python.org/3/library/zoneinfo.html#data-sources).

Scope evidence: [Rocket Lab HASTE](https://rocketlabcorp.com/launch/haste/), read 2026-11-01; no external contact or correction request was sent.
