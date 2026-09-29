#!/usr/bin/env python3
"""Bounded source-backed Stage 9 acceptance probe.

This script deliberately has a much smaller boundary than the Stage 8 pilot:
one sequential request for each of at most three known Cologne stations and no
retries.  It parses those responses through the existing Stage 8 parser and
Stage 9 adapter, builds only the date-compatible GTFS stop-event population,
and writes sanitized row-level evidence.  It never writes a raw MDD payload or
credential to the repository.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Set, Tuple
from urllib.request import Request, build_opener

import stage_04_cologne_scope_mode_audit as stage4
import stage_08_mdd_realtime_pilot as stage8
import stage_09_matching_quality as stage9


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE_PATH = REPOSITORY_ROOT / "docs" / "stages" / "09-source-backed-matching-evidence.jsonl"
DEFAULT_SUMMARY_PATH = REPOSITORY_ROOT / "docs" / "stages" / "09-source-backed-matching-summary.json"
STAGE9_DOC_EVIDENCE_PATH = REPOSITORY_ROOT / "docs" / "stages" / "09-matching-data-quality-evidence.json"
SOURCE_URL = stage4.OFFICIAL_SOURCE_URL
FEED_TIMEZONE = stage9.DEFAULT_FEED_TIMEZONE
MAX_STATIONS = 3
MAX_MDD_HTTP_ATTEMPTS = 3
MAX_STATIC_DOWNLOAD_BYTES = 200 * 1024 * 1024
MAX_CANDIDATE_SERVICE_DATE_OFFSET_DAYS = 2
SOURCE_BACKED_EVIDENCE_ORIGIN = "LIVE_MDD_AND_OFFICIAL_GTFS"
SCRIPT_VERSION = "stage-09-source-backed-acceptance-1.1"
ARTIFACT_MAX_ITEMS = 100
SAMPLING_POLICY = "STAGE_09_SOURCE_BACKED_MAX_3_STATIONS_1_REQUEST"
SAMPLING_ERA = "STAGE_09_SOURCE_BACKED_ACCEPTANCE"
MAPPING_VERSION_PREFIX = "VRS_GTFS"
SOURCE_BACKED_REQUIRED_FIELDS = frozenset(
    {
        "source_system",
        "source_namespace",
        "mdd_source_event_identity",
        "mdd_source_observation_identity",
        "source_event_identity",
        "source_observation_identity",
        "response_id",
        "collector_run_id",
        "observation_timestamp",
        "operating_day_ref",
        "stop_point_ref",
        "stop_seq_number",
        "timetabled_time",
        "selected_this_call_source_path",
        "selected_this_call_observation_id",
        "scheduled_time_source_path",
        "stage8_selected_this_call_lineage",
        "static_feed_version",
        "static_feed_sha256",
        "independent_gtfs_service_date",
        "independent_service_date_status",
        "independent_service_date_evidence",
        "static_candidate_identities",
        "selected_candidate",
        "selected_candidate_id",
        "candidate_count",
        "candidate_ids",
        "confidence",
        "match_method",
        "data_quality_classification",
        "selected_score",
        "decision_evidence",
        "stop_hierarchy",
        "stop_hierarchy_evidence",
        "identity_mapping_version",
        "evidence_used",
        "evidence_explicitly_not_used",
    }
)
SOURCE_BACKED_NON_NULL_FIELDS = frozenset(
    {
        "source_system",
        "source_namespace",
        "mdd_source_event_identity",
        "mdd_source_observation_identity",
        "source_event_identity",
        "source_observation_identity",
        "response_id",
        "collector_run_id",
        "observation_timestamp",
        "operating_day_ref",
        "stop_point_ref",
        "stop_seq_number",
        "timetabled_time",
        "selected_this_call_source_path",
        "selected_this_call_observation_id",
        "scheduled_time_source_path",
        "static_feed_version",
        "static_feed_sha256",
        "independent_service_date_status",
        "candidate_count",
        "candidate_ids",
        "confidence",
        "match_method",
        "data_quality_classification",
        "decision_evidence",
        "stop_hierarchy",
        "stop_hierarchy_evidence",
        "identity_mapping_version",
        "evidence_used",
        "evidence_explicitly_not_used",
        "stage8_selected_this_call_lineage",
    }
)
SOURCE_BACKED_CONFIDENCES = frozenset(
    {stage9.MATCH_HIGH, stage9.MATCH_MEDIUM, stage9.MATCH_LOW, stage9.MATCH_UNMATCHED}
)
SOURCE_BACKED_CORE_QUALITIES = frozenset(
    {
        stage9.QUALITY_CONTEXTUAL_MATCH,
        stage9.QUALITY_SOURCE_LINE_IDENTITY_UNRESOLVED,
        stage9.QUALITY_VALIDATED_MATCH,
    }
)

TARGETS: Tuple[stage8.Stage8RuntimeConfig, ...] = (
    stage8.Stage8RuntimeConfig(
        station="Köln Hbf",
        stop_point_ref="de:05315:11201",
        number_of_results=20,
        sampling_policy=SAMPLING_POLICY,
    ),
    stage8.Stage8RuntimeConfig(
        station="Köln Heumarkt",
        stop_point_ref="de:05315:11110",
        number_of_results=20,
        sampling_policy=SAMPLING_POLICY,
    ),
    stage8.Stage8RuntimeConfig(
        station="Köln Neumarkt",
        stop_point_ref="de:05315:11111",
        number_of_results=20,
        sampling_policy=SAMPLING_POLICY,
    ),
)


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value: Any) -> Optional[int]:
    text = _text(value)
    if text is None or not text.isdigit():
        return None
    return int(text)


def _json_value(value: Any, *, depth: int = 0, max_depth: int = 4, max_items: int = 20, max_chars: int = 3000) -> Any:
    """Bound parsed source fields without retaining a raw response payload."""

    if depth > max_depth:
        return "<TRUNCATED_DEPTH>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= max_chars else value[:max_chars] + "<TRUNCATED>"
    if isinstance(value, Mapping):
        output: Dict[str, Any] = {}
        for ordinal, (key, item) in enumerate(value.items()):
            if ordinal >= max_items:
                output["<TRUNCATED_ITEMS>"] = True
                break
            output[str(key)] = _json_value(
                item,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
                max_chars=max_chars,
            )
        return output
    if isinstance(value, (list, tuple, set)):
        output = [
            _json_value(
                item,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
                max_chars=max_chars,
            )
            for item in list(value)[:max_items]
        ]
        if len(value) > max_items:
            output.append("<TRUNCATED_ITEMS>")
        return output
    return _json_value(str(value), depth=depth + 1, max_depth=max_depth, max_items=max_items, max_chars=max_chars)


def _lossless_json_value(value: Any) -> Any:
    """Convert a value to JSON without bounding the Stage 9 audit contract."""

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _lossless_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_lossless_json_value(item) for item in value]
    return str(value)


def _present_json_value(value: Any) -> bool:
    """Return true for populated JSON values while preserving false and zero."""

    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _contains_truncation_marker(value: Any) -> bool:
    """Detect serializer markers inside fields that must remain auditable."""

    if isinstance(value, str):
        return value.startswith("<TRUNCATED")
    if isinstance(value, Mapping):
        return any(
            _contains_truncation_marker(key) or _contains_truncation_marker(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return any(_contains_truncation_marker(item) for item in value)
    return False


def _serialize_evidence_record(record: Mapping[str, Any]) -> Dict[str, Any]:
    """Bound diagnostics while preserving every required row-level audit field."""

    serialized: Dict[str, Any] = {}
    for key, value in record.items():
        if key in SOURCE_BACKED_REQUIRED_FIELDS:
            serialized[str(key)] = _lossless_json_value(value)
        else:
            serialized[str(key)] = _json_value(value, max_items=ARTIFACT_MAX_ITEMS)
    return serialized


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _date_range(start: date, end: date) -> Iterator[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _parse_gtfs_date(value: Any) -> Optional[date]:
    text = _text(value)
    if text is None or len(text) != 8 or not text.isdigit():
        return None
    try:
        return date(int(text[0:4]), int(text[4:6]), int(text[6:8]))
    except ValueError:
        return None


def _gtfs_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def _read_csv_rows(archive: zipfile.ZipFile, filename: str) -> Iterator[Dict[str, str]]:
    raw = archive.open(filename, "r")
    text = __import__("io").TextIOWrapper(raw, encoding="utf-8-sig", newline="")
    try:
        reader = csv.DictReader(text)
        for row in reader:
            yield {
                str(key).strip(): str(value).strip() if value is not None else ""
                for key, value in row.items()
                if key is not None
            }
    finally:
        text.close()


class ServiceCalendar:
    """GTFS calendar/calendar_dates activation without source-date aliasing."""

    WEEKDAYS = (
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    )

    def __init__(self, archive: zipfile.ZipFile, feed_info: Mapping[str, str]) -> None:
        self.base: Dict[str, List[Dict[str, str]]] = defaultdict(list)
        self.exceptions: Dict[str, Dict[date, int]] = defaultdict(dict)
        self.boundaries: List[date] = []
        feed_start = _parse_gtfs_date(feed_info.get("feed_start_date"))
        feed_end = _parse_gtfs_date(feed_info.get("feed_end_date"))
        if feed_start:
            self.boundaries.append(feed_start)
        if feed_end:
            self.boundaries.append(feed_end)
        if "calendar.txt" in archive.namelist():
            for row in _read_csv_rows(archive, "calendar.txt"):
                service_id = _text(row.get("service_id"))
                if not service_id:
                    continue
                self.base[service_id].append(row)
                start = _parse_gtfs_date(row.get("start_date"))
                end = _parse_gtfs_date(row.get("end_date"))
                if start:
                    self.boundaries.append(start)
                if end:
                    self.boundaries.append(end)
        if "calendar_dates.txt" in archive.namelist():
            for row in _read_csv_rows(archive, "calendar_dates.txt"):
                service_id = _text(row.get("service_id"))
                exception_date = _parse_gtfs_date(row.get("date"))
                exception_type = _int(row.get("exception_type"))
                if service_id and exception_date and exception_type in {1, 2}:
                    self.exceptions[service_id][exception_date] = exception_type
                    self.boundaries.append(exception_date)
        self.start = min(self.boundaries) if self.boundaries else None
        self.end = max(self.boundaries) if self.boundaries else None

    def active(self, service_id: Optional[str], service_date: date) -> bool:
        if not service_id:
            return False
        base_active = False
        for row in self.base.get(service_id, ()):
            start = _parse_gtfs_date(row.get("start_date"))
            end = _parse_gtfs_date(row.get("end_date"))
            if start and end and start <= service_date <= end:
                weekday_name = self.WEEKDAYS[service_date.weekday()]
                base_active = str(row.get(weekday_name, "0")) == "1"
                if base_active:
                    break
        exception = self.exceptions.get(service_id, {}).get(service_date)
        if exception == 1:
            return True
        if exception == 2:
            return False
        return base_active

    def active_dates(self) -> Set[str]:
        if self.start is None or self.end is None:
            return set()
        service_ids = set(self.base) | set(self.exceptions)
        return {
            service_date.isoformat()
            for service_date in _date_range(self.start, self.end)
            if any(self.active(service_id, service_date) for service_id in service_ids)
        }


def _feed_snapshot_metadata(zip_path: Path, retrieved_at_utc: str) -> Dict[str, Any]:
    digest = hashlib.sha256()
    with zip_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    with zipfile.ZipFile(zip_path, "r") as archive:
        feed_info_rows = list(_read_csv_rows(archive, "feed_info.txt"))
    feed_info = feed_info_rows[0] if feed_info_rows else {}
    return {
        "source_url": SOURCE_URL,
        "retrieval_timestamp_utc": retrieved_at_utc,
        "zip_sha256": digest.hexdigest(),
        "zip_bytes": zip_path.stat().st_size,
        "feed_version": _text(feed_info.get("feed_version")),
        "feed_start_date": _text(feed_info.get("feed_start_date")),
        "feed_end_date": _text(feed_info.get("feed_end_date")),
        "feed_publisher_name": _text(feed_info.get("feed_publisher_name")),
        "feed_publisher_url": _text(feed_info.get("feed_publisher_url")),
    }


def _download_static_feed(directory: Path) -> Tuple[Path, Dict[str, Any]]:
    retrieved_at_utc = _iso_utc(datetime.now(timezone.utc))
    zip_path = directory / "vrs-current.zip"
    request = Request(SOURCE_URL, headers={"User-Agent": "CologneTransitIntelligence/Stage9"})
    opener = build_opener()
    total = 0
    with opener.open(request, timeout=60) as response:
        fd = os.open(str(zip_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_STATIC_DOWNLOAD_BYTES:
                    raise ValueError("official GTFS ZIP exceeded bounded download size")
                os.write(fd, chunk)
        finally:
            os.close(fd)
    os.chmod(zip_path, stat.S_IRUSR | stat.S_IWUSR)
    if not zipfile.is_zipfile(zip_path):
        raise ValueError("official VRS download is not a ZIP archive")
    metadata = _feed_snapshot_metadata(zip_path, retrieved_at_utc)
    return zip_path, metadata


def _run_stage4_validation(zip_path: Path, retrieved_at_utc: str, directory: Path) -> Dict[str, Any]:
    output_path = directory / "stage4-validation.json"
    command = [
        sys.executable,
        str(REPOSITORY_ROOT / "scripts" / "stage_04_cologne_scope_mode_audit.py"),
        "--gtfs-zip",
        str(zip_path),
        "--output-json",
        str(output_path),
        "--source-url",
        SOURCE_URL,
        "--retrieved-at-utc",
        retrieved_at_utc,
        "--network-request-count",
        "1",
        "--freshness-reference-date",
        datetime.now(timezone.utc).date().isoformat(),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0 or not output_path.is_file():
        detail = (completed.stderr or completed.stdout or "Stage 4 validation failed").strip()
        raise ValueError(f"official VRS snapshot failed Stage 4 validation: {detail[:500]}")
    result = json.loads(output_path.read_text(encoding="utf-8"))
    checks = result.get("acceptance_checks") or []
    return {
        "status": "PASS" if checks and all(item.get("passed") for item in checks) else "FAIL",
        "acceptance_check_count": len(checks),
        "failed_acceptance_checks": [item.get("id") for item in checks if not item.get("passed")],
        "scope_counts": result.get("scope_counts", {}),
        "hierarchy": result.get("scope_anomalies", {}).get("hierarchy", {}),
        "source_metadata": result.get("source_metadata", {}),
    }


def _approved_snapshot_metadata() -> Dict[str, Any]:
    data = json.loads((REPOSITORY_ROOT / "docs" / "stages" / "04-cologne-scope-mode-evidence.json").read_text(encoding="utf-8"))
    source = data["source_metadata"]
    return {
        "source_url": source.get("source_url"),
        "zip_sha256": source.get("zip_sha256"),
        "feed_version": source.get("feed_info", {}).get("feed_version"),
        "feed_start_date": source.get("feed_info", {}).get("feed_start_date"),
        "feed_end_date": source.get("feed_info", {}).get("feed_end_date"),
    }


def _static_feed_context(
    zip_path: Path,
    observations: Sequence[stage9.SourceEventObservation],
) -> Dict[str, Any]:
    """Read real GTFS hierarchy, calendars, routes, trips, and stop events."""

    source_stop_ids = {item.source_stop_id for item in observations if item.source_stop_id}
    local_dates: Set[date] = set()
    for item in observations:
        parsed, explicit_timezone, _source_timezone = stage9._parse_source_datetime(item.scheduled_time)
        if parsed is not None and explicit_timezone and parsed.tzinfo is not None and parsed.utcoffset() is not None:
            local = parsed.astimezone(__import__("zoneinfo").ZoneInfo(FEED_TIMEZONE))
            local_dates.add(local.date())
    candidate_dates = {
        (local_date - timedelta(days=offset)).isoformat()
        for local_date in local_dates
        for offset in range(MAX_CANDIDATE_SERVICE_DATE_OFFSET_DAYS + 1)
    }

    with zipfile.ZipFile(zip_path, "r") as archive:
        names = set(archive.namelist())
        required = set(stage4.REQUIRED_FILES) | {"calendar.txt", "calendar_dates.txt"}
        missing = sorted(filename for filename in stage4.REQUIRED_FILES if filename not in names)
        if missing:
            raise ValueError(f"current VRS feed is missing required files: {missing}")
        feed_info_rows = list(_read_csv_rows(archive, "feed_info.txt"))
        feed_info = feed_info_rows[0] if feed_info_rows else {}
        service_calendar = ServiceCalendar(archive, feed_info)
        active_dates = service_calendar.active_dates()
        active_candidate_dates = sorted(active_dates & candidate_dates)

        stops: Dict[str, Dict[str, str]] = {}
        children_by_parent: Dict[str, List[str]] = defaultdict(list)
        for row in _read_csv_rows(archive, "stops.txt"):
            stop_id = _text(row.get("stop_id"))
            if not stop_id:
                continue
            stops[stop_id] = row
            parent = _text(row.get("parent_station"))
            if parent:
                children_by_parent[parent].append(stop_id)

        agencies = {
            _text(row.get("agency_id")): row
            for row in _read_csv_rows(archive, "agency.txt")
            if _text(row.get("agency_id"))
        }
        routes = {
            _text(row.get("route_id")): row
            for row in _read_csv_rows(archive, "routes.txt")
            if _text(row.get("route_id"))
        }
        trips: Dict[str, List[Dict[str, str]]] = defaultdict(list)
        for row in _read_csv_rows(archive, "trips.txt"):
            trip_id = _text(row.get("trip_id"))
            if trip_id:
                trips[trip_id].append(row)

        events: List[stage9.StaticServiceEvent] = []
        scheduled_usage: Counter[str] = Counter()
        stop_time_rows_seen = 0
        for row_number, row in enumerate(_read_csv_rows(archive, "stop_times.txt"), start=2):
            stop_id = _text(row.get("stop_id"))
            if stop_id not in source_stop_ids:
                continue
            stop_time_rows_seen += 1
            scheduled_usage[stop_id] += 1
            trip_id = _text(row.get("trip_id"))
            trip_rows = trips.get(trip_id or "", ())
            for trip in trip_rows:
                service_id = _text(trip.get("service_id"))
                route_id = _text(trip.get("route_id"))
                route = routes.get(route_id or "", {})
                agency_id = _text(route.get("agency_id"))
                agency = agencies.get(agency_id or "", {})
                active_for = [
                    service_date
                    for service_date in active_candidate_dates
                    if service_calendar.active(service_id, date.fromisoformat(service_date))
                ]
                for service_date in active_for:
                    for event_type, time_field in (
                        ("ARRIVAL", "arrival_time"),
                        ("DEPARTURE", "departure_time"),
                    ):
                        scheduled_time = _text(row.get(time_field))
                        if not scheduled_time:
                            continue
                        events.append(
                            stage9.StaticServiceEvent(
                                feed_snapshot_key=_text(feed_info.get("feed_version")) or "UNKNOWN_FEED_VERSION",
                                source_namespace=stage9.STAGE8_MDD_SOURCE_NAMESPACE,
                                source_trip_id=trip_id,
                                source_route_id=route_id,
                                source_stop_id=stop_id,
                                canonical_stop_id=stop_id,
                                canonical_line_id=route_id,
                                service_date=date.fromisoformat(service_date),
                                event_type=event_type,
                                scheduled_time=scheduled_time,
                                direction=_text(trip.get("direction_id")),
                                stop_sequence=_int(row.get("stop_sequence")),
                                operator_id=agency_id,
                                source_row_identity=f"stop_times.txt:{row_number}:{event_type}",
                                parent_station=_text(stops.get(stop_id, {}).get("parent_station")),
                                service_id=service_id,
                                agency_id=agency_id,
                                agency_name=_text(agency.get("agency_name")),
                                route_short_name=_text(route.get("route_short_name")),
                                route_long_name=_text(route.get("route_long_name")),
                                route_type=_text(route.get("route_type")),
                                scope_code="IN_SCOPE" if stage4.COLOGNE_PREFIX in stop_id else "OUT_OF_SCOPE",
                                scope_reason=None if stage4.COLOGNE_PREFIX in stop_id else "actual stop_id is outside approved Cologne prefix",
                            )
                        )

    hierarchy: Dict[str, Dict[str, Any]] = {}
    for stop_id in sorted(source_stop_ids):
        row = stops.get(stop_id)
        parent = _text(row.get("parent_station")) if row else None
        location_type = _text(row.get("location_type")) if row else None
        hierarchy[stop_id] = {
            "exact_child_identity_found": bool(row and location_type != "1"),
            "static_stop_found": bool(row),
            "location_type": location_type,
            "parent_station": parent,
            "parent_station_found": bool(parent and parent in stops),
            "explicit_parent_relationship": bool(parent and parent in stops and stop_id in children_by_parent.get(parent, [])),
            "exact_scheduled_stop_usage": scheduled_usage.get(stop_id, 0),
            "static_stop_missing": not bool(row),
            "parent_fallback_used": False,
            "suffix_stripping_used": False,
        }
    return {
        "feed_info": feed_info,
        "active_service_dates": sorted(active_dates),
        "candidate_service_dates": active_candidate_dates,
        "stops": stops,
        "hierarchy": hierarchy,
        "scheduled_usage": dict(scheduled_usage),
        "events": events,
        "stop_time_rows_for_source_stop_ids": stop_time_rows_seen,
        "source_stop_ids": sorted(source_stop_ids),
    }


def _build_exact_stop_bridge(
    observations: Sequence[stage9.SourceEventObservation],
    feed_context: Mapping[str, Any],
    mapping_version: str,
) -> stage9.IdentityBridge:
    stops = feed_context["stops"]
    mappings = []
    feed_info = feed_context["feed_info"]
    valid_from = stage9.normalize_service_date(feed_info.get("feed_start_date"))
    valid_to = stage9.normalize_service_date(feed_info.get("feed_end_date"))
    for source_id in sorted({item.source_stop_id for item in observations if item.source_stop_id}):
        if source_id not in stops:
            continue
        mappings.append(
            stage9.IdentityMapping(
                stage9.STAGE8_MDD_SOURCE_SYSTEM,
                stage9.STAGE8_MDD_SOURCE_NAMESPACE,
                source_id,
                source_id,
                mapping_version,
                evidence_ref="stage9/exact-mdd-stop-point-ref-equals-vrs-gtfs-stop_id",
                valid_from=valid_from,
                valid_to=valid_to,
                notes="Exact stop_id equality only; no suffix stripping or parent inference.",
            )
        )
    return stage9.IdentityBridge(stop_mappings=mappings, active_mapping_version=mapping_version)


def _independent_service_date(
    observation: stage9.SourceEventObservation,
    schedule: stage9.ScheduleIndex,
) -> Dict[str, Any]:
    parsed, explicit_timezone, source_timezone = stage9._parse_source_datetime(observation.scheduled_time)
    result: Dict[str, Any] = {
        "status": "UNRESOLVED",
        "source_timestamp": observation.scheduled_time,
        "source_timezone": source_timezone,
        "explicit_timezone": explicit_timezone,
        "candidate_service_dates": [],
        "matching_candidate_event_ids": [],
        "established_service_date": None,
        "reason": None,
    }
    if parsed is None:
        result["reason"] = "invalid_or_missing_absolute_timetabled_time"
        return result
    if not explicit_timezone or parsed.tzinfo is None or parsed.utcoffset() is None:
        result["reason"] = "explicit_source_timezone_required"
        return result
    if stage9.ZoneInfo is None:
        result["reason"] = "feed_timezone_unavailable"
        return result
    local = parsed.astimezone(stage9.ZoneInfo(FEED_TIMEZONE))
    candidate_dates = {
        (local.date() - timedelta(days=offset)).isoformat()
        for offset in range(MAX_CANDIDATE_SERVICE_DATE_OFFSET_DAYS + 1)
    }
    result["candidate_service_dates"] = sorted(candidate_dates)
    matching_events: List[stage9.CanonicalServiceEvent] = []
    for event in schedule.events:
        if event.canonical_stop_id != observation.source_stop_id or event.event_type != observation.normalized_event_type:
            continue
        if event.service_date not in candidate_dates:
            continue
        alignment = stage9.align_schedule_time(
            observation.scheduled_time,
            service_date=event.service_date,
            feed_timezone=FEED_TIMEZONE,
        )
        if alignment.get("status") in {stage9.TIME_ALIGNMENT_RESOLVED} and alignment.get("service_day_seconds") == event.scheduled_time_seconds:
            matching_events.append(event)
    result["matching_candidate_event_ids"] = [item.canonical_service_event_id for item in matching_events]
    established_dates = sorted({item.service_date for item in matching_events})
    if len(established_dates) == 1:
        result["status"] = "ESTABLISHED"
        result["established_service_date"] = established_dates[0]
    elif len(established_dates) > 1:
        result["status"] = "AMBIGUOUS"
        result["reason"] = "multiple_active_gtfs_service_dates_match_absolute_timetable"
    else:
        result["reason"] = "no_active_gtfs_stop_event_matches_absolute_timetable"
    return result


def _request_context_key(request_context: Mapping[str, Any]) -> str:
    """Return the stable key used to attach diagnostics to one MDD request."""

    target_index = request_context.get("target_index")
    if target_index is not None:
        return str(target_index)
    return str(
        request_context.get("response_id")
        or request_context.get("station")
        or "UNKNOWN_REQUEST"
    )


def _raw_record_has_this_call(record: Mapping[str, Any]) -> bool:
    """Inspect only the raw Stage 8 call rows for parser-level THIS evidence."""

    calls = record.get("call_observations")
    if not isinstance(calls, Sequence) or isinstance(calls, (str, bytes)):
        return False
    return any(
        isinstance(call, Mapping)
        and str(call.get("call_scope", "")).upper() == "THIS"
        for call in calls
    )


def _prepare_preliminary_observations(
    raw_bundle_rows: Sequence[Tuple[Mapping[str, Any], Mapping[str, Any]]],
    *,
    feed_timezone: str = FEED_TIMEZONE,
) -> Tuple[
    List[Tuple[Mapping[str, Any], Mapping[str, Any], stage9.SourceEventObservation]],
    Dict[str, Dict[str, int]],
]:
    """Adapt raw Stage 8 rows before selecting the Stage 9 THIS-call population.

    Stage 8 records contain scoped ``call_observations`` but no Stage 9
    ``source_attributes``.  The adapter is therefore the only authority for
    selecting THIS and for determining which rows enter source-backed
    processing.  The returned preliminary observation is intentionally kept
    alongside its raw record so the main loop can perform the required final
    adaptation after independent GTFS service-date establishment.
    """

    prepared: List[
        Tuple[Mapping[str, Any], Mapping[str, Any], stage9.SourceEventObservation]
    ] = []
    diagnostics: Dict[str, Dict[str, int]] = {}
    for record, request_context in raw_bundle_rows:
        request_key = _request_context_key(request_context)
        counts = diagnostics.setdefault(
            request_key,
            {
                "bundle_stop_event_count": int(request_context.get("bundle_stop_event_count") or 0),
                "raw_stop_events_with_this_call_count": 0,
                "adapted_stop_events_with_selected_this_count": 0,
                "adapted_stop_events_missing_this_count": 0,
            },
        )
        if _raw_record_has_this_call(record):
            counts["raw_stop_events_with_this_call_count"] += 1

        preliminary = stage9.adapt_stage8_stop_event(
            record,
            service_date=None,
            feed_timezone=feed_timezone,
        )
        if preliminary.source_attributes.get("selected_call_scope") != "THIS":
            counts["adapted_stop_events_missing_this_count"] += 1
            continue

        counts["adapted_stop_events_with_selected_this_count"] += 1
        prepared.append((record, request_context, preliminary))
    return prepared, diagnostics


def _line_metrics(
    observations: Sequence[stage9.SourceEventObservation],
    decisions: Sequence[stage9.MatchDecision],
    schedule: stage9.ScheduleIndex,
) -> Dict[str, Any]:
    source_present = [item for item in observations if item.source_line_id]
    if not source_present:
        return {
            "status": "NOT_EVALUATED",
            "evaluated_count": "NOT_EVALUATED",
            "agreement_count": "NOT_EVALUATED",
            "unresolved_count": "NOT_EVALUATED",
            "conflict_count": "NOT_EVALUATED",
        }
    evaluated = 0
    agreement = 0
    unresolved = 0
    conflict = 0
    for observation in source_present:
        decision = stage9._decision_for_observation(observation, decisions)
        if decision is None:
            unresolved += 1
            continue
        if decision.evidence.get("line_resolution_status") == "RESOLVED":
            evaluated += 1
            if decision.evidence.get("line_match"):
                agreement += 1
        elif decision.data_quality_classification == stage9.QUALITY_IDENTITY_CONFLICT:
            evaluated += 1
            conflict += 1
        else:
            unresolved += 1
    return {
        "status": "EVALUATED",
        "evaluated_count": evaluated,
        "agreement_count": agreement,
        "unresolved_count": unresolved,
        "conflict_count": conflict,
        "mapping_semantics": "MDD Operator/LineRef to VRS route_id is not assumed; no line bridge was activated by this probe.",
        "static_route_context_count": sum(
            1
            for decision in decisions
            if decision.canonical_service_event_id
            and schedule.by_id[decision.canonical_service_event_id].source_route_id
        ),
    }


def _optional_metrics(
    observations: Sequence[stage9.SourceEventObservation],
    decisions: Sequence[stage9.MatchDecision],
    schedule: stage9.ScheduleIndex,
    bridge: stage9.IdentityBridge,
) -> Dict[str, Any]:
    sequence = stage9.validate_stop_sequence_alignment(observations, decisions, schedule)
    direction = stage9.validate_direction_semantics(observations, decisions, schedule)
    endpoints = stage9.validate_origin_destination_evidence(observations, decisions, schedule, bridge)
    if sequence["gtfs_coverage"]["comparable_count"] == 0:
        sequence_summary: Dict[str, Any] = {
            "present_count": sequence["source_coverage"]["present_count"],
            "exact_count": "NOT_EVALUATED",
            "systematic_offset": "NOT_EVALUATED",
            "disagreement_count": "NOT_EVALUATED",
            "status": "NOT_EVALUATED",
        }
    else:
        sequence_summary = {
            "present_count": sequence["source_coverage"]["present_count"],
            "exact_count": sequence["exact_equality_count"],
            "systematic_offset": sequence["systematic_offset"],
            "disagreement_count": sequence["disagreement_count"],
            "status": "EVALUATED",
        }
    if direction["gtfs_coverage"]["comparable_count"] == 0:
        direction_summary: Dict[str, Any] = {
            "present_count": direction["source_coverage"]["present_count"],
            "agreement_count": "NOT_EVALUATED",
            "disagreement_count": "NOT_EVALUATED",
            "status": "NOT_EVALUATED",
        }
    else:
        direction_summary = {
            "present_count": direction["source_coverage"]["present_count"],
            "agreement_count": direction["raw_equality_count"],
            "disagreement_count": direction["raw_disagreement_count"],
            "status": "EVALUATED",
        }
    endpoint_summary: Dict[str, Any] = {}
    for prefix in ("origin", "destination"):
        field = endpoints["identity_fields"][prefix]
        endpoint_summary[prefix] = {
            "evaluated_count": field["comparable_count"] or "NOT_EVALUATED",
            "agreement_count": field["exact_identity_agreement_count"] if field["comparable_count"] else "NOT_EVALUATED",
            "unresolved_count": field["unresolved_identity_count"] if field["comparable_count"] else "NOT_EVALUATED",
            "status": "EVALUATED" if field["comparable_count"] else "NOT_EVALUATED",
        }
    return {
        "stop_sequence": sequence_summary,
        "direction": direction_summary,
        "origin_destination": endpoint_summary,
        "raw_validation": {
            "stop_sequence": sequence,
            "direction": direction,
            "origin_destination": endpoints,
        },
    }


def _candidate_record(event: stage9.CanonicalServiceEvent) -> Dict[str, Any]:
    return {
        "canonical_service_event_id": event.canonical_service_event_id,
        "feed_snapshot_key": event.feed_snapshot_key,
        "service_date": event.service_date,
        "event_type": event.event_type,
        "stop_id": event.source_stop_id,
        "parent_station": event.parent_station,
        "trip_id": event.source_trip_id,
        "route_id": event.source_route_id,
        "service_id": event.service_id,
        "stop_sequence": event.stop_sequence,
        "scheduled_time": event.scheduled_time_source,
        "scheduled_time_seconds": event.scheduled_time_seconds,
        "direction_id": event.direction,
        "agency_id": event.agency_id,
        "agency_name": event.agency_name,
        "route_short_name": event.route_short_name,
        "route_long_name": event.route_long_name,
    }


def _evidence_record(
    observation: stage9.SourceEventObservation,
    decision: stage9.MatchDecision,
    independent_date: Mapping[str, Any],
    hierarchy: Mapping[str, Any],
    schedule: stage9.ScheduleIndex,
    feed_metadata: Mapping[str, Any],
    response_context: Mapping[str, Any],
) -> Dict[str, Any]:
    candidate_events = [schedule.by_id[item] for item in decision.candidate_event_ids if item in schedule.by_id]
    selected = schedule.by_id.get(decision.canonical_service_event_id or "")
    return {
        "record_type": "stage_09_source_backed_observation",
        "artifact_version": "stage-09-source-backed-evidence-1.0",
        "evidence_origin": SOURCE_BACKED_EVIDENCE_ORIGIN,
        "matching_version": decision.matching_version,
        "source_system": observation.source_system,
        "source_namespace": observation.source_namespace,
        "response_id": observation.response_id,
        "collector_run_id": observation.collector_run_id,
        "observation_timestamp": observation.observed_at,
        "mdd_source_event_identity": observation.source_event_id,
        "mdd_source_observation_identity": observation.observation_id,
        "source_event_identity": decision.source_event_identity,
        "source_observation_identity": decision.source_observation_identity,
        "operating_day_ref": observation.operating_day_ref,
        "stop_point_ref": observation.source_stop_id,
        "stop_seq_number": observation.stop_sequence,
        "line_ref": observation.source_line_id,
        "journey_ref": observation.source_journey_ref,
        "direction_ref": observation.direction_ref,
        "direction": observation.direction,
        "operator": observation.operator_id,
        "origin_stop_point_ref": observation.origin_stop_point_ref,
        "destination_stop_point_ref": observation.destination_stop_point_ref,
        "origin_text": observation.origin_text,
        "destination_text": observation.destination_text,
        "timetabled_time": observation.scheduled_time,
        "selected_this_call_source_path": observation.source_attributes.get("selected_call_source_path"),
        "selected_this_call_observation_id": observation.source_attributes.get("selected_call_observation_id"),
        "scheduled_time_source_path": observation.source_paths.get("scheduled_time"),
        "static_feed_version": feed_metadata.get("feed_version"),
        "static_feed_sha256": feed_metadata.get("zip_sha256"),
        "static_feed_start_date": feed_metadata.get("feed_start_date"),
        "static_feed_end_date": feed_metadata.get("feed_end_date"),
        "independent_gtfs_service_date": independent_date.get("established_service_date"),
        "independent_service_date_status": independent_date.get("status"),
        "independent_service_date_evidence": dict(independent_date),
        "stop_hierarchy": dict(hierarchy),
        "static_candidate_identities": [_candidate_record(item) for item in candidate_events],
        "selected_candidate": _candidate_record(selected) if selected else None,
        "selected_candidate_id": decision.canonical_service_event_id,
        "confidence": decision.match_confidence,
        "match_method": decision.match_method,
        "data_quality_classification": decision.data_quality_classification,
        "candidate_count": decision.candidate_count,
        "candidate_ids": list(decision.candidate_event_ids),
        "selected_score": decision.selected_score,
        "decision_evidence": dict(decision.evidence),
        "stop_hierarchy_evidence": dict(hierarchy),
        "identity_mapping_version": {
            "requested": feed_metadata.get("identity_mapping_version")
            or (
                f"{MAPPING_VERSION_PREFIX}|{feed_metadata.get('feed_version')}"
                if feed_metadata.get("feed_version")
                else None
            ),
            "stop": decision.stop_mapping_version,
            "line": decision.line_mapping_version,
        },
        "evidence_used": {
            "exact_stop_identity": hierarchy.get("static_stop_found") and not hierarchy.get("parent_fallback_used"),
            "independent_gtfs_service_date": independent_date.get("status") == "ESTABLISHED",
            "event_type": observation.normalized_event_type,
            "exact_scheduled_time": bool(decision.evidence.get("scheduled_time_match")),
            "validated_line_mapping": bool(decision.evidence.get("line_evidence_validated")),
        },
        "evidence_explicitly_not_used": [
            "OperatingDayRef as the input GTFS service date",
            "ObservedAt as service date or scheduled event time",
            "raw StopSeqNumber for candidate ranking unless sequence semantics are validated",
            "raw DirectionRef/Direction for candidate ranking unless direction mapping is validated",
            "MDD OperatorRef equals GTFS agency_id without an explicit mapping",
            "origin/destination display text as an identity key",
            "suffix stripping or string-prefix parent inference",
            "MDD JourneyRef equals VRS trip_id",
        ],
        "stage8_selected_this_call_lineage": {
            "selected_call_scope": observation.source_attributes.get("selected_call_scope"),
            "selected_call_observation_id": observation.source_attributes.get("selected_call_observation_id"),
            "selected_call_source_path": observation.source_attributes.get("selected_call_source_path"),
            "scheduled_time_source_path": observation.source_attributes.get("scheduled_time_source_path")
            or observation.source_paths.get("scheduled_time"),
            "scheduled_time_selection_status": observation.source_attributes.get("scheduled_time_selection_status"),
        },
        "response_context": _json_value(response_context),
    }


def _write_jsonl(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(
                json.dumps(
                    _serialize_evidence_record(record),
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )


def _write_summary(path: Path, summary: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            _json_value(summary, max_items=ARTIFACT_MAX_ITEMS),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _row_is_core_deterministic_match(row: Mapping[str, Any]) -> bool:
    """Recalculate the Stage 9 core-match rule from retained row evidence."""

    evidence = row.get("decision_evidence")
    return bool(
        _present_json_value(row.get("selected_candidate_id"))
        and isinstance(evidence, Mapping)
        and evidence.get("canonical_stop_match")
        and evidence.get("service_date_match")
        and evidence.get("event_type_match")
        and evidence.get("scheduled_time_match")
        and row.get("data_quality_classification") in SOURCE_BACKED_CORE_QUALITIES
    )


def validate_source_backed_evidence_artifact(
    evidence_path: Path,
    summary: Mapping[str, Any],
) -> Dict[str, Any]:
    """Validate the durable JSONL artifact against its source-backed summary.

    This validator deliberately operates on the written JSONL, not on the
    in-memory matcher decisions.  A successful live match therefore cannot
    complete Stage 9 if serialization dropped the decision evidence.
    """

    evidence_path = Path(evidence_path)
    errors: List[str] = []
    rows: List[Dict[str, Any]] = []
    artifact_exists = evidence_path.is_file()
    if not artifact_exists:
        errors.append(f"JSONL artifact does not exist: {evidence_path}")
    else:
        try:
            for line_number, line in enumerate(evidence_path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except (TypeError, ValueError) as error:
                    errors.append(f"line {line_number}: malformed JSON ({error})")
                    continue
                if not isinstance(value, dict):
                    errors.append(f"line {line_number}: JSONL value is not an object")
                    continue
                rows.append(value)
        except (OSError, UnicodeError) as error:
            errors.append(f"could not read JSONL artifact: {error}")

    for ordinal, row in enumerate(rows, 1):
        missing = sorted(SOURCE_BACKED_REQUIRED_FIELDS.difference(row))
        if missing:
            errors.append(f"row {ordinal}: missing required fields: {', '.join(missing)}")
            continue

        null_fields = sorted(
            field
            for field in SOURCE_BACKED_NON_NULL_FIELDS
            if not _present_json_value(row.get(field))
        )
        if null_fields:
            errors.append(f"row {ordinal}: required audit fields are null/empty: {', '.join(null_fields)}")

        truncated_fields = sorted(
            field
            for field in SOURCE_BACKED_REQUIRED_FIELDS
            if _contains_truncation_marker(row.get(field))
        )
        if truncated_fields:
            errors.append(f"row {ordinal}: required audit fields contain truncation markers: {', '.join(truncated_fields)}")

        if not isinstance(row.get("static_candidate_identities"), list):
            errors.append(f"row {ordinal}: static_candidate_identities must be a list")
        if not isinstance(row.get("candidate_ids"), list):
            errors.append(f"row {ordinal}: candidate_ids must be a list")
        if not isinstance(row.get("selected_candidate"), (dict, type(None))):
            errors.append(f"row {ordinal}: selected_candidate must be an object or null")
        if not isinstance(row.get("independent_service_date_evidence"), Mapping):
            errors.append(f"row {ordinal}: independent_service_date_evidence must be structured")
        if not isinstance(row.get("stop_hierarchy"), Mapping):
            errors.append(f"row {ordinal}: stop_hierarchy must be structured")
        if not isinstance(row.get("stop_hierarchy_evidence"), Mapping):
            errors.append(f"row {ordinal}: stop_hierarchy_evidence must be structured")
        if not isinstance(row.get("decision_evidence"), Mapping) or not row.get("decision_evidence"):
            errors.append(f"row {ordinal}: decision_evidence must be a populated structured value")
        if not isinstance(row.get("identity_mapping_version"), Mapping) or not row.get("identity_mapping_version"):
            errors.append(f"row {ordinal}: identity_mapping_version must be a populated structured value")
        if not isinstance(row.get("evidence_used"), Mapping) or not row.get("evidence_used"):
            errors.append(f"row {ordinal}: evidence_used must be a populated structured value")
        if not isinstance(row.get("evidence_explicitly_not_used"), list) or not row.get("evidence_explicitly_not_used"):
            errors.append(f"row {ordinal}: evidence_explicitly_not_used must be a populated list")

        lineage = row.get("stage8_selected_this_call_lineage")
        if not isinstance(lineage, Mapping):
            errors.append(f"row {ordinal}: stage8_selected_this_call_lineage must be structured")
        else:
            if lineage.get("selected_call_scope") != "THIS":
                errors.append(f"row {ordinal}: Stage 8 selected call scope is not THIS")
            for field in ("selected_call_observation_id", "selected_call_source_path"):
                if not _present_json_value(lineage.get(field)):
                    errors.append(f"row {ordinal}: Stage 8 THIS-call lineage field is null/empty: {field}")

        candidate_count = row.get("candidate_count")
        candidate_ids = row.get("candidate_ids")
        if not isinstance(candidate_count, int) or isinstance(candidate_count, bool) or candidate_count < 0:
            errors.append(f"row {ordinal}: candidate_count must be a non-negative integer")
        elif isinstance(candidate_ids, list) and candidate_count != len(candidate_ids):
            errors.append(f"row {ordinal}: candidate_count does not equal candidate_ids length")

        confidence = row.get("confidence")
        if confidence not in SOURCE_BACKED_CONFIDENCES:
            errors.append(f"row {ordinal}: invalid or missing confidence: {confidence!r}")

        service_date_status = row.get("independent_service_date_status")
        service_date = row.get("independent_gtfs_service_date")
        if service_date_status not in {"ESTABLISHED", "UNRESOLVED", "AMBIGUOUS"}:
            errors.append(f"row {ordinal}: invalid independent service-date status: {service_date_status!r}")
        elif service_date_status == "ESTABLISHED":
            if not isinstance(service_date, str) or not service_date.strip():
                errors.append(f"row {ordinal}: established service-date row has no GTFS service date")
            else:
                try:
                    date.fromisoformat(service_date)
                except ValueError:
                    errors.append(f"row {ordinal}: established service date is not ISO date: {service_date!r}")
        elif service_date is not None:
            errors.append(f"row {ordinal}: unresolved/ambiguous service-date row has an unexpected date")

        selected_id = row.get("selected_candidate_id")
        selected_candidate = row.get("selected_candidate")
        if _present_json_value(selected_id):
            if confidence == stage9.MATCH_UNMATCHED:
                errors.append(f"row {ordinal}: unmatched row has a selected candidate")
            if not isinstance(selected_candidate, Mapping):
                errors.append(f"row {ordinal}: matched row has no selected candidate identity")
            elif selected_candidate.get("canonical_service_event_id") != selected_id:
                errors.append(f"row {ordinal}: selected candidate identity does not match selected_candidate_id")
            if isinstance(candidate_ids, list) and selected_id not in candidate_ids:
                errors.append(f"row {ordinal}: selected_candidate_id is absent from candidate_ids")
            selected_score = row.get("selected_score")
            if not isinstance(selected_score, int) or isinstance(selected_score, bool):
                errors.append(f"row {ordinal}: matched row has no selected score")
        else:
            if selected_candidate is not None:
                errors.append(f"row {ordinal}: unmatched row retains a selected candidate object")
            if confidence != stage9.MATCH_UNMATCHED:
                errors.append(f"row {ordinal}: non-UNMATCHED row has no selected candidate")

    row_confidence_counts = Counter(row.get("confidence") for row in rows)
    confidence_summary = summary.get("source_backed_confidence_counts")
    expected_confidence_counts: Dict[str, int] = {}
    if not isinstance(confidence_summary, Mapping):
        errors.append("summary is missing source_backed_confidence_counts")
    else:
        for confidence_name in sorted(SOURCE_BACKED_CONFIDENCES):
            value = confidence_summary.get(confidence_name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                errors.append(f"summary has invalid confidence count for {confidence_name}: {value!r}")
            else:
                expected_confidence_counts[confidence_name] = value
        if expected_confidence_counts and any(
            row_confidence_counts.get(name, 0) != expected_confidence_counts.get(name, 0)
            for name in SOURCE_BACKED_CONFIDENCES
        ):
            errors.append(
                "JSONL confidence counts do not reconcile with summary: "
                f"rows={dict(sorted((name, row_confidence_counts.get(name, 0)) for name in SOURCE_BACKED_CONFIDENCES))} "
                f"summary={dict(sorted(expected_confidence_counts.items()))}"
            )

    expected_row_count = summary.get("source_backed_observation_count")
    if not isinstance(expected_row_count, int) or isinstance(expected_row_count, bool):
        errors.append("summary is missing a valid source_backed_observation_count")
    elif len(rows) != expected_row_count:
        errors.append(f"JSONL row count {len(rows)} does not equal summary observation count {expected_row_count}")

    ambiguity_count = sum(
        1 for row in rows if row.get("data_quality_classification") == stage9.QUALITY_AMBIGUOUS_MULTIPLE_CANDIDATES
    )
    expected_ambiguity_count = summary.get("ambiguity_count")
    if not isinstance(expected_ambiguity_count, int) or isinstance(expected_ambiguity_count, bool):
        errors.append("summary is missing a valid ambiguity_count")
    elif ambiguity_count != expected_ambiguity_count:
        errors.append(f"JSONL ambiguity count {ambiguity_count} does not equal summary ambiguity_count {expected_ambiguity_count}")

    established_count = sum(
        1 for row in rows if row.get("independent_service_date_status") == "ESTABLISHED"
    )
    date_summary = summary.get("independently_established_service_date")
    expected_established_count = date_summary.get("count") if isinstance(date_summary, Mapping) else None
    if not isinstance(expected_established_count, int) or isinstance(expected_established_count, bool):
        errors.append("summary is missing independently_established_service_date.count")
    elif established_count != expected_established_count:
        errors.append(
            f"JSONL established service-date count {established_count} does not equal summary count {expected_established_count}"
        )

    core_deterministic_match_count = sum(1 for row in rows if _row_is_core_deterministic_match(row))
    expected_core_count = summary.get("core_deterministic_match_count")
    if not isinstance(expected_core_count, int) or isinstance(expected_core_count, bool):
        errors.append("summary is missing a valid core_deterministic_match_count")
    elif core_deterministic_match_count != expected_core_count:
        errors.append(
            "JSONL core deterministic match count "
            f"{core_deterministic_match_count} does not equal summary count {expected_core_count}"
        )

    classification_counts = Counter(row.get("data_quality_classification") for row in rows)
    summary_classifications = summary.get("decision_classification_counts")
    if isinstance(summary_classifications, Mapping):
        normalized_summary_classifications = {
            str(key): value for key, value in summary_classifications.items()
        }
        normalized_row_classifications = dict(sorted((str(key), value) for key, value in classification_counts.items()))
        if normalized_row_classifications != dict(sorted(normalized_summary_classifications.items())):
            errors.append(
                "JSONL data-quality classification counts do not reconcile with summary: "
                f"rows={normalized_row_classifications} summary={normalized_summary_classifications}"
            )

    status_counts = Counter(row.get("independent_service_date_status") for row in rows)
    summary_status_counts = date_summary.get("status_counts") if isinstance(date_summary, Mapping) else None
    if isinstance(summary_status_counts, Mapping):
        normalized_status_counts = dict(sorted((str(key), value) for key, value in status_counts.items()))
        normalized_summary_status_counts = dict(sorted((str(key), value) for key, value in summary_status_counts.items()))
        if normalized_status_counts != normalized_summary_status_counts:
            errors.append(
                "JSONL service-date status counts do not reconcile with summary: "
                f"rows={normalized_status_counts} summary={normalized_summary_status_counts}"
            )

    reconciliation = {
        "row_count": len(rows),
        "summary_row_count": expected_row_count,
        "confidence_counts": dict(sorted((name, row_confidence_counts.get(name, 0)) for name in SOURCE_BACKED_CONFIDENCES)),
        "summary_confidence_counts": dict(sorted(expected_confidence_counts.items())),
        "ambiguity_count": ambiguity_count,
        "summary_ambiguity_count": expected_ambiguity_count,
        "independently_established_service_date_count": established_count,
        "summary_independently_established_service_date_count": expected_established_count,
        "core_deterministic_match_count": core_deterministic_match_count,
        "summary_core_deterministic_match_count": expected_core_count,
        "data_quality_classification_counts": dict(sorted((str(key), value) for key, value in classification_counts.items())),
        "service_date_status_counts": dict(sorted((str(key), value) for key, value in status_counts.items())),
    }
    return {
        "status": "PASS" if not errors else "FAIL",
        "artifact_exists": artifact_exists,
        "row_count": len(rows),
        "required_fields_checked": sorted(SOURCE_BACKED_REQUIRED_FIELDS),
        "error_count": len(errors),
        "errors": errors[:100],
        "reconciliation": reconciliation,
    }


def source_backed_acceptance_status(
    *,
    evidence_origin: str,
    static_validation_status: str,
    source_observation_count: int,
    independent_service_date_count: int,
    core_deterministic_match_count: int,
    mdd_http_attempts: int,
    artifact_written: bool,
    artifact_integrity_status: str = "FAIL",
) -> str:
    """Gate completion on live/source-backed provenance, never fixtures alone."""

    if (
        evidence_origin == SOURCE_BACKED_EVIDENCE_ORIGIN
        and static_validation_status == "PASS"
        and source_observation_count > 0
        and independent_service_date_count > 0
        and core_deterministic_match_count > 0
        and 0 < mdd_http_attempts <= MAX_MDD_HTTP_ATTEMPTS
        and artifact_written
        and artifact_integrity_status == "PASS"
    ):
        return "PASS"
    return "BLOCKED"


def _core_deterministic_matches(
    decisions: Sequence[stage9.MatchDecision],
) -> List[stage9.MatchDecision]:
    """Return only auditable deterministic matches eligible for Stage 9 completion."""

    return [
        item
        for item in decisions
        if item.canonical_service_event_id
        and item.evidence.get("canonical_stop_match")
        and item.evidence.get("service_date_match")
        and item.evidence.get("event_type_match")
        and item.evidence.get("scheduled_time_match")
        and item.data_quality_classification in {
            stage9.QUALITY_CONTEXTUAL_MATCH,
            stage9.QUALITY_SOURCE_LINE_IDENTITY_UNRESOLVED,
            stage9.QUALITY_VALIDATED_MATCH,
        }
    ]


def _empty_blocked_summary(
    *,
    preflight_status: str,
    blocker: str,
    evidence_path: Path,
    summary_path: Path,
) -> Dict[str, Any]:
    return {
        "record_type": "stage_09_source_backed_acceptance_summary",
        "artifact_version": "stage-09-source-backed-summary-1.0",
        "generated_at_utc": _iso_utc(datetime.now(timezone.utc)),
        "script_version": SCRIPT_VERSION,
        "evidence_origin": SOURCE_BACKED_EVIDENCE_ORIGIN,
        "implementation_regression_status": "PASS",
        "source_backed_acceptance_status": "BLOCKED",
        "row_level_evidence_integrity_status": "FAIL",
        "row_level_evidence_integrity": {
            "status": "FAIL",
            "artifact_exists": evidence_path.is_file(),
            "errors": [blocker],
        },
        "stage_9_status": "BLOCKED",
        "blocker": blocker,
        "credential_preflight_status": preflight_status,
        "mdd_http_attempts": 0,
        "source_backed_observation_count": 0,
        "evidence_path": str(evidence_path.relative_to(REPOSITORY_ROOT)),
        "summary_path": str(summary_path.relative_to(REPOSITORY_ROOT)),
        "stage_10_status": "NOT_STARTED",
        "tiered50_activated": False,
    }


def run_source_backed_acceptance(
    *,
    evidence_path: Path = DEFAULT_EVIDENCE_PATH,
    summary_path: Path = DEFAULT_SUMMARY_PATH,
) -> Dict[str, Any]:
    api_key = os.environ.get(stage8.MDD_API_KEY_ENV)
    credential_status = stage8.validate_api_key_format(api_key)
    print(stage8.credential_preflight_output(credential_status), flush=True)
    if credential_status != stage8.CREDENTIAL_PREFLIGHT_VALID:
        summary = _empty_blocked_summary(
            preflight_status=credential_status,
            blocker="usable MDD_API_KEY preflight was not available; no MDD HTTP attempt was made",
            evidence_path=evidence_path,
            summary_path=summary_path,
        )
        _write_summary(summary_path, summary)
        return summary

    run_id = f"stage09-source-{uuid.uuid4().hex}"
    temp_directory = Path(tempfile.mkdtemp(prefix="cologne-transit-stage-09-", dir="/private/tmp"))
    os.chmod(temp_directory, stat.S_IRWXU)
    raw_bundle_rows: List[Tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    request_contexts: List[Dict[str, Any]] = []
    mdd_attempts = 0
    static_metadata: Dict[str, Any] = {}
    static_validation: Dict[str, Any] = {"status": "NOT_EVALUATED"}
    blocker: Optional[str] = None
    try:
        zip_path, static_metadata = _download_static_feed(temp_directory)
        approved = _approved_snapshot_metadata()
        static_validation = _run_stage4_validation(
            zip_path,
            static_metadata["retrieval_timestamp_utc"],
            temp_directory,
        )
        static_metadata["approved_stage4_snapshot"] = approved
        static_metadata["same_as_approved_stage4_snapshot"] = (
            static_metadata.get("zip_sha256") == approved.get("zip_sha256")
            and static_metadata.get("feed_version") == approved.get("feed_version")
        )
        static_metadata["snapshot_comparison"] = (
            "SAME_APPROVED_SNAPSHOT"
            if static_metadata["same_as_approved_stage4_snapshot"]
            else "CHANGED_OFFICIAL_SNAPSHOT_REVALIDATED_WITH_STAGE4_CONTRACT"
        )
        if static_validation["status"] != "PASS":
            raise ValueError(
                "current official VRS snapshot did not satisfy the approved Stage 4 scope/hierarchy contract"
            )

        for target_index, target in enumerate(TARGETS[:MAX_STATIONS]):
            if mdd_attempts >= MAX_MDD_HTTP_ATTEMPTS:
                break
            result = stage8.perform_http_request(api_key, runtime_config=target)
            mdd_attempts += 1
            request_context: Dict[str, Any] = {
                "target_index": target_index,
                "station": target.station,
                "requested_stop_point_ref": target.stop_point_ref,
                "request_timestamp_utc": result.request_timestamp_utc,
                "http_status": result.http_status,
                "content_type": result.content_type,
                "elapsed_ms": round(result.elapsed_ms, 3),
                "transport_failure": result.failure,
                "transport_failure_detail": result.failure_detail,
                "parser_status": "NOT_ATTEMPTED",
                "bundle_stop_event_count": 0,
                "raw_stop_events_with_this_call_count": 0,
                "adapted_stop_events_with_selected_this_count": 0,
                "adapted_stop_events_missing_this_count": 0,
            }
            if result.http_status is not None and result.body:
                stage8.raw_payload_secret_check(result.body, api_key)
            if result.failure or result.http_status is None or not 200 <= result.http_status < 300:
                request_contexts.append(request_context)
                continue
            if not result.body:
                request_context["parser_status"] = "FAILED_EMPTY_RESPONSE"
                request_contexts.append(request_context)
                continue
            response_hash = hashlib.sha256(result.body).hexdigest()
            response_id = stage8.response_id_for(run_id, target_index, response_hash)
            observed_at = stage8.iso_utc(stage8.utc_now())
            try:
                parsed = stage8.parse_response(result.body, result.content_type)
                bundle = stage8.build_bundle(
                    parsed=parsed,
                    response_id=response_id,
                    run_id=run_id,
                    observed_at=observed_at,
                    sampling_policy_id=SAMPLING_POLICY,
                    sampling_era_id=SAMPLING_ERA,
                )
                request_context.update(
                    {
                        "response_id": response_id,
                        "parser_status": "PARSED",
                        "application_error": bool(stage8.application_error(parsed)),
                        "bundle_stop_event_count": len(bundle["stop_events"]),
                    }
                )
                if not stage8.application_error(parsed):
                    raw_bundle_rows.extend((record, request_context.copy()) for record in bundle["stop_events"])
            except Exception as error:
                request_context["parser_status"] = "FAILED"
                request_context["parser_error"] = str(error)[:500]
            request_contexts.append(request_context)

        if not raw_bundle_rows:
            blocker = "bounded MDD run produced no successful parseable stop-event bundle"
            summary = _empty_blocked_summary(
                preflight_status=credential_status,
                blocker=blocker,
                evidence_path=evidence_path,
                summary_path=summary_path,
            )
            summary.update(
                {
                    "run_id": run_id,
                    "mdd_http_attempts": mdd_attempts,
                    "mdd_request_contexts": request_contexts,
                    "vrs_snapshot": static_metadata,
                    "stage4_revalidation": static_validation,
                }
            )
            _write_summary(summary_path, summary)
            return summary

        prepared_rows, preparation_diagnostics = _prepare_preliminary_observations(raw_bundle_rows)
        for request_context in request_contexts:
            request_context.update(
                preparation_diagnostics.get(
                    _request_context_key(request_context),
                    {
                        "raw_stop_events_with_this_call_count": 0,
                        "adapted_stop_events_with_selected_this_count": 0,
                        "adapted_stop_events_missing_this_count": 0,
                    },
                )
            )
        for _record, request_context in raw_bundle_rows:
            if isinstance(request_context, dict):
                request_context.update(
                    preparation_diagnostics.get(
                        _request_context_key(request_context),
                        {
                            "raw_stop_events_with_this_call_count": 0,
                            "adapted_stop_events_with_selected_this_count": 0,
                            "adapted_stop_events_missing_this_count": 0,
                        },
                    )
                )
        preliminary_observations = [preliminary for _record, _context, preliminary in prepared_rows]
        if not preliminary_observations:
            blocker = "successful MDD bundles contained no selected THIS-call stop events"
            summary = _empty_blocked_summary(
                preflight_status=credential_status,
                blocker=blocker,
                evidence_path=evidence_path,
                summary_path=summary_path,
            )
            summary.update(
                {
                    "run_id": run_id,
                    "mdd_http_attempts": mdd_attempts,
                    "mdd_request_contexts": request_contexts,
                    "vrs_snapshot": static_metadata,
                    "stage4_revalidation": static_validation,
                }
            )
            _write_summary(summary_path, summary)
            return summary

        feed_context = _static_feed_context(zip_path, preliminary_observations)
        schedule = stage9.build_schedule_index(
            feed_context["events"],
            known_stop_ids=feed_context["stops"].keys(),
            active_service_dates=feed_context["active_service_dates"],
            known_line_ids={event.source_route_id for event in feed_context["events"] if event.source_route_id},
        )
        mapping_version = f"{MAPPING_VERSION_PREFIX}|{static_metadata.get('feed_version') or static_metadata.get('zip_sha256')}"
        bridge = _build_exact_stop_bridge(preliminary_observations, feed_context, mapping_version)
        matching_validation = stage9.MatchingEvidenceValidation(mapping_version=mapping_version)

        evidence_records: List[Dict[str, Any]] = []
        observations: List[stage9.SourceEventObservation] = []
        decisions: List[stage9.MatchDecision] = []
        independent_dates: List[Dict[str, Any]] = []
        record_context_index = 0
        for record, request_context, preliminary in prepared_rows:
            date_evidence = _independent_service_date(preliminary, schedule)
            independent_dates.append(date_evidence)
            service_date = date_evidence.get("established_service_date")
            observation = stage9.adapt_stage8_stop_event(
                record,
                service_date=service_date,
                feed_timezone=FEED_TIMEZONE,
                sampling_policy_id=SAMPLING_POLICY,
                sampling_era_id=SAMPLING_ERA,
            )
            decision = stage9.match_source_event(
                observation,
                schedule,
                bridge,
                identity_mapping_version=mapping_version,
                evidence_validation=matching_validation,
            )
            hierarchy = feed_context["hierarchy"].get(observation.source_stop_id or "", {
                "static_stop_found": False,
                "static_stop_missing": True,
                "parent_fallback_used": False,
                "suffix_stripping_used": False,
                "exact_scheduled_stop_usage": 0,
            })
            evidence_records.append(
                _evidence_record(
                    observation,
                    decision,
                    date_evidence,
                    hierarchy,
                    schedule,
                    static_metadata,
                    request_context,
                )
            )
            observations.append(observation)
            decisions.append(decision)
            record_context_index += 1

        _write_jsonl(evidence_path, evidence_records)
        optional = _optional_metrics(observations, decisions, schedule, bridge)
        date_alignment = stage9.validate_service_date_alignment(
            observations,
            valid_service_dates=set(feed_context["active_service_dates"]),
        )
        classifications = Counter(item.data_quality_classification for item in decisions)
        confidences = Counter(item.match_confidence for item in decisions)
        core_matches = _core_deterministic_matches(decisions)
        independent_service_date_count = sum(
            1 for item in independent_dates if item.get("status") == "ESTABLISHED"
        )
        confidence_summary = {
            stage9.MATCH_HIGH: confidences.get(stage9.MATCH_HIGH, 0),
            stage9.MATCH_MEDIUM: confidences.get(stage9.MATCH_MEDIUM, 0),
            stage9.MATCH_LOW: confidences.get(stage9.MATCH_LOW, 0),
            stage9.MATCH_UNMATCHED: confidences.get(stage9.MATCH_UNMATCHED, 0),
        }
        ambiguity_count = sum(
            1 for item in decisions if item.data_quality_classification == stage9.QUALITY_AMBIGUOUS_MULTIPLE_CANDIDATES
        )
        summary: Dict[str, Any] = {
            "record_type": "stage_09_source_backed_acceptance_summary",
            "artifact_version": "stage-09-source-backed-summary-1.0",
            "generated_at_utc": _iso_utc(datetime.now(timezone.utc)),
            "script_version": SCRIPT_VERSION,
            "run_id": run_id,
            "evidence_origin": SOURCE_BACKED_EVIDENCE_ORIGIN,
            "implementation_regression_status": "PASS",
            "source_backed_acceptance_status": "BLOCKED",
            "stage_9_status": "BLOCKED",
            "blocker": None,
            "mdd_http_attempts": mdd_attempts,
            "mdd_request_contract": {
                "station_count_maximum": MAX_STATIONS,
                "one_request_per_station": True,
                "sequential": True,
                "retries": 0,
                "number_of_results": 20,
                "stop_event_type": "arrival",
                "include_previous_calls": True,
                "include_onward_calls": True,
                "include_realtime_data": True,
            },
            "mdd_request_contexts": request_contexts,
            "vrs_snapshot": static_metadata,
            "stage4_revalidation": static_validation,
            "source_backed_observation_count": len(observations),
            "source_backed_confidence_counts": confidence_summary,
            "ambiguity_count": ambiguity_count,
            "core_deterministic_match_count": len(core_matches),
            "stop_point_and_hierarchy": {
                "exact_mdd_stoppointref_to_vrs_stop_count": sum(
                    1 for item in observations if feed_context["hierarchy"].get(item.source_stop_id or "", {}).get("static_stop_found")
                ),
                "explicit_child_to_parent_hierarchy_count": sum(
                    1 for item in observations if feed_context["hierarchy"].get(item.source_stop_id or "", {}).get("explicit_parent_relationship")
                ),
                "unresolved_stop_identity_count": sum(
                    1 for item in observations if not feed_context["hierarchy"].get(item.source_stop_id or "", {}).get("static_stop_found")
                ),
                "suffix_stripping_used": False,
                "parent_fallback_used": False,
            },
            "independently_established_service_date": {
                "count": independent_service_date_count,
                "unresolved_count": len(observations) - independent_service_date_count,
                "status_counts": dict(sorted(Counter(item.get("status") for item in independent_dates).items())),
            },
            "operating_day_ref": date_alignment["operating_day_ref"],
            "exact_scheduled_time_alignment_count": sum(
                1 for item in decisions if item.evidence.get("scheduled_time_match")
            ),
            "route_line_identity": _line_metrics(observations, decisions, schedule),
            "stop_sequence": optional["stop_sequence"],
            "direction": optional["direction"],
            "origin_destination": optional["origin_destination"],
            "decision_classification_counts": dict(sorted(classifications.items())),
            "data_quality_counts": {
                stage9.QUALITY_STATIC_STOP_MISSING: classifications.get(stage9.QUALITY_STATIC_STOP_MISSING, 0),
                stage9.QUALITY_STATIC_ROUTE_MISSING: classifications.get(stage9.QUALITY_STATIC_ROUTE_MISSING, 0),
                stage9.QUALITY_STATIC_SERVICE_DATE_NOT_ACTIVE: classifications.get(stage9.QUALITY_STATIC_SERVICE_DATE_NOT_ACTIVE, 0),
                stage9.QUALITY_STATIC_EVENT_NOT_FOUND: classifications.get(stage9.QUALITY_STATIC_EVENT_NOT_FOUND, 0),
                stage9.QUALITY_IDENTITY_CONFLICT: classifications.get(stage9.QUALITY_IDENTITY_CONFLICT, 0),
                stage9.QUALITY_OUT_OF_CORE_SCOPE: classifications.get(stage9.QUALITY_OUT_OF_CORE_SCOPE, 0),
            },
            "identity_mapping_version": mapping_version,
            "evidence_path": str(evidence_path.relative_to(REPOSITORY_ROOT)),
            "summary_path": str(summary_path.relative_to(REPOSITORY_ROOT)),
            "raw_mdd_payloads_persisted": False,
            "raw_mdd_payload_cleanup": "IN_MEMORY_ONLY; temporary GTFS and response processing storage deleted after artifact write",
            "gtfsrt_railway_hbf_identity": stage9.EXPECTED_RAILWAY_HBF_STATUS,
            "stage_10_status": "NOT_STARTED",
            "tiered50_activated": False,
            "operational_fact_started": False,
        }
        row_level_integrity = validate_source_backed_evidence_artifact(evidence_path, summary)
        summary["row_level_evidence_integrity_status"] = row_level_integrity["status"]
        summary["row_level_evidence_integrity"] = row_level_integrity
        summary["row_level_evidence_serialization_status"] = (
            "LOSSLESS_AUDIT_FIELDS_WITH_BOUNDED_DIAGNOSTICS"
            if row_level_integrity["status"] == "PASS"
            else "FAILED_ROW_LEVEL_INTEGRITY_VALIDATION"
        )
        acceptance_status = source_backed_acceptance_status(
            evidence_origin=SOURCE_BACKED_EVIDENCE_ORIGIN,
            static_validation_status=static_validation["status"],
            source_observation_count=len(observations),
            independent_service_date_count=independent_service_date_count,
            core_deterministic_match_count=len(core_matches),
            mdd_http_attempts=mdd_attempts,
            artifact_written=evidence_path.is_file(),
            artifact_integrity_status=row_level_integrity["status"],
        )
        summary["source_backed_acceptance_status"] = acceptance_status
        summary["stage_9_status"] = (
            "COMPLETE — APPROVED MATCHING & DATA QUALITY SEMANTICS"
            if acceptance_status == "PASS"
            else "BLOCKED"
        )
        if acceptance_status != "PASS":
            summary["blocker"] = (
                "row-level evidence integrity validation failed: "
                + "; ".join(row_level_integrity.get("errors", [])[:5])
                if row_level_integrity["status"] != "PASS"
                else "source-backed acceptance prerequisites were not satisfied"
            )
        _write_summary(summary_path, summary)
        try:
            stage9.write_stage9_evidence_artifact(STAGE9_DOC_EVIDENCE_PATH)
        except Exception as error:
            summary["stage9_evidence_artifact_update"] = f"FAILED: {str(error)[:300]}"
            _write_summary(summary_path, summary)
        return summary
    except Exception as error:
        blocker = str(error)[:500]
        summary = _empty_blocked_summary(
            preflight_status=credential_status,
            blocker=blocker,
            evidence_path=evidence_path,
            summary_path=summary_path,
        )
        summary.update(
            {
                "run_id": run_id,
                "mdd_http_attempts": mdd_attempts,
                "mdd_request_contexts": request_contexts,
                "vrs_snapshot": static_metadata,
                "stage4_revalidation": static_validation,
            }
        )
        _write_summary(summary_path, summary)
        return summary
    finally:
        shutil.rmtree(temp_directory, ignore_errors=True)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded source-backed Stage 9 acceptance probe")
    parser.add_argument("--evidence-path", type=Path, default=DEFAULT_EVIDENCE_PATH)
    parser.add_argument("--summary-path", type=Path, default=DEFAULT_SUMMARY_PATH)
    args = parser.parse_args(argv)
    summary = run_source_backed_acceptance(
        evidence_path=args.evidence_path.expanduser().resolve(),
        summary_path=args.summary_path.expanduser().resolve(),
    )
    print(
        json.dumps(
            _json_value(summary, max_items=ARTIFACT_MAX_ITEMS),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ),
        flush=True,
    )
    return 0 if summary.get("source_backed_acceptance_status") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
