#!/usr/bin/env python3
# HOST-RUN ONLY.
# Do not execute from Codex.
"""Bounded Stage 3A disruption and alert evidence audit.

This is a diagnostic preparation tool for manual execution from the approved
host terminal.  A normal host run makes one GTFS-Realtime GET and one MDD
StopEvent POST for each of the two fixed diagnostic stations: three HTTP
attempts in total.  It has no retry, concurrency, redirect-following, or
fallback-endpoint path and does not write to the repository, SQL Server,
Power BI, the scheduler, or the Legacy repository.

The output is deterministic JSON Lines at the record/schema level.  Values
that describe the current snapshot, request timing, and HTTP responses are
necessarily host-run observations.  Raw response bytes, when retained, are
written only to a mode-700 temporary directory outside the repository.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import Message
from http.client import HTTPResponse
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple


PROBE_NAME = "stage_03a_disruption_evidence_probe"
GTFS_RT_URL = "https://realtime.gtfs.de/realtime-free.pb"
MDD_ENDPOINT = "https://mdd.gorheinland.com/delfi"
MDD_API_KEY_ENV = "MDD_API_KEY"
GTFS_RT_BINDINGS_REQUIREMENT = "gtfs-realtime-bindings==2.2.0"
VALIDATED_HOST_RUNTIME = "Python 3.9.6"

MAX_HTTP_ATTEMPTS = 3
GTFS_RT_MAX_HTTP_ATTEMPTS = 1
MDD_MAX_HTTP_ATTEMPTS = 2
MDD_DISCOVERY_MAX_HTTP_ATTEMPTS = 10
RETRIES = 0
CONCURRENCY = "none"
FALLBACK_ENDPOINTS: Tuple[str, ...] = ()
REQUEST_TIMEOUT_SECONDS = 30
MAX_GTFS_RT_RESPONSE_BYTES = 100 * 1024 * 1024
MAX_MDD_RESPONSE_BYTES = 25 * 1024 * 1024
MAX_TEXT_EXAMPLES = 10
MAX_MDD_FIELD_EXAMPLES = 5
MAX_SHORT_TEXT_CHARS = 240
RAW_TEMP_PREFIX = "cologne-transit-stage-03a-"
GTFSDE_PROVENANCE_DESCRIPTION_PREFIX = "Echtzeitdaten aufbereitet von GTFS.de"

MDD_REASON_FIELD_TERMS = (
    "situation",
    "reason",
    "cause",
    "incident",
    "info",
    "message",
    "summary",
    "description",
    "detail",
    "advice",
    "text",
    "content",
    "effect",
    "impact",
    "consequence",
    "severity",
    "affect",
    "valid",
    "disruption",
    "problem",
    "fullref",
)

MDD_REFERENCE_TYPE_NAMES = {
    "situationfullref": "SituationFullRef",
    "situationref": "SituationRef",
    "incidentcall": "IncidentCall",
    "infocall": "InfoCall",
}

MDD_ERROR_FIELD_NAMES = {"errormessage", "errorcode"}
MDD_GENERAL_ATTRIBUTE_FIELD_NAMES = frozenset({"attribute", "generalattribute"})
MDD_EXTENSION_FIELD_MARKER = "extension"
MDD_OPERATIONAL_STATUS_FIELDS = {
    "cancelled": "Cancelled",
    "deviation": "Deviation",
    "unplanned": "Unplanned",
    "notservicedstop": "NotServicedStop",
    "unplannedstop": "UnplannedStop",
    "noboardingatstop": "NoBoardingAtStop",
    "noalightingatstop": "NoAlightingAtStop",
    "demandstop": "DemandStop",
}

NEUMARKT_STOP_IDS = frozenset(
    ("106842", "207108", "441164", "581903", "96309")
)


@dataclass(frozen=True)
class MddStation:
    key: str
    display_name: str
    stop_point_ref: str


MDD_STATIONS: Tuple[MddStation, ...] = (
    MddStation("koeln_neumarkt", "Köln Neumarkt", "de:05315:11111"),
    MddStation("koeln_bf_muelheim", "Köln Bf Mülheim", "de:05315:19201"),
)

# Diagnostic discovery locations only.  This list does not define production
# monitoring scope, station cadence, or a future collector panel.
MDD_DISCOVERY_STATIONS: Tuple[MddStation, ...] = (
    MddStation("koeln_bf_muelheim", "Köln Bf Mülheim", "de:05315:19201"),
    MddStation("koeln_heumarkt", "Köln Heumarkt", "de:05315:11110"),
    MddStation(
        "koeln_breslauer_platz_hbf",
        "Köln Breslauer Platz/Hbf",
        "de:05315:11212",
    ),
    MddStation(
        "koeln_muelheim_wiener_platz",
        "Köln Mülheim Wiener Platz",
        "de:05315:19211",
    ),
    MddStation("koeln_hbf", "Köln Hbf", "de:05315:11201"),
    MddStation("koeln_messe_deutz_bf", "Köln Messe/Deutz Bf", "de:05315:11901"),
    MddStation("koeln_bf_ehrenfeld", "Köln Bf Ehrenfeld", "de:05315:14201"),
    MddStation("koeln_wahn_sbahn", "Köln Wahn S-Bahn", "de:05315:17701"),
    MddStation("koeln_hansaring", "Köln Hansaring", "de:05315:11801"),
    MddStation("koeln_bf_loevenich", "Köln Bf Lövenich", "de:05315:13701"),
)


@dataclass
class FetchResult:
    source: str
    method: str
    request_timestamp_utc: str
    http_status: Optional[int]
    response_content_type: Optional[str]
    response_bytes: int
    elapsed_ms: float
    body: bytes
    failure: Optional[str]
    raw_artifact_path: Optional[str]


@dataclass
class AttemptTracker:
    total_http_attempts: int = 0
    attempts_by_source: Dict[str, int] = None  # type: ignore[assignment]
    max_http_attempts: int = MAX_HTTP_ATTEMPTS

    def __post_init__(self) -> None:
        if self.attempts_by_source is None:
            self.attempts_by_source = {}

    def begin(self, source: str) -> None:
        """Count before opening the socket, including failures and timeouts."""

        if self.total_http_attempts >= self.max_http_attempts:
            raise RuntimeError("HTTP attempt budget would be exceeded")
        self.total_http_attempts += 1
        self.attempts_by_source[source] = self.attempts_by_source.get(source, 0) + 1


class ResponseTooLarge(ValueError):
    """The bounded diagnostic response-size guard was reached."""


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects so one planned request cannot become extra requests."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: HTTPResponse,
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> Optional[urllib.request.Request]:
        return None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_utc(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def response_charset(content_type: Optional[str]) -> str:
    if content_type:
        for part in content_type.split(";")[1:]:
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            if name.strip().lower() == "charset":
                return value.strip().strip('"') or "utf-8"
    return "utf-8"


def decode_response(content: bytes, content_type: Optional[str]) -> str:
    try:
        return content.decode(response_charset(content_type), errors="replace")
    except LookupError:
        return content.decode("utf-8", errors="replace")


def read_bounded(stream: Any, maximum_bytes: int) -> bytes:
    chunks: List[bytes] = []
    total = 0
    while True:
        chunk = stream.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > maximum_bytes:
            raise ResponseTooLarge("response exceeded the bounded byte limit")
        chunks.append(chunk)
    return b"".join(chunks)


def create_raw_temp_directory() -> Optional[str]:
    """Create the only allowed raw-artifact location, outside the repository."""

    candidates = ["/private/tmp", tempfile.gettempdir()]
    seen: Set[str] = set()
    for parent in candidates:
        if parent in seen or not os.path.isdir(parent) or not os.access(parent, os.W_OK):
            continue
        seen.add(parent)
        try:
            directory = tempfile.mkdtemp(prefix=RAW_TEMP_PREFIX, dir=parent)
            os.chmod(directory, 0o700)
            return directory
        except OSError:
            continue
    return None


def write_raw_artifact(
    raw_directory: Optional[str],
    artifact_name: str,
    content: bytes,
    secret: Optional[str] = None,
) -> Optional[str]:
    """Write a diagnostic body with mode 600, never persisting the API key."""

    if not raw_directory or not content:
        return None
    if secret and secret.encode("utf-8") in content:
        return None
    raw_path = os.path.join(raw_directory, artifact_name)
    file_descriptor = os.open(
        raw_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        os.fchmod(file_descriptor, 0o600)
        with os.fdopen(file_descriptor, "wb") as raw_file:
            raw_file.write(content)
    except Exception:
        try:
            os.close(file_descriptor)
        except OSError:
            pass
        try:
            os.unlink(raw_path)
        except OSError:
            pass
        raise
    return raw_path


def perform_get(tracker: AttemptTracker, raw_directory: Optional[str]) -> FetchResult:
    """Perform the sole GTFS-RT GET.  There is intentionally no retry path."""

    source = "GTFS_RT"
    tracker.begin(source)
    request_timestamp = utc_now()
    started = time.perf_counter()
    request = urllib.request.Request(
        GTFS_RT_URL,
        method="GET",
        headers={
            "Accept": "application/x-protobuf, application/octet-stream",
            "User-Agent": "cologne-transit-stage-03a-disruption-audit/1.0",
        },
    )
    status: Optional[int] = None
    content_type: Optional[str] = None
    content = b""
    failure: Optional[str] = None
    opener = urllib.request.build_opener(NoRedirectHandler())
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status = int(response.status)
            content_type = response.headers.get("Content-Type")
            content = read_bounded(response, MAX_GTFS_RT_RESPONSE_BYTES)
    except urllib.error.HTTPError as error:
        status = int(error.code)
        content_type = error.headers.get("Content-Type") if error.headers else None
        try:
            content = read_bounded(error, MAX_GTFS_RT_RESPONSE_BYTES)
        except (OSError, ResponseTooLarge):
            content = b""
        failure = "HTTP_STATUS"
    except (urllib.error.URLError, TimeoutError, OSError, ResponseTooLarge):
        failure = "REQUEST_FAILED"
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    raw_path = write_raw_artifact(raw_directory, "gtfs-rt-snapshot.pb", content)
    return FetchResult(
        source=source,
        method="GET",
        request_timestamp_utc=format_utc(request_timestamp),
        http_status=status,
        response_content_type=content_type,
        response_bytes=len(content),
        elapsed_ms=elapsed_ms,
        body=content,
        failure=failure,
        raw_artifact_path=raw_path,
    )


def build_mdd_request_body(
    stop_point_ref: str,
    request_timestamp_utc: str,
    dep_arr_time_utc: str,
) -> bytes:
    """Build the established validated TRIAS 1.2 StopEvent request."""

    body = f'''<?xml version="1.0" encoding="UTF-8"?>
<Trias xmlns="http://www.vdv.de/trias"
       xmlns:siri="http://www.siri.org.uk/siri"
       version="1.2">
  <ServiceRequest>
    <siri:RequestTimestamp>{request_timestamp_utc}</siri:RequestTimestamp>
    <siri:RequestorRef>CologneTransitIntelligence</siri:RequestorRef>
    <RequestPayload>
      <StopEventRequest>
        <Location>
          <LocationRef>
            <StopPointRef>{stop_point_ref}</StopPointRef>
          </LocationRef>
          <DepArrTime>{dep_arr_time_utc}</DepArrTime>
        </Location>
        <Params>
          <NumberOfResults>5</NumberOfResults>
          <StopEventType>arrival</StopEventType>
          <IncludePreviousCalls>true</IncludePreviousCalls>
          <IncludeOnwardCalls>true</IncludeOnwardCalls>
          <IncludeRealtimeData>true</IncludeRealtimeData>
        </Params>
      </StopEventRequest>
    </RequestPayload>
  </ServiceRequest>
</Trias>
'''
    return body.encode("utf-8")


def perform_mdd_post(
    tracker: AttemptTracker,
    station: MddStation,
    api_key: str,
    raw_directory: Optional[str],
) -> FetchResult:
    """Perform exactly one MDD POST for one station; there is no retry path."""

    source = "MDD_" + station.key.upper()
    tracker.begin(source)
    request_timestamp = utc_now()
    request_timestamp_utc = format_utc(request_timestamp)
    body = build_mdd_request_body(
        station.stop_point_ref,
        request_timestamp_utc,
        request_timestamp_utc,
    )
    request = urllib.request.Request(
        MDD_ENDPOINT,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/xml; charset=utf-8",
            "Accept": "application/json",
            "x-api-key": api_key,
        },
    )
    started = time.perf_counter()
    status: Optional[int] = None
    content_type: Optional[str] = None
    content = b""
    failure: Optional[str] = None
    opener = urllib.request.build_opener(NoRedirectHandler())
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status = int(response.status)
            content_type = response.headers.get("Content-Type")
            content = read_bounded(response, MAX_MDD_RESPONSE_BYTES)
    except urllib.error.HTTPError as error:
        status = int(error.code)
        content_type = error.headers.get("Content-Type") if error.headers else None
        try:
            content = read_bounded(error, MAX_MDD_RESPONSE_BYTES)
        except (OSError, ResponseTooLarge):
            content = b""
        failure = "HTTP_STATUS"
    except (urllib.error.URLError, TimeoutError, OSError, ResponseTooLarge):
        failure = "REQUEST_FAILED"
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    raw_path = write_raw_artifact(
        raw_directory,
        station.key + ".json",
        content,
        secret=api_key,
    )
    return FetchResult(
        source=source,
        method="POST",
        request_timestamp_utc=request_timestamp_utc,
        http_status=status,
        response_content_type=content_type,
        response_bytes=len(content),
        elapsed_ms=elapsed_ms,
        body=content,
        failure=failure,
        raw_artifact_path=raw_path,
    )


def load_gtfs_realtime_bindings() -> Any:
    try:
        from google.transit import gtfs_realtime_pb2
    except ImportError:
        return None
    return gtfs_realtime_pb2


def field_descriptor(message: Any, field_name: str) -> Any:
    descriptor = getattr(message, "DESCRIPTOR", None)
    if descriptor is None:
        return None
    return descriptor.fields_by_name.get(field_name)


def descriptor_is_repeated(field: Any) -> bool:
    repeated = getattr(field, "is_repeated", None)
    if repeated is not None:
        return bool(repeated)
    return getattr(field, "label", None) == 3


def field_is_present(message: Any, field_name: str) -> bool:
    """Return serialized presence without treating a protobuf default as set.

    GTFS-Realtime's optional enum fields have default values.  Reading an
    enum attribute is therefore not a presence test.  Prefer HasField and
    use ListFields only as a compatibility fallback; never compare the
    returned value with the descriptor default.
    """

    field = field_descriptor(message, field_name)
    if field is None:
        return False
    if descriptor_is_repeated(field):
        try:
            return len(getattr(message, field_name)) > 0
        except (AttributeError, TypeError):
            return False
    try:
        return bool(message.HasField(field_name))
    except (AttributeError, TypeError, ValueError):
        try:
            return any(
                descriptor.name == field_name
                for descriptor, _value in message.ListFields()
            )
        except (AttributeError, TypeError, ValueError):
            return False


def get_message(message: Any, field_name: str) -> Any:
    field = field_descriptor(message, field_name)
    if field is None or field.message_type is None or not field_is_present(message, field_name):
        return None
    return getattr(message, field_name)


def get_repeated(message: Any, field_name: str) -> List[Any]:
    field = field_descriptor(message, field_name)
    if field is None or not descriptor_is_repeated(field):
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
    return value if value.strip() else None


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


def enum_value(
    message: Any,
    field_name: str,
    only_if_present: bool = True,
) -> Optional[Tuple[str, int]]:
    field = field_descriptor(message, field_name)
    if field is None or field.enum_type is None:
        return None
    if only_if_present and not field_is_present(message, field_name):
        return None
    try:
        numeric_value = int(getattr(message, field_name))
    except (AttributeError, TypeError, ValueError):
        return None
    enum_item = field.enum_type.values_by_number.get(numeric_value)
    name = enum_item.name if enum_item is not None else "UNRECOGNIZED_%s" % numeric_value
    return name, numeric_value


def epoch_to_iso(seconds: Optional[int]) -> Optional[str]:
    if seconds is None:
        return None
    try:
        return iso_utc(datetime.fromtimestamp(seconds, tz=timezone.utc))
    except (OverflowError, OSError, ValueError):
        return None


def translated_texts(message: Any) -> List[Tuple[str, Optional[str]]]:
    if message is None:
        return []
    values: List[Tuple[str, Optional[str]]] = []
    for translation in get_repeated(message, "translation"):
        text = text_field(translation, "text")
        if text is None:
            continue
        language = text_field(translation, "language")
        values.append((text, language))
    return values


def alert_translated_texts(alert: Any, field_name: str) -> List[Tuple[str, Optional[str]]]:
    """Extract non-empty translated values from a present Alert field."""

    return translated_texts(get_message(alert, field_name))


@dataclass
class AlertAudit:
    entity_index: int
    entity_id: Optional[str]
    cause_explicitly_present: bool
    cause_name: Optional[str]
    cause_numeric_value: Optional[int]
    effect_explicitly_present: bool
    effect_name: Optional[str]
    effect_numeric_value: Optional[int]
    severity_level_explicitly_present: bool
    severity_level_name: Optional[str]
    severity_level_numeric_value: Optional[int]
    cause_detail_explicitly_present: bool
    effect_detail_explicitly_present: bool
    url_explicitly_present: bool
    header_text_explicitly_present: bool
    description_text_explicitly_present: bool
    header_texts: List[Tuple[str, Optional[str]]]
    description_texts: List[Tuple[str, Optional[str]]]
    cause_detail_texts: List[Tuple[str, Optional[str]]]
    effect_detail_texts: List[Tuple[str, Optional[str]]]
    active_period_present: bool
    communication_period_present: bool
    impact_period_present: bool
    informed_entity_present: bool
    informed_entity_count: int
    selectors: List[Dict[str, Optional[str]]]
    scope_types: Set[str]


def alert_selector(selector: Any) -> Dict[str, Optional[str]]:
    trip = get_message(selector, "trip")
    direct_trip_id = text_field(selector, "trip_id")
    trip_id = direct_trip_id or text_field(trip, "trip_id")
    route_id = text_field(selector, "route_id") or text_field(trip, "route_id")
    stop_id = text_field(selector, "stop_id")
    agency_id = text_field(selector, "agency_id")
    return {
        "trip_id": trip_id,
        "route_id": route_id,
        "stop_id": stop_id,
        "agency_id": agency_id,
    }


def scope_types_for_selectors(selectors: Sequence[Dict[str, Optional[str]]]) -> Set[str]:
    types: Set[str] = set()
    for selector in selectors:
        if selector.get("trip_id"):
            types.add("TRIP")
        if selector.get("route_id"):
            types.add("ROUTE")
        if selector.get("stop_id"):
            types.add("STOP")
        if selector.get("agency_id"):
            types.add("AGENCY")
    return types


SCOPE_ORDER = ("TRIP", "ROUTE", "STOP", "AGENCY")


def exact_scope_label(scope_types: Set[str]) -> str:
    ordered = [scope for scope in SCOPE_ORDER if scope in scope_types]
    if not ordered:
        return "NO_IDENTIFYING_SCOPE"
    if len(ordered) == 1:
        return ordered[0] + "_ONLY"
    if len(ordered) == 2:
        return "_".join(ordered)
    return "MULTI_SCOPE"


def exact_scope_set_label(scope_types: Set[str]) -> str:
    ordered = [scope for scope in SCOPE_ORDER if scope in scope_types]
    return "_".join(ordered) if ordered else "NO_IDENTIFYING_SCOPE"


def round_number(value: float) -> float:
    rounded = round(value, 3)
    return int(rounded) if rounded == int(rounded) else rounded


def percentage(numerator: int, denominator: int) -> Optional[float]:
    if denominator == 0:
        return None
    return round_number(100.0 * numerator / denominator)


def percentile(values: Sequence[int], percentile_value: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if percentile_value <= 0:
        return ordered[0]
    if percentile_value >= 100:
        return ordered[-1]
    rank = int((percentile_value / 100.0) * len(ordered) + 0.999999999)
    return ordered[max(0, min(len(ordered) - 1, rank - 1))]


def length_statistics(values: Sequence[int]) -> Dict[str, Any]:
    if not values:
        return {"count": 0, "min": None, "median": None, "p90": None, "max": None}
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        median: Any = ordered[middle]
    else:
        median = round_number((ordered[middle - 1] + ordered[middle]) / 2.0)
    return {
        "count": len(ordered),
        "min": ordered[0],
        "median": median,
        "p90": percentile(ordered, 90),
        "max": ordered[-1],
    }


def language_counts(values: Iterable[Tuple[str, Optional[str]]]) -> Tuple[Dict[str, int], int]:
    counts: Counter[str] = Counter()
    missing_language_count = 0
    for _text, language in values:
        if language and language.strip():
            counts[language.strip()] += 1
        else:
            missing_language_count += 1
    return dict(sorted(counts.items())), missing_language_count


def redact_text(value: Any, secret: Optional[str] = None) -> str:
    text = str(value)
    if secret:
        text = text.replace(secret, "[REDACTED]")
    sanitized: List[str] = []
    for character in text:
        if character in "\r\n\t":
            sanitized.append(" ")
        elif ord(character) < 32 or ord(character) == 127:
            sanitized.append(" ")
        else:
            sanitized.append(character)
    return re.sub(r"\s+", " ", "".join(sanitized)).strip()


def short_text(value: Any, secret: Optional[str] = None) -> Optional[str]:
    if value is None:
        return None
    text = redact_text(value, secret)
    if not text:
        return None
    if len(text) <= MAX_SHORT_TEXT_CHARS:
        return text
    return text[: MAX_SHORT_TEXT_CHARS - 1].rstrip() + "…"


def enum_distribution(
    alerts: Sequence[AlertAudit],
    field_name: str,
) -> List[Dict[str, Any]]:
    counter: Counter[Tuple[str, int]] = Counter()
    for alert in alerts:
        if field_name == "cause":
            name = alert.cause_name
            numeric_value = alert.cause_numeric_value
        elif field_name == "effect":
            name = alert.effect_name
            numeric_value = alert.effect_numeric_value
        elif field_name == "severity_level":
            name = alert.severity_level_name
            numeric_value = alert.severity_level_numeric_value
        else:
            raise ValueError("unsupported alert enum field")
        if name is None or numeric_value is None:
            continue
        counter[(name, numeric_value)] += 1
    all_count = len(alerts)
    present_count = sum(counter.values())
    output: List[Dict[str, Any]] = []
    coverage_field = field_name
    for (name, numeric_value), count in sorted(counter.items(), key=lambda item: item[0]):
        output.append(
            {
                "value": name,
                "numeric_value": numeric_value,
                "count": count,
                "percentage_of_all_alerts": percentage(count, all_count),
                "percentage_of_alerts_with_field": percentage(count, present_count),
                "percentage_of_alerts_with_%s" % coverage_field: percentage(
                    count, present_count
                ),
            }
        )
    return output


def enum_presence_value_matrix(
    alerts: Sequence[AlertAudit],
    field_name: str,
    unknown_name: str,
    other_name: str,
) -> Dict[str, Any]:
    """Separate absent, explicit default, OTHER, and observed known values."""

    present_attribute = field_name + "_explicitly_present"
    name_attribute = field_name + "_name"
    absent_count = 0
    unknown_count = 0
    other_count = 0
    specific_counts: Counter[str] = Counter()
    unrecognized_counts: Counter[str] = Counter()
    for alert in alerts:
        if not getattr(alert, present_attribute):
            absent_count += 1
            continue
        name = getattr(alert, name_attribute)
        if name == unknown_name:
            unknown_count += 1
        elif name == other_name:
            other_count += 1
        elif name is not None and name.startswith("UNRECOGNIZED_"):
            unrecognized_counts[name] += 1
        else:
            specific_counts[name or "UNNAMED_ENUM_VALUE"] += 1

    total = len(alerts)
    rows = [
        {
            "category": "FIELD_ABSENT",
            "count": absent_count,
            "percentage_of_alerts": percentage(absent_count, total),
        },
        {
            "category": "FIELD_PRESENT_UNKNOWN",
            "count": unknown_count,
            "percentage_of_alerts": percentage(unknown_count, total),
        },
        {
            "category": "FIELD_PRESENT_OTHER",
            "count": other_count,
            "percentage_of_alerts": percentage(other_count, total),
        },
        {
            "category": "FIELD_PRESENT_SPECIFIC_KNOWN",
            "count": sum(specific_counts.values()),
            "percentage_of_alerts": percentage(sum(specific_counts.values()), total),
            "observed_values": [
                {
                    "value": name,
                    "count": count,
                    "percentage_of_alerts": percentage(count, total),
                }
                for name, count in sorted(specific_counts.items())
            ],
        },
    ]
    return {
        "field_name": field_name,
        "unknown_enum_name": unknown_name,
        "other_enum_name": other_name,
        "matrix": rows,
        "unrecognized_values": [
            {
                "value": name,
                "count": count,
                "percentage_of_alerts": percentage(count, total),
            }
            for name, count in sorted(unrecognized_counts.items())
        ],
    }


def normalized_source_text(value: Any) -> str:
    """Normalize only outer and repeated whitespace for exact text statistics."""

    return re.sub(r"\s+", " ", str(value).strip())


def alert_text_attribute(field_name: str) -> str:
    return {
        "header_text": "header_texts",
        "description_text": "description_texts",
        "cause_detail": "cause_detail_texts",
        "effect_detail": "effect_detail_texts",
    }[field_name]


def text_frequency_statistics(
    alerts: Sequence[AlertAudit],
    field_name: str,
    top_n: int = 20,
) -> Dict[str, Any]:
    """Count each normalized text once per alert and retain source wording."""

    counts: Counter[str] = Counter()
    for alert in alerts:
        values = getattr(alert, alert_text_attribute(field_name))
        per_alert_values = {
            normalized_source_text(text)
            for text, _language in values
            if normalized_source_text(text)
        }
        counts.update(per_alert_values)
    total_alerts = len(alerts)
    top_values = [
        {
            "text": text,
            "count": count,
            "percentage_of_alerts": percentage(count, total_alerts),
        }
        for text, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[
            :top_n
        ]
    ]
    return {
        "distinct_%s_count" % field_name: len(counts),
        "top_%d_%ss" % (top_n, field_name): top_values,
    }


def non_provenance_description_values(alert: AlertAudit) -> Set[str]:
    return {
        normalized_source_text(text)
        for text, _language in alert.description_texts
        if normalized_source_text(text)
        and not normalized_source_text(text).startswith(
            GTFSDE_PROVENANCE_DESCRIPTION_PREFIX
        )
    }


def non_provenance_description_statistics(
    alerts: Sequence[AlertAudit],
    top_n: int = 20,
) -> Dict[str, Any]:
    """Count non-provenance description values without classifying their meaning."""

    counts: Counter[str] = Counter()
    non_provenance_alert_count = 0
    for alert in alerts:
        values = non_provenance_description_values(alert)
        if values:
            non_provenance_alert_count += 1
            counts.update(values)
    top_values = [
        {
            "text": text,
            "count": count,
            "percentage_of_alerts": percentage(count, len(alerts)),
            "percentage_of_non_provenance_alerts": percentage(
                count, non_provenance_alert_count
            ),
        }
        for text, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[
            :top_n
        ]
    ]
    return {
        "non_provenance_description_alert_count": non_provenance_alert_count,
        "non_provenance_description_value_count": sum(counts.values()),
        "distinct_non_provenance_description_text_count": len(counts),
        "top_%d_non_provenance_description_texts" % top_n: top_values,
    }


def known_unknown_cause_counts(alerts: Sequence[AlertAudit]) -> Dict[str, int]:
    unknown_count = sum(
        alert.cause_explicitly_present and alert.cause_name == "UNKNOWN_CAUSE"
        for alert in alerts
    )
    known_or_other_count = sum(
        alert.cause_explicitly_present and alert.cause_name != "UNKNOWN_CAUSE"
        for alert in alerts
    )
    specific_known_count = sum(
        alert.cause_explicitly_present
        and alert.cause_name not in {"UNKNOWN_CAUSE", "OTHER_CAUSE"}
        for alert in alerts
    )
    return {
        "cause_field_absent_count": len(alerts)
        - sum(alert.cause_explicitly_present for alert in alerts),
        "unknown_cause_count": unknown_count,
        "known_or_other_cause_count": known_or_other_count,
        "specific_known_cause_count": specific_known_count,
    }


def translation_quality_statistics(
    alerts: Sequence[AlertAudit],
    field_name: str,
) -> Dict[str, Any]:
    values = [
        (text, language)
        for alert in alerts
        for text, language in getattr(alert, alert_text_attribute(field_name))
    ]
    languages, missing_language_count = language_counts(values)
    return {
        "translation_count": len(values),
        "language_counts": languages,
        "without_explicit_language_count": missing_language_count,
        "character_length_statistics": length_statistics(
            [len(text) for text, _language in values]
        ),
        "max_sanitized_examples": [
            short_text(text) for text, _language in values[:MAX_TEXT_EXAMPLES]
        ],
    }


def alert_field_support(alert: Any, field_names: Sequence[str]) -> Dict[str, bool]:
    return {
        field_name: field_descriptor(alert, field_name) is not None
        for field_name in field_names
    }


def alert_text_examples(
    alerts: Sequence[AlertAudit], secret: Optional[str] = None
) -> List[Dict[str, Any]]:
    examples: List[Dict[str, Any]] = []
    for alert in alerts:
        if not alert.header_texts and not alert.description_texts:
            continue
        examples.append(
            {
                "alert_entity_id": short_text(alert.entity_id, secret),
                "cause": alert.cause_name,
                "effect": alert.effect_name,
                "entity_scope": sorted(alert.scope_types),
                "scope_combination": exact_scope_label(alert.scope_types),
                "informed_entity_count": alert.informed_entity_count,
                "header_text": short_text(
                    alert.header_texts[0][0] if alert.header_texts else None,
                    secret,
                ),
                "description_text": short_text(
                    alert.description_texts[0][0]
                    if alert.description_texts
                    else None,
                    secret,
                ),
            }
        )
        if len(examples) >= MAX_TEXT_EXAMPLES:
            break
    return examples


def analyze_feed(feed: Any, request_timestamp: datetime) -> Tuple[Dict[str, Any], List[AlertAudit], Dict[str, Any]]:
    entities = get_repeated(feed, "entity")
    alerts: List[AlertAudit] = []
    entity_type_counts: Counter[str] = Counter()
    alert_field_names = (
        "active_period",
        "communication_period",
        "impact_period",
        "informed_entity",
        "cause",
        "effect",
        "severity_level",
        "cause_detail",
        "effect_detail",
        "url",
        "header_text",
        "description_text",
    )
    field_support: Dict[str, bool] = {}
    for entity_index, entity in enumerate(entities):
        present_types: List[str] = []
        for field_name in ("alert", "trip_update", "vehicle"):
            if get_message(entity, field_name) is not None:
                present_types.append(field_name)
        if not present_types:
            entity_type_counts["other"] += 1
        else:
            for field_name in present_types:
                entity_type_counts[field_name] += 1
        alert = get_message(entity, "alert")
        if alert is None:
            continue
        if not field_support:
            field_support = alert_field_support(alert, alert_field_names)
        selectors = [alert_selector(item) for item in get_repeated(alert, "informed_entity")]
        scope_types = scope_types_for_selectors(selectors)
        cause = enum_value(alert, "cause")
        effect = enum_value(alert, "effect")
        severity_level = enum_value(alert, "severity_level")
        header_text_values = alert_translated_texts(alert, "header_text")
        description_text_values = alert_translated_texts(alert, "description_text")
        cause_detail_text_values = alert_translated_texts(alert, "cause_detail")
        effect_detail_text_values = alert_translated_texts(alert, "effect_detail")
        alerts.append(
            AlertAudit(
                entity_index=entity_index,
                entity_id=text_field(entity, "id"),
                cause_explicitly_present=field_is_present(alert, "cause"),
                cause_name=cause[0] if cause is not None else None,
                cause_numeric_value=cause[1] if cause is not None else None,
                effect_explicitly_present=field_is_present(alert, "effect"),
                effect_name=effect[0] if effect is not None else None,
                effect_numeric_value=effect[1] if effect is not None else None,
                severity_level_explicitly_present=field_is_present(
                    alert, "severity_level"
                ),
                severity_level_name=(
                    severity_level[0] if severity_level is not None else None
                ),
                severity_level_numeric_value=(
                    severity_level[1] if severity_level is not None else None
                ),
                cause_detail_explicitly_present=field_is_present(
                    alert, "cause_detail"
                ),
                effect_detail_explicitly_present=field_is_present(
                    alert, "effect_detail"
                ),
                url_explicitly_present=field_is_present(alert, "url"),
                header_text_explicitly_present=field_is_present(
                    alert, "header_text"
                ),
                description_text_explicitly_present=field_is_present(
                    alert, "description_text"
                ),
                header_texts=header_text_values,
                description_texts=description_text_values,
                cause_detail_texts=cause_detail_text_values,
                effect_detail_texts=effect_detail_text_values,
                active_period_present=field_is_present(alert, "active_period"),
                communication_period_present=field_is_present(
                    alert, "communication_period"
                ),
                impact_period_present=field_is_present(alert, "impact_period"),
                informed_entity_present=field_is_present(alert, "informed_entity"),
                informed_entity_count=len(selectors),
                selectors=selectors,
                scope_types=scope_types,
            )
        )

    total_alert_count = len(alerts)
    cause_present_count = sum(
        alert.cause_explicitly_present for alert in alerts
    )
    effect_present_count = sum(
        alert.effect_explicitly_present for alert in alerts
    )
    severity_level_present_count = sum(
        alert.severity_level_explicitly_present for alert in alerts
    )
    cause_detail_present_count = sum(
        alert.cause_detail_explicitly_present for alert in alerts
    )
    effect_detail_present_count = sum(
        alert.effect_detail_explicitly_present for alert in alerts
    )
    url_present_count = sum(alert.url_explicitly_present for alert in alerts)
    header_present_count = sum(
        alert.header_text_explicitly_present for alert in alerts
    )
    description_present_count = sum(
        alert.description_text_explicitly_present for alert in alerts
    )
    header_nonempty_text_count = sum(bool(alert.header_texts) for alert in alerts)
    description_nonempty_text_count = sum(
        bool(alert.description_texts) for alert in alerts
    )
    active_period_present_count = sum(alert.active_period_present for alert in alerts)
    communication_period_present_count = sum(
        alert.communication_period_present for alert in alerts
    )
    impact_period_present_count = sum(
        alert.impact_period_present for alert in alerts
    )
    informed_entity_present_count = sum(
        alert.informed_entity_present for alert in alerts
    )
    all_header_values = [value for alert in alerts for value in alert.header_texts]
    all_description_values = [
        value for alert in alerts for value in alert.description_texts
    ]
    header_language_counts, header_missing_language_count = language_counts(all_header_values)
    description_language_counts, description_missing_language_count = language_counts(
        all_description_values
    )
    scope_type_alert_counts: Counter[str] = Counter()
    scope_combination_counts: Counter[str] = Counter()
    scope_exact_combination_counts: Counter[str] = Counter()
    for alert in alerts:
        for scope_type in alert.scope_types:
            scope_type_alert_counts[scope_type] += 1
        scope_combination_counts[exact_scope_label(alert.scope_types)] += 1
        scope_exact_combination_counts[exact_scope_set_label(alert.scope_types)] += 1

    header_lengths = [len(text) for text, _language in all_header_values]
    description_lengths = [len(text) for text, _language in all_description_values]
    header_coverage = percentage(header_present_count, total_alert_count)
    description_coverage = percentage(description_present_count, total_alert_count)
    feed_header = get_message(feed, "header")
    feed_header_timestamp_seconds = optional_int_field(feed_header, "timestamp")
    feed_header_timestamp = epoch_to_iso(feed_header_timestamp_seconds)
    feed_age_seconds: Optional[float] = None
    if feed_header_timestamp_seconds is not None:
        try:
            feed_age_seconds = round_number(
                (
                    request_timestamp
                    - datetime.fromtimestamp(
                        feed_header_timestamp_seconds, tz=timezone.utc
                    )
                ).total_seconds()
            )
        except (OverflowError, OSError, ValueError):
            feed_age_seconds = None
    repeated_field_counts = {
        "active_period": active_period_present_count,
        "communication_period": communication_period_present_count,
        "impact_period": impact_period_present_count,
        "informed_entity": informed_entity_present_count,
    }
    repeated_field_presence = {
        field_name: {
            "supported_by_installed_binding": field_support.get(field_name, False),
            "non_zero_length_alert_count": count,
            "zero_length_alert_count": total_alert_count - count,
            "percentage_of_alerts": percentage(count, total_alert_count),
        }
        for field_name, count in repeated_field_counts.items()
    }
    cause_matrix = enum_presence_value_matrix(
        alerts, "cause", "UNKNOWN_CAUSE", "OTHER_CAUSE"
    )
    effect_matrix = enum_presence_value_matrix(
        alerts, "effect", "UNKNOWN_EFFECT", "OTHER_EFFECT"
    )
    text_frequencies: Dict[str, Any] = {}
    for field_name in (
        "header_text",
        "description_text",
        "cause_detail",
        "effect_detail",
    ):
        text_frequencies.update(text_frequency_statistics(alerts, field_name))
    gtfsde_provenance_description_count = sum(
        any(
            normalized_source_text(text).startswith(
                GTFSDE_PROVENANCE_DESCRIPTION_PREFIX
            )
            for text, _language in alert.description_texts
        )
        for alert in alerts
    )
    summary: Dict[str, Any] = {
        "audit_status": "PARSED",
        "gtfs_realtime_version": text_field(feed_header, "gtfs_realtime_version"),
        "feed_header_timestamp_utc": feed_header_timestamp,
        "feed_header_age_seconds_at_request": feed_age_seconds,
        "feed_entity_count": len(entities),
        "entity_type_counts": dict(sorted(entity_type_counts.items())),
        "total_alert_count": total_alert_count,
        "cause_explicitly_present_count": cause_present_count,
        "cause_explicitly_absent_count": total_alert_count - cause_present_count,
        "cause_present_count": cause_present_count,
        "cause_missing_count": total_alert_count - cause_present_count,
        "effect_explicitly_present_count": effect_present_count,
        "effect_explicitly_absent_count": total_alert_count - effect_present_count,
        "effect_present_count": effect_present_count,
        "effect_missing_count": total_alert_count - effect_present_count,
        "severity_level_explicitly_present_count": severity_level_present_count,
        "severity_level_explicitly_absent_count": total_alert_count
        - severity_level_present_count,
        "cause_detail_explicitly_present_count": cause_detail_present_count,
        "cause_detail_explicitly_absent_count": total_alert_count
        - cause_detail_present_count,
        "effect_detail_explicitly_present_count": effect_detail_present_count,
        "effect_detail_explicitly_absent_count": total_alert_count
        - effect_detail_present_count,
        "url_explicitly_present_count": url_present_count,
        "url_explicitly_absent_count": total_alert_count - url_present_count,
        "header_text_explicitly_present_count": header_present_count,
        "header_text_explicitly_absent_count": total_alert_count - header_present_count,
        "description_text_explicitly_present_count": description_present_count,
        "description_text_explicitly_absent_count": total_alert_count
        - description_present_count,
        "header_text_present_count": header_present_count,
        "header_text_missing_count": total_alert_count - header_present_count,
        "description_text_present_count": description_present_count,
        "description_text_missing_count": total_alert_count - description_present_count,
        "header_text_nonempty_translation_alert_count": header_nonempty_text_count,
        "description_text_nonempty_translation_alert_count": description_nonempty_text_count,
        "field_support": field_support,
        "repeated_field_presence": repeated_field_presence,
        "active_period_present_count": active_period_present_count,
        "active_period_missing_count": total_alert_count - active_period_present_count,
        "informed_entity_count": sum(alert.informed_entity_count for alert in alerts),
        "alerts_with_trip_scope": sum("TRIP" in alert.scope_types for alert in alerts),
        "alerts_with_route_scope": sum("ROUTE" in alert.scope_types for alert in alerts),
        "alerts_with_stop_scope": sum("STOP" in alert.scope_types for alert in alerts),
        "alerts_with_agency_scope": sum("AGENCY" in alert.scope_types for alert in alerts),
        "alerts_with_multiple_scope_types": sum(
            len(alert.scope_types) > 1 for alert in alerts
        ),
        "scope_type_alert_counts": dict(sorted(scope_type_alert_counts.items())),
        "scope_combination_counts": dict(sorted(scope_combination_counts.items())),
        "scope_exact_combination_counts": dict(
            sorted(scope_exact_combination_counts.items())
        ),
        "coverage_percentages": {
            "cause": percentage(cause_present_count, total_alert_count),
            "effect": percentage(effect_present_count, total_alert_count),
            "severity_level": percentage(
                severity_level_present_count, total_alert_count
            ),
            "cause_detail": percentage(cause_detail_present_count, total_alert_count),
            "effect_detail": percentage(
                effect_detail_present_count, total_alert_count
            ),
            "url": percentage(url_present_count, total_alert_count),
            "header_text": header_coverage,
            "description_text": description_coverage,
            "active_period": percentage(active_period_present_count, total_alert_count),
            "communication_period": percentage(
                communication_period_present_count, total_alert_count
            ),
            "impact_period": percentage(
                impact_period_present_count, total_alert_count
            ),
            "informed_entity": percentage(
                informed_entity_present_count, total_alert_count
            ),
        },
        "cause_presence_value_matrix": cause_matrix,
        "effect_presence_value_matrix": effect_matrix,
        "severity_level_distribution": enum_distribution(alerts, "severity_level"),
        "header_language_counts": header_language_counts,
        "header_text_without_explicit_language_count": header_missing_language_count,
        "description_language_counts": description_language_counts,
        "description_text_without_explicit_language_count": description_missing_language_count,
        "header_text_character_length_statistics": length_statistics(header_lengths),
        "description_text_character_length_statistics": length_statistics(
            description_lengths
        ),
        "cause_detail": {
            "source_label": "SOURCE-REPORTED CAUSE DETAIL",
            "supported_by_installed_binding": field_support.get("cause_detail", False),
            "explicit_field_presence_count": cause_detail_present_count,
            **translation_quality_statistics(alerts, "cause_detail"),
        },
        "effect_detail": {
            "source_label": "SOURCE-REPORTED EFFECT DETAIL",
            "supported_by_installed_binding": field_support.get("effect_detail", False),
            "explicit_field_presence_count": effect_detail_present_count,
            **translation_quality_statistics(alerts, "effect_detail"),
        },
        "gtfsde_provenance_description_count": gtfsde_provenance_description_count,
        "gtfsde_provenance_description_percentage": percentage(
            gtfsde_provenance_description_count, total_alert_count
        ),
        **non_provenance_description_statistics(alerts),
        **text_frequencies,
        "max_sanitized_short_examples": alert_text_examples(alerts),
    }

    local_trip_ids: Set[str] = set()
    local_route_ids: Set[str] = set()
    local_stop_time_update_count = 0
    matching_trip_update_count = 0
    trip_ids_without_value_count = 0
    for entity in entities:
        trip_update = get_message(entity, "trip_update")
        if trip_update is None:
            continue
        trip = get_message(trip_update, "trip")
        trip_id = text_field(trip, "trip_id")
        route_id = text_field(trip, "route_id")
        matched_this_trip = False
        for stop_time_update in get_repeated(trip_update, "stop_time_update"):
            if text_field(stop_time_update, "stop_id") not in NEUMARKT_STOP_IDS:
                continue
            matched_this_trip = True
            local_stop_time_update_count += 1
            if trip_id:
                local_trip_ids.add(trip_id)
            else:
                trip_ids_without_value_count += 1
            if route_id:
                local_route_ids.add(route_id)
        if matched_this_trip:
            matching_trip_update_count += 1

    local_context = {
        "neumarkt_trip_count": len(local_trip_ids),
        "neumarkt_stop_time_update_count": local_stop_time_update_count,
        "neumarkt_matching_trip_update_count": matching_trip_update_count,
        "neumarkt_trip_id_missing_on_matching_update_count": trip_ids_without_value_count,
        "neumarkt_route_count_if_source_reported": len(local_route_ids),
        "neumarkt_trip_id_examples": sorted(local_trip_ids)[:MAX_TEXT_EXAMPLES],
        "neumarkt_route_id_examples": sorted(local_route_ids)[:MAX_TEXT_EXAMPLES],
        "neumarkt_stop_ids_used": sorted(NEUMARKT_STOP_IDS),
        "identity_use": "CURRENT_SNAPSHOT_DIAGNOSTIC_ONLY",
        "trip_ids": local_trip_ids,
        "route_ids": local_route_ids,
    }
    return summary, alerts, local_context


def empty_feed_summary(status: str) -> Dict[str, Any]:
    return {
        "audit_status": status,
        "feed_entity_count": 0,
        "entity_type_counts": {},
        "total_alert_count": 0,
        "cause_explicitly_present_count": 0,
        "cause_explicitly_absent_count": 0,
        "cause_present_count": 0,
        "cause_missing_count": 0,
        "effect_explicitly_present_count": 0,
        "effect_explicitly_absent_count": 0,
        "effect_present_count": 0,
        "effect_missing_count": 0,
        "severity_level_explicitly_present_count": 0,
        "severity_level_explicitly_absent_count": 0,
        "cause_detail_explicitly_present_count": 0,
        "cause_detail_explicitly_absent_count": 0,
        "effect_detail_explicitly_present_count": 0,
        "effect_detail_explicitly_absent_count": 0,
        "url_explicitly_present_count": 0,
        "url_explicitly_absent_count": 0,
        "header_text_explicitly_present_count": 0,
        "header_text_explicitly_absent_count": 0,
        "description_text_explicitly_present_count": 0,
        "description_text_explicitly_absent_count": 0,
        "header_text_present_count": 0,
        "header_text_missing_count": 0,
        "description_text_present_count": 0,
        "description_text_missing_count": 0,
        "header_text_nonempty_translation_alert_count": 0,
        "description_text_nonempty_translation_alert_count": 0,
        "field_support": {},
        "repeated_field_presence": {},
        "active_period_present_count": 0,
        "active_period_missing_count": 0,
        "informed_entity_count": 0,
        "alerts_with_trip_scope": 0,
        "alerts_with_route_scope": 0,
        "alerts_with_stop_scope": 0,
        "alerts_with_agency_scope": 0,
        "alerts_with_multiple_scope_types": 0,
        "scope_type_alert_counts": {},
        "scope_combination_counts": {},
        "scope_exact_combination_counts": {},
        "coverage_percentages": {
            "cause": None,
            "effect": None,
            "severity_level": None,
            "cause_detail": None,
            "effect_detail": None,
            "url": None,
            "header_text": None,
            "description_text": None,
            "active_period": None,
            "communication_period": None,
            "impact_period": None,
            "informed_entity": None,
        },
        "cause_presence_value_matrix": {},
        "effect_presence_value_matrix": {},
        "severity_level_distribution": [],
        "header_language_counts": {},
        "header_text_without_explicit_language_count": 0,
        "description_language_counts": {},
        "description_text_without_explicit_language_count": 0,
        "header_text_character_length_statistics": length_statistics([]),
        "description_text_character_length_statistics": length_statistics([]),
        "cause_detail": {
            "source_label": "SOURCE-REPORTED CAUSE DETAIL",
            "supported_by_installed_binding": False,
            "explicit_field_presence_count": 0,
            **translation_quality_statistics([], "cause_detail"),
        },
        "effect_detail": {
            "source_label": "SOURCE-REPORTED EFFECT DETAIL",
            "supported_by_installed_binding": False,
            "explicit_field_presence_count": 0,
            **translation_quality_statistics([], "effect_detail"),
        },
        "gtfsde_provenance_description_count": 0,
        "gtfsde_provenance_description_percentage": None,
        "non_provenance_description_alert_count": 0,
        "non_provenance_description_value_count": 0,
        "distinct_non_provenance_description_text_count": 0,
        "top_20_non_provenance_description_texts": [],
        "distinct_header_text_count": 0,
        "distinct_description_text_count": 0,
        "distinct_cause_detail_count": 0,
        "distinct_effect_detail_count": 0,
        "top_20_header_texts": [],
        "top_20_description_texts": [],
        "top_20_cause_details": [],
        "top_20_effect_details": [],
        "max_sanitized_short_examples": [],
    }


def cause_effect_distribution_for_alerts(
    alerts: Sequence[AlertAudit],
) -> Dict[str, Any]:
    cause_present_count = sum(
        alert.cause_explicitly_present for alert in alerts
    )
    effect_present_count = sum(
        alert.effect_explicitly_present for alert in alerts
    )
    return {
        "linked_alert_count": len(alerts),
        "cause_present_count": cause_present_count,
        "cause_missing_count": len(alerts) - cause_present_count,
        "effect_present_count": effect_present_count,
        "effect_missing_count": len(alerts) - effect_present_count,
        "cause_distribution": enum_distribution(alerts, "cause"),
        "effect_distribution": enum_distribution(alerts, "effect"),
    }


def local_neumarkt_summary(
    alerts: Sequence[AlertAudit],
    local_context: Dict[str, Any],
) -> Dict[str, Any]:
    trip_ids = local_context.get("trip_ids", set())
    route_ids = local_context.get("route_ids", set())
    linked_alerts: List[AlertAudit] = []
    trip_link_count = 0
    stop_link_count = 0
    route_link_count = 0
    link_type_counts: Counter[str] = Counter()
    direct_link_type_sets: List[Set[str]] = []
    trip_linked_alerts: List[AlertAudit] = []
    for alert in alerts:
        link_types_set: Set[str] = set()
        if any(
            selector.get("trip_id") in trip_ids
            for selector in alert.selectors
            if selector.get("trip_id")
        ):
            link_types_set.add("TRIP")
        if any(
            selector.get("stop_id") in NEUMARKT_STOP_IDS
            for selector in alert.selectors
            if selector.get("stop_id")
        ):
            link_types_set.add("STOP")
        if bool(route_ids) and any(
            selector.get("route_id") in route_ids
            for selector in alert.selectors
            if selector.get("route_id")
        ):
            link_types_set.add("ROUTE")
        direct_link_type_sets.append(link_types_set)
        link_types = [
            link_type for link_type in ("TRIP", "STOP", "ROUTE") if link_type in link_types_set
        ]
        if "TRIP" in link_types_set:
            trip_link_count += 1
            trip_linked_alerts.append(alert)
        if "STOP" in link_types_set:
            stop_link_count += 1
        if "ROUTE" in link_types_set:
            route_link_count += 1
        if link_types:
            linked_alerts.append(alert)
            link_type_counts["_".join(link_types)] += 1

    linked_distribution = cause_effect_distribution_for_alerts(linked_alerts)
    linked_non_provenance_alerts = [
        alert for alert in linked_alerts if non_provenance_description_values(alert)
    ]
    trip_linked_non_provenance_alerts = [
        alert
        for alert in trip_linked_alerts
        if non_provenance_description_values(alert)
    ]
    return {
        "neumarkt_trip_count": local_context["neumarkt_trip_count"],
        "neumarkt_stop_time_update_count": local_context[
            "neumarkt_stop_time_update_count"
        ],
        "alerts_exact_trip_link_count": trip_link_count,
        "alerts_exact_stop_link_count": stop_link_count,
        "alerts_route_context_count_if_testable": route_link_count,
        "alerts_with_any_direct_link_count": len(linked_alerts),
        "alerts_with_multiple_direct_link_types": sum(
            len(link_types) > 1 for link_types in direct_link_type_sets
        ),
        "route_context_testable": bool(route_ids),
        "route_context_note": (
            "Only route_ids reported by Neumarkt-matching TripUpdates were used."
            if route_ids
            else "No source-reported route_id was available on matching TripUpdates."
        ),
        "neumarkt_route_id_examples": sorted(route_ids)[:MAX_TEXT_EXAMPLES],
        "link_type_combination_counts": dict(sorted(link_type_counts.items())),
        "linked_alert_cause_distribution": linked_distribution["cause_distribution"],
        "linked_alert_effect_distribution": linked_distribution["effect_distribution"],
        "linked_alert_cause_present_count": linked_distribution["cause_present_count"],
        "linked_alert_cause_missing_count": linked_distribution["cause_missing_count"],
        "linked_alert_known_vs_unknown_cause_counts": known_unknown_cause_counts(
            linked_alerts
        ),
        "linked_alert_effect_present_count": linked_distribution["effect_present_count"],
        "linked_alert_effect_missing_count": linked_distribution["effect_missing_count"],
        "linked_alerts_with_non_provenance_description_text": len(
            linked_non_provenance_alerts
        ),
        "trip_linked_alerts_with_non_provenance_description_text": len(
            trip_linked_non_provenance_alerts
        ),
        "identity_use": local_context["identity_use"],
    }


def normalized_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def path_for_key(parent_path: str, key: Any) -> str:
    key_text = str(key)
    if key_text and all(character.isalnum() or character == "_" for character in key_text):
        return parent_path + "." + key_text
    return parent_path + "[" + json.dumps(key_text, ensure_ascii=False) + "]"


def json_value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return type(value).__name__


def iter_dict_entries_with_context(
    value: Any,
    path: str = "$",
) -> Iterator[Tuple[str, str, Any, str, Any]]:
    """Yield object-key paths with the containing object for structural audits."""

    if isinstance(value, dict):
        for key, child in value.items():
            child_path = path_for_key(path, key)
            yield child_path, str(key), child, path, value
            for item in iter_dict_entries_with_context(child, child_path):
                yield item
    elif isinstance(value, list):
        list_path = path + "[*]"
        for child in value:
            for item in iter_dict_entries_with_context(child, list_path):
                yield item


def iter_dict_entries(
    value: Any,
    path: str = "$",
) -> Iterator[Tuple[str, str, Any]]:
    for child_path, key, child, _parent_path, _parent in iter_dict_entries_with_context(
        value, path
    ):
        yield child_path, key, child


def path_component_names(path: str) -> List[str]:
    return [normalized_key(component) for component in re.findall(r"[A-Za-z0-9_]+", path)]


def flatten_scalar_values(value: Any) -> Iterator[Any]:
    if isinstance(value, dict):
        for child in value.values():
            for item in flatten_scalar_values(child):
                yield item
    elif isinstance(value, list):
        for child in value:
            for item in flatten_scalar_values(child):
                yield item
    elif value is not None:
        yield value


def json_path_inventory(value: Any, path: str = "$") -> List[Dict[str, Any]]:
    """Inventory every observed object key path and its value types."""

    inventory: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"occurrence_count": 0, "value_types": Counter()}
    )
    for child_path, _key, child, _parent_path, _parent in iter_dict_entries_with_context(
        value, path
    ):
        record = inventory[child_path]
        record["occurrence_count"] += 1
        record["value_types"][json_value_type(child)] += 1
    output: List[Dict[str, Any]] = []
    for path in sorted(inventory):
        record = inventory[path]
        value_types = dict(sorted(record["value_types"].items()))
        output.append(
            {
                "path": path,
                "occurrence_count": record["occurrence_count"],
                "value_type": (
                    next(iter(value_types))
                    if len(value_types) == 1
                    else "MIXED"
                ),
                "value_types": value_types,
            }
        )
    return output


def path_inventory_by_path(
    inventory: Sequence[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    return {record["path"]: record for record in inventory}


def field_semantic_category(key: str, path: str) -> str:
    """Apply only source-field semantics that are explicit in the field name."""

    normalized = normalized_key(key)
    if "textualcontent" in path_component_names(path):
        return "GENERAL_INFORMATION_TEXT"
    if "error" in normalized:
        return "UNCLASSIFIED_SOURCE_TEXT"
    if normalized in {"reason", "reasongroup", "reasoncontent", "reasonname"}:
        return "REASON_TEXT"
    if normalized in {"effect", "effecttext", "impact", "consequence"}:
        return "EFFECT_TEXT"
    if normalized in {"advice", "advicetext"}:
        return "ADVICE_TEXT"
    if normalized in {"textualcontent", "customerinformation"}:
        return "GENERAL_INFORMATION_TEXT"
    return "UNCLASSIFIED_SOURCE_TEXT"


def reason_like_field_inventory(
    value: Any,
    secret: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Discover candidate reason/information fields without semantic guessing."""

    inventory: Dict[str, Dict[str, Any]] = {}
    for path, key, child, _parent_path, _parent in iter_dict_entries_with_context(value):
        components = path_component_names(path)
        matched_terms = sorted(
            {
                term
                for term in MDD_REASON_FIELD_TERMS
                if any(term in component for component in components)
            }
        )
        if not matched_terms:
            continue
        record = inventory.setdefault(
            path,
            {
                "path": path,
                "occurrence_count": 0,
                "value_types": Counter(),
                "matched_terms": set(),
                "sanitized_value_examples": [],
                "field_name": key,
                "semantic_category": field_semantic_category(key, path),
            },
        )
        record["occurrence_count"] += 1
        record["value_types"][json_value_type(child)] += 1
        record["matched_terms"].update(matched_terms)
        existing_examples = record["sanitized_value_examples"]
        if len(existing_examples) < MAX_MDD_FIELD_EXAMPLES:
            for scalar in flatten_scalar_values(child):
                example = short_text(scalar, secret)
                if example is None:
                    continue
                if example not in existing_examples:
                    existing_examples.append(example)
                if len(existing_examples) >= MAX_MDD_FIELD_EXAMPLES:
                    break

    output: List[Dict[str, Any]] = []
    for path in sorted(inventory):
        record = inventory[path]
        value_types = dict(sorted(record["value_types"].items()))
        output.append(
            {
                "path": path,
                "field_name": record["field_name"],
                "occurrence_count": record["occurrence_count"],
                "value_type": (
                    next(iter(value_types))
                    if len(value_types) == 1
                    else "MIXED"
                ),
                "value_types": value_types,
                "matched_terms": sorted(record["matched_terms"]),
                "semantic_category": record["semantic_category"],
                "sanitized_value_examples": record["sanitized_value_examples"],
            }
        )
    return output


def direct_named_values(value: Any, names: Set[str]) -> List[Any]:
    if not isinstance(value, dict):
        return []
    return [child for key, child in value.items() if normalized_key(key) in names]


def direct_named_value(value: Any, names: Set[str]) -> Any:
    values = direct_named_values(value, names)
    return values[0] if values else None


def source_scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        if isinstance(value, str) and not value.strip():
            return None
        return value
    if isinstance(value, dict):
        for name in ({"value"}, {"text"}, {"name"}, {"id"}):
            child = direct_named_value(value, name)
            if child is not None:
                extracted = source_scalar(child)
                if extracted is not None:
                    return extracted
        return None
    if isinstance(value, list):
        for child in value:
            extracted = source_scalar(child)
            if extracted is not None:
                return extracted
    return None


def source_text_values(
    value: Any,
    inherited_language: Optional[str] = None,
) -> List[Tuple[str, Optional[str]]]:
    if value is None:
        return []
    if isinstance(value, str):
        return [(value, inherited_language)] if value.strip() else []
    if isinstance(value, (int, float, bool)):
        return [(str(value), inherited_language)]
    if isinstance(value, list):
        output: List[Tuple[str, Optional[str]]] = []
        for child in value:
            output.extend(source_text_values(child, inherited_language))
        return output
    if not isinstance(value, dict):
        return []
    language_value = direct_named_value(value, {"language", "lang"})
    language_scalar = source_scalar(language_value)
    language = str(language_scalar) if language_scalar is not None else inherited_language
    for name in ({"value"}, {"text"}, {"name"}):
        child = direct_named_value(value, name)
        if child is not None:
            return source_text_values(child, language)
    return []


def as_object_list(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def is_populated_source_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def source_value_is_true(value: Any) -> bool:
    scalar = source_scalar(value)
    if scalar is True:
        return True
    return isinstance(scalar, str) and scalar.strip().lower() == "true"


def named_object_items(
    path: str,
    child: Any,
) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """Yield non-empty object items while preserving their exact JSON paths."""

    if isinstance(child, dict):
        if child:
            yield path, child
        return
    if isinstance(child, list):
        for index, item in enumerate(child):
            if isinstance(item, dict) and item:
                yield path + "[%d]" % index, item


def direct_attribute_field(
    value: Dict[str, Any],
    object_path: str,
    names: Set[str],
) -> Optional[Tuple[str, Any]]:
    for key, child in value.items():
        if normalized_key(key) in names and is_populated_source_value(child):
            return path_for_key(object_path, key), child
    return None


def sanitized_attribute_value(
    value: Any,
    field_name: str,
    secret: Optional[str],
) -> Any:
    if field_name in {"Text", "InfoURL"}:
        values = [
            short_text(item, secret)
            for item, _language in source_text_values(value)
            if short_text(item, secret) is not None
        ]
        if not values:
            return None
        return values[0] if len(values) == 1 else values[:MAX_MDD_FIELD_EXAMPLES]
    scalar = source_scalar(value)
    if scalar is not None:
        return short_text(scalar, secret)
    examples = [
        short_text(item, secret)
        for item in flatten_scalar_values(value)
        if short_text(item, secret) is not None
    ]
    return examples[:MAX_MDD_FIELD_EXAMPLES] or None


def collect_general_attribute_records(
    parsed: Any,
    secret: Optional[str],
) -> List[Dict[str, Any]]:
    """Inventory populated TRIAS Attribute/GeneralAttribute objects.

    These are deliberately labelled passenger information attributes.  Their
    presence does not establish a disruption cause.
    """

    output: List[Dict[str, Any]] = []
    field_specs = (
        ("Text", {"text"}),
        ("Code", {"code"}),
        ("AllFacilities", {"allfacilities"}),
        ("Mandatory", {"mandatory"}),
        ("Importance", {"importance"}),
        ("InfoURL", {"infourl"}),
        ("Status", {"status"}),
    )
    for path, key, child, parent_path, parent in iter_dict_entries_with_context(parsed):
        if normalized_key(key) not in MDD_GENERAL_ATTRIBUTE_FIELD_NAMES:
            continue
        for object_path, attribute in named_object_items(path, child):
            if not any(
                is_populated_source_value(field_value)
                for field_value in attribute.values()
            ):
                continue
            fields: Dict[str, Any] = {}
            field_paths: Dict[str, str] = {}
            for field_name, names in field_specs:
                field = direct_attribute_field(attribute, object_path, names)
                if field is None:
                    continue
                field_path, field_value = field
                sanitized = sanitized_attribute_value(field_value, field_name, secret)
                if sanitized is None:
                    continue
                fields[field_name] = sanitized
                field_paths[field_name] = field_path
            output.append(
                {
                    "path": object_path,
                    "field_name": key,
                    "classification": "PASSENGER_INFORMATION_ATTRIBUTE",
                    "present_fields": sorted(fields),
                    "fields": fields,
                    "field_paths": field_paths,
                    "direct_object_keys": sorted(str(name) for name in attribute),
                    "nearest_enclosing_object_path": parent_path,
                    "nearest_enclosing_object_type": path_object_name(parent_path),
                    "sibling_field_names": sorted(
                        str(name) for name in parent if str(name) != str(key)
                    )
                    if isinstance(parent, dict)
                    else [],
                }
            )
    return output


def collect_extension_records(
    parsed: Any,
    secret: Optional[str],
) -> List[Dict[str, Any]]:
    """Inventory populated Extension/provider-extension structures only."""

    output: List[Dict[str, Any]] = []
    for path, key, child, _parent_path, _parent in iter_dict_entries_with_context(parsed):
        if MDD_EXTENSION_FIELD_MARKER not in normalized_key(key):
            continue
        items: List[Tuple[str, Any]] = []
        if isinstance(child, list):
            items = [
                (path + "[%d]" % index, item)
                for index, item in enumerate(child)
                if is_populated_source_value(item)
            ]
        elif is_populated_source_value(child):
            items = [(path, child)]
        for item_path, item in items:
            nested_paths = [
                inventory_item["path"]
                for inventory_item in json_path_inventory(item, item_path)
            ]
            output.append(
                {
                    "path": item_path,
                    "field_name": key,
                    "value_type": json_value_type(item),
                    "direct_object_keys": sorted(str(name) for name in item)
                    if isinstance(item, dict)
                    else [],
                    "nested_populated_paths": nested_paths,
                    "sanitized_value_examples": [
                        short_text(value, secret)
                        for value in list(flatten_scalar_values(item))[
                            :MAX_MDD_FIELD_EXAMPLES
                        ]
                        if short_text(value, secret) is not None
                    ],
                }
            )
    return output


def collect_operational_status_records(parsed: Any) -> List[Dict[str, Any]]:
    """Inventory source-reported operational/status flags set to true."""

    records: List[Dict[str, Any]] = []
    for path, key, child, parent_path, parent in iter_dict_entries_with_context(parsed):
        status_name = MDD_OPERATIONAL_STATUS_FIELDS.get(normalized_key(key))
        if status_name is None or not source_value_is_true(child):
            continue
        records.append(
            {
                "path": path,
                "field_name": key,
                "status_field": status_name,
                "value": True,
                "association_scope": relation_scope_for_reference(path),
                "nearest_enclosing_object_path": parent_path,
                "nearest_enclosing_object_type": path_object_name(parent_path),
                "sibling_field_names": sorted(
                    str(name) for name in parent if str(name) != str(key)
                )
                if isinstance(parent, dict)
                else [],
            }
        )
    return records


def named_object_entries(
    value: Any,
    names: Set[str],
) -> List[Tuple[str, Dict[str, Any]]]:
    entries: List[Tuple[str, Dict[str, Any]]] = []
    for path, key, child in iter_dict_entries(value):
        if normalized_key(key) not in names:
            continue
        objects = as_object_list(child)
        for index, item in enumerate(objects):
            # `iter_dict_entries` yields the collection key itself.  Add the
            # concrete item index once; adding another wildcard would invent
            # a nesting level in the reported source path.
            item_path = path + "[%d]" % index if isinstance(child, list) else path
            entries.append((item_path, item))
    return entries


def named_results(parsed: Any) -> List[Dict[str, Any]]:
    return [
        item
        for _path, item in named_object_entries(parsed, {"stopeventresult"})
    ]


def parse_mdd_json(
    result: FetchResult,
) -> Tuple[Optional[Any], Optional[str]]:
    if result.http_status != 200 or not result.body:
        return None, result.failure or "NO_PARSEABLE_SUCCESS_RESPONSE"
    try:
        return json.loads(decode_response(result.body, result.response_content_type)), None
    except (TypeError, ValueError):
        return None, "JSON_PARSE_ERROR"


def path_object_name(path: str) -> Optional[str]:
    components = re.findall(r"[A-Za-z0-9_]+", path)
    return components[-1] if components else None


def source_identity_key(
    participant_ref: Any,
    situation_number: Any,
    version: Any,
    identifier: Any,
    fallback_value: Any,
) -> Tuple[str, bool, Optional[str], Optional[str], Optional[str], Optional[str]]:
    participant_text = (
        str(participant_ref) if participant_ref is not None else None
    )
    situation_number_text = (
        str(situation_number) if situation_number is not None else None
    )
    version_text = str(version) if version is not None else None
    identifier_text = str(identifier) if identifier is not None else None
    if participant_text and situation_number_text:
        base_key = "PARTICIPANT_AND_SITUATION_NUMBER|%s|%s" % (
            participant_text,
            situation_number_text,
        )
        identity_key = base_key + ("|VERSION|" + version_text if version_text else "")
        return (
            identity_key,
            True,
            participant_text,
            situation_number_text,
            version_text,
            identifier_text,
        )
    if identifier_text:
        return (
            "IDENTIFIER|" + identifier_text,
            True,
            participant_text,
            situation_number_text,
            version_text,
            identifier_text,
        )
    try:
        canonical = json.dumps(fallback_value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        canonical = str(fallback_value)
    return (
        "UNIDENTIFIED|" + canonical,
        False,
        participant_text,
        situation_number_text,
        version_text,
        identifier_text,
    )


def reference_identity(
    value: Any,
    path: str,
) -> Dict[str, Any]:
    participant = None
    situation_number = None
    version = None
    identifier = None
    if isinstance(value, dict):
        participant = source_scalar(direct_named_value(value, {"participantref"}))
        situation_number = source_scalar(
            direct_named_value(value, {"situationnumber"})
        )
        version = source_scalar(direct_named_value(value, {"version"}))
        identifier = source_scalar(
            direct_named_value(value, {"identifier", "id", "value", "ref"})
        )
    else:
        identifier = source_scalar(value)
    (
        identity_key,
        identifiable,
        participant_text,
        situation_number_text,
        version_text,
        identifier_text,
    ) = source_identity_key(
        participant,
        situation_number,
        version,
        identifier,
        value,
    )
    base_key = identity_key
    if "|VERSION|" in base_key:
        base_key = base_key.split("|VERSION|", 1)[0]
    return {
        "identity_key": identity_key,
        "identity_key_without_version": base_key,
        "identifiable": identifiable,
        "participant_ref": participant_text,
        "situation_number": situation_number_text,
        "version": version_text,
        "identifier": identifier_text,
        "path": path,
    }


def reference_origin_for_path(path: str) -> str:
    components = path_component_names(path)
    if any(component in {"errormessage", "errorcode"} for component in components):
        return "ERROR_STRUCTURE"
    if any(
        component in {"situationfullref", "situationref"}
        for component in components
    ):
        return "SITUATION_FULL_REF_OR_SITUATION_REF"
    if "service" in components:
        return "SERVICE_INFORMATION"
    if any(
        component in {"previouscall", "thiscall", "onwardcall", "callatstop"}
        for component in components
    ):
        return "CALL_INFORMATION"
    return "OTHER_SOURCE_STRUCTURE"


def reference_parent_context(
    parent_path: str,
    parent: Any,
    key: str,
) -> Dict[str, Any]:
    sibling_fields: List[str] = []
    if isinstance(parent, dict):
        sibling_fields = sorted(str(name) for name in parent if str(name) != key)
    return {
        "nearest_enclosing_object_path": parent_path,
        "nearest_enclosing_object_type": path_object_name(parent_path),
        "sibling_field_names": sibling_fields,
    }


def collect_reference_records(
    parsed: Any,
    secret: Optional[str] = None,
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for path, key, child, parent_path, parent in iter_dict_entries_with_context(parsed):
        reference_type = MDD_REFERENCE_TYPE_NAMES.get(normalized_key(key))
        if reference_type is None:
            continue
        items: List[Tuple[str, Any]]
        if isinstance(child, list):
            items = [
                (path + "[%d]" % index, item)
                for index, item in enumerate(child)
                if item is not None and item != {} and item != ""
            ]
        elif child is not None and child != {} and child != "":
            items = [(path, child)]
        else:
            items = []
        for item_path, item in items:
            record = {
                "path": item_path,
                "reference_type": reference_type,
                "value_type": json_value_type(item),
                "source_origin": reference_origin_for_path(item_path),
                "sanitized_value_examples": [
                    short_text(scalar, secret)
                    for scalar in list(flatten_scalar_values(item))[:MAX_MDD_FIELD_EXAMPLES]
                    if short_text(scalar, secret) is not None
                ],
            }
            record.update(reference_parent_context(parent_path, parent, key))
            record.update(reference_identity(item, item_path))
            records.append(record)
    return records


def collect_reference_entries(parsed: Any) -> List[Tuple[str, str, Any]]:
    """Return the raw reference-entry view used by older helper callers."""

    entries: List[Tuple[str, str, Any]] = []
    for path, key, child, _parent_path, _parent in iter_dict_entries_with_context(parsed):
        reference_type = MDD_REFERENCE_TYPE_NAMES.get(normalized_key(key))
        if reference_type is None:
            continue
        if isinstance(child, list):
            for index, item in enumerate(child):
                if item is not None and item != {} and item != "":
                    entries.append((path + "[%d]" % index, reference_type, item))
        elif child is not None and child != {} and child != "":
            entries.append((path, reference_type, child))
    return entries


def error_field_inventory(
    parsed: Any,
    secret: Optional[str] = None,
) -> List[Dict[str, Any]]:
    records: Dict[str, Dict[str, Any]] = {}
    for path, key, child, _parent_path, _parent in iter_dict_entries_with_context(parsed):
        if normalized_key(key) not in MDD_ERROR_FIELD_NAMES:
            continue
        scalar_values = [
            short_text(value, secret)
            for value in flatten_scalar_values(child)
            if short_text(value, secret) is not None
        ]
        record = records.setdefault(
            path,
            {
                "path": path,
                "field_name": key,
                "occurrence_count": 0,
                "value_types": Counter(),
                "non_empty_value_count": 0,
                "sanitized_value_examples": [],
            },
        )
        record["occurrence_count"] += 1
        record["value_types"][json_value_type(child)] += 1
        record["non_empty_value_count"] += len(scalar_values)
        for example in scalar_values:
            if example not in record["sanitized_value_examples"]:
                record["sanitized_value_examples"].append(example)
            if len(record["sanitized_value_examples"]) >= MAX_MDD_FIELD_EXAMPLES:
                break
    output: List[Dict[str, Any]] = []
    for path in sorted(records):
        record = records[path]
        value_types = dict(sorted(record["value_types"].items()))
        output.append(
            {
                "path": path,
                "field_name": record["field_name"],
                "occurrence_count": record["occurrence_count"],
                "value_type": (
                    next(iter(value_types))
                    if len(value_types) == 1
                    else "MIXED"
                ),
                "value_types": value_types,
                "non_empty_value_count": record["non_empty_value_count"],
                "sanitized_value_examples": record["sanitized_value_examples"],
            }
        )
    return output


def collect_incident_info_occurrences(
    parsed: Any,
    secret: Optional[str] = None,
) -> List[Dict[str, Any]]:
    token_pattern = re.compile(r"(?:IncidentCall|InfoCall)", re.IGNORECASE)
    grouped: Dict[str, Dict[str, Any]] = {}
    for path, key, child, parent_path, parent in iter_dict_entries_with_context(parsed):
        matched_in: Set[str] = set()
        matched_identifier_types: Set[str] = set()
        identifier_counts: Counter[str] = Counter()
        matched_scalar_values: List[Any] = []
        if token_pattern.search(str(key)):
            matched_in.add("FIELD_NAME")
            for identifier_type in ("IncidentCall", "InfoCall"):
                if re.search(identifier_type, str(key), re.IGNORECASE):
                    matched_identifier_types.add(identifier_type)
        direct_scalar_values: Iterable[Any]
        if isinstance(child, (str, int, float, bool)):
            direct_scalar_values = (child,)
        elif isinstance(child, list):
            direct_scalar_values = (
                item
                for item in child
                if isinstance(item, (str, int, float, bool))
            )
        else:
            direct_scalar_values = ()
        for item in direct_scalar_values:
            if not isinstance(item, (str, int, float, bool)):
                continue
            matched_types = [
                identifier_type
                for identifier_type in ("IncidentCall", "InfoCall")
                if re.search(identifier_type, str(item), re.IGNORECASE)
            ]
            if not matched_types:
                continue
            matched_in.add("VALUE")
            matched_scalar_values.append(item)
            for identifier_type in matched_types:
                matched_identifier_types.add(identifier_type)
                identifier_counts[identifier_type] += 1
        if not matched_in:
            continue
        candidate_values = [
            short_text(item, secret)
            for item in matched_scalar_values
            if short_text(item, secret) is not None
        ]
        if token_pattern.search(str(key)) and not candidate_values:
            candidate_values = [
                short_text(item, secret) for item in flatten_scalar_values(child)
            ]
        if not identifier_counts:
            for identifier_type in ("IncidentCall", "InfoCall"):
                if identifier_type in matched_identifier_types:
                    identifier_counts[identifier_type] += 1
        record = grouped.setdefault(
            path,
            {
                "path": path,
                "occurrence_count": 0,
                "matched_in": set(),
                "matched_identifier_types": set(),
                "identifier_counts": Counter(),
                "sample_identifiers": [],
                "source_origin": reference_origin_for_path(path),
            },
        )
        record["occurrence_count"] += 1
        record["matched_in"].update(matched_in)
        record["matched_identifier_types"].update(matched_identifier_types)
        record["identifier_counts"].update(identifier_counts)
        for candidate in candidate_values:
            if candidate and candidate not in record["sample_identifiers"]:
                record["sample_identifiers"].append(candidate)
            if len(record["sample_identifiers"]) >= MAX_MDD_FIELD_EXAMPLES:
                break
        record.update(reference_parent_context(parent_path, parent, key))
    output: List[Dict[str, Any]] = []
    for path in sorted(grouped):
        record = grouped[path]
        output.append(
            {
                "path": path,
                "occurrence_count": record["occurrence_count"],
                "matched_in": sorted(record["matched_in"]),
                "matched_identifier_types": sorted(record["matched_identifier_types"]),
                "identifier_counts": dict(sorted(record["identifier_counts"].items())),
                "sample_identifiers": record["sample_identifiers"][:MAX_MDD_FIELD_EXAMPLES],
                "parent_object_path": record["nearest_enclosing_object_path"],
                "parent_object_type": record["nearest_enclosing_object_type"],
                "sibling_field_names": record["sibling_field_names"],
                "source_origin": record["source_origin"],
            }
        )
    return output


def relation_scope_for_reference(path: str) -> str:
    components = path_component_names(path)
    if any(
        component in {"previouscall", "thiscall", "onwardcall", "callatstop"}
        for component in components
    ):
        return "CALL"
    if any("journey" in component for component in components):
        return "JOURNEY"
    if "service" in components:
        return "SERVICE"
    if any(component in {"stopevent", "stopeventresult"} for component in components):
        return "STOP_EVENT"
    if any(
        component in {"stopeventresponsecontext", "responsecontext", "situations"}
        for component in components
    ):
        return "RESPONSE_CONTEXT"
    return "OTHER_OR_UNCLASSIFIED"


def situation_validity_values(
    situation: Dict[str, Any],
) -> Tuple[Optional[Any], Optional[Any]]:
    valid_from = source_scalar(direct_named_value(situation, {"validfrom"}))
    valid_to = source_scalar(direct_named_value(situation, {"validto"}))
    periods: List[Dict[str, Any]] = []
    for value in direct_named_values(situation, {"validityperiod"}):
        periods.extend(as_object_list(value))
    if valid_from is None:
        for period in periods:
            candidate = source_scalar(direct_named_value(period, {"starttime", "validfrom"}))
            if candidate is not None:
                valid_from = candidate
                break
    if valid_to is None:
        for period in periods:
            candidate = source_scalar(direct_named_value(period, {"endtime", "validto"}))
            if candidate is not None:
                valid_to = candidate
                break
    return valid_from, valid_to


def situation_identity(
    situation: Dict[str, Any],
    path: str,
) -> Tuple[str, bool, Optional[str], Optional[str], Optional[str], Optional[str]]:
    participant = source_scalar(direct_named_value(situation, {"participantref"}))
    situation_number = source_scalar(
        direct_named_value(situation, {"situationnumber"})
    )
    version = source_scalar(direct_named_value(situation, {"version"}))
    identifier = source_scalar(direct_named_value(situation, {"identifier", "id"}))
    return source_identity_key(
        participant,
        situation_number,
        version,
        identifier,
        situation,
    )


def source_field_paths(
    situation: Dict[str, Any],
    entry_path: str,
    names: Set[str],
) -> List[str]:
    paths: List[str] = []
    for path, key, child in iter_dict_entries(situation, entry_path):
        if normalized_key(key) in names and source_scalar(child) is not None:
            paths.append(path)
    return paths


def situation_record(
    entry_path: str,
    situation: Dict[str, Any],
    secret: Optional[str],
) -> Dict[str, Any]:
    (
        identity_key,
        identifiable,
        participant,
        situation_number,
        version,
        identifier,
    ) = situation_identity(
        situation, entry_path
    )
    summary_values = source_text_values(direct_named_value(situation, {"summary"}))
    description_values = source_text_values(
        direct_named_value(situation, {"description"})
    )
    detail_values = source_text_values(direct_named_value(situation, {"detail"}))
    valid_from, valid_to = situation_validity_values(situation)
    validity_paths = source_field_paths(
        situation,
        entry_path,
        {"validfrom", "validto", "starttime", "endtime"},
    )
    return {
        "entry_path": entry_path,
        "identity_key": identity_key,
        "identifiable": identifiable,
        "participant_ref": short_text(participant, secret),
        "situation_number": short_text(situation_number, secret),
        "version": short_text(version, secret),
        "identifier": short_text(identifier, secret),
        "identity_key_without_version": (
            identity_key.split("|VERSION|", 1)[0]
            if "|VERSION|" in identity_key
            else identity_key
        ),
        "all_observed_field_paths": sorted(
            {
                path
                for path, _key, _child in iter_dict_entries(situation, entry_path)
            }
        ),
        "summary_values": summary_values,
        "description_values": description_values,
        "detail_values": detail_values,
        "valid_from": short_text(valid_from, secret),
        "valid_to": short_text(valid_to, secret),
        "observed_source_field_paths": source_field_paths(
            situation,
            entry_path,
            {
                "participantref",
                "situationnumber",
                "identifier",
                "id",
                "summary",
                "description",
                "detail",
            },
        )
        + validity_paths,
    }


def situation_object_field_inventory(
    situation_entries: Sequence[Tuple[str, Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Inventory every field below each observed inline Situation object."""

    inventory: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"occurrence_count": 0, "value_types": Counter()}
    )
    for entry_path, situation in situation_entries:
        for path, _key, child in iter_dict_entries(situation, entry_path):
            record = inventory[path]
            record["occurrence_count"] += 1
            record["value_types"][json_value_type(child)] += 1
    output: List[Dict[str, Any]] = []
    for path in sorted(inventory):
        record = inventory[path]
        value_types = dict(sorted(record["value_types"].items()))
        output.append(
            {
                "path": path,
                "occurrence_count": record["occurrence_count"],
                "value_type": (
                    next(iter(value_types))
                    if len(value_types) == 1
                    else "MIXED"
                ),
                "value_types": value_types,
            }
        )
    return output


def aggregate_situation_content(
    records: Sequence[Dict[str, Any]],
    secret: Optional[str] = None,
) -> Dict[str, Any]:
    total = len(records)
    unique_keys = {record["identity_key"] for record in records}
    unidentified_count = sum(not record["identifiable"] for record in records)
    all_summary_values = [
        value for record in records for value in record["summary_values"]
    ]
    all_description_values = [
        value for record in records for value in record["description_values"]
    ]
    all_detail_values = [value for record in records for value in record["detail_values"]]
    summary_languages, summary_missing_language_count = language_counts(all_summary_values)
    description_languages, description_missing_language_count = language_counts(
        all_description_values
    )
    detail_languages, detail_missing_language_count = language_counts(all_detail_values)
    examples: List[Dict[str, Any]] = []
    for record in records:
        if not (
            record["summary_values"]
            or record["description_values"]
            or record["detail_values"]
        ):
            continue
        examples.append(
            {
                "participant_ref": short_text(record["participant_ref"], secret),
                "situation_number": short_text(record["situation_number"], secret),
                "identifier": short_text(record["identifier"], secret),
                "classification": "SOURCE-REPORTED SITUATION TEXT",
                "summary": short_text(
                    record["summary_values"][0][0]
                    if record["summary_values"]
                    else None,
                    secret,
                ),
                "description": short_text(
                    record["description_values"][0][0]
                    if record["description_values"]
                    else None,
                    secret,
                ),
                "detail": short_text(
                    record["detail_values"][0][0]
                    if record["detail_values"]
                    else None,
                    secret,
                ),
            }
        )
        if len(examples) >= MAX_TEXT_EXAMPLES:
            break
    observed_paths = sorted(
        {
            path
            for record in records
            for path in record["observed_source_field_paths"]
        }
    )
    summary_presence = sum(bool(record["summary_values"]) for record in records)
    description_presence = sum(bool(record["description_values"]) for record in records)
    detail_presence = sum(bool(record["detail_values"]) for record in records)
    valid_from_presence = sum(record["valid_from"] is not None for record in records)
    valid_to_presence = sum(record["valid_to"] is not None for record in records)
    participant_presence = sum(record["participant_ref"] is not None for record in records)
    situation_number_presence = sum(
        record["situation_number"] is not None for record in records
    )
    version_presence = sum(record.get("version") is not None for record in records)
    identifier_presence = sum(record["identifier"] is not None for record in records)
    return {
        "situation_count": total,
        "unique_situation_count": len(unique_keys),
        "unidentified_situation_count": unidentified_count,
        "participant_ref_present_count": participant_presence,
        "situation_number_present_count": situation_number_presence,
        "version_present_count": version_presence,
        "identifier_present_count": identifier_presence,
        "situation_identity_present_count": sum(
            record["identifiable"] for record in records
        ),
        "summary_present_count": summary_presence,
        "description_present_count": description_presence,
        "detail_present_count": detail_presence,
        "valid_from_present_count": valid_from_presence,
        "valid_to_present_count": valid_to_presence,
        "coverage_percentages": {
            "participant_ref": percentage(participant_presence, total),
            "situation_number": percentage(situation_number_presence, total),
            "version": percentage(version_presence, total),
            "identifier": percentage(identifier_presence, total),
            "summary": percentage(summary_presence, total),
            "description": percentage(description_presence, total),
            "detail": percentage(detail_presence, total),
            "valid_from": percentage(valid_from_presence, total),
            "valid_to": percentage(valid_to_presence, total),
        },
        "summary_language_counts": summary_languages,
        "summary_without_explicit_language_count": summary_missing_language_count,
        "description_language_counts": description_languages,
        "description_without_explicit_language_count": description_missing_language_count,
        "detail_language_counts": detail_languages,
        "detail_without_explicit_language_count": detail_missing_language_count,
        "summary_character_length_statistics": length_statistics(
            [len(text) for text, _language in all_summary_values]
        ),
        "description_character_length_statistics": length_statistics(
            [len(text) for text, _language in all_description_values]
        ),
        "detail_character_length_statistics": length_statistics(
            [len(text) for text, _language in all_detail_values]
        ),
        "observed_source_field_paths": observed_paths,
        "max_sanitized_short_examples": examples,
    }


def merge_path_inventories(
    inventories: Sequence[Sequence[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for inventory in inventories:
        for item in inventory:
            record = merged.setdefault(
                item["path"],
                {
                    "path": item["path"],
                    "occurrence_count": 0,
                    "value_types": Counter(),
                },
            )
            record["occurrence_count"] += item.get("occurrence_count", 0)
            record["value_types"].update(item.get("value_types", {}))
    output: List[Dict[str, Any]] = []
    for path in sorted(merged):
        record = merged[path]
        value_types = dict(sorted(record["value_types"].items()))
        output.append(
            {
                "path": path,
                "occurrence_count": record["occurrence_count"],
                "value_type": (
                    next(iter(value_types))
                    if len(value_types) == 1
                    else "MIXED"
                ),
                "value_types": value_types,
            }
        )
    return output


def merge_reason_like_inventories(
    inventories: Sequence[Sequence[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for inventory in inventories:
        for item in inventory:
            record = merged.setdefault(
                item["path"],
                {
                    "path": item["path"],
                    "field_name": item.get("field_name"),
                    "occurrence_count": 0,
                    "value_types": Counter(),
                    "matched_terms": set(),
                    "semantic_category": item.get(
                        "semantic_category", "UNCLASSIFIED_SOURCE_TEXT"
                    ),
                    "sanitized_value_examples": [],
                },
            )
            record["occurrence_count"] += item.get("occurrence_count", 0)
            record["value_types"].update(item.get("value_types", {}))
            record["matched_terms"].update(item.get("matched_terms", []))
            for example in item.get("sanitized_value_examples", []):
                if example not in record["sanitized_value_examples"]:
                    record["sanitized_value_examples"].append(example)
                if len(record["sanitized_value_examples"]) >= MAX_MDD_FIELD_EXAMPLES:
                    break
    output: List[Dict[str, Any]] = []
    for path in sorted(merged):
        record = merged[path]
        value_types = dict(sorted(record["value_types"].items()))
        output.append(
            {
                "path": path,
                "field_name": record["field_name"],
                "occurrence_count": record["occurrence_count"],
                "value_type": (
                    next(iter(value_types))
                    if len(value_types) == 1
                    else "MIXED"
                ),
                "value_types": value_types,
                "matched_terms": sorted(record["matched_terms"]),
                "semantic_category": record["semantic_category"],
                "sanitized_value_examples": record["sanitized_value_examples"],
            }
        )
    return output


def sanitize_reference_record(
    record: Dict[str, Any],
    secret: Optional[str],
) -> Dict[str, Any]:
    output = dict(record)
    for field_name in (
        "participant_ref",
        "situation_number",
        "version",
        "identifier",
        "identity_key",
        "identity_key_without_version",
    ):
        if field_name in output:
            output[field_name] = short_text(output[field_name], secret)
    output["sanitized_value_examples"] = [
        short_text(value, secret)
        for value in output.get("sanitized_value_examples", [])
        if short_text(value, secret) is not None
    ][:MAX_MDD_FIELD_EXAMPLES]
    output.pop("path", None)
    output["full_json_path"] = record.get("path")
    return output


def analyze_mdd_response(
    station: MddStation,
    result: FetchResult,
    secret: Optional[str],
) -> Dict[str, Any]:
    parsed, parse_failure = parse_mdd_json(result)
    results: List[Dict[str, Any]] = []
    situation_entries: List[Tuple[str, Dict[str, Any]]] = []
    reference_records: List[Dict[str, Any]] = []
    json_inventory: List[Dict[str, Any]] = []
    reason_like_inventory: List[Dict[str, Any]] = []
    incident_info_occurrences: List[Dict[str, Any]] = []
    error_inventory: List[Dict[str, Any]] = []
    general_attribute_records: List[Dict[str, Any]] = []
    extension_records: List[Dict[str, Any]] = []
    operational_status_records: List[Dict[str, Any]] = []
    if parsed is not None:
        results = named_results(parsed)
        situation_entries = named_object_entries(
            parsed,
            {"ptsituation", "ptsituationelement", "roadsituation", "situation"},
        )
        reference_records = collect_reference_records(parsed, secret)
        json_inventory = json_path_inventory(parsed)
        reason_like_inventory = reason_like_field_inventory(parsed, secret)
        incident_info_occurrences = collect_incident_info_occurrences(parsed, secret)
        error_inventory = error_field_inventory(parsed, secret)
        general_attribute_records = collect_general_attribute_records(parsed, secret)
        extension_records = collect_extension_records(parsed, secret)
        operational_status_records = collect_operational_status_records(parsed)
    situation_records = [
        situation_record(path, item, secret) for path, item in situation_entries
    ]
    situation_object_inventory = situation_object_field_inventory(situation_entries)
    situation_references = [
        reference
        for reference in reference_records
        if reference["reference_type"] in {"SituationFullRef", "SituationRef"}
    ]
    relation_counts: Counter[str] = Counter(
        relation_scope_for_reference(reference["path"])
        for reference in situation_references
    )
    reference_type_counts: Counter[str] = Counter(
        reference["reference_type"] for reference in reference_records
    )
    content = aggregate_situation_content(situation_records, secret)
    inline_identity_keys = {
        identity_key
        for record in situation_records
        for identity_key in (
            record["identity_key"],
            record.get("identity_key_without_version"),
        )
        if identity_key
    }
    full_ref_records = [
        reference
        for reference in reference_records
        if reference["reference_type"] == "SituationFullRef"
    ]
    unique_full_ref_keys = {
        reference["identity_key"] for reference in full_ref_records
    }
    resolved_full_ref_records = [
        reference
        for reference in full_ref_records
        if reference["identity_key"] in inline_identity_keys
        or reference["identity_key_without_version"] in inline_identity_keys
    ]
    sanitized_full_ref_records = [
        dict(sanitize_reference_record(reference, secret), **{
            "association_scope": relation_scope_for_reference(reference["path"]),
            "resolution_status": (
                "RESOLVED_INLINE_SITUATION"
                if reference in resolved_full_ref_records
                else "UNRESOLVED_INLINE_SITUATION"
            ),
        })
        for reference in full_ref_records
    ]
    general_attribute_field_counts: Counter[str] = Counter(
        field_name
        for record in general_attribute_records
        for field_name in record.get("present_fields", [])
    )
    operational_status_counts: Counter[str] = Counter(
        record["status_field"] for record in operational_status_records
    )
    operational_status_path_counts: Counter[str] = Counter(
        record["path"] for record in operational_status_records
    )
    incident_call_identifier_count = sum(
        record.get("identifier_counts", {}).get("IncidentCall", 0)
        for record in incident_info_occurrences
    )
    info_call_identifier_count = sum(
        record.get("identifier_counts", {}).get("InfoCall", 0)
        for record in incident_info_occurrences
    )
    error_message_count = sum(
        item.get("non_empty_value_count", 0)
        for item in error_inventory
        if normalized_key(item["field_name"]) == "errormessage"
    )
    relation_scope_keys = (
        "CALL",
        "SERVICE",
        "JOURNEY",
        "STOP_EVENT",
        "RESPONSE_CONTEXT",
        "OTHER_OR_UNCLASSIFIED",
    )
    status = "PARSED" if parsed is not None else (parse_failure or "NOT_PARSED")
    output: Dict[str, Any] = {
        "station": station.display_name,
        "station_key": station.key,
        "stop_point_ref": station.stop_point_ref,
        "http_status": result.http_status,
        "response_content_type": result.response_content_type,
        "response_bytes": result.response_bytes,
        "elapsed_ms": result.elapsed_ms,
        "request_timestamp_utc": result.request_timestamp_utc,
        "raw_artifact_path": result.raw_artifact_path,
        "response_audit_status": status,
        "result_count": len(results),
        "situation_count": len(situation_entries),
        "inline_situation_count": len(situation_entries),
        "reference_entry_count": len(reference_records),
        "situation_reference_count": len(situation_references),
        "reference_type_counts": dict(sorted(reference_type_counts.items())),
        "incident_call_reference_count": reference_type_counts.get("IncidentCall", 0),
        "info_call_reference_count": reference_type_counts.get("InfoCall", 0),
        "incident_call_identifier_count": incident_call_identifier_count,
        "info_call_identifier_count": info_call_identifier_count,
        "unique_situation_count": content["unique_situation_count"],
        "relation_scope_counts": {
            key: relation_counts.get(key, 0) for key in relation_scope_keys
        },
        "call_level_situation_link_count": relation_counts.get("CALL", 0),
        "service_level_situation_link_count": relation_counts.get("SERVICE", 0),
        "other_unclassified_relation_count": relation_counts.get(
            "OTHER_OR_UNCLASSIFIED", 0
        ),
        "situation_full_ref_count": len(full_ref_records),
        "journey_level_situation_full_ref_count": relation_counts.get("JOURNEY", 0),
        "call_level_situation_full_ref_count": relation_counts.get("CALL", 0),
        "other_situation_full_ref_count": len(full_ref_records)
        - relation_counts.get("JOURNEY", 0)
        - relation_counts.get("CALL", 0),
        "unique_situation_full_ref_count": len(unique_full_ref_keys),
        "resolved_inline_situation_ref_count": len(resolved_full_ref_records),
        "unresolved_situation_ref_count": len(full_ref_records)
        - len(resolved_full_ref_records),
        "situation_full_ref_records": sanitized_full_ref_records,
        "situation_reference_records": [
            dict(
                sanitize_reference_record(reference, secret),
                association_scope=relation_scope_for_reference(reference["path"]),
            )
            for reference in situation_references
        ],
        "json_path_inventory": json_inventory,
        "reason_like_field_inventory": reason_like_inventory,
        "incident_info_occurrences": incident_info_occurrences,
        "error_message_field_inventory": error_inventory,
        "error_message_count": error_message_count,
        "error_message_non_empty_value_count": len(
            {
                example
                for item in error_inventory
                if normalized_key(item["field_name"]) == "errormessage"
                for example in item.get("sanitized_value_examples", [])
            }
        ),
        "situation_object_field_inventory": situation_object_inventory,
        "situation_object_entry_paths": [path for path, _item in situation_entries],
        "general_attribute_count": len(general_attribute_records),
        "general_attribute_text_present_count": general_attribute_field_counts.get(
            "Text", 0
        ),
        "general_attribute_code_present_count": general_attribute_field_counts.get(
            "Code", 0
        ),
        "general_attribute_info_url_present_count": general_attribute_field_counts.get(
            "InfoURL", 0
        ),
        "general_attribute_field_counts": dict(sorted(general_attribute_field_counts.items())),
        "general_attribute_records": general_attribute_records,
        "extension_populated_count": len(extension_records),
        "extension_records": extension_records,
        "operational_status_counts": dict(sorted(operational_status_counts.items())),
        "operational_status_true_path_counts": dict(
            sorted(operational_status_path_counts.items())
        ),
        "operational_status_records": operational_status_records,
        "summary_present_count": content["summary_present_count"],
        "description_present_count": content["description_present_count"],
        "detail_present_count": content["detail_present_count"],
        "valid_from_present_count": content["valid_from_present_count"],
        "valid_to_present_count": content["valid_to_present_count"],
        "content_coverage_percentages": content["coverage_percentages"],
        "summary_character_length_statistics": content[
            "summary_character_length_statistics"
        ],
        "description_character_length_statistics": content[
            "description_character_length_statistics"
        ],
        "detail_character_length_statistics": content[
            "detail_character_length_statistics"
        ],
        "situation_content": content,
        "_situation_records": situation_records,
        "_reference_records": reference_records,
    }
    return output


def empty_mdd_station_summary(
    station: MddStation,
    status: str,
) -> Dict[str, Any]:
    """Return a schema-stable no-request MDD station record."""

    return {
        "station": station.display_name,
        "station_key": station.key,
        "stop_point_ref": station.stop_point_ref,
        "response_audit_status": status,
        "http_status": None,
        "response_content_type": None,
        "response_bytes": 0,
        "elapsed_ms": None,
        "request_timestamp_utc": None,
        "raw_artifact_path": None,
        "result_count": 0,
        "situation_count": 0,
        "inline_situation_count": 0,
        "reference_entry_count": 0,
        "situation_reference_count": 0,
        "reference_type_counts": {},
        "incident_call_reference_count": 0,
        "info_call_reference_count": 0,
        "incident_call_identifier_count": 0,
        "info_call_identifier_count": 0,
        "unique_situation_count": 0,
        "call_level_situation_link_count": 0,
        "service_level_situation_link_count": 0,
        "other_unclassified_relation_count": 0,
        "relation_scope_counts": {
            "CALL": 0,
            "SERVICE": 0,
            "JOURNEY": 0,
            "STOP_EVENT": 0,
            "RESPONSE_CONTEXT": 0,
            "OTHER_OR_UNCLASSIFIED": 0,
        },
        "situation_full_ref_count": 0,
        "journey_level_situation_full_ref_count": 0,
        "call_level_situation_full_ref_count": 0,
        "other_situation_full_ref_count": 0,
        "unique_situation_full_ref_count": 0,
        "resolved_inline_situation_ref_count": 0,
        "unresolved_situation_ref_count": 0,
        "situation_full_ref_records": [],
        "situation_reference_records": [],
        "json_path_inventory": [],
        "reason_like_field_inventory": [],
        "incident_info_occurrences": [],
        "error_message_field_inventory": [],
        "error_message_count": 0,
        "error_message_non_empty_value_count": 0,
        "situation_object_field_inventory": [],
        "situation_object_entry_paths": [],
        "general_attribute_count": 0,
        "general_attribute_text_present_count": 0,
        "general_attribute_code_present_count": 0,
        "general_attribute_info_url_present_count": 0,
        "general_attribute_field_counts": {},
        "general_attribute_records": [],
        "extension_populated_count": 0,
        "extension_records": [],
        "operational_status_counts": {},
        "operational_status_true_path_counts": {},
        "operational_status_records": [],
        "summary_present_count": 0,
        "description_present_count": 0,
        "detail_present_count": 0,
        "valid_from_present_count": 0,
        "valid_to_present_count": 0,
        "content_coverage_percentages": {
            "participant_ref": None,
            "situation_number": None,
            "version": None,
            "identifier": None,
            "summary": None,
            "description": None,
            "detail": None,
            "valid_from": None,
            "valid_to": None,
        },
        "situation_content": aggregate_situation_content([]),
        "_situation_records": [],
        "_reference_records": [],
    }


def aggregate_mdd_summaries(
    station_summaries: Sequence[Dict[str, Any]],
    secret: Optional[str] = None,
) -> Dict[str, Any]:
    records = [
        record
        for summary in station_summaries
        for record in summary.get("_situation_records", [])
    ]
    content = aggregate_situation_content(records, secret)
    result_count = sum(summary.get("result_count", 0) for summary in station_summaries)
    reference_entry_count = sum(
        summary.get("reference_entry_count", 0) for summary in station_summaries
    )
    situation_reference_count = sum(
        summary.get("situation_reference_count", 0) for summary in station_summaries
    )
    reference_type_counts: Counter[str] = Counter()
    for summary in station_summaries:
        reference_type_counts.update(summary.get("reference_type_counts", {}))
    relation_keys = (
        "CALL",
        "SERVICE",
        "JOURNEY",
        "STOP_EVENT",
        "RESPONSE_CONTEXT",
        "OTHER_OR_UNCLASSIFIED",
    )
    relation_counts = {
        key: sum(
            summary.get("relation_scope_counts", {}).get(key, 0)
            for summary in station_summaries
        )
        for key in relation_keys
    }
    all_reference_records = [
        reference
        for summary in station_summaries
        for reference in summary.get("_reference_records", [])
    ]
    all_full_ref_records = [
        reference
        for reference in all_reference_records
        if reference["reference_type"] == "SituationFullRef"
    ]
    full_ref_relation_counts: Counter[str] = Counter(
        relation_scope_for_reference(reference["path"])
        for reference in all_full_ref_records
    )
    unique_full_ref_keys = {
        reference["identity_key"] for reference in all_full_ref_records
    }
    json_path_inventory = merge_path_inventories(
        [summary.get("json_path_inventory", []) for summary in station_summaries]
    )
    reason_like_inventory = merge_reason_like_inventories(
        [summary.get("reason_like_field_inventory", []) for summary in station_summaries]
    )
    situation_object_field_inventory = merge_path_inventories(
        [
            summary.get("situation_object_field_inventory", [])
            for summary in station_summaries
        ]
    )
    general_attribute_records = [
        dict(record, station_key=summary.get("station_key"))
        for summary in station_summaries
        for record in summary.get("general_attribute_records", [])
    ]
    general_attribute_field_counts: Counter[str] = Counter()
    for summary in station_summaries:
        general_attribute_field_counts.update(
            summary.get("general_attribute_field_counts", {})
        )
    extension_records = [
        dict(record, station_key=summary.get("station_key"))
        for summary in station_summaries
        for record in summary.get("extension_records", [])
    ]
    operational_status_records = [
        dict(record, station_key=summary.get("station_key"))
        for summary in station_summaries
        for record in summary.get("operational_status_records", [])
    ]
    operational_status_counts: Counter[str] = Counter()
    operational_status_path_counts: Counter[str] = Counter()
    for summary in station_summaries:
        operational_status_counts.update(summary.get("operational_status_counts", {}))
        operational_status_path_counts.update(
            summary.get("operational_status_true_path_counts", {})
        )
    incident_call_identifier_count = sum(
        summary.get("incident_call_identifier_count", 0)
        for summary in station_summaries
    )
    info_call_identifier_count = sum(
        summary.get("info_call_identifier_count", 0)
        for summary in station_summaries
    )
    station_views = []
    for summary in station_summaries:
        view = dict(summary)
        view.pop("_situation_records", None)
        view.pop("_reference_records", None)
        station_views.append(view)
    return {
        "sample_station_count": len(station_summaries),
        "sample_result_count": result_count,
        "situation_count": content["situation_count"],
        "inline_situation_count": content["situation_count"],
        "reference_entry_count": reference_entry_count,
        "situation_reference_count": situation_reference_count,
        "reference_type_counts": dict(sorted(reference_type_counts.items())),
        "incident_call_reference_count": reference_type_counts.get("IncidentCall", 0),
        "info_call_reference_count": reference_type_counts.get("InfoCall", 0),
        "incident_call_identifier_count": incident_call_identifier_count,
        "info_call_identifier_count": info_call_identifier_count,
        "unique_situation_count": content["unique_situation_count"],
        "situation_full_ref_count": len(all_full_ref_records),
        "journey_level_situation_full_ref_count": full_ref_relation_counts.get(
            "JOURNEY", 0
        ),
        "call_level_situation_full_ref_count": full_ref_relation_counts.get(
            "CALL", 0
        ),
        "other_situation_full_ref_count": len(all_full_ref_records)
        - full_ref_relation_counts.get("JOURNEY", 0)
        - full_ref_relation_counts.get("CALL", 0),
        "unique_situation_full_ref_count": len(unique_full_ref_keys),
        "resolved_inline_situation_ref_count": sum(
            summary.get("resolved_inline_situation_ref_count", 0)
            for summary in station_summaries
        ),
        "unresolved_situation_ref_count": sum(
            summary.get("unresolved_situation_ref_count", 0)
            for summary in station_summaries
        ),
        "call_level_situation_link_count": relation_counts["CALL"],
        "service_level_situation_link_count": relation_counts["SERVICE"],
        "other_unclassified_relation_count": relation_counts["OTHER_OR_UNCLASSIFIED"],
        "relation_scope_counts": relation_counts,
        "json_path_inventory": json_path_inventory,
        "reason_like_field_inventory": reason_like_inventory,
        "situation_object_field_inventory": situation_object_field_inventory,
        "situation_full_ref_records": [
            dict(
                sanitize_reference_record(reference, secret),
                station_key=next(
                    (
                        summary.get("station_key")
                        for summary in station_summaries
                        if reference in summary.get("_reference_records", [])
                    ),
                    None,
                ),
                association_scope=relation_scope_for_reference(reference["path"]),
            )
            for reference in all_full_ref_records
        ],
        "incident_info_occurrences": [
            {
                "station_key": summary.get("station_key"),
                "occurrences": summary.get("incident_info_occurrences", []),
            }
            for summary in station_summaries
        ],
        "error_message_field_inventory": [
            {
                "station_key": summary.get("station_key"),
                "fields": summary.get("error_message_field_inventory", []),
            }
            for summary in station_summaries
        ],
        "error_message_non_empty_value_count": sum(
            summary.get("error_message_non_empty_value_count", 0)
            for summary in station_summaries
        ),
        "error_message_count": sum(
            summary.get("error_message_count", 0)
            for summary in station_summaries
        ),
        "general_attribute_count": len(general_attribute_records),
        "general_attribute_text_present_count": general_attribute_field_counts.get(
            "Text", 0
        ),
        "general_attribute_code_present_count": general_attribute_field_counts.get(
            "Code", 0
        ),
        "general_attribute_info_url_present_count": general_attribute_field_counts.get(
            "InfoURL", 0
        ),
        "general_attribute_field_counts": dict(
            sorted(general_attribute_field_counts.items())
        ),
        "general_attribute_records": general_attribute_records,
        "extension_populated_count": len(extension_records),
        "extension_records": extension_records,
        "operational_status_counts": dict(sorted(operational_status_counts.items())),
        "operational_status_true_path_counts": dict(
            sorted(operational_status_path_counts.items())
        ),
        "operational_status_records": operational_status_records,
        "summary_present_count": content["summary_present_count"],
        "description_present_count": content["description_present_count"],
        "detail_present_count": content["detail_present_count"],
        "valid_from_present_count": content["valid_from_present_count"],
        "valid_to_present_count": content["valid_to_present_count"],
        "content_coverage_percentages": content["coverage_percentages"],
        "situation_content": content,
        "station_summaries": station_views,
    }


def final_summary(
    feed_summary: Dict[str, Any],
    local_summary: Dict[str, Any],
    mdd_summary: Dict[str, Any],
    tracker: AttemptTracker,
    bindings_present: bool,
    mdd_api_key_present: bool,
) -> Dict[str, Any]:
    total_alert_count = feed_summary.get("total_alert_count", 0)
    cause_percentage = feed_summary.get("coverage_percentages", {}).get("cause")
    effect_percentage = feed_summary.get("coverage_percentages", {}).get("effect")
    if not bindings_present:
        cause_statement = (
            "GTFS-RT cause population was not measured because the validated "
            "gtfs-realtime-bindings package was unavailable on the host."
        )
        effect_statement = (
            "GTFS-RT effect population was not measured because the validated "
            "gtfs-realtime-bindings package was unavailable on the host."
        )
    elif total_alert_count == 0:
        cause_statement = (
            "The retrieved snapshot contained no ServiceAlerts; cause population "
            "cannot be assessed from this snapshot."
        )
        effect_statement = (
            "The retrieved snapshot contained no ServiceAlerts; effect population "
            "cannot be assessed from this snapshot."
        )
    else:
        cause_statement = (
            "In this snapshot, cause was explicitly populated for %s%% of alerts; "
            "explicit UNKNOWN_CAUSE remains a populated source value and is not "
            "counted as missing. This is descriptive evidence, not a threshold "
            "or production-approval decision." % cause_percentage
        )
        effect_statement = (
            "In this snapshot, effect was explicitly populated for %s%% of alerts. "
            "This is descriptive evidence, not a threshold or production-approval "
            "decision." % effect_percentage
        )

    scope_counts = feed_summary.get("scope_type_alert_counts", {})
    if not total_alert_count:
        scope_statement = "No ServiceAlerts were available for scope assessment."
    elif not scope_counts:
        scope_statement = (
            "No identifying trip, route, stop, or agency scope was present on the "
            "observed alerts."
        )
    else:
        maximum = max(scope_counts.values())
        most_frequent = sorted(
            key for key, value in scope_counts.items() if value == maximum
        )
        scope_statement = (
            "Scope types overlap and are counted per alert; no exclusive 'mostly' "
            "classification is made. The most frequent observed type(s) were %s."
            % ", ".join(most_frequent)
        )

    mdd_content = mdd_summary.get("situation_content", {})
    mdd_situation_count = mdd_content.get("situation_count", 0)
    mdd_text_counts = {
        "summary": mdd_content.get("summary_present_count", 0),
        "description": mdd_content.get("description_present_count", 0),
        "detail": mdd_content.get("detail_present_count", 0),
    }
    if not mdd_situation_count:
        mdd_text_statement = (
            "No MDD Situation objects were observed in the two sampled responses; "
            "usable human-readable Situation content is not demonstrated by this "
            "sample."
        )
    else:
        mdd_text_statement = (
            "MDD returned %d Situation objects in the two sampled responses. "
            "Summary/Description/Detail population was %s/%s/%s%% respectively; "
            "the result is bounded sample evidence, not general coverage."
            % (
                mdd_situation_count,
                percentage(mdd_text_counts["summary"], mdd_situation_count),
                percentage(mdd_text_counts["description"], mdd_situation_count),
                percentage(mdd_text_counts["detail"], mdd_situation_count),
            )
        )

    if not mdd_api_key_present:
        mdd_text_statement = (
            "MDD Situation content was not measured because MDD_API_KEY was not "
            "available on the host."
        )

    return {
        "http_attempts": tracker.total_http_attempts,
        "http_attempt_budget": tracker.max_http_attempts,
        "http_attempts_by_source": dict(sorted(tracker.attempts_by_source.items())),
        "http_budget_compliance": tracker.total_http_attempts
        <= tracker.max_http_attempts,
        "gtfs_rt_cause_population_percentage": cause_percentage,
        "gtfs_rt_cause_explicit_presence_count": feed_summary.get(
            "cause_explicitly_present_count", 0
        ),
        "gtfs_rt_cause_explicit_absence_count": feed_summary.get(
            "cause_explicitly_absent_count", 0
        ),
        "gtfs_rt_cause_presence_value_matrix": feed_summary.get(
            "cause_presence_value_matrix", {}
        ),
        "gtfs_rt_cause_evidence_statement": cause_statement,
        "gtfs_rt_effect_population_percentage": effect_percentage,
        "gtfs_rt_effect_explicit_presence_count": feed_summary.get(
            "effect_explicitly_present_count", 0
        ),
        "gtfs_rt_effect_explicit_absence_count": feed_summary.get(
            "effect_explicitly_absent_count", 0
        ),
        "gtfs_rt_effect_presence_value_matrix": feed_summary.get(
            "effect_presence_value_matrix", {}
        ),
        "gtfs_rt_effect_evidence_statement": effect_statement,
        "gtfs_rt_severity_level_explicit_presence_count": feed_summary.get(
            "severity_level_explicitly_present_count", 0
        ),
        "gtfs_rt_cause_detail": feed_summary.get("cause_detail", {}),
        "gtfs_rt_effect_detail": feed_summary.get("effect_detail", {}),
        "gtfs_rt_url_explicit_presence_count": feed_summary.get(
            "url_explicitly_present_count", 0
        ),
        "gtfs_rt_text_frequency": {
            key: feed_summary.get(key)
            for key in (
                "distinct_header_text_count",
                "distinct_description_text_count",
                "distinct_cause_detail_count",
                "distinct_effect_detail_count",
                "top_20_header_texts",
                "top_20_description_texts",
                "top_20_cause_details",
                "top_20_effect_details",
                "gtfsde_provenance_description_count",
                "gtfsde_provenance_description_percentage",
                "non_provenance_description_alert_count",
                "non_provenance_description_value_count",
                "distinct_non_provenance_description_text_count",
                "top_20_non_provenance_description_texts",
            )
        },
        "gtfs_rt_alert_scope_type_counts": scope_counts,
        "gtfs_rt_alert_scope_evidence_statement": scope_statement,
        "neumarkt_directly_linked_alert_count": local_summary.get(
            "alerts_with_any_direct_link_count", 0
        ),
        "neumarkt_direct_alert_link_summary": {
            "exact_trip": local_summary.get("alerts_exact_trip_link_count", 0),
            "exact_stop": local_summary.get("alerts_exact_stop_link_count", 0),
            "route_context_if_testable": local_summary.get(
                "alerts_route_context_count_if_testable", 0
            ),
        },
        "neumarkt_linked_alert_known_vs_unknown_cause_counts": local_summary.get(
            "linked_alert_known_vs_unknown_cause_counts", {}
        ),
        "neumarkt_trip_linked_alerts_with_non_provenance_description_text": local_summary.get(
            "trip_linked_alerts_with_non_provenance_description_text", 0
        ),
        "mdd_human_readable_situation_evidence_statement": mdd_text_statement,
        "mdd_situation_text_population_counts": mdd_text_counts,
        "mdd_reference_type_counts": mdd_summary.get("reference_type_counts", {}),
        "mdd_incident_call_reference_count": mdd_summary.get(
            "incident_call_reference_count", 0
        ),
        "mdd_info_call_reference_count": mdd_summary.get(
            "info_call_reference_count", 0
        ),
        "mdd_situation_full_ref_count": mdd_summary.get(
            "situation_full_ref_count", 0
        ),
        "mdd_journey_level_situation_full_ref_count": mdd_summary.get(
            "journey_level_situation_full_ref_count", 0
        ),
        "mdd_call_level_situation_full_ref_count": mdd_summary.get(
            "call_level_situation_full_ref_count", 0
        ),
        "mdd_other_situation_full_ref_count": mdd_summary.get(
            "other_situation_full_ref_count", 0
        ),
        "mdd_unique_situation_full_ref_count": mdd_summary.get(
            "unique_situation_full_ref_count", 0
        ),
        "mdd_resolved_inline_situation_ref_count": mdd_summary.get(
            "resolved_inline_situation_ref_count", 0
        ),
        "mdd_unresolved_situation_ref_count": mdd_summary.get(
            "unresolved_situation_ref_count", 0
        ),
        "mdd_incident_info_occurrence_count": sum(
            len(item.get("occurrences", []))
            for item in mdd_summary.get("incident_info_occurrences", [])
        ),
        "mdd_error_message_non_empty_value_count": mdd_summary.get(
            "error_message_non_empty_value_count", 0
        ),
        "mdd_error_message_count": mdd_summary.get("error_message_count", 0),
        "mdd_incident_call_identifier_count": mdd_summary.get(
            "incident_call_identifier_count", 0
        ),
        "mdd_info_call_identifier_count": mdd_summary.get(
            "info_call_identifier_count", 0
        ),
        "mdd_general_attribute_count": mdd_summary.get(
            "general_attribute_count", 0
        ),
        "mdd_general_attribute_text_present_count": mdd_summary.get(
            "general_attribute_text_present_count", 0
        ),
        "mdd_general_attribute_code_present_count": mdd_summary.get(
            "general_attribute_code_present_count", 0
        ),
        "mdd_general_attribute_info_url_present_count": mdd_summary.get(
            "general_attribute_info_url_present_count", 0
        ),
        "mdd_extension_populated_count": mdd_summary.get(
            "extension_populated_count", 0
        ),
        "mdd_operational_status_counts": mdd_summary.get(
            "operational_status_counts", {}
        ),
        "mdd_association_scope_counts": mdd_summary.get("relation_scope_counts", {}),
        "mdd_association_scope_statement": (
            "MDD association counts are retained by relation scope: CALL=%s, "
            "SERVICE=%s, JOURNEY=%s, STOP_EVENT=%s, RESPONSE_CONTEXT=%s, "
            "OTHER_OR_UNCLASSIFIED=%s. Association does not establish causality."
            % (
                mdd_summary.get("call_level_situation_link_count", 0),
                mdd_summary.get("service_level_situation_link_count", 0),
                mdd_summary.get("relation_scope_counts", {}).get("JOURNEY", 0),
                mdd_summary.get("relation_scope_counts", {}).get("STOP_EVENT", 0),
                mdd_summary.get("relation_scope_counts", {}).get(
                    "RESPONSE_CONTEXT", 0
                ),
                mdd_summary.get("other_unclassified_relation_count", 0),
            )
        ),
        "mdd_api_key_present": mdd_api_key_present,
        "cross_source_matching_performed": False,
        "root_cause_inference_performed": False,
        "causality_guardrail": (
            "Provider-Reported Cause, Reported Disruption Cause, Reported Service "
            "Effect, and Associated Situation/Alert remain source evidence. No "
            "Root Cause, Proven Cause, or Confirmed Cause of Delay is inferred."
        ),
        "production_approval": "NONE",
        "actual_time_status": "UNAVAILABLE",
        "actual_delay_kpi": "NOT_APPROVED",
        "what_remains_unvalidated": [
            "Representative repeated snapshots and temporal stability of cause/effect population.",
            "Full Cologne stop, service, route, and alert identity coverage beyond current Neumarkt IDs.",
            "MDD JourneyRef to GTFS-RT trip_id reconciliation and any cross-source attribution.",
            "A normalized cause taxonomy or causal interpretation of provider text.",
            "Production ingestion, retention, cadence, database schema, scheduler, and Power BI design.",
            "Actual Time, Actual Delay, and prediction accuracy versus confirmed actual timing.",
        ],
    }


def bounded_observation_label(count: int) -> str:
    return "OBSERVED_IN_THIS_SAMPLE" if count else "NOT_OBSERVED_IN_THIS_SAMPLE"


def populated_reason_evidence_paths(
    station_summaries: Sequence[Dict[str, Any]],
) -> List[str]:
    paths = {
        item["path"]
        for summary in station_summaries
        for item in summary.get("reason_like_field_inventory", [])
        if item.get("semantic_category") == "REASON_TEXT"
        and item.get("sanitized_value_examples")
    }
    return sorted(paths)


def mdd_discovery_summary(
    station_summaries: Sequence[Dict[str, Any]],
    tracker: AttemptTracker,
    api_key_present: bool,
    stop_reason: Optional[str],
    secret: Optional[str] = None,
) -> Dict[str, Any]:
    aggregate = aggregate_mdd_summaries(station_summaries, secret)
    reason_paths = populated_reason_evidence_paths(station_summaries)
    operational_status_total = sum(
        aggregate.get("operational_status_counts", {}).values()
    )
    successful_http_response_count = sum(
        summary.get("http_status") == 200 for summary in station_summaries
    )
    discovery_status = (
        "MDD_API_KEY_MISSING"
        if not api_key_present
        else "STOPPED_ON_MDD_AUTH_FAILURE"
        if stop_reason and stop_reason.startswith("MDD_AUTH_FAILURE_")
        else "COMPLETED_10_STATION_ATTEMPTS"
    )
    output: Dict[str, Any] = {
        "discovery_status": discovery_status,
        "diagnostic_sample_station_count": len(MDD_DISCOVERY_STATIONS),
        "attempted_station_count": len(station_summaries),
        "skipped_station_count": len(MDD_DISCOVERY_STATIONS) - len(station_summaries),
        "successful_http_response_count": successful_http_response_count,
        "max_http_attempts": MDD_DISCOVERY_MAX_HTTP_ATTEMPTS,
        "http_attempts": tracker.total_http_attempts,
        "http_attempts_by_source": dict(sorted(tracker.attempts_by_source.items())),
        "http_budget_compliance": tracker.total_http_attempts
        <= MDD_DISCOVERY_MAX_HTTP_ATTEMPTS,
        "gtfs_rt_http_attempts": 0,
        "mdd_http_attempts": tracker.total_http_attempts,
        "stop_reason": stop_reason,
        "auth_failure_stop": bool(
            stop_reason and stop_reason.startswith("MDD_AUTH_FAILURE_")
        ),
        "inline_situation_observation": bounded_observation_label(
            aggregate.get("inline_situation_count", 0)
        ),
        "situation_full_ref_observation": bounded_observation_label(
            aggregate.get("situation_full_ref_count", 0)
        ),
        "situation_full_ref_association_scope_counts": {
            key: aggregate.get("relation_scope_counts", {}).get(key, 0)
            for key in (
                "CALL",
                "SERVICE",
                "JOURNEY",
                "STOP_EVENT",
                "RESPONSE_CONTEXT",
                "OTHER_OR_UNCLASSIFIED",
            )
        },
        "explicit_reason_field_observation": bounded_observation_label(
            len(reason_paths)
        ),
        "explicit_reason_field_paths": reason_paths,
        "general_attribute_observation": bounded_observation_label(
            aggregate.get("general_attribute_count", 0)
        ),
        "general_attribute_classification": (
            "PASSENGER_INFORMATION_ATTRIBUTE"
            if aggregate.get("general_attribute_count", 0)
            else None
        ),
        "operational_status_observation": bounded_observation_label(
            operational_status_total
        ),
        "incident_call_identifier_observation": bounded_observation_label(
            aggregate.get("incident_call_identifier_count", 0)
        ),
        "info_call_identifier_observation": bounded_observation_label(
            aggregate.get("info_call_identifier_count", 0)
        ),
        "extension_observation": bounded_observation_label(
            aggregate.get("extension_populated_count", 0)
        ),
        "error_message_observation": bounded_observation_label(
            aggregate.get("error_message_count", 0)
        ),
        "reason_causality_guardrail": (
            "A populated Reason-like field is source-reported evidence only; it "
            "does not establish Root Cause, Proven Cause, or Confirmed Cause of Delay."
        ),
        "aggregate_mdd_summary": aggregate,
    }
    for field_name in (
        "sample_result_count",
        "inline_situation_count",
        "situation_full_ref_count",
        "journey_level_situation_full_ref_count",
        "call_level_situation_full_ref_count",
        "other_situation_full_ref_count",
        "resolved_inline_situation_ref_count",
        "unresolved_situation_ref_count",
        "general_attribute_count",
        "general_attribute_text_present_count",
        "general_attribute_code_present_count",
        "general_attribute_info_url_present_count",
        "extension_populated_count",
        "error_message_count",
        "incident_call_identifier_count",
        "info_call_identifier_count",
        "operational_status_counts",
        "reason_like_field_inventory",
        "json_path_inventory",
        "situation_full_ref_records",
        "incident_info_occurrences",
        "error_message_field_inventory",
    ):
        output[field_name] = aggregate.get(field_name, 0)
    return output


def run_mdd_discovery_mode() -> int:
    """Run the broader bounded MDD-only discovery audit.

    This path intentionally does not load GTFS bindings or call the GTFS-RT
    fetch function.  It is the host command for the follow-up discovery run.
    """

    api_key = os.environ.get(MDD_API_KEY_ENV)
    api_key_present = bool(api_key)
    raw_directory = create_raw_temp_directory() if api_key_present else None
    tracker = AttemptTracker(max_http_attempts=MDD_DISCOVERY_MAX_HTTP_ATTEMPTS)
    emit(
        "run_configuration",
        {
            "probe_name": PROBE_NAME,
            "execution_mode": "MDD_DISCOVERY_HOST_RUN_ONLY",
            "codex_must_not_execute": True,
            "gtfs_rt_included": False,
            "gtfs_rt_http_attempts": 0,
            "mdd_endpoint": MDD_ENDPOINT,
            "mdd_method": "POST",
            "mdd_stations": [
                {
                    "station": station.display_name,
                    "stop_point_ref": station.stop_point_ref,
                }
                for station in MDD_DISCOVERY_STATIONS
            ],
            "mdd_station_count": len(MDD_DISCOVERY_STATIONS),
            "mdd_max_http_attempts": MDD_DISCOVERY_MAX_HTTP_ATTEMPTS,
            "max_http_attempts": MDD_DISCOVERY_MAX_HTTP_ATTEMPTS,
            "requests_per_station": 1,
            "number_of_results": 5,
            "include_previous_calls": True,
            "include_onward_calls": True,
            "include_realtime_data": True,
            "retries": RETRIES,
            "concurrency": CONCURRENCY,
            "fallback_endpoints": list(FALLBACK_ENDPOINTS),
            "mdd_api_key_environment_variable": MDD_API_KEY_ENV,
            "mdd_api_key_present": api_key_present,
            "raw_artifact_policy": "temporary_mode_700_directory_outside_repository",
            "raw_temp_directory": raw_directory,
        },
    )

    station_summaries: List[Dict[str, Any]] = []
    stop_reason: Optional[str] = None
    if not api_key_present:
        stop_reason = "MDD_API_KEY_MISSING"
    else:
        assert api_key is not None
        for station in MDD_DISCOVERY_STATIONS:
            result = perform_mdd_post(tracker, station, api_key, raw_directory)
            summary = analyze_mdd_response(station, result, api_key)
            station_summaries.append(summary)
            emitted_summary = dict(summary)
            emitted_summary.pop("_situation_records", None)
            emitted_summary.pop("_reference_records", None)
            emit("mdd_discovery_station_summary", emitted_summary)
            if result.http_status in (401, 403):
                stop_reason = "MDD_AUTH_FAILURE_%s" % result.http_status
                break

    discovery = mdd_discovery_summary(
        station_summaries,
        tracker,
        api_key_present,
        stop_reason,
        secret=api_key,
    )
    emit("mdd_discovery_summary", discovery)
    emit(
        "final_summary",
        {
            "execution_mode": "MDD_DISCOVERY_HOST_RUN_ONLY",
            "stage_3a_status": (
                "PARTIAL HOST EVIDENCE — MDD SITUATION DISCOVERY PENDING"
            ),
            "discovery_status": discovery["discovery_status"],
            "http_attempts": tracker.total_http_attempts,
            "http_attempt_budget": MDD_DISCOVERY_MAX_HTTP_ATTEMPTS,
            "http_budget_compliance": discovery["http_budget_compliance"],
            "gtfs_rt_http_attempts": 0,
            "mdd_http_attempts": tracker.total_http_attempts,
            "answers": {
                "inline_situations": discovery["inline_situation_observation"],
                "situation_full_refs": discovery["situation_full_ref_observation"],
                "explicit_reason_evidence": discovery[
                    "explicit_reason_field_observation"
                ],
                "general_attributes": discovery["general_attribute_observation"],
                "operational_status_fields": discovery[
                    "operational_status_observation"
                ],
                "incident_call_identifiers": discovery[
                    "incident_call_identifier_observation"
                ],
                "info_call_identifiers": discovery["info_call_identifier_observation"],
                "provider_extensions": discovery["extension_observation"],
                "real_error_messages": discovery["error_message_observation"],
            },
            "stop_reason": stop_reason,
            "cross_source_matching_performed": False,
            "root_cause_inference_performed": False,
            "production_approval": "NONE",
            "actual_time_status": "UNAVAILABLE",
            "actual_delay_kpi": "NOT_APPROVED",
        },
    )
    return 0


def emit(record_type: str, payload: Dict[str, Any]) -> None:
    record = {"record_type": record_type}
    record.update(payload)
    print(json.dumps(record, ensure_ascii=False, sort_keys=True))


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(
        description=(
            "HOST-RUN ONLY: perform the bounded Stage 3A disruption evidence "
            "audit. The default mode uses one GTFS-RT GET and two MDD POST "
            "requests; --mode mdd-discovery uses only ten bounded MDD POST "
            "requests."
        )
    )
    argument_parser.add_argument(
        "--mode",
        choices=("full", "mdd-discovery"),
        default="full",
        help="Select the default combined audit or the host-only MDD discovery run.",
    )
    return argument_parser


def run_full_audit() -> int:
    raw_directory = create_raw_temp_directory()
    api_key = os.environ.get(MDD_API_KEY_ENV)
    mdd_api_key_present = bool(api_key)
    bindings = load_gtfs_realtime_bindings()
    tracker = AttemptTracker()

    emit(
        "run_configuration",
        {
            "probe_name": PROBE_NAME,
            "execution_mode": "HOST_RUN_ONLY",
            "codex_must_not_execute": True,
            "validated_host_runtime": VALIDATED_HOST_RUNTIME,
            "gtfs_rt_url": GTFS_RT_URL,
            "gtfs_rt_method": "GET",
            "gtfs_rt_max_http_attempts": GTFS_RT_MAX_HTTP_ATTEMPTS,
            "mdd_endpoint": MDD_ENDPOINT,
            "mdd_method": "POST",
            "mdd_stations": [
                {
                    "station": station.display_name,
                    "stop_point_ref": station.stop_point_ref,
                }
                for station in MDD_STATIONS
            ],
            "mdd_max_http_attempts": MDD_MAX_HTTP_ATTEMPTS,
            "max_http_attempts": MAX_HTTP_ATTEMPTS,
            "retries": RETRIES,
            "concurrency": CONCURRENCY,
            "fallback_endpoints": list(FALLBACK_ENDPOINTS),
            "gtfs_rt_bindings_requirement": GTFS_RT_BINDINGS_REQUIREMENT,
            "gtfs_rt_bindings_present": bindings is not None,
            "mdd_api_key_environment_variable": MDD_API_KEY_ENV,
            "mdd_api_key_present": mdd_api_key_present,
            "raw_artifact_policy": "temporary_mode_700_directory_outside_repository",
            "raw_temp_directory": raw_directory,
            "neumarkt_stop_ids_current_snapshot_only": sorted(NEUMARKT_STOP_IDS),
        },
    )

    gtfs_fetch: Optional[FetchResult] = None
    alerts: List[AlertAudit] = []
    local_context: Dict[str, Any] = {
        "neumarkt_trip_count": 0,
        "neumarkt_stop_time_update_count": 0,
        "neumarkt_matching_trip_update_count": 0,
        "neumarkt_trip_id_missing_on_matching_update_count": 0,
        "neumarkt_route_count_if_source_reported": 0,
        "neumarkt_trip_id_examples": [],
        "neumarkt_route_id_examples": [],
        "neumarkt_stop_ids_used": sorted(NEUMARKT_STOP_IDS),
        "identity_use": "CURRENT_SNAPSHOT_DIAGNOSTIC_ONLY",
        "trip_ids": set(),
        "route_ids": set(),
    }
    # The bounded source audit owns exactly one GTFS-RT request.  Parsing is a
    # separate concern: even if the validated bindings are unavailable, retain
    # the one host-run snapshot attempt and report that the payload could not
    # be interpreted rather than silently changing the request budget.
    gtfs_fetch = perform_get(tracker, raw_directory)
    if bindings is None:
        feed_summary = empty_feed_summary("BINDINGS_UNAVAILABLE")
    else:
        if gtfs_fetch.http_status == 200 and gtfs_fetch.body:
            try:
                feed = bindings.FeedMessage()
                feed.ParseFromString(gtfs_fetch.body)
                feed_summary, alerts, local_context = analyze_feed(feed, utc_now())
            except Exception:
                feed_summary = empty_feed_summary("PROTOBUF_PARSE_ERROR")
                alerts = []
        else:
            feed_summary = empty_feed_summary(
                gtfs_fetch.failure or "GTFS_RT_REQUEST_NOT_SUCCESSFUL"
            )
    feed_summary.update(
        {
            "http_status": gtfs_fetch.http_status if gtfs_fetch else None,
            "response_content_type": (
                gtfs_fetch.response_content_type if gtfs_fetch else None
            ),
            "response_bytes": gtfs_fetch.response_bytes if gtfs_fetch else 0,
            "elapsed_ms": gtfs_fetch.elapsed_ms if gtfs_fetch else None,
            "request_timestamp_utc": gtfs_fetch.request_timestamp_utc if gtfs_fetch else None,
            "raw_artifact_path": gtfs_fetch.raw_artifact_path if gtfs_fetch else None,
            "failure": gtfs_fetch.failure if gtfs_fetch else None,
        }
    )
    emit("gtfs_rt_feed_summary", feed_summary)
    emit(
        "gtfs_rt_cause_distribution",
        {
            "total_alert_count": feed_summary.get("total_alert_count", 0),
            "cause_present_count": feed_summary.get("cause_present_count", 0),
            "cause_missing_count": feed_summary.get("cause_missing_count", 0),
            "distribution": enum_distribution(alerts, "cause"),
        },
    )
    emit(
        "gtfs_rt_effect_distribution",
        {
            "total_alert_count": feed_summary.get("total_alert_count", 0),
            "effect_present_count": feed_summary.get("effect_present_count", 0),
            "effect_missing_count": feed_summary.get("effect_missing_count", 0),
            "distribution": enum_distribution(alerts, "effect"),
        },
    )
    emit(
        "gtfs_rt_presence_matrix",
        {
            "total_alert_count": feed_summary.get("total_alert_count", 0),
            "cause_explicitly_present_count": feed_summary.get(
                "cause_explicitly_present_count", 0
            ),
            "cause_explicitly_absent_count": feed_summary.get(
                "cause_explicitly_absent_count", 0
            ),
            "effect_explicitly_present_count": feed_summary.get(
                "effect_explicitly_present_count", 0
            ),
            "effect_explicitly_absent_count": feed_summary.get(
                "effect_explicitly_absent_count", 0
            ),
            "cause_presence_value_matrix": feed_summary.get(
                "cause_presence_value_matrix", {}
            ),
            "effect_presence_value_matrix": feed_summary.get(
                "effect_presence_value_matrix", {}
            ),
        },
    )
    emit(
        "gtfs_rt_detail_fields",
        {
            "total_alert_count": feed_summary.get("total_alert_count", 0),
            "severity_level_explicitly_present_count": feed_summary.get(
                "severity_level_explicitly_present_count", 0
            ),
            "cause_detail": feed_summary.get("cause_detail", {}),
            "effect_detail": feed_summary.get("effect_detail", {}),
            "url_explicitly_present_count": feed_summary.get(
                "url_explicitly_present_count", 0
            ),
        },
    )
    emit(
        "gtfs_rt_text_frequency",
        {
            "total_alert_count": feed_summary.get("total_alert_count", 0),
            "distinct_header_text_count": feed_summary.get(
                "distinct_header_text_count", 0
            ),
            "distinct_description_text_count": feed_summary.get(
                "distinct_description_text_count", 0
            ),
            "distinct_cause_detail_count": feed_summary.get(
                "distinct_cause_detail_count", 0
            ),
            "distinct_effect_detail_count": feed_summary.get(
                "distinct_effect_detail_count", 0
            ),
            "top_20_header_texts": feed_summary.get("top_20_header_texts", []),
            "top_20_description_texts": feed_summary.get(
                "top_20_description_texts", []
            ),
            "top_20_cause_details": feed_summary.get(
                "top_20_cause_details", []
            ),
            "top_20_effect_details": feed_summary.get(
                "top_20_effect_details", []
            ),
            "gtfsde_provenance_description_count": feed_summary.get(
                "gtfsde_provenance_description_count", 0
            ),
            "gtfsde_provenance_description_percentage": feed_summary.get(
                "gtfsde_provenance_description_percentage"
            ),
            "non_provenance_description_alert_count": feed_summary.get(
                "non_provenance_description_alert_count", 0
            ),
            "non_provenance_description_value_count": feed_summary.get(
                "non_provenance_description_value_count", 0
            ),
            "distinct_non_provenance_description_text_count": feed_summary.get(
                "distinct_non_provenance_description_text_count", 0
            ),
            "top_20_non_provenance_description_texts": feed_summary.get(
                "top_20_non_provenance_description_texts", []
            ),
        },
    )
    emit(
        "gtfs_rt_scope_distribution",
        {
            "total_alert_count": feed_summary.get("total_alert_count", 0),
            "informed_entity_count": feed_summary.get("informed_entity_count", 0),
            "alerts_with_trip_scope": feed_summary.get("alerts_with_trip_scope", 0),
            "alerts_with_route_scope": feed_summary.get("alerts_with_route_scope", 0),
            "alerts_with_stop_scope": feed_summary.get("alerts_with_stop_scope", 0),
            "alerts_with_agency_scope": feed_summary.get("alerts_with_agency_scope", 0),
            "alerts_with_multiple_scope_types": feed_summary.get(
                "alerts_with_multiple_scope_types", 0
            ),
            "scope_combination_counts": feed_summary.get("scope_combination_counts", {}),
            "scope_exact_combination_counts": feed_summary.get(
                "scope_exact_combination_counts", {}
            ),
        },
    )
    emit(
        "gtfs_rt_text_quality",
        {
            "header_language_counts": feed_summary.get("header_language_counts", {}),
            "description_language_counts": feed_summary.get(
                "description_language_counts", {}
            ),
            "header_text_character_length_statistics": feed_summary.get(
                "header_text_character_length_statistics", length_statistics([])
            ),
            "description_text_character_length_statistics": feed_summary.get(
                "description_text_character_length_statistics", length_statistics([])
            ),
            "max_sanitized_short_examples": feed_summary.get(
                "max_sanitized_short_examples", []
            ),
        },
    )

    local_summary = local_neumarkt_summary(alerts, local_context)
    emit(
        "gtfs_rt_neumarkt_local_summary",
        {"cologne_neumarkt_current_snapshot": local_summary},
    )

    mdd_station_summaries: List[Dict[str, Any]] = []
    if mdd_api_key_present and api_key is not None:
        for station in MDD_STATIONS:
            result = perform_mdd_post(tracker, station, api_key, raw_directory)
            mdd_station_summaries.append(analyze_mdd_response(station, result, api_key))
    else:
        for station in MDD_STATIONS:
            mdd_station_summaries.append(
                empty_mdd_station_summary(station, "MDD_API_KEY_MISSING")
            )
    for station_summary in mdd_station_summaries:
        emitted_station_summary = dict(station_summary)
        emitted_station_summary.pop("_situation_records", None)
        emit("mdd_station_summary", emitted_station_summary)

    mdd_summary = aggregate_mdd_summaries(mdd_station_summaries, api_key)
    emit("mdd_situation_summary", mdd_summary)
    emit(
        "final_summary",
        final_summary(
            feed_summary=feed_summary,
            local_summary=local_summary,
            mdd_summary=mdd_summary,
            tracker=tracker,
            bindings_present=bindings is not None,
            mdd_api_key_present=mdd_api_key_present,
        ),
    )
    return 0


def main() -> int:
    arguments = parser().parse_args()
    if arguments.mode == "mdd-discovery":
        return run_mdd_discovery_mode()
    return run_full_audit()


if __name__ == "__main__":
    raise SystemExit(main())
