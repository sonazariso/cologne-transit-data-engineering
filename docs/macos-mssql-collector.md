# Prepared macOS direct-persistence collector

This document describes the macOS direct-persistence collector and its
Tiered50-only production path. Source-faithful MDD/TRIAS evidence is written
directly to the authoritative Microsoft database. The original additive SQL
contract, exact 43-row Tiered50 materialization, dedicated-login grants, and
fresh host/VM/SQL evidence are complete. The historical Legacy7 `launchd` job
was unloaded and disabled after its final recorded boundary; its implementation
and evidence remain available for audit, but it is not a production fallback.
The Tiered50 LaunchAgent is installed in a separate versioned bundle and is
currently disabled after the first scheduled heartbeat failed its cadence gate.
The bounded October bootstrap is registered for the observed dashboard value
and conservative local accounting; future automatic rollover remains
fail-closed until either the provider reset timezone/boundary contract is
verified and registered or the bounded policy is explicitly approved and
registered.

## Current Tiered50 deployment validation

The approved `TIERED50_PANEL_V1` manifest is preserved unchanged. The
repository now contains an explicit, separately guarded `tiered50` runner
mode, the `MAC_TIERED50_PRODUCTION` collection context, additive SQL
materialization for stable identities 8–50, and a distinct disabled
LaunchAgent template. The Tiered50 path reuses the source-faithful collector,
claims, quota, retry, parser, and transactional SQL persistence core only
after its own gates pass; it does not turn the Legacy7 target count into 50.

The current status is **BLOCKED — TIERED50 STOPPED AFTER FAILED FIRST SCHEDULED
HEARTBEAT; LEGACY7 RETIRED**.
Offline validation passes for the exact 50-target manifest, six-phase
schedule, source-field persistence contract, stable target/bucket claims,
retry/quota boundaries, durable lease, and fail-closed credential handling.
The migration is applied and IDs 8–50 are represented; administrator activation
committed the target state and sampling era, then the scheduled runtime was
stopped after its first bucket failed the cadence gate. No manual MDD request
or manual Heumarkt request was issued by materialization, retirement, or
preflight.

Fresh evidence records six consecutive expected Legacy7 buckets before its
retirement, current SQL capacity and checksum-verified backup readiness,
launchd and power state, and an observable VMware guest. The supplied
Monitoring screenshot records 554 cumulative requests against a 250000
monthly limit for the selected October 2026 category. Provider cutoff,
freshness, reset bounds/timezone, and post-cutoff/external usage remain
unverified; the automatic future-month path therefore fails closed. The first
scheduled Tiered50 bucket ran 18 target audits, with 2 successes and 16
heartbeat-budget skips, and is recorded as an outage. The machine-readable result is
[`docs/operational/tiered50-deployment-validation-evidence.json`](operational/tiered50-deployment-validation-evidence.json), with the operator
summary in [`docs/operational/tiered50-deployment-validation-report.md`](operational/tiered50-deployment-validation-report.md).

## Cadence-correction addendum — 2026-10-07

The existing cutover evidence reports that the following three Mac runs each
contain seven target audits, seven initial HTTP attempts, and seven parsed
snapshots:

- `127809b0-70fa-4770-9943-664942be5fc3`;
- `83c3f261-5bf0-484e-8de6-b6f0e0724a49`; and
- `5a7e014c-6266-421c-b402-98c4f1cb89b6`.

Review identified this as a bounded seven-target-per-heartbeat oversampling
window, not the approved one-target-per-five-minute Legacy7 cadence. The rows
remain unchanged and must be excluded from simple Windows-versus-Mac cadence
volume comparisons. A fresh read-only SQL probe confirmed the counts and the
append-only methodology evidence recorded actual 21,
historical-equivalent 3, and excess 18 initial attempts. The additive lineage
migration also passed live validation. During the pre-activation recovery
window the machine-specific LaunchAgent was unloaded and disabled while the
SQL VM was unreachable; no new MDD request was made in that window. The
dedicated-login, preflight, and corrected scheduled-heartbeat gates later
passed.

Corrected future Mac audits use `MAC_LEGACY7_PRODUCTION`, preserve
`SamplingTargetId`, `SamplingSlot`, `LogicalDueBucket`, and
`SelectionPolicyVersion`, and use the dedicated `CologneTransitCollector`
runtime login. The source-faithful slot mapping and selector definition are
validated by [`scripts/legacy7_sql_probe.py`](../scripts/legacy7_sql_probe.py).
The exact grants for the dedicated database role are
listed in [`scripts/provision_macos_runtime_login.py`](../scripts/provision_macos_runtime_login.py)
and contain no secret material.

The one-time administrator provisioning grants this exact role/object set.
It is generated from the code-backed heartbeat permission matrix in
[`scripts/provision_macos_runtime_login.py`](../scripts/provision_macos_runtime_login.py):

| Permission | Object |
| --- | --- |
| `SELECT` | `ctl.MddRealtimeSamplingTarget`, `ctl.MddRealtimeSamplingSlot`, `ctl.MddMacTiered50ProductionTarget` |
| `EXECUTE, VIEW DEFINITION` | `ctl.uspGetMddRealtimeSamplingTarget` |
| `VIEW DEFINITION` | `ctl.MddMacTargetAudit` (lineage-column validation only) |
| `SELECT, INSERT, UPDATE` | `ctl.MddMacCollectorRun` |
| `SELECT, INSERT, UPDATE` | `ctl.MddMacProductionLease` |
| `INSERT` | `ctl.MddMacProductionLeaseEvent` |
| `SELECT, INSERT, UPDATE` | `ctl.MddMacDispatcherClaim`, `ctl.MddMacTargetClaim`, `ctl.MddMacTargetAudit` |
| `INSERT` | `ctl.MddMacDispatcherClaimEvent`, `ctl.MddMacTargetClaimEvent`, `ctl.MddMacHttpAttempt`, `ctl.MddMacQuotaAudit` |
| `SELECT, INSERT, UPDATE` | `ctl.MddMacQuotaPermit` |
| `SELECT` | `ctl.MddMacSharedQuotaAccounting`, `ctl.MddMacConservativeBootstrapAuthorization`, `ctl.MddMacQuotaResetContract` |
| `EXECUTE` | `ctl.uspEnsureMddMacQuotaPeriod` |
| `EXECUTE` | `stg.uspPersistMddMacSnapshot` |

The runtime role receives no `DELETE`, schema, migration, broad server-role,
database-owner, or datareader/datawriter permission. Provisioning verifies the
positive target/selector and runtime-schema path, plus metadata-based denials
for server administration, table control/alter/delete, database creation, and
direct reads of unrelated import/source-evidence/analytics objects. The
runtime role does not receive direct permission on
`stg.MddRealtimeStopObservation`, `stg.MddRealtimeSituationObservation`,
`stg.MddRealtimeStopSituationLink`, `ctl.MddMacSamplingEra`,
`ctl.MddMacMethodologyEvidence`, `analytics.vwMddMacRecordedAtTimeHistory`, or
`analytics.vwMddMacSourceFieldCoverage`; administrator/import/diagnostic tools
use an administrator credential for those reads.

Schema validation is split deliberately. `MssqlPersistence.validate_full_schema()`
is an administrator/migration/import path and may inspect the complete additive
surface, including import tables, all `ctl.MddMac*`/`stg.MddMac*` evidence
objects, and analytics views. `validate_runtime_schema()` is the production
path used by the runner, preflight, and dedicated-login positive validation;
it checks only the target/slot objects, runtime claim/audit/quota objects,
the two runtime procedures, and the `SamplingSlot`/
`SelectionPolicyVersion` lineage columns. It never requires metadata visibility
over unrelated migration objects and does not grant database-level
`VIEW DEFINITION`.

Dedicated-login provisioning is recovery-safe. The candidate password is
stored first in the owner-only pending Keychain item
`MSSQL_PASSWORD_PENDING`, the SQL login is created or explicitly rotated, and
positive/negative checks run using that exact candidate. Finalization promotes
the same value to `MSSQL_PASSWORD`, writes only non-secret runtime JSON, and
removes the pending item last. A post-commit failure returns
`CREDENTIAL_FINALIZATION_RECOVERY_REQUIRED`, leaves the LaunchAgent disabled,
retains the pending item, and can be resumed with `--resume` without generating
another password.

The three preserved cutover-validation Mac runs completed successfully:
`127809b0-70fa-4770-9943-664942be5fc3` (bucket `5971301`),
`83c3f261-5bf0-484e-8de6-b6f0e0724a49` (bucket `5971302`), and
`5a7e014c-6266-421c-b402-98c4f1cb89b6` (bucket `5971303`). Each run had
seven successful target audits, seven HTTP attempts, seven parsed snapshots,
and no duplicate target/bucket keys.

The corrected production activation then completed three ordinary scheduled
heartbeats. Their run IDs were
`85d2d601-ff66-4fd1-b3e6-105a1a384478` (bucket `5971342`, slot 3, target 1),
`8286961f-0edc-4b64-b5ef-15ae776c1389` (bucket `5971343`, slot 4, target 4),
and `24cb4cab-38f9-4629-bedf-6bf41c52b57a` (bucket `5971344`, slot 5, target
6). Each completed with one SQL-selected target, HTTP 200, persisted lineage,
and status `COMPLETE` under `MAC_LEGACY7_PRODUCTION`. The append-only sampling
era `MAC_LEGACY7_PRODUCTION` is effective at the first corrected heartbeat,
`2026-10-07T19:51:46.041Z`; it is not a Tiered50 cutover.

The durable sanitized machine-readable closeout record is
[`docs/operational/legacy7-production-transition-closeout-evidence.json`](operational/legacy7-production-transition-closeout-evidence.json).

The durable historical source is explicitly
`/Users/admin/cologne-transit-data/live-research/collector.sqlite3`. Its
read-only proof is: 575,041,536 bytes, SHA-256
`0edc9b1a56cb6128ac5bed6508e5c37b9b4d1ab18c1d78092e6fcb1ac9b674fc`,
SQLite application schema 5, role `LIVE_RESEARCH`, context
`LIVE_RESEARCH_SINGLE_1`, integrity/quick checks `ok`, and one contiguous
5.9306-hour source session from `2026-10-01T16:59:32.654121Z` through
`2026-10-01T22:55:22.809977Z`. This is historical research evidence and is
never relabeled as Mac, Windows, Legacy7, or Tiered50 production evidence.

## Runtime boundary

The macOS working copy contains the parser, direct DB-API adapter, migration,
offline tests, and a safe-by-default tracked `launchd` template. The user-local
LaunchAgent is generated from that template and is active; it runs from the
owner-only runtime bundle under
`~/Library/Application Support/CologneTransitIntelligence/`. The adapter uses
the pinned `pymssql==2.3.2` wheel because the actual Python 3.9 x86_64
environment has no installed ODBC client.

Non-secret SQL/endpoint settings are stored in the owner-only local runtime
configuration at
`~/Library/Application Support/CologneTransitIntelligence/runtime.json`
(mode `0600`, parent directory `0700`). `MDD_API_KEY` and the SQL password are
resolved from macOS Keychain generic-password items at runtime; they are never
stored in the repository, plist, runtime JSON, or diagnostics. Explicit
environment values remain supported as one-process overrides.

The runner is fail-closed. It requires all of the following before it
can construct an enabled MDD client:

- `COLLECTOR_MAC_SQL_ENABLED=1`;
- `COLLECTOR_WINDOWS_RUNTIME_STATE=CONFIRMED_RETIRED`;
- `COLLECTOR_CUTOVER_APPROVED=1`;
- `COLLECTOR_MAC_SQL_TARGET_SCOPE=LEGACY7_EXPLICIT_APPROVAL`;
- `COLLECTOR_MAC_SQL_VALIDATION_POLICY=OFFLINE_RECONCILIATION_ONLY`; and
- a valid `MDD_API_KEY`.

It then reads the unchanged seven rows from `ctl.MddRealtimeSamplingTarget`,
the ten rows from `ctl.MddRealtimeSamplingSlot`, and the existing
`ctl.uspGetMddRealtimeSamplingTarget` definition. It requires IDs 1–7, the
existing StopPointRef mapping, and exactly one SQL-selected target for the
current deterministic UTC bucket. It does not use the deferred 50-target
manifest as a live target list and does not create a new sampling era.

The exact task `\Cologne Transit Realtime Collector` is now operator-confirmed
`DISABLED`. Read-only SQL confirmation captured two post-retirement boundaries
with zero newer Windows runs or realtime observations. The proof boundary was
`2026-10-07T16:37:17.988028Z`; this is an evidence boundary, not an invented
operator-disable timestamp. The authoritative final Windows run is
`CollectorRunId 40393`, completed at `2026-10-07T13:43:56Z`, and the final
Windows observation is at `2026-10-07T13:43:55Z`. No SQL-side task disable
operation was used for the final retirement. Existing SQLite evidence was
reconciled offline; no manual Heumarkt request was made or required.

The active runtime uses the dedicated `CologneTransitCollector` login and
`CologneTransitCollectorRuntime` database role. Provisioning and finalization
completed with the pending Keychain credential removed only after positive and
negative permission checks passed. The runtime login reports `sysadmin = false`
and belongs to no broad server or database data role.

## Old and prepared flow

```text
retired Windows collector
        │
        ├── unchanged ctl.MddCollectorRun / stg.MddRealtime* history
        └── existing Power BI-facing views

prepared macOS Tiered50 runner (currently disabled)
        │  current-bucket, no catch-up, direct DB-API connection
        ▼
ctl.MddMac* claims/audit/quota ── transaction ──► stg.MddMac* source evidence
                                                        │
                                                        └── analytics.vwMddMac*
```

The migration is additive and parallel. It does not alter, backfill, delete,
truncate, reseed, or rename any existing `ctl.MddCollectorRun`, sampling
target/slot, `stg.MddRealtime*`, GTFS, warehouse, or Power BI-facing object.
Power BI is outside this work.

## Persistence contract

Every source response becomes one `stg.MddMacSourceSnapshot` plus zero or more
occurrence rows. The snapshot is the normalized, secret-scrubbed parser
document and its SHA-256; it is not a permanent raw HTTP archive. Each child
row has a foreign key to its snapshot, and each snapshot has foreign keys to
the new run and audit rows.

| Object | Grain and key | Nullable/source-history rules | Duplicate/index rule |
| --- | --- | --- | --- |
| `ctl.MddMacCollectorRun` | One dispatcher run; identity `MacCollectorRunId`, unique `RunId` | Completion/status/summary remain nullable until the run finishes | Unique `RunId`; status/started index |
| `ctl.MddMacDispatcherClaim` | One `(PanelVersion, LogicalDueBucket)` claim | Completed time is nullable; stale claims are visible, not silently removed | Composite primary key; run FK |
| `ctl.MddMacDispatcherClaimEvent` | Append-only claim attempt event | Existing status may be NULL for a first claim | Identity key; bucket/time index; run FK |
| `ctl.MddMacTargetClaim` | One `(PanelVersion, TargetIdentity, LogicalDueBucket)` target claim | Start/completion are nullable while pending | Composite primary key; run FK |
| `ctl.MddMacTargetClaimEvent` | Append-only target claim attempt event | Existing status may be NULL for a first claim | Identity key; target/bucket/time index; run FK |
| `ctl.MddMacTargetAudit` | One target outcome within a run/bucket; identity `AuditId` | HTTP/parser/persistence/error fields remain nullable when not observed | Unique `(RunId, TargetIdentity, LogicalDueBucket)`; status/time index; run FK |
| `ctl.MddMacMethodologyEvidence` | Append-only bounded methodology/oversampling evidence | Window, counts, run IDs, and sanitized evidence JSON remain source-lineage fields | Identity key; no historical audit update/delete path |
| `ctl.MddMacHttpAttempt` | One actual transport attempt per audit/attempt number | HTTP status, failure, retry, and provider confirmation can be NULL when inapplicable | Unique `(AuditId, AttemptNumber)`; completion-time index; audit and quota-permit FKs |
| `ctl.MddMacQuotaPermit` | One quota reservation/finalization | Finalization time is NULL while pending | Permit GUID primary key; status is never inferred from local simulation |
| `ctl.MddMacQuotaAudit` | Append-only decision record for a run/target/bucket | Target/bucket may be NULL for run-level decisions | Identity key; run FK |
| `ctl.MddMacSamplingEra` | Append-only prepared-era/cutover evidence | Effective time may be NULL; production current-era identity is tracked by the append-only current-era pointer and actual nonfuture activation timestamp | Identity key; no historical rewrite |
| `stg.MddMacSourceSnapshot` | One parsed response observation per audit; identity `SnapshotId` | Source response timestamp is lexical and nullable; `ObservedAtUtc` is collector lineage and required | Unique `AuditId`; target/observed index; run/audit FKs |
| `stg.MddMacCallObservation` | One source call occurrence: event + `CallScope` + call ordinal | Stop identity, flags, service identity, and source paths are independently nullable | Unique `(SnapshotId, EventOrdinal, CallScope, CallOrdinal)`; snapshot FK |
| `stg.MddMacTimingObservation` | One timing occurrence: event + call + `ARRIVAL`/`DEPARTURE` | Timetabled, estimated, low/high, and RecordedAtTime are independent nullable lexical source fields | Unique `(SnapshotId, EventOrdinal, CallScope, CallOrdinal, TimingContext)`; source-identity index; snapshot FK |
| `stg.MddMacSituationEvidence` | One provider Situation/reference evidence occurrence | Situation fields, validity, affected entity, and association scope remain nullable | Identity key; participant/situation/snapshot index; snapshot FK |
| `stg.MddMacServiceSectionEvidence` | One ServiceSection occurrence by event/section ordinal/source path | All section identity and mode fields remain source-nullable; semantic role is required | Unique `(SnapshotId, EventOrdinal, SectionOrdinal, SourcePath)`; source-order index; snapshot FK |
| `stg.MddMacAttributeEvidence` | One Attribute/GeneralAttribute occurrence by event/attribute ordinal/source path | Code/text/call association fields remain nullable | Unique `(SnapshotId, EventOrdinal, AttributeOrdinal, SourcePath)`; snapshot FK |
| `stg.MddMacResponseError` | One provider response error field occurrence | Error value/scope/path are required evidence, not a thrown-away message | Unique `(SnapshotId, SourcePath, ErrorFieldName)`; snapshot FK |
| `stg.MddMacParserGap` | One unmapped-but-retained source field occurrence | Source value/representation/hint/context may be NULL; field/path/status are required | Unique `(SnapshotId, SourcePath, NormalizedFieldName)`; field/snapshot index; snapshot FK |

The migration creates `stg.uspPersistMddMacSnapshot`, which accepts the
normalized child sets as JSON arrays and inserts the complete response inside
one SQL transaction. An already-persisted `AuditId` is treated as an idempotent
duplicate and returns the existing snapshot without inserting another child
set. A parser/persistence exception rolls back the snapshot and all child
rows; the collector then completes the audit as failed.

## Historical SQLite import and reconciliation

The separate importer is `scripts/import_live_research_sqlite.py`. The source
argument is mandatory; there is no source auto-discovery. Without `--apply` it
performs only read-only proofing. The apply path first runs the read-only
Windows-history overlap audit, then writes a generic import batch and durable
source-lineage rows. It never deduplicates on station plus rounded timestamp,
never overwrites Windows rows, and never relabels research rows as runtime
rows.

The proven source inventory is:

- `FULL_IMPORT`: `collector_runs` (75), `dispatcher_claims` (75),
  `dispatcher_claim_events` (75), `target_claims` (75), `claim_events` (75),
  `target_audits` (75), `http_attempts` (73), `quota_permits` (73),
  `quota_audit` (76), `parsed_snapshots` (70), `timing_observations` (72,190),
  `recorded_at_time_evidence` (0), and `service_section_observations` (1,400).
- `LINEAGE_ONLY`: `database_metadata` (6), `manifest_targets` (50),
  `sampling_eras` (0), `shared_usage` (1), `raw_evidence` (4), and SQLite's
  `sqlite_sequence` (9). The two SQLite reporting aliases are views and are
  not durable import tables; their source timing rows are imported instead.
- `NOT_IMPORTED_WITH_EXPLICIT_REASON`: none.

The exact dry-run and apply commands are:

```text
PYTHONPATH=src python3 scripts/import_live_research_sqlite.py \
  --source /Users/admin/cologne-transit-data/live-research/collector.sqlite3

PYTHONPATH=src python3 scripts/import_live_research_sqlite.py \
  --source /Users/admin/cologne-transit-data/live-research/collector.sqlite3 \
  --apply
```

Each batch is `IN_PROGRESS`, `COMPLETE`, or `FAILED`; a failed batch is never
reported as complete. The source SHA-256/table/source-key uniqueness contract
makes an exact rerun idempotent and records duplicates explicitly. Snapshot
JSON is passed through the same source-faithful SQL procedure as native Mac
evidence; timing, ServiceSection, RecordedAtTime, Situation, Attribute,
response-error, and parser-gap projections retain their source fields and
lineage.

The schema migration, fresh capacity preflight, six-hour import, exact
idempotency rerun, and read-only post-import reconciliation are complete. Four
failed import batches remain retained and auditable, including the interrupted
partial batch; no failed history was deleted. The final report records 70
snapshots, 38,120 calls, 72,190 timings, 1,400 ServiceSections, 121 parser
gaps, zero unexplained differences, and zero source `RecordedAtTime` values.
The historical capacity/import work is complete; the former Legacy7 path also
has preserved dedicated-login, scheduler, and heartbeat evidence. See the
[operational record](operational/s98-live-research-import-and-cutover.md).

## Timing and RecordedAtTime

`TimetabledTimeSourceText`, `EstimatedTimeSourceText`, low/high estimate
fields, and `RecordedAtTimeSourceText` are persisted separately. The adapter
does not create an `ActualTime` column, derive an actual time, or use the
collector timestamp as source timing.

The source-event identity is deterministic and MDD-local. Its conservative
basis is operating day, journey, stop point, timetable, and arrival/departure
context; mutable estimates and RecordedAtTime are not key fields. Every
source occurrence is appended with snapshot, event ordinal, call scope,
arrival/departure context, lexical value, and source path. A later snapshot
with a NULL RecordedAtTime inserts a new timing row and does not overwrite the
earlier non-NULL row.

`analytics.vwMddMacRecordedAtTimeHistory` exposes both the latest source row
(which can be NULL) and the first/last non-NULL observed source values. This
preserves the distinction between “latest response omitted it” and “it was
never observed.”

## Situation, cause, and gaps

Situation/attribute data is provider-reported evidence only. The schema keeps
Situation number, participant, summary/description/detail, validity, affected
entity/journey/line JSON, source paths, associations, and source
representation. It has no `RootCause` field and the adapter never infers
cancellation, cause, or resolution from a missing event.

Multiple ServiceSections and multiple Attribute/GeneralAttribute occurrences
remain separate rows. Unmapped relevant fields are retained in
`stg.MddMacParserGap` with path, type, value/representation, mapping status,
and context. Full request headers, API keys, and permanent raw response bytes
are not persisted.

## Scheduling, retry, and retention controls

- The installed scheduler interval is five minutes with `StartInterval=300`,
  `ThrottleInterval=300`, no `RunAtLoad`, and no missed-bucket replay.
- The approved Tiered50 cadence is 110 logical requests per 30-minute
  super-cycle and 220/hour, with the persisted phase sequence
  `[19,18,18,18,19,18]`. The historical Legacy7 cadence and three oversampling
  runs remain source evidence only and are excluded from current production
  volume comparisons.
- Dispatcher and target claims are durable and stale claims are observable.
- Initial and retry quota reservations are durable and fail closed when the
  provider-wide baseline is incomplete. Unknown shared-consumer usage is not
  treated as zero; HTTP 500 is not classified as quota exhaustion without
  provider evidence.
- A temporary raw-evidence directory is optional, owner-only (`0700`), with
  owner-only files (`0600`), secret-marker rejection, and automatic maximum
  retention of 24 hours. It is outside Git and is not a database source of
  record.

## Connectivity and validation evidence

The live SQL validation established reachability to `172.16.192.128:1433`,
selected database `CologneTransitIntelligence`, preserved sampling-target IDs
1–7, and verified prepared IDs 8–50. The additive migration and exact
materialization were applied idempotently. The production preflight used the
same path and passed without issuing an MDD request. Fresh availability evidence
captured the final Legacy7 boundary and six accounted/successful expected
buckets; no Tiered50 network request has been issued.

The read-only overlap audit found zero Windows-era collector runs, Heumarkt
stop observations, or situation observations in the historical six-hour
interval. That is a count result only; exact equivalence, shared events, and
same-event different snapshots remain `NOT_PROVABLE` without legacy payload
hashes/source-event identities. No destructive merge or timestamp-based
deduplication was performed.

The historical pre-import capacity gate was satisfied before import. The
post-import evidence recorded 46,185,209,856 bytes of free `C:\` volume space
and 45,050,331,136 bytes of projected reserve. Four failed import batches
remain retained as immutable `FAILED` history; no source rows were deleted.

The migration is applied by `scripts/apply_mssql_migration.py` only with an
explicit `--apply`. It is batch-aware, atomic, refuses a different database
name, validates required objects before the single commit, captures pre/post
row-count baselines, and rejects any decrease or target-ID change.
`scripts/validate_mssql_migration.py` provides static and read-only live
validation. The direct adapter, importer, and parser tests use fixtures and
fake connections; they do not write fixture rows into the authoritative
database.

## Current operation and rollback

The historical controlled cutover established the Legacy7 scope. Its tracked
and generated plist are retained as disabled audit artifacts. The current
Tiered50 plist is the owner-local artifact at
`~/Library/LaunchAgents/com.cologne-transit-intelligence.mdd-tiered50-collector.plist`;
it uses the exact interpreter
`/Library/Developer/CommandLineTools/usr/bin/python3`, `StartInterval=300`,
`RunAtLoad=false`, and non-secret gates. It points to a separate versioned
owner-only runtime bundle and is currently disabled. If a fail-closed gate
fails, preserve evidence and leave both Legacy7 and Tiered50 disabled; do not
issue a manual Heumarkt request or reactivate a Windows/Legacy7 writer.

During pre-activation recovery, the SQL VM was temporarily unavailable and a
scheduled invocation exited during connectivity preflight before opening a SQL
run or issuing an MDD request. The job was re-enabled only after the VM was
reachable and all live gates passed.

Recovery is scheduler-level and additive: stop/unload Tiered50, leave all new
evidence intact for diagnosis, and resume Tiered50 only after its gates pass
again. Legacy7 remains retired and no Windows collector may be resumed by this
runtime. No migration rollback should drop new objects or delete historical
rows. Existing tables and Power BI objects are untouched, so this preparation
has no destructive database rollback step.

## Artifacts

- Migration: [`sql/mdd-mac-collector/create-source-faithful-persistence.sql`](../sql/mdd-mac-collector/create-source-faithful-persistence.sql)
- Adapter: [`src/cologne_transit_collector/mssql_persistence.py`](../src/cologne_transit_collector/mssql_persistence.py)
- Mac runner: [`src/cologne_transit_collector/macos_runner.py`](../src/cologne_transit_collector/macos_runner.py)
- Safe tracked scheduler template: [`deployment/launchd/com.cologne-transit-intelligence.mdd-collector.plist`](../deployment/launchd/com.cologne-transit-intelligence.mdd-collector.plist)
- Runtime configuration: [`scripts/configure_macos_runtime.py`](../scripts/configure_macos_runtime.py)
- LaunchAgent installer: [`scripts/install_macos_launch_agent.py`](../scripts/install_macos_launch_agent.py)
- Production preflight: [`scripts/macos_production_preflight.py`](../scripts/macos_production_preflight.py)
- Read-only Legacy7 SQL probe: [`scripts/legacy7_sql_probe.py`](../scripts/legacy7_sql_probe.py)
- Dedicated-login provisioning and permission tests: [`scripts/provision_macos_runtime_login.py`](../scripts/provision_macos_runtime_login.py)
- Append-only cadence/era evidence writer: [`scripts/record_legacy7_methodology_evidence.py`](../scripts/record_legacy7_methodology_evidence.py)
- Windows retirement confirmation: [`scripts/confirm_windows_retirement.py`](../scripts/confirm_windows_retirement.py)
- Static/live migration validator: [`scripts/validate_mssql_migration.py`](../scripts/validate_mssql_migration.py)
- Additive migration runner: [`scripts/apply_mssql_migration.py`](../scripts/apply_mssql_migration.py)
- Explicit SQLite importer: [`scripts/import_live_research_sqlite.py`](../scripts/import_live_research_sqlite.py)
- Offline contract tests: [`tests/test_mssql_persistence.py`](../tests/test_mssql_persistence.py), [`tests/test_mssql_runtime.py`](../tests/test_mssql_runtime.py), [`tests/test_legacy7_schedule.py`](../tests/test_legacy7_schedule.py), [`tests/test_macos_legacy7_security.py`](../tests/test_macos_legacy7_security.py), and [`tests/test_s91_sqlite_import.py`](../tests/test_s91_sqlite_import.py)
