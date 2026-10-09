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
import re
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
    load_current_evidence_artifact,
    normalize_sql_instance_identity,
    resolve_current_evidence_path,
    sql_instance_identities_match,
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
    BOOTSTRAP_LATEST_STOP_UTC,
    validate_stored_bootstrap_row,
)
from cologne_transit_collector.runtime_configuration import (  # noqa: E402
    ADMIN_KEYCHAIN_SERVICE as RUNTIME_ADMIN_KEYCHAIN_SERVICE,
    EXPECTED_SQL_DATABASE,
    MacOSKeychainSecretProvider,
    MSSQL_CREDENTIAL,
    RUNTIME_CONFIG_PATH_ENV,
    load_local_runtime_values,
    resolve_administrator_connection_settings,
    resolve_secret,
)
from provision_macos_runtime_login import inspect_runtime_permissions  # noqa: E402
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
OPERATIONAL_EVIDENCE_PATH = resolve_current_evidence_path(repository_root=ROOT)
ERA_CODE = "TIERED50_PRODUCTION"
TIMESTAMP_KIND = "REAL_TIERED50_CUTOVER"
RESUME_EVENT_TABLE = "ctl.MddMacProductionResumeEvent"
EXPECTED_TIERED50_TARGET_IDS = frozenset(range(8, 51))
DEFAULT_RESUME_KEY = "TIERED50_PANEL_V1:RESUME:CURRENT_SAMPLING_ERA"
RESUME_APP_LOCK_RESOURCE = "CologneTransitIntelligence:TIERED50:RESUME"
ADMIN_KEYCHAIN_SERVICE = RUNTIME_ADMIN_KEYCHAIN_SERVICE
RESUME_STOPPED_EXISTING_EVENT_STATUS = "IDEMPOTENT_EVENT_RECORDED_SERVICE_STOPPED"
RESUME_LOADED_EXISTING_EVENT_STATUS = "IDEMPOTENT_ALREADY_LOADED_NOT_VALIDATED"


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


def _authoritative_sql_utc(connection: Any) -> datetime:
    """Read the authoritative SQL UTC clock for a lifecycle decision.

    Resume authorization must not use the macOS wall clock.  The connection is
    the already locked administrator session, so the clock read is also bound
    to the SQL instance that owns the transition lock.
    """

    cursor = connection.cursor()
    try:
        cursor.execute("SELECT SYSUTCDATETIME() AS AuthoritativeUtc")
        row = cursor.fetchone()
    except BaseException as exc:
        raise RuntimeError("authoritative SQL UTC time is unavailable; resume denied") from exc
    value = _row_value(row, "AuthoritativeUtc", 0)
    parsed = _utc_datetime(value)
    if parsed is None:
        raise RuntimeError("authoritative SQL UTC time is unavailable; resume denied")
    return parsed


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
    """Resolve an explicit administrator path without runtime fallback.

    The preferred Mac path keeps the password in a user Keychain item.  A
    one-process ``MSSQL_ADMIN_PASSWORD`` override remains available for an
    operator-controlled emergency invocation, but it is never written to a
    file, plist, evidence artifact, or diagnostic.  No ``MSSQL_*`` runtime
    value is consulted for any missing administrator field.
    """

    settings, _source = resolve_administrator_connection_settings(
        os.environ,
        secret_provider=MacOSKeychainSecretProvider(),
    )
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
    runtime_sql_identity = normalize_sql_instance_identity(
        {
            "server_name": login.get("server_name"),
            "machine_name": login.get("machine_name"),
            "instance_name": login.get("instance_name"),
            "database_name": login.get("database_name"),
        }
    )
    if not runtime_sql_identity.get("server_name"):
        raise RuntimeError("authoritative runtime SQL instance identity is unavailable")
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
    runtime_permission_matrix = inspect_runtime_permissions(settings)
    if runtime_permission_matrix.get("status") != "PASS":
        missing = runtime_permission_matrix.get("missing_permissions") or []
        prohibited = runtime_permission_matrix.get("prohibited_privileges") or []
        detail = ", ".join(str(item) for item in [*missing, *prohibited])
        raise RuntimeError(
            "dedicated runtime SQL permission matrix is not proven"
            + (f": {detail}" if detail else "")
        )
    privilege_boundary = runtime_permission_matrix.get("prohibited_privilege_checks", {})
    schema = persistence.validate_runtime_schema()
    tiered_schema = persistence.validate_tiered50_runtime_schema()
    quota = persistence.current_month_usage(at_utc=now)
    if not quota or not bool(quota.get("state_available")):
        raise RuntimeError("dedicated runtime cannot read a current provider-wide quota baseline")
    return {
        "config_path": config_path,
        "settings": settings.redacted(),
        "login": login,
        "sql_instance_identity": runtime_sql_identity,
        "privilege_boundary": privilege_boundary,
        "runtime_permission_matrix": runtime_permission_matrix,
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
    expected_runtime_sql_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Require fresh administrator evidence before changing production state."""

    try:
        evidence, authenticated, current_path = load_current_evidence_artifact(
            OPERATIONAL_EVIDENCE_PATH,
            repository_root=ROOT,
            now_utc=now,
            expected_database=EXPECTED_SQL_DATABASE,
            public_key_path=os.environ.get("MSSQL_ADMIN_EVIDENCE_PUBLIC_KEY"),
            expected_sql_identity=expected_runtime_sql_identity,
        )
    except AdminEvidenceError as exc:
        raise RuntimeError(str(exc)) from exc
    generated_at = _utc_datetime(evidence.get("generated_at_utc"))
    if generated_at is None:
        raise RuntimeError("administrator operational evidence is missing or stale")
    host = evidence.get("host")
    sql = evidence.get("sql")
    if not isinstance(host, dict) or not isinstance(sql, dict):
        raise RuntimeError("administrator operational evidence is structurally incomplete")
    if expected_runtime_sql_identity is not None:
        evidence_identity = authenticated.get("sql_instance_identity")
        if not isinstance(evidence_identity, Mapping) or not sql_instance_identities_match(
            evidence_identity,
            expected_runtime_sql_identity,
        ):
            raise RuntimeError("administrator operational evidence is bound to a different SQL instance")
    admin_authorization = sql.get("admin_authorization")
    if not isinstance(admin_authorization, dict) or admin_authorization.get("status") != "PASS":
        raise RuntimeError("MISSING_ADMIN_CREDENTIALS_OR_PERMISSIONS: administrative-query authorization is not proven")
    admin_connection = sql.get("admin_connection")
    if not isinstance(admin_connection, dict) or admin_connection.get("status") != "PASS":
        raise RuntimeError("MISSING_ADMIN_CREDENTIALS_OR_PERMISSIONS: administrative connection is not authorized")
    backup = sql.get("backup_verifyonly")
    if not isinstance(backup, dict) or backup.get("status") != "PASS":
        raise RuntimeError("latest SQL backup VERIFYONLY evidence is not passing")
    latest_backup = sql.get("latest_full_backup")
    if (
        not isinstance(latest_backup, dict)
        or latest_backup.get("complete_media_set_proven") is not True
        or int(latest_backup.get("media_count", 0) or 0) < 1
        or latest_backup.get("has_backup_checksums") is not True
    ):
        raise RuntimeError("latest checksum-bearing full backup media set is not completely proven")
    backup_gate = sql.get("backup_gate")
    if not isinstance(backup_gate, dict) or backup_gate.get("status") != "PASS":
        raise RuntimeError("administrator backup gate is not independently proven")
    volumes = sql.get("volume_capacity")
    if not isinstance(volumes, list) or not any(int(row.get("available_bytes", 0) or 0) > 0 for row in volumes if isinstance(row, dict)):
        raise RuntimeError("SQL database-host volume capacity is not proven")
    database_files = sql.get("database_files")
    database_files_evidence = sql.get("database_files_evidence")
    if (
        not isinstance(database_files_evidence, dict)
        or database_files_evidence.get("status") != "PASS"
        or not isinstance(database_files, list)
        or not database_files
    ):
        raise RuntimeError("SQL database-file capacity is not proven")
    capacity_gate = sql.get("capacity_gate")
    if not isinstance(capacity_gate, dict) or capacity_gate.get("status") != "PASS":
        raise RuntimeError("administrator SQL capacity gate is not independently proven")
    log_space_gate = sql.get("log_space_gate")
    if not isinstance(log_space_gate, dict) or log_space_gate.get("status") != "PASS":
        raise RuntimeError("administrator SQL log-space capacity gate is not independently proven")
    cutover_history_evidence = sql.get("production_cutover_history_evidence")
    cutover_history = sql.get("production_cutover_history")
    if (
        not isinstance(cutover_history_evidence, dict)
        or cutover_history_evidence.get("status") != "PASS"
        or not isinstance(cutover_history, list)
        or not any(
            isinstance(row, dict) and row.get("tiered50_activation_status") == "ACTIVATED"
            for row in cutover_history
        )
        or not any(
            isinstance(row, dict) and row.get("tiered50_activation_status") == "OUTAGE"
            for row in cutover_history
        )
    ):
        raise RuntimeError("original Tiered50 activation and historical outage evidence is not proven")
    competing_writer_evidence = sql.get("competing_production_writers_evidence")
    competing_writers = sql.get("competing_production_writers")
    if (
        not isinstance(competing_writer_evidence, dict)
        or competing_writer_evidence.get("status") != "PASS"
        or not isinstance(competing_writers, list)
        or any(isinstance(row, dict) and int(row.get("running_count", 0) or 0) > 0 for row in competing_writers)
    ):
        raise RuntimeError("absence of competing production writers is not proven")
    quota_ledger_evidence = sql.get("current_quota_ledger_evidence")
    if not isinstance(quota_ledger_evidence, dict) or quota_ledger_evidence.get("status") != "PASS":
        raise RuntimeError("current SQL quota ledger evidence is not proven by the administrator path")
    quota_ledger = sql.get("current_quota_ledger")
    quota_integrity = sql.get("quota_integrity") or sql.get("quota_usage")
    quota_mode = (
        str(quota_integrity.get("authorization_mode") or quota_integrity.get("source_kind") or "")
        if isinstance(quota_integrity, Mapping)
        else ""
    )
    complete_ledger_proven = (
        isinstance(quota_ledger, list)
        and len(quota_ledger) == 1
        and isinstance(quota_ledger[0], dict)
        and str(quota_ledger[0].get("accounting_status") or "") == "COMPLETE"
    )
    bounded_bootstrap_proven = (
        not quota_ledger
        and isinstance(quota_integrity, Mapping)
        and quota_integrity.get("status") == "PASS"
        and quota_integrity.get("state_available") is True
        and quota_mode in {BOOTSTRAP_AUTHORIZATION_MODE, "BOUNDED_BOOTSTRAP"}
    )
    if not complete_ledger_proven and not bounded_bootstrap_proven:
        raise RuntimeError("current SQL quota ledger is missing, ambiguous, or not COMPLETE")
    vmware = host.get("vmware")
    vmware_returncode = vmware.get("returncode") if isinstance(vmware, dict) else None
    if not isinstance(vmware, dict) or vmware_returncode is None or int(vmware_returncode) != 0:
        raise RuntimeError("running Windows VM evidence is not proven")
    windows_disk = sql.get("windows_vm_disk")
    if (
        not isinstance(windows_disk, dict)
        or windows_disk.get("status") != "PASS"
        or not isinstance(windows_disk.get("volumes"), list)
        or not any(
            isinstance(volume, dict) and int(volume.get("free_bytes", 0) or 0) > 0
            for volume in windows_disk["volumes"]
        )
    ):
        raise RuntimeError("Windows VM disk capacity is not proven")
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
        "artifact": str(current_path),
        "generated_at_utc": generated_at.isoformat().replace("+00:00", "Z"),
        "authenticated_artifact": authenticated,
        "backup_verifyonly": "PASS",
        "sql_volume_capacity": "PASS",
        "running_windows_vm": "PASS",
        "windows_vm_disk_capacity": "PASS",
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


def _read_current_competing_writers(connection: Any) -> list[dict[str, Any]]:
    """Read the writer fence in a short transaction under the session lock."""

    cursor = connection.cursor()
    try:
        cursor.execute("SET XACT_ABORT ON; BEGIN TRANSACTION;")
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
        rows = list(cursor.fetchall())
        cursor.execute("COMMIT TRANSACTION;")
        connection.commit()
    except BaseException:
        try:
            connection.rollback()
        except BaseException:
            pass
        raise
    return validate_no_competing_production_writers(rows)


def _refresh_resume_readiness(
    *,
    connection: Any,
    runtime_config_path: str,
    expected_runtime_sql_identity: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Repeat all time-sensitive gates immediately before LaunchAgent enablement."""

    _assert_resume_operation_lock(connection)
    first_authoritative_utc = _authoritative_sql_utc(connection)
    refreshed_runtime = _validate_limited_runtime(now=first_authoritative_utc)
    refreshed_quota = dict(refreshed_runtime.get("quota") or {})
    _validate_resume_quota_authorization(
        refreshed_quota,
        authoritative_utc=first_authoritative_utc,
    )
    refreshed_identity = refreshed_runtime.get("sql_instance_identity")
    if (
        isinstance(expected_runtime_sql_identity, Mapping)
        and expected_runtime_sql_identity.get("server_name")
        and isinstance(refreshed_identity, Mapping)
        and not sql_instance_identities_match(refreshed_identity, expected_runtime_sql_identity)
    ):
        raise RuntimeError("installed runtime SQL instance identity changed during resume preflight")

    # Validate the signed artifact once at the beginning of the refresh and
    # again using the SQL time immediately before the transition.  This catches
    # evidence expiry while a slow SQL/launchd preparation is in progress.
    refreshed_evidence = _validate_operational_evidence(
        now=first_authoritative_utc,
        require_historical_heartbeat=False,
        expected_runtime_sql_identity=refreshed_identity
        if isinstance(refreshed_identity, Mapping)
        else expected_runtime_sql_identity,
    )
    refreshed_writers = _read_current_competing_writers(connection)
    _assert_resume_operation_lock(connection)

    transition_authoritative_utc = _authoritative_sql_utc(connection)
    transition_runtime = _validate_limited_runtime(now=transition_authoritative_utc)
    transition_quota = dict(transition_runtime.get("quota") or {})
    transition_identity = transition_runtime.get("sql_instance_identity")
    if (
        isinstance(expected_runtime_sql_identity, Mapping)
        and expected_runtime_sql_identity.get("server_name")
        and isinstance(transition_identity, Mapping)
        and not sql_instance_identities_match(transition_identity, expected_runtime_sql_identity)
    ):
        raise RuntimeError("installed runtime SQL instance identity changed during resume transition")
    _validate_resume_quota_authorization(
        transition_quota,
        authoritative_utc=transition_authoritative_utc,
    )
    refreshed_runtime = transition_runtime
    refreshed_quota = transition_quota
    refreshed_identity = transition_identity or refreshed_identity
    _validate_resume_quota_authorization(
        refreshed_quota,
        authoritative_utc=transition_authoritative_utc,
    )
    refreshed_evidence = _validate_operational_evidence(
        now=transition_authoritative_utc,
        require_historical_heartbeat=False,
        expected_runtime_sql_identity=refreshed_identity
        if isinstance(refreshed_identity, Mapping)
        else expected_runtime_sql_identity,
    )
    refreshed_writers = _read_current_competing_writers(connection)
    _assert_resume_operation_lock(connection)

    # This is the last launchd observation before the SQL-clock boundary below.
    # If another actor loaded the service during preflight, do not unload it or
    # claim that this invocation performed the transition.
    refreshed_launch_agent = _installed_tiered50_resume_readiness(
        runtime_config_path=runtime_config_path
    )
    if refreshed_launch_agent.get("loaded_before_resume"):
        raise RuntimeError("Tiered50 LaunchAgent became loaded during resume preflight")

    actual_transition_utc = _authoritative_sql_utc(connection)
    _validate_resume_quota_authorization(
        refreshed_quota,
        authoritative_utc=actual_transition_utc,
    )
    refreshed_evidence = _validate_operational_evidence(
        now=actual_transition_utc,
        require_historical_heartbeat=False,
        expected_runtime_sql_identity=refreshed_identity
        if isinstance(refreshed_identity, Mapping)
        else expected_runtime_sql_identity,
    )
    _assert_resume_operation_lock(connection)
    return {
        "authoritative_sql_utc": actual_transition_utc,
        "limited_runtime": refreshed_runtime,
        "quota": refreshed_quota,
        "operational_evidence": refreshed_evidence,
        "competing_writers": refreshed_writers,
        "launch_agent": refreshed_launch_agent,
        "resume_lock": {
            "resource": RESUME_APP_LOCK_RESOURCE,
            "lock_owner": "SESSION",
            "mode": "Exclusive",
        },
    }


def _launch_agent_loaded() -> bool:
    result = subprocess.run(
        ["/bin/launchctl", "print", f"gui/{os.getuid()}/{TIERED50_LABEL}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.returncode == 0


_MISSING_QUOTA_VALUE = object()


def _strict_quota_integer(quota: Mapping[str, Any], field: str) -> int:
    """Parse one SQL quota integer without collapsing NULL or zero."""

    value = quota.get(field, _MISSING_QUOTA_VALUE)
    if value is _MISSING_QUOTA_VALUE:
        raise RuntimeError(f"current SQL quota ledger is missing {field}")
    if value is None:
        raise RuntimeError(f"current SQL quota ledger has NULL {field}")
    if isinstance(value, bool):
        raise RuntimeError(f"current SQL quota ledger has invalid {field}")
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"current SQL quota ledger has invalid {field}") from exc
    if isinstance(value, float) and value != parsed:
        raise RuntimeError(f"current SQL quota ledger has invalid {field}")
    if parsed < 0:
        raise RuntimeError(f"current SQL quota ledger has negative {field}")
    return parsed


def _validate_resume_quota_authorization(
    quota: Mapping[str, Any],
    *,
    authoritative_utc: datetime,
) -> dict[str, Any]:
    """Validate the current SQL quota state without truthiness fallbacks."""

    now = _utc_datetime(authoritative_utc)
    if now is None:
        raise RuntimeError("authoritative SQL UTC time is unavailable; resume denied")
    bootstrap_authorization = str(quota.get("authorization_mode") or "") == BOOTSTRAP_AUTHORIZATION_MODE
    october_bootstrap_row = str(quota.get("month_key") or "") == "2026-10" and (
        quota.get("provider_period_metadata_verified") is not True
    )
    if now >= BOOTSTRAP_LATEST_STOP_UTC and (bootstrap_authorization or october_bootstrap_row):
        raise RuntimeError("the approved October bootstrap hard stop has been reached")
    if quota.get("state_available") is not True:
        raise RuntimeError("current SQL quota ledger is not available for resume")
    reserved = _strict_quota_integer(quota, "reserved_shared_usage")
    if reserved != 0:
        raise RuntimeError("pending quota permits must be zero before resume")
    accounted = _strict_quota_integer(quota, "known_total")
    if accounted >= AUTOMATIC_RETRY_PROTECTION_BOUNDARY:
        raise RuntimeError("current SQL quota ledger does not leave approved retry headroom")

    # ``current_month_usage`` validates the bootstrap or provider period before
    # returning, but these checks protect the transition if the SQL row becomes
    # stale while the administrative preflight is still running.
    stale_after = _utc_datetime(quota.get("stale_after_utc"))
    if stale_after is not None and now >= stale_after:
        raise RuntimeError("current SQL quota authorization expired before resume transition")
    period_start = _utc_datetime(quota.get("provider_period_start_utc"))
    period_end = _utc_datetime(quota.get("provider_period_end_utc"))
    if (period_start is None) != (period_end is None):
        raise RuntimeError("current SQL provider period bounds are incomplete")
    if period_start is not None and period_end is not None and not period_start <= now < period_end:
        raise RuntimeError("current SQL provider period no longer covers the resume transition")
    return {
        "state_available": True,
        "reserved_shared_usage": reserved,
        "known_total": accounted,
        "stale_after_utc": stale_after,
        "provider_period_start_utc": period_start,
        "provider_period_end_utc": period_end,
    }


def _launchctl_state_after_rollback() -> dict[str, Any]:
    """Read actual launchd state, distinguishing unloaded from unreadable."""

    qualified = f"gui/{os.getuid()}/{TIERED50_LABEL}"
    domain = f"gui/{os.getuid()}"
    try:
        loaded_result = subprocess.run(
            ["/bin/launchctl", "print", qualified],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "status": "UNREADABLE",
            "loaded": None,
            "disabled": None,
            "error_type": type(exc).__name__,
            "reason": "launchctl state could not be read",
        }

    output = f"{loaded_result.stdout}\n{loaded_result.stderr}".casefold()
    if loaded_result.returncode == 0:
        loaded: bool | None = True
    elif any(marker in output for marker in ("could not find service", "no such process", "not found", "unknown service")):
        loaded = False
    else:
        loaded = None

    try:
        disabled_result = subprocess.run(
            ["/bin/launchctl", "print-disabled", domain],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "status": "UNREADABLE",
            "loaded": loaded,
            "disabled": None,
            "error_type": type(exc).__name__,
            "reason": "launchctl disabled-state could not be read",
        }
    if disabled_result.returncode != 0:
        return {
            "status": "UNREADABLE",
            "loaded": loaded,
            "disabled": None,
            "launchctl_print_disabled_returncode": disabled_result.returncode,
            "reason": "launchctl disabled-state command failed",
        }
    disabled_output = f"{disabled_result.stdout}\n{disabled_result.stderr}"
    match = re.search(
        rf"[\"']?{re.escape(TIERED50_LABEL)}[\"']?\s*=>\s*(true|false)",
        disabled_output,
        flags=re.IGNORECASE,
    )
    disabled = bool(match and match.group(1).casefold() == "true")
    return {
        "status": "READABLE" if loaded is not None else "UNREADABLE",
        "loaded": loaded,
        "disabled": disabled,
        "launchctl_print_returncode": loaded_result.returncode,
        "launchctl_print_disabled_returncode": disabled_result.returncode,
    }


def _installed_tiered50_disabled_state() -> dict[str, Any]:
    try:
        with TIERED50_INSTALLED_PATH.open("rb") as handle:
            plist = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException) as exc:
        return {
            "status": "UNREADABLE",
            "exists": TIERED50_INSTALLED_PATH.exists(),
            "disabled": None,
            "error_type": type(exc).__name__,
        }
    return {
        "status": "PASS",
        "exists": True,
        "disabled": bool(plist.get("Disabled")),
        "path": str(TIERED50_INSTALLED_PATH),
    }


def _disable_tiered50_after_resume_failure(*, runtime_config_path: str) -> dict[str, Any]:
    """Stop a partial resume and verify the resulting state before reporting it."""

    rollback_failures: list[dict[str, str]] = []
    actions: list[str] = []
    try:
        actions.append("install_disabled_plist")
        install_launch_agent(
            activate=False,
            runtime_config_path=runtime_config_path,
            mode=TIERED50_MODE,
        )
    except BaseException as exc:
        rollback_failures.append(
            {
                "action": "install_disabled_plist",
                "error_type": type(exc).__name__,
                "reason": str(exc)[:500],
            }
        )
    try:
        actions.append("bootout_and_disable_launchagent")
        bootstrap_launch_agent(activate=False, mode=TIERED50_MODE)
    except BaseException as exc:
        rollback_failures.append(
            {
                "action": "bootout_and_disable_launchagent",
                "error_type": type(exc).__name__,
                "reason": str(exc)[:500],
            }
        )

    launchctl_state = _launchctl_state_after_rollback()
    plist_state = _installed_tiered50_disabled_state()
    if launchctl_state.get("loaded") is True:
        status = "STILL_ACTIVE"
    elif (
        launchctl_state.get("status") == "READABLE"
        and launchctl_state.get("loaded") is False
        and launchctl_state.get("disabled") is True
        and plist_state.get("status") == "PASS"
        and plist_state.get("disabled") is True
    ):
        status = "STOPPED_VERIFIED"
    else:
        status = "STOP_NOT_VERIFIED"
    return {
        "status": status,
        "critical_operational_blocker": status != "STOPPED_VERIFIED",
        "actions_attempted": actions,
        "rollback_failures": rollback_failures,
        "launchctl_state": launchctl_state,
        "installed_plist_state": plist_state,
    }


class Tiered50ResumeFailure(RuntimeError):
    """A resume failure that retains the original and rollback outcomes."""

    def __init__(self, message: str, *, original_failure: BaseException, rollback: Mapping[str, Any]) -> None:
        self.original_failure_type = type(original_failure).__name__
        self.original_failure_reason = str(original_failure)[:500]
        self.rollback = dict(rollback)
        super().__init__(message)


def _resume_failure(
    message: str,
    *,
    original_failure: BaseException,
    rollback: Mapping[str, Any],
) -> Tiered50ResumeFailure:
    rollback_status = str(rollback.get("status") or "STOP_NOT_VERIFIED")
    if rollback_status == "STOPPED_VERIFIED":
        detail = f"{message}; rollback_status=STOPPED_VERIFIED"
    else:
        detail = (
            f"{message}; CRITICAL_OPERATIONAL_BLOCKER=RESUME_ROLLBACK_{rollback_status}; "
            "Tiered50 stop is not verified"
        )
    return Tiered50ResumeFailure(detail, original_failure=original_failure, rollback=rollback)


def _acquire_resume_operation_lock(connection: Any) -> None:
    """Acquire a SQL-session application lock for the whole resume transition.

    The lock owner is the SQL session rather than an ordinary row
    transaction.  Validation rows can therefore be committed before slow
    launchd work while the exclusive resume transition remains serialized.
    SQL Server releases a session-owned application lock when this connection
    closes, including an abnormal process termination.
    """

    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SET XACT_ABORT ON;
            DECLARE @lock_result INT;
            EXEC @lock_result = sys.sp_getapplock
                @Resource = %s,
                @LockMode = N'Exclusive',
                @LockOwner = N'Session',
                @LockTimeout = 0;
            IF @lock_result < 0
                THROW 51001, 'Tiered50 resume operation is already in progress', 1;
            """,
            (RESUME_APP_LOCK_RESOURCE,),
        )
        connection.commit()
    except BaseException as exc:
        try:
            connection.rollback()
        except Exception:
            pass
        if "already in progress" in str(exc).casefold() or "51001" in str(exc):
            raise RuntimeError("TIERED50_RESUME_ALREADY_IN_PROGRESS") from exc
        raise RuntimeError("TIERED50_RESUME_COORDINATION_UNAVAILABLE") from exc


def _release_resume_operation_lock(connection: Any) -> None:
    """Release the session application lock, preserving close-time cleanup."""

    cursor = connection.cursor()
    cursor.execute(
        "EXEC sys.sp_releaseapplock @Resource = %s, @LockOwner = N'Session';",
        (RESUME_APP_LOCK_RESOURCE,),
    )
    connection.commit()


def _assert_resume_operation_lock(connection: Any) -> dict[str, str]:
    """Confirm that the same SQL session still owns the exclusive lock."""

    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT APPLOCK_MODE(N'public', %s, N'Session') AS ResumeLockMode
            """,
            (RESUME_APP_LOCK_RESOURCE,),
        )
        row = cursor.fetchone()
    except BaseException as exc:
        raise RuntimeError("TIERED50_RESUME_COORDINATION_UNAVAILABLE") from exc
    mode = str(_row_value(row, "ResumeLockMode", 0) or "").strip().casefold()
    if mode != "exclusive":
        raise RuntimeError("TIERED50_RESUME_LOCK_NOT_HELD")
    return {"resource": RESUME_APP_LOCK_RESOURCE, "lock_owner": "SESSION", "mode": "Exclusive"}


def _record_resume_event(
    *,
    settings: MssqlConnectionSettings,
    resume_key: str,
    lifecycle: Mapping[str, Any],
    evidence: Mapping[str, Any],
    connection: Any | None = None,
) -> dict[str, Any]:
    """Record one actual resume after launchd has successfully started."""

    owns_connection = connection is None
    if owns_connection:
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
        if owns_connection:
            connection.close()
    return event


def resume_tiered50_production(*, resume_key: str | None = None) -> dict[str, Any]:
    """Safely resume the existing Tiered50 installation without recutting over."""

    key = str(resume_key or DEFAULT_RESUME_KEY).strip()
    if not key or len(key) > 240 or any(marker in key.casefold() for marker in ("password", "secret", "token")):
        raise RuntimeError("resume key is empty, too long, or contains a prohibited marker")

    settings = _administrator_connection_settings()
    connection = connect_mssql(settings)
    lock_acquired = False
    try:
        _acquire_resume_operation_lock(connection)
        lock_acquired = True
        now = _authoritative_sql_utc(connection)
        limited_runtime = _validate_limited_runtime(now=now)
        quota = dict(limited_runtime.get("quota") or {})
        _validate_resume_quota_authorization(quota, authoritative_utc=now)
        launch_agent = _installed_tiered50_resume_readiness(
            runtime_config_path=str(limited_runtime["config_path"])
        )
        operational_evidence = _validate_operational_evidence(
            now=now,
            require_historical_heartbeat=False,
            expected_runtime_sql_identity=limited_runtime.get("sql_instance_identity")
            or (limited_runtime.get("login") if isinstance(limited_runtime.get("login"), Mapping) else None),
        )

        manifest = load_default_manifest(ROOT)
        lifecycle: dict[str, Any]
        existing_resume: Any = None
        latest_resume: Any = None
        competing_writers: list[dict[str, Any]] = []
        cursor = connection.cursor()
        try:
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
            if existing_resume is None:
                cursor.execute(
                    f"""
                    SELECT TOP (1) ResumeEventId, ResumeKey, PanelVersion, EraCode,
                           SamplingEraId, OriginalActivationAtUtc, ResumeAtUtc,
                           LaunchAgentLabel
                    FROM {RESUME_EVENT_TABLE} WITH (UPDLOCK, HOLDLOCK)
                    WHERE ResumeKey = %s
                    """,
                    (key,),
                )
                existing_resume = cursor.fetchone()
            cursor.execute("COMMIT TRANSACTION;")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

        evidence: dict[str, Any] = {
            "operation": "RESUME",
            "resume_key": key,
            "requested_at_utc": now.isoformat().replace("+00:00", "Z"),
            "resume_coordination": {
                "resource": RESUME_APP_LOCK_RESOURCE,
                "lock_owner": "SESSION",
                "ordinary_row_locks_released_before_launchd": True,
            },
            "lifecycle": lifecycle,
            "competing_writers": competing_writers,
            "limited_runtime": {
                "settings": limited_runtime.get("settings"),
                "login": limited_runtime.get("login"),
                "sql_instance_identity": limited_runtime.get("sql_instance_identity"),
                "quota": quota,
            },
            "launch_agent_readiness": launch_agent,
            "operational_evidence": operational_evidence,
            "historical_requests_replayed": 0,
            "legacy7_mutated": False,
            "target_rows_mutated": 0,
            "sampling_eras_created": 0,
        }

        def same_lifecycle(row: Any) -> bool:
            return (
                str(_named_value(row, "PanelVersion", 2) or "") == manifest.panel_version
                and str(_named_value(row, "EraCode", 3) or "") == ERA_CODE
                and int(_named_value(row, "SamplingEraId", 4) or 0) == int(lifecycle["sampling_era_id"])
                and _utc_datetime(_named_value(row, "OriginalActivationAtUtc", 5))
                == lifecycle["effective_at_utc"]
                and str(_named_value(row, "LaunchAgentLabel", 7) or "") == TIERED50_LABEL
            )

        if latest_resume is not None and launch_agent.get("loaded_before_resume"):
            if not same_lifecycle(latest_resume):
                raise RuntimeError("existing Tiered50 resume event conflicts with immutable activation state")
            return {
                "status": "IDEMPOTENT_ALREADY_ACTIVE",
                "result_code": RESUME_LOADED_EXISTING_EVENT_STATUS,
                "operation": "RESUME",
                "resume_key": str(_named_value(latest_resume, "ResumeKey", 1) or key),
                "requested_resume_key": key,
                "effective_at_utc": lifecycle["effective_at_utc"].isoformat().replace("+00:00", "Z"),
                "sampling_era_id": lifecycle["sampling_era_id"],
                "launch_agent": launch_agent,
                "service_state": "LOADED_NOT_VALIDATED",
                "production_validation": "NOT_PROVEN",
                "operational_success": True,
                "historical_state_preserved": True,
                "mdd_requests_issued": 0,
                "writes_performed": 0,
            }
        if existing_resume is not None and launch_agent.get("loaded_before_resume") is not True:
            if not same_lifecycle(existing_resume):
                raise RuntimeError("resume key already exists for a conflicting Tiered50 lifecycle")
            return {
                "status": "IDEMPOTENT_ALREADY_RECORDED",
                "result_code": RESUME_STOPPED_EXISTING_EVENT_STATUS,
                "operation": "RESUME",
                "resume_key": str(_named_value(existing_resume, "ResumeKey", 1) or key),
                "requested_resume_key": key,
                "effective_at_utc": lifecycle["effective_at_utc"].isoformat().replace("+00:00", "Z"),
                "sampling_era_id": lifecycle["sampling_era_id"],
                "launch_agent": {**launch_agent, "status": "STOPPED"},
                "service_state": "STOPPED",
                "production_validation": "NOT_PROVEN",
                "operational_success": False,
                "required_action": (
                    "Review the preserved outage, renew all readiness gates, and supply a new explicit "
                    "resume transition key before attempting production resume again."
                ),
                "historical_state_preserved": True,
                "mdd_requests_issued": 0,
                "writes_performed": 0,
                "resume_transition": "RECORDED_BUT_NOT_ACTIVE",
            }

        # The first preflight above protects the immutable history read.  A
        # slow SQL/evidence/launchd preparation must not authorize enablement
        # using that stale snapshot, so repeat the time-sensitive gates while
        # the same session-owned resume lock is still held.
        refreshed = _refresh_resume_readiness(
            connection=connection,
            runtime_config_path=str(limited_runtime["config_path"]),
            expected_runtime_sql_identity=(
                limited_runtime.get("sql_instance_identity")
                if isinstance(limited_runtime.get("sql_instance_identity"), Mapping)
                else None
            ),
        )
        now = refreshed["authoritative_sql_utc"]
        limited_runtime = refreshed["limited_runtime"]
        quota = refreshed["quota"]
        operational_evidence = refreshed["operational_evidence"]
        competing_writers = refreshed["competing_writers"]
        launch_agent = refreshed["launch_agent"]
        evidence["requested_at_utc"] = now.isoformat().replace("+00:00", "Z")
        evidence["limited_runtime"] = {
            "settings": limited_runtime.get("settings"),
            "login": limited_runtime.get("login"),
            "sql_instance_identity": limited_runtime.get("sql_instance_identity"),
            "quota": quota,
        }
        evidence["operational_evidence"] = operational_evidence
        evidence["competing_writers"] = competing_writers
        evidence["resume_coordination"]["final_authoritative_sql_utc"] = now

        installed: dict[str, Any] = {
            "label": TIERED50_LABEL,
            "installed_plist_path": str(TIERED50_INSTALLED_PATH),
            "runtime_bundle_path": launch_agent.get("runtime_bundle_path"),
        }
        try:
            if not launch_agent.get("loaded_before_resume"):
                # Re-render only the existing Tiered50 artifact with the
                # current source fingerprint.  This does not materialize
                # targets or touch SQL history.
                installed = install_launch_agent(
                    activate=True,
                    runtime_config_path=str(limited_runtime["config_path"]),
                    mode=TIERED50_MODE,
                )
                bootstrap_launch_agent(activate=True, mode=TIERED50_MODE)
                if not _launch_agent_loaded():
                    raise RuntimeError("Tiered50 LaunchAgent did not remain loaded after bootstrap")
            else:
                installed["status"] = "ALREADY_LOADED_AFTER_INCOMPLETE_PRIOR_TRANSITION"
        except BaseException as exc:
            rollback = _disable_tiered50_after_resume_failure(
                runtime_config_path=str(limited_runtime["config_path"])
            )
            raise _resume_failure(
                "Tiered50 resume LaunchAgent startup failed",
                original_failure=exc,
                rollback=rollback,
            ) from exc

        try:
            recorded = _record_resume_event(
                settings=settings,
                resume_key=key,
                lifecycle=lifecycle,
                evidence=evidence,
                connection=connection,
            )
        except BaseException as exc:
            rollback = _disable_tiered50_after_resume_failure(
                runtime_config_path=str(limited_runtime["config_path"])
            )
            raise _resume_failure(
                "Tiered50 resume event could not be recorded",
                original_failure=exc,
                rollback=rollback,
            ) from exc

        resume_at = recorded.get("resume_at_utc")
        return {
            "status": "PASS" if recorded["status"] == "RECORDED" else recorded["status"],
            "result_code": "RESUMED_AND_EVENT_RECORDED" if recorded["status"] == "RECORDED" else recorded["status"],
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
            "service_state": "LOADED",
            "production_validation": "NOT_PROVEN",
            "operational_success": recorded["status"] == "RECORDED",
            "resume_at_utc": resume_at,
            "mdd_requests_issued": 0,
            "writes_performed": 1 if recorded["status"] == "RECORDED" else 0,
        }
    finally:
        if lock_acquired:
            try:
                _release_resume_operation_lock(connection)
            except BaseException:
                # Closing the SQL session still releases the session-owned
                # application lock.  A release diagnostic must not hide the
                # original resume outcome.
                pass
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
    settings = _administrator_connection_settings()
    limited_runtime = _validate_limited_runtime(now=now)
    launch_agent = _validate_launch_agent_template(runtime_config_path=str(limited_runtime["config_path"]))
    operational_evidence = _validate_operational_evidence(
        now=now,
        expected_runtime_sql_identity=limited_runtime.get("sql_instance_identity"),
    )
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
                registered_contract=MssqlPersistence._active_reset_contract_for_baseline(
                    cursor,
                    baseline=baseline_mapping,
                    lock=True,
                ),
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


def _resume_exit_code(result: Mapping[str, Any]) -> int:
    """Keep idempotent event detection distinct from operational success."""

    if result.get("status") == "PASS" and result.get("operational_success") is not False:
        return 0
    if (
        result.get("status") == "IDEMPOTENT_ALREADY_ACTIVE"
        and result.get("service_state") == "LOADED_NOT_VALIDATED"
    ):
        return 0
    return 2


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
        return _resume_exit_code(result) if args.resume else 0
    except Exception as exc:
        result: dict[str, Any] = {
            "status": "BLOCKED",
            "error_type": type(exc).__name__,
            "reason": str(exc)[:500],
        }
        rollback = getattr(exc, "rollback", None)
        if isinstance(rollback, dict):
            result["rollback"] = rollback
            result["original_failure"] = {
                "error_type": getattr(exc, "original_failure_type", None),
                "reason": getattr(exc, "original_failure_reason", None),
            }
        print(json.dumps(result, sort_keys=True, default=str))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
