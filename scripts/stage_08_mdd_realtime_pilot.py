#!/usr/bin/env python3
"""Stage 8 bounded MDD/TRIAS realtime pilot.

This is a Stage-8-specific diagnostic runtime.  It makes only the approved
MDD requests for the effective evidence-supported pilot target, uses a fixed
pilot-start request anchor, and keeps temporary raw responses and append-only
diagnostic JSONL outside the repository.  It does not use GTFS-RT, SQL,
PostgreSQL, a production cadence, or a mutable latest-event store.

The script intentionally has no cadence or station CLI switches.  The
diagnostic may pass an explicit, evidence-supported Stage 8 runtime
configuration into the same run path; otherwise the approved V3 defaults are
used.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import tempfile
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import Message
from http.client import HTTPResponse
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from xml.sax.saxutils import escape as xml_escape


SCRIPT_VERSION = "stage-08-mdd-realtime-pilot-1.2"
ENDPOINT = "https://mdd.gorheinland.com/delfi"
MDD_API_KEY_ENV = "MDD_API_KEY"
DEFAULT_STATION = "Köln Hbf"
DEFAULT_STOP_POINT_REF = "de:05315:11201"
REQUESTOR_REF = "CologneTransitIntelligence"
TRIAS_VERSION = "1.2"
STOP_EVENT_TYPE = "arrival"
DEFAULT_NUMBER_OF_RESULTS = 20
INCLUDE_PREVIOUS_CALLS = True
INCLUDE_ONWARD_CALLS = True
INCLUDE_REALTIME_DATA = True
REQUEST_TIMEOUT_SECONDS = 30
RETRIES = 0
SAMPLE_INTERVAL_SECONDS = 3
MAX_DURATION_SECONDS = 60 * 60
MAX_LOGICAL_SLOTS = 1200
DEFAULT_SAMPLING_POLICY = "MDD_STAGE_8_KOELN_HBF_3S_60MIN_V3"
HBF_N5_SAMPLING_POLICY = "MDD_STAGE_8_KOELN_HBF_3S_60MIN_V4_N5_DIAGNOSTIC"
HEUMARKT_N5_SAMPLING_POLICY = "MDD_STAGE_8_KOELN_HEUMARKT_3S_60MIN_V1_N5_DIAGNOSTIC"
PARSER_VERSION = "stage-08-mdd-parser-1.1"
RAW_TEMP_PREFIX = "cologne-transit-stage-08-"
MAX_RESPONSE_BYTES = 25 * 1024 * 1024
MAX_EXCERPT_CHARS = 1000
MAX_ERROR_CHARS = 500

SUCCESS_APPLICATION_STATUSES = {"ok", "success", "successful", "completed", "true"}


@dataclass(frozen=True)
class Stage8RuntimeConfig:
    """Effective configuration for one bounded Stage 8 pilot run."""

    station: str
    stop_point_ref: str
    number_of_results: int
    sampling_policy: str

    def __post_init__(self) -> None:
        if not self.station.strip():
            raise ValueError("Stage 8 station must not be empty")
        if not self.stop_point_ref.strip():
            raise ValueError("Stage 8 StopPointRef must not be empty")
        if self.number_of_results <= 0:
            raise ValueError("Stage 8 NumberOfResults must be positive")
        if not self.sampling_policy.strip():
            raise ValueError("Stage 8 sampling policy must not be empty")


DEFAULT_RUNTIME_CONFIG = Stage8RuntimeConfig(
    station=DEFAULT_STATION,
    stop_point_ref=DEFAULT_STOP_POINT_REF,
    number_of_results=DEFAULT_NUMBER_OF_RESULTS,
    sampling_policy=DEFAULT_SAMPLING_POLICY,
)
HBF_N5_RUNTIME_CONFIG = Stage8RuntimeConfig(
    station=DEFAULT_STATION,
    stop_point_ref=DEFAULT_STOP_POINT_REF,
    number_of_results=5,
    sampling_policy=HBF_N5_SAMPLING_POLICY,
)
HEUMARKT_N5_RUNTIME_CONFIG = Stage8RuntimeConfig(
    station="Köln Heumarkt",
    stop_point_ref="de:05315:11110",
    number_of_results=5,
    sampling_policy=HEUMARKT_N5_SAMPLING_POLICY,
)

# These names remain as the approved V3 module defaults for callers that use
# the request-builder helpers directly.  The actual pilot path uses the
# explicit Stage8RuntimeConfig passed through run_pilot().
STATION = DEFAULT_RUNTIME_CONFIG.station
STOP_POINT_REF = DEFAULT_RUNTIME_CONFIG.stop_point_ref
NUMBER_OF_RESULTS = DEFAULT_RUNTIME_CONFIG.number_of_results
SAMPLING_POLICY = DEFAULT_RUNTIME_CONFIG.sampling_policy


def runtime_config_record(config: Stage8RuntimeConfig) -> Dict[str, Any]:
    """Return the non-secret effective configuration for artifact lineage."""

    return {
        "station": config.station,
        "stop_point_ref": config.stop_point_ref,
        "number_of_results": config.number_of_results,
        "sampling_policy": config.sampling_policy,
    }


def runtime_config_from_record(
    value: Optional[Dict[str, Any]],
) -> Optional[Stage8RuntimeConfig]:
    """Rebuild a runtime configuration from a previously selected record."""

    if not value:
        return None
    return Stage8RuntimeConfig(
        station=str(value["station"]),
        stop_point_ref=str(value["stop_point_ref"]),
        number_of_results=int(value["number_of_results"]),
        sampling_policy=str(value["sampling_policy"]),
    )

SCOPE_NAMES = {
    "thiscall": "THIS",
    "previouscall": "PREVIOUS",
    "onwardcall": "ONWARD",
}

TIMING_CONTEXT_NAMES = {
    "servicearrival": "ARRIVAL",
    "servicedeparture": "DEPARTURE",
}

TIMING_FIELD_NAMES = {
    "timetabledtime": "TimetabledTime",
    "estimatedtime": "EstimatedTime",
    "recordedattime": "RecordedAtTime",
    "estimatedtimelow": "EstimatedTimeLow",
    "estimatedtimehigh": "EstimatedTimeHigh",
}

OPERATIONAL_CALL_FIELDS = {
    "unplannedstop": "UnplannedStop",
    "notservicedstop": "NotServicedStop",
    "noboardingatstop": "NoBoardingAtStop",
    "noalightingatstop": "NoAlightingAtStop",
}

OPERATIONAL_EVENT_FIELDS = {
    "cancelled": "Cancelled",
    "journeycancelled": "Cancelled",
    "deviation": "Deviation",
}

REFERENCE_FIELDS = {"situationfullref", "situationref"}
SITUATION_OBJECT_FIELDS = {
    "ptsituation",
    "ptsituationelement",
    "roadsituation",
    "situation",
}
ATTRIBUTE_FIELDS = {"attribute", "generalattribute"}
ERROR_FIELDS = {"errormessage", "errorcode"}

# These are the source fields that this pilot maps into its diagnostic grains.
# A relevant source field outside this set becomes explicit parser-gap
# evidence; it is never silently discarded.
RECOGNIZED_SOURCE_FIELDS = {
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
    "lineref",
    "directionref",
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
    "destination",
    "destinationref",
    "destinationstoppointref",
    "destinationstoppointname",
    "destinationtext",
    "previouscall",
    "thiscall",
    "onwardcall",
    "callatstop",
    "stoppointref",
    "stoppointname",
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
    "language",
    "lang",
    "value",
    "name",
    "ref",
}

RELEVANT_FIELD_TERMS = (
    "situation",
    "affect",
    "attribute",
    "generalattribute",
    "incident",
    "infocall",
    "error",
    "recorded",
    "estimated",
    "timetabled",
    "arrival",
    "departure",
    "bay",
    "cancel",
    "deviation",
    "unplanned",
    "notserviced",
    "boarding",
    "alighting",
    "operatingday",
    "journey",
    "direction",
    "operator",
    "stoppoint",
    "stopseq",
    "origin",
    "destination",
    "resultid",
    "priority",
    "valid",
    "creation",
    "publishedline",
    "railsubmode",
)


class PilotParserError(ValueError):
    """The received body cannot be parsed under the approved JSON contract."""


class CredentialContentError(ValueError):
    """The response body unexpectedly contained the API-key value."""


class ResponseTooLarge(ValueError):
    """The bounded response-size guard was reached."""


class PersistenceReadbackError(ValueError):
    """The append-only JSONL persistence could not be read back safely."""


@dataclass
class HTTPResult:
    request_timestamp: datetime
    request_timestamp_utc: str
    http_status: Optional[int]
    content_type: Optional[str]
    body: bytes
    elapsed_ms: float
    request_started_mono: float
    request_ended_mono: float
    failure: Optional[str]
    failure_detail: Optional[str]


@dataclass
class ParsedResponse:
    parsed: Any
    results: List[Tuple[str, Dict[str, Any]]]
    response_timestamp: Any
    application_status: Any
    response_error_entries: List[Dict[str, Any]]
    result_error_entries: List[Dict[str, Any]]
    situation_entries: List[Tuple[str, Dict[str, Any]]]
    reference_entries: List[Tuple[str, Any]]
    attribute_entries: List[Tuple[str, str, Any]]
    parser_gaps: List[Dict[str, Any]]


class NoRedirectHandler(HTTPRedirectHandler):
    """Prevent urllib from turning one planned request into hidden requests."""

    def redirect_request(
        self,
        req: Request,
        fp: HTTPResponse,
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> None:
        raise HTTPError(req.full_url, code, "redirect rejected by Stage 8 pilot", headers, fp)


class JsonlWriter:
    """Append-only mode-600 JSONL writer with immediate flush/fsync."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd = os.open(
            str(path),
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        os.fchmod(self.fd, 0o600)
        self.stream = os.fdopen(self.fd, "a", encoding="utf-8", buffering=1)

    def append(self, record: Dict[str, Any]) -> None:
        self.stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        self.stream.flush()
        os.fsync(self.fd)

    def close(self) -> None:
        try:
            self.stream.close()
        except OSError:
            pass


class PilotStore:
    """Protected append-only diagnostic persistence outside Git."""

    FILES = {
        "response_snapshots": "response_snapshots.jsonl",
        "stop_event_observations": "stop_event_observations.jsonl",
        "call_observations": "call_observations.jsonl",
        "situation_observations": "situation_observations.jsonl",
        "situation_associations": "stop_event_situation_associations.jsonl",
        "service_attributes": "service_attribute_observations.jsonl",
        "error_messages": "error_message_observations.jsonl",
        "recorded_at_time": "recorded_at_time_observations.jsonl",
        "parser_gaps": "parser_gap_evidence.jsonl",
        "collection_slots": "collection_slots.jsonl",
        "validation_results": "validation_results.jsonl",
    }

    def __init__(self, directory: Path, api_key: str) -> None:
        self.directory = directory
        self.api_key = api_key
        self.writers: Dict[str, JsonlWriter] = {}
        self.seen_response_ids: Set[str] = set()
        for logical_name, filename in self.FILES.items():
            path = directory / filename
            self.writers[logical_name] = JsonlWriter(path)

    def append(self, logical_name: str, record: Dict[str, Any]) -> None:
        safe_record = sanitize_for_artifact(record, self.api_key)
        self.writers[logical_name].append(safe_record)

    def records_for_response(self, logical_name: str, response_id: str) -> List[Dict[str, Any]]:
        """Read the append-only JSONL artifact, not the in-memory bundle."""

        path = self.writers[logical_name].path
        records: List[Dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except (TypeError, ValueError) as error:
                    raise PersistenceReadbackError(
                        f"invalid JSONL in {logical_name} at line {line_number}"
                    ) from error
                if not isinstance(record, dict):
                    raise PersistenceReadbackError(
                        f"non-object JSONL in {logical_name} at line {line_number}"
                    )
                if record.get("response_id") == response_id:
                    records.append(record)
        return records

    def close(self) -> None:
        for writer in self.writers.values():
            writer.close()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def parser_lineage(
    *,
    pilot_run_id: Optional[str],
    response_id: Optional[str],
    observed_at: Optional[str],
) -> Dict[str, Any]:
    """Common direct lineage fields for parser-generated diagnostic records."""

    return {
        "pilot_run_id": pilot_run_id,
        "response_id": response_id,
        "observed_at": observed_at,
        "parser_version": PARSER_VERSION,
    }


def trias_utc(value: datetime) -> str:
    """Return the approved no-fraction UTC TRIAS timestamp representation."""

    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def path_for_key(parent_path: str, key: Any) -> str:
    key_text = str(key)
    if key_text and all(character.isalnum() or character == "_" for character in key_text):
        return f"{parent_path}.{key_text}"
    return f"{parent_path}[{json.dumps(key_text, ensure_ascii=False)}]"


def iter_dict_entries(
    value: Any,
    path: str = "$",
) -> Iterator[Tuple[str, str, Any, str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = path_for_key(path, key)
            yield child_path, str(key), child, path, value
            yield from iter_dict_entries(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_path = f"{path}[{index}]"
            yield from iter_dict_entries(child, child_path)


def path_components(path: str) -> List[str]:
    return [normalized_key(item) for item in re.findall(r"[A-Za-z0-9_]+", path)]


def source_value(value: Any) -> Any:
    """Extract a source scalar while preserving the source lexical value."""

    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        if isinstance(value, str) and not value.strip():
            return None
        return value
    if isinstance(value, dict):
        for key_name in ("value", "text", "name", "id", "ref", "sourcetype"):
            for key, child in value.items():
                if normalized_key(key) == key_name:
                    extracted = source_value(child)
                    if extracted is not None:
                        return extracted
        return None
    if isinstance(value, list):
        extracted_values = [source_value(item) for item in value]
        extracted_values = [item for item in extracted_values if item is not None]
        if not extracted_values:
            return None
        return extracted_values[0] if len(extracted_values) == 1 else extracted_values
    return None


def source_value_at_path(value: Any, path: str) -> Tuple[Optional[str], Any]:
    """Return the first source scalar and its exact leaf path."""

    if value is None:
        return path, None
    if isinstance(value, (str, int, float, bool)):
        return path, source_value(value)
    if isinstance(value, dict):
        for preferred_name in ("value", "text", "name", "id", "ref", "sourcetype"):
            for key, child in value.items():
                if normalized_key(key) != preferred_name:
                    continue
                child_path = path_for_key(path, key)
                leaf_path, extracted = source_value_at_path(child, child_path)
                if extracted is not None:
                    return leaf_path, extracted
        return path, source_value(value)
    if isinstance(value, list):
        for index, item in enumerate(value):
            leaf_path, extracted = source_value_at_path(item, f"{path}[{index}]")
            if extracted is not None:
                return leaf_path, extracted
        return path, source_value(value)
    return path, source_value(value)


def scalar_values(value: Any) -> Iterator[Any]:
    if isinstance(value, dict):
        for child in value.values():
            yield from scalar_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from scalar_values(child)
    elif value is not None:
        yield value


def is_populated(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (dict, list)):
        return bool(value)
    return True


def direct_named_entries(value: Any, names: Set[str]) -> List[Tuple[str, Any]]:
    if not isinstance(value, dict):
        return []
    entries: List[Tuple[str, Any]] = []
    for key, child in value.items():
        if normalized_key(key) in names:
            entries.append((str(key), child))
    return entries


def named_source_values(value: Any, names: Set[str]) -> List[Tuple[str, Any]]:
    output: List[Tuple[str, Any]] = []
    for path, key, child, _parent_path, _parent in iter_dict_entries(value):
        if normalized_key(key) not in names:
            continue
        extracted = source_value(child)
        if extracted is not None:
            output.append((path, extracted))
    return output


def first_named_source(value: Any, names: Set[str]) -> Tuple[Optional[str], Any]:
    values = named_source_values(value, names)
    return values[0] if values else (None, None)


def named_occurrences(
    value: Any,
    names: Set[str],
    root_path: str = "$",
) -> List[Tuple[str, Any]]:
    """Return every named occurrence, retaining list item paths."""

    output: List[Tuple[str, Any]] = []
    for path, key, child, _parent_path, _parent in iter_dict_entries(value, root_path):
        if normalized_key(key) not in names:
            continue
        if isinstance(child, list):
            for index, item in enumerate(child):
                output.append((f"{path}[{index}]", item))
        else:
            output.append((path, child))
    return output


def named_object_entries(
    value: Any,
    names: Set[str],
    root_path: str = "$",
) -> List[Tuple[str, Dict[str, Any]]]:
    output: List[Tuple[str, Dict[str, Any]]] = []
    for path, key, child, _parent_path, _parent in iter_dict_entries(value, root_path):
        if normalized_key(key) not in names:
            continue
        if isinstance(child, dict):
            output.append((path, child))
        elif isinstance(child, list):
            for index, item in enumerate(child):
                if isinstance(item, dict):
                    output.append((f"{path}[{index}]", item))
    return output


def direct_named_value(value: Any, names: Set[str]) -> Any:
    entries = direct_named_entries(value, names)
    return entries[0][1] if entries else None


def direct_named_raw(
    value: Any,
    names: Set[str],
    root_path: str = "$",
) -> Tuple[Optional[str], Any]:
    if not isinstance(value, dict):
        return None, None
    for key, child in value.items():
        if normalized_key(key) in names:
            return path_for_key(root_path, key), child
    return None, None


def as_object_occurrences(value: Any, path: str) -> List[Tuple[str, Any]]:
    if isinstance(value, list):
        return [(f"{path}[{index}]", item) for index, item in enumerate(value)]
    return [(path, value)]


def scope_calls(
    stop_event: Any,
    scope_name: str,
    root_path: str = "$.stopEvent",
) -> List[Tuple[str, Any]]:
    call_path, child = direct_named_raw(stop_event, {scope_name.casefold()}, root_path)
    if call_path is None:
        return []
    return as_object_occurrences(child, call_path)


def direct_object(value: Any, names: Set[str]) -> Optional[Dict[str, Any]]:
    child = direct_named_value(value, names)
    return child if isinstance(child, dict) else None


def call_at_stop(call: Any) -> Optional[Dict[str, Any]]:
    child = direct_named_value(call, {"callatstop"})
    if isinstance(child, dict):
        return child
    if isinstance(child, list):
        return next((item for item in child if isinstance(item, dict)), None)
    return None


def source_identity_key(
    participant_ref: Any,
    situation_number: Any,
    version: Any,
    identifier: Any,
    fallback: Any,
) -> Optional[str]:
    if participant_ref is not None and situation_number is not None:
        base = f"PARTICIPANT_AND_SITUATION_NUMBER|{participant_ref}|{situation_number}"
        return base + (f"|VERSION|{version}" if version is not None else "")
    if identifier is not None:
        return f"IDENTIFIER|{identifier}"
    return None


def identity_without_version(identity: Optional[str]) -> Optional[str]:
    if identity is None:
        return None
    return identity.split("|VERSION|", 1)[0]


def situation_identity(value: Dict[str, Any], path: str) -> Dict[str, Any]:
    participant = source_value(direct_named_value(value, {"participantref"}))
    situation_number = source_value(direct_named_value(value, {"situationnumber"}))
    version = source_value(direct_named_value(value, {"version"}))
    identifier = source_value(direct_named_value(value, {"identifier", "id"}))
    identity = source_identity_key(
        participant,
        situation_number,
        version,
        identifier,
        value,
    )
    return {
        "identity_key": identity,
        "identity_key_without_version": identity_without_version(identity),
        "participant_ref": participant,
        "situation_number": situation_number,
        "version": version,
        "identifier": identifier,
        "source_path": path,
    }


def relation_scope(path: str) -> str:
    components = path_components(path)
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


def decode_response(body: bytes, content_type: Optional[str]) -> str:
    charset = "utf-8"
    if content_type:
        for part in content_type.split(";")[1:]:
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            if name.strip().casefold() == "charset":
                charset = value.strip().strip('"') or "utf-8"
                break
    try:
        return body.decode(charset, errors="strict")
    except (LookupError, UnicodeDecodeError):
        return body.decode("utf-8", errors="replace")


def clean_error(value: Any, api_key: str) -> str:
    text = " ".join(str(value).split())
    if api_key:
        text = text.replace(api_key, "[REDACTED]")
    return (text or type(value).__name__)[:MAX_ERROR_CHARS]


def sanitize_for_artifact(value: Any, api_key: str) -> Any:
    """Sanitize values before any diagnostic JSONL or stdout write."""

    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value.replace(api_key, "[REDACTED]") if api_key else value
    if isinstance(value, list):
        return [sanitize_for_artifact(item, api_key) for item in value]
    if isinstance(value, dict):
        return {
            sanitize_for_artifact(key, api_key): sanitize_for_artifact(child, api_key)
            for key, child in value.items()
        }
    return sanitize_for_artifact(str(value), api_key)


def source_excerpt(value: Any, api_key: str) -> Any:
    values = []
    for scalar in scalar_values(value):
        text = clean_error(scalar, api_key)
        if text and text not in values:
            values.append(text)
        if len(values) >= 5:
            break
    if not values:
        return None
    return values[0] if len(values) == 1 else values


def create_temp_directory() -> Path:
    preferred = Path("/private/tmp")
    parent = preferred if preferred.is_dir() and os.access(preferred, os.W_OK) else Path(tempfile.gettempdir())
    directory = Path(tempfile.mkdtemp(prefix=RAW_TEMP_PREFIX, dir=str(parent)))
    os.chmod(directory, 0o700)
    return directory


def write_raw_artifact(directory: Path, filename: str, body: bytes, api_key: str) -> Path:
    if api_key.encode("utf-8") in body:
        raise CredentialContentError("API-key value found in response content")
    path = directory / filename
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            path.unlink()
        except OSError:
            pass
        raise
    return path


def read_bounded(stream: Any, maximum_bytes: int) -> bytes:
    chunks: List[bytes] = []
    total = 0
    while True:
        chunk = stream.read(min(1024 * 1024, maximum_bytes - total + 1))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > maximum_bytes:
            raise ResponseTooLarge("response exceeded bounded byte limit")
    return b"".join(chunks)


def build_request_body(
    request_timestamp_utc: str,
    pilot_anchor_dep_arr_time: str,
    *,
    stop_point_ref: str = STOP_POINT_REF,
    number_of_results: int = NUMBER_OF_RESULTS,
) -> bytes:
    """Build the approved TRIAS 1.2 request with bounded diagnostic overrides."""

    request_timestamp_utc = xml_escape(request_timestamp_utc)
    pilot_anchor_dep_arr_time = xml_escape(pilot_anchor_dep_arr_time)
    return f'''<?xml version="1.0" encoding="UTF-8"?>
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
            <StopPointRef>{xml_escape(stop_point_ref)}</StopPointRef>
          </LocationRef>
          <DepArrTime>{pilot_anchor_dep_arr_time}</DepArrTime>
        </Location>
        <Params>
          <NumberOfResults>{number_of_results}</NumberOfResults>
          <StopEventType>{STOP_EVENT_TYPE}</StopEventType>
          <IncludePreviousCalls>true</IncludePreviousCalls>
          <IncludeOnwardCalls>true</IncludeOnwardCalls>
          <IncludeRealtimeData>true</IncludeRealtimeData>
        </Params>
      </StopEventRequest>
    </RequestPayload>
  </ServiceRequest>
</Trias>
'''.encode("utf-8")


def perform_http_request(
    api_key: str,
    pilot_anchor_dep_arr_time: Optional[str] = None,
    *,
    runtime_config: Optional[Stage8RuntimeConfig] = None,
    stop_point_ref: Optional[str] = None,
    number_of_results: Optional[int] = None,
) -> HTTPResult:
    effective_config = runtime_config or DEFAULT_RUNTIME_CONFIG
    effective_stop_point_ref = (
        effective_config.stop_point_ref
        if stop_point_ref is None
        else stop_point_ref
    )
    effective_number_of_results = (
        effective_config.number_of_results
        if number_of_results is None
        else number_of_results
    )
    request_timestamp = utc_now()
    request_timestamp_utc = trias_utc(request_timestamp)
    dep_arr_time = (
        request_timestamp_utc
        if pilot_anchor_dep_arr_time is None
        else pilot_anchor_dep_arr_time
    )
    body = build_request_body(
        request_timestamp_utc,
        dep_arr_time,
        stop_point_ref=effective_stop_point_ref,
        number_of_results=effective_number_of_results,
    )
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
    status: Optional[int] = None
    content_type: Optional[str] = None
    response_body = b""
    failure: Optional[str] = None
    failure_detail: Optional[str] = None
    opener = build_opener(NoRedirectHandler())
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status = int(response.status)
            content_type = response.headers.get("Content-Type")
            response_body = read_bounded(response, MAX_RESPONSE_BYTES)
    except HTTPError as error:
        status = int(error.code)
        content_type = error.headers.get("Content-Type") if error.headers else None
        try:
            response_body = read_bounded(error, MAX_RESPONSE_BYTES)
        except Exception as read_error:
            failure = "HTTP_ERROR"
            failure_detail = clean_error(read_error, api_key)
        if failure is None:
            failure = "HTTP_ERROR"
            failure_detail = f"HTTP_{status}"
    except ResponseTooLarge as error:
        failure = "HTTP_ERROR"
        failure_detail = clean_error(error, api_key)
    except (socket.timeout, TimeoutError) as error:
        failure = "TIMEOUT"
        failure_detail = clean_error(error, api_key)
    except URLError as error:
        reason = getattr(error, "reason", error)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            failure = "TIMEOUT"
        else:
            failure = "NETWORK_OR_DNS_FAILURE"
        failure_detail = clean_error(reason, api_key)
    except OSError as error:
        failure = "NETWORK_OR_DNS_FAILURE"
        failure_detail = clean_error(error, api_key)
    except Exception as error:  # pragma: no cover - defensive host-runtime boundary
        failure = "NETWORK_OR_DNS_FAILURE"
        failure_detail = clean_error(error, api_key)
    ended = time.perf_counter()
    return HTTPResult(
        request_timestamp=request_timestamp,
        request_timestamp_utc=request_timestamp_utc,
        http_status=status,
        content_type=content_type,
        body=response_body,
        elapsed_ms=(ended - started) * 1000,
        request_started_mono=started,
        request_ended_mono=ended,
        failure=failure,
        failure_detail=failure_detail,
    )


def result_entries(parsed: Any) -> List[Tuple[str, Dict[str, Any]]]:
    entries = named_object_entries(parsed, {"stopeventresult"})
    raw_entries = named_occurrences(parsed, {"stopeventresult"})
    if len(entries) != len(raw_entries) or any(not isinstance(item, dict) for _path, item in raw_entries):
        raise PilotParserError("StopEventResult occurrence was not an object")
    return entries


def error_entries(
    parsed: Any,
    results: Optional[Sequence[Tuple[str, Dict[str, Any]]]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Extract response/result errors with source and result lineage.

    ``ErrorMessage`` and ``ErrorCode`` are detected independently.  The
    result context is resolved from the source path so a result-level
    ``ErrorMessage`` can be persisted without being confused with a
    response-level message or with an ``IncidentCall``.
    """

    result_contexts: List[Dict[str, Any]] = []
    for ordinal, (result_path, result) in enumerate(results or result_entries(parsed)):
        result_identity, result_identity_path = result_id(result, result_path, ordinal)
        result_contexts.append(
            {
                "result_source_path": result_path,
                "source_result_id": result_identity,
                "source_result_id_path": result_identity_path,
                "result_ordinal": ordinal,
            }
        )

    def context_for_path(path: str) -> Optional[Dict[str, Any]]:
        matches = [
            context
            for context in result_contexts
            if path == context["result_source_path"]
            or path.startswith(context["result_source_path"] + ".")
            or path.startswith(context["result_source_path"] + "[")
        ]
        return max(matches, key=lambda item: len(item["result_source_path"])) if matches else None

    response_errors: List[Dict[str, Any]] = []
    result_errors: List[Dict[str, Any]] = []
    for path, key, child, parent_path, parent in iter_dict_entries(parsed):
        if normalized_key(key) not in ERROR_FIELDS:
            continue
        child_occurrences = (
            [
                (f"{path}[{index}]", item)
                for index, item in enumerate(child)
            ]
            if isinstance(child, list)
            else [(path, child)]
        )
        for occurrence_path, occurrence in child_occurrences:
            result_context = context_for_path(occurrence_path)
            error_code_path: Optional[str] = None
            error_code: Any = None
            if isinstance(parent, dict):
                for parent_key, parent_child in parent.items():
                    if normalized_key(parent_key) != "errorcode":
                        continue
                    candidate_path = path_for_key(parent_path, parent_key)
                    candidate_value_path, candidate_value = source_value_at_path(
                        parent_child,
                        candidate_path,
                    )
                    if candidate_value is not None:
                        error_code_path = candidate_value_path
                        error_code = candidate_value
                        break
            if error_code is None and isinstance(occurrence, dict):
                nested_code_path, nested_code_raw = direct_named_raw(
                    occurrence,
                    {"errorcode"},
                    occurrence_path,
                )
                if nested_code_path is not None:
                    error_code_path, error_code = source_value_at_path(
                        nested_code_raw,
                        nested_code_path,
                    )
            item = {
                "source_path": occurrence_path,
                "field_name": key,
                "populated": is_populated(occurrence),
                "value_excerpt": source_excerpt(occurrence, ""),
                "source_value": source_value(occurrence),
                "source_representation": occurrence,
                "error_scope": "RESULT_LEVEL" if result_context else "RESPONSE_LEVEL",
                "source_result_id": result_context.get("source_result_id") if result_context else None,
                "source_result_id_path": (
                    result_context.get("source_result_id_path") if result_context else None
                ),
                "result_source_path": (
                    result_context.get("result_source_path") if result_context else None
                ),
                "result_ordinal": result_context.get("result_ordinal") if result_context else None,
                "error_code": error_code,
                "error_code_source_path": error_code_path,
            }
            if result_context:
                result_errors.append(item)
            else:
                response_errors.append(item)
    return response_errors, result_errors


def relevant_gap(key: str) -> bool:
    normalized = normalized_key(key)
    if "extension" in normalized:
        return True
    return any(term in normalized for term in RELEVANT_FIELD_TERMS)


def parser_gap_entries(parsed: Any) -> List[Dict[str, Any]]:
    gaps: List[Dict[str, Any]] = []
    for path, key, child, _parent_path, _parent in iter_dict_entries(parsed):
        normalized = normalized_key(key)
        if normalized in RECOGNIZED_SOURCE_FIELDS or not relevant_gap(key):
            continue
        gaps.append(
            {
                "source_path": path,
                "field_name": key,
                "value_type": type(child).__name__,
                "value_excerpt": source_excerpt(child, ""),
                "mapping_status": "UNMAPPED_RELEVANT_SOURCE_FIELD",
                "semantic_hint": "RELEVANT_FIELD_NAME_OUTSIDE_APPROVED_MAPPING",
            }
        )
    return gaps


def normalized_parser_gap_path(path: Any) -> str:
    """Normalize array indexes while retaining the source structure."""

    return re.sub(r"\[\d+\]", "[]", str(path))


def parse_response(body: bytes, content_type: Optional[str]) -> ParsedResponse:
    try:
        parsed = json.loads(decode_response(body, content_type))
    except (TypeError, ValueError) as error:
        raise PilotParserError("JSON_PARSE_ERROR") from error
    if not isinstance(parsed, dict):
        raise PilotParserError("JSON_ROOT_NOT_OBJECT")

    results = result_entries(parsed)
    response_errors, result_errors = error_entries(parsed, results)
    service_delivery = direct_object(parsed, {"servicedelivery"})
    response_timestamp = source_value(
        direct_named_value(service_delivery or parsed, {"responsetimestamp"})
    )
    application_status = source_value(
        direct_named_value(service_delivery or parsed, {"status"})
    )
    situations = named_object_entries(parsed, SITUATION_OBJECT_FIELDS)
    references = named_occurrences(parsed, REFERENCE_FIELDS)
    attributes: List[Tuple[str, str, Any]] = []
    for path, key, child, _parent_path, _parent in iter_dict_entries(parsed):
        if normalized_key(key) not in ATTRIBUTE_FIELDS:
            continue
        for item_path, item in as_object_occurrences(child, path):
            attributes.append((item_path, key, item))
    return ParsedResponse(
        parsed=parsed,
        results=results,
        response_timestamp=response_timestamp,
        application_status=application_status,
        response_error_entries=response_errors,
        result_error_entries=result_errors,
        situation_entries=situations,
        reference_entries=references,
        attribute_entries=attributes,
        parser_gaps=parser_gap_entries(parsed),
    )


def application_error(parsed: ParsedResponse) -> bool:
    status = source_value(parsed.application_status)
    if status is not None and str(status).strip().casefold() not in SUCCESS_APPLICATION_STATUSES:
        return True
    return any(item["populated"] for item in parsed.response_error_entries + parsed.result_error_entries)


def service_for_stop_event(stop_event: Any) -> Dict[str, Any]:
    child = direct_named_value(stop_event, {"service"})
    if isinstance(child, dict):
        return child
    if isinstance(child, list):
        return next((item for item in child if isinstance(item, dict)), {})
    return {}


def context_identity(service: Any, stop_event: Any) -> Dict[str, Any]:
    identity: Dict[str, Any] = {}
    for field_name, names in (
        ("operating_day_ref", {"operatingdayref"}),
        ("journey_ref", {"journeyref"}),
        ("line_ref", {"lineref"}),
        ("direction_ref", {"directionref"}),
        ("operator_ref", {"operatorref"}),
    ):
        _path, value = first_named_source(service, names)
        if value is None:
            _path, value = first_named_source(stop_event, names)
        identity[field_name] = value
    for field_name, names in (
        ("pt_mode", {"ptmode", "mode", "modes"}),
        ("rail_submode", {"railsubmode", "submode"}),
        ("published_line_name", {"publishedlinename"}),
    ):
        _path, value = first_named_source(service, names)
        identity[field_name] = value
    return identity


def origin_destination(service: Any, root_path: str = "$") -> Dict[str, Any]:
    """Retain service-level origin/destination identity and text independently."""

    output: Dict[str, Any] = {}
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
        ref_path, raw_ref = direct_named_raw(service, specific_ref_names, root_path)
        text_path, raw_text = direct_named_raw(service, specific_text_names, root_path)
        if ref_path is None:
            ref_path, raw_ref = first_named_raw(service, specific_ref_names, root_path)
        if text_path is None:
            text_path, raw_text = first_named_raw(service, specific_text_names, root_path)
        container_path: Optional[str] = None
        container: Any = None
        if ref_path is None or text_path is None:
            container_path, container = direct_named_raw(service, container_names, root_path)
            if container_path is None:
                container_path, container = first_named_raw(service, container_names, root_path)
            if isinstance(container, dict):
                if ref_path is None:
                    ref_path, raw_ref = first_named_raw(
                        container,
                        {"stoppointref", "originref", "destinationref", "ref", "id"},
                        container_path or root_path,
                    )
                if text_path is None:
                    text_path, raw_text = first_named_raw(
                        container,
                        {"origintext", "destinationtext", "stoppointname", "name", "text", "value"},
                        container_path or root_path,
                    )

        ref_value_path, ref = source_value_at_path(raw_ref, ref_path or root_path)
        text_value_path, text = source_value_at_path(raw_text, text_path or root_path)
        if ref is not None:
            ref_path = ref_value_path
        if text is not None:
            text_path = text_value_path
        output[f"{prefix}_stop_point_ref"] = ref
        output[f"{prefix}_stop_point_ref_source_path"] = ref_path
        output[f"{prefix}_text"] = text
        output[f"{prefix}_text_source_path"] = text_path
        output[f"{prefix}_source_path"] = container_path

        # Preserve the earlier diagnostic aliases while making the source
        # identity meaning explicit in the canonical fields above.
        output[f"{prefix}_ref"] = ref
        output[f"{prefix}_ref_source_path"] = ref_path

    return output


def timing_context_records(call_stop: Any, context_name: str) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    for path, key, child, _parent_path, _parent in iter_dict_entries(call_stop):
        if normalized_key(key) != context_name:
            continue
        for context_path, context_value in as_object_occurrences(child, path):
            fields: List[Dict[str, Any]] = []
            for field_path, field_key, field_child, _pp, _parent_obj in iter_dict_entries(
                context_value, context_path
            ):
                canonical = TIMING_FIELD_NAMES.get(normalized_key(field_key))
                if canonical is None:
                    continue
                extracted = source_value(field_child)
                if extracted is None:
                    continue
                fields.append(
                    {
                        "field": canonical,
                        "source_path": field_path,
                        "value": extracted,
                    }
                )
            by_field: Dict[str, List[Any]] = defaultdict(list)
            field_paths: Dict[str, List[str]] = defaultdict(list)
            for field in fields:
                by_field[field["field"]].append(field["value"])
                field_paths[field["field"]].append(field["source_path"])
            output.append(
                {
                    "context": TIMING_CONTEXT_NAMES[context_name],
                    "source_path": context_path,
                    "fields": fields,
                    "field_values": dict(by_field),
                    "field_paths": dict(field_paths),
                }
            )
    return output


def first_named_value(value: Any, names: Set[str]) -> Any:
    _path, result = first_named_source(value, names)
    return result


def first_named_raw(
    value: Any,
    names: Set[str],
    root_path: str = "$",
) -> Tuple[Optional[str], Any]:
    for path, key, child, _parent_path, _parent in iter_dict_entries(value, root_path):
        if normalized_key(key) in names:
            return path, child
    return None, None


def call_record(
    *,
    response_id: str,
    stop_event_id: str,
    result_path: str,
    call_path: str,
    call_scope: str,
    call_ordinal: int,
    call: Any,
    stop_event: Any,
    service: Any,
    observed_at: str,
    event_identity: str,
    pilot_run_id: Optional[str] = None,
    source_result_id: Any = None,
) -> Dict[str, Any]:
    call_stop = call_at_stop(call)
    call_stop = call_stop or {}
    stop_ref_path, stop_ref = first_named_source(call_stop, {"stoppointref"})
    stop_name_path, stop_name = first_named_source(call_stop, {"stoppointname"})
    seq_path, stop_seq = first_named_source(call_stop, {"stopseqnumber"})
    flags: Dict[str, Any] = {}
    flag_paths: Dict[str, Any] = {}
    for source_name, canonical_name in OPERATIONAL_CALL_FIELDS.items():
        path, value = first_named_source(call_stop, {source_name})
        flags[canonical_name] = value
        flag_paths[canonical_name] = path
    timing: Dict[str, List[Dict[str, Any]]] = {
        "arrival": timing_context_records(call_stop, "servicearrival"),
        "departure": timing_context_records(call_stop, "servicedeparture"),
    }
    planned_path, planned_bay = first_named_source(call_stop, {"plannedbay"})
    estimated_path, estimated_bay = first_named_source(call_stop, {"estimatedbay"})
    return {
        "record_type": "call_observation",
        "call_observation_id": f"{stop_event_id}|call|{call_scope}|{call_ordinal}",
        **parser_lineage(
            pilot_run_id=pilot_run_id,
            response_id=response_id,
            observed_at=observed_at,
        ),
        "response_id": response_id,
        "stop_event_observation_id": stop_event_id,
        "event_source_identity": event_identity,
        "source_result_id": source_result_id,
        "call_scope": call_scope,
        "call_ordinal": call_ordinal,
        "source_path": call_path,
        "result_source_path": result_path,
        "stop_point_ref": stop_ref,
        "stop_point_ref_source_path": stop_ref_path,
        "stop_point_name": stop_name,
        "stop_point_name_source_path": stop_name_path,
        "stop_seq_number": stop_seq,
        "stop_seq_number_source_path": seq_path,
        "arrival_timing": timing["arrival"],
        "departure_timing": timing["departure"],
        "planned_bay": planned_bay,
        "planned_bay_source_path": planned_path,
        "estimated_bay": estimated_bay,
        "estimated_bay_source_path": estimated_path,
        "operational_flags": flags,
        "operational_flag_source_paths": flag_paths,
    }


def stable_event_identity(identity: Dict[str, Any], result_identity: Any) -> Optional[str]:
    required = (
        identity.get("journey_ref"),
        identity.get("stop_point_ref"),
        identity.get("timetabled_time"),
    )
    if not all(value is not None and str(value) != "" for value in required):
        return None
    payload = {
        "namespace": "MDD",
        "operating_day_ref": identity.get("operating_day_ref"),
        "journey_ref": identity.get("journey_ref"),
        "stop_point_ref": identity.get("stop_point_ref"),
        "event_type": "ARRIVAL",
        "timetabled_time": identity.get("timetabled_time"),
        "stop_seq_number": identity.get("stop_seq_number"),
        "line_ref": identity.get("line_ref"),
        "direction_ref": identity.get("direction_ref"),
        "operator_ref": identity.get("operator_ref"),
    }
    return "MDD_EVENT|" + json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def source_event_identity(identity: Dict[str, Any], result_identity: Any) -> str:
    stable = stable_event_identity(identity, result_identity)
    if stable is not None:
        return stable
    fallback = {
        "namespace": "MDD",
        "result_identity": result_identity,
        "journey_ref": identity.get("journey_ref"),
        "stop_point_ref": identity.get("stop_point_ref"),
        "event_type": "ARRIVAL",
        "timetabled_time": identity.get("timetabled_time"),
    }
    return "MDD_EVENT_FALLBACK|" + json.dumps(fallback, ensure_ascii=False, sort_keys=True, default=str)


def result_id(result: Dict[str, Any], result_path: str, ordinal: int) -> Tuple[Any, str]:
    path, value = first_named_source(result, {"resultid"})
    if value is None:
        return None, f"RESULT_ORDINAL_{ordinal}"
    return value, path or result_path


def stop_event_record(
    *,
    response_id: str,
    run_id: str,
    observed_at: str,
    result_path: str,
    result: Dict[str, Any],
    result_ordinal: int,
) -> Dict[str, Any]:
    result_id_value, result_id_path = result_id(result, result_path, result_ordinal)
    stop_event_path, stop_event_raw = direct_named_raw(result, {"stopevent"}, result_path)
    stop_event = stop_event_raw if isinstance(stop_event_raw, dict) else {}
    service_path, service_raw = direct_named_raw(
        stop_event,
        {"service"},
        stop_event_path or f"{result_path}.stopEvent",
    )
    service = service_raw if isinstance(service_raw, dict) else service_for_stop_event(stop_event)
    identity = context_identity(service, stop_event)
    this_calls = scope_calls(stop_event, "thiscall", stop_event_path or f"{result_path}.stopEvent")
    this_call_stop = call_at_stop(this_calls[0][1]) if this_calls else None
    this_call_stop = this_call_stop or {}
    stop_ref_path, stop_ref = first_named_source(this_call_stop, {"stoppointref"})
    seq_path, stop_seq = first_named_source(this_call_stop, {"stopseqnumber"})
    arrival_timing = timing_context_records(this_call_stop, "servicearrival")
    timetable = None
    if arrival_timing:
        timetable_values = arrival_timing[0].get("field_values", {}).get("TimetabledTime", [])
        timetable = timetable_values[0] if timetable_values else None
    identity.update(
        {
            "stop_point_ref": stop_ref,
            "stop_seq_number": stop_seq,
            "arrival_context": "arrival",
            "timetabled_time": timetable,
        }
    )
    event_identity = source_event_identity(identity, result_id_value or result_path)
    stop_event_id = f"{response_id}|stop_event|{result_ordinal}"
    event_flags: Dict[str, Any] = {}
    event_flag_paths: Dict[str, Any] = {}
    for source_name, canonical_name in OPERATIONAL_EVENT_FIELDS.items():
        path, value = first_named_source(stop_event, {source_name})
        if value is None:
            path, value = first_named_source(service, {source_name})
        if canonical_name in event_flags and event_flags[canonical_name] is not None:
            continue
        event_flags[canonical_name] = value
        event_flag_paths[canonical_name] = path
    calls: List[Dict[str, Any]] = []
    for source_scope, scope_name in (
        ("THIS", "thiscall"),
        ("PREVIOUS", "previouscall"),
        ("ONWARD", "onwardcall"),
    ):
        for ordinal, (call_path, call) in enumerate(
            scope_calls(stop_event, scope_name, stop_event_path or f"{result_path}.stopEvent")
        ):
            calls.append(
                call_record(
                    response_id=response_id,
                    stop_event_id=stop_event_id,
                    result_path=result_path,
                    call_path=call_path,
                    call_scope=source_scope,
                    call_ordinal=ordinal,
                    call=call,
                    stop_event=stop_event,
                    service=service,
                    observed_at=observed_at,
                    event_identity=event_identity,
                    pilot_run_id=run_id,
                    source_result_id=result_id_value,
                )
            )
    origin_destination_values = origin_destination(
        service,
        service_path or stop_event_path or f"{result_path}.stopEvent.service",
    )
    return {
        "record_type": "stop_event_observation",
        "stop_event_observation_id": stop_event_id,
        **parser_lineage(
            pilot_run_id=run_id,
            response_id=response_id,
            observed_at=observed_at,
        ),
        "response_id": response_id,
        "source_result_id": result_id_value,
        "source_result_id_path": result_id_path,
        "result_ordinal": result_ordinal,
        "source_path": result_path,
        "result_source_path": result_path,
        "event_source_identity": event_identity,
        "stable_event_identity": stable_event_identity(identity, result_id_value),
        "operating_day_ref": identity.get("operating_day_ref"),
        "journey_ref": identity.get("journey_ref"),
        "line_ref": identity.get("line_ref"),
        "direction_ref": identity.get("direction_ref"),
        "operator_ref": identity.get("operator_ref"),
        "pt_mode": identity.get("pt_mode"),
        "rail_submode": identity.get("rail_submode"),
        "published_line_name": identity.get("published_line_name"),
        "origin_destination": origin_destination_values,
        "origin_stop_point_ref": origin_destination_values.get("origin_stop_point_ref"),
        "origin_stop_point_ref_source_path": origin_destination_values.get(
            "origin_stop_point_ref_source_path"
        ),
        "origin_text": origin_destination_values.get("origin_text"),
        "origin_text_source_path": origin_destination_values.get("origin_text_source_path"),
        "destination_stop_point_ref": origin_destination_values.get("destination_stop_point_ref"),
        "destination_stop_point_ref_source_path": origin_destination_values.get(
            "destination_stop_point_ref_source_path"
        ),
        "destination_text": origin_destination_values.get("destination_text"),
        "destination_text_source_path": origin_destination_values.get(
            "destination_text_source_path"
        ),
        "stop_point_ref": stop_ref,
        "stop_point_ref_source_path": stop_ref_path,
        "stop_seq_number": stop_seq,
        "stop_seq_number_source_path": seq_path,
        "event_type": "ARRIVAL",
        "operational_flags": event_flags,
        "operational_flag_source_paths": event_flag_paths,
        "call_observations": calls,
    }


def reference_identity(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        participant = source_value(direct_named_value(value, {"participantref"}))
        situation_number = source_value(direct_named_value(value, {"situationnumber"}))
        version = source_value(direct_named_value(value, {"version"}))
        identifier = source_value(direct_named_value(value, {"identifier", "id", "ref", "value"}))
    else:
        participant = None
        situation_number = None
        version = None
        identifier = source_value(value)
    identity = source_identity_key(participant, situation_number, version, identifier, value)
    return {
        "identity_key": identity,
        "identity_key_without_version": identity_without_version(identity),
        "participant_ref": participant,
        "situation_number": situation_number,
        "version": version,
        "identifier": identifier,
    }


def situation_record(
    response_id: str,
    observed_at: str,
    path: str,
    situation: Dict[str, Any],
    ordinal: int,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    identity = situation_identity(situation, path)
    valid_from = first_named_value(situation, {"validfrom", "starttime"})
    valid_to = first_named_value(situation, {"validto", "endtime"})
    source_field_path, source_raw = first_named_raw(situation, {"source"}, path)
    source_type_path: Optional[str] = None
    source_type: Any = None
    if source_field_path is not None:
        source_type_raw_path, source_type_raw = first_named_raw(
            source_raw,
            {"sourcetype"},
            source_field_path,
        )
        if source_type_raw_path is not None:
            source_type_path, source_type = source_value_at_path(
                source_type_raw,
                source_type_raw_path,
            )
    source_scalar = source_value(source_raw)
    source_representation = source_raw if is_populated(source_raw) else None
    source_record_value = source_scalar
    if source_record_value is None and source_type is not None:
        source_record_value = source_type
    if source_record_value is None and source_representation is not None:
        # A populated structured Source must never be reduced to NULL merely
        # because the generic scalar helper does not know its shape.
        source_record_value = source_representation
    return {
        "record_type": "situation_observation",
        "situation_observation_id": f"{response_id}|situation|{ordinal}",
        **parser_lineage(
            pilot_run_id=run_id,
            response_id=response_id,
            observed_at=observed_at,
        ),
        "source_path": path,
        "occurrence_ordinal": ordinal,
        **identity,
        "creation_time": first_named_value(situation, {"creationtime"}),
        "valid_from": valid_from,
        "valid_to": valid_to,
        "summary": first_named_value(situation, {"summary"}),
        "description": first_named_value(situation, {"description"}),
        "detail": first_named_value(situation, {"detail"}),
        "source": source_record_value,
        "source_value": source_scalar,
        "source_representation": source_representation,
        "source_field_path": source_field_path,
        "source_type": source_type,
        "source_type_source_path": source_type_path,
        "source_field_paths": {
            "source": source_field_path,
            "source_type": source_type_path,
        },
        "priority": first_named_value(situation, {"priority"}),
        "affected_entity_context": {
            "affects": sanitize_for_artifact(direct_named_value(situation, {"affects"}), ""),
            "affected_entity": sanitize_for_artifact(direct_named_value(situation, {"affectedentity"}), ""),
            "affected_journey": sanitize_for_artifact(direct_named_value(situation, {"affectedjourney"}), ""),
            "affected_line": sanitize_for_artifact(direct_named_value(situation, {"affectedline"}), ""),
        },
        "source_fragment": sanitize_for_artifact(situation, ""),
    }


def situation_associations(
    *,
    response_id: str,
    result_stop_event_ids: Sequence[Tuple[str, str]],
    situations: Sequence[Dict[str, Any]],
    references: Sequence[Tuple[str, Any]],
    result_paths: Sequence[Tuple[str, str]],
    run_id: Optional[str] = None,
    observed_at: Optional[str] = None,
    result_source_ids: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    for ordinal, (path, value) in enumerate(references):
        if not is_populated(value):
            continue
        ref = reference_identity(value)
        linked_situation: Optional[Dict[str, Any]] = None
        for situation in situations:
            if ref["identity_key"] is not None and (
                ref["identity_key"] == situation.get("identity_key")
                or ref["identity_key_without_version"] == situation.get("identity_key_without_version")
            ):
                linked_situation = situation
                break
        result_ordinal: Optional[int] = None
        stop_event_id: Optional[str] = None
        result_source_id: Any = None
        for candidate_result_ordinal, (result_path, candidate_stop_event_id) in enumerate(result_paths):
            if path == result_path or path.startswith(result_path + ".") or path.startswith(result_path + "["):
                result_ordinal = candidate_result_ordinal
                stop_event_id = candidate_stop_event_id
                if result_source_ids is not None:
                    result_source_id = result_source_ids.get(result_path)
                break
        output.append(
            {
                "record_type": "stop_event_situation_association",
                "association_id": f"{response_id}|situation_ref|{ordinal}",
                **parser_lineage(
                    pilot_run_id=run_id,
                    response_id=response_id,
                    observed_at=observed_at,
                ),
                "stop_event_observation_id": stop_event_id,
                "source_result_id": result_source_id,
                "result_ordinal": result_ordinal,
                "source_path": path,
                "situation_full_ref_source_path": path,
                "association_scope": relation_scope(path),
                "relation_scope": relation_scope(path),
                "situation_full_ref": sanitize_for_artifact(value, ""),
                "situation_full_ref_source_representation": sanitize_for_artifact(value, ""),
                "situation_ref_identity": ref,
                "situation_observation_id": (
                    linked_situation.get("situation_observation_id") if linked_situation else None
                ),
                "resolved_situation_observation_id": (
                    linked_situation.get("situation_observation_id") if linked_situation else None
                ),
                "resolution_status": "RESOLVED_INLINE_SITUATION" if linked_situation else "UNRESOLVED_INLINE_SITUATION",
            }
        )
    return output


def service_attribute_record(
    *,
    response_id: str,
    observed_at: str,
    path: str,
    field_name: str,
    value: Any,
    result_paths: Sequence[Tuple[str, str]],
    call_scopes: Sequence[Tuple[str, str]],
    ordinal: int,
    run_id: Optional[str] = None,
    result_source_ids: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    codes = named_source_values(value, {"code"})
    texts = named_source_values(value, {"text"})
    incident_values: List[Any] = []
    for candidate_path, candidate_value in named_source_values(value, {"code", "text", "incidentcall", "infocall"}):
        if "incidentcall" in str(candidate_value).casefold():
            incident_values.append(candidate_value)
    for candidate in scalar_values(value):
        if "incidentcall" in str(candidate).casefold() and candidate not in incident_values:
            incident_values.append(candidate)
    incident = bool(incident_values)
    parent_result_id: Optional[str] = None
    stop_event_id: Optional[str] = None
    source_result_id: Any = None
    for result_path, candidate_stop_event_id in result_paths:
        if path == result_path or path.startswith(result_path + ".") or path.startswith(result_path + "["):
            stop_event_id = candidate_stop_event_id
            parent_result_id = result_path
            if result_source_ids is not None:
                source_result_id = result_source_ids.get(result_path)
            break
    scope = relation_scope(path)
    return {
        "record_type": "service_attribute_observation",
        "service_attribute_observation_id": f"{response_id}|attribute|{ordinal}",
        **parser_lineage(
            pilot_run_id=run_id,
            response_id=response_id,
            observed_at=observed_at,
        ),
        "stop_event_observation_id": stop_event_id,
        "source_result_id": source_result_id,
        "source_path": path,
        "attribute_field_name": field_name,
        "relation_scope": scope,
        "classification": (
            "INCIDENT_SERVICE_INFORMATION_ATTRIBUTE"
            if incident
            else "GENERAL_PASSENGER_INFORMATION_ATTRIBUTE"
        ),
        "code_values": [value for _path, value in codes],
        "text_values": [value for _path, value in texts],
        "incident_call_values": incident_values,
        "incident_call_present": incident,
        "source_field_paths": [item_path for item_path, _item in named_source_values(value, {"code", "text", "incidentcall", "infocall"})],
        "source_fragment": sanitize_for_artifact(value, ""),
        "parent_result_source_path": parent_result_id,
    }


def error_message_record(
    *,
    response_id: str,
    observed_at: str,
    entry: Dict[str, Any],
    ordinal: int,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Create one persisted record for one populated source ErrorMessage."""

    return {
        "record_type": "error_message_observation",
        "error_message_observation_id": f"{response_id}|error_message|{ordinal}",
        **parser_lineage(
            pilot_run_id=run_id,
            response_id=response_id,
            observed_at=observed_at,
        ),
        "error_scope": entry.get("error_scope"),
        "source_path": entry.get("source_path"),
        "field_name": entry.get("field_name"),
        "source_result_id": entry.get("source_result_id"),
        "source_result_id_path": entry.get("source_result_id_path"),
        "result_source_path": entry.get("result_source_path"),
        "result_ordinal": entry.get("result_ordinal"),
        "source_value": entry.get("source_value"),
        "source_representation": entry.get("source_representation"),
        "value_excerpt": entry.get("value_excerpt"),
        "error_code": entry.get("error_code"),
        "error_code_source_path": entry.get("error_code_source_path"),
    }


def build_bundle(
    *,
    parsed: ParsedResponse,
    response_id: str,
    run_id: str,
    observed_at: str,
) -> Dict[str, Any]:
    stop_events: List[Dict[str, Any]] = []
    result_paths: List[Tuple[str, str]] = []
    result_source_ids: Dict[str, Any] = {}
    for ordinal, (result_path, result) in enumerate(parsed.results):
        record = stop_event_record(
            response_id=response_id,
            run_id=run_id,
            observed_at=observed_at,
            result_path=result_path,
            result=result,
            result_ordinal=ordinal,
        )
        stop_events.append(record)
        result_paths.append((result_path, record["stop_event_observation_id"]))
        result_source_ids[result_path] = record.get("source_result_id")

    situations = [
        situation_record(response_id, observed_at, path, item, ordinal, run_id)
        for ordinal, (path, item) in enumerate(parsed.situation_entries)
    ]
    situation_by_identity: Dict[str, Dict[str, Any]] = {}
    for situation in situations:
        if situation.get("identity_key"):
            situation_by_identity[situation["identity_key"]] = situation
        if situation.get("identity_key_without_version"):
            situation_by_identity.setdefault(situation["identity_key_without_version"], situation)
    references = situation_associations(
        response_id=response_id,
        result_stop_event_ids=[(path, stop_id) for path, stop_id in result_paths],
        situations=list(situations),
        references=parsed.reference_entries,
        result_paths=result_paths,
        run_id=run_id,
        observed_at=observed_at,
        result_source_ids=result_source_ids,
    )
    attributes = [
        service_attribute_record(
            response_id=response_id,
            observed_at=observed_at,
            path=path,
            field_name=field_name,
            value=value,
            result_paths=result_paths,
            call_scopes=[],
            ordinal=ordinal,
            run_id=run_id,
            result_source_ids=result_source_ids,
        )
        for ordinal, (path, field_name, value) in enumerate(parsed.attribute_entries)
    ]
    error_messages = [
        error_message_record(
            response_id=response_id,
            observed_at=observed_at,
            entry=entry,
            ordinal=ordinal,
            run_id=run_id,
        )
        for ordinal, entry in enumerate(
            parsed.response_error_entries + parsed.result_error_entries
        )
        if normalized_key(entry.get("field_name")) == "errormessage"
        and entry.get("populated")
    ]
    gaps: List[Dict[str, Any]] = []
    for ordinal, gap in enumerate(parsed.parser_gaps):
        result_source_path: Optional[str] = None
        source_result_id: Any = None
        for candidate_result_path, candidate_result_id in result_source_ids.items():
            gap_path = str(gap.get("source_path", ""))
            if (
                gap_path == candidate_result_path
                or gap_path.startswith(candidate_result_path + ".")
                or gap_path.startswith(candidate_result_path + "[")
            ):
                result_source_path = candidate_result_path
                source_result_id = candidate_result_id
                break
        gaps.append(
            {
                "record_type": "parser_gap_evidence",
                **parser_lineage(
                    pilot_run_id=run_id,
                    response_id=response_id,
                    observed_at=observed_at,
                ),
                "parser_gap_id": f"{response_id}|parser_gap|{ordinal}",
                "field_path": gap.get("source_path"),
                "source_result_id": source_result_id,
                "result_source_path": result_source_path,
                **gap,
            }
        )
    return {
        "stop_events": stop_events,
        "calls": [call for event in stop_events for call in event["call_observations"]],
        "situations": situations,
        "associations": references,
        "attributes": attributes,
        "error_messages": error_messages,
        "parser_gaps": gaps,
        "response_error_entries": parsed.response_error_entries,
        "result_error_entries": parsed.result_error_entries,
        "application_error": application_error(parsed),
        "application_status": parsed.application_status,
        "response_timestamp": parsed.response_timestamp,
    }


def reconcile_bundle(
    bundle: Dict[str, Any],
    parsed: ParsedResponse,
    store: Optional[PilotStore] = None,
    response_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Reconcile parser output against records read back from JSONL."""

    if response_id is None:
        for item in bundle["stop_events"]:
            if item.get("response_id") is not None:
                response_id = item["response_id"]
                break
        if response_id is None:
            for logical_name in (
                "situations",
                "associations",
                "attributes",
                "error_messages",
                "parser_gaps",
            ):
                for item in bundle.get(logical_name, []):
                    if item.get("response_id") is not None:
                        response_id = item["response_id"]
                        break
                if response_id is not None:
                    break

    expected_stop_events = len(parsed.results)
    expected_calls = 0
    for _path, result in parsed.results:
        stop_event = direct_object(result, {"stopevent"}) or {}
        for scope_name in ("thiscall", "previouscall", "onwardcall"):
            expected_calls += len(scope_calls(stop_event, scope_name))
    expected_situations = len(parsed.situation_entries)
    expected_associations = sum(1 for _path, value in parsed.reference_entries if is_populated(value))
    expected_attributes = len(parsed.attribute_entries)
    expected_error_messages = sum(
        1
        for item in parsed.response_error_entries + parsed.result_error_entries
        if normalized_key(item.get("field_name")) == "errormessage"
        and item.get("populated")
    )
    expected_gaps = len(parsed.parser_gaps)

    actual_records: Dict[str, List[Dict[str, Any]]] = {}
    persistence_readback_ok = store is not None and response_id is not None
    if persistence_readback_ok:
        for logical_name in (
            "stop_event_observations",
            "call_observations",
            "situation_observations",
            "situation_associations",
            "service_attributes",
            "error_messages",
            "parser_gaps",
        ):
            actual_records[logical_name] = store.records_for_response(logical_name, response_id)
    else:
        for logical_name in (
            "stop_event_observations",
            "call_observations",
            "situation_observations",
            "situation_associations",
            "service_attributes",
            "error_messages",
            "parser_gaps",
        ):
            actual_records[logical_name] = []

    persisted_stop_events = len(actual_records["stop_event_observations"])
    persisted_calls = len(actual_records["call_observations"])
    persisted_situations = len(actual_records["situation_observations"])
    persisted_associations = len(actual_records["situation_associations"])
    persisted_attributes = len(actual_records["service_attributes"])
    persisted_error_messages = len(actual_records["error_messages"])
    persisted_gaps = len(actual_records["parser_gaps"])

    def ids_match(
        expected_items: Sequence[Dict[str, Any]],
        actual_items: Sequence[Dict[str, Any]],
        field_name: str,
    ) -> bool:
        expected_ids = [item.get(field_name) for item in expected_items]
        actual_ids = [item.get(field_name) for item in actual_items]
        return (
            None not in expected_ids
            and None not in actual_ids
            and len(set(expected_ids)) == len(expected_ids)
            and len(set(actual_ids)) == len(actual_ids)
            and Counter(expected_ids) == Counter(actual_ids)
        )

    def gap_keys(items: Sequence[Dict[str, Any]]) -> Counter[Tuple[Any, Any, Any]]:
        return Counter(
            (
                item.get("source_path"),
                item.get("field_name"),
                item.get("mapping_status"),
            )
            for item in items
        )

    def error_keys(
        items: Sequence[Dict[str, Any]],
    ) -> Counter[Tuple[Any, Any, Any, Any, Any]]:
        def key_atom(value: Any) -> Any:
            if isinstance(value, (dict, list)):
                return json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
            return value

        return Counter(
            (
                key_atom(item.get("error_scope")),
                key_atom(item.get("source_path")),
                key_atom(item.get("source_result_id")),
                key_atom(item.get("result_source_path")),
                key_atom(item.get("error_code")),
            )
            for item in items
        )

    checks = {
        "actual_jsonl_persistence_readback_available": persistence_readback_ok,
        "stop_event_result_count_equals_persisted_stop_event_count": expected_stop_events == persisted_stop_events,
        "stop_event_observation_ids_match_persisted_records": ids_match(
            bundle["stop_events"],
            actual_records["stop_event_observations"],
            "stop_event_observation_id",
        ),
        "call_occurrence_count_equals_persisted_call_count": expected_calls == persisted_calls,
        "call_occurrence_ids_match_persisted_records": ids_match(
            bundle["calls"],
            actual_records["call_observations"],
            "call_observation_id",
        ),
        "situation_occurrence_count_equals_persisted_situation_count": expected_situations == persisted_situations,
        "situation_observation_ids_match_persisted_records": ids_match(
            bundle["situations"],
            actual_records["situation_observations"],
            "situation_observation_id",
        ),
        "situation_association_occurrence_count_equals_persisted_link_count": expected_associations == persisted_associations,
        "situation_association_ids_match_persisted_records": ids_match(
            bundle["associations"],
            actual_records["situation_associations"],
            "association_id",
        ),
        "situation_association_scopes_present": all(
            bool(item.get("association_scope"))
            for item in actual_records["situation_associations"]
        ),
        "attribute_occurrence_count_equals_persisted_attribute_count": expected_attributes == persisted_attributes,
        "attribute_observation_ids_match_persisted_records": ids_match(
            bundle["attributes"],
            actual_records["service_attributes"],
            "service_attribute_observation_id",
        ),
        "error_message_count_equals_persisted_error_message_count": (
            expected_error_messages == persisted_error_messages
        ),
        "error_message_observation_ids_match_persisted_records": ids_match(
            bundle["error_messages"],
            actual_records["error_messages"],
            "error_message_observation_id",
        ),
        "error_message_scope_and_source_lineage_match": (
            error_keys(bundle["error_messages"])
            == error_keys(actual_records["error_messages"])
        ),
        "error_message_values_are_persisted": all(
            item.get("source_value") is not None
            or item.get("source_representation") is not None
            for item in actual_records["error_messages"]
        ),
        "persisted_error_message_fields_are_not_incident_calls": all(
            normalized_key(item.get("field_name")) == "errormessage"
            for item in actual_records["error_messages"]
        ),
        "parser_gap_count_equals_persisted_gap_count": expected_gaps == persisted_gaps,
        "parser_gap_occurrences_match_persisted_records": gap_keys(bundle["parser_gaps"])
        == gap_keys(actual_records["parser_gaps"]),
        "parser_gaps_explicit_when_present": all(
            item.get("mapping_status") == "UNMAPPED_RELEVANT_SOURCE_FIELD"
            for item in actual_records["parser_gaps"]
        ),
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "expected": {
            "stop_event_results": expected_stop_events,
            "calls": expected_calls,
            "situations": expected_situations,
            "associations": expected_associations,
            "attributes": expected_attributes,
            "error_messages": expected_error_messages,
            "parser_gaps": expected_gaps,
        },
        "persisted": {
            "stop_event_observations": persisted_stop_events,
            "call_observations": persisted_calls,
            "situation_observations": persisted_situations,
            "situation_associations": persisted_associations,
            "service_attributes": persisted_attributes,
            "error_message_observations": persisted_error_messages,
            "parser_gap_evidence": persisted_gaps,
        },
    }


def response_id_for(run_id: str, slot_index: int, content_hash: str) -> str:
    """Idempotency identity: same run + same slot + same content only."""

    identity = f"{run_id}|scheduled_slot={slot_index}|content_sha256={content_hash}"
    return "MDD_RESPONSE|" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def persist_bundle(
    *,
    store: PilotStore,
    bundle: Dict[str, Any],
    parsed: ParsedResponse,
    response_snapshot: Dict[str, Any],
    response_id: str,
) -> Dict[str, Any]:
    if response_id in store.seen_response_ids:
        reconciliation = reconcile_bundle(
            bundle,
            parsed,
            store=store,
            response_id=response_id,
        )
        if reconciliation["status"] == "PASS":
            reconciliation["status"] = "PASS_EXACT_REPLAY_IDEMPOTENT"
        return {
            "idempotent_replay": True,
            "persistence_status": "PASS" if reconciliation["status"] == "PASS_EXACT_REPLAY_IDEMPOTENT" else "FAIL",
            "reconciliation": reconciliation,
        }

    store.append("response_snapshots", response_snapshot)
    for item in bundle["stop_events"]:
        store.append("stop_event_observations", item)
    for item in bundle["calls"]:
        store.append("call_observations", item)
        for context_name in ("arrival", "departure"):
            for timing in item.get(f"{context_name}_timing", []):
                for field in timing.get("fields", []):
                    if field.get("field") != "RecordedAtTime" or field.get("value") is None:
                        continue
                    comparison_key = timing_comparison_key(
                        {"event_source_identity": item.get("event_source_identity")},
                        item,
                        timing,
                    )
                    if comparison_key is None:
                        # The occurrence is still retained immediately, but
                        # no cross-response first-seen identity is claimed
                        # without a source call anchor.
                        first_seen = item["observed_at"]
                    else:
                        recorded_key = (comparison_key, field.get("value"))
                        first_seen = RECORDED_FIRST_SEEN.setdefault(
                            recorded_key,
                            item["observed_at"],
                        )
                    store.append(
                        "recorded_at_time",
                        {
                            "record_type": "recorded_at_time_observation",
                            **parser_lineage(
                                pilot_run_id=item.get("pilot_run_id"),
                                response_id=item["response_id"],
                                observed_at=item["observed_at"],
                            ),
                            "recorded_at_time_observed_at": first_seen,
                            "recorded_at_time": field["value"],
                            "event_source_identity": item.get("event_source_identity"),
                            "source_result_id": item.get("source_result_id"),
                            "result_source_path": item.get("result_source_path"),
                            "stop_event_observation_id": item.get("stop_event_observation_id"),
                            "call_observation_id": item.get("call_observation_id"),
                            "call_scope": item.get("call_scope"),
                            "arrival_departure_context": timing.get("context"),
                            "source_path": field.get("source_path"),
                            "corresponding_timetabled_time": first_timing_value(
                                timing, "TimetabledTime"
                            ),
                            "corresponding_estimated_time": first_timing_value(
                                timing, "EstimatedTime"
                            ),
                        },
                    )
    for item in bundle["situations"]:
        store.append("situation_observations", item)
    for item in bundle["associations"]:
        store.append("situation_associations", item)
    for item in bundle["attributes"]:
        store.append("service_attributes", item)
    for item in bundle["error_messages"]:
        store.append("error_messages", item)
    for item in bundle["parser_gaps"]:
        store.append("parser_gaps", item)

    reconciliation = reconcile_bundle(
        bundle,
        parsed,
        store=store,
        response_id=response_id,
    )
    store.append(
        "validation_results",
        {
            "record_type": "response_reconciliation",
            **parser_lineage(
                pilot_run_id=response_snapshot.get("pilot_run_id"),
                response_id=response_id,
                observed_at=response_snapshot.get("observed_at"),
            ),
            "reconciliation": reconciliation,
        },
    )
    store.seen_response_ids.add(response_id)
    return {
        "idempotent_replay": False,
        "persistence_status": "PASS" if reconciliation["status"] == "PASS" else "FAIL",
        "reconciliation": reconciliation,
    }


def first_timing_value(timing: Dict[str, Any], field_name: str) -> Any:
    values = timing.get("field_values", {}).get(field_name, [])
    return values[0] if values else None


def source_identity_atom(value: Any) -> Optional[str]:
    """Create a hashable representation from already-retained source evidence."""

    if not is_populated(value):
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    return str(value)


def timing_comparison_key(
    event: Dict[str, Any],
    call: Dict[str, Any],
    timing: Optional[Dict[str, Any]],
    context: Optional[str] = None,
) -> Optional[Tuple[str, ...]]:
    """Return a conservative cross-response identity for one call context.

    Stop sequence is the strongest retained call anchor.  When it is absent,
    the source TimetabledTime can identify the call context.  A bare scope,
    context, or StopPointRef is deliberately insufficient because multiple
    calls can share those values.
    """

    event_identity = source_identity_atom(event.get("event_source_identity"))
    call_scope = source_identity_atom(call.get("call_scope"))
    context_name = source_identity_atom(
        timing.get("context") if timing is not None else context
    )
    stop_point_ref = source_identity_atom(call.get("stop_point_ref"))
    stop_seq_number = source_identity_atom(call.get("stop_seq_number"))
    timetabled_time = (
        source_identity_atom(first_timing_value(timing, "TimetabledTime"))
        if timing is not None
        else None
    )
    if event_identity is None or call_scope is None or context_name is None:
        return None
    if stop_seq_number is not None:
        return (
            "MDD_CALL_CONTEXT",
            event_identity,
            call_scope,
            context_name,
            "STOP_POINT_AND_SEQUENCE",
            stop_point_ref or "",
            stop_seq_number,
        )
    if timetabled_time is not None:
        return (
            "MDD_CALL_CONTEXT",
            event_identity,
            call_scope,
            context_name,
            "STOP_POINT_AND_TIMETABLED_TIME",
            stop_point_ref or "",
            timetabled_time,
        )
    return None


RECORDED_FIRST_SEEN: Dict[Tuple[Tuple[str, ...], Any], str] = {}


class PilotMetrics:
    def __init__(self) -> None:
        self.attempted_http_requests = 0
        self.successful_http_responses = 0
        self.skipped_overlap_slots = 0
        self.timeout_count = 0
        self.network_failure_count = 0
        self.parser_failure_count = 0
        self.persistence_failure_count = 0
        self.mdd_application_error_count = 0
        self.auth_failure_count = 0
        self.rate_limit_count = 0
        self.http_error_count = 0
        self.success_count = 0
        self.reconciliation_pass_count = 0
        self.http_status_distribution: Counter[str] = Counter()
        self.outcome_distribution: Counter[str] = Counter()
        self.response_durations_ms: List[float] = []
        self.stop_event_result_occurrences = 0
        self.call_occurrences: Counter[str] = Counter()
        self.timing_presence: Counter[str] = Counter()
        self.planned_bay_presence = 0
        self.estimated_bay_presence = 0
        self.operational_flags: Dict[str, Counter[str]] = defaultdict(Counter)
        self.situation_occurrences = 0
        self.situation_associations = 0
        self.incident_call_occurrences = 0
        self.ordinary_attribute_occurrences = 0
        self.attribute_occurrences = 0
        self.response_error_populated_count = 0
        self.result_error_populated_count = 0
        self.unmapped_source_field_count = 0
        self.parser_gap_findings: Counter[Tuple[str, str, str, str]] = Counter()
        self.event_history: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self.recorded_occurrences: List[Dict[str, Any]] = []
        self.recorded_context_history: Dict[Tuple[str, ...], List[Dict[str, Any]]] = defaultdict(list)
        self.successful_response_sequence = 0

    def record_http_result(self, result: HTTPResult) -> None:
        self.attempted_http_requests += 1
        if result.http_status is not None:
            self.http_status_distribution[str(result.http_status)] += 1
            if 200 <= result.http_status < 300:
                self.successful_http_responses += 1
        elif result.failure:
            self.http_status_distribution[result.failure] += 1
        self.response_durations_ms.append(round(result.elapsed_ms, 3))
        if result.failure == "TIMEOUT":
            self.timeout_count += 1
        elif result.failure == "NETWORK_OR_DNS_FAILURE":
            self.network_failure_count += 1

    def record_outcome(self, outcome: str) -> None:
        self.outcome_distribution[outcome] += 1
        if outcome == "SUCCESS":
            self.success_count += 1
        elif outcome == "PARSER_FAILURE":
            self.parser_failure_count += 1
        elif outcome == "PILOT_PERSISTENCE_FAILURE":
            self.persistence_failure_count += 1
        elif outcome == "MDD_APPLICATION_ERROR":
            self.mdd_application_error_count += 1
        elif outcome == "AUTHENTICATION_OR_AUTHORIZATION_FAILURE":
            self.auth_failure_count += 1
        elif outcome == "RATE_LIMIT_RESPONSE":
            self.rate_limit_count += 1
        elif outcome == "HTTP_ERROR":
            self.http_error_count += 1

    def record_skipped_overlap(self) -> None:
        self.skipped_overlap_slots += 1
        self.outcome_distribution["SCHEDULED_SLOT_SKIPPED_OVERLAP"] += 1

    def record_bundle(self, bundle: Dict[str, Any], response_sequence: int) -> None:
        self.reconciliation_pass_count += 1
        self.successful_response_sequence = response_sequence
        stop_events = bundle["stop_events"]
        self.stop_event_result_occurrences += len(stop_events)
        self.situation_occurrences += len(bundle["situations"])
        self.situation_associations += len(bundle["associations"])
        self.unmapped_source_field_count += len(bundle["parser_gaps"])
        for gap in bundle["parser_gaps"]:
            self.parser_gap_findings[
                (
                    str(gap.get("field_name")),
                    normalized_parser_gap_path(gap.get("source_path")),
                    str(gap.get("value_type")),
                    str(gap.get("mapping_status")),
                )
            ] += 1
        self.response_error_populated_count += sum(
            1
            for item in bundle["response_error_entries"]
            if normalized_key(item.get("field_name")) == "errormessage"
            and item.get("populated")
        )
        self.result_error_populated_count += sum(
            1
            for item in bundle["result_error_entries"]
            if normalized_key(item.get("field_name")) == "errormessage"
            and item.get("populated")
        )
        for attribute in bundle["attributes"]:
            self.attribute_occurrences += 1
            if attribute.get("incident_call_present"):
                self.incident_call_occurrences += 1
            else:
                self.ordinary_attribute_occurrences += 1
        for event in stop_events:
            stable_identity = event.get("stable_event_identity")
            for call in event.get("call_observations", []):
                self.call_occurrences[call.get("call_scope", "UNKNOWN")] += 1
            arrival_estimates: List[Any] = []
            timing_observations: List[Dict[str, Any]] = []
            for call in event.get("call_observations", []):
                for context_name in ("arrival", "departure"):
                    timings = list(call.get(f"{context_name}_timing", []))
                    if timings:
                        for timing in timings:
                            timing_observations.append(
                                {
                                    "call": call,
                                    "timing": timing,
                                    "comparison_key": timing_comparison_key(
                                        event,
                                        call,
                                        timing,
                                    ),
                                }
                            )
                            if context_name == "arrival":
                                arrival_estimates.extend(
                                    timing.get("field_values", {}).get("EstimatedTime", [])
                                )
                    else:
                        timing_observations.append(
                            {
                                "call": call,
                                "timing": None,
                                "comparison_key": timing_comparison_key(
                                    event,
                                    call,
                                    None,
                                    context_name.upper(),
                                ),
                            }
                        )

            key_counts = Counter(
                item["comparison_key"]
                for item in timing_observations
                if item["comparison_key"] is not None
            )
            ambiguous_keys = {
                key for key, count in key_counts.items() if count > 1
            }
            for item in timing_observations:
                comparison_key = item["comparison_key"]
                timing = item["timing"]
                call = item["call"]
                if comparison_key is None:
                    if timing is not None:
                        self._record_timing(
                            timing,
                            call,
                            response_sequence,
                            event,
                            comparison_key=None,
                            comparison_status="UNCOMPARABLE_INSUFFICIENT_SOURCE_EVIDENCE",
                        )
                    continue
                if comparison_key in ambiguous_keys:
                    # A duplicate source anchor within one response cannot
                    # establish which call is the same occurrence later.
                    if timing is not None:
                        self._record_timing(
                            timing,
                            call,
                            response_sequence,
                            event,
                            comparison_key=None,
                            comparison_status="UNCOMPARABLE_AMBIGUOUS_SOURCE_EVIDENCE",
                        )
                    continue
                if timing is None:
                    self._record_timing_absence(
                        event,
                        comparison_key,
                        response_sequence,
                        "TIMING_CONTEXT_MISSING",
                    )
                else:
                    self._record_timing(
                        timing,
                        call,
                        response_sequence,
                        event,
                        comparison_key=comparison_key,
                        comparison_status="COMPARABLE_SOURCE_CALL_CONTEXT",
                    )

            # A missing call has no current source occurrence with which to
            # establish comparability.  Leave it as no later comparable call
            # rather than treating another call in the same scope as an
            # omission for this occurrence.

            if stable_identity:
                self.event_history[stable_identity].append(
                    {
                        "response_sequence": response_sequence,
                        "observed_at": event.get("observed_at"),
                        "estimated_time": arrival_estimates[0] if arrival_estimates else None,
                    }
                )
            for flag_name, value in event.get("operational_flags", {}).items():
                if value is not None:
                    self.operational_flags[flag_name][value_distribution_key(value)] += 1
            for call in event.get("call_observations", []):
                if call.get("planned_bay") is not None:
                    self.planned_bay_presence += 1
                if call.get("estimated_bay") is not None:
                    self.estimated_bay_presence += 1
                for flag_name, value in call.get("operational_flags", {}).items():
                    if value is not None:
                        self.operational_flags[flag_name][value_distribution_key(value)] += 1

    def _record_timing(
        self,
        timing: Dict[str, Any],
        call: Dict[str, Any],
        response_sequence: int,
        event: Dict[str, Any],
        *,
        comparison_key: Optional[Tuple[str, ...]],
        comparison_status: str,
    ) -> None:
        context = timing.get("context")
        recorded_values = list(timing.get("field_values", {}).get("RecordedAtTime", []))
        if comparison_key is not None:
            self.recorded_context_history[comparison_key].append(
                {
                    "response_sequence": response_sequence,
                    "response_id": event.get("response_id"),
                    "recorded_at_time_present": bool(recorded_values),
                    **({"recorded_at_times": recorded_values} if recorded_values else {}),
                }
            )
        for field_name, values in timing.get("field_values", {}).items():
            self.timing_presence[field_name] += len(values)
            if field_name != "RecordedAtTime":
                continue
            for value in values:
                occurrence = {
                    "event_source_identity": event.get("event_source_identity"),
                    "pilot_run_id": event.get("pilot_run_id"),
                    "call_scope": call.get("call_scope"),
                    "arrival_departure_context": context,
                    "response_id": event.get("response_id"),
                    "response_sequence": response_sequence,
                    "observed_at": event.get("observed_at"),
                    "recorded_at_time_observed_at": event.get("observed_at"),
                    "recorded_at_time": value,
                    "source_call_observation_id": call.get("call_observation_id"),
                    "lifecycle_comparison_status": comparison_status,
                    "source_path": next(
                        (
                            field.get("source_path")
                            for field in timing.get("fields", [])
                            if field.get("field") == "RecordedAtTime" and field.get("value") == value
                        ),
                        None,
                    ),
                }
                if comparison_key is not None:
                    occurrence["_lifecycle_comparison_key"] = comparison_key
                    occurrence["recorded_at_time_observed_at"] = RECORDED_FIRST_SEEN.get(
                        (comparison_key, value),
                        event.get("observed_at"),
                    )
                self.recorded_occurrences.append(occurrence)

    def _record_timing_absence(
        self,
        event: Dict[str, Any],
        comparison_key: Tuple[str, ...],
        response_sequence: int,
        reason: str,
    ) -> None:
        self.recorded_context_history[comparison_key].append(
            {
                "response_sequence": response_sequence,
                "response_id": event.get("response_id"),
                "recorded_at_time_present": False,
                "absence_reason": reason,
            }
        )

    def finalize_recorded_occurrences(self) -> None:
        for occurrence in self.recorded_occurrences:
            key = occurrence.pop("_lifecycle_comparison_key", None)
            if key is None:
                occurrence["later_response_result"] = "NO_LATER_COMPARABLE_CALL_OBSERVATION"
                continue
            current_sequence = occurrence.get("response_sequence")
            if current_sequence is None:
                current_sequence = next(
                    (
                        item["response_sequence"]
                        for item in self.recorded_context_history[key]
                        if item["response_id"] == occurrence.get("response_id")
                        and occurrence.get("recorded_at_time")
                        in item.get("recorded_at_times", [])
                    ),
                    None,
                )
            later = [
                item for item in self.recorded_context_history[key]
                if current_sequence is not None and item["response_sequence"] > current_sequence
            ]
            if not later:
                occurrence["later_response_result"] = "NO_LATER_COMPARABLE_CALL_OBSERVATION"
            else:
                later_results: List[Dict[str, Any]] = []
                for item in later:
                    if not item.get("recorded_at_time_present"):
                        result = "OMITTED_OR_DISAPPEARED_IN_LATER_RESPONSE"
                    elif occurrence.get("recorded_at_time") in item.get("recorded_at_times", []):
                        result = "RETAINED_IN_LATER_RESPONSE"
                    else:
                        result = "DIFFERENT_SOURCE_RECORDED_AT_TIME_IN_LATER_RESPONSE"
                    later_result = {
                        "response_sequence": item["response_sequence"],
                        "response_id": item.get("response_id"),
                        "result": result,
                        "recorded_at_time_present": bool(item.get("recorded_at_time_present")),
                    }
                    if item.get("recorded_at_time_present"):
                        later_result["recorded_at_times"] = item.get("recorded_at_times", [])
                    later_results.append(later_result)
                occurrence["later_response_results"] = later_results
                occurrence["later_response_result"] = later_results[0]["result"]

    def summary(self) -> Dict[str, Any]:
        repeated_events = {
            key: values for key, values in self.event_history.items() if len(values) >= 2
        }
        estimate_revisions = 0
        for values in self.event_history.values():
            ordered = sorted(values, key=lambda item: item["response_sequence"])
            previous: Any = None
            previous_present = False
            for item in ordered:
                estimate = item.get("estimated_time")
                if estimate is not None and previous_present and estimate != previous:
                    estimate_revisions += 1
                if estimate is not None:
                    previous = estimate
                    previous_present = True
        durations = sorted(self.response_durations_ms)
        parser_gap_findings = [
            {
                "field_name": field_name,
                "source_path": source_path,
                "value_type": value_type,
                "mapping_status": mapping_status,
                "occurrence_count": occurrence_count,
            }
            for (
                field_name,
                source_path,
                value_type,
                mapping_status,
            ), occurrence_count in sorted(self.parser_gap_findings.items())
        ]
        return {
            "planned_slots": MAX_LOGICAL_SLOTS,
            "attempted_http_requests": self.attempted_http_requests,
            "successful_http_responses": self.successful_http_responses,
            "http_status_distribution": dict(sorted(self.http_status_distribution.items())),
            "outcome_distribution": dict(sorted(self.outcome_distribution.items())),
            "skipped_overlap_slots": self.skipped_overlap_slots,
            "timeout_count": self.timeout_count,
            "network_dns_failure_count": self.network_failure_count,
            "parser_failure_count": self.parser_failure_count,
            "pilot_persistence_failure_count": self.persistence_failure_count,
            "mdd_application_error_count": self.mdd_application_error_count,
            "successful_parse_reconciliation_responses": self.success_count,
            "reconciliation_pass_count": self.reconciliation_pass_count,
            "successful_parse_reconciliation_rate_over_successful_http_responses": (
                round(self.success_count / self.successful_http_responses, 6)
                if self.successful_http_responses
                else None
            ),
            "stop_event_result_occurrences": self.stop_event_result_occurrences,
            "previous_call_occurrences": self.call_occurrences.get("PREVIOUS", 0),
            "this_call_occurrences": self.call_occurrences.get("THIS", 0),
            "onward_call_occurrences": self.call_occurrences.get("ONWARD", 0),
            "timing_field_presence": dict(sorted(self.timing_presence.items())),
            "planned_bay_presence": self.planned_bay_presence,
            "estimated_bay_presence": self.estimated_bay_presence,
            "operational_flag_value_distribution": {
                key: dict(sorted(value.items()))
                for key, value in sorted(self.operational_flags.items())
            },
            "situation_occurrences": self.situation_occurrences,
            "situation_full_ref_associations": self.situation_associations,
            "incident_call_occurrences": self.incident_call_occurrences,
            "ordinary_passenger_information_attribute_occurrences": self.ordinary_attribute_occurrences,
            "attribute_occurrences": self.attribute_occurrences,
            "populated_response_level_error_message_count": self.response_error_populated_count,
            "populated_result_level_error_message_count": self.result_error_populated_count,
            "unmapped_source_field_count": self.unmapped_source_field_count,
            "parser_gap_findings": parser_gap_findings,
            "estimated_time_revision_count": estimate_revisions,
            "events_observed_repeatedly": len(repeated_events),
            "response_duration_ms": {
                "count": len(durations),
                "min": durations[0] if durations else None,
                "median": percentile(durations, 0.50),
                "p95": percentile(durations, 0.95),
                "max": durations[-1] if durations else None,
            },
            "recorded_at_time_occurrences": list(self.recorded_occurrences),
            "recorded_at_time_distinct_events": len({item.get("event_source_identity") for item in self.recorded_occurrences}),
        }


def value_distribution_key(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "NULL"
    return str(value)


def percentile(values: Sequence[float], fraction: float) -> Optional[float]:
    if not values:
        return None
    index = max(0, min(len(values) - 1, int(round((len(values) - 1) * fraction))))
    return values[index]


def classification_for_http(result: HTTPResult) -> str:
    if result.failure == "TIMEOUT":
        return "TIMEOUT"
    if result.failure == "NETWORK_OR_DNS_FAILURE":
        return "NETWORK_OR_DNS_FAILURE"
    if result.http_status in {401, 403}:
        return "AUTHENTICATION_OR_AUTHORIZATION_FAILURE"
    if result.http_status == 429:
        return "RATE_LIMIT_RESPONSE"
    if result.http_status is not None and not 200 <= result.http_status < 300:
        return "HTTP_ERROR"
    if result.failure:
        return result.failure
    return "PARSER_FAILURE"


def write_collection_slot(
    store: PilotStore,
    *,
    run_id: str,
    slot_index: int,
    scheduled_at: datetime,
    state: str,
    outcome: str,
    request_timestamp: Optional[datetime] = None,
    response_id: Optional[str] = None,
    http_status: Optional[int] = None,
    elapsed_ms: Optional[float] = None,
    detail: Optional[str] = None,
    runtime_config: Stage8RuntimeConfig = DEFAULT_RUNTIME_CONFIG,
) -> None:
    store.append(
        "collection_slots",
        {
            "record_type": "collection_slot",
            "parser_version": PARSER_VERSION,
            "pilot_run_id": run_id,
            "effective_runtime_config": runtime_config_record(runtime_config),
            "station": runtime_config.station,
            "requested_stop_identity": {
                "station": runtime_config.station,
                "stop_point_ref": runtime_config.stop_point_ref,
            },
            "requested_stop_point_ref": runtime_config.stop_point_ref,
            "number_of_results": runtime_config.number_of_results,
            "sampling_policy": runtime_config.sampling_policy,
            "scheduled_slot": slot_index,
            "scheduled_slot_utc": iso_utc(scheduled_at),
            "observed_at": iso_utc(request_timestamp) if request_timestamp else None,
            "state": state,
            "outcome": outcome,
            "request_timestamp": iso_utc(request_timestamp) if request_timestamp else None,
            "response_id": response_id,
            "http_status": http_status,
            "elapsed_ms": elapsed_ms,
            "detail": detail,
        },
    )


def response_snapshot_record(
    *,
    run_id: str,
    response_id: str,
    slot_index: int,
    scheduled_at: datetime,
    result: HTTPResult,
    pilot_anchor: str,
    observed_at: str,
    content_hash: Optional[str],
    raw_path: Optional[Path],
    parsed: Optional[ParsedResponse],
    parse_error: Optional[str],
    credential_content_check: str,
    runtime_config: Stage8RuntimeConfig = DEFAULT_RUNTIME_CONFIG,
) -> Dict[str, Any]:
    return {
        "record_type": "response_snapshot",
        "pilot_run_id": run_id,
        "effective_runtime_config": runtime_config_record(runtime_config),
        "station": runtime_config.station,
        "requested_stop_identity": {
            "station": runtime_config.station,
            "stop_point_ref": runtime_config.stop_point_ref,
        },
        "sampling_policy": runtime_config.sampling_policy,
        "response_id": response_id,
        "scheduled_slot": slot_index,
        "scheduled_slot_utc": iso_utc(scheduled_at),
        "request_timestamp": iso_utc(result.request_timestamp),
        "observed_at": observed_at,
        "pilot_anchor_dep_arr_time": pilot_anchor,
        "requested_stop_point_ref": runtime_config.stop_point_ref,
        "effective_request_parameters": {
            "requestor_ref": REQUESTOR_REF,
            "stop_event_type": STOP_EVENT_TYPE,
            "number_of_results": runtime_config.number_of_results,
            "include_previous_calls": INCLUDE_PREVIOUS_CALLS,
            "include_onward_calls": INCLUDE_ONWARD_CALLS,
            "include_realtime_data": INCLUDE_REALTIME_DATA,
            "time_window": "OMITTED",
            "include_operating_days": "OMITTED",
        },
        "http_status": result.http_status,
        "application_status": parsed.application_status if parsed else None,
        "response_timestamp": parsed.response_timestamp if parsed else None,
        "response_content_type": result.content_type,
        "response_byte_size": len(result.body),
        "content_sha256": content_hash,
        "elapsed_ms": round(result.elapsed_ms, 3),
        "raw_artifact_path": str(raw_path) if raw_path else None,
        "parser_version": PARSER_VERSION,
        "parse_status": "PARSED" if parsed else "FAILED",
        "parse_error": parse_error,
        "result_count": len(parsed.results) if parsed else None,
        "response_level_error_count": len(parsed.response_error_entries) if parsed else None,
        "populated_response_level_error_count": (
            sum(
                1
                for item in parsed.response_error_entries
                if normalized_key(item.get("field_name")) == "errormessage"
                and item.get("populated")
            )
            if parsed
            else None
        ),
        "result_level_error_count": len(parsed.result_error_entries) if parsed else None,
        "populated_result_level_error_count": (
            sum(
                1
                for item in parsed.result_error_entries
                if normalized_key(item.get("field_name")) == "errormessage"
                and item.get("populated")
            )
            if parsed
            else None
        ),
        "credential_content_check": credential_content_check,
        "transport_failure": result.failure,
        "transport_failure_detail": result.failure_detail,
    }


def cleanup_directory(directory: Optional[Path]) -> str:
    if directory is None:
        return "NOT_CREATED"
    try:
        resolved = directory.resolve()
        if resolved.parent != Path("/private/tmp").resolve() and resolved.parent != Path(tempfile.gettempdir()).resolve():
            return "FAILED_UNSAFE_CLEANUP_TARGET"
        shutil.rmtree(resolved)
        return "SUCCEEDED" if not resolved.exists() else "FAILED_REMAINS"
    except Exception:
        return "FAILED"


def terminal_status(
    *,
    preflight_missing: bool,
    stop_reason: Optional[str],
    metrics: PilotMetrics,
    cleanup_result: str,
) -> str:
    if preflight_missing:
        return "BLOCKED"
    if cleanup_result != "SUCCEEDED":
        return "BLOCKED"
    if stop_reason in {
        "AUTHENTICATION_OR_AUTHORIZATION_FAILURE",
        "RATE_LIMIT_RESPONSE",
        "THREE_CONSECUTIVE_FAILED_REQUEST_SLOTS",
        "CREDENTIAL_SAFETY_FAILURE",
        "PILOT_PERSISTENCE_FAILURE",
        "UNRECOVERABLE_RUNTIME_FAILURE",
    }:
        return "BLOCKED"
    if metrics.success_count == 0:
        return "BLOCKED"
    if metrics.persistence_failure_count > 0:
        return "BLOCKED"
    return "COMPLETE"


def run_pilot(
    runtime_config: Optional[Stage8RuntimeConfig] = None,
) -> Dict[str, Any]:
    effective_runtime_config = runtime_config or DEFAULT_RUNTIME_CONFIG
    api_key = os.environ.get(MDD_API_KEY_ENV)
    print("MDD_API_KEY: PRESENT" if api_key else "MDD_API_KEY: MISSING", flush=True)
    run_id = f"stage08-{uuid.uuid4().hex}"
    if not api_key:
        return {
            "record_type": "stage_08_summary",
            "script_version": SCRIPT_VERSION,
            "stage_8_status": "BLOCKED",
            "pilot_run_id": run_id,
            "effective_runtime_config": runtime_config_record(effective_runtime_config),
            "station": effective_runtime_config.station,
            "requested_stop_identity": {
                "station": effective_runtime_config.station,
                "stop_point_ref": effective_runtime_config.stop_point_ref,
            },
            "requested_stop_point_ref": effective_runtime_config.stop_point_ref,
            "number_of_results": effective_runtime_config.number_of_results,
            "sampling_policy": effective_runtime_config.sampling_policy,
            "preflight": "MDD_API_KEY: MISSING",
            "planned_slots": MAX_LOGICAL_SLOTS,
            "attempted_http_requests": 0,
            "stop_reason": "MDD_API_KEY_MISSING",
            "cleanup_result": "NOT_CREATED",
            "cleanup_pending": False,
            "cleanup_max_retention_hours": 24,
            "request_contract": {
                "requestor_ref": REQUESTOR_REF,
                "stop_event_type": STOP_EVENT_TYPE,
                "number_of_results": effective_runtime_config.number_of_results,
                "include_previous_calls": INCLUDE_PREVIOUS_CALLS,
                "include_onward_calls": INCLUDE_ONWARD_CALLS,
                "include_realtime_data": INCLUDE_REALTIME_DATA,
                "time_window": "OMITTED",
                "include_operating_days": "OMITTED",
                "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
                "retries": RETRIES,
                "concurrency": "none",
            },
            "unmapped_source_field_count": 0,
            "parser_gap_findings": [],
            "recorded_at_time_result": "RECORDED_AT_TIME_NOT_OBSERVED_IN_STAGE_08_PILOT",
            "stage_8_pilot_started": False,
            "stage_9_or_later_work_performed": False,
        }

    directory: Optional[Path] = None
    store: Optional[PilotStore] = None
    RECORDED_FIRST_SEEN.clear()
    metrics = PilotMetrics()
    stop_reason: Optional[str] = None
    consecutive_failures = 0
    run_started = utc_now()
    t0_mono = time.perf_counter()
    pilot_anchor = trias_utc(run_started)
    response_sequence = 0
    completed_slots = 0

    try:
        directory = create_temp_directory()
        store = PilotStore(directory, api_key)

        def record_skipped_slot(slot_number: int, detail: str) -> None:
            nonlocal stop_reason
            skipped_at = run_started + timedelta(seconds=slot_number * SAMPLE_INTERVAL_SECONDS)
            metrics.record_skipped_overlap()
            try:
                write_collection_slot(
                    store,
                    run_id=run_id,
                    slot_index=slot_number,
                    scheduled_at=skipped_at,
                    state="SKIPPED",
                    outcome="SCHEDULED_SLOT_SKIPPED_OVERLAP",
                    detail=detail,
                    runtime_config=effective_runtime_config,
                )
            except Exception:
                metrics.persistence_failure_count += 1
                stop_reason = "PILOT_PERSISTENCE_FAILURE"

        def record_termination() -> None:
            try:
                store.append(
                    "collection_slots",
                    {
                        "record_type": "pilot_termination",
                        "parser_version": PARSER_VERSION,
                        "pilot_run_id": run_id,
                        "effective_runtime_config": runtime_config_record(
                            effective_runtime_config
                        ),
                        "station": effective_runtime_config.station,
                        "requested_stop_identity": {
                            "station": effective_runtime_config.station,
                            "stop_point_ref": effective_runtime_config.stop_point_ref,
                        },
                        "requested_stop_point_ref": effective_runtime_config.stop_point_ref,
                        "number_of_results": effective_runtime_config.number_of_results,
                        "sampling_policy": effective_runtime_config.sampling_policy,
                        "observed_at": iso_utc(utc_now()),
                        "terminated_after_slot": slot_index - 1,
                        "stop_reason": stop_reason,
                    },
                )
            except Exception:
                metrics.persistence_failure_count += 1

        slot_index = 0
        while slot_index < MAX_LOGICAL_SLOTS:
            scheduled_mono = t0_mono + slot_index * SAMPLE_INTERVAL_SECONDS
            if slot_index > 0:
                remaining = scheduled_mono - time.perf_counter()
                if remaining > 0:
                    time.sleep(remaining)
                else:
                    # The scheduler already returned after this fixed slot's
                    # start time.  Record the miss; never issue a late catch-up
                    # request for it.
                    record_skipped_slot(
                        slot_index,
                        "previous HTTP request/response processing cycle was still active at fixed slot time",
                    )
                    completed_slots += 1
                    slot_index += 1
                    if stop_reason is not None:
                        record_termination()
                        break
                    continue
            scheduled_at = run_started + timedelta(seconds=slot_index * SAMPLE_INTERVAL_SECONDS)
            result = perform_http_request(
                api_key,
                pilot_anchor,
                runtime_config=effective_runtime_config,
            )
            metrics.record_http_result(result)
            response_id: Optional[str] = None
            outcome: str
            observed_at = iso_utc(utc_now())
            raw_path: Optional[Path] = None
            content_hash: Optional[str] = None
            parsed: Optional[ParsedResponse] = None
            bundle: Optional[Dict[str, Any]] = None
            parse_error: Optional[str] = None
            response_persisted = False

            if result.http_status is not None:
                try:
                    if result.body:
                        raw_path = write_raw_artifact(
                            directory,
                            f"response-{slot_index:04d}.payload",
                            result.body,
                            api_key,
                        )
                    content_hash = hashlib.sha256(result.body).hexdigest()
                    response_id = response_id_for(run_id, slot_index, content_hash)
                except CredentialContentError as error:
                    outcome = "PILOT_PERSISTENCE_FAILURE"
                    stop_reason = "CREDENTIAL_SAFETY_FAILURE"
                    parse_error = clean_error(error, api_key)
                except Exception as error:
                    outcome = "PILOT_PERSISTENCE_FAILURE"
                    parse_error = clean_error(error, api_key)
            if result.http_status in {401, 403}:
                outcome = "AUTHENTICATION_OR_AUTHORIZATION_FAILURE"
            elif result.http_status == 429:
                outcome = "RATE_LIMIT_RESPONSE"
            elif result.http_status is not None and not 200 <= result.http_status < 300:
                outcome = "HTTP_ERROR"
            elif result.failure:
                outcome = result.failure
            elif response_id is None:
                outcome = "PILOT_PERSISTENCE_FAILURE"
            else:
                try:
                    if not result.body:
                        raise PilotParserError("EMPTY_RESPONSE_BODY")
                    parsed = parse_response(result.body, result.content_type)
                    bundle = build_bundle(
                        parsed=parsed,
                        response_id=response_id,
                        run_id=run_id,
                        observed_at=observed_at,
                    )
                    snapshot = response_snapshot_record(
                        run_id=run_id,
                        response_id=response_id,
                        slot_index=slot_index,
                        scheduled_at=scheduled_at,
                        result=result,
                        pilot_anchor=pilot_anchor,
                        observed_at=observed_at,
                        content_hash=content_hash,
                        raw_path=raw_path,
                        parsed=parsed,
                        parse_error=None,
                        credential_content_check="PASS",
                        runtime_config=effective_runtime_config,
                    )
                    if application_error(parsed):
                        outcome = "MDD_APPLICATION_ERROR"
                    else:
                        outcome = "SUCCESS"
                    persist_result = persist_bundle(
                        store=store,
                        bundle=bundle,
                        parsed=parsed,
                        response_snapshot=snapshot,
                        response_id=response_id,
                    )
                    response_persisted = True
                    if persist_result["persistence_status"] != "PASS":
                        outcome = "PILOT_PERSISTENCE_FAILURE"
                    elif persist_result["reconciliation"].get("status") in {
                        "PASS",
                        "PASS_EXACT_REPLAY_IDEMPOTENT",
                    }:
                        metrics.record_bundle(bundle, response_sequence)
                        response_sequence += 1
                    else:
                        outcome = "PILOT_PERSISTENCE_FAILURE"
                except PersistenceReadbackError as error:
                    outcome = "PILOT_PERSISTENCE_FAILURE"
                    stop_reason = stop_reason or "PILOT_PERSISTENCE_FAILURE"
                    parse_error = clean_error(error, api_key)
                except PilotParserError as error:
                    outcome = "PARSER_FAILURE"
                    parse_error = clean_error(error, api_key)
                    if response_id is not None:
                        snapshot = response_snapshot_record(
                            run_id=run_id,
                            response_id=response_id,
                            slot_index=slot_index,
                            scheduled_at=scheduled_at,
                            result=result,
                            pilot_anchor=pilot_anchor,
                            observed_at=observed_at,
                            content_hash=content_hash,
                            raw_path=raw_path,
                            parsed=None,
                            parse_error=parse_error,
                            credential_content_check="PASS",
                            runtime_config=effective_runtime_config,
                        )
                        try:
                            store.append("response_snapshots", snapshot)
                            response_persisted = True
                        except Exception:
                            outcome = "PILOT_PERSISTENCE_FAILURE"
                except Exception as error:  # pragma: no cover - defensive host-runtime boundary
                    outcome = "PILOT_PERSISTENCE_FAILURE"
                    parse_error = clean_error(error, api_key)

            if (
                result.http_status is not None
                and response_id is not None
                and not 200 <= result.http_status < 300
                and not response_persisted
            ):
                # Preserve a response snapshot for every received HTTP
                # response, including HTTP failures.  Parsing an error body is
                # best-effort; the transport classification remains primary.
                error_body_parsed: Optional[ParsedResponse] = None
                error_body_parse_error: Optional[str] = None
                if result.body:
                    try:
                        error_body_parsed = parse_response(result.body, result.content_type)
                    except PilotParserError as error:
                        error_body_parse_error = clean_error(error, api_key)
                snapshot = response_snapshot_record(
                    run_id=run_id,
                    response_id=response_id,
                    slot_index=slot_index,
                    scheduled_at=scheduled_at,
                    result=result,
                    pilot_anchor=pilot_anchor,
                    observed_at=observed_at,
                    content_hash=content_hash,
                    raw_path=raw_path,
                    parsed=error_body_parsed,
                    parse_error=error_body_parse_error,
                    credential_content_check="PASS",
                    runtime_config=effective_runtime_config,
                )
                try:
                    store.append("response_snapshots", snapshot)
                    response_persisted = True
                except Exception:
                    outcome = "PILOT_PERSISTENCE_FAILURE"
                    stop_reason = "PILOT_PERSISTENCE_FAILURE"
            if result.http_status is not None and response_id is not None and not response_persisted:
                # Failed persistence/parsing has already been classified.  Do
                # not attempt a mutable repair write; the slot record is the
                # authoritative failure lineage.
                pass
            metrics.record_outcome(outcome)
            detail = parse_error or result.failure_detail
            try:
                write_collection_slot(
                    store,
                    run_id=run_id,
                    slot_index=slot_index,
                    scheduled_at=scheduled_at,
                    state="ATTEMPTED",
                    outcome=outcome,
                    request_timestamp=result.request_timestamp,
                    response_id=response_id,
                    http_status=result.http_status,
                    elapsed_ms=round(result.elapsed_ms, 3),
                    detail=detail,
                    runtime_config=effective_runtime_config,
                )
            except Exception:
                metrics.persistence_failure_count += 1
                stop_reason = "PILOT_PERSISTENCE_FAILURE"

            if outcome == "SUCCESS":
                consecutive_failures = 0
            elif outcome not in {"SCHEDULED_SLOT_SKIPPED_OVERLAP"}:
                consecutive_failures += 1

            if outcome in {
                "AUTHENTICATION_OR_AUTHORIZATION_FAILURE",
                "RATE_LIMIT_RESPONSE",
            }:
                stop_reason = outcome
            elif outcome == "PILOT_PERSISTENCE_FAILURE":
                stop_reason = stop_reason or "PILOT_PERSISTENCE_FAILURE"
            elif stop_reason == "CREDENTIAL_SAFETY_FAILURE":
                pass
            elif consecutive_failures >= 3:
                stop_reason = "THREE_CONSECUTIVE_FAILED_REQUEST_SLOTS"

            completed_slots += 1
            slot_index += 1
            # Fixed start-to-start overlap handling is based on the moment the
            # complete HTTP request/response processing cycle returns control
            # to the scheduler.  This includes parsing and JSONL persistence,
            # so a slot missed during post-response work is skipped too.
            while (
                slot_index < MAX_LOGICAL_SLOTS
                and t0_mono + slot_index * SAMPLE_INTERVAL_SECONDS < time.perf_counter()
            ):
                record_skipped_slot(
                    slot_index,
                    "previous HTTP request/response processing cycle was still active at fixed slot time",
                )
                completed_slots += 1
                slot_index += 1
                if stop_reason is not None:
                    break

            if stop_reason is not None:
                record_termination()
                break
    except Exception as error:  # pragma: no cover - host failure boundary
        stop_reason = stop_reason or "UNRECOVERABLE_RUNTIME_FAILURE"
        if store is not None:
            try:
                store.append(
                    "collection_slots",
                    {
                        "record_type": "pilot_runtime_failure",
                        "parser_version": PARSER_VERSION,
                        "pilot_run_id": run_id,
                        "effective_runtime_config": runtime_config_record(
                            effective_runtime_config
                        ),
                        "station": effective_runtime_config.station,
                        "requested_stop_identity": {
                            "station": effective_runtime_config.station,
                            "stop_point_ref": effective_runtime_config.stop_point_ref,
                        },
                        "requested_stop_point_ref": effective_runtime_config.stop_point_ref,
                        "number_of_results": effective_runtime_config.number_of_results,
                        "sampling_policy": effective_runtime_config.sampling_policy,
                        "observed_at": iso_utc(utc_now()),
                        "stop_reason": stop_reason,
                        "detail": clean_error(error, api_key),
                    },
                )
            except Exception:
                metrics.persistence_failure_count += 1
    finally:
        if store is not None:
            store.close()

    run_ended = utc_now()
    metrics.finalize_recorded_occurrences()
    metrics_summary = metrics.summary()
    evidence_extraction_and_reconciliation_complete = (
        metrics.success_count > 0
        and metrics.parser_failure_count == 0
        and metrics.persistence_failure_count == 0
        and stop_reason is None
    )
    actual_http_evidence_exists = metrics.attempted_http_requests > 0
    if evidence_extraction_and_reconciliation_complete:
        cleanup_result = cleanup_directory(directory)
    elif actual_http_evidence_exists:
        # A blocked run may retain protected diagnostic artifacts for bounded
        # review.  The caller must delete them no later than this deadline;
        # they are never a permanent raw archive.
        cleanup_result = "PENDING_MAX_24_HOURS"
    else:
        cleanup_result = cleanup_directory(directory)
    cleanup_pending = cleanup_result == "PENDING_MAX_24_HOURS"
    cleanup_retention_deadline = (
        iso_utc(run_ended + timedelta(hours=24)) if cleanup_pending else None
    )
    status = terminal_status(
        preflight_missing=False,
        stop_reason=stop_reason,
        metrics=metrics,
        cleanup_result=cleanup_result,
    )
    summary = {
        "record_type": "stage_08_summary",
        "script_version": SCRIPT_VERSION,
        "stage_8_status": status,
        "stage_8_pilot_started": True,
        "pilot_run_id": run_id,
        "effective_runtime_config": runtime_config_record(effective_runtime_config),
        "station": effective_runtime_config.station,
        "requested_stop_identity": {
            "station": effective_runtime_config.station,
            "stop_point_ref": effective_runtime_config.stop_point_ref,
        },
        "requested_stop_point_ref": effective_runtime_config.stop_point_ref,
        "number_of_results": effective_runtime_config.number_of_results,
        "source": "MDD/TRIAS",
        "endpoint": ENDPOINT,
        "trias_version": TRIAS_VERSION,
        "sampling_policy": effective_runtime_config.sampling_policy,
        "pilot_started_at_utc": iso_utc(run_started),
        "pilot_ended_at_utc": iso_utc(run_ended),
        "pilot_anchor_dep_arr_time": pilot_anchor,
        "request_contract": {
            "requestor_ref": REQUESTOR_REF,
            "stop_event_type": STOP_EVENT_TYPE,
            "number_of_results": effective_runtime_config.number_of_results,
            "include_previous_calls": INCLUDE_PREVIOUS_CALLS,
            "include_onward_calls": INCLUDE_ONWARD_CALLS,
            "include_realtime_data": INCLUDE_REALTIME_DATA,
            "time_window": "OMITTED",
            "include_operating_days": "OMITTED",
            "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
            "retries": RETRIES,
            "concurrency": "none",
        },
        "execution": {
            "planned_slots": MAX_LOGICAL_SLOTS,
            "completed_or_skipped_slots": completed_slots,
            "actual_elapsed_seconds": round((run_ended - run_started).total_seconds(), 3),
            "stop_reason": stop_reason,
            "raw_and_jsonl_directory_cleanup_target": str(directory) if directory else None,
            "cleanup_result": cleanup_result,
            "cleanup_pending": cleanup_pending,
            "cleanup_max_retention_hours": 24,
            "cleanup_retention_deadline_utc": cleanup_retention_deadline,
            "evidence_extraction_and_reconciliation_complete": (
                evidence_extraction_and_reconciliation_complete
            ),
        },
        "metrics": metrics_summary,
        "recorded_at_time_result": (
            "RECORDED_AT_TIME_NOT_OBSERVED_IN_STAGE_08_PILOT"
            if not metrics.recorded_occurrences
            else "RECORDED_AT_TIME_OCCURRENCES_OBSERVED"
        ),
        "recorded_at_time_occurrences": metrics.recorded_occurrences,
        "bounded_interpretation": "One station, one bounded Stage 8 pilot only; no network-wide coverage claim.",
        "actual_delay_status": "NOT_APPROVED",
        "gtfs_rt_used": False,
        "stage_9_or_later_work_performed": False,
    }
    return sanitize_for_artifact(summary, api_key)


def main() -> int:
    summary = run_pilot()
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if summary.get("stage_8_status") == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
