# Phase 2 execution plan — resumable history and observed changes

**Adopted execution plan, 7 November 2026.** This was drafted against commit `3f73804` and is now being executed. Local implementation batches P2.0–P2.7 are in place; bounded refresh and 1957 verification have run. Full historical acquisition and final verification remain open. The latest dated source evidence and actual progress are in [phase2_verification.json](phase2_verification.json) and [handoff](handoff.md). Keep implementation completion distinct from acquisition completion.

**Intended result.** One local pipeline maintains current source records and meaningful observed changes across runs, refreshes recent/upcoming data, and backfills orbital history from 1957 with durable progress. An interruption or replay cannot duplicate accepted state or erase prior evidence. Coverage, uncertain dates and unknown scope remain visible. No cloud account is required.

The execution order is **P2.0 → P2.1 → P2.2 → P2.3 → P2.4 → P2.5 → P2.6 → P2.7**. Each batch should end with a reviewable local commit, its relevant checks, and an updated handoff. Implementation and real extraction completion are separate milestones.

**1. Starting point and concrete gaps**

| Current behavior | Phase 2 work |
|---|---|
| `pipeline.ingest` preserves responses and a per-run next URL. | Add frozen extraction windows, parent backfill/refresh plans and complete-window progress. |
| `normalize` selects and deduplicates records inside one run. | Persist latest accepted records and observed changes across runs. |
| Observation key is `(launch_id, semantic_hash)`. | Preserve a later return to an earlier value: A → B → A must remain three observations. |
| Current selection prefers source-update time and then receipt time. | Handle late older records and equal-timestamp conflicts explicitly. |
| Precision ID/name is retained, but filtering uses a NET placeholder. | Derive uncertainty intervals and evaluate overlap with the requested manifest window. |
| Every allowed source query requires `include_suborbital=false`. | Provide a controlled correction/reconciliation route that can detect records leaving orbital scope. |
| Request budget belongs to one `LL2Client` instance. | Share one invocation budget across all refresh components and auxiliary requests. |
| Per-run Parquet outputs are replaceable. | Produce consistent persistent-state exports with revision/coverage metadata and recoverable materialization. |

Keep raw JSON, the existing installed package layout, Python 3.12, uv/uv.lock, httpx/Pydantic/Polars and offline default tests. Preserve working Phase 1 CLI behavior unless an explicit migration is documented.

**2. Proposed implementation decisions**

These are draft choices to record in docs/decisions.md when implementation adopts them. They do not change the current architecture merely by appearing here.

| Choice | Proposed default and reason |
|---|---|
| Local persistent state | Add `data/state/launch_atlas.sqlite` using Python's standard-library sqlite3. Transactions are needed to couple current records, observations, accepted inputs and watermarks. This adds no server or third-party package; raw remains filesystem-backed and analytical outputs remain Parquet. SQLite is local acquisition state, not a substitute for the proposed BigQuery/dbt warehouse. [Python documentation](https://docs.python.org/3.12/library/sqlite3.html). |
| Raw evidence | Preserve immutable response bytes and checksums. Keep current raw directories readable and old manifests intact. New manifests receive an explicit schema version and response-receipt timestamps. |
| Atomicity | Commit validated state changes and the relevant window watermark in one SQLite transaction. Export Parquet after that commit into a new generation; advertise that generation only after all files pass checks. |
| Observation identity | Separate reusable semantic content versions, record sightings, and meaningful observation events. A content hash is not the primary key of the observation timeline. |
| Scope | Continue excluding unknown/suborbital scope from orbital facts while retaining records and missingness. Broaden only the bounded correction/reconciliation query where necessary to detect reclassification; preserve actual query scope in provenance. |
| Window policy | Freeze bounds, source endpoint, filter scope, ordering, page size and policy/parser versions before the first page. Resume uses that saved plan. |
| Refresh defaults | Recent NET lookback: 30 days. Upcoming display horizon: 90 days. Initial incremental overlap: 48 hours; source-update cutoff: invocation start minus 5 minutes. These are configurable project safeguards, not provider guarantees. |
| Request sizes | Keep smoke defaults unchanged. Start historical detailed pages at 100 and refresh pages at 50; measure latency/bytes and record any revision. Changing size after repeated timeouts restarts that bounded window/pass explicitly, not an arbitrary offset with changed semantics. |
| Execution | Explicit local commands only. Scheduled operation remains Phase 4. A missing credential or spending decision does not block this phase. |

The provider currently documents a free limit of 15 requests/hour/IP, at most 100 records/page, offset pagination and detailed responses. Upcoming results may retain recently completed launches. Recheck these constraints before the live acceptance run; every source request/retry remains subject to the shared budget. [LL2 FAQ](https://github.com/TheSpaceDevs/Tutorials/blob/main/faqs/faq_LL2.md).

**3. State contracts and ownership**

Use one canonical local data root and writer lock for backfill, refresh and reconciliation. Preserve the existing quota ledger independently of data transaction rollback: an HTTP request still consumed quota even if the data transaction failed. Local coordination does not cover other computers or unrelated consumers of the same public IP.

| Logical state | Grain / key | Required behavior |
|---|---|---|
| Ingestion run / invocation | `run_id`; one execution attempt | Start/end, code/parser/config versions, budget and actual requests, component statuses/errors. |
| Extraction plan/window | `plan_id`, `window_id` | Immutable source/query bounds; parent/child relationships, pass number, page cursor, validation and commit status. |
| Raw request evidence | `run_id + request_ordinal` | Reserved request, response/transport result, receipt time, checksum/path; preserve retries and failures. |
| Semantic version | `launch_id + semantic_hash_version + semantic_hash` | Canonical launch-relevant content; reusable when the same content returns later. |
| Record sighting | Acquisition occurrence + `launch_id` + record ordinal | Link every accepted retrieval to its raw record and source-update time, including unchanged and stale observations. Reprocessing that acquisition is idempotent. |
| Meaningful observation | Stable `observation_id`, linked to sighting/version | First observed state or a real accepted transition; observed time distinct from source-update time. A → B → A retains three events referencing two content versions. |
| Launch current | `launch_id` | Latest accepted source representation, semantic version, raw reference, first/last seen and review flags. An older backfill record cannot overwrite newer accepted state. |
| Stream cursor / coverage | `stream_id` or historical `window_id` | Last committed upper bound, latest complete check and coverage/reconciliation status. No implied global completeness from one successful page. |
| Local export generation | `state_revision` / `generation_id` | File checksums, row counts, included window revisions, coverage and validation. Single atomic manifest points to the latest usable local generation. |

Keep normalized launch/provider/configuration/family/pad/site/stage grains. A correction removes obsolete current stage/landing associations for that launch while preserving earlier evidence. Entity labels/countries/coordinates remain current source metadata, not historical Type 2 dimensions. Landing outcomes remain separate from launch outcomes. Do not let source all-time hardware/provider counters become historical flight facts.

Use explicit SQLite transactions, foreign keys, schema migrations, UTC serialization and parameterized statements. Test the transaction settings on the supported Python version. Avoid implicit datetime adapters and filesystem copies of an open database as a backup strategy. Rebuild a disposable database from raw input as the primary recovery proof.

**4. Date, status and observation rules to implement first**

**Scheduling intervals.** Preserve source NET, precision ID/name and launch-window bounds. Add `schedule_earliest_utc`, `schedule_latest_exclusive_utc`, `interval_basis` and `precision_supported`. Interpret known second/minute/hour/day precisions as their corresponding UTC buckets; month, quarter, half-year and year as calendar periods. Verify all currently configured precision enums/descriptions before freezing mappings. Do not infer an unrecognized precision from its label. Preserve source window bounds separately; do not silently intersect conflicting definitions.

A source NET of `2026-12-31` with Year precision describes a 2026 calendar interval, not a precisely scheduled 31 December launch. For analysis horizon `[A, B)`, include an uncertain schedule when `earliest < B` and `latest_exclusive > A`. An exact/second-level time outside the horizon remains excluded. Label interval overlap as uncertainty, not proof the launch will occur within 90 days. Unsupported precision or an inconsistent period is retained for review and prevents a claim of complete upcoming coverage.

**Acquisition envelope.** Local overlap logic cannot recover an unfetched record. Because supported coarse intervals can span a year, retrieve an upcoming envelope covering the relevant whole calendar years, then apply the 90-day interval predicate locally. For a November-to-February horizon this includes the relevant portions of both calendar years, even if an annual placeholder is at the following December's end. Record the extra fetched range, number of pages and uncertainty coverage. Split the envelope into bounded calendar subwindows if needed. This can cost substantially more than a narrow 90-day NET query. If quota prevents completion, checkpoint and label coverage incomplete; do not narrow the query silently.

**Changes.** Create a transition for changed NET, precision, status, mission/orbit/scope, provider/configuration/pad identity or historical hardware-flight/landing fields. Sort unordered collections and normalize timestamps before hashing. Exclude media, counters and source-update-only changes; do not hash an entire nested payload object accidentally. Payload completeness stays unknown. Record metadata-only retrieval changes without adding fictitious launch/schedule events.

For equal precision, record the NET movement and its signed interval/time difference. For coarse intervals, distinguish later/earlier disjoint periods from overlapping uncertainty. For different precisions, record refinement/coarsening or a general schedule revision; no numeric delay unless a separately defined comparison is defensible. Historical schedule analysis starts at the earliest actual observation captured by this project, never the launch date or a later import date.

**Ordering and conflicts.** Newer source-update time may update current state. Older observations are retained and flagged without replacing it. Equal source-update time plus different semantic content becomes a visible conflict requiring reconciliation; do not invent source chronology from file order. Replaying an already applied acquisition changes neither the transition count nor current state. Repeated unchanged acquisitions update last-seen/source-check evidence. A parser/semantic-hash migration is a reprocessing event, not an invented source change.

**Scope and statuses.** Retain the conservative orbital rule already recorded in docs/metrics.md. Unknown orbit is not proof of suborbital flight; known failure to reach orbit is not proof the attempt was suborbital. Preserve status enums; unknown status fails the affected validation gate. A past NET alone never establishes liftoff. Treat In Flight/Payload Deployed as unresolved launch outcomes as currently defined. Time passing alone cannot turn Go/TBD/TBC into an attempt. Source reclassification must update the current fact while preserving its earlier observed representation.

**5. Window completion, consistency and recovery**

An extraction window passes through distinct states: `planned → extracting → extracted → validated → committed → exported`. Budget stop, source throttling and retry wait are paused states with a saved cursor; schema/quality/conflict failures are named failures. Keep `extraction_complete`, `validation_passed`, `state_committed` and `export_ready` distinct rather than overloading the existing `complete` flag.

For each page: reserve quota/request budget → fetch → atomically store raw → persist accepted page/cursor → parse and stage. Raw stays available if parsing fails. Retain partial staging, but do not advance that window's committed cursor or claim a successful refresh. Once all pages are durable, validate the full window and merge it transactionally into persistent state. A parent plan tracks child windows independently; an accepted child does not make unfinished siblings complete.

The source uses mutable offset pages. Frozen filters and ordering do not create an upstream snapshot. Use bounded windows, duplicate/cycle/count diagnostics, overlap and repeat reconciliation passes. For closed historical partitions, require two consecutive completed passes with matching distinct IDs and launch-relevant content before labeling the partition reconciled. A source count match alone is insufficient. Cap consecutive unstable attempts, retain evidence, and return a reviewable incomplete result rather than loop indefinitely. This is a convergence check, not an independent census or a proof of an immutable source snapshot.

Incremental source-update windows use documented inclusive bounds, initially `[previous_cursor - 48h, frozen_cutoff]`. Handle boundary ties through overlap/idempotency; verify supported ordering and any secondary sort rather than assuming composite/keyset pagination. A valid empty complete window can advance to its frozen cutoff. A failed/partial window cannot. Reconcile incrementals against recent full-window scans; a `last_updated` feed alone cannot prove deletion/reclassification coverage.

Crash behavior must be explicit:

| Failure point | Recovery / invariant |
|---|---|
| After request reservation, before response | Reservation remains consumed; resume without promising a response exists. |
| After raw write, before page checkpoint | Discover/reuse the durable artifact or safely refetch; no accepted input duplicated. |
| After page checkpoint, before normalization | Replay staged raw with no required network call. |
| During persistent-state merge | SQLite rollback leaves current records, observations and committed cursor unchanged. |
| After database commit, before manifest update/export | The committed-input ledger prevents a second application; regenerate manifests/export from the committed revision. |
| During Parquet export | Prior usable export manifest remains selected; rebuild the new generation without refetching or creating observations. |

Database commit is the authoritative acceptance point; new run manifests are audit/readback views of it, not an independent conflicting authority. Export files refer to a specific consistent database revision. These are local recovery guarantees, not validation of future BigQuery publication/rollback.

**6. Ordered implementation batches**

| Batch | Changes / primary files | Acceptance gate |
|---|---|---|
| **P2.0 — baseline and specification** | Run existing checks. Inventory cached raw/manifests and sample sizes. Add nonempty multi-run fixtures and policy specifications. Record chosen draft decisions with evidence. | Existing behavior still passes; each fixture states whether public, reduced or synthetic. No public API needed for the core fixture suite. |
| **P2.1 — precision and change semantics** | Add `time_precision.py`, `observations.py`; refactor `normalize.py` to expose pure record parsing, canonical projection and explicit scope evaluation. | Calendar rollover/leap-year/boundary, unknown precision, hash stability, A → B → A and status/scope tests pass. Returned tables retain their declared grains. |
| **P2.2 — persistent state** | Add `storage/state.py` and schema/migration definitions; transactional version/sighting/observation/current/cursor storage; import legacy evidence explicitly. | Repeat apply is idempotent; stale records do not overwrite current; migration/rebuild and rollback are verified. Existing raw files/manifests are not rewritten. |
| **P2.3 — extraction windows and shared budget** | Add `windows.py`; refactor `pipeline.py`, `clients/ll2.py`, `config.py` and `storage/local.py` around a single invocation context. | Every source call, including retries/config/verification calls, consumes the same cap; multi-component resume and every crash boundary above are exercised. |
| **P2.4 — persistent refresh** | Add `refresh.py`; unified last-updated corrections, 30-day recent reconciliation and broad upcoming acquisition with local interval filtering; wire `refresh` into the same pipeline. | Cross-endpoint IDs deduplicate; completion, component freshness and unknown coverage are explicit. An upcoming failure cannot mark the whole refresh successful. |
| **P2.5 — historical backfill and reconciliation** | Add `backfill.py` and `reconcile.py`; parent plans with calendar-year partitions, adaptive splitting of dense/unstable years into months, and repeat-pass checks. | Interrupted multi-window runs resume; completed child windows are not lost; moved/reclassified/missing records produce reviewable diagnostics. |
| **P2.6 — exports, diagnostics and operations** | Add `coverage.py` and `storage/exports.py`; persistent Parquet snapshots, status output, run/coverage summaries and documented rebuild/retention controls. | Bad candidate/export leaves the previous usable export; no launch-count multiplication; fresh checkout can replay and rebuild state offline. |
| **P2.7 — bounded live acceptance and history execution** | Verify uncertain source capabilities, capture genuine observations at multiple times, exercise stop/resume, then run the historical plan across quota windows. Update README/architecture/metrics/operations/roadmap/handoff. | Evidence distinguishes implemented capability, observed live operation, completed history and outstanding coverage. Do not mark full Phase 2 complete while required historical partitions remain unfinished. |

New file names are proposed module boundaries, not a requirement to create empty files. Split an implementation batch further if its review surface becomes large.

**P2.4 refresh behavior.** Prioritize pending recent/upcoming acquisition so change collection starts early; continue/resume incremental windows fairly so none is starved by a large manifest. Once initialized, process: pending committed-cursor correction window, recent launched-record reconciliation, upcoming envelope, validation/merge and generation export. Freeze the complete component plan on first invocation. One `max_requests` cap applies to the invocation, not separately to each endpoint. Preserve per-component checkpoints between invocations. Set last-successful-full-refresh only when every required component is complete; record last attempted and last successful component check separately.

The bounded corrections query should include changed out-of-scope records where supported, so a previously orbital record cannot disappear from the feed merely by becoming suborbital. This requires an explicit query-context exception to today's orbital-only allowlist and a parser that derives scope from source fields rather than assuming every returned record is orbital. Verify the boolean filter behavior before adopting it. If unsupported, use verified batched tracked-ID reconciliation without the orbital filter. Either route must pass URL/query allowlisting and quota controls; do not implement per-launch request loops or begin a suborbital census.

**P2.5 history behavior.** Freeze an `as_of_utc`, plan all NET partitions from 1957 up to it, and track requested, fetched, reconciled, unknown-scope and failed coverage separately. Start with the small 1957 fixture/live partition, then one dense recent partition before scaling. Maintain recent/upcoming observations while the older backfill progresses. Bootstrap the correction stream at backfill start and retain that start boundary so later source updates can repair earlier partitions. Do not initialize a cursor to “now” merely because an old cached sample exists.

Scan tracked records missing from a reconciled partition using the verified bounded correction/batched-ID route. A record moved to another date partition, reclassified, or absent from a truncated upcoming horizon is not deleted. Record `not_seen_in_reconciliation` with evidence and check scope/identity before drawing any conclusion. Never hard-delete the historical raw/observation trail; unresolved absence stays unresolved. Track newly appearing historical IDs and changes to qualifying counts explicitly.

**P2.6 coverage behavior.** Establish a reproducible baseline from the cached 106-record extract, including its 19 unknown-scope records; those are dated observations, not future test constants. List affected source IDs and reasons (missing mission/orbit, unsupported classification or contradiction), preserving unresolved cases. Any later manual scope override needs a separate versioned evidence/reviewer/reason record; it cannot change the retained source classification or be inferred from a fuzzy launch name. Default Phase 2 behavior remains exclusion with explicit coverage.

Report coverage per extraction window and historical year: source-reported count, fetched/distinct IDs, qualifying attempts, scope exclusions, outcome categories, missing/unsupported precision, missing provider/configuration/site/coordinates, presence of the stage-detail field versus actual stage rows, identifiable non-placeholder hardware, and landing attempts with known versus unknown outcome. Distinguish data absence from fields not requested; distinguish planned upcoming hardware associations from flown stages. Show numerator and denominator beside each coverage ratio, returning unknown for a zero denominator. Empty structured payload arrays still do not establish actual payload coverage. A broad source-count difference triggers review; it does not justify manufacturing missing records or silently changing scope.

**7. Required test matrix**

Use real cached public input plus clearly labeled synthetic mutations. Default tests stay offline; mock transports model pagination, source changes and failures. Persist assertions across independent connections/process restarts where recovery behavior is at issue.

| Case | Expected result |
|---|---|
| Same record in two independent runs | One current record; last-seen advances; no false semantic transition. |
| Replay/reapply the same raw acquisition | No additional sighting/transition/current mutation or watermark movement. |
| A → B → A | Three chronological observations; the repeated content version is reused without collapsing the final event. |
| Older backfill arrives after a newer refresh | Evidence retained; current state remains newer. |
| Same source timestamp, different content | Conflict surfaced; no silent last-file-wins overwrite or successful affected-window commit. |
| Counter/media/update-time-only change | Retrieval evidence updates; no launch/schedule transition. |
| Parser/hash-version change | Explicit reprocessing lineage, not fabricated source history. |
| Precision refines Month → Day | Precision change preserved; no automatic postponement in days. |
| Quarter/half-year/year boundary or leap day | Correct UTC calendar interval and exclusive end; source value unchanged. |
| Coarse interval overlaps horizon but NET is outside | Wider acquisition fixture contains it; local overlap includes it with uncertainty. |
| Unsupported/inconsistent precision | Raw retained; incomplete-coverage flag/review prevents a false complete manifest. |
| Upcoming endpoint contains a launched record | Current representation deduplicates by ID; status/time/scope controls fact membership. |
| Past NET with Go/TBD/TBC | Not counted as a launched attempt. |
| Success/failure/partial/in-flight/deployed | Launch denominator and classified-outcome denominator remain distinct. |
| Orbital → suborbital/unknown correction | Correction can be acquired; current fact membership changes; old evidence remains. |
| Multiple families, stages and payload entries | Attempt count remains distinct launch IDs; landing failure is not launch failure. |
| 429, 503, timeout, budget stop during component two | All calls count; cursor persists; no commit/full-refresh success for unfinished work. |
| Inclusive source-update timestamp ties / zero-result valid window | Ties deduplicate; a truly complete empty window advances only to its frozen bound. |
| Offset records move between pages | Repeat/overlap reconciliation detects divergent sets; never equate page exhaustion/count equality with a source snapshot. |
| Every crash point in section 5 | Deterministic recovery and coupled state/cursor consistency. |
| State transaction or export validation fails | Previous accepted state/export remains usable as applicable; failure evidence retained. |
| Rebuild into disposable state from retained raw | Same current records, observations, source timestamps, coverage and semantic hashes; no invented observation dates. |
| Legacy Phase 1 fixture/import | Original raw bytes and recorded acquisition time retained; no claim it covered a wider source window. |

**8. Planned CLI and operational outputs**

The following are target interfaces, **not runnable commands today**. Final flags should preserve existing single-run `--run-id`/`--resume` behavior and reject incompatible options. New `--plan-id` identifies a multi-window operation, not an HTTP invocation.

```text
uv run python -m launch_atlas refresh --max-requests 8 --run-id <refresh-run>
uv run python -m launch_atlas refresh --resume --run-id <refresh-run> --max-requests 8
uv run python -m launch_atlas backfill --start-year 1957 --end-year 2026 --plan-id <history-plan> --max-requests 8
uv run python -m launch_atlas backfill --resume --plan-id <history-plan> --max-requests 8
uv run python -m launch_atlas reconcile --plan-id <history-plan> --max-requests 4
uv run python -m launch_atlas status --json
uv run python -m launch_atlas rebuild-state --offline --output-dir <disposable-state-directory>
```

Retain working offline `replay --run-id` as per-run reconstruction. Applying raw inputs to persistent state must be explicit and idempotent, through a shared internal merge API or a clearly named CLI option; replay must not silently become a persistent-state mutation. An offline rebuild writes alternate state/export output while using original raw evidence, and cannot reset the live quota ledger or replace accepted state without an explicit switch.

Preserve exit-code meanings: 0 for completed requested work/successful offline inspection, 2 for safe incomplete progress, 1 for source/schema/validation/state errors. `status` can successfully report an incomplete plan without pretending that plan finished. Print both invocation and cumulative request counts, completed/pending windows, records seen/new/changed/unchanged/stale/conflicting, exclusions, precision/scope coverage and earliest permitted retry. Include generation/revision, last source check, last full refresh and backlog age; no published-release timestamp exists in Phase 2.

**9. Quota, backfill duration and retention**

The documented free limit is shared per IP. Keep a default ceiling of 8 requests per invocation, with source throttling authoritative; no endpoint receives an independent allowance. A manual historical batch runs only when enough shared allowance remains. Live acceptance starts with at most 3 metered requests, resumes only within the ledger/provider allowance, and reports anything not verified rather than spending an unbounded probe budget.

The 1 November source count was 7,427 historical orbital-filtered records. At 100/page, an unpartitioned single pass would have required about 75 successful pages. That is an old sizing observation, not a current expected row count. Year/month partition overhead, two-pass checks, broader upcoming acquisition, corrections and retries increase the total substantially. Do not promise a same-session/full-history completion time; record requests and remaining windows after each invocation. No hourly operational SLA is introduced by this plan.

Initial Phase 2 retention: keep all raw evidence, versions, observation history, acceptance manifests and the artifacts needed for offline rebuild. Do not enable automatic deletion. Measure bytes by source/run and changed versus unchanged retrieval; warn at a configurable local disk threshold, and stop safely on insufficient space. A later pruning policy must protect every referenced artifact or retain an equivalent replayable canonical input with provenance. Deleting duplicate full responses is deferred until that proof exists. No new cloud storage charge is involved.

**10. Live verification and final exit criteria**

Before using quota, pass offline tests and run the source request plan in dry-run/inspection mode. In the first bounded live check, verify current precision/status vocabulary, last-updated filters/order, and the proposed correction query behavior; record which assumptions remain untested if the budget cannot cover all checks. Do not call a documentation lookup a complete live filter verification.

Then demonstrate a real recent/upcoming acquisition, a deliberate small-budget stop/resume, and a second observation at a later time. Real source values may not change between checks; prove authentic unchanged ingestion honestly and use labeled synthetic mutations for deterministic change/reclassification tests. Do not manufacture a “real schedule change” for the demo. Run the 1957 and a dense recent historical partition before the remaining history. Record full-backfill progress until every requested partition has met its defined completion/reconciliation gate or is explicitly unresolved.

Phase 2 is complete only when all applicable items below have evidence:

- [ ] Persistent current records and meaningful observations work across runs; unchanged retrieval, corrections and returns to earlier values behave correctly.
- [ ] Supported date precisions have tested intervals; wide acquisition plus local overlap handles 90-day boundaries, and unsupported precision/unknown scope is visible.
- [ ] Recent/upcoming refresh uses the canonical ingestion path, one budget, saved component progress and truthful freshness.
- [ ] Historical windows from 1957 to the frozen cutoff have completed/reconciled coverage; pending/conflicting windows are not labeled complete.
- [ ] Interruptions, invalid pages, timestamp ties, throttling and reclassification cannot silently lose accepted inputs or advance an unfinished window cursor.
- [ ] Orbital and outcome denominators are defensible within documented source/unknown-scope coverage; raw source counts are not presented as independently verified worldwide totals.
- [ ] Persistent state can be rebuilt offline from retained evidence; current export revisions validate without stage/payload/family multiplication.
- [ ] `uv sync --locked`, ruff check/format, pyright and relevant pytest tests pass. Fresh-checkout setup and isolated installed-package behavior remain verified.
- [ ] Windows is checked locally; Linux is checked in an available Linux runner or explicitly reported unverified. Remote CI remains unexecuted unless a remote is separately authorized.
- [ ] README, ARCHITECTURE, ROADMAP, SETUP and source/metric/decision/operation/handoff documents accurately distinguish implementation, actual extraction and remaining limitations.

Finishing code with a partial full-history extraction is an intermediate delivery: **Phase 2 implementation verified; historical acquisition still in progress**. Retain that wording until the data exit criteria are met.

**11. Decisions and work that remain outside this draft's execution scope**

No cloud prerequisite blocks the first implementation batches. The local SQLite choice, broad correction query, hash/event identity, overlap/safety defaults, precision semantics and completion terminology should become short decision records as they are implemented; revise this draft when tests or source evidence justify a different choice.

GCP account/project/region/spend, repository name/visibility and confirmation of the Google reporting product remain later user decisions. BigQuery/dbt SQL, cloud identities, production release publication/rollback, Dagster scheduling, GitHub scheduled refresh, dashboard creation, website changes and GCAT enrichment remain in their later phases. Do not add a daemon, billable resource, public report, remote push, other-project change or notification automation to deliver Phase 2.
