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


PARSER_VERSION = "stage14-trias-parser-2.1"
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

# The source vocabulary is intentionally split by retention behavior.  The
# parser-gap path must not use one broad "recognized" set as proof that a
# field was retained: a recognized field can still be relevant and unmapped.
STRUCTURAL_SOURCE_FIELDS = frozenset(
    {
        "servicedelivery",
        "deliverypayload",
        "stopeventresponse",
        "stopeventresult",
        "stopevent",
        "service",
        "servicesection",
        "previouscall",
        "thiscall",
        "onwardcall",
        "callatstop",
        "servicearrival",
        "servicedeparture",
        "incidentcall",
        "infocall",
        "serviceinformation",
        # Observed in the S78 Heumarkt response as containers without an
        # event-level semantic mapping.  They are retained as structural
        # parser-gap exclusions until a source-specific mapping is justified.
        "parallelservice",
        "situations",
    }
)

STRUCTURAL_GAP_CONTAINER_FIELDS = frozenset({"parallelservice", "situations"})

EXPLICIT_RETAINED_SOURCE_FIELDS = frozenset(
    {
        "status",
        "responsetimestamp",
        "resultid",
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
        "stoppointref",
        "stoppointname",
        "stopname",
        "stopdisplaytext",
        "stopseqnumber",
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
        "errormessage",
        "errorcode",
        "errorcondition",
    }
)

SAFE_GENERIC_SOURCE_FIELDS = frozenset(
    {
        # These are bounded generic lexical/container metadata.  They are
        # retained by their enclosing explicit evidence where applicable and
        # are not independently treated as unmapped contract fields.
        "version",
        "calctime",
        "value",
        "content",
        "language",
        "lang",
        "name",
        "ref",
        "id",
        "at",
    }
)

# Backwards-compatible public vocabulary used by earlier Stage 8/14 checks.
# It is descriptive only; parser-gap suppression uses the categories above
# plus the actual source paths retained during parsing.
RECOGNIZED_SOURCE_FIELDS = frozenset(
    STRUCTURAL_SOURCE_FIELDS
    | EXPLICIT_RETAINED_SOURCE_FIELDS
    | SAFE_GENERIC_SOURCE_FIELDS
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

    @property
    def service_attribute_evidence(self) -> tuple[Mapping[str, Any], ...]:
        values: list[Mapping[str, Any]] = []
        for event in self.events:
            for evidence in event.get("service_attribute_evidence", []) or []:
                if isinstance(evidence, Mapping):
                    values.append(evidence)
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
            "service_attribute_evidence": [
                dict(evidence) for evidence in self.service_attribute_evidence
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
    return _safe_retained_value(value), _path_for_key(path, key)


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


def _safe_retained_value(value: Any) -> Any:
    """Keep a bounded source value when a structured field has no scalar leaf."""

    scalar = _scalar(value)
    if scalar is not None:
        return scalar
    if isinstance(value, str) and not value.strip():
        return None
    if value is None:
        return None
    # The sanitizer is defined below with the parser-gap helpers.  The call
    # happens only after module initialization, so structured retained fields
    # use the same bounded, secret-safe representation as parser gaps.
    return _sanitize_parser_gap_value(value)


def _named_source_entries(
    value: Any,
    names: set[str],
    root_path: str = "$",
) -> list[tuple[str, Any]]:
    entries: list[tuple[str, Any]] = []
    for path, key, child, _parent_path, _parent in _iter_entries(value, root_path):
        if _normalise_name(key) not in names:
            continue
        extracted = _safe_retained_value(child)
        if extracted is not None:
            entries.append((path, extracted))
    return entries


def _mark_retained_path(
    retained_paths: dict[str, bool] | None,
    path: str | None,
    *,
    subtree: bool,
) -> None:
    """Mark a source path as retained, optionally including its children."""

    if retained_paths is None or path is None:
        return
    retained_paths[path] = retained_paths.get(path, False) or subtree


def _mark_named_paths(
    value: Any,
    names: set[str],
    root_path: str,
    retained_paths: dict[str, bool] | None,
    *,
    subtree: bool = True,
) -> None:
    if retained_paths is None:
        return
    for path, key, _child, _parent_path, _parent in _iter_entries(value, root_path):
        if _normalise_name(key) in names:
            _mark_retained_path(retained_paths, path, subtree=subtree)


def _mark_direct_named_paths(
    value: Any,
    names: set[str],
    root_path: str,
    retained_paths: dict[str, bool] | None,
    *,
    subtree: bool = True,
) -> None:
    if retained_paths is None or not isinstance(value, Mapping):
        return
    for key in value:
        if _normalise_name(key) in names:
            _mark_retained_path(
                retained_paths,
                _path_for_key(root_path, key),
                subtree=subtree,
            )


def _path_is_retained(path: str, retained_paths: Mapping[str, bool]) -> bool:
    for retained_path, includes_children in retained_paths.items():
        if path == retained_path:
            return True
        if includes_children and (
            path.startswith(retained_path + ".")
            or path.startswith(retained_path + "[")
        ):
            return True
    return False


def _path_component_prefix(path: str, names: set[str]) -> str | None:
    """Return the deepest named context path embedded in a normalized path."""

    matches = list(
        re.finditer(r"(?:^|\.)([a-z0-9_]+)(?=\.|\[|$)", path)
    )
    for match in reversed(matches):
        if match.group(1) in names:
            return path[: match.end()]
    return None


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


def _origin_destination(
    service: Mapping[str, Any],
    event: Mapping[str, Any] | None = None,
    *,
    service_path: str | None,
    event_path: str | None = None,
    retained_paths: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Retain service origin/destination identity and display text independently.

    ``event`` and ``event_path`` remain accepted for source-compatible private
    callers, but are deliberately not searched.  Origin and destination are
    service-scoped contract fields; a StopEvent/call lookalike is parser-gap
    evidence, never service identity.
    """

    output: dict[str, Any] = {}
    objects = ((service, service_path),) if service_path is not None else ()
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

        # Mark the source field container before descending to a lexical leaf
        # such as Origin.StopPointRef.Value.  The parent field is the retained
        # source occurrence; its child representation is lineage detail.
        _mark_retained_path(retained_paths, ref_path, subtree=True)
        _mark_retained_path(retained_paths, text_path, subtree=True)
        ref_value_path, ref = _source_value_at_path(raw_ref, ref_path or service_path or "$")
        text_value_path, text = _source_value_at_path(raw_text, text_path or service_path or "$")
        if ref is not None:
            ref_path = ref_value_path
        if text is not None:
            text_path = text_value_path
        source_path = container_path or ref_path or text_path
        _mark_retained_path(retained_paths, container_path, subtree=False)
        _mark_retained_path(retained_paths, ref_path, subtree=True)
        _mark_retained_path(retained_paths, text_path, subtree=True)
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


def _timing_context(
    call: Mapping[str, Any],
    call_path: str,
    names: set[str],
    retained_paths: dict[str, bool] | None = None,
) -> dict[str, Any]:
    found, key, value = _direct_entry(call, names)
    source_path = _path_for_key(call_path, key) if found and key is not None else None
    _mark_retained_path(retained_paths, source_path, subtree=False)
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
        _mark_retained_path(retained_paths, paths[output_name], subtree=True)
    values["source_path"] = source_path
    values["source_paths"] = paths
    return values


def _timing_context_from_call(
    objects: tuple[tuple[Mapping[str, Any], str], ...],
    names: set[str],
    retained_paths: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Read timing only from the current call/CallAtStop source scope."""

    for obj, path in objects:
        timing = _timing_context(obj, path, names, retained_paths)
        if timing["source_path"] is not None:
            return timing
    return _timing_context({}, "$", names, retained_paths)


def _source_from_objects(
    name_sets: tuple[set[str], ...],
    objects: tuple[tuple[Mapping[str, Any], str], ...],
    *,
    retained_paths: dict[str, bool] | None = None,
    recursive: bool = False,
) -> tuple[Any, str | None]:
    for names in name_sets:
        for obj, path in objects:
            if recursive:
                _mark_named_paths(obj, names, path, retained_paths)
                entries = _named_source_entries(obj, names, path)
                if entries:
                    return entries[0][1], entries[0][0]
            else:
                _mark_direct_named_paths(obj, names, path, retained_paths)
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
    retained_paths: dict[str, bool] | None = None,
) -> Mapping[str, Any]:
    context, context_path = _unwrap_call(call, source_path)
    # Only the current occurrence and its CallAtStop child are in scope here.
    # StopEvent/Service objects are deliberately not searched: absent call
    # fields must remain NULL rather than being inherited from journey context.
    objects = ((context, context_path), (call, source_path))
    _mark_retained_path(retained_paths, source_path, subtree=False)
    _mark_retained_path(retained_paths, context_path, subtree=False)

    stop_point_ref, stop_point_path = _source_from_objects(
        ({"stoppointref"},), objects, retained_paths=retained_paths
    )
    stop_display_text, stop_display_path = _source_from_objects(
        ({"stoppointname", "stopname", "stopdisplaytext"},),
        objects,
        retained_paths=retained_paths,
    )
    stop_seq_number, stop_seq_path = _source_from_objects(
        ({"stopseqnumber"},), objects, retained_paths=retained_paths
    )
    stop_seq_number = _sequence(stop_seq_number)
    planned_bay, planned_bay_path = _source_from_objects(
        ({"plannedbay"},), objects, retained_paths=retained_paths
    )
    estimated_bay, estimated_bay_path = _source_from_objects(
        ({"estimatedbay"},), objects, retained_paths=retained_paths
    )

    def flag(name: str) -> tuple[Any, str | None]:
        return _source_from_objects(
            ({_normalise_name(name)},), objects, retained_paths=retained_paths
        )

    arrival = _timing_context_from_call(
        objects, {"servicearrival"}, retained_paths
    )
    departure = _timing_context_from_call(
        objects, {"servicedeparture"}, retained_paths
    )
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
    scope_evidence: dict[str, Mapping[str, Any]] = {}
    for field_name, names in (
        ("cancelled", {"cancelled", "journeycancelled"}),
        ("deviation", {"deviation"}),
    ):
        value, field_path = _source_from_objects(
            (names,), objects, retained_paths=retained_paths
        )
        if field_path is not None:
            scope_evidence[field_name] = {
                "value": _flag(value),
                "source_path": field_path,
                "semantic_role": "CALL_SCOPE_SOURCE_EVIDENCE_NOT_EVENT_IDENTITY",
            }
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
        "source_scope_evidence": scope_evidence,
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
    retained_paths: dict[str, bool] | None = None,
) -> tuple[list[Mapping[str, Any]], list[Any]]:
    evidence: list[Mapping[str, Any]] = []
    references: list[Any] = []
    for path, key, raw_value, _parent_path, _parent in _iter_entries(result_obj, result_path):
        normalized = _normalise_name(key)
        if normalized not in {"situationfullref", "situationref"}:
            continue
        _mark_retained_path(retained_paths, path, subtree=True)
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
                    "source_representation": _sanitize_parser_gap_value(raw_value),
                }
            )
    situation_occurrences: list[tuple[str, str, Mapping[str, Any]]] = []
    for path, key, raw_value, _parent_path, _parent in _iter_entries(
        result_obj, result_path
    ):
        if _normalise_name(key) not in {
            "ptsituation",
            "ptsituationelement",
            "roadsituation",
            "situation",
        }:
            continue
        if isinstance(raw_value, list):
            situation_occurrences.extend(
                (f"{path}[{index}]", key, item)
                for index, item in enumerate(raw_value)
                if isinstance(item, Mapping)
            )
        elif isinstance(raw_value, Mapping):
            situation_occurrences.append((path, key, raw_value))

    for path, key, item in situation_occurrences:
        _mark_retained_path(retained_paths, path, subtree=True)
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

        affected_context: dict[str, Any] = {}
        affected_paths: dict[str, list[str]] = {}
        for output_name, names in (
            ("affects", {"affects"}),
            ("affected_entity", {"affectedentity"}),
            ("affected_journey", {"affectedjourney"}),
            ("affected_line", {"affectedline"}),
        ):
            entries = _named_source_entries(item, names, path)
            values = [value for _value_path, value in entries]
            affected_context[output_name] = (
                values[0] if len(values) == 1 else values if values else None
            )
            affected_paths[output_name] = [value_path for value_path, _value in entries]

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
                "affected_entity_context": affected_context,
                "affected_entity_field_paths": affected_paths,
                "source_representation": _sanitize_parser_gap_value(item),
            }
        )
    return evidence, references


def _collapse_source_values(values: list[Any]) -> Any:
    if not values:
        return None
    return values[0] if len(values) == 1 else values


def _parse_service_attribute_evidence(
    result_obj: Mapping[str, Any],
    result_path: str,
    result_id: Any,
    event_ordinal: int,
    event_path: str,
    service_path: str | None,
    retained_paths: dict[str, bool] | None = None,
) -> list[Mapping[str, Any]]:
    """Retain provider Attribute/GeneralAttribute evidence without causality."""

    evidence: list[Mapping[str, Any]] = []
    attribute_occurrences: list[tuple[str, str, Any]] = []
    for path, field_name, raw_value, _parent_path, _parent in _iter_entries(
        result_obj, result_path
    ):
        if _normalise_name(field_name) not in {"attribute", "generalattribute"}:
            continue
        _mark_retained_path(retained_paths, path, subtree=True)
        if isinstance(raw_value, list):
            attribute_occurrences.extend(
                (f"{path}[{index}]", field_name, item)
                for index, item in enumerate(raw_value)
            )
        else:
            attribute_occurrences.append((path, field_name, raw_value))

    for ordinal, (path, field_name, raw_value) in enumerate(attribute_occurrences):
        _mark_retained_path(retained_paths, path, subtree=True)
        code_entries = _named_source_entries(raw_value, {"code"}, path)
        text_entries = _named_source_entries(raw_value, {"text"}, path)
        code_values = [value for _path, value in code_entries]
        text_values = [value for _path, value in text_entries]
        incident_path = _path_component_prefix(path, {"incidentcall"})
        info_path = _path_component_prefix(path, {"infocall"})
        association_context_path = (
            incident_path
            or info_path
            or _path_component_prefix(path, {"serviceinformation"})
            or service_path
            or event_path
            or result_path
        )
        evidence.append(
            {
                "evidence_type": "SERVICE_ATTRIBUTE",
                "attribute_type": field_name,
                "attribute_name": field_name,
                "attribute_field_name": field_name,
                "source_path": path,
                "association_context_path": association_context_path,
                "association_scope": _association_scope(path),
                "call_scope": _call_scope_from_path(path),
                "result_id": result_id,
                "result_source_path": result_path,
                "result_ordinal": event_ordinal,
                "event_ordinal": event_ordinal,
                "event_source_path": event_path,
                "service_source_path": service_path,
                "incident_call_source_path": incident_path,
                "info_call_source_path": info_path,
                "code": _collapse_source_values(code_values),
                "code_values": code_values,
                "code_source_paths": [value_path for value_path, _value in code_entries],
                "text": _collapse_source_values(text_values),
                "text_values": text_values,
                "text_source_paths": [value_path for value_path, _value in text_entries],
                "source_field_paths": [
                    value_path for value_path, _value in code_entries + text_entries
                ],
                "source_representation": _sanitize_parser_gap_value(raw_value),
                "source_fragment": _sanitize_parser_gap_value(raw_value),
                "semantic_role": "PROVIDER_REPORTED_EVIDENCE_ONLY",
            }
        )
    return evidence


def _parse_service_context_evidence(
    service: Mapping[str, Any],
    service_path: str | None,
    retained_paths: dict[str, bool] | None = None,
) -> list[Mapping[str, Any]]:
    """Retain non-identity service scalars without promoting their meaning."""

    if service_path is None:
        return []
    evidence: list[Mapping[str, Any]] = []
    for field_name, names in (
        ("stop_point_ref", {"stoppointref"}),
        ("planned_bay", {"plannedbay"}),
        ("estimated_bay", {"estimatedbay"}),
    ):
        found, key, raw_value = _direct_entry(service, names)
        if not found or key is None:
            continue
        source_path = _path_for_key(service_path, key)
        _mark_retained_path(retained_paths, source_path, subtree=True)
        evidence.append(
            {
                "evidence_type": "SERVICE_SOURCE_FIELD",
                "field_name": field_name,
                "source_field_name": key,
                "source_path": source_path,
                "value": _safe_retained_value(raw_value),
                "source_representation": _sanitize_parser_gap_value(raw_value),
                "semantic_role": "SERVICE_CONTEXT_ONLY",
            }
        )
    return evidence


def _parse_errors(
    root: Mapping[str, Any],
    result_entries: list[tuple[str, Mapping[str, Any]]],
    retained_paths: dict[str, bool] | None = None,
) -> list[Mapping[str, Any]]:
    errors: list[Mapping[str, Any]] = []
    for path, key, raw_value, _parent_path, _parent in _iter_entries(root):
        normalized = _normalise_name(key)
        if normalized not in {"errormessage", "errorcode", "errorcondition"}:
            continue
        _mark_retained_path(retained_paths, path, subtree=True)
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
    retained_paths: Mapping[str, bool] | None = None,
) -> list[Mapping[str, Any]]:
    """Retain relevant source fields that are outside the approved mapping.

    A source name in ``RECOGNIZED_SOURCE_FIELDS`` is not sufficient to skip a
    field.  Only an actual retained source path, an intentionally structural
    container, or safe generic metadata may be omitted from gap evidence.
    """

    retained_paths = retained_paths or {}

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
    gap_paths: list[str] = []
    for path, key, raw_value, _parent_path, _parent in _iter_entries(root):
        normalized = _normalise_name(key)
        if _path_is_retained(path, retained_paths):
            continue
        if normalized in STRUCTURAL_SOURCE_FIELDS:
            if normalized in STRUCTURAL_GAP_CONTAINER_FIELDS:
                # Do not turn the members of a deliberately structural
                # container into nested duplicates of the same gap.
                gap_paths.append(path)
            continue
        if normalized in SAFE_GENERIC_SOURCE_FIELDS:
            continue
        if "extension" not in normalized and not any(
            term in normalized for term in RELEVANT_FIELD_TERMS
        ):
            continue
        context = context_for_path(path)
        if context is None and not any(
            component in {
                "servicedelivery",
                "deliverypayload",
                "stopeventresponse",
                "stopeventresult",
            }
            for component in (
                _normalise_name(item)
                for item in re.findall(r"[A-Za-z0-9_]+", path)
            )
        ):
            # Unrelated envelope metadata is not a source-contract gap.
            continue
        if any(
            path.startswith(previous_path + ".")
            or path.startswith(previous_path + "[")
            for previous_path in gap_paths
        ):
            # One bounded parent representation is sufficient for an
            # unmapped relevant container and prevents nested duplicate gaps.
            continue
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
        gap_paths.append(path)
    return gaps


def _parse_event(
    result: Any,
    result_path: str,
    ordinal: int,
    retained_paths: dict[str, bool] | None = None,
) -> Mapping[str, Any]:
    result_obj = _as_object(result)
    found, stop_event_key, stop_event_value = _direct_entry(result_obj, {"stopevent"})
    event = _as_object(stop_event_value if found else result_obj)
    event_path = _path_for_key(result_path, stop_event_key) if found and stop_event_key else result_path
    _mark_retained_path(retained_paths, event_path if found else None, subtree=False)
    found, service_key, service_value = _direct_entry(event, {"service"})
    service = _as_object(service_value if found else {})
    service_path = _path_for_key(event_path, service_key) if found and service_key else None
    _mark_retained_path(retained_paths, service_path, subtree=False)
    result_id, result_id_path = _direct_source(result_obj, {"resultid"}, result_path)
    _mark_retained_path(retained_paths, result_id_path, subtree=True)
    service_objects = ((service, service_path),) if service_path is not None else ()
    identity_objects = service_objects + ((event, event_path),)
    event_objects = ((event, event_path),) + service_objects

    def identity_value(names: set[str]) -> tuple[Any, str | None]:
        return _source_from_objects(
            (names,),
            identity_objects,
            retained_paths=retained_paths,
        )

    def service_identity(names: set[str]) -> tuple[Any, str | None]:
        return _source_from_objects(
            (names,),
            service_objects,
            retained_paths=retained_paths,
        )

    identity_values: dict[str, Any] = {}
    identity_paths: dict[str, str | None] = {}
    for output_name, names in (
        ("journey_ref", {"journeyref"}),
        ("vehicle_journey_ref", {"vehiclejourneyref"}),
        ("dated_vehicle_journey_ref", {"datedvehiclejourneyref"}),
        ("affected_vehicle_journey", {"affectedvehiclejourney"}),
        ("vehicle_journeys", {"vehiclejourneys"}),
        ("operating_days_description", {"operatingdaysdescription"}),
        ("direction", {"direction"}),
        ("origins", {"origins"}),
        ("destinations", {"destinations"}),
        ("operating_day_ref", {"operatingdayref"}),
        ("line_ref", {"lineref"}),
        ("direction_ref", {"directionref"}),
        ("operator_ref", {"operatorref"}),
    ):
        value, value_path = identity_value(names)
        identity_values[output_name] = value
        identity_values[f"{output_name}_source_path"] = value_path
        identity_paths[output_name] = value_path

    for output_name, names in (
        ("pt_mode", {"ptmode", "mode", "modes"}),
        ("rail_submode", {"railsubmode", "submode"}),
        ("published_line_name", {"publishedlinename"}),
    ):
        value, value_path = service_identity(names)
        identity_values[output_name] = value
        identity_values[f"{output_name}_source_path"] = value_path
        identity_paths[output_name] = value_path
    # Keep the approved submode name and a direct compatibility representation
    # without creating a second source lookup or inventing a value.
    identity_values["submode"] = identity_values["rail_submode"]
    identity_values["submode_source_path"] = identity_values["rail_submode_source_path"]
    identity_paths["submode"] = identity_values["submode_source_path"]

    origin_destination = _origin_destination(
        service,
        service_path=service_path,
        retained_paths=retained_paths,
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

    source_identity_extensions = {}
    for field_name in (
        "vehicle_journey_ref",
        "dated_vehicle_journey_ref",
        "affected_vehicle_journey",
        "vehicle_journeys",
        "operating_days_description",
        "direction",
        "origins",
        "destinations",
    ):
        source_identity_extensions[field_name] = {
            "value": identity_values.get(field_name),
            "source_path": identity_values.get(f"{field_name}_source_path"),
        }

    calls_by_scope: dict[str, list[Mapping[str, Any]]] = {scope: [] for scope in CALL_SCOPES}
    for scope, names in (
        ("PREVIOUS", {"previouscall"}),
        ("THIS", {"thiscall", "callatstop"}),
        ("ONWARD", {"onwardcall"}),
    ):
        _mark_named_paths(event, names, event_path, retained_paths, subtree=False)
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
                    retained_paths=retained_paths,
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
        result_obj, result_path, result_id, ordinal, retained_paths
    )
    service_context_evidence = _parse_service_context_evidence(
        service, service_path, retained_paths
    )
    service_attribute_evidence = _parse_service_attribute_evidence(
        result_obj,
        result_path,
        result_id,
        ordinal,
        event_path,
        service_path,
        retained_paths,
    )
    cancelled, cancelled_path = _source_from_objects(
        ({"cancelled"}, {"journeycancelled"}),
        event_objects,
        retained_paths=retained_paths,
    )
    deviation, deviation_path = _source_from_objects(
        ({"deviation"},), event_objects, retained_paths=retained_paths
    )
    event_record = {
        "result_id": result_id,
        "result_source_path": result_path,
        "result_id_source_path": result_id_path,
        "result_ordinal": ordinal,
        "event_ordinal": ordinal,
        **identity_values,
        "source_identity_extensions": source_identity_extensions,
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
        "service_context_evidence": service_context_evidence,
        "service_attribute_evidence": service_attribute_evidence,
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
    delivery_path, service_delivery = _first_named_raw(root, {"servicedelivery"}, "$")
    found_delivery = delivery_path is not None
    delivery = _as_object(service_delivery if found_delivery else root)
    _retained_paths: dict[str, bool] = {}
    _mark_retained_path(_retained_paths, delivery_path, subtree=False)
    status_path, status_value = _first_named_raw(
        delivery,
        {"status"},
        delivery_path or "$",
    )
    status = _safe_retained_value(status_value)
    _mark_retained_path(_retained_paths, status_path, subtree=True)
    status_text = str(status).lower() if status is not None else ""

    result_entries = _named_object_occurrences(root, {"stopeventresult"})
    events = tuple(
        _parse_event(result, result_path, ordinal, _retained_paths)
        for ordinal, (result_path, result) in enumerate(result_entries)
    )
    errors = tuple(_parse_errors(root, result_entries, _retained_paths))
    response_time_path, response_time_value = _first_named_raw(
        root, {"responsetimestamp"}, "$"
    )
    response_timestamp = _safe_retained_value(response_time_value)
    _mark_retained_path(_retained_paths, response_time_path, subtree=True)

    snapshot = ParsedSnapshot(
        parser_version=PARSER_VERSION,
        response_timestamp=response_timestamp,
        events=events,
        parser_gaps=tuple(
            _parser_gap_entries(root, result_entries, _retained_paths)
        ),
        response_errors=errors,
        application_status=status_text or None,
        application_status_source_path=status_path,
    )
    # A syntactically valid application-error response is retained as a
    # structured snapshot. The collector records it as a deterministic failure
    # without losing ErrorMessage/ErrorCode/Situation evidence.
    return snapshot


parse_response = parse_trias_response
