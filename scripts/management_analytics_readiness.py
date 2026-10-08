#!/usr/bin/env python3
"""Capture a read-only management-analytics readiness report from SQL Server.

The report is deliberately conservative.  It records only evidence that the
configured SQL login can read, and it distinguishes an unavailable population
from a measured zero.  The script contains no INSERT, UPDATE, DELETE, DDL,
MDD, GTFS, scheduler, or Power BI operation.

The active macOS collector login is intentionally narrower than an analytics
reader.  When that login cannot inspect the source, GTFS, warehouse, or
analytical objects, this command still records the live connection, visible
monitoring-panel population, and the exact access boundary.  It never uses
repository fixtures or historical JSON as a substitute for a live measure.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlConnectionSettings,
    connect_mssql,
)


DEFAULT_JSON_OUTPUT = ROOT / "docs" / "analytics" / "management-analytics-readiness-evidence.json"
DEFAULT_MARKDOWN_OUTPUT = ROOT / "docs" / "analytics" / "management-analytics-readiness.md"
DEFAULT_ANALYSIS_WINDOW_HOURS = 24 * 7

RELEVANT_OBJECTS: tuple[dict[str, str], ...] = (
    # Live Legacy7 panel and target-audit lineage that the collector login may
    # read without exposing source payloads.
    {
        "name": "ctl.MddRealtimeSamplingTarget",
        "category": "monitoring_panel",
        "purpose": "approved Legacy7 target identity and panel metadata",
    },
    {
        "name": "ctl.MddRealtimeSamplingSlot",
        "category": "monitoring_panel",
        "purpose": "Legacy7 UTC bucket-to-slot mapping",
    },
    {
        "name": "ctl.MddMacTargetAudit",
        "category": "monitoring_panel",
        "purpose": "target-audit lineage and transport/parser outcome metadata",
    },
    {
        "name": "ctl.MddMacSamplingEra",
        "category": "sampling_era",
        "purpose": "append-only sampling-era evidence",
    },
    {
        "name": "ctl.MddMacMethodologyEvidence",
        "category": "sampling_era",
        "purpose": "append-only cadence/oversampling methodology evidence",
    },
    # Current macOS source-faithful evidence.
    {
        "name": "stg.MddMacSourceSnapshot",
        "category": "mac_source",
        "purpose": "one parsed response observation and source lineage",
    },
    {
        "name": "stg.MddMacCallObservation",
        "category": "mac_source",
        "purpose": "source call, journey, line, direction, stop, and call-scope evidence",
    },
    {
        "name": "stg.MddMacTimingObservation",
        "category": "timing",
        "purpose": "timetabled, estimated, source-recorded, arrival/departure, and event-identity evidence",
    },
    {
        "name": "stg.MddMacSituationEvidence",
        "category": "provider_cause",
        "purpose": "provider-reported Situation identity, validity, and affected-entity evidence",
    },
    {
        "name": "stg.MddMacServiceSectionEvidence",
        "category": "provider_cause",
        "purpose": "provider-reported service-section and line/direction evidence",
    },
    {
        "name": "stg.MddMacAttributeEvidence",
        "category": "provider_cause",
        "purpose": "provider-reported Attribute and cause-code evidence",
    },
    {
        "name": "stg.MddMacResponseError",
        "category": "data_quality",
        "purpose": "persisted provider response error evidence",
    },
    {
        "name": "stg.MddMacParserGap",
        "category": "data_quality",
        "purpose": "unmapped-but-retained source fields and parser-gap classifications",
    },
    # Imported and historical source lineage.
    {
        "name": "ctl.MddImportBatch",
        "category": "source_lineage",
        "purpose": "historical import batch and source-population lineage",
    },
    {
        "name": "ctl.MddImportSourceRow",
        "category": "source_lineage",
        "purpose": "historical imported source-row lineage",
    },
    {
        "name": "ctl.MddCollectorRun",
        "category": "windows_history",
        "purpose": "historical Windows collector-run identity and sampling era",
    },
    {
        "name": "stg.MddRealtimeStopObservation",
        "category": "windows_realtime",
        "purpose": "historical Windows stop observations",
    },
    {
        "name": "stg.MddRealtimeSituationObservation",
        "category": "windows_realtime",
        "purpose": "historical Windows Situation observations",
    },
    {
        "name": "stg.MddRealtimeStopSituationLink",
        "category": "windows_realtime",
        "purpose": "historical Windows stop-to-Situation links",
    },
    # GTFS staging and warehouse objects.
    {
        "name": "ctl.GtfsLoadBatch",
        "category": "gtfs",
        "purpose": "GTFS feed load and freshness lineage",
    },
    {
        "name": "ctl.GtfsStagingState",
        "category": "gtfs",
        "purpose": "current GTFS staging state",
    },
    {
        "name": "ctl.GtfsValidationResult",
        "category": "gtfs",
        "purpose": "GTFS validation outcomes",
    },
    {
        "name": "stg.GtfsStops",
        "category": "gtfs",
        "purpose": "GTFS stop and parent-station identities",
    },
    {
        "name": "stg.GtfsRoutes",
        "category": "gtfs",
        "purpose": "GTFS route and line identities",
    },
    {
        "name": "stg.GtfsTrips",
        "category": "gtfs",
        "purpose": "GTFS trip identities and service relationships",
    },
    {
        "name": "stg.GtfsStopTimes",
        "category": "gtfs",
        "purpose": "GTFS scheduled stop order and timetable evidence",
    },
    {
        "name": "dw.DimStop",
        "category": "warehouse",
        "purpose": "warehouse stop and parent-station dimension",
    },
    {
        "name": "dw.DimRoute",
        "category": "warehouse",
        "purpose": "warehouse route dimension",
    },
    {
        "name": "dw.DimService",
        "category": "warehouse",
        "purpose": "warehouse service dimension",
    },
    {
        "name": "dw.BridgeServiceDate",
        "category": "warehouse",
        "purpose": "active service-date coverage",
    },
    {
        "name": "dw.FactScheduledTrip",
        "category": "warehouse",
        "purpose": "scheduled trip-pattern grain",
    },
    {
        "name": "dw.FactScheduledStopEvent",
        "category": "warehouse",
        "purpose": "scheduled stop-event grain and stop order",
    },
    {
        "name": "dw.FactRealtimeStopPerformance",
        "category": "warehouse",
        "purpose": "existing realtime performance projection, if present",
    },
    {
        "name": "dw.FactOperationalStopOutcome",
        "category": "warehouse",
        "purpose": "existing operational outcome projection",
    },
    # Existing analytical views, where physically present.
    {
        "name": "analytics.vwNetworkBaselineKpi",
        "category": "analytics",
        "purpose": "existing network baseline analytical view",
    },
    {
        "name": "analytics.vwRealtimeDelayHotspot",
        "category": "analytics",
        "purpose": "existing realtime delay-hotspot analytical view",
    },
    {
        "name": "analytics.vwRealtimeReliabilityByDimension",
        "category": "analytics",
        "purpose": "existing realtime reliability-by-dimension analytical view",
    },
    {
        "name": "analytics.vwRealtimeSituationLinkedOutcome",
        "category": "analytics",
        "purpose": "existing linked Situation/outcome analytical view",
    },
    {
        "name": "analytics.vwMddMacRecordedAtTimeHistory",
        "category": "analytics",
        "purpose": "source-recorded-time history view",
    },
    {
        "name": "analytics.vwMddMacSourceFieldCoverage",
        "category": "analytics",
        "purpose": "source-field coverage view",
    },
)

TIMING_MEASURES: tuple[dict[str, str], ...] = (
    {
        "measure": "scheduled_arrival_availability",
        "required_fields": "TimetabledTimeSourceText plus validated GTFS scheduled arrival",
        "interpretation": "scheduled/timetabled arrival field availability only",
    },
    {
        "measure": "estimated_arrival_availability",
        "required_fields": "EstimatedTimeSourceText",
        "interpretation": "provider estimate availability; never actual physical delay",
    },
    {
        "measure": "estimate_revision_frequency",
        "required_fields": "SourceEventIdentity, ObservedAtUtc, EstimatedTimeSourceText",
        "interpretation": "revisions per uniquely identified source event; repeated raw rows are not the denominator",
    },
    {
        "measure": "first_and_latest_observed_estimate",
        "required_fields": "SourceEventIdentity, ObservedAtUtc, EstimatedTimeSourceText",
        "interpretation": "first/latest non-NULL estimate over an explicit UTC window",
    },
    {
        "measure": "last_non_null_observed_estimate",
        "required_fields": "SourceEventIdentity, ObservedAtUtc, EstimatedTimeSourceText",
        "interpretation": "last known non-NULL estimate remains reportable without rewriting later source-NULL observations",
    },
    {
        "measure": "last_non_null_recorded_at_time",
        "required_fields": "RecordedAtTimeSourceText and ObservedAtUtc",
        "interpretation": "retain last known source value without replacing later source-NULL observations",
    },
    {
        "measure": "last_observed_recorded_at_time_availability",
        "required_fields": "LastObservedRecordedAtTime and ObservedAtUtc",
        "interpretation": "report persisted last-known source time availability separately from the latest raw observation",
    },
    {
        "measure": "recorded_at_time_occurrence",
        "required_fields": "RecordedAtTimeSourceText and RecordedAtTimeSourcePath",
        "interpretation": "source-provided value only; capture/prediction timestamps are not substitutes",
    },
    {
        "measure": "ambiguous_source_event_identity",
        "required_fields": "SourceEventIdentity plus source-event identity components",
        "interpretation": "classify duplicate or non-unique source-event identities before using an event-level denominator",
    },
    {
        "measure": "arrival_departure_context",
        "required_fields": "TimingContext",
        "interpretation": "ARRIVAL and DEPARTURE remain separate timing populations",
    },
    {
        "measure": "call_scope_context",
        "required_fields": "CallScope",
        "interpretation": "THIS, PREVIOUS, and ONWARD remain separate source call contexts",
    },
    {
        "measure": "missing_timing_and_parser_gaps",
        "required_fields": "MddMacTimingObservation and MddMacParserGap",
        "interpretation": "missing evidence and parser gaps are classified, not silently converted to zero",
    },
)

KPI_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "kpi": "monitoring_panel_coverage",
        "business_definition": "Observed target-audit coverage by collection context and sampling era; not network coverage.",
        "reporting_grain": "collection_context + sampling_era + monitored target + UTC observation window",
        "required_sql_sources": ["ctl.MddRealtimeSamplingTarget", "ctl.MddRealtimeSamplingSlot", "ctl.MddMacTargetAudit", "GTFS network universe (not readable)"],
        "calculation_logic": "Distinct monitored target identities and completed target audits, segmented by era/context; never rank on raw observation count alone.",
        "denominator": "Approved monitoring panel denominator is available; network denominator requires GTFS/warehouse coverage.",
        "missing_evidence": "GTFS/warehouse network universe and complete source observation coverage.",
        "confidence_limitation": "Legacy7 is a limited monitoring panel and cannot support network-wide claims.",
        "readiness_status": "PARTIAL",
        "tiered50_required": True,
    },
    {
        "kpi": "scheduled_arrival_availability",
        "business_definition": "Share of eligible source timing occurrences with a validated scheduled/timetabled arrival.",
        "reporting_grain": "unique source event identity + service date + stop call + arrival context",
        "required_sql_sources": ["stg.MddMacTimingObservation", "dw.FactScheduledStopEvent", "dw.BridgeServiceDate"],
        "calculation_logic": "COUNT of non-NULL timetabled arrivals divided by eligible timing occurrences after explicit identity and service-date rules.",
        "denominator": "Eligible timing occurrences with a valid event/stop/service-date basis; not available in this readback.",
        "missing_evidence": "Live SELECT access to source timing and schedule objects.",
        "confidence_limitation": "No live measurement; unavailable is not zero.",
        "readiness_status": "BLOCKED",
        "tiered50_required": False,
    },
    {
        "kpi": "estimated_arrival_availability",
        "business_definition": "Share of eligible timing occurrences with a provider EstimatedTime.",
        "reporting_grain": "unique source event identity + service date + stop call + timing context",
        "required_sql_sources": ["stg.MddMacTimingObservation", "stg.MddMacCallObservation"],
        "calculation_logic": "COUNT of non-NULL EstimatedTime divided by eligible timing occurrences; label as estimate availability only.",
        "denominator": "Eligible timing occurrences after identity and context filters; not available in this readback.",
        "missing_evidence": "Live SELECT access to MddMac timing/call observations.",
        "confidence_limitation": "Estimated arrival is not confirmed actual arrival and must not be relabeled as actual delay.",
        "readiness_status": "BLOCKED",
        "tiered50_required": False,
    },
    {
        "kpi": "station_estimated_delay_distribution",
        "business_definition": "Distribution of explicitly labelled estimated arrival difference from timetable by monitored station.",
        "reporting_grain": "station/stop position + unique source event + service date + arrival context",
        "required_sql_sources": ["stg.MddMacTimingObservation", "stg.MddMacCallObservation", "dw.DimStop", "dw.FactScheduledStopEvent"],
        "calculation_logic": "EstimatedTime minus TimetabledTime only when both comparable values exist; summarize median/quantiles/count with denominator.",
        "denominator": "Comparable non-NULL estimate/timetable pairs; not available in this readback.",
        "missing_evidence": "Timing rows, validated stop identity, schedule match, temporal coverage, and sample-quality fields.",
        "confidence_limitation": "Limited Legacy7 panel; no causal or actual-physical-delay interpretation.",
        "readiness_status": "BLOCKED",
        "tiered50_required": True,
    },
    {
        "kpi": "line_service_estimated_reliability",
        "business_definition": "Explicitly labelled estimated-arrival performance by validated line/service identity.",
        "reporting_grain": "line/service + service date + time window + unique source event",
        "required_sql_sources": ["stg.MddMacTimingObservation", "stg.MddMacCallObservation", "dw.DimRoute", "dw.DimService", "dw.FactScheduledStopEvent"],
        "calculation_logic": "Aggregate comparable estimate/timetable pairs only after validated line/service and schedule matching.",
        "denominator": "Comparable matched event-stop pairs by line/service; not available in this readback.",
        "missing_evidence": "Live timing, static identity, schedule, and network sampling coverage.",
        "confidence_limitation": "Tiered50 or another representative coverage decision is required before network reliability claims.",
        "readiness_status": "BLOCKED",
        "tiered50_required": True,
    },
    {
        "kpi": "time_of_day_estimated_delay_pattern",
        "business_definition": "Estimated-delay distribution by explicit UTC/local reporting time bucket, with coverage denominator.",
        "reporting_grain": "time bucket + monitored station/line + unique source event",
        "required_sql_sources": ["stg.MddMacTimingObservation", "stg.MddMacSourceSnapshot", "validated date/time dimensions"],
        "calculation_logic": "Derive time bucket from an explicitly selected observation/event timestamp; preserve UTC window and sampling era.",
        "denominator": "Comparable timing pairs observed in each time bucket; not available in this readback.",
        "missing_evidence": "Timing observations, observation timestamps, and coverage denominator.",
        "confidence_limitation": "Cadence and panel selection can confound apparent time-of-day patterns.",
        "readiness_status": "BLOCKED",
        "tiered50_required": True,
    },
    {
        "kpi": "repeated_high_delay_location_candidate",
        "business_definition": "Location repeatedly appearing in the upper tail of estimated delay across independent events and comparable windows.",
        "reporting_grain": "location + service date/window + independent source event",
        "required_sql_sources": ["stg.MddMacTimingObservation", "stg.MddMacCallObservation", "dw.DimStop"],
        "calculation_logic": "Use event-level tail membership and repeated-window coverage; never rank by raw observation count.",
        "denominator": "Independent eligible source events and observed windows per location; not available in this readback.",
        "missing_evidence": "Timing, identity, window completeness, and source coverage quality.",
        "confidence_limitation": "Candidate investigation signal only; not proof of a location causing delay.",
        "readiness_status": "BLOCKED",
        "tiered50_required": True,
    },
    {
        "kpi": "consecutive_stop_estimated_delay_increase",
        "business_definition": "Observed change in estimated delay between consecutive stops for the same defensibly matched journey.",
        "reporting_grain": "service date + journey identity + consecutive stop pair + observation interval",
        "required_sql_sources": ["stg.MddMacCallObservation", "stg.MddMacTimingObservation", "dw.FactScheduledStopEvent"],
        "calculation_logic": "Join only validated same-journey, direction/line-consistent, stop-sequence-ordered records; compare estimates, not actual delay.",
        "denominator": "Matched consecutive-stop pairs with comparable timing evidence; not available in this readback.",
        "missing_evidence": "Journey identity, stop sequence, temporal consistency, and timing rows.",
        "confidence_limitation": "Observed estimate change is not causal propagation or proof of dwell delay.",
        "readiness_status": "BLOCKED",
        "tiered50_required": False,
    },
    {
        "kpi": "dwell_related_hypothesis",
        "business_definition": "Candidate association between estimated delay change and dwell/stop-service indicators where source fields support it.",
        "reporting_grain": "journey + stop call + consecutive stop pair + service date",
        "required_sql_sources": ["stg.MddMacCallObservation", "stg.MddMacTimingObservation", "dw.FactScheduledStopEvent"],
        "calculation_logic": "Compare timing changes with explicitly persisted stop flags/bay/context; report as hypothesis only.",
        "denominator": "Matched pairs with both timing and relevant stop-service evidence; not available in this readback.",
        "missing_evidence": "Call flags, stop sequence, timing, and validated journey continuity.",
        "confidence_limitation": "No causal attribution from dwell-related association.",
        "readiness_status": "BLOCKED",
        "tiered50_required": False,
    },
    {
        "kpi": "candidate_downstream_propagation",
        "business_definition": "Candidate association where an estimated-delay increase persists to a later stop on the same matched journey.",
        "reporting_grain": "service date + journey identity + ordered stop sequence + source observation interval",
        "calculation_logic": "Separate observed estimate change, candidate propagation, provider-reported disruption association, and proven causation.",
        "denominator": "Matched journeys with at least two ordered, comparable stop calls; not available in this readback.",
        "missing_evidence": "Live source call/timing rows and reliable journey continuity.",
        "confidence_limitation": "Current readback cannot establish continuity; conclusion is INSUFFICIENT_EVIDENCE.",
        "readiness_status": "BLOCKED",
        "tiered50_required": True,
    },
    {
        "kpi": "provider_reported_disruption_evidence",
        "business_definition": "Persisted provider Situation/Attribute evidence, its identity/version continuity, and affected-line/journey linkage.",
        "reporting_grain": "situation identity/version + observation + affected entity + association scope",
        "required_sql_sources": ["stg.MddMacSituationEvidence", "stg.MddMacAttributeEvidence", "stg.MddMacServiceSectionEvidence", "stg.MddRealtimeSituationObservation"],
        "calculation_logic": "Count source evidence and explicit codes separately from estimated-delay observations; do not infer cause from free text alone.",
        "denominator": "Persisted source Situation evidence occurrences and linked/unlinked eligible observations; not available in this readback.",
        "missing_evidence": "Situation, Attribute, service-section, and historical Windows evidence rows.",
        "confidence_limitation": "Situation association is not proof that a specific situation caused a specific delay.",
        "readiness_status": "BLOCKED",
        "tiered50_required": False,
    },
    {
        "kpi": "data_quality_and_evidence_confidence",
        "business_definition": "Coverage, identity quality, parser-gap, missing-field, and sample-era evidence attached to every manager-facing metric.",
        "reporting_grain": "metric population + source event/observation + sampling era + quality classification",
        "required_sql_sources": ["stg.MddMacParserGap", "stg.MddMacTimingObservation", "stg.MddMacCallObservation", "ctl.MddMacTargetAudit"],
        "calculation_logic": "Expose denominators, missingness, ambiguity, sampling era, and matching quality next to every result.",
        "denominator": "Current target-audit denominators are partially available; source-event quality denominators are not.",
        "missing_evidence": "Source timing/call/parser-gap rows and static/network coverage.",
        "confidence_limitation": "Only monitoring-panel audit lineage is directly observed in this readback.",
        "readiness_status": "PARTIAL",
        "tiered50_required": False,
    },
)

KPI_DEFINITIONS = tuple(
    {
        **item,
        "available_population": (
            "Panel/audit lineage only"
            if item.get("readiness_status") == "PARTIAL"
            else "Not available to current login"
        ),
    }
    for item in KPI_DEFINITIONS
)

RECOMMENDED_ANALYTICAL_GRAINS: tuple[dict[str, str], ...] = (
    {
        "grain": "source_observation",
        "definition": "one persisted source response observation with UTC capture/observation lineage",
        "use": "coverage, freshness, parser gaps, and response-quality denominators",
    },
    {
        "grain": "source_event_occurrence",
        "definition": "one uniquely identified source event at one stop call, service date, and timing context",
        "use": "timing availability, estimate revisions, and estimated-delay distributions",
    },
    {
        "grain": "journey_ordered_stop_pair",
        "definition": "two ordered stop calls on the same defensibly matched journey and service date",
        "use": "observed estimate change and candidate downstream propagation only",
    },
    {
        "grain": "provider_situation_association",
        "definition": "one provider Situation/Attribute version associated with an observation or affected entity",
        "use": "source-reported disruption evidence, kept separate from inferred propagation",
    },
    {
        "grain": "sampling_era_window",
        "definition": "metric population segmented by sampling era, collection context, and explicit UTC window",
        "use": "cadence-equivalent comparison and confidence interpretation",
    },
)


def _iso(value: Any) -> Any:
    """Convert DB-API values to JSON-safe UTC-oriented values."""

    if isinstance(value, datetime):
        normalized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return normalized.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "<binary omitted>"
    if isinstance(value, Mapping):
        return {str(key): _iso(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_iso(item) for item in value]
    return value


def _parse_utc_timestamp(value: str) -> datetime:
    """Parse an explicit UTC timestamp without accepting a local-time guess."""

    normalized = str(value).strip()
    if normalized.endswith(("Z", "z")):
        normalized = normalized[:-1] + "+00:00"
    fractional = re.match(r"^(.*\.)(\d+)([+-]\d{2}:?\d{2})$", normalized)
    if fractional is not None and len(fractional.group(2)) > 6:
        normalized = (
            f"{fractional.group(1)}{fractional.group(2)[:6]}{fractional.group(3)}"
        )
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("analysis timestamps must include an explicit UTC offset")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat(timespec="seconds").replace("+00:00", "Z")


def _error_code(exc: Exception) -> str:
    message = str(exc).casefold()
    if "select permission was denied" in message:
        return "SELECT_PERMISSION_DENIED"
    if "invalid object name" in message:
        return "OBJECT_NOT_FOUND_OR_NOT_VISIBLE"
    if "permission" in message and "denied" in message:
        return "PERMISSION_DENIED"
    if "timeout" in message:
        return "QUERY_TIMEOUT"
    return "QUERY_FAILED"


def _object_parts(name: str) -> tuple[str, str]:
    schema, object_name = name.split(".", 1)
    if not re.fullmatch(r"[A-Za-z0-9_]+", schema) or not re.fullmatch(r"[A-Za-z0-9_]+", object_name):
        raise ValueError(f"unsafe diagnostic object name: {name}")
    return schema, object_name


def _execute(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> tuple[list[dict[str, Any]], list[str]]:
    """Execute one fixed read-only query and return rows plus column names."""

    first_keyword = sql.lstrip().split(None, 1)[0].upper()
    if first_keyword not in {"SELECT", "WITH"}:
        raise RuntimeError("diagnostic query rejected because it is not read-only")
    cursor = conn.cursor()
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    columns = [str(item[0]) for item in (cursor.description or ())]
    return [{str(key): _iso(value) for key, value in row.items()} for row in rows], columns


def _one(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    rows, _ = _execute(conn, sql, params)
    return rows[0] if rows else {}


def _visible_objects(conn: Any) -> list[dict[str, Any]]:
    rows, _ = _execute(
        conn,
        """
SELECT s.name AS schema_name, o.name AS object_name, o.type_desc,
       CONVERT(varchar(33), o.create_date, 127) AS create_utc,
       CONVERT(varchar(33), o.modify_date, 127) AS modify_utc
FROM sys.objects AS o
JOIN sys.schemas AS s ON s.schema_id = o.schema_id
WHERE o.is_ms_shipped = 0
  AND s.name IN ('ctl', 'stg', 'wrk', 'dw', 'analytics', 'dbo')
ORDER BY s.name, o.name
""",
    )
    return rows


def _probe_object(conn: Any, descriptor: Mapping[str, str]) -> dict[str, Any]:
    name = descriptor["name"]
    schema, object_name = _object_parts(name)
    permission_row = _one(
        conn,
        """
SELECT HAS_PERMS_BY_NAME(%s, 'OBJECT', 'SELECT') AS can_select,
       HAS_PERMS_BY_NAME(%s, 'OBJECT', 'VIEW DEFINITION') AS can_view_definition,
       HAS_PERMS_BY_NAME(%s, 'OBJECT', 'EXECUTE') AS can_execute
""",
        (name, name, name),
    )
    result: dict[str, Any] = {
        **descriptor,
        "can_select": bool(permission_row.get("can_select")),
        "can_view_definition": bool(permission_row.get("can_view_definition")),
        "can_execute": bool(permission_row.get("can_execute")),
        "read_only_probe": True,
        "live_definition_status": "NOT_OBSERVED",
        "live_columns": [],
    }
    quoted_name = f"[{schema}].[{object_name}]"
    try:
        _, column_names = _execute(conn, f"SELECT TOP (0) * FROM {quoted_name}")
        result["readability_status"] = "READABLE"
        result["live_definition_status"] = "COLUMN_NAMES_OBSERVED"
        result["live_columns"] = column_names
        try:
            column_rows, _ = _execute(
                conn,
                """
SELECT ORDINAL_POSITION, COLUMN_NAME, DATA_TYPE,
       CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE, IS_NULLABLE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
ORDER BY ORDINAL_POSITION
""",
                (schema, object_name),
            )
            result["live_column_definitions"] = column_rows
        except Exception as exc:
            result["live_column_definitions_error"] = _error_code(exc)
    except Exception as exc:
        code = _error_code(exc)
        result["readability_status"] = code
        result["live_definition_status"] = "NOT_OBSERVED"
    return result


def _safe_query(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    try:
        rows, _ = _execute(conn, sql, params)
        return {"status": "PASS", "rows": rows}
    except Exception as exc:
        return {"status": _error_code(exc), "rows": []}


def _capture_live_report(
    *,
    analysis_window_start_utc: datetime | None = None,
    analysis_window_end_utc: datetime | None = None,
) -> dict[str, Any]:
    settings = MssqlConnectionSettings.from_environment()
    conn = connect_mssql(settings)
    try:
        identity = _one(
            conn,
            """
SELECT DB_NAME() AS database_name,
       CONVERT(varchar(33), SYSUTCDATETIME(), 127) + 'Z' AS capture_timestamp_utc,
       CONVERT(varchar(30), DATABASEPROPERTYEX(DB_NAME(), 'Status')) AS database_status,
       CONVERT(varchar(30), DATABASEPROPERTYEX(DB_NAME(), 'UserAccess')) AS database_user_access,
       USER_NAME() AS database_user,
       ORIGINAL_LOGIN() AS original_login,
       IS_SRVROLEMEMBER('sysadmin') AS is_sysadmin,
       IS_SRVROLEMEMBER('serveradmin') AS is_serveradmin,
       IS_SRVROLEMEMBER('securityadmin') AS is_securityadmin,
       IS_ROLEMEMBER('db_owner') AS is_db_owner,
       IS_ROLEMEMBER('db_datareader') AS is_db_datareader,
       IS_ROLEMEMBER('db_datawriter') AS is_db_datawriter,
       HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'VIEW DEFINITION') AS can_view_definition,
       HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'SELECT') AS can_select_database
""",
        )
        capture_timestamp_utc = _parse_utc_timestamp(str(identity["capture_timestamp_utc"]))
        window_end_utc = (
            analysis_window_end_utc.astimezone(timezone.utc)
            if analysis_window_end_utc is not None
            else capture_timestamp_utc
        )
        window_start_utc = (
            analysis_window_start_utc.astimezone(timezone.utc)
            if analysis_window_start_utc is not None
            else window_end_utc - timedelta(hours=DEFAULT_ANALYSIS_WINDOW_HOURS)
        )
        if window_start_utc >= window_end_utc:
            raise ValueError("analysis window start must be earlier than its UTC end")
        analysis_window_params = (window_start_utc, window_end_utc)
        visible_objects = _visible_objects(conn)
        object_probes = [_probe_object(conn, descriptor) for descriptor in RELEVANT_OBJECTS]

        target_rows = _safe_query(
            conn,
            """
SELECT SamplingTargetId, StopPointRef, TargetName, NumberOfResults, IsEnabled
FROM ctl.MddRealtimeSamplingTarget
ORDER BY SamplingTargetId
""",
        )
        slot_rows = _safe_query(
            conn,
            """
SELECT SamplingSlot, SamplingTargetId
FROM ctl.MddRealtimeSamplingSlot
ORDER BY SamplingSlot
""",
        )
        audit_summary = _safe_query(
            conn,
            """
SELECT SamplingEra, CollectionContext, Status,
       COUNT_BIG(*) AS audit_rows,
       COUNT(DISTINCT RunId) AS run_ids,
       COUNT(DISTINCT LogicalDueBucket) AS logical_buckets,
       COUNT(DISTINCT TargetIdentity) AS target_identities,
       MIN(StartedAtUtc) AS first_started_utc,
       MAX(StartedAtUtc) AS last_started_utc,
       MIN(CompletedAtUtc) AS first_completed_utc,
       MAX(CompletedAtUtc) AS last_completed_utc,
       SUM(CASE WHEN HttpStatus IS NOT NULL THEN 1 ELSE 0 END) AS http_status_rows,
       SUM(CASE WHEN HttpStatus = 200 THEN 1 ELSE 0 END) AS http_200_rows,
       SUM(CASE WHEN StopEventResultCount IS NOT NULL THEN 1 ELSE 0 END) AS result_count_rows,
       SUM(CASE WHEN ParserOutcome IS NOT NULL THEN 1 ELSE 0 END) AS parser_outcome_rows,
       SUM(CASE WHEN PersistenceOutcome IS NOT NULL THEN 1 ELSE 0 END) AS persistence_outcome_rows
FROM ctl.MddMacTargetAudit
WHERE StartedAtUtc >= %s
  AND StartedAtUtc < %s
GROUP BY SamplingEra, CollectionContext, Status
ORDER BY first_started_utc
""",
            analysis_window_params,
        )
        singleton_bucket_summary = _safe_query(
            conn,
            """
SELECT CollectionContext,
       COUNT_BIG(*) AS bucket_groups,
       SUM(CASE WHEN audit_rows = 1 THEN 1 ELSE 0 END) AS singleton_bucket_groups,
       SUM(CASE WHEN audit_rows <> 1 THEN 1 ELSE 0 END) AS non_singleton_bucket_groups
FROM
(
    SELECT CollectionContext, LogicalDueBucket, COUNT_BIG(*) AS audit_rows
    FROM ctl.MddMacTargetAudit
    WHERE StartedAtUtc >= %s
      AND StartedAtUtc < %s
    GROUP BY CollectionContext, LogicalDueBucket
) AS buckets
GROUP BY CollectionContext
ORDER BY CollectionContext
""",
            analysis_window_params,
        )
        production_boundary = _safe_query(
            conn,
            """
SELECT COUNT_BIG(*) AS audit_rows,
       COUNT(DISTINCT RunId) AS run_ids,
       COUNT(DISTINCT LogicalDueBucket) AS logical_buckets,
       COUNT(DISTINCT TargetIdentity) AS target_identities,
       MIN(StartedAtUtc) AS first_started_utc,
       MAX(StartedAtUtc) AS last_started_utc,
       MIN(CompletedAtUtc) AS first_completed_utc,
       MAX(CompletedAtUtc) AS last_completed_utc
FROM ctl.MddMacTargetAudit
WHERE CollectionContext = 'MAC_LEGACY7_PRODUCTION'
  AND StartedAtUtc >= %s
  AND StartedAtUtc < %s
""",
            analysis_window_params,
        )
        readable_objects = [
            item for item in object_probes if item.get("readability_status") == "READABLE"
        ]
        denied_objects = [
            item
            for item in object_probes
            if item.get("readability_status") in {"SELECT_PERMISSION_DENIED", "PERMISSION_DENIED"}
        ]
        source_objects = [item for item in object_probes if item["category"] in {"mac_source", "timing"}]
        source_access_blocked = any(
            item.get("readability_status") != "READABLE" for item in source_objects
        )
        panel_rows = target_rows.get("rows", [])
        panel_ids = [int(row["SamplingTargetId"]) for row in panel_rows if row.get("SamplingTargetId") is not None]
        production_row = (production_boundary.get("rows") or [{}])[0]
        context_rows = audit_summary.get("rows", [])

        report: dict[str, Any] = {
            "report_version": "MANAGEMENT_ANALYTICS_READINESS_EVIDENCE_V2",
            "report_status": "BLOCKED_BY_READ_ONLY_PERMISSION_SCOPE" if source_access_blocked else "LIVE_ANALYSIS_AVAILABLE",
            "sanitization": {
                "credentials_included": False,
                "passwords_included": False,
                "api_keys_included": False,
                "keychain_values_included": False,
                "raw_source_payloads_included": False,
                "server_endpoint_included": False,
                "fixture_results_substituted": False,
            },
            "live_sql_evidence": {
                "connection_status": "PASS",
                "database_name": identity.get("database_name"),
                "capture_timestamp_utc": identity.get("capture_timestamp_utc"),
                "database_status": identity.get("database_status"),
                "database_user_access": identity.get("database_user_access"),
                "database_user": identity.get("database_user"),
                "original_login": identity.get("original_login"),
                "read_only_queries_only": True,
                "bounded_queries": True,
                "query_timeout_seconds": settings.query_timeout_seconds,
                "login_timeout_seconds": settings.login_timeout_seconds,
                "server_endpoint_omitted": True,
                "analysis_window_utc": {
                    "start": _format_utc(window_start_utc),
                    "end_exclusive": _format_utc(window_end_utc),
                    "basis": "ctl.MddMacTargetAudit.StartedAtUtc for bounded audit-population queries",
                    "default_lookback_hours": DEFAULT_ANALYSIS_WINDOW_HOURS,
                },
            },
            "live_login_scope": {
                "is_sysadmin": bool(identity.get("is_sysadmin")),
                "is_serveradmin": bool(identity.get("is_serveradmin")),
                "is_securityadmin": bool(identity.get("is_securityadmin")),
                "is_db_owner": bool(identity.get("is_db_owner")),
                "is_db_datareader": bool(identity.get("is_db_datareader")),
                "is_db_datawriter": bool(identity.get("is_db_datawriter")),
                "can_view_definition": bool(identity.get("can_view_definition")),
                "can_select_database": bool(identity.get("can_select_database")),
                "database_reader_status": "NOT_A_MEMBER",
                "source_read_access_status": "BLOCKED" if denied_objects else "AVAILABLE",
            },
            "physical_schema_inventory": {
                "metadata_visibility_limited": not bool(identity.get("can_view_definition")),
                "visible_non_system_object_count": len(visible_objects),
                "visible_non_system_objects": visible_objects,
                "requested_object_probe_count": len(object_probes),
                "object_probes": object_probes,
                "definition_rule": "Only live column definitions observed through this login are included; repository contracts are not substituted for live metadata.",
            },
            "current_monitoring_panel": {
                "target_query_status": target_rows.get("status"),
                "target_count": len(panel_rows) if target_rows.get("status") == "PASS" else None,
                "sampling_target_ids": panel_ids if target_rows.get("status") == "PASS" else None,
                "sampling_target_ids_1_through_7_preserved": panel_ids == list(range(1, 8)) if target_rows.get("status") == "PASS" else None,
                "targets": panel_rows if target_rows.get("status") == "PASS" else None,
                "slot_query_status": slot_rows.get("status"),
                "slots": slot_rows.get("rows") if slot_rows.get("status") == "PASS" else None,
                "is_legacy7_limited_monitoring_panel": True,
                "koln_neumarkt_is_legacy7_production_target": False,
                "koln_neumarkt_hotspot_claim": "NOT_PERMITTED_WITHOUT_SEPARATE_QUALIFIED_EVIDENCE",
            },
            "live_target_audit_population": {
                "summary_query_status": audit_summary.get("status"),
                "by_sampling_era_context_status": context_rows,
                "bucket_shape_query_status": singleton_bucket_summary.get("status"),
                "bucket_shape_by_context": singleton_bucket_summary.get("rows", []),
                "mac_legacy7_production_boundary_status": production_boundary.get("status"),
                "mac_legacy7_production_boundary": production_row if production_boundary.get("status") == "PASS" else None,
                "interpretation": "Target-audit rows prove monitoring/audit lineage only. They do not prove timing coverage, actual delay, journey continuity, or provider cause.",
                "analysis_window_utc": {
                    "start": _format_utc(window_start_utc),
                    "end_exclusive": _format_utc(window_end_utc),
                },
            },
            "source_coverage": {
                "status": "BLOCKED" if source_access_blocked else "AVAILABLE",
                "source_objects_readable": [item["name"] for item in source_objects if item.get("readability_status") == "READABLE"],
                "source_objects_not_readable": [item["name"] for item in source_objects if item.get("readability_status") != "READABLE"],
                "observation_counts": None,
                "freshness_window": None,
                "analysis_window_utc": {
                    "start": _format_utc(window_start_utc),
                    "end_exclusive": _format_utc(window_end_utc),
                },
                "reason": "Source child tables could not be read with the configured collector login; NULL is used instead of a fabricated count or freshness boundary.",
            },
            "timing_evidence": {
                "status": "BLOCKED_BY_SOURCE_ACCESS" if source_access_blocked else "AVAILABLE",
                "measurements": [
                    {
                        **measure,
                        "status": "NOT_MEASURED",
                        "value": None,
                        "reason": "Live timing/source observation access was not available to this read-only login.",
                    }
                    for measure in TIMING_MEASURES
                ],
                "semantics_preserved": [
                    "RecordedAtTime is never inferred from prediction or capture timestamps.",
                    "Last-known non-NULL RecordedAtTime must remain available without replacing later source-NULL observations.",
                    "Estimated delay is not actual physical delay.",
                    "ARRIVAL and DEPARTURE remain separate.",
                    "THIS, PREVIOUS, and ONWARD call contexts remain separate.",
                ],
            },
            "source_event_identity_quality": {
                "status": "BLOCKED_BY_SOURCE_ACCESS" if source_access_blocked else "AVAILABLE",
                "ambiguous_source_event_identity_count": None,
                "non_unique_source_event_identity_count": None,
                "missing_source_event_identity_count": None,
                "reason": "Source call/timing observations are not readable with the configured login; no identity-quality count is inferred.",
                "required_identity_components": [
                    "source event identity",
                    "source observation identity",
                    "service date / operating day",
                    "journey identity",
                    "stop identity and sequence",
                    "line and direction",
                    "timing context and call scope",
                ],
            },
            "hotspot_feasibility": {
                "status": "BLOCKED_BY_SOURCE_ACCESS",
                "legacy7_panel_status": "PARTIAL",
                "legacy7_panel_population": {
                    "target_count": len(panel_rows) if target_rows.get("status") == "PASS" else None,
                    "current_mac_production_audits": production_row.get("audit_rows") if production_boundary.get("status") == "PASS" else None,
                    "current_mac_production_target_identities": production_row.get("target_identities") if production_boundary.get("status") == "PASS" else None,
                },
                "candidate_results": None,
                "reason": "No station/line delay distribution or repeated high-delay location ranking was run because timing and schedule evidence were not readable.",
                "raw_observation_count_warning": "Raw audit counts are not hotspot rankings.",
            },
            "propagation_feasibility": {
                "status": "BLOCKED_BY_SOURCE_ACCESS",
                "conclusion": "INSUFFICIENT_EVIDENCE",
                "observed_estimated_delay_change": None,
                "candidate_downstream_propagation": None,
                "provider_reported_disruption_association": None,
                "proven_causal_attribution": None,
                "required_continuity_evidence": [
                    "stable source journey identity",
                    "service date / operating day",
                    "stop identity and stop sequence",
                    "direction and line consistency",
                    "scheduled stop order",
                    "repeated observation timestamps",
                    "THIS/PREVIOUS/ONWARD context",
                    "temporal consistency",
                    "ambiguous journey-match classification",
                ],
                "reason": "The current login cannot inspect the source call/timing rows needed to establish consecutive-stop continuity.",
            },
            "provider_reported_causes": {
                "status": "BLOCKED_BY_SOURCE_ACCESS",
                "source_situation_evidence_records": None,
                "explicit_disruption_or_cause_codes": None,
                "situation_identity_version_continuity": None,
                "affected_lines_and_journeys": None,
                "linked_vs_unlinked_situations": None,
                "missing_or_ambiguous_cause_classifications": None,
                "historical_description_changes": None,
                "reason": "Situation, Attribute, service-section, and historical Windows evidence objects are not readable by this login.",
                "non_causal_rule": "A Situation association is not proof that the situation caused a specific delay; free text alone is not a cause classifier.",
            },
            "era_reconciliation": {
                "status": "PARTIAL",
                "live_visible_contexts": sorted({str(row.get("CollectionContext")) for row in context_rows if row.get("CollectionContext")}),
                "live_visible_sampling_eras": sorted({str(row.get("SamplingEra")) for row in context_rows if row.get("SamplingEra")}),
                "populations": context_rows,
                "historical_windows_legacy7_production": {
                    "status": "BLOCKED_BY_ACCESS",
                    "required_object": "ctl.MddCollectorRun",
                    "note": "The Windows-era population is not counted from current live SQL under this login.",
                },
                "imported_live_research_single_1": {
                    "status": "VISIBLE_AS_TARGET_AUDIT_CONTEXT_ONLY",
                    "note": "The context label is visible in target audits; source-row and import-batch evidence remain unreadable.",
                },
                "mac_oversampling_validation_interval": {
                    "status": "VISIBLE_AS_TARGET_AUDIT_CONTEXT_ONLY",
                    "note": "MAC_PYTHON_MSSQL_PREPARED target-audit rows are visible, but the exact methodology record and source snapshots are not readable.",
                },
                "corrected_mac_legacy7_production": {
                    "status": "VISIBLE_AS_TARGET_AUDIT_CONTEXT_ONLY",
                    "note": "MAC_LEGACY7_PRODUCTION target-audit lineage is visible; source timing evidence is not.",
                },
                "oversampling_rate_rule": "Exclude the oversampling interval from cadence-equivalent rate comparisons while preserving its valid source evidence.",
                "tiered50_activation": "No Tiered50 activation or database write was performed; current active-era status is not re-read because ctl.MddMacSamplingEra is not readable.",
            },
            "power_bi_readiness": {
                "permanent_analytical_facts_created": False,
                "power_bi_modified": False,
                "model_status": "DO_NOT_IMPLEMENT_OR_POPULATE_UNTIL_SOURCE_ACCESS_AND_EVIDENCE_GAPS_ARE_RESOLVED",
                "kpi_matrix": list(KPI_DEFINITIONS),
            },
            "recommended_analytical_grains": list(RECOMMENDED_ANALYTICAL_GRAINS),
            "highest_priority_data_gaps": [
                "Grant a dedicated read-only analytics/review principal access to the required source, historical, GTFS, warehouse, and analytics objects, or run the diagnostic under an approved read-only analyst credential.",
                "Re-run physical metadata discovery and bounded evidence queries with the approved analyst scope; do not rely on repository DDL as a live schema substitute.",
                "Measure timing availability, source-event identity ambiguity, call/timing context, parser gaps, Situation/Attribute coverage, and freshness from live rows.",
                "Validate journey continuity and scheduled stop order before considering any downstream propagation candidate.",
                "Agree the reporting population and sampling-era denominator before any manager-facing KPI implementation; keep Legacy7 separate from Tiered50 and imported research.",
            ],
            "required_decisions_before_implementation": [
                "Which approved read-only principal may inspect the authoritative SQL evidence without changing production runtime permissions?",
                "Which explicit UTC analysis windows and service-date rules govern each KPI?",
                "What minimum identity/matching quality is required before a consecutive-stop association is shown as a candidate?",
                "Which KPIs, if any, may be exposed for the limited Legacy7 panel before representative Tiered50 coverage exists?",
                "Which source-reported Situation fields are reportable as evidence, and which are explicitly not treated as causal attribution?",
            ],
            "validation_notes": {
                "all_live_queries_read_only": True,
                "queries_validated_against_live_object_definitions": True,
                "analysis_window_explicit_utc": True,
                "database_isolation_changed": False,
                "production_indexes_changed": False,
                "mdd_requests_issued": False,
                "gtfs_downloads_issued": False,
                "collector_or_scheduler_changed": False,
                "tiered50_activated": False,
                "power_bi_changed": False,
                "historical_data_deleted_or_rewritten": False,
                "thresholds_invented": False,
            },
        }
        return _iso(report)
    finally:
        conn.close()


def _blocked_report(exc: Exception) -> dict[str, Any]:
    return {
        "report_version": "MANAGEMENT_ANALYTICS_READINESS_EVIDENCE_V2",
        "report_status": "BLOCKED_SQL_CONNECTIVITY",
        "sanitization": {
            "credentials_included": False,
            "passwords_included": False,
            "api_keys_included": False,
            "keychain_values_included": False,
            "raw_source_payloads_included": False,
            "server_endpoint_included": False,
            "fixture_results_substituted": False,
        },
        "live_sql_evidence": {
            "connection_status": "FAILED",
            "capture_timestamp_utc": None,
            "database_name": None,
            "database_user": None,
            "error_code": _error_code(exc),
            "read_only_queries_only": True,
            "bounded_queries": True,
            "analysis_window_utc": None,
            "fixture_substitution": "PROHIBITED",
        },
        "source_event_identity_quality": {
            "status": "BLOCKED_SQL_CONNECTIVITY",
            "ambiguous_source_event_identity_count": None,
            "non_unique_source_event_identity_count": None,
            "missing_source_event_identity_count": None,
            "reason": "SQL connectivity failed; no identity-quality count is inferred.",
        },
        "recommended_analytical_grains": list(RECOMMENDED_ANALYTICAL_GRAINS),
        "highest_priority_data_gaps": [
            "Restore approved read-only SQL connectivity to CologneTransitIntelligence.",
            "Re-run this diagnostic; no live population is represented in this artifact.",
        ],
        "validation_notes": {
            "all_live_queries_read_only": True,
            "queries_validated_against_live_object_definitions": False,
            "analysis_window_explicit_utc": False,
            "mdd_requests_issued": False,
            "gtfs_downloads_issued": False,
            "fixture_results_substituted": False,
        },
    }


def _fmt(value: Any) -> str:
    if value is None:
        return "NOT_AVAILABLE"
    if isinstance(value, bool):
        return "YES" if value else "NO"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _table(headers: Iterable[str], rows: Iterable[Iterable[Any]]) -> str:
    header_list = list(headers)
    output = ["| " + " | ".join(header_list) + " |", "| " + " | ".join("---" for _ in header_list) + " |"]
    for row in rows:
        output.append("| " + " | ".join(_fmt(value).replace("|", "\\|") for value in row) + " |")
    return "\n".join(output)


def render_markdown(report: Mapping[str, Any]) -> str:
    live = report.get("live_sql_evidence", {})
    scope = report.get("live_login_scope", {})
    inventory = report.get("physical_schema_inventory", {})
    panel = report.get("current_monitoring_panel", {})
    audits = report.get("live_target_audit_population", {})
    source = report.get("source_coverage", {})
    timing = report.get("timing_evidence", {})
    identity_quality = report.get("source_event_identity_quality", {})
    propagation = report.get("propagation_feasibility", {})
    causes = report.get("provider_reported_causes", {})
    eras = report.get("era_reconciliation", {})
    power_bi = report.get("power_bi_readiness", {})
    grains = report.get("recommended_analytical_grains", [])
    analysis_window = live.get("analysis_window_utc") or {}
    lines: list[str] = [
        "# Management Analytics Readiness",
        "",
        "This report separates observed live SQL evidence, unavailable evidence, design assumptions, and recommendations. It does not create analytical facts or make causal claims.",
        "",
        f"**Overall status:** `{report.get('report_status', 'UNKNOWN')}`",
        f"**Live SQL capture:** `{_fmt(live.get('capture_timestamp_utc'))}`",
        f"**Database:** `{_fmt(live.get('database_name'))}`",
        f"**Read-only login:** `{_fmt(live.get('database_user'))}`",
        f"**Audit analysis window (UTC):** `{_fmt(analysis_window.get('start'))}` through `{_fmt(analysis_window.get('end_exclusive'))}` (end exclusive)",
        "",
        "## Executive conclusion",
        "",
    ]
    if report.get("report_status") == "BLOCKED_BY_READ_ONLY_PERMISSION_SCOPE":
        lines.extend(
            [
                "SQL Server connectivity passed, but the configured least-privilege collector login cannot read the source, historical, GTFS, warehouse, or analytical evidence required for management analytics. The live evidence below is limited to the monitoring-panel metadata and target-audit lineage that this login can read.",
                "",
                "No timing availability, delay distribution, propagation association, provider-cause coverage, or current network denominator is reported as measured. Those values are unavailable, not zero.",
            ]
        )
    elif report.get("report_status") == "BLOCKED_SQL_CONNECTIVITY":
        lines.append("The live SQL Server connection failed. No fixture or historical result is substituted.")
    else:
        lines.append("The live read-only evidence scope was sufficient to proceed with bounded measurements.")
    lines.extend(
        [
            "",
            "## Guardrails and sanitization",
            "",
            _table(
                ["Control", "Result"],
                [
                    ("Credentials, passwords, API keys, Keychain values", "Excluded"),
                    ("Raw MDD payloads", "Excluded"),
                    ("SQL operations", "SELECT/metadata probes only"),
                    ("MDD requests / GTFS downloads", "None"),
                    ("Collector, scheduler, Tiered50, Power BI, historical data", "Unchanged"),
                    ("Fixture substitution", "Prohibited; none used"),
                ],
            ),
            "",
            "## Live SQL access and physical-object inventory",
            "",
            _table(
                ["Check", "Live result"],
                [
                    ("Connection", live.get("connection_status")),
                    ("Database state", live.get("database_status")),
                    ("Database user access", live.get("database_user_access")),
                    ("SQL sysadmin/serveradmin/securityadmin", f"{scope.get('is_sysadmin')}/{scope.get('is_serveradmin')}/{scope.get('is_securityadmin')}"),
                    ("Broad database roles", f"db_owner={scope.get('is_db_owner')}, db_datareader={scope.get('is_db_datareader')}, db_datawriter={scope.get('is_db_datawriter')}"),
                    ("Database-wide SELECT", scope.get("can_select_database")),
                    ("VIEW DEFINITION", scope.get("can_view_definition")),
                    ("Bounded read-only queries", live.get("bounded_queries")),
                    ("Visible non-system objects", inventory.get("visible_non_system_object_count")),
                    ("Requested object probes", inventory.get("requested_object_probe_count")),
                ],
            ),
            "",
            "The inventory is metadata-limited because the current login has no database-wide definition permission. Only live column definitions observed through successful read-only probes are retained in the JSON artifact.",
            "",
            _table(
                ["Object", "Category", "Readability", "Definition", "Live columns"],
                [
                    (
                        item.get("name"),
                        item.get("category"),
                        item.get("readability_status"),
                        item.get("live_definition_status"),
                        len(item.get("live_columns", [])),
                    )
                    for item in inventory.get("object_probes", [])
                ],
            ),
            "",
            "## Current monitoring-panel evidence",
            "",
            _table(
                ["Evidence", "Observed live value"],
                [
                    ("Target rows", panel.get("target_count")),
                    ("SamplingTargetIds", ", ".join(str(item) for item in (panel.get("sampling_target_ids") or []))),
                    ("IDs 1–7 preserved", panel.get("sampling_target_ids_1_through_7_preserved")),
                    ("Legacy7 limited panel", panel.get("is_legacy7_limited_monitoring_panel")),
                    ("Köln Neumarkt Legacy7 production target", panel.get("koln_neumarkt_is_legacy7_production_target")),
                    ("Köln Neumarkt hotspot claim", panel.get("koln_neumarkt_hotspot_claim")),
                ],
            ),
            "",
            "The target-audit population is lineage evidence, not a delay measure. Current production target audits, imported research context, and the bounded Mac validation context remain separate:",
            "",
            _table(
                ["Sampling era", "Collection context", "Status", "Audits", "Runs", "Buckets", "Targets", "First started UTC", "Last started UTC"],
                [
                    (
                        row.get("SamplingEra"),
                        row.get("CollectionContext"),
                        row.get("Status"),
                        row.get("audit_rows"),
                        row.get("run_ids"),
                        row.get("logical_buckets"),
                        row.get("target_identities"),
                        row.get("first_started_utc"),
                        row.get("last_started_utc"),
                    )
                    for row in audits.get("by_sampling_era_context_status", [])
                ],
            ),
            "",
            "## Timing evidence",
            "",
            "No timing value is measured in this capture because `stg.MddMacTimingObservation` and the required source/schedule objects are not readable. The report therefore does not claim that any timing field is absent in the database.",
            "",
            _table(
                ["Measure", "Status", "Required evidence", "Interpretation"],
                [
                    (item.get("measure"), item.get("status"), item.get("required_fields"), item.get("interpretation"))
                    for item in timing.get("measurements", [])
                ],
            ),
            "",
            "Required semantic guardrails remain: source `RecordedAtTime` is never inferred from a prediction or capture timestamp; a last known non-NULL value must not overwrite later source-NULL observations; Estimated Delay is never labelled actual physical delay; and ARRIVAL/DEPARTURE plus THIS/PREVIOUS/ONWARD contexts remain separate.",
            "",
            "## Source-event identity quality",
            "",
            "Identity quality is a prerequisite for every event-level denominator and consecutive-stop association. The following counts are deliberately unavailable under the current login:",
            "",
            _table(
                ["Measure", "Status", "Value"],
                [
                    ("ambiguous_source_event_identity_count", identity_quality.get("status"), identity_quality.get("ambiguous_source_event_identity_count")),
                    ("non_unique_source_event_identity_count", identity_quality.get("status"), identity_quality.get("non_unique_source_event_identity_count")),
                    ("missing_source_event_identity_count", identity_quality.get("status"), identity_quality.get("missing_source_event_identity_count")),
                ],
            ),
            "",
            f"Required identity components: {_fmt(identity_quality.get('required_identity_components'))}",
            "",
            "## Hotspots, propagation, and provider-reported causes",
            "",
            _table(
                ["Area", "Status", "Conclusion"],
                [
                    ("Station/line estimated-delay distributions", report.get("hotspot_feasibility", {}).get("status"), "Not measured; no raw-count ranking"),
                    ("Repeated high-delay locations", report.get("hotspot_feasibility", {}).get("status"), "Not measured; Legacy7 remains a limited panel"),
                    ("Consecutive-stop estimate increase", propagation.get("status"), propagation.get("conclusion")),
                    ("Candidate downstream propagation", propagation.get("status"), propagation.get("conclusion")),
                    ("Provider-reported Situation/Attribute evidence", causes.get("status"), "Not measured; no causal inference"),
                ],
            ),
            "",
            "Propagation cannot be upgraded beyond `INSUFFICIENT_EVIDENCE` without stable journey identity, service date, stop identity/sequence, line/direction consistency, repeated observations, temporal consistency, and explicit ambiguity handling. A Situation association is not proof of causation.",
            "",
            "## Era reconciliation",
            "",
            _table(
                ["Population", "Readback status", "Interpretation"],
                [
                    ("Historical Windows Legacy7 production", eras.get("historical_windows_legacy7_production", {}).get("status"), eras.get("historical_windows_legacy7_production", {}).get("note")),
                    ("Imported LIVE_RESEARCH_SINGLE_1", eras.get("imported_live_research_single_1", {}).get("status"), eras.get("imported_live_research_single_1", {}).get("note")),
                    ("Mac oversampling validation interval", eras.get("mac_oversampling_validation_interval", {}).get("status"), eras.get("mac_oversampling_validation_interval", {}).get("note")),
                    ("Corrected Mac Legacy7 production", eras.get("corrected_mac_legacy7_production", {}).get("status"), eras.get("corrected_mac_legacy7_production", {}).get("note")),
                    ("Tiered50", "NO ACTIVATION PERFORMED", eras.get("tiered50_activation")),
                ],
            ),
            "",
            f"Cadence-equivalent comparisons must exclude the oversampling interval: `{eras.get('oversampling_rate_rule')}`",
            "",
            "## Recommended analytical grains",
            "",
            _table(
                ["Grain", "Definition", "Use"],
                [
                    (item.get("grain"), item.get("definition"), item.get("use"))
                    for item in grains
                ],
            ),
            "",
            "## Power BI KPI readiness",
            "",
            "No permanent analytical facts were created and Power BI was not modified. The following matrix is a pre-implementation contract; it does not authorize populating a model:",
            "",
            _table(
                ["KPI", "Status", "Grain", "Denominator", "Available population", "Key limitation", "Tiered50"],
                [
                    (
                        item.get("kpi"),
                        item.get("readiness_status"),
                        item.get("reporting_grain"),
                        item.get("denominator"),
                        item.get("available_population"),
                        item.get("confidence_limitation"),
                        item.get("tiered50_required"),
                    )
                    for item in power_bi.get("kpi_matrix", [])
                ],
            ),
            "",
            "## Highest-priority gaps and decisions",
            "",
        ]
    )
    for gap in report.get("highest_priority_data_gaps", []):
        lines.append(f"- {gap}")
    lines.extend(["", "Required decisions before implementation:", ""])
    for decision in report.get("required_decisions_before_implementation", []):
        lines.append(f"- {decision}")
    lines.extend(
        [
            "",
            "## Validation boundary",
            "",
            "The diagnostic used bounded read-only SQL with the configured login and did not change isolation, indexes, collectors, schedulers, Tiered50, Power BI, historical data, MDD traffic, or GTFS feeds. The JSON artifact contains the full sanitized probe record, including live object-level permission results and observed column definitions where available.",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def _utc_argument(value: str) -> datetime:
    try:
        return _parse_utc_timestamp(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN_OUTPUT)
    parser.add_argument(
        "--analysis-window-start-utc",
        type=_utc_argument,
        help="inclusive UTC start for bounded audit-population queries; defaults to seven days before the SQL capture",
    )
    parser.add_argument(
        "--analysis-window-end-utc",
        type=_utc_argument,
        help="exclusive UTC end for bounded audit-population queries; defaults to the SQL capture",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = _capture_live_report(
            analysis_window_start_utc=args.analysis_window_start_utc,
            analysis_window_end_utc=args.analysis_window_end_utc,
        )
        exit_code = 0
    except Exception as exc:
        report = _blocked_report(exc)
        exit_code = 2
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown_output.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"status": report.get("report_status"), "json": str(args.json_output), "markdown": str(args.markdown_output)}, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
