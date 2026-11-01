# Sources and local contracts

Primary source: [Launch Library 2](https://thespacedevs.com/llapi), by The Space Devs, a community-maintained database. Production base `https://ll.thespacedevs.com/2.3.0/`. [OpenAPI schema](https://ll.thespacedevs.com/2.3.0/json), [FAQ](https://github.com/TheSpaceDevs/Tutorials/blob/main/faqs/faq_LL2.md), [terms](https://github.com/TheSpaceDevs/Tutorials/blob/main/faqs/faq_TSD.md#terms-of-use). Terms permit derived uses, discourage forwarding unchanged data, disclaim perfect accuracy and encourage attribution. Keep code and source-data licensing separate. Image licenses do not follow automatically from API access.

Verified 2026-11-01, 16:52:28–16:52:44 UTC, using eight bounded GETs in scripts/verify_source.py (no retries). Public query/response checksums and timestamps are committed in source_verification.json; unchanged bodies are local in ignored data/source-spike. Do not rerun the probe casually: it consumes shared quota. Throttle observation: 15/hour/IP; /api-throttle itself is documented as unmetered. Schema/docs also take HTTP requests and are conservatively counted in our probe budget.

Observed, not completeness claims:

- Historical query, normal mode, include_suborbital=false: reported 7,427 records; first two were Sputnik 1 (1957-10-04) and Sputnik 2 (1957-11-03). This is a point-in-time source count, never a test invariant.
- Three recent detailed records: two Falcon 9 flights and one GSLV Mk II. Latest sampled NET 2026-10-31T04:04:34Z; source update 05:09:34Z. No separate actual-liftoff field was established; keep the name NET even for historical records.
- Same Falcon record in normal mode has no rocket.launcher_stage or rocket.payloads fields. Detailed mode supplies both; 2/3 recent records had stage entries. That measures returned sample coverage, not completeness of booster histories.
- B1093: source flight number 18, reused=true, previous flight 2026-09-20T01:47:00Z, successful attempted landing; source interval P41DT2H17M34S agrees with the two timestamps. These are source observations, not independent audits.
- All three detailed recent rocket.payloads arrays were empty, including Starlink Group 15-18 whose mission description names satellites. Empty structured arrays mean zero returned entries and unknown real payload coverage.
- Filtered upcoming query (2026-11-01 through 2027-01-30) reported 106 records; first ten included second/minute/hour/day precision. Filtering last_updated >= 2099 returned zero; schema advertises both last_updated bounds and NET bounds. This establishes basic filter behavior, not a complete incremental sync protocol.
- Responses have count/next/previous/results; pagination uses same-host offset URLs. Shapes/unknown fields are retained in raw. No per-launch detail calls are needed.

Status vocabulary was read live and retained in tests/fixtures/ll2_statuses.json:

| ID | Source label | Local treatment |
|---|---|---|
| 1, 2, 5, 8 | Go / TBD / Hold / TBC | Not yet launched |
| 3 | Launch Successful | Success |
| 4 | Launch Failure | Failure |
| 7 | Partial Failure | Partial failure |
| 6, 9 | In Flight / Payload Deployed | Launched, outcome unresolved |
| Other | Unknown | Flag and fail quality; never success |

Scope requires the source query's include_suborbital=false AND a supplied mission orbit that is not suborbital/unknown. A confirmed launched status and nonfuture NET are also required for fact_launch_attempt. Missing orbit is retained as unknown scope and excluded pending review; this can conservatively undercount legitimate attempts. A source orbital counter is not a qualification test; upcoming records also contain those counters. Failure is not excluded merely for failing to reach orbit.

The installed CLI subsequently fetched a complete 1957 query (3 records, including a failure) and the complete source-NET-bounded upcoming window (106 records across two accepted pages). The first 100-record attempt timed out; its retry consumed the second request and checkpointed at offset=100. A one-request resume fetched the remaining six. This validates request-budget and replay behavior with real data, not stable-snapshot completeness. Of those 106 records, 19 have unknown orbital scope, including three HASTE records with mission=null. [Rocket Lab identifies HASTE as suborbital](https://rocketlabcorp.com/launch/haste/); no name-based override is applied. The remaining 87 have source-supported non-suborbital orbits, still not completed launch attempts.

Upcoming precision distribution: Minute 5, Hour 9, Day 6, Month 10, Quarter 4: 13, Year Half 2: 1, Year 62. Month/quarter/year placeholders often use the period's last date (for example 2026-12-31). Preserve both value and label; a NET bound selects that placeholder, not the full possible launch interval. The rolling window may omit an overlapping coarse period when its placeholder falls outside. Phase 2 must settle interval-overlap semantics before a public manifest claims completeness.

Probe timestamp caveat: the one-time script recorded request-start timestamps under observed_at_utc. Operational acquisition was updated to stamp response receipt; early bootstrap run envelopes retain their originally captured times without retrospective fabrication.

| Parquet table | Grain / unique key |
|---|---|
| launch_record_current | One latest source record per launch_id within this run; includes upcoming |
| fact_launch_attempt | Qualifying launched orbital subset; launch_id |
| launch_observation | One distinct semantic launch-record hash within run; launch_id + record_hash; excludes media, counters and update-time-only changes |
| dim_provider | One provider_id; type and list of current source countries |
| dim_vehicle_configuration | One configuration_id (variant) |
| dim_vehicle_family | One family_id; known parent and field-presence flag |
| bridge_configuration_family | configuration_id + family_id; multiple families must not multiply attempts |
| dim_launch_site | One site_id (location); coordinates and current geographic country |
| dim_pad | One pad_id, linked to site_id; independent coordinates |
| stage_flight | launch_id + stage_flight_id; source hardware, flight number, reuse and optional landing fields |

Typed UTC timestamps remain distinct for NET, source update, observation and source launch windows. Precision ID/name remains nullable; absent precision is not assumed second precision. Future times are schedules, not completed events. Source country metadata is current, not historical state attribution. Missing hardware/landing fields stay null; no reuse or landing denominator is inferred from an empty list. No payload table or public total is produced.

The fixture is a documented, reduced six-record public subset. It can reproduce normalization offline; it cannot establish representative reliability or full extraction coverage. Synthetic edge cases exist only in tests.

Future sources: GCAT for reviewed identifier crosswalks and measured payload coverage (CC BY 4.0); CelesTrak only for a defined orbital extension; account-gated ESA DISCOS only after terms/access validation. None is downloaded or required in Phase 1.
