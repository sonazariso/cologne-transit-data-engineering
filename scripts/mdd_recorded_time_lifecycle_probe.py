#!/usr/bin/env python3
"""Near-event RecordedAtTime lifecycle probe for manual host execution.

This is a diagnostic-only script. It makes one discovery request and at most
six sequential follow-up requests against the proven MDD/TRIAS contract. It
does not retry, follow redirects, execute SQL, invoke the Legacy repository, or
write raw responses into the repository.

Codex must not execute this script. Run it manually from a trusted macOS host
only after reviewing the accompanying lifecycle-validation document.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from email.message import Message
from http.client import HTTPResponse
from typing import Any, Iterable, Iterator
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


ENDPOINT = "https://mdd.gorheinland.com/delfi"
STATION = "Köln Hbf"
STOP_POINT_REF = "de:05315:11201"
REQUESTOR_REF = "CologneTransitIntelligence"
STOP_EVENT_TYPE = "arrival"
INCLUDE_PREVIOUS_CALLS = True
INCLUDE_ONWARD_CALLS = True
INCLUDE_REALTIME_DATA = True
DISCOVERY_NUMBER_OF_RESULTS = 20
FOLLOW_UP_NUMBER_OF_RESULTS = 20
REQUEST_TIMEOUT_SECONDS = 30
RETRIES = 0
MAX_HTTP_ATTEMPTS = 7
RAW_TEMP_PREFIX = "cologne-transit-recorded-time-lifecycle-"

FOLLOW_UP_CHECKPOINTS: tuple[tuple[str, int], ...] = (
    ("T-5_MINUTES", -5),
    ("T-1_MINUTE", -1),
    ("T+1_MINUTE", 1),
    ("T+5_MINUTES", 5),
    ("T+15_MINUTES", 15),
    ("T+30_MINUTES", 30),
)

RESULT_KEYS = {"stopeventresult"}
THIS_CALL_KEYS = {"thiscall"}
PREVIOUS_CALL_KEYS = {"previouscall"}
ONWARD_CALL_KEYS = {"onwardcall"}
STOP_EVENT_KEYS = {"stopevent"}
CALL_AT_STOP_KEYS = {"callatstop"}
SERVICE_KEYS = {"service"}
SERVICE_ARRIVAL_KEYS = {"servicearrival"}
SERVICE_DEPARTURE_KEYS = {"servicedeparture"}
TIMETABLED_KEYS = {"timetabledtime"}
ESTIMATED_KEYS = {"estimatedtime"}
RECORDED_KEYS = {"recordedattime"}
ERROR_KEYS = {"errormessage", "errorcode", "code", "message"}
PROVIDER_GMT_SUFFIX = "[GMT]"

TARGET_FOUND = "TARGET_FOUND"
TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
TARGET_MATCH_AMBIGUOUS = "TARGET_MATCH_AMBIGUOUS"


class NoRedirectHandler(HTTPRedirectHandler):
    """Prevent urllib from turning one logical probe into multiple requests."""

    def redirect_request(
        self,
        req: Request,
        fp: HTTPResponse,
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> None:
        return None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def build_request_body(
    request_timestamp_utc: str,
    dep_arr_time_utc: str,
    number_of_results: int,
) -> bytes:
    """Build the verified XML/TRIAS 1.2 StopEvent request."""

    body = f'''<?xml version="1.0" encoding="UTF-8"?>
<Trias xmlns="http://www.vdv.de/trias"
       xmlns:siri="http://www.siri.org.uk/siri"
       version="1.2">
  <ServiceRequest>
    <siri:RequestTimestamp>{request_timestamp_utc}</siri:RequestTimestamp>
    <siri:RequestorRef>{REQUESTOR_REF}</siri:RequestorRef>
    <RequestPayload>
      <StopEventRequest>
        <Location>
          <LocationRef>
            <StopPointRef>{STOP_POINT_REF}</StopPointRef>
          </LocationRef>
          <DepArrTime>{dep_arr_time_utc}</DepArrTime>
        </Location>
        <Params>
          <NumberOfResults>{number_of_results}</NumberOfResults>
          <StopEventType>{STOP_EVENT_TYPE}</StopEventType>
          <IncludePreviousCalls>{str(INCLUDE_PREVIOUS_CALLS).lower()}</IncludePreviousCalls>
          <IncludeOnwardCalls>{str(INCLUDE_ONWARD_CALLS).lower()}</IncludeOnwardCalls>
          <IncludeRealtimeData>{str(INCLUDE_REALTIME_DATA).lower()}</IncludeRealtimeData>
        </Params>
      </StopEventRequest>
    </RequestPayload>
  </ServiceRequest>
</Trias>
'''
    return body.encode("utf-8")


def response_charset(content_type: str | None) -> str:
    if content_type:
        for part in content_type.split(";")[1:]:
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            if name.strip().lower() == "charset":
                return value.strip().strip('"') or "utf-8"
    return "utf-8"


def decode_response(content: bytes, content_type: str | None) -> str:
    try:
        return content.decode(response_charset(content_type), errors="replace")
    except LookupError:
        return content.decode("utf-8", errors="replace")


def create_raw_temp_directory() -> str:
    """Create the one mode-700 raw-artifact directory outside the repository."""

    raw_directory = tempfile.mkdtemp(prefix=RAW_TEMP_PREFIX, dir="/private/tmp")
    try:
        os.chmod(raw_directory, 0o700)
    except OSError:
        pass
    return raw_directory


def write_raw_artifact(
    raw_directory: str,
    artifact_name: str,
    content: bytes,
    api_key: str,
) -> str | None:
    """Write a body only after confirming it does not contain the API key."""

    if api_key.encode("utf-8") in content:
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


def safe_text(value: Any, api_key: str) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text.replace(api_key, "[REDACTED]") if api_key else text


def safe_value(value: Any, api_key: str) -> Any:
    """Return a bounded scalar/list representation, never an arbitrary payload."""

    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return safe_text(value, api_key)
    if isinstance(value, list):
        return [safe_value(item, api_key) for item in value]
    if isinstance(value, dict):
        return {
            safe_text(key, api_key): safe_value(child, api_key)
            for key, child in value.items()
        }
    return safe_text(value, api_key)


def normalized_key(key: Any) -> str:
    return str(key).replace("_", "").lower()


def path_for_key(parent_path: str, key: Any) -> str:
    key_text = str(key)
    if key_text and all(character.isalnum() or character == "_" for character in key_text):
        return f"{parent_path}.{key_text}"
    return f"{parent_path}[{json.dumps(key_text, ensure_ascii=False)}]"


def iter_dict_entries(
    value: Any,
    path: str = "$",
) -> Iterator[tuple[str, str, Any]]:
    """Yield dictionary entries with structural paths without printing payloads."""

    if isinstance(value, dict):
        for key, child in value.items():
            child_path = path_for_key(path, key)
            yield child_path, str(key), child
            yield from iter_dict_entries(child, child_path)
    elif isinstance(value, list):
        list_path = f"{path}[*]"
        for child in value:
            yield from iter_dict_entries(child, list_path)


def direct_named_value(value: Any, names: set[str]) -> Any:
    """Return a direct child by case-insensitive semantic field name."""

    if not isinstance(value, dict):
        return None
    for key, child in value.items():
        if normalized_key(key) in names:
            return child
    return None


def first_named_value(value: Any, names: set[str]) -> Any:
    for _path, key, child in iter_dict_entries(value):
        if normalized_key(key) in names:
            return child
    return None


def source_value(value: Any) -> Any:
    """Extract a source scalar/list without emitting arbitrary nested objects."""

    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, str) and not value.strip():
            return None
        return value
    if isinstance(value, dict):
        for name in ("value", "text", "name"):
            child = direct_named_value(value, {name})
            if child is not None:
                extracted = source_value(child)
                if extracted is not None:
                    return extracted
        return None
    if isinstance(value, list):
        extracted_values = [source_value(item) for item in value]
        extracted_values = [item for item in extracted_values if item is not None]
        if not extracted_values:
            return None
        if len(extracted_values) == 1:
            return extracted_values[0]
        return extracted_values
    return None


def named_source_values(value: Any, names: set[str]) -> list[tuple[str, Any]]:
    values: list[tuple[str, Any]] = []
    for path, key, child in iter_dict_entries(value):
        if normalized_key(key) not in names:
            continue
        extracted = source_value(child)
        if extracted is not None:
            values.append((path, extracted))
    return values


def first_named_source_value(value: Any, names: set[str]) -> Any:
    values = named_source_values(value, names)
    return values[0][1] if values else None


def as_object_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def scope_calls(stop_event: Any, scope: str) -> list[dict[str, Any]]:
    names = {
        "thiscall": THIS_CALL_KEYS,
        "previouscall": PREVIOUS_CALL_KEYS,
        "onwardcall": ONWARD_CALL_KEYS,
    }[scope]
    raw_value = direct_named_value(stop_event, names)
    return as_object_list(raw_value)


def call_at_stop(call: Any) -> dict[str, Any] | None:
    value = direct_named_value(call, CALL_AT_STOP_KEYS)
    return value if isinstance(value, dict) else None


def timing_container(call_stop: Any, field_names: set[str]) -> Any:
    return direct_named_value(call_stop, field_names)


def nonempty_timing_value(container: Any, names: set[str]) -> Any:
    return first_named_source_value(container, names)


def timing_summary(call_stop: Any, field_names: set[str]) -> dict[str, Any] | None:
    container = timing_container(call_stop, field_names)
    if container is None:
        return None
    return {
        "timetabled_time": nonempty_timing_value(container, TIMETABLED_KEYS),
        "estimated_time": nonempty_timing_value(container, ESTIMATED_KEYS),
        "recorded_at_time": nonempty_timing_value(container, RECORDED_KEYS),
    }


def call_scope_counts(
    stop_event: Any,
    scope: str,
) -> tuple[int, int, int, int]:
    calls = scope_calls(stop_event, scope)
    estimated_count = 0
    recorded_count = 0
    timetabled_count = 0
    for call in calls:
        call_stop = call_at_stop(call)
        if call_stop is None:
            continue
        for field_names in (SERVICE_ARRIVAL_KEYS, SERVICE_DEPARTURE_KEYS):
            timing = timing_container(call_stop, field_names)
            timetabled_count += len(named_source_values(timing, TIMETABLED_KEYS))
            estimated_count += len(named_source_values(timing, ESTIMATED_KEYS))
            recorded_count += len(named_source_values(timing, RECORDED_KEYS))
    return len(calls), timetabled_count, estimated_count, recorded_count


def normalize_timestamp_text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(PROVIDER_GMT_SUFFIX):
        text = text[: -len(PROVIDER_GMT_SUFFIX)]
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    return text


def parse_timestamp(value: Any) -> datetime | None:
    normalized = normalize_timestamp_text(value)
    if normalized is None:
        return None
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def parse_aware_timestamp(value: Any) -> datetime | None:
    parsed = parse_timestamp(value)
    if parsed is None or parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def timestamps_equal(left: Any, right: Any) -> bool:
    left_parsed = parse_aware_timestamp(left)
    right_parsed = parse_aware_timestamp(right)
    if left_parsed is not None and right_parsed is not None:
        return left_parsed == right_parsed
    return isinstance(left, str) and isinstance(right, str) and left.strip() == right.strip()


def first_reference_from_service(service: Any, names: set[str]) -> Any:
    return first_named_source_value(service, names)


def service_identity(service: Any) -> dict[str, Any]:
    operating_day_ref = first_reference_from_service(service, {"operatingdayref"})
    journey_ref = first_reference_from_service(service, {"journeyref"})
    operator_ref = first_reference_from_service(service, {"operatorref"})
    line_ref = first_reference_from_service(service, {"lineref"})
    direction_ref = first_reference_from_service(service, {"directionref"})
    sections = as_object_list(direct_named_value(service, {"servicesection"}))
    for section in sections:
        line_ref = line_ref or first_reference_from_service(section, {"lineref"})
        direction_ref = direction_ref or first_reference_from_service(
            section, {"directionref"}
        )
        operator_ref = operator_ref or first_reference_from_service(
            section, {"operatorref"}
        )
    return {
        "operating_day_ref": operating_day_ref,
        "journey_ref": journey_ref,
        "line_ref": line_ref,
        "direction_ref": direction_ref,
        "operator_ref": operator_ref,
    }


def extract_event(result: Any) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    stop_event = direct_named_value(result, STOP_EVENT_KEYS)
    if not isinstance(stop_event, dict):
        return None
    this_calls = scope_calls(stop_event, "thiscall")
    if not this_calls:
        return None
    this_call = this_calls[0]
    this_call_stop = call_at_stop(this_call)
    if this_call_stop is None:
        return None
    arrival_container = timing_container(this_call_stop, SERVICE_ARRIVAL_KEYS)
    if arrival_container is None:
        return None
    service = direct_named_value(stop_event, SERVICE_KEYS)
    if not isinstance(service, (dict, list)):
        service = {}
    service_object = service if isinstance(service, dict) else (service[0] if service else {})
    identity = service_identity(service_object)
    stop_point_ref = first_named_source_value(this_call_stop, {"stoppointref"})
    stop_seq_number = first_named_source_value(this_call_stop, {"stopseqnumber"})
    return {
        "result": result,
        "stop_event": stop_event,
        "service": service_object,
        "this_call": this_call,
        "this_call_stop": this_call_stop,
        "identity": {
            **identity,
            "stop_point_ref": stop_point_ref,
            "stop_seq_number": stop_seq_number,
            "arrival_context": "arrival",
            "timetabled_time": nonempty_timing_value(arrival_container, TIMETABLED_KEYS),
        },
        "this_call_arrival": timing_summary(this_call_stop, SERVICE_ARRIVAL_KEYS),
        "this_call_departure": timing_summary(this_call_stop, SERVICE_DEPARTURE_KEYS),
    }


def named_results(parsed: Any) -> list[Any]:
    result_value = first_named_value(parsed, RESULT_KEYS)
    if isinstance(result_value, list):
        return result_value
    if isinstance(result_value, dict):
        return [result_value]
    return []


def application_errors(parsed: Any) -> list[str]:
    errors: list[str] = []
    for _path, key, value in iter_dict_entries(parsed):
        if normalized_key(key) not in ERROR_KEYS:
            continue
        extracted = source_value(value)
        if extracted is None:
            continue
        candidates = extracted if isinstance(extracted, list) else [extracted]
        for candidate in candidates:
            if isinstance(candidate, (str, int, float)) and str(candidate).strip():
                text = str(candidate)
                if text not in errors:
                    errors.append(text)
    return errors


def analyze_json(content: bytes, content_type: str | None) -> dict[str, Any]:
    decoded = decode_response(content, content_type)
    try:
        parsed = json.loads(decoded)
    except (TypeError, ValueError):
        return {
            "json_parse_success": False,
            "parsed": None,
            "results": [],
            "application_errors": ["JSON_PARSE_ERROR"],
        }
    return {
        "json_parse_success": True,
        "parsed": parsed,
        "results": named_results(parsed),
        "application_errors": application_errors(parsed),
    }


def discovery_reference(event: dict[str, Any]) -> tuple[datetime | None, str | None]:
    arrival = event["this_call_arrival"] or {}
    estimated = arrival.get("estimated_time")
    estimated_dt = parse_aware_timestamp(estimated)
    if estimated_dt is not None:
        return estimated_dt, "ESTIMATED"
    timetabled = arrival.get("timetabled_time")
    timetabled_dt = parse_aware_timestamp(timetabled)
    if timetabled_dt is not None:
        return timetabled_dt, "TIMETABLED"
    return None, None


def select_target(
    results: Iterable[Any],
    now_utc: datetime,
) -> tuple[dict[str, Any] | None, datetime | None, str | None]:
    candidates: list[tuple[datetime, str, dict[str, Any]]] = []
    lower_bound = now_utc + timedelta(minutes=8)
    upper_bound = now_utc + timedelta(minutes=25)
    for result in results:
        event = extract_event(result)
        if event is None:
            continue
        identity = event["identity"]
        if not identity.get("journey_ref") or not identity.get("stop_point_ref"):
            continue
        timetabled_dt = parse_aware_timestamp(identity.get("timetabled_time"))
        if timetabled_dt is None:
            continue
        reference_dt, reference_source = discovery_reference(event)
        if reference_dt is None or reference_source is None:
            continue
        if not lower_bound <= reference_dt <= upper_bound:
            continue
        candidates.append((reference_dt, str(identity.get("journey_ref")), event))
    if not candidates:
        return None, None, None
    candidates.sort(key=lambda item: (item[0], item[1]))
    event = candidates[0][2]
    reference_dt, reference_source = discovery_reference(event)
    return event, reference_dt, reference_source


def identity_matches(target: dict[str, Any], candidate: dict[str, Any]) -> bool:
    target_identity = target["identity"]
    candidate_identity = candidate["identity"]
    for field_name in ("journey_ref", "stop_point_ref"):
        target_value = target_identity.get(field_name)
        candidate_value = candidate_identity.get(field_name)
        if not target_value or target_value != candidate_value:
            return False
    if not timestamps_equal(
        target_identity.get("timetabled_time"), candidate_identity.get("timetabled_time")
    ):
        return False
    for field_name in (
        "operating_day_ref",
        "line_ref",
        "direction_ref",
        "operator_ref",
        "stop_seq_number",
    ):
        target_value = target_identity.get(field_name)
        candidate_value = candidate_identity.get(field_name)
        if target_value and candidate_value and target_value != candidate_value:
            return False
    return candidate_identity.get("arrival_context") == "arrival"


def locate_target(
    results: Iterable[Any],
    target: dict[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    matches: list[dict[str, Any]] = []
    for result in results:
        event = extract_event(result)
        if event is not None and identity_matches(target, event):
            matches.append(event)
    if len(matches) == 1:
        return TARGET_FOUND, matches[0]
    if len(matches) > 1:
        return TARGET_MATCH_AMBIGUOUS, None
    return TARGET_NOT_FOUND, None


def iter_timing_containers(
    value: Any,
    path: str = "$",
) -> Iterator[tuple[str, str, dict[str, Any]]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = path_for_key(path, key)
            key_name = normalized_key(key)
            if key_name in SERVICE_ARRIVAL_KEYS | SERVICE_DEPARTURE_KEYS:
                for item in as_object_list(child):
                    item_path = f"{child_path}[*]" if isinstance(child, list) else child_path
                    yield item_path, key_name, item
            yield from iter_timing_containers(child, child_path)
    elif isinstance(value, list):
        list_path = f"{path}[*]"
        for child in value:
            yield from iter_timing_containers(child, list_path)


def context_for_path(path: str) -> str:
    lowered = path.lower()
    scope = ""
    if "thiscall" in lowered:
        scope = "THIS_CALL"
    elif "previouscall" in lowered:
        scope = "PREVIOUS_CALL"
    elif "onwardcall" in lowered:
        scope = "ONWARD_CALL"
    timing = ""
    if "servicearrival" in lowered:
        timing = "ARRIVAL"
    elif "servicedeparture" in lowered:
        timing = "DEPARTURE"
    if scope and timing:
        return f"{scope}_{timing}"
    return "OTHER_TARGET_RESULT_CONTEXT"


def nearest_timing_container(
    path: str,
    timing_containers: list[tuple[str, str, dict[str, Any]]],
) -> tuple[str, dict[str, Any]] | None:
    candidates: list[tuple[int, str, dict[str, Any]]] = []
    for container_path, container_type, container in timing_containers:
        if path == container_path or path.startswith(f"{container_path}.") or path.startswith(
            f"{container_path}["
        ):
            candidates.append((len(container_path), container_type, container))
    if not candidates:
        return None
    _length, container_type, container = max(candidates, key=lambda item: item[0])
    return container_type, container


def diagnostic_delay_candidate(
    context: str,
    recorded_at_time: Any,
    timetabled_time: Any,
) -> dict[str, Any] | None:
    if context != "THIS_CALL_ARRIVAL":
        return None
    recorded_dt = parse_aware_timestamp(recorded_at_time)
    timetabled_dt = parse_aware_timestamp(timetabled_time)
    if recorded_dt is None or timetabled_dt is None:
        return None
    return {
        "label": "DIAGNOSTIC_ACTUAL_DELAY_CANDIDATE",
        "recorded_at_time": recorded_at_time,
        "timetabled_time": timetabled_time,
        "delay_seconds": round((recorded_dt - timetabled_dt).total_seconds(), 3),
    }


def recorded_at_time_occurrences(event: dict[str, Any]) -> list[dict[str, Any]]:
    result = event["result"]
    timing_containers = list(iter_timing_containers(result, "$.matched_target"))
    occurrences: list[dict[str, Any]] = []
    for path, key, value in iter_dict_entries(result, "$.matched_target"):
        if normalized_key(key) not in RECORDED_KEYS:
            continue
        extracted = source_value(value)
        if extracted is None:
            continue
        timing_match = nearest_timing_container(path, timing_containers)
        corresponding_timetabled = None
        corresponding_estimated = None
        if timing_match is not None:
            _timing_type, timing_value = timing_match
            corresponding_timetabled = nonempty_timing_value(timing_value, TIMETABLED_KEYS)
            corresponding_estimated = nonempty_timing_value(timing_value, ESTIMATED_KEYS)
        context = context_for_path(path)
        occurrence = {
            "structural_context": context,
            "structural_json_path": path,
            "value": extracted,
            "corresponding_timetabled_time": corresponding_timetabled,
            "corresponding_estimated_time": corresponding_estimated,
        }
        candidate = diagnostic_delay_candidate(
            context,
            extracted,
            corresponding_timetabled,
        )
        if candidate is not None:
            occurrence["diagnostic_actual_delay_candidate"] = candidate
        occurrences.append(occurrence)
    return occurrences


def event_identity_output(event: dict[str, Any] | None, api_key: str) -> dict[str, Any] | None:
    if event is None:
        return None
    identity = event["identity"]
    return {
        "operating_day_ref": safe_value(identity.get("operating_day_ref"), api_key),
        "journey_ref": safe_value(identity.get("journey_ref"), api_key),
        "line_ref": safe_value(identity.get("line_ref"), api_key),
        "direction_ref": safe_value(identity.get("direction_ref"), api_key),
        "stop_point_ref": safe_value(identity.get("stop_point_ref"), api_key),
        "timetabled_time": safe_value(identity.get("timetabled_time"), api_key),
    }


def matched_observation_summary(
    *,
    observation_index: int,
    observation_label: str,
    planned_observation_time: datetime | None,
    request_timestamp: datetime,
    initial_reference_time: datetime | None,
    http_status: int | None,
    elapsed_ms: float,
    target_match_status: str,
    matched_event: dict[str, Any] | None,
    analysis: dict[str, Any],
    raw_artifact_path: str | None,
    failure: str | None,
    api_key: str,
) -> dict[str, Any]:
    seconds_relative = None
    if initial_reference_time is not None:
        seconds_relative = round(
            (request_timestamp - initial_reference_time).total_seconds(), 3
        )
    summary: dict[str, Any] = {
        "observation_index": observation_index,
        "observation_label": observation_label,
        "request_timestamp_utc": iso_utc(request_timestamp),
        "planned_observation_time_utc": (
            iso_utc(planned_observation_time) if planned_observation_time is not None else None
        ),
        "seconds_relative_to_initial_reference": seconds_relative,
        "http_status": http_status,
        "elapsed_ms": round(elapsed_ms, 3),
        "target_match_status": target_match_status,
        "operating_day_ref": None,
        "journey_ref": None,
        "line_ref": None,
        "direction_ref": None,
        "stop_point_ref": None,
        "timetabled_time": None,
        "this_call_arrival": None,
        "this_call_departure": None,
        "previouscall_count": 0,
        "previouscall_estimated_time_count": 0,
        "previouscall_recorded_time_count": 0,
        "onwardcall_count": 0,
        "onwardcall_estimated_time_count": 0,
        "onwardcall_recorded_time_count": 0,
        "thiscall_count": 0,
        "thiscall_estimated_time_count": 0,
        "thiscall_recorded_time_count": 0,
        "operational": {
            "not_serviced_stop": None,
            "unplanned_stop": None,
            "no_boarding_at_stop": None,
            "no_alighting_at_stop": None,
            "journey_cancelled": None,
            "deviation": None,
        },
        "platform": {"planned_bay": None, "estimated_bay": None},
        "recorded_at_time_occurrences": [],
        "diagnostic_actual_delay_candidates": [],
        "application_errors": [safe_text(item, api_key) for item in analysis["application_errors"]],
        "request_failure": safe_text(failure, api_key),
        "raw_artifact_path": raw_artifact_path,
    }
    if matched_event is None:
        return summary

    identity = matched_event["identity"]
    summary.update(
        {
            "operating_day_ref": safe_value(identity.get("operating_day_ref"), api_key),
            "journey_ref": safe_value(identity.get("journey_ref"), api_key),
            "line_ref": safe_value(identity.get("line_ref"), api_key),
            "direction_ref": safe_value(identity.get("direction_ref"), api_key),
            "stop_point_ref": safe_value(identity.get("stop_point_ref"), api_key),
            "timetabled_time": safe_value(identity.get("timetabled_time"), api_key),
            "this_call_arrival": safe_value(matched_event["this_call_arrival"], api_key),
            "this_call_departure": safe_value(matched_event["this_call_departure"], api_key),
        }
    )
    stop_event = matched_event["stop_event"]
    for scope, prefix in (
        ("thiscall", "thiscall"),
        ("previouscall", "previouscall"),
        ("onwardcall", "onwardcall"),
    ):
        count, _timetabled, estimated, recorded = call_scope_counts(stop_event, scope)
        summary[f"{prefix}_count"] = count
        summary[f"{prefix}_estimated_time_count"] = estimated
        summary[f"{prefix}_recorded_time_count"] = recorded

    call_stop = matched_event["this_call_stop"]
    summary["operational"] = {
        "not_serviced_stop": safe_value(
            first_named_source_value(call_stop, {"notservicedstop"}), api_key
        ),
        "unplanned_stop": safe_value(
            first_named_source_value(call_stop, {"unplannedstop"}), api_key
        ),
        "no_boarding_at_stop": safe_value(
            first_named_source_value(call_stop, {"noboardingatstop"}), api_key
        ),
        "no_alighting_at_stop": safe_value(
            first_named_source_value(call_stop, {"noalightingatstop"}), api_key
        ),
        "journey_cancelled": safe_value(
            first_named_source_value(stop_event, {"cancelled", "journeycancelled"}), api_key
        ),
        "deviation": safe_value(
            first_named_source_value(matched_event["service"], {"deviation"}), api_key
        ),
    }
    summary["platform"] = {
        "planned_bay": safe_value(
            first_named_source_value(call_stop, {"plannedbay"}), api_key
        ),
        "estimated_bay": safe_value(
            first_named_source_value(call_stop, {"estimatedbay"}), api_key
        ),
    }
    occurrences = recorded_at_time_occurrences(matched_event)
    summary["recorded_at_time_occurrences"] = safe_value(occurrences, api_key)
    summary["diagnostic_actual_delay_candidates"] = safe_value(
        [
            occurrence["diagnostic_actual_delay_candidate"]
            for occurrence in occurrences
            if "diagnostic_actual_delay_candidate" in occurrence
        ],
        api_key,
    )
    return summary


def perform_http_request(
    *,
    api_key: str,
    dep_arr_time_utc: str,
    number_of_results: int,
) -> dict[str, Any]:
    """Perform exactly one HTTP attempt; this function contains no retry path."""

    request_timestamp = utc_now()
    request_timestamp_utc = format_utc(request_timestamp)
    body = build_request_body(request_timestamp_utc, dep_arr_time_utc, number_of_results)
    request = Request(
        ENDPOINT,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/xml; charset=utf-8",
            "x-api-key": api_key,
        },
    )
    started = time.perf_counter()
    status: int | None = None
    content_type: str | None = None
    content = b""
    failure: str | None = None
    opener = build_opener(NoRedirectHandler())
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status = int(response.status)
            content_type = response.headers.get("Content-Type")
            content = response.read()
    except HTTPError as error:
        status = int(error.code)
        content_type = error.headers.get("Content-Type") if error.headers else None
        content = error.read()
        failure = f"HTTP_{status}"
    except (URLError, TimeoutError, OSError) as error:
        failure = f"TRANSPORT_{type(error).__name__}"
    elapsed_ms = (time.perf_counter() - started) * 1000
    return {
        "request_timestamp": request_timestamp,
        "request_timestamp_utc": request_timestamp_utc,
        "http_status": status,
        "content_type": content_type,
        "content": content,
        "elapsed_ms": elapsed_ms,
        "failure": failure,
    }


def response_stop_reason(response: dict[str, Any]) -> str | None:
    failure = response.get("failure")
    status = response.get("http_status")
    if isinstance(failure, str) and failure.startswith("TRANSPORT_"):
        return failure
    if status in {401, 403, 429}:
        return f"HTTP_{status}"
    if isinstance(status, int) and 500 <= status <= 599:
        return "HTTP_5XX"
    if status is not None and status != 200:
        return f"HTTP_{status}"
    return None


def build_planned_observations(
    initial_reference_time: datetime,
    selection_time: datetime,
) -> list[dict[str, Any]]:
    planned: list[dict[str, Any]] = []
    for observation_index, (label, offset_minutes) in enumerate(FOLLOW_UP_CHECKPOINTS, start=1):
        planned_time = initial_reference_time + timedelta(minutes=offset_minutes)
        skipped = planned_time <= selection_time
        planned.append(
            {
                "observation_index": observation_index,
                "observation_label": label,
                "offset_minutes": offset_minutes,
                "planned_observation_time_utc": iso_utc(planned_time),
                "skipped": skipped,
                "skip_reason": "PAST_AT_SELECTION" if skipped else None,
                "planned_time": planned_time,
            }
        )
    return planned


def print_json(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def wait_until(target_time: datetime) -> None:
    """Sleep locally until a scheduled checkpoint without issuing extra requests."""

    while True:
        remaining = (target_time - utc_now()).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 60.0))


def recorded_occurrences_from_observations(
    observations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    occurrences: list[dict[str, Any]] = []
    for observation in observations:
        for occurrence in observation.get("recorded_at_time_occurrences", []):
            if isinstance(occurrence, dict):
                occurrences.append(
                    {
                        "observation_index": observation["observation_index"],
                        "observation_label": observation["observation_label"],
                        **occurrence,
                    }
                )
    return occurrences


def classify_lifecycle(
    observations: list[dict[str, Any]],
    initial_reference_time: datetime | None,
    stopped_early: bool,
    no_suitable_target: bool,
) -> str:
    if no_suitable_target:
        return "NO_SUITABLE_TARGET"
    if any(
        observation.get("target_match_status") == TARGET_MATCH_AMBIGUOUS
        for observation in observations
    ):
        return "TARGET_MATCH_BECAME_AMBIGUOUS"
    occurrences = recorded_occurrences_from_observations(observations)
    contexts = {occurrence.get("structural_context") for occurrence in occurrences}
    if any(context in {"THIS_CALL_ARRIVAL", "THIS_CALL_DEPARTURE"} for context in contexts):
        return "THIS_CALL_RECORDED_AT_TIME_OBSERVED"
    if any(
        context in {"PREVIOUS_CALL_ARRIVAL", "PREVIOUS_CALL_DEPARTURE"}
        for context in contexts
    ):
        return "RECORDED_AT_TIME_OBSERVED_IN_PREVIOUS_CALLS_ONLY"
    if occurrences:
        return "RECORDED_AT_TIME_OBSERVED_OTHER_CONTEXT"

    found = [
        observation
        for observation in observations
        if observation.get("target_match_status") == TARGET_FOUND
    ]
    if not found or initial_reference_time is None:
        return "INCONCLUSIVE" if stopped_early else "INCONCLUSIVE"

    post_reference = [
        observation
        for observation in observations
        if isinstance(observation.get("seconds_relative_to_initial_reference"), (int, float))
        and observation["seconds_relative_to_initial_reference"] >= 0
    ]
    post_reference_found = [
        observation for observation in post_reference if observation.get("target_match_status") == TARGET_FOUND
    ]
    if post_reference_found:
        last_found_index = max(item["observation_index"] for item in post_reference_found)
        later_not_found = [
            item
            for item in post_reference
            if item["observation_index"] > last_found_index
            and item.get("target_match_status") == TARGET_NOT_FOUND
        ]
        if later_not_found:
            return "TARGET_DISAPPEARED_POST_EVENT"

    successful_200 = [item for item in observations if item.get("http_status") == 200]
    all_successful_observations_found = bool(successful_200) and all(
        item.get("target_match_status") == TARGET_FOUND for item in successful_200
    )
    if all_successful_observations_found and not stopped_early:
        return "NO_RECORDED_AT_TIME_TARGET_REMAINED_OBSERVABLE"
    return "INCONCLUSIVE"


def collector_implication(classification: str) -> str:
    implications = {
        "THIS_CALL_RECORDED_AT_TIME_OBSERVED": (
            "Collector should preserve RecordedAtTime when present and Stage 7 "
            "must validate coverage before enabling Actual Delay."
        ),
        "RECORDED_AT_TIME_OBSERVED_IN_PREVIOUS_CALLS_ONLY": (
            "Collector should preserve lifecycle call structures because actual "
            "timing may be exposed for already-served calls even when absent from ThisCall."
        ),
        "RECORDED_AT_TIME_OBSERVED_OTHER_CONTEXT": (
            "RecordedAtTime was observed outside the ThisCall arrival context; "
            "preserve the field and validate semantics before any Actual Delay use."
        ),
        "NO_RECORDED_AT_TIME_TARGET_REMAINED_OBSERVABLE": (
            "Observed StopEvent lifecycle remained prediction/timetable based for "
            "this event; prospective estimate collection remains necessary and an "
            "actual-time source is still unvalidated."
        ),
        "TARGET_DISAPPEARED_POST_EVENT": (
            "StopEvent target visibility may end before a recorded actual timestamp "
            "becomes observable; preserve pre-event realtime observations and keep "
            "actual-time availability unresolved."
        ),
        "TARGET_MATCH_BECAME_AMBIGUOUS": (
            "Target correlation became ambiguous; do not infer lifecycle semantics. "
            "Strengthen identity validation in Stage 7."
        ),
        "TARGET_DELAYED_BEYOND_OBSERVATION_WINDOW": (
            "The target did not remain observable through the planned window; "
            "actual-time availability remains unresolved."
        ),
        "NO_SUITABLE_TARGET": (
            "No suitable event was available in the single discovery request; no "
            "lifecycle conclusion was reached."
        ),
        "INCONCLUSIVE": (
            "Lifecycle evidence was incomplete; prospective collection remains "
            "necessary and actual-time semantics remain unvalidated."
        ),
    }
    return implications[classification]


def final_summary(
    *,
    selected_target: dict[str, Any] | None,
    initial_reference_time: datetime | None,
    initial_reference_source: str | None,
    planned_observations: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    retries: int,
    lifecycle_classification: str,
    raw_temp_directory: str | None,
    stopped_early: bool,
    stop_reason: str | None,
    api_key: str,
) -> dict[str, Any]:
    occurrences = recorded_occurrences_from_observations(observations)
    estimated_values = [
        item.get("this_call_arrival", {}).get("estimated_time")
        for item in observations
        if isinstance(item.get("this_call_arrival"), dict)
        and item["this_call_arrival"].get("estimated_time") is not None
    ]
    contexts = {occurrence.get("structural_context") for occurrence in occurrences}
    first_occurrence = occurrences[0] if occurrences else None
    last_occurrence = occurrences[-1] if occurrences else None
    return {
        "probe_name": "mdd_recorded_time_lifecycle_probe",
        "selected_target_identity": event_identity_output(selected_target, api_key),
        "initial_reference_time": (
            iso_utc(initial_reference_time) if initial_reference_time is not None else None
        ),
        "initial_reference_source": initial_reference_source,
        "planned_observations": [
            {
                key: value
                for key, value in item.items()
                if key != "planned_time"
            }
            for item in planned_observations
        ],
        "attempted_observations": len(observations),
        "successful_http_200_count": sum(
            observation.get("http_status") == 200 for observation in observations
        ),
        "total_http_attempts": len(observations),
        "retries": retries,
        "target_found_observation_count": sum(
            observation.get("target_match_status") == TARGET_FOUND
            for observation in observations
        ),
        "target_not_found_observation_count": sum(
            observation.get("target_match_status") == TARGET_NOT_FOUND
            for observation in observations
        ),
        "thiscall_recorded_at_time_observed": any(
            context in {"THIS_CALL_ARRIVAL", "THIS_CALL_DEPARTURE"} for context in contexts
        ),
        "previouscall_recorded_at_time_observed": any(
            context in {"PREVIOUS_CALL_ARRIVAL", "PREVIOUS_CALL_DEPARTURE"}
            for context in contexts
        ),
        "recorded_at_time_first_observation": first_occurrence,
        "recorded_at_time_last_observation": last_occurrence,
        "initial_estimated_time": (
            safe_value(selected_target["this_call_arrival"].get("estimated_time"), api_key)
            if selected_target is not None
            else None
        ),
        "last_observed_estimated_time": estimated_values[-1] if estimated_values else None,
        "lifecycle_classification": lifecycle_classification,
        "collector_implication": collector_implication(lifecycle_classification),
        "raw_temp_directory": raw_temp_directory,
        "stopped_early": stopped_early,
        "stop_reason": stop_reason,
    }


def main() -> int:
    api_key = os.environ.get("MDD_API_KEY")
    if not api_key:
        print_json(
            {
                "probe_name": "mdd_recorded_time_lifecycle_probe",
                "preflight": "MDD_API_KEY PRESENT: NO",
                "total_http_attempts": 0,
                "retries": RETRIES,
                "lifecycle_classification": "INCONCLUSIVE",
            }
        )
        return 2

    raw_temp_directory = create_raw_temp_directory()
    observations: list[dict[str, Any]] = []
    attempts = 0
    stopped_early = False
    stop_reason: str | None = None

    discovery_time = utc_now()
    discovery_response = perform_http_request(
        api_key=api_key,
        dep_arr_time_utc=format_utc(discovery_time),
        number_of_results=DISCOVERY_NUMBER_OF_RESULTS,
    )
    attempts += 1
    discovery_raw_path = None
    if discovery_response["content"]:
        discovery_raw_path = write_raw_artifact(
            raw_temp_directory,
            "00-discovery.raw",
            discovery_response["content"],
            api_key,
        )
    discovery_analysis = analyze_json(
        discovery_response["content"], discovery_response["content_type"]
    )
    discovery_now = utc_now()
    if discovery_response["http_status"] == 200 and discovery_analysis["json_parse_success"]:
        target, initial_reference_time, initial_reference_source = select_target(
            discovery_analysis["results"], discovery_now
        )
    else:
        target, initial_reference_time, initial_reference_source = None, None, None
    discovery_stop_reason = response_stop_reason(discovery_response)

    if target is None:
        no_suitable_target = discovery_stop_reason is None
        stopped_early = True
        stop_reason = discovery_stop_reason or "NO_SUITABLE_TARGET"
        classification = classify_lifecycle(
            observations=[],
            initial_reference_time=None,
            stopped_early=stopped_early,
            no_suitable_target=no_suitable_target,
        )
        print_json(
            {
                "probe_name": "mdd_recorded_time_lifecycle_probe",
                "discovery_state": "NO_SUITABLE_TARGET" if no_suitable_target else "DISCOVERY_STOPPED",
                "http_status": discovery_response["http_status"],
                "elapsed_ms": round(discovery_response["elapsed_ms"], 3),
                "application_errors": [
                    safe_text(item, api_key) for item in discovery_analysis["application_errors"]
                ],
                "raw_artifact_path": discovery_raw_path,
            }
        )
        print_json(
            final_summary(
                selected_target=None,
                initial_reference_time=None,
                initial_reference_source=None,
                planned_observations=[],
                observations=[],
                retries=RETRIES,
                lifecycle_classification=classification,
                raw_temp_directory=raw_temp_directory,
                stopped_early=stopped_early,
                stop_reason=stop_reason,
                api_key=api_key,
            )
        )
        return 0 if no_suitable_target else 1

    selected_identity = event_identity_output(target, api_key)
    print_json(
        {
            "selection": "SELECTED_TARGET",
            "operating_day_ref": selected_identity["operating_day_ref"],
            "journey_ref": selected_identity["journey_ref"],
            "line_ref": selected_identity["line_ref"],
            "direction_ref": selected_identity["direction_ref"],
            "stop_point_ref": selected_identity["stop_point_ref"],
            "timetabled_time": selected_identity["timetabled_time"],
            "initial_estimated_time": safe_value(
                target["this_call_arrival"].get("estimated_time"), api_key
            ),
            "reference_time": iso_utc(initial_reference_time),
            "reference_time_source": initial_reference_source,
        }
    )

    planned_observations = build_planned_observations(initial_reference_time, discovery_now)
    print_json(
        {
            "planned_schedule": [
                {
                    key: value
                    for key, value in item.items()
                    if key != "planned_time"
                }
                for item in planned_observations
            ]
        }
    )

    discovery_summary = matched_observation_summary(
        observation_index=0,
        observation_label="DISCOVERY",
        planned_observation_time=discovery_time,
        request_timestamp=discovery_response["request_timestamp"],
        initial_reference_time=initial_reference_time,
        http_status=discovery_response["http_status"],
        elapsed_ms=discovery_response["elapsed_ms"],
        target_match_status=TARGET_FOUND,
        matched_event=target,
        analysis=discovery_analysis,
        raw_artifact_path=discovery_raw_path,
        failure=discovery_response["failure"],
        api_key=api_key,
    )
    observations.append(discovery_summary)
    print_json(discovery_summary)
    if discovery_stop_reason is not None:
        stopped_early = True
        stop_reason = discovery_stop_reason

    target_timetabled_dt = parse_aware_timestamp(target["identity"]["timetabled_time"])
    if target_timetabled_dt is None:
        stopped_early = True
        stop_reason = "TARGET_TIMETABLED_TIME_NOT_PARSEABLE"
    else:
        fixed_dep_arr_anchor = format_utc(target_timetabled_dt - timedelta(minutes=5))
        if not stopped_early:
            for planned in planned_observations:
                if planned["skipped"]:
                    continue
                wait_until(planned["planned_time"])
                response = perform_http_request(
                    api_key=api_key,
                    dep_arr_time_utc=fixed_dep_arr_anchor,
                    number_of_results=FOLLOW_UP_NUMBER_OF_RESULTS,
                )
                attempts += 1
                artifact_name = f"{planned['observation_index']:02d}-{planned['observation_label'].lower()}.raw"
                raw_path = None
                if response["content"]:
                    raw_path = write_raw_artifact(
                        raw_temp_directory,
                        artifact_name,
                        response["content"],
                        api_key,
                    )
                analysis = analyze_json(response["content"], response["content_type"])
                match_status = TARGET_NOT_FOUND
                matched_event = None
                if response["http_status"] == 200 and analysis["json_parse_success"]:
                    match_status, matched_event = locate_target(
                        analysis["results"], target
                    )
                summary = matched_observation_summary(
                    observation_index=planned["observation_index"],
                    observation_label=planned["observation_label"],
                    planned_observation_time=planned["planned_time"],
                    request_timestamp=response["request_timestamp"],
                    initial_reference_time=initial_reference_time,
                    http_status=response["http_status"],
                    elapsed_ms=response["elapsed_ms"],
                    target_match_status=match_status,
                    matched_event=matched_event,
                    analysis=analysis,
                    raw_artifact_path=raw_path,
                    failure=response["failure"],
                    api_key=api_key,
                )
                observations.append(summary)
                print_json(summary)
                response_reason = response_stop_reason(response)
                if response_reason is not None:
                    stopped_early = True
                    stop_reason = response_reason
                    break
                if attempts >= MAX_HTTP_ATTEMPTS:
                    stopped_early = True
                    stop_reason = "MAX_HTTP_ATTEMPTS_REACHED"
                    break

    classification = classify_lifecycle(
        observations=observations,
        initial_reference_time=initial_reference_time,
        stopped_early=stopped_early,
        no_suitable_target=False,
    )
    print_json(
        final_summary(
            selected_target=target,
            initial_reference_time=initial_reference_time,
            initial_reference_source=initial_reference_source,
            planned_observations=planned_observations,
            observations=observations,
            retries=RETRIES,
            lifecycle_classification=classification,
            raw_temp_directory=raw_temp_directory,
            stopped_early=stopped_early,
            stop_reason=stop_reason,
            api_key=api_key,
        )
    )
    return 1 if stopped_early else 0


if __name__ == "__main__":
    raise SystemExit(main())
