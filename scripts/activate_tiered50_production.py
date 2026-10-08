#!/usr/bin/env python3
"""Explicitly activate the already-materialized Tiered50 SQL configuration.

The default command is read-only. ``--apply`` requires an operator-supplied
real UTC boundary and ``--legacy7-stopped``; it never sends an MDD request and
does not manipulate launchd. The Legacy7 LaunchAgent must be stopped and
verified separately before this SQL transaction is authorized.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.domain import (  # noqa: E402
    MAC_TIERED50_PRODUCTION_COLLECTION_CONTEXT,
    SamplingEra,
)
from cologne_transit_collector.manifest import load_default_manifest  # noqa: E402
from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlConnectionSettings,
    MssqlPersistence,
    connect_mssql,
)
from cologne_transit_collector.runtime_configuration import EXPECTED_SQL_DATABASE  # noqa: E402


TARGET_TABLE = "ctl.MddMacTiered50ProductionTarget"
ERA_CODE = "TIERED50_PRODUCTION"
TIMESTAMP_KIND = "REAL_TIERED50_CUTOVER"


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--effective-at must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _row_value(row: Any, name: str, index: int = 0) -> Any:
    if isinstance(row, dict):
        return row.get(name)
    return row[index] if row is not None else None


def _utc_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise RuntimeError("Tiered50 sampling-era boundary is not a valid timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def read_only_status() -> dict[str, object]:
    manifest = load_default_manifest(ROOT)
    settings = MssqlConnectionSettings.from_environment()
    if settings.database != EXPECTED_SQL_DATABASE:
        raise RuntimeError(f"SQL Server target database must be {EXPECTED_SQL_DATABASE}")
    persistence = MssqlPersistence(
        settings=settings,
        collection_context=MAC_TIERED50_PRODUCTION_COLLECTION_CONTEXT,
    )
    runtime_schema = persistence.validate_runtime_schema()
    tiered_schema = persistence.validate_tiered50_runtime_schema()
    connection = connect_mssql(settings)
    try:
        cursor = connection.cursor()
        cursor.execute(
            f"""
            SELECT SamplingTargetId, SourceParentStationId, IsEnabled,
                   ActivationStatus, ActivatedAtUtc
            FROM {TARGET_TABLE}
            WHERE PanelVersion = %s AND SamplingEra = %s
            ORDER BY SamplingTargetId
            """,
            (manifest.panel_version, SamplingEra.TIERED50.value),
        )
        rows = cursor.fetchall()
        cursor.execute(
            """
            SELECT CollectionContext, COUNT_BIG(*) AS RunningCount
            FROM ctl.MddMacCollectorRun
            WHERE Status IN ('RUNNING', 'CLAIMED')
              AND CollectionContext IN (%s, %s)
            GROUP BY CollectionContext
            """,
            ("MAC_LEGACY7_PRODUCTION", MAC_TIERED50_PRODUCTION_COLLECTION_CONTEXT),
        )
        writers = cursor.fetchall()
        cursor.execute(
            """
            SELECT SamplingEraId, EraCode, PanelVersion, EffectiveAtUtc,
                   TimestampKind, IsActive, IsSynthetic
            FROM ctl.MddMacSamplingEra
            WHERE EraCode = %s
            ORDER BY SamplingEraId
            """,
            (ERA_CODE,),
        )
        eras = cursor.fetchall()
    finally:
        connection.close()

    serialized_rows = [
        {
            "sampling_target_id": int(_row_value(row, "SamplingTargetId", 0)),
            "source_parent_station_id": str(_row_value(row, "SourceParentStationId", 1) or ""),
            "is_enabled": bool(_row_value(row, "IsEnabled", 2)),
            "activation_status": str(_row_value(row, "ActivationStatus", 3) or ""),
            "activated_at_utc": _row_value(row, "ActivatedAtUtc", 4),
        }
        for row in rows
    ]
    serialized_writers = [
        {
            "collection_context": str(_row_value(row, "CollectionContext", 0) or ""),
            "running_count": int(_row_value(row, "RunningCount", 1) or 0),
        }
        for row in writers
    ]
    serialized_eras = [
        {
            "sampling_era_id": int(_row_value(row, "SamplingEraId", 0)),
            "era_code": str(_row_value(row, "EraCode", 1) or ""),
            "panel_version": str(_row_value(row, "PanelVersion", 2) or ""),
            "effective_at_utc": _row_value(row, "EffectiveAtUtc", 3),
            "timestamp_kind": str(_row_value(row, "TimestampKind", 4) or ""),
            "is_active": bool(_row_value(row, "IsActive", 5)),
            "is_synthetic": bool(_row_value(row, "IsSynthetic", 6)),
        }
        for row in eras
    ]
    return {
        "status": "PASS",
        "database": EXPECTED_SQL_DATABASE,
        "schema": {"base": runtime_schema, "tiered50": tiered_schema},
        "target_count": len(serialized_rows),
        "targets": serialized_rows,
        "running_production_writers": serialized_writers,
        "tiered50_eras": serialized_eras,
        "mdd_requests_issued": 0,
        "writes_performed": 0,
    }


def apply_activation(*, effective_at: datetime, legacy7_stopped: bool) -> dict[str, object]:
    if effective_at is None:
        raise RuntimeError("--effective-at is required with --apply; no cutover time may be fabricated")
    if not legacy7_stopped:
        raise RuntimeError("--legacy7-stopped is required; launchd state is not changed by this command")
    effective_at = effective_at.astimezone(timezone.utc)
    if effective_at > datetime.now(timezone.utc) + timedelta(seconds=5):
        raise RuntimeError("Tiered50 effective boundary cannot be in the future")

    manifest = load_default_manifest(ROOT)
    settings = MssqlConnectionSettings.from_environment()
    if settings.database != EXPECTED_SQL_DATABASE:
        raise RuntimeError(f"SQL Server target database must be {EXPECTED_SQL_DATABASE}")
    connection = connect_mssql(settings)
    try:
        cursor = connection.cursor()
        cursor.execute("SET XACT_ABORT ON; BEGIN TRANSACTION;")
        cursor.execute(
            f"""
            SELECT SamplingTargetId, IsEnabled, ActivationStatus
            FROM {TARGET_TABLE} WITH (UPDLOCK, HOLDLOCK)
            WHERE PanelVersion = %s AND SamplingEra = %s
            ORDER BY SamplingTargetId
            """,
            (manifest.panel_version, SamplingEra.TIERED50.value),
        )
        target_rows = list(cursor.fetchall())
        if len(target_rows) != 43:
            raise RuntimeError("exactly 43 prepared Tiered50 targets are required before activation")
        if any(
            int(_row_value(row, "SamplingTargetId", 0)) not in range(8, 51)
            or str(_row_value(row, "ActivationStatus", 2) or "") not in {"PREPARED", "ACTIVE"}
            for row in target_rows
        ):
            raise RuntimeError("Tiered50 target materialization is not the approved 8 through 50 set")
        cursor.execute(
            """
            SELECT CollectionContext, COUNT_BIG(*) AS RunningCount
            FROM ctl.MddMacCollectorRun WITH (UPDLOCK, HOLDLOCK)
            WHERE Status IN ('RUNNING', 'CLAIMED')
              AND CollectionContext = %s
            GROUP BY CollectionContext
            """,
            ("MAC_LEGACY7_PRODUCTION",),
        )
        competing = list(cursor.fetchall())
        if any(int(_row_value(row, "RunningCount", 1) or 0) for row in competing):
            raise RuntimeError("Legacy7 production writer is still running")

        cursor.execute(
            f"""
            UPDATE {TARGET_TABLE}
            SET IsEnabled = 1,
                ActivationStatus = 'ACTIVE',
                ActivatedAtUtc = %s
            WHERE PanelVersion = %s AND SamplingEra = %s
            """,
            (effective_at, manifest.panel_version, SamplingEra.TIERED50.value),
        )

        cursor.execute(
            """
            SELECT SamplingEraId, PanelVersion, EffectiveAtUtc, TimestampKind,
                   IsActive, IsSynthetic
            FROM ctl.MddMacSamplingEra WITH (UPDLOCK, HOLDLOCK)
            WHERE EraCode = %s
            """,
            (ERA_CODE,),
        )
        era_rows = list(cursor.fetchall())
        if len(era_rows) > 1:
            raise RuntimeError("conflicting or duplicate Tiered50 sampling-era history exists")
        if not era_rows:
            cursor.execute(
                """
                INSERT INTO ctl.MddMacSamplingEra
                    (EraCode, PanelVersion, EffectiveAtUtc, TimestampKind, IsActive, IsSynthetic)
                VALUES (%s, %s, %s, %s, 1, 0)
                """,
                (ERA_CODE, manifest.panel_version, effective_at, TIMESTAMP_KIND),
            )
            era_action = "INSERTED"
        else:
            row = era_rows[0]
            stored_boundary = _utc_datetime(_row_value(row, "EffectiveAtUtc", 2))
            is_exact = (
                str(_row_value(row, "PanelVersion", 1) or "") == manifest.panel_version
                and stored_boundary == effective_at
                and str(_row_value(row, "TimestampKind", 3) or "") == TIMESTAMP_KIND
                and bool(_row_value(row, "IsActive", 4))
                and not bool(_row_value(row, "IsSynthetic", 5))
            )
            if not is_exact:
                raise RuntimeError(
                    "existing Tiered50 sampling-era row conflicts with the requested real cutover boundary"
                )
            era_action = "REUSED"
        cursor.execute("COMMIT TRANSACTION;")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {
        "status": "PASS",
        "database": EXPECTED_SQL_DATABASE,
        "target_rows_activated": 43,
        "sampling_era_action": era_action,
        "effective_at_utc": effective_at.isoformat().replace("+00:00", "Z"),
        "mdd_requests_issued": 0,
        "writes_performed": 44 if era_action == "INSERTED" else 43,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="activate the prepared target rows")
    parser.add_argument("--legacy7-stopped", action="store_true", help="operator-confirmed Legacy7 stop gate")
    parser.add_argument("--effective-at", help="real UTC cutover boundary, required with --apply")
    args = parser.parse_args(argv)
    try:
        result = (
            apply_activation(
                effective_at=parse_utc(args.effective_at) if args.effective_at else None,  # type: ignore[arg-type]
                legacy7_stopped=args.legacy7_stopped,
            )
            if args.apply
            else read_only_status()
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED", "error_type": type(exc).__name__}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
