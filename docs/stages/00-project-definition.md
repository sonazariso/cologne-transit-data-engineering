# Stage 0 — Project Definition

**Status:** APPROVED  
**Approval date:** 2026-09-25

## 1. Purpose

Cologne Transport Intelligence is a Business Intelligence and data-engineering portfolio project for analysing public transport **system performance within Cologne**.

The system should produce useful management information rather than a collection of disconnected technical metrics. A non-technical manager should be able to understand whether the transport system is operating well during the selected period, where problems are concentrated, how serious they are, and what evidence supports the conclusion.

Data engineering, collection, matching, warehousing, and modelling are supporting capabilities. They are not the primary business outcome.

## 2. Primary management question

> Is public transport within Cologne operating well during the period under analysis?

Supporting questions include:

- What part of the Cologne public-transport system is represented by the available data?
- What is the actual delay performance where actual timing evidence is available?
- How does performance change over time?
- Which lines, modes, stations, and time periods show stronger or weaker performance?
- Are cancellations or non-serviced stops observable from explicit source evidence?
- Where are disruptions or situation records associated with poor performance?
- Does the source establish a cause, or only an association?
- Are there platform/service changes supported by source evidence?
- Where are data coverage or matching limitations affecting interpretation?
- Which areas deserve management attention?
- Can evidence-supported operational recommendations eventually be produced?

Any later “best/worst” ranking must define its KPI, denominator, period, coverage requirements, and minimum sample size before publication.

## 3. System Performance

**System Performance** is the umbrella management concept. It may eventually contain multiple separately defined dimensions, including:

- Actual Delay Performance
- Cancellation / Service Availability
- Reliability / consistency
- Disruption evidence
- Station/platform operational evidence
- Coverage and Data Quality
- Trend over time

No arbitrary composite score such as “73/100” will be introduced unless its weighting and interpretation can be defended and approved in a later stage.

## 4. Timing priority

The project's primary timing objective is:

`RecordedAtTime` → validated actual/recorded event time → **Actual Delay**

`EstimatedTime` is secondary. If useful and sufficiently covered, it may support:

- Estimated Delay
- Estimated vs Actual comparison
- Prediction accuracy

Important guardrail: the existence of `RecordedAtTime` in a TRIAS contract does not prove that the MDD endpoint returns it for the required events. Stage 2 must validate actual MDD responses and measure coverage before an Actual Delay KPI is implemented.

If sufficient actual/recorded timing evidence cannot be established, estimated timing must not be renamed or presented as Actual Delay.

## 5. Geographical scope

The core analytical scope is **public transport performance inside the city of Cologne (Köln)**.

A route is not automatically a core Cologne KPI route merely because it has at least one stop/event in Cologne. Later technical stages must define an evidence-based rule that distinguishes whole-route identity from the service/events observed inside Cologne.

Existing legacy observations outside the new analytical scope are not to be deleted merely because they are out of scope.

## 6. Transport modes

### Core starting scope

- Stadtbahn / Tram
- Urban/KVB Bus
- S-Bahn within Cologne

### Requires Stage 4 validation

- Regional Express (RE) service/events relevant inside Cologne
- Regional Bahn (RB) service/events relevant inside Cologne
- Regional / Other Bus service/events genuinely relevant to Cologne

### Separate analytical category

- Rail Replacement Bus (SEV)

### Outside core KPI scope for now

- ICE
- IC
- NightJet
- FlixTrain
- Thalys / equivalent long-distance category if present

Long-distance evidence may be retained in source/raw layers when legitimately received, but is not part of the core management KPI scope at this stage.

The formal mode inventory and technical classification must be re-proven from source data in Stage 4; legacy counts are evidence, not authoritative classification.

## 7. Reporting audience

Primary audience: a non-technical operational or management decision maker.

The first management view should make it possible to understand, without SQL/TRIAS/GTFS knowledge:

- Is performance currently acceptable/problematic according to explicitly defined measures?
- What is the main observed problem?
- Where is it occurring?
- When did it occur / how long has it persisted?
- How severe/widespread is it?
- Which lines/stations/modes are affected?
- Is there evidence about the reason, or only a correlated situation record?
- Is the situation improving or worsening?

Technical coverage, matching, collector health, and methodology should remain available as drill-down/context rather than overwhelm the executive view.

## 8. Evidence policy

**No invented operational facts.**

Delay, cancellation, service status, disruption, platform change, arrival/departure status, and causal claims must be either:

1. directly supported by an approved source field; or
2. deterministically derived from approved source fields with documented semantics.

Absence of an estimate is not automatically a cancellation. A situation record occurring near a delayed service is not automatically the cause of that delay. NULL/unknown values must remain distinguishable from negative/false values.

## 9. Explicitly out of scope at Stage 0

- Rebuilding the legacy implementation by copying it wholesale.
- Treating legacy architecture as automatically correct.
- Starting a new MDD collector before source/quota review.
- Deleting or resetting legacy realtime history.
- Presenting estimated delay as actual delay.
- Inventing cancellation or causality from incomplete evidence.
- Treating the monitored station panel as the entire Cologne network.
- A final composite system-performance score without an approved methodology.

## 10. Approval gate

Stage 0 is approved. Stage 1 may begin, but later-stage architecture/implementation decisions remain open until their own approval gates.
