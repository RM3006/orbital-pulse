# Roadmap

| Phase | Actual status | Remaining exit work |
|---|---|---|
| 0 — API / architecture spike | Complete locally | Eight-request source probe; schema/status/shape/coverage evidence and architecture decisions recorded. Broader CLI run exposed missing-orbit and coarse-date limitations; conservative scope policy recorded. |
| 1 — harness / local slice | Complete locally | Installable Python package, locked environment, CLI, raw/checkpoints, per-run normalized Parquet, offline replay and substantive tests. Windows verified; remote Linux/Windows CI is configured but has not run. |
| 2 — history / observed changes | Not complete | Quota-aware full historical backfill, cross-run current state and semantic change history, immutable refresh windows/overlap, complete-window watermark, deletion/reclassification reconciliation, precision interval semantics and unknown-scope review. Current offset resume is a page checkpoint, not a guarantee against source movement. |
| 3 — warehouse / releases | Proposed, account and budget gated | Compatible Dagster/dbt/BigQuery group, real nonempty SQL models and scratch-warehouse tests, read-only reporting identity, candidate validation + publication pointer and tested rollback. |
| 4 — operating schedule | Not implemented | Same Dagster dependency chain under scheduled GitHub Actions, persisted state, freshness and failure visibility; verify real scheduled executions. |
| 5 — dashboard / portfolio | Not started | Confirm Data Studio, build/test report and signed-out embedding; publication requires authorization. |
| 6 — enrichment | Deferred | GCAT identifier crosswalk/coverage, then additional sources only for a defined question. |

Immediate local next work: Phase 2 precision-period overlap and persistent observations, using cached upcoming records before spending more quota. Cloud credentials and spend are not blockers for that work. No full-history total, reliability trend, reuse statistic or running refresh SLA is claimed by the bootstrap.
