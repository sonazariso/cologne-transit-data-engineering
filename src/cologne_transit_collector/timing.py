"""Source-faithful timing observations and MDD-local event identity.

This module deliberately keeps timing semantics narrow.  The collector
timestamp is observation lineage, not a source event time, and neither
``EstimatedTime`` nor ``RecordedAtTime`` participates in the source-event
identity.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any


SOURCE_EVENT_IDENTITY_ESTABLISHED = "SOURCE_EVENT_IDENTITY_ESTABLISHED"
SOURCE_EVENT_IDENTITY_INSUFFICIENT = "SOURCE_EVENT_IDENTITY_INSUFFICIENT"
SOURCE_EVENT_IDENTITY_NAMESPACE = "MDD_SOURCE_EVENT"
TIMING_CONTEXTS = ("ARRIVAL", "DEPARTURE")
TIMING_FIELDS = (
    "timetabled_time",
    "estimated_time",
    "estimated_time_low",
    "estimated_time_high",
    "recorded_at_time",
)

_REQUIRED_IDENTITY_FIELDS = (
    "operating_day_ref",
    "journey_ref",
    "stop_point_ref",
    "timetabled_time",
    "timing_context",
)
_DISAMBIGUATING_EVENT_FIELDS = (
    "vehicle_journey_ref",
    "dated_vehicle_journey_ref",
    "line_ref",
    "direction_ref",
    "operator_ref",
)


def _present(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _source_scalar(value: Any) -> Any:
    """Keep normalized source values JSON-safe without inventing semantics."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _source_scalar(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_source_scalar(item) for item in value]
    return str(value)


def _text(value: Any) -> str | None:
    if not _present(value):
        return None
    if isinstance(value, (Mapping, list, tuple)):
        return json.dumps(_source_scalar(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(value)


def _event_source_path(event: Mapping[str, Any], field_name: str) -> str | None:
    direct = event.get(f"{field_name}_source_path")
    if direct is not None:
        return str(direct)
    paths = event.get("identity_source_paths")
    if isinstance(paths, Mapping) and paths.get(field_name) is not None:
        return str(paths[field_name])
    return None


def source_event_identity(
    event: Mapping[str, Any],
    call: Mapping[str, Any],
    timing_context: str,
) -> dict[str, Any]:
    """Build a deterministic, explicitly MDD-local source-event identity.

    The conservative required basis is operating day + journey + stop point +
    timetable + arrival/departure context.  Call scope is intentionally not
    part of the key because one source event can move from ONWARD to THIS to
    PREVIOUS across snapshots.  ``ResultId`` and mutable timing values are
    retained as lineage/data but never enter this identity.
    """

    context = str(timing_context).upper()
    timing = call.get(context.casefold())
    timing = timing if isinstance(timing, Mapping) else {}
    basis: dict[str, Any] = {
        "identity_namespace": SOURCE_EVENT_IDENTITY_NAMESPACE,
        "operating_day_ref": _source_scalar(event.get("operating_day_ref")),
        "journey_ref": _source_scalar(event.get("journey_ref")),
        "stop_point_ref": _source_scalar(call.get("stop_point_ref")),
        "timetabled_time": _source_scalar(timing.get("timetabled_time")),
        "timing_context": context,
    }
    missing = [field for field in _REQUIRED_IDENTITY_FIELDS if not _present(basis.get(field))]
    disambiguating: dict[str, Any] = {}
    for field in _DISAMBIGUATING_EVENT_FIELDS:
        value = event.get(field)
        if _present(value):
            disambiguating[field] = _source_scalar(value)
    stop_sequence = call.get("stop_seq_number")
    if _present(stop_sequence):
        disambiguating["stop_seq_number"] = _source_scalar(stop_sequence)
    basis["disambiguating_source_fields"] = disambiguating

    if missing:
        return {
            "source_event_identity": None,
            "source_event_identity_status": SOURCE_EVENT_IDENTITY_INSUFFICIENT,
            "source_event_identity_basis": basis,
            "source_event_identity_missing_fields": missing,
        }

    canonical_basis = {
        **{field: basis[field] for field in _REQUIRED_IDENTITY_FIELDS},
        "disambiguating_source_fields": disambiguating,
    }
    encoded = json.dumps(
        canonical_basis,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return {
        "source_event_identity": f"{SOURCE_EVENT_IDENTITY_NAMESPACE}|{digest}",
        "source_event_identity_status": SOURCE_EVENT_IDENTITY_ESTABLISHED,
        "source_event_identity_basis": basis,
        "source_event_identity_missing_fields": [],
    }


def _event_calls(event: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    calls = event.get("calls")
    if isinstance(calls, list):
        return [call for call in calls if isinstance(call, Mapping)]
    collected: list[Mapping[str, Any]] = []
    for key in ("previous_calls", "this_calls", "onward_calls"):
        values = event.get(key)
        if isinstance(values, list):
            collected.extend(call for call in values if isinstance(call, Mapping))
    if collected:
        return collected
    this_call = event.get("this_call")
    return [this_call] if isinstance(this_call, Mapping) else []


def _timing_context_available(timing: Mapping[str, Any]) -> bool:
    # Parser-created timing dictionaries always carry these keys, even when
    # the corresponding source context was absent.  The parent path is the
    # reliable distinction between "no ServiceArrival/ServiceDeparture" and
    # an existing context whose individual fields are all NULL.
    if "source_path" in timing or "source_paths" in timing:
        source_path = timing.get("source_path")
        if _present(source_path):
            return True
        source_paths = timing.get("source_paths")
        return isinstance(source_paths, Mapping) and any(
            _present(value) for value in source_paths.values()
        )
    # Hand-authored deterministic fixtures may omit parser lineage paths.  An
    # explicitly supplied timing field still represents a source context.
    return any(field in timing for field in TIMING_FIELDS)


def _occurrences(parsed_snapshot: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    seen: set[tuple[Any, Any, Any, Any, Any]] = set()
    events = [event for event in parsed_snapshot.get("events", ()) or () if isinstance(event, Mapping)]
    event_occurrence_key_present = any(
        "recorded_at_time_occurrences" in event for event in events
    )
    candidates: list[Any] = []
    if not event_occurrence_key_present:
        top_level = parsed_snapshot.get("recorded_at_time_occurrences", ())
        candidates.extend(top_level if isinstance(top_level, list) else [])
    for event in events:
        if not isinstance(event, Mapping):
            continue
        values = event.get("recorded_at_time_occurrences", ())
        if isinstance(values, list):
            candidates.extend(values)
    for occurrence in candidates:
        if not isinstance(occurrence, Mapping):
            continue
        key = (
            occurrence.get("event_ordinal", occurrence.get("result_ordinal")),
            occurrence.get("call_scope"),
            occurrence.get("call_ordinal"),
            occurrence.get("timing_context"),
            occurrence.get("source_path"),
        )
        if key in seen:
            continue
        seen.add(key)
        yield occurrence


def iter_timing_observations(parsed_snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Flatten parser calls into one row-shaped record per timing context.

    The result is intentionally independent of snapshot/run identifiers; the
    persistence layer adds those lineage values and the observation timestamp.
    No deduplication is performed across calls or snapshots.
    """

    rows: list[dict[str, Any]] = []
    for event_index, event_value in enumerate(parsed_snapshot.get("events", ()) or ()):
        if not isinstance(event_value, Mapping):
            continue
        event = event_value
        event_ordinal = event.get("event_ordinal", event.get("result_ordinal", event_index))
        calls = _event_calls(event)
        for call_index, call_value in enumerate(calls):
            call = call_value
            call_scope = str(call.get("call_scope", ""))
            call_ordinal = call.get("call_ordinal", call_index)
            for context in TIMING_CONTEXTS:
                timing_value = call.get(context.casefold())
                if not isinstance(timing_value, Mapping) or not _timing_context_available(timing_value):
                    continue
                identity = source_event_identity(event, call, context)
                timing_paths = timing_value.get("source_paths")
                timing_paths = dict(timing_paths) if isinstance(timing_paths, Mapping) else {}
                call_paths = call.get("source_paths")
                call_paths = dict(call_paths) if isinstance(call_paths, Mapping) else {}
                event_paths = {
                    field: _event_source_path(event, field)
                    for field in (
                        "operating_day_ref",
                        "journey_ref",
                        "vehicle_journey_ref",
                        "dated_vehicle_journey_ref",
                        "line_ref",
                        "direction_ref",
                        "operator_ref",
                    )
                }
                source_paths = {
                    "result": event.get("result_source_path"),
                    "result_id": event.get("result_id_source_path"),
                    "event": event.get("service_source_path"),
                    "event_fields": event_paths,
                    "call": call_paths,
                    "timing": timing_paths,
                }
                rows.append(
                    {
                        "event_ordinal": event_ordinal,
                        "result_id": _text(event.get("result_id")),
                        "call_scope": call_scope,
                        "call_ordinal": call_ordinal,
                        "timing_context": context,
                        "operating_day_ref": _text(event.get("operating_day_ref")),
                        "journey_ref": _text(event.get("journey_ref")),
                        "vehicle_journey_ref": _text(event.get("vehicle_journey_ref")),
                        "dated_vehicle_journey_ref": _text(event.get("dated_vehicle_journey_ref")),
                        "line_ref": _text(event.get("line_ref")),
                        "direction_ref": _text(event.get("direction_ref")),
                        "operator_ref": _text(event.get("operator_ref")),
                        "stop_point_ref": _text(call.get("stop_point_ref")),
                        "stop_seq_number": call.get("stop_seq_number"),
                        "timetabled_time": _text(timing_value.get("timetabled_time")),
                        "estimated_time": _text(timing_value.get("estimated_time")),
                        "estimated_time_low": _text(timing_value.get("estimated_time_low")),
                        "estimated_time_high": _text(timing_value.get("estimated_time_high")),
                        "recorded_at_time": _text(timing_value.get("recorded_at_time")),
                        "source_path": timing_value.get("source_path"),
                        "timetabled_time_source_path": timing_paths.get("timetabled_time"),
                        "estimated_time_source_path": timing_paths.get("estimated_time"),
                        "estimated_time_low_source_path": timing_paths.get("estimated_time_low"),
                        "estimated_time_high_source_path": timing_paths.get("estimated_time_high"),
                        "recorded_at_time_source_path": timing_paths.get("recorded_at_time"),
                        "stop_point_ref_source_path": call_paths.get("stop_point_ref"),
                        "stop_seq_number_source_path": call_paths.get("stop_seq_number"),
                        "source_paths": source_paths,
                        **identity,
                    }
                )

    # A few existing deterministic tests intentionally inject an occurrence
    # record directly into the parser's evidence projection.  Preserve that
    # source evidence in the normalized timing projection without creating a
    # second row when the call-level timing context is already present.
    for occurrence in _occurrences(parsed_snapshot):
        if occurrence.get("recorded_at_time") is None:
            continue
        event_ordinal = occurrence.get("event_ordinal", occurrence.get("result_ordinal"))
        key = (
            event_ordinal,
            str(occurrence.get("call_scope", "")),
            occurrence.get("call_ordinal", 0),
            str(occurrence.get("timing_context", "")).upper(),
        )
        matching = next(
            (
                row
                for row in rows
                if (
                    row.get("event_ordinal"),
                    row.get("call_scope"),
                    row.get("call_ordinal"),
                    row.get("timing_context"),
                )
                == key
            ),
            None,
        )
        if matching is not None:
            if matching.get("recorded_at_time") is None:
                matching["recorded_at_time"] = _text(occurrence.get("recorded_at_time"))
                matching["recorded_at_time_source_path"] = occurrence.get("source_path") or matching.get("recorded_at_time_source_path")
            continue
        event = next(
            (
                value
                for index, value in enumerate(parsed_snapshot.get("events", ()) or ())
                if isinstance(value, Mapping)
                and value.get("event_ordinal", value.get("result_ordinal", index)) == event_ordinal
            ),
            {},
        )
        context = str(occurrence.get("timing_context", "")).upper()
        call = next(
            (
                value
                for value in _event_calls(event)
                if str(value.get("call_scope", "")) == str(occurrence.get("call_scope", ""))
                and value.get("call_ordinal", 0) == occurrence.get("call_ordinal", 0)
            ),
            {},
        )
        timing = call.get(context.casefold()) if isinstance(call, Mapping) else {}
        timing = timing if isinstance(timing, Mapping) else {}
        identity = source_event_identity(event, call, context)
        rows.append(
            {
                "event_ordinal": event_ordinal,
                "result_id": _text(occurrence.get("result_id", event.get("result_id"))),
                "call_scope": str(occurrence.get("call_scope", "")),
                "call_ordinal": occurrence.get("call_ordinal", 0),
                "timing_context": context,
                "operating_day_ref": _text(event.get("operating_day_ref")),
                "journey_ref": _text(event.get("journey_ref")),
                "vehicle_journey_ref": _text(event.get("vehicle_journey_ref")),
                "dated_vehicle_journey_ref": _text(event.get("dated_vehicle_journey_ref")),
                "line_ref": _text(event.get("line_ref")),
                "direction_ref": _text(event.get("direction_ref")),
                "operator_ref": _text(event.get("operator_ref")),
                "stop_point_ref": _text(call.get("stop_point_ref")),
                "stop_seq_number": call.get("stop_seq_number"),
                "timetabled_time": _text(timing.get("timetabled_time")),
                "estimated_time": _text(timing.get("estimated_time")),
                "estimated_time_low": _text(timing.get("estimated_time_low")),
                "estimated_time_high": _text(timing.get("estimated_time_high")),
                "recorded_at_time": _text(occurrence.get("recorded_at_time")),
                "source_path": timing.get("source_path") or occurrence.get("source_path"),
                "timetabled_time_source_path": None,
                "estimated_time_source_path": None,
                "estimated_time_low_source_path": None,
                "estimated_time_high_source_path": None,
                "recorded_at_time_source_path": occurrence.get("source_path"),
                "stop_point_ref_source_path": None,
                "stop_seq_number_source_path": None,
                "source_paths": {"occurrence": occurrence.get("source_path")},
                **identity,
            }
        )
    return rows


# Explicit aliases make the source-local vocabulary discoverable to callers
# without introducing a generic event or derived source-time abstraction.
build_source_event_identity = source_event_identity
flatten_timing_observations = iter_timing_observations
