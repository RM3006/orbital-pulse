# Roadmap

| Phase | Actual status | Remaining exit work |
|---|---|---|
| 0 — API / architecture spike | Complete locally | Public LL2 behavior and scope/precision limitations recorded. |
| 1 — harness / local slice | Complete locally | Installable CLI, locked environment, raw/checkpoints, per-run Parquet, offline replay and tests. Remote CI is configured but not observed running. |
| 2 — history / observed changes | Local implementation operational; acquisition incomplete | Continue 138 pending annual windows from 1958 onward (including later split/reconcile work); review coverage and rerun deterministic rebuild validation. No claim of full-history completeness. |
| 3 — warehouse / releases | Proposed, account and budget gated | Compatible Dagster/dbt/BigQuery group, nonempty SQL models and scratch-warehouse tests, read-only reporting identity, candidate validation + publication pointer and tested rollback. |
| 4 — operating schedule | Not implemented | Same dependency chain under scheduled GitHub Actions, persisted state, freshness/failure visibility and verified scheduled executions. |
| 5 — dashboard / portfolio | Not started | Confirm reporting product, build/test report and signed-out embedding; publication requires authorization. |
| 6 — enrichment | Deferred | GCAT identifier crosswalk/coverage, then sources only for a defined question. |

Phase 2 implementation includes cross-run transactional state, A→B→A observation events, per-window acquisition/reconciliation, shared refresh budget, precision interval overlap, immutable Parquet generations and offline rebuild. The exact design and acceptance criteria are in the [execution plan](docs/phase_2_execution_plan.md); live run evidence and its limits are in [phase2_verification.json](docs/phase2_verification.json).

Next local action is to continue the metered public-source history backfill, beginning with 1958. Cloud credentials, spend and reporting-product decisions do not block that work. No full-history total, reliability trend, reuse statistic or refresh SLA is claimed.
