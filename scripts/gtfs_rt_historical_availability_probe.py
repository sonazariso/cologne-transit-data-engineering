#!/usr/bin/env python3
# HOST-RUN ONLY.
# Do not execute from Codex.
# This bounded diagnostic queries only Internet Archive / Wayback metadata and,
# when a candidate exists, replays at most one archived payload per horizon.
"""Audit third-party point-in-time availability of an exact GTFS-RT URL.

S19 is a post-Stage-02 validation, not a production collector.  The script
uses the Internet Archive CDX API to discover captures of the exact current
GTFS.de realtime URL and optionally replays one nearest capture per requested
calendar-month horizon.  It never requests the live GTFS.de URL directly.

Normal output is deterministic JSON Lines: run configuration, one record per
horizon, and a final summary.  Network execution is intentionally left to the
user's host terminal; Codex must not run this script.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import os
import re
import socket
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


PROBE_NAME = "gtfs_rt_historical_availability_probe"
SOURCE_FEED_URL = "https://realtime.gtfs.de/realtime-free.pb"
SOURCE_IDENTITY_SCOPE = "EXACT_URL_ONLY"
ARCHIVE_SOURCE = "Internet Archive / Wayback Machine"
CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"
ARCHIVE_REPLAY_BASE = "https://web.archive.org/web"
LIVE_SOURCE_HOST = "realtime.gtfs.de"
ALLOWED_ARCHIVE_HOSTS = frozenset(("web.archive.org",))
CDX_FIELDS = ("timestamp", "original", "statuscode", "mimetype", "digest", "length")
MAX_CDX_ROWS = 100
CDX_TIMEOUT_SECONDS = 30
ARCHIVE_REPLAY_TIMEOUT_SECONDS = 90
MAX_CDX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_ARCHIVE_PAYLOAD_BYTES = 150 * 1024 * 1024
MAX_EXAMPLES = 10
MAX_ERROR_LENGTH = 240
CURRENT_NEUMARKT_IDS = frozenset(("106842", "207108", "441164", "581903", "96309"))

RECOVERABLE_CLASSIFICATIONS = frozenset(
    (
        "ARCHIVED_GTFS_RT_SNAPSHOT_PARSEABLE",
        "ARCHIVED_GTFS_RT_SNAPSHOT_PARSEABLE_TEMPORAL_MISMATCH",
    )
)

DETERMINATE_NONRECOVERABLE_CLASSIFICATIONS = frozenset(
    (
        "NO_ARCHIVE_CAPTURE_FOUND",
        "ARCHIVE_REPLAY_FAILED",
        "ARCHIVE_REPLAY_NOT_GTFS_RT_PROTOBUF",
    )
)


class ResponseTooLarge(ValueError):
    """Raised when a response exceeds the diagnostic byte bound."""

    def __init__(self, observed_bytes: int, maximum_bytes: int) -> None:
        super().__init__("response exceeded the bounded byte limit")
        self.observed_bytes = observed_bytes
        self.maximum_bytes = maximum_bytes


class ArchiveRedirectRejected(Exception):
    """A redirect was examined and deliberately not followed."""

    def __init__(
        self,
        status_code: Optional[int],
        destination_url: str,
        destination_host: Optional[str],
        reason: str,
    ) -> None:
        super().__init__(reason)
        self.status_code = status_code
        self.destination_url = destination_url
        self.destination_host = destination_host
        self.reason = reason


@dataclass(frozen=True)
class Capture:
    timestamp: datetime
    original: str
    statuscode: int
    mimetype: Optional[str]
    digest: Optional[str]
    length: Optional[int]
    raw_timestamp: str


@dataclass
class CDXParseResult:
    captures: List[Capture]
    rows_returned: int
    capture_result_set_may_be_truncated: bool
    invalid_row_count: int = 0


@dataclass
class HTTPResult:
    http_status: Optional[int]
    elapsed_ms: Optional[float]
    body: Optional[bytes]
    content_type: Optional[str]
    content_length: Optional[int]
    final_host: Optional[str]
    error_type: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class AttemptTracker:
    total_http_attempts: int = 0
    successful_http_attempts: int = 0

    def begin(self) -> None:
        # Increment before the attempt, including DNS failures and timeouts.
        self.total_http_attempts += 1

    def finish(self, status_code: Optional[int]) -> None:
        if status_code is not None and 200 <= status_code < 300:
            self.successful_http_attempts += 1


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("must use YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("must use YYYY-MM-DD")
    return parsed


def non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be greater than or equal to zero")
    return parsed


def positive_int(value: str) -> int:
    parsed = non_negative_int(value)
    if parsed == 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def target_hour(value: str) -> int:
    parsed = non_negative_int(value)
    if parsed > 23:
        raise argparse.ArgumentTypeError("must be between 0 and 23 UTC")
    return parsed


def subtract_calendar_months(value: date, months: int) -> date:
    """Subtract calendar months and clamp to the destination month's end."""

    if months < 0:
        raise ValueError("months must be greater than or equal to zero")
    zero_based_month = value.year * 12 + (value.month - 1) - months
    year, month_index = divmod(zero_based_month, 12)
    month = month_index + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def make_target_reference(target_date: date, hour_utc: int) -> datetime:
    return datetime.combine(
        target_date,
        datetime_time(hour=hour_utc, tzinfo=timezone.utc),
    )


def wayback_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")


def parse_wayback_timestamp(value: Any) -> Optional[datetime]:
    """Parse the canonical 14-digit CDX timestamp as timezone-aware UTC."""

    if value is None:
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d{14}", text) is None:
        return None
    try:
        return datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def clean_error(error: BaseException) -> str:
    message = " ".join(str(error).split())
    return (message or type(error).__name__)[:MAX_ERROR_LENGTH]


def parse_content_length(headers: Any) -> Optional[int]:
    try:
        value = headers.get("Content-Length")
    except AttributeError:
        return None
    if value is None:
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def read_response_bounded(response: Any, maximum_bytes: int) -> bytes:
    body = bytearray()
    while True:
        chunk = response.read(1024 * 1024)
        if not chunk:
            break
        body.extend(chunk)
        if len(body) > maximum_bytes:
            raise ResponseTooLarge(len(body), maximum_bytes)
    return bytes(body)


def host_from_url(value: str) -> Optional[str]:
    try:
        parsed = urllib.parse.urlsplit(value)
        host = parsed.hostname
    except ValueError:
        return None
    return host.casefold() if host else None


def allowed_archive_host(value: str) -> Optional[str]:
    """Return the approved host, or None for live/unapproved destinations."""

    host = host_from_url(value)
    if host is None or host == LIVE_SOURCE_HOST:
        return None
    if host not in ALLOWED_ARCHIVE_HOSTS:
        return None
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme.casefold() != "https":
            return None
        if parsed.port not in (None, 443):
            return None
    except ValueError:
        return None
    return host


def validate_archive_request_url(value: str) -> str:
    host = allowed_archive_host(value)
    if host is None:
        raise ValueError("request destination is not an approved archive host")
    return host


class ArchiveRedirectGuard(urllib.request.HTTPRedirectHandler):
    """Validate redirect destinations without allowing hidden requests.

    The single planned CDX/replay request per operation is preserved.  A
    redirect is therefore examined and rejected rather than followed.  The
    destination is still checked against the archive allowlist so a live-feed
    redirect is explicitly distinguishable from an approved-host redirect.
    """

    def redirect_request(
        self,
        request: urllib.request.Request,
        response: Any,
        code: int,
        message: str,
        headers: Any,
        newurl: str,
    ) -> Any:
        destination_host = host_from_url(newurl)
        if destination_host == LIVE_SOURCE_HOST:
            reason = "redirect to live GTFS.de source rejected"
        elif allowed_archive_host(newurl) is None:
            reason = "redirect to unapproved host rejected"
        else:
            reason = "approved archive redirect not followed within request budget"
        raise ArchiveRedirectRejected(code, newurl, destination_host, reason)


def request_archive_get(
    url: str,
    timeout_seconds: int,
    tracker: AttemptTracker,
    maximum_response_bytes: int,
) -> HTTPResult:
    """Perform one guarded archive GET with no retries or automatic redirects."""

    request_host = validate_archive_request_url(url)
    tracker.begin()
    started = time.monotonic()
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json, application/x-protobuf, application/octet-stream, text/plain;q=0.1",
            "User-Agent": "cologne-transit-gtfs-rt-historical-availability-audit/1.0",
        },
    )
    opener = urllib.request.build_opener(ArchiveRedirectGuard())

    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            status_code = int(response.getcode())
            content_type = response.headers.get("Content-Type")
            content_length = parse_content_length(response.headers)
            body = read_response_bounded(response, maximum_response_bytes)
        elapsed_ms = round((time.monotonic() - started) * 1000, 3)
        tracker.finish(status_code)
        return HTTPResult(
            http_status=status_code,
            elapsed_ms=elapsed_ms,
            body=body,
            content_type=content_type,
            content_length=content_length,
            final_host=request_host,
        )
    except ArchiveRedirectRejected as error:
        elapsed_ms = round((time.monotonic() - started) * 1000, 3)
        return HTTPResult(
            http_status=error.status_code,
            elapsed_ms=elapsed_ms,
            body=None,
            content_type=None,
            content_length=None,
            final_host=error.destination_host,
            error_type=(
                "LIVE_SOURCE_REDIRECT_REJECTED"
                if error.destination_host == LIVE_SOURCE_HOST
                else "ARCHIVE_REDIRECT_REJECTED"
            ),
            error_message=error.reason,
        )
    except urllib.error.HTTPError as error:
        content_type = error.headers.get("Content-Type") if error.headers else None
        content_length = parse_content_length(error.headers)
        body: Optional[bytes] = None
        response_bytes_error: Optional[str] = None
        try:
            body = read_response_bounded(error, maximum_response_bytes)
        except ResponseTooLarge as size_error:
            response_bytes_error = type(size_error).__name__
        elapsed_ms = round((time.monotonic() - started) * 1000, 3)
        tracker.finish(error.code)
        return HTTPResult(
            http_status=int(error.code),
            elapsed_ms=elapsed_ms,
            body=body,
            content_type=content_type,
            content_length=content_length,
            final_host=request_host,
            error_type=response_bytes_error or "HTTP_ERROR",
            error_message=clean_error(error),
        )
    except ResponseTooLarge as error:
        elapsed_ms = round((time.monotonic() - started) * 1000, 3)
        return HTTPResult(
            http_status=None,
            elapsed_ms=elapsed_ms,
            body=None,
            content_type=None,
            content_length=None,
            final_host=request_host,
            error_type="RESPONSE_TOO_LARGE",
            error_message=clean_error(error),
        )
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as error:
        elapsed_ms = round((time.monotonic() - started) * 1000, 3)
        return HTTPResult(
            http_status=None,
            elapsed_ms=elapsed_ms,
            body=None,
            content_type=None,
            content_length=None,
            final_host=request_host,
            error_type=type(error).__name__,
            error_message=clean_error(error),
        )


def build_cdx_request_url(search_start: datetime, search_end: datetime) -> str:
    """Build an exact-URL CDX query bounded to one configured time window."""

    params = [
        ("url", SOURCE_FEED_URL),
        ("matchType", "exact"),
        ("output", "json"),
        ("fl", ",".join(CDX_FIELDS)),
        ("filter", "statuscode:200"),
        ("limit", str(MAX_CDX_ROWS)),
        ("from", wayback_timestamp(search_start)),
        ("to", wayback_timestamp(search_end)),
    ]
    return "%s?%s" % (CDX_ENDPOINT, urllib.parse.urlencode(params))


def build_archive_replay_url(capture: Capture) -> str:
    """Build Wayback raw replay URL for the already exact-filtered capture."""

    if capture.original != SOURCE_FEED_URL:
        raise ValueError("capture is outside the exact source identity scope")
    encoded_original = urllib.parse.quote(capture.original, safe=":/")
    return "%s/%sid_/%s" % (
        ARCHIVE_REPLAY_BASE,
        capture.raw_timestamp,
        encoded_original,
    )


def _normalise_field_name(value: Any) -> str:
    name = str(value).strip().casefold()
    aliases = {
        "url": "original",
        "mime": "mimetype",
        "status": "statuscode",
        "status_code": "statuscode",
    }
    return aliases.get(name, name)


def _is_cdx_header(row: Sequence[Any]) -> bool:
    names = {_normalise_field_name(value) for value in row}
    return "timestamp" in names and ("original" in names or "statuscode" in names)


def _row_to_values(row: Any, field_names: Sequence[str]) -> Optional[Dict[str, Any]]:
    if isinstance(row, dict):
        values: Dict[str, Any] = {}
        for key, value in row.items():
            values[_normalise_field_name(key)] = value
        return values
    if not isinstance(row, list):
        return None
    return {
        field_names[index]: row[index]
        for index in range(min(len(row), len(field_names)))
    }


def _optional_length(value: Any) -> Optional[int]:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def parse_cdx_payload(body: bytes) -> CDXParseResult:
    """Parse bounded CDX JSON and retain only exact successful captures."""

    decoded = body.decode("utf-8-sig")
    payload = json.loads(decoded)
    if not isinstance(payload, list):
        raise ValueError("CDX JSON root is not an array")
    if not payload:
        return CDXParseResult([], 0, False)

    field_names = list(CDX_FIELDS)
    rows: List[Any] = payload
    if isinstance(payload[0], list) and _is_cdx_header(payload[0]):
        field_names = [_normalise_field_name(value) for value in payload[0]]
        rows = payload[1:]

    captures: List[Capture] = []
    invalid_row_count = 0
    for row in rows:
        values = _row_to_values(row, field_names)
        if values is None:
            invalid_row_count += 1
            continue
        capture_time = parse_wayback_timestamp(values.get("timestamp"))
        original = str(values.get("original", "")).strip()
        try:
            statuscode = int(str(values.get("statuscode", "")).strip())
        except (TypeError, ValueError):
            statuscode = None
        if (
            capture_time is None
            or original != SOURCE_FEED_URL
            or statuscode != 200
        ):
            invalid_row_count += 1
            continue
        mimetype_value = values.get("mimetype")
        digest_value = values.get("digest")
        captures.append(
            Capture(
                timestamp=capture_time,
                original=original,
                statuscode=statuscode,
                mimetype=(str(mimetype_value).strip() or None)
                if mimetype_value is not None
                else None,
                digest=(str(digest_value).strip() or None)
                if digest_value is not None
                else None,
                length=_optional_length(values.get("length")),
                raw_timestamp=str(values.get("timestamp")).strip(),
            )
        )

    rows_returned = len(rows)
    return CDXParseResult(
        captures=captures,
        rows_returned=rows_returned,
        capture_result_set_may_be_truncated=rows_returned == MAX_CDX_ROWS,
        invalid_row_count=invalid_row_count,
    )


def captures_in_window(
    captures: Iterable[Capture],
    search_start: datetime,
    search_end: datetime,
) -> List[Capture]:
    return sorted(
        [
            capture
            for capture in captures
            if search_start <= capture.timestamp <= search_end
        ],
        key=lambda capture: (
            capture.timestamp,
            capture.original,
            capture.digest or "",
            capture.length if capture.length is not None else -1,
        ),
    )


def select_nearest_capture(
    captures: Iterable[Capture],
    target_reference: datetime,
    search_start: datetime,
    search_end: datetime,
) -> Tuple[List[Capture], Optional[Capture]]:
    """Select nearest capture; an equal-distance tie chooses the earlier one."""

    candidates = captures_in_window(captures, search_start, search_end)
    if not candidates:
        return candidates, None
    selected = min(
        candidates,
        key=lambda capture: (
            abs((capture.timestamp - target_reference).total_seconds()),
            capture.timestamp,
            capture.digest or "",
            capture.length if capture.length is not None else -1,
        ),
    )
    return candidates, selected


def create_raw_temp_directory() -> Optional[Path]:
    """Create a private raw-evidence directory outside the repository."""

    candidates = [Path("/private/tmp"), Path(tempfile.gettempdir())]
    seen: set[str] = set()
    for parent in candidates:
        parent_text = str(parent)
        if parent_text in seen or not parent.is_dir() or not os.access(parent, os.W_OK):
            continue
        seen.add(parent_text)
        try:
            directory = Path(
                tempfile.mkdtemp(
                    prefix="cologne-transit-gtfs-rt-history-",
                    dir=parent_text,
                )
            )
            os.chmod(directory, 0o700)
            return directory
        except OSError:
            continue
    return None


def write_snapshot(directory: Optional[Path], months_back: int, body: bytes) -> Optional[str]:
    if directory is None:
        return None
    path = directory / ("snapshot-%sm.pb" % months_back)
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as output:
            output.write(body)
        os.chmod(path, 0o600)
        return str(path)
    except OSError:
        return None


def has_field(message: Any, field_name: str) -> bool:
    try:
        return bool(message.HasField(field_name))
    except (AttributeError, ValueError):
        try:
            return field_name in {field.name for field, _ in message.ListFields()}
        except (AttributeError, TypeError):
            return False


def text_field(message: Any, field_name: str) -> Optional[str]:
    if not has_field(message, field_name):
        return None
    value = getattr(message, field_name, None)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def assigned_stop_id_text(stop_update: Any) -> Optional[str]:
    """Read assigned_stop_id across the direct and nested schema forms."""

    direct_value = text_field(stop_update, "assigned_stop_id")
    if direct_value is not None:
        return direct_value
    properties = getattr(stop_update, "stop_time_properties", None)
    if properties is not None:
        return text_field(properties, "assigned_stop_id")
    return None


def enum_name(message: Any, field_name: str, only_if_present: bool = False) -> Optional[str]:
    if only_if_present and not has_field(message, field_name):
        return None
    try:
        value = int(getattr(message, field_name))
        descriptor = message.DESCRIPTOR.fields_by_name[field_name]
        return descriptor.enum_type.values_by_number[value].name
    except (AttributeError, KeyError, TypeError, ValueError):
        return str(getattr(message, field_name, "")) or None


def optional_int(message: Any, field_name: str) -> Optional[int]:
    if not has_field(message, field_name):
        return None
    try:
        return int(getattr(message, field_name))
    except (AttributeError, TypeError, ValueError):
        return None


def epoch_to_datetime(seconds: Optional[int]) -> Optional[datetime]:
    if seconds is None:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def capped_sorted(values: Iterable[str]) -> List[str]:
    return sorted(set(values))[:MAX_EXAMPLES]


def increment_count(counts: Dict[str, int], value: Optional[str]) -> None:
    if value is not None:
        counts[value] = counts.get(value, 0) + 1


def empty_inventory() -> Dict[str, Any]:
    return {
        "gtfs_realtime_version": None,
        "feed_incrementality": None,
        "feed_header_timestamp_utc": None,
        "feed_header_vs_archive_capture_delta_seconds": None,
        "feed_header_same_utc_date_as_archive_capture": None,
        "temporal_consistency_classification": "INCONCLUSIVE",
        "entity_count_total": 0,
        "trip_update_entity_count": 0,
        "vehicle_entity_count": 0,
        "alert_entity_count": 0,
        "other_entity_count": 0,
        "other_entity_type_counts": {},
        "stop_time_update_count_total": 0,
        "trip_updates_with_timestamp_count": 0,
        "trip_updates_without_timestamp_count": 0,
        "arrival_time_count": 0,
        "arrival_delay_count": 0,
        "arrival_uncertainty_count": 0,
        "departure_time_count": 0,
        "departure_delay_count": 0,
        "departure_uncertainty_count": 0,
        "assigned_stop_id_count": 0,
        "trip_schedule_relationship_counts": {},
        "stop_schedule_relationship_counts": {},
        "canceled_trip_count": 0,
        "skipped_stop_update_count": 0,
        "no_data_stop_update_count": 0,
        "unique_trip_id_count": 0,
        "unique_route_id_count": 0,
        "unique_stop_id_count": 0,
        "trip_id_examples": [],
        "route_id_examples": [],
        "stop_id_examples": [],
        "entity_id_examples": [],
        "current_neumarkt_id_strings_observed_in_archive": [],
        "current_id_reobservation_is_historical_identity_proof": False,
        "standard_stop_time_event_time_semantics": "ESTIMATED_OR_ACTUAL_EVENT_TIME",
        "actual_time_semantic_classification": "NO_EXPLICIT_ACTUAL_SEMANTIC_OBSERVED",
        "explicit_actual_semantic_evidence": [],
        "historical_station_identity": "HISTORICAL_STATION_IDENTITY_NOT_TESTED",
        "historical_static_compatibility": "HISTORICAL_STATIC_COMPATIBILITY_NOT_TESTED",
        "continuous_archive_coverage_established": False,
    }


def analyse_feed(feed: Any, archive_capture: datetime) -> Dict[str, Any]:
    """Inventory one parseable FeedMessage without station matching."""

    result = empty_inventory()
    entities = list(getattr(feed, "entity", []))
    result["entity_count_total"] = len(entities)
    trip_ids: set[str] = set()
    route_ids: set[str] = set()
    stop_ids: set[str] = set()
    entity_ids: set[str] = set()
    current_neumarkt_ids: set[str] = set()
    trip_relationships: Dict[str, int] = {}
    stop_relationships: Dict[str, int] = {}
    other_types: Dict[str, int] = {}

    for entity in entities:
        entity_id = text_field(entity, "id")
        if entity_id:
            entity_ids.add(entity_id)
        entity_type = None
        try:
            entity_type = entity.WhichOneof("entity")
        except (AttributeError, ValueError):
            entity_type = None

        if has_field(entity, "trip_update"):
            result["trip_update_entity_count"] += 1
            entity_type = "trip_update"
            trip_update = entity.trip_update
            trip = trip_update.trip
            trip_id = text_field(trip, "trip_id")
            route_id = text_field(trip, "route_id")
            if trip_id:
                trip_ids.add(trip_id)
            if route_id:
                route_ids.add(route_id)
            trip_relationship = enum_name(trip, "schedule_relationship")
            increment_count(trip_relationships, trip_relationship)
            if trip_relationship == "CANCELED":
                result["canceled_trip_count"] += 1
            if optional_int(trip_update, "timestamp") is not None:
                result["trip_updates_with_timestamp_count"] += 1
            else:
                result["trip_updates_without_timestamp_count"] += 1

            for stop_update in getattr(trip_update, "stop_time_update", []):
                result["stop_time_update_count_total"] += 1
                stop_id = text_field(stop_update, "stop_id")
                assigned_stop_id = assigned_stop_id_text(stop_update)
                if stop_id:
                    stop_ids.add(stop_id)
                    if stop_id in CURRENT_NEUMARKT_IDS:
                        current_neumarkt_ids.add(stop_id)
                if assigned_stop_id:
                    result["assigned_stop_id_count"] += 1
                    if assigned_stop_id in CURRENT_NEUMARKT_IDS:
                        current_neumarkt_ids.add(assigned_stop_id)
                stop_relationship = enum_name(stop_update, "schedule_relationship")
                increment_count(stop_relationships, stop_relationship)
                if stop_relationship == "SKIPPED":
                    result["skipped_stop_update_count"] += 1
                if stop_relationship == "NO_DATA":
                    result["no_data_stop_update_count"] += 1

                for event_name, time_key, delay_key, uncertainty_key in (
                    ("arrival", "arrival_time_count", "arrival_delay_count", "arrival_uncertainty_count"),
                    ("departure", "departure_time_count", "departure_delay_count", "departure_uncertainty_count"),
                ):
                    event = getattr(stop_update, event_name)
                    if optional_int(event, "time") is not None:
                        result[time_key] += 1
                    if optional_int(event, "delay") is not None:
                        result[delay_key] += 1
                    if optional_int(event, "uncertainty") is not None:
                        result[uncertainty_key] += 1
        elif has_field(entity, "vehicle"):
            result["vehicle_entity_count"] += 1
            entity_type = "vehicle"
        elif has_field(entity, "alert"):
            result["alert_entity_count"] += 1
            entity_type = "alert"

        if entity_type not in {"trip_update", "vehicle", "alert"}:
            result["other_entity_count"] += 1
            label = entity_type or "unknown"
            other_types[label] = other_types.get(label, 0) + 1

    result["other_entity_type_counts"] = dict(sorted(other_types.items()))
    result["trip_schedule_relationship_counts"] = dict(sorted(trip_relationships.items()))
    result["stop_schedule_relationship_counts"] = dict(sorted(stop_relationships.items()))
    result["unique_trip_id_count"] = len(trip_ids)
    result["unique_route_id_count"] = len(route_ids)
    result["unique_stop_id_count"] = len(stop_ids)
    result["trip_id_examples"] = capped_sorted(trip_ids)
    result["route_id_examples"] = capped_sorted(route_ids)
    result["stop_id_examples"] = capped_sorted(stop_ids)
    result["entity_id_examples"] = capped_sorted(entity_ids)
    result["current_neumarkt_id_strings_observed_in_archive"] = capped_sorted(current_neumarkt_ids)

    header = getattr(feed, "header", None)
    header_timestamp: Optional[datetime] = None
    header_timestamp_field_present = False
    if header is not None and has_field(feed, "header"):
        version = text_field(header, "gtfs_realtime_version")
        if version is not None:
            result["gtfs_realtime_version"] = version
        result["feed_incrementality"] = enum_name(header, "incrementality", only_if_present=True)
        header_seconds = optional_int(header, "timestamp")
        header_timestamp_field_present = header_seconds is not None
        header_timestamp = epoch_to_datetime(header_seconds)
        result["feed_header_timestamp_utc"] = iso_utc(header_timestamp)

    if header_timestamp is None:
        result["temporal_consistency_classification"] = (
            "HEADER_TIMESTAMP_MISSING" if not header_timestamp_field_present else "INCONCLUSIVE"
        )
    else:
        delta_seconds = (header_timestamp - archive_capture).total_seconds()
        result["feed_header_vs_archive_capture_delta_seconds"] = int(delta_seconds)
        same_date = header_timestamp.date() == archive_capture.date()
        result["feed_header_same_utc_date_as_archive_capture"] = same_date
        result["temporal_consistency_classification"] = (
            "SAME_UTC_DATE" if same_date else "DIFFERENT_UTC_DATE"
        )
    return result


def obvious_text_response(body: bytes, content_type: Optional[str]) -> bool:
    """Detect common text/error replays without printing or scraping them."""

    normalized_type = (content_type or "").split(";", 1)[0].strip().casefold()
    if normalized_type in {
        "text/html",
        "application/json",
        "application/xml",
        "text/xml",
    }:
        return True
    prefix = body[:512].lstrip().lower()
    if prefix.startswith(
        (
            b"<!doctype html",
            b"<html",
            b"<head",
            b"<body",
            b"<title",
            b"wayback machine",
            b"error:",
        )
    ):
        return True
    if normalized_type.startswith("text/"):
        try:
            decoded = body[:4096].decode("utf-8")
        except UnicodeDecodeError:
            return False
        if not decoded:
            return True
        printable_count = sum(1 for character in decoded if character.isprintable() or character in "\r\n\t")
        return printable_count / len(decoded) >= 0.90
    return False


def load_feed_message(body: bytes) -> Tuple[Optional[Any], Optional[str]]:
    try:
        from google.transit import gtfs_realtime_pb2
    except ImportError:
        return None, "GTFS_RT_BINDINGS_UNAVAILABLE"
    try:
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(body)
        return feed, None
    except Exception as error:  # protobuf parser failures are expected audit outcomes
        return None, type(error).__name__


def base_horizon_record(
    months_back: int,
    target_date: date,
    target_reference: datetime,
    search_start: datetime,
    search_end: datetime,
    metadata_only: bool,
) -> Dict[str, Any]:
    result = empty_inventory()
    result.update(
        {
            "record_type": "horizon_result",
            "months_back": months_back,
            "target_date": target_date.isoformat(),
            "target_reference_utc": iso_utc(target_reference),
            "search_window_start_utc": iso_utc(search_start),
            "search_window_end_utc": iso_utc(search_end),
            "cdx_request_attempted": False,
            "cdx_http_status": None,
            "cdx_elapsed_ms": None,
            "cdx_error_type": None,
            "cdx_error_message": None,
            "cdx_rows_returned": 0,
            "cdx_invalid_row_count": 0,
            "archive_capture_count_in_window": 0,
            "capture_result_set_may_be_truncated": False,
            "first_archive_capture_in_window_utc": None,
            "last_archive_capture_in_window_utc": None,
            "selected_capture_timestamp_utc": None,
            "selected_capture_distance_seconds": None,
            "selected_capture_original": None,
            "selected_capture_statuscode": None,
            "selected_capture_mimetype": None,
            "selected_capture_digest": None,
            "selected_capture_length": None,
            "archive_replay_url": None,
            "archive_replay_attempted": False,
            "archive_replay_http_status": None,
            "archive_replay_final_host": None,
            "archive_replay_elapsed_ms": None,
            "archive_replay_error_type": None,
            "archive_replay_error_message": None,
            "response_content_type": None,
            "response_content_length": None,
            "response_bytes": None,
            "response_content_length_matches": None,
            "response_obvious_text": None,
            "archive_payload_sha256": None,
            "protobuf_parse_success": False,
            "protobuf_parse_error_type": None,
            "metadata_only": metadata_only,
            "raw_temp_path": None,
            "historical_availability_classification": "INCONCLUSIVE",
        }
    )
    return result


def run_horizon(
    months_back: int,
    as_of_date: date,
    target_hour_utc: int,
    window_days: int,
    metadata_only: bool,
    raw_directory: Optional[Path],
    tracker: AttemptTracker,
) -> Dict[str, Any]:
    target_date = subtract_calendar_months(as_of_date, months_back)
    target_reference = make_target_reference(target_date, target_hour_utc)
    search_start = target_reference - timedelta(days=window_days)
    search_end = target_reference + timedelta(days=window_days)
    result = base_horizon_record(
        months_back,
        target_date,
        target_reference,
        search_start,
        search_end,
        metadata_only,
    )

    cdx_url = build_cdx_request_url(search_start, search_end)
    result["cdx_request_attempted"] = True
    cdx_response = request_archive_get(
        cdx_url,
        CDX_TIMEOUT_SECONDS,
        tracker,
        MAX_CDX_RESPONSE_BYTES,
    )
    result["cdx_http_status"] = cdx_response.http_status
    result["cdx_elapsed_ms"] = cdx_response.elapsed_ms
    result["cdx_error_type"] = cdx_response.error_type
    result["cdx_error_message"] = cdx_response.error_message

    if cdx_response.http_status != 200 or cdx_response.body is None:
        result["historical_availability_classification"] = "INCONCLUSIVE"
        return result

    try:
        cdx_result = parse_cdx_payload(cdx_response.body)
    except (UnicodeError, ValueError, json.JSONDecodeError, TypeError) as error:
        result["cdx_error_type"] = type(error).__name__
        result["cdx_error_message"] = clean_error(error)
        result["historical_availability_classification"] = "INCONCLUSIVE"
        return result

    result["cdx_rows_returned"] = cdx_result.rows_returned
    result["cdx_invalid_row_count"] = cdx_result.invalid_row_count
    result["capture_result_set_may_be_truncated"] = cdx_result.capture_result_set_may_be_truncated
    in_window, selected = select_nearest_capture(
        cdx_result.captures,
        target_reference,
        search_start,
        search_end,
    )
    result["archive_capture_count_in_window"] = len(in_window)
    if in_window:
        result["first_archive_capture_in_window_utc"] = iso_utc(in_window[0].timestamp)
        result["last_archive_capture_in_window_utc"] = iso_utc(in_window[-1].timestamp)

    if selected is None:
        result["historical_availability_classification"] = "NO_ARCHIVE_CAPTURE_FOUND"
        return result

    result["selected_capture_timestamp_utc"] = iso_utc(selected.timestamp)
    result["selected_capture_distance_seconds"] = int(
        abs((selected.timestamp - target_reference).total_seconds())
    )
    result["selected_capture_original"] = selected.original
    result["selected_capture_statuscode"] = selected.statuscode
    result["selected_capture_mimetype"] = selected.mimetype
    result["selected_capture_digest"] = selected.digest
    result["selected_capture_length"] = selected.length

    if metadata_only:
        result["historical_availability_classification"] = (
            "ARCHIVE_METADATA_FOUND_PAYLOAD_NOT_REQUESTED"
        )
        return result

    replay_url = build_archive_replay_url(selected)
    result["archive_replay_url"] = replay_url
    result["archive_replay_attempted"] = True
    replay_response = request_archive_get(
        replay_url,
        ARCHIVE_REPLAY_TIMEOUT_SECONDS,
        tracker,
        MAX_ARCHIVE_PAYLOAD_BYTES,
    )
    result["archive_replay_http_status"] = replay_response.http_status
    result["archive_replay_final_host"] = replay_response.final_host
    result["archive_replay_elapsed_ms"] = replay_response.elapsed_ms
    result["archive_replay_error_type"] = replay_response.error_type
    result["archive_replay_error_message"] = replay_response.error_message
    result["response_content_type"] = replay_response.content_type
    result["response_content_length"] = replay_response.content_length
    result["response_bytes"] = len(replay_response.body) if replay_response.body is not None else None
    if replay_response.body is not None and replay_response.content_length is not None:
        result["response_content_length_matches"] = (
            replay_response.content_length == len(replay_response.body)
        )

    if replay_response.http_status != 200 or replay_response.body is None:
        result["historical_availability_classification"] = "ARCHIVE_REPLAY_FAILED"
        return result

    body = replay_response.body
    result["archive_payload_sha256"] = hashlib.sha256(body).hexdigest()
    result["raw_temp_path"] = write_snapshot(raw_directory, months_back, body)
    result["response_obvious_text"] = obvious_text_response(body, replay_response.content_type)
    if result["response_obvious_text"]:
        result["protobuf_parse_error_type"] = "OBVIOUS_TEXT_OR_ARCHIVE_ERROR_RESPONSE"
        result["historical_availability_classification"] = "ARCHIVE_REPLAY_NOT_GTFS_RT_PROTOBUF"
        return result

    feed, parse_error_type = load_feed_message(body)
    result["protobuf_parse_success"] = feed is not None and parse_error_type is None
    result["protobuf_parse_error_type"] = parse_error_type
    if feed is None:
        if parse_error_type == "GTFS_RT_BINDINGS_UNAVAILABLE":
            result["historical_availability_classification"] = "INCONCLUSIVE"
        else:
            result["historical_availability_classification"] = "ARCHIVE_REPLAY_NOT_GTFS_RT_PROTOBUF"
        return result

    result.update(analyse_feed(feed, selected.timestamp))
    if result["temporal_consistency_classification"] == "DIFFERENT_UTC_DATE":
        result["historical_availability_classification"] = (
            "ARCHIVED_GTFS_RT_SNAPSHOT_PARSEABLE_TEMPORAL_MISMATCH"
        )
    else:
        result["historical_availability_classification"] = "ARCHIVED_GTFS_RT_SNAPSHOT_PARSEABLE"
    return result


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Host-run-only bounded Wayback point-snapshot availability audit "
            "for the exact GTFS.de realtime URL."
        )
    )
    parser.add_argument(
        "--as-of-date",
        type=parse_iso_date,
        default=utc_now().date(),
        help="audit date in YYYY-MM-DD (default: current UTC date)",
    )
    parser.add_argument(
        "--months",
        nargs="+",
        type=positive_int,
        default=[3, 6],
        help="calendar-month horizons to test (default: 3 6)",
    )
    parser.add_argument(
        "--window-days",
        type=non_negative_int,
        default=7,
        help="half-width of the UTC search window in days (default: 7)",
    )
    parser.add_argument(
        "--target-hour-utc",
        type=target_hour,
        default=12,
        help="UTC hour used for deterministic target references (default: 12)",
    )
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="discover metadata without replaying archived payloads",
    )
    return parser.parse_args(argv)


def aggregate_classification(
    horizon_results: Sequence[Dict[str, Any]],
    metadata_only: bool,
) -> str:
    by_month: Dict[int, str] = {
        int(result["months_back"]): result["historical_availability_classification"]
        for result in horizon_results
    }
    three = by_month.get(3)
    six = by_month.get(6)
    if three is None or six is None:
        return "HISTORICAL_ARCHIVE_TEST_INCONCLUSIVE"
    three_recoverable = three in RECOVERABLE_CLASSIFICATIONS
    six_recoverable = six in RECOVERABLE_CLASSIFICATIONS
    if three_recoverable and six_recoverable:
        return "BOTH_3M_AND_6M_POINT_SNAPSHOTS_RECOVERABLE"
    if three_recoverable and six in DETERMINATE_NONRECOVERABLE_CLASSIFICATIONS:
        return "ONLY_3M_POINT_SNAPSHOT_RECOVERABLE"
    if six_recoverable and three in DETERMINATE_NONRECOVERABLE_CLASSIFICATIONS:
        return "ONLY_6M_POINT_SNAPSHOT_RECOVERABLE"
    metadata_outcomes = {
        "NO_ARCHIVE_CAPTURE_FOUND",
        "ARCHIVE_METADATA_FOUND_PAYLOAD_NOT_REQUESTED",
    }
    if (
        metadata_only
        and three in metadata_outcomes
        and six in metadata_outcomes
        and (
            three == "ARCHIVE_METADATA_FOUND_PAYLOAD_NOT_REQUESTED"
            or six == "ARCHIVE_METADATA_FOUND_PAYLOAD_NOT_REQUESTED"
        )
    ):
        return "THIRD_PARTY_ARCHIVE_METADATA_ONLY"
    if three in DETERMINATE_NONRECOVERABLE_CLASSIFICATIONS and six in DETERMINATE_NONRECOVERABLE_CLASSIFICATIONS:
        return "NO_3M_OR_6M_POINT_SNAPSHOT_RECOVERABLE"
    return "HISTORICAL_ARCHIVE_TEST_INCONCLUSIVE"


def collector_implication(aggregate: str) -> str:
    if aggregate == "BOTH_3M_AND_6M_POINT_SNAPSHOTS_RECOVERABLE":
        return (
            "Third-party point-in-time GTFS-Realtime snapshots were recoverable near both "
            "tested historical horizons. This does not establish provider-supported or "
            "continuous historical GTFS-Realtime retention. Prospective collection remains "
            "required for controlled operational history."
        )
    if aggregate in {
        "ONLY_3M_POINT_SNAPSHOT_RECOVERABLE",
        "ONLY_6M_POINT_SNAPSHOT_RECOVERABLE",
    }:
        return (
            "A third-party point-in-time GTFS-Realtime snapshot was recoverable near one "
            "tested historical horizon only. The evidence is sparse and does not establish "
            "continuous historical coverage."
        )
    if aggregate == "NO_3M_OR_6M_POINT_SNAPSHOT_RECOVERABLE":
        return (
            "No usable third-party GTFS-Realtime point snapshot was recovered near the "
            "tested 3-month or 6-month horizons. This does not prove that no historical copy "
            "exists elsewhere. Prospective collection remains required if controlled "
            "historical realtime analysis is needed."
        )
    if aggregate == "THIRD_PARTY_ARCHIVE_METADATA_ONLY":
        return (
            "Third-party archive metadata was found, but archived payloads were not requested. "
            "Payload recoverability and continuous historical coverage remain untested."
        )
    return (
        "The bounded third-party historical archive test was inconclusive for at least one "
        "tested horizon. Prospective collection remains required for controlled historical "
        "realtime analysis."
    )


def emit_json(record: Dict[str, Any]) -> None:
    print(json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    execution_timestamp = utc_now()
    raw_directory = create_raw_temp_directory()
    tracker = AttemptTracker()
    maximum_http_attempts = len(args.months) * (1 if args.metadata_only else 2)

    emit_json(
        {
            "record_type": "run_configuration",
            "probe_name": PROBE_NAME,
            "execution_timestamp_utc": iso_utc(execution_timestamp),
            "source_feed_url": SOURCE_FEED_URL,
            "source_identity_scope": SOURCE_IDENTITY_SCOPE,
            "archive_source": ARCHIVE_SOURCE,
            "as_of_date": args.as_of_date.isoformat(),
            "requested_month_horizons": list(args.months),
            "window_days": args.window_days,
            "target_reference_hour_utc": args.target_hour_utc,
            "metadata_only": bool(args.metadata_only),
            "maximum_http_attempts": maximum_http_attempts,
            "provider_history_endpoint_validated": False,
            "provider_history_retention_policy_validated": False,
        }
    )

    horizon_results: List[Dict[str, Any]] = []
    for months_back in args.months:
        try:
            result = run_horizon(
                months_back=months_back,
                as_of_date=args.as_of_date,
                target_hour_utc=args.target_hour_utc,
                window_days=args.window_days,
                metadata_only=args.metadata_only,
                raw_directory=raw_directory,
                tracker=tracker,
            )
        except Exception as error:  # keep expected host audit failures JSON-serializable
            target_date = subtract_calendar_months(args.as_of_date, months_back)
            target_reference = make_target_reference(target_date, args.target_hour_utc)
            search_start = target_reference - timedelta(days=args.window_days)
            search_end = target_reference + timedelta(days=args.window_days)
            result = base_horizon_record(
                months_back,
                target_date,
                target_reference,
                search_start,
                search_end,
                args.metadata_only,
            )
            result["cdx_error_type"] = type(error).__name__
            result["cdx_error_message"] = clean_error(error)
            result["historical_availability_classification"] = "INCONCLUSIVE"
        horizon_results.append(result)
        emit_json(result)

    aggregate = aggregate_classification(horizon_results, args.metadata_only)
    by_month: Dict[int, str] = {
        int(result["months_back"]): result["historical_availability_classification"]
        for result in horizon_results
    }
    recoverable_count = sum(
        1
        for result in horizon_results
        if result["historical_availability_classification"] in RECOVERABLE_CLASSIFICATIONS
    )
    emit_json(
        {
            "record_type": "final_summary",
            "probe_name": PROBE_NAME,
            "execution_timestamp_utc": iso_utc(execution_timestamp),
            "source_feed_url": SOURCE_FEED_URL,
            "archive_source": ARCHIVE_SOURCE,
            "requested_horizons": list(args.months),
            "total_http_attempts": tracker.total_http_attempts,
            "successful_http_attempts": tracker.successful_http_attempts,
            "recoverable_point_snapshot_count": recoverable_count,
            "three_month_classification": by_month.get(3),
            "six_month_classification": by_month.get(6),
            "aggregate_history_classification": aggregate,
            "provider_history_endpoint_validated": False,
            "provider_history_retention_policy_validated": False,
            "provider_continuous_history_established": False,
            "continuous_archive_coverage_established": False,
            "third_party_archive_only": True,
            "historical_station_identity": "HISTORICAL_STATION_IDENTITY_NOT_TESTED",
            "historical_static_compatibility": "HISTORICAL_STATIC_COMPATIBILITY_NOT_TESTED",
            "actual_time_kpi_approved": False,
            "collector_implication": collector_implication(aggregate),
            "raw_temp_directory": str(raw_directory) if raw_directory else None,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
