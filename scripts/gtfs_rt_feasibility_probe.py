#!/usr/bin/env python3
# Do not execute from Codex.
# This script performs bounded external HTTP requests.
# Run manually from the normal macOS Terminal.
"""Bounded, read-only GTFS-Realtime source-feasibility audit.

The script deliberately keeps the GTFS-Realtime source under test separate
from the project's MDD/TRIAS source and production collector.  It is a host-
run diagnostic: inventory makes one request and lifecycle makes at most seven
sequential requests for one selected station.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


SOURCE_URL = "https://realtime.gtfs.de/realtime-free.pb"
PROBE_NAME = "gtfs_rt_feasibility_probe"
REQUEST_TIMEOUT_SECONDS = 30
MAX_FEED_BYTES = 50 * 1024 * 1024
MAX_EXAMPLES = 10
MAX_ERROR_LENGTH = 240


@dataclass(frozen=True)
class StationConfig:
    key: str
    display_name: str
    stop_prefix: str


STATIONS = {
    "hbf": StationConfig("hbf", "Köln Hbf", "de:05315:11201"),
    "neumarkt": StationConfig("neumarkt", "Köln Neumarkt", "de:05315:11111"),
}


@dataclass
class FetchResult:
    request_timestamp: datetime
    http_status: Optional[int]
    elapsed_ms: float
    response_bytes: int
    body: Optional[bytes]
    content_type: Optional[str]
    content_length: Optional[int] = None
    content_length_matches: Optional[bool] = None
    error_kind: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class EventRecord:
    time_seconds: Optional[int]
    delay_seconds: Optional[int]
    uncertainty_seconds: Optional[int]


@dataclass
class StopTimeRecord:
    message: Any
    stop_id: Optional[str]
    stop_sequence: Optional[int]
    arrival: EventRecord
    departure: EventRecord
    schedule_relationship: Optional[str]
    assigned_stop_id: Optional[str]
    stop_index: int


@dataclass
class TripUpdateRecord:
    message: Any
    entity_id: Optional[str]
    entity_index: int
    trip: dict[str, Any]
    stops: list[StopTimeRecord]
    timestamp_seconds: Optional[int]


@dataclass
class FeedAnalysis:
    summary: dict[str, Any]
    trip_records: list[TripUpdateRecord]
    station_trip_record_indices: dict[str, set[int]]
    station_stop_records: list[tuple[str, TripUpdateRecord, StopTimeRecord]]


@dataclass
class StaticGTFSIndex:
    path: str
    trips: dict[str, dict[str, str]]
    routes: set[str]
    stops: dict[str, dict[str, str]]
    stop_times_by_trip: dict[str, list[dict[str, str]]]


@dataclass
class Target:
    station: StationConfig
    identity: dict[str, Any]
    event_context: str
    initial_event_seconds: int


@dataclass
class TargetEvaluation:
    target_match_status: str
    trip_match_status: str
    trip_record: Optional[TripUpdateRecord]
    stop_record: Optional[StopTimeRecord]
    potential_trip_match_count: int
    potential_stop_match_count: int


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects so one planned request cannot become hidden requests."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        raise urllib.error.HTTPError(
            req.full_url,
            code,
            "redirect rejected by bounded audit",
            headers,
            fp,
        )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def epoch_to_iso(seconds: Optional[int]) -> Optional[str]:
    if seconds is None:
        return None
    try:
        return iso_utc(datetime.fromtimestamp(seconds, tz=timezone.utc))
    except (OverflowError, OSError, ValueError):
        return None


def round_number(value: float) -> float:
    return round(value, 3)


def clean_error(exc: BaseException) -> str:
    message = " ".join(str(exc).split())
    if not message:
        message = type(exc).__name__
    return message[:MAX_ERROR_LENGTH]


def load_gtfs_realtime_bindings() -> Any:
    try:
        from google.transit import gtfs_realtime_pb2
    except ImportError:
        print("GTFS_RT_BINDINGS_PRESENT: NO")
        print("Install with:")
        print("python3 -m pip install -r scripts/requirements-gtfs-rt-audit.txt")
        return None
    return gtfs_realtime_pb2


def create_raw_temp_directory() -> Optional[Path]:
    """Create a private diagnostic directory outside the repository."""

    candidates = [Path("/private/tmp"), Path(tempfile.gettempdir())]
    seen: set[str] = set()
    for parent in candidates:
        parent_text = str(parent)
        if parent_text in seen or not parent.is_dir() or not os.access(parent, os.W_OK):
            continue
        seen.add(parent_text)
        try:
            directory = Path(tempfile.mkdtemp(prefix="cologne-transit-gtfs-rt-audit-", dir=parent_text))
            os.chmod(directory, 0o700)
            return directory
        except OSError:
            continue
    return None


def write_raw_snapshot(directory: Optional[Path], attempt_number: int, body: bytes) -> Optional[str]:
    if directory is None:
        return None
    path = directory / f"attempt-{attempt_number:02d}.pb"
    try:
        file_descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(file_descriptor, "wb") as output:
            output.write(body)
        os.chmod(path, 0o600)
        return str(path)
    except OSError:
        return None


def read_response_bounded(response: Any) -> bytes:
    body = bytearray()
    while True:
        chunk = response.read(1024 * 1024)
        if not chunk:
            break
        body.extend(chunk)
        if len(body) > MAX_FEED_BYTES:
            raise ValueError(f"response exceeds bounded {MAX_FEED_BYTES} byte limit")
    return bytes(body)


def header_content_length(headers: Any) -> Optional[int]:
    try:
        value = headers.get("Content-Length")
    except AttributeError:
        return None
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def fetch_feed(raw_directory: Optional[Path], attempt_number: int) -> FetchResult:
    request_timestamp = utc_now()
    started = time.monotonic()
    request = urllib.request.Request(
        SOURCE_URL,
        method="GET",
        headers={
            "Accept": "application/x-protobuf, application/octet-stream",
            "User-Agent": "cologne-transit-gtfs-rt-feasibility-audit/1.0",
        },
    )
    opener = urllib.request.build_opener(NoRedirectHandler())

    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status = int(response.getcode())
            content_type = response.headers.get("Content-Type")
            content_length = header_content_length(response.headers)
            body = read_response_bounded(response)
        elapsed_ms = round_number((time.monotonic() - started) * 1000)
        content_length_matches = (
            None if content_length is None else content_length == len(body)
        )
        if status != 200:
            return FetchResult(
                request_timestamp=request_timestamp,
                http_status=status,
                elapsed_ms=elapsed_ms,
                response_bytes=len(body),
                body=None,
                content_type=content_type,
                content_length=content_length,
                content_length_matches=content_length_matches,
                error_kind="HTTP_STATUS",
                error_message=f"HTTP status {status}",
            )
        write_raw_snapshot(raw_directory, attempt_number, body)
        return FetchResult(
            request_timestamp=request_timestamp,
            http_status=status,
            elapsed_ms=elapsed_ms,
            response_bytes=len(body),
            body=body,
            content_type=content_type,
            content_length=content_length,
            content_length_matches=content_length_matches,
        )
    except urllib.error.HTTPError as exc:
        elapsed_ms = round_number((time.monotonic() - started) * 1000)
        return FetchResult(
            request_timestamp=request_timestamp,
            http_status=int(exc.code) if exc.code is not None else None,
            elapsed_ms=elapsed_ms,
            response_bytes=0,
            body=None,
            content_type=exc.headers.get("Content-Type") if exc.headers else None,
            content_length=header_content_length(exc.headers) if exc.headers else None,
            error_kind="HTTP_STATUS",
            error_message=f"HTTP status {exc.code}",
        )
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, ValueError) as exc:
        elapsed_ms = round_number((time.monotonic() - started) * 1000)
        return FetchResult(
            request_timestamp=request_timestamp,
            http_status=None,
            elapsed_ms=elapsed_ms,
            response_bytes=0,
            body=None,
            content_type=None,
            error_kind="REQUEST_FAILED",
            error_message=clean_error(exc),
        )


def field_descriptor(message: Any, field_name: str) -> Any:
    descriptor = getattr(message, "DESCRIPTOR", None)
    if descriptor is None:
        return None
    return descriptor.fields_by_name.get(field_name)


def field_is_present(message: Any, field_name: str) -> bool:
    field = field_descriptor(message, field_name)
    if field is None:
        return False
    repeated_label = getattr(field, "LABEL_REPEATED", 3)
    if field.label == repeated_label:
        try:
            return len(getattr(message, field_name)) > 0
        except (AttributeError, TypeError):
            return False
    try:
        return bool(message.HasField(field_name))
    except (AttributeError, TypeError, ValueError):
        try:
            return getattr(message, field_name) != field.default_value
        except (AttributeError, TypeError):
            return False


def get_message(message: Any, field_name: str) -> Any:
    field = field_descriptor(message, field_name)
    if field is None or field.message_type is None or not field_is_present(message, field_name):
        return None
    return getattr(message, field_name)


def get_repeated(message: Any, field_name: str) -> list[Any]:
    field = field_descriptor(message, field_name)
    if field is None:
        return []
    repeated_label = getattr(field, "LABEL_REPEATED", 3)
    if field.label != repeated_label:
        return []
    try:
        return list(getattr(message, field_name))
    except (AttributeError, TypeError):
        return []


def text_field(message: Any, field_name: str) -> Optional[str]:
    if message is None or field_descriptor(message, field_name) is None:
        return None
    try:
        value = str(getattr(message, field_name))
    except (AttributeError, TypeError):
        return None
    return value if value else None


def optional_int_field(message: Any, field_name: str) -> Optional[int]:
    if message is None or field_descriptor(message, field_name) is None:
        return None
    try:
        value = int(getattr(message, field_name))
    except (AttributeError, TypeError, ValueError):
        return None
    if not field_is_present(message, field_name) and value == 0:
        return None
    return value


def enum_name(message: Any, field_name: str, only_if_present: bool = False) -> Optional[str]:
    field = field_descriptor(message, field_name)
    if field is None or field.enum_type is None:
        return None
    if only_if_present and not field_is_present(message, field_name):
        return None
    try:
        value = int(getattr(message, field_name))
        enum_value = field.enum_type.values_by_number.get(value)
        return enum_value.name if enum_value is not None else str(value)
    except (AttributeError, TypeError, ValueError):
        return None


def extract_event(message: Any, field_name: str) -> EventRecord:
    event = get_message(message, field_name)
    if event is None:
        return EventRecord(None, None, None)
    time_seconds = optional_int_field(event, "time")
    if time_seconds is not None and time_seconds <= 0:
        time_seconds = None
    return EventRecord(
        time_seconds=time_seconds,
        delay_seconds=optional_int_field(event, "delay"),
        uncertainty_seconds=optional_int_field(event, "uncertainty"),
    )


def extract_assigned_stop_id(message: Any) -> Optional[str]:
    direct_value = text_field(message, "assigned_stop_id")
    if direct_value is not None:
        return direct_value
    properties = get_message(message, "stop_time_properties")
    return text_field(properties, "assigned_stop_id")


def extract_trip_descriptor(message: Any) -> dict[str, Any]:
    trip = get_message(message, "trip")
    if trip is None:
        return {
            "trip_id": None,
            "route_id": None,
            "direction_id": None,
            "start_date": None,
            "start_time": None,
            "schedule_relationship": None,
        }
    return {
        "trip_id": text_field(trip, "trip_id"),
        "route_id": text_field(trip, "route_id"),
        "direction_id": text_field(trip, "direction_id"),
        "start_date": text_field(trip, "start_date"),
        "start_time": text_field(trip, "start_time"),
        "schedule_relationship": enum_name(trip, "schedule_relationship"),
    }


def extract_stop_time_update(message: Any, stop_index: int) -> StopTimeRecord:
    return StopTimeRecord(
        message=message,
        stop_id=text_field(message, "stop_id"),
        stop_sequence=optional_int_field(message, "stop_sequence"),
        arrival=extract_event(message, "arrival"),
        departure=extract_event(message, "departure"),
        schedule_relationship=enum_name(message, "schedule_relationship"),
        assigned_stop_id=extract_assigned_stop_id(message),
        stop_index=stop_index,
    )


def iter_entity_fields(entity: Any) -> Iterable[str]:
    descriptor = getattr(entity, "DESCRIPTOR", None)
    if descriptor is None:
        return []
    return (field.name for field in descriptor.fields if field_is_present(entity, field.name))


def extract_trip_records(feed: Any) -> list[TripUpdateRecord]:
    records: list[TripUpdateRecord] = []
    for entity_index, entity in enumerate(get_repeated(feed, "entity")):
        trip_update = get_message(entity, "trip_update")
        if trip_update is None:
            continue
        records.append(
            TripUpdateRecord(
                message=trip_update,
                entity_id=text_field(entity, "id"),
                entity_index=entity_index,
                trip=extract_trip_descriptor(trip_update),
                stops=[
                    extract_stop_time_update(stop_update, stop_index)
                    for stop_index, stop_update in enumerate(get_repeated(trip_update, "stop_time_update"))
                ],
                timestamp_seconds=optional_int_field(trip_update, "timestamp"),
            )
        )
    return records


def station_match_type(stop_id: Optional[str], station: StationConfig) -> Optional[str]:
    if stop_id is None:
        return None
    if stop_id == station.stop_prefix:
        return "exact_parent"
    if stop_id.startswith(f"{station.stop_prefix}:"):
        return "child_prefix"
    return None


def capped_sorted(values: Iterable[str]) -> list[str]:
    return sorted(set(values))[:MAX_EXAMPLES]


def station_match_summary(
    station: StationConfig,
    exact_ids: set[str],
    child_ids: set[str],
) -> dict[str, Any]:
    return {
        "station_display_name": station.display_name,
        "stop_prefix": station.stop_prefix,
        "count": len(exact_ids | child_ids),
        "exact_parent_count": len(exact_ids),
        "child_prefix_count": len(child_ids),
        "exact_stop_id_examples": capped_sorted(exact_ids),
        "child_stop_id_examples": capped_sorted(child_ids),
    }


def alert_selector_references(selector: Any) -> tuple[Optional[str], Optional[str], Optional[str]]:
    trip_descriptor = get_message(selector, "trip")
    trip_id = text_field(trip_descriptor, "trip_id")
    route_id = text_field(selector, "route_id") or text_field(trip_descriptor, "route_id")
    stop_id = text_field(selector, "stop_id")
    direct_trip_id = text_field(selector, "trip_id")
    return direct_trip_id or trip_id, route_id, stop_id


def inspect_actual_semantic_surface(bindings: Any) -> dict[str, Any]:
    """Inspect descriptors without treating names as proof of actual timing.

    Standard StopTimeEvent.time has estimated-or-actual semantics in GTFS-RT.
    A declared extension is surfaced as ambiguous for manual review; its name
    alone is never promoted to an explicit actual-time conclusion.
    """

    extension_names: set[str] = set()
    visited: set[int] = set()

    def walk_message_descriptors(descriptor: Any) -> Iterable[Any]:
        descriptor_id = id(descriptor)
        if descriptor_id in visited:
            return []
        visited.add(descriptor_id)
        nested = list(descriptor.nested_types)
        result = [descriptor]
        for nested_descriptor in nested:
            result.extend(walk_message_descriptors(nested_descriptor))
        return result

    file_descriptor = getattr(bindings, "DESCRIPTOR", None)
    if file_descriptor is not None:
        for descriptor in file_descriptor.message_types_by_name.values():
            for message_descriptor in walk_message_descriptors(descriptor):
                for extension in getattr(message_descriptor, "extensions", []):
                    extension_names.add(extension.full_name)
        for extension in getattr(file_descriptor, "extensions", []):
            extension_names.add(extension.full_name)
        for extension in getattr(file_descriptor, "extensions_by_name", {}).values():
            extension_names.add(extension.full_name)

    classification = (
        "ACTUAL_SEMANTIC_AMBIGUOUS"
        if extension_names
        else "NO_EXPLICIT_ACTUAL_SEMANTIC_OBSERVED"
    )
    return {
        "actual_time_semantic_classification": classification,
        "standard_stop_time_event_time_semantics": "ESTIMATED_OR_ACTUAL_EVENT_TIME",
        "provider_specific_extension_fields_exposed": capped_sorted(extension_names),
        "provider_specific_extension_field_count": len(extension_names),
        "explicit_actual_semantic_evidence": [],
    }


def analyze_feed(feed: Any, request_timestamp: datetime) -> FeedAnalysis:
    entities = get_repeated(feed, "entity")
    trip_records = extract_trip_records(feed)
    station_trip_record_indices = {station_key: set() for station_key in STATIONS}
    station_stop_records: list[tuple[str, TripUpdateRecord, StopTimeRecord]] = []

    trip_ids: set[str] = set()
    route_ids: set[str] = set()
    stop_ids: set[str] = set()
    hbf_exact_ids: set[str] = set()
    hbf_child_ids: set[str] = set()
    neumarkt_exact_ids: set[str] = set()
    neumarkt_child_ids: set[str] = set()
    hbf_trip_indices: set[int] = set()
    neumarkt_trip_indices: set[int] = set()

    trip_schedule_relationship_counts: dict[str, int] = {}
    stop_schedule_relationship_counts: dict[str, int] = {}
    arrival_time_count = 0
    arrival_delay_count = 0
    arrival_uncertainty_count = 0
    departure_time_count = 0
    departure_delay_count = 0
    departure_uncertainty_count = 0
    skipped_stop_update_count = 0
    no_data_stop_update_count = 0
    assigned_stop_id_count = 0

    for record_index, record in enumerate(trip_records):
        trip_id = record.trip.get("trip_id")
        route_id = record.trip.get("route_id")
        if trip_id:
            trip_ids.add(trip_id)
        if route_id:
            route_ids.add(route_id)
        trip_relationship = record.trip.get("schedule_relationship")
        if trip_relationship is not None:
            trip_schedule_relationship_counts[trip_relationship] = (
                trip_schedule_relationship_counts.get(trip_relationship, 0) + 1
            )

        for stop_record in record.stops:
            stop_id = stop_record.stop_id
            if stop_id:
                stop_ids.add(stop_id)
            if stop_record.arrival.time_seconds is not None:
                arrival_time_count += 1
            if stop_record.arrival.delay_seconds is not None:
                arrival_delay_count += 1
            if stop_record.arrival.uncertainty_seconds is not None:
                arrival_uncertainty_count += 1
            if stop_record.departure.time_seconds is not None:
                departure_time_count += 1
            if stop_record.departure.delay_seconds is not None:
                departure_delay_count += 1
            if stop_record.departure.uncertainty_seconds is not None:
                departure_uncertainty_count += 1
            stop_relationship = stop_record.schedule_relationship
            if stop_relationship is not None:
                stop_schedule_relationship_counts[stop_relationship] = (
                    stop_schedule_relationship_counts.get(stop_relationship, 0) + 1
                )
                if stop_relationship == "SKIPPED":
                    skipped_stop_update_count += 1
                if stop_relationship == "NO_DATA":
                    no_data_stop_update_count += 1
            if stop_record.assigned_stop_id:
                assigned_stop_id_count += 1

            for station_key, station in STATIONS.items():
                match_type = station_match_type(stop_id, station)
                if match_type is None:
                    continue
                station_stop_records.append((station_key, record, stop_record))
                station_trip_record_indices[station_key].add(record_index)
                if station_key == "hbf":
                    if match_type == "exact_parent":
                        hbf_exact_ids.add(stop_id or "")
                        hbf_trip_indices.add(record_index)
                    else:
                        hbf_child_ids.add(stop_id or "")
                        hbf_trip_indices.add(record_index)
                else:
                    if match_type == "exact_parent":
                        neumarkt_exact_ids.add(stop_id or "")
                        neumarkt_trip_indices.add(record_index)
                    else:
                        neumarkt_child_ids.add(stop_id or "")
                        neumarkt_trip_indices.add(record_index)

    header = get_message(feed, "header")
    feed_header_timestamp_seconds = optional_int_field(header, "timestamp")
    feed_header_timestamp = (
        datetime.fromtimestamp(feed_header_timestamp_seconds, tz=timezone.utc)
        if feed_header_timestamp_seconds is not None
        else None
    )
    feed_age_seconds = (
        round_number((request_timestamp - feed_header_timestamp).total_seconds())
        if feed_header_timestamp is not None
        else None
    )
    header_validation_errors: list[str] = []
    gtfs_realtime_version = text_field(header, "gtfs_realtime_version")
    if header is None:
        header_validation_errors.append("header_missing")
    elif not gtfs_realtime_version:
        header_validation_errors.append("gtfs_realtime_version_missing")
    header_validation_status = "VALID" if not header_validation_errors else "INCONCLUSIVE"

    alert_entity_count = 0
    alert_informed_entity_count = 0
    alerts_referencing_trip_ids = 0
    alerts_referencing_route_ids = 0
    alerts_referencing_stop_ids = 0
    alert_station_stop_ids: set[str] = set()
    for entity in entities:
        alert = get_message(entity, "alert")
        if alert is None:
            continue
        alert_entity_count += 1
        trip_reference_found = False
        route_reference_found = False
        stop_reference_found = False
        for selector in get_repeated(alert, "informed_entity"):
            alert_informed_entity_count += 1
            trip_id, route_id, stop_id = alert_selector_references(selector)
            trip_reference_found = trip_reference_found or trip_id is not None
            route_reference_found = route_reference_found or route_id is not None
            stop_reference_found = stop_reference_found or stop_id is not None
            if stop_id and any(station_match_type(stop_id, station) for station in STATIONS.values()):
                alert_station_stop_ids.add(stop_id)
        if trip_reference_found:
            alerts_referencing_trip_ids += 1
        if route_reference_found:
            alerts_referencing_route_ids += 1
        if stop_reference_found:
            alerts_referencing_stop_ids += 1

    vehicle_entity_count = 0
    other_entity_count = 0
    other_entity_type_counts: dict[str, int] = {}
    recognized_payloads = {"trip_update", "vehicle", "alert"}
    ignored_entity_fields = {"id", "is_deleted"}
    for entity in entities:
        if get_message(entity, "vehicle") is not None:
            vehicle_entity_count += 1
        present_fields = set(iter_entity_fields(entity))
        if not present_fields.intersection(recognized_payloads):
            other_entity_count += 1
        for field_name in sorted(present_fields - recognized_payloads - ignored_entity_fields):
            other_entity_type_counts[field_name] = other_entity_type_counts.get(field_name, 0) + 1

    hbf_match_summary = station_match_summary(STATIONS["hbf"], hbf_exact_ids, hbf_child_ids)
    neumarkt_match_summary = station_match_summary(
        STATIONS["neumarkt"], neumarkt_exact_ids, neumarkt_child_ids
    )
    cologne_trip_update_count = len(hbf_trip_indices | neumarkt_trip_indices)
    cologne_evidence_types: list[str] = []
    if hbf_trip_indices:
        cologne_evidence_types.append("HBF_TRIP_UPDATE_STOP_ID_PREFIX")
    if neumarkt_trip_indices:
        cologne_evidence_types.append("NEUMARKT_TRIP_UPDATE_STOP_ID_PREFIX")
    if alert_station_stop_ids:
        cologne_evidence_types.append("SERVICE_ALERT_STOP_ID_PREFIX")

    summary = {
        "gtfs_realtime_version": gtfs_realtime_version,
        "feed_incrementality": enum_name(header, "incrementality", only_if_present=True),
        "feed_header_timestamp_utc": iso_utc(feed_header_timestamp),
        "feed_age_seconds_at_download": feed_age_seconds,
        "feed_header_validation_status": header_validation_status,
        "feed_header_validation_errors": header_validation_errors,
        "entity_count_total": len(entities),
        "trip_update_entity_count": len(trip_records),
        "vehicle_entity_count": vehicle_entity_count,
        "alert_entity_count": alert_entity_count,
        "other_entity_count": other_entity_count,
        "other_entity_type_counts": other_entity_type_counts,
        "trip_updates_with_timestamp_count": sum(
            1 for record in trip_records if record.timestamp_seconds is not None
        ),
        "trip_updates_without_timestamp_count": sum(
            1 for record in trip_records if record.timestamp_seconds is None
        ),
        "stop_time_update_count_total": sum(len(record.stops) for record in trip_records),
        "stop_time_updates_with_stop_id_count": sum(
            1 for record in trip_records for stop in record.stops if stop.stop_id is not None
        ),
        "stop_time_updates_with_stop_sequence_count": sum(
            1 for record in trip_records for stop in record.stops if stop.stop_sequence is not None
        ),
        "arrival_time_count": arrival_time_count,
        "arrival_delay_count": arrival_delay_count,
        "arrival_uncertainty_count": arrival_uncertainty_count,
        "departure_time_count": departure_time_count,
        "departure_delay_count": departure_delay_count,
        "departure_uncertainty_count": departure_uncertainty_count,
        "trip_schedule_relationship_counts": dict(sorted(trip_schedule_relationship_counts.items())),
        "stop_schedule_relationship_counts": dict(sorted(stop_schedule_relationship_counts.items())),
        "canceled_trip_count": trip_schedule_relationship_counts.get("CANCELED", 0),
        "skipped_stop_update_count": skipped_stop_update_count,
        "no_data_stop_update_count": no_data_stop_update_count,
        "assigned_stop_id_count": assigned_stop_id_count,
        "unique_trip_id_count": len(trip_ids),
        "unique_route_id_count": len(route_ids),
        "unique_stop_id_count": len(stop_ids),
        "trip_id_examples": capped_sorted(trip_ids),
        "route_id_examples": capped_sorted(route_ids),
        "stop_id_examples": capped_sorted(stop_ids),
        "hbf_stop_id_matches": hbf_match_summary,
        "neumarkt_stop_id_matches": neumarkt_match_summary,
        "hbf_trip_update_count": len(hbf_trip_indices),
        "neumarkt_trip_update_count": len(neumarkt_trip_indices),
        "cologne_trip_update_count": cologne_trip_update_count,
        "cologne_evidence_types": cologne_evidence_types,
        "alert_count": alert_entity_count,
        "alert_informed_entity_count": alert_informed_entity_count,
        "alerts_referencing_trip_ids": alerts_referencing_trip_ids,
        "alerts_referencing_route_ids": alerts_referencing_route_ids,
        "alerts_referencing_stop_ids": alerts_referencing_stop_ids,
        "alert_station_stop_id_examples": capped_sorted(alert_station_stop_ids),
    }
    return FeedAnalysis(
        summary=summary,
        trip_records=trip_records,
        station_trip_record_indices=station_trip_record_indices,
        station_stop_records=station_stop_records,
    )


def parse_feed(body: Optional[bytes], bindings: Any) -> tuple[Any, Optional[str]]:
    if body is None:
        return None, "response body unavailable"
    try:
        feed = bindings.FeedMessage()
        feed.ParseFromString(body)
        return feed, None
    except Exception as exc:  # protobuf implementations expose different decode exceptions
        return None, clean_error(exc)


def read_csv_rows(archive: zipfile.ZipFile, filename: str) -> list[dict[str, str]]:
    if filename not in archive.namelist():
        raise FileNotFoundError(f"{filename} missing from static GTFS ZIP")
    with archive.open(filename, "r") as binary_stream:
        text_stream = io.TextIOWrapper(binary_stream, encoding="utf-8-sig", newline="")
        return [dict(row) for row in csv.DictReader(text_stream)]


def load_static_gtfs(path_text: Optional[str], relevant_trip_ids: set[str]) -> tuple[Optional[StaticGTFSIndex], Optional[str]]:
    if path_text is None:
        return None, None
    path = Path(path_text)
    if not path.is_absolute():
        return None, "STATIC_GTFS_PATH_NOT_ABSOLUTE"
    if not path.is_file():
        return None, "STATIC_GTFS_FILE_NOT_FOUND"
    try:
        with zipfile.ZipFile(path, "r") as archive:
            trips_rows = read_csv_rows(archive, "trips.txt")
            routes_rows = read_csv_rows(archive, "routes.txt")
            stops_rows = read_csv_rows(archive, "stops.txt")
            stop_times_rows = read_csv_rows(archive, "stop_times.txt")
    except (OSError, zipfile.BadZipFile, UnicodeError, csv.Error, FileNotFoundError) as exc:
        return None, clean_error(exc)

    trips: dict[str, dict[str, str]] = {}
    for row in trips_rows:
        trip_id = row.get("trip_id", "")
        if trip_id and trip_id not in trips:
            trips[trip_id] = row
    routes = {row.get("route_id", "") for row in routes_rows if row.get("route_id", "")}
    stops: dict[str, dict[str, str]] = {}
    for row in stops_rows:
        stop_id = row.get("stop_id", "")
        if stop_id and stop_id not in stops:
            stops[stop_id] = row
    stop_times_by_trip: dict[str, list[dict[str, str]]] = {}
    for row in stop_times_rows:
        trip_id = row.get("trip_id", "")
        if trip_id and trip_id in relevant_trip_ids:
            stop_times_by_trip.setdefault(trip_id, []).append(row)
    return (
        StaticGTFSIndex(
            path=str(path),
            trips=trips,
            routes=routes,
            stops=stops,
            stop_times_by_trip=stop_times_by_trip,
        ),
        None,
    )


def relevant_static_trip_ids(analysis: FeedAnalysis) -> set[str]:
    trip_ids: set[str] = set()
    relevant_indices = set()
    for indices in analysis.station_trip_record_indices.values():
        relevant_indices.update(indices)
    for index in relevant_indices:
        trip_id = analysis.trip_records[index].trip.get("trip_id")
        if trip_id:
            trip_ids.add(trip_id)
    return trip_ids


def has_static_parent_reference(static: StaticGTFSIndex, station: StationConfig) -> bool:
    if station.stop_prefix in static.stops:
        return True
    return any(
        row.get("parent_station", "") == station.stop_prefix
        for row in static.stops.values()
    )


def static_stop_match_kind(
    static: StaticGTFSIndex,
    station: StationConfig,
    stop_id: Optional[str],
) -> str:
    if not stop_id:
        return "NOT_TESTABLE_MISSING_STOP_ID"
    if stop_id in static.stops:
        return "EXACT"
    if station_match_type(stop_id, station) and has_static_parent_reference(static, station):
        return "PARENT_PREFIX_ONLY"
    return "NOT_FOUND"


def compute_static_compatibility(
    analysis: FeedAnalysis,
    static: Optional[StaticGTFSIndex],
    static_error: Optional[str],
) -> dict[str, Any]:
    if static is None:
        classification = (
            "LOCAL_STATIC_COMPATIBILITY_NOT_TESTED"
            if static_error is None
            else "INCONCLUSIVE"
        )
        return {
            "static_compatibility_classification": classification,
            "static_compatibility_measurements": {},
            "static_gtfs_path": None,
            "static_gtfs_error": static_error,
        }

    relevant_indices: set[int] = set()
    for indices in analysis.station_trip_record_indices.values():
        relevant_indices.update(indices)
    relevant_trip_records = [analysis.trip_records[index] for index in sorted(relevant_indices)]
    relevant_stops = [
        (station_key, trip_record, stop_record)
        for station_key, trip_record, stop_record in analysis.station_stop_records
        if trip_record.entity_index in {record.entity_index for record in relevant_trip_records}
    ]

    trip_id_exact = 0
    trip_id_not_found = 0
    trip_id_missing = 0
    route_id_exact = 0
    route_id_not_found = 0
    route_id_missing = 0
    stop_id_exact = 0
    stop_id_parent_only = 0
    stop_id_not_found = 0
    stop_id_missing = 0
    sequence_consistent = 0
    sequence_inconsistent = 0
    sequence_not_testable = 0

    for record in relevant_trip_records:
        trip_id = record.trip.get("trip_id")
        if not trip_id:
            trip_id_missing += 1
        elif trip_id in static.trips:
            trip_id_exact += 1
        else:
            trip_id_not_found += 1
        route_id = record.trip.get("route_id")
        if not route_id:
            route_id_missing += 1
        elif route_id in static.routes:
            route_id_exact += 1
        else:
            route_id_not_found += 1

    for station_key, _record, stop in relevant_stops:
        station = STATIONS[station_key]
        match_kind = static_stop_match_kind(static, station, stop.stop_id)
        if match_kind == "EXACT":
            stop_id_exact += 1
        elif match_kind == "PARENT_PREFIX_ONLY":
            stop_id_parent_only += 1
        elif match_kind == "NOT_FOUND":
            stop_id_not_found += 1
        else:
            stop_id_missing += 1

        trip_id = _record.trip.get("trip_id")
        if (
            not trip_id
            or trip_id not in static.trips
            or not stop.stop_id
            or stop.stop_id not in static.stops
            or stop.stop_sequence is None
        ):
            sequence_not_testable += 1
            continue
        candidate_rows = [
            row
            for row in static.stop_times_by_trip.get(trip_id, [])
            if row.get("stop_id", "") == stop.stop_id
        ]
        matching_sequence = False
        for row in candidate_rows:
            try:
                if int(row.get("stop_sequence", "")) == stop.stop_sequence:
                    matching_sequence = True
                    break
            except (TypeError, ValueError):
                continue
        if matching_sequence:
            sequence_consistent += 1
        else:
            sequence_inconsistent += 1

    relevant_observations = len(relevant_trip_records) + len(relevant_stops)
    exact_identity_evidence = trip_id_exact + route_id_exact + stop_id_exact
    direct_identity_problem = trip_id_not_found + route_id_not_found + stop_id_not_found + sequence_inconsistent
    all_supplied_identifiers_exact = (
        relevant_trip_records
        and trip_id_not_found == 0
        and route_id_not_found == 0
        and stop_id_not_found == 0
        and stop_id_parent_only == 0
        and sequence_inconsistent == 0
    )
    if relevant_observations == 0:
        classification = "INCONCLUSIVE"
    elif all_supplied_identifiers_exact:
        classification = "STATIC_IDENTITY_HIGH_COMPATIBILITY"
    elif exact_identity_evidence > 0:
        classification = "STATIC_IDENTITY_PARTIAL_COMPATIBILITY"
    elif direct_identity_problem > 0:
        classification = "STATIC_IDENTITY_LOW_COMPATIBILITY"
    else:
        classification = "INCONCLUSIVE"

    measurements = {
        "relevant_trip_update_count": len(relevant_trip_records),
        "relevant_stop_time_update_count": len(relevant_stops),
        "trip_id_exact_match_count": trip_id_exact,
        "trip_id_not_found_count": trip_id_not_found,
        "trip_id_missing_count": trip_id_missing,
        "route_id_exact_match_count": route_id_exact,
        "route_id_not_found_count": route_id_not_found,
        "route_id_missing_count": route_id_missing,
        "stop_id_exact_match_count": stop_id_exact,
        "stop_id_parent_prefix_only_count": stop_id_parent_only,
        "stop_id_not_found_count": stop_id_not_found,
        "stop_id_missing_count": stop_id_missing,
        "stop_sequence_consistent_count": sequence_consistent,
        "stop_sequence_inconsistent_count": sequence_inconsistent,
        "stop_sequence_not_testable_count": sequence_not_testable,
        "classification_basis": (
            "all supplied identifiers exact and all testable stop sequences consistent"
            if classification == "STATIC_IDENTITY_HIGH_COMPATIBILITY"
            else "measurements show partial, low, or inconclusive identity evidence"
        ),
    }
    return {
        "static_compatibility_classification": classification,
        "static_compatibility_measurements": measurements,
        "static_gtfs_path": static.path,
        "static_gtfs_error": static_error,
    }


def select_target(
    analysis: FeedAnalysis,
    station: StationConfig,
    discovery_timestamp: datetime,
) -> Optional[Target]:
    discovery_seconds = discovery_timestamp.timestamp()
    arrival_candidates: list[tuple[float, TripUpdateRecord, StopTimeRecord, str]] = []
    departure_candidates: list[tuple[float, TripUpdateRecord, StopTimeRecord, str]] = []
    for record in analysis.trip_records:
        if not record.trip.get("trip_id"):
            continue
        if record.trip.get("schedule_relationship") == "CANCELED":
            continue
        for stop in record.stops:
            if station_match_type(stop.stop_id, station) is None:
                continue
            if stop.schedule_relationship in {"SKIPPED", "NO_DATA"}:
                continue
            for context, event, candidates in (
                ("ARRIVAL", stop.arrival, arrival_candidates),
                ("DEPARTURE", stop.departure, departure_candidates),
            ):
                if event.time_seconds is None:
                    continue
                delta = event.time_seconds - discovery_seconds
                if 8 * 60 <= delta <= 25 * 60:
                    candidates.append((delta, record, stop, context))

    candidates = arrival_candidates or departure_candidates
    if not candidates:
        return None

    def candidate_sort_key(item: tuple[float, TripUpdateRecord, StopTimeRecord, str]) -> tuple[Any, ...]:
        delta, record, stop, context = item
        return (
            0 if record.timestamp_seconds is not None else 1,
            round(delta, 3),
            record.entity_id or "",
            record.trip.get("trip_id") or "",
            record.trip.get("start_date") or "",
            stop.stop_id or "",
            stop.stop_sequence if stop.stop_sequence is not None else 2**31,
            context,
        )

    _delta, record, stop, context = sorted(candidates, key=candidate_sort_key)[0]
    identity = {
        "entity_id": record.entity_id,
        "trip_id": record.trip.get("trip_id"),
        "route_id": record.trip.get("route_id"),
        "direction_id": record.trip.get("direction_id"),
        "start_date": record.trip.get("start_date"),
        "start_time": record.trip.get("start_time"),
        "trip_schedule_relationship": record.trip.get("schedule_relationship"),
        "stop_id": stop.stop_id,
        "stop_sequence": stop.stop_sequence,
        "event_context": context,
    }
    event_seconds = stop.arrival.time_seconds if context == "ARRIVAL" else stop.departure.time_seconds
    if event_seconds is None:
        return None
    return Target(
        station=station,
        identity=identity,
        event_context=context,
        initial_event_seconds=event_seconds,
    )


def trip_identity_matches(record: TripUpdateRecord, target: Target) -> bool:
    for field_name in ("trip_id", "route_id", "direction_id", "start_date", "start_time"):
        expected = target.identity.get(field_name)
        if expected is not None and record.trip.get(field_name) != expected:
            return False
    return target.identity.get("trip_id") is not None


def resolve_trip_match(
    records: list[TripUpdateRecord],
    target: Target,
) -> tuple[str, Optional[TripUpdateRecord], int]:
    potential = [record for record in records if trip_identity_matches(record, target)]
    expected_entity_id = target.identity.get("entity_id")
    if expected_entity_id is not None:
        exact = [record for record in potential if record.entity_id == expected_entity_id]
        if len(exact) == 1:
            return "TARGET_FOUND", exact[0], len(potential)
        if len(exact) > 1 or potential:
            return "TARGET_AMBIGUOUS", None, len(potential)
        return "TARGET_NOT_FOUND", None, 0
    if len(potential) == 1:
        return "TARGET_FOUND", potential[0], 1
    if len(potential) > 1:
        return "TARGET_AMBIGUOUS", None, len(potential)
    return "TARGET_NOT_FOUND", None, 0


def resolve_target(
    analysis: FeedAnalysis,
    target: Target,
) -> TargetEvaluation:
    trip_status, trip_record, potential_trip_count = resolve_trip_match(analysis.trip_records, target)
    if trip_status != "TARGET_FOUND" or trip_record is None:
        return TargetEvaluation(
            target_match_status=trip_status,
            trip_match_status=trip_status,
            trip_record=None,
            stop_record=None,
            potential_trip_match_count=potential_trip_count,
            potential_stop_match_count=0,
        )

    stop_candidates: list[StopTimeRecord] = []
    for stop in trip_record.stops:
        if stop.stop_id != target.identity.get("stop_id"):
            continue
        if stop.schedule_relationship not in {"SKIPPED", "NO_DATA", "CANCELED"}:
            if target.event_context == "ARRIVAL" and stop.arrival.time_seconds is None and get_message(stop.message, "arrival") is None:
                continue
            if target.event_context == "DEPARTURE" and stop.departure.time_seconds is None and get_message(stop.message, "departure") is None:
                continue
        target_sequence = target.identity.get("stop_sequence")
        if target_sequence is not None and stop.stop_sequence != target_sequence:
            continue
        stop_candidates.append(stop)
    if len(stop_candidates) == 1:
        return TargetEvaluation(
            target_match_status="TARGET_FOUND",
            trip_match_status=trip_status,
            trip_record=trip_record,
            stop_record=stop_candidates[0],
            potential_trip_match_count=potential_trip_count,
            potential_stop_match_count=1,
        )
    if len(stop_candidates) > 1:
        return TargetEvaluation(
            target_match_status="TARGET_AMBIGUOUS",
            trip_match_status=trip_status,
            trip_record=trip_record,
            stop_record=None,
            potential_trip_match_count=potential_trip_count,
            potential_stop_match_count=len(stop_candidates),
        )
    return TargetEvaluation(
        target_match_status="TARGET_NOT_FOUND",
        trip_match_status=trip_status,
        trip_record=trip_record,
        stop_record=None,
        potential_trip_match_count=potential_trip_count,
        potential_stop_match_count=0,
    )


def current_event(stop: Optional[StopTimeRecord], context: str) -> EventRecord:
    if stop is None:
        return EventRecord(None, None, None)
    return stop.arrival if context == "ARRIVAL" else stop.departure


def matched_identity(target: Target, evaluation: TargetEvaluation) -> Optional[dict[str, Any]]:
    if evaluation.trip_record is None:
        return None
    record = evaluation.trip_record
    stop = evaluation.stop_record
    return {
        "entity_id": record.entity_id,
        "trip_id": record.trip.get("trip_id"),
        "route_id": record.trip.get("route_id"),
        "direction_id": record.trip.get("direction_id"),
        "start_date": record.trip.get("start_date"),
        "start_time": record.trip.get("start_time"),
        "trip_schedule_relationship": record.trip.get("schedule_relationship"),
        "stop_id": stop.stop_id if stop is not None else target.identity.get("stop_id"),
        "stop_sequence": stop.stop_sequence if stop is not None else target.identity.get("stop_sequence"),
        "event_context": target.event_context,
    }


def static_scheduled_comparison(
    static: Optional[StaticGTFSIndex],
    target: Target,
) -> dict[str, Any]:
    if static is None:
        return {
            "status": "LOCAL_STATIC_COMPATIBILITY_NOT_TESTED",
            "trip_id": target.identity.get("trip_id"),
            "stop_id": target.identity.get("stop_id"),
            "event_context": target.event_context,
        }
    trip_id = target.identity.get("trip_id")
    stop_id = target.identity.get("stop_id")
    if not trip_id or trip_id not in static.trips:
        return {"status": "TRIP_ID_NOT_FOUND", "trip_id": trip_id, "stop_id": stop_id}
    if not stop_id or stop_id not in static.stops:
        station = target.station
        if station_match_type(stop_id, station) and has_static_parent_reference(static, station):
            status = "PARENT_PREFIX_ONLY_NO_EXACT_STOP_TIME_MATCH"
        else:
            status = "STOP_ID_NOT_FOUND"
        return {"status": status, "trip_id": trip_id, "stop_id": stop_id}
    rows = [
        row
        for row in static.stop_times_by_trip.get(trip_id, [])
        if row.get("stop_id", "") == stop_id
    ]
    target_sequence = target.identity.get("stop_sequence")
    if target_sequence is not None:
        sequence_rows = []
        for row in rows:
            try:
                if int(row.get("stop_sequence", "")) == target_sequence:
                    sequence_rows.append(row)
            except (TypeError, ValueError):
                continue
        rows = sequence_rows
    if len(rows) != 1:
        return {
            "status": "STATIC_STOP_TIME_AMBIGUOUS" if rows else "STATIC_STOP_TIME_NOT_FOUND",
            "trip_id": trip_id,
            "stop_id": stop_id,
            "stop_sequence": target_sequence,
        }
    row = rows[0]
    return {
        "status": "STATIC_STOP_TIME_EXACT_MATCH",
        "trip_id": trip_id,
        "stop_id": stop_id,
        "stop_sequence": row.get("stop_sequence") or None,
        "gtfs_local_arrival_time": row.get("arrival_time") or None,
        "gtfs_local_departure_time": row.get("departure_time") or None,
        "event_context": target.event_context,
        "note": "GTFS stop_times values are reported as static feed local service times; no actual-time meaning is inferred.",
    }


def feed_header_fields(analysis: FeedAnalysis) -> dict[str, Any]:
    return {
        "feed_header_timestamp_utc": analysis.summary.get("feed_header_timestamp_utc"),
        "feed_age_seconds": analysis.summary.get("feed_age_seconds_at_download"),
    }


def make_observation_snapshot(
    observation_index: int,
    observation_label: str,
    planned_timestamp: datetime,
    fetch_result: FetchResult,
    analysis: Optional[FeedAnalysis],
    target: Optional[Target],
    evaluation: Optional[TargetEvaluation],
    static_comparison: Optional[dict[str, Any]],
    observation_status: str = "OBSERVED",
    error_message: Optional[str] = None,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "probe_name": PROBE_NAME,
        "mode": "lifecycle",
        "observation_index": observation_index,
        "observation_label": observation_label,
        "observation_status": observation_status,
        "planned_observation_time_utc": iso_utc(planned_timestamp),
        "request_timestamp_utc": iso_utc(fetch_result.request_timestamp) if observation_status == "OBSERVED" else None,
        "http_status": fetch_result.http_status if observation_status == "OBSERVED" else None,
        "elapsed_ms": fetch_result.elapsed_ms if observation_status == "OBSERVED" else None,
        "response_bytes": fetch_result.response_bytes if observation_status == "OBSERVED" else None,
        "content_length": fetch_result.content_length if observation_status == "OBSERVED" else None,
        "content_length_matches": fetch_result.content_length_matches if observation_status == "OBSERVED" else None,
        "protobuf_parse_success": analysis is not None,
        "feed_header_timestamp_utc": None,
        "feed_age_seconds": None,
        "target_match_status": evaluation.target_match_status if evaluation else None,
        "target_identity": target.identity if target else None,
        "matched_target_identity": matched_identity(target, evaluation) if target and evaluation else None,
        "event_context": target.event_context if target else None,
        "scheduled_comparison": static_comparison,
        "gtfs_rt_arrival_time_utc": None,
        "gtfs_rt_arrival_delay_seconds": None,
        "gtfs_rt_arrival_uncertainty_seconds": None,
        "gtfs_rt_departure_time_utc": None,
        "gtfs_rt_departure_delay_seconds": None,
        "gtfs_rt_departure_uncertainty_seconds": None,
        "reported_event_time": None,
        "reported_event_time_context": target.event_context if target else None,
        "reported_event_time_unix_seconds": None,
        "stop_schedule_relationship": None,
        "assigned_stop_id": None,
        "trip_schedule_relationship": None,
        "trip_update_timestamp_utc": None,
        "trip_update_age_seconds": None,
        "target_stop_is_past_relative_to_request_time": None,
        "target_event_time_delta_seconds_from_initial": None,
        "target_post_event_relative_to_initial": (
            fetch_result.request_timestamp >= datetime.fromtimestamp(target.initial_event_seconds, tz=timezone.utc)
            if target and observation_status == "OBSERVED"
            else None
        ),
        "target_trip_match_status": evaluation.trip_match_status if evaluation else None,
        "potential_trip_match_count": evaluation.potential_trip_match_count if evaluation else None,
        "potential_stop_match_count": evaluation.potential_stop_match_count if evaluation else None,
        "observation_error": error_message or fetch_result.error_message,
    }
    if analysis is not None:
        snapshot.update(feed_header_fields(analysis))
    if evaluation is None or evaluation.trip_record is None:
        return snapshot

    trip_record = evaluation.trip_record
    snapshot["trip_schedule_relationship"] = trip_record.trip.get("schedule_relationship")
    if trip_record.timestamp_seconds is not None:
        snapshot["trip_update_timestamp_utc"] = epoch_to_iso(trip_record.timestamp_seconds)
        snapshot["trip_update_age_seconds"] = round_number(
            (fetch_result.request_timestamp - datetime.fromtimestamp(trip_record.timestamp_seconds, tz=timezone.utc)).total_seconds()
        )
    if evaluation.stop_record is None:
        return snapshot

    stop_record = evaluation.stop_record
    snapshot["stop_schedule_relationship"] = stop_record.schedule_relationship
    snapshot["assigned_stop_id"] = stop_record.assigned_stop_id
    snapshot["gtfs_rt_arrival_time_utc"] = epoch_to_iso(stop_record.arrival.time_seconds)
    snapshot["gtfs_rt_arrival_delay_seconds"] = stop_record.arrival.delay_seconds
    snapshot["gtfs_rt_arrival_uncertainty_seconds"] = stop_record.arrival.uncertainty_seconds
    snapshot["gtfs_rt_departure_time_utc"] = epoch_to_iso(stop_record.departure.time_seconds)
    snapshot["gtfs_rt_departure_delay_seconds"] = stop_record.departure.delay_seconds
    snapshot["gtfs_rt_departure_uncertainty_seconds"] = stop_record.departure.uncertainty_seconds
    event = current_event(stop_record, target.event_context if target else "ARRIVAL")
    snapshot["reported_event_time"] = epoch_to_iso(event.time_seconds)
    snapshot["reported_event_time_unix_seconds"] = event.time_seconds
    if event.time_seconds is not None:
        event_datetime = datetime.fromtimestamp(event.time_seconds, tz=timezone.utc)
        snapshot["target_stop_is_past_relative_to_request_time"] = (
            event_datetime <= fetch_result.request_timestamp
        )
        if target is not None:
            snapshot["target_event_time_delta_seconds_from_initial"] = event.time_seconds - target.initial_event_seconds
    return snapshot


class LifecycleTracker:
    def __init__(self, target: Target) -> None:
        self.target = target
        self.target_found_observation_count = 0
        self.target_not_found_observation_count = 0
        self.target_ambiguous_observation_count = 0
        self.reported_times: list[tuple[int, int, datetime]] = []
        self.reported_time_changes = 0
        self.first_post_event_observation: Optional[int] = None
        self.first_observation_where_event_time_was_past: Optional[int] = None
        self.last_target_visible_observation: Optional[int] = None
        self.first_post_event_reported_count = 0
        self.post_event_visible = False
        self.time_changed_after_event = False
        self.target_disappeared_post_event = False
        self.found_before_initial_event = False
        self.trip_timestamps: list[int] = []
        self.trip_timestamp_advanced = False
        self.trip_relationships: list[str] = []
        self.cancellation_observed = False
        self.skipped_stop_observed = False
        self.no_data_observed = False

    def observe(
        self,
        observation_index: int,
        request_timestamp: datetime,
        evaluation: Optional[TargetEvaluation],
    ) -> None:
        if evaluation is None:
            return
        post_event = request_timestamp >= datetime.fromtimestamp(
            self.target.initial_event_seconds,
            tz=timezone.utc,
        )
        if post_event and self.first_post_event_observation is None:
            self.first_post_event_observation = observation_index

        if evaluation.target_match_status == "TARGET_FOUND":
            self.target_found_observation_count += 1
            self.last_target_visible_observation = observation_index
            if not post_event:
                self.found_before_initial_event = True
            else:
                self.post_event_visible = True
            if evaluation.stop_record is not None:
                if evaluation.stop_record.schedule_relationship == "SKIPPED":
                    self.skipped_stop_observed = True
                if evaluation.stop_record.schedule_relationship == "NO_DATA":
                    self.no_data_observed = True
                event = current_event(evaluation.stop_record, self.target.event_context)
                if event.time_seconds is not None:
                    if (
                        event.time_seconds <= request_timestamp.timestamp()
                        and self.first_observation_where_event_time_was_past is None
                    ):
                        self.first_observation_where_event_time_was_past = observation_index
                    if post_event:
                        self.first_post_event_reported_count += 1
                        if self.reported_times and self.reported_times[-1][1] != event.time_seconds:
                            self.time_changed_after_event = True
                    if self.reported_times and self.reported_times[-1][1] != event.time_seconds:
                        self.reported_time_changes += 1
                    self.reported_times.append((observation_index, event.time_seconds, request_timestamp))
        elif evaluation.target_match_status == "TARGET_NOT_FOUND":
            self.target_not_found_observation_count += 1
            if post_event and self.found_before_initial_event:
                self.target_disappeared_post_event = True
        elif evaluation.target_match_status == "TARGET_AMBIGUOUS":
            self.target_ambiguous_observation_count += 1

        trip_record = evaluation.trip_record
        if trip_record is not None:
            relationship = trip_record.trip.get("schedule_relationship")
            if relationship is not None:
                self.trip_relationships.append(relationship)
                if relationship == "CANCELED":
                    self.cancellation_observed = True
            if trip_record.timestamp_seconds is not None:
                if self.trip_timestamps and trip_record.timestamp_seconds > self.trip_timestamps[-1]:
                    self.trip_timestamp_advanced = True
                self.trip_timestamps.append(trip_record.timestamp_seconds)

    def summary(self) -> dict[str, Any]:
        reported_seconds = [item[1] for item in self.reported_times]
        distinct_reported_seconds = sorted(set(reported_seconds))
        schedule_relationship_changed = len(set(self.trip_relationships)) > 1
        post_event_reported_times = [
            seconds
            for _index, seconds, request_timestamp in self.reported_times
            if request_timestamp >= datetime.fromtimestamp(self.target.initial_event_seconds, tz=timezone.utc)
        ]
        last_time_changed_after_event = self.time_changed_after_event
        retained_post_event_time = self.post_event_visible and bool(post_event_reported_times)
        return {
            "first_reported_event_time": epoch_to_iso(reported_seconds[0]) if reported_seconds else None,
            "last_reported_event_time": epoch_to_iso(reported_seconds[-1]) if reported_seconds else None,
            "minimum_reported_event_time": epoch_to_iso(min(reported_seconds)) if reported_seconds else None,
            "maximum_reported_event_time": epoch_to_iso(max(reported_seconds)) if reported_seconds else None,
            "distinct_reported_event_time_count": len(distinct_reported_seconds),
            "reported_event_time_change_count": self.reported_time_changes,
            "first_post_event_observation": self.first_post_event_observation,
            "first_observation_where_event_time_was_past": self.first_observation_where_event_time_was_past,
            "last_target_visible_observation": self.last_target_visible_observation,
            "target_found_observation_count": self.target_found_observation_count,
            "target_not_found_observation_count": self.target_not_found_observation_count,
            "target_ambiguous_observation_count": self.target_ambiguous_observation_count,
            "target_remained_visible_after_initial_event": self.post_event_visible,
            "target_time_reported_after_initial_event": retained_post_event_time,
            "target_time_continued_changing_after_initial_event": last_time_changed_after_event,
            "target_disappeared_after_initial_event": self.target_disappeared_post_event,
            "trip_update_timestamp_first": epoch_to_iso(self.trip_timestamps[0]) if self.trip_timestamps else None,
            "trip_update_timestamp_last": epoch_to_iso(self.trip_timestamps[-1]) if self.trip_timestamps else None,
            "trip_update_timestamp_advanced": self.trip_timestamp_advanced,
            "trip_schedule_relationship_changed": schedule_relationship_changed,
            "trip_schedule_relationship_history": self.trip_relationships,
            "cancellation_observed": self.cancellation_observed,
            "skipped_stop_observed": self.skipped_stop_observed,
            "no_data_observed": self.no_data_observed,
        }


def lifecycle_classification(
    target: Optional[Target],
    tracker: Optional[LifecycleTracker],
    discovery_failed: bool,
) -> str:
    if target is None:
        return "HTTP_OR_PARSE_FAILURE" if discovery_failed else "NO_SUITABLE_TARGET"
    if tracker is None:
        return "INCONCLUSIVE"
    if tracker.target_ambiguous_observation_count:
        return "TARGET_MATCH_BECAME_AMBIGUOUS"
    if tracker.cancellation_observed:
        return "TARGET_BECAME_CANCELED"
    if tracker.skipped_stop_observed:
        return "TARGET_STOP_BECAME_SKIPPED"
    if tracker.no_data_observed:
        return "TARGET_STOP_BECAME_NO_DATA"
    if tracker.target_disappeared_post_event:
        return "TARGET_DISAPPEARED_POST_EVENT"
    post_event_reported_times = [
        seconds
        for _index, seconds, request_timestamp in tracker.reported_times
        if request_timestamp >= datetime.fromtimestamp(tracker.target.initial_event_seconds, tz=timezone.utc)
    ]
    if tracker.post_event_visible and post_event_reported_times:
        if tracker.time_changed_after_event:
            return "TARGET_RETAINED_POST_EVENT_TIME_CHANGED"
        return "TARGET_RETAINED_POST_EVENT_TIME_UNCHANGED"
    return "INCONCLUSIVE"


def collector_implication(
    classification: str,
    actual_semantic_classification: str,
    static_classification: str,
) -> str:
    if classification == "TARGET_BECAME_CANCELED":
        implication = "The tested GTFS-RT feed exposed explicit cancellation evidence for the selected trip."
    elif classification == "TARGET_STOP_BECAME_SKIPPED":
        implication = "The tested GTFS-RT feed exposed explicit skipped-stop evidence for the selected stop."
    elif classification == "TARGET_STOP_BECAME_NO_DATA":
        implication = "The tested GTFS-RT feed exposed explicit NO_DATA evidence for the selected stop; this is not cancellation evidence."
    elif classification == "TARGET_RETAINED_POST_EVENT_TIME_CHANGED":
        implication = "GTFS-RT provided repeated reported event-time observations that changed after the initial reference; this is useful prediction-evolution evidence."
    elif classification == "TARGET_RETAINED_POST_EVENT_TIME_UNCHANGED":
        implication = "GTFS-RT retained the reported event time after the initial reference without an observed change; stability does not confirm actual arrival."
    elif classification == "TARGET_DISAPPEARED_POST_EVENT":
        implication = "The selected StopTimeUpdate was no longer matched after the initial reference; disappearance is lifecycle evidence, not proof that the stop was served or missed."
    elif classification == "NO_SUITABLE_TARGET":
        implication = "No suitable station target was found in the discovery snapshot, so this run does not establish lifecycle evidence."
    elif classification == "HTTP_OR_PARSE_FAILURE":
        implication = "The discovery request or protobuf parse failed, so this run does not establish a collector implication."
    else:
        implication = "The lifecycle result is inconclusive for the selected target."

    if actual_semantic_classification != "EXPLICIT_ACTUAL_SEMANTIC_OBSERVED":
        implication += " Standard StopTimeEvent.time semantics do not establish confirmed actual arrival."
    if static_classification == "LOCAL_STATIC_COMPATIBILITY_NOT_TESTED":
        implication += " Static identity compatibility with the project's VRS/go.Rheinland GTFS was not tested because no local static feed was supplied."
    return implication


def json_line(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def inventory_classification(fetch_result: FetchResult, analysis: Optional[FeedAnalysis], parse_error: Optional[str]) -> str:
    if fetch_result.http_status != 200:
        return "GTFS_RT_HTTP_FAILED"
    if analysis is None or parse_error is not None:
        return "GTFS_RT_PARSE_FAILED"
    if fetch_result.content_length_matches is False:
        return "INCONCLUSIVE"
    if analysis.summary.get("feed_header_validation_status") != "VALID":
        return "INCONCLUSIVE"
    if analysis.summary.get("trip_update_entity_count", 0) == 0:
        return "GTFS_RT_REACHABLE_NO_TRIP_UPDATES"
    if analysis.summary.get("cologne_trip_update_count", 0) > 0:
        return "GTFS_RT_REACHABLE_WITH_COLOGNE_EVIDENCE"
    return "GTFS_RT_REACHABLE_NO_COLOGNE_EVIDENCE_FOUND"


def inventory(args: argparse.Namespace, bindings: Any, semantic_surface: dict[str, Any]) -> int:
    raw_directory = create_raw_temp_directory()
    fetch_result = fetch_feed(raw_directory, 1)
    feed, parse_error = parse_feed(fetch_result.body, bindings) if fetch_result.http_status == 200 else (None, None)
    analysis = analyze_feed(feed, fetch_result.request_timestamp) if feed is not None else None
    static_result: dict[str, Any]
    if analysis is None:
        static_result = {
            "static_compatibility_classification": (
                "LOCAL_STATIC_COMPATIBILITY_NOT_TESTED"
                if args.static_gtfs is None
                else "INCONCLUSIVE"
            ),
            "static_compatibility_measurements": {},
            "static_gtfs_path": str(Path(args.static_gtfs)) if args.static_gtfs else None,
            "static_gtfs_error": "feed unavailable for compatibility test",
        }
    else:
        static, static_error = load_static_gtfs(args.static_gtfs, relevant_static_trip_ids(analysis))
        static_result = compute_static_compatibility(analysis, static, static_error)

    record: dict[str, Any] = {
        "probe_name": PROBE_NAME,
        "mode": "inventory",
        "source_url": SOURCE_URL,
        "request_timestamp_utc": iso_utc(fetch_result.request_timestamp),
        "http_status": fetch_result.http_status,
        "elapsed_ms": fetch_result.elapsed_ms,
        "response_bytes": fetch_result.response_bytes,
        "content_length": fetch_result.content_length,
        "content_length_matches": fetch_result.content_length_matches,
        "protobuf_parse_success": analysis is not None and parse_error is None,
        "protobuf_parse_error": parse_error,
        "raw_temp_directory": str(raw_directory) if raw_directory else None,
        "inventory_classification": inventory_classification(fetch_result, analysis, parse_error),
        "gtfs_realtime_version": None,
        "feed_incrementality": None,
        "feed_header_timestamp_utc": None,
        "feed_age_seconds_at_download": None,
        "entity_count_total": 0,
        "trip_update_entity_count": 0,
        "vehicle_entity_count": 0,
        "alert_entity_count": 0,
        "other_entity_count": 0,
        "other_entity_type_counts": {},
        "trip_updates_with_timestamp_count": 0,
        "trip_updates_without_timestamp_count": 0,
        "stop_time_update_count_total": 0,
        "stop_time_updates_with_stop_id_count": 0,
        "stop_time_updates_with_stop_sequence_count": 0,
        "arrival_time_count": 0,
        "arrival_delay_count": 0,
        "arrival_uncertainty_count": 0,
        "departure_time_count": 0,
        "departure_delay_count": 0,
        "departure_uncertainty_count": 0,
        "trip_schedule_relationship_counts": {},
        "stop_schedule_relationship_counts": {},
        "canceled_trip_count": 0,
        "skipped_stop_update_count": 0,
        "no_data_stop_update_count": 0,
        "assigned_stop_id_count": 0,
        "unique_trip_id_count": 0,
        "unique_route_id_count": 0,
        "unique_stop_id_count": 0,
        "hbf_stop_id_matches": station_match_summary(STATIONS["hbf"], set(), set()),
        "neumarkt_stop_id_matches": station_match_summary(STATIONS["neumarkt"], set(), set()),
        "hbf_trip_update_count": 0,
        "neumarkt_trip_update_count": 0,
        "cologne_trip_update_count": 0,
        "cologne_evidence_types": [],
        "alert_count": 0,
        "alert_informed_entity_count": 0,
        "alerts_referencing_trip_ids": 0,
        "alerts_referencing_route_ids": 0,
        "alerts_referencing_stop_ids": 0,
        "alert_station_stop_id_examples": [],
        **semantic_surface,
        **static_result,
    }
    if analysis is not None:
        record.update(analysis.summary)
    json_line(record)
    return 0 if fetch_result.http_status == 200 and analysis is not None and parse_error is None else 1


def make_skipped_snapshot(
    observation_index: int,
    label: str,
    planned_timestamp: datetime,
    target: Target,
    static_comparison: dict[str, Any],
) -> dict[str, Any]:
    fetch_result = FetchResult(
        request_timestamp=planned_timestamp,
        http_status=None,
        elapsed_ms=0,
        response_bytes=0,
        body=None,
        content_type=None,
        error_kind="SKIPPED_ALREADY_PAST",
        error_message="checkpoint was already in the past when scheduling began",
    )
    return make_observation_snapshot(
        observation_index=observation_index,
        observation_label=label,
        planned_timestamp=planned_timestamp,
        fetch_result=fetch_result,
        analysis=None,
        target=target,
        evaluation=None,
        static_comparison=static_comparison,
        observation_status="SKIPPED_ALREADY_PAST",
        error_message="checkpoint was already in the past when scheduling began",
    )


def wait_until(target_time: datetime) -> None:
    while True:
        remaining = (target_time - utc_now()).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 30.0))


def lifecycle(args: argparse.Namespace, bindings: Any, semantic_surface: dict[str, Any]) -> int:
    station = STATIONS[args.station]
    raw_directory = create_raw_temp_directory()
    total_http_attempts = 0
    successful_http_200_count = 0
    parse_success_count = 0
    discovery_failed = False
    static_result: dict[str, Any] = {
        "static_compatibility_classification": (
            "LOCAL_STATIC_COMPATIBILITY_NOT_TESTED"
            if args.static_gtfs is None
            else "INCONCLUSIVE"
        ),
        "static_compatibility_measurements": {},
        "static_gtfs_path": str(Path(args.static_gtfs)) if args.static_gtfs else None,
        "static_gtfs_error": None,
    }

    total_http_attempts += 1
    discovery_fetch = fetch_feed(raw_directory, total_http_attempts)
    if discovery_fetch.http_status == 200:
        successful_http_200_count += 1
    discovery_feed, discovery_parse_error = parse_feed(discovery_fetch.body, bindings) if discovery_fetch.http_status == 200 else (None, None)
    if discovery_feed is not None and discovery_parse_error is None:
        parse_success_count += 1
        discovery_analysis = analyze_feed(discovery_feed, discovery_fetch.request_timestamp)
        static, static_error = load_static_gtfs(
            args.static_gtfs,
            relevant_static_trip_ids(discovery_analysis),
        )
        static_result = compute_static_compatibility(discovery_analysis, static, static_error)
    else:
        discovery_analysis = None
        discovery_failed = True
        static_result["static_gtfs_error"] = "feed unavailable for compatibility test"

    target = (
        select_target(discovery_analysis, station, discovery_fetch.request_timestamp)
        if discovery_analysis is not None
        else None
    )
    if target is not None:
        static_comparison = static_scheduled_comparison(static, target)
    else:
        static_comparison = {
            "status": static_result.get("static_compatibility_classification"),
            "event_context": None,
        }

    discovery_evaluation = (
        resolve_target(discovery_analysis, target)
        if discovery_analysis is not None and target is not None
        else None
    )
    discovery_snapshot = make_observation_snapshot(
        observation_index=0,
        observation_label="DISCOVERY",
        planned_timestamp=discovery_fetch.request_timestamp,
        fetch_result=discovery_fetch,
        analysis=discovery_analysis,
        target=target,
        evaluation=discovery_evaluation,
        static_comparison=static_comparison,
        error_message=discovery_parse_error,
    )
    json_line(discovery_snapshot)

    tracker: Optional[LifecycleTracker] = LifecycleTracker(target) if target is not None else None
    if tracker is not None and discovery_evaluation is not None:
        tracker.observe(0, discovery_fetch.request_timestamp, discovery_evaluation)

    if target is not None and tracker is not None:
        checkpoints = [
            ("T_MINUS_5_MINUTES", -5),
            ("T_MINUS_1_MINUTE", -1),
            ("T_PLUS_1_MINUTE", 1),
            ("T_PLUS_5_MINUTES", 5),
            ("T_PLUS_15_MINUTES", 15),
            ("T_PLUS_30_MINUTES", 30),
        ]
        initial_event = datetime.fromtimestamp(target.initial_event_seconds, tz=timezone.utc)
        for observation_index, (label, offset_minutes) in enumerate(checkpoints, start=1):
            planned_timestamp = initial_event + timedelta(minutes=offset_minutes)
            if planned_timestamp < utc_now():
                skipped_snapshot = make_skipped_snapshot(
                    observation_index,
                    label,
                    planned_timestamp,
                    target,
                    static_comparison,
                )
                json_line(skipped_snapshot)
                continue

            wait_until(planned_timestamp)
            total_http_attempts += 1
            fetch_result = fetch_feed(raw_directory, total_http_attempts)
            if fetch_result.http_status == 200:
                successful_http_200_count += 1
            feed, parse_error = parse_feed(fetch_result.body, bindings) if fetch_result.http_status == 200 else (None, None)
            if feed is not None and parse_error is None:
                parse_success_count += 1
                analysis = analyze_feed(feed, fetch_result.request_timestamp)
                evaluation = resolve_target(analysis, target)
            else:
                analysis = None
                evaluation = None
            snapshot = make_observation_snapshot(
                observation_index=observation_index,
                observation_label=label,
                planned_timestamp=planned_timestamp,
                fetch_result=fetch_result,
                analysis=analysis,
                target=target,
                evaluation=evaluation,
                static_comparison=static_comparison,
                error_message=parse_error,
            )
            json_line(snapshot)
            if analysis is not None and evaluation is not None:
                tracker.observe(observation_index, fetch_result.request_timestamp, evaluation)

    tracker_summary = tracker.summary() if tracker is not None else {
        "first_reported_event_time": None,
        "last_reported_event_time": None,
        "minimum_reported_event_time": None,
        "maximum_reported_event_time": None,
        "distinct_reported_event_time_count": 0,
        "reported_event_time_change_count": 0,
        "first_post_event_observation": None,
        "first_observation_where_event_time_was_past": None,
        "last_target_visible_observation": None,
        "target_found_observation_count": 0,
        "target_not_found_observation_count": 0,
        "target_ambiguous_observation_count": 0,
        "target_remained_visible_after_initial_event": False,
        "target_time_reported_after_initial_event": False,
        "target_time_continued_changing_after_initial_event": False,
        "target_disappeared_after_initial_event": False,
        "trip_update_timestamp_first": None,
        "trip_update_timestamp_last": None,
        "trip_update_timestamp_advanced": False,
        "trip_schedule_relationship_changed": False,
        "trip_schedule_relationship_history": [],
        "cancellation_observed": False,
        "skipped_stop_observed": False,
        "no_data_observed": False,
    }
    actual_semantic_classification = semantic_surface["actual_time_semantic_classification"]
    static_classification = static_result["static_compatibility_classification"]
    classification = lifecycle_classification(target, tracker, discovery_failed)
    final_summary = {
        "probe_name": PROBE_NAME,
        "mode": "lifecycle",
        "station": station.key,
        "station_display_name": station.display_name,
        "station_prefix": station.stop_prefix,
        "source_url": SOURCE_URL,
        "total_http_attempts": total_http_attempts,
        "successful_http_200_count": successful_http_200_count,
        "parse_success_count": parse_success_count,
        "retries": 0,
        "selected_target_identity": target.identity if target else None,
        "initial_event_time": epoch_to_iso(target.initial_event_seconds) if target else None,
        "initial_event_context": target.event_context if target else None,
        **tracker_summary,
        "lifecycle_classification": classification,
        "actual_time_semantic_classification": actual_semantic_classification,
        "static_compatibility_classification": static_classification,
        "static_compatibility_measurements": static_result.get("static_compatibility_measurements", {}),
        "static_gtfs_path": static_result.get("static_gtfs_path"),
        "static_gtfs_error": static_result.get("static_gtfs_error"),
        "raw_temp_directory": str(raw_directory) if raw_directory else None,
        "collector_implication": collector_implication(
            classification,
            actual_semantic_classification,
            static_classification,
        ),
        **semantic_surface,
    }
    json_line(final_summary)
    return 0 if not discovery_failed else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Host-run-only, bounded GTFS-Realtime feasibility audit."
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)

    inventory_parser = subparsers.add_parser(
        "inventory",
        help="make exactly one request and emit a feed/entity inventory",
    )
    inventory_parser.add_argument(
        "--static-gtfs",
        metavar="/absolute/path/to/gtfs.zip",
        help="optional local static GTFS ZIP; it is read but never modified or downloaded",
    )

    lifecycle_parser = subparsers.add_parser(
        "lifecycle",
        help="follow one discovered station target for at most seven requests",
    )
    lifecycle_parser.add_argument("--station", choices=sorted(STATIONS), required=True)
    lifecycle_parser.add_argument(
        "--static-gtfs",
        metavar="/absolute/path/to/gtfs.zip",
        help="optional local static GTFS ZIP; it is read but never modified or downloaded",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    bindings = load_gtfs_realtime_bindings()
    if bindings is None:
        return 2
    semantic_surface = inspect_actual_semantic_surface(bindings)
    if args.mode == "inventory":
        return inventory(args, bindings, semantic_surface)
    return lifecycle(args, bindings, semantic_surface)


if __name__ == "__main__":
    raise SystemExit(main())
