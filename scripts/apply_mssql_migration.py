#!/usr/bin/env python3
"""Apply the additive collector migration through the pinned DB-API client.

The command requires ``--apply``.  Without it, it only performs static and
read-only validation.  It never issues MDD requests and it never changes the
existing realtime, target, warehouse, or reporting objects.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlConnectionSettings,
    MssqlPersistence,
    connect_mssql,
)
from validate_mssql_migration import split_batches, validate_static  # noqa: E402


REQUIRED_MIGRATION_OBJECTS = (
    "ctl.MddMacCollectorRun",
    "ctl.MddMacProductionLease",
    "ctl.MddMacProductionLeaseEvent",
    "ctl.MddMacSharedQuotaAccounting",
    "ctl.MddMacCurrentProductionEra",
    "ctl.MddMacProductionCutover",
    "stg.MddMacSourceSnapshot",
    "stg.MddMacTimingObservation",
    "stg.MddMacSituationEvidence",
    "stg.MddMacServiceSectionEvidence",
    "stg.MddMacAttributeEvidence",
    "stg.MddMacParserGap",
    "ctl.MddImportBatch",
    "ctl.MddImportTableAudit",
    "ctl.MddImportEvidenceAudit",
    "ctl.MddImportSourceRow",
    "ctl.MddImportOverlap",
    "ctl.MddMacMethodologyEvidence",
)


def _drain(cursor: object) -> None:
    fetchall = getattr(cursor, "fetchall", None)
    if not callable(fetchall):
        return
    try:
        fetchall()
    except Exception:
        pass


def _validate_required_objects_in_transaction(cursor: object) -> None:
    """Verify the migration objects before the single transaction commits."""

    names = ", ".join(
        "N'" + object_name.replace("'", "''") + "'" for object_name in REQUIRED_MIGRATION_OBJECTS
    )
    cursor.execute(
        """
        SELECT CONCAT(SCHEMA_NAME(o.schema_id), N'.', o.name) AS ObjectName
        FROM sys.objects AS o
        WHERE CONCAT(SCHEMA_NAME(o.schema_id), N'.', o.name) IN (%s)
        """ % names
    )
    rows = cursor.fetchall()
    found = {str(row[0] if not isinstance(row, dict) else row["ObjectName"]) for row in rows}
    missing = sorted(set(REQUIRED_MIGRATION_OBJECTS) - found)
    if missing:
        raise RuntimeError("migration transaction is missing required objects: " + ", ".join(missing))


def execute_batches_atomically(connection: object, batches: list[str]) -> int:
    """Execute every batch and validate objects before one final commit."""

    cursor = connection.cursor()
    # pymssql with autocommit disabled opens one implicit transaction for the
    # connection.  Adding BEGIN TRANSACTION here would nest it; a single
    # commit would then leave the outer transaction open and the close would
    # roll the migration back.  Keep the DB-API transaction as the one atomic
    # unit and enable SQL Server's fail-fast rollback behavior.
    cursor.execute("SET XACT_ABORT ON;")
    _drain(cursor)
    executed = 0
    try:
        for batch in batches:
            cursor.execute(batch)
            _drain(cursor)
            executed += 1
        _validate_required_objects_in_transaction(cursor)
        _drain(cursor)
        connection.commit()
        return executed
    except BaseException:
        try:
            connection.rollback()
        finally:
            raise


def apply_migration() -> dict[str, object]:
    static = validate_static()
    if static["status"] != "PASS":
        return {"status": "FAIL", "static": static}
    settings = MssqlConnectionSettings.from_environment()
    adapter = MssqlPersistence(settings)
    before = adapter.baseline()
    connection = connect_mssql(settings)
    batches = split_batches((ROOT / "sql" / "mdd-mac-collector" / "create-source-faithful-persistence.sql").read_text(encoding="utf-8"))
    executed = 0
    try:
        executed = execute_batches_atomically(connection, batches)
    except BaseException:
        try:
            connection.rollback()
        finally:
            connection.close()
        raise
    connection.close()
    schema = adapter.validate_full_schema()
    after = adapter.baseline()
    preserved = {
        key: int(after[key]) >= int(before[key])
        for key in (
            "collector_runs",
            "sampling_targets",
            "stop_observations",
            "situation_observations",
            "stop_situation_links",
        )
    }
    preserved["sampling_target_ids"] = after["sampling_target_ids"] == before["sampling_target_ids"] == [1, 2, 3, 4, 5, 6, 7]
    return {
        "status": "PASS" if all(preserved.values()) else "FAIL",
        "executed_batches": executed,
        "schema": schema,
        "before": before,
        "after": after,
        "historical_preservation": preserved,
    }


def read_only_check() -> dict[str, object]:
    static = validate_static()
    if static["status"] != "PASS":
        return {"status": "FAIL", "static": static}
    adapter = MssqlPersistence(MssqlConnectionSettings.from_environment())
    return {"status": "PASS", "static": static, "schema": adapter.validate_full_schema(), "baseline": adapter.baseline()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="perform the explicitly requested additive migration")
    args = parser.parse_args(argv)
    try:
        result = apply_migration() if args.apply else read_only_check()
    except Exception as exc:
        result = {"status": "FAIL", "error_type": type(exc).__name__, "reason": str(exc)[:300]}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
