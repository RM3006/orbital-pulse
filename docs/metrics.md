# Metric contracts

These contracts define release 1 metrics and distinguish the implemented initial marts from deferred calculations. The revision-27 dbt model set was built and tested in BigQuery scratch: daily launch/family outcome counts, stage-flight fields and upcoming schedule intervals are available there. Final reuse/landing rates, turnaround and schedule-movement calculations remain to implement. No reporting release or worldwide headline finding has been published; historical acquisition remains incomplete. The newer revision-42 local export has not yet been loaded to BigQuery.

| Metric | Numerator / denominator | Scope, grain, time and exclusions |
|---|---|---|
| Orbital attempts | Distinct qualifying launch_id | LL2 orbital-filtered source AND supplied non-suborbital mission orbit; status 3/4/6/7/9, nonfuture nonnull NET. Suborbital and unknown-scope records excluded; unknown scope reported as coverage and may undercount legitimate attempts. Time is source NET in UTC; not asserted to be independently measured liftoff. |
| Launch success share | Success / (success + failure + partial failure) | Attempt grain and selected NET window; partial failures displayed separately, not counted as success. In-flight/deployed unknown outcomes remain in attempt totals and are exposed as unresolved, excluded from classified denominator. Never return 0% for an empty denominator. |
| Provider attempt share | Provider attempts / all scoped attempts | Same time window and denominator, including unknown provider in the total. Not revenue or market value. |
| Landing success share | Successful landings / attempted landings with known outcomes | Landing grain, require attempt=true. No-landing launches and null outcomes excluded; expose null coverage. Deferred calculation. |
| Booster reuse share | Reused identifiable booster flights / identifiable booster flights with known reuse | Stage-flight grain; source flight number/reused field; publish known hardware coverage. Not interchangeable with launch-level share. Deferred. |
| Turnaround | Difference between consecutive known flights of identical hardware | UTC flight times; reconcile with source interval; sparse extraction cannot imply no intervening flights. Deferred. |
| Site cadence | Attempts or intervals within one site_id | Pad and location are separate grains; current geographic country is not historical sovereignty. Deferred. |
| Upcoming manifest | Distinct eligible launch IDs whose supported NET precision interval overlaps [frozen_as_of, frozen_as_of+90 days) | `fact_upcoming_schedule` is launch grain, one row per launch ID per export horizon. Store the source NET placeholder and `[schedule_earliest, schedule_latest_exclusive)` interval; include month/quarter/half-year/year candidates when their intervals overlap. Unsupported precision remains in the coverage counts and is excluded from this fact. The bounded acquisition envelope is intentionally broader than the final interval filter. This is a schedule-uncertainty window, not an exact launch schedule. |
| Schedule movement | Difference between meaningful comparable-precision observations | Collection begins with this project; no reconstructed historic schedules. Precision refinement alone is not a delay. Deferred. |
| Payload count / mass | Source-linked recorded entries with coverage and estimation flags | Deferred. Empty arrays and descriptions cannot establish actual total payload count/mass. |
| Freshness | Source check, source update and published-release times separately | Run manifest observations are check evidence. A successful partial sample is not a completed full refresh. No published-release timestamp exists yet. |

Future comparative views: full available history from 1957, primary comparisons from 2000. Compare current year to the same elapsed previous-year period. Always display classified denominator/sample sizes and unresolved counts. Store additive numerators/denominators in marts; compute ratios from their sums under filters. Do not average percentages, use Top-N as a total, or sum counts across overlapping family memberships.

## Authored mart grains

- `mart_launch_attempt_daily`: one row per snapshot, UTC launch date, provider, configuration and site. `attempt_count` and outcome counts are additive across those dimensions; group by null provider/site/configuration rather than dropping unknowns.
- `mart_family_outcomes_daily`: one row per snapshot, UTC launch date and family. Each row counts distinct launches within that family; counts are non-additive across families because configurations can belong to multiple families.
- `mart_stage_flight`: one row per snapshot, launch and source stage-flight association. It supports hardware/reuse exploration and is not a launch-count mart.
- `mart_upcoming_schedule`: one supported launch schedule per snapshot horizon, retaining source precision and its derived half-open interval.

The SQL defines these grains and tests launch-volume preservation against the core attempt table. Actual BigQuery execution, test results and independently compared metric values remain unverified until a cloud warehouse is available and authorized.
