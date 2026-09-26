#!/usr/bin/env python3
# HOST-RUN ONLY.
# Do not execute from Codex.
# This script may download a large public static GTFS ZIP.
"""Read-only bridge between GTFS.de static and realtime stop identities.

The default operation is local inspection of a caller-supplied GTFS.de ZIP.
``download-and-inspect`` performs one explicitly requested download of the
current public GTFS.de local/public-transit feed and keeps the ZIP in a
private temporary directory outside the repository.  The optional realtime
verification is a separate, explicitly enabled single request.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import socket
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple


STATIC_URL = "https://download.gtfs.de/germany/nv_free/latest.zip"
REALTIME_URL = "https://realtime.gtfs.de/realtime-free.pb"
REQUEST_TIMEOUT_SECONDS = 60
REALTIME_TIMEOUT_SECONDS = 30
MAX_REALTIME_BYTES = 100 * 1024 * 1024
MAX_EXAMPLES = 10
MAX_HBF_DIAGNOSTIC_CANDIDATES = 100
PROBE_NAME = "gtfsde_static_identity_bridge_probe"


@dataclass(frozen=True)
class StationSpec:
    key: str
    display_name: str
    variants: Tuple[str, ...]


STATIONS = (
    StationSpec("hbf", "Köln Hbf", ("Köln Hauptbahnhof",)),
    StationSpec("neumarkt", "Köln Neumarkt", ("Neumarkt",)),
)


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects so one planned GET cannot become hidden requests."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Any:
        raise urllib.error.HTTPError(
            req.full_url,
            code,
            "redirect rejected by bounded identity bridge",
            headers,
            fp,
        )


def clean_error(error: BaseException) -> str:
    message = " ".join(str(error).split())
    return (message or type(error).__name__)[:240]


def normalize_text(value: Optional[str]) -> str:
    if value is None:
        return ""
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split()).casefold()


def normalized_name_tokens(value: Optional[str]) -> Set[str]:
    """Return conservative Unicode word tokens for diagnostic name matching."""
    normalized = normalize_text(value)
    return set(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))


def hbf_match_reason(value: Optional[str]) -> Optional[str]:
    """Match only Köln plus an explicit Hbf/Hauptbahnhof token pair."""
    tokens = normalized_name_tokens(value)
    if "köln" not in tokens:
        return None

    matched_terms = []
    if "hbf" in tokens:
        matched_terms.append("Hbf")
    if "hauptbahnhof" in tokens:
        matched_terms.append("Hauptbahnhof")
    if not matched_terms:
        return None
    return "contains Köln + %s token(s)" % " + ".join(matched_terms)


def optional_text(row: Dict[str, str], field_name: str) -> Optional[str]:
    value = row.get(field_name, "")
    value = value.strip() if value is not None else ""
    return value or None


def stop_row(row: Dict[str, str]) -> Dict[str, Any]:
    location_type = optional_text(row, "location_type")
    if location_type is not None:
        try:
            location_type_value: Any = int(location_type)
        except ValueError:
            location_type_value = location_type
    else:
        location_type_value = None
    return {
        "stop_id": optional_text(row, "stop_id"),
        "stop_name": optional_text(row, "stop_name"),
        "parent_station": optional_text(row, "parent_station"),
        "location_type": location_type_value,
        "platform_code": optional_text(row, "platform_code"),
        "stop_lat": optional_text(row, "stop_lat"),
        "stop_lon": optional_text(row, "stop_lon"),
    }


def row_stop_id(row: Dict[str, str]) -> Optional[str]:
    value = row.get("stop_id", "")
    value = value.strip() if value is not None else ""
    return value or None


def row_parent_station(row: Dict[str, str]) -> Optional[str]:
    value = row.get("parent_station", "")
    value = value.strip() if value is not None else ""
    return value or None


def archive_member(archive: zipfile.ZipFile, filename: str) -> str:
    if filename in archive.namelist():
        return filename
    matches = [
        name
        for name in archive.namelist()
        if name.rstrip("/").rsplit("/", 1)[-1] == filename
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise FileNotFoundError("%s missing from static GTFS ZIP" % filename)
    raise ValueError("multiple %s entries found in static GTFS ZIP" % filename)


def iter_stop_rows(archive: zipfile.ZipFile) -> Iterable[Dict[str, str]]:
    member = archive_member(archive, "stops.txt")
    with archive.open(member, "r") as binary_stream:
        text_stream = None
        try:
            text_stream = io.TextIOWrapper(
                binary_stream,
                encoding="utf-8-sig",
                newline="",
            )
            reader = csv.DictReader(text_stream)
            for row in reader:
                yield dict(row)
        finally:
            if text_stream is not None:
                text_stream.detach()


def bounded_examples(values: Iterable[str]) -> List[str]:
    return sorted(set(values))[:MAX_EXAMPLES]


def collect_name_candidates(
    archive: zipfile.ZipFile,
    spec: StationSpec,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    exact: List[Dict[str, Any]] = []
    variants: List[Dict[str, Any]] = []
    exact_name = normalize_text(spec.display_name)
    variant_names = {normalize_text(value) for value in spec.variants}
    for row in iter_stop_rows(archive):
        if not row_stop_id(row):
            continue
        normalized_name = normalize_text(row.get("stop_name"))
        if normalized_name == exact_name:
            exact.append(stop_row(row))
        elif normalized_name in variant_names:
            variants.append(stop_row(row))
    return exact, variants


def inspect_parent_and_children(
    archive: zipfile.ZipFile,
    candidate_rows: Sequence[Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    candidate_ids = {
        row["stop_id"] for row in candidate_rows if row.get("stop_id")
    }
    referenced_parent_ids = {
        row["parent_station"]
        for row in candidate_rows
        if row.get("parent_station")
    }
    parent_ids_to_inspect = candidate_ids | referenced_parent_ids
    parent_rows: Dict[str, Dict[str, Any]] = {}
    children_by_parent: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for row in iter_stop_rows(archive):
        stop_id = row_stop_id(row)
        parent_id = row_parent_station(row)
        if stop_id in referenced_parent_ids and stop_id is not None:
            parent_rows[stop_id] = stop_row(row)
        if parent_id in parent_ids_to_inspect and stop_id is not None:
            children_by_parent[parent_id].append(stop_row(row))

    return parent_rows, dict(children_by_parent)


def location_type_is_station(row: Dict[str, Any]) -> bool:
    return row.get("location_type") == 1 or str(row.get("location_type")) == "1"


def unique_rows(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_id: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        stop_id = row.get("stop_id")
        if stop_id:
            by_id.setdefault(str(stop_id), row)
    return [by_id[stop_id] for stop_id in sorted(by_id)]


def diagnostic_candidate_row(row: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """Preserve source fields and add the normalized diagnostic explanation."""
    if not row_stop_id(row):
        return None
    match_reason = hbf_match_reason(row.get("stop_name"))
    if match_reason is None:
        return None
    candidate = stop_row(row)
    candidate["normalized_stop_name"] = normalize_text(row.get("stop_name"))
    candidate["match_reason"] = match_reason
    return candidate


def collect_hbf_diagnostic_candidates(
    archive: zipfile.ZipFile,
) -> Tuple[List[Dict[str, Any]], int, bool]:
    """Find a bounded, deterministic set of conservative Hbf name candidates."""
    all_candidates: List[Dict[str, Any]] = []
    for row in iter_stop_rows(archive):
        candidate = diagnostic_candidate_row(row)
        if candidate is not None:
            all_candidates.append(candidate)

    candidates = unique_rows(all_candidates)
    total = len(candidates)
    bounded_candidates = candidates[:MAX_HBF_DIAGNOSTIC_CANDIDATES]
    return bounded_candidates, total, total > len(bounded_candidates)


def source_row(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return only the source stop fields for parent/child summaries."""
    if row is None:
        return None
    return {
        "stop_id": row.get("stop_id"),
        "stop_name": row.get("stop_name"),
        "parent_station": row.get("parent_station"),
        "location_type": row.get("location_type"),
        "platform_code": row.get("platform_code"),
        "stop_lat": row.get("stop_lat"),
        "stop_lon": row.get("stop_lon"),
    }


def clearly_matching_hbf_parent(row: Optional[Dict[str, Any]]) -> bool:
    """Require a station-typed parent whose own name identifies Köln Hbf."""
    if row is None or not location_type_is_station(row):
        return False
    return hbf_match_reason(row.get("stop_name")) is not None


def diagnostic_parent_analysis(
    archive: zipfile.ZipFile,
    candidate_rows: Sequence[Dict[str, Any]],
    candidates_truncated: bool,
) -> Dict[str, Any]:
    """Build parent/child evidence and select only deterministic mappings."""
    parent_rows, children_by_parent = inspect_parent_and_children(
        archive,
        candidate_rows,
    )
    primary_rows_by_id = {
        str(row["stop_id"]): source_row(row)
        for row in candidate_rows
        if row.get("stop_id")
    }

    plausible_parent_ids: Set[str] = set()
    for row in candidate_rows:
        stop_id = row.get("stop_id")
        parent_id = row.get("parent_station")
        if parent_id:
            plausible_parent_ids.add(str(parent_id))
        if stop_id and (
            location_type_is_station(row)
            or str(stop_id) in children_by_parent
        ):
            plausible_parent_ids.add(str(stop_id))

    parent_summaries: List[Dict[str, Any]] = []
    clearly_matching_parent_ids: List[str] = []
    for parent_id in sorted(plausible_parent_ids):
        parent = source_row(parent_rows.get(parent_id))
        if parent is None:
            parent = primary_rows_by_id.get(parent_id)
        if parent is None:
            parent = {
                "stop_id": parent_id,
                "stop_name": None,
                "parent_station": None,
                "location_type": None,
                "platform_code": None,
                "stop_lat": None,
                "stop_lon": None,
            }
        child_stops = unique_rows(children_by_parent.get(parent_id, []))
        child_ids = [
            str(row["stop_id"])
            for row in child_stops
            if row.get("stop_id")
        ]
        parent_summary = {
            "parent_station": parent_id,
            "parent_stop": parent,
            "child_count": len(child_ids),
            "child_stop_ids": child_ids,
            "child_stops": child_stops,
            "candidate_child_stop_ids": [
                str(row["stop_id"])
                for row in candidate_rows
                if row.get("stop_id")
                and row.get("parent_station") == parent_id
            ],
        }
        parent_summaries.append(parent_summary)
        if clearly_matching_hbf_parent(parent):
            clearly_matching_parent_ids.append(parent_id)

    selected_parent_id: Optional[str] = None
    selection_note = None
    if candidates_truncated:
        selection_note = (
            "candidate output reached the bounded limit; automatic selection is disabled"
        )
    elif len(plausible_parent_ids) > 1:
        selection_note = (
            "multiple plausible parent stations were found; no parent was selected"
        )
    elif len(clearly_matching_parent_ids) == 1:
        candidate_parent_id = clearly_matching_parent_ids[0]
        selected_summary = next(
            summary
            for summary in parent_summaries
            if summary["parent_station"] == candidate_parent_id
        )
        candidate_child_rows = [
            row
            for row in candidate_rows
            if str(row.get("stop_id")) != candidate_parent_id
        ]
        candidate_children_consistent = all(
            row.get("parent_station") == candidate_parent_id
            for row in candidate_child_rows
        )
        selected_summary["candidate_children_consistent"] = (
            candidate_children_consistent
        )
        if selected_summary["child_count"] > 0 and candidate_children_consistent:
            selected_parent_id = candidate_parent_id
        elif not candidate_children_consistent:
            selection_note = (
                "relevant candidate child stops do not consistently reference "
                "the same parent station"
            )
        else:
            selection_note = (
                "the clearly matching parent has no child stops in stops.txt"
            )
    elif len(clearly_matching_parent_ids) > 1:
        selection_note = (
            "multiple clearly matching Köln Hbf/Hauptbahnhof parent stations were found"
        )
    elif not parent_summaries:
        selection_note = "no plausible parent station or parent/child group was found"
    else:
        selection_note = (
            "no single clearly matching Köln Hbf/Hauptbahnhof parent station was found"
        )

    selected_summary = next(
        (
            summary
            for summary in parent_summaries
            if summary["parent_station"] == selected_parent_id
        ),
        None,
    )
    return {
        "station_candidates": [
            row for row in candidate_rows if location_type_is_station(row)
        ],
        "parent_candidates": parent_summaries,
        "selected_parent_station_id": selected_parent_id,
        "selected_parent_summary": selected_summary,
        "selection_note": selection_note,
    }


def resolve_station(
    archive: zipfile.ZipFile,
    spec: StationSpec,
    diagnostic_candidates: bool = False,
) -> Dict[str, Any]:
    exact_candidates, variant_candidates = collect_name_candidates(archive, spec)
    primary_candidates = exact_candidates if exact_candidates else variant_candidates
    match_basis = "EXACT_STOP_NAME" if exact_candidates else "VARIANT_STOP_NAME"
    result: Dict[str, Any] = {
        "station_key": spec.key,
        "station_name": spec.display_name,
        "classification": "STATION_MAPPING_NOT_FOUND",
        "match_basis": None if not primary_candidates else match_basis,
        "variant_matching_used": bool(not exact_candidates and variant_candidates),
        "exact_name_candidates": unique_rows(exact_candidates),
        "variant_name_candidates": unique_rows(variant_candidates),
        "parent_candidates": [],
        "selected_parent_station": None,
        "selected_parent_station_id": None,
        "child_stops": [],
        "child_stop_ids": [],
        "resolved_static_stop_ids": [],
        "mapping_notes": [],
        "diagnostic_candidates": [],
        "diagnostic_candidate_total": 0,
        "diagnostic_candidates_truncated": False,
        "diagnostic_station_candidates": [],
        "diagnostic_parent_candidates": [],
        "diagnostic_selection_note": None,
    }
    if not primary_candidates:
        if spec.key == "hbf" and diagnostic_candidates:
            (
                bounded_candidates,
                candidate_total,
                candidates_truncated,
            ) = collect_hbf_diagnostic_candidates(archive)
            diagnostic = diagnostic_parent_analysis(
                archive,
                bounded_candidates,
                candidates_truncated,
            )
            result.update(
                {
                    "diagnostic_candidates": bounded_candidates,
                    "diagnostic_candidate_total": candidate_total,
                    "diagnostic_candidates_truncated": candidates_truncated,
                    "diagnostic_station_candidates": diagnostic[
                        "station_candidates"
                    ],
                    "diagnostic_parent_candidates": diagnostic[
                        "parent_candidates"
                    ],
                    "diagnostic_selection_note": diagnostic["selection_note"],
                }
            )
            selected_parent_id = diagnostic["selected_parent_station_id"]
            selected_summary = diagnostic["selected_parent_summary"]
            if selected_parent_id is not None and selected_summary is not None:
                selected_parent = selected_summary["parent_stop"]
                child_stops = selected_summary["child_stops"]
                child_stop_ids = selected_summary["child_stop_ids"]
                result.update(
                    {
                        "classification": "STATION_MAPPING_RESOLVED",
                        "parent_candidates": [
                            summary["parent_stop"]
                            for summary in diagnostic["parent_candidates"]
                        ],
                        "selected_parent_station": selected_parent,
                        "selected_parent_station_id": selected_parent_id,
                        "child_stops": child_stops,
                        "child_stop_ids": child_stop_ids,
                        "resolved_static_stop_ids": sorted(
                            set(child_stop_ids)
                        ),
                    }
                )
                result["mapping_notes"].append(
                    "bounded Köln Hbf diagnostic candidates produced deterministic "
                    "parent/child evidence"
                )
            else:
                result["classification"] = (
                    "STATION_MAPPING_NOT_FOUND"
                    if not bounded_candidates
                    else "STATION_MAPPING_AMBIGUOUS"
                )
                if diagnostic["selection_note"]:
                    result["mapping_notes"].append(
                        diagnostic["selection_note"]
                    )
            if not bounded_candidates:
                result["mapping_notes"].append(
                    "no conservative Köln + Hbf/Hauptbahnhof diagnostic candidate "
                    "was found"
                )
            return result
        result["mapping_notes"].append("no exact or labelled variant stop_name match")
        return result

    parent_rows, children_by_parent = inspect_parent_and_children(
        archive,
        primary_candidates,
    )
    plausible_parent_ids: Set[str] = set()
    for row in primary_candidates:
        stop_id = row.get("stop_id")
        if not stop_id:
            continue
        if location_type_is_station(row) or stop_id in children_by_parent:
            plausible_parent_ids.add(str(stop_id))
        parent_id = row.get("parent_station")
        if parent_id:
            plausible_parent_ids.add(str(parent_id))

    parent_candidates: List[Dict[str, Any]] = []
    primary_rows_by_id = {
        str(row["stop_id"]): row
        for row in primary_candidates
        if row.get("stop_id")
    }
    for parent_id in sorted(plausible_parent_ids):
        parent_candidates.append(
            parent_rows.get(
                parent_id,
                primary_rows_by_id.get(
                    parent_id,
                    {
                        "stop_id": parent_id,
                        "stop_name": None,
                        "parent_station": None,
                        "location_type": None,
                        "platform_code": None,
                        "stop_lat": None,
                        "stop_lon": None,
                    },
                ),
            )
        )
    result["parent_candidates"] = parent_candidates

    if len(plausible_parent_ids) > 1:
        result["classification"] = "STATION_MAPPING_AMBIGUOUS"
        result["mapping_notes"].append(
            "multiple plausible parent stations were found; no parent was selected"
        )
        return result

    selected_parent_id: Optional[str] = None
    if len(plausible_parent_ids) == 1:
        selected_parent_id = next(iter(plausible_parent_ids))
    elif len(primary_candidates) == 1:
        selected_parent_id = str(primary_candidates[0]["stop_id"])
    else:
        result["classification"] = "STATION_MAPPING_AMBIGUOUS"
        result["mapping_notes"].append(
            "multiple candidates exist without enough parent/child evidence"
        )
        return result

    selected_parent = next(
        (
            row
            for row in parent_candidates
            if row.get("stop_id") == selected_parent_id
        ),
        next(
            (
                row
                for row in primary_candidates
                if row.get("stop_id") == selected_parent_id
            ),
            None,
        ),
    )
    child_stops = unique_rows(children_by_parent.get(selected_parent_id, []))
    resolved_ids = [
        str(row["stop_id"])
        for row in child_stops
        if row.get("stop_id")
    ]
    if not resolved_ids and selected_parent_id:
        resolved_ids = [selected_parent_id]
        result["mapping_notes"].append(
            "no child stops were present; the selected stop/station ID is retained"
        )

    result.update(
        {
            "classification": "STATION_MAPPING_RESOLVED",
            "selected_parent_station": selected_parent,
            "selected_parent_station_id": selected_parent_id,
            "child_stops": child_stops,
            "child_stop_ids": resolved_ids,
            "resolved_static_stop_ids": sorted(set(resolved_ids)),
        }
    )
    if not exact_candidates:
        result["mapping_notes"].append(
            "variant stop_name evidence is labelled and is not an exact-name assertion"
        )
    return result


@dataclass
class DownloadResult:
    http_status: Optional[int]
    download_bytes: int
    elapsed_seconds: float
    temporary_directory: Optional[str]
    temporary_path: Optional[str]
    error: Optional[str]


def make_private_download_directory() -> Path:
    preferred = Path("/private/tmp")
    if not preferred.is_dir() or not os.access(str(preferred), os.W_OK):
        raise OSError("/private/tmp is not available and downloads are restricted to /private/tmp")
    directory = Path(
        tempfile.mkdtemp(
            prefix="cologne-transit-gtfsde-static-",
            dir=str(preferred),
        )
    )
    os.chmod(str(directory), 0o700)
    return directory


def download_static_feed() -> DownloadResult:
    started = time.monotonic()
    directory: Optional[Path] = None
    path: Optional[Path] = None
    try:
        directory = make_private_download_directory()
        path = directory / "gtfsde-nv-latest.zip"
        request = urllib.request.Request(
            STATIC_URL,
            method="GET",
            headers={
                "Accept": "application/zip, application/octet-stream",
                "User-Agent": "cologne-transit-gtfsde-static-identity-bridge/1.0",
            },
        )
        opener = urllib.request.build_opener(NoRedirectHandler())
        with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status = int(response.getcode())
            file_descriptor = os.open(
                str(path),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            byte_count = 0
            try:
                with os.fdopen(file_descriptor, "wb") as output:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        byte_count += len(chunk)
                os.chmod(str(path), 0o600)
            except Exception:
                try:
                    os.close(file_descriptor)
                except OSError:
                    pass
                raise
        if status != 200:
            return DownloadResult(
                status,
                byte_count,
                time.monotonic() - started,
                str(directory),
                str(path),
                "HTTP status %s" % status,
            )
        return DownloadResult(
            status,
            byte_count,
            time.monotonic() - started,
            str(directory),
            str(path),
            None,
        )
    except urllib.error.HTTPError as error:
        status = int(error.code) if error.code is not None else None
        return DownloadResult(
            status,
            0,
            time.monotonic() - started,
            str(directory) if directory else None,
            str(path) if path else None,
            clean_error(error),
        )
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, ValueError) as error:
        return DownloadResult(
            None,
            0,
            time.monotonic() - started,
            str(directory) if directory else None,
            str(path) if path else None,
            clean_error(error),
        )


def descriptor_is_repeated(field: Any) -> bool:
    is_repeated = getattr(field, "is_repeated", None)
    if is_repeated is not None:
        return bool(is_repeated)
    return False


def field_descriptor(message: Any, field_name: str) -> Any:
    descriptor = getattr(message, "DESCRIPTOR", None)
    if descriptor is None:
        return None
    return descriptor.fields_by_name.get(field_name)


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
    return value or None


def read_realtime_body() -> Tuple[Optional[int], int, Optional[bytes], Optional[str]]:
    request = urllib.request.Request(
        REALTIME_URL,
        method="GET",
        headers={
            "Accept": "application/x-protobuf, application/octet-stream",
            "User-Agent": "cologne-transit-gtfsde-static-identity-bridge/1.0",
        },
    )
    opener = urllib.request.build_opener(NoRedirectHandler())
    try:
        with opener.open(request, timeout=REALTIME_TIMEOUT_SECONDS) as response:
            status = int(response.getcode())
            body = bytearray()
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > MAX_REALTIME_BYTES:
                    return status, len(body), None, "realtime response exceeded bounded byte limit"
            if status != 200:
                return status, len(body), None, "HTTP status %s" % status
            return status, len(body), bytes(body), None
    except urllib.error.HTTPError as error:
        return (
            int(error.code) if error.code is not None else None,
            0,
            None,
            clean_error(error),
        )
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, ValueError) as error:
        return None, 0, None, clean_error(error)


def verify_realtime_ids(
    station_results: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    try:
        from google.transit import gtfs_realtime_pb2
    except ImportError as error:
        gtfs_realtime_pb2 = None
        binding_error = "GTFS-Realtime bindings unavailable: %s" % clean_error(error)
    else:
        binding_error = None

    http_status, response_bytes, body, fetch_error = read_realtime_body()
    report: Dict[str, Any] = {
        "source_url": REALTIME_URL,
        "http_status": http_status,
        "response_bytes": response_bytes,
        "protobuf_parse_success": False,
        "protobuf_parse_error": None,
        "error": fetch_error or binding_error,
        "stations": {},
    }
    if body is None:
        return report
    if gtfs_realtime_pb2 is None:
        report["protobuf_parse_error"] = binding_error
        return report

    try:
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(body)
    except Exception as error:  # protobuf implementations expose different decode exceptions
        report["protobuf_parse_error"] = clean_error(error)
        return report

    report["protobuf_parse_success"] = True
    for station_result in station_results:
        station_key = station_result["station_key"]
        resolved_ids = set(station_result.get("resolved_static_stop_ids", []))
        matched_ids: Set[str] = set()
        matching_trip_updates: Set[int] = set()
        exact_match_count = 0
        for entity_index, entity in enumerate(get_repeated(feed, "entity")):
            trip_update = get_message(entity, "trip_update")
            if trip_update is None:
                continue
            entity_match = False
            for stop_update in get_repeated(trip_update, "stop_time_update"):
                stop_id = text_field(stop_update, "stop_id")
                if stop_id in resolved_ids:
                    exact_match_count += 1
                    matched_ids.add(str(stop_id))
                    entity_match = True
            if entity_match:
                matching_trip_updates.add(entity_index)
        report["stations"][station_key] = {
            "resolved_static_stop_ids": sorted(resolved_ids),
            "realtime_exact_match_count": exact_match_count,
            "matched_realtime_stop_id_examples": bounded_examples(matched_ids),
            "matching_trip_update_count": len(matching_trip_updates),
        }
    return report


def per_station_classification(
    station_result: Dict[str, Any],
    verification: Optional[Dict[str, Any]],
) -> str:
    static_classification = station_result["classification"]
    if static_classification == "STATION_MAPPING_AMBIGUOUS":
        return static_classification
    if static_classification == "STATION_MAPPING_NOT_FOUND":
        return static_classification
    if static_classification != "STATION_MAPPING_RESOLVED":
        return "INCONCLUSIVE"
    if verification is None:
        return "STATION_MAPPING_RESOLVED_RT_NOT_CHECKED"
    if not verification.get("protobuf_parse_success"):
        return "INCONCLUSIVE"
    station_verification = verification.get("stations", {}).get(
        station_result["station_key"],
        {},
    )
    if station_verification.get("realtime_exact_match_count", 0) > 0:
        return "STATION_MAPPING_RESOLVED_AND_RT_CONFIRMED"
    return "STATION_MAPPING_RESOLVED_RT_NOT_OBSERVED"


def aggregate_classification(station_results: Sequence[Dict[str, Any]]) -> str:
    classifications = [result["classification"] for result in station_results]
    if any(classification == "INCONCLUSIVE" for classification in classifications):
        return "INCONCLUSIVE"
    confirmed = sum(
        classification == "STATION_MAPPING_RESOLVED_AND_RT_CONFIRMED"
        for classification in classifications
    )
    resolved = sum(
        classification.startswith("STATION_MAPPING_RESOLVED")
        for classification in classifications
    )
    if confirmed == len(classifications):
        return "GTFSDE_IDENTITY_BRIDGE_CONFIRMED"
    if all(
        classification == "STATION_MAPPING_RESOLVED_RT_NOT_CHECKED"
        for classification in classifications
    ):
        return "GTFSDE_STATIC_MAPPING_RESOLVED_RT_PENDING"
    if all(
        classification == "STATION_MAPPING_RESOLVED_RT_NOT_OBSERVED"
        for classification in classifications
    ):
        return "GTFSDE_IDENTITY_BRIDGE_NOT_ESTABLISHED"
    if resolved > 0:
        return "GTFSDE_IDENTITY_BRIDGE_PARTIAL"
    return "GTFSDE_IDENTITY_BRIDGE_NOT_ESTABLISHED"


def inspect_zip(
    path: Path,
    station_specs: Sequence[StationSpec] = STATIONS,
    diagnostic_candidates: bool = False,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    try:
        with zipfile.ZipFile(str(path), "r") as archive:
            results = [
                resolve_station(
                    archive,
                    station,
                    diagnostic_candidates=diagnostic_candidates,
                )
                for station in station_specs
            ]
        return results, None
    except (OSError, zipfile.BadZipFile, UnicodeError, csv.Error, ValueError, FileNotFoundError) as error:
        return [], clean_error(error)


def json_line(value: Dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Host-run-only GTFS.de static/realtime identity bridge audit."
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)

    local_parser = subparsers.add_parser(
        "inspect-local",
        help="inspect an existing local GTFS.de static ZIP without network access",
    )
    local_parser.add_argument(
        "--gtfs-zip",
        required=True,
        type=Path,
        help="existing GTFS.de static ZIP; it is read but never modified",
    )
    local_parser.add_argument(
        "--verify-realtime",
        action="store_true",
        help="after local inspection, make exactly one current-feed GET",
    )
    local_parser.add_argument(
        "--station",
        choices=("all", "hbf", "neumarkt"),
        default="all",
        help="limit local inspection to one configured station (default: all)",
    )
    local_parser.add_argument(
        "--diagnostic-candidates",
        action="store_true",
        help=(
            "run bounded Köln Hbf candidate discovery when exact/variant "
            "matching fails; local-only and never combined with realtime verification"
        ),
    )

    download_parser = subparsers.add_parser(
        "download-and-inspect",
        help="download the current large GTFS.de static ZIP once, then inspect it",
    )
    download_parser.add_argument(
        "--verify-realtime",
        action="store_true",
        help="after static inspection, make exactly one current-feed GET",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.mode == "inspect-local":
        if args.diagnostic_candidates and args.verify_realtime:
            parser.error(
                "--diagnostic-candidates is local-only and cannot be combined "
                "with --verify-realtime"
            )
        if args.diagnostic_candidates and args.station == "neumarkt":
            parser.error(
                "--diagnostic-candidates applies only to the Köln Hbf station"
            )

    station_specs = STATIONS
    if args.mode == "inspect-local" and args.station != "all":
        station_specs = tuple(
            station for station in STATIONS if station.key == args.station
        )

    download: Optional[DownloadResult] = None
    if args.mode == "inspect-local":
        static_path = args.gtfs_zip
        if not static_path.is_file():
            json_line(
                {
                    "probe_name": PROBE_NAME,
                    "mode": args.mode,
                    "static_zip_path": str(static_path),
                    "aggregate_classification": "INCONCLUSIVE",
                    "error": "local GTFS ZIP not found",
                }
            )
            return 1
    else:
        download = download_static_feed()
        static_path = Path(download.temporary_path) if download.temporary_path else None
        if download.error or static_path is None:
            json_line(
                {
                    "probe_name": PROBE_NAME,
                    "mode": args.mode,
                    "source_url": STATIC_URL,
                    "http_status": download.http_status,
                    "download_bytes": download.download_bytes,
                    "elapsed_seconds": round(download.elapsed_seconds, 3),
                    "temporary_directory": download.temporary_directory,
                    "temporary_path": download.temporary_path,
                    "aggregate_classification": "INCONCLUSIVE",
                    "error": download.error,
                }
            )
            return 1

    station_results, inspection_error = inspect_zip(
        static_path,
        station_specs=station_specs,
        diagnostic_candidates=(
            args.diagnostic_candidates
            if args.mode == "inspect-local"
            else False
        ),
    )
    if inspection_error is not None:
        json_line(
            {
                "probe_name": PROBE_NAME,
                "mode": args.mode,
                "static_zip_path": str(static_path),
                "temporary_directory": download.temporary_directory if download else None,
                "aggregate_classification": "INCONCLUSIVE",
                "error": inspection_error,
            }
        )
        return 1

    verification = verify_realtime_ids(station_results) if args.verify_realtime else None
    for result in station_results:
        result["static_mapping_classification"] = result["classification"]
        result["classification"] = per_station_classification(result, verification)

    output: Dict[str, Any] = {
        "probe_name": PROBE_NAME,
        "mode": args.mode,
        "source_static_url": STATIC_URL,
        "static_zip_path": str(static_path),
        "station_filter": args.station if args.mode == "inspect-local" else "all",
        "diagnostic_candidates_requested": bool(
            args.diagnostic_candidates
            if args.mode == "inspect-local"
            else False
        ),
        "temporary_directory": download.temporary_directory if download else None,
        "download": (
            {
                "http_status": download.http_status,
                "download_bytes": download.download_bytes,
                "elapsed_seconds": round(download.elapsed_seconds, 3),
                "temporary_path": download.temporary_path,
            }
            if download
            else None
        ),
        "realtime_verification_requested": bool(args.verify_realtime),
        "realtime_verification": verification,
        "stations": station_results,
        "aggregate_classification": aggregate_classification(station_results),
        "interpretation": (
            "GTFS.de static identity evidence is kept separate from the project's "
            "VRS/go.Rheinland static feed; no warehouse integration or interchangeability "
            "claim is made."
        ),
    }
    json_line(output)
    if verification is not None and not verification.get("protobuf_parse_success"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
