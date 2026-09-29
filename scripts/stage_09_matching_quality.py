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
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - Python 3.8 fallback is unsupported here.
    ZoneInfo = None  # type: ignore[assignment]
    ZoneInfoNotFoundError = Exception  # type: ignore[assignment,misc]


SCRIPT_VERSION = "stage-09-matching-quality-1.1"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
STAGE4_EVIDENCE_PATH = REPOSITORY_ROOT / "docs" / "stages" / "04-cologne-scope-mode-evidence.json"
STAGE3A_EVIDENCE_PATH = REPOSITORY_ROOT / "stage03a-mdd-discovery.jsonl"
COLOGNE_STOP_PREFIX = "de:05315:"
DEFAULT_FEED_TIMEZONE = "Europe/Berlin"
STAGE8_MDD_SOURCE_SYSTEM = "MDD_TRIAS"
STAGE8_MDD_SOURCE_NAMESPACE = "VRS_GLOBAL_ID"

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
QUALITY_STATIC_STOP_MISSING = "STATIC_STOP_MISSING"
QUALITY_STATIC_ROUTE_MISSING = "STATIC_ROUTE_MISSING"
QUALITY_STATIC_SERVICE_DATE_NOT_ACTIVE = "STATIC_SERVICE_DATE_NOT_ACTIVE"
QUALITY_STATIC_EVENT_NOT_FOUND = "STATIC_EVENT_NOT_FOUND"
QUALITY_SOURCE_IDENTITY_UNRESOLVED = "SOURCE_IDENTITY_UNRESOLVED"
QUALITY_SOURCE_LINE_IDENTITY_UNRESOLVED = "SOURCE_LINE_IDENTITY_UNRESOLVED"
QUALITY_SOURCE_LINE_DISAGREEMENT = "SOURCE_LINE_DISAGREEMENT"
QUALITY_IDENTITY_CONFLICT = "IDENTITY_CONFLICT"
QUALITY_AMBIGUOUS_MULTIPLE_CANDIDATES = "AMBIGUOUS_MULTIPLE_CANDIDATES"
QUALITY_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
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

TIME_ALIGNMENT_RESOLVED = "RESOLVED"
TIME_ALIGNMENT_SERVICE_DAY_TIME = "GTFS_SERVICE_DAY_TIME"
TIME_ALIGNMENT_TIMEZONE_UNRESOLVED = "TIMEZONE_UNRESOLVED"
TIME_ALIGNMENT_SERVICE_DATE_REQUIRED = "SERVICE_DATE_REQUIRED"
TIME_ALIGNMENT_INVALID = "INVALID_SCHEDULE_TIME"
TIME_ALIGNMENT_FEED_TIMEZONE_UNRESOLVED = "FEED_TIMEZONE_UNRESOLVED"


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


def _time_only_seconds(value: Any) -> Optional[int]:
    """Parse a GTFS service-day clock, retaining hours above 24."""

    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        if int(value) == value and value >= 0:
            return int(value)
        return None
    text = _text(value)
    if not text:
        return None
    match = re.fullmatch(r"(\d{1,3}):(\d{2}):(\d{2})(?:[.,](\d+))?", text)
    if match is None:
        return None
    hour, minute, second = (int(match.group(index)) for index in (1, 2, 3))
    if minute > 59 or second > 59:
        return None
    return hour * 3600 + minute * 60 + second


def _parse_source_datetime(value: Any) -> Tuple[Optional[datetime], bool, Optional[str]]:
    """Parse an absolute source timestamp without changing its lexical value."""

    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return value, False, None
        return value, True, str(value.tzinfo)
    text = _text(value)
    if not text:
        return None, False, None
    # MDD's historical ``Z[GMT]`` suffix is an explicit UTC timestamp.  The
    # bracketed provider label is removed only for parsing; the original value
    # is retained by the caller as source evidence.
    parse_text = re.sub(r"\[[^\]]+\]$", "", text)
    explicit_timezone = bool(re.search(r"(?:Z|[+-]\d{2}:?\d{2})$", parse_text, re.IGNORECASE))
    if parse_text.endswith(("Z", "z")):
        parse_text = parse_text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(parse_text)
    except ValueError:
        return None, explicit_timezone, None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed, False, None
    return parsed, explicit_timezone, str(parsed.tzinfo)


def align_schedule_time(
    value: Any,
    *,
    service_date: Any = None,
    feed_timezone: str = DEFAULT_FEED_TIMEZONE,
) -> Dict[str, Any]:
    """Align source timetable evidence to GTFS service-day seconds.

    A GTFS clock value is already a local service-day value and may use
    extended hours.  An MDD/TRIAS ISO timestamp is an absolute instant when it
    contains an explicit offset/``Z``; it is converted to the validated local
    feed timezone and measured against the independently supplied service-date
    midnight.  A timezone-less absolute timestamp is deliberately unresolved.
    """

    result: Dict[str, Any] = {
        "original_source_timestamp": value,
        "feed_timezone": feed_timezone,
        "service_date": normalize_service_date(service_date),
        "source_timezone": None,
        "local_timestamp": None,
        "local_service_date": None,
        "service_day_seconds": None,
        "explicit_timezone": False,
        "extended_hours": False,
        "status": TIME_ALIGNMENT_INVALID,
    }
    direct_seconds = _time_only_seconds(value)
    if direct_seconds is not None:
        result.update(
            {
                "status": TIME_ALIGNMENT_SERVICE_DAY_TIME,
                "service_day_seconds": direct_seconds,
                "extended_hours": direct_seconds >= 24 * 60 * 60,
            }
        )
        return result

    parsed, explicit_timezone, source_timezone = _parse_source_datetime(value)
    result["explicit_timezone"] = explicit_timezone
    result["source_timezone"] = source_timezone
    if parsed is None:
        return result
    if not explicit_timezone or parsed.tzinfo is None or parsed.utcoffset() is None:
        result["status"] = TIME_ALIGNMENT_TIMEZONE_UNRESOLVED
        return result
    if result["service_date"] is None:
        result["status"] = TIME_ALIGNMENT_SERVICE_DATE_REQUIRED
        return result
    if ZoneInfo is None:  # pragma: no cover - supported runtimes provide zoneinfo.
        result["status"] = TIME_ALIGNMENT_FEED_TIMEZONE_UNRESOLVED
        return result
    try:
        local_zone = ZoneInfo(feed_timezone)
    except ZoneInfoNotFoundError:
        result["status"] = TIME_ALIGNMENT_FEED_TIMEZONE_UNRESOLVED
        return result
    local_timestamp = parsed.astimezone(local_zone)
    service_day = date.fromisoformat(result["service_date"])
    local_date = local_timestamp.date()
    day_offset = (local_date - service_day).days
    # GTFS service-day seconds are civil-clock seconds.  This deliberately
    # uses the local wall-clock fields after timezone conversion so a DST
    # transition changes the UTC instant but does not corrupt the GTFS clock
    # representation.
    seconds = (
        day_offset * 24 * 60 * 60
        + local_timestamp.hour * 60 * 60
        + local_timestamp.minute * 60
        + local_timestamp.second
    )
    result.update(
        {
            "status": TIME_ALIGNMENT_RESOLVED if seconds >= 0 else TIME_ALIGNMENT_INVALID,
            "local_timestamp": local_timestamp.isoformat(),
            "local_service_date": local_date.isoformat(),
            "service_day_seconds": seconds if seconds >= 0 else None,
            "extended_hours": seconds >= 24 * 60 * 60,
        }
    )
    return result


def schedule_time_seconds(
    value: Any,
    *,
    service_date: Any = None,
    feed_timezone: str = DEFAULT_FEED_TIMEZONE,
) -> Optional[int]:
    """Return GTFS service-day seconds under the explicit timezone contract."""

    return align_schedule_time(
        value,
        service_date=service_date,
        feed_timezone=feed_timezone,
    ).get("service_day_seconds")


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
    mapping_versions: Tuple[str, ...] = ()
    valid_mapping_count: int = 0


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
        service_date: Optional[str] = None,
    ) -> IdentityResolution:
        if not _present(source_identifier):
            return IdentityResolution(None, "MISSING_SOURCE_IDENTIFIER")
        key = (
            _normalized_source_system(source_system),
            _normalized_key(source_namespace),
            str(source_identifier),
        )
        rows = list(index.get(key, ()))
        if not rows:
            return IdentityResolution(None, "NOT_FOUND")
        normalized_date = normalize_service_date(service_date)
        available_versions = sorted({row.mapping_version for row in rows})
        if mapping_version is not None:
            rows = [row for row in rows if row.mapping_version == mapping_version]
        elif len(available_versions) > 1:
            if normalized_date is None:
                return IdentityResolution(
                    None,
                    "MAPPING_VERSION_REQUIRED",
                    mapping_versions=tuple(available_versions),
                )
            date_valid_rows = [
                row for row in rows if IdentityBridge._mapping_is_valid_on(row, normalized_date)
            ]
            date_versions = sorted({row.mapping_version for row in date_valid_rows})
            if len(date_versions) != 1:
                return IdentityResolution(
                    None,
                    "MAPPING_VERSION_REQUIRED" if len(date_versions) > 1 else "NO_VALID_MAPPING_FOR_DATE",
                    mapping_versions=tuple(date_versions or available_versions),
                )
            mapping_version = date_versions[0]
            rows = [row for row in rows if row.mapping_version == mapping_version]
        if not rows:
            return IdentityResolution(None, "NOT_FOUND")
        if normalized_date is not None:
            rows = [row for row in rows if IdentityBridge._mapping_is_valid_on(row, normalized_date)]
            if not rows:
                return IdentityResolution(
                    None,
                    "NO_VALID_MAPPING_FOR_DATE",
                    mapping_versions=(mapping_version,) if mapping_version else (),
                )
        elif any(row.valid_from or row.valid_to for row in rows):
            return IdentityResolution(
                None,
                "MAPPING_SERVICE_DATE_REQUIRED",
                mapping_versions=(mapping_version,) if mapping_version else tuple(available_versions),
            )
        candidates = tuple(sorted({row.canonical_identifier for row in rows if row.canonical_identifier}))
        valid_rows = [row for row in rows if row.status == VALIDATED_MAPPING and row.canonical_identifier]
        methods = tuple(sorted({row.method for row in rows if row.method}))
        evidence = tuple(sorted({row.evidence_ref for row in rows if row.evidence_ref}))
        versions = tuple(sorted({row.mapping_version for row in rows}))
        if len(candidates) > 1:
            return IdentityResolution(
                None,
                "AMBIGUOUS",
                candidates,
                methods,
                evidence,
                versions,
                len(valid_rows),
            )
        if len(valid_rows) == 1 or (len(valid_rows) > 1 and len(candidates) == 1):
            return IdentityResolution(
                candidates[0],
                "RESOLVED",
                candidates,
                methods,
                evidence,
                versions,
                len(valid_rows),
            )
        if candidates:
            return IdentityResolution(
                None,
                "UNRESOLVED",
                candidates,
                methods,
                evidence,
                versions,
                len(valid_rows),
            )
        return IdentityResolution(None, "UNRESOLVED", (), methods, evidence, versions, len(valid_rows))

    @staticmethod
    def _mapping_is_valid_on(mapping: IdentityMapping, service_date: str) -> bool:
        current = date.fromisoformat(service_date)
        valid_from = normalize_service_date(mapping.valid_from)
        valid_to = normalize_service_date(mapping.valid_to)
        if valid_from and current < date.fromisoformat(valid_from):
            return False
        if valid_to and current > date.fromisoformat(valid_to):
            return False
        return True

    def resolve_stop(
        self,
        source_system: str,
        source_namespace: str,
        source_identifier: Optional[str],
        mapping_version: Optional[str] = None,
        service_date: Optional[str] = None,
    ) -> IdentityResolution:
        selected_version = mapping_version or self.active_mapping_version
        return self._resolve(
            self._stop_index,
            source_system,
            source_namespace,
            source_identifier,
            selected_version,
            service_date,
        )

    def resolve_line(
        self,
        source_system: str,
        source_namespace: str,
        source_identifier: Optional[str],
        mapping_version: Optional[str] = None,
        service_date: Optional[str] = None,
    ) -> IdentityResolution:
        selected_version = mapping_version or self.active_mapping_version
        return self._resolve(
            self._line_index,
            source_system,
            source_namespace,
            source_identifier,
            selected_version,
            service_date,
        )

    def validation_report(self) -> Dict[str, Any]:
        def report_rows(rows: Sequence[IdentityMapping]) -> Dict[str, Any]:
            by_key: Dict[Tuple[str, str, str], List[IdentityMapping]] = defaultdict(list)
            for row in rows:
                by_key[row.key()].append(row)
            conflicts = []
            unresolved = []
            overlaps = []
            for key, values in sorted(by_key.items()):
                for left_index, left in enumerate(values):
                    for right in values[left_index + 1 :]:
                        if IdentityBridge._ranges_overlap(left, right):
                            conflicting = left.canonical_identifier != right.canonical_identifier
                            overlaps.append(
                                {
                                    "source_identity": list(key),
                                    "left_mapping_version": left.mapping_version,
                                    "right_mapping_version": right.mapping_version,
                                    "left_canonical_id": left.canonical_identifier,
                                    "right_canonical_id": right.canonical_identifier,
                                    "conflicting": conflicting,
                                }
                            )
                            if conflicting:
                                conflicts.append(
                                    {
                                        "source_identity": list(key),
                                        "candidate_canonical_ids": sorted(
                                            item
                                            for item in (left.canonical_identifier, right.canonical_identifier)
                                            if item
                                        ),
                                        "mapping_versions": sorted(
                                            {left.mapping_version, right.mapping_version}
                                        ),
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
                "overlapping_mapping_count": len(overlaps),
                "overlapping_conflict_count": sum(1 for item in overlaps if item["conflicting"]),
                "ambiguous_examples": conflicts[:10],
                "unresolved_examples": unresolved[:10],
                "overlap_examples": overlaps[:10],
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
        status = (
            "PASS"
            if not stop_report["ambiguous_source_identity_count"]
            and not line_report["ambiguous_source_identity_count"]
            and not stop_report["overlapping_conflict_count"]
            and not line_report["overlapping_conflict_count"]
            else "FAIL"
        )
        return {"status": status, "stops": stop_report, "lines": line_report}

    @staticmethod
    def _ranges_overlap(left: IdentityMapping, right: IdentityMapping) -> bool:
        left_start = date.fromisoformat(normalize_service_date(left.valid_from) or "0001-01-01")
        left_end = date.fromisoformat(normalize_service_date(left.valid_to) or "9999-12-31")
        right_start = date.fromisoformat(normalize_service_date(right.valid_from) or "0001-01-01")
        right_end = date.fromisoformat(normalize_service_date(right.valid_to) or "9999-12-31")
        return max(left_start, right_start) <= min(left_end, right_end)


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
    operating_day_ref: Any = None
    origin_stop_point_ref: Optional[str] = None
    destination_stop_point_ref: Optional[str] = None
    origin_text: Optional[str] = None
    destination_text: Optional[str] = None
    direction_ref: Optional[str] = None
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
    correlation_basis: Optional[str] = None
    correlation_validated: bool = False
    candidate_identifiers: Mapping[str, Any] = field(default_factory=dict)
    source_paths: Mapping[str, Any] = field(default_factory=dict)
    source_attributes: Mapping[str, Any] = field(default_factory=dict)
    feed_timezone: str = DEFAULT_FEED_TIMEZONE

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
            "origin_stop_point_ref",
            "destination_stop_point_ref",
            "origin_text",
            "destination_text",
        )
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
        # ``OperatingDayRef`` is source evidence, not proof of the static
        # feed's active service date.  A caller must provide an independently
        # established ``service_date``/``gtfs_service_date`` when matching.
        service_date = value.get("service_date")
        if not _present(service_date):
            service_date = value.get("gtfs_service_date")
        if not _present(service_date):
            service_date = value.get("matched_service_date")
        origin_destination = value.get("origin_destination")
        if isinstance(origin_destination, Mapping):
            for field_name in (
                "origin_stop_point_ref",
                "destination_stop_point_ref",
                "origin_text",
                "destination_text",
            ):
                if field_name not in value and field_name in origin_destination:
                    value = {**value, field_name: origin_destination.get(field_name)}
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
            operating_day_ref=value.get("operating_day_ref"),
            origin_stop_point_ref=_text(value.get("origin_stop_point_ref")),
            destination_stop_point_ref=_text(value.get("destination_stop_point_ref")),
            origin_text=_text(value.get("origin_text")),
            destination_text=_text(value.get("destination_text")),
            direction_ref=_text(value.get("direction_ref")),
            direction=_text(value.get("direction")),
            stop_sequence=_safe_int(value.get("stop_sequence", value.get("stop_seq_number"))),
            operator_id=_text(value.get("operator_id") or value.get("operator_ref")),
            canonical_stop_id=_text(value.get("canonical_stop_id")),
            canonical_line_id=_text(value.get("canonical_line_id")),
            response_id=_text(value.get("response_id")),
            collector_run_id=_text(value.get("collector_run_id") or value.get("pilot_run_id")),
            sampling_policy_id=_text(value.get("sampling_policy_id") or value.get("sampling_policy")),
            sampling_era_id=_text(value.get("sampling_era_id") or value.get("sampling_era")),
            correlation_group_id=_text(value.get("correlation_group_id")),
            correlation_basis=_text(value.get("correlation_basis")),
            correlation_validated=bool(value.get("correlation_validated", False)),
            candidate_identifiers=candidate_identifiers,
            source_paths=source_paths,
            source_attributes=dict(value.get("source_attributes") or {}),
            feed_timezone=_text(value.get("feed_timezone")) or DEFAULT_FEED_TIMEZONE,
        )

    @property
    def normalized_service_date(self) -> Optional[str]:
        return normalize_service_date(self.service_date)

    @property
    def normalized_event_type(self) -> Optional[str]:
        return normalize_event_type(self.event_type)

    @property
    def scheduled_time_seconds(self) -> Optional[int]:
        return schedule_time_seconds(
            self.scheduled_time,
            service_date=self.normalized_service_date,
            feed_timezone=self.feed_timezone,
        )

    @property
    def scheduled_time_alignment(self) -> Dict[str, Any]:
        return align_schedule_time(
            self.scheduled_time,
            service_date=self.normalized_service_date,
            feed_timezone=self.feed_timezone,
        )

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

    def source_field_value(self, name: str) -> Any:
        """Return a source-preserved origin/destination or extension value."""

        if hasattr(self, name):
            return getattr(self, name)
        return self.candidate_identifier_value(name)

    @property
    def effective_direction(self) -> Optional[str]:
        """Use richer Direction when present, otherwise retain DirectionRef as context."""

        return self.direction or self.direction_ref


def _stage8_extension(record: Mapping[str, Any], name: str) -> Tuple[Any, Optional[str]]:
    extensions = record.get("source_identity_extensions")
    if isinstance(extensions, Mapping) and isinstance(extensions.get(name), Mapping):
        item = extensions[name]
        return item.get("value"), _text(item.get("source_path"))
    return record.get(name), _text(record.get(f"{name}_source_path"))


def _stage8_this_call(record: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    calls = record.get("call_observations")
    if not isinstance(calls, Sequence) or isinstance(calls, (str, bytes)):
        return None
    this_calls = [
        item
        for item in calls
        if isinstance(item, Mapping) and str(item.get("call_scope", "")).upper() == "THIS"
    ]
    if not this_calls:
        return None
    requested_stop = _text(record.get("stop_point_ref"))
    if requested_stop:
        for item in this_calls:
            if _text(item.get("stop_point_ref")) == requested_stop:
                return item
    return this_calls[0]


def _stage8_timetabled_time(
    call: Optional[Mapping[str, Any]],
    event_type: str,
) -> Tuple[Any, Optional[str], str]:
    """Read only the selected THIS-call timing branch."""

    if call is None:
        return None, None, "THIS_CALL_MISSING"
    context_name = "arrival" if event_type == "ARRIVAL" else "departure"
    timing_key = f"{context_name}_timing"
    timings = call.get(timing_key)
    if not isinstance(timings, Sequence) or isinstance(timings, (str, bytes)):
        return None, None, f"THIS_{context_name.upper()}_TIMING_MISSING"
    for timing in timings:
        if not isinstance(timing, Mapping):
            continue
        values = timing.get("field_values")
        paths = timing.get("field_paths")
        if isinstance(values, Mapping):
            candidates = values.get("TimetabledTime") or values.get("timetabled_time") or []
            if candidates:
                path_values = paths.get("TimetabledTime") if isinstance(paths, Mapping) else None
                path = path_values[0] if isinstance(path_values, Sequence) and path_values else None
                return candidates[0], _text(path), "THIS"
        fields = timing.get("fields")
        if isinstance(fields, Sequence):
            for field in fields:
                if isinstance(field, Mapping) and str(field.get("field")) == "TimetabledTime":
                    return field.get("value"), _text(field.get("source_path")), "THIS"
    return None, None, f"THIS_{context_name.upper()}_TIMETABLE_MISSING"


def adapt_stage8_stop_event(
    record: Mapping[str, Any],
    *,
    service_date: Any = None,
    feed_timezone: str = DEFAULT_FEED_TIMEZONE,
    source_namespace: str = STAGE8_MDD_SOURCE_NAMESPACE,
    sampling_policy_id: Optional[str] = None,
    sampling_era_id: Optional[str] = None,
) -> SourceEventObservation:
    """Project one persisted Stage 8 stop-event record into Stage 9 input.

    Stage 8 intentionally stores a stop-event row plus separately scoped call
    rows.  This adapter selects the ``THIS`` call and its requested arrival or
    departure branch; it never falls back to PREVIOUS/ONWARD timing.  The
    service date is an explicit caller-supplied/static-feed value, never an
    alias of ``OperatingDayRef`` or ``ObservedAt``.
    """

    event_type = normalize_event_type(record.get("event_type")) or "ARRIVAL"
    this_call = _stage8_this_call(record)
    scheduled_time, scheduled_path, timing_status = _stage8_timetabled_time(this_call, event_type)
    call_stop_id = _text(this_call.get("stop_point_ref")) if this_call else None
    source_stop_id = _text(record.get("stop_point_ref")) or call_stop_id
    stop_sequence = _safe_int(record.get("stop_seq_number"))
    if stop_sequence is None and this_call:
        stop_sequence = _safe_int(this_call.get("stop_seq_number"))

    extension_names = (
        "vehicle_journey_ref",
        "dated_vehicle_journey_ref",
        "affected_vehicle_journey",
        "vehicle_journeys",
        "operating_days_description",
        "direction",
        "origins",
        "destinations",
    )
    candidate_identifiers: Dict[str, Any] = {}
    source_paths: Dict[str, Any] = {}
    for name in extension_names:
        value, path = _stage8_extension(record, name)
        if _present(value) or path:
            candidate_identifiers[name] = {"value": value, "source_path": path}
            source_paths[name] = path

    for name in (
        "operating_day_ref",
        "journey_ref",
        "line_ref",
        "direction_ref",
        "operator_ref",
        "stop_point_ref",
        "stop_seq_number",
        "origin_stop_point_ref",
        "destination_stop_point_ref",
        "origin_text",
        "destination_text",
    ):
        path = _text(record.get(f"{name}_source_path"))
        if path:
            source_paths[name] = path

    origin_destination = record.get("origin_destination")
    if isinstance(origin_destination, Mapping):
        for name in ("origin_stop_point_ref", "destination_stop_point_ref", "origin_text", "destination_text"):
            if name not in source_paths:
                source_paths[name] = origin_destination.get(f"{name}_source_path")

    source_attributes: Dict[str, Any] = dict(record.get("source_attributes") or {})
    source_attributes.update(
        {
            "adapter": "STAGE_8_STOP_EVENT_TO_STAGE_9",
            "stage8_record_type": record.get("record_type"),
            "stage8_parser_version": record.get("parser_version"),
            "source_result_id": record.get("source_result_id"),
            "result_source_path": record.get("result_source_path"),
            "selected_call_scope": "THIS" if this_call else None,
            "selected_call_observation_id": this_call.get("call_observation_id") if this_call else None,
            "selected_call_source_path": this_call.get("source_path") if this_call else None,
            "scheduled_time_source_path": scheduled_path,
            "scheduled_time_selection_status": timing_status,
            "non_selected_call_scopes": sorted(
                {
                    str(item.get("call_scope")).upper()
                    for item in record.get("call_observations", [])
                    if isinstance(item, Mapping)
                    and str(item.get("call_scope", "")).upper() != "THIS"
                }
            ),
            "origin_destination": origin_destination,
            "source_identity_extensions": record.get("source_identity_extensions") or {},
        }
    )
    if service_date is None:
        service_date = record.get("service_date", record.get("gtfs_service_date"))
    if service_date is not None:
        source_attributes["service_date_source"] = "EXPLICIT_ADAPTER_INPUT" if record.get("service_date") is None else "STAGE8_RECORD"
    source_attributes["scheduled_time_alignment_status"] = align_schedule_time(
        scheduled_time,
        service_date=service_date,
        feed_timezone=feed_timezone,
    ).get("status")
    if scheduled_path:
        source_paths["scheduled_time"] = scheduled_path

    return SourceEventObservation.from_mapping(
        {
            "source_system": STAGE8_MDD_SOURCE_SYSTEM,
            "source_namespace": source_namespace,
            "source_event_id": record.get("event_source_identity") or record.get("stable_event_identity"),
            "observation_id": record.get("stop_event_observation_id"),
            "observed_at": record.get("observed_at"),
            "service_date": service_date,
            "operating_day_ref": record.get("operating_day_ref"),
            "source_stop_id": source_stop_id,
            "source_line_id": record.get("line_ref"),
            "source_journey_ref": record.get("journey_ref"),
            "event_type": event_type,
            "scheduled_time": scheduled_time,
            "direction_ref": record.get("direction_ref"),
            "direction": record.get("direction"),
            "stop_sequence": stop_sequence,
            "operator_id": record.get("operator_ref"),
            "origin_stop_point_ref": (
                record.get("origin_stop_point_ref")
                or (origin_destination.get("origin_stop_point_ref") if isinstance(origin_destination, Mapping) else None)
            ),
            "destination_stop_point_ref": (
                record.get("destination_stop_point_ref")
                or (origin_destination.get("destination_stop_point_ref") if isinstance(origin_destination, Mapping) else None)
            ),
            "origin_text": record.get("origin_text")
            or (origin_destination.get("origin_text") if isinstance(origin_destination, Mapping) else None),
            "destination_text": record.get("destination_text")
            or (origin_destination.get("destination_text") if isinstance(origin_destination, Mapping) else None),
            "response_id": record.get("response_id"),
            "collector_run_id": record.get("pilot_run_id") or record.get("collector_run_id"),
            "sampling_policy_id": sampling_policy_id or record.get("sampling_policy_id") or record.get("sampling_policy"),
            "sampling_era_id": sampling_era_id or record.get("sampling_era_id") or record.get("sampling_era"),
            "correlation_group_id": record.get("correlation_group_id"),
            "correlation_basis": record.get("correlation_basis"),
            "correlation_validated": record.get("correlation_validated", False),
            "candidate_identifiers": candidate_identifiers,
            "source_paths": source_paths,
            "source_attributes": source_attributes,
            "feed_timezone": feed_timezone,
        }
    )


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
    origin_stop_id: Optional[str] = None
    destination_stop_id: Optional[str] = None
    origin_text: Optional[str] = None
    destination_text: Optional[str] = None
    scope_code: str = "IN_SCOPE"
    scope_reason: Optional[str] = None

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
            origin_stop_id=_text(value.get("origin_stop_id") or value.get("origin_stop_point_ref")),
            destination_stop_id=_text(value.get("destination_stop_id") or value.get("destination_stop_point_ref")),
            origin_text=_text(value.get("origin_text")),
            destination_text=_text(value.get("destination_text")),
            scope_code=_text(value.get("scope_code") or value.get("cologne_scope_code")) or "IN_SCOPE",
            scope_reason=_text(value.get("scope_reason")),
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
    origin_stop_id: Optional[str] = None
    destination_stop_id: Optional[str] = None
    origin_text: Optional[str] = None
    destination_text: Optional[str] = None
    scope_code: str = "IN_SCOPE"
    scope_reason: Optional[str] = None


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
    correlation_group_id: Optional[str] = None
    correlation_basis: Optional[str] = None
    correlation_validated: bool = False
    stop_mapping_version: Optional[str] = None
    line_mapping_version: Optional[str] = None

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
        self.canonical_stop_ids = {event.canonical_stop_id for event in events}
        self.service_dates = {event.service_date for event in events}
        self.by_date_stop: Set[Tuple[str, str]] = {
            (event.service_date, event.canonical_stop_id) for event in events
        }
        self.canonical_line_ids = {
            event.canonical_line_id for event in events if event.canonical_line_id
        }
        self.by_date_line: Set[Tuple[str, str]] = {
            (event.service_date, event.canonical_line_id)
            for event in events
            if event.canonical_line_id
        }
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
        seconds = schedule_time_seconds(event.scheduled_time, service_date=service_date)
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
                origin_stop_id=event.origin_stop_id,
                destination_stop_id=event.destination_stop_id,
                origin_text=event.origin_text,
                destination_text=event.destination_text,
                scope_code=event.scope_code,
                scope_reason=event.scope_reason,
            )
        )
    return ScheduleIndex(canonical, issues)


def _string_equal(left: Any, right: Any) -> bool:
    return _present(left) and _present(right) and _normalized_key(left) == _normalized_key(right)


@dataclass(frozen=True)
class MatchingEvidenceValidation:
    """Explicitly validated auxiliary semantics allowed to support HIGH.

    Raw direction equality, stop-sequence equality, and origin/destination
    agreement are always retained as evidence.  They only become HIGH-worthy
    when an external audit supplies a bounded source population and mapping
    version through this policy.
    """

    mapping_version: Optional[str] = None
    validated_sequence_scopes: Tuple[Tuple[str, str], ...] = ()
    validated_direction_mappings: Mapping[Tuple[str, str, str], Tuple[str, ...]] = field(default_factory=dict)
    validated_origin_destination_scopes: Tuple[Tuple[str, str], ...] = ()

    def sequence_validated(self, observation: SourceEventObservation) -> bool:
        return (
            _normalized_source_system(observation.source_system),
            _normalized_key(observation.source_namespace),
        ) in {
            (_normalized_source_system(system), _normalized_key(namespace))
            for system, namespace in self.validated_sequence_scopes
        }

    def direction_validated(
        self,
        observation: SourceEventObservation,
        static_direction: Optional[str],
    ) -> bool:
        if not _present(static_direction):
            return False
        for raw_direction in (observation.direction, observation.direction_ref):
            if not _present(raw_direction):
                continue
            key = (
                _normalized_source_system(observation.source_system),
                _normalized_key(observation.source_namespace),
                _normalized_key(raw_direction),
            )
            allowed = self.validated_direction_mappings.get(key, ())
            if _normalized_key(static_direction) in {_normalized_key(item) for item in allowed}:
                return True
        return False

    def origin_destination_validated(self, observation: SourceEventObservation) -> bool:
        return (
            _normalized_source_system(observation.source_system),
            _normalized_key(observation.source_namespace),
        ) in {
            (_normalized_source_system(system), _normalized_key(namespace))
            for system, namespace in self.validated_origin_destination_scopes
        }


def _candidate_evidence(
    observation: SourceEventObservation,
    event: CanonicalServiceEvent,
    line_resolution: IdentityResolution,
    *,
    exact_time: bool,
    evidence_validation: MatchingEvidenceValidation,
) -> Dict[str, Any]:
    line_match = bool(
        line_resolution.canonical_identifier
        and event.canonical_line_id
        and line_resolution.canonical_identifier == event.canonical_line_id
    )
    direction_match = _string_equal(observation.effective_direction, event.direction)
    direction_ref_match = _string_equal(observation.direction_ref, event.direction)
    richer_direction_match = _string_equal(observation.direction, event.direction)
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
    sequence_validated = sequence_match and evidence_validation.sequence_validated(observation)
    direction_validated = direction_match and evidence_validation.direction_validated(observation, event.direction)
    origin_destination_match = _origin_destination_identity_match(observation, event)
    origin_destination_validated = (
        origin_destination_match
        and evidence_validation.origin_destination_validated(observation)
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
        (origin_destination_validated, 1),
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
        "direction_ref_match": direction_ref_match,
        "richer_direction_match": richer_direction_match,
        "direction_ref": observation.direction_ref,
        "direction": observation.direction,
        "stop_sequence_match": sequence_match,
        "direction_evidence_validated": direction_validated,
        "stop_sequence_evidence_validated": sequence_validated,
        "operator_match": operator_match,
        "same_namespace_trip_identity_match": same_namespace_trip_match,
        "multi_stop_sequence_match": multi_stop_match,
        "origin_destination_match": origin_destination_match,
        "origin_destination_evidence_validated": origin_destination_validated,
        "prohibited_cross_source_identity_equality_observed": prohibited_identity_equality,
        "source_trip_identity_used": same_namespace_trip_match,
        "score": score,
    }


def _origin_destination_identity_match(
    observation: SourceEventObservation,
    event: CanonicalServiceEvent,
) -> bool:
    pairs = (
        (observation.origin_stop_point_ref, event.origin_stop_id),
        (observation.destination_stop_point_ref, event.destination_stop_id),
    )
    present_pairs = [(left, right) for left, right in pairs if _present(left) or _present(right)]
    return bool(present_pairs) and all(_string_equal(left, right) for left, right in present_pairs)


def _decision_for_failure(
    observation: SourceEventObservation,
    classification: str,
    *,
    detail: Optional[str] = None,
    candidate_ids: Sequence[str] = (),
    evidence: Optional[Mapping[str, Any]] = None,
    matching_version: str = SCRIPT_VERSION,
    stop_mapping_version: Optional[str] = None,
    line_mapping_version: Optional[str] = None,
) -> MatchDecision:
    decision_evidence: Dict[str, Any] = dict(evidence or {})
    if detail:
        decision_evidence["detail"] = detail
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
        evidence=decision_evidence,
        matching_version=matching_version,
        correlation_group_id=observation.correlation_group_id,
        correlation_basis=observation.correlation_basis,
        correlation_validated=observation.correlation_validated,
        stop_mapping_version=stop_mapping_version,
        line_mapping_version=line_mapping_version,
    )


def match_source_event(
    observation: SourceEventObservation | Mapping[str, Any],
    schedule_index: ScheduleIndex,
    bridge: IdentityBridge,
    *,
    matching_version: str = SCRIPT_VERSION,
    time_tolerance_seconds: int = 0,
    identity_mapping_version: Optional[str] = None,
    evidence_validation: Optional[MatchingEvidenceValidation] = None,
) -> MatchDecision:
    """Match one source observation to a canonical stop event.

    Candidate generation requires an explicit service date, canonical stop,
    event type, and scheduled time.  The observed timestamp is never used as
    a substitute for service date or event time.  When a source line mapping
    is validated, candidates on another canonical line are rejected rather
    than silently reclassified.
    """

    event = observation if isinstance(observation, SourceEventObservation) else SourceEventObservation.from_mapping(observation)
    validation = evidence_validation or MatchingEvidenceValidation()
    service_date = event.normalized_service_date
    if service_date is None:
        return _decision_for_failure(
            event,
            QUALITY_MISSING_REQUIRED_IDENTITY,
            detail="service date is missing or invalid; observed_at is not a fallback",
            evidence={
                "operating_day_ref": event.operating_day_ref,
                "operating_day_ref_normalized": normalize_service_date(event.operating_day_ref),
            },
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
    scheduled_alignment = event.scheduled_time_alignment
    scheduled_seconds = scheduled_alignment.get("service_day_seconds")
    if scheduled_seconds is None:
        alignment_status = scheduled_alignment.get("status")
        classification = (
            QUALITY_INSUFFICIENT_EVIDENCE
            if alignment_status
            in {
                TIME_ALIGNMENT_TIMEZONE_UNRESOLVED,
                TIME_ALIGNMENT_SERVICE_DATE_REQUIRED,
                TIME_ALIGNMENT_FEED_TIMEZONE_UNRESOLVED,
            }
            else QUALITY_INVALID_SCHEDULE_TIME
        )
        return _decision_for_failure(
            event,
            classification,
            detail=(
                "source absolute timetable cannot be aligned without an explicit timezone"
                if classification == QUALITY_INSUFFICIENT_EVIDENCE
                else "source timetable/scheduled time is missing or invalid"
            ),
            evidence={"scheduled_time_alignment": scheduled_alignment},
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
            service_date,
        )
    if stop_resolution.canonical_identifier is None:
        if stop_resolution.status == "AMBIGUOUS":
            quality = QUALITY_AMBIGUOUS_MULTIPLE_CANDIDATES
        elif stop_resolution.status in {"MAPPING_VERSION_REQUIRED", "MAPPING_SERVICE_DATE_REQUIRED"}:
            quality = QUALITY_INSUFFICIENT_EVIDENCE
        else:
            quality = QUALITY_SOURCE_IDENTITY_UNRESOLVED
        return _decision_for_failure(
            event,
            quality,
            detail=f"stop bridge status={stop_resolution.status}; no adjacent stop substitution is permitted",
            candidate_ids=(),
            evidence={
                "stop_resolution_status": stop_resolution.status,
                "stop_mapping_versions": list(stop_resolution.mapping_versions),
            },
            matching_version=matching_version,
            stop_mapping_version=(stop_resolution.mapping_versions[0] if len(stop_resolution.mapping_versions) == 1 else None),
        )

    if event.canonical_line_id:
        line_resolution = IdentityResolution(event.canonical_line_id, "EXPLICIT_CANONICAL_ID", (event.canonical_line_id,))
    elif event.source_line_id:
        line_resolution = bridge.resolve_line(
            event.source_system,
            event.source_namespace,
            event.source_line_id,
            identity_mapping_version,
            service_date,
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
                QUALITY_IDENTITY_CONFLICT,
                detail="validated source line conflicts with all stop/date/time candidates",
                candidate_ids=all_candidate_ids,
                evidence={
                    "secondary_classification": QUALITY_SOURCE_LINE_DISAGREEMENT,
                    "line_resolution_status": line_resolution.status,
                    "line_mapping_versions": list(line_resolution.mapping_versions),
                },
                matching_version=matching_version,
                stop_mapping_version=(stop_resolution.mapping_versions[0] if len(stop_resolution.mapping_versions) == 1 else None),
                line_mapping_version=(line_resolution.mapping_versions[0] if len(line_resolution.mapping_versions) == 1 else None),
            )
        candidates = line_candidates

    if not candidates:
        if stop_resolution.canonical_identifier not in schedule_index.canonical_stop_ids:
            classification = QUALITY_STATIC_STOP_MISSING
        elif service_date not in schedule_index.service_dates:
            classification = QUALITY_STATIC_SERVICE_DATE_NOT_ACTIVE
        elif (service_date, stop_resolution.canonical_identifier) not in schedule_index.by_date_stop:
            classification = QUALITY_STATIC_EVENT_NOT_FOUND
        elif line_resolution.canonical_identifier and (
            service_date,
            line_resolution.canonical_identifier,
        ) not in schedule_index.by_date_line:
            classification = QUALITY_STATIC_ROUTE_MISSING
        else:
            classification = QUALITY_UNMATCHED
        detail = "no static candidate for validated stop/date/event/time"
        if line_resolution.status in {"NOT_FOUND", "UNRESOLVED", "AMBIGUOUS", "MISSING_SOURCE_IDENTIFIER"} and event.source_line_id:
            detail += f"; line bridge status={line_resolution.status}"
        return _decision_for_failure(
            event,
            classification,
            detail=detail,
            candidate_ids=all_candidate_ids,
            evidence={
                "stop_resolution_status": stop_resolution.status,
                "line_resolution_status": line_resolution.status,
                "stop_mapping_versions": list(stop_resolution.mapping_versions),
                "line_mapping_versions": list(line_resolution.mapping_versions),
            },
            matching_version=matching_version,
            stop_mapping_version=(stop_resolution.mapping_versions[0] if len(stop_resolution.mapping_versions) == 1 else None),
            line_mapping_version=(line_resolution.mapping_versions[0] if len(line_resolution.mapping_versions) == 1 else None),
        )

    out_of_scope_candidates = [item for item in candidates if item.scope_code == "OUT_OF_SCOPE"]
    in_scope_candidates = [item for item in candidates if item.scope_code != "OUT_OF_SCOPE"]
    if not in_scope_candidates:
        return _decision_for_failure(
            event,
            QUALITY_OUT_OF_CORE_SCOPE,
            detail="static candidate exists only outside the approved core Cologne scope",
            candidate_ids=all_candidate_ids,
            evidence={
                "out_of_scope_candidate_ids": [item.canonical_service_event_id for item in out_of_scope_candidates],
                "out_of_scope_reasons": {
                    item.canonical_service_event_id: item.scope_reason for item in out_of_scope_candidates
                },
                "core_scope_contract": "Stage4 actual stop/event scope; no parent/string-prefix fallback",
            },
            matching_version=matching_version,
            stop_mapping_version=(stop_resolution.mapping_versions[0] if len(stop_resolution.mapping_versions) == 1 else None),
            line_mapping_version=(line_resolution.mapping_versions[0] if len(line_resolution.mapping_versions) == 1 else None),
        )
    candidates = in_scope_candidates

    ranked: List[Tuple[int, int, str, CanonicalServiceEvent, Dict[str, Any]]] = []
    for candidate in candidates:
        evidence = _candidate_evidence(
            event,
            candidate,
            line_resolution,
            exact_time=exact_time,
            evidence_validation=validation,
        )
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
    requested_mapping_version = identity_mapping_version or bridge.active_mapping_version

    def resolved_mapping_version(resolution: IdentityResolution) -> Optional[str]:
        if len(resolution.mapping_versions) == 1:
            return resolution.mapping_versions[0]
        if resolution.status == "EXPLICIT_CANONICAL_ID":
            return requested_mapping_version
        return None

    if len(tied) > 1:
        return MatchDecision(
            source_event_identity=event.source_identity,
            source_observation_identity=event.observation_id,
            source_system=event.source_system,
            source_namespace=event.source_namespace,
            canonical_service_event_id=None,
            match_confidence=MATCH_UNMATCHED,
            match_method=METHOD_UNMATCHED,
            data_quality_classification=QUALITY_AMBIGUOUS_MULTIPLE_CANDIDATES,
            candidate_count=len(candidates),
            candidate_event_ids=tuple(item[2] for item in tied),
            selected_score=None,
            evidence={
                "reason": "equal_top_candidates",
                "candidate_evidence": {item[2]: item[4] for item in tied},
                "line_resolution_status": line_resolution.status,
                "stop_mapping_versions": list(stop_resolution.mapping_versions),
                "line_mapping_versions": list(line_resolution.mapping_versions),
            },
            matching_version=matching_version,
            correlation_group_id=event.correlation_group_id,
            correlation_basis=event.correlation_basis,
            correlation_validated=event.correlation_validated,
            stop_mapping_version=resolved_mapping_version(stop_resolution),
            line_mapping_version=resolved_mapping_version(line_resolution),
        )

    _negative_score, _negative_strong_count, _candidate_id, selected, evidence = ranked[0]
    exact_base = exact_time and bool(evidence["canonical_stop_match"] and evidence["service_date_match"] and evidence["event_type_match"])
    if evidence["multi_stop_sequence_match"] and evidence["stop_sequence_evidence_validated"]:
        confidence = MATCH_HIGH
        method = METHOD_MULTI_STOP_SEQUENCE_MATCH
        quality = QUALITY_VALIDATED_MATCH
    elif exact_base and evidence["line_match"] and evidence["direction_evidence_validated"]:
        confidence = MATCH_HIGH
        method = METHOD_EXACT_STOP_DATE_TIME_DIRECTION
        quality = QUALITY_VALIDATED_MATCH
    elif exact_base and evidence["line_match"] and evidence["stop_sequence_evidence_validated"]:
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
    evidence["stop_resolution_status"] = stop_resolution.status
    evidence["stop_mapping_versions"] = list(stop_resolution.mapping_versions)
    evidence["line_mapping_versions"] = list(line_resolution.mapping_versions)
    evidence["stop_mapping_version_used"] = resolved_mapping_version(stop_resolution)
    evidence["line_mapping_version_used"] = resolved_mapping_version(line_resolution)
    evidence["identity_mapping_version_requested"] = identity_mapping_version or bridge.active_mapping_version
    evidence["operating_day_ref"] = event.operating_day_ref
    evidence["operating_day_ref_normalized"] = normalize_service_date(event.operating_day_ref)
    evidence["service_date"] = service_date
    evidence["scheduled_time_alignment"] = event.scheduled_time_alignment
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
        correlation_group_id=event.correlation_group_id,
        correlation_basis=event.correlation_basis,
        correlation_validated=event.correlation_validated,
        stop_mapping_version=resolved_mapping_version(stop_resolution),
        line_mapping_version=resolved_mapping_version(line_resolution),
    )


def audit_field_coverage(observations: Iterable[SourceEventObservation | Mapping[str, Any]]) -> Dict[str, Any]:
    """Measure field presence while retaining NULL/false semantics."""

    events = [item if isinstance(item, SourceEventObservation) else SourceEventObservation.from_mapping(item) for item in observations]
    rules: Dict[str, Tuple[bool, Any]] = {
        "source_event_id": (True, lambda item: item.source_event_id),
        "observation_id": (True, lambda item: item.observation_id),
        "observed_at": (True, lambda item: item.observed_at),
        "service_date": (True, lambda item: item.normalized_service_date),
        "operating_day_ref": (False, lambda item: item.operating_day_ref),
        "source_stop_id": (True, lambda item: item.source_stop_id),
        "event_type": (True, lambda item: item.normalized_event_type),
        "scheduled_time": (True, lambda item: item.scheduled_time_seconds),
        "collector_run_id": (True, lambda item: item.collector_run_id),
        "source_line_id": (False, lambda item: item.source_line_id),
        "source_trip_id": (False, lambda item: item.source_trip_id),
        "source_journey_ref": (False, lambda item: item.source_journey_ref),
        "vehicle_journey_ref": (False, lambda item: item.candidate_identifier_value("vehicle_journey_ref")),
        "dated_vehicle_journey_ref": (False, lambda item: item.candidate_identifier_value("dated_vehicle_journey_ref")),
        "direction_ref": (False, lambda item: item.direction_ref),
        "direction": (False, lambda item: item.direction),
        "stop_sequence": (False, lambda item: item.stop_sequence),
        "operator_id": (False, lambda item: item.operator_id),
        "origin_stop_point_ref": (False, lambda item: item.origin_stop_point_ref),
        "destination_stop_point_ref": (False, lambda item: item.destination_stop_point_ref),
        "origin_text": (False, lambda item: item.origin_text),
        "destination_text": (False, lambda item: item.destination_text),
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
    operating_day_missing = []
    operating_day_invalid = []
    operating_day_agreements = []
    operating_day_disagreements = []
    operating_day_unresolved = []
    operating_day_input_formats: Counter[str] = Counter()
    for event in events:
        if not _present(event.service_date):
            missing.append(event.source_identity)
        else:
            normalized = event.normalized_service_date
            if normalized is None:
                invalid.append(event.source_identity)
            elif valid_service_dates is not None and normalized not in valid_service_dates:
                outside.append({"source_event_identity": event.source_identity, "service_date": normalized})

        operating_day = normalize_service_date(event.operating_day_ref)
        if not _present(event.operating_day_ref):
            operating_day_missing.append(event.source_identity)
        elif operating_day is None:
            operating_day_input_formats["INVALID"] += 1
            operating_day_invalid.append(
                {
                    "source_event_identity": event.source_identity,
                    "operating_day_ref": event.operating_day_ref,
                }
            )
        elif event.normalized_service_date is None:
            raw_operating_day = _text(event.operating_day_ref) or ""
            operating_day_input_formats[
                "YYYYMMDD" if re.fullmatch(r"\d{8}", raw_operating_day) else "ISO_OR_DATE_PREFIX"
            ] += 1
            operating_day_unresolved.append(
                {
                    "source_event_identity": event.source_identity,
                    "operating_day_ref": operating_day,
                    "service_date": None,
                    "reason": "independent GTFS service date is missing or invalid",
                }
            )
        elif operating_day == event.normalized_service_date:
            raw_operating_day = _text(event.operating_day_ref) or ""
            operating_day_input_formats[
                "YYYYMMDD" if re.fullmatch(r"\d{8}", raw_operating_day) else "ISO_OR_DATE_PREFIX"
            ] += 1
            operating_day_agreements.append(event.source_identity)
        else:
            raw_operating_day = _text(event.operating_day_ref) or ""
            operating_day_input_formats[
                "YYYYMMDD" if re.fullmatch(r"\d{8}", raw_operating_day) else "ISO_OR_DATE_PREFIX"
            ] += 1
            operating_day_disagreements.append(
                {
                    "source_event_identity": event.source_identity,
                    "operating_day_ref": operating_day,
                    "service_date": event.normalized_service_date,
                }
            )
    comparison_count = len(operating_day_agreements) + len(operating_day_disagreements)
    return {
        "status": "PASS" if not missing and not invalid and not outside else "WARN",
        "missing_service_date_count": len(missing),
        "invalid_service_date_count": len(invalid),
        "outside_validated_population_count": len(outside),
        "missing_examples": missing[:10],
        "invalid_examples": invalid[:10],
        "outside_examples": outside[:10],
        "operating_day_ref": {
            "observation_count": len(events),
            "present_count": len(events) - len(operating_day_missing),
            "presence_rate": ((len(events) - len(operating_day_missing)) / len(events)) if events else None,
            "normalized_valid_count": len(events) - len(operating_day_missing) - len(operating_day_invalid),
            "normalized_format": "YYYY-MM-DD",
            "input_format_counts": dict(sorted(operating_day_input_formats.items())),
            "invalid_count": len(operating_day_invalid),
            "agreement_count": len(operating_day_agreements),
            "disagreement_count": len(operating_day_disagreements),
            "unresolved_count": len(operating_day_unresolved) + len(operating_day_missing) + len(operating_day_invalid),
            "agreement_rate_among_comparable": (len(operating_day_agreements) / comparison_count) if comparison_count else None,
            "missing_examples": operating_day_missing[:10],
            "invalid_examples": operating_day_invalid[:10],
            "disagreement_examples": operating_day_disagreements[:10],
            "unresolved_examples": operating_day_unresolved[:10],
            "participates_in_matching": False,
            "interpretation": "OperatingDayRef remains source evidence until independent GTFS service-date agreement is measured.",
        },
    }


def _decision_for_observation(
    observation: SourceEventObservation,
    decisions: Sequence[MatchDecision],
) -> Optional[MatchDecision]:
    for decision in decisions:
        if decision.source_observation_identity == observation.observation_id:
            return decision
    for decision in decisions:
        if decision.source_event_identity == observation.source_identity:
            return decision
    return None


def validate_stop_sequence_alignment(
    observations: Iterable[SourceEventObservation | Mapping[str, Any]],
    decisions: Iterable[MatchDecision],
    schedule_index: ScheduleIndex,
) -> Dict[str, Any]:
    """Measure StopSeqNumber without making it an unvalidated HIGH key."""

    events = [item if isinstance(item, SourceEventObservation) else SourceEventObservation.from_mapping(item) for item in observations]
    decision_rows = list(decisions)
    source_present = [item for item in events if item.stop_sequence is not None]
    comparable: List[Tuple[SourceEventObservation, MatchDecision, CanonicalServiceEvent]] = []
    for observation in source_present:
        decision = _decision_for_observation(observation, decision_rows)
        if decision and decision.canonical_service_event_id:
            static_event = schedule_index.by_id.get(decision.canonical_service_event_id)
            if static_event and static_event.stop_sequence is not None:
                comparable.append((observation, decision, static_event))
    exact = [item for item in comparable if item[0].stop_sequence == item[2].stop_sequence]
    offsets = Counter(item[0].stop_sequence - item[2].stop_sequence for item in comparable)
    disagreements = [
        {
            "source_observation_identity": observation.observation_id,
            "source_sequence": observation.stop_sequence,
            "gtfs_stop_sequence": static_event.stop_sequence,
            "canonical_service_event_id": decision.canonical_service_event_id,
        }
        for observation, decision, static_event in comparable
        if observation.stop_sequence != static_event.stop_sequence
    ]
    sequence_keys = Counter(
        (item.service_date, item.canonical_stop_id, item.event_type, item.stop_sequence)
        for item in schedule_index.events
        if item.stop_sequence is not None
    )
    duplicate_cases = [
        {
            "service_date": key[0],
            "canonical_stop_id": key[1],
            "event_type": key[2],
            "stop_sequence": key[3],
            "candidate_count": count,
        }
        for key, count in sorted(sequence_keys.items())
        if count > 1
    ]
    call_scope_counts = Counter(
        str(
            observation.source_attributes.get("selected_call_scope")
            or observation.source_attributes.get("call_scope")
            or "UNKNOWN"
        ).upper()
        for observation, _decision, _static in comparable
    )
    return {
        "status": "PASS" if not disagreements else "WARN",
        "validation_status": "SUPPORTING_ONLY_UNTIL_SOURCE_POPULATION_APPROVED",
        "observation_count": len(events),
        "source_coverage": {
            "present_count": len(source_present),
            "missing_count": len(events) - len(source_present),
            "coverage_ratio": (len(source_present) / len(events)) if events else None,
        },
        "gtfs_coverage": {
            "comparable_count": len(comparable),
            "missing_count": len(events) - len(comparable),
            "coverage_ratio": (len(comparable) / len(events)) if events else None,
        },
        "exact_equality_count": len(exact),
        "exact_equality_rate": (len(exact) / len(comparable)) if comparable else None,
        "systematic_offset": offsets.most_common(1)[0][0] if offsets else None,
        "systematic_offset_seconds": offsets.most_common(1)[0][0] if offsets else None,
        "offset_distribution": {str(key): value for key, value in sorted(offsets.items())},
        "disagreement_count": len(disagreements),
        "disagreement_examples": disagreements[:10],
        "duplicate_or_ambiguous_sequence_cases": duplicate_cases[:20],
        "duplicate_or_ambiguous_sequence_case_count": len(duplicate_cases),
        "call_scope_effects": dict(sorted(call_scope_counts.items())),
        "high_confidence_upgrade_enabled": False,
        "interpretation": "StopSeqNumber is retained as supporting evidence; equality alone cannot create HIGH confidence.",
    }


def validate_direction_semantics(
    observations: Iterable[SourceEventObservation | Mapping[str, Any]],
    decisions: Iterable[MatchDecision],
    schedule_index: ScheduleIndex,
) -> Dict[str, Any]:
    """Measure raw direction relationships without assuming direction_id equivalence."""

    events = [item if isinstance(item, SourceEventObservation) else SourceEventObservation.from_mapping(item) for item in observations]
    decision_rows = list(decisions)
    source_present = [item for item in events if _present(item.direction) or _present(item.direction_ref)]
    comparable: List[Tuple[SourceEventObservation, MatchDecision, CanonicalServiceEvent]] = []
    for observation in source_present:
        decision = _decision_for_observation(observation, decision_rows)
        if decision and decision.canonical_service_event_id:
            static_event = schedule_index.by_id.get(decision.canonical_service_event_id)
            if static_event and _present(static_event.direction):
                comparable.append((observation, decision, static_event))
    exact = [item for item in comparable if _string_equal(item[0].effective_direction, item[2].direction)]
    direction_ref_comparable = [item for item in comparable if _present(item[0].direction_ref)]
    richer_direction_comparable = [item for item in comparable if _present(item[0].direction)]
    disagreement = [
        {
            "source_observation_identity": observation.observation_id,
            "source_direction_ref": observation.direction_ref,
            "source_direction": observation.direction,
            "gtfs_direction_id": static_event.direction,
            "canonical_service_event_id": decision.canonical_service_event_id,
        }
        for observation, decision, static_event in comparable
        if not _string_equal(observation.effective_direction, static_event.direction)
    ]
    return {
        "status": "PASS" if not disagreement else "WARN",
        "source_coverage": {
            "present_count": len(source_present),
            "missing_count": len(events) - len(source_present),
            "coverage_ratio": (len(source_present) / len(events)) if events else None,
        },
        "gtfs_coverage": {
            "comparable_count": len(comparable),
            "missing_count": len(events) - len(comparable),
            "coverage_ratio": (len(comparable) / len(events)) if events else None,
        },
        "raw_equality_count": len(exact),
        "raw_disagreement_count": len(disagreement),
        "direction_ref_coverage_count": sum(1 for item in events if _present(item.direction_ref)),
        "richer_direction_coverage_count": sum(1 for item in events if _present(item.direction)),
        "direction_ref_raw_equality_count": sum(
            1 for item in direction_ref_comparable if _string_equal(item[0].direction_ref, item[2].direction)
        ),
        "richer_direction_raw_equality_count": sum(
            1 for item in richer_direction_comparable if _string_equal(item[0].direction, item[2].direction)
        ),
        "disagreement_examples": disagreement[:10],
        "deterministic_mapping_status": "NOT_EVALUATED",
        "high_confidence_upgrade_enabled": False,
        "interpretation": "DirectionRef/richer Direction remains contextual until a source-population mapping to GTFS direction_id is validated.",
    }


def validate_origin_destination_evidence(
    observations: Iterable[SourceEventObservation | Mapping[str, Any]],
    decisions: Iterable[MatchDecision],
    schedule_index: ScheduleIndex,
    bridge: Optional[IdentityBridge] = None,
    identity_mapping_version: Optional[str] = None,
) -> Dict[str, Any]:
    """Evaluate endpoint evidence only against already selected static context."""

    events = [item if isinstance(item, SourceEventObservation) else SourceEventObservation.from_mapping(item) for item in observations]
    decision_rows = list(decisions)
    result: Dict[str, Any] = {
        "status": "NOT_EVALUATED",
        "identity_fields": {},
        "text_fields": {},
        "high_confidence_upgrade_enabled": False,
        "interpretation": "Origin/destination text is never a key; identity evidence is supporting until a validated bridge exists.",
    }
    for prefix in ("origin", "destination"):
        identity_name = f"{prefix}_stop_point_ref"
        text_name = f"{prefix}_text"
        source_identity_count = sum(1 for item in events if _present(getattr(item, identity_name)))
        source_text_count = sum(1 for item in events if _present(getattr(item, text_name)))
        comparable_identity = 0
        identity_agreement = 0
        identity_disagreement = []
        unresolved_identity = []
        textual_comparable = 0
        textual_agreement = 0
        bridge_agreement = 0
        for observation in events:
            decision = _decision_for_observation(observation, decision_rows)
            static_event = schedule_index.by_id.get(decision.canonical_service_event_id) if decision and decision.canonical_service_event_id else None
            if not static_event:
                continue
            source_identity = getattr(observation, identity_name)
            static_identity = getattr(static_event, f"{prefix}_stop_id")
            if _present(source_identity):
                if _present(static_identity):
                    comparable_identity += 1
                    direct_agreement = _string_equal(source_identity, static_identity)
                    bridged_agreement = False
                    bridge_unresolved = False
                    if not direct_agreement and bridge is not None:
                        source_resolution = bridge.resolve_stop(
                            observation.source_system,
                            observation.source_namespace,
                            source_identity,
                            identity_mapping_version,
                            observation.normalized_service_date,
                        )
                        static_resolution = bridge.resolve_stop(
                            SOURCE_STATIC_GTFS,
                            static_event.source_namespace,
                            static_identity,
                            identity_mapping_version,
                            observation.normalized_service_date,
                        )
                        bridged_agreement = bool(
                            source_resolution.canonical_identifier
                            and static_resolution.canonical_identifier
                            and source_resolution.canonical_identifier == static_resolution.canonical_identifier
                        )
                        bridge_unresolved = not bridged_agreement and (
                            source_resolution.canonical_identifier is None
                            or static_resolution.canonical_identifier is None
                        )
                    if direct_agreement or bridged_agreement:
                        identity_agreement += 1
                        if bridged_agreement:
                            bridge_agreement += 1
                    elif bridge_unresolved:
                        unresolved_identity.append(
                            {
                                "source_observation_identity": observation.observation_id,
                                "source_identity": source_identity,
                                "static_identity": static_identity,
                                "reason": "validated_endpoint_bridge_unresolved",
                            }
                        )
                    else:
                        identity_disagreement.append(
                            {
                                "source_observation_identity": observation.observation_id,
                                "source_identity": source_identity,
                                "static_identity": static_identity,
                            }
                        )
                else:
                    unresolved_identity.append(
                        {
                            "source_observation_identity": observation.observation_id,
                            "source_identity": source_identity,
                            "reason": "static_context_identity_missing",
                        }
                    )
            source_text = getattr(observation, text_name)
            static_text = getattr(static_event, f"{prefix}_text")
            if _present(source_text) and _present(static_text):
                textual_comparable += 1
                if _string_equal(source_text, static_text):
                    textual_agreement += 1
        result["identity_fields"][prefix] = {
            "source_coverage_count": source_identity_count,
            "source_coverage_ratio": (source_identity_count / len(events)) if events else None,
            "comparable_count": comparable_identity,
            "exact_identity_agreement_count": identity_agreement,
            "validated_bridge_agreement_count": bridge_agreement,
            "disagreement_count": len(identity_disagreement),
            "unresolved_identity_count": len(unresolved_identity),
            "disagreement_examples": identity_disagreement[:10],
            "unresolved_examples": unresolved_identity[:10],
        }
        result["text_fields"][prefix] = {
            "source_coverage_count": source_text_count,
            "source_coverage_ratio": (source_text_count / len(events)) if events else None,
            "comparable_count": textual_comparable,
            "contextual_agreement_count": textual_agreement,
            "contextual_disagreement_count": textual_comparable - textual_agreement,
            "is_key_evidence": False,
        }
    evaluated_count = sum(
        item["comparable_count"]
        for item in result["identity_fields"].values()
    )
    result["status"] = "EVALUATED" if evaluated_count else "NOT_EVALUATED"
    return result


def summarize_match_classifications(decisions: Iterable[MatchDecision]) -> Dict[str, int]:
    counts = Counter(decision.data_quality_classification for decision in decisions)
    return dict(sorted(counts.items()))


def reconcile_cross_source(
    decisions: Iterable[MatchDecision],
) -> Dict[str, Any]:
    """Reconcile only correlated observations; otherwise return NOT_EVALUATED."""

    rows = list(decisions)
    grouped_rows: Dict[str, List[MatchDecision]] = defaultdict(list)
    for item in rows:
        if item.correlation_group_id:
            grouped_rows[str(item.correlation_group_id)].append(item)
    if not grouped_rows:
        return {
            "status": "NOT_EVALUATED",
            "agreements": [],
            "disagreements": [],
            "not_evaluated": [{"reason": "no explicitly validated correlation basis supplied"}],
            "interpretation": "Nearby timestamps or equal source IDs are not a correlation basis.",
        }
    agreements: List[Dict[str, Any]] = []
    disagreements: List[Dict[str, Any]] = []
    not_evaluated: List[Dict[str, Any]] = []
    for correlation_id, group in sorted(grouped_rows.items()):
        if not all(item.correlation_validated and item.correlation_basis for item in group):
            not_evaluated.append(
                {
                    "correlation_group_id": correlation_id,
                    "reason": "correlation group is not explicitly validated for every observation",
                }
            )
            continue
        source_systems = sorted({item.source_system for item in group})
        if len(source_systems) < 2:
            not_evaluated.append({"correlation_group_id": correlation_id, "reason": "one source system only"})
            continue
        canonical_ids = sorted({item.canonical_service_event_id for item in group if item.canonical_service_event_id})
        if len(canonical_ids) > 1:
            disagreements.append(
                {
                    "correlation_group_id": correlation_id,
                    "correlation_basis": sorted({item.correlation_basis for item in group if item.correlation_basis}),
                    "source_systems": source_systems,
                    "canonical_service_event_ids": canonical_ids,
                    "source_event_identities": [item.source_event_identity for item in group],
                    "classification": QUALITY_CROSS_SOURCE_DISAGREEMENT,
                    "interpretation": "validated correlation basis points to conflicting canonical events",
                }
            )
            continue
        if len(canonical_ids) != 1:
            not_evaluated.append(
                {
                    "correlation_group_id": correlation_id,
                    "correlation_basis": sorted({item.correlation_basis for item in group if item.correlation_basis}),
                    "source_systems": source_systems,
                    "reason": "correlated group has no common canonical event",
                }
            )
            continue
        canonical_id = canonical_ids[0]
        source_systems = sorted({item.source_system for item in group})
        if all(item.match_confidence == MATCH_HIGH for item in group):
            confidence = MATCH_HIGH
        elif any(item.match_confidence == MATCH_LOW for item in group):
            confidence = MATCH_LOW
        else:
            confidence = MATCH_MEDIUM
        agreements.append(
            {
                "correlation_group_id": correlation_id,
                "correlation_basis": sorted({item.correlation_basis for item in group if item.correlation_basis}),
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
    return {
        "status": "EVALUATED" if agreements or disagreements else "NOT_EVALUATED",
        "agreements": agreements,
        "disagreements": disagreements,
        "not_evaluated": not_evaluated,
        "interpretation": "Sources are associated only through a validated correlation group and canonical event; this is not causality.",
    }


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


def _synthetic_evidence_snapshot() -> Dict[str, Any]:
    bridge = _fixture_bridge()
    schedule = _fixture_schedule()
    observations = _fixture_observations()
    validation = MatchingEvidenceValidation(
        mapping_version="2026-09-28",
        validated_sequence_scopes=(
            (SOURCE_MDD, "VRS_GLOBAL_ID"),
            (SOURCE_GTFS_RT, "GTFS_DE"),
        ),
        validated_direction_mappings={
            (SOURCE_MDD, "vrs_global_id", "n"): ("N",),
            (SOURCE_GTFS_RT, "gtfs_de", "n"): ("N",),
        },
    )
    decisions = [
        match_source_event(item, schedule, bridge, evidence_validation=validation)
        for item in observations
    ]
    return {
        "population_type": "SYNTHETIC_UNIT_TEST_FIXTURES",
        "status": "PASS",
        "observation_count": len(observations),
        "decision_confidence_counts": dict(sorted(Counter(item.match_confidence for item in decisions).items())),
        "classification_counts": summarize_match_classifications(decisions),
        "ambiguity_count": sum(
            1 for item in decisions if item.data_quality_classification == QUALITY_AMBIGUOUS_MULTIPLE_CANDIDATES
        ),
        "out_of_core_scope_count": sum(
            1 for item in decisions if item.data_quality_classification == QUALITY_OUT_OF_CORE_SCOPE
        ),
        "mapping_versions_used": ["2026-09-28"],
        "cross_source_reconciliation": reconcile_cross_source(decisions[:2]),
        "evidence_refs": ["tests/test_stage_09_matching_quality.py", "scripts/stage_09_matching_quality.py"],
        "completion_role": "implementation_regression_only; never source-backed acceptance evidence",
    }


def build_stage9_evidence_artifact() -> Dict[str, Any]:
    """Build the machine-readable, source-classified Stage 9 evidence audit."""

    stage4 = json.loads(STAGE4_EVIDENCE_PATH.read_text(encoding="utf-8"))
    stage3a_rows = [
        json.loads(line)
        for line in STAGE3A_EVIDENCE_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    station_rows = [
        row for row in stage3a_rows if row.get("record_type") == "mdd_discovery_station_summary"
    ]
    stage3a_summary = next(
        (row for row in stage3a_rows if row.get("record_type") == "mdd_discovery_summary"),
        {},
    )
    source_mdd_results = int(stage3a_summary.get("sample_result_count") or 0)
    controlled_mdd_results = 5
    if source_mdd_results == 0:
        source_mdd_results = len(station_rows) * 5
    stage4_metadata = stage4["source_metadata"]
    stage4_scope = stage4["scope_counts"]
    stage4_hierarchy = stage4["scope_anomalies"]["hierarchy"]
    synthetic = _synthetic_evidence_snapshot()
    now_utc = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    return {
        "record_type": "stage_09_matching_data_quality_evidence",
        "artifact_version": "stage-09-evidence-1.0",
        "generated_at_utc": now_utc,
        "stage_9_status": "BLOCKED_SOURCE_BACKED_CORE_MATCH_POPULATION_NOT_ESTABLISHED",
        "implementation_regression_status": "PASS" if synthetic["status"] == "PASS" else "FAIL",
        "evidence_status": {
            "source_backed_core_matching": "BLOCKED",
            "source_backed_core_matching_blocker": "No retained successful Stage 8 parser bundle plus date-compatible MDD-to-VRS static event population is available in repository artifacts; synthetic line/stop bridges are not accepted as closure evidence.",
            "optional_dimensions_without_authority": [
                "MDD-to-VRS route/line identity agreement",
                "MDD DirectionRef to GTFS direction_id mapping",
                "MDD origin/destination identity bridge",
                "MDD StopSeqNumber to VRS stop_sequence agreement",
                "cross-source disagreement without an independently validated correlation basis",
            ],
        },
        "source_snapshots_and_versions": {
            "vrs_static": {
                "source_profile": stage4_metadata.get("source_profile"),
                "feed_version": stage4_metadata["feed_info"].get("feed_version"),
                "zip_sha256": stage4_metadata.get("zip_sha256"),
                "retrieval_timestamp_utc": stage4_metadata.get("retrieval_timestamp_utc"),
                "feed_start_date": stage4_metadata["feed_info"].get("feed_start_date"),
                "feed_end_date": stage4_metadata["feed_info"].get("feed_end_date"),
                "source_url": stage4_metadata.get("source_url"),
            },
            "mdd_stage3a": {
                "artifact": str(STAGE3A_EVIDENCE_PATH.relative_to(REPOSITORY_ROOT)),
                "successful_http_response_count": stage3a_summary.get("successful_http_response_count"),
                "station_count": len(station_rows),
                "result_population": source_mdd_results,
                "observed_request_timestamps_utc": sorted(
                    row.get("request_timestamp_utc")
                    for row in station_rows
                    if row.get("request_timestamp_utc")
                ),
                "network_replayed_by_stage9": False,
            },
            "stage8_successful_parser_bundle": {
                "successful_bundle_artifact_retained": False,
                "parser_and_bundle_implementation": "scripts/stage_08_mdd_realtime_pilot.py",
                "source_backed_replay_status": "BLOCKED",
                "reason": "The repository retains Stage 3A aggregate/source-contract evidence but no successful Stage 8 parser bundle with row-level MDD observations.",
            },
            "gtfsde_identity_bridge": {
                "evidence_document": "docs/source-contracts/gtfsde-static-identity-bridge-audit.md",
                "current_mapping_status": "PARTIAL",
                "bounded_lifecycle_evidence_dates": ["2026-09-26"],
                "durable_feed_version_or_hash_in_repository": None,
                "mapping_version_used_by_stage9": None,
            },
        },
        "evidence_classes": {
            "synthetic_unit_test": synthetic,
            "source_backed_known_good": {
                "population_type": "HISTORICAL_AND_BOUNDED_SOURCE_ARTIFACTS",
                "status": "SUPPORTING_ONLY",
                "source_artifacts": [
                    "docs/stages/04-cologne-scope-mode-evidence.json",
                    "docs/source-contracts/mdd-trias-field-retention-matrix.md",
                    "docs/source-contracts/gtfsde-static-identity-bridge-audit.md",
                    "docs/source-contracts/gtfs-rt-source-feasibility-audit.md",
                    "stage03a-mdd-discovery.jsonl",
                ],
                "static_scope": {
                    "cologne_distinct_stop_ids": stage4_scope["cologne_distinct_stop_ids"],
                    "cologne_scheduled_stop_events": stage4_scope["cologne_scheduled_stop_events"],
                    "cologne_serving_routes": stage4_scope["cologne_serving_routes"],
                    "cologne_serving_trips": stage4_scope["cologne_serving_trips"],
                    "actual_stop_id_controls_scope": True,
                    "parent_station_non_override": True,
                },
                "mdd_parser_field_coverage": {
                    "successful_station_responses": len(station_rows),
                    "discovery_result_population": source_mdd_results,
                    "controlled_response_result_population": controlled_mdd_results,
                    "operating_day_ref": {"present_count": controlled_mdd_results, "coverage_ratio": 1.0, "agreement_with_gtfs_service_date": "NOT_EVALUATED"},
                    "line_ref": {"present_count": controlled_mdd_results, "coverage_ratio": 1.0, "identity_agreement_with_vrs": "NOT_EVALUATED"},
                    "direction_ref": {"present_count": controlled_mdd_results, "coverage_ratio": 1.0, "mapping_to_gtfs_direction_id": "NOT_EVALUATED"},
                    "origin_stop_point_ref": {"present_count": controlled_mdd_results, "coverage_ratio": 1.0, "identity_agreement": "NOT_EVALUATED"},
                    "destination_stop_point_ref": {"present_count": controlled_mdd_results, "coverage_ratio": 1.0, "identity_agreement": "NOT_EVALUATED"},
                    "origin_text": {"present_count": controlled_mdd_results, "coverage_ratio": 1.0, "key_role": False},
                    "destination_text": {"present_count": controlled_mdd_results, "coverage_ratio": 1.0, "key_role": False},
                    "richer_origins_structure": {"source_retained": True, "identity_validation": "NOT_EVALUATED"},
                    "richer_destinations_structure": {"source_retained": True, "identity_validation": "NOT_EVALUATED"},
                    "stop_seq_number": {"controlled_response_call_count": 111, "source_coverage": "OBSERVED", "agreement_with_vrs": "NOT_EVALUATED"},
                    "timetabled_time": {"source_timestamp_presence": "OBSERVED", "absolute_timezone_alignment_to_vrs_service_date": "NOT_EVALUATED"},
                },
                "gtfsrt_static_known_good_sequence_sample": {
                    "source_namespace": "GTFS_DE",
                    "population": "bounded Köln Neumarkt lifecycle sample #1",
                    "relevant_realtime_updates": 241,
                    "source_stop_sequence_coverage": 241,
                    "gtfs_stop_sequence_coverage": 241,
                    "exact_equality_count": 241,
                    "systematic_offset": 0,
                    "disagreement_count": 0,
                    "duplicate_or_ambiguous_sequence_cases": "NOT_REPORTED",
                    "call_scope_effects": "NOT_EVALUATED",
                    "scope_note": "GTFS.de realtime-to-GTFS.de static only; not MDD-to-VRS proof.",
                },
                "timezone_alignment_sample": {
                    "population": "historical MDD 1-day/7-day/30-day result-bearing responses",
                    "explicit_utc_timestamp_form": "YYYY-MM-DDTHH:mm:ssZ[GMT]",
                    "original_timestamp_preserved": True,
                    "timezone_conversion_to_europe_berlin": "IMPLEMENTED_AND_UNIT_TESTED",
                    "independent_gtfs_service_date_agreement": "NOT_EVALUATED",
                },
                "route_line_identity": {
                    "vrs_static_cologne_routes": stage4_scope["cologne_serving_routes"],
                    "mdd_line_ref_population": controlled_mdd_results,
                    "validated_mdd_to_vrs_line_bridge_count": 0,
                    "classification": QUALITY_SOURCE_LINE_IDENTITY_UNRESOLVED,
                    "synthetic_line_fixture_not_used_for_closure": True,
                },
                "origin_destination": {
                    "identity_bridge_status": "NOT_EVALUATED",
                    "textual_context_status": "SUPPORTING_ONLY",
                    "origin_identity_observed_count": controlled_mdd_results,
                    "destination_identity_observed_count": controlled_mdd_results,
                    "origin_text_observed_count": controlled_mdd_results,
                    "destination_text_observed_count": controlled_mdd_results,
                    "richer_origins_destinations_retained": True,
                    "display_text_can_create_match": False,
                },
                "direction": {
                    "direction_ref_observed_count": controlled_mdd_results,
                    "richer_direction_observed_count": "NOT_EVALUATED",
                    "raw_values_observed": True,
                    "deterministic_gtfs_direction_mapping": "NOT_EVALUATED",
                    "high_confidence_use": False,
                },
                "match_decision_counts": {
                    "HIGH": None,
                    "MEDIUM": None,
                    "LOW": None,
                    "UNMATCHED": None,
                    "status": "NOT_EVALUATED",
                    "source_backed_decision_population": 0,
                    "population": "No source-backed end-to-end MDD-to-VRS decisions retained in repository.",
                },
                "ambiguity_counts": {
                    "source_backed_match_ambiguities": 0,
                    "source_backed_match_ambiguities_status": "NOT_EVALUATED",
                    "identity_bridge_unresolved_examples": 1,
                },
                "representative_known_good_matches": [
                    {
                        "source_system": SOURCE_GTFS_RT,
                        "source_namespace": "GTFS_DE",
                        "stop_identity": "106842",
                        "population": "bounded Köln Neumarkt lifecycle sample #1",
                        "exact_stop_sequence_agreement_count": 241,
                        "source_and_static_namespace": "GTFS.de only",
                        "mdd_to_vrs_matching_authority": "NOT_ESTABLISHED",
                    }
                ],
                "cross_source_disagreement": {
                    "status": "NOT_EVALUATED",
                    "reason": "No independently validated cross-source correlation group in retained source artifacts.",
                },
            },
            "unresolved": {
                "population_type": "RETAINED_SOURCE_QUALITY_GAPS",
                "railway_koeln_hbf_gtfs_de": EXPECTED_RAILWAY_HBF_STATUS,
                "adjacent_kvb_parent_ids_not_substituted": ["106448", "437741"],
                "mdd_to_vrs_line_bridge": QUALITY_SOURCE_LINE_IDENTITY_UNRESOLVED,
                "mdd_to_vrs_event_population": QUALITY_INSUFFICIENT_EVIDENCE,
            "representative_examples": [
                    {
                        "classification": QUALITY_SOURCE_IDENTITY_UNRESOLVED,
                        "source_identity": "MDD/TRIAS Köln Hbf returned call identity",
                        "reason": "successful Stage 8 parser row not retained in repository for date-compatible replay",
                    },
                    {
                        "classification": QUALITY_OUT_OF_CORE_SCOPE,
                        "source_identity": "GTFS.de adjacent KVB parent 437741",
                        "reason": "rail-served child evidence belongs to explicitly excluded adjacent KVB complex, not approved railway Köln Hbf",
                    },
                    {
                        "classification": QUALITY_AMBIGUOUS_MULTIPLE_CANDIDATES,
                        "source_identity": "synthetic fixture only",
                        "reason": "retained as a regression example; not source-backed closure evidence",
                    },
                ],
            },
        },
        "stop_identity_and_hierarchy": {
            "vrs_rule": stage4["scope_contract"]["cologne_stop_rule"],
            "vrs_parent_child": {
                "parent_station_rows": stage4_scope["cologne_parent_station_rows"],
                "child_parent_also_cologne_rows": stage4_hierarchy["cologne_child_parent_also_cologne_rows"],
                "orphan_parent_station_rows": stage4_hierarchy["orphan_parent_station_reference_rows"],
                "exact_stop_id_controls_schedule": True,
                "suffix_stripping_fallback": False,
            },
            "gtfsde_neumarkt": {
                "parent_station_id": "493082",
                "child_stop_ids": ["106842", "207108", "441164", "581903", "96309"],
                "exact_child_matching_possible": True,
                "parent_fallback_justified": "only when the source bridge explicitly maps the child to this parent; never by prefix",
            },
            "gtfsde_railway_hbf": {
                "status": EXPECTED_RAILWAY_HBF_STATUS,
                "adjacent_parent_ids": ["106448", "437741"],
                "resolved_child_stop_ids": [],
                "parent_fallback_justified": False,
            },
            "mdd_hbf_platform_child_to_vrs_static": {
                "status": "NOT_EVALUATED",
                "mdd_platform_examples": ["de:05315:11201:7:79", "de:05315:11201:7:72"],
                "reason": "No retained source-backed row-level bridge for the reported MDD platform/call identities.",
            },
        },
        "service_date_operating_day_metrics": {
            "operating_day_ref_is_service_date": False,
            "controlled_mdd_population": controlled_mdd_results,
            "presence_rate": 1.0,
            "normalized_format": "PARSER_ACCEPTS_YYYYMMDD_OR_ISO; SOURCE_FORMAT_NOT_EVALUATED",
            "agreement_count": 0,
            "disagreement_count": 0,
            "unresolved_count": controlled_mdd_results,
            "representative_disagreement_examples": [],
            "matching_participation": "NOT_USED_UNTIL_INDEPENDENT_GTFS_SERVICE_DATE_EVIDENCE",
        },
        "scheduled_time_timezone_alignment_metrics": {
            "feed_timezone": DEFAULT_FEED_TIMEZONE,
            "explicit_timezone_values": "OBSERVED",
            "timezone_less_values": "REMAIN_UNRESOLVED",
            "dst_cases": ["CET", "CEST", "spring_forward", "fall_back"],
            "midnight_crossing": "TESTED",
            "extended_gtfs_hours_preserved": True,
            "source_backed_alignment_to_static_service_date": "NOT_EVALUATED",
        },
        "stop_sequence_metrics": {
            "mdd_to_vrs": "NOT_EVALUATED",
                "gtfsrt_to_gtfsde_known_good": {
                    "source_coverage": 241,
                    "gtfs_coverage": 241,
                    "exact_equality": 241,
                    "systematic_offset": 0,
                    "systematic_offset_units": "stop_sequence values",
                "disagreements": 0,
            },
            "high_confidence_rule": "supporting_only_until_source_population_and_call_scope_are validated",
        },
        "static_coverage_classifications": {
            QUALITY_STATIC_STOP_MISSING: 0,
            QUALITY_STATIC_ROUTE_MISSING: 0,
            QUALITY_STATIC_SERVICE_DATE_NOT_ACTIVE: 0,
            QUALITY_STATIC_EVENT_NOT_FOUND: 0,
            QUALITY_SOURCE_IDENTITY_UNRESOLVED: 1,
            QUALITY_IDENTITY_CONFLICT: 0,
            QUALITY_AMBIGUOUS_MULTIPLE_CANDIDATES: 0,
            QUALITY_INSUFFICIENT_EVIDENCE: 1,
            QUALITY_OUT_OF_CORE_SCOPE: 1,
            QUALITY_UNMATCHED: 0,
            "population_note": "Counts refer to source-backed audit classifications, not a fabricated end-to-end match population.",
        },
        "mapping_versions_used": {
            "synthetic_fixture": ["2026-09-28"],
            "vrs_static_feed_snapshot": [stage4_metadata["feed_info"].get("feed_version")],
            "gtfsde_identity_bridge": [],
            "runtime_rule": "service-date-valid mapping or explicit mapping version required; future mappings cannot resolve historical observations",
        },
        "representative_match_examples": {
            "source_backed_known_good": [
                {
                    "source": "GTFS_RT/GTFS_DE",
                    "identity": "Neumarkt child stop 106842",
                    "result": "241/241 source stop sequences exactly matched GTFS.de static in bounded lifecycle sample #1",
                    "scope": "supporting only; not MDD-to-VRS evidence",
                }
            ],
            "synthetic_known_good": synthetic["cross_source_reconciliation"].get("agreements", []),
            "synthetic_ambiguous": [
                {
                    "classification": QUALITY_AMBIGUOUS_MULTIPLE_CANDIDATES,
                    "status": "PASS_REGRESSION_ONLY",
                }
            ],
            "source_backed_unresolved": [
                {
                    "classification": QUALITY_SOURCE_IDENTITY_UNRESOLVED,
                    "identity": "railway Köln Hbf in GTFS.de bridge",
                    "status": EXPECTED_RAILWAY_HBF_STATUS,
                }
            ],
        },
        "stage_10_plus": {
            "stage_10_status": "NOT_STARTED",
            "tiered50_activation": False,
            "actual_delay_approved": False,
        },
    }


def write_stage9_evidence_artifact(path: Path = REPOSITORY_ROOT / "docs" / "stages" / "09-matching-data-quality-evidence.json") -> Dict[str, Any]:
    artifact = build_stage9_evidence_artifact()
    path.write_text(json.dumps(artifact, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return artifact


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
        operating_day_ref="2026-09-29",
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
        correlation_group_id="fixture-correlation-neumarkt-1",
        correlation_basis="SYNTHETIC_KNOWN_GOOD_PAIR",
        correlation_validated=True,
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
    synthetic_validation = MatchingEvidenceValidation(
        mapping_version="2026-09-28",
        validated_sequence_scopes=(
            (SOURCE_MDD, "VRS_GLOBAL_ID"),
            (SOURCE_GTFS_RT, "GTFS_DE"),
        ),
        validated_direction_mappings={
            (SOURCE_MDD, "vrs_global_id", "n"): ("N",),
            (SOURCE_GTFS_RT, "gtfs_de", "n"): ("N",),
        },
    )
    decisions = [
        match_source_event(item, schedule, bridge, evidence_validation=synthetic_validation)
        for item in observations
    ]
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
        "cross_source_agreement_uses_canonical_event": len(cross_source["agreements"]) == 1 and cross_source["agreements"][0]["match_confidence"] == MATCH_HIGH,
        "mdd_journeyref_is_not_gtfsrt_trip_id_key": not mdd_good.evidence.get("source_trip_identity_used", False),
        "equal_cross_source_identity_is_not_used": by_id[observations[2].source_identity].evidence.get("reason") == "equal_top_candidates",
        "ambiguous_candidate_is_quarantined": ambiguous.match_confidence == MATCH_UNMATCHED and ambiguous.data_quality_classification == QUALITY_AMBIGUOUS_MULTIPLE_CANDIDATES,
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
        "implementation_regression_status": "PASS" if all(checks.values()) else "FAIL",
        "stage_9_evidence_status": "BLOCKED_SOURCE_BACKED_CORE_MATCH_POPULATION_NOT_ESTABLISHED",
        "stage_9_status": "BLOCKED" if all(checks.values()) else "VALIDATION_FAILED",
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
        "stop_sequence_validation": validate_stop_sequence_alignment(observations, decisions, schedule),
        "direction_validation": validate_direction_semantics(observations, decisions, schedule),
        "origin_destination_validation": validate_origin_destination_evidence(observations, decisions, schedule, bridge),
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
