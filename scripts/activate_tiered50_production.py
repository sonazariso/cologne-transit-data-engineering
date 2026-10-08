#!/usr/bin/env python3
"""Explicitly activate the already-materialized Tiered50 SQL configuration.

The default command is read-only. ``--apply`` requires an operator-supplied
real UTC boundary, a current provider-wide quota baseline, and a separately
validated dedicated runtime login.  Legacy7 is already retired and is never
mutated by this command.  No MDD request is issued here.
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
    ABSOLUTE_PROVIDER_CEILING,
    AUTOMATIC_RETRY_PROTECTION_BOUNDARY,
    MAC_TIERED50_PRODUCTION_COLLECTION_CONTEXT,
    NORMAL_OPERATIONAL_MAXIMUM,
    SamplingEra,
)
from cologne_transit_collector.manifest import load_default_manifest  # noqa: E402
from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlConnectionSettings,
    MssqlPersistence,
    connect_mssql,
)
from cologne_transit_collector.quota_baseline import validate_stored_baseline_row  # noqa: E402
from cologne_transit_collector.runtime_configuration import (  # noqa: E402
    EXPECTED_SQL_DATABASE,
    MSSQL_CREDENTIAL,
    load_local_runtime_values,
    resolve_secret,
)
from install_macos_launch_agent import (  # noqa: E402
    TIERED50_MODE,
    bootstrap as bootstrap_launch_agent,
    build_installed_plist,
    install as install_launch_agent,
)


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


def _installed_runtime_connection() -> tuple[MssqlConnectionSettings, str]:
    """Resolve the same limited SQL login used by the installed LaunchAgent."""

    local_values, config_path, loaded = load_local_runtime_values()
    if not loaded:
        raise RuntimeError("installed Tiered50 runtime configuration is not available")
    values = dict(local_values)
    configured_user = str(values.get("MSSQL_USER", values.get("SQL_SERVER_USER", "")) or "")
    if configured_user.casefold() != "colognetransitcollector":
        raise RuntimeError("installed Tiered50 runtime must use CologneTransitCollector")
    values["MSSQL_PASSWORD"] = resolve_secret(values, credential_type=MSSQL_CREDENTIAL)
    settings = MssqlConnectionSettings.from_environment(values)
    if settings.database != EXPECTED_SQL_DATABASE:
        raise RuntimeError(f"installed Tiered50 runtime database must be {EXPECTED_SQL_DATABASE}")
    return settings, str(config_path)


def _validate_limited_runtime(*, now: datetime) -> dict[str, Any]:
    settings, config_path = _installed_runtime_connection()
    persistence = MssqlPersistence(
        settings=settings,
        collection_context=MAC_TIERED50_PRODUCTION_COLLECTION_CONTEXT,
    )
    login = dict(persistence.runtime_login_status())
    if str(login.get("database_name") or "") != EXPECTED_SQL_DATABASE:
        raise RuntimeError("dedicated runtime connection reached the wrong database")
    if str(login.get("login_name") or "").casefold() != "colognetransitcollector":
        raise RuntimeError("dedicated runtime connection did not use CologneTransitCollector")
    if any(
        bool(login.get(name))
        for name in (
            "is_sysadmin",
            "is_serveradmin",
            "is_securityadmin",
            "is_db_owner",
            "is_db_datareader",
            "is_db_datawriter",
        )
    ):
        raise RuntimeError("dedicated runtime connection has a prohibited broad privilege")
    privilege_boundary = persistence.runtime_privilege_boundary()
    schema = persistence.validate_runtime_schema()
    tiered_schema = persistence.validate_tiered50_runtime_schema()
    quota = persistence.current_month_usage(at_utc=now)
    if not quota or not bool(quota.get("state_available")):
        raise RuntimeError("dedicated runtime cannot read a current provider-wide quota baseline")
    return {
        "config_path": config_path,
        "settings": settings.redacted(),
        "login": login,
        "privilege_boundary": privilege_boundary,
        "runtime_schema": schema,
        "tiered50_schema": tiered_schema,
        "quota": dict(quota),
    }


def _validate_launch_agent_template(*, runtime_config_path: str) -> dict[str, Any]:
    """Validate the exact production plist without loading or invoking it."""

    rendered = build_installed_plist(
        activate=True,
        runtime_config_path=runtime_config_path,
        mode=TIERED50_MODE,
    )
    if rendered.get("Disabled") or rendered.get("RunAtLoad"):
        raise RuntimeError("Tiered50 LaunchAgent activation template is not a scheduled production bundle")
    if rendered.get("StartInterval") != 300:
        raise RuntimeError("Tiered50 LaunchAgent interval is not the approved five-minute schedule")
    environment = dict(rendered.get("EnvironmentVariables", {}))
    if environment.get("COLLECTOR_MAC_TIERED50_ENABLED") != "1":
        raise RuntimeError("Tiered50 LaunchAgent enable gate is not set in the approved render")
    if any(marker in json.dumps(rendered, ensure_ascii=False) for marker in ("MDD_API_KEY", "MSSQL_PASSWORD", "SQL_SERVER_PASSWORD")):
        raise RuntimeError("Tiered50 LaunchAgent render contains a prohibited secret marker")
    return {
        "label": rendered.get("Label"),
        "start_interval_seconds": rendered.get("StartInterval"),
        "runtime_config_path": runtime_config_path,
        "secret_free": True,
        "activation_gate": "VALIDATED",
    }


def read_only_status() -> dict[str, object]:
    now = datetime.now(timezone.utc)
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
            SELECT TOP (2) MonthKey, ProviderQuotaCeiling, NormalOperationalMaximum,
                   RetryProtectionBoundary, ConfirmedProviderAttempts,
                   ConservativeUnknownAttempts, ExplicitSharedConsumerReservedAttempts,
                   AccountingStatus, IncludesAllSharedConsumers, BaselineAsOfUtc,
                   LocalUsageIncludedThroughUtc, SourceDescription,
                   AccountingModel, ProviderMeasurementCutoffUtc, StaleAfterUtc,
                   SourceEvidenceHash, ReconciliationKey, ProviderPeriodTimezone,
                   ProviderPeriodStartUtc, ProviderPeriodEndUtc
            FROM ctl.MddMacSharedQuotaAccounting
            WHERE ProviderPeriodStartUtc <= %s
              AND ProviderPeriodEndUtc > %s
            ORDER BY ProviderPeriodStartUtc DESC, MonthKey
            """,
            (now, now),
        )
        quota_baselines = list(cursor.fetchall())
        quota_baseline = quota_baselines[0] if len(quota_baselines) == 1 else None
        quota_baseline_resolution = (
            "ONE_CURRENT_PROVIDER_PERIOD"
            if len(quota_baselines) == 1
            else "MISSING_CURRENT_PROVIDER_PERIOD"
            if not quota_baselines
            else "AMBIGUOUS_CURRENT_PROVIDER_PERIOD"
        )
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
            "accounting_model": str(_row_value(quota_baseline, "AccountingModel", 12) or ""),
            "provider_measurement_cutoff_utc": _row_value(quota_baseline, "ProviderMeasurementCutoffUtc", 13),
            "stale_after_utc": _row_value(quota_baseline, "StaleAfterUtc", 14),
            "source_evidence_hash": str(_row_value(quota_baseline, "SourceEvidenceHash", 15) or ""),
            "reconciliation_key": str(_row_value(quota_baseline, "ReconciliationKey", 16) or ""),
            "provider_period_timezone": str(_row_value(quota_baseline, "ProviderPeriodTimezone", 17) or ""),
            "provider_period_start_utc": _row_value(quota_baseline, "ProviderPeriodStartUtc", 18),
            "provider_period_end_utc": _row_value(quota_baseline, "ProviderPeriodEndUtc", 19),
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
        "quota_baseline_resolution": quota_baseline_resolution,
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

    manifest = load_default_manifest(ROOT)
    settings = MssqlConnectionSettings.from_environment()
    if settings.database != EXPECTED_SQL_DATABASE:
        raise RuntimeError(f"SQL Server target database must be {EXPECTED_SQL_DATABASE}")
    limited_runtime = _validate_limited_runtime(now=now)
    launch_agent = _validate_launch_agent_template(runtime_config_path=str(limited_runtime["config_path"]))
    connection = connect_mssql(settings)
    evidence: dict[str, object] = {
        "legacy7_status": "ALREADY_RETIRED_NOT_MUTATED",
        "limited_runtime": limited_runtime,
        "launch_agent_template": launch_agent,
        "requested_effective_at_utc": effective_at.isoformat().replace("+00:00", "Z"),
    }
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
              AND CollectionContext IN (%s, %s)
            GROUP BY CollectionContext
            """,
            ("MAC_LEGACY7_PRODUCTION", MAC_TIERED50_PRODUCTION_COLLECTION_CONTEXT),
        )
        competing = list(cursor.fetchall())
        if any(int(_row_value(row, "RunningCount", 1) or 0) for row in competing):
            raise RuntimeError("a production collector writer is still running")
        evidence["competing_writers"] = [
            {
                "collection_context": str(_row_value(row, "CollectionContext", 0) or ""),
                "running_count": int(_row_value(row, "RunningCount", 1) or 0),
            }
            for row in competing
        ]

        cursor.execute(
            """
            SELECT TOP (2) MonthKey, ProviderQuotaCeiling, NormalOperationalMaximum,
                   RetryProtectionBoundary, ConfirmedProviderAttempts,
                   ConservativeUnknownAttempts, ExplicitSharedConsumerReservedAttempts,
                   AccountingStatus, IncludesAllSharedConsumers, BaselineAsOfUtc,
                   LocalUsageIncludedThroughUtc, SourceDescription,
                   AccountingModel, ProviderMeasurementCutoffUtc, StaleAfterUtc,
                   SourceEvidenceHash, ReconciliationKey, ProviderPeriodTimezone,
                   ProviderPeriodStartUtc, ProviderPeriodEndUtc
            FROM ctl.MddMacSharedQuotaAccounting WITH (UPDLOCK, HOLDLOCK)
            WHERE ProviderPeriodStartUtc <= %s
              AND ProviderPeriodEndUtc > %s
            ORDER BY ProviderPeriodStartUtc DESC, MonthKey
            """,
            (effective_at, effective_at),
        )
        baseline_candidates = list(cursor.fetchall())
        if len(baseline_candidates) != 1:
            raise RuntimeError(
                "current provider billing period is missing or ambiguous; activation cannot select a UTC calendar month"
            )
        baseline = baseline_candidates[0]
        month_key = str(_row_value(baseline, "MonthKey", 0) or "")
        baseline_mapping = (
            {
                "MonthKey": _row_value(baseline, "MonthKey", 0),
                "ProviderQuotaCeiling": _row_value(baseline, "ProviderQuotaCeiling", 1),
                "NormalOperationalMaximum": _row_value(baseline, "NormalOperationalMaximum", 2),
                "RetryProtectionBoundary": _row_value(baseline, "RetryProtectionBoundary", 3),
                "ConfirmedProviderAttempts": _row_value(baseline, "ConfirmedProviderAttempts", 4),
                "ConservativeUnknownAttempts": _row_value(baseline, "ConservativeUnknownAttempts", 5),
                "ExplicitSharedConsumerReservedAttempts": _row_value(baseline, "ExplicitSharedConsumerReservedAttempts", 6),
                "AccountingStatus": _row_value(baseline, "AccountingStatus", 7),
                "IncludesAllSharedConsumers": _row_value(baseline, "IncludesAllSharedConsumers", 8),
                "BaselineAsOfUtc": _row_value(baseline, "BaselineAsOfUtc", 9),
                "LocalUsageIncludedThroughUtc": _row_value(baseline, "LocalUsageIncludedThroughUtc", 10),
                "SourceDescription": _row_value(baseline, "SourceDescription", 11),
                "AccountingModel": _row_value(baseline, "AccountingModel", 12),
                "ProviderMeasurementCutoffUtc": _row_value(baseline, "ProviderMeasurementCutoffUtc", 13),
                "StaleAfterUtc": _row_value(baseline, "StaleAfterUtc", 14),
                "SourceEvidenceHash": _row_value(baseline, "SourceEvidenceHash", 15),
                "ReconciliationKey": _row_value(baseline, "ReconciliationKey", 16),
                "ProviderPeriodTimezone": _row_value(baseline, "ProviderPeriodTimezone", 17),
                "ProviderPeriodStartUtc": _row_value(baseline, "ProviderPeriodStartUtc", 18),
                "ProviderPeriodEndUtc": _row_value(baseline, "ProviderPeriodEndUtc", 19),
            }
        )
        baseline_valid, baseline_reason = validate_stored_baseline_row(
            baseline_mapping,
            at_utc=effective_at,
        )
        if not baseline_valid:
            raise RuntimeError(
                f"current provider-wide shared-credential quota evidence is not registrable: {baseline_reason}"
            )

        local_cutoff = _utc_datetime(baseline_mapping["LocalUsageIncludedThroughUtc"])
        assert local_cutoff is not None
        cursor.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN Status IN ('HTTP_PROVIDER_ATTEMPT_CONFIRMED', 'CONSERVATIVE_LIVE_ATTEMPT', 'PENDING_AUTHORIZATION')
                                  AND ReservedAtUtc > %s THEN Amount ELSE 0 END), 0) AS local_after_cutoff
            FROM ctl.MddMacQuotaPermit WITH (UPDLOCK, HOLDLOCK)
            WHERE MonthKey = %s
            """,
            (local_cutoff, month_key),
        )
        local_quota = cursor.fetchone()
        baseline_total = (
            int(baseline_mapping["ConfirmedProviderAttempts"] or 0)
            + int(baseline_mapping["ConservativeUnknownAttempts"] or 0)
            + int(baseline_mapping["ExplicitSharedConsumerReservedAttempts"] or 0)
        )
        current_accounted = baseline_total + int(_row_value(local_quota, "local_after_cutoff", 0) or 0)
        if current_accounted >= AUTOMATIC_RETRY_PROTECTION_BOUNDARY:
            raise RuntimeError(
                "shared-credential quota does not leave the approved automatic-retry protection headroom"
            )
        if current_accounted >= NORMAL_OPERATIONAL_MAXIMUM or current_accounted >= ABSOLUTE_PROVIDER_CEILING:
            raise RuntimeError("shared-credential quota does not leave approved operational headroom")
        evidence["quota_headroom"] = {
            "baseline_total": baseline_total,
            "local_usage_after_cutoff": int(_row_value(local_quota, "local_after_cutoff", 0) or 0),
            "current_conservative_accounted_total": current_accounted,
            "normal_operational_headroom": NORMAL_OPERATIONAL_MAXIMUM - current_accounted,
            "retry_protection_headroom": AUTOMATIC_RETRY_PROTECTION_BOUNDARY - current_accounted,
            "provider_ceiling_headroom": ABSOLUTE_PROVIDER_CEILING - current_accounted,
        }
        evidence["provider_period"] = {
            "month_key": month_key,
            "start_utc": _utc_datetime(baseline_mapping["ProviderPeriodStartUtc"]),
            "end_utc": _utc_datetime(baseline_mapping["ProviderPeriodEndUtc"]),
            "timezone": str(baseline_mapping["ProviderPeriodTimezone"] or ""),
        }

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
                reason="TIERED50_ACTIVATION_BLOCKED_BEFORE_SQL_COMMIT",
                evidence=evidence,
            )
        except Exception:
            pass
        raise
    finally:
        connection.close()

    try:
        installed = install_launch_agent(
            activate=True,
            runtime_config_path=str(limited_runtime["config_path"]),
            mode=TIERED50_MODE,
        )
        bootstrap_launch_agent(activate=True, mode=TIERED50_MODE)
    except BaseException as exc:
        evidence["launch_agent_activation_error_type"] = type(exc).__name__
        try:
            _record_cutover_outage(
                effective_at=effective_at,
                reason="TIERED50_LAUNCHAGENT_START_FAILED_AFTER_SQL_ACTIVATION",
                evidence=evidence,
            )
        except Exception:
            pass
        raise RuntimeError("Tiered50 SQL activation committed but the existing LaunchAgent could not be started") from exc

    return {
        "status": "PASS",
        "database": EXPECTED_SQL_DATABASE,
        "target_rows_activated": 43,
        "sampling_era_action": era_action,
        "effective_at_utc": effective_at.isoformat().replace("+00:00", "Z"),
        "mdd_requests_issued": 0,
        "writes_performed": 44 if era_action == "INSERTED" else 43,
        "legacy7_status": "ALREADY_RETIRED_NOT_MUTATED",
        "launch_agent": {
            "status": "STARTED",
            "label": installed.get("label"),
            "installed_plist_path": installed.get("installed_plist_path"),
            "runtime_bundle_path": installed.get("runtime_bundle_path"),
        },
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
