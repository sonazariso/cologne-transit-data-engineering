# Stage 9 — Matching & Data Quality

**Status:** `COMPLETE — APPROVED MATCHING & DATA QUALITY SEMANTICS`<br>
**Date:** `2026-09-29`<br>
**Validation mode:** deterministic implementation regression plus bounded source-backed probe

Stage 9 implements the matching and data-quality foundation handed off by the
Stage 3C design. The implementation regression and one bounded source-backed
acceptance execution pass. The live run made three sequential MDD requests,
retained 60 parsed stop-event observations through the Stage 8 parser and
Stage 9 adapter, independently established 54 GTFS service dates, and produced
51 core deterministic matches. Synthetic fixtures remain regression evidence
only. The implementation is designed for the eventual Tiered50 collector
without activating Tiered50, selecting the final 50 stations, building the
operational fact, or changing the production cadence/result-limit decisions
owned by later stages.

The implementation is deliberately evidence-preserving. An unresolved or
ambiguous identity is a retained data-quality result; it is not converted into
a guessed canonical mapping.

## 1. Authoritative inputs and evidence boundary

The implementation uses these existing authorities:

- the approved Stage 4 VRS Global-ID reference snapshot and its direct Cologne rule
  (`stop_id` beginning with `de:05315:`);
- the Stage 5 logical layers and source-event grains;
- the Stage 3C stop-event-first reconciliation contract;
- Stage 8 MDD source-faithful observations and lineage; and
- the bounded GTFS.de identity-bridge evidence.

The approved Stage 4 reference snapshot remains source evidence rather than a
claim that a PostgreSQL warehouse has already been loaded: 3,137 Cologne stop IDs,
1,144,295 Cologne scheduled stop events, 1,082,490 internal segments, 61,805
Cologne-serving trip patterns, and 137 Cologne-serving routes. The offline
validation reconciles these recorded counts and checks the zero-anomaly
acceptance findings in the approved Stage 4 JSON.

The Stage 9 run used the current official VRS snapshot `VERSION__20260929_0106`
with SHA-256
`ca64753223d3908a86b74fc322103b9e4c6c9c90275cfc5cb2c4c624b0082ee0`. This
differs from the approved Stage 4 reference snapshot
`VERSION__20260928_0042` / SHA-256
`6ae1c7f65299438f3b0bc049ed3626734b291378446e114dfecc4bcea4636b6b`; the
current snapshot passed all 17 Stage 4 revalidation checks. The distinction is
explicit in the run evidence: Stage 4 semantics and scope/hierarchy contracts
were revalidated, while historical exact row counts were not silently replaced.
No GTFS-RT request or production database connection was made. The SQL
migration is prepared for the approved PostgreSQL architecture and is not an
excuse to claim that a live warehouse load occurred. The machine-readable
evidence audit is the authoritative classification of synthetic, source-backed,
unresolved, and not-evaluated populations:
[`09-matching-data-quality-evidence.json`](09-matching-data-quality-evidence.json).

## 2. Implemented artifacts

| Artifact | Responsibility |
|---|---|
| [`scripts/stage_09_matching_quality.py`](../../scripts/stage_09_matching_quality.py) | Standard-library matching engine, identity bridges, indexed candidate generation, confidence/method decisions, field coverage, service-date checks, repeated-observation continuity, alert-scope checks, and offline acceptance population |
| [`scripts/stage_09_source_backed_acceptance.py`](../../scripts/stage_09_source_backed_acceptance.py) | Strictly bounded source-backed acceptance probe: one official VRS download and at most one sequential MDD request for each of three approved Cologne targets; raw responses are never persisted |
| [`sql/stage_09_matching_quality.sql`](../../sql/stage_09_matching_quality.sql) | PostgreSQL 18 control, canonical, match-candidate, data-quality, view, and index contract |
| [`tests/test_stage_09_matching_quality.py`](../../tests/test_stage_09_matching_quality.py) | Offline regression tests for known-good, ambiguous, unresolved, namespace, lineage, and parser-retention behavior |
| [`scripts/stage_08_mdd_realtime_pilot.py`](../../scripts/stage_08_mdd_realtime_pilot.py) | Prospective source-contract hardening: retains candidate `vehicleJourneyRef`, `datedVehicleJourneyRef`, `affectedVehicleJourney`, `vehicleJourneys`, `operatingDaysDescription`, `direction`, `origins`, and `destinations` with source paths |
| [`09-matching-data-quality-evidence.json`](09-matching-data-quality-evidence.json) | Source-classified Stage 9 evidence artifact; synthetic regression is separated from the accepted bounded source-backed population |
| [`09-source-backed-matching-summary.json`](09-source-backed-matching-summary.json) | Latest bounded source-backed probe status, request count, source snapshot metadata, diagnostics, and acceptance metrics |
| `09-source-backed-matching-evidence.jsonl` | Created only after a parseable source-backed population exists; contains sanitized row-level Stage 9 evidence and no credentials/raw payloads |

Run the bounded validation with:

```bash
python3 scripts/stage_09_matching_quality.py --offline-validation
```

The command performs zero network requests and zero database connections.

## 3. Identity and scope contract

### Source namespaces remain separate

MDD/TRIAS `JourneyRef`, MDD richer journey references, GTFS-RT `trip_id`,
GTFS-RT `route_id`, VRS `trip_id`, and VRS `route_id` remain separate source
values. The implementation never uses:

```text
MDD JourneyRef == GTFS-RT trip_id
```

as a match rule. A same-looking value in another namespace is retained as
candidate evidence only. A source trip identity can contribute only when an
explicit same-namespace/static identity condition is validated; otherwise the
match relies on independent stop-event dimensions.

### Canonical stops

The bridge is versioned and source-qualified by
`source_system + source_namespace + source_identifier`. It supports one
canonical stop with many validated source child/platform identities and
retains `VALIDATED`, `UNRESOLVED`, and `AMBIGUOUS` outcomes.

- VRS Cologne scope uses the actual `stop_id` referenced by `stop_times.txt`.
  `parent_station` does not pull a non-Cologne event into scope.
- The bounded GTFS.de Neumarkt evidence maps parent `493082` and children
  `106842`, `207108`, `441164`, `581903`, and `96309` to one canonical
  Neumarkt identity for the evidenced snapshot/era.
- Railway Köln Hbf remains `UNRESOLVED_RAILWAY_HBF_IDENTITY` in the GTFS.de
  bridge. Adjacent KVB parents `106448` and `437741` are not substituted for
  railway Köln Hbf.
- The VRS snapshot reports the actual `stop_id`/`parent_station` hierarchy
  used by `stop_times.txt`: 2,273 Cologne child/parent rows, 864 parent
  station rows, and zero orphan or outside-Cologne parent references. This
  validates exact child matching where a schedule row carries that child;
  it does not authorize suffix stripping or a parent fallback for an
  unresolved platform identity.
- MDD platform examples such as `de:05315:11201:7:79` and
  `de:05315:11201:7:72` remain unresolved against the retained VRS row-level
  event population. A parent fallback is permitted only when an explicit,
  versioned source bridge validates it.
- A source stop not in the bridge produces `SOURCE_IDENTITY_UNRESOLVED` and
  cannot be rescued by station name, route name, coordinates, or broad time.

### Canonical lines

Line mappings are explicit, versioned, and namespace-qualified. A line number,
route label, agency, or text similarity alone does not create a canonical line.
Conflicting source-to-line mappings remain ambiguous and are excluded from a
validated line match.

## 4. Stop-event matching

The canonical unit is a date-expanded stop event:

```text
service date
canonical stop
canonical line (when validated)
event type: ARRIVAL or DEPARTURE
scheduled time in service-day seconds
direction / stop sequence / operator evidence
static feed snapshot and source-row lineage
```

The runtime builds exact composite indexes on service date, canonical stop,
event type, and scheduled time, plus a sorted time index for an explicitly
requested tolerance. It does not scan the complete schedule for every
observation. This supports additional monitored stations and higher
Tiered50 observation volume without changing the evidence rule.

Candidate ranking uses only independent, source-qualified evidence:

- validated canonical stop, service date, event type, and scheduled time;
- validated canonical line;
- validated direction mapping;
- validated stop sequence or multi-stop sequence consistency;
- operator only under same-source/static identity or an explicit mapping; and
- same-namespace source trip identity only where that namespace is explicitly
  shared with the static candidate.

Raw Direction/DirectionRef, StopSeqNumber, origin/destination, and operator
equality remain retained evidence, but cannot alter score, rank, or tie
resolution until their source/namespace semantics are validated. A validated
line mapping may filter/rank candidates. This prevents an unvalidated provider
coincidence from selecting one otherwise equal static event.

`ObservedAt` is never a fallback for service date or scheduled event time. A
missing date, invalid time, unresolved stop, line disagreement, or ambiguous
top candidate remains unmatched with its reason and candidate IDs.

MDD absolute `TimetabledTime` values are preserved verbatim and converted from
their explicit source timezone to `Europe/Berlin` before comparison with GTFS
service-day seconds. Timezone-less absolute values remain
`INSUFFICIENT_EVIDENCE`; GTFS clock values above 24:00 remain extended hours.
`OperatingDayRef` is retained separately and does not become service-date
truth by alias or by `ObservedAt` derivation.

Raw DirectionRef, StopSeqNumber, and origin/destination identity are
supporting evidence until a source-population mapping is validated. Display
text is never an identity key. The current source-backed evidence artifact
therefore records direction and MDD-to-VRS line/sequence/endpoint relationships
as `NOT_EVALUATED` rather than promoting synthetic equality to HIGH.

| Outcome | Meaning |
|---|---|
| `HIGH` | Unique stop/date/time/event candidate with validated line plus direction, sequence, or multi-stop evidence; eligible for a stop-event cross-source association |
| `MEDIUM` | Useful unique contextual match with missing or incomplete disambiguation; not a strong trip-specific causal claim |
| `LOW` | Weak context such as a permitted time-tolerance result; diagnostic only |
| `UNMATCHED` | No defensible candidate, unresolved identity, ambiguous top candidates, or source disagreement |

Every decision records `MatchConfidence`, `MatchMethod`, candidate count,
candidate IDs, score/evidence flags, mapping-version lineage, source event
identity, source observation identity, and any validated correlation basis.
Ambiguous candidates are retained for review rather than arbitrarily selecting
the first row.

## 5. Data-quality contract

Required observation lineage and matching fields are measured independently:

- source event identity and source observation identity;
- `ObservedAt`;
- service/operating date;
- source stop identity;
- arrival/departure event type;
- source timetable/scheduled time;
- collector run;
- sampling policy; and
- sampling era.

Optional fields remain optional and are reported as coverage, not filled with
synthetic defaults. `NULL` remains distinct from `FALSE`, zero, empty text,
and not-observed. The implementation reports, among others:

`MISSING_REQUIRED_IDENTITY`, `STATIC_COVERAGE_GAP`, `STATIC_STOP_MISSING`,
`STATIC_ROUTE_MISSING`, `STATIC_SERVICE_DATE_NOT_ACTIVE`,
`STATIC_EVENT_NOT_FOUND`, `IDENTITY_CONFLICT`,
`AMBIGUOUS_MULTIPLE_CANDIDATES`, `INSUFFICIENT_EVIDENCE`,
`SOURCE_IDENTITY_UNRESOLVED`, `SOURCE_LINE_IDENTITY_UNRESOLVED`,
`SOURCE_LINE_DISAGREEMENT`, `INVALID_SERVICE_DATE`,
`INVALID_SCHEDULE_TIME`, `LINEAGE_INCOMPLETE`, `DUPLICATE_SOURCE_OBSERVATION`,
`CROSS_SOURCE_DISAGREEMENT`, and `OUT_OF_CORE_SCOPE`.

Repeated observations are not collapsed because estimate values happen to be
equal. Their response/observation IDs, `ObservedAt`, sampling policy, and
sampling era remain available for historical continuity. An exact replay may
be idempotent at ingestion, but it is not treated as permission to discard a
legitimate later observation.

MDD Situation and GTFS-RT ServiceAlert scope remains provider-reported
context. Route-, stop-, and agency-level alerts remain context only. A
trip-level alert requires a HIGH canonical service-event reconciliation before
it can be described as reported for that matched service; no implementation
path creates a root-cause or causal-delay claim.

## 6. Persistence and working-layer contract

The migration adds:

- versioned canonical stop and source-identity bridge rows;
- versioned canonical line and source-identity bridge rows;
- date-expanded canonical service events;
- one source-event match decision per source observation, including unmatched
  decisions;
- one auditable row per retained candidate;
- append-only matching validation results and data-quality issues; and
- rebuildable review/coverage views in `wrk`.

The SQL contract also requires a non-empty source-observation grain,
version-compatible canonical-event foreign keys, mapping-version columns on
match decisions, self-consistent matched/unmatched confidence/method pairs,
and an overlap-review view for conflicting effective mappings.

Frequently queried matching fields are relational. Bounded source-shaped
evidence and candidate-detail flags use `jsonb`; full realtime payloads and
credentials are not moved into the matching layer. Matching indexes cover
stop/date/event/time, line/date/stop/time, source trip lookup, bridge lookup,
review by confidence/era, and candidate rank. Conditional source indexes are
created only when the corresponding Stage 5 source tables exist.

## 7. Validation and evidence gate

The current offline regression returns `overall_status = PASS`,
`implementation_regression_status = PASS`, and
`stage_9_status = BLOCKED`. It proves the implementation behavior only:

- the approved Stage 4 baseline reconciles;
- synthetic known-good MDD and GTFS-RT stop events reach HIGH through
  explicitly supplied fixture evidence;
- synthetic cross-source agreement is through one canonical event and an
  explicitly validated fixture correlation basis, not source-ID equality;
- equal-looking cross-source journey values are not used as a key;
- ambiguous candidates are quarantined as `UNMATCHED`;
- unresolved railway Hbf is not replaced with an adjacent KVB complex;
- missing service date is not derived from `ObservedAt`;
- repeated observations and sampling-era lineage are retained;
- candidate richer MDD identity fields remain available with source paths;
- extended schedule hours do not wrap at midnight; and
- route-level alert context remains non-causal; and
- timezone, DST, midnight, extended-hour, adapter, and mapping-validity
  regressions pass.

The regression suite also proves that unvalidated StopSeqNumber and direction
coincidences cannot break an otherwise equal candidate tie, while an explicitly
validated auxiliary policy can resolve that tie. The SQL overlap-review view
compares effective ranges across different mapping versions and reports both
mapping versions, both ranges, both canonical targets, and the conflict flag.

The source-backed audit is separate from the offline regression and now passes
the bounded acceptance gate. The retained evidence shows:

- three bounded sequential MDD responses, each HTTP 200 and parseable, with 20
  stop events per response;
- 20 raw Stage 8 stop events with a THIS call in each request context, and 20
  adapted observations with a selected THIS call in each context;
- a current VRS snapshot that passed all 17 Stage 4 revalidation checks;
- 60 retained source-backed observations, 54 independently established GTFS
  service dates, and 51 core deterministic matches; and
- the existing GTFS.de bridge evidence, with railway Köln Hbf still unresolved
  and adjacent KVB complexes not substituted.

The latest probe result is recorded in
[`09-source-backed-matching-summary.json`](09-source-backed-matching-summary.json):
`source_backed_acceptance_status = PASS`, `stage_9_status = COMPLETE`, and
`mdd_http_attempts = 3`. The row-level JSONL was produced through the real
Stage 8 parser → Stage 8 bundle builder → Stage 9 adapter → independent GTFS
service-date establishment → final Stage 9 adaptation → matcher path. The
bounded contract remains capped at three sequential MDD attempts with zero
retries.

This is not a production-wide precision estimate, and the real GTFS.de
railway-Hbf bridge remains an explicit open identity-quality result.

## 8. Boundary to later stages

Stage 9 does not:

- build or approve `dw.fact_operational_service_event`;
- define first/latest/actual-time consolidation or Actual Delay;
- create a Power BI model or semantic report;
- select the final 50 stations;
- choose production cadence, concurrency, retries, or `NumberOfResults`;
- activate a Tiered50 collector or cutover; or
- claim that a provider-reported Situation/alert caused an outcome.

Those remain Stage 10 and later work. The Stage 8 historical completion record,
the future 50-station objective, and the 250,000-request ceiling remain
unchanged. Stage 10 is `NOT STARTED`.
