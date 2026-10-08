#!/usr/bin/env python3
"""Apply and verify the additive Tiered50 target materialization.

The default command is static/read-only. ``--apply`` is required for the one
explicit SQL write and only creates/prepares the separate 43-row table; it
never updates the historical 1..7 sampling rows or enables a target.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlConnectionSettings,
    connect_mssql,
)
from validate_mssql_migration import split_batches  # noqa: E402
from validate_tiered50_migration import validate_static  # noqa: E402


MATERIALIZATION = ROOT / "sql" / "mdd-mac-collector" / "materialize-tiered50-production.sql"


def _row_value(row: Any, name: str, index: int = 0) -> Any:
    if isinstance(row, dict):
        return row.get(name)
    return row[index]


def _drain(cursor: Any) -> None:
    try:
        cursor.fetchall()
    except Exception:
        pass


def _verify(cursor: Any) -> dict[str, Any]:
    cursor.execute(
        """
        SELECT COUNT_BIG(*) AS target_count,
               COALESCE(SUM(CASE WHEN IsEnabled = 1 THEN 1 ELSE 0 END), 0) AS active_count,
               COALESCE(SUM(CASE WHEN ActivationStatus = 'PREPARED' THEN 1 ELSE 0 END), 0) AS prepared_count,
               MIN(SamplingTargetId) AS minimum_id,
               MAX(SamplingTargetId) AS maximum_id
        FROM ctl.MddMacTiered50ProductionTarget
        WHERE PanelVersion = N'TIERED50_PANEL_V1' AND SamplingEra = 'Tiered50'
        """
    )
    row = cursor.fetchone()
    cursor.execute(
        """
        SELECT COUNT_BIG(*) AS legacy_count,
               MIN(SamplingTargetId) AS minimum_id,
               MAX(SamplingTargetId) AS maximum_id
        FROM ctl.MddRealtimeSamplingTarget
        WHERE SamplingTargetId BETWEEN 1 AND 7
        """
    )
    legacy = cursor.fetchone()
    result = {
        "tiered50_target_count": int(_row_value(row, "target_count", 0) or 0),
        "tiered50_active_count": int(_row_value(row, "active_count", 1) or 0),
        "tiered50_prepared_count": int(_row_value(row, "prepared_count", 2) or 0),
        "tiered50_minimum_id": int(_row_value(row, "minimum_id", 3) or 0),
        "tiered50_maximum_id": int(_row_value(row, "maximum_id", 4) or 0),
        "legacy7_count": int(_row_value(legacy, "legacy_count", 0) or 0),
        "legacy7_minimum_id": int(_row_value(legacy, "minimum_id", 1) or 0),
        "legacy7_maximum_id": int(_row_value(legacy, "maximum_id", 2) or 0),
    }
    if result != {
        "tiered50_target_count": 43,
        "tiered50_active_count": 0,
        "tiered50_prepared_count": 43,
        "tiered50_minimum_id": 8,
        "tiered50_maximum_id": 50,
        "legacy7_count": 7,
        "legacy7_minimum_id": 1,
        "legacy7_maximum_id": 7,
    }:
        raise RuntimeError(f"materialization reconciliation failed: {result}")
    return result


def apply_materialization() -> dict[str, Any]:
    static = validate_static()
    if static["status"] != "PASS":
        return {"status": "FAIL", "static": static}
    settings = MssqlConnectionSettings.from_environment()
    connection = connect_mssql(settings)
    batches = split_batches(MATERIALIZATION.read_text(encoding="utf-8"))
    try:
        cursor = connection.cursor()
        cursor.execute("SET XACT_ABORT ON;")
        _drain(cursor)
        for batch in batches:
            cursor.execute(batch)
            _drain(cursor)
        verified = _verify(cursor)
        _drain(cursor)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {
        "status": "PASS",
        "static": static,
        "verification": verified,
        "writes_performed": 43,
        "mdd_requests_issued": 0,
    }


def read_only() -> dict[str, Any]:
    static = validate_static()
    settings = MssqlConnectionSettings.from_environment()
    connection = connect_mssql(settings)
    try:
        verified = _verify(connection.cursor())
    finally:
        connection.close()
    return {"status": "PASS", "static": static, "verification": verified, "writes_performed": 0}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="perform the explicit additive materialization")
    args = parser.parse_args(argv)
    try:
        result = apply_materialization() if args.apply else read_only()
    except Exception as exc:
        result = {"status": "FAIL", "error_type": type(exc).__name__, "reason": str(exc)[:300]}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
