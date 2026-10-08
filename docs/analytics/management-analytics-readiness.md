# Management Analytics Readiness

This report separates observed live SQL evidence, unavailable evidence, design assumptions, and recommendations. It does not create analytical facts or make causal claims.

**Overall status:** `BLOCKED_BY_READ_ONLY_PERMISSION_SCOPE`
**Live SQL capture:** `2026-10-08T08:49:22.0292316Z`
**Database:** `CologneTransitIntelligence`
**Read-only login:** `CologneTransitCollector`
**Audit analysis window (UTC):** `2026-10-01T08:49:22Z` through `2026-10-08T08:49:22Z` (end exclusive)

## Executive conclusion

SQL Server connectivity passed, but the configured least-privilege collector login cannot read the source, historical, GTFS, warehouse, or analytical evidence required for management analytics. The live evidence below is limited to the monitoring-panel metadata and target-audit lineage that this login can read.

No timing availability, delay distribution, propagation association, provider-cause coverage, or current network denominator is reported as measured. Those values are unavailable, not zero.

## Guardrails and sanitization

| Control | Result |
| --- | --- |
| Credentials, passwords, API keys, Keychain values | Excluded |
| Raw MDD payloads | Excluded |
| SQL operations | SELECT/metadata probes only |
| MDD requests / GTFS downloads | None |
| Collector, scheduler, Tiered50, Power BI, historical data | Unchanged |
| Fixture substitution | Prohibited; none used |

## Live SQL access and physical-object inventory

| Check | Live result |
| --- | --- |
| Connection | PASS |
| Database state | ONLINE |
| Database user access | MULTI_USER |
| SQL sysadmin/serveradmin/securityadmin | False/False/False |
| Broad database roles | db_owner=False, db_datareader=False, db_datawriter=False |
| Database-wide SELECT | NO |
| VIEW DEFINITION | NO |
| Bounded read-only queries | YES |
| Visible non-system objects | 49 |
| Requested object probes | 40 |

The inventory is metadata-limited because the current login has no database-wide definition permission. Only live column definitions observed through successful read-only probes are retained in the JSON artifact.

| Object | Category | Readability | Definition | Live columns |
| --- | --- | --- | --- | --- |
| ctl.MddRealtimeSamplingTarget | monitoring_panel | READABLE | COLUMN_NAMES_OBSERVED | 7 |
| ctl.MddRealtimeSamplingSlot | monitoring_panel | READABLE | COLUMN_NAMES_OBSERVED | 2 |
| ctl.MddMacTargetAudit | monitoring_panel | READABLE | COLUMN_NAMES_OBSERVED | 29 |
| ctl.MddMacSamplingEra | sampling_era | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| ctl.MddMacMethodologyEvidence | sampling_era | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| stg.MddMacSourceSnapshot | mac_source | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| stg.MddMacCallObservation | mac_source | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| stg.MddMacTimingObservation | timing | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| stg.MddMacSituationEvidence | provider_cause | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| stg.MddMacServiceSectionEvidence | provider_cause | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| stg.MddMacAttributeEvidence | provider_cause | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| stg.MddMacResponseError | data_quality | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| stg.MddMacParserGap | data_quality | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| ctl.MddImportBatch | source_lineage | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| ctl.MddImportSourceRow | source_lineage | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| ctl.MddCollectorRun | windows_history | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| stg.MddRealtimeStopObservation | windows_realtime | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| stg.MddRealtimeSituationObservation | windows_realtime | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| stg.MddRealtimeStopSituationLink | windows_realtime | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| ctl.GtfsLoadBatch | gtfs | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| ctl.GtfsStagingState | gtfs | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| ctl.GtfsValidationResult | gtfs | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| stg.GtfsStops | gtfs | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| stg.GtfsRoutes | gtfs | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| stg.GtfsTrips | gtfs | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| stg.GtfsStopTimes | gtfs | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| dw.DimStop | warehouse | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| dw.DimRoute | warehouse | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| dw.DimService | warehouse | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| dw.BridgeServiceDate | warehouse | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| dw.FactScheduledTrip | warehouse | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| dw.FactScheduledStopEvent | warehouse | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| dw.FactRealtimeStopPerformance | warehouse | OBJECT_NOT_FOUND_OR_NOT_VISIBLE | NOT_OBSERVED | 0 |
| dw.FactOperationalStopOutcome | warehouse | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| analytics.vwNetworkBaselineKpi | analytics | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| analytics.vwRealtimeDelayHotspot | analytics | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| analytics.vwRealtimeReliabilityByDimension | analytics | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| analytics.vwRealtimeSituationLinkedOutcome | analytics | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| analytics.vwMddMacRecordedAtTimeHistory | analytics | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |
| analytics.vwMddMacSourceFieldCoverage | analytics | SELECT_PERMISSION_DENIED | NOT_OBSERVED | 0 |

## Current monitoring-panel evidence

| Evidence | Observed live value |
| --- | --- |
| Target rows | 7 |
| SamplingTargetIds | 1, 2, 3, 4, 5, 6, 7 |
| IDs 1–7 preserved | YES |
| Legacy7 limited panel | YES |
| Köln Neumarkt Legacy7 production target | NO |
| Köln Neumarkt hotspot claim | NOT_PERMITTED_WITHOUT_SEPARATE_QUALIFIED_EVIDENCE |

The target-audit population is lineage evidence, not a delay measure. Current production target audits, imported research context, and the bounded Mac validation context remain separate:

| Sampling era | Collection context | Status | Audits | Runs | Buckets | Targets | First started UTC | Last started UTC |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Tiered50 | LIVE_RESEARCH_SINGLE_1 | SKIPPED | 2 | 2 | 2 | 1 | 2026-10-01T11:29:41.637Z | 2026-10-01T16:59:32.660Z |
| Tiered50 | LIVE_RESEARCH_SINGLE_1 | SUCCEEDED | 70 | 70 | 70 | 1 | 2026-10-01T11:30:36.332Z | 2026-10-01T22:55:00.707Z |
| Tiered50 | LIVE_RESEARCH_SINGLE_1 | FAILED | 3 | 3 | 3 | 1 | 2026-10-01T21:10:00.524Z | 2026-10-01T21:20:00.540Z |
| Legacy7 | MAC_PYTHON_MSSQL_PREPARED | SUCCEEDED | 42 | 6 | 6 | 7 | 2026-10-07T16:25:01.867Z | 2026-10-07T17:04:08.702Z |
| Legacy7 | MAC_LEGACY7_PRODUCTION | SUCCEEDED | 36 | 36 | 36 | 7 | 2026-10-07T19:40:17.282Z | 2026-10-08T08:45:22.446Z |
| Legacy7 | MAC_LEGACY7_PRODUCTION | SKIPPED | 8 | 8 | 8 | 6 | 2026-10-08T07:54:35.769Z | 2026-10-08T08:29:59.252Z |

## Timing evidence

No timing value is measured in this capture because `stg.MddMacTimingObservation` and the required source/schedule objects are not readable. The report therefore does not claim that any timing field is absent in the database.

| Measure | Status | Required evidence | Interpretation |
| --- | --- | --- | --- |
| scheduled_arrival_availability | NOT_MEASURED | TimetabledTimeSourceText plus validated GTFS scheduled arrival | scheduled/timetabled arrival field availability only |
| estimated_arrival_availability | NOT_MEASURED | EstimatedTimeSourceText | provider estimate availability; never actual physical delay |
| estimate_revision_frequency | NOT_MEASURED | SourceEventIdentity, ObservedAtUtc, EstimatedTimeSourceText | revisions per uniquely identified source event; repeated raw rows are not the denominator |
| first_and_latest_observed_estimate | NOT_MEASURED | SourceEventIdentity, ObservedAtUtc, EstimatedTimeSourceText | first/latest non-NULL estimate over an explicit UTC window |
| last_non_null_observed_estimate | NOT_MEASURED | SourceEventIdentity, ObservedAtUtc, EstimatedTimeSourceText | last known non-NULL estimate remains reportable without rewriting later source-NULL observations |
| last_non_null_recorded_at_time | NOT_MEASURED | RecordedAtTimeSourceText and ObservedAtUtc | retain last known source value without replacing later source-NULL observations |
| last_observed_recorded_at_time_availability | NOT_MEASURED | LastObservedRecordedAtTime and ObservedAtUtc | report persisted last-known source time availability separately from the latest raw observation |
| recorded_at_time_occurrence | NOT_MEASURED | RecordedAtTimeSourceText and RecordedAtTimeSourcePath | source-provided value only; capture/prediction timestamps are not substitutes |
| ambiguous_source_event_identity | NOT_MEASURED | SourceEventIdentity plus source-event identity components | classify duplicate or non-unique source-event identities before using an event-level denominator |
| arrival_departure_context | NOT_MEASURED | TimingContext | ARRIVAL and DEPARTURE remain separate timing populations |
| call_scope_context | NOT_MEASURED | CallScope | THIS, PREVIOUS, and ONWARD remain separate source call contexts |
| missing_timing_and_parser_gaps | NOT_MEASURED | MddMacTimingObservation and MddMacParserGap | missing evidence and parser gaps are classified, not silently converted to zero |

Required semantic guardrails remain: source `RecordedAtTime` is never inferred from a prediction or capture timestamp; a last known non-NULL value must not overwrite later source-NULL observations; Estimated Delay is never labelled actual physical delay; and ARRIVAL/DEPARTURE plus THIS/PREVIOUS/ONWARD contexts remain separate.

## Source-event identity quality

Identity quality is a prerequisite for every event-level denominator and consecutive-stop association. The following counts are deliberately unavailable under the current login:

| Measure | Status | Value |
| --- | --- | --- |
| ambiguous_source_event_identity_count | BLOCKED_BY_SOURCE_ACCESS | NOT_AVAILABLE |
| non_unique_source_event_identity_count | BLOCKED_BY_SOURCE_ACCESS | NOT_AVAILABLE |
| missing_source_event_identity_count | BLOCKED_BY_SOURCE_ACCESS | NOT_AVAILABLE |

Required identity components: ["source event identity", "source observation identity", "service date / operating day", "journey identity", "stop identity and sequence", "line and direction", "timing context and call scope"]

## Hotspots, propagation, and provider-reported causes

| Area | Status | Conclusion |
| --- | --- | --- |
| Station/line estimated-delay distributions | BLOCKED_BY_SOURCE_ACCESS | Not measured; no raw-count ranking |
| Repeated high-delay locations | BLOCKED_BY_SOURCE_ACCESS | Not measured; Legacy7 remains a limited panel |
| Consecutive-stop estimate increase | BLOCKED_BY_SOURCE_ACCESS | INSUFFICIENT_EVIDENCE |
| Candidate downstream propagation | BLOCKED_BY_SOURCE_ACCESS | INSUFFICIENT_EVIDENCE |
| Provider-reported Situation/Attribute evidence | BLOCKED_BY_SOURCE_ACCESS | Not measured; no causal inference |

Propagation cannot be upgraded beyond `INSUFFICIENT_EVIDENCE` without stable journey identity, service date, stop identity/sequence, line/direction consistency, repeated observations, temporal consistency, and explicit ambiguity handling. A Situation association is not proof of causation.

## Era reconciliation

| Population | Readback status | Interpretation |
| --- | --- | --- |
| Historical Windows Legacy7 production | BLOCKED_BY_ACCESS | The Windows-era population is not counted from current live SQL under this login. |
| Imported LIVE_RESEARCH_SINGLE_1 | VISIBLE_AS_TARGET_AUDIT_CONTEXT_ONLY | The context label is visible in target audits; source-row and import-batch evidence remain unreadable. |
| Mac oversampling validation interval | VISIBLE_AS_TARGET_AUDIT_CONTEXT_ONLY | MAC_PYTHON_MSSQL_PREPARED target-audit rows are visible, but the exact methodology record and source snapshots are not readable. |
| Corrected Mac Legacy7 production | VISIBLE_AS_TARGET_AUDIT_CONTEXT_ONLY | MAC_LEGACY7_PRODUCTION target-audit lineage is visible; source timing evidence is not. |
| Tiered50 | NO ACTIVATION PERFORMED | No Tiered50 activation or database write was performed; current active-era status is not re-read because ctl.MddMacSamplingEra is not readable. |

Cadence-equivalent comparisons must exclude the oversampling interval: `Exclude the oversampling interval from cadence-equivalent rate comparisons while preserving its valid source evidence.`

## Recommended analytical grains

| Grain | Definition | Use |
| --- | --- | --- |
| source_observation | one persisted source response observation with UTC capture/observation lineage | coverage, freshness, parser gaps, and response-quality denominators |
| source_event_occurrence | one uniquely identified source event at one stop call, service date, and timing context | timing availability, estimate revisions, and estimated-delay distributions |
| journey_ordered_stop_pair | two ordered stop calls on the same defensibly matched journey and service date | observed estimate change and candidate downstream propagation only |
| provider_situation_association | one provider Situation/Attribute version associated with an observation or affected entity | source-reported disruption evidence, kept separate from inferred propagation |
| sampling_era_window | metric population segmented by sampling era, collection context, and explicit UTC window | cadence-equivalent comparison and confidence interpretation |

## Power BI KPI readiness

No permanent analytical facts were created and Power BI was not modified. The following matrix is a pre-implementation contract; it does not authorize populating a model:

| KPI | Status | Grain | Denominator | Available population | Key limitation | Tiered50 |
| --- | --- | --- | --- | --- | --- | --- |
| monitoring_panel_coverage | PARTIAL | collection_context + sampling_era + monitored target + UTC observation window | Approved monitoring panel denominator is available; network denominator requires GTFS/warehouse coverage. | Panel/audit lineage only | Legacy7 is a limited monitoring panel and cannot support network-wide claims. | YES |
| scheduled_arrival_availability | BLOCKED | unique source event identity + service date + stop call + arrival context | Eligible timing occurrences with a valid event/stop/service-date basis; not available in this readback. | Not available to current login | No live measurement; unavailable is not zero. | NO |
| estimated_arrival_availability | BLOCKED | unique source event identity + service date + stop call + timing context | Eligible timing occurrences after identity and context filters; not available in this readback. | Not available to current login | Estimated arrival is not confirmed actual arrival and must not be relabeled as actual delay. | NO |
| station_estimated_delay_distribution | BLOCKED | station/stop position + unique source event + service date + arrival context | Comparable non-NULL estimate/timetable pairs; not available in this readback. | Not available to current login | Limited Legacy7 panel; no causal or actual-physical-delay interpretation. | YES |
| line_service_estimated_reliability | BLOCKED | line/service + service date + time window + unique source event | Comparable matched event-stop pairs by line/service; not available in this readback. | Not available to current login | Tiered50 or another representative coverage decision is required before network reliability claims. | YES |
| time_of_day_estimated_delay_pattern | BLOCKED | time bucket + monitored station/line + unique source event | Comparable timing pairs observed in each time bucket; not available in this readback. | Not available to current login | Cadence and panel selection can confound apparent time-of-day patterns. | YES |
| repeated_high_delay_location_candidate | BLOCKED | location + service date/window + independent source event | Independent eligible source events and observed windows per location; not available in this readback. | Not available to current login | Candidate investigation signal only; not proof of a location causing delay. | YES |
| consecutive_stop_estimated_delay_increase | BLOCKED | service date + journey identity + consecutive stop pair + observation interval | Matched consecutive-stop pairs with comparable timing evidence; not available in this readback. | Not available to current login | Observed estimate change is not causal propagation or proof of dwell delay. | NO |
| dwell_related_hypothesis | BLOCKED | journey + stop call + consecutive stop pair + service date | Matched pairs with both timing and relevant stop-service evidence; not available in this readback. | Not available to current login | No causal attribution from dwell-related association. | NO |
| candidate_downstream_propagation | BLOCKED | service date + journey identity + ordered stop sequence + source observation interval | Matched journeys with at least two ordered, comparable stop calls; not available in this readback. | Not available to current login | Current readback cannot establish continuity; conclusion is INSUFFICIENT_EVIDENCE. | YES |
| provider_reported_disruption_evidence | BLOCKED | situation identity/version + observation + affected entity + association scope | Persisted source Situation evidence occurrences and linked/unlinked eligible observations; not available in this readback. | Not available to current login | Situation association is not proof that a specific situation caused a specific delay. | NO |
| data_quality_and_evidence_confidence | PARTIAL | metric population + source event/observation + sampling era + quality classification | Current target-audit denominators are partially available; source-event quality denominators are not. | Panel/audit lineage only | Only monitoring-panel audit lineage is directly observed in this readback. | NO |

## Highest-priority gaps and decisions

- Grant a dedicated read-only analytics/review principal access to the required source, historical, GTFS, warehouse, and analytics objects, or run the diagnostic under an approved read-only analyst credential.
- Re-run physical metadata discovery and bounded evidence queries with the approved analyst scope; do not rely on repository DDL as a live schema substitute.
- Measure timing availability, source-event identity ambiguity, call/timing context, parser gaps, Situation/Attribute coverage, and freshness from live rows.
- Validate journey continuity and scheduled stop order before considering any downstream propagation candidate.
- Agree the reporting population and sampling-era denominator before any manager-facing KPI implementation; keep Legacy7 separate from Tiered50 and imported research.

Required decisions before implementation:

- Which approved read-only principal may inspect the authoritative SQL evidence without changing production runtime permissions?
- Which explicit UTC analysis windows and service-date rules govern each KPI?
- What minimum identity/matching quality is required before a consecutive-stop association is shown as a candidate?
- Which KPIs, if any, may be exposed for the limited Legacy7 panel before representative Tiered50 coverage exists?
- Which source-reported Situation fields are reportable as evidence, and which are explicitly not treated as causal attribution?

## Validation boundary

The diagnostic used bounded read-only SQL with the configured login and did not change isolation, indexes, collectors, schedulers, Tiered50, Power BI, historical data, MDD traffic, or GTFS feeds. The JSON artifact contains the full sanitized probe record, including live object-level permission results and observed column definitions where available.

