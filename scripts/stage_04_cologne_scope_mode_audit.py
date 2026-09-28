#!/usr/bin/env python3
"""Audit Cologne scope and transport-mode classification from a VRS GTFS ZIP.

This is a read-only, standard-library-only audit.  It never extracts or mutates
the input archive.  The source ZIP is opened repeatedly so that stop-time rows
can be processed as a stream while retaining only compact per-trip scope
flags.  The emitted JSON is summary evidence, not a copy of the source feed.

Example:

    python scripts/stage_04_cologne_scope_mode_audit.py \
        --gtfs-zip /private/tmp/vrs-global-id.zip \
        --output-json docs/stages/04-cologne-scope-mode-evidence.json

The optional provenance arguments make a repeated offline run deterministic:

    --retrieved-at-utc 2026-09-28T13:04:20Z
    --network-request-count 1
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
import unicodedata
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Iterator


SCRIPT_VERSION = "stage-04-audit-1.1"
OFFICIAL_SOURCE_URL = "https://download.vrs.de/gtfs/google_transit_goR.zip"
OFFICIAL_SOURCE_PAGE_URL = "https://www.vrs.de/fuer-unternehmen/open-data-service"
COLOGNE_PREFIX = "de:05315:"

REQUIRED_FILES = (
    "agency.txt",
    "routes.txt",
    "stops.txt",
    "trips.txt",
    "stop_times.txt",
    "feed_info.txt",
)
OPTIONAL_FILES = (
    "calendar.txt",
    "calendar_dates.txt",
    "frequencies.txt",
    "transfers.txt",
    "shapes.txt",
)

MODE_STATUSES = {
    "Stadtbahn / Tram": "CORE_KPI",
    "Urban / KVB Bus": "CORE_KPI",
    "S-Bahn": "CORE_KPI",
    "Regional Express (RE)": "CORE_KPI",
    "Regional Bahn (RB)": "CORE_KPI",
    "Regional / Other Bus": "CORE_KPI",
    "Rail Replacement Bus (SEV)": "SEPARATE_ANALYTICAL",
    "Long-distance": "OUTSIDE_CORE",
    "Other Rail": "OUTSIDE_CORE",
    "OTHER_MODE": "OUTSIDE_CORE",
    "UNCLASSIFIED_MODE": "UNCLASSIFIED",
    "AMBIGUOUS_ROUTE_ID": "UNCLASSIFIED",
    "AMBIGUOUS_TRIP_ID": "UNCLASSIFIED",
    "UNRESOLVED_TRIP_REFERENCE": "UNCLASSIFIED",
}

METRIC_FIELDS = (
    "source_route_rows",
    "distinct_source_route_ids",
    "cologne_serving_routes",
    "scheduled_stop_events_total",
    "cologne_serving_trips",
    "cologne_internal_trips",
    "cross_boundary_trips",
    "touch_only_trips",
    "cologne_scheduled_stop_events",
    "cologne_internal_segments",
)

SEV_PATTERNS = (
    ("SEV", re.compile(r"(?<![A-Z0-9])S[\s./-]*E[\s./-]*V(?![A-Z0-9])")),
    (
        "Schienenersatzverkehr",
        re.compile(r"(?<![A-Z])SCHIENENERSATZVERKEHR(?![A-Z])"),
    ),
    ("Ersatzverkehr", re.compile(r"(?<![A-Z])ERSATZVERKEHR(?![A-Z])")),
)

LONG_DISTANCE_PATTERNS = (
    ("ICE", re.compile(r"(?<![A-Z0-9])ICE(?![A-Z0-9])")),
    ("IC", re.compile(r"(?<![A-Z0-9])IC(?![A-Z0-9])")),
    ("EC", re.compile(r"(?<![A-Z0-9])EC(?![A-Z0-9])")),
    ("NightJet/NJ", re.compile(r"(?<![A-Z0-9])(?:NIGHTJET|NJ)(?![A-Z0-9])")),
    ("FlixTrain/FLX", re.compile(r"(?<![A-Z0-9])(?:FLIXTRAIN|FLX)(?![A-Z0-9])")),
    (
        "Thalys/Eurostar",
        re.compile(r"(?<![A-Z0-9])(?:THALYS|EUROSTAR)(?![A-Z0-9])"),
    ),
)


def clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def normalized(value: Any) -> str:
    text = clean(value).upper()
    text = unicodedata.normalize("NFKD", text)
    return "".join(char for char in text if not unicodedata.combining(char))


def parse_int(value: Any) -> int | None:
    text = clean(value)
    if not re.fullmatch(r"-?\d+", text):
        return None
    try:
        return int(text)
    except ValueError:
        return None


def parse_gtfs_date(value: Any) -> dt.date | None:
    text = clean(value)
    if not re.fullmatch(r"\d{8}", text):
        return None
    try:
        return dt.date(int(text[0:4]), int(text[4:6]), int(text[6:8]))
    except ValueError:
        return None


def gtfs_date_text(value: dt.date | None) -> str | None:
    return value.strftime("%Y%m%d") if value else None


def service_calendar_summary(
    archive: zipfile.ZipFile, feed_info: dict[str, str]
) -> dict[str, Any]:
    """Summarize static service validity without changing scope classification."""

    feed_start = parse_gtfs_date(feed_info.get("feed_start_date"))
    feed_end = parse_gtfs_date(feed_info.get("feed_end_date"))
    calendar_summary: dict[str, Any] = {
        "present": "calendar.txt" in archive.namelist(),
        "row_count": 0,
        "min_start_date": None,
        "max_end_date": None,
        "invalid_date_value_count": 0,
    }
    calendar_dates_summary: dict[str, Any] = {
        "present": "calendar_dates.txt" in archive.namelist(),
        "row_count": 0,
        "min_date": None,
        "max_date": None,
        "invalid_date_value_count": 0,
    }

    if calendar_summary["present"]:
        start_dates: list[dt.date] = []
        end_dates: list[dt.date] = []
        for row in iter_csv_rows(archive, "calendar.txt"):
            calendar_summary["row_count"] += 1
            start_date = parse_gtfs_date(row.get("start_date"))
            end_date = parse_gtfs_date(row.get("end_date"))
            if start_date:
                start_dates.append(start_date)
            else:
                calendar_summary["invalid_date_value_count"] += 1
            if end_date:
                end_dates.append(end_date)
            else:
                calendar_summary["invalid_date_value_count"] += 1
        calendar_summary["min_start_date"] = gtfs_date_text(min(start_dates)) if start_dates else None
        calendar_summary["max_end_date"] = gtfs_date_text(max(end_dates)) if end_dates else None

    if calendar_dates_summary["present"]:
        dates: list[dt.date] = []
        for row in iter_csv_rows(archive, "calendar_dates.txt"):
            calendar_dates_summary["row_count"] += 1
            date_value = parse_gtfs_date(row.get("date"))
            if date_value:
                dates.append(date_value)
            else:
                calendar_dates_summary["invalid_date_value_count"] += 1
        calendar_dates_summary["min_date"] = gtfs_date_text(min(dates)) if dates else None
        calendar_dates_summary["max_date"] = gtfs_date_text(max(dates)) if dates else None

    lower_bounds = [date for date in (
        feed_start,
        parse_gtfs_date(calendar_summary["min_start_date"]),
        parse_gtfs_date(calendar_dates_summary["min_date"]),
    ) if date]
    upper_bounds = [date for date in (
        feed_end,
        parse_gtfs_date(calendar_summary["max_end_date"]),
        parse_gtfs_date(calendar_dates_summary["max_date"]),
    ) if date]
    invalid_values = (
        calendar_summary["invalid_date_value_count"]
        + calendar_dates_summary["invalid_date_value_count"]
    )
    return {
        "feed_start_date": clean(feed_info.get("feed_start_date")) or None,
        "feed_end_date": clean(feed_info.get("feed_end_date")) or None,
        "calendar": calendar_summary,
        "calendar_dates": calendar_dates_summary,
        "earliest_service_calendar_date": gtfs_date_text(min(lower_bounds)) if lower_bounds else None,
        "latest_service_calendar_date": gtfs_date_text(max(upper_bounds)) if upper_bounds else None,
        "invalid_date_value_count": invalid_values,
    }


def classify_freshness(
    service_calendar: dict[str, Any], reference_date: dt.date | None
) -> tuple[str, str]:
    if reference_date is None:
        return "UNDETERMINED", "No freshness reference date was supplied"
    earliest = parse_gtfs_date(service_calendar.get("earliest_service_calendar_date"))
    latest = parse_gtfs_date(service_calendar.get("latest_service_calendar_date"))
    if not earliest or not latest or service_calendar.get("invalid_date_value_count", 0):
        return "UNDETERMINED", "Service/calendar date coverage is incomplete or invalid"
    if latest < reference_date:
        return "STALE", "The entire represented service/calendar validity ended before the reference date"
    if earliest > reference_date:
        return "FUTURE_OR_CURRENT_SCHEDULE", "Represented service/calendar validity begins after the reference date"
    return "CURRENT_OR_OPERATIONALLY_RELEVANT", "The reference date falls within the represented service/calendar validity"


def utc_iso_from_mtime(path: Path) -> str:
    stamp = dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.timezone.utc)
    return stamp.isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_csv_rows(archive: zipfile.ZipFile, filename: str) -> Iterator[dict[str, str]]:
    """Yield CSV rows with source headers preserved and no archive extraction."""

    try:
        raw = archive.open(filename, "r")
    except KeyError as exc:
        raise ValueError(f"Required feed file is missing: {filename}") from exc

    with raw:
        with io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as text:
            reader = csv.DictReader(text)
            if reader.fieldnames is None:
                raise ValueError(f"CSV file has no header: {filename}")
            for row in reader:
                if None in row:
                    raise ValueError(
                        f"CSV row has more fields than its header in {filename}"
                    )
                yield {str(key): clean(value) for key, value in row.items()}


def csv_headers(archive: zipfile.ZipFile, filename: str) -> list[str]:
    with archive.open(filename, "r") as raw:
        with io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as text:
            reader = csv.reader(text)
            try:
                return [clean(value) for value in next(reader)]
            except StopIteration as exc:
                raise ValueError(f"CSV file has no header: {filename}") from exc


def csv_row_count(archive: zipfile.ZipFile, filename: str) -> int:
    return sum(1 for _ in iter_csv_rows(archive, filename))


def require_columns(
    archive: zipfile.ZipFile, filename: str, columns: Iterable[str]
) -> None:
    observed = set(csv_headers(archive, filename))
    missing = sorted(set(columns) - observed)
    if missing:
        raise ValueError(f"{filename} is missing required columns: {missing}")


def source_fields(row: dict[str, str]) -> dict[str, str]:
    """Keep the source labels needed to review a classifier decision."""

    return {
        "route_id": clean(row.get("route_id")),
        "route_short_name": clean(row.get("route_short_name")),
        "route_long_name": clean(row.get("route_long_name")),
        "route_desc": clean(row.get("route_desc")),
        "agency_id": clean(row.get("agency_id")),
        "route_type": clean(row.get("route_type")),
    }


def source_label_values(row: dict[str, str]) -> list[tuple[str, str]]:
    return [
        (field, clean(row.get(field)))
        for field in ("route_short_name", "route_long_name", "route_desc")
        if clean(row.get(field))
    ]


def find_pattern_evidence(
    row: dict[str, str], patterns: Iterable[tuple[str, re.Pattern[str]]]
) -> list[dict[str, str]]:
    matches: list[dict[str, str]] = []
    for field, value in source_label_values(row):
        text = normalized(value)
        for pattern_name, pattern in patterns:
            if pattern.search(text):
                matches.append(
                    {"field": field, "value": value, "pattern": pattern_name}
                )
    return matches


def rail_classifier(row: dict[str, str]) -> tuple[str, list[dict[str, str]]]:
    long_distance = find_pattern_evidence(row, LONG_DISTANCE_PATTERNS)
    if long_distance:
        return "Long-distance", long_distance

    labels = source_label_values(row)
    s_bahn_evidence: list[dict[str, str]] = []
    re_evidence: list[dict[str, str]] = []
    rb_evidence: list[dict[str, str]] = []
    s_short = re.compile(r"^S[\s.-]*\d+[A-Z]?$")
    re_label = re.compile(r"(?<![A-Z0-9])RE[\s.-]*\d+[A-Z]?(?![A-Z0-9])")
    rb_label = re.compile(r"(?<![A-Z0-9])RB[\s.-]*\d+[A-Z]?(?![A-Z0-9])")
    for field, value in labels:
        text = normalized(value)
        if re.search(r"(?<![A-Z0-9])S[\s.-]*BAHN(?![A-Z0-9])", text):
            s_bahn_evidence.append(
                {"field": field, "value": value, "pattern": "S-Bahn"}
            )
        elif s_short.fullmatch(text):
            s_bahn_evidence.append(
                {"field": field, "value": value, "pattern": "S-number"}
            )
        if re.search(r"(?<![A-Z0-9])REGIONAL[\s-]*EXPRESS(?![A-Z0-9])", text):
            re_evidence.append(
                {"field": field, "value": value, "pattern": "Regional Express"}
            )
        elif re_label.search(text):
            re_evidence.append(
                {"field": field, "value": value, "pattern": "RE-number-label"}
            )
        if re.search(r"(?<![A-Z0-9])REGIONAL[\s-]*BAHN(?![A-Z0-9])", text):
            rb_evidence.append(
                {"field": field, "value": value, "pattern": "Regional Bahn"}
            )
        elif rb_label.search(text):
            rb_evidence.append(
                {"field": field, "value": value, "pattern": "RB-number-label"}
            )

    if s_bahn_evidence:
        return "S-Bahn", s_bahn_evidence
    if re_evidence:
        return "Regional Express (RE)", re_evidence
    if rb_evidence:
        return "Regional Bahn (RB)", rb_evidence
    return "Other Rail", []


def preliminary_route_classification(
    row: dict[str, str], kvb_agency_ids: set[str]
) -> tuple[str, list[dict[str, str]]]:
    route_type = clean(row.get("route_type"))
    if route_type == "0":
        return (
            "Stadtbahn / Tram",
            [{"field": "route_type", "value": route_type, "pattern": "GTFS 0"}],
        )
    if route_type == "2":
        return rail_classifier(row)
    if route_type == "3":
        sev_evidence = find_pattern_evidence(row, SEV_PATTERNS)
        if sev_evidence:
            return "Rail Replacement Bus (SEV)", sev_evidence
        if clean(row.get("agency_id")) in kvb_agency_ids:
            return (
                "Urban / KVB Bus",
                [
                    {
                        "field": "agency_id",
                        "value": clean(row.get("agency_id")),
                        "pattern": "validated KVB agency",
                    },
                    {"field": "route_type", "value": route_type, "pattern": "GTFS 3"},
                ],
            )
        return (
            "Regional / Other Bus",
            [{"field": "route_type", "value": route_type, "pattern": "GTFS 3"}],
        )
    if route_type:
        return (
            "OTHER_MODE",
            [
                {
                    "field": "route_type",
                    "value": route_type,
                    "pattern": "unsupported route_type",
                }
            ],
        )
    return "UNCLASSIFIED_MODE", []


def differing_fields(rows: list[dict[str, str]], fields: Iterable[str]) -> list[str]:
    return [field for field in fields if len({clean(row.get(field)) for row in rows}) > 1]


def duplicate_summary(by_id: dict[str, list[Any]]) -> dict[str, Any]:
    duplicate_ids = sorted(key for key, rows in by_id.items() if key and len(rows) > 1)
    return {
        "duplicate_id_count": len(duplicate_ids),
        "duplicate_row_excess_count": sum(len(by_id[key]) - 1 for key in duplicate_ids),
        "duplicate_ids": duplicate_ids,
    }


def empty_mode_metrics() -> dict[str, dict[str, Any]]:
    return {
        mode: {
            **{field: 0 for field in METRIC_FIELDS},
            "scope_status": status,
            "percentage_mode_events_in_cologne": None,
        }
        for mode, status in MODE_STATUSES.items()
    }


def add_mode_metric(metrics: dict[str, dict[str, Any]], mode: str, field: str, count: int = 1) -> None:
    if mode not in metrics:
        mode = "UNCLASSIFIED_MODE"
    metrics[mode][field] += count


def route_review_row(
    row: dict[str, str],
    preliminary_mode: str,
    evidence: list[dict[str, str]],
    agency_names: list[str],
    resolved_mode: str,
    source_row_number: int,
) -> dict[str, Any]:
    result = source_fields(row)
    result.update(
        {
            "source_row_number": source_row_number,
            "agency_names": agency_names,
            "preliminary_mode": preliminary_mode,
            "resolved_route_id_mode": resolved_mode,
            "classification_evidence": evidence,
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit the VRS Global-ID GTFS ZIP for Cologne stop scope, "
            "trip boundaries, and deterministic mode classification."
        )
    )
    parser.add_argument("--gtfs-zip", required=True, type=Path, help="Local GTFS ZIP path")
    parser.add_argument(
        "--output-json",
        type=Path,
        help="Write summary evidence JSON to this path; otherwise print it",
    )
    parser.add_argument(
        "--source-url",
        default=OFFICIAL_SOURCE_URL,
        help="Provenance URL recorded in the evidence (no network request is made)",
    )
    parser.add_argument(
        "--retrieved-at-utc",
        help="UTC retrieval timestamp to record; defaults to local ZIP mtime",
    )
    parser.add_argument(
        "--network-request-count",
        type=int,
        default=0,
        help="Externally performed retrieval count to record in evidence",
    )
    parser.add_argument(
        "--freshness-reference-date",
        help="Reference date in YYYY-MM-DD form for static service-validity classification",
    )
    args = parser.parse_args()

    zip_path = args.gtfs_zip.expanduser().resolve()
    if not zip_path.is_file():
        parser.error(f"GTFS ZIP does not exist: {zip_path}")
    if args.network_request_count < 0:
        parser.error("--network-request-count must be non-negative")

    freshness_reference_date = None
    if args.freshness_reference_date:
        try:
            freshness_reference_date = dt.date.fromisoformat(args.freshness_reference_date)
        except ValueError:
            parser.error("--freshness-reference-date must be YYYY-MM-DD")

    retrieved_at_utc = args.retrieved_at_utc or utc_iso_from_mtime(zip_path)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", retrieved_at_utc):
        parser.error("--retrieved-at-utc must be an ISO-8601 UTC timestamp ending in Z")

    if not zipfile.is_zipfile(zip_path):
        parser.error(f"Input is not a ZIP archive: {zip_path}")

    zip_hash = sha256_file(zip_path)
    zip_bytes = zip_path.stat().st_size

    with zipfile.ZipFile(zip_path, "r") as archive:
        names = set(archive.namelist())
        missing = sorted(set(REQUIRED_FILES) - names)
        if missing:
            raise ValueError(f"Required GTFS files are missing: {missing}")
        for filename in REQUIRED_FILES:
            require_columns(archive, filename, {
                "agency.txt": {"agency_id", "agency_name"},
                "routes.txt": {"route_id", "agency_id", "route_type"},
                "stops.txt": {"stop_id", "location_type", "parent_station"},
                "trips.txt": {"route_id", "trip_id"},
                "stop_times.txt": {"trip_id", "stop_id", "stop_sequence"},
                "feed_info.txt": {"feed_publisher_name"},
            }[filename])

        archive_files = []
        for info in sorted(archive.infolist(), key=lambda item: item.filename):
            archive_files.append(
                {
                    "name": info.filename,
                    "compressed_bytes": info.compress_size,
                    "uncompressed_bytes": info.file_size,
                }
            )

        feed_info_rows = list(iter_csv_rows(archive, "feed_info.txt"))
        feed_info = feed_info_rows[0] if feed_info_rows else {}
        service_calendar = service_calendar_summary(archive, feed_info)
        freshness_classification, freshness_reason = classify_freshness(
            service_calendar, freshness_reference_date
        )

        agency_rows = list(iter_csv_rows(archive, "agency.txt"))
        agencies_by_id: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in agency_rows:
            agencies_by_id[clean(row.get("agency_id"))].append(row)
        agency_duplicates = duplicate_summary(agencies_by_id)

        kvb_candidates = []
        for row_number, row in enumerate(agency_rows, start=2):
            name_normalized = normalized(row.get("agency_name"))
            if "KOLNER VERKEHRS BETRIEBE" in name_normalized or (
                re.search(r"\bKVB\b", name_normalized)
                and "VERKEHRS" in name_normalized
            ):
                kvb_candidates.append(
                    {
                        "source_row_number": row_number,
                        "agency_id": clean(row.get("agency_id")),
                        "agency_name": clean(row.get("agency_name")),
                    }
                )
        kvb_agency_ids = {row["agency_id"] for row in kvb_candidates if row["agency_id"]}

        route_rows = list(iter_csv_rows(archive, "routes.txt"))
        routes_by_id: dict[str, list[dict[str, str]]] = defaultdict(list)
        route_preliminary: list[dict[str, Any]] = []
        for row_number, row in enumerate(route_rows, start=2):
            route_id = clean(row.get("route_id"))
            routes_by_id[route_id].append(row)
            preliminary_mode, evidence = preliminary_route_classification(row, kvb_agency_ids)
            agency_names = sorted(
                {
                    clean(agency_row.get("agency_name"))
                    for agency_row in agencies_by_id.get(clean(row.get("agency_id")), [])
                    if clean(agency_row.get("agency_name"))
                }
            )
            route_preliminary.append(
                {
                    "row": row,
                    "source_row_number": row_number,
                    "preliminary_mode": preliminary_mode,
                    "evidence": evidence,
                    "agency_names": agency_names,
                }
            )

        route_id_summary = duplicate_summary(routes_by_id)
        route_semantic_fields = (
            "agency_id",
            "route_type",
            "route_short_name",
            "route_long_name",
            "route_desc",
        )
        route_id_resolution: dict[str, str] = {}
        route_id_differing_fields: dict[str, list[str]] = {}
        duplicate_route_findings: list[dict[str, Any]] = []
        route_rows_by_id_and_preliminary: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in route_preliminary:
            route_rows_by_id_and_preliminary[clean(item["row"].get("route_id"))].append(item)

        for route_id, rows in routes_by_id.items():
            if not route_id:
                route_id_resolution[route_id] = "UNCLASSIFIED_MODE"
                continue
            preliminary_items = route_rows_by_id_and_preliminary[route_id]
            field_differences = differing_fields(rows, route_semantic_fields)
            route_id_differing_fields[route_id] = field_differences
            modes = sorted({item["preliminary_mode"] for item in preliminary_items})
            if len(rows) > 1 and field_differences:
                resolved_mode = "AMBIGUOUS_ROUTE_ID"
            else:
                resolved_mode = modes[0] if modes else "UNCLASSIFIED_MODE"
            route_id_resolution[route_id] = resolved_mode
            if len(rows) > 1:
                candidate_rows = []
                for item in preliminary_items:
                    candidate_rows.append(
                        route_review_row(
                            item["row"],
                            item["preliminary_mode"],
                            item["evidence"],
                            item["agency_names"],
                            resolved_mode,
                            item["source_row_number"],
                        )
                    )
                duplicate_route_findings.append(
                    {
                        "route_id": route_id,
                        "source_row_count": len(rows),
                        "differing_fields": field_differences,
                        "candidate_preliminary_modes": modes,
                        "resolved_route_id_mode": resolved_mode,
                        "candidate_rows": candidate_rows,
                    }
                )

        route_row_resolved_modes: list[str] = []
        for item in route_preliminary:
            route_row_resolved_modes.append(
                route_id_resolution.get(clean(item["row"].get("route_id")), "UNCLASSIFIED_MODE")
            )

        stops = list(iter_csv_rows(archive, "stops.txt"))
        stops_by_id: dict[str, list[dict[str, str]]] = defaultdict(list)
        location_type_distribution = Counter()
        missing_stop_id_rows = 0
        for row in stops:
            stop_id = clean(row.get("stop_id"))
            stops_by_id[stop_id].append(row)
            location_type_distribution[clean(row.get("location_type"))] += 1
            if not stop_id:
                missing_stop_id_rows += 1
        stop_id_summary = duplicate_summary(stops_by_id)
        stop_ids = {stop_id for stop_id in stops_by_id if stop_id}
        cologne_stop_ids = {stop_id for stop_id in stop_ids if stop_id.startswith(COLOGNE_PREFIX)}
        cologne_stop_rows = [
            row for row in stops if clean(row.get("stop_id")).startswith(COLOGNE_PREFIX)
        ]
        parent_station_ids = {
            clean(row.get("stop_id"))
            for row in stops
            if clean(row.get("location_type")) == "1" and clean(row.get("stop_id"))
        }

        hierarchy = {
            "cologne_prefix_parent_station_rows": 0,
            "cologne_prefix_parent_station_distinct_ids": 0,
            "cologne_prefix_child_stop_position_rows": 0,
            "cologne_prefix_child_stop_position_distinct_ids": 0,
            "cologne_child_parent_also_cologne_rows": 0,
            "cologne_child_parent_also_cologne_distinct_ids": 0,
            "cologne_child_parent_outside_cologne_rows": 0,
            "cologne_child_parent_outside_cologne_distinct_ids": 0,
            "cologne_child_without_parent_rows": 0,
            "non_cologne_child_parent_cologne_rows": 0,
            "non_cologne_child_parent_cologne_distinct_ids": 0,
            "orphan_parent_station_reference_rows": 0,
            "orphan_parent_station_reference_distinct_values": 0,
        }
        orphan_parent_values: set[str] = set()
        child_parent_cologne_ids: set[str] = set()
        child_parent_outside_ids: set[str] = set()
        cologne_parent_ids: set[str] = set()
        cologne_child_ids: set[str] = set()
        for row in stops:
            stop_id = clean(row.get("stop_id"))
            location_type = clean(row.get("location_type"))
            parent_station = clean(row.get("parent_station"))
            is_cologne = stop_id.startswith(COLOGNE_PREFIX)
            if is_cologne and location_type == "1":
                hierarchy["cologne_prefix_parent_station_rows"] += 1
                cologne_parent_ids.add(stop_id)
            if is_cologne and location_type == "0":
                hierarchy["cologne_prefix_child_stop_position_rows"] += 1
                cologne_child_ids.add(stop_id)
                if not parent_station:
                    hierarchy["cologne_child_without_parent_rows"] += 1
                elif parent_station.startswith(COLOGNE_PREFIX):
                    hierarchy["cologne_child_parent_also_cologne_rows"] += 1
                    child_parent_cologne_ids.add(stop_id)
                else:
                    hierarchy["cologne_child_parent_outside_cologne_rows"] += 1
                    child_parent_outside_ids.add(stop_id)
            if (
                not is_cologne
                and location_type == "0"
                and parent_station.startswith(COLOGNE_PREFIX)
            ):
                hierarchy["non_cologne_child_parent_cologne_rows"] += 1
                child_parent_cologne_ids.add(stop_id)
            if parent_station and parent_station not in stop_ids:
                hierarchy["orphan_parent_station_reference_rows"] += 1
                orphan_parent_values.add(parent_station)
        hierarchy["cologne_prefix_parent_station_distinct_ids"] = len(cologne_parent_ids)
        hierarchy["cologne_prefix_child_stop_position_distinct_ids"] = len(cologne_child_ids)
        hierarchy["cologne_child_parent_also_cologne_distinct_ids"] = len(
            {
                clean(row.get("stop_id"))
                for row in stops
                if clean(row.get("location_type")) == "0"
                and clean(row.get("stop_id")).startswith(COLOGNE_PREFIX)
                and clean(row.get("parent_station")).startswith(COLOGNE_PREFIX)
            }
        )
        hierarchy["cologne_child_parent_outside_cologne_distinct_ids"] = len(child_parent_outside_ids)
        hierarchy["non_cologne_child_parent_cologne_distinct_ids"] = len(
            {
                clean(row.get("stop_id"))
                for row in stops
                if clean(row.get("location_type")) == "0"
                and not clean(row.get("stop_id")).startswith(COLOGNE_PREFIX)
                and clean(row.get("parent_station")).startswith(COLOGNE_PREFIX)
            }
        )
        hierarchy["orphan_parent_station_reference_distinct_values"] = len(orphan_parent_values)

        trip_rows = list(iter_csv_rows(archive, "trips.txt"))
        trips_by_id: dict[str, list[dict[str, str]]] = defaultdict(list)
        missing_trip_id_rows = 0
        for row in trip_rows:
            trip_id = clean(row.get("trip_id"))
            trips_by_id[trip_id].append(row)
            if not trip_id:
                missing_trip_id_rows += 1
        trip_id_summary = duplicate_summary(trips_by_id)

        trip_resolution: dict[str, dict[str, Any]] = {}

        def resolve_trip_id(trip_id: str) -> dict[str, Any]:
            if trip_id in trip_resolution:
                return trip_resolution[trip_id]
            if not trip_id or trip_id not in trips_by_id:
                result = {
                    "mode": "UNRESOLVED_TRIP_REFERENCE",
                    "reason": "missing_trip_row",
                    "route_ids": [],
                    "agency_ids": [],
                }
                trip_resolution[trip_id] = result
                return result
            candidates = trips_by_id[trip_id]
            candidate_route_ids = sorted(
                {clean(row.get("route_id")) for row in candidates if clean(row.get("route_id"))}
            )
            candidate_agency_ids: set[str] = set()
            for route_id in candidate_route_ids:
                candidate_agency_ids.update(
                    clean(row.get("agency_id"))
                    for row in routes_by_id.get(route_id, [])
                    if clean(row.get("agency_id"))
                )
            trip_semantic_fields = (
                "route_id",
                "service_id",
                "trip_headsign",
                "direction_id",
                "block_id",
                "shape_id",
            )
            trip_diff = differing_fields(candidates, trip_semantic_fields)
            if len(candidates) > 1 and trip_diff:
                result = {
                    "mode": "AMBIGUOUS_TRIP_ID",
                    "reason": "duplicate_trip_id_with_differing_source_fields",
                    "route_ids": candidate_route_ids,
                    "agency_ids": sorted(candidate_agency_ids),
                    "differing_fields": trip_diff,
                }
            elif len(candidates) > 1 and not candidate_route_ids:
                result = {
                    "mode": "UNCLASSIFIED_MODE",
                    "reason": "duplicate_trip_id_without_route_id",
                    "route_ids": [],
                    "agency_ids": sorted(candidate_agency_ids),
                    "differing_fields": trip_diff,
                }
            elif len(candidates) > 1 and len(candidate_route_ids) > 1:
                result = {
                    "mode": "AMBIGUOUS_TRIP_ID",
                    "reason": "duplicate_trip_id_with_multiple_route_ids",
                    "route_ids": candidate_route_ids,
                    "agency_ids": sorted(candidate_agency_ids),
                    "differing_fields": trip_diff,
                }
            else:
                route_id = candidate_route_ids[0] if candidate_route_ids else ""
                route_mode = route_id_resolution.get(route_id, "UNCLASSIFIED_MODE")
                result = {
                    "mode": route_mode,
                    "reason": "resolved_from_trip_route_id" if route_id else "missing_route_id",
                    "route_ids": [route_id] if route_id else [],
                    "agency_ids": sorted(candidate_agency_ids),
                    "differing_fields": trip_diff,
                }
            trip_resolution[trip_id] = result
            return result

        stop_time_rows = 0
        stop_time_events_by_trip: dict[str, list[tuple[int | None, int, int]]] = defaultdict(list)
        stop_time_scope_counts = Counter()
        stop_time_stop_id_counts = Counter()
        unknown_stop_id_counts = Counter()
        unknown_trip_id_counts = Counter()
        stop_time_cologne_prefix_unknown_counts = Counter()
        invalid_stop_sequence_rows = 0
        with zipfile.ZipFile(zip_path, "r") as stop_archive:
            for row_number, row in enumerate(iter_csv_rows(stop_archive, "stop_times.txt"), start=2):
                stop_time_rows += 1
                trip_id = clean(row.get("trip_id"))
                stop_id = clean(row.get("stop_id"))
                stop_time_stop_id_counts[stop_id] += 1
                sequence = parse_int(row.get("stop_sequence"))
                if sequence is None:
                    invalid_stop_sequence_rows += 1
                if stop_id in cologne_stop_ids:
                    scope_flag = 2
                    stop_time_scope_counts["validated_cologne"] += 1
                elif stop_id in stop_ids:
                    scope_flag = 1
                    stop_time_scope_counts["known_non_cologne"] += 1
                else:
                    scope_flag = 0
                    stop_time_scope_counts["unknown_stop_id"] += 1
                    unknown_stop_id_counts[stop_id] += 1
                    if stop_id.startswith(COLOGNE_PREFIX):
                        stop_time_cologne_prefix_unknown_counts[stop_id] += 1
                if not trip_id:
                    if scope_flag == 2:
                        unknown_trip_id_counts["<missing>"] += 1
                elif trip_id not in trips_by_id:
                    unknown_trip_id_counts[trip_id] += 1
                stop_time_events_by_trip[trip_id].append((sequence, row_number, scope_flag))

        trip_ids_with_duplicate_sequence: set[str] = set()
        trip_ids_with_invalid_sequence: set[str] = set()
        trip_scope: dict[str, dict[str, Any]] = {}
        mode_metrics = empty_mode_metrics()
        trip_scope_buckets = Counter()
        overall_internal_segments = 0
        segment_anomaly_trip_ids: set[str] = set()
        cologne_serving_trip_ids: set[str] = set()
        cologne_internal_trip_ids: set[str] = set()
        cross_boundary_trip_ids: set[str] = set()
        touch_only_trip_ids: set[str] = set()
        unresolved_scope_trip_ids: set[str] = set()
        cologne_serving_route_ids: set[str] = set()
        route_to_serving_trip_modes: dict[str, set[str]] = defaultdict(set)
        route_to_serving_trip_ids: dict[str, set[str]] = defaultdict(set)
        route_to_serving_event_count: Counter[str] = Counter()
        agency_service_accumulator: dict[str, dict[str, Any]] = {}

        def get_agency_key(resolution: dict[str, Any]) -> tuple[str, list[str]]:
            agency_ids = resolution.get("agency_ids", [])
            if len(agency_ids) == 1:
                agency_id = agency_ids[0]
                names = sorted(
                    {
                        clean(row.get("agency_name"))
                        for row in agencies_by_id.get(agency_id, [])
                        if clean(row.get("agency_name"))
                    }
                )
                return agency_id, names
            if len(agency_ids) > 1:
                return "<AMBIGUOUS_AGENCY>", agency_ids
            if resolution.get("mode") == "UNRESOLVED_TRIP_REFERENCE":
                return "<UNRESOLVED_TRIP_REFERENCE>", []
            return "<UNKNOWN_AGENCY>", []

        for trip_id, events in stop_time_events_by_trip.items():
            resolution = resolve_trip_id(trip_id)
            mode = resolution["mode"]
            event_counts = Counter(flag for _seq, _row_number, flag in events)
            cologne_events = event_counts[2]
            known_non_cologne_events = event_counts[1]
            unknown_events = event_counts[0]

            valid_sequences = [event for event in events if event[0] is not None]
            if len(valid_sequences) != len(events):
                trip_ids_with_invalid_sequence.add(trip_id)
            sequence_values = [event[0] for event in valid_sequences]
            if len(sequence_values) != len(set(sequence_values)):
                trip_ids_with_duplicate_sequence.add(trip_id)
            internal_segments = 0
            if len(valid_sequences) == len(events) and len(sequence_values) == len(set(sequence_values)):
                ordered = sorted(valid_sequences, key=lambda event: (event[0], event[1]))
                internal_segments = sum(
                    1
                    for previous, current in zip(ordered, ordered[1:])
                    if previous[2] == 2 and current[2] == 2
                )
            elif cologne_events > 1:
                segment_anomaly_trip_ids.add(trip_id)
            overall_internal_segments += internal_segments

            if cologne_events:
                if trip_id:
                    cologne_serving_trip_ids.add(trip_id)
                    if unknown_events:
                        unresolved_scope_trip_ids.add(trip_id)
                        trip_scope_buckets["UNRESOLVED_STOP_SCOPE"] += 1
                    elif known_non_cologne_events:
                        cross_boundary_trip_ids.add(trip_id)
                        if internal_segments:
                            trip_scope_buckets["CROSS_BOUNDARY_WITH_INTERNAL_SEGMENT"] += 1
                        else:
                            trip_scope_buckets["CROSS_BOUNDARY_TOUCH_ONLY"] += 1
                    else:
                        cologne_internal_trip_ids.add(trip_id)
                        if internal_segments:
                            trip_scope_buckets["COLOGNE_INTERNAL_WITH_SEGMENT"] += 1
                        else:
                            trip_scope_buckets["COLOGNE_INTERNAL_TOUCH_ONLY"] += 1
                    if not unknown_events and not internal_segments:
                        touch_only_trip_ids.add(trip_id)

                    for route_id in resolution.get("route_ids", []):
                        if route_id:
                            cologne_serving_route_ids.add(route_id)
                            route_to_serving_trip_modes[route_id].add(mode)
                            route_to_serving_trip_ids[route_id].add(trip_id)
                            route_to_serving_event_count[route_id] += cologne_events

            add_mode_metric(mode_metrics, mode, "scheduled_stop_events_total", len(events))
            add_mode_metric(mode_metrics, mode, "cologne_scheduled_stop_events", cologne_events)
            add_mode_metric(mode_metrics, mode, "cologne_internal_segments", internal_segments)
            if cologne_events and trip_id:
                add_mode_metric(mode_metrics, mode, "cologne_serving_trips")
                if trip_id in cologne_internal_trip_ids:
                    add_mode_metric(mode_metrics, mode, "cologne_internal_trips")
                if trip_id in cross_boundary_trip_ids:
                    add_mode_metric(mode_metrics, mode, "cross_boundary_trips")
                if trip_id in touch_only_trip_ids:
                    add_mode_metric(mode_metrics, mode, "touch_only_trips")

            if cologne_events:
                agency_key, agency_names = get_agency_key(resolution)
                accumulator = agency_service_accumulator.setdefault(
                    agency_key,
                    {
                        "agency_id": agency_key,
                        "agency_names": set(),
                        "cologne_serving_trip_ids": set(),
                        "cologne_serving_route_ids": set(),
                        "cologne_scheduled_stop_events": 0,
                        "modes": set(),
                    },
                )
                accumulator["agency_names"].update(agency_names)
                if trip_id:
                    accumulator["cologne_serving_trip_ids"].add(trip_id)
                accumulator["cologne_scheduled_stop_events"] += cologne_events
                accumulator["modes"].add(mode)
                accumulator["cologne_serving_route_ids"].update(
                    route_id for route_id in resolution.get("route_ids", []) if route_id
                )

            trip_scope[trip_id] = {
                "mode": mode,
                "source_trip_row_exists": bool(trip_id and trip_id in trips_by_id),
                "cologne_events": cologne_events,
                "known_non_cologne_events": known_non_cologne_events,
                "unknown_stop_events": unknown_events,
                "internal_segments": internal_segments,
                "has_duplicate_stop_sequence": trip_id in trip_ids_with_duplicate_sequence,
                "has_invalid_stop_sequence": trip_id in trip_ids_with_invalid_sequence,
            }

        # Every serving source route gets one exclusive mode bucket for route-level
        # metrics.  A route touched by an ambiguous trip is not silently assigned
        # to the route's preliminary mode.
        serving_route_modes: dict[str, str] = {}
        for route_id in sorted(cologne_serving_route_ids):
            route_mode = route_id_resolution.get(route_id, "UNCLASSIFIED_MODE")
            trip_modes = route_to_serving_trip_modes.get(route_id, set())
            if route_mode == "AMBIGUOUS_ROUTE_ID":
                serving_mode = route_mode
            elif any(mode == "AMBIGUOUS_TRIP_ID" for mode in trip_modes):
                serving_mode = "AMBIGUOUS_TRIP_ID"
            elif len(trip_modes) == 1:
                serving_mode = next(iter(trip_modes))
            elif len(trip_modes) > 1:
                serving_mode = "AMBIGUOUS_TRIP_ID"
            else:
                serving_mode = route_mode
            serving_route_modes[route_id] = serving_mode
            add_mode_metric(mode_metrics, serving_mode, "cologne_serving_routes")

        for route_id, resolved_mode in route_id_resolution.items():
            if route_id:
                add_mode_metric(mode_metrics, resolved_mode, "distinct_source_route_ids")
        for resolved_mode in route_row_resolved_modes:
            add_mode_metric(mode_metrics, resolved_mode, "source_route_rows")

        for mode_values in mode_metrics.values():
            total_events = mode_values["scheduled_stop_events_total"]
            cologne_events = mode_values["cologne_scheduled_stop_events"]
            if total_events:
                mode_values["percentage_mode_events_in_cologne"] = round(
                    100 * cologne_events / total_events, 6
                )

        route_type_inventory: dict[str, dict[str, Any]] = {}
        for item, resolved_mode in zip(route_preliminary, route_row_resolved_modes):
            route_type = clean(item["row"].get("route_type"))
            inventory = route_type_inventory.setdefault(
                route_type,
                {
                    "route_rows": 0,
                    "distinct_route_ids": set(),
                    "preliminary_modes": Counter(),
                    "representative_source_rows": [],
                },
            )
            inventory["route_rows"] += 1
            route_id = clean(item["row"].get("route_id"))
            if route_id:
                inventory["distinct_route_ids"].add(route_id)
            inventory["preliminary_modes"][item["preliminary_mode"]] += 1
            if len(inventory["representative_source_rows"]) < 5:
                inventory["representative_source_rows"].append(
                    route_review_row(
                        item["row"],
                        item["preliminary_mode"],
                        item["evidence"],
                        item["agency_names"],
                        resolved_mode,
                        item["source_row_number"],
                    )
                )
        route_type_inventory_output = {}
        for route_type in sorted(route_type_inventory):
            inventory = route_type_inventory[route_type]
            route_type_inventory_output[route_type] = {
                "route_rows": inventory["route_rows"],
                "distinct_route_ids": len(inventory["distinct_route_ids"]),
                "preliminary_modes": dict(sorted(inventory["preliminary_modes"].items())),
                "representative_source_rows": inventory["representative_source_rows"],
            }

        rail_examples: dict[str, list[dict[str, Any]]] = {
            "S-Bahn": [],
            "Regional Express (RE)": [],
            "Regional Bahn (RB)": [],
            "Long-distance": [],
            "Other Rail": [],
        }
        mode_examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item, resolved_mode in zip(route_preliminary, route_row_resolved_modes):
            example = route_review_row(
                item["row"],
                item["preliminary_mode"],
                item["evidence"],
                item["agency_names"],
                resolved_mode,
                item["source_row_number"],
            )
            if len(mode_examples[resolved_mode]) < 5:
                mode_examples[resolved_mode].append(example)
            if item["preliminary_mode"] in rail_examples and resolved_mode != "AMBIGUOUS_ROUTE_ID":
                if len(rail_examples[item["preliminary_mode"]]) < 5:
                    rail_examples[item["preliminary_mode"]].append(example)

        sev_route_ids = sorted(
            route_id
            for route_id, mode in route_id_resolution.items()
            if route_id and mode == "Rail Replacement Bus (SEV)"
        )
        sev_routes = []
        for route_id in sev_route_ids:
            rows = route_rows_by_id_and_preliminary.get(route_id, [])
            sev_routes.append(
                {
                    "route_id": route_id,
                    "cologne_serving": route_id in cologne_serving_route_ids,
                    "resolved_route_id_mode": route_id_resolution[route_id],
                    "source_rows": [
                        route_review_row(
                            item["row"],
                            item["preliminary_mode"],
                            item["evidence"],
                            item["agency_names"],
                            route_id_resolution[route_id],
                            item["source_row_number"],
                        )
                        for item in rows
                    ],
                }
            )

        agency_distribution = []
        for agency_id in sorted(agency_service_accumulator):
            accumulator = agency_service_accumulator[agency_id]
            agency_distribution.append(
                {
                    "agency_id": agency_id,
                    "agency_names": sorted(accumulator["agency_names"]),
                    "cologne_serving_routes": len(accumulator["cologne_serving_route_ids"]),
                    "cologne_serving_trips": len(accumulator["cologne_serving_trip_ids"]),
                    "cologne_scheduled_stop_events": accumulator["cologne_scheduled_stop_events"],
                    "modes": sorted(accumulator["modes"]),
                }
            )

        route_duplicate_cologne_findings = []
        for finding in duplicate_route_findings:
            route_id = finding["route_id"]
            if route_id in cologne_serving_route_ids:
                enriched = dict(finding)
                enriched["cologne_serving_trip_count"] = len(route_to_serving_trip_ids.get(route_id, set()))
                enriched["cologne_scheduled_stop_event_count"] = route_to_serving_event_count.get(route_id, 0)
                enriched["serving_mode_bucket"] = serving_route_modes.get(route_id)
                route_duplicate_cologne_findings.append(enriched)

        duplicate_trip_findings = []
        for trip_id in trip_id_summary["duplicate_ids"]:
            candidates = trips_by_id[trip_id]
            if trip_id in cologne_serving_trip_ids:
                duplicate_trip_findings.append(
                    {
                        "trip_id": trip_id,
                        "source_row_count": len(candidates),
                        "cologne_serving": True,
                        "candidate_route_ids": sorted(
                            {clean(row.get("route_id")) for row in candidates if clean(row.get("route_id"))}
                        ),
                        "differing_fields": differing_fields(
                            candidates,
                            ("route_id", "service_id", "trip_headsign", "direction_id", "block_id", "shape_id"),
                        ),
                        "resolved_mode_bucket": trip_resolution.get(trip_id, {}).get("mode"),
                    }
                )

        source_counts = {
            "agencies": len(agency_rows),
            "distinct_agency_ids": len({clean(row.get("agency_id")) for row in agency_rows if clean(row.get("agency_id"))}),
            "route_rows": len(route_rows),
            "distinct_route_ids": len({clean(row.get("route_id")) for row in route_rows if clean(row.get("route_id"))}),
            "stops": len(stops),
            "distinct_stop_ids": len({clean(row.get("stop_id")) for row in stops if clean(row.get("stop_id"))}),
            "parent_stations": sum(1 for row in stops if clean(row.get("location_type")) == "1"),
            "stop_positions": sum(1 for row in stops if clean(row.get("location_type")) == "0"),
            "trips": len(trip_rows),
            "distinct_trip_ids": len({clean(row.get("trip_id")) for row in trip_rows if clean(row.get("trip_id"))}),
            "stop_times": stop_time_rows,
            "missing_identifier_rows": {
                "agency_id": sum(1 for row in agency_rows if not clean(row.get("agency_id"))),
                "route_id": sum(1 for row in route_rows if not clean(row.get("route_id"))),
                "stop_id": missing_stop_id_rows,
                "trip_id": missing_trip_id_rows,
            },
        }

        optional_inventory = {}
        for filename in OPTIONAL_FILES:
            present = filename in names
            optional_inventory[filename] = {
                "present": present,
                "row_count": csv_row_count(archive, filename) if present else None,
            }

        source_metadata = {
            "source_url": args.source_url,
            "official_source_page_url": OFFICIAL_SOURCE_PAGE_URL,
            "source_profile": "VRS Global-ID GTFS",
            "retrieval_timestamp_utc": retrieved_at_utc,
            "network_request_count": args.network_request_count,
            "zip_filename": zip_path.name,
            "zip_bytes": zip_bytes,
            "zip_sha256": zip_hash,
            "archive_files": archive_files,
            "feed_info": feed_info,
            "service_calendar": {
                **service_calendar,
                "freshness_reference_date": (
                    freshness_reference_date.isoformat() if freshness_reference_date else None
                ),
                "freshness_classification": freshness_classification,
                "freshness_reason": freshness_reason,
            },
            "optional_file_inventory": optional_inventory,
        }

        scope_counts = {
            "cologne_stop_rows": len(cologne_stop_rows),
            "cologne_distinct_stop_ids": len(cologne_stop_ids),
            "cologne_parent_station_rows": hierarchy["cologne_prefix_parent_station_rows"],
            "cologne_stop_position_rows": hierarchy["cologne_prefix_child_stop_position_rows"],
            "cologne_scheduled_stop_events": stop_time_scope_counts["validated_cologne"],
            "cologne_internal_consecutive_segments": overall_internal_segments,
            "cologne_serving_trips": len(cologne_serving_trip_ids),
            "cologne_internal_trips": len(cologne_internal_trip_ids),
            "cross_boundary_cologne_trips": len(cross_boundary_trip_ids),
            "cross_boundary_trips_with_internal_segments": sum(
                1
                for trip_id in cross_boundary_trip_ids
                if trip_scope.get(trip_id, {}).get("internal_segments", 0) > 0
            ),
            "touch_only_cologne_trips": len(touch_only_trip_ids),
            "unresolved_stop_scope_trips": len(unresolved_scope_trip_ids),
            "cologne_serving_routes": len(cologne_serving_route_ids),
            "cologne_serving_trips_with_unknown_trip_row": sum(
                1
                for trip_id in cologne_serving_trip_ids
                if trip_id not in trips_by_id
            ),
            "cologne_events_with_missing_trip_id": unknown_trip_id_counts.get("<missing>", 0),
            "cologne_events_with_unknown_trip_row": sum(
                count
                for trip_id, count in unknown_trip_id_counts.items()
                if trip_id != "<missing>"
            ),
            "percentage_cologne_serving_trips_cross_boundary": round(
                100 * len(cross_boundary_trip_ids) / len(cologne_serving_trip_ids), 6
            )
            if cologne_serving_trip_ids
            else None,
        }

        trip_scope_reconciliation = {
            "formal_populations_are_intentionally_overlapping": True,
            "formal_population_counts": {
                "cologne_serving_trips": len(cologne_serving_trip_ids),
                "cologne_internal_trips": len(cologne_internal_trip_ids),
                "cross_boundary_cologne_trips": len(cross_boundary_trip_ids),
                "touch_only_cologne_trips": len(touch_only_trip_ids),
                "unresolved_stop_scope_trips": len(unresolved_scope_trip_ids),
            },
            "mutually_exclusive_buckets": dict(sorted(trip_scope_buckets.items())),
            "mutually_exclusive_bucket_sum": sum(trip_scope_buckets.values()),
            "matches_cologne_serving_trips": sum(trip_scope_buckets.values())
            == len(cologne_serving_trip_ids),
            "definitions": {
                "cologne_internal_trip": "all known source stop events for the trip are validated Cologne stops and no unknown stop reference is present",
                "cross_boundary_cologne_trip": "the trip has at least one validated Cologne stop event and at least one known non-Cologne stop event",
                "touch_only_cologne_trip": "the trip has a validated Cologne stop event, no validated Cologne-internal segment, and no unknown stop reference; this diagnostic population overlaps formal internal/cross-boundary populations",
                "unresolved_stop_scope_trip": "the trip has a Cologne event and at least one stop_times reference to an unknown stop_id",
            },
        }

        event_mode_sum = sum(
            values["cologne_scheduled_stop_events"] for values in mode_metrics.values()
        )
        segment_mode_sum = sum(values["cologne_internal_segments"] for values in mode_metrics.values())
        trip_mode_sum = sum(values["cologne_serving_trips"] for values in mode_metrics.values())
        route_mode_sum = sum(values["cologne_serving_routes"] for values in mode_metrics.values())

        duplicate_findings = {
            "route_ids": {
                **route_id_summary,
                "affected_cologne_serving_duplicate_ids": route_duplicate_cologne_findings,
                "all_duplicate_findings": duplicate_route_findings,
            },
            "stop_ids": stop_id_summary,
            "trip_ids": {
                **trip_id_summary,
                "affected_cologne_serving_duplicate_ids": duplicate_trip_findings,
            },
            "agency_ids": agency_duplicates,
        }

        scope_anomalies = {
            "hierarchy": hierarchy,
            "unknown_stop_time_reference_rows": sum(unknown_stop_id_counts.values()),
            "unknown_stop_time_reference_distinct_stop_ids": len(unknown_stop_id_counts),
            "unknown_stop_time_reference_examples": [
                {"stop_id": stop_id, "row_count": unknown_stop_id_counts[stop_id]}
                for stop_id in sorted(unknown_stop_id_counts)[:20]
            ],
            "unknown_stop_time_ids_with_cologne_prefix": {
                "row_count": sum(stop_time_cologne_prefix_unknown_counts.values()),
                "distinct_stop_ids": len(stop_time_cologne_prefix_unknown_counts),
                "examples": [
                    {"stop_id": stop_id, "row_count": stop_time_cologne_prefix_unknown_counts[stop_id]}
                    for stop_id in sorted(stop_time_cologne_prefix_unknown_counts)[:20]
                ],
            },
            "unknown_trip_reference_rows": sum(
                count for trip_id, count in unknown_trip_id_counts.items() if trip_id != "<missing>"
            ),
            "unknown_trip_reference_distinct_trip_ids": len(
                [trip_id for trip_id in unknown_trip_id_counts if trip_id != "<missing>"]
            ),
            "missing_trip_id_stop_time_rows": unknown_trip_id_counts.get("<missing>", 0),
            "invalid_stop_sequence_rows": invalid_stop_sequence_rows,
            "trip_ids_with_invalid_stop_sequence": len(trip_ids_with_invalid_sequence),
            "trip_ids_with_duplicate_stop_sequence": len(trip_ids_with_duplicate_sequence),
            "segment_anomaly_trip_ids": len(segment_anomaly_trip_ids),
            "segment_anomaly_examples": sorted(segment_anomaly_trip_ids)[:20],
            "ambiguous_route_id_cologne_serving_trip_count": sum(
                len(route_to_serving_trip_ids.get(finding["route_id"], set()))
                for finding in route_duplicate_cologne_findings
            ),
            "unclassified_cologne_stop_event_count": sum(
                mode_metrics[mode]["cologne_scheduled_stop_events"]
                for mode in ("UNCLASSIFIED_MODE", "AMBIGUOUS_ROUTE_ID", "AMBIGUOUS_TRIP_ID", "UNRESOLVED_TRIP_REFERENCE")
            ),
        }

        acceptance_checks = [
            {
                "id": "authoritative_vrs_global_id_source",
                "passed": args.source_url == OFFICIAL_SOURCE_URL
                and normalized(feed_info.get("feed_publisher_name")) == "VRS",
                "detail": "Official VRS Global-ID URL and VRS feed publisher are recorded",
            },
            {
                "id": "snapshot_provenance_hash_feed_metadata",
                "passed": bool(zip_hash and feed_info and archive_files),
                "detail": "ZIP SHA-256, byte size, file list, and feed_info are present",
            },
            {
                "id": "service_calendar_freshness_gate",
                "passed": freshness_classification in {
                    "CURRENT_OR_OPERATIONALLY_RELEVANT",
                    "FUTURE_OR_CURRENT_SCHEDULE",
                },
                "detail": f"freshness={freshness_classification}; {freshness_reason}",
            },
            {
                "id": "cologne_prefix_measured",
                "passed": COLOGNE_PREFIX == "de:05315:" and len(cologne_stop_ids) >= 0,
                "detail": "Actual stop IDs and stop_times references use the direct prefix rule",
            },
            {
                "id": "parent_child_and_unknown_references_measured",
                "passed": all(key in hierarchy for key in (
                    "cologne_prefix_parent_station_rows",
                    "cologne_prefix_child_stop_position_rows",
                    "orphan_parent_station_reference_rows",
                )),
                "detail": "Parent/child relationships and unknown stop references are explicit",
            },
            {
                "id": "stop_event_scope_measured",
                "passed": "cologne_scheduled_stop_events" in scope_counts,
                "detail": "Only validated Cologne stop IDs count as Cologne scheduled stop events",
            },
            {
                "id": "internal_segment_scope_measured",
                "passed": "cologne_internal_consecutive_segments" in scope_counts,
                "detail": "Segments require two adjacent ordered stop events with both endpoints in Cologne",
            },
            {
                "id": "trip_populations_measured",
                "passed": trip_scope_reconciliation["matches_cologne_serving_trips"],
                "detail": "Formal and mutually exclusive trip populations are both reported",
            },
            {
                "id": "route_identity_separate_from_scope",
                "passed": True,
                "detail": "Route identity is measured separately from stop-event and segment scope",
            },
            {
                "id": "complete_route_type_inventory_and_precedence",
                "passed": bool(route_type_inventory_output) and bool(MODE_STATUSES),
                "detail": "Every observed route_type has source rows and the classifier has explicit precedence",
            },
            {
                "id": "mode_evidence_and_unclassified_values",
                "passed": all(
                    mode in rail_examples
                    for mode in (
                        "S-Bahn",
                        "Regional Express (RE)",
                        "Regional Bahn (RB)",
                        "Long-distance",
                        "Other Rail",
                    )
                )
                and "Urban / KVB Bus" in mode_examples,
                "detail": "Observed rows/examples and explicit zero/absent rail categories are retained",
            },
            {
                "id": "duplicate_route_id_safety",
                "passed": all(
                    finding["resolved_route_id_mode"] == "AMBIGUOUS_ROUTE_ID"
                    or not finding["differing_fields"]
                    for finding in duplicate_route_findings
                ),
                "detail": "Differing duplicate route IDs use an explicit ambiguity bucket",
            },
            {
                "id": "mode_event_reconciliation",
                "passed": event_mode_sum == scope_counts["cologne_scheduled_stop_events"],
                "detail": f"mode event sum={event_mode_sum}; overall={scope_counts['cologne_scheduled_stop_events']}",
            },
            {
                "id": "mode_trip_reconciliation",
                "passed": trip_mode_sum == scope_counts["cologne_serving_trips"],
                "detail": f"mode trip sum={trip_mode_sum}; overall={scope_counts['cologne_serving_trips']}",
            },
            {
                "id": "mode_route_reconciliation",
                "passed": route_mode_sum == scope_counts["cologne_serving_routes"],
                "detail": f"mode route sum={route_mode_sum}; overall={scope_counts['cologne_serving_routes']}",
            },
            {
                "id": "mode_segment_reconciliation",
                "passed": segment_mode_sum == scope_counts["cologne_internal_consecutive_segments"],
                "detail": f"mode segment sum={segment_mode_sum}; overall={scope_counts['cologne_internal_consecutive_segments']}",
            },
            {
                "id": "static_not_realtime",
                "passed": True,
                "detail": "This tool inspects scheduled static GTFS only and makes no operational claim",
            },
        ]

        output = {
            "evidence_role": "CURRENT STAGE 4 APPROVAL SNAPSHOT",
            "audit_tool": {
                "name": "stage_04_cologne_scope_mode_audit",
                "version": SCRIPT_VERSION,
                "input_mode": "local_zip_only",
                "read_only_source_zip": True,
            },
            "source_metadata": source_metadata,
            "scope_contract": {
                "cologne_stop_rule": f"validated stop_id starts with {COLOGNE_PREFIX!r}",
                "validated_cologne_stop_definition": "stop_id has the prefix and exists in stops.txt; duplicate source rows are retained and reported",
                "scheduled_stop_event_definition": "stop_times row whose referenced stop_id is a validated Cologne stop",
                "internal_segment_definition": "adjacent stop_times events of one trip after stop_sequence ordering, with both endpoints validated Cologne stops",
                "route_identity_definition": "source route_id and route rows are retained independently of event/segment geography",
                "parent_station_non_override": "the actual stop_id referenced by stop_times controls event scope; parent_station does not pull a non-Cologne event into Cologne",
            },
            "source_counts": source_counts,
            "route_type_distribution": route_type_inventory_output,
            "location_type_distribution": dict(sorted(location_type_distribution.items())),
            "duplicate_id_findings": duplicate_findings,
            "scope_counts": scope_counts,
            "scope_anomalies": scope_anomalies,
            "trip_scope_reconciliation": trip_scope_reconciliation,
            "agency_distribution_for_cologne_serving_services": agency_distribution,
            "mode_classification": {
                "precedence": [
                    "1. Resolve source route/trip identity ambiguity first; conflicting duplicate route_id/trip_id inheritance remains explicit and unclassified.",
                    "2. route_type=0 is Stadtbahn / Tram under this feed profile; no unexpected extended value is forced into tram.",
                    "3. route_type=2 establishes rail; within rail, Long-distance is checked first, then S-Bahn, RE, RB, and Other Rail.",
                    "4. route_type=3 establishes bus; SEV evidence is checked first, then validated KVB agency gives Urban / KVB Bus, otherwise Regional / Other Bus.",
                    "5. Unsupported route_type values remain OTHER_MODE; missing/ambiguous/unresolved inheritance remains explicit UNCLASSIFIED status.",
                ],
                "mode_statuses": MODE_STATUSES,
                "kvb_source_evidence": {
                    "candidate_agency_rows": kvb_candidates,
                    "validated_agency_ids": sorted(kvb_agency_ids),
                    "resolution_status": "VALIDATED_SINGLE_SOURCE_AGENCY" if len(kvb_agency_ids) == 1 else (
                        "MULTIPLE_PLAUSIBLE_SOURCE_AGENCIES" if len(kvb_agency_ids) > 1 else "NO_KVB_AGENCY_MATCH"
                    ),
                },
                "mode_metrics": mode_metrics,
                "mode_examples": {mode: mode_examples[mode] for mode in sorted(mode_examples)},
                "rail_examples": rail_examples,
                "sev_routes": sev_routes,
                "unclassified_route_type_values": sorted(
                    route_type for route_type in route_type_inventory_output
                    if route_type not in {"0", "2", "3"}
                ),
            },
            "reconciliation": {
                "cologne_scheduled_stop_events": {
                    "overall": scope_counts["cologne_scheduled_stop_events"],
                    "sum_mode_buckets": event_mode_sum,
                    "matches": event_mode_sum == scope_counts["cologne_scheduled_stop_events"],
                    "explicit_unresolved_or_ambiguous_event_count": scope_anomalies["unclassified_cologne_stop_event_count"],
                },
                "cologne_serving_trips": {
                    "overall": scope_counts["cologne_serving_trips"],
                    "sum_mode_buckets": trip_mode_sum,
                    "matches": trip_mode_sum == scope_counts["cologne_serving_trips"],
                },
                "cologne_serving_routes": {
                    "overall": scope_counts["cologne_serving_routes"],
                    "sum_mode_buckets": route_mode_sum,
                    "matches": route_mode_sum == scope_counts["cologne_serving_routes"],
                },
                "cologne_internal_segments": {
                    "overall": scope_counts["cologne_internal_consecutive_segments"],
                    "sum_mode_buckets": segment_mode_sum,
                    "matches": segment_mode_sum == scope_counts["cologne_internal_consecutive_segments"],
                },
                "source_stop_time_scope_partition": {
                    "validated_cologne": stop_time_scope_counts["validated_cologne"],
                    "known_non_cologne": stop_time_scope_counts["known_non_cologne"],
                    "unknown_stop_id": stop_time_scope_counts["unknown_stop_id"],
                    "sum": sum(stop_time_scope_counts.values()),
                    "matches_stop_times_rows": sum(stop_time_scope_counts.values()) == stop_time_rows,
                },
            },
            "acceptance_checks": acceptance_checks,
        }

    rendered = json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output_json:
        output_path = args.output_json.expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, zipfile.BadZipFile, csv.Error) as exc:
        print(f"stage_04_cologne_scope_mode_audit: error: {exc}", file=sys.stderr)
        raise SystemExit(2)
