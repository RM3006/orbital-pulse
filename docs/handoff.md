# Current handoff

Updated 2026-11-07 after Phase 2 implementation and bounded live verification. Project folder remains `orbital-pulse`; distribution `space-launch-atlas`; package `launch_atlas`. Local Git only; no remote, cloud resource, published site or schedule. Other portfolio projects were not modified.

## What runs locally

The installable Python 3.12 CLI supports doctor, smoke, backfill, import-raw, replay, state-merge, state-status, state-export, plan-backfill, plan-refresh, plan-reconcile, run-plan and rebuild-state. Raw LL2 pages are immutable/checksummed; SQLite persists current source records, content versions, sightings, observation events, conflicts, plans, windows and cursors. Refresh uses one request budget across component windows. Parquet exports are revisioned candidates with a last-good active pointer. Rebuild works offline from completed validated live raw runs; demo imports require explicit run IDs.

Relevant commands and recovery rules are in [operations.md](operations.md). Current contracts are in [architecture](../ARCHITECTURE.md), [decisions](decisions.md), and [metrics](metrics.md). No warehouse, dashboard or public reporting release exists.

## Live evidence (2026-11-07)

- A three-component frozen refresh plan completed in seven LL2 requests: recent 22 source records, upcoming 250 records across five pages, corrections 37 source records. Categories overlap and must not be summed as distinct launches.
- A 90-day upcoming interval fact contains 179 supported source intervals overlapping the frozen horizon. Coverage reports 33 upcoming candidates with unsupported precision, 4 records missing precision, 24 with unknown orbital scope, among other total-source coverage fields. This interval fact is not an exact-date launch schedule.
- SQLite contains 287 distinct current launch records, 308 semantic observation events and zero conflicts after refresh plus two history passes. State is local and source coverage is not claimed complete.
- The 1957 two-pass plan completed in two requests; the two pass raw-response hashes matched. The broader 1957–2026 backfill plan contains 140 initial annual windows; 2 are committed/reconciled and 138 remain. A split threshold can replace dense years with monthly child windows during execution.
- One bounded `include_suborbital=true`/`last_updated` query was accepted by the API (HTTP 200), but its one-page probe was intentionally incomplete and was not merged. It verifies query acceptance, not completeness or successful orbital reclassification detection.
- An offline rebuild from seven accepted live runs reproduced the state (287 current records, 308 observations, zero conflicts). The final check must also compare the upcoming Parquet generation after refresh-as-of metadata preservation; see verification evidence.

This is extraction evidence, not a worldwide reliability analysis. Raw and state directories are ignored local data. See [phase2_verification.json](phase2_verification.json) for plan IDs, run IDs, counts, hashes and limits.

## Checks

Final Windows/Python 3.12.13 checks on the Phase 2 code: `uv sync --locked --offline`; `ruff check .`; `ruff format --check .` (39 files formatted); `pyright` (0 errors/warnings); `pytest --basetemp .pytest-tmp -q` (**62 passed**); `uv build --wheel --offline`; isolated offline wheel install and package import/doctor; CLI help; and active-export doctor. The isolated installed wheel reported Python 3.12.13, httpx 0.28.1, Pydantic 2.14.2 and Polars 1.44.2. The offline rebuild applied seven complete live runs and reproduced all 11 active Parquet file SHA-256 values at state revision 7; quality passed. The Phase 2 regression suite includes state transition/rollback, precision boundary, two-pass reconciliation, dense-window split, shared-budget, export fail-safe and frozen-horizon rebuild tests.

Remote Windows/Linux CI has not run; local validation was Windows only. No billable resource, cloud identity, publication or portfolio change was made. The local raw/state/quota files are ignored and were not committed; generated package build outputs are ignored as well.

## Next action and unresolved decisions

Continue the metered historical backfill from 1958, with two-pass reconciliation and coverage review; stay within the shared LL2 quota ledger. The next *external-stage* unresolved decisions are GCP account/project and approved monthly spend, reporting product confirmation, and eventual repository remote/name/visibility. None blocks local backfill.
