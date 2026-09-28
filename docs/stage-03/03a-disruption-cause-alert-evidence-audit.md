# Stage 3A — Disruption Cause & Alert Evidence Audit

**Status:** `PARTIAL HOST EVIDENCE — MDD SITUATION DISCOVERY PENDING`
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

### Broader MDD-only discovery mode

The existing two-station audit remains the completed baseline. The probe also
now has an explicit host-only discovery mode for the broader bounded question
of whether current MDD responses expose Situation/reason evidence elsewhere.
This mode makes **no GTFS-RT request** and uses exactly one MDD POST per listed
diagnostic station, up to ten total attempts. These locations are diagnostic
only and do not define production scope or cadence:

| Station | `StopPointRef` |
|---|---|
| Köln Bf Mülheim | `de:05315:19201` |
| Köln Heumarkt | `de:05315:11110` |
| Köln Breslauer Platz/Hbf | `de:05315:11212` |
| Köln Mülheim Wiener Platz | `de:05315:19211` |
| Köln Hbf | `de:05315:11201` |
| Köln Messe/Deutz Bf | `de:05315:11901` |
| Köln Bf Ehrenfeld | `de:05315:14201` |
| Köln Wahn S-Bahn | `de:05315:17701` |
| Köln Hansaring | `de:05315:11801` |
| Köln Bf Lövenich | `de:05315:13701` |

The mode preserves the established request contract (`NumberOfResults=5`,
`IncludePreviousCalls=true`, `IncludeOnwardCalls=true`, and
`IncludeRealtimeData=true`), performs no retries or concurrency, and stops on
the first `401`/`403`. A missing `MDD_API_KEY` causes zero HTTP requests and a
clear `MDD_API_KEY_MISSING` JSONL status.

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
- non-provenance description counts, distinct non-provenance values, bounded
  top non-provenance values, and the Neumarkt trip-linked subset; and
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

`SituationFullRef`, `SituationRef`, `IncidentCall`, and `InfoCall` are counted
and labelled as source reference types where observed. None is emitted as an
`application_error` merely because it is a short code or identifier. Real
`ErrorMessage` fields are inventoried separately.

Observed Situation structures are inventoried without inventing absent fields.
Where present, the audit measures population of `SituationNumber`/identifier,
`ParticipantRef`, `Summary`, `Description`, `Detail`, and validity start/end
values, plus percentages, explicit languages, character-length statistics, and
up to ten sanitized short examples. Situation text is labelled
`SOURCE-REPORTED SITUATION TEXT`; it is not normalized into a cause taxonomy.

`SituationFullRef`, `SituationRef`, and their CALL/SERVICE locations remain
associations. Association does not establish that a Situation caused a
particular delay, cancellation, or service effect.

The JSONL station output also reports, when present, journey-level and
call-level `SituationFullRef` counts, `Attribute`/`GeneralAttribute` details,
operational status flags (`Cancelled`, `Deviation`, `Unplanned`,
`NotServicedStop`, `UnplannedStop`, `NoBoardingAtStop`, `NoAlightingAtStop`,
and `DemandStop`), populated `Extension` paths/keys, and populated
`ErrorMessage` values. Operational flags remain effect/status evidence and
are not interpreted as reasons. Attributes are labelled
`PASSENGER_INFORMATION_ATTRIBUTE` unless source semantics explicitly prove
otherwise.

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

## Deep source-field forensic validation

This section is an offline audit of the successful host artifacts from
`/private/tmp/cologne-transit-stage-03a-wzv9x1qc/`. The exact directory was
available and contained `gtfs-rt-snapshot.pb`, `koeln_neumarkt.json`, and
`koeln_bf_muelheim.json`. No new HTTP, MDD, GTFS-RT, DNS, or SQL request was
made. The raw files remain outside the repository.

### GTFS-Realtime protobuf presence

The installed `gtfs-realtime-bindings==2.2.0` binding supports the optional
enum fields `cause`, `effect`, and `severity_level`, as well as the optional
message fields `cause_detail`, `effect_detail`, and `url`. Reading an optional
enum attribute alone is not a presence test: an absent field returns its
protobuf default. The corrected probe therefore uses `HasField()` and
`ListFields()` only, and never compares a returned value with a descriptor
default.

The artifact contained **95,578** Alert entities. Explicit presence was:

| Field | Explicitly present | Explicitly absent | Percentage present |
|---|---:|---:|---:|
| `cause` | 95,578 | 0 | 100.000% |
| `effect` | 95,578 | 0 | 100.000% |
| `severity_level` | 95,578 | 0 | 100.000% |
| `cause_detail` | 0 | 95,578 | 0.000% |
| `effect_detail` | 0 | 95,578 | 0.000% |
| `header_text` | 95,578 | 0 | 100.000% |
| `description_text` | 95,578 | 0 | 100.000% |
| `url` | 0 | 95,578 | 0.000% |

Repeated-field presence by non-zero length was `active_period=0`,
`communication_period=0`, `impact_period=0`, and `informed_entity=95,578`.
All four fields are supported by this binding. `informed_entity` contained
95,578 total references in the artifact.

The old implementation's `HasField()` path worked with this installed
binding, so the artifact's prior cause/effect counts were not actually
inflated by protobuf defaults. Its fallback comparison against
`default_value`, however, was unsafe and would have counted a default as
source presence in a compatibility path. That defect is corrected and covered
by synthetic absent-versus-explicit-default tests.

The observed cause matrix was:

| Category | Count | Percentage of Alerts |
|---|---:|---:|
| Field absent | 0 | 0.000% |
| Present + `UNKNOWN_CAUSE` | 95,404 | 99.818% |
| Present + `OTHER_CAUSE` | 115 | 0.120% |
| Present + specific known cause | 59 | 0.062% |

The specific known values were `CONSTRUCTION` (24), `TECHNICAL_PROBLEM`
(33), `MEDICAL_EMERGENCY` (1), and `POLICE_ACTIVITY` (1). The effect matrix
was field absent `0`, present + `UNKNOWN_EFFECT` `95,578`, present +
`OTHER_EFFECT` `0`, and present + specific known effect `0`.

Severity values were `INFO` (95,404), `WARNING` (140), and `SEVERE` (34).
No semantic disruption classification was applied to any Alert.

### GTFS detail fields and text statistics

`cause_detail` and `effect_detail` were omitted from the previous audit. The
binding supports both fields, but neither was explicitly serialized in this
artifact. Consequently both fields had zero translations, no language values,
no character-length observations, and no examples. They are now emitted and
labelled **SOURCE-REPORTED CAUSE DETAIL** and **SOURCE-REPORTED EFFECT
DETAIL**; no cause was inferred from their absence.

Exact normalized text statistics trim leading/trailing whitespace and collapse
repeated whitespace only. They are counted once per Alert carrying a given
normalized value, with percentages using the 95,578-Alert denominator. The
artifact had 468 distinct header texts and 551 distinct description texts.
The top lists, including all requested top-20 entries, are emitted as
`top_20_header_texts`, `top_20_description_texts`, `top_20_cause_details`, and
`top_20_effect_details`. The leading values were:

- Header: `Fahrradmitnahme begrenzt möglich` (371), `Komfort Check-in verfügbar - wenn möglich bitte einchecken` (189), and `Fahrzeuggebundene Einstiegshilfe vorhanden` (161).
- Description: `Echtzeitdaten aufbereitet von GTFS.de, bereitgestellt von DELFI` (64,609) and `Echtzeitdaten aufbereitet von GTFS.de, bereitgestellt von Verkehrsverbund Bremen & Niedersachsen (VBN)` (11,598).

The exact observed provenance prefix
`Echtzeitdaten aufbereitet von GTFS.de` occurred at the start of 85,916
description values, or **89.891% of Alerts**. The remaining descriptions were
not automatically classified as disruptions; many were service or passenger
information. The corrected output also reports **9,662** alerts with
non-provenance description text, **540** distinct non-provenance descriptions,
and the top non-provenance values. The Neumarkt trip-linked subset contained
**0** alerts with non-provenance description text in this artifact; its cause
summary was **269 `UNKNOWN_CAUSE`** and **0 known-or-other causes**. These are
bounded snapshot observations, not semantic classifications.

### MDD raw JSON structure

Each JSON artifact was recursively inventoried by object key path, occurrence
count, and value type. Each response contained five `stopEventResult` records.
The important observed paths were:

In the table, `...` is only a display abbreviation for the literal common
prefix `$.serviceDelivery.deliveryPayload.stopEventResponse` (the JSONL probe
emits each full path).

| Observed path | Neumarkt | Bf Mülheim | Observation |
|---|---:|---:|---|
| `$.serviceDelivery.deliveryPayload.stopEventResponse.stopEventResponseContext` | 1 object | 1 object | response context present |
| `...stopEventResponseContext.situations.ptSituation` | 1 empty array | 1 empty array | no inline PT Situation |
| `...stopEventResponseContext.situations.roadSituation` | 1 empty array | 1 empty array | no road Situation |
| `...stopEventResult[*].stopEvent.service.situationFullRef` | 5 empty arrays | 5 empty arrays | no service references |
| `...stopEventResult[*].stopEvent.thisCall.callAtStop.situationFullRef` | 5 empty arrays | 5 empty arrays | no current-call references |
| `...stopEventResult[*].stopEvent.previousCall[*].callAtStop.situationFullRef` | 99 empty arrays | 67 empty arrays | no previous-call references |
| `...stopEventResult[*].stopEvent.onwardCall[*].callAtStop.situationFullRef` | 59 empty arrays | 97 empty arrays | no onward-call references |
| `...stopEventResponse.errorMessage` | 1 empty array | 1 empty array | no response errors |
| `...stopEventResult[*].errorMessage` | 5 empty arrays | 5 empty arrays | no result errors |

Thus the two-response sample had `situation_count=0`,
`situation_full_ref_count=0`, `unique_situation_full_ref_count=0`,
`resolved_inline_situation_ref_count=0`, and
`unresolved_situation_ref_count=0`. There were no `SituationRef`,
`IncidentCall`, or `InfoCall` occurrences. `TextualContent`, customer
information, `Reason`, `Effect`, `Advice`, `Consequence`, and equivalent
populated Situation text were not observed.

The complete path inventory also surfaced fields outside the old
Summary/Description/Detail focus. Populated examples included service
`destinationText[*].text`, `originText[*].text`,
`serviceSection[*].mode.name[*].text`,
`serviceSection[*].publishedLineName[*].text`, and call-stop
`stopPointName[*].text`. These are source text and identity/wayfinding
metadata, not disruption reasons. In the Bf Mülheim response, the raw payload
also contained operational fields `service.cancelled` (1),
`notServicedStop` (47), `noBoardingAtStop` (47), and `noAlightingAtStop` (47)
across the observed call stops. They remain source-reported operational
flags, not inferred causes.

### Situation references, incident/info identifiers, and errors

The corrected probe now reports every non-empty `SituationFullRef` and
`SituationRef` with its full JSON path, nearest enclosing object, sibling
fields, participant, situation number, version, identity, association scope,
and inline-resolution status. It preserves the demonstrated association
labels `CALL`, `SERVICE`, `JOURNEY`, `STOP_EVENT`, `RESPONSE_CONTEXT`, and
`OTHER_OR_UNCLASSIFIED` without assuming a fixed provider structure.

For these artifacts, there were no `IncidentCall` or `InfoCall` identifiers,
so there is no raw path or parent structure from which to assign an origin.
The exact `ErrorMessage` paths above were separately inventoried and all their
arrays were empty; no real error value was observed. Therefore any previous
diagnostic interpretation that grouped IncidentCall/InfoCall-like identifiers
under `application_errors` was structurally wrong. The corrected probe keeps
incident/info references, Situation references, and real `ErrorMessage`
structures in separate outputs and never calls a reference an error merely
because it is a short identifier.

### Parser comparison and corrections

Against the previous Stage 3A output, the forensic answers are:

1. **GTFS default counting:** not inflated for this artifact under the
   installed binding, but the unsafe default-value fallback was a real parser
   defect and is removed.
2. **GTFS detail fields:** omitted previously; now audited.
3. **MDD Situation objects:** none were present, so no populated Situation was
   missed in this sample.
4. **MDD SituationFullRef references:** the raw fields existed as empty arrays,
   but no reference item existed to miss; their paths and emptiness are now
   explicitly retained.
5. **Reason/information fields outside Summary/Description/Detail:** generic
   source text, empty Situation/reference scaffolding, and operational flags
   were present; no populated MDD reason/effect/advice content was observed.
6. **IncidentCall/InfoCall paths:** none occurred in either artifact, so their
   actual location remains unknown rather than guessed.
7. **`application_errors`:** treating references as errors was structurally
   wrong; real `ErrorMessage` content was absent in both responses.

The audit-only script now uses protobuf presence APIs, audits detail and
severity fields, emits exact text frequencies, inventories every MDD JSON
path, recursively inventories inline Situation objects, resolves references
when identities permit, preserves association scope, separates incident/info
identifiers from real errors, and retains source paths without becoming a
production raw-document store.

### Required evidence-location coverage verification

The current implementation was checked before adding the broader discovery
mode. The status below distinguishes existing recursive coverage from the
narrow reporting additions required for the new sample:

| Evidence location/category | Verification status | Current output or correction |
|---|---|---|
| Response-context `Situation` objects | **ALREADY_COVERED** | Recursive inline Situation detection and full populated-field inventory were already present. |
| `DatedJourney` → `SituationFullRef` | **MISSING_COVERAGE_FIXED** | Recursive reference detection already existed; journey-level counts and structural association output are now explicit. |
| `CallAtStop` → `SituationFullRef` | **MISSING_COVERAGE_FIXED** | Recursive reference detection already existed; call-level counts and structural association output are now explicit. |
| Journey/service `Attribute` / `GeneralAttribute` | **MISSING_COVERAGE_FIXED** | Populated objects now emit exact paths, `Text`, `Code`, `AllFacilities`, `Mandatory`, `Importance`, `InfoURL`, and `Status` values as `PASSENGER_INFORMATION_ATTRIBUTE`. |
| Operational status fields | **MISSING_COVERAGE_FIXED** | True values now emit exact paths and separate per-field counts; they remain effect/status evidence. |
| `ErrorMessage` | **ALREADY_COVERED** | Error fields and populated values were already inventoried separately from references. A direct `error_message_count` is now also emitted. |
| `Extension` / provider-specific extension structures | **MISSING_COVERAGE_FIXED** | Populated extension paths, direct keys, nested paths, and bounded sanitized scalar examples are now inventoried. |
| GTFS non-provenance text and Neumarkt linked-text metrics | **MISSING_COVERAGE_FIXED** | The existing global text inventory was retained and explicit non-provenance and trip-linked counts/top values were added without NLP classification. |

The generic reason-like inventory, exact JSON path inventory, Situation
identity capture, reference resolution, and IncidentCall/InfoCall structural
inventory were already present and were reused rather than duplicated.

### Historical IncidentCall/InfoCall search

Before preparing the broader run, the repository diagnostic locations and
`/private/tmp/cologne-transit-recorded-time-lifecycle-*` were searched
offline. The historical lifecycle logs do contain flattened diagnostic
occurrences: `hbf-lifecycle-1.log` and `hbf-lifecycle-2.log` contain 169
`IncidentCall` identifier occurrences across 14 JSONL records and 30 distinct
identifiers. In those flattened records the identifier is located at
`$.application_errors[*]`, with the record object `$` as its flattened parent;
the surrounding diagnostic siblings include `journey_ref`, `stop_point_ref`,
`observation_label`, `http_status`, and `raw_artifact_path`. The three
Neumarkt lifecycle logs contain no `IncidentCall` or `InfoCall` identifier.
No `InfoCall` identifier was found in any searched repository log.

The `raw_artifact_path` values in the historical records point to 35 files
under the requested `/private/tmp/cologne-transit-recorded-time-lifecycle-*`
prefixes, but every referenced file was absent when checked. The flattened
`application_errors` array therefore cannot be treated as the original MDD
JSON container, and the raw parent/sibling structure and semantic origin of
the identifiers cannot be recovered. The bounded result is:

`RAW_SOURCE_STRUCTURE_UNAVAILABLE_FOR_HISTORICAL_IDENTIFIER`

The old `application_errors` name is not treated as evidence that those
identifiers were errors. The current MDD analyzer reports any future
occurrence by exact raw path, parent object, sibling fields, and structural
origin when the raw response is available; genuine `ErrorMessage` values
remain a separate output.

### Existing GTFS-RT artifact check

The retained artifact
`/private/tmp/cologne-transit-stage-03a-wzv9x1qc/gtfs-rt-snapshot.pb` and
the two retained MDD JSON files remain available for offline inspection. No
new GTFS-RT download was made. The output now explicitly covers the
non-provenance description count, distinct values, top values, Neumarkt
trip-linked non-provenance alerts, and known-versus-`UNKNOWN_CAUSE` counts;
the existing protobuf presence/detail logic was reused and no duplicate GTFS
implementation was added.

Remaining unknowns are temporal stability across repeated snapshots, whether
other provider responses contain populated Situation content, the raw
location and semantics of the historical `IncidentCall` identifiers,
`InfoCall` structures, and any cross-source attribution. This bounded offline
finding set does not close Stage 3A or authorize production implementation.

## Decision gate after host evidence

Stage 3A remains `PARTIAL HOST EVIDENCE — MDD SITUATION DISCOVERY PENDING`
until the broader MDD-only discovery mode is executed and its resulting JSONL
is reviewed. After host evidence, the next decision may
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

# Broader MDD-only discovery; do not run from Codex.
python3 scripts/stage_03a_disruption_evidence_probe.py --mode mdd-discovery
```
