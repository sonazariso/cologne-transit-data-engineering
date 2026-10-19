#!/usr/bin/env python3
"""Validate Tiered50 materialization statically or through read-only SQL."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.manifest import load_default_manifest  # noqa: E402
from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlConnectionSettings,
    MssqlPersistence,
)


MIGRATION = ROOT / "sql" / "mdd-mac-collector" / "materialize-tiered50-production.sql"
FORBIDDEN = re.compile(
    r"\b(?:DROP\s+(?:TABLE|DATABASE)|TRUNCATE\s+TABLE|DELETE\s+FROM|DBCC\s+(?:SHRINK|CHECKIDENT))\b",
    re.IGNORECASE,
)
ROW_PATTERN = re.compile(
    r"\(\s*(\d+)\s*,\s*N'([^']+)'\s*,\s*N'([^']+)'\s*,\s*N'([^']*)'\s*,\s*N'([^']+)'\s*,\s*'Tiered50'\s*,\s*N'([ABC])'\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*N'([^']+)'\s*\)"
)


def validate_static() -> dict[str, object]:
    text = MIGRATION.read_text(encoding="utf-8")
    errors: list[str] = []
    if FORBIDDEN.search(text):
        errors.append("Tiered50 migration contains a destructive statement")
    for marker in (
        "ctl.MddMacTiered50ProductionTarget",
        "SamplingTargetId BETWEEN 8 AND 50",
        "ActivationStatus",
        "IsEnabled = 0",
        "UX_ctl_MddMacTargetClaim_SamplingTarget_Bucket",
        "ctl.MddRealtimeSamplingTarget",
    ):
        if marker not in text:
            errors.append(f"Tiered50 migration is missing marker: {marker}")
    if "UPDATE ctl.MddRealtimeSamplingTarget" in text:
        errors.append("historical sampling-target rows must not be updated")
    rows = [match.groups() for match in ROW_PATTERN.finditer(text)]
    manifest = load_default_manifest(ROOT)
    approved = [target for target in manifest.targets if not target.is_legacy7_member]
    if len(rows) != 43:
        errors.append(f"Tiered50 migration contains {len(rows)} materialized rows, expected 43")
    observed = {
        row[1]: {
            "sampling_target_id": int(row[0]),
            "parent_stop_point_ref": row[2],
            "station_name": row[3],
            "panel_version": row[4],
            "tier": row[5],
            "phase_index": int(row[6]),
            "phase_count": int(row[7]),
            "cadence_minutes": int(row[8]),
            "number_of_results": int(row[9]),
            "continuity_classification": row[10],
        }
        for row in rows
    }
    if {item["sampling_target_id"] for item in observed.values()} != set(range(8, 51)):
        errors.append("Tiered50 migration identities are not exactly 8 through 50")
    for target in approved:
        row = observed.get(target.source_parent_station_id)
        expected = {
            "sampling_target_id": 8 + [item.source_parent_station_id for item in approved].index(target.source_parent_station_id),
            "parent_stop_point_ref": target.source_parent_station_id,
            "station_name": target.station_name,
            "panel_version": target.panel_version,
            "tier": target.tier,
            "phase_index": target.phase_index,
            "phase_count": target.phase_count,
            "cadence_minutes": target.cadence_minutes,
            "number_of_results": target.number_of_results,
            "continuity_classification": target.continuity_classification,
        }
        if row != expected:
            errors.append(f"materialized row differs from approved manifest: {target.source_parent_station_id}")
    return {
        "status": "PASS" if not errors else "FAIL",
        "migration": str(MIGRATION),
        "materialized_row_count": len(rows),
        "expected_new_target_count": 43,
        "historical_sampling_target_rows_modified": False,
        "errors": errors,
    }


def validate_live() -> dict[str, object]:
    adapter = MssqlPersistence(MssqlConnectionSettings.from_environment())
    return {"status": "PASS", "schema": adapter.validate_tiered50_runtime_schema()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="perform read-only schema checks")
    args = parser.parse_args(argv)
    result = validate_static()
    if result["status"] == "PASS" and args.live:
        try:
            result["live"] = validate_live()
        except Exception as exc:
            result = {**result, "status": "FAIL", "live": {"status": "FAIL", "error_type": type(exc).__name__}}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
