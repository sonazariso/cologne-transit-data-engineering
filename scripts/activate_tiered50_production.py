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
import os
import plistlib
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.domain import (  # noqa: E402
    ABSOLUTE_PROVIDER_CEILING,
    AUTOMATIC_RETRY_PROTECTION_BOUNDARY,
    MAC_TIERED50_PRODUCTION_COLLECTION_CONTEXT,
    NORMAL_OPERATIONAL_MAXIMUM,
    SamplingEra,
)
from cologne_transit_collector.admin_evidence import (  # noqa: E402
    AdminEvidenceError,
    validate_admin_evidence_artifact,
)
from cologne_transit_collector.manifest import load_default_manifest  # noqa: E402
from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlConnectionSettings,
    MssqlPersistence,
    connect_mssql,
)
from cologne_transit_collector.quota_baseline import validate_stored_baseline_row  # noqa: E402
from cologne_transit_collector.conservative_bootstrap import (  # noqa: E402
    BOOTSTRAP_AUTHORIZATION_MODE,
    validate_stored_bootstrap_row,
)
from cologne_transit_collector.runtime_configuration import (  # noqa: E402
    EXPECTED_SQL_DATABASE,
    MSSQL_CREDENTIAL,
    RUNTIME_CONFIG_PATH_ENV,
    load_local_runtime_values,
    resolve_secret,
)
from install_macos_launch_agent import (  # noqa: E402
    TIERED50_INSTALLED_PATH,
    TIERED50_LABEL,
    TIERED50_MODE,
    _tiered50_bundle_fingerprint,
    bootstrap as bootstrap_launch_agent,
    build_installed_plist,
    install as install_launch_agent,
    tiered50_runtime_bundle_directory,
)
from cologne_transit_collector.schedule import validate_launchd_wall_clock_schedule  # noqa: E402


TARGET_TABLE = "ctl.MddMacTiered50ProductionTarget"
OPERATIONAL_EVIDENCE_PATH = ROOT / "docs" / "operational" / "tiered50-production-evidence-current-20261008.json"
ERA_CODE = "TIERED50_PRODUCTION"
TIMESTAMP_KIND = "REAL_TIERED50_CUTOVER"
RESUME_EVENT_TABLE = "ctl.MddMacProductionResumeEvent"
EXPECTED_TIERED50_TARGET_IDS = frozenset(range(8, 51))
DEFAULT_RESUME_KEY = "TIERED50_PANEL_V1:RESUME:CURRENT_SAMPLING_ERA"


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


def _administrator_connection_settings() -> MssqlConnectionSettings:
    """Resolve an explicit administrator path without runtime fallback."""

    values: dict[str, str] = {}
    missing: list[str] = []
    for suffix in ("HOST", "DATABASE", "USER", "PASSWORD"):
        name = f"MSSQL_ADMIN_{suffix}"
        value = os.environ.get(name)
        if value is None or not value.strip():
            missing.append(name)
        else:
            values[f"MSSQL_{suffix}"] = value
    for suffix in ("PORT", "LOGIN_TIMEOUT_SECONDS", "QUERY_TIMEOUT_SECONDS"):
        name = f"MSSQL_ADMIN_{suffix}"
        value = os.environ.get(name)
        if value is not None and value.strip():
            values[f"MSSQL_{suffix}"] = value
    if missing:
        raise RuntimeError(
            "MISSING_ADMIN_CREDENTIALS_OR_PERMISSIONS: explicitly configure " + ",".join(missing)
        )
    settings = MssqlConnectionSettings.from_environment(values)
    if settings.database != EXPECTED_SQL_DATABASE:
        raise RuntimeError(f"SQL Server target database must be {EXPECTED_SQL_DATABASE}")
    if str(settings.user or "").casefold() == "colognetransitcollector":
        raise RuntimeError("Tiered50 lifecycle writes require a separately authorized administrator connection")
    return settings


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
    schedule = validate_launchd_wall_clock_schedule(rendered)
    environment = dict(rendered.get("EnvironmentVariables", {}))
    if environment.get("COLLECTOR_MAC_TIERED50_ENABLED") != "1":
        raise RuntimeError("Tiered50 LaunchAgent enable gate is not set in the approved render")
    if any(marker in json.dumps(rendered, ensure_ascii=False) for marker in ("MDD_API_KEY", "MSSQL_PASSWORD", "SQL_SERVER_PASSWORD")):
        raise RuntimeError("Tiered50 LaunchAgent render contains a prohibited secret marker")
    return {
        "label": rendered.get("Label"),
        "schedule": schedule,
        "runtime_config_path": runtime_config_path,
        "secret_free": True,
        "activation_gate": "VALIDATED",
    }


def _validate_operational_evidence(
    *,
    now: datetime,
    require_historical_heartbeat: bool = True,
) -> dict[str, Any]:
    """Require fresh administrator evidence before changing production state."""

    try:
        evidence = json.loads(OPERATIONAL_EVIDENCE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("fresh administrator operational evidence is unavailable") from exc
    generated_at = _utc_datetime(evidence.get("generated_at_utc"))
    if generated_at is None or generated_at > now + timedelta(seconds=5) or now - generated_at > timedelta(minutes=30):
        raise RuntimeError("administrator operational evidence is missing or stale")
    try:
        authenticated = validate_admin_evidence_artifact(
            evidence,
            now_utc=now,
            expected_database=EXPECTED_SQL_DATABASE,
            public_key_path=os.environ.get("MSSQL_ADMIN_EVIDENCE_PUBLIC_KEY"),
        )
    except AdminEvidenceError as exc:
        raise RuntimeError(str(exc)) from exc
    host = evidence.get("host")
    sql = evidence.get("sql")
    if not isinstance(host, dict) or not isinstance(sql, dict):
        raise RuntimeError("administrator operational evidence is structurally incomplete")
    admin_authorization = sql.get("admin_authorization")
    if not isinstance(admin_authorization, dict) or admin_authorization.get("status") != "PASS":
        raise RuntimeError("MISSING_ADMIN_CREDENTIALS_OR_PERMISSIONS: administrative-query authorization is not proven")
    admin_connection = sql.get("admin_connection")
    if not isinstance(admin_connection, dict) or admin_connection.get("status") != "PASS":
        raise RuntimeError("MISSING_ADMIN_CREDENTIALS_OR_PERMISSIONS: administrative connection is not authorized")
    backup = sql.get("backup_verifyonly")
    if not isinstance(backup, dict) or backup.get("status") != "PASS":
        raise RuntimeError("latest SQL backup VERIFYONLY evidence is not passing")
    backup_gate = sql.get("backup_gate")
    if not isinstance(backup_gate, dict) or backup_gate.get("status") != "PASS":
        raise RuntimeError("administrator backup gate is not independently proven")
    volumes = sql.get("volume_capacity")
    if not isinstance(volumes, list) or not any(int(row.get("available_bytes", 0) or 0) > 0 for row in volumes if isinstance(row, dict)):
        raise RuntimeError("SQL database-host volume capacity is not proven")
    capacity_gate = sql.get("capacity_gate")
    if not isinstance(capacity_gate, dict) or capacity_gate.get("status") != "PASS":
        raise RuntimeError("administrator SQL capacity gate is not independently proven")
    log_space_gate = sql.get("log_space_gate")
    if not isinstance(log_space_gate, dict) or log_space_gate.get("status") != "PASS":
        raise RuntimeError("administrator SQL log-space capacity gate is not independently proven")
    vmware = host.get("vmware")
    vmware_returncode = vmware.get("returncode") if isinstance(vmware, dict) else None
    if not isinstance(vmware, dict) or vmware_returncode is None or int(vmware_returncode) != 0:
        raise RuntimeError("running Windows VM evidence is not proven")
    plist = host.get("tiered50_plist")
    if (
        not isinstance(plist, dict)
        or not plist.get("exists")
        or bool(plist.get("contains_secret_marker"))
    ):
        raise RuntimeError("installed Tiered50 LaunchAgent evidence is missing or unsafe")
    try:
        installed_schedule = {
            "StartCalendarInterval": [
                {"Minute": int(minute)}
                for minute in plist.get("start_calendar_interval_minutes", [])
            ],
            "RunAtLoad": plist.get("run_at_load"),
        }
        validate_launchd_wall_clock_schedule(installed_schedule)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("installed Tiered50 LaunchAgent is not the approved wall-clock schedule") from exc
    heartbeat = sql.get("six_consecutive_expected_heartbeats")
    if require_historical_heartbeat and (
        not isinstance(heartbeat, dict) or not heartbeat.get("all_accounted") or not heartbeat.get("all_succeeded")
    ):
        raise RuntimeError("six consecutive prior scheduled heartbeat records are not proven")
    mac_disk = host.get("mac_disk")
    if not isinstance(mac_disk, dict) or int(mac_disk.get("free_bytes", 0) or 0) <= 0:
        raise RuntimeError("macOS host capacity is not proven")
    return {
        "artifact": str(OPERATIONAL_EVIDENCE_PATH),
        "generated_at_utc": generated_at.isoformat().replace("+00:00", "Z"),
        "authenticated_artifact": authenticated,
        "backup_verifyonly": "PASS",
        "sql_volume_capacity": "PASS",
        "running_windows_vm": "PASS",
        "installed_tiered50_launchagent": "PRESENT_SECRET_FREE",
        "prior_scheduled_heartbeat_evidence": (
            "SIX_CONSECUTIVE_SUCCESSFUL_HISTORICAL_BUCKETS_READ_ONLY"
            if require_historical_heartbeat
            else "NOT_REQUIRED_FOR_RESUME; HISTORICAL_FAILURES_PRESERVED"
        ),
        "mac_host_capacity": "PASS",
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

    settings = _administrator_connection_settings()
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


def _named_value(row: Any, name: str, index: int = 0) -> Any:
    if isinstance(row, Mapping):
        lowered = {str(key).casefold(): value for key, value in row.items()}
        return lowered.get(name.casefold())
    return _row_value(row, name, index)


def validate_no_competing_production_writers(rows: Sequence[Any]) -> list[dict[str, Any]]:
    """Serialize and reject active Legacy7/Tiered50 writers under the lock."""

    writers = [
        {
            "collection_context": str(_named_value(row, "CollectionContext", 0) or ""),
            "running_count": int(_named_value(row, "RunningCount", 1) or 0),
        }
        for row in rows
    ]
    if any(item["running_count"] > 0 for item in writers):
        raise RuntimeError("a competing production collector writer is active")
    return writers


def validate_existing_tiered50_activation(
    *,
    target_rows: Sequence[Any],
    era_rows: Sequence[Any],
    current_era_rows: Sequence[Any],
    activation_rows: Sequence[Any],
    outage_rows: Sequence[Any],
) -> dict[str, Any]:
    """Validate immutable first-cutover state before a resume is allowed."""

    if len(target_rows) != 43:
        raise RuntimeError("resume requires exactly 43 materialized Tiered50 target rows")
    target_ids = {
        int(_named_value(row, "SamplingTargetId", 0) or 0)
        for row in target_rows
    }
    if target_ids != set(EXPECTED_TIERED50_TARGET_IDS):
        raise RuntimeError("resume requires the immutable Tiered50 target IDs 8 through 50")
    if any(
        not bool(_named_value(row, "IsEnabled", 1))
        or str(_named_value(row, "ActivationStatus", 2) or "") != "ACTIVE"
        or str(_named_value(row, "PanelVersion", 3) or "") != "TIERED50_PANEL_V1"
        or str(_named_value(row, "SamplingEra", 4) or "") != "Tiered50"
        for row in target_rows
    ):
        raise RuntimeError("Tiered50 target activation state is not internally consistent")
    boundaries = {
        _utc_datetime(_named_value(row, "ActivatedAtUtc", 5))
        for row in target_rows
    }
    if len(boundaries) != 1 or None in boundaries:
        raise RuntimeError("Tiered50 target rows do not share one immutable activation boundary")
    effective_at = next(iter(boundaries))
    assert effective_at is not None

    if len(era_rows) != 1:
        raise RuntimeError("resume requires exactly one Tiered50 sampling-era row")
    era = era_rows[0]
    sampling_era_id = int(_named_value(era, "SamplingEraId", 0) or 0)
    if (
        str(_named_value(era, "PanelVersion", 1) or "") != "TIERED50_PANEL_V1"
        or _utc_datetime(_named_value(era, "EffectiveAtUtc", 2)) != effective_at
        or str(_named_value(era, "TimestampKind", 3) or "") != TIMESTAMP_KIND
        or not bool(_named_value(era, "IsActive", 4))
        or bool(_named_value(era, "IsSynthetic", 5))
    ):
        raise RuntimeError("Tiered50 sampling-era identity or boundary is invalid")

    if len(current_era_rows) != 1:
        raise RuntimeError("resume requires one current Tiered50 production-era pointer")
    current = current_era_rows[0]
    if (
        int(_named_value(current, "CurrentMarkerId", 0) or 0) != 1
        or int(_named_value(current, "SamplingEraId", 1) or 0) != sampling_era_id
        or str(_named_value(current, "EraCode", 2) or "") != ERA_CODE
        or str(_named_value(current, "PanelVersion", 3) or "") != "TIERED50_PANEL_V1"
        or _utc_datetime(_named_value(current, "EffectiveAtUtc", 4)) != effective_at
    ):
        raise RuntimeError("current Tiered50 production-era pointer is inconsistent")

    matching_activations = [
        row
        for row in activation_rows
        if str(_named_value(row, "Tiered50ActivationStatus", 1) or "") == "ACTIVATED"
        and _utc_datetime(_named_value(row, "Tiered50ActivationAtUtc", 2)) == effective_at
    ]
    if len(matching_activations) != 1:
        raise RuntimeError("the historical Tiered50 activation record is missing or ambiguous")
    matching_outages = [
        row
        for row in outage_rows
        if str(_named_value(row, "Tiered50ActivationStatus", 1) or "") == "OUTAGE"
        and _utc_datetime(_named_value(row, "Tiered50ActivationAtUtc", 2)) == effective_at
    ]
    if not matching_outages:
        raise RuntimeError("the historical Tiered50 outage record is missing")
    return {
        "sampling_era_id": sampling_era_id,
        "effective_at_utc": effective_at,
        "target_count": len(target_rows),
        "target_ids": sorted(target_ids),
        "activation_record_count": len(matching_activations),
        "outage_record_count": len(matching_outages),
        "historical_state_preserved": True,
    }


def _installed_tiered50_resume_readiness(*, runtime_config_path: str) -> dict[str, Any]:
    """Validate the already-installed bundle and plist without enabling it."""

    if not TIERED50_INSTALLED_PATH.exists():
        raise RuntimeError("installed Tiered50 LaunchAgent is missing")
    try:
        with TIERED50_INSTALLED_PATH.open("rb") as handle:
            installed = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException) as exc:
        raise RuntimeError("installed Tiered50 LaunchAgent is unreadable") from exc
    if installed.get("Label") != TIERED50_LABEL:
        raise RuntimeError("installed Tiered50 LaunchAgent label is not Tiered50")
    if any(
        marker in json.dumps(installed, ensure_ascii=False)
        for marker in ("MDD_API_KEY", "MSSQL_PASSWORD", "SQL_SERVER_PASSWORD")
    ):
        raise RuntimeError("installed Tiered50 LaunchAgent contains a prohibited secret marker")
    validate_launchd_wall_clock_schedule(installed)
    expected_bundle = tiered50_runtime_bundle_directory()
    expected_entry = expected_bundle / "macos_launchd_entry.py"
    arguments = [str(value) for value in installed.get("ProgramArguments", [])]
    if len(arguments) < 2 or Path(arguments[1]).resolve() != expected_entry.resolve():
        raise RuntimeError("installed Tiered50 runtime bundle does not match the latest source fingerprint")
    if not expected_entry.is_file() or not (expected_bundle / "src").is_dir():
        raise RuntimeError("latest Tiered50 runtime bundle is not installed")
    environment = dict(installed.get("EnvironmentVariables", {}))
    if environment.get(RUNTIME_CONFIG_PATH_ENV) != str(Path(runtime_config_path).expanduser().resolve()):
        raise RuntimeError("installed Tiered50 LaunchAgent references a different runtime configuration")
    return {
        "label": TIERED50_LABEL,
        "installed_plist_path": str(TIERED50_INSTALLED_PATH),
        "runtime_bundle_path": str(expected_bundle),
        "runtime_bundle_fingerprint": _tiered50_bundle_fingerprint(),
        "disabled_before_resume": bool(installed.get("Disabled")),
        "loaded_before_resume": _launch_agent_loaded(),
        "secret_free": True,
        "schedule": validate_launchd_wall_clock_schedule(installed),
    }


def _launch_agent_loaded() -> bool:
    result = subprocess.run(
        ["/bin/launchctl", "print", f"gui/{os.getuid()}/{TIERED50_LABEL}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.returncode == 0


def _disable_tiered50_after_resume_failure(*, runtime_config_path: str) -> None:
    """Leave the existing Tiered50 artifact stopped after a partial start."""

    try:
        install_launch_agent(
            activate=False,
            runtime_config_path=runtime_config_path,
            mode=TIERED50_MODE,
        )
        bootstrap_launch_agent(activate=False, mode=TIERED50_MODE)
    except Exception:
        # The caller reports the original resume failure.  The artifact was
        # already safe-by-default and no SQL activation state is rewritten.
        pass


def _record_resume_event(
    *,
    settings: MssqlConnectionSettings,
    resume_key: str,
    lifecycle: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Record one actual resume after launchd has successfully started."""

    connection = connect_mssql(settings)
    event: dict[str, Any]
    try:
        cursor = connection.cursor()
        cursor.execute("SET XACT_ABORT ON; BEGIN TRANSACTION;")
        cursor.execute(
            f"""
            SELECT TOP (1) ResumeEventId, ResumeKey, PanelVersion, EraCode,
                   SamplingEraId, OriginalActivationAtUtc, ResumeAtUtc,
                   LaunchAgentLabel
            FROM {RESUME_EVENT_TABLE} WITH (UPDLOCK, HOLDLOCK)
            WHERE ResumeKey = %s
            """,
            (resume_key,),
        )
        existing = cursor.fetchone()
        if existing is not None:
            same = (
                str(_named_value(existing, "PanelVersion", 2) or "") == "TIERED50_PANEL_V1"
                and str(_named_value(existing, "EraCode", 3) or "") == ERA_CODE
                and int(_named_value(existing, "SamplingEraId", 4) or 0) == int(lifecycle["sampling_era_id"])
                and _utc_datetime(_named_value(existing, "OriginalActivationAtUtc", 5))
                == lifecycle["effective_at_utc"]
                and str(_named_value(existing, "LaunchAgentLabel", 7) or "") == TIERED50_LABEL
            )
            if not same:
                raise RuntimeError("resume key already exists for a conflicting Tiered50 lifecycle")
            cursor.execute("COMMIT TRANSACTION;")
            connection.commit()
            event = {
                "status": "IDEMPOTENT_ALREADY_RECORDED",
                "resume_event_id": int(_named_value(existing, "ResumeEventId", 0) or 0),
                "resume_key": resume_key,
                "resume_at_utc": _utc_datetime(_named_value(existing, "ResumeAtUtc", 6)),
            }
        else:
            cursor.execute(
                f"""
                INSERT INTO {RESUME_EVENT_TABLE}
                    (ResumeKey, PanelVersion, EraCode, SamplingEraId,
                     OriginalActivationAtUtc, ResumeAtUtc, LaunchAgentLabel,
                     EvidenceJson)
                OUTPUT INSERTED.ResumeEventId, INSERTED.ResumeAtUtc
                VALUES (%s, %s, %s, %s, %s, SYSUTCDATETIME(), %s, %s)
                """,
                (
                    resume_key,
                    "TIERED50_PANEL_V1",
                    ERA_CODE,
                    int(lifecycle["sampling_era_id"]),
                    lifecycle["effective_at_utc"],
                    TIERED50_LABEL,
                    json.dumps(dict(evidence), ensure_ascii=False, sort_keys=True, default=str),
                ),
            )
            inserted = cursor.fetchone()
            if inserted is None:
                raise RuntimeError("SQL Server did not return the Tiered50 resume event")
            cursor.execute("COMMIT TRANSACTION;")
            connection.commit()
            event = {
                "status": "RECORDED",
                "resume_event_id": int(_named_value(inserted, "ResumeEventId", 0) or 0),
                "resume_key": resume_key,
                "resume_at_utc": _utc_datetime(_named_value(inserted, "ResumeAtUtc", 1)),
            }
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    return event


def resume_tiered50_production(*, resume_key: str | None = None) -> dict[str, Any]:
    """Safely resume the existing Tiered50 installation without recutting over."""

    now = datetime.now(timezone.utc)
    key = str(resume_key or DEFAULT_RESUME_KEY).strip()
    if not key or len(key) > 240 or any(marker in key.casefold() for marker in ("password", "secret", "token")):
        raise RuntimeError("resume key is empty, too long, or contains a prohibited marker")

    settings = _administrator_connection_settings()
    limited_runtime = _validate_limited_runtime(now=now)
    quota = dict(limited_runtime.get("quota") or {})
    if not quota.get("state_available"):
        raise RuntimeError("current SQL quota ledger is not available for resume")
    if int(quota.get("reserved_shared_usage", 0) or 0) != 0:
        raise RuntimeError("pending quota permits must be zero before resume")
    accounted = int(quota.get("known_total", -1) or -1)
    if accounted < 0 or accounted >= AUTOMATIC_RETRY_PROTECTION_BOUNDARY:
        raise RuntimeError("current SQL quota ledger does not leave approved retry headroom")
    launch_agent = _installed_tiered50_resume_readiness(runtime_config_path=str(limited_runtime["config_path"]))
    operational_evidence = _validate_operational_evidence(now=now, require_historical_heartbeat=False)

    manifest = load_default_manifest(ROOT)
    connection = connect_mssql(settings)
    lifecycle: dict[str, Any]
    existing_resume: Any = None
    latest_resume: Any = None
    competing_writers: list[dict[str, Any]] = []
    try:
        cursor = connection.cursor()
        cursor.execute("SET XACT_ABORT ON; BEGIN TRANSACTION;")
        cursor.execute(
            f"""
            SELECT SamplingTargetId, IsEnabled, ActivationStatus,
                   PanelVersion, SamplingEra, ActivatedAtUtc
            FROM {TARGET_TABLE} WITH (UPDLOCK, HOLDLOCK)
            WHERE PanelVersion = %s AND SamplingEra = %s
            ORDER BY SamplingTargetId
            """,
            (manifest.panel_version, SamplingEra.TIERED50.value),
        )
        target_rows = list(cursor.fetchall())
        cursor.execute(
            """
            SELECT SamplingEraId, PanelVersion, EffectiveAtUtc,
                   TimestampKind, IsActive, IsSynthetic
            FROM ctl.MddMacSamplingEra WITH (UPDLOCK, HOLDLOCK)
            WHERE EraCode = %s
            """,
            (ERA_CODE,),
        )
        era_rows = list(cursor.fetchall())
        cursor.execute(
            """
            SELECT CurrentMarkerId, SamplingEraId, EraCode, PanelVersion,
                   EffectiveAtUtc
            FROM ctl.MddMacCurrentProductionEra WITH (UPDLOCK, HOLDLOCK)
            WHERE CurrentMarkerId = 1
            """
        )
        current_era_rows = list(cursor.fetchall())
        cursor.execute(
            """
            SELECT CutoverKey, Tiered50ActivationStatus,
                   Tiered50ActivationAtUtc
            FROM ctl.MddMacProductionCutover WITH (UPDLOCK, HOLDLOCK)
            WHERE PanelVersion = %s AND Tiered50ActivationAtUtc IS NOT NULL
            ORDER BY CutoverId
            """,
            (manifest.panel_version,),
        )
        cutover_rows = list(cursor.fetchall())
        activation_rows = [
            row for row in cutover_rows
            if str(_named_value(row, "Tiered50ActivationStatus", 1) or "") == "ACTIVATED"
        ]
        outage_rows = [
            row for row in cutover_rows
            if str(_named_value(row, "Tiered50ActivationStatus", 1) or "") == "OUTAGE"
        ]
        lifecycle = validate_existing_tiered50_activation(
            target_rows=target_rows,
            era_rows=era_rows,
            current_era_rows=current_era_rows,
            activation_rows=activation_rows,
            outage_rows=outage_rows,
        )
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
        competing_writers = validate_no_competing_production_writers(list(cursor.fetchall()))
        cursor.execute(
            f"""
            SELECT TOP (20) ResumeEventId, ResumeKey, PanelVersion, EraCode,
                   SamplingEraId, OriginalActivationAtUtc, ResumeAtUtc,
                   LaunchAgentLabel
            FROM {RESUME_EVENT_TABLE} WITH (UPDLOCK, HOLDLOCK)
            WHERE PanelVersion = %s
            ORDER BY ResumeEventId DESC
            """,
            (manifest.panel_version,),
        )
        resume_rows = list(cursor.fetchall())
        latest_resume = resume_rows[0] if resume_rows else None
        existing_resume = next(
            (
                row
                for row in resume_rows
                if str(_named_value(row, "ResumeKey", 1) or "") == key
            ),
            None,
        )
        cursor.execute("COMMIT TRANSACTION;")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()

    evidence: dict[str, Any] = {
        "operation": "RESUME",
        "resume_key": key,
        "requested_at_utc": now.isoformat().replace("+00:00", "Z"),
        "lifecycle": lifecycle,
        "competing_writers": competing_writers,
        "limited_runtime": {
            "settings": limited_runtime.get("settings"),
            "login": limited_runtime.get("login"),
            "quota": quota,
        },
        "launch_agent_readiness": launch_agent,
        "operational_evidence": operational_evidence,
        "historical_requests_replayed": 0,
        "legacy7_mutated": False,
        "target_rows_mutated": 0,
        "sampling_eras_created": 0,
    }

    active_resume = latest_resume if latest_resume is not None else existing_resume
    if active_resume is not None and launch_agent.get("loaded_before_resume"):
        existing_same = (
            str(_named_value(active_resume, "PanelVersion", 2) or "") == manifest.panel_version
            and int(_named_value(active_resume, "SamplingEraId", 4) or 0) == int(lifecycle["sampling_era_id"])
            and _utc_datetime(_named_value(active_resume, "OriginalActivationAtUtc", 5))
            == lifecycle["effective_at_utc"]
        )
        if not existing_same:
            raise RuntimeError("existing Tiered50 resume event conflicts with immutable activation state")
        return {
            "status": "IDEMPOTENT_ALREADY_ACTIVE",
            "operation": "RESUME",
            "resume_key": str(_named_value(active_resume, "ResumeKey", 1) or key),
            "requested_resume_key": key,
            "effective_at_utc": lifecycle["effective_at_utc"].isoformat().replace("+00:00", "Z"),
            "sampling_era_id": lifecycle["sampling_era_id"],
            "launch_agent": launch_agent,
            "historical_state_preserved": True,
            "mdd_requests_issued": 0,
            "writes_performed": 0,
        }
    if existing_resume is not None:
        raise RuntimeError(
            "a previous resume event exists but Tiered50 is stopped; provide a new explicit --resume-key"
        )

    try:
        # Re-render only the existing Tiered50 artifact with the current source
        # fingerprint.  This does not materialize targets or touch SQL history.
        installed = install_launch_agent(
            activate=True,
            runtime_config_path=str(limited_runtime["config_path"]),
            mode=TIERED50_MODE,
        )
        bootstrap_launch_agent(activate=True, mode=TIERED50_MODE)
        if not _launch_agent_loaded():
            raise RuntimeError("Tiered50 LaunchAgent did not remain loaded after bootstrap")
    except BaseException as exc:
        _disable_tiered50_after_resume_failure(runtime_config_path=str(limited_runtime["config_path"]))
        raise RuntimeError("Tiered50 resume LaunchAgent startup failed; Tiered50 was left stopped") from exc

    try:
        recorded = _record_resume_event(
            settings=settings,
            resume_key=key,
            lifecycle=lifecycle,
            evidence=evidence,
        )
    except BaseException as exc:
        _disable_tiered50_after_resume_failure(runtime_config_path=str(limited_runtime["config_path"]))
        raise RuntimeError("Tiered50 resume event could not be recorded; Tiered50 was left stopped") from exc

    resume_at = recorded.get("resume_at_utc")
    return {
        "status": "PASS" if recorded["status"] == "RECORDED" else recorded["status"],
        "operation": "RESUME",
        "resume_key": key,
        "resume_event": recorded,
        "effective_at_utc": lifecycle["effective_at_utc"].isoformat().replace("+00:00", "Z"),
        "sampling_era_id": lifecycle["sampling_era_id"],
        "target_rows_mutated": 0,
        "sampling_eras_created": 0,
        "historical_state_preserved": True,
        "historical_requests_replayed": 0,
        "legacy7_status": "RETIRED_NOT_MUTATED",
        "launch_agent": {
            "status": "STARTED",
            "label": installed.get("label"),
            "installed_plist_path": installed.get("installed_plist_path"),
            "runtime_bundle_path": installed.get("runtime_bundle_path"),
        },
        "resume_at_utc": resume_at,
        "mdd_requests_issued": 0,
        "writes_performed": 1 if recorded["status"] == "RECORDED" else 0,
    }


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
    settings = _administrator_connection_settings()
    limited_runtime = _validate_limited_runtime(now=now)
    launch_agent = _validate_launch_agent_template(runtime_config_path=str(limited_runtime["config_path"]))
    operational_evidence = _validate_operational_evidence(now=now)
    connection = connect_mssql(settings)
    evidence: dict[str, object] = {
        "legacy7_status": "ALREADY_RETIRED_NOT_MUTATED",
        "limited_runtime": limited_runtime,
        "launch_agent_template": launch_agent,
        "operational_evidence": operational_evidence,
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
        authorization_mode = "COMPLETE"
        baseline = baseline_candidates[0] if len(baseline_candidates) == 1 else None
        if len(baseline_candidates) > 1:
            raise RuntimeError(
                "multiple provider billing-period baselines cover the cutover boundary; activation is ambiguous"
            )
        baseline_mapping: dict[str, Any] | None = None
        bootstrap_mapping: dict[str, Any] | None = None
        if baseline is not None:
            baseline_mapping = {
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
            baseline_valid, baseline_reason = validate_stored_baseline_row(
                baseline_mapping,
                at_utc=effective_at,
            )
            if not baseline_valid:
                baseline = None

        if baseline is None:
            cursor.execute(
                """
                SELECT TOP (2) AuthorizationId, AuthorizationKey, MonthKey,
                       AuthorizationMode, AccountingModel,
                       ScreenshotCumulativeRequests, ScreenshotMonthlyLimit,
                       ScreenshotSha256, ConfirmedLocalAttempts,
                       ConservativeUnknownLocalAttempts, PendingLocalReservations,
                       AccountedTotalAtAuthorization, ProviderQuotaCeiling,
                       NormalOperationalMaximum, RetryProtectionBoundary,
                       ValidFromUtc, LocalUsageIncludedThroughUtc, StopAtUtc,
                       OperatorApprovalAtUtc, OperatorApprovalStatementHash,
                       CredentialExclusivityConfirmed, ProviderScopeVerified,
                       ProviderMeasurementCutoffEstablished,
                       ProviderResetTimezoneEstablished, UnexpectedExternalUsage,
                       SourceEvidenceHash, AuthorizationStatus, EvidenceJson,
                       RecordedAtUtc, RevokedAtUtc, RevocationReason
                FROM ctl.MddMacConservativeBootstrapAuthorization WITH (UPDLOCK, HOLDLOCK)
                WHERE AuthorizationStatus = 'ACTIVE'
                  AND ValidFromUtc <= %s
                  AND StopAtUtc > %s
                ORDER BY ValidFromUtc DESC, AuthorizationId DESC
                """,
                (effective_at, effective_at),
            )
            bootstrap_rows = list(cursor.fetchall())
            if len(bootstrap_rows) != 1:
                raise RuntimeError(
                    "current provider baseline is unavailable and no single active bounded October bootstrap covers the cutover boundary"
                )
            bootstrap = bootstrap_rows[0]
            bootstrap_mapping = {
                "AuthorizationId": _row_value(bootstrap, "AuthorizationId", 0),
                "AuthorizationKey": _row_value(bootstrap, "AuthorizationKey", 1),
                "MonthKey": _row_value(bootstrap, "MonthKey", 2),
                "AuthorizationMode": _row_value(bootstrap, "AuthorizationMode", 3),
                "AccountingModel": _row_value(bootstrap, "AccountingModel", 4),
                "ScreenshotCumulativeRequests": _row_value(bootstrap, "ScreenshotCumulativeRequests", 5),
                "ScreenshotMonthlyLimit": _row_value(bootstrap, "ScreenshotMonthlyLimit", 6),
                "ScreenshotSha256": _row_value(bootstrap, "ScreenshotSha256", 7),
                "ConfirmedLocalAttempts": _row_value(bootstrap, "ConfirmedLocalAttempts", 8),
                "ConservativeUnknownLocalAttempts": _row_value(bootstrap, "ConservativeUnknownLocalAttempts", 9),
                "PendingLocalReservations": _row_value(bootstrap, "PendingLocalReservations", 10),
                "AccountedTotalAtAuthorization": _row_value(bootstrap, "AccountedTotalAtAuthorization", 11),
                "ProviderQuotaCeiling": _row_value(bootstrap, "ProviderQuotaCeiling", 12),
                "NormalOperationalMaximum": _row_value(bootstrap, "NormalOperationalMaximum", 13),
                "RetryProtectionBoundary": _row_value(bootstrap, "RetryProtectionBoundary", 14),
                "ValidFromUtc": _row_value(bootstrap, "ValidFromUtc", 15),
                "LocalUsageIncludedThroughUtc": _row_value(bootstrap, "LocalUsageIncludedThroughUtc", 16),
                "StopAtUtc": _row_value(bootstrap, "StopAtUtc", 17),
                "OperatorApprovalAtUtc": _row_value(bootstrap, "OperatorApprovalAtUtc", 18),
                "OperatorApprovalStatementHash": _row_value(bootstrap, "OperatorApprovalStatementHash", 19),
                "CredentialExclusivityConfirmed": _row_value(bootstrap, "CredentialExclusivityConfirmed", 20),
                "ProviderScopeVerified": _row_value(bootstrap, "ProviderScopeVerified", 21),
                "ProviderMeasurementCutoffEstablished": _row_value(bootstrap, "ProviderMeasurementCutoffEstablished", 22),
                "ProviderResetTimezoneEstablished": _row_value(bootstrap, "ProviderResetTimezoneEstablished", 23),
                "UnexpectedExternalUsage": _row_value(bootstrap, "UnexpectedExternalUsage", 24),
                "SourceEvidenceHash": _row_value(bootstrap, "SourceEvidenceHash", 25),
                "AuthorizationStatus": _row_value(bootstrap, "AuthorizationStatus", 26),
                "EvidenceJson": _row_value(bootstrap, "EvidenceJson", 27),
                "RecordedAtUtc": _row_value(bootstrap, "RecordedAtUtc", 28),
                "RevokedAtUtc": _row_value(bootstrap, "RevokedAtUtc", 29),
                "RevocationReason": _row_value(bootstrap, "RevocationReason", 30),
            }
            bootstrap_valid, bootstrap_reason = validate_stored_bootstrap_row(
                bootstrap_mapping,
                at_utc=effective_at,
            )
            if not bootstrap_valid:
                raise RuntimeError(f"bounded October bootstrap is not valid: {bootstrap_reason}")
            authorization_mode = BOOTSTRAP_AUTHORIZATION_MODE

        month_key = str(
            (baseline_mapping if baseline is not None else bootstrap_mapping)["MonthKey"]  # type: ignore[index]
            or ""
        )
        selected_mapping = baseline_mapping if baseline is not None else bootstrap_mapping
        assert selected_mapping is not None
        local_cutoff = _utc_datetime(selected_mapping["LocalUsageIncludedThroughUtc"])
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
        if authorization_mode == BOOTSTRAP_AUTHORIZATION_MODE:
            baseline_total = int(selected_mapping["AccountedTotalAtAuthorization"] or 0)
        else:
            baseline_total = (
                int(selected_mapping["ConfirmedProviderAttempts"] or 0)
                + int(selected_mapping["ConservativeUnknownAttempts"] or 0)
                + int(selected_mapping["ExplicitSharedConsumerReservedAttempts"] or 0)
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
        evidence["authorization_mode"] = authorization_mode
        evidence["provider_period"] = (
            {
                "month_key": month_key,
                "start_utc": _utc_datetime(selected_mapping["ProviderPeriodStartUtc"]),
                "end_utc": _utc_datetime(selected_mapping["ProviderPeriodEndUtc"]),
                "timezone": str(selected_mapping["ProviderPeriodTimezone"] or ""),
            }
            if authorization_mode == "COMPLETE"
            else {
                "provider_metadata_verified": False,
                "valid_from_utc": _utc_datetime(selected_mapping["ValidFromUtc"]),
                "stop_at_utc": _utc_datetime(selected_mapping["StopAtUtc"]),
            }
        )

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
        "--resume",
        action="store_true",
        help="resume the already activated Tiered50 panel without creating a cutover or sampling era",
    )
    parser.add_argument(
        "--resume-key",
        help="explicit idempotency key for a new resume transition; never contains a secret",
    )
    parser.add_argument(
        "--legacy7-stopped",
        action="store_true",
        help="deprecated compatibility flag; launchd state is always retired and verified directly",
    )
    parser.add_argument("--effective-at", help="real UTC cutover boundary, required with --apply")
    args = parser.parse_args(argv)
    try:
        if args.resume and not args.apply:
            raise RuntimeError("--resume requires explicit --apply")
        if args.resume and args.effective_at:
            raise RuntimeError("--resume never accepts or fabricates an effective cutover timestamp")
        result = (
            resume_tiered50_production(resume_key=args.resume_key)
            if args.resume
            else apply_activation(
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
