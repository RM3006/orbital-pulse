# Space Launch Atlas — project brief

Updated 2026-11-08. This file defines product intent and scope. [ARCHITECTURE.md](ARCHITECTURE.md) owns the technology/design description; [ROADMAP.md](ROADMAP.md) owns all delivery steps, current progress and next actions.

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

## Source and metric constraints

Launch Library 2 is community-maintained public data, not an independently verified census. Preserve unchanged raw responses, source/update/observation times, checksums and missingness. Cache and respect shared quotas, retries and `Retry-After`. Keep source facts distinct from derived metrics. Use separate grains for launches, stage flights, landings, payloads and schedule observations; never multiply launch counts through joins or combine their outcomes into a score.

[docs/data_sources.md](docs/data_sources.md) owns source/provenance and grain details. [docs/metrics.md](docs/metrics.md) owns metric formulas, denominators, exclusions and time bases. Code is MIT; source data and images retain their own terms.

## Boundaries and open deployment inputs

This is a launch analytics project. Live telemetry, satellite tracking, collision avoidance, exhaustive missile/suborbital coverage, financial performance estimates and predictive failure scores are outside the current scope. Add optional sources/services only for a concrete analytical question.

The user requires projects to remain free and authorized BigQuery loading/processing within the free-tier limits of their active Google Cloud Free Trial on 2026-11-07: 1 TiB of query processing and 10 GiB of storage per month. Do not upgrade to a paid account or use trial credit beyond those free limits. On 2026-11-08 the user also authorized Terraform to change the existing project for the bounded Step 3 setup: BigQuery, IAM and required API configuration, six datasets, workload service accounts and least-privilege BigQuery IAM. Raw source files remain local; Terraform must not create GCS resources or storage permissions. This does not authorize paid usage, account changes, public reporting, website publication or changes to other GCP services/projects. Data Studio's BigQuery connector requires a billing-associated project and queries may incur costs; a personal Google account is selected to own the report, with a weekly initial BigQuery warehouse refresh. Connecting a report remains gated on completing history, a production release, read-only credential validation and measured query usage. The GCP project and location are set in ignored `.env.local`. The user authorized the public [RM3006/orbital-pulse](https://github.com/RM3006/orbital-pulse) repository and project-work pushes on 2026-11-08; unattended runner/access design remains unresolved. Weekly is a target cadence, not an operating schedule. Local development does not require cloud credentials.

On 2026-11-08 the user authorized local dbt authentication through Google Cloud CLI ADC. The active ADC was created with only the `cloud-platform` OAuth scope (Cloud SQL was left unchecked), after explaining that this is broader than a BigQuery-only scope. The dbt profile still declares BigQuery; actual project use is limited to the separately authorized BigQuery free-tier work and bounded Terraform setup described above. The earlier dedicated Desktop OAuth client remains in Testing but is no longer the active local ADC. No domain or public OAuth branding is needed for this personal local workflow. This authentication choice itself does not authorize additional GCP services, paid usage or website/report publication.

The billing console was inspected again on 2026-11-08: the existing account is active and explicitly labelled Free Trial. The private expiry/cost observations are retained only in ignored verification evidence. Billing association satisfies the Data Studio BigQuery connector prerequisite but is not a zero-spend guarantee. BigQuery's free usage allowances also exist after the trial if billing remains active; continuing billing or changing account type is a separate decision, and no upgrade is authorized. A personal Google account can author no-cost Data Studio reports without a domain or managed organization; the user selected their personal account as report owner and weekly as the initial BigQuery refresh cadence on 2026-11-08. No schedule or Data Studio source/report has been created. The query credential must have effective read-only access; the report owner's personal account can serve if its IAM roles qualify, otherwise use a distinct least-privilege identity. Measure end-to-end usage before connecting project data. Reporting and schedule gates are recorded in ROADMAP.md.

Do not provision billable resources, publish the site/report, push remotely or modify the other portfolio projects without authorization. Keep credentials private and reporting access read-only. The existing `human-protein-atlas` and `paper-to-patent` projects are structural references only.

## Documentation discipline

Use **Step** for delivery milestones and keep all step plans/status in `ROADMAP.md`. Reuse existing documents rather than adding an execution plan or guide for each task. Record substantive decisions in `docs/decisions.md`; keep dated extraction evidence in the existing JSON verification summaries. Setup and recovery live together in `docs/operations.md`.
