#!/usr/bin/env python3
"""Stage 9 matching and data-quality foundation.

The module is deliberately source-first and offline-capable.  It provides the
deterministic identity bridges, indexed stop-event candidate generation,
confidence decisions, lineage checks, and bounded validation used by the
Stage 9 implementation.  It does not poll either realtime source and it does
not build the Stage 10 operational fact.

Important identity rule:

    MDD JourneyRef != GTFS-RT trip_id

Source identifiers are retained in their own namespaces.  A canonical
service event is reached through validated stop/date/time/line evidence; a
string that happens to be equal in two providers is never used as a match by
itself.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


SCRIPT_VERSION = "stage-09-matching-quality-1.0"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
STAGE4_EVIDENCE_PATH = REPOSITORY_ROOT / "docs" / "stages" / "04-cologne-scope-mode-evidence.json"
COLOGNE_STOP_PREFIX = "de:05315:"

SOURCE_MDD = "MDD_TRIAS"
SOURCE_GTFS_RT = "GTFS_RT"
SOURCE_STATIC_GTFS = "VRS_GTFS"

MATCH_HIGH = "HIGH"
MATCH_MEDIUM = "MEDIUM"
MATCH_LOW = "LOW"
MATCH_UNMATCHED = "UNMATCHED"

METHOD_EXACT_STOP_DATE_TIME_DIRECTION = "EXACT_STOP_DATE_TIME_DIRECTION"
METHOD_EXACT_STOP_DATE_TIME_SEQUENCE = "EXACT_STOP_DATE_TIME_SEQUENCE"
METHOD_MULTI_STOP_SEQUENCE_MATCH = "MULTI_STOP_SEQUENCE_MATCH"
METHOD_ROUTE_TIME_CONTEXT_ONLY = "ROUTE_TIME_CONTEXT_ONLY"
METHOD_STOP_TIME_CONTEXT_ONLY = "STOP_TIME_CONTEXT_ONLY"
METHOD_UNMATCHED = "UNMATCHED"

QUALITY_VALIDATED_MATCH = "VALIDATED_MATCH"
QUALITY_CONTEXTUAL_MATCH = "CONTEXTUAL_MATCH"
QUALITY_WEAK_CONTEXT_MATCH = "WEAK_CONTEXT_MATCH"
QUALITY_AMBIGUOUS_MATCH = "AMBIGUOUS_MATCH"
QUALITY_UNMATCHED = "UNMATCHED"
QUALITY_MISSING_REQUIRED_IDENTITY = "MISSING_REQUIRED_IDENTITY"
QUALITY_STATIC_COVERAGE_GAP = "STATIC_COVERAGE_GAP"
QUALITY_SOURCE_IDENTITY_UNRESOLVED = "SOURCE_IDENTITY_UNRESOLVED"
QUALITY_SOURCE_LINE_IDENTITY_UNRESOLVED = "SOURCE_LINE_IDENTITY_UNRESOLVED"
QUALITY_SOURCE_LINE_DISAGREEMENT = "SOURCE_LINE_DISAGREEMENT"
QUALITY_INVALID_SERVICE_DATE = "INVALID_SERVICE_DATE"
QUALITY_INVALID_SCHEDULE_TIME = "INVALID_SCHEDULE_TIME"
QUALITY_LINEAGE_INCOMPLETE = "LINEAGE_INCOMPLETE"
QUALITY_DUPLICATE_SOURCE_OBSERVATION = "DUPLICATE_SOURCE_OBSERVATION"
QUALITY_CROSS_SOURCE_DISAGREEMENT = "CROSS_SOURCE_DISAGREEMENT"
QUALITY_OUT_OF_CORE_SCOPE = "OUT_OF_CORE_SCOPE"

VALIDATED_MAPPING = "VALIDATED"
UNRESOLVED_MAPPING = "UNRESOLVED"
AMBIGUOUS_MAPPING = "AMBIGUOUS"

EXPECTED_RAILWAY_HBF_STATUS = "UNRESOLVED_RAILWAY_HBF_IDENTITY"


def _present(value: Any) -> bool:
    """Return whether a source value is present without treating False as NULL."""

    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
        return int(value.strip())
    return None


def _normalized_namespace(value: Any) -> str:
    return _text(value) or "UNKNOWN_NAMESPACE"


def _normalized_source_system(value: Any) -> str:
    return (_text(value) or "UNKNOWN_SOURCE").upper()


def _normalized_key(value: Any) -> str:
    return str(value).strip().casefold()


def is_cologne_stop_id(stop_id: Optional[str]) -> bool:
    """Apply the approved direct VRS Global-ID Cologne stop rule."""

    return bool(stop_id and stop_id.startswith(COLOGNE_STOP_PREFIX))


def normalize_service_date(value: Any) -> Optional[str]:
    """Normalize a service date without deriving it from observation time.

    A date-like ISO value is accepted, as is GTFS ``YYYYMMDD``.  Datetime
    offsets are not converted: a caller must supply the source's service-date
    semantics explicitly.  Returning ``None`` is intentional evidence of
    missing or invalid date identity.
    """

    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = _text(value)
    if not text:
        return None
    if re.fullmatch(r"\d{8}", text):
        try:
            return datetime.strptime(text, "%Y%m%d").date().isoformat()
        except ValueError:
            return None
    match = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
    if match:
        try:
            return date.fromisoformat(match.group(1)).isoformat()
        except ValueError:
            return None
    return None


def schedule_time_seconds(value: Any) -> Optional[int]:
    """Return service-day seconds for GTFS/TRIAS schedule-time evidence.

    GTFS extended hours are retained rather than wrapped at midnight.  For an
    ISO timestamp the lexical local time component is used; timezone conversion
    is intentionally outside this helper because a source-to-static timezone
    alignment must be an explicit mapping decision.
    """

    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        if int(value) == value and value >= 0:
            return int(value)
        return None
    text = _text(value)
    if not text:
        return None
    time_match = re.search(r"(?:T|\s)(\d{1,3}):(\d{2}):(\d{2})(?:[.,](\d+))?", text)
    if time_match is None:
        time_match = re.fullmatch(r"(\d{1,3}):(\d{2}):(\d{2})(?:[.,](\d+))?", text)
    if time_match is None:
        return None
    hour, minute, second = (int(time_match.group(index)) for index in (1, 2, 3))
    if minute > 59 or second > 59:
        return None
    return hour * 3600 + minute * 60 + second


def normalize_event_type(value: Any) -> Optional[str]:
    text = _text(value)
    if not text:
        return None
    upper = text.upper()
    if upper in {"ARRIVAL", "ARRIVE", "A"}:
        return "ARRIVAL"
    if upper in {"DEPARTURE", "DEPART", "D"}:
        return "DEPARTURE"
    return None


def _hash_identity(parts: Sequence[Any]) -> str:
    payload = "\x1f".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IdentityMapping:
    """One explicitly evidenced source identifier to canonical identifier row."""

    source_system: str
    source_namespace: str
    source_identifier: str
    canonical_identifier: Optional[str]
    mapping_version: str
    status: str = VALIDATED_MAPPING
    method: str = "EXPLICIT_EVIDENCE"
    confidence: str = MATCH_HIGH
    evidence_ref: Optional[str] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    notes: Optional[str] = None

    def key(self) -> Tuple[str, str, str]:
        return (
            _normalized_source_system(self.source_system),
            _normalized_key(self.source_namespace),
            self.source_identifier,
        )


@dataclass(frozen=True)
class IdentityResolution:
    canonical_identifier: Optional[str]
    status: str
    candidates: Tuple[str, ...] = ()
    mapping_methods: Tuple[str, ...] = ()
    evidence_refs: Tuple[str, ...] = ()


class IdentityBridge:
    """Versioned source-to-canonical bridge with explicit ambiguity handling."""

    def __init__(
        self,
        *,
        stop_mappings: Iterable[IdentityMapping] = (),
        line_mappings: Iterable[IdentityMapping] = (),
        active_mapping_version: Optional[str] = None,
    ) -> None:
        self.stop_mappings = tuple(stop_mappings)
        self.line_mappings = tuple(line_mappings)
        self.active_mapping_version = active_mapping_version
        self._stop_index = self._index(self.stop_mappings)
        self._line_index = self._index(self.line_mappings)

    @staticmethod
    def _index(mappings: Iterable[IdentityMapping]) -> Dict[Tuple[str, str, str], List[IdentityMapping]]:
        index: Dict[Tuple[str, str, str], List[IdentityMapping]] = defaultdict(list)
        for mapping in mappings:
            index[mapping.key()].append(mapping)
        return dict(index)

    @staticmethod
    def _resolve(
        index: Mapping[Tuple[str, str, str], Sequence[IdentityMapping]],
        source_system: str,
        source_namespace: str,
        source_identifier: Optional[str],
        mapping_version: Optional[str] = None,
    ) -> IdentityResolution:
        if not _present(source_identifier):
            return IdentityResolution(None, "MISSING_SOURCE_IDENTIFIER")
        key = (
            _normalized_source_system(source_system),
            _normalized_key(source_namespace),
            str(source_identifier),
        )
        rows = [
            row
            for row in index.get(key, ())
            if mapping_version is None or row.mapping_version == mapping_version
        ]
        if not rows:
            return IdentityResolution(None, "NOT_FOUND")
        candidates = tuple(sorted({row.canonical_identifier for row in rows if row.canonical_identifier}))
        valid_rows = [row for row in rows if row.status == VALIDATED_MAPPING and row.canonical_identifier]
        methods = tuple(sorted({row.method for row in rows if row.method}))
        evidence = tuple(sorted({row.evidence_ref for row in rows if row.evidence_ref}))
        if len(candidates) > 1:
            return IdentityResolution(None, "AMBIGUOUS", candidates, methods, evidence)
        if len(valid_rows) == 1 or (len(valid_rows) > 1 and len(candidates) == 1):
            return IdentityResolution(candidates[0], "RESOLVED", candidates, methods, evidence)
        if candidates:
            return IdentityResolution(None, "UNRESOLVED", candidates, methods, evidence)
        return IdentityResolution(None, "UNRESOLVED", (), methods, evidence)

    def resolve_stop(
        self,
        source_system: str,
        source_namespace: str,
        source_identifier: Optional[str],
        mapping_version: Optional[str] = None,
    ) -> IdentityResolution:
        return self._resolve(
            self._stop_index,
            source_system,
            source_namespace,
            source_identifier,
            mapping_version or self.active_mapping_version,
        )

    def resolve_line(
        self,
        source_system: str,
        source_namespace: str,
        source_identifier: Optional[str],
        mapping_version: Optional[str] = None,
    ) -> IdentityResolution:
        return self._resolve(
            self._line_index,
            source_system,
            source_namespace,
            source_identifier,
            mapping_version or self.active_mapping_version,
        )

    def validation_report(self) -> Dict[str, Any]:
        def report_rows(rows: Sequence[IdentityMapping]) -> Dict[str, Any]:
            by_key: Dict[Tuple[str, str, str], List[IdentityMapping]] = defaultdict(list)
            for row in rows:
                by_key[row.key()].append(row)
            conflicts = []
            unresolved = []
            for key, values in sorted(by_key.items()):
                candidate_ids = sorted({item.canonical_identifier for item in values if item.canonical_identifier})
                if len(candidate_ids) > 1:
                    conflicts.append(
                        {
                            "source_identity": list(key),
                            "candidate_canonical_ids": candidate_ids,
                            "classification": "AMBIGUOUS_MAPPING",
                        }
                    )
                if not any(item.status == VALIDATED_MAPPING and item.canonical_identifier for item in values):
                    unresolved.append({"source_identity": list(key), "classification": UNRESOLVED_MAPPING})
            return {
                "mapping_count": len(rows),
                "distinct_source_identity_count": len(by_key),
                "ambiguous_source_identity_count": len(conflicts),
                "unresolved_source_identity_count": len(unresolved),
                "ambiguous_examples": conflicts[:10],
                "unresolved_examples": unresolved[:10],
            }

        active_filter = self.active_mapping_version
        stop_rows = (
            [row for row in self.stop_mappings if row.mapping_version == active_filter]
            if active_filter
            else self.stop_mappings
        )
        line_rows = (
            [row for row in self.line_mappings if row.mapping_version == active_filter]
            if active_filter
            else self.line_mappings
        )
        stop_report = report_rows(stop_rows)
        line_report = report_rows(line_rows)
        # Ambiguous/unresolved rows are expected for the bounded railway Hbf
        # bridge.  A conflict is never promoted to a resolved mapping.
        status = "PASS" if not stop_report["ambiguous_source_identity_count"] and not line_report["ambiguous_source_identity_count"] else "FAIL"
        return {"status": status, "stops": stop_report, "lines": line_report}


@dataclass(frozen=True)
class SourceEventObservation:
    """One source-faithful observed stop event, including collector lineage."""

    source_system: str
    source_namespace: str
    source_event_id: Optional[str]
    observation_id: Optional[str]
    observed_at: Optional[str]
    service_date: Any
    source_stop_id: Optional[str]
    source_line_id: Optional[str]
    source_trip_id: Optional[str]
    source_journey_ref: Optional[str]
    event_type: Optional[str]
    scheduled_time: Any
    direction: Optional[str] = None
    stop_sequence: Optional[int] = None
    operator_id: Optional[str] = None
    canonical_stop_id: Optional[str] = None
    canonical_line_id: Optional[str] = None
    response_id: Optional[str] = None
    collector_run_id: Optional[str] = None
    sampling_policy_id: Optional[str] = None
    sampling_era_id: Optional[str] = None
    correlation_group_id: Optional[str] = None
    candidate_identifiers: Mapping[str, Any] = field(default_factory=dict)
    source_paths: Mapping[str, Any] = field(default_factory=dict)
    source_attributes: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SourceEventObservation":
        source_system = _normalized_source_system(
            value.get("source_system") or value.get("source")
        )
        source_namespace = _normalized_namespace(
            value.get("source_namespace") or value.get("namespace") or source_system
        )
        candidate_identifiers: Dict[str, Any] = dict(value.get("candidate_identifiers") or {})
        source_paths: Dict[str, Any] = dict(value.get("source_paths") or {})
        rich_fields = (
            "vehicle_journey_ref",
            "dated_vehicle_journey_ref",
            "affected_vehicle_journey",
            "vehicle_journeys",
            "operating_days_description",
            "direction",
            "origins",
            "destinations",
        )
        for field_name in rich_fields:
            if field_name in value and _present(value.get(field_name)):
                candidate_identifiers.setdefault(
                    field_name,
                    {
                        "value": value[field_name],
                        "source_path": value.get(f"{field_name}_source_path"),
                    },
                )
                source_paths.setdefault(field_name, value.get(f"{field_name}_source_path"))

        event_id = _text(
            value.get("source_event_id")
            or value.get("event_source_identity")
            or value.get("source_event_identity")
        )
        observation_id = _text(
            value.get("observation_id")
            or value.get("source_observation_id")
            or value.get("stop_event_observation_id")
        )
        scheduled_time = (
            value.get("scheduled_time")
            if "scheduled_time" in value
            else value.get("timetabled_time", value.get("scheduled_time_source"))
        )
        service_date = value.get(
            "service_date",
            value.get("operating_day_ref", value.get("start_date")),
        )
        source_stop_id = _text(
            value.get("source_stop_id")
            or value.get("stop_point_ref")
            or value.get("stop_id")
        )
        source_line_id = _text(
            value.get("source_line_id")
            or value.get("line_ref")
            or value.get("route_id")
        )
        source_trip_id = _text(value.get("source_trip_id") or value.get("trip_id"))
        source_journey_ref = _text(
            value.get("source_journey_ref") or value.get("journey_ref")
        )
        return cls(
            source_system=source_system,
            source_namespace=source_namespace,
            source_event_id=event_id,
            observation_id=observation_id,
            observed_at=_text(value.get("observed_at") or value.get("observed_at_utc")),
            service_date=service_date,
            source_stop_id=source_stop_id,
            source_line_id=source_line_id,
            source_trip_id=source_trip_id,
            source_journey_ref=source_journey_ref,
            event_type=value.get("event_type") or value.get("arrival_departure_context"),
            scheduled_time=scheduled_time,
            direction=_text(value.get("direction") or value.get("direction_ref")),
            stop_sequence=_safe_int(value.get("stop_sequence", value.get("stop_seq_number"))),
            operator_id=_text(value.get("operator_id") or value.get("operator_ref")),
            canonical_stop_id=_text(value.get("canonical_stop_id")),
            canonical_line_id=_text(value.get("canonical_line_id")),
            response_id=_text(value.get("response_id")),
            collector_run_id=_text(value.get("collector_run_id") or value.get("pilot_run_id")),
            sampling_policy_id=_text(value.get("sampling_policy_id") or value.get("sampling_policy")),
            sampling_era_id=_text(value.get("sampling_era_id") or value.get("sampling_era")),
            correlation_group_id=_text(value.get("correlation_group_id")),
            candidate_identifiers=candidate_identifiers,
            source_paths=source_paths,
            source_attributes=dict(value.get("source_attributes") or {}),
        )

    @property
    def normalized_service_date(self) -> Optional[str]:
        return normalize_service_date(self.service_date)

    @property
    def normalized_event_type(self) -> Optional[str]:
        return normalize_event_type(self.event_type)

    @property
    def scheduled_time_seconds(self) -> Optional[int]:
        return schedule_time_seconds(self.scheduled_time)

    @property
    def source_identity(self) -> Optional[str]:
        if not self.source_event_id:
            return None
        return f"{self.source_system}|{self.source_namespace}|{self.source_event_id}"

    def candidate_identifier_value(self, name: str) -> Any:
        value = self.candidate_identifiers.get(name)
        if isinstance(value, Mapping) and "value" in value:
            return value.get("value")
        return value


def _missing_lineage_fields(observation: SourceEventObservation) -> List[str]:
    """Return required observation-lineage fields that are absent.

    The matcher may still retain a source observation with incomplete lineage,
    but it must not describe that decision as a fully validated historical
    match.  This keeps data-quality review separate from candidate generation.
    """

    required = {
        "source_event_id": observation.source_event_id,
        "observation_id": observation.observation_id,
        "observed_at": observation.observed_at,
        "collector_run_id": observation.collector_run_id,
        "sampling_policy_id": observation.sampling_policy_id,
        "sampling_era_id": observation.sampling_era_id,
    }
    return [field_name for field_name, value in required.items() if not _present(value)]


@dataclass(frozen=True)
class StaticServiceEvent:
    """One date-expanded static stop event used as matching evidence."""

    feed_snapshot_key: str
    source_namespace: str
    source_trip_id: Optional[str]
    source_route_id: Optional[str]
    source_stop_id: Optional[str]
    canonical_stop_id: Optional[str]
    canonical_line_id: Optional[str]
    service_date: Any
    event_type: Any
    scheduled_time: Any
    direction: Optional[str] = None
    stop_sequence: Optional[int] = None
    operator_id: Optional[str] = None
    source_row_identity: Optional[str] = None
    sequence_signature: Tuple[Tuple[str, int, int], ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "StaticServiceEvent":
        return cls(
            feed_snapshot_key=str(value.get("feed_snapshot_key") or value.get("feed_snapshot") or "UNKNOWN_FEED"),
            source_namespace=_normalized_namespace(value.get("source_namespace") or SOURCE_STATIC_GTFS),
            source_trip_id=_text(value.get("source_trip_id") or value.get("trip_id")),
            source_route_id=_text(value.get("source_route_id") or value.get("route_id")),
            source_stop_id=_text(value.get("source_stop_id") or value.get("stop_id")),
            canonical_stop_id=_text(value.get("canonical_stop_id")),
            canonical_line_id=_text(value.get("canonical_line_id")),
            service_date=value.get("service_date"),
            event_type=value.get("event_type"),
            scheduled_time=value.get("scheduled_time", value.get("scheduled_time_source")),
            direction=_text(value.get("direction") or value.get("direction_id")),
            stop_sequence=_safe_int(value.get("stop_sequence")),
            operator_id=_text(value.get("operator_id") or value.get("agency_id")),
            source_row_identity=_text(value.get("source_row_identity") or value.get("source_row_number")),
            sequence_signature=tuple(value.get("sequence_signature") or ()),
        )


@dataclass(frozen=True)
class CanonicalServiceEvent:
    canonical_service_event_id: str
    feed_snapshot_key: str
    source_namespace: str
    source_trip_id: Optional[str]
    source_route_id: Optional[str]
    source_stop_id: Optional[str]
    canonical_stop_id: str
    canonical_line_id: Optional[str]
    service_date: str
    event_type: str
    scheduled_time_seconds: int
    scheduled_time_source: Any
    direction: Optional[str]
    stop_sequence: Optional[int]
    operator_id: Optional[str]
    source_row_identity: Optional[str]
    sequence_signature: Tuple[Tuple[str, int, int], ...] = ()


@dataclass(frozen=True)
class MatchDecision:
    source_event_identity: Optional[str]
    source_observation_identity: Optional[str]
    source_system: str
    source_namespace: str
    canonical_service_event_id: Optional[str]
    match_confidence: str
    match_method: str
    data_quality_classification: str
    candidate_count: int
    candidate_event_ids: Tuple[str, ...]
    selected_score: Optional[int]
    evidence: Mapping[str, Any]
    matching_version: str = SCRIPT_VERSION

    @property
    def matched(self) -> bool:
        return self.canonical_service_event_id is not None

    def to_record(self) -> Dict[str, Any]:
        result = asdict(self)
        result["candidate_event_ids"] = list(self.candidate_event_ids)
        result["matched"] = self.matched
        return result


class ScheduleIndex:
    """Deterministic indexed lookup over canonical service events.

    The exact-key and sorted time indexes avoid scanning every event for each
    realtime observation.  The index is additive: a new station, feed
    snapshot, or sampling era adds rows without changing the matching rule.
    """

    def __init__(self, events: Sequence[CanonicalServiceEvent], issues: Sequence[Dict[str, Any]]) -> None:
        self.events = tuple(events)
        self.issues = tuple(issues)
        self.by_id = {event.canonical_service_event_id: event for event in events}
        self.by_exact: Dict[Tuple[str, str, str, int], List[str]] = defaultdict(list)
        self.by_prefix: Dict[Tuple[str, str, str], List[Tuple[int, str]]] = defaultdict(list)
        for event in events:
            exact_key = (
                event.service_date,
                event.canonical_stop_id,
                event.event_type,
                event.scheduled_time_seconds,
            )
            prefix = exact_key[:3]
            self.by_exact[exact_key].append(event.canonical_service_event_id)
            self.by_prefix[prefix].append((event.scheduled_time_seconds, event.canonical_service_event_id))
        for values in self.by_exact.values():
            values.sort()
        for values in self.by_prefix.values():
            values.sort()

    def exact_candidates(
        self,
        service_date: str,
        canonical_stop_id: str,
        event_type: str,
        scheduled_seconds: int,
    ) -> List[CanonicalServiceEvent]:
        ids = self.by_exact.get((service_date, canonical_stop_id, event_type, scheduled_seconds), [])
        return [self.by_id[event_id] for event_id in ids]

    def nearby_candidates(
        self,
        service_date: str,
        canonical_stop_id: str,
        event_type: str,
        scheduled_seconds: int,
        tolerance_seconds: int,
    ) -> List[CanonicalServiceEvent]:
        prefix = (service_date, canonical_stop_id, event_type)
        values = self.by_prefix.get(prefix, [])
        left = bisect.bisect_left(values, (scheduled_seconds - tolerance_seconds, ""))
        right = bisect.bisect_right(values, (scheduled_seconds + tolerance_seconds, "\uffff"))
        return [self.by_id[event_id] for _seconds, event_id in values[left:right]]

    def query_count(self) -> int:
        return len(self.events)


def _canonical_event_id(event: StaticServiceEvent, service_date: str, event_type: str, seconds: int) -> str:
    digest = _hash_identity(
        (
            event.feed_snapshot_key,
            service_date,
            event.source_namespace,
            event.source_trip_id,
            event.source_stop_id,
            event.source_row_identity,
            event.stop_sequence,
            event_type,
            seconds,
        )
    )
    return f"CSE|{event.feed_snapshot_key}|{digest}"


def build_schedule_index(events: Iterable[StaticServiceEvent | Mapping[str, Any]]) -> ScheduleIndex:
    """Build canonical date-expanded events and the scalable lookup indexes."""

    canonical: List[CanonicalServiceEvent] = []
    issues: List[Dict[str, Any]] = []
    duplicate_keys: Counter[Tuple[Any, ...]] = Counter()
    for ordinal, raw_event in enumerate(events):
        event = raw_event if isinstance(raw_event, StaticServiceEvent) else StaticServiceEvent.from_mapping(raw_event)
        service_date = normalize_service_date(event.service_date)
        event_type = normalize_event_type(event.event_type)
        seconds = schedule_time_seconds(event.scheduled_time)
        if service_date is None:
            issues.append({"classification": QUALITY_INVALID_SERVICE_DATE, "ordinal": ordinal})
            continue
        if event_type is None:
            issues.append({"classification": QUALITY_MISSING_REQUIRED_IDENTITY, "ordinal": ordinal, "field": "event_type"})
            continue
        if seconds is None:
            issues.append({"classification": QUALITY_INVALID_SCHEDULE_TIME, "ordinal": ordinal})
            continue
        if not event.canonical_stop_id:
            issues.append({"classification": QUALITY_STATIC_COVERAGE_GAP, "ordinal": ordinal, "field": "canonical_stop_id"})
            continue
        duplicate_key = (
            event.feed_snapshot_key,
            service_date,
            event.source_namespace,
            event.source_trip_id,
            event.source_stop_id,
            event.stop_sequence,
            event_type,
        )
        duplicate_keys[duplicate_key] += 1
        if duplicate_keys[duplicate_key] > 1:
            issues.append(
                {
                    "classification": "DUPLICATE_STATIC_EVENT_IDENTITY",
                    "duplicate_key": list(duplicate_key),
                    "ordinal": ordinal,
                }
            )
        source_row_identity = event.source_row_identity or f"DERIVED_ORDINAL_{ordinal}"
        if event.source_row_identity is None:
            issues.append(
                {
                    "classification": QUALITY_MISSING_REQUIRED_IDENTITY,
                    "ordinal": ordinal,
                    "field": "source_row_identity",
                    "detail": "derived ordinal is not source evidence",
                }
            )
        canonical.append(
            CanonicalServiceEvent(
                canonical_service_event_id=_canonical_event_id(event, service_date, event_type, seconds),
                feed_snapshot_key=event.feed_snapshot_key,
                source_namespace=event.source_namespace,
                source_trip_id=event.source_trip_id,
                source_route_id=event.source_route_id,
                source_stop_id=event.source_stop_id,
                canonical_stop_id=event.canonical_stop_id,
                canonical_line_id=event.canonical_line_id,
                service_date=service_date,
                event_type=event_type,
                scheduled_time_seconds=seconds,
                scheduled_time_source=event.scheduled_time,
                direction=event.direction,
                stop_sequence=event.stop_sequence,
                operator_id=event.operator_id,
                source_row_identity=source_row_identity,
                sequence_signature=event.sequence_signature,
            )
        )
    return ScheduleIndex(canonical, issues)


def _string_equal(left: Any, right: Any) -> bool:
    return _present(left) and _present(right) and _normalized_key(left) == _normalized_key(right)


def _candidate_evidence(
    observation: SourceEventObservation,
    event: CanonicalServiceEvent,
    line_resolution: IdentityResolution,
    *,
    exact_time: bool,
) -> Dict[str, Any]:
    line_match = bool(
        line_resolution.canonical_identifier
        and event.canonical_line_id
        and line_resolution.canonical_identifier == event.canonical_line_id
    )
    direction_match = _string_equal(observation.direction, event.direction)
    sequence_match = (
        observation.stop_sequence is not None
        and event.stop_sequence is not None
        and observation.stop_sequence == event.stop_sequence
    )
    operator_match = _string_equal(observation.operator_id, event.operator_id)
    same_namespace_trip_match = bool(
        observation.source_trip_id
        and event.source_trip_id
        and observation.source_system == SOURCE_STATIC_GTFS
        and _normalized_key(observation.source_namespace) == _normalized_key(event.source_namespace)
        and observation.source_trip_id == event.source_trip_id
    )
    multi_stop_match = bool(
        observation.source_attributes.get("sequence_context")
        and event.sequence_signature
        and tuple(observation.source_attributes.get("sequence_context")) == event.sequence_signature
    )
    prohibited_identity_equality = bool(
        observation.source_journey_ref
        and event.source_trip_id
        and observation.source_journey_ref == event.source_trip_id
        and (
            observation.source_system != SOURCE_STATIC_GTFS
            or _normalized_key(observation.source_namespace) != _normalized_key(event.source_namespace)
        )
    )
    score = 4 + 3 + 2 + (4 if exact_time else 1)
    for matched, weight in (
        (line_match, 2),
        (direction_match, 1),
        (sequence_match, 2),
        (operator_match, 1),
        (same_namespace_trip_match, 3),
        (multi_stop_match, 3),
    ):
        if matched:
            score += weight
    return {
        "canonical_stop_match": True,
        "service_date_match": True,
        "event_type_match": True,
        "scheduled_time_match": exact_time,
        "source_trip_id": observation.source_trip_id,
        "source_journey_ref": observation.source_journey_ref,
        "line_match": line_match,
        "direction_match": direction_match,
        "stop_sequence_match": sequence_match,
        "operator_match": operator_match,
        "same_namespace_trip_identity_match": same_namespace_trip_match,
        "multi_stop_sequence_match": multi_stop_match,
        "prohibited_cross_source_identity_equality_observed": prohibited_identity_equality,
        "source_trip_identity_used": same_namespace_trip_match,
        "score": score,
    }


def _decision_for_failure(
    observation: SourceEventObservation,
    classification: str,
    *,
    detail: Optional[str] = None,
    candidate_ids: Sequence[str] = (),
    matching_version: str = SCRIPT_VERSION,
) -> MatchDecision:
    evidence: Dict[str, Any] = {"detail": detail} if detail else {}
    return MatchDecision(
        source_event_identity=observation.source_identity,
        source_observation_identity=observation.observation_id,
        source_system=observation.source_system,
        source_namespace=observation.source_namespace,
        canonical_service_event_id=None,
        match_confidence=MATCH_UNMATCHED,
        match_method=METHOD_UNMATCHED,
        data_quality_classification=classification,
        candidate_count=len(candidate_ids),
        candidate_event_ids=tuple(candidate_ids),
        selected_score=None,
        evidence=evidence,
        matching_version=matching_version,
    )


def match_source_event(
    observation: SourceEventObservation | Mapping[str, Any],
    schedule_index: ScheduleIndex,
    bridge: IdentityBridge,
    *,
    matching_version: str = SCRIPT_VERSION,
    time_tolerance_seconds: int = 0,
    identity_mapping_version: Optional[str] = None,
) -> MatchDecision:
    """Match one source observation to a canonical stop event.

    Candidate generation requires an explicit service date, canonical stop,
    event type, and scheduled time.  The observed timestamp is never used as
    a substitute for service date or event time.  When a source line mapping
    is validated, candidates on another canonical line are rejected rather
    than silently reclassified.
    """

    event = observation if isinstance(observation, SourceEventObservation) else SourceEventObservation.from_mapping(observation)
    service_date = event.normalized_service_date
    if service_date is None:
        return _decision_for_failure(
            event,
            QUALITY_MISSING_REQUIRED_IDENTITY,
            detail="service date is missing or invalid; observed_at is not a fallback",
            matching_version=matching_version,
        )
    event_type = event.normalized_event_type
    if event_type is None:
        return _decision_for_failure(
            event,
            QUALITY_MISSING_REQUIRED_IDENTITY,
            detail="event type must be ARRIVAL or DEPARTURE",
            matching_version=matching_version,
        )
    scheduled_seconds = event.scheduled_time_seconds
    if scheduled_seconds is None:
        return _decision_for_failure(
            event,
            QUALITY_INVALID_SCHEDULE_TIME,
            detail="source timetable/scheduled time is missing or invalid",
            matching_version=matching_version,
        )

    if event.canonical_stop_id:
        stop_resolution = IdentityResolution(event.canonical_stop_id, "EXPLICIT_CANONICAL_ID", (event.canonical_stop_id,))
    else:
        stop_resolution = bridge.resolve_stop(
            event.source_system,
            event.source_namespace,
            event.source_stop_id,
            identity_mapping_version,
        )
    if stop_resolution.canonical_identifier is None:
        quality = QUALITY_AMBIGUOUS_MATCH if stop_resolution.status == "AMBIGUOUS" else QUALITY_SOURCE_IDENTITY_UNRESOLVED
        return _decision_for_failure(
            event,
            quality,
            detail=f"stop bridge status={stop_resolution.status}; no adjacent stop substitution is permitted",
            candidate_ids=(),
            matching_version=matching_version,
        )

    if event.canonical_line_id:
        line_resolution = IdentityResolution(event.canonical_line_id, "EXPLICIT_CANONICAL_ID", (event.canonical_line_id,))
    elif event.source_line_id:
        line_resolution = bridge.resolve_line(
            event.source_system,
            event.source_namespace,
            event.source_line_id,
            identity_mapping_version,
        )
    else:
        line_resolution = IdentityResolution(None, "MISSING_SOURCE_IDENTIFIER")

    exact = schedule_index.exact_candidates(
        service_date,
        stop_resolution.canonical_identifier,
        event_type,
        scheduled_seconds,
    )
    candidates = exact
    exact_time = True
    if not candidates and time_tolerance_seconds > 0:
        candidates = schedule_index.nearby_candidates(
            service_date,
            stop_resolution.canonical_identifier,
            event_type,
            scheduled_seconds,
            time_tolerance_seconds,
        )
        exact_time = False
    all_candidate_ids = [item.canonical_service_event_id for item in candidates]

    if line_resolution.canonical_identifier:
        line_candidates = [
            item for item in candidates if item.canonical_line_id == line_resolution.canonical_identifier
        ]
        if not line_candidates and candidates:
            return _decision_for_failure(
                event,
                QUALITY_SOURCE_LINE_DISAGREEMENT,
                detail="validated source line conflicts with all stop/date/time candidates",
                candidate_ids=all_candidate_ids,
                matching_version=matching_version,
            )
        candidates = line_candidates

    if not candidates:
        detail = "no static candidate for validated stop/date/event/time"
        if line_resolution.status in {"NOT_FOUND", "UNRESOLVED", "AMBIGUOUS"} and event.source_line_id:
            detail += f"; line bridge status={line_resolution.status}"
        return _decision_for_failure(
            event,
            QUALITY_STATIC_COVERAGE_GAP,
            detail=detail,
            candidate_ids=all_candidate_ids,
            matching_version=matching_version,
        )

    ranked: List[Tuple[int, int, str, CanonicalServiceEvent, Dict[str, Any]]] = []
    for candidate in candidates:
        evidence = _candidate_evidence(event, candidate, line_resolution, exact_time=exact_time)
        strong_count = sum(
            1
            for field_name in (
                "line_match",
                "direction_match",
                "stop_sequence_match",
                "operator_match",
                "multi_stop_sequence_match",
                "same_namespace_trip_identity_match",
            )
            if evidence[field_name]
        )
        ranked.append((-int(evidence["score"]), -strong_count, candidate.canonical_service_event_id, candidate, evidence))
    ranked.sort()
    top_score = ranked[0][0:2]
    tied = [item for item in ranked if item[0:2] == top_score]
    if len(tied) > 1:
        return MatchDecision(
            source_event_identity=event.source_identity,
            source_observation_identity=event.observation_id,
            source_system=event.source_system,
            source_namespace=event.source_namespace,
            canonical_service_event_id=None,
            match_confidence=MATCH_UNMATCHED,
            match_method=METHOD_UNMATCHED,
            data_quality_classification=QUALITY_AMBIGUOUS_MATCH,
            candidate_count=len(candidates),
            candidate_event_ids=tuple(item[2] for item in tied),
            selected_score=None,
            evidence={
                "reason": "equal_top_candidates",
                "candidate_evidence": {item[2]: item[4] for item in tied},
                "line_resolution_status": line_resolution.status,
            },
            matching_version=matching_version,
        )

    _negative_score, _negative_strong_count, _candidate_id, selected, evidence = ranked[0]
    exact_base = exact_time and bool(evidence["canonical_stop_match"] and evidence["service_date_match"] and evidence["event_type_match"])
    if evidence["multi_stop_sequence_match"]:
        confidence = MATCH_HIGH
        method = METHOD_MULTI_STOP_SEQUENCE_MATCH
        quality = QUALITY_VALIDATED_MATCH
    elif exact_base and evidence["line_match"] and evidence["direction_match"]:
        confidence = MATCH_HIGH
        method = METHOD_EXACT_STOP_DATE_TIME_DIRECTION
        quality = QUALITY_VALIDATED_MATCH
    elif exact_base and evidence["line_match"] and evidence["stop_sequence_match"]:
        confidence = MATCH_HIGH
        method = METHOD_EXACT_STOP_DATE_TIME_SEQUENCE
        quality = QUALITY_VALIDATED_MATCH
    elif exact_base and evidence["line_match"]:
        confidence = MATCH_MEDIUM
        method = METHOD_STOP_TIME_CONTEXT_ONLY
        quality = QUALITY_CONTEXTUAL_MATCH
    elif exact_base:
        confidence = MATCH_MEDIUM
        method = METHOD_STOP_TIME_CONTEXT_ONLY
        quality = QUALITY_CONTEXTUAL_MATCH
    else:
        confidence = MATCH_LOW
        method = METHOD_STOP_TIME_CONTEXT_ONLY
        quality = QUALITY_WEAK_CONTEXT_MATCH
    if line_resolution.status in {"NOT_FOUND", "UNRESOLVED", "AMBIGUOUS", "MISSING_SOURCE_IDENTIFIER"} and event.source_line_id:
        evidence = dict(evidence)
        evidence["line_identity_quality"] = QUALITY_SOURCE_LINE_IDENTITY_UNRESOLVED
        if quality in {
            QUALITY_VALIDATED_MATCH,
            QUALITY_CONTEXTUAL_MATCH,
            QUALITY_WEAK_CONTEXT_MATCH,
        }:
            quality = QUALITY_SOURCE_LINE_IDENTITY_UNRESOLVED
            if confidence == MATCH_HIGH:
                confidence = MATCH_MEDIUM

    evidence = dict(evidence)
    missing_lineage_fields = _missing_lineage_fields(event)
    evidence["lineage_missing_fields"] = missing_lineage_fields
    if missing_lineage_fields and quality in {
        QUALITY_VALIDATED_MATCH,
        QUALITY_CONTEXTUAL_MATCH,
        QUALITY_WEAK_CONTEXT_MATCH,
    }:
        quality = QUALITY_LINEAGE_INCOMPLETE
        if confidence == MATCH_HIGH:
            confidence = MATCH_MEDIUM
    evidence["line_resolution_status"] = line_resolution.status
    evidence["line_resolution_candidates"] = list(line_resolution.candidates)
    evidence["candidate_rank"] = [item[2] for item in ranked]
    evidence["candidate_count_before_line_filter"] = len(all_candidate_ids)
    return MatchDecision(
        source_event_identity=event.source_identity,
        source_observation_identity=event.observation_id,
        source_system=event.source_system,
        source_namespace=event.source_namespace,
        canonical_service_event_id=selected.canonical_service_event_id,
        match_confidence=confidence,
        match_method=method,
        data_quality_classification=quality,
        candidate_count=len(candidates),
        candidate_event_ids=tuple(item[2] for item in ranked),
        selected_score=int(evidence["score"]),
        evidence=evidence,
        matching_version=matching_version,
    )


def audit_field_coverage(observations: Iterable[SourceEventObservation | Mapping[str, Any]]) -> Dict[str, Any]:
    """Measure field presence while retaining NULL/false semantics."""

    events = [item if isinstance(item, SourceEventObservation) else SourceEventObservation.from_mapping(item) for item in observations]
    rules: Dict[str, Tuple[bool, Any]] = {
        "source_event_id": (True, lambda item: item.source_event_id),
        "observation_id": (True, lambda item: item.observation_id),
        "observed_at": (True, lambda item: item.observed_at),
        "service_date": (True, lambda item: item.normalized_service_date),
        "source_stop_id": (True, lambda item: item.source_stop_id),
        "event_type": (True, lambda item: item.normalized_event_type),
        "scheduled_time": (True, lambda item: item.scheduled_time_seconds),
        "collector_run_id": (True, lambda item: item.collector_run_id),
        "source_line_id": (False, lambda item: item.source_line_id),
        "source_trip_id": (False, lambda item: item.source_trip_id),
        "source_journey_ref": (False, lambda item: item.source_journey_ref),
        "vehicle_journey_ref": (False, lambda item: item.candidate_identifier_value("vehicle_journey_ref")),
        "dated_vehicle_journey_ref": (False, lambda item: item.candidate_identifier_value("dated_vehicle_journey_ref")),
        "direction": (False, lambda item: item.direction),
        "stop_sequence": (False, lambda item: item.stop_sequence),
        "operator_id": (False, lambda item: item.operator_id),
        "sampling_policy_id": (True, lambda item: item.sampling_policy_id),
        "sampling_era_id": (True, lambda item: item.sampling_era_id),
    }
    report: Dict[str, Any] = {"observation_count": len(events), "fields": {}, "required_field_failures": []}
    for field_name, (required, getter) in rules.items():
        present_count = sum(1 for item in events if _present(getter(item)))
        missing_count = len(events) - present_count
        entry = {
            "required": required,
            "present_count": present_count,
            "missing_count": missing_count,
            "coverage_ratio": (present_count / len(events)) if events else None,
        }
        report["fields"][field_name] = entry
        if required and missing_count:
            report["required_field_failures"].append(field_name)
    report["status"] = "PASS" if not report["required_field_failures"] else "WARN"
    return report


def audit_observation_continuity(
    observations: Iterable[SourceEventObservation | Mapping[str, Any]],
) -> Dict[str, Any]:
    """Prove repeated source events remain observations across sampling eras."""

    events = [item if isinstance(item, SourceEventObservation) else SourceEventObservation.from_mapping(item) for item in observations]
    by_event: Dict[Optional[str], List[SourceEventObservation]] = defaultdict(list)
    for event in events:
        by_event[event.source_identity].append(event)
    observation_id_groups: Dict[str, List[Optional[str]]] = defaultdict(list)
    for event in events:
        if event.observation_id:
            observation_id_groups[event.observation_id].append(event.source_identity)
    duplicate_observation_ids = sorted(
        observation_id
        for observation_id, source_identities in observation_id_groups.items()
        if len(source_identities) > 1
    )
    event_rows: List[Dict[str, Any]] = []
    for source_identity, rows in by_event.items():
        ids = [row.observation_id for row in rows if row.observation_id]
        event_rows.append(
            {
                "source_event_identity": source_identity,
                "observation_count": len(rows),
                "distinct_observation_id_count": len(set(ids)),
                "sampling_eras": sorted({row.sampling_era_id for row in rows if row.sampling_era_id}),
                "observed_at_values": [row.observed_at for row in rows],
            }
        )
    return {
        "status": "PASS" if not duplicate_observation_ids else "WARN",
        "source_event_group_count": len(by_event),
        "observation_count": len(events),
        "distinct_sampling_era_count": len({item.sampling_era_id for item in events if item.sampling_era_id}),
        "duplicate_observation_ids": sorted(set(duplicate_observation_ids)),
        "groups": sorted(event_rows, key=lambda item: str(item["source_event_identity"])),
        "repeated_observations_are_not_collapsed": all(
            item["observation_count"] == item["distinct_observation_id_count"]
            for item in event_rows
        ),
    }


def audit_observation_lineage(
    observations: Iterable[SourceEventObservation | Mapping[str, Any]],
) -> Dict[str, Any]:
    """Classify missing collector lineage without dropping the source row."""

    events = [item if isinstance(item, SourceEventObservation) else SourceEventObservation.from_mapping(item) for item in observations]
    missing_rows: List[Dict[str, Any]] = []
    for event in events:
        missing_fields = _missing_lineage_fields(event)
        if missing_fields:
            missing_rows.append(
                {
                    "source_event_identity": event.source_identity,
                    "source_observation_identity": event.observation_id,
                    "classification": QUALITY_LINEAGE_INCOMPLETE,
                    "missing_fields": missing_fields,
                }
            )
    continuity = audit_observation_continuity(events)
    duplicate_rows = [
        {
            "source_observation_identity": observation_id,
            "classification": QUALITY_DUPLICATE_SOURCE_OBSERVATION,
        }
        for observation_id in continuity["duplicate_observation_ids"]
    ]
    issues = missing_rows + duplicate_rows
    return {
        "status": "PASS" if not issues else "WARN",
        "observation_count": len(events),
        "lineage_incomplete_count": len(missing_rows),
        "duplicate_source_observation_count": len(duplicate_rows),
        "issues": issues,
    }


def validate_service_date_alignment(
    observations: Iterable[SourceEventObservation | Mapping[str, Any]],
    valid_service_dates: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    events = [item if isinstance(item, SourceEventObservation) else SourceEventObservation.from_mapping(item) for item in observations]
    missing = []
    invalid = []
    outside = []
    for event in events:
        if not _present(event.service_date):
            missing.append(event.source_identity)
            continue
        normalized = event.normalized_service_date
        if normalized is None:
            invalid.append(event.source_identity)
        elif valid_service_dates is not None and normalized not in valid_service_dates:
            outside.append({"source_event_identity": event.source_identity, "service_date": normalized})
    return {
        "status": "PASS" if not missing and not invalid and not outside else "WARN",
        "missing_service_date_count": len(missing),
        "invalid_service_date_count": len(invalid),
        "outside_validated_population_count": len(outside),
        "missing_examples": missing[:10],
        "invalid_examples": invalid[:10],
        "outside_examples": outside[:10],
    }


def reconcile_cross_source(
    decisions: Iterable[MatchDecision],
) -> List[Dict[str, Any]]:
    """Associate sources only through the canonical event, never ID equality."""

    rows = list(decisions)
    groups: Dict[str, List[MatchDecision]] = defaultdict(list)
    for decision in rows:
        if decision.canonical_service_event_id:
            groups[decision.canonical_service_event_id].append(decision)
    output: List[Dict[str, Any]] = []
    for canonical_id, group in sorted(groups.items()):
        source_systems = sorted({item.source_system for item in group})
        if len(source_systems) < 2:
            continue
        if all(item.match_confidence == MATCH_HIGH for item in group):
            confidence = MATCH_HIGH
        elif any(item.match_confidence == MATCH_LOW for item in group):
            confidence = MATCH_LOW
        else:
            confidence = MATCH_MEDIUM
        output.append(
            {
                "canonical_service_event_id": canonical_id,
                "source_systems": source_systems,
                "source_event_identities": [item.source_event_identity for item in group],
                "match_confidence": confidence,
                "match_method": (
                    METHOD_MULTI_STOP_SEQUENCE_MATCH
                    if confidence == MATCH_HIGH
                    and all(item.evidence.get("multi_stop_sequence_match") for item in group)
                    else METHOD_EXACT_STOP_DATE_TIME_SEQUENCE
                    if confidence == MATCH_HIGH
                    and all(item.evidence.get("stop_sequence_match") for item in group)
                    else METHOD_EXACT_STOP_DATE_TIME_DIRECTION
                    if confidence == MATCH_HIGH
                    and all(item.evidence.get("direction_match") for item in group)
                    else METHOD_STOP_TIME_CONTEXT_ONLY
                ),
                "source_identifier_equality_used": False,
                "interpretation": "cross-source evidence agrees on one canonical stop event; not causality or full-trip proof",
            }
        )
    return output


def classify_alert_association(
    alert: Mapping[str, Any],
    decisions: Iterable[MatchDecision],
) -> Dict[str, Any]:
    """Keep GTFS-RT alert scope distinct from the matched event scope."""

    scope = (_text(alert.get("association_scope") or alert.get("scope")) or "UNKNOWN").upper()
    if scope not in {"TRIP", "ROUTE", "STOP", "AGENCY"}:
        scope = "UNKNOWN"
    high_trip_match = False
    if scope == "TRIP":
        alert_trip_id = _text(alert.get("trip_id"))
        alert_namespace = _normalized_namespace(alert.get("source_namespace") or SOURCE_GTFS_RT)
        high_trip_match = any(
            decision.match_confidence == MATCH_HIGH
            and decision.source_system == SOURCE_GTFS_RT
            and _normalized_namespace(decision.source_namespace) == alert_namespace
            and decision.canonical_service_event_id
            and alert_trip_id
            and decision.evidence.get("source_trip_id") == alert_trip_id
            for decision in decisions
        )
    if scope == "TRIP" and high_trip_match:
        association_status = "TRIP_CONTEXT_HIGH_MATCH"
    elif scope == "TRIP":
        association_status = "TRIP_REFERENCE_NOT_RECONCILED"
    elif scope in {"ROUTE", "STOP", "AGENCY"}:
        association_status = f"{scope}_CONTEXT_ONLY"
    else:
        association_status = "UNKNOWN_SCOPE_REVIEW"
    return {
        "alert_id": _text(alert.get("alert_id")),
        "association_scope": scope,
        "association_status": association_status,
        "reported_cause": alert.get("reported_cause"),
        "reported_effect": alert.get("reported_effect"),
        "causal_claim_permitted": False,
    }


def validate_stage4_baseline(path: Path = STAGE4_EVIDENCE_PATH) -> Dict[str, Any]:
    """Reconcile the approved Stage 4 evidence without loading a feed/network."""

    data = json.loads(path.read_text(encoding="utf-8"))
    scope = data["scope_counts"]
    source = data["source_counts"]
    recon = data["reconciliation"]
    anomalies = data["scope_anomalies"]
    checks = {
        "all_stage4_acceptance_checks_pass": all(item.get("passed") for item in data["acceptance_checks"]),
        "cologne_stop_rule_present": "de:05315:" in data["scope_contract"]["cologne_stop_rule"],
        "cologne_stop_events_match_reconciliation": recon["cologne_scheduled_stop_events"]["matches"],
        "cologne_internal_segments_match_reconciliation": recon["cologne_internal_segments"]["matches"],
        "all_cologne_events_classified": anomalies["unclassified_cologne_stop_event_count"] == 0,
        "no_unknown_stop_time_references": anomalies["unknown_stop_time_reference_rows"] == 0,
        "no_unknown_trip_references": anomalies["unknown_trip_reference_rows"] == 0,
        "no_duplicate_trip_stop_sequence": anomalies["trip_ids_with_duplicate_stop_sequence"] == 0,
        "source_counts_reconcile_stop_times": recon["source_stop_time_scope_partition"]["matches_stop_times_rows"],
        "known_cologne_event_baseline_is_1144295": scope["cologne_scheduled_stop_events"] == 1_144_295,
        "known_cologne_stop_baseline_is_3137": scope["cologne_distinct_stop_ids"] == 3_137,
        "known_cologne_trip_baseline_is_61805": scope["cologne_serving_trips"] == 61_805,
        "known_cologne_route_baseline_is_137": scope["cologne_serving_routes"] == 137,
        "complete_source_snapshot_is_retained": source["stop_times"] == 2_943_746,
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "feed_snapshot": {
            "feed_version": data["source_metadata"]["feed_info"].get("feed_version"),
            "zip_sha256": data["source_metadata"]["zip_sha256"],
        },
        "scope_counts": {
            "cologne_distinct_stop_ids": scope["cologne_distinct_stop_ids"],
            "cologne_scheduled_stop_events": scope["cologne_scheduled_stop_events"],
            "cologne_internal_segments": scope["cologne_internal_consecutive_segments"],
            "cologne_serving_trips": scope["cologne_serving_trips"],
            "cologne_serving_routes": scope["cologne_serving_routes"],
        },
    }


def _fixture_bridge() -> IdentityBridge:
    stop_rows: List[IdentityMapping] = [
        IdentityMapping(SOURCE_MDD, "VRS_GLOBAL_ID", "de:05315:11111", "CS:COLOGNE:NEUMARKT", "2026-09-28", evidence_ref="gtfsde-s18.1/neumarkt"),
        IdentityMapping(SOURCE_MDD, "VRS_GLOBAL_ID", "de:05315:11201", "CS:COLOGNE:HBF_VRS", "2026-09-28", evidence_ref="stage4/vrs-stop-rule"),
        IdentityMapping(SOURCE_MDD, "VRS_GLOBAL_ID", "de:05315:11110", "CS:COLOGNE:HEUMARKT", "2026-09-28", evidence_ref="stage8/diagnostic-target"),
    ]
    for child_id in ("106842", "207108", "441164", "581903", "96309"):
        stop_rows.append(
            IdentityMapping(
                SOURCE_GTFS_RT,
                "GTFS_DE",
                child_id,
                "CS:COLOGNE:NEUMARKT",
                "2026-09-28",
                evidence_ref="gtfsde-s18.1/neumarkt-parent-493082",
            )
        )
    line_rows = [
        IdentityMapping(SOURCE_MDD, "VRS_GLOBAL_ID", "mdd-line-18", "CL:KVB:18", "2026-09-28", evidence_ref="fixture/line-bridge"),
        IdentityMapping(SOURCE_GTFS_RT, "GTFS_DE", "gtfs-route-18", "CL:KVB:18", "2026-09-28", evidence_ref="fixture/line-bridge"),
        IdentityMapping(SOURCE_STATIC_GTFS, "VRS_GLOBAL_ID", "vrs-route-18", "CL:KVB:18", "2026-09-28", evidence_ref="fixture/line-bridge"),
    ]
    return IdentityBridge(stop_mappings=stop_rows, line_mappings=line_rows)


def _fixture_schedule() -> ScheduleIndex:
    rows = [
        StaticServiceEvent(
            feed_snapshot_key="VERSION__20260928_0042",
            source_namespace="VRS_GLOBAL_ID",
            source_trip_id="vrs-trip-neu-n",
            source_route_id="vrs-route-18",
            source_stop_id="de:05315:11111",
            canonical_stop_id="CS:COLOGNE:NEUMARKT",
            canonical_line_id="CL:KVB:18",
            service_date="2026-09-29",
            event_type="ARRIVAL",
            scheduled_time="10:15:00",
            direction="N",
            stop_sequence=7,
            operator_id="KVB",
            source_row_identity="stop_times:neu-n:7",
        ),
        StaticServiceEvent(
            feed_snapshot_key="VERSION__20260928_0042",
            source_namespace="VRS_GLOBAL_ID",
            source_trip_id="vrs-trip-neu-s",
            source_route_id="vrs-route-18",
            source_stop_id="de:05315:11111",
            canonical_stop_id="CS:COLOGNE:NEUMARKT",
            canonical_line_id="CL:KVB:18",
            service_date="2026-09-29",
            event_type="ARRIVAL",
            scheduled_time="10:15:00",
            direction="S",
            stop_sequence=7,
            operator_id="KVB",
            source_row_identity="stop_times:neu-s:7",
        ),
        StaticServiceEvent(
            feed_snapshot_key="VERSION__20260928_0042",
            source_namespace="VRS_GLOBAL_ID",
            source_trip_id="vrs-trip-hbf",
            source_route_id="vrs-route-18",
            source_stop_id="de:05315:11201",
            canonical_stop_id="CS:COLOGNE:HBF_VRS",
            canonical_line_id="CL:KVB:18",
            service_date="2026-09-29",
            event_type="ARRIVAL",
            scheduled_time="10:20:00",
            direction="N",
            stop_sequence=8,
            operator_id="KVB",
            source_row_identity="stop_times:hbf:8",
        ),
    ]
    return build_schedule_index(rows)


def _fixture_observations() -> List[SourceEventObservation]:
    base = dict(
        service_date="2026-09-29",
        event_type="ARRIVAL",
        scheduled_time="2026-09-29T10:15:00+02:00",
        direction="N",
        stop_sequence=7,
        operator_id="KVB",
        observed_at="2026-09-29T10:00:00Z",
        response_id="response-1",
        collector_run_id="run-1",
        sampling_policy_id="MDD_STAGE_8_KOELN_HBF_3S_60MIN_V3",
        sampling_era_id="ERA_STAGE_8_V3",
        candidate_identifiers={
            "vehicle_journey_ref": {"value": "mdd-vj-1", "source_path": "$.service.vehicleJourneyRef"},
            "dated_vehicle_journey_ref": {"value": "mdd-dvj-1", "source_path": "$.service.datedVehicleJourneyRef"},
        },
    )
    return [
        SourceEventObservation(
            source_system=SOURCE_MDD,
            source_namespace="VRS_GLOBAL_ID",
            source_event_id="mdd-event-neu-1",
            observation_id="mdd-observation-neu-1",
            source_stop_id="de:05315:11111",
            source_line_id="mdd-line-18",
            source_trip_id=None,
            source_journey_ref="vrs-trip-neu-n",
            **base,
        ),
        SourceEventObservation(
            source_system=SOURCE_GTFS_RT,
            source_namespace="GTFS_DE",
            source_event_id="gtfs-event-neu-1",
            observation_id="gtfs-observation-neu-1",
            source_stop_id="106842",
            source_line_id="gtfs-route-18",
            source_trip_id="gtfs-trip-neu-1",
            source_journey_ref=None,
            **{**base, "response_id": "gtfs-response-1", "collector_run_id": "gtfs-run-1", "sampling_policy_id": "GTFSRT_VALIDATED_SAMPLE_V1", "sampling_era_id": "ERA_GTFSRT_V1"},
        ),
        SourceEventObservation(
            source_system=SOURCE_MDD,
            source_namespace="VRS_GLOBAL_ID",
            source_event_id="mdd-event-ambiguous",
            observation_id="mdd-observation-ambiguous",
            source_stop_id="de:05315:11111",
            source_line_id="mdd-line-18",
            source_trip_id=None,
            source_journey_ref="gtfs-trip-neu-1",
            direction=None,
            stop_sequence=None,
            **{key: value for key, value in base.items() if key not in {"direction", "stop_sequence"}},
        ),
        SourceEventObservation(
            source_system=SOURCE_GTFS_RT,
            source_namespace="GTFS_DE",
            source_event_id="gtfs-event-hbf-unresolved",
            observation_id="gtfs-observation-hbf-unresolved",
            source_stop_id="106448",
            source_line_id="gtfs-route-18",
            source_trip_id="gtfs-trip-hbf",
            source_journey_ref=None,
            **{**base, "response_id": "gtfs-response-2", "collector_run_id": "gtfs-run-2", "sampling_policy_id": "GTFSRT_VALIDATED_SAMPLE_V1", "sampling_era_id": "ERA_GTFSRT_V1"},
        ),
        SourceEventObservation(
            source_system=SOURCE_MDD,
            source_namespace="VRS_GLOBAL_ID",
            source_event_id="mdd-event-missing-date",
            observation_id="mdd-observation-missing-date",
            service_date=None,
            source_stop_id="de:05315:11111",
            source_line_id="mdd-line-18",
            source_trip_id=None,
            source_journey_ref="vrs-trip-neu-n",
            **{key: value for key, value in base.items() if key != "service_date"},
        ),
        SourceEventObservation(
            source_system=SOURCE_MDD,
            source_namespace="VRS_GLOBAL_ID",
            source_event_id="mdd-event-repeat",
            observation_id="mdd-observation-repeat-1",
            source_stop_id="de:05315:11111",
            source_line_id="mdd-line-18",
            source_trip_id=None,
            source_journey_ref="vrs-trip-neu-n",
            **base,
        ),
        SourceEventObservation(
            source_system=SOURCE_MDD,
            source_namespace="VRS_GLOBAL_ID",
            source_event_id="mdd-event-repeat",
            observation_id="mdd-observation-repeat-2",
            source_stop_id="de:05315:11111",
            source_line_id="mdd-line-18",
            source_trip_id=None,
            source_journey_ref="vrs-trip-neu-n",
            **{**base, "observed_at": "2026-09-29T10:00:03Z", "response_id": "response-2", "sampling_era_id": "ERA_STAGE_8_V3"},
        ),
    ]


def run_offline_validation() -> Dict[str, Any]:
    """Run the bounded Stage 9 acceptance population without network access."""

    baseline = validate_stage4_baseline()
    bridge = _fixture_bridge()
    schedule = _fixture_schedule()
    observations = _fixture_observations()
    decisions = [match_source_event(item, schedule, bridge) for item in observations]
    by_id = {item.source_event_identity: item for item in decisions}
    cross_source = reconcile_cross_source([by_id[observations[0].source_identity], by_id[observations[1].source_identity]])
    continuity = audit_observation_continuity(observations)
    lineage = audit_observation_lineage(observations)
    coverage = audit_field_coverage(observations)
    date_alignment = validate_service_date_alignment(observations)
    bridge_report = bridge.validation_report()
    alert_report = classify_alert_association(
        {"alert_id": "alert-route-18", "association_scope": "ROUTE", "reported_cause": "UNKNOWN"},
        decisions,
    )

    mdd_good = by_id[observations[0].source_identity]
    gtfs_good = by_id[observations[1].source_identity]
    ambiguous = by_id[observations[2].source_identity]
    unresolved_hbf = by_id[observations[3].source_identity]
    missing_date = by_id[observations[4].source_identity]
    checks = {
        "stage4_static_baseline_reconciles": baseline["status"] == "PASS",
        "indexed_schedule_contains_known_good_events": schedule.query_count() == 3,
        "mdd_known_good_stop_event_is_high_confidence": mdd_good.match_confidence == MATCH_HIGH and mdd_good.matched,
        "gtfsrt_known_good_stop_event_is_high_confidence": gtfs_good.match_confidence == MATCH_HIGH and gtfs_good.matched,
        "cross_source_agreement_uses_canonical_event": len(cross_source) == 1 and cross_source[0]["match_confidence"] == MATCH_HIGH,
        "mdd_journeyref_is_not_gtfsrt_trip_id_key": not mdd_good.evidence.get("source_trip_identity_used", False),
        "equal_cross_source_identity_is_not_used": by_id[observations[2].source_identity].evidence.get("reason") == "equal_top_candidates",
        "ambiguous_candidate_is_quarantined": ambiguous.match_confidence == MATCH_UNMATCHED and ambiguous.data_quality_classification == QUALITY_AMBIGUOUS_MATCH,
        "unresolved_railway_hbf_is_not_substituted": unresolved_hbf.match_confidence == MATCH_UNMATCHED and unresolved_hbf.data_quality_classification == QUALITY_SOURCE_IDENTITY_UNRESOLVED,
        "missing_service_date_does_not_use_observed_at": missing_date.data_quality_classification == QUALITY_MISSING_REQUIRED_IDENTITY and not missing_date.matched,
        "repeated_observations_are_retained": continuity["repeated_observations_are_not_collapsed"] and continuity["observation_count"] == len(observations),
        "sampling_eras_remain_explicit": continuity["distinct_sampling_era_count"] == 2,
        "observation_lineage_is_complete": lineage["status"] == "PASS",
        "field_coverage_reports_missingness": coverage["fields"]["service_date"]["missing_count"] == 1,
        "false_values_would_not_be_counted_as_missing": _present(False),
        "service_date_alignment_preserves_missingness": date_alignment["missing_service_date_count"] == 1,
        "route_alert_remains_route_context": alert_report["association_status"] == "ROUTE_CONTEXT_ONLY" and not alert_report["causal_claim_permitted"],
        "extended_schedule_time_is_not_wrapped": schedule_time_seconds("25:05:00") == 90_300,
        "bridge_has_no_ambiguous_mapping": bridge_report["status"] == "PASS",
        "richer_identity_candidates_are_retained": observations[0].candidate_identifier_value("dated_vehicle_journey_ref") == "mdd-dvj-1",
        "hbf_adjacent_kvb_identity_not_promoted": bridge.resolve_stop(SOURCE_GTFS_RT, "GTFS_DE", "106448").status == "NOT_FOUND",
    }
    return {
        "record_type": "stage_09_offline_validation",
        "script_version": SCRIPT_VERSION,
        "stage_9_status": "COMPLETE" if all(checks.values()) else "VALIDATION_FAILED",
        "overall_status": "PASS" if all(checks.values()) else "FAIL",
        "network_requests": 0,
        "database_connections": 0,
        "checks": checks,
        "stage4_baseline": baseline,
        "bridge_validation": bridge_report,
        "schedule_index": {"canonical_event_count": schedule.query_count(), "validation_issue_count": len(schedule.issues)},
        "match_decisions": [decision.to_record() for decision in decisions],
        "cross_source_reconciliation": cross_source,
        "field_coverage": coverage,
        "service_date_alignment": date_alignment,
        "observation_continuity": continuity,
        "observation_lineage": lineage,
        "alert_scope_validation": alert_report,
        "expected_unresolved_evidence": {
            "railway_koeln_hbf_gtfs_de": EXPECTED_RAILWAY_HBF_STATUS,
            "adjacent_kvb_parent_ids": ["106448", "437741"],
            "note": "The unresolved bridge state is a retained data-quality result, not a failed attempt to infer identity.",
        },
        "future_architecture_guardrails": {
            "mdd_journeyref_equals_gtfsrt_trip_id": False,
            "stop_event_first": True,
            "source_observations_append_only": True,
            "sampling_era_lineage_required": True,
            "actual_delay_approved": False,
            "tiered50_production_activation": False,
        },
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Offline Stage 9 matching/data-quality validation")
    parser.add_argument(
        "--offline-validation",
        action="store_true",
        help="run the built-in known-good/ambiguous/unresolved validation population (default)",
    )
    args = parser.parse_args(argv)
    del args
    summary = run_offline_validation()
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if summary["overall_status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
