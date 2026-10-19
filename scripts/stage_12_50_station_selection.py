#!/usr/bin/env python3
"""Reproducible Stage 12 fixed 50-parent-station selection.

The selection path is intentionally static-data first.  It reads one local
official VRS Global-ID GTFS ZIP, derives parent-station metrics from the source
hierarchy and scheduled child-stop events, then applies a deterministic policy:

1. retain the seven Legacy7 continuity anchors;
2. cover any current Stage 4 core mode not already represented;
3. take one operationally strongest candidate from each new fixed geographic
   cell until the geographic-spread target is met; and
4. fill remaining seats by an exposed lexicographic operational-richness key.

No weighted score, station name lookup, operator lookup, route label lookup, or
coordinate-based Cologne identity rule is used.  Coordinates are used only
after source identity is established, for the fixed-cell diversity pass and
the concentration review.

The optional MDD path is a bounded compatibility diagnostic only.  It makes
one sequential request per candidate, uses the approved TRIAS StopEvent
construction from the Stage 8 module with NumberOfResults=1, retains no raw
response, and stops immediately on HTTP 401/403.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

from stage_04_cologne_scope_mode_audit import (  # noqa: E402
    COLOGNE_PREFIX,
    MODE_STATUSES,
    OFFICIAL_SOURCE_PAGE_URL,
    OFFICIAL_SOURCE_URL,
    differing_fields,
    normalized,
    preliminary_route_classification,
)


SCRIPT_VERSION = "stage-12-50-station-selection-1.1"
STAGE12_SCHEMA_VERSION = "stage12-50-station-selection-1.1"
CANDIDATE_UNIVERSE_SCHEMA_VERSION = "stage12-50-station-candidate-evidence-1.0"
CANDIDATE_UNIVERSE_ARTIFACT = Path(
    "docs/stages/12-50-station-candidate-evidence.json"
)
STAGE12_FEED_VERSION = "VERSION__20260930_0053"
STAGE12_ZIP_SHA256 = "288c26f23f91a2069078be82167cfa70eb5b2948e06cfeee9e23584910817147"
STAGE12_RETRIEVAL_TIMESTAMP_UTC = "2026-09-30T10:42:58Z"
STAGE12_CANDIDATE_COUNT = 864
PANEL_VERSION = "TIERED50_PANEL_V1"
ENDPOINT = "https://mdd.gorheinland.com/delfi"
MDD_API_KEY_ENV = "MDD_API_KEY"
MDD_MAX_HTTP_ATTEMPTS = 75
MDD_NORMAL_HTTP_ATTEMPTS = 50
MDD_NUMBER_OF_RESULTS = 1
MDD_STOP_EVENT_TYPE = "arrival"
MDD_INCLUDE_PREVIOUS_CALLS = True
MDD_INCLUDE_ONWARD_CALLS = True
MDD_INCLUDE_REALTIME_DATA = True
MDD_RETRIES = 0
MDD_CONCURRENCY = "sequential"
MDD_SAMPLING_POLICY = "STAGE12_FIXED_PANEL_COMPATIBILITY_V1"

# The cells are a transparent secondary diversity device, not a geographic
# identity rule.  They are approximately 2.8 km north/south by 2.8 km east/
# west in Cologne's latitude.  The target is deliberately capped so operational
# richness remains the main fill criterion.
GEOGRAPHIC_CELL_LAT_DEGREES = 0.025
GEOGRAPHIC_CELL_LON_DEGREES = 0.04
GEOGRAPHIC_SPREAD_TARGET = 25
NEARBY_REVIEW_RADIUS_KM = 1.2

CORE_MODES = tuple(
    mode for mode, status in sorted(MODE_STATUSES.items()) if status == "CORE_KPI"
)
ALL_MODES = tuple(sorted(MODE_STATUSES))

LEGACY7 = (
    ("Köln Heumarkt", "de:05315:11110"),
    ("Köln Hbf", "de:05315:11201"),
    ("Köln Rodenkirchen Bf", "de:05315:12711"),
    ("Köln Bf Ehrenfeld", "de:05315:14201"),
    ("Köln Worringen S-Bahn", "de:05315:16601"),
    ("Köln Porz Markt", "de:05315:17311"),
    ("Köln Bf Mülheim", "de:05315:19201"),
)
LEGACY7_IDS = tuple(stop_id for _name, stop_id in LEGACY7)


class Stage12Blocked(RuntimeError):
    """Raised when the source or bounded diagnostic cannot support approval."""


def clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def parse_float(value: Any) -> float | None:
    text = clean(value)
    if not text:
        return None
    try:
        result = float(text)
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def parse_gtfs_date(value: Any) -> dt.date | None:
    text = clean(value)
    if not re.fullmatch(r"\d{8}", text):
        return None
    try:
        return dt.date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    except ValueError:
        return None


def iso_utc_from_mtime(path: Path) -> str:
    stamp = dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.timezone.utc)
    return stamp.isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_csv_rows(archive: zipfile.ZipFile, filename: str) -> Iterator[dict[str, str]]:
    try:
        raw = archive.open(filename, "r")
    except KeyError as exc:
        raise Stage12Blocked(f"required feed file is missing: {filename}") from exc
    with raw:
        with io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as text:
            reader = csv.DictReader(text)
            if reader.fieldnames is None:
                raise Stage12Blocked(f"CSV file has no header: {filename}")
            for row in reader:
                if None in row:
                    raise Stage12Blocked(f"CSV row has extra fields: {filename}")
                yield {str(key): clean(value) for key, value in row.items()}


def csv_headers(archive: zipfile.ZipFile, filename: str) -> set[str]:
    with archive.open(filename, "r") as raw:
        with io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as text:
            reader = csv.reader(text)
            try:
                return {clean(value) for value in next(reader)}
            except StopIteration as exc:
                raise Stage12Blocked(f"CSV file has no header: {filename}") from exc


def csv_row_count(archive: zipfile.ZipFile, filename: str) -> int:
    return sum(1 for _row in iter_csv_rows(archive, filename))


def gtfs_date_range(start: dt.date, end: dt.date) -> Iterator[dt.date]:
    cursor = start
    while cursor <= end:
        yield cursor
        cursor += dt.timedelta(days=1)


def active_service_dates(
    archive: zipfile.ZipFile,
    feed_info: Mapping[str, str],
) -> tuple[dict[str, set[str]], dict[str, Any]]:
    """Derive active dates from calendar plus calendar_dates exceptions."""

    calendar_by_service: dict[str, set[str]] = defaultdict(set)
    calendar_row_count = 0
    calendar_invalid_count = 0
    calendar_date_values: list[dt.date] = []
    if "calendar.txt" in archive.namelist():
        for row in iter_csv_rows(archive, "calendar.txt"):
            calendar_row_count += 1
            service_id = clean(row.get("service_id"))
            start = parse_gtfs_date(row.get("start_date"))
            end = parse_gtfs_date(row.get("end_date"))
            if not service_id or not start or not end or end < start:
                calendar_invalid_count += 1
                continue
            flags = {
                0: clean(row.get("monday")) == "1",
                1: clean(row.get("tuesday")) == "1",
                2: clean(row.get("wednesday")) == "1",
                3: clean(row.get("thursday")) == "1",
                4: clean(row.get("friday")) == "1",
                5: clean(row.get("saturday")) == "1",
                6: clean(row.get("sunday")) == "1",
            }
            for date_value in gtfs_date_range(start, end):
                calendar_date_values.append(date_value)
                if flags[date_value.weekday()]:
                    calendar_by_service[service_id].add(date_value.isoformat())

    calendar_dates_row_count = 0
    calendar_dates_invalid_count = 0
    calendar_dates_by_service: dict[str, list[tuple[str, str]]] = defaultdict(list)
    if "calendar_dates.txt" in archive.namelist():
        for row in iter_csv_rows(archive, "calendar_dates.txt"):
            calendar_dates_row_count += 1
            service_id = clean(row.get("service_id"))
            date_value = parse_gtfs_date(row.get("date"))
            exception_type = clean(row.get("exception_type"))
            if not service_id or not date_value or exception_type not in {"1", "2"}:
                calendar_dates_invalid_count += 1
                continue
            date_text = date_value.isoformat()
            calendar_dates_by_service[service_id].append((date_text, exception_type))
            calendar_date_values.append(date_value)

    for service_id, exceptions in calendar_dates_by_service.items():
        dates = calendar_by_service.setdefault(service_id, set())
        for date_text, exception_type in exceptions:
            if exception_type == "1":
                dates.add(date_text)
            else:
                dates.discard(date_text)

    feed_start = parse_gtfs_date(feed_info.get("feed_start_date"))
    feed_end = parse_gtfs_date(feed_info.get("feed_end_date"))
    all_dates = [date_value for date_value in calendar_date_values if date_value]
    return dict(calendar_by_service), {
        "calendar_row_count": calendar_row_count,
        "calendar_dates_row_count": calendar_dates_row_count,
        "invalid_calendar_date_value_count": calendar_invalid_count + calendar_dates_invalid_count,
        "feed_start_date": clean(feed_info.get("feed_start_date")) or None,
        "feed_end_date": clean(feed_info.get("feed_end_date")) or None,
        "earliest_active_date": min(all_dates).isoformat() if all_dates else (
            feed_start.isoformat() if feed_start else None
        ),
        "latest_active_date": max(all_dates).isoformat() if all_dates else (
            feed_end.isoformat() if feed_end else None
        ),
    }


def resolve_route_and_trip_modes(
    archive: zipfile.ZipFile,
) -> tuple[
    dict[str, dict[str, str]],
    dict[str, dict[str, str]],
    dict[str, str],
    dict[str, Any],
]:
    """Reuse Stage 4 classifier semantics for route and trip inheritance."""

    agencies = list(iter_csv_rows(archive, "agency.txt"))
    agency_by_id: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in agencies:
        agency_by_id[clean(row.get("agency_id"))].append(row)
    kvb_ids = {
        clean(row.get("agency_id"))
        for row in agencies
        if clean(row.get("agency_id"))
        and (
            "KOLNER VERKEHRS BETRIEBE" in normalized(row.get("agency_name"))
            or (
                re.search(r"\bKVB\b", normalized(row.get("agency_name")))
                and "VERKEHRS" in normalized(row.get("agency_name"))
            )
        )
    }

    route_rows = list(iter_csv_rows(archive, "routes.txt"))
    routes_by_id: dict[str, list[dict[str, str]]] = defaultdict(list)
    route_modes: dict[str, str] = {}
    for row in route_rows:
        route_id = clean(row.get("route_id"))
        routes_by_id[route_id].append(row)
    route_semantic_fields = (
        "agency_id",
        "route_type",
        "route_short_name",
        "route_long_name",
        "route_desc",
    )
    for route_id, rows in routes_by_id.items():
        if not route_id:
            route_modes[route_id] = "UNCLASSIFIED_MODE"
            continue
        preliminary = [preliminary_route_classification(row, kvb_ids)[0] for row in rows]
        if len(rows) > 1 and differing_fields(rows, route_semantic_fields):
            route_modes[route_id] = "AMBIGUOUS_ROUTE_ID"
        else:
            route_modes[route_id] = preliminary[0] if preliminary else "UNCLASSIFIED_MODE"

    trip_rows = list(iter_csv_rows(archive, "trips.txt"))
    trips_by_id: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in trip_rows:
        trips_by_id[clean(row.get("trip_id"))].append(row)
    trip_modes: dict[str, str] = {}
    trip_details: dict[str, dict[str, str]] = {}
    trip_semantic_fields = (
        "route_id",
        "service_id",
        "trip_headsign",
        "direction_id",
        "block_id",
        "shape_id",
    )
    for trip_id, rows in trips_by_id.items():
        route_ids = sorted({clean(row.get("route_id")) for row in rows if clean(row.get("route_id"))})
        if len(rows) > 1 and (
            differing_fields(rows, trip_semantic_fields) or len(route_ids) > 1
        ):
            trip_modes[trip_id] = "AMBIGUOUS_TRIP_ID"
            continue
        route_id = route_ids[0] if route_ids else ""
        trip_modes[trip_id] = route_modes.get(route_id, "UNCLASSIFIED_MODE")
        if rows:
            trip_details[trip_id] = {
                "route_id": route_id,
                "service_id": clean(rows[0].get("service_id")),
            }

    duplicate_summary = {
        "duplicate_route_id_count": sum(1 for rows in routes_by_id.values() if len(rows) > 1),
        "duplicate_trip_id_count": sum(1 for rows in trips_by_id.values() if len(rows) > 1),
        "duplicate_route_row_excess_count": sum(max(0, len(rows) - 1) for rows in routes_by_id.values()),
        "duplicate_trip_row_excess_count": sum(max(0, len(rows) - 1) for rows in trips_by_id.values()),
        "ambiguous_route_id_count": sum(mode == "AMBIGUOUS_ROUTE_ID" for mode in route_modes.values()),
        "ambiguous_trip_id_count": sum(mode == "AMBIGUOUS_TRIP_ID" for mode in trip_modes.values()),
        "kvb_agency_ids": sorted(kvb_ids),
    }
    return dict(routes_by_id), trip_details, trip_modes, {
        "route_modes": route_modes,
        "duplicate_summary": duplicate_summary,
        "route_row_count": len(route_rows),
        "trip_row_count": len(trip_rows),
    }


def geo_cell(latitude: float | None, longitude: float | None) -> str | None:
    if latitude is None or longitude is None:
        return None
    lat_index = math.floor(latitude / GEOGRAPHIC_CELL_LAT_DEGREES)
    lon_index = math.floor(longitude / GEOGRAPHIC_CELL_LON_DEGREES)
    return f"lat{lat_index}:lon{lon_index}"


def richness_key(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    """Exposed lexicographic key; no hidden or weighted score."""

    return (
        -int(candidate.get("scheduled_event_count", 0)),
        -int(candidate.get("distinct_route_count", 0)),
        -int(candidate.get("distinct_approved_mode_count", 0)),
        -int(candidate.get("active_child_stop_count", 0)),
        -int(candidate.get("scheduled_service_day_count", 0)),
        str(candidate.get("source_parent_station_id", "")),
    )


def candidate_sort(candidates: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted(candidates, key=richness_key)


def _candidate_copy(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(candidate, ensure_ascii=False))


def candidate_modes(candidate: Mapping[str, Any]) -> list[str]:
    """Return the approved mode set used by the authoritative selector."""

    modes = candidate.get("approved_mode_set")
    if modes is None:
        modes = candidate.get("observed_mode_set", [])
    return sorted({str(mode) for mode in modes})


def select_panel(
    candidates: Sequence[Mapping[str, Any]],
    *,
    legacy_ids: Sequence[str] = LEGACY7_IDS,
    core_modes: Sequence[str] = CORE_MODES,
    panel_size: int = 50,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Return ordered panel seats, reserves, and auditable selection metadata."""

    by_id = {str(candidate["source_parent_station_id"]): candidate for candidate in candidates}
    missing_legacy = [stop_id for stop_id in legacy_ids if stop_id not in by_id]
    if missing_legacy:
        raise Stage12Blocked(
            "Legacy7 continuity anchors do not resolve in current source: "
            + ", ".join(missing_legacy)
        )
    if len(by_id) != len(candidates):
        raise Stage12Blocked("candidate parent station IDs are not unique")
    if len(candidates) < panel_size:
        raise Stage12Blocked(f"only {len(candidates)} eligible candidates for {panel_size} seats")

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()

    def add(candidate: Mapping[str, Any], phase: str, reason: str) -> None:
        stop_id = str(candidate["source_parent_station_id"])
        if stop_id in selected_ids:
            return
        row = _candidate_copy(candidate)
        row["selection_phase"] = phase
        row["selection_reason"] = reason
        selected.append(row)
        selected_ids.add(stop_id)

    for station_name, stop_id in LEGACY7:
        candidate = by_id[stop_id]
        add(
            candidate,
            "legacy_anchor",
            f"Legacy7 continuity anchor retained: {station_name} ({stop_id})",
        )

    represented_modes = {
        mode for row in selected for mode in candidate_modes(row) if mode in core_modes
    }
    mode_additions: list[dict[str, str]] = []
    for mode in sorted(core_modes):
        if mode in represented_modes:
            continue
        mode_candidates = [
            candidate
            for candidate in candidates
            if candidate["source_parent_station_id"] not in selected_ids
            and mode in candidate_modes(candidate)
        ]
        if not mode_candidates:
            continue
        candidate = candidate_sort(mode_candidates)[0]
        add(candidate, "mode_coverage", f"Covers current core mode not yet represented: {mode}")
        represented_modes.add(mode)
        mode_additions.append({"mode": mode, "station_id": candidate["source_parent_station_id"]})

    occupied_cells = sorted(
        {
            candidate.get("geographic_cell")
            for candidate in candidates
            if candidate.get("geographic_cell") is not None
        }
    )
    geographic_target = min(GEOGRAPHIC_SPREAD_TARGET, len(occupied_cells))
    cell_best: dict[str, Mapping[str, Any]] = {}
    for candidate in candidate_sort(candidates):
        cell = candidate.get("geographic_cell")
        if cell and cell not in cell_best:
            cell_best[cell] = candidate
    selected_cells = {row.get("geographic_cell") for row in selected if row.get("geographic_cell")}
    for cell in sorted(
        cell_best,
        key=lambda cell: (richness_key(cell_best[cell]), cell),
    ):
        if len(selected) >= panel_size or len(selected_cells) >= geographic_target:
            break
        candidate = cell_best[cell]
        if candidate["source_parent_station_id"] in selected_ids or cell in selected_cells:
            continue
        add(
            candidate,
            "geographic_spread",
            f"First selected parent station in fixed geographic cell {cell}",
        )
        selected_cells.add(cell)

    for candidate in candidate_sort(candidates):
        if len(selected) >= panel_size:
            break
        if candidate["source_parent_station_id"] in selected_ids:
            continue
        add(
            candidate,
            "operational_richness",
            "Operational-richness fill by exposed lexicographic key "
            "(scheduled events, routes, modes, child positions, service days, ID)",
        )

    if len(selected) != panel_size or len({row["source_parent_station_id"] for row in selected}) != panel_size:
        raise Stage12Blocked("deterministic selection did not produce exactly 50 unique parents")

    selected_ids = {row["source_parent_station_id"] for row in selected}
    reserves = []
    for reserve_rank, candidate in enumerate(
        candidate_sort(candidate for candidate in candidates if candidate["source_parent_station_id"] not in selected_ids),
        start=1,
    ):
        row = _candidate_copy(candidate)
        row["reserve_rank"] = reserve_rank
        row["reserve_reason"] = "Next candidate by the same exposed lexicographic richness key"
        reserves.append(row)

    metadata = {
        "policy_order": [
            "Legacy7 continuity anchors",
            "current Stage 4 core-mode coverage for any missing mode",
            "one operationally strongest candidate per new fixed geographic cell",
            "lexicographic operational-richness fill",
        ],
        "panel_size": panel_size,
        "geographic_cell_definition": {
            "latitude_cell_degrees": GEOGRAPHIC_CELL_LAT_DEGREES,
            "longitude_cell_degrees": GEOGRAPHIC_CELL_LON_DEGREES,
            "target_distinct_cells": geographic_target,
            "occupied_candidate_cells": len(occupied_cells),
            "coordinates_are_secondary_evidence_only": True,
        },
        "richness_key_order": [
            "scheduled_event_count_descending",
            "distinct_route_count_descending",
            "distinct_approved_mode_count_descending",
            "active_child_stop_count_descending",
            "scheduled_service_day_count_descending",
            "source_parent_station_id_ascending_tiebreaker",
        ],
        "legacy7_ids": list(legacy_ids),
        "mode_additions": mode_additions,
        "selected_geographic_cells": len(selected_cells),
        "reserve_candidate_count": len(reserves),
    }
    for rank, row in enumerate(selected, start=1):
        row["SelectionRank"] = rank
    return selected, reserves, metadata


def haversine_km(first: Mapping[str, Any], second: Mapping[str, Any]) -> float | None:
    lat1 = first.get("latitude")
    lon1 = first.get("longitude")
    lat2 = second.get("latitude")
    lon2 = second.get("longitude")
    if None in {lat1, lon1, lat2, lon2}:
        return None
    radius = 6371.0088
    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    d_phi = math.radians(float(lat2) - float(lat1))
    d_lambda = math.radians(float(lon2) - float(lon1))
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a)))


def geographic_review(
    all_candidates: Sequence[Mapping[str, Any]],
    selected: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    occupied = {candidate.get("geographic_cell") for candidate in all_candidates if candidate.get("geographic_cell")}
    selected_cells = {candidate.get("geographic_cell") for candidate in selected if candidate.get("geographic_cell")}
    cell_counts = Counter(candidate.get("geographic_cell") for candidate in selected if candidate.get("geographic_cell"))
    with_coordinates = [candidate for candidate in selected if candidate.get("latitude") is not None and candidate.get("longitude") is not None]
    nearby_pairs: list[dict[str, Any]] = []
    for index, first in enumerate(with_coordinates):
        for second in with_coordinates[index + 1 :]:
            distance = haversine_km(first, second)
            if distance is None or distance > NEARBY_REVIEW_RADIUS_KM:
                continue
            first_modes = set(candidate_modes(first))
            second_modes = set(candidate_modes(second))
            nearby_pairs.append(
                {
                    "distance_km": round(distance, 3),
                    "first_stop_point_ref": first["source_parent_station_id"],
                    "first_station_name": first["station_name"],
                    "second_stop_point_ref": second["source_parent_station_id"],
                    "second_station_name": second["station_name"],
                    "distinct_parent_station_id": True,
                    "different_route_count": len(
                        set(first.get("route_ids", [])) ^ set(second.get("route_ids", []))
                    ),
                    "different_mode_set": sorted(first_modes ^ second_modes),
                    "retention_reason": (
                        "Both are distinct source parent stations; their separate scheduled "
                        "route/mode/child-stop evidence is retained despite proximity."
                    ),
                }
            )
    nearby_pairs.sort(key=lambda row: (row["distance_km"], row["first_stop_point_ref"], row["second_stop_point_ref"]))

    diagnostics: list[dict[str, Any]] = []
    for label, predicate in (
        ("central-station", lambda c: c["source_parent_station_id"] == "de:05315:11201"),
        (
            "Deutz diagnostic area",
            lambda c: "DEUTZ" in normalized(c.get("station_name")),
        ),
        (
            "Mülheim diagnostic area",
            lambda c: "MULHEIM" in normalized(c.get("station_name")),
        ),
    ):
        candidates = [candidate for candidate in all_candidates if predicate(candidate)]
        selected_here = [candidate for candidate in selected if predicate(candidate)]
        diagnostics.append(
            {
                "label": label,
                "candidate_count": len(candidates),
                "selected_count": len(selected_here),
                "selected_station_ids": [candidate["source_parent_station_id"] for candidate in selected_here],
                "diagnostic_only_after_source_identity": True,
            }
        )

    return {
        "coordinate_use": "Secondary diversity diagnostics only; source identity is established from validated stop_id and hierarchy first.",
        "candidate_parent_stations_with_coordinates": sum(
            candidate.get("latitude") is not None and candidate.get("longitude") is not None
            for candidate in all_candidates
        ),
        "selected_parent_stations_with_coordinates": len(with_coordinates),
        "occupied_fixed_cells": len(occupied),
        "selected_fixed_cells": len(selected_cells),
        "fixed_cell_coverage_percent": round(100 * len(selected_cells) / len(occupied), 3) if occupied else None,
        "largest_selected_cell_count": max(cell_counts.values()) if cell_counts else 0,
        "largest_selected_cell_share_percent": round(100 * max(cell_counts.values()) / len(selected), 3) if cell_counts else 0.0,
        "selected_cell_counts_top": [
            {"geographic_cell": cell, "selected_count": count}
            for cell, count in sorted(cell_counts.items(), key=lambda item: (-item[1], item[0]))[:10]
        ],
        "nearby_review_radius_km": NEARBY_REVIEW_RADIUS_KM,
        "nearby_retained_pair_count": len(nearby_pairs),
        "nearby_retained_pairs_reviewed": nearby_pairs[:25],
        "named_interchange_area_diagnostics": diagnostics,
        "finding": (
            "The panel uses a fixed-cell secondary spread pass and retains nearby stations when "
            "their source parent identities and scheduled service evidence are distinct; no "
            "name or coordinate was used to establish Cologne membership."
        ),
    }


def source_provenance(
    zip_path: Path,
    archive: zipfile.ZipFile,
    feed_info: Mapping[str, str],
    service_calendar: Mapping[str, Any],
    retrieved_at_utc: str,
    source_counts: Mapping[str, Any],
) -> dict[str, Any]:
    relevant_files = [
        "agency.txt",
        "routes.txt",
        "stops.txt",
        "trips.txt",
        "stop_times.txt",
        "feed_info.txt",
        "calendar.txt",
        "calendar_dates.txt",
    ]
    row_counts: dict[str, int | None] = {}
    for filename in relevant_files:
        row_counts[filename] = csv_row_count(archive, filename) if filename in archive.namelist() else None
    return {
        "source_url": OFFICIAL_SOURCE_URL,
        "official_source_page_url": OFFICIAL_SOURCE_PAGE_URL,
        "source_profile": "VRS Global-ID GTFS",
        "retrieval_timestamp_utc": retrieved_at_utc,
        "zip_sha256": sha256_file(zip_path),
        "zip_bytes": zip_path.stat().st_size,
        "feed_publisher_name": clean(feed_info.get("feed_publisher_name")),
        "feed_version": clean(feed_info.get("feed_version")) or None,
        "feed_start_date": clean(feed_info.get("feed_start_date")) or None,
        "feed_end_date": clean(feed_info.get("feed_end_date")) or None,
        "relevant_row_counts": row_counts,
        "source_scope_counts": dict(source_counts),
        "service_calendar_summary": dict(service_calendar),
        "raw_zip_committed": False,
    }


def _empty_candidate(parent: Mapping[str, str], child_ids: Sequence[str]) -> dict[str, Any]:
    latitude = parse_float(parent.get("stop_lat"))
    longitude = parse_float(parent.get("stop_lon"))
    return {
        "source_parent_station_id": clean(parent.get("stop_id")),
        "station_name": clean(parent.get("stop_name")),
        "latitude": latitude,
        "longitude": longitude,
        "geographic_cell": geo_cell(latitude, longitude),
        "parent_location_type": clean(parent.get("location_type")),
        "source_identity_validated": clean(parent.get("stop_id")).startswith(COLOGNE_PREFIX),
        "valid_source_hierarchy": True,
        "static_child_stop_count": len(child_ids),
        "static_child_stop_ids": sorted(child_ids),
        "scheduled_event_count": 0,
        "distinct_route_count": 0,
        "route_ids": [],
        "observed_mode_set": [],
        "distinct_approved_mode_count": 0,
        "active_child_stop_count": 0,
        "scheduled_service_day_count": 0,
        "service_id_count": 0,
        "scheduled_trip_count": 0,
        "event_count_by_mode": {},
        "route_ids_by_mode": {},
        "service_ids": [],
        "active_service_dates": [],
        "continuity_anchor": clean(parent.get("stop_id")) in LEGACY7_IDS,
        "mdd_compatibility_status": "NOT_RUN",
    }


def derive_population(zip_path: Path, retrieved_at_utc: str) -> dict[str, Any]:
    required_files = {
        "agency.txt": {"agency_id", "agency_name"},
        "routes.txt": {"route_id", "agency_id", "route_type"},
        "stops.txt": {"stop_id", "location_type", "parent_station", "stop_name", "stop_lat", "stop_lon"},
        "trips.txt": {"trip_id", "route_id", "service_id"},
        "stop_times.txt": {"trip_id", "stop_id", "stop_sequence"},
        "feed_info.txt": {"feed_publisher_name"},
    }
    with zipfile.ZipFile(zip_path, "r") as archive:
        names = set(archive.namelist())
        missing = sorted(set(required_files) - names)
        if missing:
            raise Stage12Blocked("required feed files are missing: " + ", ".join(missing))
        for filename, columns in required_files.items():
            observed = csv_headers(archive, filename)
            if not columns <= observed:
                raise Stage12Blocked(
                    f"{filename} is missing required columns: {sorted(columns - observed)}"
                )

        feed_rows = list(iter_csv_rows(archive, "feed_info.txt"))
        feed_info = feed_rows[0] if feed_rows else {}
        if clean(feed_info.get("feed_publisher_name")).upper() != "VRS":
            raise Stage12Blocked("current source publisher is not VRS")
        if clean(feed_info.get("feed_version")) == "":
            raise Stage12Blocked("feed_info.txt has no feed_version")
        service_dates_by_id, service_calendar = active_service_dates(archive, feed_info)

        stops = list(iter_csv_rows(archive, "stops.txt"))
        stops_by_id: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in stops:
            stops_by_id[clean(row.get("stop_id"))].append(row)
        duplicate_stop_ids = sorted(stop_id for stop_id, rows in stops_by_id.items() if stop_id and len(rows) > 1)
        parent_rows = [row for row in stops if clean(row.get("location_type")) == "1"]
        parent_rows_by_id: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in parent_rows:
            parent_rows_by_id[clean(row.get("stop_id"))].append(row)
        unique_parents = {
            stop_id: rows[0]
            for stop_id, rows in parent_rows_by_id.items()
            if stop_id and len(rows) == 1
        }
        cologne_parent_ids = {
            stop_id for stop_id in unique_parents if stop_id.startswith(COLOGNE_PREFIX)
        }
        all_source_parent_ids = set(unique_parents)

        child_ids_by_parent: dict[str, set[str]] = defaultdict(set)
        hierarchy_counts = Counter()
        orphan_parent_refs: set[str] = set()
        invalid_child_rows: list[str] = []
        for row in stops:
            stop_id = clean(row.get("stop_id"))
            parent_id = clean(row.get("parent_station"))
            location_type = clean(row.get("location_type"))
            if location_type != "0":
                continue
            if stop_id.startswith(COLOGNE_PREFIX):
                hierarchy_counts["cologne_child_rows"] += 1
            if not parent_id:
                if stop_id.startswith(COLOGNE_PREFIX):
                    hierarchy_counts["cologne_child_without_parent_rows"] += 1
                continue
            if parent_id not in all_source_parent_ids:
                orphan_parent_refs.add(parent_id)
                if stop_id.startswith(COLOGNE_PREFIX):
                    hierarchy_counts["cologne_child_orphan_parent_rows"] += 1
                continue
            if parent_id.startswith(COLOGNE_PREFIX) and not stop_id.startswith(COLOGNE_PREFIX):
                hierarchy_counts["non_cologne_child_parent_cologne_rows"] += 1
                invalid_child_rows.append(stop_id)
                continue
            if parent_id in cologne_parent_ids and stop_id.startswith(COLOGNE_PREFIX):
                child_ids_by_parent[parent_id].add(stop_id)
                hierarchy_counts["cologne_child_parent_also_cologne_rows"] += 1

        routes_by_id, trip_details, trip_modes, mode_resolution = resolve_route_and_trip_modes(archive)
        route_modes = mode_resolution["route_modes"]
        candidates = {
            parent_id: _empty_candidate(parent, sorted(child_ids_by_parent.get(parent_id, set())))
            for parent_id, parent in unique_parents.items()
            if parent_id.startswith(COLOGNE_PREFIX)
            and clean(parent.get("location_type")) == "1"
            and parent_id not in duplicate_stop_ids
            and parent_id in child_ids_by_parent
        }
        station_service_ids: dict[str, set[str]] = defaultdict(set)
        station_trip_ids: dict[str, set[str]] = defaultdict(set)
        station_route_ids: dict[str, set[str]] = defaultdict(set)
        station_modes: dict[str, set[str]] = defaultdict(set)
        station_child_ids: dict[str, set[str]] = defaultdict(set)
        station_event_counts: Counter[str] = Counter()
        station_mode_events: dict[str, Counter[str]] = defaultdict(Counter)
        station_mode_routes: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        network_mode_events: Counter[str] = Counter()
        network_mode_routes: dict[str, set[str]] = defaultdict(set)
        network_mode_trips: dict[str, set[str]] = defaultdict(set)
        cologne_stop_event_count = 0
        unknown_stop_time_rows = 0
        unknown_trip_time_rows = 0
        parent_direct_event_count = 0
        invalid_sequence_rows = 0
        child_to_parent = {
            child_id: parent_id
            for parent_id, child_ids in child_ids_by_parent.items()
            for child_id in child_ids
        }

        # The feed has enough rows that stop_times is streamed once.  All
        # identity/mode joins are source-ID lookups prepared above.
        for row in iter_csv_rows(archive, "stop_times.txt"):
            stop_id = clean(row.get("stop_id"))
            trip_id = clean(row.get("trip_id"))
            sequence = clean(row.get("stop_sequence"))
            if not re.fullmatch(r"\d+", sequence):
                invalid_sequence_rows += 1
            if stop_id not in stops_by_id:
                unknown_stop_time_rows += 1
                continue
            if trip_id not in trip_details:
                unknown_trip_time_rows += 1
                continue
            if not stop_id.startswith(COLOGNE_PREFIX):
                continue
            cologne_stop_event_count += 1
            mode = trip_modes.get(trip_id, "UNRESOLVED_TRIP_REFERENCE")
            trip_detail = trip_details[trip_id]
            route_id = trip_detail.get("route_id", "")
            network_mode_events[mode] += 1
            network_mode_trips[mode].add(trip_id)
            if route_id:
                network_mode_routes[mode].add(route_id)
            parent_id = child_to_parent.get(stop_id)
            if parent_id is None:
                if stop_id in cologne_parent_ids:
                    parent_direct_event_count += 1
                continue
            if parent_id not in candidates:
                continue
            station_event_counts[parent_id] += 1
            station_child_ids[parent_id].add(stop_id)
            station_trip_ids[parent_id].add(trip_id)
            station_service_ids[parent_id].add(trip_detail.get("service_id", ""))
            if route_id:
                station_route_ids[parent_id].add(route_id)
            station_modes[parent_id].add(mode)
            station_mode_events[parent_id][mode] += 1
            if route_id:
                station_mode_routes[parent_id][mode].add(route_id)

        # The parent lookup is source-hierarchy based; child_to_parent is
        # retained as a structural evidence map for the validation records.
        for parent_id, candidate in candidates.items():
            candidate["scheduled_event_count"] = station_event_counts[parent_id]
            candidate["route_ids"] = sorted(station_route_ids[parent_id])
            candidate["distinct_route_count"] = len(station_route_ids[parent_id])
            candidate["observed_mode_set"] = sorted(station_modes[parent_id])
            candidate["distinct_approved_mode_count"] = len(
                set(candidate["observed_mode_set"]) & set(CORE_MODES)
            )
            candidate["active_child_stop_count"] = len(station_child_ids[parent_id])
            candidate["scheduled_trip_count"] = len(station_trip_ids[parent_id])
            service_ids = sorted(service_id for service_id in station_service_ids[parent_id] if service_id)
            active_dates = sorted({
                date_text
                for service_id in service_ids
                for date_text in service_dates_by_id.get(service_id, set())
            })
            candidate["service_ids"] = service_ids
            candidate["service_id_count"] = len(service_ids)
            candidate["active_service_dates"] = active_dates
            candidate["scheduled_service_day_count"] = len(active_dates)
            candidate["event_count_by_mode"] = dict(sorted(station_mode_events[parent_id].items()))
            candidate["route_ids_by_mode"] = {
                mode: sorted(route_ids)
                for mode, route_ids in sorted(station_mode_routes[parent_id].items())
            }

        candidates_with_events = [candidate for candidate in candidates.values() if candidate["scheduled_event_count"] > 0]
        candidates_without_events = [candidate for candidate in candidates.values() if candidate["scheduled_event_count"] == 0]
        invalid_or_unresolved = {
            "duplicate_parent_station_ids": sorted(
                stop_id for stop_id in duplicate_stop_ids if stop_id.startswith(COLOGNE_PREFIX)
            ),
            "invalid_location_type_parent_rows": sorted(
                stop_id
                for stop_id, rows in parent_rows_by_id.items()
                if stop_id.startswith(COLOGNE_PREFIX)
                and any(clean(row.get("location_type")) != "1" for row in rows)
            ),
            "orphan_parent_station_reference_count": len(orphan_parent_refs),
            "orphan_parent_station_reference_examples": sorted(orphan_parent_refs)[:20],
            "cologne_child_without_parent_rows": hierarchy_counts["cologne_child_without_parent_rows"],
            "cologne_child_orphan_parent_rows": hierarchy_counts["cologne_child_orphan_parent_rows"],
            "non_cologne_child_parent_cologne_rows": hierarchy_counts["non_cologne_child_parent_cologne_rows"],
            "unknown_stop_time_rows": unknown_stop_time_rows,
            "unknown_trip_time_rows": unknown_trip_time_rows,
            "invalid_stop_sequence_rows": invalid_sequence_rows,
        }
        material_violations = [
            key for key in (
                "duplicate_parent_station_ids",
                "orphan_parent_station_reference_count",
                "cologne_child_without_parent_rows",
                "cologne_child_orphan_parent_rows",
                "non_cologne_child_parent_cologne_rows",
                "unknown_stop_time_rows",
                "unknown_trip_time_rows",
            )
            if invalid_or_unresolved.get(key)
        ]
        if material_violations:
            raise Stage12Blocked(
                "current feed materially violates approved Stage 4 hierarchy assumptions: "
                + ", ".join(material_violations)
            )

        source_counts = {
            "agencies": csv_row_count(archive, "agency.txt"),
            "route_rows": mode_resolution["route_row_count"],
            "stops": len(stops),
            "parent_stations": len(parent_rows),
            "stop_positions": sum(clean(row.get("location_type")) == "0" for row in stops),
            "trips": mode_resolution["trip_row_count"],
            "stop_times": csv_row_count(archive, "stop_times.txt"),
            "cologne_parent_station_candidates": len(candidates),
            "cologne_scheduled_stop_events": cologne_stop_event_count,
            "cologne_parent_scheduled_child_events": sum(station_event_counts.values()),
        }
        provenance = source_provenance(
            zip_path,
            archive,
            feed_info,
            service_calendar,
            retrieved_at_utc,
            source_counts,
        )
        return {
            "feed_info": feed_info,
            "source_provenance": provenance,
            "source_counts": source_counts,
            "candidates": list(candidates.values()),
            "candidates_without_events": [candidate["source_parent_station_id"] for candidate in candidates_without_events],
            "candidate_validation": {
                "total_cologne_parent_station_candidates": len(candidates),
                "candidates_with_scheduled_events": len(candidates_with_events),
                "candidates_without_scheduled_event_evidence": len(candidates_without_events),
                "duplicate_invalid_unresolved": invalid_or_unresolved,
                "valid_parent_location_type_required": "1",
                "validated_identity_rule": f"stop_id starts with {COLOGNE_PREFIX}",
                "membership_rule": "stop_id parent_station reference to location_type=1 source parent; child stop IDs also pass the direct Cologne prefix rule",
                "scheduled_event_rule": "stop_times row for a validated child stop position with a resolved trip row",
            },
            "hierarchy_validation": {
                "cologne_parent_station_ids": len(cologne_parent_ids),
                "cologne_child_stop_position_ids": len(child_to_parent),
                "cologne_child_parent_also_cologne_rows": hierarchy_counts["cologne_child_parent_also_cologne_rows"],
                "orphan_parent_reference_count": len(orphan_parent_refs),
                "non_cologne_child_parent_cologne_rows": hierarchy_counts["non_cologne_child_parent_cologne_rows"],
                "parent_without_valid_child_count": sum(
                    parent_id not in child_ids_by_parent for parent_id in cologne_parent_ids
                ),
                "direct_parent_stop_time_event_count_excluded": parent_direct_event_count,
            },
            "mode_resolution": {
                "core_modes": list(CORE_MODES),
                "all_mode_statuses": dict(sorted(MODE_STATUSES.items())),
                "mode_metrics_network": {
                    mode: {
                        "scope_status": MODE_STATUSES[mode],
                        "cologne_scheduled_stop_events": network_mode_events[mode],
                        "cologne_serving_routes": len(network_mode_routes[mode]),
                        "cologne_serving_trips": len(network_mode_trips[mode]),
                    }
                    for mode in ALL_MODES
                },
                "route_mode_resolution": mode_resolution["duplicate_summary"],
            },
        }


REPLAY_CANDIDATE_FIELDS = (
    "source_parent_station_id",
    "station_name",
    "latitude",
    "longitude",
    "geographic_cell",
    "parent_location_type",
    "source_identity_validated",
    "source_hierarchy_validated",
    "static_child_stop_count",
    "scheduled_event_count",
    "distinct_route_count",
    "approved_mode_set",
    "distinct_approved_mode_count",
    "active_child_stop_count",
    "scheduled_service_day_count",
    "continuity_anchor",
)


def sanitize_candidate_for_replay(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only the metrics needed by the authoritative selection policy."""

    parent_id = str(candidate.get("source_parent_station_id", ""))
    modes = candidate_modes(candidate)
    return {
        "source_parent_station_id": parent_id,
        "station_name": str(candidate.get("station_name", "")),
        "latitude": candidate.get("latitude"),
        "longitude": candidate.get("longitude"),
        "geographic_cell": candidate.get("geographic_cell"),
        "parent_location_type": str(candidate.get("parent_location_type", "")),
        "source_identity_validated": bool(candidate.get("source_identity_validated")),
        "source_hierarchy_validated": bool(
            candidate.get(
                "source_hierarchy_validated",
                candidate.get("valid_source_hierarchy", False),
            )
        ),
        "static_child_stop_count": int(candidate.get("static_child_stop_count", 0)),
        "scheduled_event_count": int(candidate.get("scheduled_event_count", 0)),
        "distinct_route_count": int(candidate.get("distinct_route_count", 0)),
        "approved_mode_set": modes,
        "distinct_approved_mode_count": int(
            candidate.get("distinct_approved_mode_count", len(set(modes) & set(CORE_MODES)))
        ),
        "active_child_stop_count": int(candidate.get("active_child_stop_count", 0)),
        "scheduled_service_day_count": int(
            candidate.get("scheduled_service_day_count", 0)
        ),
        "continuity_anchor": bool(
            candidate.get("continuity_anchor", parent_id in LEGACY7_IDS)
        ),
    }


def selection_policy_parameters() -> dict[str, Any]:
    """Return the complete fixed policy contract used by ``select_panel``."""

    return {
        "policy_order": [
            "Legacy7 continuity anchors",
            "current Stage 4 core-mode coverage for any missing mode",
            "one operationally strongest candidate per new fixed geographic cell",
            "lexicographic operational-richness fill",
        ],
        "panel_size": 50,
        "legacy7_ids": list(LEGACY7_IDS),
        "core_modes": list(CORE_MODES),
        "geographic_cell_definition": {
            "latitude_cell_degrees": GEOGRAPHIC_CELL_LAT_DEGREES,
            "longitude_cell_degrees": GEOGRAPHIC_CELL_LON_DEGREES,
            "target_distinct_cells": GEOGRAPHIC_SPREAD_TARGET,
            "coordinates_are_secondary_evidence_only": True,
        },
        "richness_key_order": [
            "scheduled_event_count_descending",
            "distinct_route_count_descending",
            "distinct_approved_mode_count_descending",
            "active_child_stop_count_descending",
            "scheduled_service_day_count_descending",
            "source_parent_station_id_ascending_tiebreaker",
        ],
    }


def _mode_coverage_replay_outcome(
    selected: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    represented = sorted(
        {
            mode
            for candidate in selected
            for mode in candidate_modes(candidate)
            if mode in CORE_MODES
        }
    )
    return {
        "core_modes": list(CORE_MODES),
        "represented_core_modes": represented,
        "unrepresented_core_modes": sorted(set(CORE_MODES) - set(represented)),
        "selected_station_count_by_mode": {
            mode: sum(mode in candidate_modes(candidate) for candidate in selected)
            for mode in CORE_MODES
        },
    }


def _assert_exact_stage12_snapshot(source_provenance: Mapping[str, Any]) -> None:
    if (
        source_provenance.get("feed_version") != STAGE12_FEED_VERSION
        or source_provenance.get("zip_sha256") != STAGE12_ZIP_SHA256
    ):
        raise Stage12Blocked("EXACT_STAGE12_SOURCE_SNAPSHOT_NOT_AVAILABLE")


def validate_candidate_universe_artifact(artifact: Mapping[str, Any]) -> None:
    """Validate the sanitized, exact-snapshot candidate-universe artifact."""

    required_keys = {
        "schema_version",
        "stage12_schema_version",
        "stage",
        "panel_version",
        "script",
        "feed_version",
        "feed_sha256",
        "candidate_count",
        "generated_at_utc",
        "source_url",
        "source_provenance",
        "selection_policy",
        "candidate_fields",
        "candidates",
        "replay_expectations",
    }
    missing = sorted(required_keys - set(artifact))
    if missing:
        raise Stage12Blocked(
            "candidate universe artifact is missing required keys: " + ", ".join(missing)
        )
    if artifact["schema_version"] != CANDIDATE_UNIVERSE_SCHEMA_VERSION:
        raise Stage12Blocked("unexpected candidate universe artifact schema version")
    if artifact["stage12_schema_version"] != STAGE12_SCHEMA_VERSION:
        raise Stage12Blocked("candidate universe artifact has an unexpected Stage 12 schema version")
    if artifact["stage"] != 12 or artifact["panel_version"] != PANEL_VERSION:
        raise Stage12Blocked("candidate universe artifact has an unexpected Stage 12 identity")
    if artifact["script"].get("version") != SCRIPT_VERSION:
        raise Stage12Blocked("candidate universe artifact has an unexpected script version")
    if artifact["feed_version"] != STAGE12_FEED_VERSION:
        raise Stage12Blocked("candidate universe artifact has an unexpected feed version")
    if artifact["feed_sha256"] != STAGE12_ZIP_SHA256:
        raise Stage12Blocked("EXACT_STAGE12_SOURCE_SNAPSHOT_NOT_AVAILABLE")
    if artifact["source_url"] != OFFICIAL_SOURCE_URL:
        raise Stage12Blocked("candidate universe artifact has an unexpected source URL")
    source_provenance = artifact["source_provenance"]
    if (
        source_provenance.get("source_url") != OFFICIAL_SOURCE_URL
        or source_provenance.get("feed_version") != STAGE12_FEED_VERSION
        or source_provenance.get("zip_sha256") != STAGE12_ZIP_SHA256
    ):
        raise Stage12Blocked("candidate universe source provenance does not reconcile")
    policy = artifact["selection_policy"]
    fixed_policy = selection_policy_parameters()
    if any(policy.get(key) != value for key, value in fixed_policy.items()):
        raise Stage12Blocked("candidate universe selection policy does not reconcile")
    if artifact["candidate_fields"] != list(REPLAY_CANDIDATE_FIELDS):
        raise Stage12Blocked("candidate universe artifact replay field contract changed")

    candidates = artifact["candidates"]
    if not isinstance(candidates, list) or len(candidates) != STAGE12_CANDIDATE_COUNT:
        raise Stage12Blocked("candidate universe artifact must contain exactly 864 candidates")
    if artifact["candidate_count"] != len(candidates):
        raise Stage12Blocked("candidate universe artifact candidate count does not reconcile")
    candidate_ids = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise Stage12Blocked("candidate universe artifact contains a non-object candidate")
        missing_fields = [
            field for field in REPLAY_CANDIDATE_FIELDS if field not in candidate
        ]
        if missing_fields:
            raise Stage12Blocked(
                "candidate is missing replay fields: " + ", ".join(missing_fields)
            )
        parent_id = str(candidate["source_parent_station_id"])
        candidate_ids.append(parent_id)
        if not parent_id.startswith(COLOGNE_PREFIX):
            raise Stage12Blocked("candidate universe contains a non-Cologne parent identity")
        if candidate["parent_location_type"] != "1":
            raise Stage12Blocked("candidate universe contains a non-parent station")
        if not candidate["source_identity_validated"]:
            raise Stage12Blocked("candidate universe contains an unvalidated source identity")
        if not candidate["source_hierarchy_validated"]:
            raise Stage12Blocked("candidate universe contains an unvalidated source hierarchy")
        modes = candidate["approved_mode_set"]
        if modes != sorted(set(modes)):
            raise Stage12Blocked("candidate approved mode sets must be sorted and unique")
        if candidate["distinct_approved_mode_count"] != len(
            set(modes) & set(CORE_MODES)
        ):
            raise Stage12Blocked("candidate approved mode count does not reconcile")
        if candidate["continuity_anchor"] != (parent_id in LEGACY7_IDS):
            raise Stage12Blocked("candidate Legacy7 continuity flag does not reconcile")
        if candidate["geographic_cell"] != geo_cell(
            candidate["latitude"], candidate["longitude"]
        ):
            raise Stage12Blocked("candidate geographic cell does not reconcile to coordinates")
        for field in (
            "static_child_stop_count",
            "scheduled_event_count",
            "distinct_route_count",
            "distinct_approved_mode_count",
            "active_child_stop_count",
            "scheduled_service_day_count",
        ):
            if not isinstance(candidate[field], int) or candidate[field] < 0:
                raise Stage12Blocked(f"candidate metric is not a non-negative integer: {field}")
    if len(candidate_ids) != len(set(candidate_ids)):
        raise Stage12Blocked("candidate universe artifact contains duplicate parent identities")

    expectations = artifact["replay_expectations"]
    expectation_keys = {
        "selection_rank_ordered_parent_ids",
        "selection_phases",
        "first_25_reserve_parent_ids",
        "selected_geographic_cell_count",
        "mode_coverage_outcome",
    }
    if not expectation_keys <= set(expectations):
        raise Stage12Blocked("candidate universe artifact is missing replay expectations")
    if len(expectations["selection_rank_ordered_parent_ids"]) != 50:
        raise Stage12Blocked("candidate universe replay expectations must contain 50 panel IDs")
    if len(expectations["selection_phases"]) != 50:
        raise Stage12Blocked("candidate universe replay expectations must contain 50 phases")
    if len(expectations["first_25_reserve_parent_ids"]) != 25:
        raise Stage12Blocked("candidate universe replay expectations must contain 25 reserves")


def build_candidate_universe_artifact(
    population: Mapping[str, Any],
    *,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build the durable sanitized candidate universe for the approved snapshot."""

    source_provenance = population["source_provenance"]
    _assert_exact_stage12_snapshot(source_provenance)
    candidates = [
        sanitize_candidate_for_replay(candidate)
        for candidate in population["candidates"]
    ]
    if len(candidates) != STAGE12_CANDIDATE_COUNT:
        raise Stage12Blocked("EXACT_STAGE12_SOURCE_SNAPSHOT_NOT_AVAILABLE")
    candidates = sorted(candidates, key=lambda row: row["source_parent_station_id"])
    selected, reserves, selection_metadata = select_panel(candidates)
    mode_outcome = _mode_coverage_replay_outcome(selected)
    artifact = {
        "schema_version": CANDIDATE_UNIVERSE_SCHEMA_VERSION,
        "stage12_schema_version": STAGE12_SCHEMA_VERSION,
        "stage": 12,
        "panel_version": PANEL_VERSION,
        "script": {
            "name": "stage_12_50_station_selection",
            "version": SCRIPT_VERSION,
        },
        "feed_version": source_provenance["feed_version"],
        "feed_sha256": source_provenance["zip_sha256"],
        "candidate_count": len(candidates),
        "generated_at_utc": generated_at_utc
        or dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_url": OFFICIAL_SOURCE_URL,
        "source_provenance": {
            "source_url": OFFICIAL_SOURCE_URL,
            "official_source_page_url": OFFICIAL_SOURCE_PAGE_URL,
            "feed_version": source_provenance["feed_version"],
            "zip_sha256": source_provenance["zip_sha256"],
            "zip_bytes": source_provenance["zip_bytes"],
            "retrieval_timestamp_utc": source_provenance["retrieval_timestamp_utc"],
            "raw_gtfs_committed": False,
            "raw_mdd_responses_committed": False,
            "secrets_committed": False,
        },
        "selection_policy": {
            **selection_policy_parameters(),
            "source_population_occupied_candidate_cells": selection_metadata[
                "geographic_cell_definition"
            ]["occupied_candidate_cells"],
        },
        "candidate_fields": list(REPLAY_CANDIDATE_FIELDS),
        "candidates": candidates,
        "replay_expectations": {
            "selection_rank_ordered_parent_ids": [
                row["source_parent_station_id"] for row in selected
            ],
            "selection_phases": [row["selection_phase"] for row in selected],
            "first_25_reserve_parent_ids": [
                row["source_parent_station_id"] for row in reserves[:25]
            ],
            "selected_geographic_cell_count": selection_metadata[
                "selected_geographic_cells"
            ],
            "mode_coverage_outcome": mode_outcome,
        },
    }
    validate_candidate_universe_artifact(artifact)
    return artifact


def generate_candidate_universe_artifact(
    gtfs_zip: Path,
    *,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Derive candidate evidence only from the exact approved Stage 12 ZIP."""

    zip_path = gtfs_zip.expanduser().resolve()
    if not zip_path.is_file() or not zipfile.is_zipfile(zip_path):
        raise Stage12Blocked("EXACT_STAGE12_SOURCE_SNAPSHOT_NOT_AVAILABLE")
    if sha256_file(zip_path) != STAGE12_ZIP_SHA256:
        raise Stage12Blocked("EXACT_STAGE12_SOURCE_SNAPSHOT_NOT_AVAILABLE")
    population = derive_population(zip_path, STAGE12_RETRIEVAL_TIMESTAMP_UTC)
    return build_candidate_universe_artifact(
        population,
        generated_at_utc=generated_at_utc,
    )


def write_candidate_universe_artifact(
    artifact: Mapping[str, Any],
    output_path: Path,
) -> None:
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_candidate_universe_artifact(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Stage12Blocked(f"cannot read candidate universe artifact: {path}") from exc
    if not isinstance(artifact, Mapping):
        raise Stage12Blocked("candidate universe artifact must be a JSON object")
    validate_candidate_universe_artifact(artifact)
    return dict(artifact)


def replay_candidate_universe(
    artifact: Mapping[str, Any],
    *,
    expected_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Replay the authoritative selector from sanitized candidates only."""

    validate_candidate_universe_artifact(artifact)
    selected, reserves, selection_metadata = select_panel(artifact["candidates"])
    expectations = artifact["replay_expectations"]
    actual_panel_ids = [row["source_parent_station_id"] for row in selected]
    actual_phases = [row["selection_phase"] for row in selected]
    actual_reserve_ids = [
        row["source_parent_station_id"] for row in reserves[:25]
    ]
    actual_mode_outcome = _mode_coverage_replay_outcome(selected)

    expected_panel_ids = expectations["selection_rank_ordered_parent_ids"]
    expected_panel_ranks = list(range(1, 51))
    expected_phases = expectations["selection_phases"]
    expected_reserve_ids = expectations["first_25_reserve_parent_ids"]
    expected_geo_count = expectations["selected_geographic_cell_count"]
    expected_mode_outcome = expectations["mode_coverage_outcome"]
    if expected_evidence is not None:
        expected_panel = expected_evidence.get("panel", [])
        expected_panel_ids = [
            row.get("source_parent_station_id")
            for row in expected_panel
            if isinstance(row, Mapping)
        ]
        expected_panel_ranks = [
            row.get("SelectionRank")
            for row in expected_panel
            if isinstance(row, Mapping)
        ]
        expected_phases = [
            row.get("selection_phase")
            for row in expected_panel
            if isinstance(row, Mapping)
        ]
        expected_reserve_ids = [
            row.get("source_parent_station_id")
            for row in expected_evidence.get("reserve_candidates", [])[:25]
            if isinstance(row, Mapping)
        ]
        expected_geo_count = expected_evidence.get("selection_methodology", {}).get(
            "selected_geographic_cells"
        )
        expected_mode_rows = expected_evidence.get("mode_coverage", {}).get(
            "core_mode_rows", []
        )
        expected_mode_outcome = {
            "core_modes": list(CORE_MODES),
            "represented_core_modes": sorted(
                row["mode"]
                for row in expected_mode_rows
                if row.get("selected_station_count", 0) > 0
            ),
            "unrepresented_core_modes": sorted(
                row["mode"]
                for row in expected_mode_rows
                if row.get("selected_station_count", 0) == 0
            ),
            "selected_station_count_by_mode": {
                row["mode"]: row.get("selected_station_count", 0)
                for row in expected_mode_rows
            },
        }

    checks = {
        "same_panel_ids_and_order": actual_panel_ids == expected_panel_ids,
        "same_panel_ranks": [row["SelectionRank"] for row in selected]
        == expected_panel_ranks,
        "same_selection_phases": actual_phases == expected_phases,
        "all_legacy7_anchors": set(LEGACY7_IDS) <= set(actual_panel_ids),
        "same_first_25_reserve_ids": actual_reserve_ids == expected_reserve_ids,
        "same_geographic_cell_count": selection_metadata["selected_geographic_cells"]
        == expected_geo_count,
        "same_mode_coverage_outcome": actual_mode_outcome == expected_mode_outcome,
        "artifact_expectations_match": (
            actual_panel_ids == expectations["selection_rank_ordered_parent_ids"]
            and actual_phases == expectations["selection_phases"]
            and actual_reserve_ids == expectations["first_25_reserve_parent_ids"]
            and selection_metadata["selected_geographic_cells"]
            == expectations["selected_geographic_cell_count"]
            and actual_mode_outcome == expectations["mode_coverage_outcome"]
        ),
    }
    return {
        "verified": all(checks.values()),
        "candidate_count": len(artifact["candidates"]),
        "source_sha256": artifact["feed_sha256"],
        "panel_count": len(selected),
        "first_25_reserve_count": len(actual_reserve_ids),
        "network_access_required": False,
        "mdd_credential_required": False,
        **checks,
    }


def candidate_universe_replay_reference(
    artifact_path: Path,
    artifact: Mapping[str, Any],
    verification: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        relative_path = artifact_path.expanduser().resolve().relative_to(REPOSITORY_ROOT)
    except ValueError as exc:
        raise Stage12Blocked("candidate universe artifact must be inside the repository") from exc
    return {
        "artifact_path": relative_path.as_posix(),
        "artifact_sha256": sha256_file(artifact_path),
        "artifact_schema_version": artifact["schema_version"],
        "stage12_schema_version": artifact["stage12_schema_version"],
        "panel_version": artifact["panel_version"],
        "feed_version": artifact["feed_version"],
        "source_zip_sha256": artifact["feed_sha256"],
        "candidate_count": artifact["candidate_count"],
        "offline_replay_verified": bool(verification["verified"]),
        "network_access_required": False,
        "mdd_credential_required": False,
        "verified_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
        "verification": dict(verification),
    }


def scalar_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return None


def probe_one_station(candidate: Mapping[str, Any], api_key: str) -> dict[str, Any]:
    """Make one sanitized compatibility attempt using the approved builder/parser."""

    try:
        import stage_08_mdd_realtime_pilot as stage8
    except Exception as exc:  # pragma: no cover - repository import failure
        return {
            "compatibility_pass": False,
            "replaceable_failure": False,
            "probe_status": "PROBE_RUNTIME_IMPORT_FAILURE",
            "probe_error_type": type(exc).__name__,
            "http_attempts": 0,
            "requested_stop_point_ref": candidate["source_parent_station_id"],
        }

    config = stage8.Stage8RuntimeConfig(
        station=str(candidate["station_name"]),
        stop_point_ref=str(candidate["source_parent_station_id"]),
        number_of_results=MDD_NUMBER_OF_RESULTS,
        sampling_policy=MDD_SAMPLING_POLICY,
    )
    result = stage8.perform_http_request(api_key, runtime_config=config)
    record: dict[str, Any] = {
        "requested_stop_point_ref": candidate["source_parent_station_id"],
        "station_name": candidate["station_name"],
        "request_contract": {
            "endpoint": ENDPOINT,
            "requestor_ref": stage8.REQUESTOR_REF,
            "stop_event_type": MDD_STOP_EVENT_TYPE,
            "number_of_results": MDD_NUMBER_OF_RESULTS,
            "include_previous_calls": MDD_INCLUDE_PREVIOUS_CALLS,
            "include_onward_calls": MDD_INCLUDE_ONWARD_CALLS,
            "include_realtime_data": MDD_INCLUDE_REALTIME_DATA,
            "time_window": "OMITTED",
            "include_operating_days": "OMITTED",
            "max_attempts": 1,
            "retries": MDD_RETRIES,
            "concurrency": MDD_CONCURRENCY,
        },
        "http_attempts": 1,
        "http_status": result.http_status,
        "content_type": result.content_type,
        "elapsed_ms": round(float(result.elapsed_ms), 3),
        "transport_failure": result.failure,
        "parser_success": False,
        "valid_trias_application_response": False,
        "application_status": None,
        "returned_event_count": 0,
        "source_stop_point_refs": [],
        "source_stop_point_ref_evidence": "NOT_EVALUATED",
        "compatibility_pass": False,
        "replaceable_failure": True,
        "probe_status": None,
    }

    if result.http_status in {401, 403}:
        record.update(
            {
                "probe_status": "HTTP_AUTHORIZATION_FAILURE",
                "replaceable_failure": False,
            }
        )
        return record
    if result.http_status is not None and not 200 <= result.http_status < 300:
        record["probe_status"] = "HTTP_FAILURE"
        return record
    if result.failure:
        record.update(
            {
                "probe_status": result.failure,
                "replaceable_failure": False,
            }
        )
        return record
    try:
        parsed = stage8.parse_response(result.body, result.content_type)
        record["parser_success"] = True
        record["application_status"] = scalar_text(stage8.source_value(parsed.application_status))
        record["returned_event_count"] = len(parsed.results)
        application_failed = stage8.application_error(parsed)
        record["valid_trias_application_response"] = not application_failed
        if application_failed:
            record["probe_status"] = "APPLICATION_ERROR"
            return record
        bundle = stage8.build_bundle(
            parsed=parsed,
            response_id="STAGE12_COMPATIBILITY_RESPONSE",
            run_id="STAGE12_COMPATIBILITY_RUN",
            observed_at=stage8.iso_utc(stage8.utc_now()),
            sampling_policy_id=MDD_SAMPLING_POLICY,
        )
        refs: set[str] = set()
        for event in bundle.get("stop_events", []):
            value = event.get("stop_point_ref")
            if value is not None and str(value).strip():
                refs.add(str(value).strip())
            for call in event.get("call_observations", []):
                value = call.get("stop_point_ref")
                if value is not None and str(value).strip():
                    refs.add(str(value).strip())
        record["source_stop_point_refs"] = sorted(refs)
        if not parsed.results:
            record.update(
                {
                    "probe_status": "NO_RESULT_VALID_RESPONSE_STATIC_SERVICE_EVIDENCE",
                    "source_stop_point_ref_evidence": "NO_RESULT_NOT_APPLICABLE",
                    "compatibility_pass": True,
                    "replaceable_failure": False,
                }
            )
            return record
        expected = set(candidate.get("static_child_stop_ids", [])) | {
            str(candidate["source_parent_station_id"])
        }
        matching_refs = sorted(refs & expected)
        record["matching_source_stop_point_refs"] = matching_refs
        if matching_refs:
            record.update(
                {
                    "probe_status": "VALID_TRIAS_SOURCE_STOP_POINT_EVIDENCE",
                    "source_stop_point_ref_evidence": "MATCHES_REQUESTED_PARENT_OR_STATIC_CHILD",
                    "compatibility_pass": True,
                    "replaceable_failure": False,
                }
            )
            return record
        record.update(
            {
                "probe_status": "SOURCE_STOP_POINT_REF_MISMATCH_OR_MISSING",
                "source_stop_point_ref_evidence": "NO_MATCH_TO_REQUESTED_PARENT_OR_STATIC_CHILD",
            }
        )
        return record
    except Exception as exc:
        record.update(
            {
                "probe_status": "PARSER_FAILURE",
                "parser_error_type": type(exc).__name__,
            }
        )
        return record


def apply_compatibility_probe(
    selected: Sequence[Mapping[str, Any]],
    reserves: Sequence[Mapping[str, Any]],
    api_key: str,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Probe slots sequentially and replace only a failed non-anchor slot."""

    slots = [_candidate_copy(candidate) for candidate in selected]
    reserve_rows = [_candidate_copy(candidate) for candidate in reserves]
    probe_records: list[dict[str, Any]] = []
    replacements: list[dict[str, Any]] = []
    attempts = 0
    status = "COMPLETE"
    blocker: str | None = None
    reserve_index = 0

    for slot_index, original in enumerate(slots, start=1):
        current = original
        while True:
            if attempts >= MDD_MAX_HTTP_ATTEMPTS:
                status = "BLOCKED"
                blocker = "MDD_HTTP_ATTEMPT_BUDGET_EXHAUSTED"
                break
            probe = probe_one_station(current, api_key)
            attempts += int(probe.get("http_attempts", 0))
            probe["panel_slot"] = slot_index
            probe["attempt_sequence"] = len(probe_records) + 1
            probe_records.append(probe)
            current["mdd_compatibility_status"] = probe["probe_status"]
            current["mdd_compatibility"] = probe
            if probe.get("probe_status") == "HTTP_AUTHORIZATION_FAILURE":
                status = "EXTERNALLY_BLOCKED"
                blocker = "HTTP_401_OR_403_STOPPED_ALL_FURTHER_PROBING"
                break
            if probe.get("compatibility_pass"):
                break
            if original["source_parent_station_id"] in LEGACY7_IDS:
                status = "BLOCKED"
                blocker = "LEGACY7_COMPATIBILITY_FAILURE_REQUIRES_EXPLICIT_IDENTITY_DECISION"
                break
            if not probe.get("replaceable_failure"):
                status = "BLOCKED"
                blocker = f"NON_REPLACEABLE_PROBE_FAILURE:{probe.get('probe_status')}"
                break
            if reserve_index >= len(reserve_rows):
                status = "BLOCKED"
                blocker = "NO_DEFENSIBLE_RESERVE_REMAINS"
                break
            used_ids = {row["source_parent_station_id"] for row in slots}
            replacement = None
            replacement_rank = None
            while reserve_index < len(reserve_rows):
                candidate = reserve_rows[reserve_index]
                reserve_rank = int(candidate.get("reserve_rank", reserve_index + 1))
                reserve_index += 1
                if candidate["source_parent_station_id"] in used_ids:
                    continue
                replacement = candidate
                replacement_rank = reserve_rank
                break
            if replacement is None:
                status = "BLOCKED"
                blocker = "NO_DEFENSIBLE_RESERVE_REMAINS"
                break
            replacements.append(
                {
                    "panel_slot": slot_index,
                    "original_stop_point_ref": original["source_parent_station_id"],
                    "original_station_name": original["station_name"],
                    "failure_status": probe["probe_status"],
                    "replacement_stop_point_ref": replacement["source_parent_station_id"],
                    "replacement_station_name": replacement["station_name"],
                    "reserve_rank": replacement_rank,
                    "reason": "Next highest remaining defensible reserve after failed compatibility requirement",
                }
            )
            current = replacement
            current["selection_phase"] = "compatibility_replacement"
            current["selection_reason"] = (
                "Replacement for a failed provisional candidate; next remaining "
                "defensible reserve validated under the same bounded probe"
            )
            slots[slot_index - 1] = current
        if status != "COMPLETE":
            break

    if status == "COMPLETE" and len(probe_records) < MDD_NORMAL_HTTP_ATTEMPTS:
        status = "BLOCKED"
        blocker = "FINAL_PANEL_COMPATIBILITY_INCOMPLETE"
    for rank, row in enumerate(slots, start=1):
        row["SelectionRank"] = rank
    return slots, reserve_rows, {
        "status": status,
        "blocker": blocker,
        "http_attempts_consumed": attempts,
        "max_http_attempts": MDD_MAX_HTTP_ATTEMPTS,
        "normal_case_http_attempts": MDD_NORMAL_HTTP_ATTEMPTS,
        "sequential": True,
        "retries": 0,
        "probe_record_count": len(probe_records),
        "final_panel_probe_count": sum(
            1 for row in slots if row.get("mdd_compatibility", {}).get("compatibility_pass")
        ),
        "raw_responses_persisted": False,
    }, probe_records, replacements


def mode_coverage(
    population: Mapping[str, Any],
    selected: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    mode_metrics = population["mode_resolution"]["mode_metrics_network"]
    rows = []
    for mode in CORE_MODES:
        network_events = int(mode_metrics[mode]["cologne_scheduled_stop_events"])
        selected_events = sum(int(candidate.get("event_count_by_mode", {}).get(mode, 0)) for candidate in selected)
        selected_stations = sum(mode in candidate_modes(candidate) for candidate in selected)
        rows.append(
            {
                "mode": mode,
                "scope_status": MODE_STATUSES[mode],
                "network_cologne_scheduled_stop_events": network_events,
                "network_cologne_serving_routes": mode_metrics[mode]["cologne_serving_routes"],
                "selected_station_count": selected_stations,
                "selected_station_event_representation": selected_events,
                "selected_event_representation_percent": round(100 * selected_events / network_events, 3) if network_events else None,
                "underrepresentation_note": (
                    "Panel sample represents this core mode but not the whole Cologne network; "
                    "event representation is scheduled stop-event evidence, not passenger demand."
                ),
            }
        )
    return {
        "inventory_source": "Stage 4 approved mode inventory, re-applied to the current official snapshot",
        "core_mode_rows": rows,
        "zero_current_evidence_modes_not_required": [
            mode
            for mode in ALL_MODES
            if MODE_STATUSES[mode] == "CORE_KPI"
            and mode_metrics[mode]["cologne_scheduled_stop_events"] == 0
        ],
        "separate_or_outside_core_observed_modes": {
            mode: {
                "scope_status": MODE_STATUSES[mode],
                "network_cologne_scheduled_stop_events": mode_metrics[mode]["cologne_scheduled_stop_events"],
            }
            for mode in ALL_MODES
            if MODE_STATUSES[mode] != "CORE_KPI"
            and mode_metrics[mode]["cologne_scheduled_stop_events"] > 0
        },
        "terminology_guardrail": "Scheduled-event volume is not passenger demand and no ridership source was used.",
    }


def legacy_evidence(
    candidates_by_id: Mapping[str, Mapping[str, Any]],
    selected: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    selected_ids = {candidate["source_parent_station_id"] for candidate in selected}
    rows = []
    for station_name, stop_id in LEGACY7:
        candidate = candidates_by_id.get(stop_id)
        rows.append(
            {
                "station_name": station_name,
                "source_parent_station_id": stop_id,
                "current_static_identity_resolves": candidate is not None,
                "parent_station_status": candidate.get("parent_location_type") == "1" if candidate else False,
                "valid_source_hierarchy": candidate.get("valid_source_hierarchy", False) if candidate else False,
                "scheduled_service_evidence": bool(candidate and candidate.get("scheduled_event_count", 0) > 0),
                "scheduled_event_count": candidate.get("scheduled_event_count", 0) if candidate else 0,
                "mdd_request_stop_point_ref": stop_id,
                "selected_in_final_panel": stop_id in selected_ids,
                "anchor_policy": "Continuity anchor, not blindly trusted; current identity and service evidence verified.",
            }
        )
    return rows


def acceptance_gates(
    *,
    population: Mapping[str, Any],
    selected: Sequence[Mapping[str, Any]],
    mode_rows: Sequence[Mapping[str, Any]],
    probe_summary: Mapping[str, Any],
    compatibility_requested: bool,
    candidate_replay: Mapping[str, Any] | None,
    stage13_production_not_started: bool = True,
) -> list[dict[str, Any]]:
    candidate_validation = population["candidate_validation"]
    legacy_ok = all(
        row["current_static_identity_resolves"]
        and row["parent_station_status"]
        and row["valid_source_hierarchy"]
        and row["scheduled_service_evidence"]
        and row["selected_in_final_panel"]
        for row in legacy_evidence(
            {candidate["source_parent_station_id"]: candidate for candidate in population["candidates"]},
            selected,
        )
    )
    gates = [
        {"id": "source_provenance", "passed": bool(population["source_provenance"].get("zip_sha256")), "detail": "Official URL, retrieval timestamp, ZIP hash/size, feed version/date range, and relevant row counts recorded."},
        {"id": "cologne_parent_population_validated", "passed": candidate_validation["total_cologne_parent_station_candidates"] > 0 and not any(candidate_validation["duplicate_invalid_unresolved"].values()), "detail": "Validated location_type=1 direct-prefix parents with source hierarchy and resolved scheduled child events."},
        {"id": "exactly_50_unique_parent_stations", "passed": len(selected) == 50 and len({row["source_parent_station_id"] for row in selected}) == 50, "detail": "Final panel has exactly 50 unique parent-station IDs."},
        {"id": "selection_method_reproducible", "passed": bool(candidate_replay and candidate_replay.get("verified")), "detail": "Deterministic algorithm reproducibility is established by the authoritative select_panel function and recorded policy; durable exact-input replayability is established by candidate_universe_replay_verified."},
        {"id": "candidate_universe_replay_verified", "passed": bool(candidate_replay and candidate_replay.get("verified")), "detail": "Approved fixed panel reproduced offline from the sanitized complete Stage 12 candidate population; no network access, original ZIP, MDD, or MDD credential is required."},
        {"id": "every_selected_station_has_evidence", "passed": all(row.get("scheduled_event_count", 0) > 0 and row.get("selection_reason") for row in selected), "detail": "Every selected row carries measured static evidence and a selection reason."},
        {"id": "current_core_mode_representation_measured", "passed": all(row["network_cologne_scheduled_stop_events"] > 0 and row["selected_station_count"] > 0 for row in mode_rows), "detail": "Every current core mode with evidence is represented and measured; zero-evidence modes are not invented."},
        {"id": "geographic_concentration_reviewed", "passed": True, "detail": "Fixed-cell coverage, concentration counts, nearby retained pairs, and central/Deutz/Mülheim diagnostics recorded."},
        {"id": "legacy7_continuity_anchors_retained", "passed": legacy_ok, "detail": "All seven current Legacy7 identities remain valid and are retained."},
        {"id": "mdd_compatibility_test_completed_for_final_panel", "passed": compatibility_requested and probe_summary.get("status") == "COMPLETE" and probe_summary.get("final_panel_probe_count") == 50, "detail": "Bounded one-request-per-final-station diagnostic completed with sanitized classifications."},
        {"id": "no_unresolved_duplicate_final_identities", "passed": len({row["source_parent_station_id"] for row in selected}) == len(selected), "detail": "No duplicate final parent identity."},
        {"id": "panel_version_created", "passed": PANEL_VERSION != "", "detail": f"Fixed panel version is {PANEL_VERSION}."},
        {"id": "fixed_membership_versioning_policy", "passed": True, "detail": "Membership is fixed after approval; changes require a new version with effective time, reason, and predecessor."},
        {"id": "no_production_cadence_or_tier_decisions", "passed": True, "detail": "SelectionRank is a panel rank only; no Tier A/B/C cadence, NumberOfResults, concurrency, retry, or overlap decision was made."},
        {"id": "stage13_production_work_not_started", "passed": stage13_production_not_started, "detail": "Stage 13 production scheduler/collector activation and Stage 14 cutover remain NOT STARTED; existing readiness notes remain blocked."},
        {"id": "no_secret_or_raw_payload_persisted", "passed": True, "detail": "Only sanitized evidence is written; MDD raw responses and API key are not persisted."},
    ]
    return gates


def build_evidence(
    *,
    gtfs_zip: Path,
    retrieved_at_utc: str | None = None,
    probe_mdd: bool = False,
    api_key: str | None = None,
) -> dict[str, Any]:
    zip_path = gtfs_zip.expanduser().resolve()
    if not zip_path.is_file() or not zipfile.is_zipfile(zip_path):
        raise Stage12Blocked(f"not a GTFS ZIP: {zip_path}")
    effective_retrieved_at = retrieved_at_utc or iso_utc_from_mtime(zip_path)
    population = derive_population(zip_path, effective_retrieved_at)
    selected, reserves, selection_metadata = select_panel(population["candidates"])
    probe_records: list[dict[str, Any]] = []
    replacements: list[dict[str, Any]] = []
    if probe_mdd:
        if api_key is None:
            raise Stage12Blocked("MDD_API_KEY is missing; zero MDD requests permitted")
        selected, reserves, probe_summary, probe_records, replacements = apply_compatibility_probe(
            selected, reserves, api_key
        )
    else:
        probe_summary = {
            "status": "NOT_RUN",
            "blocker": "MDD_COMPATIBILITY_PROBE_NOT_REQUESTED",
            "http_attempts_consumed": 0,
            "max_http_attempts": MDD_MAX_HTTP_ATTEMPTS,
            "normal_case_http_attempts": MDD_NORMAL_HTTP_ATTEMPTS,
            "sequential": True,
            "retries": 0,
            "probe_record_count": 0,
            "final_panel_probe_count": 0,
            "raw_responses_persisted": False,
        }
    # Keep replacement history separate from any compatibility payloads so a
    # future panel version can interpret the original selection slot.
    candidates_by_id = {candidate["source_parent_station_id"]: candidate for candidate in population["candidates"]}
    for row in selected:
        row.setdefault("SelectionRank", len([item for item in selected if item["source_parent_station_id"] <= row["source_parent_station_id"]]))
        row["continuity_anchor"] = row["source_parent_station_id"] in LEGACY7_IDS
    mode_rows = mode_coverage(population, selected)
    candidate_artifact_path = REPOSITORY_ROOT / CANDIDATE_UNIVERSE_ARTIFACT
    candidate_replay: dict[str, Any]
    candidate_replay_reference: dict[str, Any]
    if candidate_artifact_path.is_file():
        candidate_artifact = load_candidate_universe_artifact(candidate_artifact_path)
        candidate_replay = replay_candidate_universe(
            candidate_artifact,
            expected_evidence={
                "panel": selected,
                "reserve_candidates": reserves[:25],
                "selection_methodology": selection_metadata,
                "mode_coverage": mode_rows,
            },
        )
        candidate_replay_reference = candidate_universe_replay_reference(
            candidate_artifact_path,
            candidate_artifact,
            candidate_replay,
        )
    else:
        candidate_replay = {
            "verified": False,
            "candidate_count": 0,
            "network_access_required": False,
            "mdd_credential_required": False,
        }
        candidate_replay_reference = {
            "artifact_path": CANDIDATE_UNIVERSE_ARTIFACT.as_posix(),
            "artifact_schema_version": CANDIDATE_UNIVERSE_SCHEMA_VERSION,
            "stage12_schema_version": STAGE12_SCHEMA_VERSION,
            "panel_version": PANEL_VERSION,
            "feed_version": population["source_provenance"].get("feed_version"),
            "source_zip_sha256": population["source_provenance"].get("zip_sha256"),
            "candidate_count": 0,
            "offline_replay_verified": False,
            "network_access_required": False,
            "mdd_credential_required": False,
            "verification": candidate_replay,
        }
    gates = acceptance_gates(
        population=population,
        selected=selected,
        mode_rows=mode_rows["core_mode_rows"],
        probe_summary=probe_summary,
        compatibility_requested=probe_mdd,
        candidate_replay=candidate_replay,
    )
    all_passed = all(gate["passed"] for gate in gates)
    status = "COMPLETE — APPROVED FIXED 50-STATION PANEL" if all_passed else "BLOCKED — STAGE 12 ACCEPTANCE GATE FAILED"
    return {
        "schema_version": STAGE12_SCHEMA_VERSION,
        "stage": 12,
        "status": status,
        "panel_version": PANEL_VERSION,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "script": {
            "name": "stage_12_50_station_selection",
            "version": SCRIPT_VERSION,
            "source_zip_read_only": True,
            "raw_gtfs_committed": False,
        },
        "source_provenance": population["source_provenance"],
        "approved_stage4_semantics": {
            "source_url": OFFICIAL_SOURCE_URL,
            "identity_rule": f"validated stop_id starts with {COLOGNE_PREFIX}",
            "parent_station_rule": "location_type=1 parent; child membership from actual parent_station hierarchy",
            "coordinates_role": "secondary geographic-diversity evidence only",
            "material_structural_violation_detected": False,
            "current_feed_reconciles_to_stage4_scope": True,
        },
        "candidate_population": population["candidate_validation"],
        "candidate_universe_replay": candidate_replay_reference,
        "hierarchy_validation": population["hierarchy_validation"],
        "selection_methodology": selection_metadata,
        "legacy7_continuity": legacy_evidence(candidates_by_id, selected),
        "panel": selected,
        "reserve_candidates": reserves[:25],
        "mode_coverage": mode_rows,
        "geographic_concentration_review": geographic_review(population["candidates"], selected),
        "mdd_compatibility_probe": {
            **probe_summary,
            "endpoint": ENDPOINT,
            "records": probe_records,
            "interpretation": "A valid parseable no-result response is retained as valid compatibility evidence when static scheduled service exists; it is not treated as an invalid StopPointRef.",
        },
        "replacements": replacements,
        "fixed_panel_versioning_contract": {
            "panel_version": PANEL_VERSION,
            "membership_fixed_after_stage12_approval": True,
            "station_replacement_is_not_dynamic": True,
            "membership_change_requires_new_panel_version": True,
            "old_versions_remain_historically_interpretable": True,
            "new_version_records": ["effective_date_time", "reason", "predecessor_panel_version"],
            "legacy7_history_never_deleted": True,
            "future_stage14_preserves_existing_historical_sampling_identities": True,
            "reporting_distinguishes_sampling_panel_eras": True,
            "selection_rank_is_not_production_tier": True,
        },
        "acceptance_gates": gates,
        "stage13_boundary": {
            "production_cadence_approved": False,
            "production_tiers_approved": False,
            "number_of_results_approved": False,
            "concurrency_approved": False,
            "overlap_claiming_implemented": False,
            "stage14_cutover_started": False,
            "stage13_production_status": "NOT STARTED",
            "note": "Pre-existing offline Stage 13 readiness notes remain conditionally blocked and are not changed into production approval by Stage 12.",
        },
    }


def validate_evidence(evidence: Mapping[str, Any]) -> None:
    """Validate the durable artifact shape without reading any secret."""

    required_keys = {
        "schema_version",
        "stage",
        "status",
        "panel_version",
        "source_provenance",
        "candidate_population",
        "candidate_universe_replay",
        "panel",
        "reserve_candidates",
        "mode_coverage",
        "geographic_concentration_review",
        "mdd_compatibility_probe",
        "fixed_panel_versioning_contract",
        "acceptance_gates",
        "stage13_boundary",
    }
    missing = sorted(required_keys - set(evidence))
    if missing:
        raise Stage12Blocked("evidence is missing required keys: " + ", ".join(missing))
    panel = evidence["panel"]
    if not isinstance(panel, list) or len(panel) != 50:
        raise Stage12Blocked("evidence panel must contain exactly 50 rows")
    panel_ids = [row.get("source_parent_station_id") for row in panel if isinstance(row, Mapping)]
    if len(panel_ids) != 50 or len(set(panel_ids)) != 50:
        raise Stage12Blocked("evidence panel parent identities are not unique")
    if evidence["panel_version"] != PANEL_VERSION:
        raise Stage12Blocked("unexpected fixed panel version")
    if evidence["schema_version"] != STAGE12_SCHEMA_VERSION:
        raise Stage12Blocked("unexpected Stage 12 evidence schema version")
    replay_reference = evidence["candidate_universe_replay"]
    if replay_reference.get("artifact_path") != CANDIDATE_UNIVERSE_ARTIFACT.as_posix():
        raise Stage12Blocked("unexpected candidate universe artifact path")
    artifact_path = REPOSITORY_ROOT / replay_reference["artifact_path"]
    if not artifact_path.is_file():
        raise Stage12Blocked("durable candidate universe artifact is missing")
    artifact = load_candidate_universe_artifact(artifact_path)
    if replay_reference.get("artifact_sha256") != sha256_file(artifact_path):
        raise Stage12Blocked("candidate universe artifact hash does not reconcile")
    if replay_reference.get("source_zip_sha256") != STAGE12_ZIP_SHA256:
        raise Stage12Blocked("candidate universe replay does not reference the approved Stage 12 source")
    if evidence["source_provenance"].get("zip_sha256") != STAGE12_ZIP_SHA256:
        raise Stage12Blocked("Stage 12 evidence source hash changed")
    replay_verification = replay_candidate_universe(artifact, expected_evidence=evidence)
    if not replay_reference.get("offline_replay_verified") or not replay_verification["verified"]:
        raise Stage12Blocked("durable candidate universe offline replay is not verified")
    gate_ids = {gate.get("id") for gate in evidence["acceptance_gates"]}
    required_replay_gates = {
        "selection_method_reproducible",
        "candidate_universe_replay_verified",
    }
    if not required_replay_gates <= gate_ids:
        raise Stage12Blocked("Stage 12 evidence is missing candidate-universe replay gates")
    if evidence["stage13_boundary"].get("production_cadence_approved"):
        raise Stage12Blocked("Stage 12 evidence cannot approve production cadence")
    if evidence["stage13_boundary"].get("production_tiers_approved"):
        raise Stage12Blocked("Stage 12 evidence cannot approve production tiers")
    rendered = json.dumps(evidence, ensure_ascii=False, sort_keys=True).lower()
    for forbidden in ("x-api-key:", "authorization:", "mdd-key.txt"):
        if forbidden in rendered:
            raise Stage12Blocked(f"forbidden secret/payload marker in evidence: {forbidden}")
    if evidence["status"].startswith("COMPLETE") and not all(
        bool(gate.get("passed")) for gate in evidence["acceptance_gates"]
    ):
        raise Stage12Blocked("complete evidence contains a failed acceptance gate")


def refresh_existing_evidence_replay_reference(
    evidence_path: Path,
    candidate_artifact_path: Path,
) -> None:
    """Add the offline replay reference without rerunning the MDD probe."""

    evidence_path = evidence_path.expanduser().resolve()
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    if not isinstance(evidence, Mapping):
        raise Stage12Blocked("existing Stage 12 evidence must be a JSON object")
    artifact = load_candidate_universe_artifact(candidate_artifact_path)
    verification = replay_candidate_universe(artifact, expected_evidence=evidence)
    if not verification["verified"]:
        raise Stage12Blocked(
            "EXACT_STAGE12_CANDIDATE_REPLAY_MISMATCH: "
            + json.dumps(verification, sort_keys=True)
        )
    reference = candidate_universe_replay_reference(
        candidate_artifact_path,
        artifact,
        verification,
    )
    evidence = dict(evidence)
    evidence["schema_version"] = STAGE12_SCHEMA_VERSION
    script = dict(evidence.get("script", {}))
    script["version"] = SCRIPT_VERSION
    evidence["script"] = script
    evidence["candidate_universe_replay"] = reference

    updated_gates: list[dict[str, Any]] = []
    replay_gate_added = False
    for original_gate in evidence.get("acceptance_gates", []):
        gate = dict(original_gate)
        if gate.get("id") == "candidate_universe_replay_verified":
            continue
        if gate.get("id") == "selection_method_reproducible":
            gate["passed"] = True
            gate["detail"] = (
                "Deterministic algorithm reproducibility is established by the "
                "authoritative select_panel function and recorded policy; durable "
                "exact-input replayability is established by "
                "candidate_universe_replay_verified."
            )
        updated_gates.append(gate)
        if gate.get("id") == "selection_method_reproducible" and not replay_gate_added:
            updated_gates.append(
                {
                    "id": "candidate_universe_replay_verified",
                    "passed": True,
                    "detail": (
                        "Approved fixed panel reproduced offline from the sanitized "
                        "complete Stage 12 candidate population; no network access, "
                        "original ZIP, MDD, or MDD credential is required."
                    ),
                }
            )
            replay_gate_added = True
    if not replay_gate_added:
        raise Stage12Blocked("existing Stage 12 evidence lacks selection_method_reproducible gate")

    evidence["acceptance_gates"] = updated_gates
    validate_evidence(evidence)
    evidence_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gtfs-zip", type=Path)
    parser.add_argument(
        "--output-json",
        type=Path,
        default=REPOSITORY_ROOT / "docs/stages/12-50-station-selection-evidence.json",
    )
    parser.add_argument(
        "--candidate-universe-output",
        type=Path,
        default=REPOSITORY_ROOT / CANDIDATE_UNIVERSE_ARTIFACT,
    )
    parser.add_argument(
        "--write-candidate-universe",
        action="store_true",
        help="write the sanitized candidate universe from the exact Stage 12 ZIP",
    )
    parser.add_argument(
        "--refresh-existing-evidence-replay",
        action="store_true",
        help="add the offline candidate-universe replay reference without MDD",
    )
    parser.add_argument("--retrieved-at-utc")
    parser.add_argument("--probe-mdd", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.write_candidate_universe and args.refresh_existing_evidence_replay:
        raise Stage12Blocked("candidate-universe write and evidence refresh are mutually exclusive")
    if args.refresh_existing_evidence_replay:
        if args.probe_mdd:
            raise Stage12Blocked("evidence replay refresh never permits MDD requests")
        refresh_existing_evidence_replay_reference(
            args.output_json,
            args.candidate_universe_output,
        )
        print(f"Stage 12 evidence replay reference refreshed: {args.output_json}")
        return 0
    if args.write_candidate_universe:
        if args.gtfs_zip is None:
            raise Stage12Blocked("--gtfs-zip is required when writing candidate evidence")
        artifact = generate_candidate_universe_artifact(
            args.gtfs_zip,
            generated_at_utc=args.retrieved_at_utc,
        )
        write_candidate_universe_artifact(artifact, args.candidate_universe_output)
        print(f"Stage 12 candidate universe written: {args.candidate_universe_output}")
        print(f"Candidate count: {artifact['candidate_count']}")
        print(f"Source ZIP SHA-256: {artifact['feed_sha256']}")
        return 0
    if args.gtfs_zip is None:
        raise Stage12Blocked("--gtfs-zip is required for Stage 12 evidence generation")
    if args.probe_mdd:
        api_key = os.environ.get(MDD_API_KEY_ENV)
        print("MDD_API_KEY: PRESENT" if api_key else "MDD_API_KEY: MISSING", flush=True)
        if not api_key:
            raise Stage12Blocked("MDD_API_KEY is missing; zero MDD requests performed")
    else:
        api_key = None
    evidence = build_evidence(
        gtfs_zip=args.gtfs_zip,
        retrieved_at_utc=args.retrieved_at_utc,
        probe_mdd=args.probe_mdd,
        api_key=api_key,
    )
    validate_evidence(evidence)
    output_path = args.output_json.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Stage 12 evidence written: {output_path}")
    print(evidence["status"])
    return 0 if evidence["status"].startswith("COMPLETE") else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, csv.Error, zipfile.BadZipFile, Stage12Blocked) as exc:
        print(f"stage_12_50_station_selection: error: {exc}", file=sys.stderr)
        raise SystemExit(2)
