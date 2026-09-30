"""Source-faithful, transport-independent TRIAS response parsing.

The parser intentionally retains nullable source timing fields and call-scope
context. It does not derive ActualTime, physical delay, cancellation, or
platform state from missing/estimated values.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


PARSER_VERSION = "stage14-trias-parser-1.0"
SUCCESS_STATUSES = {"ok", "success", "successful", "completed", "true", ""}


class TriasParserError(ValueError):
    """Deterministic response decoding/schema failure; never retry automatically."""


class TriasApplicationError(ValueError):
    """A syntactically valid response reports a provider/application error."""


@dataclass(frozen=True)
class ParsedSnapshot:
    parser_version: str
    response_timestamp: Any
    events: tuple[Mapping[str, Any], ...]
    parser_gaps: tuple[Mapping[str, Any], ...] = ()
    response_errors: tuple[str, ...] = ()
    application_status: str | None = None

    @property
    def result_count(self) -> int:
        return len(self.events)

    def to_dict(self) -> dict[str, Any]:
        return {
            "parser_version": self.parser_version,
            "response_timestamp": self.response_timestamp,
            "events": [dict(event) for event in self.events],
            "parser_gaps": [dict(gap) for gap in self.parser_gaps],
            "response_errors": list(self.response_errors),
            "application_status": self.application_status,
            "raw_payload_persisted": False,
        }


def _normalise_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower().split("}")[-1])


def _children(obj: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(obj, Mapping):
        yield from ((str(key), value) for key, value in obj.items())


def _scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _normalise_name(key) in {"value", "text", "content"}:
                return _scalar(child)
        return None
    if isinstance(value, list):
        if not value:
            return None
        return _scalar(value[0])
    return str(value)


def _direct(obj: Any, names: set[str]) -> tuple[bool, Any]:
    if not isinstance(obj, Mapping):
        return False, None
    for key, value in obj.items():
        if _normalise_name(key) in names:
            return True, value
    return False, None


def _first_recursive(obj: Any, names: set[str]) -> tuple[bool, Any]:
    direct = _direct(obj, names)
    if direct[0]:
        return direct
    if isinstance(obj, Mapping):
        for value in obj.values():
            found = _first_recursive(value, names)
            if found[0]:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _first_recursive(value, names)
            if found[0]:
                return found
    return False, None


def _all_recursive(obj: Any, names: set[str]) -> list[Any]:
    found: list[Any] = []
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            if _normalise_name(key) in names:
                found.extend(value if isinstance(value, list) else [value])
            found.extend(_all_recursive(value, names))
    elif isinstance(obj, list):
        for value in obj:
            found.extend(_all_recursive(value, names))
    return found


def _as_object(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _xml_to_object(element: ET.Element) -> Any:
    children = list(element)
    if not children:
        return element.text
    result: dict[str, Any] = {}
    for child in children:
        name = child.tag.rsplit("}", 1)[-1]
        value = _xml_to_object(child)
        if name in result:
            previous = result[name]
            result[name] = previous + [value] if isinstance(previous, list) else [previous, value]
        else:
            result[name] = value
    if element.attrib:
        result.update({f"@{key}": value for key, value in element.attrib.items()})
    return result


def _decode_body(body: bytes, content_type: str | None) -> Mapping[str, Any]:
    stripped = body.lstrip()
    looks_json = "json" in (content_type or "").lower() or stripped.startswith((b"{", b"["))
    if looks_json:
        try:
            parsed = json.loads(body.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TriasParserError("response is not valid JSON") from exc
    else:
        try:
            root = ET.fromstring(body)
            parsed = {root.tag.rsplit("}", 1)[-1]: _xml_to_object(root)}
        except (ET.ParseError, UnicodeDecodeError) as exc:
            raise TriasParserError("response is not valid XML") from exc
    if not isinstance(parsed, Mapping):
        raise TriasParserError("TRIAS response root must be an object")
    return parsed


def _timing_context(call: Mapping[str, Any], names: set[str]) -> dict[str, Any]:
    found, value = _direct(call, names)
    if not found:
        found, value = _direct(_as_object(call.get("callAtStop")), names)
    context = _as_object(value)

    def field(name: str) -> Any:
        found_field, field_value = _direct(context, {name})
        return _scalar(field_value) if found_field else None

    return {
        "timetabled_time": field("timetabledtime"),
        "estimated_time": field("estimatedtime"),
        "recorded_at_time": field("recordedattime"),
        "estimated_time_low": field("estimatedtimelow"),
        "estimated_time_high": field("estimatedtimehigh"),
    }


def _call_context(call: Mapping[str, Any]) -> Mapping[str, Any]:
    found, value = _direct(call, {"callatstop"})
    return _as_object(value) if found else call


def _parse_event(result: Any, ordinal: int) -> Mapping[str, Any]:
    result_obj = _as_object(result)
    found, stop_event = _direct(result_obj, {"stopevent"})
    event = _as_object(stop_event if found else result_obj)
    found, service = _direct(event, {"service"})
    service_obj = _as_object(service if found else event)
    found, this_call = _direct(event, {"thiscall"})
    if not found:
        found, this_call = _direct(event, {"callatstop"})
    this_call_obj = _as_object(this_call if found else {})
    this_context = _call_context(this_call_obj)
    previous = _direct(event, {"previouscall"})[1]
    onward = _direct(event, {"onwardcall"})[1]
    if not isinstance(previous, list):
        previous = [] if previous is None else [previous]
    if not isinstance(onward, list):
        onward = [] if onward is None else [onward]

    def source(name: str, *objects: Mapping[str, Any]) -> Any:
        for obj in objects:
            present, value = _direct(obj, {_normalise_name(name)})
            if present:
                return _scalar(value)
        return None

    result_id = source("resultId", result_obj)
    return {
        "result_id": result_id,
        "result_ordinal": ordinal,
        "journey_ref": source("journeyRef", service_obj, event),
        "operating_day_ref": source("operatingDayRef", service_obj, event),
        "line_ref": source("lineRef", service_obj, event),
        "direction_ref": source("directionRef", service_obj, event),
        "operator_ref": source("operatorRef", service_obj, event),
        "origin": source("origin", service_obj, event),
        "destination": source("destination", service_obj, event),
        "this_call": {
            "stop_point_ref": source("stopPointRef", this_context, this_call_obj),
            "stop_seq_number": source("stopSeqNumber", this_context, this_call_obj),
            "arrival": _timing_context(this_context, {"servicearrival"}),
            "departure": _timing_context(this_context, {"servicedeparture"}),
            "planned_bay": source("plannedBay", this_context, this_call_obj),
            "estimated_bay": source("estimatedBay", this_context, this_call_obj),
            "not_serviced_stop": source("notServicedStop", this_context, this_call_obj),
            "no_boarding_at_stop": source("noBoardingAtStop", this_context, this_call_obj),
            "no_alighting_at_stop": source("noAlightingAtStop", this_context, this_call_obj),
            "unplanned_stop": source("unplannedStop", this_context, this_call_obj),
        },
        "call_scope_counts": {"previous": len(previous), "this": 1, "onward": len(onward)},
        "situation_references": [
            _scalar(value)
            for value in _all_recursive(service_obj, {"situationfullref", "situationref"})
            if _scalar(value) is not None
        ],
    }


def parse_trias_response(body: bytes, content_type: str | None = None) -> ParsedSnapshot:
    root = _decode_body(body, content_type)
    found_delivery, service_delivery = _first_recursive(root, {"servicedelivery"})
    delivery = _as_object(service_delivery if found_delivery else root)
    found_status, status_value = _direct(delivery, {"status"})
    status = _scalar(status_value) if found_status else None
    status_text = str(status).lower() if status is not None else ""
    error_values = _all_recursive(root, {"errormessage", "errorcode", "errorcondition"})
    errors = tuple(str(_scalar(value)) for value in error_values if _scalar(value) not in (None, ""))
    if status_text and status_text not in SUCCESS_STATUSES:
        raise TriasApplicationError("TRIAS application status indicates failure")
    if errors:
        raise TriasApplicationError("TRIAS response contains an application error")

    result_values = _all_recursive(root, {"stopeventresult"})
    if not result_values:
        found_response, response = _first_recursive(root, {"stopeventresponse"})
        result_values = _all_recursive(response if found_response else root, {"stopeventresult"})
    events: list[Mapping[str, Any]] = []
    for value in result_values:
        if isinstance(value, list):
            events.extend(_parse_event(item, len(events)) for item in value)
        else:
            events.append(_parse_event(value, len(events)))

    found_response_time, response_time_value = _first_recursive(root, {"responsetimestamp"})
    response_timestamp = _scalar(response_time_value) if found_response_time else None
    return ParsedSnapshot(
        parser_version=PARSER_VERSION,
        response_timestamp=response_timestamp,
        events=tuple(events),
        response_errors=errors,
        application_status=status_text or None,
    )


parse_response = parse_trias_response
