# Cross-Source Reconciliation Contract

**Status:** `APPROVED DESIGN GUARDRAIL — IMPLEMENTATION NOT STARTED`  
**Project state:** Stage 0 **APPROVED**; Stage 1 **APPROVED**; Stage 2
**APPROVED**; Stage 3 **NEXT / NOT STARTED**

This document defines the approved design contract for reconciling MDD/TRIAS
and GTFS-RT evidence within the Cologne Transport Intelligence project. It is
a documentation and design guardrail only. It does not approve Stage 3
implementation, final schemas, SQL tables, migrations, collectors, ETL,
matching algorithms, DAX, Power BI pages, production mappings, or scheduler
changes.

## 1. Purpose

The project uses two realtime sources with different identities and semantics:

- MDD/TRIAS is the **primary realtime source**.
- GTFS-RT is the **complementary/secondary realtime source**.

The sources must remain independently interpretable before any reconciliation
or association layer is introduced. This contract defines:

- how source observations may be reconciled;
- how Cologne analytical scope is determined;
- how cross-boundary and through-services are handled;
- how GTFS-RT ServiceAlert scope is preserved;
- which reported-cause claims are permitted;
- how MDD Situation evidence is interpreted; and
- which unresolved questions remain for Stage 3 design and validation.

The contract is intended to prevent a Germany-wide GTFS-RT feed, a weak
identity overlap, or a provider-reported cause/effect value from silently
expanding project scope or becoming an unsupported causal claim.

## 2. Source roles

MDD/TRIAS remains the primary realtime source because it is aligned with the
Cologne/VRS operating context and provides timetable, estimate, call sequence,
platform/bay, and situation evidence. GTFS-RT remains complementary because it
provides independent operational evidence, trip/stop relationships,
`SKIPPED`/`NO_DATA` states, and `ServiceAlerts`.

The sources have separate namespaces and provider semantics. Future processing
must preserve MDD observations and GTFS-RT observations independently before
creating any canonical or cross-source association. A common analytical view
may be derived later, but it must retain source provenance, source-specific
fields, observation time, and match evidence.

The GTFS-RT feed is a source feed, not the project's analytical geography.
Availability of Germany-wide data does not make the project Germany-wide,
NRW-wide, complete-VRS, or complete-GTFS.de analytics.

## 3. Geographic scope

The authoritative analytical scope is **Cologne/Köln only**. The core
analytical unit is transport activity evidenced inside Cologne.

Cologne scope must ultimately be determined from validated stop/station
identity and service evidence. It must not be inferred from any one of the
following alone:

- route name;
- agency name;
- text containing `Köln` or `Cologne`;
- line number; or
- a broad route or time match.

For a service extending beyond Cologne, only the validated in-scope Cologne
stop events and related Cologne service evidence may contribute to Cologne
analysis. The outside-city portion must not be used to calculate Cologne KPI
denominators or reported as though it were Cologne-only performance.

The phrase **Cologne-observed segment/service performance** is appropriate for
cross-boundary services. **Whole-route performance** is not appropriate unless
the full route has been explicitly added to a future approved scope.

## 4. Mode scope

The contract preserves the existing project direction for Cologne-relevant:

- bus;
- Stadtbahn/tram/light rail;
- S-Bahn;
- RB; and
- RE.

The event must still be inside the approved Cologne scope. A mode label, route
label, or feed presence does not by itself make an observation analytically in
scope. Any remaining service-level classification and coverage validation is a
later approved design/validation activity.

Long-distance services, including ICE, IC/EC, NightJet, and equivalent
long-distance rail, remain outside core KPI scope for now. If encountered in
GTFS-RT, they must be ignored for core Cologne KPI calculations or retained
only as explicitly labelled `OUT_OF_CORE_SCOPE` evidence. They must not alter
core KPI denominators or silently expand the project scope.

## 5. Cologne internal-event rule

An **in-scope stop event** is not automatically an **in-scope full trip**.

For routes or trips that extend outside Cologne:

- retain or analyze only stop events and service evidence inside the approved
  Cologne scope;
- exclude external portions from Cologne network KPI calculations;
- do not use the full trip's external stops in Cologne denominators; and
- do not describe the complete inter-city or regional route as having the
  observed Cologne result.

A regional train may therefore contribute its in-scope Köln stop events,
service state, disruption evidence, skipped-stop evidence, and estimated-delay
observations without making the complete route a Cologne KPI subject.

## 6. Through-service rule

Through-services are not core route-level KPI subjects. They may contribute
Cologne-specific evidence such as:

- Cologne stop-event observations;
- Cologne platform evidence;
- Cologne disruption evidence;
- Cologne skipped-stop evidence; and
- Cologne estimated-delay observations.

The distinction is mandatory:

> **IN-SCOPE STOP EVENT** does not automatically mean **IN-SCOPE FULL TRIP**.

For an analysis that claims movement inside Cologne, the movement must be
supported by in-scope Cologne stop-event evidence. Ideally both segment
endpoints are in-scope Cologne stops. A train merely passing through one
Cologne stop does not automatically represent an internal Cologne journey
segment. Stop-event analysis and internal-segment analysis remain separate
concepts.

## 7. Long-distance exclusion

ICE, IC/EC, NightJet, and equivalent long-distance services are
`OUT_OF_CORE_SCOPE` for the current core KPI model. Their GTFS-RT
`StopTimeUpdate` and `ServiceAlert` records must not change Cologne KPI
denominators. They may be retained as clearly labelled diagnostic or future
evidence, subject to a later approved design.

## 8. Source identity problem

MDD `JourneyRef` and GTFS-RT `trip_id` belong to different source identity
systems and are not assumed to be equal. The prohibited rule is:

```text
MDD JourneyRef == GTFS-RT trip_id
```

The project's current GTFS.de evidence is bounded. Köln Neumarkt has the
current validated GTFS.de parent stop `493082` with children `106842`,
`207108`, `441164`, `581903`, and `96309`. These identifiers must not be
assumed historically stable without validation.

Railway Köln Hauptbahnhof remains unresolved in GTFS.de. The adjacent KVB
complexes `106448` (`Köln Dom/Hbf`) and `437741` (`Köln Breslauer Platz/Hbf`)
must not be substituted for the railway Köln Hbf identity.

Both source identifiers must instead map, where validated, to canonical
operational identities and retain their original source values for audit.

## 9. Canonical stop concept

The future design needs a conceptual **CanonicalStop** identity bridge. This
is a design concept, not a final table or schema definition. It may include
conceptual attributes such as:

```text
CanonicalStop
  CanonicalStopId
  StopName
  CologneScopeFlag

MDD identity
  MddStopPointRef

GTFS.de identity
  GtfsDeParentStopId
  GtfsDeChildStopId

Validation
  ValidFrom
  ValidTo
  ValidationStatus
  ValidationMethod
```

The bridge must preserve one-to-many parent/child relationships where the
source represents a station complex or platform structure. It must also
preserve unresolved, ambiguous, invalid, and time-bounded mappings rather than
forcing an identity.

## 10. Canonical line concept

The future design needs a conceptual **CanonicalLine** identity that can map
source-specific line/route identifiers, names, modes, and operators where the
mapping is validated. It must retain source identifiers and mapping evidence,
including validity or ambiguity where relevant.

Line name, route name, agency, or line number alone is not sufficient evidence
for a stop or service identity. The final canonical-line mapping is not yet
implemented and must not be invented as part of this documentation contract.

## 11. Canonical service-event concept

The reconciliation unit is a canonical operational service event, not a direct
cross-source identifier equality. Conceptually:

```text
MDD observation
        |
        v
Canonical Service Event
        ^
        |
GTFS-RT / GTFS.de observation
```

A canonical service event may be represented by multiple consistent dimensions,
including:

- `ServiceDate` or operating day;
- canonical line identity;
- canonical stop identity;
- direction;
- scheduled/timetabled time;
- arrival/departure event context;
- stop sequence where available;
- operator where useful; and
- adjacent-stop sequence where useful.

MDD `JourneyRef`, GTFS-RT `trip_id`, and other source identifiers remain source
attributes supporting the mapping. They are not, by themselves, the canonical
identity.

## 12. Stop-event matching strategy

The preferred first reconciliation unit is a **stop event**, not an assumed
complete journey. A candidate stop-event match should compare as many
independent dimensions as the sources provide without requiring every source
to populate every dimension.

For example, an MDD observation for line 18 at Köln Neumarkt with a 10:15
scheduled arrival and direction X may be matched to a GTFS-RT observation only
when the validated canonical line and stop, service date, scheduled time,
direction where available, and event context provide consistent evidence.

Evidence strength is ordered as follows:

```text
multiple consistent stop-event matches with sequence evidence
        > single high-quality stop-event match
        > route/time proximity only
```

Multiple consecutive matches (for example Stop A, Stop B, Stop C, and Stop D)
with corresponding timetable sequence evidence materially strengthen a
journey-level equivalence claim. A route, station, operator, or broad-time
overlap alone does not prove identity.

## 13. Journey-level confidence

Journey-level equivalence may be considered only after stop-event evidence has
been reconciled. A single stop-event match can support a high-confidence
stop-event association when its independent dimensions are strong, but it does
not automatically prove full-journey equality.

Confidence in a service/trip-level match increases when the sources agree on
multiple stops, sequence, service date, canonical line, scheduled times,
direction, and event context. The future implementation must keep the
stop-event evidence that supports any journey-level conclusion.

## 14. Match confidence levels

The future reconciliation layer must define conceptual levels:

### HIGH

Strong identity agreement exists across sufficient dimensions, such as service
date, canonical stop, canonical line, scheduled time, direction where
available, event type, and stop sequence or multi-stop consistency. A HIGH
match may support trip/service-level cross-source attribution, subject to the
separate alert-scope rules below.

### MEDIUM

Useful evidence exists, but one or more important dimensions are missing or
ambiguous. A MEDIUM match may support contextual analysis or a potentially
associated alert. It must not support a strong trip-specific cause claim.

### LOW / UNMATCHED

Only weak overlap exists, such as the same route, station, operator, or broad
time window. Do not attribute a GTFS-RT alert or cause to an MDD trip on this
basis. LOW/UNMATCHED evidence is limited to context, diagnostics, or future
matching review.

## 15. Match methods

Reconciliation must preserve both `MatchConfidence` and an auditable
`MatchMethod`. Conceptual method values include:

- `EXACT_STOP_DATE_TIME_DIRECTION`;
- `EXACT_STOP_DATE_TIME_SEQUENCE`;
- `MULTI_STOP_SEQUENCE_MATCH`;
- `ROUTE_TIME_CONTEXT_ONLY`;
- `STOP_TIME_CONTEXT_ONLY`; and
- `UNMATCHED`.

These are conceptual examples, not a final SQL enum or implementation
contract. The stored method must make clear which evidence supported the
association and which dimensions were unavailable or ambiguous.

## 16. GTFS-RT ServiceAlert scopes

GTFS-RT `ServiceAlert` entity scope must be preserved. The following scopes are
not interchangeable:

- trip;
- route;
- stop; and
- agency.

An alert can be relevant to Cologne without being attributable to a particular
trip. The association level and the alert's referenced entity must remain
visible in future analytical data.

## 17. Trip-level alert association

If a GTFS-RT `ServiceAlert` explicitly references `trip_id`, and that trip has
a HIGH-confidence reconciliation to an MDD/canonical service event, the alert
may be associated with the matched service.

Permitted conceptual fields include:

```text
ReportedCause
ReportedEffect
AlertSource = GTFS_RT
AssociationLevel = TRIP
MatchConfidence = HIGH
```

Permitted wording is:

> GTFS-RT reported this alert for the matched service.

Even in this case, the cause/effect remains provider-reported evidence. It must
not be called a proven root cause or used to assert that it produced an exact
number of minutes of delay.

## 18. Route-level alert association

If an alert references only `route_id`, it must not automatically become the
cause of every delayed journey on that route. The permitted interpretation is:

> A route-level alert was active for the service context.

The conceptual association level is `ROUTE`. A route alert can provide
disruption context for in-scope Cologne service on that route, but
`CausalAttribution = NOT_ESTABLISHED` unless stronger source linkage exists.

## 19. Stop-level alert association

If an alert references a stop that maps to an in-scope canonical Cologne stop,
the project may state:

> A stop-level alert was active at this in-scope stop.

The conceptual association level is `STOP`. This must not automatically be
reported as the cause of a particular trip's delay.

## 20. Agency-level context

Agency-wide alerts are broad context only. Their conceptual association level
is `AGENCY_CONTEXT`. They may support clearly labelled management-level
disruption context, but they must not be assigned as causes to individual
delayed trips or automatically included in trip-level KPI calculations.

## 21. MDD Situation semantics

MDD `SituationFullRef` establishes an association with a reported MDD
Situation. It does not automatically prove that the Situation caused a
specific amount of `EstimatedTime` delay or any other measured outcome.

Future design should preserve conceptual fields such as:

```text
SituationAssociation
SituationSource = MDD
AssociationScope
ObservedAt
```

MDD Situation evidence and GTFS-RT alert evidence remain independently
interpretable even when they appear compatible.

## 22. Reported cause terminology

Use terminology such as:

- **Provider-Reported Cause**;
- **Reported Disruption Cause**;
- **Reported Service Effect**; and
- **Associated Alert**.

The following terms are not permitted without future evidence that explicitly
validates causality:

- Root Cause;
- Proven Cause; and
- Confirmed Cause of Delay.

`ReportedCause` is not `RootCause`. A reported effect such as
`SIGNIFICANT_DELAYS` is not proof of a measured delay amount.

## 23. Cross-source agreement

When MDD and GTFS-RT independently report compatible disruption context for
the same HIGH-confidence canonical service event, the future design may record
a conceptual `CrossSourceConfirmation = TRUE` (or an equivalent approved
term).

For example, an MDD construction-related Situation and a GTFS-RT trip alert
with `cause = CONSTRUCTION` and `effect = SIGNIFICANT_DELAYS` provide stronger
cross-source reported disruption agreement. They still do not constitute
scientifically proven root cause or prove that the disruption produced an exact
delay amount.

## 24. Cross-source disagreement

Sources must not be forced to agree. If MDD reports Situation A and GTFS-RT
reports Alert B, preserve both source observations and their provenance.

Future analytics must be able to show, for example:

```text
MDD reported:     Situation A
GTFS-RT reported: Alert B
```

One source must not overwrite, normalize away, or silently replace the other
source's reported evidence.

## 25. Missingness semantics

Missingness is source-specific evidence, not a positive operational outcome.

- A missing GTFS-RT `StopTimeUpdate` is not automatically on time, served,
  cancelled, skipped, or absent.
- A missing MDD event is not automatically on time, served, cancelled, skipped,
  or service absent.
- An explicit GTFS-RT `SKIPPED` or `NO_DATA` state remains a source-reported
  state and must not be conflated with an unobserved/missing update.
- An absent alert, absent cause, or absent match is not evidence that no
  disruption occurred.
- Out-of-scope, unresolved, unmatched, and not-observed states should remain
  distinguishable in later design.

These rules preserve NULL/unknown semantics and prevent feed coverage gaps from
becoming fabricated performance outcomes.

## 26. Source-faithful storage

Before reconciliation, preserve the sources independently. Conceptually, the
future design has separate source observations and an association layer:

```text
MDD source observation       GTFS-RT source observation
MDD Situation                GTFS-RT alert
            \                 /
             \               /
              reconciliation / association layer
```

Do not put one blended `Cause` field inside the primary raw/source observation.
Source-specific reported cause/effect, situation, alert scope, identity, and
observation time must remain available alongside any derived association.

The following are conceptual future entities only; they are not approved table
names or schemas: `CanonicalStop`, `CanonicalLine`, `CanonicalServiceEvent`,
`SourceEventMatch`, `MddObservation`, `MddSituation`, `GtfsRtObservation`,
`GtfsRtAlert`, `GtfsRtAlertEntity`, and `AlertAssociation`.

## 27. Cologne GTFS-RT filtering

Future conceptual processing follows this boundary:

```text
Germany-wide GTFS-RT feed
        |
        v
Source parsing
        |
        v
Cologne scope filter
        |
        v
Source-faithful Cologne GTFS-RT observations
        |
        v
Cross-source reconciliation
```

The analytical layer must not automatically persist every Germany-wide
`StopTimeUpdate` or `ServiceAlert`. An entity may be potentially relevant when
one of the following validated relationships exists:

- its stop maps to an in-scope canonical Cologne stop;
- its trip can be reconciled to an in-scope Cologne service event;
- its alert explicitly references an in-scope Cologne stop;
- its alert references a trip with in-scope Cologne service events; or
- its route-level alert affects a route with an active in-scope Cologne
  service context.

ServiceAlerts should enter the Cologne analytical context only through a
validated relationship to an in-scope Cologne stop, service/trip, route
context, or another explicitly approved Cologne operational entity.
Agency-wide alerts require special handling and must not automatically enter
trip-level KPI calculations. The final persistence/filtering algorithm is not
defined by this contract.

## 28. Cross-boundary trip handling

A future GTFS-RT `TripUpdate` may contain stop updates inside and outside Köln.
The design must conceptually distinguish:

```text
InScopeCologneStopEvent = TRUE / FALSE
```

Only `TRUE` stop events contribute to Cologne stop-event KPI analysis. Outside-
city updates may be discarded from the analytical layer or retained
temporarily when needed for safe reconciliation; permanent storage is a later
approved design decision.

For route-level analysis, aggregate only in-scope Cologne observations. For
internal movement or segment/travel analysis, require in-scope Cologne
stop-event evidence, ideally at both endpoints. Do not use a single passing
stop to infer a full internal Cologne journey.

## 29. KPI claim rules

Future KPI concepts permitted by this contract include:

- Estimated Delay;
- Estimated Delay Rate;
- Severe Estimated Delay Signal;
- Prediction Revision Count;
- Prediction Volatility;
- Platform Change Signal;
- Reported Disruption Count;
- Trips with High-Confidence Reported Cause;
- Skipped Stop Count/Rate;
- `NO_DATA` Count/Rate;
- Cross-Source Disruption Agreement;
- Realtime Coverage; and
- Realtime Freshness.

All core KPI denominators must be Cologne-only and must use only validated
in-scope evidence. Cross-boundary route results must be labelled as
Cologne-observed segment/service performance.

The following remain unapproved until confirmed actual time is available and
validated:

- Actual Delay;
- Actual On-Time Performance; and
- Prediction Accuracy vs Actual.

Estimated timing must remain explicitly labelled as estimated. A provider
reported cause may be counted as reported disruption evidence, but must not be
presented as proven causality merely because it is associated with an estimate.

### Management analytics

Future management analysis may ask:

- Which Cologne lines show the highest Estimated Delay burden?
- Which in-scope stops experience the most disruption evidence?
- Which reported disruption causes occur most often?
- Which reported causes/effects are associated with the highest Estimated Delay
  levels?
- Which lines or stops have frequent `SKIPPED` service?
- Where is prediction instability highest?
- Where do MDD and GTFS-RT independently report disruption evidence?
- Which operational problems are persistent over time?

These questions must be answered using language such as **association**,
**reported cause**, **reported effect**, and **operational signal** unless
causality is explicitly validated in a later approved evidence package.

## 30. Claim matrix

| Evidence | Permitted claim | Not permitted |
|---|---|---|
| HIGH-confidence trip match + trip-level GTFS-RT alert + cause/effect populated | Provider-reported disruption cause/effect for the matched service | Proven root cause; exact causal explanation of the measured delay |
| HIGH-confidence match + route-level alert only | Route-level disruption context | Trip-specific cause attribution |
| MEDIUM match + trip-level alert | Potentially associated trip alert | Cause of this delay |
| LOW / UNMATCHED match | Context or debugging evidence | Service-level cause attribution |
| In-scope stop-level alert | Stop-level alert active at the in-scope stop | Alert caused a particular trip's delay |
| Agency-level alert | Broad management/disruption context | Individual-trip cause or automatic trip KPI attribution |
| MDD Situation association | MDD reported Situation associated with the observation | Situation caused a specific delay amount |
| HIGH-confidence compatible MDD and GTFS-RT reports | Cross-source reported disruption agreement | Scientifically proven root cause |

The example of a valid claim is:

> MDD showed an estimated delay of 8 minutes for this service, while GTFS-RT
> reported a technical problem with significant delays for the matched
> service.

The following claim is invalid under this contract:

> The technical problem caused exactly 8 minutes of delay.

## 31. Unresolved issues

The following remain Stage 3 design or validation items:

1. Confirmed Actual Time remains unavailable.
2. Actual Delay remains unapproved.
3. Railway Köln Hbf GTFS.de identity remains unresolved.
4. The full Cologne GTFS.de stop-identity bridge is not yet built.
5. Canonical line mapping between MDD and GTFS.de is not yet implemented.
6. Cross-source service-event matching has not yet been empirically validated
   across a representative sample.
7. GTFS-RT cause/effect population quality has not yet been measured.
8. The final alert persistence and filtering strategy is not yet designed.

The bounded Köln Neumarkt identity evidence must not be generalized into
historical identity stability or used to substitute for the unresolved railway
Köln Hbf mapping.

## 32. Stage 3 implementation implications

When Stage 3 is explicitly started, its design review must address at least:

- source-faithful MDD and GTFS-RT staging before reconciliation;
- a validated canonical stop bridge and a separately governed canonical line
  mapping;
- canonical service-event matching using multiple dimensions;
- auditable `MatchConfidence` and `MatchMethod` evidence;
- distinct trip, route, stop, and agency alert scope;
- Cologne-only filtering before analytical persistence;
- explicit handling of through-services and external trip portions;
- long-distance `OUT_OF_CORE_SCOPE` treatment;
- provider-reported versus proven-cause terminology;
- source-specific missingness and provenance; and
- validation of representative matching and cause/effect coverage.

This list is a Stage 3 entry contract, not a Stage 3 implementation plan. Stage
3 remains **NEXT / NOT STARTED**. No SQL tables, C# models, collector code,
ETL, matching algorithms, DAX measures, Power BI pages, or production mappings
are created by this document.
