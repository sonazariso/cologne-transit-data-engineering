# Stage 3C — Cross-Source Matching Design

**Status:** `COMPLETE — APPROVED DESIGN CONTRACT — IMPLEMENTATION NOT STARTED`<br>
**Completion date:** `2026-09-28`<br>
**Project state:** Stage 0 **APPROVED**; Stage 1 **APPROVED**; Stage 2
**APPROVED**; Stage 3 **COMPLETE**

This document closes Stage 3C as a conceptual reconciliation design. It does
not implement matching, create canonical mappings, create database tables,
run a production collector, or authorize causal attribution.

## Decision boundary

| Classification | Stage 3C result |
|---|---|
| **Validated source fact** | MDD `JourneyRef` and GTFS-RT `trip_id` come from different identifier namespaces. Neumarkt has bounded current GTFS.de identity evidence; railway Köln Hbf remains unresolved. GTFS-RT `ServiceAlert` cause/effect fields were explicitly present in the inspected snapshot, but `UNKNOWN` values dominated. |
| **Approved design decision** | Reconcile through a conceptual canonical service-event layer, starting with the stop event. Preserve source identities, source evidence, match confidence, match method, scope, and disagreement. |
| **Deferred implementation decision** | Canonical stop/line bridge construction, matching algorithms, production associations, persistence schema, and validation sampling remain future implementation work. |
| **Open implementation risk** | The full Cologne stop/line bridge and Cologne Hbf rail identity are incomplete; representative cross-source matching has not been empirically validated. |

## Non-equivalence of source identifiers

The following rule is mandatory:

```text
MDD JourneyRef != GTFS-RT trip_id
```

The two values may be retained as source attributes and may contribute evidence
to a later mapping, but equality or string similarity alone is never a match.

## Canonical reconciliation unit

The first canonical unit is a **STOP EVENT**, not an entire trip. The
conceptual layer may contain:

```text
CanonicalServiceEvent
  ServiceDate / OperatingDay
  CanonicalLine
  CanonicalStop
  Direction
  ScheduledTime
  EventType (ARRIVAL or DEPARTURE)
  StopSequence
  Operator
```

Candidate evidence dimensions are:

- `ServiceDate` / operating day;
- validated `CanonicalLine`;
- validated `CanonicalStop`;
- direction;
- timetabled/scheduled time;
- arrival-versus-departure event type;
- stop sequence where available and useful;
- operator where useful; and
- adjacent-stop or multi-stop sequence consistency where useful.

A single stop-event association does not automatically establish full-trip
equivalence. Consistent evidence across adjacent stops and sequence context
can strengthen a later journey-level conclusion, but the supporting stop-event
evidence must remain available.

## Cologne scope

The analytical scope remains public transport performance inside Cologne.
Germany-wide GTFS-RT availability does not expand that scope.

- An in-scope Cologne stop event is not an in-scope full trip.
- For a cross-boundary service, only validated Cologne stop events and related
  Cologne service evidence enter core KPI calculations.
- For movement or segment analysis inside Cologne, both endpoints should
  normally be Cologne stops; a service merely passing one Cologne stop is not
  an internal Cologne segment.
- Long-distance services, including ICE, IC/EC, NightJet, and equivalents,
  remain outside core KPI scope unless explicitly changed later.
- External trip portions may be retained only when needed for safe
  reconciliation and under a later approved persistence decision.

## Match confidence and method

Every planned association must preserve conceptual `MatchConfidence` and
`MatchMethod`; weak evidence must not be converted into certainty.

| Confidence | Permitted use |
|---|---|
| `HIGH` | Strong agreement across multiple dimensions such as service date, canonical stop/line, scheduled time, direction, event type, and stop sequence or multi-stop consistency. May support a cross-source service-event association. |
| `MEDIUM` | Useful but incomplete or partly ambiguous evidence. Context only unless later validation supports stronger use. |
| `LOW` / `UNMATCHED` | Weak overlap or no defensible match. Debugging and data-quality evidence only; never a service-level operational fact. |

Conceptual method values include:

- `EXACT_STOP_DATE_TIME_DIRECTION`;
- `EXACT_STOP_DATE_TIME_SEQUENCE`;
- `MULTI_STOP_SEQUENCE_MATCH`;
- `ROUTE_TIME_CONTEXT_ONLY`;
- `STOP_TIME_CONTEXT_ONLY`; and
- `UNMATCHED`.

These values describe evidence and are not a final SQL enum or implementation
requirement.

## Alert and Situation association

MDD Situation associations and GTFS-RT ServiceAlert associations remain
provider-reported context. Association is not causal attribution.

For GTFS-RT alerts:

- a trip-specific alert plus a `HIGH` event reconciliation may support the
  statement that GTFS-RT reported the alert for the matched service;
- a route-only alert is route context;
- a stop-only alert is stop context;
- an agency-only alert is broad context; and
- alert scope must not be promoted to a narrower scope merely because a route,
  stop, or agency appears nearby.

Even a HIGH-confidence association does not prove that an alert caused a
specific delay, cancellation, or measured outcome. `ReportedCause`,
`ReportedEffect`, and MDD provider-reported reason text remain source evidence.
They must not be renamed `RootCause`, `ProvenCause`, or `ConfirmedCauseOfDelay`.

Cross-source agreement can strengthen evidence consistency. It does not prove
scientific causality. If MDD and GTFS-RT disagree, preserve both observations,
their timestamps, source paths, and scopes rather than forcing agreement.

## Static bridge limitations

Neumarkt has usable current GTFS.de static identity evidence. Railway Köln Hbf
is not fully resolved. The adjacent KVB identities must not be substituted for
the unresolved railway identity. The full Cologne canonical stop and line
bridge therefore remains an implementation and validation risk carried forward
from Stage 3.

## Conceptual future entities

The design may use conceptual entities such as:

- `CanonicalStop`;
- `CanonicalLine`;
- `CanonicalServiceEvent`;
- `SourceEventMatch`;
- `MddObservation`;
- `MddSituation`;
- `GtfsRtObservation`;
- `GtfsRtAlert`;
- `GtfsRtAlertEntity`; and
- `AlertAssociation`.

These are design concepts only. Stage 3C creates no PostgreSQL tables, SQL
migrations, final schema, collector, or matching engine.

## Required future validation

Before production matching is implemented, validate representative populations
for:

- canonical Cologne stop identity, including railway Köln Hbf;
- canonical line/operator identity;
- service date and scheduled-time alignment;
- direction and event type;
- stop sequence and adjacent-stop consistency;
- trip/route/stop/agency alert scope;
- HIGH/MEDIUM/LOW match precision and reviewability; and
- disagreement, missingness, and source-specific failure handling.

Until those checks are completed, MEDIUM is contextual and LOW/UNMATCHED is
not operational attribution evidence.

Related documents:

- [Stage 3A evidence audit](03a-disruption-cause-alert-evidence-audit.md)
- [Stage 3B collection and cadence contract](03b-collection-strategy-cadence-contract.md)
- [Cross-source reconciliation and Cologne scope contract](../source-contracts/cross-source-reconciliation-contract.md)
- [GTFS.de static identity bridge audit](../source-contracts/gtfsde-static-identity-bridge-audit.md)
