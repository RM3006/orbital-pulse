# Printed Atlas — page and visual data specification

This is the build reference for the five pages in the [approved interactive HTML](printed-atlas-design-export/printed-atlas.html). The prototype renders embedded data and static copy. The BigQuery bindings below describe how to reproduce that design from the warehouse. They are independent of the private Data Studio reports. See the [design specification](printed-atlas-design-spec.md) for exact styling, structure, and responsive behaviour.

## Warehouse release and table names

The design uses release `sla-release-v1-d4f4ae640e0934f11d7ad4c4`, with analysis as of `2026-11-11 11:38:30.399171 UTC`, and snapshot `sla-input-v1-36d5e54dfe66915cc6747fa6ad17f1ad1d3726b57b1cc8d4d664627d7e3e271b`. Use the named views below to build from the active validated release. To reproduce this frozen edition after the active release changes, use the corresponding `retained_<view name>` tables and filter each one to the release ID above. Resolve the edition once per export or app build. Carry `release_id` and `input_snapshot_id` through data preparation and use both when joining release relations. Use the snapshot ID to join the pinned launch label table, which has no release ID.

Short names below are aliases used only in this document. The field names following them are actual BigQuery column names.

| Alias | Fully qualified BigQuery relation | Grain and business key within the snapshot |
| --- | --- | --- |
| `daily` | `project-5584fea9-baf6-40ef-a9a.space_launch_atlas_reporting.mart_launch_attempt_daily` | One row per `launch_date_utc`, `provider_id`, `configuration_id`, `site_id`. |
| `family_daily` | `project-5584fea9-baf6-40ef-a9a.space_launch_atlas_reporting.mart_family_outcomes_daily` | One row per `launch_date_utc`, `family_id`. Counts are distinct within a family and overlap across families. |
| `provider` | `project-5584fea9-baf6-40ef-a9a.space_launch_atlas_reporting.core_dim_provider` | One row per `provider_id`. |
| `configuration` | `project-5584fea9-baf6-40ef-a9a.space_launch_atlas_reporting.core_dim_vehicle_configuration` | One row per `configuration_id`. |
| `family` | `project-5584fea9-baf6-40ef-a9a.space_launch_atlas_reporting.core_dim_vehicle_family` | One row per `family_id`. |
| `site` | `project-5584fea9-baf6-40ef-a9a.space_launch_atlas_reporting.mart_site_cadence` | One row per orbital `launch_id`. Site names and coordinates are already joined by dbt. |
| `booster` | `project-5584fea9-baf6-40ef-a9a.space_launch_atlas_reporting.mart_booster_flight` | One row per `launch_id` and identifiable `booster_id`. |
| `landing` | `project-5584fea9-baf6-40ef-a9a.space_launch_atlas_reporting.mart_landing` | One row per `landing_id`. |
| `upcoming` | `project-5584fea9-baf6-40ef-a9a.space_launch_atlas_reporting.mart_upcoming_schedule` | One row per `launch_id` in the snapshot's frozen horizon. |
| `coverage` | `project-5584fea9-baf6-40ef-a9a.space_launch_atlas_reporting.mart_data_coverage` | One row per `input_snapshot_id`. |
| `launch_labels` | `project-5584fea9-baf6-40ef-a9a.space_launch_atlas_source.sla_36d5e54dfe66915cc6747fa6_launch_record_current` | One row per `input_snapshot_id`, `launch_id`. Supplies `name` and, where needed, `provider_id`. This name is specific to the frozen snapshot. |

`launch_labels` is a release-matched lookup for pages 4 and 5. For a frontend that follows new releases automatically, make `launch_id`, `name`, and `provider_id` available in a release-scoped reporting relation through the existing warehouse release process. The reporting layer currently has no such launch-label view. A static export of this edition can use the pinned lookup above. The [metric contracts](../docs/metrics.md) define eligible attempts, outcomes, and missingness.

## Shared design and interaction

- Navigation order is Overview, Providers & vehicles, Spaceports, Reuse & landings, and Upcoming & coverage. Use the exported HTML and [desktop PNGs](printed-atlas-design-export/) as the layout reference. Overview is the default page.
- The masthead uses `coverage.reporting_analysis_as_of_utc` for the edition date in UTC. For the frozen edition, the footer is “Source · Launch Library 2” and “Production data cut · 11 Nov 2026.” This date describes the analytical cut, not the date a visitor opens the page.
- Use `#f5f6f8` for the page, `#ffffff` for cards, `#223da4` for the accent, `#202329` for primary text, `#505761` for muted text, and `#d9dee6` for dividers. Cards have an 8 px radius. Use Archivo Narrow for display headings, Instrument Sans for body and table labels, and IBM Plex Mono for metadata and numeric labels. Keep dark text on light surfaces.
- In the prototype, paired cards stack at widths up to 620 px. The reliability table moves extra outcome columns into a per-row disclosure, and the launch manifest reflows into stacked rows with each row's own time axis. Copy the prototype's disclosure defaults and keep the full row sets available.
- All dates are UTC. Display unavailable values as `—` or use the explicit unknown label called out below. A real zero, such as a site-month with no recorded attempts, stays zero.

## 1. Overview

Page key `overview`. Place the opening story and large number first, then the full-width annual chart, then provider contribution and year-to-date pace side by side.

| Visual and position | Exact table fields | Query, encoding, and interaction |
| --- | --- | --- |
| Opening headline, deck and **324** callout | `daily.launch_year_utc`, `daily.attempt_count`, `daily.provider_id`, `provider.name` | Sum `attempt_count` in 2020 and 2025. The frozen values are 113 and 324, a difference of 211. The SpaceX subset (`provider_id = 121`) rises from 25 to 165, a difference of 140. The headline and deck are editorial copy backed by these values. |
| **Orbital attempts by year** — column chart | `daily.launch_year_utc` → x and tooltip year, `SUM(daily.attempt_count)` → y and tooltip count | Group by year. The **History** selector defaults to Since 2000 and switches to Since 1957. Include complete years through 2025. The frozen 2026 annual record is present in the prototype payload but excluded from the chart. Use the common 0–350 scale, ticks at 0/175/350, blue bars from 2020 onward, and direct labels for 2020 and 2025. |
| **Who drove the change?** — comparison table | `daily.provider_id`, `daily.launch_year_utc`, `daily.attempt_count`, `provider.provider_id`, `provider.name` | Use 2020 and 2025 only. Group on provider ID, sum attempts by year and pivot. Columns are **Provider**, **2020**, **2025**, **Change** (`2025 − 2020`). Render the five named providers in the order below and pool every remaining attempt, including unknown providers if present, into **All other providers**. Zero-fill a selected provider with no attempts in either comparison year. The footer uses all attempts, 113 / 324 / +211. |
| **So far this year** — horizontal bars | `daily.launch_year_utc` → row label, `daily.launch_date_utc` → cutoff, `SUM(daily.attempt_count)` → bar and value label | Group 2023–2026 by year. For each year include Jan 1 through Nov 10 UTC, equivalent to `< DATE(year, 11, 11)`. Values are 182, 208, 265, and 260. Use a common 0–300 scale, highlight 2026, and calculate the difference against 2025 (`260 − 265 = −5`). |

Provider comparison rows in order: SpaceX (`121`, 25 → 165), China Aerospace Science and Technology Corporation (`88`, 32 → 69), Rocket Lab (`147`, 7 → 18), Arianespace (`115`, 10 → 7), Astra Space (`285`, 2 → 0), then All other providers (37 → 65). The first five are a curated comparison, not a top-five ranking.

## 2. Providers & vehicles

Page key `vehicles`. The only data visual is the full-width **Launch outcomes** table. Its **Group by** selector defaults to Configuration and switches the row grouping, label join, note, and disclosure rows. The opening deck references the default configuration view.

| Group by | Row key and label mapping | Exact numeric columns read |
| --- | --- | --- |
| **Configuration** | `daily.configuration_id` → `configuration.configuration_id`, display `configuration.name` | `daily.launch_date_utc`, `daily.attempt_count`, `daily.success_count`, `daily.classified_outcome_count`, `daily.failure_count`, `daily.partial_failure_count`, `daily.unresolved_count`. |
| **Vehicle family** | `family_daily.family_id` → `family.family_id`, display `family.name` | `family_daily.launch_date_utc`, `family_daily.attempt_count`, `family_daily.success_count`, `family_daily.classified_outcome_count`, `family_daily.failure_count`, `family_daily.partial_failure_count`, `family_daily.unresolved_count`. |
| **Provider** | `daily.provider_id` → `provider.provider_id`, display `provider.name` | `daily.launch_date_utc`, `daily.attempt_count`, `daily.success_count`, `daily.classified_outcome_count`, `daily.failure_count`, `daily.partial_failure_count`, `daily.unresolved_count`. |

Filter `launch_date_utc >= DATE '2000-01-01'` through the selected release cut. Aggregate by the ID from the selected row above. Left join the relevant dimension and display **Unknown** when the group has no label. Sort by `SUM(attempt_count)` descending, then display name ascending, then ID ascending for stable ties. Show eight rows initially and the rest under **Show more groups**. The snapshot contains 234 configuration rows, 76 family rows including Unknown, and 65 provider rows.

| Visible table column | Exact binding |
| --- | --- |
| **Configuration**, **Vehicle family**, or **Provider** | Corresponding dimension `name`. Use the dimension ID as the row key even if two names match. For example, configuration IDs `105` and `467` both display “Delta II 7420-10C.” |
| **Successes / classified** | `SUM(success_count)` / `SUM(classified_outcome_count)` displayed as two counts. `classified_outcome_count` equals success + failure + partial failure. |
| **Success rate** | `100 * SAFE_DIVIDE(SUM(success_count), SUM(classified_outcome_count))`, one decimal and `%`. Show `—` when there is no classified denominator. |
| **Failure**, **Partial**, **Unresolved** | `SUM(failure_count)`, `SUM(partial_failure_count)`, `SUM(unresolved_count)` respectively. Unresolved outcomes contribute to attempt rank and remain outside the classified denominator. |

The sampled comparison in the deck reads Falcon 9 Block 5 (642 / 643) and Soyuz 2.1a (51 / 51). These are classified sample counts from the same query. The family note states that membership can overlap. Do not add across families or rebuild this branch by joining the daily mart through a many-to-many bridge.

## 3. Spaceports

Page key `sites`. Stack the map, monthly heatmap, and observed gap table in that order. They share a release and the `site.launch_date_utc >= DATE '2024-01-01'` filter. The mart already excludes future attempts for the release. November 2026 is a partial month. Group by `site.site_id` and use `site.site_name` as the label. The frozen period has 28 known sites and no missing site IDs. Exclude unknown or unmapped groups from the map and retain separate unknown coverage if present in a later release.

| Visual and position | Exact table fields | Query, encoding, and interaction |
| --- | --- | --- |
| Opening headline and deck | `site.site_id`, `site.site_name`, `site.launch_date_utc`, `site.attempt_count`, `site.observed_gap_days` | Rank sites by `SUM(attempt_count)` over the period. Cite the median of non-null `observed_gap_days` for the top two, 4.1 days at Cape Canaveral SFS and 4.5 at Vandenberg SFB. |
| **Where the busiest spaceports are** — world map and selected-site readout | `site.site_id`, `site.site_name`, `site.latitude`, `site.longitude`, `site.launch_date_utc`, `site.attempt_count`, `site.observed_gap_days` | Map the six highest site totals, one circle per site. Area is proportional to `SUM(attempt_count)`. In the source SVG coordinate system the radius is `8 * SQRT(site_attempts / largest_site_attempts)`. The selector and circle clicks set the highlighted site and update its count, median gap and supported-gap count below the map. Default to Cape Canaveral SFS. Use the embedded Natural Earth path and `mappedSites.points` for the exact frozen projection; SVG x/y are presentation coordinates, not warehouse fields. |
| **Monthly activity at the busiest sites** — heatmap | `site.site_id` → row, `site.site_name` → label, `DATE_TRUNC(site.launch_date_utc, MONTH)` → column, `SUM(site.attempt_count)` → cell | Same six sites and rank order. Build all 35 month buckets from Jan 2024 through Nov 2026 and zero-fill a site-month with no rows. Use one common scale across cells (`0–9` in the frozen edition). Tooltip shows site, month, and exact count. The map selector does not filter the heatmap. |
| **Monthly counts** — collapsed table below the heatmap | Same fields and aggregation as the heatmap | Transpose the same matrix so month is the first column and each featured site has a column. Use exact values, including zeros. |
| **Time between observed attempts** — gap table | `site.site_id`, `site.site_name`, `site.launch_date_utc`, `site.attempt_count`, `site.observed_gap_days` | **Attempts** = `SUM(attempt_count)`, **Median days** = exact median of non-null `observed_gap_days`, **Supported gaps** = `COUNT(observed_gap_days)`. An exact BigQuery median can use `PERCENTILE_CONT(observed_gap_days, 0.5) OVER (PARTITION BY site_id)` then collapse to one row per site. Format median to one decimal or `—`. Rank by attempts descending, then name and ID. Show eight rows initially. |

Featured map and heatmap sites, in order: Cape Canaveral SFS (`12`, 205 attempts), Vandenberg SFB (`11`, 184), Jiuquan (`17`, 84), Kennedy Space Center (`27`, 56), Wenchang (`8`, 52), and Rocket Lab Launch Complex 1 (`10`, 47). The preceding launch is already determined over full history in the mart. Apply the date filter to the **current** attempt after gaps are computed so a valid predecessor outside the period still counts. `observed_gap_days` is conditional on both timestamps having second, minute or hour precision. It describes observed records, not guaranteed operating cadence.

## 4. Reuse & landings

Page key `reuse`. The full-width **Recorded flight history** card contains the serial selector, contextual counts, the flight strip, then the log. Below it, place **Recorded landing outcomes** and **Hardware coverage** side by side. Only the flight strip, its counts, and the log respond to the serial selector.

| Visual and position | Exact table fields | Query, encoding, and interaction |
| --- | --- | --- |
| **Serial number** selector | `booster.booster_id` → filter value, `booster.serial_number` → option label, `booster.identifiable_flight_count` → rank | For this edition show the six featured boosters in descending recorded flight count, breaking ties by serial and ID. Use the ID for the filter. Default to B1067. The exact options and keys are listed below. |
| Flight counts above the strip | `booster.identifiable_flight_count`, `booster.turnaround_supported`, `booster.turnaround_days` | `SUM(identifiable_flight_count)` for selected hardware, and `COUNT(turnaround_days)` for supported intervals. The mart emits `turnaround_days` only when `turnaround_supported` is true. Do not substitute a source-provided duration or treat missing values as zero. |
| Flight strip — one tick per recorded flight | `booster.booster_id`, `booster.launch_id`, `booster.launch_net_utc`, `booster.source_flight_number`, `booster.turnaround_days`, `launch_labels.name` | One mark per `launch_id + booster_id`, positioned by UTC flight date across that booster's first-to-last recorded dates. The frozen HTML uses UTC calendar dates for positions and Jan 1 year ticks. Keep separate marks in the data even when positions coincide. Tooltips show flight number, UTC date, launch name and supported turnaround when available. |
| Flight log directly below the strip | `booster.launch_id`, `booster.booster_id`, `booster.launch_net_utc`, `booster.source_flight_number`, `booster.turnaround_days`, `launch_labels.name` | Left join `launch_labels` on `(input_snapshot_id, launch_id)`. Column order is **Launch** (`name`), **UTC date** (date of `launch_net_utc`), **Flight** (`source_flight_number`), **Turnaround (days)** (`turnaround_days`, one decimal). Sort `launch_net_utc` descending with a stable `launch_id` tie-break. Show seven rows, then **earlier flights** in a disclosure. |
| **Recorded landing outcomes** — compact table | `landing.landing_id`, `landing.attempted_landing_count`, `landing.known_landing_outcome_count`, `landing.successful_landing_count`, `landing.unknown_landing_outcome_count` | **Successful** = `SUM(successful_landing_count)` (686), **Failed** = `SUM(known_landing_outcome_count) − SUM(successful_landing_count)` (23), **Unknown outcome** = `SUM(unknown_landing_outcome_count)` (0). The footer is `SUM(attempted_landing_count)` (709). Use landing grain and all recorded hardware in scope. |
| **Hardware coverage** — narrative card | `booster.reused_flight_count`, `booster.reuse_known_count`, `booster.identifiable_flight_count`, `launch_labels.provider_id`, `coverage.attempts_with_recorded_stage_flights`, `coverage.orbital_attempt_records` | Sum reused vs known reuse flags (636 / 776). Sum identifiable flights conditionally for `launch_labels.provider_id = 121` vs all identifiable flights (745 / 776). Join the lookup on `(input_snapshot_id, launch_id)`; `booster` itself has no `provider_id`. Show distinct orbital attempts with stage associations vs all orbital attempts directly from `coverage` (750 / 6,903). Keep the three denominators separate even when two happen to match. |

| Serial | `booster_id` | Recorded flights | Supported turnaround intervals |
| --- | ---: | ---: | ---: |
| B1067 | 101 | 37 | 29 |
| B1063 | 84 | 36 | 27 |
| B1071 | 107 | 35 | 27 |
| B1069 | 105 | 32 | 27 |
| B1077 | 116 | 30 | 26 |
| B1078 | 120 | 30 | 29 |

`turnaround_days` measures the elapsed time between supported consecutive flight records for the same hardware. Neither serial labels nor recorded landing attempts measure overall launch success. The stage coverage numerator already exists in `coverage`; its underlying `mart_stage_flight` has key `(input_snapshot_id, launch_id, stage_flight_id)` and can contain multiple rows per launch.

## 5. Upcoming & coverage

Page key `schedule`. The lede sits above the full-width launch manifest; **Entries by date precision** and **Coverage and methodology** follow as separate collapsed panels. The precision selector filters the manifest and its row count. The two context panels continue to describe the whole frozen edition.

| Visual and position | Exact table fields | Query, encoding, and interaction |
| --- | --- | --- |
| Opening headline and deck | `upcoming.launch_id`, `upcoming.interval_basis`, `upcoming.horizon_start_utc`, `upcoming.horizon_end_exclusive_utc` | Count all manifest entries (179), then count entries where `interval_basis = 'year'` (129). Display the frozen horizon dates, 11 Nov 2026 to 9 Feb 2027 UTC. These are possible windows that overlap the horizon. |
| **Precision** selector and result count | `upcoming.interval_basis`, `upcoming.launch_id` | Default All precisions. Options present in this edition: Minute, Hour, Day, Month, Quarter, Year. Apply the selected basis to the manifest rows and count distinct `launch_id` in the result. The source `net_precision_name` and `net_precision_id` remain available for provenance; `interval_basis` drives the filter and visual. |
| **Launch manifest** — launch name and provider columns | `upcoming.launch_id`, `upcoming.input_snapshot_id`, `launch_labels.launch_id`, `launch_labels.input_snapshot_id`, `launch_labels.name`, `launch_labels.provider_id`, `provider.provider_id`, `provider.name` | Left join `launch_labels` on `(input_snapshot_id, launch_id)` to get **Launch** and the provider key. Left join `provider` on release, snapshot and `provider_id` for **Provider**. Use **Unknown provider** for a missing join. Keep one row per `launch_id`. |
| **Launch manifest** — source status column | `upcoming.source_status_name`, `upcoming.scope_classification` | Render `source_status_name` as **Source status**. Add **Orbital scope unknown** beneath it if `scope_classification = 'unknown'`. Keep these records in the manifest if their windows overlap. |
| **Launch manifest** — reported window label and track | `upcoming.interval_basis`, `upcoming.schedule_earliest_utc`, `upcoming.schedule_latest_exclusive_utc`, `upcoming.horizon_start_utc`, `upcoming.horizon_end_exclusive_utc` | Format the text at source precision and draw the range clipped to the shared 90-day axis. Minute and second precision use a tick, all other supported precisions use a shaded bar. Tooltip contains original bounds and labels the upper bound exclusive. See formulas below. |
| **Entries by date precision** — collapsed horizontal bars | `upcoming.interval_basis` → row label, `COUNT(DISTINCT upcoming.launch_id)` → bar length and value label | Compute once over all supported manifest rows. Display in precision order, omit categories with no rows, and highlight Year. Frozen counts are 8 minute, 6 hour, 5 day, 19 month, 12 quarter, and 129 year. Scale against the largest count. |
| **Coverage and methodology** — collapsed facts and source note | `coverage.current_launch_records`, `coverage.orbital_attempt_records`, `coverage.unknown_scope_records`, `upcoming.launch_id`, `upcoming.scope_classification`, `coverage.unsupported_upcoming_precision_records` | Match the five visible values in order: source records (7,683), recorded orbital attempts (6,903), source records with unknown orbital scope (556), manifest entries with unknown orbital scope (`COUNT(DISTINCT IF(upcoming.scope_classification = 'unknown', upcoming.launch_id, NULL))`, 21), eligible-status records without supported date precision (34). The latter's overlap with the horizon is unknown. Keep it separate from manifest totals. |

Manifest eligibility is already enforced by the mart (`precision_supported IS TRUE`, earliest `<` horizon end, exclusive latest `>` horizon start). Window and horizon are half-open intervals. Sort by precision from finest to coarsest (`second`, `minute`, `hour`, `day`, `month`, `quarter`, `half_year`, `year`), then `upcoming.net_utc`, then `upcoming.launch_id` ascending. `net_utc` is used only as a sort field here. Show seven records, then **Show more entries**. The label uses the real, unclipped precision: year only, half year, quarter, month, day only, hour interval, minute or second UTC time.

```text
axis_start = horizon_start_utc
axis_end   = horizon_end_exclusive_utc
left       = max(schedule_earliest_utc, axis_start)
right      = min(schedule_latest_exclusive_utc, axis_end)
position   = 100 * (left - axis_start) / (axis_end - axis_start)
width      = 100 * (right - left) / (axis_end - axis_start)
```

Use timestamp differences in the same units for all three intervals. For the frozen edition, the axis starts `2026-11-11 11:38:30.399171 UTC` and ends `2027-02-09 11:38:30.399171 UTC`. Draw five labels at 0%, 25%, 50%, 75% and 100%. The prototype stores layout percentages rounded to four decimal places. The row graphic encodes possible dates and the label carries the true source precision.

## Reproduction checks

- Compare the first screen and all disclosures with the approved HTML. Check overview history range, all three reliability groupings, map selection, booster selection and manifest precision filtering.
- Keep annual values, entity ranks, monthly totals and publication metadata inside the selected release. Do not multiply launch counts through hardware, landings or family memberships.
- Warehouse lineage was checked against the dbt model SQL and the local Parquet files prepared for the frozen release. Reconciled values include the annual series, all three reliability result sets, the 28 site summaries and monthly counts, all 200 flights for the six featured boosters, the landing counts, and all 179 manifest rows before layout-only fields. The HTML remains the reference for exact typography, map geometry, static copy and disclosure behavior.
