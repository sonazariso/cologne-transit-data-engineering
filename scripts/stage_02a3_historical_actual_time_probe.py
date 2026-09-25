#!/usr/bin/env python3
"""Stage 02A.3 historical Actual-Time availability probe.

This is a standalone, diagnostic-only probe for manual execution from a
trusted host Terminal. It makes at most one POST per configured historical
horizon, never retries, never follows redirects, and writes response bodies
only to a permission-restricted temporary directory outside the repository.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from datetime import datetime, time as datetime_time, timedelta, timezone
from email.message import Message
from http.client import HTTPResponse
from typing import Any, Iterable, Iterator
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ENDPOINT = "https://mdd.gorheinland.com/delfi"
STATION = "Köln Hbf"
STOP_POINT_REF = "de:05315:11201"
NUMBER_OF_RESULTS = 5
STOP_EVENT_TYPE = "arrival"
INCLUDE_PREVIOUS_CALLS = True
INCLUDE_ONWARD_CALLS = True
INCLUDE_REALTIME_DATA = True
REQUEST_TIMEOUT_SECONDS = 30
RETRIES = 0
HISTORICAL_HORIZONS_DAYS = (1, 7, 30, 90, 180)
MAX_HTTP_ATTEMPTS = 5
BERLIN_TIMEZONE_NAME = "Europe/Berlin"
DATE_ALIGNMENT_SEMANTICS = "DATE-ALIGNMENT ONLY"

ERROR_MESSAGE_KEYS = {"ErrorMessage", "errorMessage"}
ERROR_CODE_KEYS = {"Code", "code", "ErrorCode", "errorCode"}
RESULT_KEYS = {"StopEventResult", "stopEventResult"}
RECORDED_AT_TIME_KEYS = {"RecordedAtTime", "recordedAtTime"}
TIMING_FIELD_ALIASES = {
    "timetabled": {"TimetabledTime", "timetabledTime"},
    "estimated": {"EstimatedTime", "estimatedTime"},
    "recorded": RECORDED_AT_TIME_KEYS,
}
SCOPE_KEYS = {
    "thiscall": {"ThisCall", "thisCall"},
    "previouscall": {"PreviousCall", "previousCall"},
    "onwardcall": {"OnwardCall", "onwardCall"},
}
KNOWN_ERROR_CODES = {
    "STOPEVENT_DATEOUTOFRANGE",
    "STOPEVENT_NOEVENTFOUND",
    "STOPEVENT_NOREALTIME",
}
STOPEVENT_CODE_PATTERN = re.compile(r"\bSTOPEVENT_[A-Z0-9_]+\b")
PROVIDER_GMT_SUFFIX_PATTERN = re.compile(r"^(?P<base>.+Z)\[GMT\]$")


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


def format_utc_xs_datetime(value: datetime) -> str:
    """Serialize an aware datetime as a valid UTC xs:dateTime."""

    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_local_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.isoformat(timespec="seconds")


def build_request_body(request_timestamp_utc: str, dep_arr_time_utc: str) -> bytes:
    """Build the fixed S15 TRIAS 1.2 StopEvent request contract."""

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
            <StopPointRef>{STOP_POINT_REF}</StopPointRef>
          </LocationRef>
          <DepArrTime>{dep_arr_time_utc}</DepArrTime>
        </Location>
        <Params>
          <NumberOfResults>{NUMBER_OF_RESULTS}</NumberOfResults>
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
        parts = [part.strip() for part in content_type.split(";")]
        for part in parts[1:]:
            if "=" in part:
                name, value = part.split("=", 1)
                if name.strip().lower() == "charset":
                    return value.strip().strip('"') or "utf-8"
    return "utf-8"


def decode_response(content: bytes, content_type: str | None) -> str:
    charset = response_charset(content_type)
    try:
        return content.decode(charset, errors="replace")
    except LookupError:
        return content.decode("utf-8", errors="replace")


def read_response(response: Any) -> tuple[int, Message, bytes]:
    return int(response.status), response.headers, response.read()


def create_raw_temp_directory() -> str:
    preferred_directory = "/private/tmp"
    temp_directory = (
        preferred_directory if os.path.isdir(preferred_directory) else tempfile.gettempdir()
    )
    raw_directory = tempfile.mkdtemp(
        prefix="cologne-transit-stage-02a3-",
        dir=temp_directory,
    )
    try:
        os.chmod(raw_directory, 0o700)
    except OSError:
        pass
    return raw_directory


def write_raw_artifact(
    raw_directory: str,
    artifact_name: str,
    content: bytes,
    api_key_bytes: bytes,
) -> str | None:
    """Write a body only after checking it for the authentication secret."""

    if api_key_bytes in content:
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


def json_value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return type(value).__name__


def path_for_key(parent_path: str, key: Any) -> str:
    if isinstance(key, str) and key and all(
        character.isalnum() or character == "_" for character in key
    ):
        return f"{parent_path}.{key}"
    return f"{parent_path}[{json.dumps(str(key), ensure_ascii=False)}]"


def iter_dict_entries(
    value: Any,
    path: str = "$",
) -> Iterator[tuple[str, str, Any]]:
    """Yield dictionary entries with structural JSON paths."""

    if isinstance(value, dict):
        for key, child in value.items():
            child_path = path_for_key(path, key)
            yield child_path, str(key), child
            yield from iter_dict_entries(child, child_path)
    elif isinstance(value, list):
        list_path = f"{path}[*]"
        for child in value:
            yield from iter_dict_entries(child, list_path)


def first_named_value(value: Any, names: set[str]) -> Any:
    for _path, key, child in iter_dict_entries(value):
        if key in names:
            return child
    return None


def named_result_values(parsed: Any) -> list[Any]:
    result_value = first_named_value(parsed, RESULT_KEYS)
    if isinstance(result_value, list):
        return result_value
    if isinstance(result_value, dict):
        return [result_value]
    return []


def as_object_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def scope_calls(result: Any, scope_name: str) -> list[dict[str, Any]]:
    if not isinstance(result, dict):
        return []
    return as_object_list(first_named_value(result, SCOPE_KEYS[scope_name]))


def has_nonempty_scalar(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def timing_values(call_objects: Iterable[dict[str, Any]]) -> dict[str, list[str]]:
    values = {"timetabled": [], "estimated": [], "recorded": []}
    for call_object in call_objects:
        for _path, key, value in iter_dict_entries(call_object):
            if not has_nonempty_scalar(value):
                continue
            if key in TIMING_FIELD_ALIASES["timetabled"]:
                values["timetabled"].append(str(value))
            elif key in TIMING_FIELD_ALIASES["estimated"]:
                values["estimated"].append(str(value))
            elif key in TIMING_FIELD_ALIASES["recorded"]:
                values["recorded"].append(str(value))
    return values


def merge_timing_values(*scopes: dict[str, list[str]]) -> dict[str, list[str]]:
    merged = {"timetabled": [], "estimated": [], "recorded": []}
    for scope in scopes:
        for field_name in merged:
            merged[field_name].extend(scope[field_name])
    return merged


def normalize_timestamp_text(value: str) -> str | None:
    """Normalize the observed provider suffix without inventing a timezone."""

    if not isinstance(value, str):
        return None
    text = value.strip()
    provider_match = PROVIDER_GMT_SUFFIX_PATTERN.fullmatch(text)
    if provider_match:
        text = provider_match.group("base")
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    return text


def parse_timestamp(value: str) -> datetime | None:
    """Parse an ISO-like value, preserving naive values as timezone-unresolved."""

    text = normalize_timestamp_text(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed


def timestamp_semantics(value: str) -> str:
    """Describe the timezone evidence carried by one provider timestamp."""

    if not isinstance(value, str) or not value.strip():
        return "INVALID_OR_EMPTY"
    original_text = value.strip()
    provider_match = PROVIDER_GMT_SUFFIX_PATTERN.fullmatch(original_text)
    normalized_text = normalize_timestamp_text(original_text)
    if normalized_text is None:
        return "UNPARSEABLE"
    try:
        parsed = datetime.fromisoformat(normalized_text)
    except ValueError:
        return "UNPARSEABLE"
    if parsed.tzinfo is None:
        return "TIMEZONE_UNRESOLVED"
    if provider_match:
        return "EXPLICIT_UTC_Z_WITH_PROVIDER_GMT_SUFFIX"
    if original_text.endswith("Z"):
        return "EXPLICIT_UTC_Z"
    return "EXPLICIT_NUMERIC_OFFSET"


def min_max_timestamps(values: list[str]) -> tuple[str | None, str | None, list[datetime]]:
    nonempty_values = [value for value in values if isinstance(value, str) and value.strip()]
    parsed_values = [parsed for value in nonempty_values if (parsed := parse_timestamp(value))]
    aware_values = [parsed for parsed in parsed_values if parsed.tzinfo is not None]
    if not parsed_values or len(parsed_values) != len(nonempty_values):
        return None, None, parsed_values
    if len(aware_values) != len(parsed_values):
        # A timezone-less provider value cannot honestly be serialized as UTC.
        return None, None, parsed_values
    if not aware_values:
        return None, None, []
    minimum = min(aware_values)
    maximum = max(aware_values)
    return (
        format_utc_xs_datetime(minimum),
        format_utc_xs_datetime(maximum),
        parsed_values,
    )


def min_max_lexical_timestamps(values: list[str]) -> tuple[str | None, str | None]:
    nonempty_values = sorted(
        {value.strip() for value in values if isinstance(value, str) and value.strip()}
    )
    if not nonempty_values:
        return None, None
    return nonempty_values[0], nonempty_values[-1]


def contains_result_scope(path: str) -> bool:
    return any(f".{result_key}" in path for result_key in RESULT_KEYS)


def error_codes_from_value(value: Any) -> set[str]:
    codes: set[str] = set()

    def add_string(candidate: str, key: str | None = None) -> None:
        if candidate in KNOWN_ERROR_CODES:
            codes.add(candidate)
        for matched_code in STOPEVENT_CODE_PATTERN.findall(candidate):
            codes.add(matched_code)
        if key in ERROR_CODE_KEYS and candidate.strip():
            codes.add(candidate.strip())

    if isinstance(value, str):
        add_string(value)
    for _path, key, child in iter_dict_entries(value):
        if isinstance(child, str):
            add_string(child, key)
    if isinstance(value, list):
        for child in value:
            if isinstance(child, str):
                add_string(child)
    return codes


def has_populated_error_message(value: Any) -> bool:
    """Return true only when an ErrorMessage value contains error evidence."""

    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(has_populated_error_message(child) for child in value)
    if isinstance(value, dict):
        return any(has_populated_error_message(child) for child in value.values())
    return has_nonempty_scalar(value)


def response_and_result_error_codes(
    parsed: Any,
    results: list[Any],
) -> tuple[list[str], list[str], int, int]:
    response_codes: set[str] = set()
    response_error_message_count = 0
    for path, key, value in iter_dict_entries(parsed):
        if key in ERROR_MESSAGE_KEYS and not contains_result_scope(path):
            if has_populated_error_message(value):
                response_error_message_count += 1
                response_codes.update(error_codes_from_value(value))

    result_codes: set[str] = set()
    result_error_message_count = 0
    for result in results:
        for _path, key, value in iter_dict_entries(result):
            if key in ERROR_MESSAGE_KEYS:
                if has_populated_error_message(value):
                    result_error_message_count += 1
                    result_codes.update(error_codes_from_value(value))
    return (
        sorted(response_codes),
        sorted(result_codes),
        response_error_message_count,
        result_error_message_count,
    )


def recorded_field_inventory(parsed: Any, api_key: str) -> tuple[bool, list[str], list[dict[str, Any]]]:
    """Find actual-looking field names without assigning them semantics."""

    recorded_observed = False
    recorded_names: set[str] = set()
    candidate_counts: dict[tuple[str, str], int] = {}
    candidate_types: dict[tuple[str, str], set[str]] = {}

    for path, key, value in iter_dict_entries(parsed):
        if key in RECORDED_AT_TIME_KEYS and has_nonempty_scalar(value):
            recorded_observed = True
            recorded_names.add(key)

        if key in RECORDED_AT_TIME_KEYS:
            continue
        if not any(marker in key for marker in ("Actual", "Recorded", "actual", "recorded")):
            continue

        inventory_key = (key, path)
        candidate_counts[inventory_key] = candidate_counts.get(inventory_key, 0) + 1
        candidate_types.setdefault(inventory_key, set()).add(json_value_type(value))

    candidates = []
    for (field_name, path), occurrence_count in sorted(candidate_counts.items()):
        types = candidate_types[(field_name, path)]
        value_type = next(iter(types)) if len(types) == 1 else "mixed"
        candidates.append(
            {
                "field_name": safe_text(field_name, api_key),
                "structural_json_path": safe_text(path, api_key),
                "occurrence_count": occurrence_count,
                "value_type": value_type,
            }
        )
    return recorded_observed, sorted(safe_text(name, api_key) for name in recorded_names), candidates


def timestamp_calendar_date(value: str, berlin_timezone: ZoneInfo) -> Any:
    parsed = parse_timestamp(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        # DATE-ALIGNMENT ONLY. No absolute instant is assigned.
        return parsed.date()
    return parsed.astimezone(berlin_timezone).date()


def date_alignment_counts(
    values: list[str],
    target_date: Any,
    execution_local_date: Any,
    berlin_timezone: ZoneInfo,
) -> dict[str, Any]:
    dates: list[Any] = []
    target_count = 0
    execution_count = 0
    for value in values:
        calendar_date = timestamp_calendar_date(value, berlin_timezone)
        if calendar_date is None:
            continue
        dates.append(calendar_date)
        target_count += calendar_date == target_date
        execution_count += calendar_date == execution_local_date
    return {
        "observed_count": len(values),
        "parseable_count": len(dates),
        "unparseable_count": len(values) - len(dates),
        "target_date_match_count": target_count,
        "execution_date_match_count": execution_count,
        "distinct_calendar_dates": sorted({date.isoformat() for date in dates}),
    }


def target_date_match(values: list[str], target_date: Any, berlin_timezone: ZoneInfo) -> bool:
    return any(
        timestamp_calendar_date(value, berlin_timezone) == target_date for value in values
    )


def temporal_alignment(
    result_count: int,
    response_error_codes: list[str],
    result_error_codes: list[str],
    thiscall_timetabled_values: list[str],
    requested_local_date: Any,
    execution_local_date: Any,
    berlin_timezone: ZoneInfo,
    http_status: int | None,
    json_parse_success: bool,
) -> str:
    all_error_codes = set(response_error_codes) | set(result_error_codes)
    if "STOPEVENT_DATEOUTOFRANGE" in all_error_codes:
        return "DATE_OUT_OF_RANGE"
    if "STOPEVENT_NOEVENTFOUND" in all_error_codes:
        return "NO_RESULTS"
    if http_status == 200 and json_parse_success and result_count == 0:
        return "NO_RESULTS"
    date_counts = date_alignment_counts(
        thiscall_timetabled_values,
        requested_local_date,
        execution_local_date,
        berlin_timezone,
    )
    parseable_count = date_counts["parseable_count"]
    observed_count = date_counts["observed_count"]
    if parseable_count and parseable_count == observed_count and date_counts[
        "target_date_match_count"
    ] == parseable_count:
        return "HISTORICAL_TARGET_MATCH"
    if parseable_count and parseable_count == observed_count and date_counts[
        "execution_date_match_count"
    ] == parseable_count:
        return "CURRENT_TIME_FALLBACK_SUSPECTED"
    majority = max(1, (observed_count + 1) // 2)
    if parseable_count >= majority and date_counts["target_date_match_count"] >= majority:
        return "HISTORICAL_TARGET_MATCH"
    if parseable_count >= majority and date_counts["execution_date_match_count"] >= majority:
        return "CURRENT_TIME_FALLBACK_SUSPECTED"
    return "INCONCLUSIVE"


def historical_classification(
    alignment: str,
    result_count: int,
    response_error_codes: list[str],
    result_error_codes: list[str],
    thiscall_timing: dict[str, list[str]],
    all_timing: dict[str, list[str]],
    requested_local_date: Any,
    berlin_timezone: ZoneInfo,
    http_status: int | None,
    json_parse_success: bool,
) -> tuple[str, bool]:
    all_error_codes = set(response_error_codes) | set(result_error_codes)
    if "STOPEVENT_DATEOUTOFRANGE" in all_error_codes:
        return "HISTORY_DATE_OUT_OF_RANGE", False
    if "STOPEVENT_NOEVENTFOUND" in all_error_codes:
        return "HISTORY_NO_EVENT", False
    if http_status == 200 and json_parse_success and result_count == 0:
        return "HISTORY_NO_EVENT", False
    if alignment == "CURRENT_TIME_FALLBACK_SUSPECTED":
        return "REQUEST_NOT_HONORED_AS_HISTORICAL", False
    if alignment != "HISTORICAL_TARGET_MATCH":
        return "INCONCLUSIVE", False

    recorded_aligned = target_date_match(
        all_timing["recorded"], requested_local_date, berlin_timezone
    )
    if recorded_aligned:
        return "HISTORICAL_ACTUAL_OBSERVED", True
    if all_timing["estimated"]:
        return "HISTORICAL_REALTIME_WITHOUT_ACTUAL", False
    return "HISTORICAL_TIMETABLE_ONLY", False


def safe_text(value: str, api_key: str) -> str:
    return value.replace(api_key, "[REDACTED]") if api_key else value


def empty_response_analysis() -> dict[str, Any]:
    return {
        "json_parse_success": False,
        "top_level_type": None,
        "result_count": 0,
        "response_error_codes": [],
        "result_error_codes": [],
        "response_error_message_count": 0,
        "result_error_message_count": 0,
        "thiscall_timing": {"timetabled": [], "estimated": [], "recorded": []},
        "previouscall_timing": {"timetabled": [], "estimated": [], "recorded": []},
        "onwardcall_timing": {"timetabled": [], "estimated": [], "recorded": []},
        "recorded_at_time_observed": False,
        "recorded_at_time_field_names": [],
        "actual_time_candidate_fields": [],
    }


def analyze_response(content: bytes, content_type: str | None, api_key: str) -> dict[str, Any]:
    analysis = empty_response_analysis()
    decoded = decode_response(content, content_type)
    try:
        parsed = json.loads(decoded)
    except (TypeError, ValueError):
        return analysis

    analysis["json_parse_success"] = True
    analysis["top_level_type"] = json_value_type(parsed)
    results = named_result_values(parsed)
    analysis["result_count"] = len(results)
    (
        response_codes,
        result_codes,
        response_error_message_count,
        result_error_message_count,
    ) = response_and_result_error_codes(parsed, results)
    analysis["response_error_codes"] = [safe_text(code, api_key) for code in response_codes]
    analysis["result_error_codes"] = [safe_text(code, api_key) for code in result_codes]
    analysis["response_error_message_count"] = response_error_message_count
    analysis["result_error_message_count"] = result_error_message_count

    scope_timing = {
        "thiscall": {"timetabled": [], "estimated": [], "recorded": []},
        "previouscall": {"timetabled": [], "estimated": [], "recorded": []},
        "onwardcall": {"timetabled": [], "estimated": [], "recorded": []},
    }
    for result in results:
        for scope_name in scope_timing:
            calls = scope_calls(result, scope_name)
            values = timing_values(calls)
            for field_name in scope_timing[scope_name]:
                scope_timing[scope_name][field_name].extend(values[field_name])

    analysis["thiscall_timing"] = scope_timing["thiscall"]
    analysis["previouscall_timing"] = scope_timing["previouscall"]
    analysis["onwardcall_timing"] = scope_timing["onwardcall"]
    (
        analysis["recorded_at_time_observed"],
        analysis["recorded_at_time_field_names"],
        analysis["actual_time_candidate_fields"],
    ) = recorded_field_inventory(parsed, api_key)
    return analysis


def response_summary(
    *,
    horizon_days: int,
    requested_local_datetime: str,
    requested_utc_datetime: str,
    request_timestamp_utc: str | None,
    http_status: int | None,
    elapsed_ms: float,
    response_content_type: str | None,
    response_bytes: int,
    raw_artifact_path: str | None,
    analysis: dict[str, Any],
    requested_local_date: Any,
    execution_local_date: Any,
    berlin_timezone: ZoneInfo,
    failure: str | None,
    api_key: str,
) -> dict[str, Any]:
    thiscall_timing = analysis["thiscall_timing"]
    previouscall_timing = analysis["previouscall_timing"]
    onwardcall_timing = analysis["onwardcall_timing"]
    all_timing = merge_timing_values(
        thiscall_timing,
        previouscall_timing,
        onwardcall_timing,
    )
    min_timetabled_utc, max_timetabled_utc, _parsed_timetabled = min_max_timestamps(
        thiscall_timing["timetabled"]
    )
    min_timetabled_lexical, max_timetabled_lexical = min_max_lexical_timestamps(
        thiscall_timing["timetabled"]
    )
    date_counts = date_alignment_counts(
        thiscall_timing["timetabled"],
        requested_local_date,
        execution_local_date,
        berlin_timezone,
    )
    alignment = temporal_alignment(
        analysis["result_count"],
        analysis["response_error_codes"],
        analysis["result_error_codes"],
        thiscall_timing["timetabled"],
        requested_local_date,
        execution_local_date,
        berlin_timezone,
        http_status,
        analysis["json_parse_success"],
    )
    classification, _actual_aligned = historical_classification(
        alignment,
        analysis["result_count"],
        analysis["response_error_codes"],
        analysis["result_error_codes"],
        thiscall_timing,
        all_timing,
        requested_local_date,
        berlin_timezone,
        http_status,
        analysis["json_parse_success"],
    )
    recorded_target_match = target_date_match(
        all_timing["recorded"], requested_local_date, berlin_timezone
    )

    summary: dict[str, Any] = {
        "horizon_days": horizon_days,
        "station": STATION,
        "stop_point_ref": STOP_POINT_REF,
        "requested_local_datetime": requested_local_datetime,
        "requested_utc_datetime": requested_utc_datetime,
        "request_timestamp_utc": request_timestamp_utc,
        "http_status": http_status,
        "elapsed_ms": elapsed_ms,
        "response_content_type": safe_text(response_content_type, api_key)
        if response_content_type
        else None,
        "response_bytes": response_bytes,
        "raw_artifact_path": raw_artifact_path,
        "json_parse_success": analysis["json_parse_success"],
        "top_level_type": analysis["top_level_type"],
        "result_count": analysis["result_count"],
        "response_error_codes": analysis["response_error_codes"],
        "result_error_codes": analysis["result_error_codes"],
        "response_error_message_count": analysis["response_error_message_count"],
        "result_error_message_count": analysis["result_error_message_count"],
        "thiscall_timetabled_count": len(thiscall_timing["timetabled"]),
        "thiscall_estimated_count": len(thiscall_timing["estimated"]),
        "thiscall_recorded_count": len(thiscall_timing["recorded"]),
        "previouscall_timetabled_count": len(previouscall_timing["timetabled"]),
        "previouscall_estimated_count": len(previouscall_timing["estimated"]),
        "previouscall_recorded_count": len(previouscall_timing["recorded"]),
        "onwardcall_timetabled_count": len(onwardcall_timing["timetabled"]),
        "onwardcall_estimated_count": len(onwardcall_timing["estimated"]),
        "onwardcall_recorded_count": len(onwardcall_timing["recorded"]),
        "min_thiscall_timetabled": min_timetabled_utc,
        "max_thiscall_timetabled": max_timetabled_utc,
        "min_thiscall_timetabled_utc": min_timetabled_utc,
        "max_thiscall_timetabled_utc": max_timetabled_utc,
        "min_thiscall_timetabled_lexical": min_timetabled_lexical,
        "max_thiscall_timetabled_lexical": max_timetabled_lexical,
        "thiscall_timetabled_timestamp_semantics": sorted(
            {timestamp_semantics(value) for value in thiscall_timing["timetabled"]}
        ),
        "date_alignment_semantics": DATE_ALIGNMENT_SEMANTICS,
        "requested_local_date": requested_local_date.isoformat(),
        "execution_local_date": execution_local_date.isoformat(),
        "thiscall_timetabled_parseable_count": date_counts["parseable_count"],
        "thiscall_timetabled_unparseable_count": date_counts["unparseable_count"],
        "thiscall_timetabled_target_date_match_count": date_counts[
            "target_date_match_count"
        ],
        "thiscall_timetabled_execution_date_match_count": date_counts[
            "execution_date_match_count"
        ],
        "thiscall_timetabled_calendar_dates": date_counts["distinct_calendar_dates"],
        "temporal_alignment": alignment,
        "recorded_at_time_observed": analysis["recorded_at_time_observed"],
        "recorded_at_time_field_names": analysis["recorded_at_time_field_names"],
        "recorded_at_time_target_date_match": recorded_target_match,
        "actual_time_candidate_fields": analysis["actual_time_candidate_fields"],
        "historical_classification": classification,
    }
    if failure:
        summary["failure"] = safe_text(failure, api_key)
    return summary


def stop_reason_for_status(status: int | None) -> str | None:
    if status in {401, 403}:
        return f"HTTP_{status}"
    if status == 429:
        return "HTTP_429"
    if status is not None and 500 <= status <= 599:
        return f"HTTP_{status}"
    return None


def main() -> int:
    api_key = os.environ.get("MDD_API_KEY")
    if not api_key:
        print("MDD_API_KEY PRESENT: NO")
        return 2

    try:
        berlin_timezone = ZoneInfo(BERLIN_TIMEZONE_NAME)
    except ZoneInfoNotFoundError:
        print(json.dumps({"preflight_error": "Europe/Berlin timezone unavailable"}))
        return 2

    api_key_bytes = api_key.encode("utf-8")
    run_started = datetime.now(timezone.utc)
    execution_local = run_started.astimezone(berlin_timezone)
    raw_directory = create_raw_temp_directory()
    opener = build_opener(NoRedirectHandler())

    total_http_attempts = 0
    successful_http_200_count = 0
    horizons_attempted: list[int] = []
    horizons_completed: list[int] = []
    recorded_at_time_horizons: list[int] = []
    historical_target_match_horizons: list[int] = []
    date_out_of_range_horizons: list[int] = []
    historical_realtime_without_actual_horizons: list[int] = []
    historical_timetable_only_horizons: list[int] = []
    request_not_honored_horizons: list[int] = []
    recorded_at_time_observed_anywhere = False
    stopped_early = False
    stop_reason: str | None = None

    for index, horizon_days in enumerate(
        HISTORICAL_HORIZONS_DAYS[:MAX_HTTP_ATTEMPTS],
        start=1,
    ):
        horizons_attempted.append(horizon_days)
        target_date = execution_local.date() - timedelta(days=horizon_days)
        target_local = datetime.combine(
            target_date,
            datetime_time(hour=12, minute=0),
            tzinfo=berlin_timezone,
        )
        target_utc = target_local.astimezone(timezone.utc)
        requested_local_datetime = format_local_datetime(target_local)
        requested_utc_datetime = format_utc_xs_datetime(target_utc)
        request_timestamp_utc = format_utc_xs_datetime(datetime.now(timezone.utc))
        body = build_request_body(request_timestamp_utc, requested_utc_datetime)
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
        total_http_attempts += 1
        status_code: int | None = None
        response_content_type: str | None = None
        response_content = b""
        failure: str | None = None

        # This is the sole MDD HTTP operation for this horizon. The loop is
        # intentionally sequential and contains no retry or fallback path.
        try:
            with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                status_code, headers, response_content = read_response(response)
                response_content_type = headers.get("Content-Type")
        except HTTPError as error:
            status_code = int(error.code)
            response_content_type = error.headers.get("Content-Type") if error.headers else None
            try:
                response_content = error.read()
            except (OSError, TimeoutError) as read_error:
                response_content = b""
                failure = type(read_error).__name__
        except (URLError, TimeoutError, OSError) as error:
            failure = type(error).__name__

        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        raw_artifact_path: str | None = None
        secret_found = bool(response_content and api_key_bytes in response_content)
        if status_code is not None:
            horizons_completed.append(horizon_days)
            if status_code == 200:
                successful_http_200_count += 1

        if secret_found:
            failure = "response_body_contained_api_key; raw_artifact_not_written"
            stop_reason = "response_body_contained_api_key"
            stopped_early = True
            analysis = empty_response_analysis()
        elif status_code is not None:
            try:
                raw_artifact_path = write_raw_artifact(
                    raw_directory,
                    f"{index:02d}-{horizon_days}d.raw",
                    response_content,
                    api_key_bytes,
                )
            except OSError:
                failure = "raw_artifact_write_failed"
                stop_reason = "raw_artifact_write_failed"
                stopped_early = True
                analysis = empty_response_analysis()
            else:
                analysis = analyze_response(response_content, response_content_type, api_key)
        else:
            analysis = empty_response_analysis()

        summary = response_summary(
            horizon_days=horizon_days,
            requested_local_datetime=requested_local_datetime,
            requested_utc_datetime=requested_utc_datetime,
            request_timestamp_utc=request_timestamp_utc,
            http_status=status_code,
            elapsed_ms=elapsed_ms,
            response_content_type=response_content_type,
            response_bytes=len(response_content),
            raw_artifact_path=raw_artifact_path,
            analysis=analysis,
            requested_local_date=target_date,
            execution_local_date=execution_local.date(),
            berlin_timezone=berlin_timezone,
            failure=failure,
            api_key=api_key,
        )
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))

        if analysis["recorded_at_time_observed"]:
            recorded_at_time_observed_anywhere = True
            recorded_at_time_horizons.append(horizon_days)
        if summary["temporal_alignment"] == "HISTORICAL_TARGET_MATCH":
            historical_target_match_horizons.append(horizon_days)
        if summary["temporal_alignment"] == "DATE_OUT_OF_RANGE":
            date_out_of_range_horizons.append(horizon_days)
        if summary["historical_classification"] == "HISTORICAL_REALTIME_WITHOUT_ACTUAL":
            historical_realtime_without_actual_horizons.append(horizon_days)
        if summary["historical_classification"] == "HISTORICAL_TIMETABLE_ONLY":
            historical_timetable_only_horizons.append(horizon_days)
        if summary["historical_classification"] == "REQUEST_NOT_HONORED_AS_HISTORICAL":
            request_not_honored_horizons.append(horizon_days)

        if failure and status_code is None and stop_reason is None:
            stopped_early = True
            stop_reason = f"transport_failure_{failure}"
        if stop_reason is None:
            stop_reason = stop_reason_for_status(status_code)
            if stop_reason is not None:
                stopped_early = True
        if stopped_early:
            break

    aggregate = {
        "probe_name": "stage_02a3_historical_actual_time_probe",
        "run_timestamp_utc": format_utc_xs_datetime(run_started),
        "total_http_attempts": total_http_attempts,
        "retries": RETRIES,
        "successful_http_200_count": successful_http_200_count,
        "horizons_attempted": horizons_attempted,
        "horizons_completed": horizons_completed,
        "raw_temp_directory": raw_directory,
        "recorded_at_time_observed_anywhere": recorded_at_time_observed_anywhere,
        "recorded_at_time_horizons": recorded_at_time_horizons,
        "historical_target_match_horizons": historical_target_match_horizons,
        "date_out_of_range_horizons": date_out_of_range_horizons,
        "historical_realtime_without_actual_horizons": historical_realtime_without_actual_horizons,
        "historical_timetable_only_horizons": historical_timetable_only_horizons,
        "request_not_honored_horizons": request_not_honored_horizons,
        "stopped_early": stopped_early,
        "stop_reason": stop_reason,
    }
    print(json.dumps(aggregate, ensure_ascii=False, sort_keys=True))
    return 1 if stopped_early else 0


if __name__ == "__main__":
    raise SystemExit(main())
