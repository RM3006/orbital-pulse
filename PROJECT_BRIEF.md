# Space Launch Atlas — project brief

Updated 2026-11-11. This file defines product intent and scope. [ARCHITECTURE.md](ARCHITECTURE.md) owns the technology/design description; [ROADMAP.md](ROADMAP.md) owns all delivery steps, current progress and next actions.

## Purpose and audience

Turn publicly available worldwide space-launch data into understandable interactive visualizations. Answer:

> How is access to orbit changing — who launches, how reliably, and with how much reuse?

Curious visitors should understand the overview quickly. Analysts should be able to filter and compare results; technical reviewers should be able to trace each metric to its source, grain and quality checks. The project also demonstrates reliable data engineering and analytics engineering: repeatable ingestion, modeling, testing and controlled publication.

Working title: **Space Launch Atlas — The Industrialization of Space**. The existing folder remains `orbital-pulse`; Python distribution/package are `space-launch-atlas` / `launch_atlas`.

## Release 1 — launch analytics

- Industry growth: worldwide orbital launch attempts over time and provider share by attempt count.
- Reliability: launch outcomes by vehicle family/variant, with sample sizes and clear time windows.
- Spaceports: site locations, activity and cadence.
- Reusability: recorded booster flights, turnaround and landing outcomes where source coverage supports them.
- Upcoming launches: a 90-day view preserving source date precision/status, with visible freshness and limitations.

Acquire available orbital history from 1957 onward; focus comparative views on 2000 onward. Compare an incomplete current year to the same elapsed period in earlier years. Launch Library 2 alone must support a useful first release. Unknown source values stay unknown, and incomplete extraction is visible.

## Release 2 — detailed satellites and spacecraft

The user confirmed detailed satellite and spacecraft views belong in the second release. Enrich launches with linked payload/mission/object information, measured identifier-match coverage, source-backed counts and mass where supported. GCAT is the intended first enrichment source. Decide the detailed release 2 chart/data contracts after inspecting source coverage; do not infer payload completeness from descriptions or empty arrays.

## Selected technical direction

- The no-cost edition of Data Studio (formerly Looker Studio) for interactive reporting and eventual portfolio embedding. Reports and data sources are hosted assets; no BI server instance or Pro subscription is required.
- Terraform for cloud infrastructure definitions.
- Python 3.12, uv, httpx, Pydantic and Polars for acquisition/normalization; raw JSON, SQLite and Parquet support local operation and offline reconstruction.
- Local raw-file retention with direct local-file loads to BigQuery; dbt for warehouse transformations. GCS is deferred while the project remains free-only.
- One Dagster dependency chain for accepted ingestion, load, SQL, quality and release activation. Scheduled GitHub Actions remain future work.

Actual installation/provisioning status and each technology's responsibility are listed once in [ARCHITECTURE.md](ARCHITECTURE.md). The complete workflow must validate before changing the reporting release, and a failure must preserve the prior good release.

On 2026-11-08 the user authorized one automatic, resumable local LL2 backfill followed by weekly source updates. The Windows worker reuses the saved history plan and shared 15/hour ledger, stops for investigation after three consecutive failed attempts or validation/reconciliation failures, and preserves raw/checkpoint/state evidence across runs. After history it performs a catch-up, raw replay/Parquet verification and weekly recent/upcoming/correction acquisition. This authorizes the local background task; the existing production release and free cloud-use gates remain in force.

## Source and metric constraints

Launch Library 2 is community-maintained public data, not an independently verified census. Preserve unchanged raw responses, source/update/observation times, checksums and missingness. Cache and respect shared quotas, retries and `Retry-After`. Keep source facts distinct from derived metrics. Use separate grains for launches, stage flights, landings, payloads and schedule observations; never multiply launch counts through joins or combine their outcomes into a score.

[docs/data_sources.md](docs/data_sources.md) owns source/provenance and grain details. [docs/metrics.md](docs/metrics.md) owns metric formulas, denominators, exclusions and time bases. Code is MIT; source data and images retain their own terms.

## Boundaries and open deployment inputs

This is a launch analytics project. Live telemetry, satellite tracking, collision avoidance, exhaustive missile/suborbital coverage, financial performance estimates and predictive failure scores are outside the current scope. Add optional sources/services only for a concrete analytical question.

The user requires this project to remain within BigQuery's no-cost monthly allowances: 1 TiB of query processing and 10 GiB of storage, as authorized for the active Google Cloud Free Trial on 2026-11-07. Do not upgrade the account or consume trial credit beyond those free limits. On 2026-11-08 the user authorized Terraform to make the bounded changes in the existing project: BigQuery datasets, required APIs, workload service accounts and least-privilege dataset IAM. Raw source files remain local; no GCS bucket or storage permission is authorized. The first full-history warehouse release was published on 2026-11-09; current release `sla-release-v1-d4f4ae640e0934f11d7ad4c4` was published on 2026-11-11. The user approved resuming weekly LL2 acquisition and setting up a local guarded warehouse schedule on 2026-11-11. The Windows tasks use the current user and require the PC awake and the user signed in; no GCP scheduler or compute resource was created. All six private Data Studio report sources were moved from scratch to production and refreshed against the current release. The report remains private. The personal account owner's existing GCP permissions are an explicitly accepted, project-scoped exception to the read-only reporting rule; no IAM change was made. Data Studio queries may incur costs and are not capped by the local warehouse scheduler. The old separate scratch preview expires on 2026-11-16 but the report no longer uses it. The GCP project and location are set in ignored `.env.local`. The user authorized the public [RM3006/orbital-pulse](https://github.com/RM3006/orbital-pulse) repository and project-work pushes on 2026-11-08. Hosted execution and identity design remain unresolved. Local development remains usable without cloud credentials.

On 2026-11-08 the user authorized local dbt authentication through Google Cloud CLI ADC; login completed successfully on 2026-11-11 with the `cloud-platform` OAuth scope (Cloud SQL was left unchecked). The local credential is stored at the standard user ADC path outside this repository and is not a service-account identity. The dbt profile still declares BigQuery; actual project use remains limited to the authorized BigQuery free-tier work and bounded Terraform setup above. The earlier dedicated Desktop OAuth client remains in Testing but is not used by the active local ADC. No domain or public OAuth branding is needed for this personal local workflow. This authentication choice does not authorize additional GCP services, paid usage or website/report publication.

The billing console was inspected on 2026-11-08 and showed the existing account as Free Trial; private expiry/cost observations remain only in ignored evidence. Billing association satisfies the Data Studio BigQuery connector prerequisite but does not guarantee zero spend. BigQuery's free usage allowances can continue after the trial if billing remains active; continuing billing or changing account type is a separate decision, and no upgrade is authorized. The user selected their personal account as report owner and a weekly initial BigQuery refresh cadence on 2026-11-08. Data Studio terms and connector authorization are complete. On 2026-11-11 the report's six sources were rebound to production and refreshed against release `sla-release-v1-d4f4ae640e0934f11d7ad4c4`. A local daily warehouse preflight is configured to publish only changed, validated snapshots and has project-scoped query/storage reserves. Google applies these free limits per billing account, so automatic publication now requires explicit confirmation that this is the only BigQuery project drawing on that account; that confirmation is currently missing. Source acquisition runs weekly. The Data Studio report itself has no recurring refresh schedule. The approved owner-credential IAM exception applies only to this private report; no IAM change, sharing, public report or website publication is authorized. The old scratch preview expires on 2026-11-16 but is no longer used by the report. See ROADMAP.md for current status and open decisions.

Do not provision billable resources, publish the site/report, push remotely or modify the other portfolio projects without authorization. Keep credentials private; the existing owner-credential exception is limited to this private report. The existing `human-protein-atlas` and `paper-to-patent` projects are structural references only.

## Documentation discipline

Use **Step** for delivery milestones and keep all step plans/status in `ROADMAP.md`. Reuse existing documents rather than adding an execution plan or guide for each task. Record substantive decisions in `docs/decisions.md`; keep dated extraction evidence in the existing JSON verification summaries. Setup and recovery live together in `docs/operations.md`.
