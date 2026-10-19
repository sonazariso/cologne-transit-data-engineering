# Stage 3 — Completion Decision

> **Historical engineering record:** This page preserves an earlier design, experiment, incident or point-in-time operational result. For the current project name, final state and safety boundaries, use [Project Status](../PROJECT-STATUS.md). Archived instructions here are **not** authorization to run a collector, apply SQL, or change production resources.


**Status:** `COMPLETE — APPROVED`<br>
**Completion date:** `2026-09-28`<br>
**Scope:** source evidence, collection/cadence design, and cross-source
matching design only

This is the authoritative Stage 3 completion record. Stage 3 is complete as a
design/contract stage; it is not a production activation or implementation
stage.

## Decision classification

| Classification | Stage 3 conclusion |
|---|---|
| **Validated source fact** | The bounded Stage 3A evidence recorded MDD provider-reported reason text, inline Situations, SituationFullRefs, service-associated references resolved to inline Situations in the sample, IncidentCall identifiers inside service attributes, operational-status evidence, and no demonstrated meaningful structured Reason field. GTFS-RT ServiceAlert cause/effect fields were present in the inspected snapshot, but `UNKNOWN` values dominated and alert text was often provenance/informational. |
| **Approved design decision** | MDD/TRIAS is the primary targeted realtime source; GTFS-RT is complementary and broad. Collection loops remain separate. MDD uses a tiered, quota-aware Sampling Policy/Era rather than an activated fixed schedule. Reconciliation begins at a canonical stop-event unit with explicit confidence and method. |
| **Deferred implementation decision** | Production collectors, scheduler, persistence schema, PostgreSQL migration, canonical bridges, matching algorithms, final station tiers/intervals, raw-payload retention duration, and Power BI changes are not implemented or activated. |
| **Open implementation risk** | `RecordedAtTime` remains unvalidated; Actual Delay and prediction accuracy versus actual remain unapproved; Cologne Hbf rail identity and the full Cologne bridge remain incomplete; operational cadence and matching precision still require measurement/validation. |

## Stage 3A — Disruption Cause & Alert Evidence Audit

**Final status: `COMPLETE — APPROVED`.**

The explicit user approval recorded with the current task closes Stage 3A for
the bounded evidence already collected. It does not reinterpret or expand that
evidence. The approved conclusions are:

- MDD provider-reported disruption/reason text was observed.
- Inline Situation objects and `SituationFullRef` were observed.
- The observed SituationFullRefs were service-associated and resolved to
  inline Situations in the bounded sample.
- IncidentCall identifiers were observed inside service Attribute/
  GeneralAttribute data; they are not protocol errors.
- GeneralAttribute contains both ordinary passenger/service information and
  operational IncidentCall information.
- Meaningful provider-reported reason text exists, but a dedicated meaningful
  structured Reason field was not demonstrated in the bounded sample.
- `ErrorMessage` remains semantically separate from Situation/Incident
  evidence; provider Extension data was not demonstrated in the sample.
- GTFS-RT ServiceAlert cause/effect fields were explicitly present in the
  inspected snapshot, but `UNKNOWN` values dominated and an Alert is not
  automatically a disruption event.
- Provider-reported reason text is evidence supplied by the source. It is not
  a `Root Cause`, `Proven Cause`, or `Confirmed Cause of Delay`.

Timing semantics remain unchanged: `TimetabledTime`, `EstimatedTime`, source
`RecordedAtTime`, and collector `ObservedAt`/`CapturedAt` are separate.
`RecordedAtTime` is nullable, source-only, never synthesized, and never copied
from an estimate, timetable, observation timestamp, or disappearance.

## Stage 3B — Collection Strategy & Cadence

**Final status: `COMPLETE — APPROVED DESIGN CONTRACT — ACTIVATION DEFERRED`.**

The approved collection architecture is:

- a logically separate MDD observation loop using targeted station/stop
  requests, bounded retries, explicit timeouts, secret externalization,
  independent observation timestamps, append-only evidence, and no replay of
  missed snapshots; and
- a logically separate GTFS-RT observation loop fetching the broad feed once
  per cycle, preserving feed timestamp and collector timestamp separately,
  and parsing the snapshot into relevant source observations.

Neither loop is dependent on the success of the other. Source-faithful
ingestion precedes any reconciliation. The approved cadence policy is tiered,
quota-aware, versioned by Sampling Policy/Era, protected against uncontrolled
overlap, and explicitly budgeted for retries, diagnostics, manual work,
calendar-month variation, and headroom. The historical 10/20/20 candidate and
its 280 logical MDD requests/hour are not approved as a production schedule.

Stage 3B completion does not imply final production intervals or final
50-station membership. Those require later runtime measurements.

## Stage 3C — Cross-Source Matching Design

**Final status: `COMPLETE — APPROVED DESIGN CONTRACT — IMPLEMENTATION NOT STARTED`.**

The approved reconciliation contract:

- never equates MDD `JourneyRef` with GTFS-RT `trip_id`;
- uses a conceptual canonical service-event layer, starting with the stop
  event rather than the full trip;
- considers service date, canonical line, canonical stop, direction,
  scheduled time, arrival/departure event type, stop sequence, operator, and
  adjacent-stop/multi-stop context where useful;
- preserves `HIGH`, `MEDIUM`, and `LOW`/`UNMATCHED` confidence and an auditable
  `MatchMethod`;
- limits HIGH matches to supportable cross-source service-event association;
  treats MEDIUM as context and LOW/UNMATCHED as diagnostic evidence;
- preserves Cologne-only scope, through-service boundaries, and long-distance
  exclusions; and
- keeps MDD Situation and GTFS-RT alert association source-reported and
  non-causal by default, with trip/route/stop/agency alert scope preserved.

The Neumarkt bridge is usable for current bounded evidence, while the railway
Köln Hbf identity and full Cologne stop/line bridge remain unresolved risks.

## Acceptance criteria

All twelve Stage 3 acceptance checks are satisfied:

1. Stage 3A is `COMPLETE — APPROVED` from explicit user approval.
2. Stage 3A evidence semantics are preserved, including provider-reported
   reason text versus the undemonstrated structured Reason field.
3. `EstimatedTime`, `RecordedAtTime`, and `ObservedAt` remain separate.
4. Stage 3B has an explicit MDD-primary / GTFS-RT-complementary architecture.
5. Stage 3B has an explicit cadence policy and quota guardrails.
6. No unvalidated production cadence is described as active or approved.
7. Stage 3C defines canonical stop-event reconciliation.
8. Match confidence and match method semantics are defined.
9. Alert/Situation association is explicitly non-causal by default.
10. PostgreSQL remains future direction only.
11. Deferred implementation items are carried forward explicitly.
12. Repository status documents are synchronized to Stage 3 completion.

## Required open items carried forward

- Source-provided `RecordedAtTime` has not been validated in observed data.
- Actual Delay remains **NOT APPROVED**.
- Prediction accuracy versus actual remains **NOT APPROVED**.
- Final production MDD station cadence is not activated.
- Final 50-station tier membership is not activated.
- Final raw-payload retention duration is not decided.
- PostgreSQL schema and migration are not implemented.
- Production collector and scheduler are not implemented.
- Railway Köln Hbf identity bridge remains unresolved.
- The full Cologne canonical stop/line bridge is not implemented.
- Production cross-source matching is not implemented.
- Cause taxonomy is not finalized.

## Explicit non-actions

Stage 3 completion did not implement PostgreSQL, modify the database, create
SQL migrations, implement collectors, implement matching, modify Power BI, or
start the next project stage. No MDD, GTFS-RT, web, DNS, or database network
request was made for this design closeout; the work uses existing repository
evidence only.

Related contracts:

- [Stage 3A evidence audit](03a-disruption-cause-alert-evidence-audit.md)
- [Stage 3B collection and cadence contract](03b-collection-strategy-cadence-contract.md)
- [Stage 3C cross-source matching design](03c-cross-source-matching-design.md)
- [Cross-source reconciliation and Cologne scope contract](../source-contracts/cross-source-reconciliation-contract.md)
- [Future observation and persistence contract](../source-contracts/future-observation-persistence-contract.md)
