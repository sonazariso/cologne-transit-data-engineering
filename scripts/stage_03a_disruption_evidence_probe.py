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
from collections import Counter
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
RETRIES = 0
CONCURRENCY = "none"
FALLBACK_ENDPOINTS: Tuple[str, ...] = ()
REQUEST_TIMEOUT_SECONDS = 30
MAX_GTFS_RT_RESPONSE_BYTES = 100 * 1024 * 1024
MAX_MDD_RESPONSE_BYTES = 25 * 1024 * 1024
MAX_TEXT_EXAMPLES = 10
MAX_SHORT_TEXT_CHARS = 240
RAW_TEMP_PREFIX = "cologne-transit-stage-03a-"

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

    def __post_init__(self) -> None:
        if self.attempts_by_source is None:
            self.attempts_by_source = {}

    def begin(self, source: str) -> None:
        """Count before opening the socket, including failures and timeouts."""

        if self.total_http_attempts >= MAX_HTTP_ATTEMPTS:
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
            return getattr(message, field_name) != field.default_value
        except (AttributeError, TypeError):
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


@dataclass
class AlertAudit:
    entity_index: int
    entity_id: Optional[str]
    cause_name: Optional[str]
    cause_numeric_value: Optional[int]
    effect_name: Optional[str]
    effect_numeric_value: Optional[int]
    header_texts: List[Tuple[str, Optional[str]]]
    description_texts: List[Tuple[str, Optional[str]]]
    active_period_present: bool
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
        else:
            raise ValueError("unsupported alert enum field")
        if name is None or numeric_value is None:
            continue
        counter[(name, numeric_value)] += 1
    all_count = len(alerts)
    present_count = sum(counter.values())
    output: List[Dict[str, Any]] = []
    coverage_field = "cause" if field_name == "cause" else "effect"
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
        selectors = [alert_selector(item) for item in get_repeated(alert, "informed_entity")]
        scope_types = scope_types_for_selectors(selectors)
        cause = enum_value(alert, "cause")
        effect = enum_value(alert, "effect")
        header_text_values = translated_texts(get_message(alert, "header_text"))
        description_text_values = translated_texts(get_message(alert, "description_text"))
        alerts.append(
            AlertAudit(
                entity_index=entity_index,
                entity_id=text_field(entity, "id"),
                cause_name=cause[0] if cause is not None else None,
                cause_numeric_value=cause[1] if cause is not None else None,
                effect_name=effect[0] if effect is not None else None,
                effect_numeric_value=effect[1] if effect is not None else None,
                header_texts=header_text_values,
                description_texts=description_text_values,
                active_period_present=bool(get_repeated(alert, "active_period")),
                informed_entity_count=len(selectors),
                selectors=selectors,
                scope_types=scope_types,
            )
        )

    total_alert_count = len(alerts)
    cause_present_count = sum(alert.cause_name is not None for alert in alerts)
    effect_present_count = sum(alert.effect_name is not None for alert in alerts)
    header_present_count = sum(bool(alert.header_texts) for alert in alerts)
    description_present_count = sum(bool(alert.description_texts) for alert in alerts)
    active_period_present_count = sum(alert.active_period_present for alert in alerts)
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
    summary: Dict[str, Any] = {
        "audit_status": "PARSED",
        "gtfs_realtime_version": text_field(feed_header, "gtfs_realtime_version"),
        "feed_header_timestamp_utc": feed_header_timestamp,
        "feed_header_age_seconds_at_request": feed_age_seconds,
        "feed_entity_count": len(entities),
        "entity_type_counts": dict(sorted(entity_type_counts.items())),
        "total_alert_count": total_alert_count,
        "cause_present_count": cause_present_count,
        "cause_missing_count": total_alert_count - cause_present_count,
        "effect_present_count": effect_present_count,
        "effect_missing_count": total_alert_count - effect_present_count,
        "header_text_present_count": header_present_count,
        "header_text_missing_count": total_alert_count - header_present_count,
        "description_text_present_count": description_present_count,
        "description_text_missing_count": total_alert_count - description_present_count,
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
            "header_text": header_coverage,
            "description_text": description_coverage,
            "active_period": percentage(active_period_present_count, total_alert_count),
        },
        "header_language_counts": header_language_counts,
        "header_text_without_explicit_language_count": header_missing_language_count,
        "description_language_counts": description_language_counts,
        "description_text_without_explicit_language_count": description_missing_language_count,
        "header_text_character_length_statistics": length_statistics(header_lengths),
        "description_text_character_length_statistics": length_statistics(
            description_lengths
        ),
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
        "cause_present_count": 0,
        "cause_missing_count": 0,
        "effect_present_count": 0,
        "effect_missing_count": 0,
        "header_text_present_count": 0,
        "header_text_missing_count": 0,
        "description_text_present_count": 0,
        "description_text_missing_count": 0,
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
            "header_text": None,
            "description_text": None,
            "active_period": None,
        },
        "header_language_counts": {},
        "header_text_without_explicit_language_count": 0,
        "description_language_counts": {},
        "description_text_without_explicit_language_count": 0,
        "header_text_character_length_statistics": length_statistics([]),
        "description_text_character_length_statistics": length_statistics([]),
        "max_sanitized_short_examples": [],
    }


def cause_effect_distribution_for_alerts(
    alerts: Sequence[AlertAudit],
) -> Dict[str, Any]:
    cause_present_count = sum(alert.cause_name is not None for alert in alerts)
    effect_present_count = sum(alert.effect_name is not None for alert in alerts)
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
        if "STOP" in link_types_set:
            stop_link_count += 1
        if "ROUTE" in link_types_set:
            route_link_count += 1
        if link_types:
            linked_alerts.append(alert)
            link_type_counts["_".join(link_types)] += 1

    linked_distribution = cause_effect_distribution_for_alerts(linked_alerts)
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
        "linked_alert_effect_present_count": linked_distribution["effect_present_count"],
        "linked_alert_effect_missing_count": linked_distribution["effect_missing_count"],
        "identity_use": local_context["identity_use"],
    }


def normalized_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def path_for_key(parent_path: str, key: Any) -> str:
    key_text = str(key)
    if key_text and all(character.isalnum() or character == "_" for character in key_text):
        return parent_path + "." + key_text
    return parent_path + "[" + json.dumps(key_text, ensure_ascii=False) + "]"


def iter_dict_entries(
    value: Any,
    path: str = "$",
) -> Iterator[Tuple[str, str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = path_for_key(path, key)
            yield child_path, str(key), child
            for item in iter_dict_entries(child, child_path):
                yield item
    elif isinstance(value, list):
        list_path = path + "[*]"
        for child in value:
            for item in iter_dict_entries(child, list_path):
                yield item


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
            item_path = path + "[*]" if isinstance(child, list) else path
            if isinstance(child, list):
                item_path += "[%d]" % index
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


REFERENCE_TYPE_NAMES = {
    "situationfullref": "SituationFullRef",
    "situationref": "SituationRef",
    "incidentcall": "IncidentCall",
}


def collect_reference_entries(parsed: Any) -> List[Tuple[str, str, Any]]:
    entries: List[Tuple[str, str, Any]] = []
    for path, key, child in iter_dict_entries(parsed):
        reference_type = REFERENCE_TYPE_NAMES.get(normalized_key(key))
        if reference_type is None:
            continue
        if isinstance(child, list):
            for index, item in enumerate(child):
                if item is not None and item != {} and item != "":
                    entries.append((path + "[%d]" % index, reference_type, item))
        elif child is not None and child != {} and child != "":
            entries.append((path, reference_type, child))
    return entries


def relation_scope_for_reference(path: str) -> str:
    lowered = path.lower()
    if re.search(r"(?:^|\.)service(?:\.|\[)", lowered):
        return "SERVICE"
    if any(
        re.search(r"(?:^|\.)" + marker + r"(?:\.|\[)", lowered)
        for marker in ("previouscall", "thiscall", "onwardcall", "callatstop")
    ):
        return "CALL"
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
) -> Tuple[str, bool, Optional[str], Optional[str], Optional[str]]:
    participant = source_scalar(direct_named_value(situation, {"participantref"}))
    situation_number = source_scalar(
        direct_named_value(situation, {"situationnumber"})
    )
    identifier = source_scalar(direct_named_value(situation, {"identifier", "id"}))
    participant_text = str(participant) if participant is not None else None
    situation_number_text = (
        str(situation_number) if situation_number is not None else None
    )
    identifier_text = str(identifier) if identifier is not None else None
    if participant_text and situation_number_text:
        return (
            "PARTICIPANT_AND_SITUATION_NUMBER|" + participant_text + "|" + situation_number_text,
            True,
            participant_text,
            situation_number_text,
            identifier_text,
        )
    if identifier_text:
        return "IDENTIFIER|" + identifier_text, True, participant_text, situation_number_text, identifier_text
    try:
        canonical = json.dumps(situation, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        canonical = path
    return "UNIDENTIFIED|" + canonical, False, participant_text, situation_number_text, identifier_text


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
    identity_key, identifiable, participant, situation_number, identifier = situation_identity(
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
        "identifier": short_text(identifier, secret),
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
    identifier_presence = sum(record["identifier"] is not None for record in records)
    return {
        "situation_count": total,
        "unique_situation_count": len(unique_keys),
        "unidentified_situation_count": unidentified_count,
        "participant_ref_present_count": participant_presence,
        "situation_number_present_count": situation_number_presence,
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


def analyze_mdd_response(
    station: MddStation,
    result: FetchResult,
    secret: Optional[str],
) -> Dict[str, Any]:
    parsed, parse_failure = parse_mdd_json(result)
    results: List[Dict[str, Any]] = []
    situation_entries: List[Tuple[str, Dict[str, Any]]] = []
    references: List[Tuple[str, str, Any]] = []
    if parsed is not None:
        results = named_results(parsed)
        situation_entries = named_object_entries(
            parsed,
            {"ptsituation", "roadsituation", "situation"},
        )
        references = collect_reference_entries(parsed)
    situation_records = [
        situation_record(path, item, secret) for path, item in situation_entries
    ]
    situation_references = [
        reference
        for reference in references
        if reference[1] in {"SituationFullRef", "SituationRef"}
    ]
    relation_counts: Counter[str] = Counter(
        relation_scope_for_reference(path) for path, _reference_type, _value in situation_references
    )
    reference_type_counts: Counter[str] = Counter(
        reference_type for _path, reference_type, _value in references
    )
    content = aggregate_situation_content(situation_records, secret)
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
        "reference_entry_count": len(references),
        "situation_reference_count": len(situation_references),
        "reference_type_counts": dict(sorted(reference_type_counts.items())),
        "incident_call_reference_count": reference_type_counts.get("IncidentCall", 0),
        "unique_situation_count": content["unique_situation_count"],
        "call_level_situation_link_count": relation_counts.get("CALL", 0),
        "service_level_situation_link_count": relation_counts.get("SERVICE", 0),
        "other_unclassified_relation_count": relation_counts.get(
            "OTHER_OR_UNCLASSIFIED", 0
        ),
        "relation_scope_counts": {
            "CALL": relation_counts.get("CALL", 0),
            "SERVICE": relation_counts.get("SERVICE", 0),
            "OTHER_OR_UNCLASSIFIED": relation_counts.get(
                "OTHER_OR_UNCLASSIFIED", 0
            ),
        },
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
    }
    return output


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
    relation_keys = ("CALL", "SERVICE", "OTHER_OR_UNCLASSIFIED")
    relation_counts = {
        key: sum(summary.get("relation_scope_counts", {}).get(key, 0) for summary in station_summaries)
        for key in relation_keys
    }
    station_views = []
    for summary in station_summaries:
        view = dict(summary)
        view.pop("_situation_records", None)
        station_views.append(view)
    return {
        "sample_station_count": len(station_summaries),
        "sample_result_count": result_count,
        "situation_count": content["situation_count"],
        "reference_entry_count": reference_entry_count,
        "situation_reference_count": situation_reference_count,
        "reference_type_counts": dict(sorted(reference_type_counts.items())),
        "incident_call_reference_count": reference_type_counts.get("IncidentCall", 0),
        "unique_situation_count": content["unique_situation_count"],
        "call_level_situation_link_count": relation_counts["CALL"],
        "service_level_situation_link_count": relation_counts["SERVICE"],
        "other_unclassified_relation_count": relation_counts["OTHER_OR_UNCLASSIFIED"],
        "relation_scope_counts": relation_counts,
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
        "http_attempt_budget": MAX_HTTP_ATTEMPTS,
        "http_attempts_by_source": dict(sorted(tracker.attempts_by_source.items())),
        "http_budget_compliance": tracker.total_http_attempts <= MAX_HTTP_ATTEMPTS,
        "gtfs_rt_cause_population_percentage": cause_percentage,
        "gtfs_rt_cause_evidence_statement": cause_statement,
        "gtfs_rt_effect_population_percentage": effect_percentage,
        "gtfs_rt_effect_evidence_statement": effect_statement,
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
        "mdd_human_readable_situation_evidence_statement": mdd_text_statement,
        "mdd_situation_text_population_counts": mdd_text_counts,
        "mdd_reference_type_counts": mdd_summary.get("reference_type_counts", {}),
        "mdd_incident_call_reference_count": mdd_summary.get(
            "incident_call_reference_count", 0
        ),
        "mdd_association_scope_counts": mdd_summary.get("relation_scope_counts", {}),
        "mdd_association_scope_statement": (
            "MDD association counts are retained by relation scope: CALL=%s, "
            "SERVICE=%s, OTHER_OR_UNCLASSIFIED=%s. Association does not establish "
            "causality."
            % (
                mdd_summary.get("call_level_situation_link_count", 0),
                mdd_summary.get("service_level_situation_link_count", 0),
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


def emit(record_type: str, payload: Dict[str, Any]) -> None:
    record = {"record_type": record_type}
    record.update(payload)
    print(json.dumps(record, ensure_ascii=False, sort_keys=True))


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(
        description=(
            "HOST-RUN ONLY: perform the bounded Stage 3A disruption evidence "
            "audit (one GTFS-RT GET and two MDD POST requests)."
        )
    )
    return argument_parser


def main() -> int:
    parser().parse_args()
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
    if bindings is None:
        feed_summary = empty_feed_summary("BINDINGS_UNAVAILABLE")
    else:
        gtfs_fetch = perform_get(tracker, raw_directory)
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
                {
                    "station": station.display_name,
                    "station_key": station.key,
                    "stop_point_ref": station.stop_point_ref,
                    "response_audit_status": "MDD_API_KEY_MISSING",
                    "http_status": None,
                    "response_content_type": None,
                    "response_bytes": 0,
                    "elapsed_ms": None,
                    "request_timestamp_utc": None,
                    "raw_artifact_path": None,
                    "result_count": 0,
                    "situation_count": 0,
                    "reference_entry_count": 0,
                    "situation_reference_count": 0,
                    "reference_type_counts": {},
                    "incident_call_reference_count": 0,
                    "unique_situation_count": 0,
                    "call_level_situation_link_count": 0,
                    "service_level_situation_link_count": 0,
                    "other_unclassified_relation_count": 0,
                    "relation_scope_counts": {
                        "CALL": 0,
                        "SERVICE": 0,
                        "OTHER_OR_UNCLASSIFIED": 0,
                    },
                    "summary_present_count": 0,
                    "description_present_count": 0,
                    "detail_present_count": 0,
                    "valid_from_present_count": 0,
                    "valid_to_present_count": 0,
                    "content_coverage_percentages": {
                        "participant_ref": None,
                        "situation_number": None,
                        "identifier": None,
                        "summary": None,
                        "description": None,
                        "detail": None,
                        "valid_from": None,
                        "valid_to": None,
                    },
                    "situation_content": aggregate_situation_content([]),
                    "_situation_records": [],
                }
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


if __name__ == "__main__":
    raise SystemExit(main())
