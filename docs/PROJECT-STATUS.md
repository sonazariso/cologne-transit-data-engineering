# Project Status

**Project:** Cologne Transport Intelligence
**Repository:** `sonazariso/cologne-transit-intelligence`
**Status date:** 2026-10-10

## Authoritative status

| Stage | Name | Status |
|---|---|---|
| 0 | Project Definition | APPROVED |
| 1 | Source Inventory & Permission | APPROVED |
| 1A | SQL Server Stabilization (S87) | COMPLETE |
| 2 | MDD/TRIAS Source-Contract Audit | APPROVED |
| 3 | Static GTFS Profile / Realtime Evidence Validation | COMPLETE |
| 4 | Cologne Scope & Mode Classification | COMPLETE — APPROVED |
| 5 | Database Architecture | COMPLETE — APPROVED ARCHITECTURE |
| 6 | Static Warehouse & Analytics Design | COMPLETE — APPROVED DESIGN; PHYSICAL IMPLEMENTATION DEFERRED |
| 7 | Realtime Pilot Design | COMPLETE — APPROVED PILOT DESIGN |
| 8 | Small Realtime Pilot | COMPLETE |
| 9 | Matching & Data Quality | COMPLETE — APPROVED MATCHING & DATA QUALITY SEMANTICS |
| 10 | Operational Fact | NOT COMPLETE — IMPLEMENTATION CORRECTED; SOURCE/DATABASE VALIDATION BLOCKED |
| 11 | Power BI Model | COMPLETE — APPROVED DESIGN; PBIP/DESKTOP IMPLEMENTATION DEFERRED |
| 12 | 50-Station Selection | COMPLETE — APPROVED FIXED 50-STATION PANEL |
| 13 | Tiered Collector Readiness | COMPLETE — APPROVED TIERED COLLECTOR READINESS DESIGN; PRODUCTION NOT STARTED |
| 14 | Collector Implementation and Legacy7 SQL Runtime | TIERED50 ACTIVATION OUTAGE — NO ACTIVE STATIONS; LEGACY7 RETIRED |
| 15 | Post-Cutover Validation | NOT STARTED — FIRST SCHEDULED TIERED50 HEARTBEAT FAILED |
| 16 | Revisit Timing/Data Coverage Issues | Not started |
| 17 | Actual Physical Delay Productionization | Not started |

## Current active architecture

### Current operational cutover — 2026-10-08

The exact Windows Task Scheduler task `\Cologne Transit Realtime Collector`
remains retired. SQL evidence confirms no newer Windows collector runs or
realtime observations after the authoritative final Windows boundary.

The final Legacy7 Mac run was
`05d97f32-7c4a-4a6c-993c-fd496c609cb8`, logical bucket `5971511`, completed
at `2026-10-08 09:57:32.860000` UTC. Preserved historical evidence accounts for and succeeds
all six expected Legacy7 buckets `5971506` through `5971511`; this is an
availability and retirement boundary, not evidence that Legacy7 remains a
production fallback.

Legacy7 was then unloaded and disabled. Its historical implementation,
observations, timing history, sampling eras, target IDs 1–7, and methodology
evidence remain preserved. The replacement Tiered50 LaunchAgent is installed
in a separate versioned bundle at
`~/Library/Application Support/CologneTransitIntelligence/runtime-bundles/`
and was briefly started only through the scheduled path after administrator
activation. It is now disabled again after the first scheduled heartbeat
failed its cadence gate. No two production writers are running, and no manual
MDD or Heumarkt request was issued.

The additive SQL migration and exact Tiered50 materialization are applied:
43 target rows use IDs 8–50, all seven historical IDs remain intact, and the
administrator activation transaction and sampling era were committed before
the runtime was stopped. The fixed panel retains 8/20/22
tier counts, 5/15/30-minute cadences, 20/30/30 result limits, phase sequence
`19/18/18/18/19/18`, cap-2 concurrency, and two-attempt policy.

Current production status is **BLOCKED — TIERED50 STOPPED AFTER FAILED FIRST
SCHEDULED HEARTBEAT; LEGACY7 RETIRED**. The first scheduled bucket
`5971541` ran 18 target audits: 2 succeeded and 16 were skipped by the
heartbeat budget, with 2 HTTP responses and 2 persisted/confirmed permits.
The LaunchAgent was stopped and the outage was recorded; zero stations are
currently collecting and no validated production cycle exists.

The current review replaces the relative Tiered50 timer with a disabled,
wall-clock `StartCalendarInterval` schedule and adds deadline/coverage
evidence without altering the panel or quota limits. The captured production
evidence remains explicitly historical; the [scheduled runtime contract](operational/tiered50-scheduled-runtime-contract.md)
records the corrected source/deployment behavior and the remaining activation
gates.

The latest source/deployment reconciliation installed the corrected Tiered50
plist in the disabled, unloaded state (`RunAtLoad=false`, twelve `00..55`
calendar entries, no `StartInterval`, and no `ThrottleInterval`). The current
read-only capture separates the limited runtime login from privileged evidence:
`admin_role_source=MSSQL_ADMIN_*_KEYCHAIN`, and the dedicated administrator
read-only path proves the actual `DataAnalyst-VM` /
`CologneTransitIntelligence` target, capacity, backup, permissions, quota, and
collector gates. The actual runtime permission inspection passed all 34
positive matrix permissions and denied every prohibited privilege. The only
identified schema drift was the missing `ctl.MddMacProductionResumeEvent`; the
scoped repair is now committed and a fresh independent administrator check
confirms the table, source-defined constraints, and history query are valid
with no SQL error 208. The stable current evidence is unsigned and still lacks
Windows VM disk capacity because VMware guest operations require the running
VM's encryption password. No Resume authorization was granted. Tiered50
remains stopped and zero stations are collecting; no activation or new MDD
request was issued.

The bounded conservative October bootstrap is registered and active for its
approved interval. It uses the dashboard observation of 554 requests against
the 250000 monthly limit plus 165 confirmed local permits, 14 conservative
uncertain permit rows, 0 pending reservations, and 73 explicitly identified
pre-bootstrap research attempts (`LIVE_RESEARCH_SINGLE_1`): 806 accounted
requests at approval. The latest SQL ledger read shows 808 conservatively
accounted attempts, including 721 confirmed HTTP responses, 87 uncertain live
attempts, 2 post-cutoff local attempts, and 0 pending permits; this is not
provider-wide usage proof. Headroom is 209194 at the normal operational maximum,
206694 at the retry-protection boundary, and 249194 at the provider ceiling.
The original proposal still has `approved: false`; the separate explicit
approval is recorded in the SQL authorization and evidence artifact.

Provider measurement freshness, reset bounds/timezone, counter semantics,
post-cutoff usage, and external evidence remain unresolved. The automatic
future-month rollover implementation is present but remains fail-closed until
either an exact provider reset contract is verified and registered or the
bounded policy is explicitly approved and registered. The October stop remains
`2026-10-30T23:00:00Z`; it is not extended by rollover.

The supplied Monitoring screenshot displays 554 cumulative requests against a
250000 monthly limit for the selected October 2026 category. The operator has
attested exclusive use of the account/application credential, but that is not
independent provider verification; measurement freshness, reset
bounds/timezone, counter semantics, post-cutoff usage, and external evidence
remain unresolved. The cutover is recorded as an auditable outage, and Legacy7
will not be reactivated. See the [dashboard evidence](operational/mdd-october-monitoring-dashboard-evidence.json),
[bootstrap evidence](operational/mdd-october-conservative-bootstrap-evidence.json),
[deployment validation evidence](operational/tiered50-deployment-validation-evidence.json),
[historical operational evidence](operational/tiered50-production-evidence.json),
[current operational evidence](operational/tiered50-production-evidence-current.json),
and [operator report](operational/tiered50-deployment-validation-report.md).

Microsoft SQL Server is the sole authoritative project database. The official
database is `CologneTransitIntelligence` on the current Windows VM. Future
collectors and pipelines are planned as Python on macOS, SQL Server remains on
the VM, and Power BI consumes SQL Server analytical/warehouse data.

The legacy `NRWTransportDW` database was classified
`LEGACY_SAFE_TO_ARCHIVE`. The verified archive was confirmed on external USB
storage, no required Köln asset needed migration, and the database was removed
from the Windows VM on 2026-10-07. The verified native backup and ZIP remain
the recovery source; the [removal evidence](archives/nrwtransportdw/20261007T112120Z/NRWTransportDW-removal-evidence-20261007T120030Z.json)
records the pre-drop files, dependency gate, drop result, recovered capacity,
and post-removal validation.

The Stage 1 SQL Server stabilization is **COMPLETE**. Fresh 2026-10-07
evidence is captured in the [stabilization record](stages/01-sql-server-stabilization.md)
and [evidence JSON](stages/01-sql-server-stabilization-evidence.json):
`CologneTransitIntelligence` is ONLINE, the new native `COPY_ONLY`, compressed,
`CHECKSUM` FULL backup passes HEADERONLY, FILELISTONLY, and VERIFYONLY, and a
full read-only `DBCC CHECKDB` reports no corruption errors. The verified
606,586,880-byte VM backup and matching Mac safety copy are retained on the
user-provided `CologneTransitData` share. Only `C:` is visible as a fixed SQL
volume and its headroom remains low, so the share remains the safe backup
destination; the later operator-confirmed Windows-task retirement is recorded
above and is separate from this stabilization capture.

PostgreSQL, SQLite, Ubuntu, and Docker are not active architecture. Their
existing documents and code remain historical/deferred-cleanup material and
were not removed or modified. `CologneTransitIntelligence` is now the only
project SQL Server database; `NRWTransportDW` is no longer attached to the
instance and remains recoverable from the verified archive.

### Current cutover transition status

The additive SQL Server contract and the exact 43-row Tiered50 materialization
were applied atomically to `CologneTransitIntelligence`; the existing seven
sampling-target IDs and Windows-era realtime counts remain unchanged. The
Tiered50 rows use IDs 8–50. Dedicated-login
provisioning, live schema validation, materialization validation, runtime
preflight, and fresh host/VM/SQL evidence passed. The tracked templates remain
secret-free and the installed Tiered50 bundle is separate, versioned, and now
disabled after the failed first scheduled heartbeat. The intended runtime uses
the validated absolute
interpreter `/Library/Developer/CommandLineTools/usr/bin/python3`.

The final Legacy7 run is `05d97f32-7c4a-4a6c-993c-fd496c609cb8` in bucket
`5971511`, completed at `2026-10-08 09:57:32.860000` UTC. Buckets `5971506`
through `5971511` are all accounted for and succeeded. Legacy7 was unloaded
and disabled after that boundary. The current cutover record is an auditable
`OUTAGE` because the first scheduled Tiered50 heartbeat failed its cadence
gate; the bounded bootstrap permitted the two observed requests, while future
automatic rollover remains fail-closed pending provider reset evidence or
explicit approval and registration of the bounded policy.

The three corrected Legacy7 heartbeat records from the prior operational
window remain preserved as historical evidence in the closeout artifact. They
are not current production status and do not authorize Legacy7 fallback.

The durable SQLite source is
`/Users/admin/cologne-transit-data/live-research/collector.sqlite3` (575,041,536
bytes; SHA-256
`0edc9b1a56cb6128ac5bed6508e5c37b9b4d1ab18c1d78092e6fcb1ac9b674fc`). It
proves `LIVE_RESEARCH_SINGLE_1`, schema 5, intact SQLite checks, and the
contiguous 5.9306-hour historical session from
`2026-10-01T16:59:32.654121Z` to `2026-10-01T22:55:22.809977Z`. It is an
offline import source only and is not relabeled as Mac, Windows, Legacy7, or
Tiered50 production evidence.

The read-only overlap audit found no Windows-era rows in that interval and did
not merge or deduplicate anything. A fresh capacity gate passed, the complete
source was imported, and an exact rerun inserted zero new rows. Four failed
import attempts remain auditable as `FAILED`, including one interrupted
partial batch; no source rows were deleted. Post-import reconciliation passed
for 70 snapshots, 38,120 calls, 72,190 timings, 1,400 ServiceSections, and
121 parser gaps, with zero unexplained differences and zero source
`RecordedAtTime` values. Offline reconciliation replaced any manual Heumarkt
validation request. The operator then retired the exact Windows task.
Dedicated-login provisioning and the historical corrected-heartbeat evidence
remain preserved; the Legacy7 LaunchAgent is now unloaded and disabled. The
first scheduled Tiered50 heartbeat ran bucket `5971541`, was stopped after its
cadence gate failed, and is recorded in the operational evidence.

See the [operational import and cutover record](operational/s98-live-research-import-and-cutover.md)
and the reusable [read-only validator](../scripts/validate_sqlite_import.py).
The durable sanitized [Legacy7 transition closeout evidence](operational/legacy7-production-transition-closeout-evidence.json)
contains the machine-readable SQL, runtime, scheduler, heartbeat, and
least-privilege audit record.

### Current Stage 3 substage

- **Stage 3 — COMPLETE** as a source-evidence, collection/cadence-design, and
  cross-source-matching-design stage.
- **Stage 3A — Disruption Cause & Alert Evidence Audit — COMPLETE — APPROVED.**
- **Stage 3B — Collection Strategy & Cadence — COMPLETE — APPROVED DESIGN;
  ACTIVATION DEFERRED.**
- **Stage 3C — Cross-Source Matching Design — COMPLETE — APPROVED DESIGN;
  Stage 9 implementation regression and bounded source-backed acceptance pass.**
- Stage 3 completion does not authorize production implementation,
  database/schema work, collector changes, scheduler changes, Power BI work,
  or the next project stage.

See the [Stage 3A audit document](stage-03/03a-disruption-cause-alert-evidence-audit.md).
See the [Stage 3 completion decision](stage-03/stage-03-completion-decision.md)
for the authoritative closeout record.

### Stage 4 closeout

- **Stage 4 — COMPLETE — APPROVED.** The current official VRS Global-ID ZIP
  was retrieved once, checked for service-calendar freshness, and audited
  locally. The previous stale snapshot is retained as historical/legacy
  evidence and is not current authority.
- Current approval snapshot: feed version `VERSION__20260928_0042`, validity
  `2026-09-02` through `2026-12-12`, and ZIP SHA-256
  `6ae1c7f65299438f3b0bc049ed3626734b291378446e114dfecc4bcea4636b6b`.
- The validated Cologne identity is the direct source rule
  `stop_id LIKE 'de:05315:%'`; parent-station relationships do not override
  the actual stop ID referenced by `stop_times.txt`.
- Cologne scope is measured at stop-event and both-endpoints internal-segment
  grain. Route/trip serving labels are discovery identities, not full
  geographical KPI scope.
- The approved mode inventory is Stadtbahn/Tram, Urban/KVB Bus, S-Bahn, RE,
  RB, Regional/Other Bus, and separate SEV, with long-distance/unsupported
  values outside core scope. Cross-boundary services contribute only their
  validated Cologne events and segments.
- The [Stage 4 completion document](stages/04-cologne-scope-mode-classification.md)
  and [Stage 4 evidence](stages/04-cologne-scope-mode-evidence.json) contain
  the snapshot metadata, counts, classifier examples, anomalies, and
  reconciliation checks. The historical/legacy evidence is preserved in
  [the separately named historical JSON](stages/04-cologne-scope-mode-evidence-historical-2026-09-28.json).
- Stage 4 completion did not authorize physical PostgreSQL, warehouse,
  collector, scheduler, matching, or Power BI implementation. Stage 5 has now
  completed its architecture approval; Stage 6's design/contract closeout is
  recorded below and physical implementation remains deferred.

### Stage 5 closeout — historical design; superseded for active platform by S87

- **Historical Stage 5 design — COMPLETE — APPROVED at the time.** PostgreSQL
  18 was approved as the future authoritative platform direction, with the
  latest supported PostgreSQL 18 minor selected at implementation time. S87
  supersedes that platform direction for the active roadmap: Microsoft SQL
  Server is now the sole authoritative project database. The retained
  migration strategy is a clean PostgreSQL redesign, not a 1:1 SQL Server
  lift-and-shift.
- The approved logical layers are `ctl`, `src`, `wrk`, `core`, `dw`, and
  `analytics`. `src` is durable source-faithful evidence; `wrk`, `core`, `dw`,
  and `analytics` are derived or validated layers with explicit ownership and
  lineage boundaries.
- The [Stage 5 architecture document](stages/05-database-architecture.md),
  [42-entry table/grain catalog](stages/05-table-grain-catalog.md), and
  [completion decision](stages/05-database-architecture-completion-decision.md)
  are authoritative. They approve the static GTFS content-hash snapshot
  strategy, append-only MDD observation history, bounded GTFS-RT persistence,
  keys/types/idempotency, evidence-driven indexes, future monthly realtime
  partitioning, control/sampling lineage, least-privilege roles, and the
  backup/recovery requirement.
- Stage 5 created no database, schema, table, migration, SQL execution,
  collector, scheduler, matching, warehouse, or Power BI implementation.
  Its static warehouse/analytics handoff is now completed as a design
  contract; physical implementation remains deferred until explicit user
  authorization.

### Stage 6 closeout

- **Stage 6 — COMPLETE — APPROVED DESIGN; PHYSICAL IMPLEMENTATION DEFERRED.**
  The [static warehouse and analytics design](stages/06-static-warehouse-analytics-design.md)
  finalizes the source-to-Cologne boundary, exact static dimension/bridge/fact
  grains, GTFS extended-hour time normalization, `calendar.txt` and
  `calendar_dates.txt` activation semantics, Stage 4 scope/mode application,
  cross-boundary behavior, static analytics outputs, snapshot lineage,
  idempotency, and the future implementation acceptance matrix.
- The current Stage 4 values are source baselines only: 3,137 Cologne stops,
  1,144,295 Cologne scheduled stop events, 1,082,490 internal segments,
  61,805 Cologne-serving trips, 39,566 Cologne-internal trips, 22,239
  cross-boundary trips, 1,606 touch-only diagnostics, and 137
  Cologne-serving routes. They are not claims about a warehouse that exists
  today.
- `FactScheduledTrip` and `FactScheduledStopEvent` remain pattern-level;
  `BridgeServiceDate` owns active Service + Date expansion, and `DateKey` does
  not enter the scheduled stop-event fact grain. Stage 6 does not depend on
  Stage 9 `core` objects.
- PostgreSQL migration/runtime, database/schema/table/migration creation, SQL,
  data loading, network retrieval, realtime work, matching, operational facts,
  and Power BI work did not occur. The [Stage 6 completion decision](stages/06-static-warehouse-analytics-completion-decision.md)
  records the closeout. Stage 7 is now separately closed as **COMPLETE —
  APPROVED PILOT DESIGN**; Stage 8 is complete as the bounded pilot execution
  described below.

### Stage 7 closeout

- **Stage 7 — COMPLETE — APPROVED PILOT DESIGN.** The [realtime pilot design](stages/07-realtime-pilot-design.md)
  fixes a bounded, reproducible MDD/TRIAS-only Stage 8 pilot for Köln Hbf,
  `de:05315:11201`, with the active V3 policy
  `MDD_STAGE_8_KOELN_HBF_3S_60MIN_V3`: fixed start-to-start 3-second slots for
  at most 60 minutes, at most 1200 scheduled logical slots and 1200 normal
  HTTP attempts, a fixed pilot-start `DepArrTime` anchor, `NumberOfResults=20`,
  `TimeWindow` omitted, a 30-second timeout, zero retries, sequential
  non-concurrent execution, and overlap-slot skipping. The bounded cadence
  reduces but cannot mathematically guarantee capture of a transient source
  `RecordedAtTime`.
- The pilot preserves the approved source-field retention, timing, estimate
  history, call scope, bay, operational-flag, Situation, service-information,
  error, parser-lineage, gap, idempotency, and reconciliation requirements,
  including immediate append-only persistence of any observed source
  `RecordedAtTime` and protection from later NULL erasure. The conservative
  monthly ceiling remains 250,000 requests; the normal operational maximum is
  210,000 requests/month and automatic retries are blocked above the separate
  207,500 projected/current protection boundary. There is no minimum utilization
  requirement; Stage 13's approved 182,980 / 189,076 conservative totals are
  valid lower-load designs with additional quota headroom. The 1200-attempt
  pilot maximum is 0.48% of the full ceiling. Stage 7's 83–84% / 207,500–
  210,000 planning target is retained as historical context only. It uses
  protected temporary raw responses only under the bounded 24-hour Stage 8
  exception and a diagnostic append-only JSONL harness because PostgreSQL
  remains deferred.
- GTFS-RT is not part of the one-source pilot; Stage 9 owns cross-source
  matching. Actual Delay remains **NOT APPROVED**. One-station evidence is not
  Cologne-wide coverage.
- The [Stage 7 completion decision](stages/07-realtime-pilot-design-completion-decision.md)
  remains authoritative for the design closeout. That closeout itself
  performed no MDD or GTFS-RT request, SQL/database access, runtime pilot, or
  PostgreSQL implementation; the later Stage 8 HTTP attempt and Stage 8
  closeout are recorded below.

### Stage 8 closeout — COMPLETE

Authoritative runtime status: `stage_8_status = COMPLETE`.

The Stage 8 runtime exists at
[scripts/stage_08_mdd_realtime_pilot.py](../scripts/stage_08_mdd_realtime_pilot.py),
and the separate authorization/request-contract diagnostic exists at
[scripts/stage_08_authorization_request_contract_diagnostic.py](../scripts/stage_08_authorization_request_contract_diagnostic.py).
The [Stage 8 record](stages/08-small-realtime-pilot-blocked.md) retains the
full chronology while this section is the current authoritative status.

The chronology is:

1. An earlier missing-key preflight reported `MDD_API_KEY: MISSING` and made
   zero requests.
2. Earlier invalid/local credential-configuration attempts included a macOS
   probe using a different credential that returned HTTP 401; the corrected
   Legacy-equivalent local credential configuration later returned HTTP 200
   for the same Köln Hbf controlled request.
3. A later 2026-09-28 Stage 8 attempt returned HTTP 401 with the observed
   German supplier-access message:
   `Sie haben keinen Zugriff auf Daten diesen Datenlieferanten. Wenn Sie weitere Datenlieferanten freischalten möchten, kontaktieren Sie bitte den Betreiber der MDD.`
4. Under the corrected local credential configuration, the differential
   diagnostic succeeded: Control A used Köln Hbf,
   `de:05315:11201`, `NumberOfResults=5`, and returned HTTP 200 with a
   parseable response; Control B used the same station and diagnostic
   `DepArrTime` anchor with `NumberOfResults=20` and returned HTTP 200 with a
   parseable response.
5. The evidence-supported classification was
   `CURRENT_CREDENTIAL_AND_STAGE_8_REQUEST_BREADTH_WORKING`. No request
   parameter change or request-contract reduction was required. The earlier
   401 therefore does not support a conclusion that `NumberOfResults=20` or
   the current Köln Hbf Stage 8 request contract requires additional supplier
   authorization. This conclusion is bounded to the tested Hbf controls and
   does not claim that every MDD data supplier behaves identically.

The full effective pilot configuration was Köln Hbf,
`de:05315:11201`, `NumberOfResults=20`, under
`MDD_STAGE_8_KOELN_HBF_3S_60MIN_V3`. It started and completed successfully:

| Measure | Result |
|---|---:|
| Actual elapsed time | approximately `3606.801` seconds |
| Planned logical slots | `1200` |
| Attempted HTTP requests | `343` |
| HTTP 200 responses | `343` |
| Successful parse/reconciliation responses | `343` |
| Reconciliation rate over successful HTTP responses | `1.0` / `100%` |
| Skipped overlap slots | `857` |
| StopEventResult occurrences | `6860` |
| Parser failures | `0` |
| Persistence failures | `0` |
| Network/DNS failures | `0` |
| Timeouts | `0` |
| MDD application errors | `0` |
| Cleanup | `SUCCEEDED` |

The `857` skipped slots are designed sequential overlap skips: the collector
does not queue or catch up concurrent requests. They are not collector
failures. This is evidence for the later Stage 13 sequential-versus-bounded-
concurrency/readiness decision; Stage 13 analysis and implementation are not
part of this closeout.

The pilot result is exactly
`RECORDED_AT_TIME_NOT_OBSERVED_IN_STAGE_08_PILOT`. This is bounded to this
one-hour pilot and is not a provider-wide absence claim. `Actual Physical
Delay = NOT_APPROVED`; an `EstimatedTime`-based value is not renamed as actual
physical delay.

The live source exposed these parser-gap fields, observed in the live source
but not yet mapped by the current Stage 8 persistence model:
`affectedVehicleJourney`, `datedVehicleJourneyRef`, `destinations`,
`direction`, `operatingDaysDescription`, `origins`, `situations`,
`vehicleJourneyRef`, and `vehicleJourneys`. They are preserved as sanitized
Stage 8 evidence for later matching/data-quality review and are not added to
the production schema by this closeout. Existing Situation parsing and
persistence, including `SituationFullRef`, `Source`, and `SourceType`, remain
unchanged.

The diagnostic metadata correction is also complete: successful HTTP 200,
parseable controls no longer receive a failure classification, and ordinary
Situation/passenger-information text no longer populates
`sanitized_provider_error_text`. Genuine failed-response error evidence,
including the observed German HTTP 401 supplier-access body, remains
available to supplier-access detection. Offline validation covered successful
controls, parser failure, timeout, network/DNS failure, HTTP 401, Situation
text, the observed German 401 body, and a generic unrelated 401 without making
an MDD request.

The future 50-station objective is unchanged. Stage 13 retains ownership of
future Tiered50 production `NumberOfResults`, cadence, concurrency, and
readiness decisions. At this Stage 8 closeout point, Stage 9 and all later
stages remained not started and untouched.

### Post-Stage-8 hardening boundary

Future Stage 8 executions now perform a shared secret-free API-key header
preflight before temporary-directory creation or HTTP transport. Unsafe format
is reported as `MDD_API_KEY: INVALID_FORMAT` and classified as
`CREDENTIAL_FORMAT_INVALID`; the API-key value is never exposed. A successful
response with a non-null source `RecordedAtTime` is marked
`RECORDED_AT_TIME_RAW_EVIDENCE`; only its corresponding raw response is
retained temporarily, for a maximum of 24 hours, while the parsed
source-faithful value and lineage remain the durable evidence. Ordinary
successful response artifacts continue normal cleanup, and intentional
positive retention is reported as
`RECORDED_AT_TIME_EVIDENCE_RETAINED_MAX_24_HOURS`, not a failure.

Future zero-occurrence runs retain only sanitized aggregate counts for the
bounded observed sample and do not claim provider-wide absence. This is
prospective hardening only: the historical Stage 8 `COMPLETE` result and its
successful original cleanup remain unchanged; no live MDD request was made by
this hardening change; the future 50-station objective and `250,000` monthly
quota are unchanged; Stage 13 retains production ownership; and Stage 9+
implementation was not started at that hardening closeout point.

### Stage 9 review correction and bounded acceptance probe — COMPLETE

The [Stage 9 matching and data-quality record](stages/09-matching-data-quality.md),
the [offline matcher](../scripts/stage_09_matching_quality.py), the bounded
[source-backed probe](../scripts/stage_09_source_backed_acceptance.py), and the
[machine-readable evidence audit](stages/09-matching-data-quality-evidence.json)
are now the current implementation/evidence authorities. The implementation
regression and bounded source-backed Stage 9 acceptance gate are PASS/COMPLETE:
the corrected probe reported `MDD_API_KEY: PRESENT`, made exactly `3` bounded
MDD attempts, and obtained a date-compatible MDD-to-VRS static event
population. Stage 9 provides:

- versioned, namespace-qualified canonical stop and line bridges;
- stop-event-first date/time candidate generation with deterministic indexes;
- auditable HIGH/MEDIUM/LOW/UNMATCHED decisions and retained candidates;
- explicit service-date, source-field, static-coverage, ambiguity, lineage,
  and sampling-era quality classifications;
- ranking protection against unvalidated auxiliary evidence breaking candidate
  ties, with validated-policy regression coverage;
- prospective retention of richer MDD identity candidates with source paths;
- source-aware cross-source reconciliation that never equates MDD `JourneyRef`
  with GTFS-RT `trip_id`;
- a mapping-overlap review view that compares effective ranges across mapping
  versions; and
- PostgreSQL DDL/index/view contracts under `sql/stage_09_matching_quality.sql`.

The synthetic offline acceptance population passed with zero network requests
and zero database connections, and remains regression evidence only. The
corrected bounded live run returned three HTTP 200 parseable responses with 20
stop events each; all 60 raw records contained a THIS call and all 60 were
retained after Stage 9 adaptation. It independently established 53 GTFS service
dates and produced 51 core deterministic matches, with 51 `MEDIUM` and 9
`UNMATCHED` decisions. The retained JSONL row-level evidence passed the
lossless audit-field integrity gate and reconciles exactly to the summary. The
current VRS snapshot
`VERSION__20260929_0106` / SHA-256
`ca64753223d3908a86b74fc322103b9e4c6c9c90275cfc5cb2c4c624b0082ee0` passed all
17 Stage 4 revalidation checks. The approved Stage 4 reference snapshot
`VERSION__20260928_0042` / SHA-256
`6ae1c7f65299438f3b0bc049ed3626734b291378446e114dfecc4bcea4636b6b` remains
separate; Stage 4 semantics, rather than historical exact row counts, were
revalidated for the changed official feed. Railway Hbf remains unresolved and
adjacent KVB complexes are not substituted. No Power BI runtime model or PBIP
artifact, 50-station selection, or Tiered50 activation has been started. Stage 10 has a corrected
source-controlled `dw.fact_operational_service_event` contract and semantic
validator. Its current status is **NOT COMPLETE — IMPLEMENTATION CORRECTED; SOURCE/DATABASE VALIDATION BLOCKED** because this checkout has no usable
PostgreSQL database or durable source-observation population. The current
Stage 9 source-backed artifact also contains zero HIGH rows, so source-backed
Stage 10 validation is blocked rather than promoted from MEDIUM context.
The exact probe result is retained in
`stages/09-source-backed-matching-summary.json` and the sanitized row-level
evidence in `stages/09-source-backed-matching-evidence.jsonl`.

### Stage 11 closeout

- **Stage 11 — COMPLETE — APPROVED DESIGN; PBIP/DESKTOP IMPLEMENTATION
  DEFERRED.** The [Power BI semantic-model design](stages/11-power-bi-model-design.md)
  defines the Static Schedule / Network Context, Operational Evidence, and
  Matching / Data Quality subject areas; exact table grains; single-direction
  relationship rules; the distinction between the semantic Scheduled Trip
  projection and the physical Stage 6 scheduled facts; the governed Date
  dimension; the VRS/static versus canonical operational identity boundary;
  explicit DAX measures and denominators; service-date-scoped matching
  coverage with separate missing-service-date evidence; estimated-delay
  threshold and NULL semantics; bay and Situation terminology; and
  sampling/coverage language with no unvalidated operational Sampling Era
  slicer.
- The [Stage 11 completion decision](stages/11-power-bi-model-completion-decision.md)
  records that no PBIP, TMDL, PBIR, PBIX, Power BI Desktop execution,
  datasource binding, refresh, render QA, PostgreSQL work, SQL execution, or
  data load occurred. `Dim Sampling Era` currently filters only the `Fact
  Matching Decision` population; Data Quality classifications carried by those
  matching decisions inherit that context, while the independent `Fact Data
  Quality Issue` and `Data Quality Issue Count` are not Sampling-Era-filtered in
  the base model. Sampling-era operational attribution remains deferred pending
  validated Stage 9/10 lineage; until then, operational KPI pages must constrain
  compatible era/time scope or show a coverage/methodology warning.
  The `powerbi/` directory contains the implementation boundary README only.
- Stage 10 remains **NOT COMPLETE — IMPLEMENTATION CORRECTED;
  SOURCE/DATABASE VALIDATION BLOCKED**. Stage 12 is now closed as
  **COMPLETE — APPROVED FIXED 50-STATION PANEL** under
  [`TIERED50_PANEL_V1`](stages/12-50-station-selection.md).

### Stage 12 closeout — approved fixed 50-station panel

- **Stage 12 — COMPLETE — APPROVED FIXED 50-STATION PANEL.** The [selection document](stages/12-50-station-selection.md), [completion decision](stages/12-50-station-selection-completion-decision.md), and [sanitized machine-readable evidence](stages/12-50-station-selection-evidence.json) are authoritative.
- The panel contains exactly 50 unique validated VRS parent stations. The
  current feed has 864 valid Cologne parent-station candidates, all 864 with
  scheduled child-stop evidence, and 1,146,295 parent-level scheduled events.
  This is schedule/service evidence, not passenger demand.
- The complete sanitized candidate universe is retained in
  [the replay artifact](stages/12-50-station-candidate-evidence.json). It has
  exactly 864 unique parent IDs and reproduces the approved panel offline via
  the authoritative selector without the original ZIP, network, MDD, or an MDD
  credential.
- Stage 4 reference snapshot `VERSION__20260928_0042` and Stage 12 selection
  snapshot `VERSION__20260930_0053` remain distinct. Count differences are
  snapshot evolution; Stage 4 identity and classification semantics were
  re-applied to Stage 12, and Stage 4 evidence was not overwritten.
- All seven Legacy7 continuity anchors remain valid `location_type=1` parent
  identities with scheduled service and are preserved unchanged.
- The selection uses continuity, current Stage 4 core-mode coverage, a fixed
  secondary geographic-cell spread pass, and an exposed lexicographic
  operational-richness key. `SelectionRank` is not a production tier.
- The current feed is `VERSION__20260930_0053` with ZIP SHA-256
  `288c26f23f91a2069078be82167cfa70eb5b2948e06cfeee9e23584910817147`.
- The bounded MDD probe made 50 sequential provider HTTP attempts: all were
  HTTP 200, parseable, valid TRIAS application responses, and returned source
  `StopPointRef` evidence matching the requested parent or validated child.
  No replacement was required and raw responses were not persisted.
- `TIERED50_PANEL_V1` is fixed after approval. Later membership changes require
  a new version with effective date/time, reason, and predecessor; historical
  identities and sampling/panel eras remain interpretable.

### Stage 13 readiness design — approved; Tiered50 production gate blocked

- **Stage 13 — COMPLETE — APPROVED TIERED COLLECTOR READINESS DESIGN; PRODUCTION NOT STARTED.** The [readiness design](stages/13-tiered-collector-readiness.md), [completion decision](stages/13-tiered-collector-readiness-completion-decision.md), and [machine-readable evidence](stages/13-tiered-collector-readiness-evidence.json) are authoritative. Explicit approval in `prompts/S68.md` finalized the material design choices; D-046 remains historical and D-047/D-048 remain chronology.
- The fixed Stage 12 panel is `TIERED50_PANEL_V1` with exactly 50 stations and all seven Legacy7 continuity anchors. Stage 12 evidence was not changed. Stage 13 density ranks 1–8 / 9–28 / 29–50 produce Tier A/B/C counts 8 / 20 / 22 at 5 / 15 / 30-minute cadence.
- The approved result limits are Tier A/B/C `NumberOfResults` 20 / 30 / 30. Retained Stage 13 station-level exact-feed evidence recalculates max station P95 / max observed static scheduled-arrival counts as A 19/20, B 23/23, and C 29/29, with 50/50 total P95 support. Static support is not realtime completeness evidence.
- The approved load is 110 logical requests per 30-minute super-cycle and 220/hour. The approved 10% retry reserve and conservative shared-credential accounting total 182,980 for 30 days and 189,076 for 31 days, including Legacy7 reserves and the historical diagnostic reserve. These lower-load totals are valid safely below the 210,000 normal operational maximum and the 207,500 automatic-retry protection boundary; no minimum monthly utilization is required. Automatic retries are blocked when projected/current shared-credential monthly consumption would exceed 207,500 across all known consumers. The Tiered50-only 31-day component is 180,048; the main shared-credential guardrail example is 189,076 with 18,424 headroom to 207,500. The historical/evaluated 10/20/20 @ 5/15/30 comparison remains unapproved and reconciles to 205,444 for 31 days with the same reserve model.
- Maximum concurrency 2 and `MaxAttempts=2` with one approved transient retry are final design decisions. Retryable HTTP statuses are 408/429/500/502/503/504 plus approved transient transport failures; 429 respects `Retry-After`; 401/403 and deterministic request, parser/schema, and TRIAS application failures are not retryable. Failed station isolation remains required.
- The approved phase due sequence is 19/18/18/18/19/18 with capacities A 8, B 7/7/6, and C 4/3/4/3/4/4. Phase assignment uses `source_parent_station_id / parent StopPointRef`; the additive production materialization was applied idempotently for the 43 rows with IDs 8–50, and the administrator activation transaction was later committed before the runtime was stopped after its first failed heartbeat gate. Overlap identity remains `(SamplingTargetId, LogicalDueBucket)` with atomic claims, no duplicate HTTP, no catch-up replay, and continuity-preserving Legacy7 history.
- `PROVIDER_MAXIMUM_NOT_ESTABLISHED` and `CORRECTED_LIVE_FORWARD_COVERAGE_UNAVAILABLE_DUE_TO_EXECUTION_ENVIRONMENT` remain explicit limitations. The retained corrected diagnostic is 24 client invocations, 0 HTTP responses, 0 confirmed provider HTTP attempts, unknown provider quota consumption, and 24 separate conservative quota-accounting attempts; no new MDD request was issued for this offline closeout.
- The historical Stage 14 collector implementation is complete, with the S78 live-research profile retained as offline/research evidence. The corrected Legacy7 selector, additive lineage migration, bounded 21/3/18 methodology evidence, dedicated-login provisioning, and historical heartbeat validations remain preserved. The Tiered50 SQL target materialization and administrator activation are complete, but the first scheduled heartbeat failed its cadence gate and Stage 15B validation remains blocked. The Tiered50 LaunchAgent is disabled, no stations are active, and Legacy7 is retired. SQL Server is the active persistence authority under S87; the SQLite profile and PostgreSQL contracts are historical/deferred-cleanup material. Stage 15A readiness remains IN PROGRESS; Stage 15B remains NOT STARTED — FIRST SCHEDULED TIERED50 HEARTBEAT FAILED.

### Current storage direction — SQL Server authoritative; prior research retained

S87 supersedes the previously discussed PostgreSQL and SQLite-first directions
for the active roadmap. The official database is SQL Server
`CologneTransitIntelligence`; the live-research SQLite profile and PostgreSQL
contracts remain historical/deferred-cleanup evidence only. The additive
macOS direct-persistence adapter and Tiered50 materialization are implemented.
The first scheduled Tiered50 activation attempt was stopped after its cadence
gate failed; no stations are active, future rollover is fail-closed pending
provider reset evidence, and Legacy7 is retired.

### Stage 14 collector implementation — Tiered50 activation outage; Legacy7 retired

- **Overall status:** `TIERED50 ACTIVATION OUTAGE — NO ACTIVE STATIONS; LEGACY7 RETIRED`.
- **Implementation status:** `STAGE 14 COLLECTOR IMPLEMENTATION COMPLETE`. S76's final source-contract retention correction is complete: JourneyRef/VehicleJourneyRef/DatedVehicleJourneyRef and DirectionRef/Direction remain independent, candidate identity extensions retain source paths, service-only mode/display/origin/destination semantics are enforced, Attribute/GeneralAttribute and affected-entity evidence is retained without causal inference, and recognized-but-unmapped relevant fields become bounded parser-gap evidence. The accepted S75 call-scope and `CLIENT_EXECUTION_FAILURE` behavior remains intact. The package under `src/cologne_transit_collector/`, thin CLI `scripts/stage_14_collector.py`, fixed manifest `config/tiered50_runtime_manifest.json`, offline validation script, sanitized fixtures, and Stage 14 tests are source-controlled.
- The manifest contains exactly 50 targets, split 7 Legacy7 members / 43 new Tiered50 members, with approved 8/20/22 tiers, 5/15/30 cadence, A20/B30/C30 result limits, persisted phase indexes, and no fabricated production SamplingTargetIds. Stage 13 phase evidence is reconciled at load time.
- Pure UTC bucket scheduling reproduces `[19,18,18,18,19,18]`, 110 executions per 30-minute super-cycle, and 220/hour. The dispatcher is scheduler-neutral, cap-2, MaxAttempts 2, failure-isolated, and deadline-aware.
- The MDD client is explicitly opt-in, POST/TRIAS 1.2, environment-secret-only, and has no import/init network activity. Default plan/dry-run/simulate paths are offline-safe. Production `live` remains blocked; `live-research` is a separate one-heartbeat Heumarkt mode with explicit storage, credential, target, and quota preconditions.
- SQLite has two explicit profiles: disposable `DEVELOPMENT` state under `.local/` and durable, non-disposable `LIVE_RESEARCH` state under the configured live root. The live profile preserves claims, audits, parsed snapshots, append-only `timing_observations`, the `LatestSourceRecordedAtTime`/`LastObservedRecordedAtTime` reporting projection, RecordedAtTime evidence, Attribute/GeneralAttribute, Situation, parser-gap, quota, and raw-evidence lineage; parsed history is indefinite, raw forensic bodies are bounded to 24 hours, and three validated daily backups remain required. It is not a production cutover and does not authorize PostgreSQL work.
- Live research defaults to `~/cologne-transit-data/live-research/collector.sqlite3` when an explicit live root is supplied, with protected `backups/` and `raw-evidence/` directories. Parsed history is retained indefinitely; raw forensic bodies are sanitized, indexed, and retained for at most 24 hours. Validated SQLite online backups retain the three most recent UTC date versions.
- The historical Stage 14 baseline reported 157 tests. The current repository suite reports 395 tests per run with `ResourceWarning` promoted to an error; the current review includes runtime-schema least-privilege, permission-contract, provisioning-recovery, append-only idempotency, sanitized-evidence, Windows evidence transport, TLS provenance, measured disk headroom, Tiered50 materialization/runtime, quota-baseline reconciliation, bounded-bootstrap, monthly-rollover, exact and bounded baseline authorization, global UTC-12..UTC+14 rollover-envelope evaluation, period-start permit accounting, wall-clock scheduling, deadline classification, live authorization-time, coverage, clock-jump, batched-skip, fixed-capacity, SQL Server skip-audit parameter/idempotency, conservative transport/quota finalization, separate-admin, denied-query, explicit-admin-permission, safe Tiered50 resume, authenticated administrative evidence, bounded rollover policy, competing-worker, stale-evidence, idempotent-resume, SQL-clock resume refresh, stopped/loaded idempotency result contracts, architecture-validator, first-heartbeat HTTP-history reconciliation, orphan/duplicate AuditId detection, Windows SQL result-shape normalization, Windows backup metadata validation, and documentation-drift regressions. The administrator migration, runtime grants, host/VM/SQL preflight, Windows-retirement, scheduler, corrected-era, and six Legacy7-heartbeat gates passed. The first scheduled Tiered50 heartbeat failed its cadence/persistence gate at bucket `5971541` (2 of 18 audits completed), so the agent was stopped and the outage was recorded. The corrected scheduler and additive runtime evidence are prepared, but no production activation or manual MDD request was performed. The exact architecture validator passes; the rollback-only real SQL Skip Audit result is preserved as a reported `REAL_SQL_SERVER_TRANSACTION_ROLLED_BACK` PASS for row counts 1, 16, 18, and 19, with no archived sanitized machine-readable output and no newly replayed production execution in this review. The final offline suite and validators passed in this review.
- The direct path is documented in [the macOS persistence contract](macos-mssql-collector.md). It uses `pymssql==2.3.2`, additive `ctl.MddMac*`/`stg.MddMac*` objects, source-faithful timing/Situation/Attribute/gap rows, durable SQL-backed leases and claims, and a separate versioned Tiered50 `launchd` bundle. The installed Tiered50 artifact is disabled after the failed first scheduled heartbeat, contains no secrets, and does not alter the historical tables or Power BI boundary.
- The historical corrected Legacy7 runtime selected one SQL-approved target per ten-slot cycle, retained `SamplingSlot`/policy lineage, used `MAC_LEGACY7_PRODUCTION`, and required `CologneTransitCollector` rather than `sa`. Its final run boundary was captured, then the LaunchAgent was unloaded and disabled; Legacy7 is not a fallback.
- One explicitly authorized S78 research heartbeat ran at `2026-10-01T11:30:00Z` for Köln Heumarkt (`de:05315:11110`): one HTTP 200, 20 parsed results, one persisted snapshot, one confirmed provider attempt, no retry, and 121 historical parser-gap fields retained with sanitized raw evidence. S80 then migrated the same live SQLite file offline from schema 2 to 4 without rewriting snapshot JSON, inserted 995 timing rows and 20 ServiceSection rows, and passed integrity, backup, and RecordedAtTime reconciliation checks. S81 migrated that file offline from schema 4 to 5 at `2026-10-01T15:15:16.410851Z`, kept all required row counts unchanged, validated the corrected reporting views, and refreshed the current-day backup. This remains lineage-separated research evidence, not production cutover evidence; the only production-path MDD requests in this review were the two requests in the first scheduled Tiered50 heartbeat, and no manual Heumarkt request was made.

### Stage 15A post-cutover validation readiness — in progress; Stage 15B not started

- **Stage 15A status:** `IN PROGRESS`. The authoritative [readiness document](stages/15-post-cutover-validation-readiness.md), [machine-readable contract](../config/stage15_validation_contract.json), read-only backend-neutral analyzer under `src/cologne_transit_stage15/`, SQLite evidence adapter, and offline regression tests are source-controlled.
- The analyzer derives expected due targets from the approved manifest, pure schedule engine, and logical bucket, then reconciles actual audit evidence. It explicitly classifies success, failure, skip, missing evidence, duplicate invocation prevention, and unexpected execution. It diagnoses dispatcher owners, duplicate/stale claims, target claims, structural retry violations, separated quota accounting, coverage, duration statistics, parser/persistence evidence, and Legacy7/cutover readiness.
- Policy-pending metrics have no invented thresholds. Measured rates, coverage percentages, parser gaps, zero-result frequency, error categories, and duration statistics remain `POLICY_PENDING`; missing required evidence is `INSUFFICIENT_EVIDENCE`. No PASS is emitted for absent post-cutover evidence.
- Stage 15A now exposes timing-history measurements without interpretation: normalized timing-observation count, events with estimate revision history, non-NULL `RecordedAtTime` evidence count, events with `LastObservedRecordedAtTime`, and latest-null/history-non-NULL count. These are measurement-only; Stage 15B remains `NOT STARTED — FIRST SCHEDULED TIERED50 HEARTBEAT FAILED`.
- **Stage 15B live post-cutover validation:** `NOT STARTED — FIRST SCHEDULED TIERED50 HEARTBEAT FAILED`. `LIVE_RESEARCH_SINGLE_1` is explicitly excluded from post-cutover evidence. A real Tiered50 activation was attempted through the scheduled runtime, but its first bucket failed the cadence gate and was stopped before a validated production cycle; no stations are currently active. Power BI is unchanged, and any controlled research request is lineage-separated and cannot satisfy the production transition check.

## Post-Stage-02 validation

Existing GTFS-RT validation:

- Current-feed inventory: **COMPLETED**.
- Static identity bridge: **BOUNDED/PARTIAL**; current Neumarkt identity
  validated, railway Köln Hbf unresolved.
- GTFS-RT bounded lifecycle validation: **COMPLETE**.
- GTFS-RT historical availability audit: **BOUNDED VALIDATION COMPLETE**;
  both the initial ±7-day and the separate ±45-day metadata-only exact-URL
  Wayback tests returned zero captures at the 3-month and 6-month targets.
- MDD/TRIAS near-event lifecycle validation: **BOUNDED VALIDATION COMPLETE**;
  six live journeys were reviewed (three at Köln Hbf and three at Köln
  Neumarkt), with no `RecordedAtTime` observed in the bounded observations.

- **GTFS-RT lifecycle validation — BOUNDED LIFECYCLE VALIDATION COMPLETE;
  NEUMARKT SAMPLES #1 AND #2 RECORDED.** The bounded, host-run-only tooling,
  inventory, and two lifecycle samples are documented in [the GTFS-RT audit](source-contracts/gtfs-rt-source-feasibility-audit.md).
  The [GTFS.de static identity bridge](source-contracts/gtfsde-static-identity-bridge-audit.md)
  remains **PARTIAL** because railway Köln Hbf is unresolved; that identity gap
  does not invalidate the completed two-sample Neumarkt lifecycle evidence.
- **GTFS-RT historical availability audit — BOUNDED VALIDATION COMPLETE.** S19
  and S19.1 tested only exact-URL Internet Archive / Wayback metadata around
  the 3-month and 6-month calendar horizons. The ±7-day run and the expanded
  ±45-day metadata-only run each returned zero captures for both horizons.
  This does not establish provider history or continuous coverage and does not
  approve GTFS-RT as a production source.
- This validation did not reopen or invalidate Stage 2 or approve GTFS-RT as a
  production source. Stage 3 is complete as a design stage; no production
  implementation is authorized.

### Post-Stage-02 source validation package

| Package | Current status |
|---|---|
| MDD/TRIAS source contract | **COMPLETED / APPROVED** |
| GTFS-RT current-feed feasibility | **BOUNDED VALIDATION COMPLETE** |
| GTFS.de identity bridge | **BOUNDED / PARTIAL** — Neumarkt current identity validated; railway Köln Hbf unresolved |
| GTFS-RT lifecycle validation | **BOUNDED VALIDATION COMPLETE** |
| GTFS-RT historical availability | **BOUNDED VALIDATION COMPLETE** — zero exact-URL captures in both ±7-day and ±45-day metadata runs |
| Realtime source-role direction | **APPROVED DIRECTION** — MDD primary; GTFS-RT complementary |
| Cross-source reconciliation and Cologne scope contract | **APPROVED CONTRACT** — Stage 9 regression implemented; bounded source-backed acceptance PASS |
| Stage 9 matching/data-quality foundation | **COMPLETE — APPROVED MATCHING & DATA QUALITY SEMANTICS** — 60 live observations; 51 core deterministic matches |
| Stage 3 collection/cadence contract | **COMPLETE — APPROVED DESIGN** — activation deferred |
| Stage 3 cross-source matching design | **COMPLETE — APPROVED DESIGN** — Stage 9 implementation regression and bounded source-backed acceptance pass |
| Confirmed Actual Time | **UNRESOLVED / UNAVAILABLE** |
| Actual Delay KPI | **NOT APPROVED** |

### Stage 2 substage status

- Stage 02A.1 — Legacy Implementation Audit: **COMPLETED** (read-only source
  evidence documented).
- Stage 02A.2 — Controlled MDD Raw-Response Audit: **COMPLETED**.

  The existing successful host-probe artifact was audited without another MDD
  request. The normalized request to `https://mdd.gorheinland.com/delfi` used
  XML/TRIAS 1.2 and returned HTTP **200** with
  `application/json;charset=UTF-8`; the body is JSON. It contains five
  `stopEventResult` records, timetable and estimate timestamps, identity,
  bay, operational-flag, and situation evidence. `RecordedAtTime`, direct
  delay, cancellation, and explicit platform-change fields were not present
  in this response.

  The artifact is temporary evidence at
  `/private/tmp/cologne-transit-stage-02a2-hhqob8km.raw`, outside this
  repository. It was not copied or committed. The audit found no unresolved
  technical blocker for this controlled response.

- Stage 02A.3 — Historical Actual-Time Availability Validation: **COMPLETED**.
  The previous host run already completed one independent HTTP request at each
  of 1, 7, 30, 90, and 180 days before execution. S16 made zero MDD HTTP
  requests and reanalysed only the five existing raw artifacts. The corrected
  result is historical timetable-only data at 1D/7D/30D and no event at 90D/
  180D; no `EstimatedTime` or `RecordedAtTime` was observed.

Current authoritative transport and authorization state:

- `HOST_DNS_RESULT: NORMAL_RESOLVER_WORKS`.
- The historical Stage 02 comparison established that the then-tested macOS
  value matched the then-tested Windows/Legacy value; it does not prove that
  the current process has that value.
- The historical Stage 02 controlled request returned HTTP 200. The later
  2026-09-28 Stage 8 HTTP 401 with the sanitized supplier-access message is
  preserved as historical failure evidence. The later corrected local
  credential configuration passed both Hbf differential controls at
  `NumberOfResults=5` and `NumberOfResults=20`; this is the current bounded
  Stage 8 authorization/request-breadth evidence.
- The earlier repository-process preflight reported `MDD_API_KEY: MISSING`.
  No credential value or credential-derived information was inspected or
  exposed; that result remains historical chronology, not the current Stage 8
  status.
- The S14 technical-closure package remains the approved Stage 02 baseline.
  Stage 02 received explicit user approval in S17 after S16 completed the
  deferred historical actual-time gate evidence.

The earlier S10/S11/S12 transport and DNS findings remain in the Stage 02 audit
as historical chronology, not as current blockers. S13 made zero MDD requests,
executed zero probes, executed no SQL, and changed no database, Legacy
repository, Task Scheduler, or Power BI state.

The durable [realtime collector requirements](collector-design/realtime-collector-requirements.md)
are approved Stage 02/Stage 7 design inputs with implementation deferred. The
separate [near-event RecordedAtTime lifecycle validation](source-contracts/mdd-trias-event-lifecycle-validation.md)
is a **POST-STAGE-02 COLLECTOR-DESIGN VALIDATION**; its bounded host evidence
is documented and does not replace the future Stage 8 pilot.

## Current authoritative decisions

- The project is management-oriented BI supported by data engineering.
- The umbrella management concept is **System Performance**.
- Analytical geography is public transport performance **inside Cologne**; a route merely passing through Cologne does not automatically make the whole route part of the core KPI scope.
- Primary timing objective: Actual Delay based on validated `RecordedAtTime` evidence.
- Stage 02 allows **Estimated Delay** only when timetable and estimate times
  are both present, comparable, and explicitly labelled. It does not approve
  an Actual Delay or prediction-accuracy KPI.
- `EstimatedTime` is secondary and may later support estimated-delay and prediction-accuracy analysis.
- MDD/TRIAS is the approved primary realtime source direction for the Cologne
  project; GTFS-RT is complementary/secondary for operational states,
  cross-source evidence, and broad coverage.
- The [cross-source reconciliation and Cologne scope contract](source-contracts/cross-source-reconciliation-contract.md)
  makes GTFS-RT analytical scope Cologne-only; Germany-wide feed coverage does
  not expand the project scope. Only validated in-scope Cologne stop, service,
  and alert evidence may enter analytical scope.
- Cross-boundary routes contribute only in-scope Cologne evidence to core KPIs;
  an in-scope stop event is not an in-scope full trip. ICE, IC/EC, NightJet,
  and equivalent long-distance services remain outside core KPI scope.
- `MDD JourneyRef` and GTFS-RT `trip_id` must not be treated as equal. Future
  trip-level cross-source attribution requires a canonical reconciliation layer
  and HIGH-confidence matching, while GTFS-RT alert scope remains distinct.
- `ReportedCause` is provider-reported evidence, not `RootCause`; MDD
  Situation references and cross-source agreement do not by themselves prove
  causality. Source provenance and disagreement must be preserved.
- MDD and GTFS-RT must initially remain source-faithful and separate. Future
  reconciliation is deferred until source-specific staging is designed.
- `ObservedAt`/`CapturedAt` is a first-class future collection attribute;
  repeated observations must preserve estimate evolution rather than only the
  latest state.
- The [future source-observation and persistence contract](source-contracts/future-observation-persistence-contract.md)
  makes `TimetabledTime`, `EstimatedTime`, source-only nullable
  `RecordedAtTime`, and `ObservedAt`/`CapturedAt` independent concepts.
  `RecordedAtTime` is never synthesized; a first-seen non-null value remains
  retained if a later response is NULL or omits the event.
- Situation/Cause evidence is future append-only historical evidence: observed
  versions, source paths, text, reason/effect fields, validity, and association
  scope are preserved rather than reduced to one mutable cause value.
- The earlier PostgreSQL direction for the redesigned append-only observation
  architecture is retained as historical/deferred-cleanup material and is
  superseded for the active roadmap by S87's SQL Server authority decision.
  No migration, production PostgreSQL tables, or persistence implementation
  was created. Database choice does not change source availability.
- The audited successful response did not contain `RecordedAtTime` or a direct
  delay value; this does not establish general MDD absence.
- `RecordedAtTime` is a TRIAS 1.2 optional actual-time field. It was not
  observed in the five tested historical responses, and 1D/7D/30D were
  timetable-only after corrected date alignment. This is not a provider-wide
  absence claim; representative coverage, reliability, and operational
  suitability remain later Stage 8/16 questions.
- `NotServicedStop` is a stop-level flag, not journey-level cancellation.
  `PlannedBay` and `EstimatedBay` remain separate, and situation association
  is not causal evidence.
- Full raw MDD responses are not a permanent or automated source of record by
  default; temporary diagnostic artifacts remain outside source control.
- Permanent or automatic full raw GTFS-RT protobuf retention is likewise not
  the default; any bounded automatic capture requires a later explicit gate.
- Core approved Stage 4 modes: Stadtbahn/Tram, urban/KVB bus, S-Bahn, RE, RB,
  and Regional/Other Bus, always limited to validated in-Cologne events and
  segments.
- Stage 4 validated the RE, RB, and Regional/Other Bus service-level scope;
  their external trip portions remain outside Cologne KPI scope.
- SEV is retained as a separate analytical category, and long-distance rail
  remains outside core KPI scope.
- ICE, IC, NightJet, FlixTrain, and Thalys are outside the core KPI scope for now; source evidence may still be retained.
- Operational claims must be source-supported or deterministically derived and documented.
- The legacy project remains intact as evidence/reference.
- Stage 02 is **APPROVED**; Stage 03 is **COMPLETE** as a design/contract
  stage.
- The [Stage 3 realtime collection design readiness document](stage-03/realtime-collection-design-readiness.md)
  now records **STAGE 3 COMPLETE — 3A APPROVED — 3B/3C DESIGN COMPLETE** and
  carries the implementation boundary forward; it does not approve production
  activation.
- The [Stage 3A disruption/cause evidence audit](stage-03/03a-disruption-cause-alert-evidence-audit.md)
  is **COMPLETE — APPROVED** for the bounded evidence already collected. Its
  conclusions remain evidence-only and non-causal.
- The [cross-source reconciliation and Cologne scope contract](source-contracts/cross-source-reconciliation-contract.md)
  is the approved Stage 3C design contract; it does not approve production
  implementation or matching code.
- The [Stage 3 completion decision](stage-03/stage-03-completion-decision.md)
  records the approved collection/cadence policy, matching design, acceptance
  checks, and carried-forward implementation risks.

## Former open critical questions and S15/S16 refinement

The S14 baseline reclassified the questions below. S15 intentionally reopened
only the historical actual-time availability evidence question, and S16
completed its offline audit without invalidating the S14 semantic or
source-contract conclusions.

1. `RecordedAtTime` semantic role — **RESOLVED_FOR_STAGE_02**. In the five
   tested historical responses, `RecordedAtTime` was not observed, and the
   1D/7D/30D responses were timetable-only after corrected date alignment.
   This is evidence for the tested requests, not a provider-wide absence
   claim. Representative population coverage, reliability, and operational
   suitability remain **DEFERRED_TO_STAGE_8** and **DEFERRED_TO_STAGE_16**.
2. Current MDD permission/access boundary — **RESOLVED_FOR_STAGE_02**. Access
   works; registration/activation, provider terms, and agreed use remain
   documented constraints. Future publication or redistribution requires a
   separate provider-terms check.
3. Quota — **RESOLVED_FOR_STAGE_02** at the conservative-contract level. The
   documented ceiling and shared-caller caveat are captured; operational
   budgeting is **DEFERRED_TO_STAGE_7** and **DEFERRED_TO_STAGE_13**.
4. Authoritative GTFS feed and coverage/update policy — **DEFERRED_TO_STAGE_3**;
   it is not a Stage 02 blocker.
5. Future retained TRIAS fields — **RESOLVED_FOR_STAGE_02** by the field-
   retention matrix; implementation/schema design belongs to later approved
   stages.
6. Full raw-payload retention — **RESOLVED_FOR_STAGE_02** by the approved
   raw-retention decision; Stage 7 now approves only the bounded Stage 8
   temporary exception with protected storage and maximum 24-hour retention.

**FINAL STAGE 02 APPROVAL: APPROVED.**

Stage 02 approval does not approve Actual Delay, a production collector change,
a new polling schedule, or any permanent raw-payload retention policy. The five
host requests were a bounded diagnostic/source-contract validation completed
before S16; S16 itself performed zero MDD HTTP requests. The later lifecycle
validation is separately tracked as post-approval evidence.

## Stage 02A.3 S16 corrected offline result

The raw artifacts at
`/private/tmp/cologne-transit-stage-02a3-3qdsj02c` were all readable JSON:

| Artifact | Bytes | Results | Corrected result |
|---|---:|---:|---|
| `01-1d.raw` | 56,216 | 5 | `HISTORICAL_TIMETABLE_ONLY`; target date matched |
| `02-7d.raw` | 55,838 | 5 | `HISTORICAL_TIMETABLE_ONLY`; target date matched |
| `03-30d.raw` | 54,340 | 5 | `HISTORICAL_TIMETABLE_ONLY`; target date matched |
| `04-90d.raw` | 429 | 0 | `HISTORY_NO_EVENT`; populated `STOPEVENT_NOEVENTFOUND` |
| `05-180d.raw` | 429 | 0 | `HISTORY_NO_EVENT`; populated `STOPEVENT_NOEVENTFOUND` |

The original 1D/7D/30D summaries reported `ThisCall` timetable counts but
`null` min/max values and `INCONCLUSIVE` alignment because the provider
lexical value ended in `Z[GMT]`. The `Z` is explicit UTC; `[GMT]` is a
provider suffix. S16 now strips only that observed suffix before parsing and
preserves timezone-less values as unresolved rather than assigning a guessed
offset. Calendar comparison is explicitly **DATE-ALIGNMENT ONLY**.

The original `response_error_message_count` and
`result_error_message_count` counts came from counting empty `ErrorMessage`
containers. S16 counts only populated messages: both counts are zero for
1D/7D/30D, while the populated response-level message and
`STOPEVENT_NOEVENTFOUND` evidence remain for 90D/180D.

No historical response contained `EstimatedTime`, `RecordedAtTime`, a
recorded/actual timing candidate, or another actual-looking timestamp. The
responses did contain delivery `responseTimestamp` values and processing
`calcTime`; neither is treated as vehicle timing. `estimatedBay` was present
as empty arrays in the result structures and is not an estimated arrival time.

At the tested 1-, 7-, and 30-day horizons, the MDD StopEvent interface
returned historical scheduled events but did not return `EstimatedTime` or
`RecordedAtTime` in those responses. Retrospective StopEvent retrieval
therefore cannot currently be relied on to reconstruct operational actual
delay. A prospective collector remains important for preserving future
realtime evidence, and any later actual-delay capability still requires a
validated actual-time source. Collector cadence and schema are deferred to
later stages.

No StopEvent result was returned for the specific tested Köln Hbf arrival
request at the 90-day or 180-day target times. This does not establish a
retention cutoff or a provider-wide history boundary. No additional
historical request is required for the current Stage 02 source-contract
decision.

The point-in-time Legacy Collector quota audit snapshot at **2026-09-25
11:23:41 UTC** recorded **1,599** collector executions, **1,629** HTTP
attempts, and **35** additional retry attempts. Against the documented
monthly quota of **250,000 requests**, that is approximately **0.6516%**
recorded collector usage, with approximately **99.3484%** remaining against
that documented quota. This proves recorded consumption by the Legacy
Collector at that audit snapshot only; it does not prove total account-level
MDD consumption if another caller shares the same API key or account.

Do not implement around these deferred items before their designated approval
gates.

See the [Stage 02 source-contract package](source-contracts/README.md) and
[detailed Stage 02 synthesis](stages/02-mdd-trias-source-contract-audit.md)
for the full evidence layers, retention matrix, analytical permission matrix,
and acceptance criteria.

## Post-Stage-02 handoff and validation

- Stage 02 approval synchronization and approved decisions are recorded in the
  [Decision Log](DECISION-LOG.md).
- Collector implementation is deferred, and later collector-design stages must
  begin with the [realtime collector requirements](collector-design/realtime-collector-requirements.md).
- The [RecordedAtTime lifecycle validation](source-contracts/mdd-trias-event-lifecycle-validation.md)
  is complete as bounded post-Stage-02 evidence: three Köln Hbf and three Köln
  Neumarkt journeys were reviewed, with no `RecordedAtTime` observed. It does
  not make a provider-wide absence claim or reopen Stage 02.
- The [GTFS-RT historical availability audit](source-contracts/gtfs-rt-historical-availability-audit.md)
  is `BOUNDED VALIDATION COMPLETE`; the tested ±7-day and ±45-day metadata
  windows recovered zero exact-URL captures at both requested horizons.
