# Stage 3A — Disruption Cause & Alert Evidence Audit

**Status:** `HOST EXECUTION PENDING`
**Project state:** Stage 0 **APPROVED**; Stage 1 **APPROVED**; Stage 2
**APPROVED**; Stage 3 **IN PROGRESS**
**Purpose:** bounded evidence validation before any Stage 3 production design

Stage 3A measures how much disruption, cause, effect, alert, and MDD Situation
evidence is actually populated in one current GTFS-Realtime snapshot and two
current MDD StopEvent responses. It does not design tables, collectors,
matching algorithms, dashboards, or a normalized cause model.

## Source roles and guardrails

- MDD/TRIAS remains the **PRIMARY REALTIME SOURCE**.
- GTFS-RT remains **COMPLEMENTARY REALTIME**.
- The Germany-wide GTFS.de feed is a source input; its feed-wide metrics are
  not Cologne analytical metrics.
- GTFS-RT analytical scope remains Cologne-only.
- The Neumarkt IDs in this audit are current-snapshot diagnostic IDs only, not
  a historical identity bridge.
- MDD `JourneyRef` must not be equated with GTFS-RT `trip_id`; Stage 3A makes
  no MDD-to-GTFS-RT match.
- Source provenance and source-specific missingness remain visible.
- Confirmed Actual Time is unavailable, Actual Delay is not approved, and
  prediction accuracy versus actual is not available.
- `Provider-Reported Cause`, `Reported Disruption Cause`, `Reported Service
  Effect`, and `Associated Situation / Alert` are evidence terms. The audit
  does not use `Root Cause`, `Proven Cause`, or `Confirmed Cause of Delay`.

## Bounded host request budget

The host-run script is [stage_03a_disruption_evidence_probe.py](../../scripts/stage_03a_disruption_evidence_probe.py).
Its fixed budget is:

| Source | Method | Endpoint | Maximum attempts | Retry/fallback behavior |
|---|---|---|---:|---|
| GTFS-RT | GET | `https://realtime.gtfs.de/realtime-free.pb` | 1 | No retry; no fallback endpoint |
| MDD | POST | `https://mdd.gorheinland.com/delfi` | 2 | One request per station; no retry; no fallback endpoint |
| **Total** |  |  | **3** | Sequential; no concurrency |

GTFS-RT is parsed with the already validated installed
`gtfs-realtime-bindings==2.2.0` package. The validated host runtime is Python
3.9.6. The MDD API key is read only from `MDD_API_KEY`; it is never printed,
hashed, persisted, or logged.

The two MDD diagnostic stations are fixed and do not define production
monitoring scope or cadence:

| Station | `StopPointRef` |
|---|---|
| Köln Neumarkt | `de:05315:11111` |
| Köln Bf Mülheim | `de:05315:19201` |

Each MDD request uses the established TRIAS 1.2 StopEvent request contract:
`NumberOfResults=5`, `StopEventType=arrival`,
`IncludePreviousCalls=true`, `IncludeOnwardCalls=true`, and
`IncludeRealtimeData=true`.

## GTFS-RT audit scope

Every `ServiceAlert` in the one retrieved snapshot is inspected. The JSONL
output measures:

- total alerts;
- cause and effect present/missing counts and exact source enum/value
  distributions;
- header and description text presence/missing counts, explicit language
  counts, and min/median/p90/max character-length statistics;
- active-period population;
- total informed-entity references;
- alert counts with trip, route, stop, agency, and multiple scope types;
- exact scope combinations, including `NO_IDENTIFYING_SCOPE` and
  `MULTI_SCOPE`; and
- no more than ten sanitized short text examples containing source wording,
  source cause/effect values, and alert entity scope.

An explicitly populated `UNKNOWN_CAUSE` is retained in the cause distribution
and is not converted to missing. No cause is inferred from header or
description text.

### Current Neumarkt sub-audit

The current GTFS.de child stop IDs used only for this snapshot are:

`106842`, `207108`, `441164`, `581903`, `96309`.

The probe collects TripUpdates whose StopTimeUpdates reference those exact
IDs, then uses the source-reported current `trip_id` values and any
source-reported `route_id` values. It separately counts alerts linked by:

- exact current trip ID;
- exact current stop ID; and
- route ID, only when a route ID is available on a matching TripUpdate.

The output section is named `cologne_neumarkt_current_snapshot` and keeps
linked-alert cause/effect distributions separate from feed-wide metrics.

## MDD Situation audit scope

For each response the probe measures `result_count`, Situation object count,
situation-reference count, unique Situation count, and relation scope:

- `call_level_situation_link_count`;
- `service_level_situation_link_count`; and
- `other_unclassified_relation_count`.

`SituationFullRef`, `SituationRef`, and `IncidentCall` are counted and labelled
as source reference types where observed. None is emitted as an
`application_error` merely because it is a short code or identifier.

Observed Situation structures are inventoried without inventing absent fields.
Where present, the audit measures population of `SituationNumber`/identifier,
`ParticipantRef`, `Summary`, `Description`, `Detail`, and validity start/end
values, plus percentages, explicit languages, character-length statistics, and
up to ten sanitized short examples. Situation text is labelled
`SOURCE-REPORTED SITUATION TEXT`; it is not normalized into a cause taxonomy.

`SituationFullRef`, `SituationRef`, and their CALL/SERVICE locations remain
associations. Association does not establish that a Situation caused a
particular delay, cancellation, or service effect.

## Output and temporary raw artifacts

The probe emits deterministic JSON Lines records for configuration, GTFS-RT
feed/cause/effect/scope/text metrics, the current Neumarkt sub-audit, one MDD
station summary per station, an aggregate MDD Situation summary, and a final
summary. A host run may write response bytes only beneath a mode-700 temporary
directory such as `/private/tmp/cologne-transit-stage-03a-XXXXXXXX/`, with
individual files mode 600. No raw payload is written into the repository and
no permanent raw-retention policy is introduced.

The final summary reports exact population percentages and neutral evidence
statements. It does not apply undocumented HIGH/MEDIUM/LOW thresholds and it
does not approve a schema, cause taxonomy, collector cadence, GTFS-RT
production ingestion, cross-source matching, causal attribution, database,
scheduler, or Power BI work.

## Decision gate after host evidence

Stage 3A remains `HOST EXECUTION PENDING` until the user runs the probe and
the resulting JSONL is reviewed. After host evidence, the next decision may
classify source-evidence usability and identify remaining validation gaps. It
must not be treated as approval of production implementation or as proof of
root cause.

Codex preparation validation is limited to compilation, AST parsing, `--help`,
synthetic in-memory GTFS-RT/MDD parsing, JSON serialization, attempt-budget
inspection, secret-safety review, and repository diff checks. The probe must
not be executed from Codex.

## Host command

```sh
cd /Users/admin/Documents/cologne-transit-intelligence

python3 scripts/stage_03a_disruption_evidence_probe.py
```
