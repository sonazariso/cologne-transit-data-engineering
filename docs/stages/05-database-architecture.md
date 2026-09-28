# Stage 5 — Database Architecture

**Status:** COMPLETE — APPROVED ARCHITECTURE
**Decision:** [D-029](../DECISION-LOG.md#d-029--stage-5-database-architecture)
**Date:** 2026-09-28

This document is the authoritative Stage 5 architecture approval for the
Cologne Transport Intelligence repository. It defines the future PostgreSQL
contracts without creating a database or authorizing production runtime
implementation.

## 1. Purpose

Stage 5 approves a durable database architecture for:

- complete, source-faithful static GTFS snapshots;
- append-only MDD realtime response observations;
- a bounded, later-approved GTFS-Realtime persistence boundary;
- validated canonical and cross-source structures;
- the first static analytical warehouse; and
- later business-facing analytics consumption.

The architecture is designed to preserve evidence first and derive analytical
state afterward. It is intentionally compatible with the approved Stage 4
Cologne scope and mode rules, the Stage 3 reconciliation design, and the
approved source-observation contracts.

The companion [Stage 5 table/grain catalog](05-table-grain-catalog.md) is part
of this approval. It contains 42 named logical entity contracts. A logical
contract is not a created PostgreSQL table.

## 2. Stage boundary

### Included

This stage approves:

- PostgreSQL as the future authoritative database platform;
- the target major-version direction;
- a clean redesign rather than a SQL Server lift-and-shift;
- schema/layer responsibilities and source-to-layer flow;
- logical table names, grains, identities, lineage, and guardrails;
- source snapshot and history strategy;
- key, uniqueness, data-type, idempotency, index, and partitioning direction;
- audit, sampling-policy, security, and backup/recovery requirements; and
- the handoff to Stage 6 and later implementation stages.

### Explicitly excluded

Stage 5 does not:

- install PostgreSQL or start Docker;
- create a PostgreSQL database, schema, table, view, function, or migration;
- execute SQL or connect to SQL Server;
- copy or transform legacy data;
- implement a static warehouse, collector, scheduler, matching layer, or
  Power BI model; or
- start Stage 6.

Stage 5 moved from **IN PROGRESS** during authoring to **COMPLETE — APPROVED
ARCHITECTURE** only after the acceptance checks in Section 23 passed. Stage 6
is **NOT STARTED / NEXT**.

## 3. Inputs and architectural constraints

The architecture follows the current authoritative repository evidence,
including:

- [Stage 4 scope and mode classification](04-cologne-scope-mode-classification.md)
  and its current evidence snapshot;
- [Stage 3 completion decision](../stage-03/stage-03-completion-decision.md);
- [collection strategy and cadence contract](../stage-03/03b-collection-strategy-cadence-contract.md);
- [cross-source matching design](../stage-03/03c-cross-source-matching-design.md);
- [future observation persistence contract](../source-contracts/future-observation-persistence-contract.md);
- [MDD/TRIAS response contract](../source-contracts/mdd-trias-response-contract.md);
- [MDD/TRIAS field-retention matrix](../source-contracts/mdd-trias-field-retention-matrix.md);
- [cross-source reconciliation contract](../source-contracts/cross-source-reconciliation-contract.md);
- [realtime collector requirements](../collector-design/realtime-collector-requirements.md); and
- [legacy reference rules](../../references/README.md).

The legacy SQL Server architecture is evidence for review, not the target
specification. The new model must not copy its table layout wholesale.

## 4. PostgreSQL platform and redesign decision

### 4.1 Platform

PostgreSQL is approved as the future authoritative database platform for the
redesigned system.

The target major version is **PostgreSQL 18**. Implementation must use the
latest supported PostgreSQL 18 minor available at implementation time. This
architecture deliberately does not hardcode a future minor Docker image tag.
PostgreSQL 19 is not selected for this project while it is prerelease/beta in
the September 2026 project context.

No PostgreSQL server, container, extension, role, or database is created by
this stage.

### 4.2 Migration strategy

The approved strategy is **CLEAN POSTGRESQL REDESIGN**.

The legacy SQL Server system remains **LEGACY IMPLEMENTATION EVIDENCE**. It is
not the authoritative target model and is not approved for 1:1 migration.
Any future legacy-history import requires an explicit, source-lineaged
migration process that preserves original timestamps, source IDs, NULL
semantics, and provenance. Imported rows must remain distinguishable from
PostgreSQL-native observations through lineage such as:

- `OriginSystem`;
- `SourceRun`; and
- `LegacyImportBatch`.

No import, migration, or legacy-data mutation occurs in Stage 5.

## 5. Logical layer architecture

The approved PostgreSQL schemas are logical architecture boundaries. They are
not yet physical schemas.

| Layer | Responsibility | Source-of-truth rule |
|---|---|---|
| `ctl` | Control, configuration, ingestion audit, sampling-policy metadata, source retrieval metadata, validation outcomes, and raw-artifact metadata | Durable operational lineage; control history must remain interpretable |
| `src` | Durable, source-faithful parsed evidence from VRS static GTFS, MDD/TRIAS, and later-approved GTFS-Realtime subsets | Append-only for observational/history data; source rows are not disposable staging |
| `wrk` | Rebuildable views, functions, and transformations used to prepare validated outputs | Never the only home of irreplaceable source history |
| `core` | Validated canonical identities and cross-source reconciliation structures | Derived/validated state; source IDs and evidence remain separately traceable |
| `dw` | Validated analytical dimensions, bridges, and facts | Derived analytical warehouse with feed/source lineage |
| `analytics` | Business-facing semantic views or later materialized reporting objects | Consumption layer only; never a source of truth |

`Power BI` is a later consumer of approved `analytics` objects. It is not a
database layer and is not modified in this stage.

### 5.1 Source-to-layer flow

```text
SOURCE
  -> ctl / src
  -> wrk
  -> core where cross-source reconciliation is needed
  -> dw
  -> analytics
  -> Power BI
```

The source-faithful layer remains independently interpretable if matching,
canonicalization, or analytics logic is later rebuilt.

## 6. Source-faithful policy

`src` is durable source evidence, not disposable staging.

- Observational source rows are append-only. A later response must not erase an
  earlier observation.
- Derived current/latest state may be rebuilt in `wrk`, `core`, `dw`, or
  `analytics`; it does not replace source history.
- `NULL` remains distinct from `FALSE`, zero, empty text, and not-observed.
- Source natural identifiers remain source columns and are never assumed to be
  universal cross-source keys.
- Duplicate source identifiers are retained as evidence of source quality.
- Source timestamps and source text are retained before normalization.
- Source disagreement between MDD and GTFS-RT remains visible.
- Source disappearance is an observation state; it is not a command to delete
  prior evidence.

## 7. Static GTFS snapshot and history strategy

The current official source direction is the VRS Global-ID GTFS validated in
Stage 4. The architecture retains the complete approved feed snapshot in
`src`, not only rows whose stops are in Cologne. Cross-boundary trips and
adjacent-service context require source evidence outside Cologne. Stage 4
geographical rules apply when building validated and analytical layers.

The architecture must support multi-million-row static loads. Current snapshot
evidence is approximately 33 agencies, 957 routes, 30,364 stops, 126,160
trips, 2,943,746 `stop_times` rows, and 1,167,322 shape rows.

### 7.1 Immutable content identity

The logical `ctl.gtfs_feed_snapshot` entity has one row per unique promoted
GTFS source ZIP/content snapshot. It records at least:

`FeedSnapshotKey`, `SourceSystem`, `SourceUrl`, `RetrievedAtUtc`, `ZipSha256`,
`ZipBytes`, `FeedVersion`, `FeedStartDate`, `FeedEndDate`, `ParserVersion`,
and `LoadStatus`.

If the same ZIP/hash is retrieved again:

- the retrieval remains auditable through control/artifact metadata;
- a second parsed source snapshot is not created; and
- the existing immutable snapshot identity is reused.

The compressed ZIP is retained outside PostgreSQL or in approved artifact
storage. PostgreSQL stores its hash/path/metadata, not the complete ZIP as a
database BLOB. Any snapshot used to create durable warehouse history must
remain reproducible.

### 7.2 Source-row preservation

The logical static source contracts are:

`src.gtfs_agency_row`, `src.gtfs_route_row`, `src.gtfs_stop_row`,
`src.gtfs_trip_row`, `src.gtfs_stop_time_row`, `src.gtfs_calendar_row`,
`src.gtfs_calendar_date_row`, `src.gtfs_frequency_row`,
`src.gtfs_transfer_row`, `src.gtfs_shape_point_row`, and
`src.gtfs_feed_info_row`.

Every source row preserves `FeedSnapshotKey` and `SourceRowNumber` in addition
to the source columns. An internal row key plus unique
`FeedSnapshotKey + SourceRowNumber` is the recommended source-row identity.
Source natural IDs such as `route_id`, `stop_id`, `trip_id`, and `service_id`
are indexed lookup values, not physical primary keys in `src`. A malformed
duplicate source ID must remain visible rather than being rejected or
silently overwritten.

Stage 4's core geography remains the direct source rule `stop_id` beginning
with `de:05315:`. The complete source feed is retained first; the rule is
applied when constructing validated/analytical scope.

## 8. MDD/TRIAS realtime source architecture

MDD persistence is designed around immutable response observations:

- `src.mdd_response_snapshot`: one executed MDD HTTP response, with collector
  run, sampling policy, request, response status/size/hash, content type,
  parser version, result count, and request/response lineage. Credentials are
  never persisted.
- `src.mdd_stop_event_observation`: one `StopEventResult` observed in one
  response. Repeated observations are retained.
- `src.mdd_call_observation`: one `CallAtStop` occurrence belonging to one
  observed stop event. Call scope is explicit: `THIS`, `PREVIOUS`, or
  `ONWARD`; call ordinal/sequence is retained where available.
- `src.mdd_situation_observation`: one Situation object observed in one
  response, including source identity, version, text, and validity fields.
- `src.mdd_stop_event_situation_link`: one observed association between a stop
  event observation and a Situation, including association scope.
- `src.mdd_service_attribute_observation`: one observed service
  `Attribute`/`GeneralAttribute` belonging to a stop event or service
  observation. `IncidentCall` code/text evidence is not an `ErrorMessage`.

Arrival and departure timing remain separate. Timetabled, estimated, and
recorded timestamps are retained separately for every timing context in which
the source provides them.

## 9. GTFS-Realtime persistence boundary

GTFS-RT is complementary. The architecture reserves these logical entities
for later approved in-scope persistence:

- `src.gtfsrt_feed_snapshot`;
- `src.gtfsrt_trip_update_observation`;
- `src.gtfsrt_stop_time_update_observation`;
- `src.gtfsrt_alert_observation`; and
- `src.gtfsrt_alert_entity_observation`.

The reserved structures do not approve permanent storage of a complete
Germany-wide protobuf feed on every collection cycle. The tested feed can
contain tens of thousands of TripUpdates and Alerts; blindly storing every
nationwide snapshot would create unnecessary volume outside the Cologne
scope. Stage 7/9 must approve the exact durable selection and matching policy.

No `VehiclePosition` table is approved by Stage 5. Full raw GTFS-RT payloads
may be short-term diagnostic evidence only, subject to a later operational
retention decision. Future protobuf persistence must preserve field presence
where defaults could otherwise hide whether a producer supplied a value, such
as absent `cause` versus explicitly supplied `UNKNOWN_CAUSE`.

## 10. Key and uniqueness strategy

### 10.1 Internal keys

Validated/internal entities use `bigint` surrogate keys generated by the
database as `GENERATED ALWAYS AS IDENTITY` when implemented. Random UUIDs are
not the default; a concrete distributed-generation need would be required to
justify them.

Source identifiers remain separate source/business columns. A source ID is not
a universal key, and equal strings in different source systems do not prove
entity identity.

### 10.2 Source uniqueness

Source tables preserve duplicate natural identifiers. Recommended source
identity is an internal row key plus unique feed/response-local row identity
such as `FeedSnapshotKey + SourceRowNumber`, or the corresponding immutable
response and ordinal lineage for realtime records. Natural IDs are indexed for
lookups but are not used as source-table primary keys when they could reject
valid source-quality evidence.

### 10.3 Validated uniqueness

`core` and `dw` may apply uniqueness only after source validation establishes
the relevant business grain. Every unique constraint must have a documented
grain in the [table/grain catalog](05-table-grain-catalog.md). Final physical
primary-key, foreign-key, and partition-key syntax is deferred to the stage
that implements each object.

## 11. PostgreSQL data-type contract

| Data | Approved convention |
|---|---|
| Internal surrogate key | `bigint GENERATED ALWAYS AS IDENTITY` when physically implemented |
| Source identifiers | `text`, including GTFS IDs, MDD refs, GTFS-RT IDs, and Situation identities |
| Observation/source timestamps | `timestamptz`; derived timestamps normalized to UTC |
| Service/operating dates | `date` |
| GTFS schedule time | Preserve exact source text in `src`; typed layers may additionally use integer seconds from service-day start, including values beyond 24:00 |
| Boolean source semantics | `boolean` with `NULL` retained as UNKNOWN / NOT PROVIDED |
| SHA-256 | Lowercase 64-character hexadecimal text or an equivalent deterministic readable representation |
| Bounded source-shaped extensions | `jsonb` only within the boundary in Section 15 |

The database must not force numeric-looking source IDs into integers. A
derived UTC timestamp or normalized schedule-second value must remain
traceable to the original source representation.

## 12. Timing semantics and history

The approved invariant is:

```text
TimetabledTime != EstimatedTime != RecordedAtTime != ObservedAt
```

The inequality expresses semantic separation, not a claim that values can
never be numerically equal.

- `TimetabledTime` is the source schedule value.
- `EstimatedTime` is the source prediction and remains historical evidence.
- `RecordedAtTime` is nullable, source-only actual/recorded-time evidence.
- `ObservedAt` is when the collector observed the source response.
- `RecordedAtTimeObservedAt` is derived lineage for when a source-provided
  recorded value was first seen.

`RecordedAtTime` is never synthesized from an estimate, the last estimate,
the timetable, collector time, or event disappearance. A later NULL or missing
event never erases a previously observed non-null source value.

MDD history is append-only; there is no single mutable current row per
journey. Later analytical layers may derive first estimate, latest estimate,
estimate revisions, first recorded time, latest source state, or Situation
evolution. Those are derived interpretations and do not belong in `src`.

Actual Delay remains **NOT APPROVED** until a validated actual-time source and
later matching/fact semantics support it.

## 13. Idempotency

Future loaders and collectors are idempotent at the ingestion boundary while
preserving legitimate history.

### Static

The same approved ZIP/content hash maps to one immutable parsed snapshot
identity. A repeated retrieval creates retrieval/audit evidence, not duplicate
parsed source-row history.

### Realtime

Re-running persistence for the same collector response/run must not duplicate
that exact response. A later legitimate observation of the same service event
is a new historical observation, even when its payload values happen to be
equal. Distinct observation timestamps are never collapsed merely because the
payload is identical. The exact physical idempotency key is implementation
work and must remain subordinate to this distinction.

## 14. Working, core, and warehouse contracts

### `wrk`

Prefer views, functions, and temporary/rebuildable transformations. A
materialized view or persisted working table requires measured performance
justification. `wrk` cannot be the only location of irreplaceable source
history.

### `core`

Reserve `core` for validated canonical identity and cross-source
reconciliation: canonical stop, canonical line, canonical service event, and
source-event match. Stage 9 owns implementation and empirical validation.
`MDD JourneyRef` must never be treated as GTFS-RT `trip_id`.

### `dw`

Stage 6 owns the first static warehouse implementation. Approved conceptual
grains are `DimDate`, `DimAgency`, `DimMode`, `DimStop`, `DimRoute`,
`DimService`, `BridgeServiceDate`, `FactScheduledTrip`, and
`FactScheduledStopEvent`. No realtime operational outcome fact is implemented
in Stage 5; the placeholder belongs to Stage 10.

Every static warehouse row that depends on GTFS retains `FeedSnapshotKey` and
relevant source IDs so it can be traced back to the feed snapshot.

## 15. JSONB boundary

`jsonb` is permitted only for:

- bounded provider-extension structures;
- rare or unmodeled source fragments where relational modelling would discard
  source evidence; and
- source-shaped auxiliary metadata.

Frequently queried or semantically important fields must be relational.
`jsonb` must not become a single-document replacement for the logical model,
and full realtime payloads are not permanently stored in `jsonb` by default.
API credentials and secrets must never be placed in `jsonb`.

## 16. Indexing strategy

Indexing is evidence-driven and is added only when it supports load
validation, matching, warehouse construction, or an approved operational
query path. Do not index every column.

Conceptual high-value static GTFS lookups include:

- `FeedSnapshotKey + route_id`;
- `FeedSnapshotKey + trip_id`;
- `FeedSnapshotKey + stop_id`;
- `FeedSnapshotKey + service_id`; and
- `FeedSnapshotKey + trip_id + stop_sequence`.

Conceptual MDD lookup dimensions include `ObservedAtUtc`,
`RequestedStopPointRef`, `JourneyRef`, `LineRef`, `OperatingDayRef`, Situation
identity, and `CollectorRunKey`.

Later GTFS-RT indexes may support observed time/feed snapshot, `trip_id`,
`stop_id`, and alert/entity identity after the durable selection policy is
approved. Exact indexes, selectivity, and maintenance cost are implementation
decisions.

## 17. Partition strategy

Do not partition every table automatically. Static snapshot lookup tables,
control tables, and dimensions do not require monthly observed-time
partitioning.

For large append-only realtime observation tables, the preferred production
direction is **monthly range partitioning by `ObservedAtUtc`** once those
tables are implemented. Final DDL must account for PostgreSQL uniqueness and
foreign-key requirements involving the partition key. Stage 5 does not invent
final partitioned primary-key syntax.

GTFS static rows are snapshot-versioned and do not require monthly
`ObservedAt` partitioning.

## 18. Audit, lineage, and sampling-policy architecture

The control layer must provide logical concepts equivalent to:

- `ctl.source_system`: one source definition;
- `ctl.ingestion_run`: one collector or load execution;
- `ctl.validation_result`: one validation result for one run/check;
- `ctl.sampling_policy`: one immutable/versioned sampling-policy definition;
  and
- `ctl.raw_artifact`: one externally retained raw-artifact metadata record.

Raw-artifact metadata may include source, path/URI, hash, size,
created/retrieved time, expiry/retention class, and associated run/snapshot.
It must not include credentials.

Every realtime observation must be attributable to `CollectorRunKey`,
`SamplingPolicyKey`/sampling era, `SourceSystem`, and `ObservedAt`. Changing
cadence creates a distinguishable policy/era; it must not rewrite history.

Every static warehouse object that depends on GTFS retains
`FeedSnapshotKey` and relevant source identifiers. Parser/version, retrieval,
validation, and artifact lineage remain available through the control and
source layers.

## 19. Security architecture

Approve least-privilege role separation conceptually:

- database owner / migration role;
- collector writer;
- warehouse transformation role;
- analytics read role; and
- Power BI read-only role.

Applications must not run as PostgreSQL superuser. Credentials remain outside
source control. Remote connections later require appropriate TLS and network
controls. Stage 5 creates no users, passwords, secrets, grants, or network
configuration.

## 20. Backup and recovery requirement

MDD and other realtime parsed observations may not be recoverable from the
provider later. Once production realtime collection begins, the durable
PostgreSQL source layer must have a tested backup/recovery strategy including:

- regular backups;
- a documented recovery test;
- retention appropriate to the observation history; and
- protection of append-only realtime evidence.

Static raw ZIP artifacts used for durable history must remain reproducible in
approved artifact storage, with hash/path metadata in `ctl`.

Stage 5 does not implement backup tooling or select an arbitrary raw-payload
retention duration. Operational retention must later balance volume, privacy,
cost, and forensic/reprocessing need.

## 21. Legacy migration policy and deferred work

The legacy SQL Server history must not be deleted or mutated. Any later import
must:

- preserve original source timestamps and IDs;
- preserve NULL semantics;
- record migration batch and source provenance;
- never rename estimates as actuals; and
- never silently merge legacy and PostgreSQL-native observations.

The redesign becomes authoritative after an approved cutover; old rows are not
made authoritative by rewriting them into the new model.

The following remain deferred:

- Stage 6 physical static warehouse objects and reconciliation counts;
- Stage 7/8 realtime collector implementation, raw-capture exception, and
  operational persistence selection;
- Stage 9 canonical identity and cross-source matching implementation;
- Stage 10 operational fact grain and outcome consolidation;
- Stage 11 semantic reporting objects and Power BI model;
- final physical constraints, partition DDL, query plans, and retention
  operations; and
- empirical validation of actual-time coverage, GTFS-RT identity bridges, and
  source-volume/cost assumptions.

These are deferred implementation decisions, not reasons to alter the Stage 5
architecture or to begin the next stage inside this task.

## 22. Stage 6 handoff

Stage 6 begins the incremental static warehouse implementation. Its first
work must:

1. use the approved PostgreSQL 18 direction without hardcoding a future minor
   image tag;
2. preserve complete VRS source snapshots in `src` and apply Cologne scope
   only in validated/analytical layers;
3. implement the approved dimensions, `BridgeServiceDate`, and scheduled
   facts at the catalogued grains;
4. carry `FeedSnapshotKey` and relevant source IDs into every GTFS-dependent
   warehouse object;
5. reconcile source-to-warehouse counts and validation outcomes through `ctl`;
6. keep `wrk` rebuildable and analytics non-authoritative; and
7. defer realtime outcome facts, collectors, matching, and Power BI changes to
   their owning stages.

## 23. Acceptance criteria

| # | Criterion | Result |
|---:|---|---|
| 1 | PostgreSQL is confirmed as the future authoritative platform | PASS |
| 2 | Supported production major-version direction is recorded | PASS — PostgreSQL 18; latest supported 18 minor at implementation time |
| 3 | Clean redesign vs. legacy lift-and-shift is explicitly decided | PASS — clean redesign |
| 4 | All logical schemas/layers have documented responsibilities | PASS |
| 5 | Source-faithful vs. derived data boundaries are explicit | PASS |
| 6 | Every planned major logical entity has an explicit grain | PASS — 42 named contracts in the catalog |
| 7 | Source snapshot/version strategy is explicit | PASS |
| 8 | Static GTFS history/reproducibility strategy is explicit | PASS |
| 9 | MDD append-only observation architecture preserves timing and Situation history | PASS |
| 10 | `RecordedAtTime`, `EstimatedTime`, `TimetabledTime`, and `ObservedAt` remain separate | PASS |
| 11 | GTFS-RT persistence boundary prevents accidental nationwide snapshot explosion | PASS |
| 12 | Key and uniqueness strategies preserve source defects | PASS |
| 13 | Data-type conventions are explicit | PASS |
| 14 | Idempotency distinguishes duplicate execution from legitimate repeated observation | PASS |
| 15 | JSONB boundary is explicit | PASS |
| 16 | Index strategy is documented | PASS |
| 17 | Realtime partitioning direction is documented without premature DDL | PASS |
| 18 | Audit/run/sampling-policy lineage is defined | PASS |
| 19 | Security/least-privilege role model is defined | PASS |
| 20 | Backup/recovery requirement for irrecoverable realtime evidence is defined | PASS |
| 21 | Legacy migration/import policy is defined | PASS |
| 22 | Stage 6 ownership and handoff are explicit | PASS |
| 23 | No database/schema/table/migration was created | PASS — documentation only |
| 24 | No collector/scheduler/Power BI change occurred | PASS |
| 25 | `git diff --check` passes | PASS — verified at closeout |

## 24. Final decision

**APPROVED ARCHITECTURE DECISION:** Stage 5 is **COMPLETE — APPROVED
ARCHITECTURE**. PostgreSQL 18 is the future authoritative platform direction,
implemented as a clean redesign with durable source-faithful evidence and
derived analytical layers. The [completion decision](05-database-architecture-completion-decision.md)
records the classifications, carried-forward risks, and final closeout.

**STAGE BOUNDARY:** No PostgreSQL database, schema, table, migration, SQL
execution, collector, scheduler, matching implementation, warehouse
implementation, or Power BI change occurred. Stage 6 is **NOT STARTED / NEXT**.
