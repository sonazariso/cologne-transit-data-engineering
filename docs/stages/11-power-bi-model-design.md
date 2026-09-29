# Stage 11 — Power BI Model Design

**Status:** `COMPLETE — APPROVED DESIGN; PBIP/DESKTOP IMPLEMENTATION DEFERRED`  
**Date:** 2026-09-29  
**Decision:** [D-042](../DECISION-LOG.md#d-042--stage-11-power-bi-model-design)

This document is the approved semantic-model design and implementation
contract for Stage 11. It does not create a Power BI project, connect to a
database, load data, execute SQL, or claim that the Stage 10 operational fact
has been physically populated.

## 1. Purpose and scope

The model supports a non-technical operational and management audience asking:

> Is public transport within Cologne operating well during the selected period,
> based only on evidence the current data supports?

The semantic model must make the following distinctions visible rather than
hide them in report logic:

- scheduled Cologne network context versus observed operational evidence;
- operational service-event coverage versus source-observation count;
- estimated timing versus Actual Physical Delay;
- HIGH-only operational outcomes versus all Stage 9 matching decisions;
- bay-change evidence, bay-unknown evidence, and bay-unchanged evidence;
- Situation association versus Situation causality; and
- monitored sample coverage versus the entire Cologne network.

Stage 11 defines the model contract only. PostgreSQL/runtime work remains
deferred, and the current Stage 10 status remains:
`NOT COMPLETE — IMPLEMENTATION CORRECTED; SOURCE/DATABASE VALIDATION BLOCKED`.

### Authoritative inputs

The design reconciles the following current contracts:

- [Stage 0 — Project Definition](00-project-definition.md)
- [Semantic and Data Guardrails](../GUARDRAILS.md)
- [Stage 5 — Database Architecture](05-database-architecture.md)
- [Stage 5 — Table/Grain Catalog](05-table-grain-catalog.md)
- [Stage 6 — Static Warehouse & Analytics Design](06-static-warehouse-analytics-design.md)
- [Stage 9 — Matching & Data Quality](09-matching-data-quality.md)
- [Stage 9 SQL contract](../../sql/stage_09_matching_quality.sql)
- [Stage 10 — Operational Fact](10-operational-fact.md)
- [Stage 10 fact contract](../../sql/04-warehouse/05-create-operational-stop-outcome.sql)
- [Project Status](../PROJECT-STATUS.md)
- [Restart Roadmap](../ROADMAP.md)
- [Decision Log](../DECISION-LOG.md)

Legacy Power BI assets are historical evidence only. They are not inputs to
this model design.

## 2. Semantic subject areas

### 2.1 Static Schedule / Network Context

This subject area uses the approved Stage 6 static contracts. It describes
source schedule coverage, routes, modes, stop positions, parent stations,
scheduled trip patterns, active service dates, and feed lineage. It never
proves that a service operated, arrived, departed, was punctual, was
cancelled, or used a physical platform.

The subject area contains the governed Date dimension, static VRS dimensions,
pattern-level scheduled facts, the `BridgeServiceDate` active-date bridge, and
the future date-expanded Daily Schedule output described below.

### 2.2 Operational Evidence

This subject area is based on `dw.fact_operational_service_event` from Stage
10. Its grain is one consolidated outcome for one `(service_date,
canonical_service_event_key)` and it admits only Stage 9 `HIGH` matches for the
explicitly selected matching run/version.

It exposes observed service-event counts, latest source estimates, estimated
delay evidence, bay evidence, Situation-link evidence, source-observation
counts, and the retained Stage 9 lineage needed for technical review. It does
not expose an Actual Delay or infer cancellation, physical arrival, physical
platform use, or causal Situation attribution.

### 2.3 Matching / Data Quality

This is a separate subject area based on Stage 9 matching decisions and data-
quality issue evidence. It must represent the complete selected matching
population:

`HIGH`, `MEDIUM`, `LOW`, `UNMATCHED`, `data_quality_classification`,
`match_method`, mapping version, candidate/lineage evidence, and source/mapping
limitations.

This population is not the Stage 10 HIGH-only fact. Matching coverage uses
matching-decision denominators and must never silently use the number of
operational fact rows as the denominator.

## 3. Semantic table catalog and grains

The names below are semantic names. A future implementation may use governed
analytics projections over the approved warehouse/core objects, but it must
preserve the stated grains and lineage.

| Subject area | Semantic table | Approved source/role | Exact grain | Important boundary |
|---|---|---|---|---|
| Static | `Dim Date` | `dw.dim_date` | One calendar date | One governed calendar; no Auto Date/Time replacement. |
| Static | `Dim Feed Snapshot` | Approved static feed metadata | One immutable approved static feed content snapshot/version | Content hash and feed validity are lineage, not operational coverage. |
| Static | `Dim Agency` | `dw.dim_agency` | One validated analytical agency identity/version for one feed snapshot | Agency identity is not geographical scope. |
| Static | `Dim Mode` | `dw.dim_mode` | One approved analytical mode | Stage 4 status remains distinct: `CORE_KPI`, `SEPARATE_ANALYTICAL`, `OUTSIDE_CORE`, or `UNCLASSIFIED`. |
| Static | `Dim Route` | `dw.dim_route` | One validated VRS static route identity for one feed snapshot | A route label does not make every route stop part of Cologne KPI scope. |
| Static | `Dim Stop Position` | VRS `dw.dim_stop` projection | One validated physical VRS stop position (`location_type=0`) for one feed snapshot | The actual stop ID referenced by `stop_times.txt` remains authoritative. |
| Static | `Dim Parent Station` | VRS `dw.dim_stop` projection | One validated parent station (`location_type=1`) for one feed snapshot | Parent station does not replace or merge a child/physical stop identity. |
| Static | `Dim Service` | `dw.dim_service` | One static GTFS service/calendar identity for one feed snapshot | It is a schedule identity, not evidence of service delivery. |
| Static | `Dim Scheduled Trip` | Dimension-like semantic projection of `dw.fact_scheduled_trip` | One validated Cologne-serving scheduled trip pattern for one feed snapshot | No active date is in this grain; it is not an operational trip outcome. |
| Static | `Bridge Service Date` | `dw.bridge_service_date` | One active `FeedSnapshot + Service + Date` pair | This owns active service/date expansion. It does not multiply the pattern facts. |
| Static | `Fact Daily Scheduled Trip` | Future derived/query output from the Stage 6 bridge and pattern facts | One `FeedSnapshot + Date + ScheduledTrip pattern` row | Date-expanded output only; it does not change `FactScheduledTrip` or `FactScheduledStopEvent` grain. It may aggregate to Stage 6's `Date + Route` profile. |
| Static | `Fact Scheduled Stop Event` | `dw.fact_scheduled_stop_event` | One validated Cologne scheduled stop event in one trip pattern for one feed snapshot | Pattern-level only; no active `DateKey`, realtime field, or actual-delay field. |
| Operational | `Dim Operational Stop` | Validated `core.canonical_stop` projection | One validated canonical stop identity/version | Canonical identity is not a VRS stop ID equality assumption. |
| Operational | `Dim Operational Line` | Validated `core.canonical_line` projection | One validated canonical line identity/version | Canonical line identity is not a VRS route ID equality assumption. |
| Operational | `Dim Operational Mode` | Validated operational mode projection, when available | One validated operational mode assignment/version | It is separate from static `Dim Mode`; unresolved operational mode remains explicit. |
| Operational | `Fact Operational Service Event` | `dw.fact_operational_service_event` | One consolidated outcome per `(service_date, canonical_service_event_key)` | HIGH Stage 9 associations only; latest NULL timing remains NULL; matching run/version is lineage, not an extra business grain. |
| Data quality | `Dim Matching Run` | `ctl.matching_run` | One Stage 9 matching execution/version | Measures must select a run/version explicitly; no implicit latest-run union. |
| Data quality | `Dim Sampling Era` | Sampling-policy/era lineage | One approved sampling policy/era | The current relationship is only to the `Fact Matching Decision` population through validated `SamplingEraKey` lineage. Data Quality classifications carried by those matching decisions inherit that context; the independent `Fact Data Quality Issue` is not Sampling-Era-filtered in the base model. It is not an operational KPI slicer until a validated operational-event projection exists. Historical eras remain distinguishable and are not silently pooled. |
| Data quality | `Fact Matching Decision` | `core.source_event_match` | One matching decision for one source observation within one selected matching run/version | Contains all `HIGH`, `MEDIUM`, `LOW`, and `UNMATCHED` decisions plus method, classification, candidates, and mapping limitations. |
| Data quality | `Fact Data Quality Issue` | `ctl.data_quality_issue` | One classified data-quality issue for one run/entity/evidence occurrence | Issue evidence is separate from matching-result counts. |
| Parameter | `Param Estimated Delay Threshold` | Disconnected analytical parameter | One integer threshold option in the governed `0..15` range | Not source truth, provider policy, or an official punctuality threshold. |

The model does not approve a physical Situation dimension or a causal bridge in
Stage 11. A later technical evidence projection may expose one observed
Situation-association row per source observation/link, but it must be joined
through validated source-observation lineage and must retain association-only
language.

## 4. Relationship and filter-direction contract

All approved relationships are one-to-many and single-direction. The static
subject area uses a small constellation: the pattern-level scheduled-trip
dimension and the scheduled facts carry their own validated foreign keys. This
avoids dimension-to-dimension snowflakes and keeps every active path
dimension/bridge → fact. The following matrix is the complete base
relationship contract. A future implementation must not add automatic
relationships.

| From (one side) | To (many side) | Cardinality / direction | Purpose and constraint |
|---|---|---|---|
| `Dim Date[DateValue]` | `Bridge Service Date[DateValue]` | 1 → many, single | Active service-calendar date filtering. |
| `Dim Date[DateValue]` | `Fact Daily Scheduled Trip[ServiceDate]` | 1 → many, single | Date-expanded schedule filtering. |
| `Dim Date[DateValue]` | `Fact Operational Service Event[ServiceDate]` | 1 → many, single | `service_date` is the operational business date. |
| `Dim Date[DateValue]` | `Fact Matching Decision[ServiceDate]` | 1 → many, single | Date filtering for decisions with a valid service date; NULL service dates remain visible as unmatched/quality evidence outside the date slice. |
| `Dim Feed Snapshot[FeedSnapshotKey]` | `Bridge Service Date[FeedSnapshotKey]` | 1 → many, single | Static snapshot selection. |
| `Dim Feed Snapshot[FeedSnapshotKey]` | `Fact Daily Scheduled Trip[FeedSnapshotKey]` | 1 → many, single | Snapshot lineage for date-expanded schedule. |
| `Dim Feed Snapshot[FeedSnapshotKey]` | `Fact Scheduled Stop Event[FeedSnapshotKey]` | 1 → many, single | Scheduled stop-event pattern snapshot lineage. |
| `Dim Agency[AgencyKey]` | `Fact Scheduled Stop Event[AgencyKey]` | 1 → many, single | Agency slicing at scheduled stop-event grain. |
| `Dim Mode[ModeKey]` | `Fact Scheduled Stop Event[ModeKey]` | 1 → many, single | Static mode slicing at scheduled stop-event grain. |
| `Dim Route[RouteKey]` | `Fact Scheduled Stop Event[RouteKey]` | 1 → many, single | Static route slicing at scheduled stop-event grain. |
| `Dim Route[RouteKey]` | `Fact Daily Scheduled Trip[RouteKey]` | 1 → many, single | Route slicing of date-expanded schedule. |
| `Dim Service[ServiceKey]` | `Bridge Service Date[ServiceKey]` | 1 → many, single | Service-calendar activation. |
| `Dim Service[ServiceKey]` | `Fact Scheduled Stop Event[ServiceKey]` | 1 → many, single | Static service identity for stop-event patterns. |
| `Dim Service[ServiceKey]` | `Fact Daily Scheduled Trip[ServiceKey]` | 1 → many, single | Service identity in the date-expanded output. |
| `Dim Mode[ModeKey]` | `Fact Daily Scheduled Trip[ModeKey]` | 1 → many, single | Static mode slicing of date-expanded schedule. |
| `Dim Agency[AgencyKey]` | `Fact Daily Scheduled Trip[AgencyKey]` | 1 → many, single | Agency slicing of date-expanded schedule. |
| `Dim Scheduled Trip[ScheduledTripKey]` | `Fact Scheduled Stop Event[ScheduledTripKey]` | 1 → many, single | Trip-pattern context for stop-event details; the two facts are not joined directly. |
| `Dim Scheduled Trip[ScheduledTripKey]` | `Fact Daily Scheduled Trip[ScheduledTripKey]` | 1 → many, single | Date-expanded trip-pattern context. |
| `Dim Stop Position[StopPositionKey]` | `Fact Scheduled Stop Event[StopPositionKey]` | 1 → many, single | Physical stop-position slicing. |
| `Dim Parent Station[ParentStationKey]` | `Fact Scheduled Stop Event[ParentStationKey]` | 1 → many, single | Parent-station reporting without overriding the actual stop position. |
| `Dim Operational Stop[OperationalStopKey]` | `Fact Operational Service Event[OperationalStopKey]` | 1 → many, single | Validated canonical operational stop slicing. |
| `Dim Operational Line[OperationalLineKey]` | `Fact Operational Service Event[OperationalLineKey]` | 1 → many, single | Validated canonical operational line slicing. |
| `Dim Operational Mode[OperationalModeKey]` | `Fact Operational Service Event[OperationalModeKey]` | 1 → many, single, when projection exists | Operational mode is used only when a validated canonical projection supplies it. |
| `Dim Matching Run[MatchingRunKey]` | `Fact Matching Decision[MatchingRunKey]` | 1 → many, single | Complete matching-decision population by explicit run/version. |
| `Dim Matching Run[MatchingRunKey]` | `Fact Data Quality Issue[MatchingRunKey]` | 1 → many, single | Data-quality issue lineage. |
| `Dim Matching Run[MatchingRunKey]` | `Fact Operational Service Event[MatchingRunKey]` | 1 → many, single | Technical lineage filter only; it does not change HIGH-only operational eligibility. |
| `Dim Sampling Era[SamplingEraKey]` | `Fact Matching Decision[SamplingEraKey]` | 1 → many, single | Sampling-era coverage analysis. |

The matrix intentionally has no relationship from `Dim Sampling Era` to either
`Fact Data Quality Issue` or `Fact Operational Service Event`. `Dim Sampling Era`
currently filters the `Fact Matching Decision` population only through the
explicit relationship above. Data Quality classifications carried by those
matching decisions inherit that context. The separate `Fact Data Quality Issue`
is not Sampling-Era-filtered in the base model, so `Data Quality Issue Count`
must not be described or implemented as Sampling-Era-filterable under the
current base model. It must not be presented as an operational KPI slicer and
has no approved relationship to `Fact Operational Service Event`.

A future relationship to the independent `Fact Data Quality Issue` or to
operational evidence may be considered only after a future stage establishes
and validates explicit `SamplingEraKey` lineage at that fact's grain. The
projection or bridge must validate its grain, key uniqueness, historical-era
semantics, and lineage before a relationship is approved. If one operational
service event contains source observations crossing an era boundary, the
implementation must not arbitrarily assign that event to one era.

The approved semantic Scheduled Trip relationships are also deliberately
distinct from physical fact joins. `Dim Scheduled Trip` is a dimension-like
semantic projection of the Stage 6 physical `dw.fact_scheduled_trip` object.
Its approved one-to-many, single-direction relationships to `Fact Scheduled
Stop Event` and `Fact Daily Scheduled Trip` remain in force; they do not join
the two physical Stage 6 facts directly.

The following relationships are explicitly prohibited:

- a direct physical fact-to-fact relationship between the Stage 6 physical
  `dw.fact_scheduled_trip` object and `dw.fact_scheduled_stop_event`;
- `Bridge Service Date` directly to the pattern-level scheduled stop-event fact;
- `Dim Operational Stop[canonical_stop_id]` directly to a VRS `stop_id`;
- `Dim Operational Line[canonical_line_id]` directly to a VRS `route_id`;
- matching decisions directly to the operational fact;
- any fact-to-fact relationship, bidirectional relationship, or accidental
  many-to-many relationship; and
- any relationship discovered solely because text IDs look alike.

Therefore, the semantic `Dim Scheduled Trip -> Fact Scheduled Stop Event` and
`Dim Scheduled Trip -> Fact Daily Scheduled Trip` paths are approved, while no
physical fact-to-fact or bidirectional relationship is introduced.

The `Dim Feed Snapshot` links terminate at the bridge and static/date-expanded
facts; the feed key is retained as lineage on the trip-pattern dimension but is
not related to that dimension, which prevents a second path to the stop-event
fact. Static dimensions remain independent slicers, and parent stations are
not related to stop positions. All relevant fact tables carry their own
validated static keys so route, mode, agency, service, and trip filters do not
need bidirectional propagation.

## 5. Governed Date strategy

`Dim Date` is the only authoritative report date dimension. It must contain at
least `DateKey`, `DateValue`, year, quarter, month, month number, ISO week/day
attributes where approved, day-of-week, day-of-week number, and weekend flag.

- Static active-date filtering uses `BridgeServiceDate`; it does not add a
  `DateKey` to the pattern-level `Fact Scheduled Stop Event` grain.
- A date-by-trip schedule view uses `Fact Daily Scheduled Trip`, a future
  derived output. It is the only date-expanded schedule table in this
  contract.
- Operational filtering uses `Fact Operational Service Event[ServiceDate]`.
  `service_date` is the operational business date, not `ObservedAt`,
  `CapturedAt`, or a collector date.
- Matching decisions use a validated service date when one exists. The active
  `Dim Date` relationship therefore filters only decisions with a valid
  `ServiceDate`; a missing service date remains a data-quality outcome and is
  not derived from `ObservedAt`.
- A Date-filtered matching population, including Date-filtered HIGH counts and
  coverage, contains only decisions with a valid service date. Decisions
  without a service date remain explicit Data Quality evidence outside that
  date slice and must not silently disappear from the interpretation of
  overall matching quality.
- `Fact Data Quality Issue` has no active Date relationship in the base model;
  issue timestamps and feed-validity dates have multiple technical meanings
  and require an explicitly named inactive/technical measure if exposed later.
- A source-observation date may be retained for technical analysis as an
  inactive alternate relationship/explicit measure only. It must not create a
  second active path in the executive model.
- Power BI Auto Date/Time must be disabled. Generated `LocalDateTable_*`
  tables are not authoritative and must not be used.

## 6. Static VRS versus canonical operational identity

The static subject area uses validated VRS/static identities:

- VRS `stop_id` and `location_type` for stop positions and parent stations;
- VRS `route_id` for static route context;
- Stage 4 mode classification and Cologne stop-event scope; and
- feed-snapshot lineage for every static identity.

The operational subject area uses Stage 9 canonical identities:

- `core.canonical_stop` for `Dim Operational Stop`;
- `core.canonical_line` for `Dim Operational Line`;
- validated mapping version/source namespace lineage; and
- the HIGH-only Stage 10 operational fact.

The design does not join canonical IDs to VRS IDs or route IDs by string
equality. A future cross-source relationship may be introduced only through an
approved Stage 9 source-identity mapping/projection with a documented key,
effective version, and uniqueness validation. Until then, operational lines
and stops remain separate from static routes and stop positions. Displaying a
static label alongside an operational label requires a validated projection;
it is not a reason to create a relationship.

## 7. Explicit DAX measure contract

The following are future explicit measures. Names and semantics are approved;
no TMDL or generated Power BI metadata is created in Stage 11. Table/column
names in the examples are semantic names from Section 3.

### 7.1 Static schedule context

These measures are labelled and interpreted as scheduled/static coverage.

| Measure | DAX logic | Source grain | Denominator / NULL behavior | Interpretation and prohibition |
|---|---|---|---|---|
| `Scheduled Trips` | `DISTINCTCOUNT('Fact Scheduled Stop Event'[ScheduledTripKey])` | Scheduled stop-event pattern rows, distinct trip key | Pattern-level distinct trip keys; blank when no qualifying rows. | Scheduled trip patterns represented by the selected static scope; not operated trips. |
| `Scheduled Stop Events` | `COUNTROWS('Fact Scheduled Stop Event')` | One scheduled stop-event pattern row | Pattern-level stop-event rows; no date multiplication. | Scheduled stop-event coverage; not observed service events. |
| `Routes` | `DISTINCTCOUNT('Fact Scheduled Stop Event'[RouteKey])` | Scheduled stop-event pattern rows, distinct route key | Distinct static route keys represented by scheduled events. | Scheduled route context; not operational route performance. |
| `Stops / Stop Positions` | `DISTINCTCOUNT('Fact Scheduled Stop Event'[StopPositionKey])` | Scheduled stop-event pattern rows, distinct position key | Distinct physical stop-position keys represented by scheduled events. | Scheduled stop-position coverage; not observed stop operation. |
| `Parent Stations` | `DISTINCTCOUNT('Fact Scheduled Stop Event'[ParentStationKey])` | Scheduled stop-event pattern rows, distinct non-blank parent key | Distinct non-blank parent-station keys represented by scheduled events. | Scheduled parent-station context; does not merge child stop identities. |
| `Active Dates` | `DISTINCTCOUNT('Bridge Service Date'[DateKey])` | One active FeedSnapshot + Service + Date bridge row | Distinct active service dates in the selected feed/service scope. | Service-calendar coverage; not observed operating dates. |
| `Daily Scheduled Trips` | `DISTINCTCOUNT('Fact Daily Scheduled Trip'[ScheduledTripKey])` | One date-expanded FeedSnapshot + Date + ScheduledTrip row | Date-expanded distinct trip patterns; blank when no rows. | Optional date-specific schedule context; not operational delivery. |

The static measures do not use the operational fact and do not imply actual
service. A date slicer does not silently turn a pattern-level stop-event count
into a daily count.

### 7.2 Operational coverage

```DAX
Operational Service Events :=
    COUNTROWS ( 'Fact Operational Service Event' )

Events With Latest Estimate :=
    CALCULATE (
        [Operational Service Events],
        KEEPFILTERS (
            FILTER (
                'Fact Operational Service Event',
                NOT ISBLANK ( 'Fact Operational Service Event'[LatestEstimatedTimeUtc] )
            )
        )
    )

Events Without Latest Estimate :=
    CALCULATE (
        [Operational Service Events],
        KEEPFILTERS (
            FILTER (
                'Fact Operational Service Event',
                ISBLANK ( 'Fact Operational Service Event'[LatestEstimatedTimeUtc] )
            )
        )
    )

Estimated Timing Coverage % :=
    DIVIDE ( [Events With Latest Estimate], [Operational Service Events] )

Source Observation Count :=
    SUM ( 'Fact Operational Service Event'[SourceObservationCount] )
```

| Measure | Source grain | Denominator / NULL behavior | Interpretation and prohibition |
|---|---|---|---|
| `Operational Service Events` | One Stage 10 row per `(service_date, canonical_service_event_key)` | No denominator; empty filtered fact population returns BLANK. | HIGH-only consolidated operational-event count; not raw observations and not network-wide service count. |
| `Events With Latest Estimate` | Same operational fact grain | No denominator; counts only non-NULL `LatestEstimatedTimeUtc`. | Source-estimate availability; not Actual Time availability. |
| `Events Without Latest Estimate` | Same operational fact grain | No denominator; counts NULL `LatestEstimatedTimeUtc`; NULL is not zero. | Estimate unavailable in the latest observation; not cancellation or on-time evidence. |
| `Estimated Timing Coverage %` | Same operational fact grain | `Events With Latest Estimate / Operational Service Events`; `DIVIDE` returns BLANK when the denominator is unavailable. | HIGH-only estimate-field coverage; not Actual Delay coverage. |
| `Source Observation Count` | Summed `SourceObservationCount` over operational fact rows | No denominator; blank when no operational rows. | Consolidated source-observation volume; never a substitute for service-event count. |

`Estimated Timing Coverage %` has the explicit denominator of HIGH-only
operational service events. It describes presence of the latest source
estimate, not Actual Time coverage. `Source Observation Count` is the sum of
source observations consolidated into those operational fact rows; it is not a
service-event count and must not replace `[Operational Service Events]`.

### 7.3 Estimated timing

```DAX
Average Latest Estimated Delay (Minutes) :=
    AVERAGEX (
        FILTER (
            'Fact Operational Service Event',
            NOT ISBLANK ( 'Fact Operational Service Event'[LatestEstimatedDelayMinutes] )
        ),
        'Fact Operational Service Event'[LatestEstimatedDelayMinutes]
    )

Median Latest Estimated Delay (Minutes) :=
    MEDIANX (
        FILTER (
            'Fact Operational Service Event',
            NOT ISBLANK ( 'Fact Operational Service Event'[LatestEstimatedDelayMinutes] )
        ),
        'Fact Operational Service Event'[LatestEstimatedDelayMinutes]
    )

Estimated Delayed Events :=
    VAR Threshold = [Selected Estimated Delay Threshold (Minutes)]
    RETURN
        CALCULATE (
            [Operational Service Events],
            KEEPFILTERS (
                FILTER (
                    'Fact Operational Service Event',
                    NOT ISBLANK ( 'Fact Operational Service Event'[LatestEstimatedDelayMinutes] )
                        && 'Fact Operational Service Event'[LatestEstimatedDelayMinutes] > Threshold
                )
            )
        )

Estimated Early Events :=
    CALCULATE (
        [Operational Service Events],
        KEEPFILTERS (
            FILTER (
                'Fact Operational Service Event',
                NOT ISBLANK ( 'Fact Operational Service Event'[LatestEstimatedDelayMinutes] )
                    && 'Fact Operational Service Event'[LatestEstimatedDelayMinutes] < 0
            )
        )
    )

Estimated Within Selected Threshold Events :=
    VAR Threshold = [Selected Estimated Delay Threshold (Minutes)]
    RETURN
        CALCULATE (
            [Operational Service Events],
            KEEPFILTERS (
                FILTER (
                    'Fact Operational Service Event',
                    NOT ISBLANK ( 'Fact Operational Service Event'[LatestEstimatedDelayMinutes] )
                        && 'Fact Operational Service Event'[LatestEstimatedDelayMinutes] >= 0
                        && 'Fact Operational Service Event'[LatestEstimatedDelayMinutes] <= Threshold
                )
            )
        )
```

| Measure | Source grain | Denominator / NULL behavior | Interpretation and prohibition |
|---|---|---|---|
| `Average Latest Estimated Delay (Minutes)` | One latest estimate per operational fact row | Non-NULL latest estimated-delay rows; BLANK when none. | Average estimated delay evidence only; no Actual Delay inference. |
| `Median Latest Estimated Delay (Minutes)` | One latest estimate per operational fact row | Non-NULL latest estimated-delay rows; BLANK when none. | Median estimated delay evidence only; no Actual Delay inference. |
| `Estimated Delayed Events` | One latest estimated-delay value per operational fact row | Count of non-NULL delay values greater than the selected threshold; BLANK when none. | Estimated delay above the selected analytical threshold; not official lateness. |
| `Estimated Early Events` | One latest estimated-delay value per operational fact row | Count of non-NULL delay values below zero; BLANK when none. | Estimated early evidence; not a confirmed actual departure/arrival state. |
| `Estimated Within Selected Threshold Events` | One latest estimated-delay value per operational fact row | Count of non-NULL values from zero through the selected threshold; BLANK when none. | Selected estimated-delay classification; not On Time or Punctual. |

The average and median denominator is the population with a non-NULL latest
estimated delay. The three classification measures exclude NULL delay values.
`Estimated Early Events` is always below zero; `Estimated Within Selected
Threshold Events` is zero through the selected non-negative threshold; and
`Estimated Delayed Events` is above that threshold. None of these measures
means Actual Delay, Actual Arrival Delay, Actual Punctuality, Physical
Arrival, or Confirmed Actual On-Time.

### 7.4 Bay evidence

```DAX
Bay Changed Events :=
    CALCULATE (
        [Operational Service Events],
        'Fact Operational Service Event'[BayEvidence] = "Changed"
    )

Bay Unchanged Evidence Events :=
    CALCULATE (
        [Operational Service Events],
        'Fact Operational Service Event'[BayEvidence] = "Unchanged"
    )

Bay Unknown Events :=
    CALCULATE (
        [Operational Service Events],
        'Fact Operational Service Event'[BayEvidence] = "Unknown"
    )

Bay Comparable Events :=
    COUNTROWS (
        FILTER (
            'Fact Operational Service Event',
            'Fact Operational Service Event'[ComparableBayObservationCount] > 0
        )
    )

Bay Change Evidence % :=
    DIVIDE ( [Bay Changed Events], [Bay Comparable Events] )
```

| Measure | Source grain | Denominator / NULL behavior | Interpretation and prohibition |
|---|---|---|---|
| `Bay Changed Events` | One consolidated bay-evidence value per operational fact row | Count where `BayEvidence = Changed`; BLANK when none. | Evidence that a comparable planned/estimated pair differed; not physical platform change. |
| `Bay Unchanged Evidence Events` | One consolidated bay-evidence value per operational fact row | Count where `BayEvidence = Unchanged`; BLANK when none. | Comparable pairs agreed; not proof of unchanged physical use. |
| `Bay Unknown Events` | One consolidated bay-evidence value per operational fact row | Count where `BayEvidence = Unknown`; BLANK when none. | No comparable pair; not unchanged. |
| `Bay Comparable Events` | One consolidated count per operational fact row | Count where `ComparableBayObservationCount > 0`; BLANK when none. | Denominator population for bay-change evidence. |
| `Bay Change Evidence %` | One consolidated bay-evidence value per operational fact row | `Bay Changed Events / Bay Comparable Events`; `DIVIDE` returns BLANK when no comparable events. | Comparable-pair change evidence only; not physical platform usage. |

`Bay Change Evidence %` uses comparable events as its explicit denominator.
`Changed` means at least one comparable `PlannedBay`/`EstimatedBay` pair
differed; `Unchanged` means comparable pairs existed and all agreed; `Unknown`
means no comparable pair existed. None proves the final physical bay used.

### 7.5 Situation evidence

```DAX
Situation-Associated Events :=
    CALCULATE (
        [Operational Service Events],
        FILTER (
            'Fact Operational Service Event',
            'Fact Operational Service Event'[SituationLinkCount] > 0
        )
    )

Events Without Situation Evidence :=
    CALCULATE (
        [Operational Service Events],
        'Fact Operational Service Event'[SituationLinkCount] = 0
    )

Situation Association Rate % :=
    DIVIDE ( [Situation-Associated Events], [Operational Service Events] )
```

| Measure | Source grain | Denominator / NULL behavior | Interpretation and prohibition |
|---|---|---|---|
| `Situation-Associated Events` | One consolidated Situation-link count per operational fact row | Count where `SituationLinkCount > 0`; BLANK when none. | At least one persisted association row; not a distinct Situation or cause. |
| `Events Without Situation Evidence` | One consolidated Situation-link count per operational fact row | Count where persisted link count equals zero; BLANK when none. | No associated link in the selected evidence; not proof that no disruption existed. |
| `Situation Association Rate %` | One consolidated operational fact row | `Situation-Associated Events / Operational Service Events`; `DIVIDE` returns BLANK when no operational events. | Association rate only; never Situation-Caused. |

The denominator for `Situation Association Rate %` is all HIGH-only
operational service events in the current filter context. The count is of
persisted observation-to-Situation association rows summarized into the fact,
not a distinct Situation count. The approved wording is
**Situation-Associated**; the model must not say Situation-Caused.

### 7.6 Matching and Data Quality

```DAX
Matching Decisions :=
    COUNTROWS ( 'Fact Matching Decision' )

HIGH Matches :=
    CALCULATE ( [Matching Decisions], 'Fact Matching Decision'[MatchConfidence] = "HIGH" )

MEDIUM Matches :=
    CALCULATE ( [Matching Decisions], 'Fact Matching Decision'[MatchConfidence] = "MEDIUM" )

LOW Matches :=
    CALCULATE ( [Matching Decisions], 'Fact Matching Decision'[MatchConfidence] = "LOW" )

Unmatched Decisions :=
    CALCULATE ( [Matching Decisions], 'Fact Matching Decision'[MatchConfidence] = "UNMATCHED" )

Matching Decisions Without Service Date :=
    CALCULATE (
        [Matching Decisions],
        REMOVEFILTERS ( 'Dim Date' ),
        KEEPFILTERS (
            FILTER (
                'Fact Matching Decision',
                ISBLANK ( 'Fact Matching Decision'[ServiceDate] )
            )
        )
    )

HIGH Match Coverage % :=
    DIVIDE ( [HIGH Matches], [Matching Decisions] )

Service-Date-Scoped HIGH Match Coverage % :=
    DIVIDE (
        CALCULATE (
            [HIGH Matches],
            KEEPFILTERS (
                FILTER (
                    'Fact Matching Decision',
                    NOT ISBLANK ( 'Fact Matching Decision'[ServiceDate] )
                )
            )
        ),
        CALCULATE (
            [Matching Decisions],
            KEEPFILTERS (
                FILTER (
                    'Fact Matching Decision',
                    NOT ISBLANK ( 'Fact Matching Decision'[ServiceDate] )
                )
            )
        )
    )

Timing Unavailable Count :=
    CALCULATE (
        [Operational Service Events],
        FILTER (
            'Fact Operational Service Event',
            ISBLANK ( 'Fact Operational Service Event'[LatestEstimatedDelayMinutes] )
        )
    )

Bay Unknown Count :=
    [Bay Unknown Events]

Data Quality Issue Count :=
    COUNTROWS ( 'Fact Data Quality Issue' )
```

| Measure | Source grain | Denominator / NULL behavior | Interpretation and prohibition |
|---|---|---|---|
| `Matching Decisions` | One Stage 9 source-observation decision per selected run/version | No denominator; BLANK when no selected decisions. | Complete matching-decision population, including all confidence outcomes. |
| `HIGH Matches` | Same matching-decision grain | Filter `MatchConfidence = HIGH`; BLANK when none. | Eligible for the HIGH-only operational association, not absolute certainty. |
| `MEDIUM Matches` | Same matching-decision grain | Filter `MatchConfidence = MEDIUM`; BLANK when none. | Contextual evidence; never promoted to the operational fact. |
| `LOW Matches` | Same matching-decision grain | Filter `MatchConfidence = LOW`; BLANK when none. | Diagnostic evidence; not an operational KPI population. |
| `Unmatched Decisions` | Same matching-decision grain | Filter `MatchConfidence = UNMATCHED`; BLANK when none. | Explicit unresolved/ambiguous/missing evidence; not zero operation. |
| `Matching Decisions Without Service Date` | Same matching-decision grain | Counts NULL `ServiceDate`; deliberately removes the `Dim Date` filter because no service-date period can be assigned; BLANK when none. | Explicit missing-service-date Data Quality evidence; not attributable to a selected service-date period and never derived from `ObservedAt`. |
| `HIGH Match Coverage %` | Same matching-decision grain | `HIGH Matches / Matching Decisions`; `DIVIDE` returns BLANK when no decisions. | Overall HIGH share of matching decisions; not operational-fact coverage. |
| `Service-Date-Scoped HIGH Match Coverage %` | Same matching-decision grain | HIGH decisions with non-NULL `ServiceDate` / all decisions with non-NULL `ServiceDate`; `DIVIDE` returns BLANK when no valid-service-date decisions. | Explicit HIGH share among decisions with an established service date; not overall matching quality and not operational-fact coverage. |
| `Timing Unavailable Count` | One latest estimated-delay value per operational fact row | Count of NULL latest estimated delay; BLANK when no rows. | Timing unavailable; not on-time or cancelled. |
| `Bay Unknown Count` | One consolidated bay-evidence value per operational fact row | Same as `Bay Unknown Events`; BLANK when none. | Bay evidence unavailable; not unchanged. |
| `Data Quality Issue Count` | One classified issue per run/entity/evidence occurrence | No denominator; BLANK when no issue rows. | Count of recorded quality issues; not Sampling-Era-filterable in the base model and not a service-performance rate. |

`HIGH Match Coverage %` uses every selected matching decision as its explicit
denominator, including MEDIUM, LOW, and UNMATCHED. With no Date filter, that
population also includes decisions whose `ServiceDate` is NULL. Under a
`Dim Date` filter, the active relationship excludes those NULL-service-date
rows; use `Service-Date-Scoped HIGH Match Coverage %` when the report needs
that scope to be explicit. `Matching Decisions Without Service Date` removes
the Date filter deliberately so the missing-date population remains visible as
Data Quality evidence, but it cannot be assigned to a service-date period.
Report and tooltip language must state: **service-date-filtered matching
coverage includes only decisions with a valid `ServiceDate`; decisions without
a valid service date are excluded from that slice and are reported separately
as Data Quality evidence.** `MatchMethod`, `DataQualityClassification`, mapping
version, source namespace, candidate evidence, and limitation codes remain
fields for technical drill-down; they are not silently converted to a new
confidence category.

### 7.7 Disconnected threshold parameter

```DAX
Selected Estimated Delay Threshold (Minutes) :=
    SELECTEDVALUE (
        'Param Estimated Delay Threshold'[ThresholdMinutes],
        0
    )
```

`Param Estimated Delay Threshold[ThresholdMinutes]` is an integer parameter
with the recommended values `0` through `15`, single-select, default `0`.
Zero means no added analytical tolerance assumption. It is not an official
Cologne punctuality policy, provider policy, source truth, or actual physical
punctuality rule.

## 8. DAX safety, denominator, and NULL rules

Future measures must follow these rules:

- use `DIVIDE()` for ratios and state the denominator in the measure contract;
- return BLANK when a semantically required denominator is unavailable;
- do not silently convert BLANK to zero where BLANK means unavailable;
- do not classify BLANK timing as within threshold;
- preserve the operational fact grain and do not count raw observations as
  service events;
- preserve the latest observation's NULL estimate; do not carry forward an
  earlier estimate with `COALESCE`;
- calculate estimated delay only from a comparable source estimate and
  timetable; never calculate Actual Delay from `EstimatedTime`;
- do not infer cancellation, service operation, or punctuality from missing
  timing; and
- never turn Situation association into causal attribution.

Required NULL/unknown semantics are:

| Evidence | Semantic treatment |
|---|---|
| NULL latest estimated time or delay | `Timing Unavailable`; excluded from estimated delay classifications and averages/medians. |
| NULL/blank bay value or no comparable bay pair | `Bay Unknown`; not `Unchanged`. |
| Zero persisted Situation links | No Situation association evidence in the selected fact population; not a causal negative. |
| Missing service date in a matching decision | Data-quality/matching limitation; never derived from observation time. |
| MEDIUM/LOW/UNMATCHED decision | Remains in the Data Quality subject area; never promoted to the HIGH-only operational fact. |
| Missing source observation | Coverage/unavailability evidence; not on-time, served, cancelled, or no delay. |

Calculated columns should not be used where an explicit measure or governed
source projection preserves the required filter, denominator, and NULL
semantics.

## 9. Approved report terminology

| Allowed | Avoid |
|---|---|
| Observed Estimated Delay | Actual Delay |
| Latest Estimated Delay | Actual Arrival Delay |
| Estimated Timing Available | Service operated normally |
| Timing Unavailable | On Time / Cancelled |
| Bay Change Evidence | Actual Platform Change |
| Bay Unknown | Platform Unchanged |
| Situation-Associated Event | Situation-Caused Delay |
| HIGH Match | Correct service with absolute certainty |
| Monitored / observed sample | Entire Cologne network |
| Scheduled Cologne network context | Observed Cologne-wide operation |
| Matching coverage | Complete data coverage |

The report must distinguish these three coverage concepts in labels and
tooltips:

1. **Scheduled coverage** — static VRS schedule and service-calendar context.
2. **Operational observation coverage** — observed/high-confidence operational
   service-event evidence in the monitored sample.
3. **Matching coverage** — all Stage 9 source-event matching decisions and
   their confidence/quality outcomes.

Any Date-filtered matching-coverage visual must use or be documented as
service-date-scoped. It must warn that decisions without a valid service date
are outside the Date slice and remain separately reported Data Quality
evidence.

## 10. Sampling and network-scope language

Operational report pages must use wording such as:

- Monitored operational sample;
- Observed service events;
- Matching coverage;
- Estimated timing coverage; and
- Scheduled Cologne network context.

The model must never imply that the monitored realtime sample equals the
entire Cologne network. A station panel, bounded pilot, or historical sampling
era is not a network-wide operational population. `Dim Sampling Era` currently
filters the `Fact Matching Decision` population only. Data Quality
classifications carried by those matching decisions inherit that context; the
separate `Fact Data Quality Issue` and `Data Quality Issue Count` are not
Sampling-Era-filtered in the base model. `Dim Sampling Era` is not an
operational KPI slicer and has no approved relationship to `Fact Operational
Service Event`. A future relationship to the independent issue fact or an
operational-event projection may be added only after explicit `SamplingEraKey`
lineage at that fact's grain is established and the projection's grain, key
uniqueness, historical-era semantics, and lineage are validated. A future
operational projection must not arbitrarily assign an operational event whose
source observations cross an era boundary.

Operational KPI pages must not silently pool different sampling eras or
cadences when that pooling can change interpretation. Until validated
operational-era attribution exists, the report must either constrain
operational KPI analysis to one compatible era/time period or show a clear
coverage/methodology warning. Sampling era remains available in technical
context for the matching-decision population and the Data Quality
classifications it carries; it does not filter the independent issue fact.

The executive report must show a prominent Data Quality/coverage warning when
matching, timing, station, or source coverage is incomplete. It must not
publish a network-wide operational conclusion from a sampled station panel.

## 11. Executive and technical reporting boundary

### Executive layer

The executive layer may emphasize:

- selected period and, where supported, sampling-era context for matching
  decisions and the Data Quality classifications carried by those decisions;
- observed operational-service-event count;
- estimated timing coverage;
- latest estimated-delay evidence using the selected analytical threshold;
- affected lines, stops, and modes only where canonical mappings are safe;
- bay-change evidence and bay-unknown count;
- Situation-associated evidence;
- scheduled Cologne network context; and
- a prominent Data Quality/coverage warning, including service-date and
  sampling-era limitations.

Executive cards must not expose unexplained `MatchMethod`, mapping-version,
source-namespace, candidate-score, or raw observation fields as KPIs.

### Drill-down / technical layer

Technical pages may expose:

- HIGH/MEDIUM/LOW/UNMATCHED confidence;
- `MatchMethod` and approved `DataQualityClassification`;
- source system, namespace, observation identity, and observation count;
- matching run/version and sampling policy/era;
- candidate count/evidence and mapping limitations;
- source evidence availability and NULL states; and
- service-date limitations, missing-service-date evidence, and methodology/
  coverage notes.

Technical detail is explanatory lineage. It does not turn a MEDIUM decision
into an operational outcome or a Situation link into a cause.

## 12. Future PBIP/TMDL implementation contract

Only after a later explicit implementation authorization may the project create
the Power BI source under `powerbi/` using:

`PBIP + TMDL + PBIR`

under source control. The future implementation must:

1. bind only to approved PostgreSQL/analytics projections or another explicitly
   approved source contract;
2. implement the table grains and relationship matrix in this document;
3. disable Auto Date/Time and use `Dim Date`;
4. add the explicit measures and disconnected threshold parameter;
5. preserve the static/canonical identity boundary and HIGH-only operational
   eligibility;
6. build executive and technical pages with the terminology and sampling
   warnings above; and
7. use Power BI Desktop for datasource binding, refresh, render QA,
   interaction QA, and save round-trip validation.

Generated Desktop-owned metadata must not be hand-edited without necessity.
No PBIP, TMDL, PBIR, PBIX, `model.bim`, datasource, refresh, render, or
Desktop-generated metadata is part of this Stage 11 completion.

## 13. Implementation-deferred items

The following remain deferred and are not silently assumed by this design:

- PostgreSQL creation, migration, SQL execution, and data loading;
- physical static warehouse and `Fact Daily Scheduled Trip` projection;
- durable source-observation population for the Stage 10 adapter;
- live Stage 10 refresh/validation and any operational fact row population;
- physical canonical-to-static identity projections where Stage 9 has not
  validated the mapping;
- any future `Dim Sampling Era` relationship to the independent `Fact Data
  Quality Issue`; explicit `SamplingEraKey` lineage at that fact's grain and
  validation of projection grain, key uniqueness, historical-era semantics,
  and lineage are required before approval;
- validated operational-event-to-sampling-era projection/bridge, including
  grain, uniqueness, lineage, and crossing-era handling; until then,
  `Dim Sampling Era` is not an operational KPI slicer;
- Power BI datasource binding, refresh, report rendering, and interaction QA;
- any Actual Physical Delay or confirmed actual on-time KPI;
- cancellation inference and causal Situation attribution; and
- Stage 12 50-station selection or any later collector/readiness work.

## 14. Stage 11 acceptance matrix

| Criterion | Result |
|---|---|
| Static Schedule / Network, Operational Evidence, and Matching/Data Quality subject areas are explicit. | PASS |
| Every approved semantic table has an exact grain. | PASS |
| Relationships are one-to-many and single-direction; the physical Stage 6 scheduled facts have no direct fact-to-fact join while the approved semantic `Dim Scheduled Trip` paths remain explicit. | PASS |
| No bidirectional, accidental many-to-many, or automatic relationship design is approved. | PASS |
| Static VRS identities and canonical operational identities are explicitly separated. | PASS |
| One governed Date dimension, operational `service_date`, and no Auto Date/Time are explicit. | PASS |
| Stage 6 pattern/date-expansion rules are preserved; `BridgeServiceDate` owns Service + Date expansion. | PASS |
| HIGH-only Stage 10 operational scope and all-confidence Stage 9 Data Quality scope are separate. | PASS |
| Required explicit DAX measure groups, denominators, and NULL behavior are defined. | PASS |
| Estimated-delay threshold policy is disconnected, `0..15`, default `0`, and not official punctuality. | PASS |
| Bay semantics preserve Changed / Unchanged / Unknown. | PASS |
| Situation semantics remain associated/non-causal. | PASS |
| Sampling-era filtering is limited to the `Fact Matching Decision` population and the Data Quality classifications it carries; the independent `Fact Data Quality Issue` is excluded, operational KPI era attribution is deferred, cross-era pooling is constrained or warned, and monitored-sample/network-language limits are explicit. | PASS |
| Date-filtered matching coverage is explicitly service-date-scoped; missing `ServiceDate` decisions have a separate evidence measure and warning. | PASS |
| Executive and technical reporting boundaries are explicit. | PASS |
| Future PBIP/TMDL/PBIR and Desktop validation requirements are documented without creating artifacts. | PASS |
| Stage 10 runtime status remains unchanged and Stage 12 is not started. | PASS |

## 15. Final Stage 11 decision

**FINAL DECISION:** Stage 11 is
**COMPLETE — APPROVED DESIGN; PBIP/DESKTOP IMPLEMENTATION DEFERRED**.

This approval authorizes the semantic design/contract only. It does not
authorize PostgreSQL/runtime work, Power BI Desktop execution, PBIP/TMDL/PBIR
creation, legacy-model copying, Stage 12, or any reinterpretation of
estimated timing, bay evidence, Situation evidence, matching confidence, or
network coverage. The corrections in this revision clarify the existing
semantic contract and do not alter the Stage 9/10 status or expose a deeper
Stage 9/10 incompatibility.
