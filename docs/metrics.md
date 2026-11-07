# Metric contracts

These contracts govern future marts. Only the sample launch outcome counts/share are computed now, from fact_launch_attempt without stage/family/payload joins. Sample counts are diagnostics, not worldwide headline findings.

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
