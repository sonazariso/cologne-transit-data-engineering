"""Source-faithful, transport-independent TRIAS response parsing.

The normalized model follows the Stage 8 parser semantics: every source call
occurrence remains addressable by scope, ordinal, and source path; timing
fields are independent nullable values; and source-reported errors/Situation
evidence remain evidence instead of becoming an opaque parser category.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


PARSER_VERSION = "stage14-trias-parser-2.0"
SUCCESS_STATUSES = {"ok", "success", "successful", "completed", "true", ""}
CALL_SCOPES = ("PREVIOUS", "THIS", "ONWARD")
TIMING_CONTEXTS = ("ARRIVAL", "DEPARTURE")
TIMING_FIELDS = (
    "timetabled_time",
    "estimated_time",
    "recorded_at_time",
    "estimated_time_low",
    "estimated_time_high",
)

# Keep this vocabulary aligned with the approved Stage 8 parser.  A field in
# this set is part of the known TRIAS/source-contract surface; a relevant
# field outside it is retained as explicit parser-gap evidence below.
RECOGNIZED_SOURCE_FIELDS = frozenset(
    {
        "version",
        "servicedelivery",
        "status",
        "responsetimestamp",
        "calctime",
        "deliverypayload",
        "stopeventresponse",
        "stopeventresult",
        "resultid",
        "stopevent",
        "service",
        "servicesection",
        "operatingdayref",
        "journeyref",
        "vehiclejourneyref",
        "datedvehiclejourneyref",
        "affectedvehiclejourney",
        "vehiclejourneys",
        "operatingdaysdescription",
        "lineref",
        "directionref",
        "direction",
        "operatorref",
        "ptmode",
        "mode",
        "modes",
        "railsubmode",
        "submode",
        "publishedlinename",
        "origin",
        "originref",
        "originstoppointref",
        "originstoppointname",
        "origintext",
        "origins",
        "destination",
        "destinationref",
        "destinationstoppointref",
        "destinationstoppointname",
        "destinationtext",
        "destinations",
        "previouscall",
        "thiscall",
        "onwardcall",
        "callatstop",
        "stoppointref",
        "stoppointname",
        "stopname",
        "stopdisplaytext",
        "stopseqnumber",
        "servicearrival",
        "servicedeparture",
        "timetabledtime",
        "estimatedtime",
        "recordedattime",
        "estimatedtimelow",
        "estimatedtimehigh",
        "plannedbay",
        "estimatedbay",
        "cancelled",
        "journeycancelled",
        "deviation",
        "unplannedstop",
        "notservicedstop",
        "noboardingatstop",
        "noalightingatstop",
        "situationfullref",
        "situationref",
        "ptsituation",
        "ptsituationelement",
        "roadsituation",
        "situation",
        "participantref",
        "situationnumber",
        "identifier",
        "id",
        "summary",
        "description",
        "detail",
        "creationtime",
        "validfrom",
        "validto",
        "validityperiod",
        "starttime",
        "endtime",
        "source",
        "sourcetype",
        "priority",
        "affects",
        "affectedentity",
        "affectedjourney",
        "affectedline",
        "attribute",
        "generalattribute",
        "code",
        "text",
        "allfacilities",
        "mandatory",
        "importance",
        "infourl",
        "incidentcall",
        "infocall",
        "errormessage",
        "errorcode",
        "errorcondition",
        "language",
        "lang",
        "value",
        "name",
        "ref",
        "at",
    }
)

RELEVANT_FIELD_TERMS = (
    "situation",
    "affect",
    "attribute",
    "generalattribute",
    "incident",
    "infocall",
    "error",
    "realtime",
    "availability",
    "status",
    "recorded",
    "estimated",
    "timetabled",
    "timing",
    "timestamp",
    "time",
    "schedule",
    "arrival",
    "departure",
    "bay",
    "platform",
    "cancel",
    "deviation",
    "unplanned",
    "notserviced",
    "boarding",
    "alighting",
    "operatingday",
    "journey",
    "service",
    "line",
    "direction",
    "operator",
    "mode",
    "submode",
    "stoppoint",
    "stopseq",
    "origin",
    "destination",
    "resultid",
    "resultref",
    "resultreference",
    "stopid",
    "stopref",
    "stopidentifier",
    "callid",
    "callref",
    "callidentifier",
    "priority",
    "valid",
    "creation",
    "publishedline",
)


class TriasParserError(ValueError):
    """Deterministic response decoding/schema failure; never retry automatically."""


class TriasApplicationError(ValueError):
    """Compatibility exception for callers that choose exception-style handling."""

    def __init__(self, message: str, snapshot: "ParsedSnapshot | None" = None) -> None:
        super().__init__(message)
        self.snapshot = snapshot


@dataclass(frozen=True)
class ParsedSnapshot:
    parser_version: str
    response_timestamp: Any
    events: tuple[Mapping[str, Any], ...]
    parser_gaps: tuple[Mapping[str, Any], ...] = ()
    response_errors: tuple[Mapping[str, Any], ...] = ()
    application_status: str | None = None
    application_status_source_path: str | None = None

    @property
    def result_count(self) -> int:
        return len(self.events)

    @property
    def application_error(self) -> bool:
        status = (self.application_status or "").casefold()
        return (bool(status) and status not in SUCCESS_STATUSES) or bool(self.response_errors)

    @property
    def recorded_at_time_occurrences(self) -> tuple[Mapping[str, Any], ...]:
        values: list[Mapping[str, Any]] = []
        for event in self.events:
            for occurrence in event.get("recorded_at_time_occurrences", []) or []:
                if isinstance(occurrence, Mapping):
                    values.append(occurrence)
        return tuple(values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "parser_version": self.parser_version,
            "response_timestamp": self.response_timestamp,
            "events": [dict(event) for event in self.events],
            "parser_gaps": [dict(gap) for gap in self.parser_gaps],
            "response_errors": [dict(error) for error in self.response_errors],
            "application_status": self.application_status,
            "application_status_source_path": self.application_status_source_path,
            "application_error": self.application_error,
            "recorded_at_time_occurrences": [
                dict(occurrence) for occurrence in self.recorded_at_time_occurrences
            ],
            "raw_payload_persisted": False,
        }


def _normalise_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower().split("}")[-1])


def _path_for_key(parent_path: str, key: Any) -> str:
    key_text = _normalise_name(key)
    if key_text and all(character.isalnum() or character == "_" for character in key_text):
        return f"{parent_path}.{key_text}"
    return f"{parent_path}[{json.dumps(key_text, ensure_ascii=False)}]"


def _iter_entries(value: Any, path: str = "$") -> Iterable[tuple[str, str, Any, str, Any]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = _path_for_key(path, key)
            yield child_path, str(key), child, path, value
            yield from _iter_entries(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_entries(child, f"{path}[{index}]")


def _scalar(value: Any) -> Any:
    """Extract one source scalar without normalizing its lexical value."""

    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, str) and not value.strip():
            return None
        return value
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _normalise_name(key) in {"value", "text", "content", "name", "id", "ref"}:
                extracted = _scalar(child)
                if extracted is not None:
                    return extracted
        return None
    if isinstance(value, list):
        values = [_scalar(item) for item in value]
        values = [item for item in values if item is not None]
        if not values:
            return None
        return values[0] if len(values) == 1 else values
    return str(value)


def _flag(value: Any) -> Any:
    if isinstance(value, str) and value.strip().casefold() in {"true", "false"}:
        return value.strip().casefold() == "true"
    return value


def _sequence(value: Any) -> Any:
    if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
        return int(value.strip())
    return value


def _direct_entry(obj: Any, names: set[str]) -> tuple[bool, str | None, Any]:
    if not isinstance(obj, Mapping):
        return False, None, None
    for key, value in obj.items():
        if _normalise_name(key) in names:
            return True, str(key), value
    return False, None, None


def _direct(obj: Any, names: set[str]) -> tuple[bool, Any]:
    found, _key, value = _direct_entry(obj, names)
    return found, value


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


def _named_occurrences(value: Any, names: set[str], root_path: str = "$") -> list[tuple[str, Any]]:
    output: list[tuple[str, Any]] = []
    for path, key, child, _parent_path, _parent in _iter_entries(value, root_path):
        if _normalise_name(key) not in names:
            continue
        if isinstance(child, list):
            output.extend((f"{path}[{index}]", item) for index, item in enumerate(child))
        else:
            output.append((path, child))
    return output


def _named_object_occurrences(value: Any, names: set[str], root_path: str = "$") -> list[tuple[str, Mapping[str, Any]]]:
    return [
        (path, item)
        for path, item in _named_occurrences(value, names, root_path)
        if isinstance(item, Mapping)
    ]


def _as_object(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_occurrences(value: Any, path: str) -> list[tuple[str, Mapping[str, Any]]]:
    if isinstance(value, list):
        return [
            (f"{path}[{index}]", item)
            for index, item in enumerate(value)
            if isinstance(item, Mapping)
        ]
    return [(path, value)] if isinstance(value, Mapping) else []


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
            parsed = _xml_to_object(root)
        except (ET.ParseError, UnicodeDecodeError) as exc:
            raise TriasParserError("response is not valid XML") from exc
    if not isinstance(parsed, Mapping):
        raise TriasParserError("TRIAS response root must be an object")
    return parsed


def _direct_source(obj: Any, names: set[str], path: str) -> tuple[Any, str | None]:
    found, key, value = _direct_entry(obj, names)
    if not found or key is None:
        return None, None
    return _scalar(value), _path_for_key(path, key)


def _source_value_at_path(value: Any, path: str) -> tuple[str | None, Any]:
    """Return a source scalar and the deepest safe source path for it."""

    if value is None:
        return path, None
    if isinstance(value, (str, int, float, bool)):
        return path, _scalar(value)
    if isinstance(value, Mapping):
        for preferred_name in ("value", "text", "name", "id", "ref", "sourcetype"):
            for key, child in value.items():
                if _normalise_name(key) != preferred_name:
                    continue
                child_path = _path_for_key(path, key)
                leaf_path, extracted = _source_value_at_path(child, child_path)
                if extracted is not None:
                    return leaf_path, extracted
        return path, _scalar(value)
    if isinstance(value, list):
        for index, item in enumerate(value):
            leaf_path, extracted = _source_value_at_path(item, f"{path}[{index}]")
            if extracted is not None:
                return leaf_path, extracted
        return path, _scalar(value)
    return path, _scalar(value)


def _direct_raw(obj: Any, names: set[str], path: str) -> tuple[str | None, Any]:
    found, key, value = _direct_entry(obj, names)
    if not found or key is None:
        return None, None
    return _path_for_key(path, key), value


def _first_named_raw(obj: Any, names: set[str], path: str) -> tuple[str | None, Any]:
    for entry_path, key, child, _parent_path, _parent in _iter_entries(obj, path):
        if _normalise_name(key) in names:
            return entry_path, child
    return None, None


def _origin_destination(service: Mapping[str, Any], event: Mapping[str, Any], *, service_path: str, event_path: str) -> dict[str, Any]:
    """Retain origin/destination identity and display text independently."""

    output: dict[str, Any] = {}
    objects = ((service, service_path), (event, event_path))
    for prefix, specific_ref_names, specific_text_names, container_names in (
        (
            "origin",
            {"originstoppointref", "originref"},
            {"origintext", "originstoppointname"},
            {"origin"},
        ),
        (
            "destination",
            {"destinationstoppointref", "destinationref"},
            {"destinationtext", "destinationstoppointname"},
            {"destination"},
        ),
    ):
        ref_path: str | None = None
        raw_ref: Any = None
        text_path: str | None = None
        raw_text: Any = None
        container_path: str | None = None
        raw_container: Any = None

        for obj, object_path in objects:
            if ref_path is None:
                ref_path, raw_ref = _direct_raw(obj, specific_ref_names, object_path)
            if text_path is None:
                text_path, raw_text = _direct_raw(obj, specific_text_names, object_path)
            if ref_path is not None and text_path is not None:
                break
        for obj, object_path in objects:
            if ref_path is None:
                ref_path, raw_ref = _first_named_raw(obj, specific_ref_names, object_path)
            if text_path is None:
                text_path, raw_text = _first_named_raw(obj, specific_text_names, object_path)
            if ref_path is not None and text_path is not None:
                break

        if ref_path is None or text_path is None:
            for obj, object_path in objects:
                candidate_path, candidate = _direct_raw(obj, container_names, object_path)
                if candidate_path is None:
                    candidate_path, candidate = _first_named_raw(obj, container_names, object_path)
                if candidate_path is None:
                    continue
                container_path, raw_container = candidate_path, candidate
                if isinstance(candidate, Mapping):
                    if ref_path is None:
                        ref_path, raw_ref = _direct_raw(
                            candidate,
                            {"stoppointref", "originref", "destinationref", "ref", "id"},
                            candidate_path,
                        )
                        if ref_path is None:
                            ref_path, raw_ref = _first_named_raw(
                                candidate,
                                {"stoppointref", "originref", "destinationref", "ref", "id"},
                                candidate_path,
                            )
                    if text_path is None:
                        text_path, raw_text = _direct_raw(
                            candidate,
                            {"origintext", "destinationtext", "stoppointname", "name", "text", "value"},
                            candidate_path,
                        )
                        if text_path is None:
                            text_path, raw_text = _first_named_raw(
                                candidate,
                                {"origintext", "destinationtext", "stoppointname", "name", "text"},
                                candidate_path,
                            )
                elif text_path is None:
                    # A scalar Origin/Destination is retained as display text;
                    # it is never promoted to a stop-point identity.
                    text_path, raw_text = candidate_path, candidate
                break

        ref_value_path, ref = _source_value_at_path(raw_ref, ref_path or service_path)
        text_value_path, text = _source_value_at_path(raw_text, text_path or service_path)
        if ref is not None:
            ref_path = ref_value_path
        if text is not None:
            text_path = text_value_path
        source_path = container_path or ref_path or text_path
        output[f"{prefix}_stop_point_ref"] = ref
        output[f"{prefix}_stop_point_ref_source_path"] = ref_path
        output[f"{prefix}_text"] = text
        output[f"{prefix}_text_source_path"] = text_path
        output[f"{prefix}_source_path"] = source_path
        output[prefix] = {
            "stop_point_ref": ref,
            "stop_point_ref_source_path": ref_path,
            "text": text,
            "text_source_path": text_path,
            "source_path": source_path,
        }
    return output


def _unwrap_call(call: Mapping[str, Any], call_path: str) -> tuple[Mapping[str, Any], str]:
    found, key, value = _direct_entry(call, {"callatstop"})
    if found and key is not None and isinstance(value, Mapping):
        return value, _path_for_key(call_path, key)
    if found and key is not None and isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, Mapping):
                return item, f"{_path_for_key(call_path, key)}[{index}]"
    return call, call_path


def _timing_context(call: Mapping[str, Any], call_path: str, names: set[str]) -> dict[str, Any]:
    found, key, value = _direct_entry(call, names)
    source_path = _path_for_key(call_path, key) if found and key is not None else None
    context = _as_object(value)
    values: dict[str, Any] = {}
    paths: dict[str, str | None] = {}
    for field_name, output_name in (
        ("timetabledtime", "timetabled_time"),
        ("estimatedtime", "estimated_time"),
        ("recordedattime", "recorded_at_time"),
        ("estimatedtimelow", "estimated_time_low"),
        ("estimatedtimehigh", "estimated_time_high"),
    ):
        present, field_key, field_value = _direct_entry(context, {field_name})
        values[output_name] = _scalar(field_value) if present else None
        paths[output_name] = _path_for_key(source_path, field_key) if present and source_path and field_key else None
    values["source_path"] = source_path
    values["source_paths"] = paths
    return values


def _timing_context_from_call(
    objects: tuple[tuple[Mapping[str, Any], str], ...],
    names: set[str],
) -> dict[str, Any]:
    """Read timing only from the current call/CallAtStop source scope."""

    for obj, path in objects:
        timing = _timing_context(obj, path, names)
        if timing["source_path"] is not None:
            return timing
    return _timing_context({}, "$", names)


def _source_from_objects(
    name_sets: tuple[set[str], ...],
    objects: tuple[tuple[Mapping[str, Any], str], ...],
) -> tuple[Any, str | None]:
    for names in name_sets:
        for obj, path in objects:
            value, source_path = _direct_source(obj, names, path)
            if source_path is not None:
                return value, source_path
    return None, None


def _parse_call(
    call: Mapping[str, Any],
    *,
    call_scope: str,
    call_ordinal: int,
    source_path: str,
) -> Mapping[str, Any]:
    context, context_path = _unwrap_call(call, source_path)
    # Only the current occurrence and its CallAtStop child are in scope here.
    # StopEvent/Service objects are deliberately not searched: absent call
    # fields must remain NULL rather than being inherited from journey context.
    objects = ((context, context_path), (call, source_path))

    stop_point_ref, stop_point_path = _source_from_objects(({"stoppointref"},), objects)
    stop_display_text, stop_display_path = _source_from_objects(
        ({"stoppointname", "stopname", "stopdisplaytext"},), objects
    )
    stop_seq_number, stop_seq_path = _source_from_objects(({"stopseqnumber"},), objects)
    stop_seq_number = _sequence(stop_seq_number)
    planned_bay, planned_bay_path = _source_from_objects(({"plannedbay"},), objects)
    estimated_bay, estimated_bay_path = _source_from_objects(({"estimatedbay"},), objects)

    def flag(name: str) -> tuple[Any, str | None]:
        return _source_from_objects(({_normalise_name(name)},), objects)

    arrival = _timing_context_from_call(objects, {"servicearrival"})
    departure = _timing_context_from_call(objects, {"servicedeparture"})
    source_paths = {
        "stop_point_ref": stop_point_path,
        "stop_display_text": stop_display_path,
        "stop_seq_number": stop_seq_path,
        "planned_bay": planned_bay_path,
        "estimated_bay": estimated_bay_path,
    }
    call_flags = {
        "unplanned_stop": flag("unplannedStop"),
        "not_serviced_stop": flag("notServicedStop"),
        "no_boarding_at_stop": flag("noBoardingAtStop"),
        "no_alighting_at_stop": flag("noAlightingAtStop"),
    }
    for field_name, (_field_value, field_path) in call_flags.items():
        source_paths[field_name] = field_path
    flags = {field_name: _flag(value) for field_name, (value, _path) in call_flags.items()}
    return {
        "call_scope": call_scope,
        "call_ordinal": call_ordinal,
        "source_path": source_path,
        "call_at_stop_source_path": context_path,
        "stop_point_ref": stop_point_ref,
        "stop_display_text": stop_display_text,
        "stop_seq_number": stop_seq_number,
        "arrival": arrival,
        "departure": departure,
        "planned_bay": planned_bay,
        "estimated_bay": estimated_bay,
        **flags,
        "source_paths": source_paths,
    }


def _association_scope(path: str) -> str:
    components = [_normalise_name(item) for item in re.findall(r"[A-Za-z0-9_]+", path)]
    if "previouscall" in components:
        return "CALL_PREVIOUS"
    if "thiscall" in components:
        return "CALL_THIS"
    if "onwardcall" in components:
        return "CALL_ONWARD"
    if "service" in components:
        return "SERVICE"
    if "stopevent" in components or "stopeventresult" in components:
        return "STOP_EVENT"
    return "RESPONSE_CONTEXT"


def _call_scope_from_path(path: str) -> str | None:
    components = {_normalise_name(item) for item in re.findall(r"[A-Za-z0-9_]+", path)}
    for key, scope in (("previouscall", "PREVIOUS"), ("thiscall", "THIS"), ("onwardcall", "ONWARD")):
        if key in components:
            return scope
    return None


def _parse_situations(
    result_obj: Mapping[str, Any],
    result_path: str,
    result_id: Any,
    event_ordinal: int,
) -> tuple[list[Mapping[str, Any]], list[Any]]:
    evidence: list[Mapping[str, Any]] = []
    references: list[Any] = []
    for path, key, raw_value, _parent_path, _parent in _iter_entries(result_obj, result_path):
        normalized = _normalise_name(key)
        if normalized not in {"situationfullref", "situationref"}:
            continue
        value = _scalar(raw_value)
        if value is None:
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            references.append(item)
            evidence.append(
                {
                    "evidence_type": "REFERENCE",
                    "reference_type": "SituationFullRef" if normalized == "situationfullref" else "SituationRef",
                    "value": item,
                    "source_path": path,
                    "association_scope": _association_scope(path),
                    "call_scope": _call_scope_from_path(path),
                    "result_id": result_id,
                    "event_ordinal": event_ordinal,
                }
            )
    for path, key, raw_value, _parent_path, _parent in _iter_entries(result_obj, result_path):
        normalized = _normalise_name(key)
        if normalized not in {"ptsituation", "ptsituationelement", "roadsituation"}:
            continue
        items = raw_value if isinstance(raw_value, list) else [raw_value]
        for item in items:
            if not isinstance(item, Mapping):
                continue
            fields: dict[str, Any] = {}
            field_paths: dict[str, str | None] = {}
            for output_name, names in (
                ("participant_ref", {"participantref"}),
                ("situation_number", {"situationnumber"}),
                ("version", {"version"}),
                ("identifier", {"identifier", "id"}),
                ("summary", {"summary"}),
                ("description", {"description"}),
                ("detail", {"detail"}),
                ("creation_time", {"creationtime"}),
                ("valid_from", {"validfrom", "starttime"}),
                ("valid_to", {"validto", "endtime"}),
                ("source", {"source"}),
                ("source_type", {"sourcetype"}),
                ("priority", {"priority"}),
            ):
                value, value_path = _direct_source(item, names, path)
                fields[output_name] = value
                field_paths[output_name] = value_path
            identity = None
            if fields["participant_ref"] is not None and fields["situation_number"] is not None:
                identity = f"PARTICIPANT_AND_SITUATION_NUMBER|{fields['participant_ref']}|{fields['situation_number']}"
                if fields["version"] is not None:
                    identity += f"|VERSION|{fields['version']}"
            elif fields["identifier"] is not None:
                identity = f"IDENTIFIER|{fields['identifier']}"
            evidence.append(
                {
                    "evidence_type": "INLINE_SITUATION",
                    "situation_type": key,
                    "source_path": path,
                    "association_scope": _association_scope(path),
                    "call_scope": _call_scope_from_path(path),
                    "result_id": result_id,
                    "event_ordinal": event_ordinal,
                    "identity_key": identity,
                    "fields": fields,
                    "field_paths": field_paths,
                    "source_representation": dict(item),
                }
            )
    return evidence, references


def _parse_errors(root: Mapping[str, Any], result_entries: list[tuple[str, Mapping[str, Any]]]) -> list[Mapping[str, Any]]:
    errors: list[Mapping[str, Any]] = []
    for path, key, raw_value, _parent_path, _parent in _iter_entries(root):
        normalized = _normalise_name(key)
        if normalized not in {"errormessage", "errorcode", "errorcondition"}:
            continue
        value = _scalar(raw_value)
        if value is None:
            continue
        result_context = None
        for ordinal, (result_path, result_obj) in enumerate(result_entries):
            if path == result_path or path.startswith(result_path + ".") or path.startswith(result_path + "["):
                result_context = (ordinal, result_path, result_obj)
                break
        result_ordinal = result_context[0] if result_context else None
        result_path = result_context[1] if result_context else None
        result_id = None
        if result_context:
            result_id, _ = _direct_source(result_context[2], {"resultid"}, result_path or "$")
        errors.append(
            {
                "field_name": key,
                "value": value,
                "source_path": path,
                "error_scope": "RESULT_LEVEL" if result_context else "RESPONSE_LEVEL",
                "result_id": result_id,
                "result_source_path": result_path,
                "result_ordinal": result_ordinal,
            }
        )
    return errors


_PARSER_GAP_SENSITIVE_NAMES = {
    "apikey",
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
}


def _sanitize_parser_gap_value(value: Any, *, depth: int = 0) -> Any:
    """Retain bounded source representation without exposing credential material."""

    if depth > 4:
        return f"<{type(value).__name__}>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, child in list(value.items())[:50]:
            key_text = str(key)
            if _normalise_name(key_text) in _PARSER_GAP_SENSITIVE_NAMES:
                sanitized[key_text] = "<redacted>"
            else:
                sanitized[key_text] = _sanitize_parser_gap_value(child, depth=depth + 1)
        if len(value) > 50:
            sanitized["<truncated>"] = len(value) - 50
        return sanitized
    if isinstance(value, list):
        sanitized_list = [
            _sanitize_parser_gap_value(item, depth=depth + 1)
            for item in value[:50]
        ]
        if len(value) > 50:
            sanitized_list.append(f"<truncated {len(value) - 50} items>")
        return sanitized_list
    return str(value)[:500]


def _parser_gap_excerpt(value: Any) -> Any:
    safe = _sanitize_parser_gap_value(value)
    if isinstance(safe, (Mapping, list)):
        try:
            text = json.dumps(safe, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            text = str(safe)
        return text[:500]
    return safe


def _parser_gap_entries(
    root: Mapping[str, Any],
    result_entries: list[tuple[str, Mapping[str, Any]]],
) -> list[Mapping[str, Any]]:
    """Retain relevant source fields that are outside the approved mapping."""

    contexts: list[tuple[int, str, Mapping[str, Any], Any]] = []
    for ordinal, (result_path, result) in enumerate(result_entries):
        result_id, _ = _direct_source(result, {"resultid"}, result_path)
        contexts.append((ordinal, result_path, result, result_id))

    def context_for_path(path: str) -> tuple[int, str, Any] | None:
        matches = [
            (ordinal, result_path, result_id)
            for ordinal, result_path, _result, result_id in contexts
            if path == result_path
            or path.startswith(result_path + ".")
            or path.startswith(result_path + "[")
        ]
        return max(matches, key=lambda item: len(item[1])) if matches else None

    gaps: list[Mapping[str, Any]] = []
    for path, key, raw_value, _parent_path, _parent in _iter_entries(root):
        normalized = _normalise_name(key)
        if normalized in RECOGNIZED_SOURCE_FIELDS:
            continue
        if "extension" not in normalized and not any(
            term in normalized for term in RELEVANT_FIELD_TERMS
        ):
            continue
        context = context_for_path(path)
        result_ordinal = context[0] if context else None
        result_path = context[1] if context else None
        result_id = context[2] if context else None
        scalar_value = _scalar(raw_value)
        gaps.append(
            {
                "field_name": str(key),
                "normalized_field_name": normalized,
                "normalized_name": normalized,
                "source_path": path,
                "value_type": type(raw_value).__name__,
                "source_value": _sanitize_parser_gap_value(scalar_value),
                "source_representation": _sanitize_parser_gap_value(raw_value),
                "value_excerpt": _parser_gap_excerpt(raw_value),
                "mapping_status": "UNMAPPED_RELEVANT_SOURCE_FIELD",
                "semantic_hint": "RELEVANT_FIELD_NAME_OUTSIDE_APPROVED_MAPPING",
                "result_id": result_id,
                "result_source_path": result_path,
                "result_ordinal": result_ordinal,
                "event_ordinal": result_ordinal,
                "event_context": "STOP_EVENT_RESULT" if context else "RESPONSE_CONTEXT",
                "association_scope": _association_scope(path),
                "call_scope": _call_scope_from_path(path),
            }
        )
    return gaps


def _parse_event(result: Any, result_path: str, ordinal: int) -> Mapping[str, Any]:
    result_obj = _as_object(result)
    found, stop_event_key, stop_event_value = _direct_entry(result_obj, {"stopevent"})
    event = _as_object(stop_event_value if found else result_obj)
    event_path = _path_for_key(result_path, stop_event_key) if found and stop_event_key else result_path
    found, service_key, service_value = _direct_entry(event, {"service"})
    service = _as_object(service_value if found else {})
    service_path = _path_for_key(event_path, service_key) if found and service_key else event_path
    result_id, result_id_path = _direct_source(result_obj, {"resultid"}, result_path)
    service_objects = ((service, service_path), (event, event_path))
    event_objects = ((event, event_path), (service, service_path))

    def service_identity(output_name: str, names: set[str]) -> tuple[Any, str | None]:
        return _source_from_objects((names,), service_objects)

    identity_values: dict[str, Any] = {}
    identity_paths: dict[str, str | None] = {}
    for output_name, names in (
        ("journey_ref", {"journeyref", "vehiclejourneyref", "datedvehiclejourneyref"}),
        ("operating_day_ref", {"operatingdayref"}),
        ("line_ref", {"lineref"}),
        ("direction_ref", {"directionref", "direction"}),
        ("operator_ref", {"operatorref"}),
        ("pt_mode", {"ptmode", "mode", "modes"}),
        ("rail_submode", {"railsubmode", "submode"}),
        ("published_line_name", {"publishedlinename"}),
    ):
        value, value_path = service_identity(output_name, names)
        identity_values[output_name] = value
        identity_values[f"{output_name}_source_path"] = value_path
        identity_paths[output_name] = value_path

    origin_destination = _origin_destination(
        service,
        event,
        service_path=service_path,
        event_path=event_path,
    )
    for prefix in ("origin", "destination"):
        identity_values[prefix] = origin_destination[prefix]
        identity_values[f"{prefix}_source_path"] = origin_destination[f"{prefix}_source_path"]
        for field_name in ("stop_point_ref", "text"):
            output_name = f"{prefix}_{field_name}"
            value = origin_destination[output_name]
            value_path = origin_destination[f"{output_name}_source_path"]
            identity_values[output_name] = value
            identity_values[f"{output_name}_source_path"] = value_path
            identity_paths[output_name] = value_path
        identity_paths[prefix] = origin_destination[f"{prefix}_source_path"]

    calls_by_scope: dict[str, list[Mapping[str, Any]]] = {scope: [] for scope in CALL_SCOPES}
    for scope, names in (
        ("PREVIOUS", {"previouscall"}),
        ("THIS", {"thiscall", "callatstop"}),
        ("ONWARD", {"onwardcall"}),
    ):
        found, call_key, call_value = _direct_entry(event, names)
        occurrences = (
            _as_occurrences(call_value, _path_for_key(event_path, call_key))
            if found and call_key is not None
            else []
        )
        for call_ordinal, (call_path, call_value) in enumerate(occurrences):
            calls_by_scope[scope].append(
                _parse_call(
                    call_value,
                    call_scope=scope,
                    call_ordinal=call_ordinal,
                    source_path=call_path,
                )
            )
    # A ThisCall response is normally a single object. Preserve one object in
    # the compatibility key while retaining the normalized list for all scopes.
    calls = [call for scope in CALL_SCOPES for call in calls_by_scope[scope]]
    recorded_occurrences: list[Mapping[str, Any]] = []
    for call in calls:
        for timing_context in TIMING_CONTEXTS:
            timing = call[timing_context.casefold()]
            value = timing.get("recorded_at_time")
            if value is None:
                continue
            recorded_occurrences.append(
                {
                    "recorded_at_time": value,
                    "result_id": result_id,
                    "result_ordinal": ordinal,
                    "event_ordinal": ordinal,
                    "call_scope": call["call_scope"],
                    "call_ordinal": call["call_ordinal"],
                    "timing_context": timing_context,
                    "source_path": timing.get("source_paths", {}).get("recorded_at_time"),
                    "observation_lineage": {
                        "result_id": result_id,
                        "result_source_path": result_path,
                        "event_source_path": event_path,
                        "call_source_path": call.get("source_path"),
                        "timing_source_path": timing.get("source_path"),
                    },
                }
            )

    situation_evidence, situation_references = _parse_situations(
        result_obj, result_path, result_id, ordinal
    )
    cancelled, cancelled_path = _source_from_objects(
        ({"cancelled"}, {"journeycancelled"}), event_objects
    )
    deviation, deviation_path = _source_from_objects(({"deviation"},), event_objects)
    event_record = {
        "result_id": result_id,
        "result_source_path": result_path,
        "result_id_source_path": result_id_path,
        "result_ordinal": ordinal,
        "event_ordinal": ordinal,
        **identity_values,
        "identity_source_paths": identity_paths,
        "service_source_path": service_path,
        "calls": calls,
        "previous_calls": calls_by_scope["PREVIOUS"],
        "this_calls": calls_by_scope["THIS"],
        "onward_calls": calls_by_scope["ONWARD"],
        "this_call": calls_by_scope["THIS"][0] if calls_by_scope["THIS"] else {
            "call_scope": "THIS",
            "call_ordinal": 0,
            "source_path": None,
            "call_at_stop_source_path": None,
            "stop_point_ref": None,
            "stop_display_text": None,
            "stop_seq_number": None,
            "arrival": {field: None for field in TIMING_FIELDS},
            "departure": {field: None for field in TIMING_FIELDS},
            "planned_bay": None,
            "estimated_bay": None,
            "unplanned_stop": None,
            "not_serviced_stop": None,
            "no_boarding_at_stop": None,
            "no_alighting_at_stop": None,
            "source_paths": {},
        },
        "call_scope_counts": {
            "previous": len(calls_by_scope["PREVIOUS"]),
            "this": len(calls_by_scope["THIS"]),
            "onward": len(calls_by_scope["ONWARD"]),
        },
        "recorded_at_time_occurrences": recorded_occurrences,
        "situation_references": situation_references,
        "situation_evidence": situation_evidence,
        "operational_event_fields": {
            "cancelled": _flag(cancelled),
            "deviation": _flag(deviation),
        },
        "operational_event_field_source_paths": {
            "cancelled": cancelled_path,
            "deviation": deviation_path,
        },
    }
    return event_record


def parse_trias_response(body: bytes, content_type: str | None = None) -> ParsedSnapshot:
    root = _decode_body(body, content_type)
    found_delivery, service_delivery = _first_recursive(root, {"servicedelivery"})
    delivery = _as_object(service_delivery if found_delivery else root)
    found_status, status_key, status_value = _direct_entry(delivery, {"status"})
    status = _scalar(status_value) if found_status else None
    status_text = str(status).lower() if status is not None else ""
    status_path = _path_for_key("$", status_key) if found_status and status_key else None

    result_entries = _named_object_occurrences(root, {"stopeventresult"})
    events = tuple(
        _parse_event(result, result_path, ordinal)
        for ordinal, (result_path, result) in enumerate(result_entries)
    )
    errors = tuple(_parse_errors(root, result_entries))
    found_response_time, response_time_value = _first_recursive(root, {"responsetimestamp"})
    response_timestamp = _scalar(response_time_value) if found_response_time else None

    snapshot = ParsedSnapshot(
        parser_version=PARSER_VERSION,
        response_timestamp=response_timestamp,
        events=events,
        parser_gaps=tuple(_parser_gap_entries(root, result_entries)),
        response_errors=errors,
        application_status=status_text or None,
        application_status_source_path=status_path,
    )
    # A syntactically valid application-error response is retained as a
    # structured snapshot. The collector records it as a deterministic failure
    # without losing ErrorMessage/ErrorCode/Situation evidence.
    return snapshot


parse_response = parse_trias_response
