#!/usr/bin/env python3
"""Offline Stage 14 manifest, policy, compilation, and architecture checks."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.manifest import load_default_manifest  # noqa: E402
from cologne_transit_collector.schedule import schedule_reconciliation  # noqa: E402
from cologne_transit_collector.trias import parse_trias_response  # noqa: E402
from cologne_transit_stage15.status import (  # noqa: E402
    STAGE_14_COLLECTOR_STATUS,
    STAGE_14_OVERALL_STATUS,
    STAGE_15_LIVE_STATUS,
)


BANNED_IMPORTS = {
    "psycopg",
    "psycopg2",
    "pyodbc",
    "pymssql",
    "win32api",
    "win32con",
    "winreg",
}
BANNED_TEXT = ("powershell", "sql server", "windows task scheduler", "windows\\")
MSSQL_ADAPTER = ROOT / "src" / "cologne_transit_collector" / "mssql_persistence.py"
SQLITE_IMPORTER = ROOT / "src" / "cologne_transit_collector" / "sqlite_import.py"


def architecture_check() -> list[str]:
    errors: list[str] = []
    package = ROOT / "src" / "cologne_transit_collector"
    for path in package.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        lowered = source.lower()
        if "pymssql" in lowered and path != MSSQL_ADAPTER:
            errors.append(f"{path}: optional database client is confined to the direct adapter")
        # The direct adapter is the approved SQL Server persistence boundary;
        # its implementation and explanatory comments may name that
        # authoritative database.  Keep all other architecture text bans
        # unchanged, including PostgreSQL/SQLite/runtime-intermediary checks.
        if path not in {SQLITE_IMPORTER, MSSQL_ADAPTER}:
            for term in BANNED_TEXT:
                if term in lowered:
                    errors.append(f"{path}: banned architecture text {term!r}")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            errors.append(f"{path}: syntax error: {exc.msg}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [str(node.module or "").split(".")[0]]
            else:
                continue
            for name in names:
                if name in BANNED_IMPORTS and not (path == MSSQL_ADAPTER and name == "pymssql"):
                    errors.append(f"{path}: banned import {name}")
        if any(marker in source for marker in ("C:\\", "D:\\", "\\\\vmware-host")):
            errors.append(f"{path}: hardcoded platform path")
    return errors


def direct_adapter_check() -> list[str]:
    errors: list[str] = []
    if not MSSQL_ADAPTER.exists():
        return [f"missing direct persistence adapter: {MSSQL_ADAPTER}"]
    source = MSSQL_ADAPTER.read_text(encoding="utf-8")
    lowered = source.lower()
    for marker in (
        "class mssqlpersistence",
        'importlib.import_module("pymssql")',
        "stg.usppersistmddmacsnapshot",
        "raw_retention_hours = 24",
    ):
        if marker not in lowered:
            errors.append(f"direct adapter is missing required marker: {marker}")
    if any(term in lowered for term in ("sqlite", "psycopg", "pyodbc")):
        errors.append("direct adapter contains a prohibited intermediary/runtime dependency")
    return errors


def run_checks() -> dict[str, object]:
    manifest = load_default_manifest(ROOT)
    reconciliation = schedule_reconciliation(manifest.targets)
    errors = architecture_check() + direct_adapter_check()
    if len(manifest.targets) != 50:
        errors.append("manifest target count is not 50")
    if len(manifest.legacy7_targets) != 7:
        errors.append("manifest Legacy7 count is not 7")
    if len(manifest.new_tiered50_targets) != 43:
        errors.append("manifest new-target count is not 43")
    if reconciliation["super_cycle_counts"] != (19, 18, 18, 18, 19, 18):
        errors.append("schedule distribution differs from Stage 13")
    if reconciliation["super_cycle_total"] != 110 or reconciliation["requests_per_hour"] != 220:
        errors.append("schedule total differs from Stage 13")
    json_fixture = ROOT / "tests" / "fixtures" / "stage14_trias_equivalent.json"
    xml_fixture = ROOT / "tests" / "fixtures" / "stage14_trias_equivalent.xml"
    try:
        json_snapshot = parse_trias_response(json_fixture.read_bytes(), "application/json")
        xml_snapshot = parse_trias_response(xml_fixture.read_bytes(), "application/xml")
        if json_snapshot.result_count != xml_snapshot.result_count:
            errors.append("JSON/XML fixture result counts do not reconcile")
        if len(json_snapshot.recorded_at_time_occurrences) != 6:
            errors.append("RecordedAtTime regression fixture does not cover six contexts")
        if len(xml_snapshot.recorded_at_time_occurrences) != 6:
            errors.append("XML RecordedAtTime regression fixture does not cover six contexts")
    except (OSError, ValueError) as exc:
        errors.append(f"TRIAS fixture validation failed: {type(exc).__name__}")
    correction_json_fixture = ROOT / "tests" / "fixtures" / "stage14_trias_source_contract_correction.json"
    correction_xml_fixture = ROOT / "tests" / "fixtures" / "stage14_trias_source_contract_correction.xml"
    try:
        correction_snapshots = (
            parse_trias_response(correction_json_fixture.read_bytes(), "application/json"),
            parse_trias_response(correction_xml_fixture.read_bytes(), "application/xml"),
        )
        for snapshot in correction_snapshots:
            event = snapshot.events[0]
            if event.get("journey_ref") is not None:
                errors.append("JourneyRef correction fixture promoted a candidate identity")
            if event.get("vehicle_journey_ref") != "vehicle-journey-candidate-1":
                errors.append("VehicleJourneyRef correction fixture was not retained")
            if event.get("dated_vehicle_journey_ref") != "dated-vehicle-journey-candidate-1":
                errors.append("DatedVehicleJourneyRef correction fixture was not retained")
            if event.get("direction_ref") != "direction-ref-1" or event.get("direction") != "Northbound candidate evidence":
                errors.append("DirectionRef/Direction correction fixture was collapsed")
            if any(
                event.get(field_name) is not None
                for field_name in (
                    "pt_mode",
                    "rail_submode",
                    "published_line_name",
                    "origin_stop_point_ref",
                    "origin_text",
                    "destination_stop_point_ref",
                    "destination_text",
                )
            ):
                errors.append("event-level service identity lookalike was promoted")
            if len(event.get("service_attribute_evidence", [])) != 2:
                errors.append("service Attribute/GeneralAttribute evidence was not retained")
            gap_names = {item.get("normalized_field_name") for item in snapshot.parser_gaps}
            if "unmappedserviceextension" not in gap_names:
                errors.append("relevant unmapped source field did not become parser-gap evidence")
            if any(item.get("normalized_field_name") in {"attribute", "generalattribute"} for item in snapshot.parser_gaps):
                errors.append("explicit attribute evidence was duplicated as parser-gap evidence")
    except (IndexError, OSError, ValueError) as exc:
        errors.append(f"source-contract correction validation failed: {type(exc).__name__}")
    stage14_doc = (ROOT / "docs" / "stages" / "14-collector-implementation.md").read_text(encoding="utf-8")
    if STAGE_14_COLLECTOR_STATUS not in stage14_doc:
        errors.append("Stage 14 implementation status is not complete")
    if STAGE_14_OVERALL_STATUS not in stage14_doc:
        errors.append("Stage 14 overall status does not match the active S87 architecture")
    guardrails = (ROOT / "docs" / "GUARDRAILS.md").read_text(encoding="utf-8")
    if STAGE_14_OVERALL_STATUS not in guardrails:
        errors.append("active guardrails do not state the S87 Stage 14 overall status")
    if STAGE_15_LIVE_STATUS not in guardrails:
        errors.append("active guardrails do not state the Stage 15 live status")
    return {
        "status": "PASS" if not errors else "FAIL",
        "simulated_transport_calls": 0,
        "live_network_calls": 0,
        "manifest_targets": len(manifest.targets),
        "legacy7_targets": len(manifest.legacy7_targets),
        "new_tiered50_targets": len(manifest.new_tiered50_targets),
        "schedule": reconciliation,
        "architecture_errors": errors,
        "stage15_status": STAGE_15_LIVE_STATUS,
    }


def main() -> int:
    result = run_checks()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=list))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
