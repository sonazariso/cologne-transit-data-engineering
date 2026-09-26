#!/usr/bin/env python3
# HOST-RUN ONLY for network-enabled operations.
# Local inspect modes read only a caller-supplied ZIP and make no requests.
# This script may download a large public static GTFS ZIP when explicitly asked.
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
import math
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
HBF_RAIL_RADIUS_METERS = 600
MAX_HBF_RAIL_CANDIDATES = 1000
MAX_RAIL_ROUTE_EXAMPLES = 20
KNOWN_ADJACENT_KVB_PARENT_IDS = ("106448", "437741")
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


def iter_csv_rows(
    archive: zipfile.ZipFile,
    filename: str,
) -> Iterable[Dict[str, str]]:
    """Stream one GTFS CSV member without extracting or materializing it."""
    member = archive_member(archive, filename)
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


def iter_stop_rows(archive: zipfile.ZipFile) -> Iterable[Dict[str, str]]:
    yield from iter_csv_rows(archive, "stops.txt")


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


def rail_stop_source(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return the bounded stop fields used by the Hbf rail diagnostic."""
    if row is None:
        return None
    return {
        "stop_id": row.get("stop_id"),
        "stop_name": row.get("stop_name"),
        "location_type": row.get("location_type"),
        "parent_station": row.get("parent_station"),
        "lat": row.get("stop_lat"),
        "lon": row.get("stop_lon"),
        "platform_code": row.get("platform_code"),
    }


def numeric_value(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value.strip())
    except (AttributeError, TypeError, ValueError):
        return None


def haversine_distance_meters(
    latitude_a: Optional[str],
    longitude_a: Optional[str],
    latitude_b: Optional[str],
    longitude_b: Optional[str],
) -> Optional[float]:
    """Calculate a supporting distance without using any external geocoder."""
    lat_a = numeric_value(latitude_a)
    lon_a = numeric_value(longitude_a)
    lat_b = numeric_value(latitude_b)
    lon_b = numeric_value(longitude_b)
    if None in (lat_a, lon_a, lat_b, lon_b):
        return None
    earth_radius_meters = 6371000.0
    lat_a_rad = math.radians(lat_a)
    lat_b_rad = math.radians(lat_b)
    delta_lat = math.radians(lat_b - lat_a)
    delta_lon = math.radians(lon_b - lon_a)
    haversine = (
        math.sin(delta_lat / 2.0) ** 2
        + math.cos(lat_a_rad)
        * math.cos(lat_b_rad)
        * math.sin(delta_lon / 2.0) ** 2
    )
    return 2.0 * earth_radius_meters * math.asin(math.sqrt(haversine))


def parse_route_type(value: Optional[str]) -> Any:
    raw_value = value.strip() if value is not None else ""
    if not raw_value:
        return None
    try:
        return int(raw_value)
    except ValueError:
        return raw_value


def route_source(row: Optional[Dict[str, str]], route_id: Optional[str] = None) -> Dict[str, Any]:
    if row is None:
        return {
            "route_id": route_id,
            "route_short_name": None,
            "route_long_name": None,
            "route_type": None,
            "agency_id": None,
        }
    return {
        "route_id": optional_text(row, "route_id") or route_id,
        "route_short_name": optional_text(row, "route_short_name"),
        "route_long_name": optional_text(row, "route_long_name"),
        "route_type": parse_route_type(optional_text(row, "route_type")),
        "agency_id": optional_text(row, "agency_id"),
    }


def route_type_key(route_type: Any) -> str:
    return "null" if route_type is None else str(route_type)


def route_type_number(route_type: Any) -> Optional[int]:
    if isinstance(route_type, bool):
        return None
    if isinstance(route_type, int):
        return route_type
    try:
        return int(str(route_type))
    except (TypeError, ValueError):
        return None


def route_label_modes(route: Dict[str, Any]) -> Set[str]:
    """Use conservative source-label evidence for S-Bahn/RE/RB modes."""
    short_name = normalize_text(route.get("route_short_name"))
    long_name = normalize_text(route.get("route_long_name"))
    label_text = " ".join(value for value in (short_name, long_name) if value)
    modes: Set[str] = set()
    if (
        re.search(r"(?:^|[^a-z])s[- ]?bahn(?:[^a-z]|$)", label_text)
        or re.fullmatch(r"s[- ]?\d{1,3}", short_name)
    ):
        modes.add("S-Bahn")
    if (
        re.search(
            r"(?:^|[^a-z])(?:re|rb)[- ]?\d{1,3}[a-z]?(?:[^a-z]|$)",
            short_name,
        )
        or re.search(r"(?:^|[^a-z])(?:regionalbahn|regional express)(?:[^a-z]|$)", label_text)
    ):
        modes.add("Regional rail")

    route_type = route_type_number(route.get("route_type"))
    if route_type == 109:
        modes.add("S-Bahn")
    elif route_type in (103, 106, 110, 111):
        modes.add("Regional rail")
    return modes


def route_is_rail(route: Dict[str, Any]) -> bool:
    route_type = route_type_number(route.get("route_type"))
    if route_type == 2 or (route_type is not None and 100 <= route_type <= 199):
        return True
    return bool(route_label_modes(route))


def route_service_classification(route: Dict[str, Any]) -> str:
    if route_is_rail(route):
        modes = route_label_modes(route)
        if modes:
            return "/".join(sorted(modes))
        return "Rail"

    route_type = route_type_number(route.get("route_type"))
    short_name = normalize_text(route.get("route_short_name"))
    if route_type == 3:
        return "Bus"
    if route_type == 0 or short_name in {"5", "16", "18"}:
        return "Stadtbahn"
    if route_type == 1:
        return "Subway"
    if route_type == 4:
        return "Ferry"
    return "Other non-rail"


def route_rail_evidence(route: Dict[str, Any]) -> List[str]:
    evidence: List[str] = []
    route_type = route_type_number(route.get("route_type"))
    if route_type == 2:
        evidence.append("GTFS route_type 2")
    elif route_type is not None and 100 <= route_type <= 199:
        evidence.append("GTFS extended rail route_type %s" % route_type)
    if "S-Bahn" in route_label_modes(route):
        evidence.append("source route label indicates S-Bahn")
    if "Regional rail" in route_label_modes(route):
        evidence.append("source route label indicates RE/RB/regional rail")
    if not evidence and route_is_rail(route):
        evidence.append("source route data indicates rail")
    return evidence


def collect_hbf_rail_stop_population(
    archive: zipfile.ZipFile,
) -> Dict[str, Any]:
    """Find bounded Hbf-area stops and enrich them with parent/child topology."""
    known_parent_rows: Dict[str, Dict[str, Any]] = {}
    for row in iter_stop_rows(archive):
        stop_id = row_stop_id(row)
        if stop_id in KNOWN_ADJACENT_KVB_PARENT_IDS:
            known_parent_rows[stop_id] = stop_row(row)

    anchors = [
        known_parent_rows[stop_id]
        for stop_id in KNOWN_ADJACENT_KVB_PARENT_IDS
        if stop_id in known_parent_rows
    ]
    candidate_rows_by_id: Dict[str, Dict[str, Any]] = {}
    candidate_reasons: Dict[str, Set[str]] = defaultdict(set)
    candidate_distances: Dict[str, float] = {}
    name_candidate_ids: Set[str] = set()

    for row in iter_stop_rows(archive):
        stop_id = row_stop_id(row)
        if stop_id is None:
            continue
        name_match = hbf_match_reason(row.get("stop_name"))
        if name_match is not None:
            candidate_rows_by_id[stop_id] = stop_row(row)
            candidate_reasons[stop_id].add("name: %s" % name_match)
            name_candidate_ids.add(stop_id)

        distances = [
            haversine_distance_meters(
                row.get("stop_lat"),
                row.get("stop_lon"),
                anchor.get("stop_lat"),
                anchor.get("stop_lon"),
            )
            for anchor in anchors
        ]
        valid_distances = [distance for distance in distances if distance is not None]
        if valid_distances:
            nearest_distance = min(valid_distances)
            if nearest_distance <= HBF_RAIL_RADIUS_METERS:
                candidate_rows_by_id[stop_id] = stop_row(row)
                candidate_reasons[stop_id].add(
                    "within_%dm_of_known_kvb_parent" % HBF_RAIL_RADIUS_METERS
                )
                candidate_distances[stop_id] = nearest_distance

    candidate_total_before_limit = len(candidate_rows_by_id)
    ordered_candidate_ids = sorted(
        candidate_rows_by_id,
        key=lambda stop_id: (0 if stop_id in name_candidate_ids else 1, stop_id),
    )
    initial_candidate_ids = ordered_candidate_ids[:MAX_HBF_RAIL_CANDIDATES]
    candidate_population_truncated = (
        candidate_total_before_limit > len(initial_candidate_ids)
    )
    candidate_rows_by_id = {
        stop_id: candidate_rows_by_id[stop_id]
        for stop_id in initial_candidate_ids
    }

    parent_ids_to_inspect: Set[str] = set()
    for stop_id in initial_candidate_ids:
        row = candidate_rows_by_id[stop_id]
        parent_id = row.get("parent_station")
        if parent_id:
            parent_ids_to_inspect.add(str(parent_id))
        if location_type_is_station(row):
            parent_ids_to_inspect.add(stop_id)

    parent_rows: Dict[str, Dict[str, Any]] = {}
    children_by_parent: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in iter_stop_rows(archive):
        stop_id = row_stop_id(row)
        parent_id = row_parent_station(row)
        if stop_id in parent_ids_to_inspect and stop_id is not None:
            parent_rows[stop_id] = stop_row(row)
        if parent_id in parent_ids_to_inspect and stop_id is not None:
            children_by_parent[parent_id].append(stop_row(row))

    # Parent topology is itself a bounded candidate-discovery signal. Include
    # actual parent rows in the candidate report, while keeping missing source
    # parents visible in the parent summaries below.
    for parent_id, parent_row in parent_rows.items():
        if parent_id not in candidate_rows_by_id:
            candidate_rows_by_id[parent_id] = parent_row
            candidate_reasons[parent_id].add("parent_of_area_candidate_stop")

    candidate_ids = sorted(candidate_rows_by_id)
    candidate_rows = [candidate_rows_by_id[stop_id] for stop_id in candidate_ids]
    return {
        "known_parent_rows": known_parent_rows,
        "anchors": anchors,
        "candidate_rows": candidate_rows,
        "candidate_reasons": {
            stop_id: sorted(candidate_reasons.get(stop_id, set()))
            for stop_id in candidate_ids
        },
        "candidate_distances": {
            stop_id: round(candidate_distances[stop_id], 3)
            for stop_id in candidate_ids
            if stop_id in candidate_distances
        },
        "candidate_total_before_limit": candidate_total_before_limit,
        "candidate_population_truncated": candidate_population_truncated,
        "parent_rows": parent_rows,
        "children_by_parent": {
            parent_id: unique_rows(children)
            for parent_id, children in children_by_parent.items()
        },
    }


def route_default(route_id: str) -> Dict[str, Any]:
    return route_source(None, route_id=route_id)


def agency_source(row: Optional[Dict[str, str]], agency_id: Optional[str]) -> Dict[str, Any]:
    if row is None:
        return {"agency_id": agency_id, "agency_name": None}
    return {
        "agency_id": optional_text(row, "agency_id") or agency_id,
        "agency_name": optional_text(row, "agency_name"),
    }


def route_detail(
    route_id: str,
    route_rows: Dict[str, Dict[str, Any]],
    agency_rows: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    route = route_rows.get(route_id, route_default(route_id))
    detail = dict(route)
    detail["service_classification"] = route_service_classification(route)
    detail["is_rail"] = route_is_rail(route)
    detail["rail_evidence"] = route_rail_evidence(route)
    agency_id = route.get("agency_id")
    detail["agency"] = agency_source(
        agency_rows.get(agency_id) if agency_id else None,
        agency_id,
    )
    return detail


def collect_hbf_rail_service_evidence(
    archive: zipfile.ZipFile,
    stop_ids: Sequence[str],
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Dict[str, Dict[str, Any]],
    Dict[str, Dict[str, Any]],
]:
    """Join stop_times, trips, routes, and agency for bounded stop IDs."""
    wanted_stop_ids = set(stop_ids)
    service_by_stop: Dict[str, Dict[str, Any]] = {
        stop_id: {"number_of_stop_time_references": 0, "trip_ids": set()}
        for stop_id in wanted_stop_ids
    }
    for row in iter_csv_rows(archive, "stop_times.txt"):
        stop_id = optional_text(row, "stop_id")
        if stop_id not in wanted_stop_ids:
            continue
        service = service_by_stop[stop_id]
        service["number_of_stop_time_references"] += 1
        trip_id = optional_text(row, "trip_id")
        if trip_id is not None:
            service["trip_ids"].add(trip_id)

    relevant_trip_ids = {
        trip_id
        for service in service_by_stop.values()
        for trip_id in service["trip_ids"]
    }
    route_by_trip: Dict[str, str] = {}
    for row in iter_csv_rows(archive, "trips.txt"):
        trip_id = optional_text(row, "trip_id")
        if trip_id not in relevant_trip_ids:
            continue
        route_id = optional_text(row, "route_id")
        if route_id is not None:
            route_by_trip[trip_id] = route_id

    route_ids: Set[str] = set()
    for service in service_by_stop.values():
        service["route_ids"] = {
            route_by_trip[trip_id]
            for trip_id in service["trip_ids"]
            if trip_id in route_by_trip
        }
        route_ids.update(service["route_ids"])

    route_rows: Dict[str, Dict[str, Any]] = {}
    for row in iter_csv_rows(archive, "routes.txt"):
        route_id = optional_text(row, "route_id")
        if route_id in route_ids:
            route_rows[route_id] = route_source(row, route_id=route_id)

    agency_ids = {
        route.get("agency_id")
        for route in route_rows.values()
        if route.get("agency_id")
    }
    agency_rows: Dict[str, Dict[str, Any]] = {}
    for row in iter_csv_rows(archive, "agency.txt"):
        agency_id = optional_text(row, "agency_id")
        if agency_id in agency_ids:
            agency_rows[agency_id] = agency_source(row, agency_id)

    return service_by_stop, route_rows, agency_rows


def service_summary(
    stop_ids: Sequence[str],
    service_by_stop: Dict[str, Dict[str, Any]],
    route_rows: Dict[str, Dict[str, Any]],
    agency_rows: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    selected_stop_ids = [stop_id for stop_id in stop_ids if stop_id in service_by_stop]
    number_of_references = sum(
        service_by_stop[stop_id].get("number_of_stop_time_references", 0)
        for stop_id in selected_stop_ids
    )
    trip_ids = {
        trip_id
        for stop_id in selected_stop_ids
        for trip_id in service_by_stop[stop_id].get("trip_ids", set())
    }
    route_ids = {
        route_id
        for stop_id in selected_stop_ids
        for route_id in service_by_stop[stop_id].get("route_ids", set())
    }
    route_details = [
        route_detail(route_id, route_rows, agency_rows)
        for route_id in sorted(route_ids)
    ]
    rail_route_details = [route for route in route_details if route["is_rail"]]
    route_type_counts: Dict[str, int] = defaultdict(int)
    service_classification_counts: Dict[str, int] = defaultdict(int)
    for route in route_details:
        route_type_counts[route_type_key(route.get("route_type"))] += 1
        service_classification_counts[route["service_classification"]] += 1

    agency_by_id: Dict[str, Dict[str, Any]] = {}
    for route in route_details:
        agency = route["agency"]
        agency_id = agency.get("agency_id")
        if agency_id is not None:
            agency_by_id.setdefault(str(agency_id), agency)

    rail_modes = sorted(
        {
            mode
            for route in rail_route_details
            for mode in route_label_modes(route)
        }
    )
    if rail_route_details and not rail_modes:
        rail_modes = ["Rail"]

    return {
        "number_of_stop_time_references": number_of_references,
        "unique_trip_count": len(trip_ids),
        "unique_route_count": len(route_ids),
        "route_type_counts": dict(sorted(route_type_counts.items())),
        "service_classification_counts": dict(
            sorted(service_classification_counts.items())
        ),
        "route_examples": route_details[:MAX_RAIL_ROUTE_EXAMPLES],
        "rail_route_examples": rail_route_details[:MAX_RAIL_ROUTE_EXAMPLES],
        "agency_examples": [
            agency_by_id[agency_id]
            for agency_id in sorted(agency_by_id)[:MAX_RAIL_ROUTE_EXAMPLES]
        ],
        "rail_route_ids": [route["route_id"] for route in rail_route_details],
        "rail_modes_observed": rail_modes,
        "is_rail_served": bool(rail_route_details),
    }


def candidate_with_service(
    row: Dict[str, Any],
    candidate_reasons: Sequence[str],
    distance_meters: Optional[float],
    service: Dict[str, Any],
) -> Dict[str, Any]:
    candidate = rail_stop_source(row)
    if candidate is None:
        candidate = {
            "stop_id": row.get("stop_id"),
            "stop_name": None,
            "location_type": None,
            "parent_station": None,
            "lat": None,
            "lon": None,
            "platform_code": None,
        }
    candidate.update(
        {
            "candidate_reasons": list(candidate_reasons),
            "distance_to_nearest_known_kvb_parent_meters": distance_meters,
            **service,
            "excluded_known_adjacent_kvb_parent": (
                candidate.get("stop_id") in KNOWN_ADJACENT_KVB_PARENT_IDS
                or candidate.get("parent_station") in KNOWN_ADJACENT_KVB_PARENT_IDS
            ),
        }
    )
    return candidate


def parent_placeholder(parent_id: str) -> Dict[str, Any]:
    return {
        "stop_id": parent_id,
        "stop_name": None,
        "location_type": None,
        "parent_station": None,
        "lat": None,
        "lon": None,
        "platform_code": None,
    }


def build_rail_parent_summary(
    parent_id: str,
    parent_row: Optional[Dict[str, Any]],
    child_rows: Sequence[Dict[str, Any]],
    service_by_stop: Dict[str, Dict[str, Any]],
    route_rows: Dict[str, Dict[str, Any]],
    agency_rows: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    children = unique_rows(child_rows)
    child_ids = [str(row["stop_id"]) for row in children if row.get("stop_id")]
    child_service = {
        stop_id: service_summary(
            [stop_id],
            service_by_stop,
            route_rows,
            agency_rows,
        )
        for stop_id in child_ids
    }
    rail_child_ids = [
        stop_id
        for stop_id in child_ids
        if child_service[stop_id]["is_rail_served"]
    ]
    non_rail_child_ids = [
        stop_id
        for stop_id in child_ids
        if not child_service[stop_id]["is_rail_served"]
        and child_service[stop_id]["unique_route_count"] > 0
    ]
    unserved_child_ids = [
        stop_id
        for stop_id in child_ids
        if child_service[stop_id]["unique_route_count"] == 0
    ]
    aggregate_ids = child_ids or [parent_id]
    aggregate_service = service_summary(
        aggregate_ids,
        service_by_stop,
        route_rows,
        agency_rows,
    )
    child_stop_details = []
    for row in children:
        stop_id = row.get("stop_id")
        detail = rail_stop_source(row)
        if detail is None:
            continue
        detail.update(child_service[str(stop_id)])
        child_stop_details.append(detail)

    rail_route_ids = aggregate_service["rail_route_ids"]
    rail_route_details = [
        route_detail(route_id, route_rows, agency_rows)
        for route_id in rail_route_ids[:MAX_RAIL_ROUTE_EXAMPLES]
    ]
    rail_route_type_counts: Dict[str, int] = defaultdict(int)
    rail_agencies: Dict[str, Dict[str, Any]] = {}
    for route_id in rail_route_ids:
        route = route_rows.get(route_id, route_default(route_id))
        rail_route_type_counts[route_type_key(route.get("route_type"))] += 1
        agency_id = route.get("agency_id")
        if agency_id is not None:
            rail_agencies.setdefault(
                str(agency_id),
                agency_source(agency_rows.get(agency_id), agency_id),
            )
    return {
        "parent_stop": rail_stop_source(parent_row) or parent_placeholder(parent_id),
        "parent_is_location_type_1": location_type_is_station(parent_row or {}),
        "child_stops": child_stop_details,
        "rail_served_child_ids": rail_child_ids,
        "non_rail_child_ids": non_rail_child_ids,
        "unserved_child_ids": unserved_child_ids,
        "route_type_counts": aggregate_service["route_type_counts"],
        "rail_route_type_counts": dict(sorted(rail_route_type_counts.items())),
        "route_label_examples": rail_route_details,
        "agency_examples": [
            rail_agencies[agency_id]
            for agency_id in sorted(rail_agencies)[:MAX_RAIL_ROUTE_EXAMPLES]
        ],
        "rail_route_ids": rail_route_ids,
        "rail_modes_observed": aggregate_service["rail_modes_observed"],
        "number_of_stop_time_references": aggregate_service[
            "number_of_stop_time_references"
        ],
        "unique_trip_count": aggregate_service["unique_trip_count"],
        "unique_route_count": aggregate_service["unique_route_count"],
        "is_known_adjacent_kvb_parent": parent_id in KNOWN_ADJACENT_KVB_PARENT_IDS,
    }


def inspect_hbf_rail_zip(archive: zipfile.ZipFile) -> Dict[str, Any]:
    """Run the local-only Hbf railway identity diagnostic."""
    population = collect_hbf_rail_stop_population(archive)
    candidate_rows = population["candidate_rows"]
    parent_rows = dict(population["parent_rows"])
    candidate_rows_by_id = {
        str(row["stop_id"]): row
        for row in candidate_rows
        if row.get("stop_id")
    }
    for stop_id, row in candidate_rows_by_id.items():
        parent_rows.setdefault(stop_id, row)

    children_by_parent = population["children_by_parent"]
    analysis_stop_ids = {
        str(row["stop_id"])
        for row in candidate_rows
        if row.get("stop_id")
    }
    for children in children_by_parent.values():
        analysis_stop_ids.update(
            str(row["stop_id"])
            for row in children
            if row.get("stop_id")
        )

    service_by_stop, route_rows, agency_rows = collect_hbf_rail_service_evidence(
        archive,
        sorted(analysis_stop_ids),
    )

    candidate_details = []
    rail_served_candidate_ids: List[str] = []
    for row in candidate_rows:
        stop_id = str(row["stop_id"])
        detail = candidate_with_service(
            row,
            population["candidate_reasons"].get(stop_id, []),
            population["candidate_distances"].get(stop_id),
            service_summary(
                [stop_id],
                service_by_stop,
                route_rows,
                agency_rows,
            ),
        )
        candidate_details.append(detail)
        if detail["is_rail_served"]:
            rail_served_candidate_ids.append(stop_id)

    # A rail-served child points to its parent station. An explicitly
    # station-typed/direct rail stop can stand in as its own parent only when
    # GTFS has no parent_station value; this is still service/topology evidence,
    # never coordinate-only selection.
    rail_parent_ids: Set[str] = set()
    rail_service_stop_ids: Set[str] = set()
    for stop_id, service in service_by_stop.items():
        if not service_summary(
            [stop_id],
            service_by_stop,
            route_rows,
            agency_rows,
        )["is_rail_served"]:
            continue
        row = candidate_rows_by_id.get(stop_id)
        if row is None:
            row = next(
                (
                    child
                    for children in children_by_parent.values()
                    for child in children
                    if child.get("stop_id") == stop_id
                ),
                None,
            )
        if row is None:
            continue
        rail_service_stop_ids.add(stop_id)
        parent_id = row.get("parent_station")
        rail_parent_ids.add(str(parent_id) if parent_id else stop_id)

    # A parent can be surfaced by a rail-served sibling even when the initial
    # area/name candidate itself has only Stadtbahn or bus service.
    for parent_id, children in children_by_parent.items():
        child_ids = [str(row["stop_id"]) for row in children if row.get("stop_id")]
        child_summary = service_summary(
            child_ids,
            service_by_stop,
            route_rows,
            agency_rows,
        )
        if child_summary["is_rail_served"]:
            rail_parent_ids.add(parent_id)

    excluded_parent_ids = sorted(
        rail_parent_ids.intersection(KNOWN_ADJACENT_KVB_PARENT_IDS)
    )
    plausible_parent_ids = sorted(
        rail_parent_ids.difference(KNOWN_ADJACENT_KVB_PARENT_IDS)
    )

    known_parent_summaries = [
        build_rail_parent_summary(
            parent_id,
            population["known_parent_rows"].get(parent_id)
            or parent_rows.get(parent_id),
            children_by_parent.get(parent_id, []),
            service_by_stop,
            route_rows,
            agency_rows,
        )
        for parent_id in KNOWN_ADJACENT_KVB_PARENT_IDS
    ]

    plausible_parent_summaries = [
        build_rail_parent_summary(
            parent_id,
            parent_rows.get(parent_id),
            children_by_parent.get(parent_id, []),
            service_by_stop,
            route_rows,
            agency_rows,
        )
        for parent_id in plausible_parent_ids
    ]

    excluded_parent_summaries = [
        build_rail_parent_summary(
            parent_id,
            population["known_parent_rows"].get(parent_id)
            or parent_rows.get(parent_id),
            children_by_parent.get(parent_id, []),
            service_by_stop,
            route_rows,
            agency_rows,
        )
        for parent_id in excluded_parent_ids
    ]

    if not candidate_details:
        classification = "HBF_RAIL_MAPPING_NOT_FOUND"
        classification_reason = (
            "no Köln Hbf-relevant name candidate or stop within the bounded "
            "central-station radius was found"
        )
    elif len(plausible_parent_summaries) > 1:
        classification = "HBF_RAIL_MAPPING_AMBIGUOUS"
        classification_reason = (
            "multiple non-excluded rail parent candidates remain; no parent was selected"
        )
    elif len(plausible_parent_summaries) == 1:
        selected_summary = plausible_parent_summaries[0]
        if population["candidate_population_truncated"]:
            classification = "HBF_RAIL_EVIDENCE_INSUFFICIENT"
            classification_reason = (
                "the bounded candidate population was truncated, so deterministic "
                "single-parent resolution is disabled"
            )
        elif not selected_summary["rail_served_child_ids"] and not (
            selected_summary["parent_stop"].get("stop_id") in rail_service_stop_ids
        ):
            classification = "HBF_RAIL_EVIDENCE_INSUFFICIENT"
            classification_reason = "the proposed parent has no retained rail-served stop"
        else:
            classification = "HBF_RAIL_MAPPING_RESOLVED"
            classification_reason = (
                "one non-excluded parent has source-backed rail service and consistent "
                "static parent/child topology"
            )
    elif rail_service_stop_ids or excluded_parent_summaries:
        classification = "HBF_RAIL_EVIDENCE_INSUFFICIENT"
        classification_reason = (
            "rail service evidence is present only on excluded adjacent KVB parents "
            "or lacks a non-excluded parent topology"
        )
    else:
        classification = "HBF_RAIL_EVIDENCE_INSUFFICIENT"
        classification_reason = (
            "bounded Hbf-area candidates were found, but no source-backed S-Bahn, "
            "regional-rail, or rail route evidence was associated with them"
        )

    selected_parent = (
        plausible_parent_summaries[0]
        if classification == "HBF_RAIL_MAPPING_RESOLVED"
        else None
    )
    report: Dict[str, Any] = {
        "diagnostic_mode": "HBF_RAIL_IDENTITY",
        "known_adjacent_kvb_parents": list(KNOWN_ADJACENT_KVB_PARENT_IDS),
        "known_adjacent_kvb_parent_details": [
            {
                "parent_stop": summary["parent_stop"],
                "child_stops": summary["child_stops"],
                "rail_served_child_ids": summary["rail_served_child_ids"],
                "non_rail_child_ids": summary["non_rail_child_ids"],
                "unserved_child_ids": summary["unserved_child_ids"],
                "route_type_counts": summary["route_type_counts"],
                "route_label_examples": summary["route_label_examples"],
                "agency_examples": summary["agency_examples"],
                "rail_modes_observed": summary["rail_modes_observed"],
            }
            for summary in known_parent_summaries
        ],
        "central_station_radius_meters": HBF_RAIL_RADIUS_METERS,
        "central_station_anchor_parents": [
            rail_stop_source(row) for row in population["anchors"]
        ],
        "coordinate_evidence": (
            "supporting only; rail identity requires stop_times/trips/routes "
            "service and parent/child topology"
        ),
        "candidate_count": len(candidate_details),
        "candidate_population_source_count": population["candidate_total_before_limit"],
        "candidate_population_truncated": population["candidate_population_truncated"],
        "candidates": candidate_details,
        "rail_served_candidate_count": len(rail_served_candidate_ids),
        "rail_served_candidate_ids": sorted(rail_served_candidate_ids),
        "excluded_known_adjacent_kvb_parent_ids_with_rail_evidence": excluded_parent_ids,
        "plausible_rail_parent_count": len(plausible_parent_summaries),
        "plausible_rail_parents": plausible_parent_summaries,
        "excluded_adjacent_kvb_parent_service_summaries": excluded_parent_summaries,
        "classification": classification,
        "classification_reason": classification_reason,
        "network_requests_made": 0,
    }
    if selected_parent is not None:
        selected_stop = selected_parent["parent_stop"]
        report.update(
            {
                "selected_rail_parent_station_id": selected_stop.get("stop_id"),
                "selected_rail_parent_stop_name": selected_stop.get("stop_name"),
                "resolved_rail_child_stop_ids": selected_parent[
                    "rail_served_child_ids"
                ],
                "rail_modes_observed": selected_parent["rail_modes_observed"],
                "route_examples": selected_parent["route_label_examples"],
            }
        )
    else:
        report.update(
            {
                "selected_rail_parent_station_id": None,
                "selected_rail_parent_stop_name": None,
                "resolved_rail_child_stop_ids": [],
                "rail_modes_observed": [],
                "route_examples": [],
            }
        )
    return report


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
    local_parser.add_argument(
        "--rail-diagnostic",
        action="store_true",
        help=(
            "run the bounded local-only Köln Hbf railway identity diagnostic "
            "through stops, stop_times, trips, routes, and agency"
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
        if args.rail_diagnostic and args.station != "hbf":
            parser.error("--rail-diagnostic requires --station hbf")
        if args.rail_diagnostic and args.verify_realtime:
            parser.error(
                "--rail-diagnostic is local-only and cannot be combined with "
                "--verify-realtime"
            )
        if args.rail_diagnostic and args.diagnostic_candidates:
            parser.error(
                "--rail-diagnostic cannot be combined with --diagnostic-candidates"
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
                    "diagnostic_mode": (
                        "HBF_RAIL_IDENTITY" if args.rail_diagnostic else None
                    ),
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

    if args.mode == "inspect-local" and args.rail_diagnostic:
        try:
            with zipfile.ZipFile(str(static_path), "r") as archive:
                rail_report = inspect_hbf_rail_zip(archive)
        except (
            OSError,
            zipfile.BadZipFile,
            UnicodeError,
            csv.Error,
            ValueError,
            FileNotFoundError,
            KeyError,
        ) as error:
            rail_report = {
                "diagnostic_mode": "HBF_RAIL_IDENTITY",
                "classification": "INCONCLUSIVE",
                "classification_reason": clean_error(error),
                "network_requests_made": 0,
                "error": clean_error(error),
            }
        rail_output = {
            "probe_name": PROBE_NAME,
            "mode": args.mode,
            "source_static_url": STATIC_URL,
            "static_zip_path": str(static_path),
            "station_filter": "hbf",
            "rail_diagnostic_requested": True,
            "realtime_verification_requested": False,
            "realtime_verification": None,
            "interpretation": (
                "GTFS.de static rail identity evidence is kept separate from the "
                "project's VRS/go.Rheinland static feed; known adjacent KVB parents "
                "are not promoted to railway Köln Hbf."
            ),
            **rail_report,
        }
        json_line(rail_output)
        return 0 if rail_report.get("classification") != "INCONCLUSIVE" else 1

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
