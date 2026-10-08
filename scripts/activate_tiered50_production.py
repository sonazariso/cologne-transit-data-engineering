#!/usr/bin/env python3
"""Explicitly activate the already-materialized Tiered50 SQL configuration.

The default command is read-only. ``--apply`` requires an operator-supplied
real UTC boundary; it retires and verifies the exact Legacy7 LaunchAgent
before entering the SQL transaction, never sends an MDD request, and records
an outage if a later gate blocks activation.
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
from install_macos_launch_agent import retire_legacy7  # noqa: E402


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
            SELECT MonthKey, ProviderQuotaCeiling, NormalOperationalMaximum,
                   RetryProtectionBoundary, ConfirmedProviderAttempts,
                   ConservativeUnknownAttempts, ExplicitSharedConsumerReservedAttempts,
                   AccountingStatus, IncludesAllSharedConsumers, BaselineAsOfUtc,
                   LocalUsageIncludedThroughUtc, SourceDescription
            FROM ctl.MddMacSharedQuotaAccounting
            WHERE MonthKey = %s
            """,
            (datetime.now(timezone.utc).strftime("%Y-%m"),),
        )
        quota_baseline = cursor.fetchone()
        cursor.execute(
            """
            SELECT TOP (1) RunId, LogicalDueBucket, CompletedAtUtc, Status
            FROM ctl.MddMacCollectorRun
            WHERE CollectionContext = 'MAC_LEGACY7_PRODUCTION'
              AND CompletedAtUtc IS NOT NULL
            ORDER BY CompletedAtUtc DESC, MacCollectorRunId DESC
            """
        )
        final_legacy7_run = cursor.fetchone()
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
    serialized_quota_baseline = (
        {
            "month_key": str(_row_value(quota_baseline, "MonthKey", 0) or ""),
            "provider_quota_ceiling": int(_row_value(quota_baseline, "ProviderQuotaCeiling", 1) or 0),
            "normal_operational_maximum": int(_row_value(quota_baseline, "NormalOperationalMaximum", 2) or 0),
            "retry_protection_boundary": int(_row_value(quota_baseline, "RetryProtectionBoundary", 3) or 0),
            "confirmed_provider_attempts": int(_row_value(quota_baseline, "ConfirmedProviderAttempts", 4) or 0),
            "conservative_unknown_attempts": int(_row_value(quota_baseline, "ConservativeUnknownAttempts", 5) or 0),
            "explicit_shared_consumer_reserved_attempts": int(
                _row_value(quota_baseline, "ExplicitSharedConsumerReservedAttempts", 6) or 0
            ),
            "accounting_status": str(_row_value(quota_baseline, "AccountingStatus", 7) or ""),
            "includes_all_shared_consumers": bool(_row_value(quota_baseline, "IncludesAllSharedConsumers", 8)),
            "baseline_as_of_utc": _row_value(quota_baseline, "BaselineAsOfUtc", 9),
            "local_usage_included_through_utc": _row_value(quota_baseline, "LocalUsageIncludedThroughUtc", 10),
            "source_description": str(_row_value(quota_baseline, "SourceDescription", 11) or ""),
        }
        if quota_baseline is not None
        else None
    )
    serialized_final_run = (
        {
            "run_id": str(_row_value(final_legacy7_run, "RunId", 0) or ""),
            "logical_due_bucket": int(_row_value(final_legacy7_run, "LogicalDueBucket", 1) or 0),
            "completed_at_utc": _row_value(final_legacy7_run, "CompletedAtUtc", 2),
            "status": str(_row_value(final_legacy7_run, "Status", 3) or ""),
        }
        if final_legacy7_run is not None
        else None
    )
    return {
        "status": "PASS",
        "database": EXPECTED_SQL_DATABASE,
        "schema": {"base": runtime_schema, "tiered50": tiered_schema},
        "target_count": len(serialized_rows),
        "targets": serialized_rows,
        "running_production_writers": serialized_writers,
        "sampling_era_read_scope": "ADMINISTRATOR_ONLY",
        "quota_baseline": serialized_quota_baseline,
        "latest_legacy7_run": serialized_final_run,
        "mdd_requests_issued": 0,
        "writes_performed": 0,
    }


def _record_cutover_outage(*, effective_at: datetime, reason: str, evidence: dict[str, object]) -> None:
    """Record a blocked post-retirement cutover without mutating history."""

    settings = MssqlConnectionSettings.from_environment()
    connection = connect_mssql(settings)
    cutover_key = f"TIERED50_PANEL_V1:{effective_at.isoformat()}:{'OUTAGE'}"
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            IF NOT EXISTS (SELECT 1 FROM ctl.MddMacProductionCutover WHERE CutoverKey = %s)
            INSERT INTO ctl.MddMacProductionCutover
                (CutoverKey, PanelVersion, Tiered50ActivationAtUtc,
                 Tiered50ActivationStatus, EvidenceJson)
            VALUES (%s, %s, %s, 'OUTAGE', %s)
            """,
            (
                cutover_key,
                cutover_key,
                "TIERED50_PANEL_V1",
                effective_at,
                json.dumps({"reason": reason, **evidence}, ensure_ascii=False, sort_keys=True, default=str),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def apply_activation(*, effective_at: datetime, legacy7_stopped: bool = False) -> dict[str, object]:
    if effective_at is None:
        raise RuntimeError("--effective-at is required with --apply; no cutover time may be fabricated")
    effective_at = effective_at.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    if effective_at > now + timedelta(seconds=5):
        raise RuntimeError("Tiered50 effective boundary cannot be in the future")
    if effective_at < now - timedelta(seconds=5):
        raise RuntimeError("Tiered50 effective boundary may not be backdated")

    retirement = retire_legacy7()

    manifest = load_default_manifest(ROOT)
    settings = MssqlConnectionSettings.from_environment()
    if settings.database != EXPECTED_SQL_DATABASE:
        raise RuntimeError(f"SQL Server target database must be {EXPECTED_SQL_DATABASE}")
    connection = connect_mssql(settings)
    evidence: dict[str, object] = {"legacy7_retirement": retirement}
    try:
        cursor = connection.cursor()
        cursor.execute("SET XACT_ABORT ON; BEGIN TRANSACTION;")
        cursor.execute(
            f"""
            SELECT SamplingTargetId, IsEnabled, ActivationStatus, ActivatedAtUtc
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
        active_boundaries = {
            _utc_datetime(_row_value(row, "ActivatedAtUtc", 3))
            for row in target_rows
            if bool(_row_value(row, "IsEnabled", 1))
        }
        if len(active_boundaries) > 1 or (active_boundaries and effective_at not in active_boundaries):
            raise RuntimeError("existing Tiered50 activation boundary conflicts with the requested boundary")
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

        month_key = effective_at.strftime("%Y-%m")
        cursor.execute(
            """
            SELECT ProviderQuotaCeiling, NormalOperationalMaximum,
                   RetryProtectionBoundary, ConfirmedProviderAttempts,
                   ConservativeUnknownAttempts, ExplicitSharedConsumerReservedAttempts,
                   AccountingStatus, IncludesAllSharedConsumers, BaselineAsOfUtc,
                   LocalUsageIncludedThroughUtc, SourceDescription
            FROM ctl.MddMacSharedQuotaAccounting WITH (UPDLOCK, HOLDLOCK)
            WHERE MonthKey = %s
            """,
            (month_key,),
        )
        baseline = cursor.fetchone()
        baseline_valid = bool(
            baseline
            and int(_row_value(baseline, "ProviderQuotaCeiling", 0) or 0) == 250000
            and int(_row_value(baseline, "NormalOperationalMaximum", 1) or 0) == 210000
            and int(_row_value(baseline, "RetryProtectionBoundary", 2) or 0) == 207500
            and str(_row_value(baseline, "AccountingStatus", 6) or "") == "COMPLETE"
            and bool(_row_value(baseline, "IncludesAllSharedConsumers", 7))
            and (_utc_datetime(_row_value(baseline, "BaselineAsOfUtc", 8)) or now) <= now
            and (_utc_datetime(_row_value(baseline, "LocalUsageIncludedThroughUtc", 9)) or now) <= now
        )
        if not baseline_valid:
            raise RuntimeError("complete provider-wide shared-credential quota baseline is unavailable")

        cursor.execute(
            """
            SELECT TOP (1) RunId, LogicalDueBucket, CompletedAtUtc, Status
            FROM ctl.MddMacCollectorRun
            WHERE CollectionContext = 'MAC_LEGACY7_PRODUCTION'
              AND CompletedAtUtc IS NOT NULL
              AND CompletedAtUtc <= %s
            ORDER BY CompletedAtUtc DESC, MacCollectorRunId DESC
            """,
            (effective_at,),
        )
        final_legacy7 = cursor.fetchone()
        evidence["final_legacy7_run"] = {
            "run_id": str(_row_value(final_legacy7, "RunId", 0) or "") if final_legacy7 else None,
            "logical_due_bucket": int(_row_value(final_legacy7, "LogicalDueBucket", 1) or 0) if final_legacy7 else None,
            "completed_at_utc": _row_value(final_legacy7, "CompletedAtUtc", 2) if final_legacy7 else None,
            "status": str(_row_value(final_legacy7, "Status", 3) or "") if final_legacy7 else None,
        }

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
                OUTPUT INSERTED.SamplingEraId
                VALUES (%s, %s, %s, %s, 1, 0)
                """,
                (ERA_CODE, manifest.panel_version, effective_at, TIMESTAMP_KIND),
            )
            inserted_era = cursor.fetchone()
            sampling_era_id = int(_row_value(inserted_era, "SamplingEraId", 0))
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
            sampling_era_id = int(_row_value(row, "SamplingEraId", 0))
        cursor.execute(
            """
            UPDATE ctl.MddMacCurrentProductionEra
            SET SamplingEraId = %s, EraCode = %s, PanelVersion = %s,
                EffectiveAtUtc = %s, RecordedAtUtc = SYSUTCDATETIME()
            WHERE CurrentMarkerId = 1
            """,
            (sampling_era_id, ERA_CODE, manifest.panel_version, effective_at),
        )
        if getattr(cursor, "rowcount", 1) == 0:
            cursor.execute(
                """
                INSERT INTO ctl.MddMacCurrentProductionEra
                    (CurrentMarkerId, SamplingEraId, EraCode, PanelVersion, EffectiveAtUtc)
                VALUES (1, %s, %s, %s, %s)
                """,
                (sampling_era_id, ERA_CODE, manifest.panel_version, effective_at),
            )
        cursor.execute(
            """
            IF NOT EXISTS
            (
                SELECT 1 FROM ctl.MddMacProductionCutover
                WHERE CutoverKey = %s
            )
            INSERT INTO ctl.MddMacProductionCutover
                (CutoverKey, PanelVersion, FinalLegacy7RunId,
                 FinalLegacy7LogicalDueBucket, FinalLegacy7PersistenceAtUtc,
                 FinalLegacy7AuditStatus, Tiered50ActivationAtUtc,
                 Tiered50ActivationStatus, EvidenceJson)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'ACTIVATED', %s)
            """,
            (
                f"TIERED50_PANEL_V1:{effective_at.isoformat()}:ACTIVATED",
                f"TIERED50_PANEL_V1:{effective_at.isoformat()}:ACTIVATED",
                manifest.panel_version,
                _row_value(final_legacy7, "RunId", 0) if final_legacy7 else None,
                _row_value(final_legacy7, "LogicalDueBucket", 1) if final_legacy7 else None,
                _row_value(final_legacy7, "CompletedAtUtc", 2) if final_legacy7 else None,
                _row_value(final_legacy7, "Status", 3) if final_legacy7 else None,
                effective_at,
                json.dumps(evidence, ensure_ascii=False, sort_keys=True, default=str),
            ),
        )
        cursor.execute("COMMIT TRANSACTION;")
        connection.commit()
    except BaseException:
        connection.rollback()
        try:
            _record_cutover_outage(
                effective_at=effective_at,
                reason="TIERED50_ACTIVATION_BLOCKED_AFTER_LEGACY7_RETIREMENT",
                evidence=evidence,
            )
        except Exception:
            pass
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
        "legacy7_retirement": retirement,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="activate the prepared target rows")
    parser.add_argument(
        "--legacy7-stopped",
        action="store_true",
        help="deprecated compatibility flag; launchd state is always retired and verified directly",
    )
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
