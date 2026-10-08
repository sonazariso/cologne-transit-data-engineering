#!/usr/bin/env python3
"""Collect fresh, read-only Tiered50 cutover evidence.

The script uses the administrator SQL connection only for bounded catalog,
capacity, backup, and historical-run reads. It never calls the MDD endpoint,
changes launchd state, activates targets, or writes SQL rows.
"""

from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlConnectionSettings,
    MssqlPersistence,
    connect_mssql,
)
from cologne_transit_collector.domain import APPROVED_SUPER_CYCLE_COUNTS  # noqa: E402
from cologne_transit_collector.runtime_configuration import EXPECTED_SQL_DATABASE  # noqa: E402


LEGACY_LABEL = "com.cologne-transit-intelligence.mdd-collector"
TIERED_LABEL = "com.cologne-transit-intelligence.mdd-tiered50-collector"
TARGET_TABLE = "ctl.MddMacTiered50ProductionTarget"


def _row_value(row: Any, name: str, index: int = 0) -> Any:
    if isinstance(row, dict):
        return row.get(name)
    return row[index] if row is not None else None


def _command(command: list[str], *, limit: int = 5000) -> dict[str, Any]:
    try:
        completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    except OSError as exc:
        return {"command": command[0], "status": "NOT_OBSERVABLE", "error_type": type(exc).__name__}
    return {
        "command": command[0],
        "returncode": completed.returncode,
        "stdout": completed.stdout[-limit:],
        "stderr": completed.stderr[-1000:],
    }


def _launchd(label: str) -> dict[str, Any]:
    return _command(["/bin/launchctl", "print", f"gui/{os.getuid()}/{label}"])


def _plist_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    try:
        with path.open("rb") as handle:
            plist = plistlib.load(handle)
        calendar = plist.get("StartCalendarInterval")
        calendar_minutes = None
        if isinstance(calendar, list):
            calendar_minutes = [
                int(item["Minute"])
                for item in calendar
                if isinstance(item, dict) and "Minute" in item
            ]
        return {
            "exists": True,
            "disabled": bool(plist.get("Disabled")),
            "run_at_load": bool(plist.get("RunAtLoad")),
            "start_interval_seconds": plist.get("StartInterval"),
            "start_calendar_interval_minutes": calendar_minutes,
            "schedule_type": (
                "START_CALENDAR_INTERVAL_LOCAL_WALL_CLOCK"
                if calendar_minutes is not None
                else "RELATIVE_INTERVAL"
            ),
            "program_arguments": [str(value) for value in plist.get("ProgramArguments", [])],
            "contains_secret_marker": any(
                marker in json.dumps(plist, ensure_ascii=False)
                for marker in ("MDD_API_KEY", "MSSQL_PASSWORD", "SQL_SERVER_PASSWORD")
            ),
        }
    except Exception as exc:
        return {"exists": True, "status": "UNREADABLE", "error_type": type(exc).__name__}


def _safe_backup_verify(connection: Any, path: str) -> dict[str, Any]:
    """Run VERIFYONLY on a path read from msdb, with no path in output."""

    try:
        autocommit = getattr(connection, "autocommit", None)
        if callable(autocommit):
            autocommit(True)
        cursor = connection.cursor()
        escaped = str(path).replace("'", "''")
        cursor.execute(f"RESTORE VERIFYONLY FROM DISK = N'{escaped}' WITH CHECKSUM")
        try:
            cursor.fetchall()
        except Exception:
            pass
        return {"status": "PASS"}
    except Exception as exc:
        return {"status": "NOT_PROVEN", "error_type": type(exc).__name__}


def collect() -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc)
    user_agent_dir = Path.home() / "Library" / "LaunchAgents"
    legacy_plist = user_agent_dir / f"{LEGACY_LABEL}.plist"
    tiered_plist = user_agent_dir / f"{TIERED_LABEL}.plist"
    evidence: dict[str, Any] = {
        "artifact_version": "TIERED50_PRODUCTION_EVIDENCE_V1",
        "generated_at_utc": generated_at.isoformat().replace("+00:00", "Z"),
        "mdd_requests_issued": 0,
        "writes_performed": 0,
        "host": {
            "uptime": _command(["/usr/bin/uptime"]),
            "power_assertions": _command(["/usr/bin/pmset", "-g", "assertions"]),
            "legacy7_launchd": _launchd(LEGACY_LABEL),
            "tiered50_launchd": _launchd(TIERED_LABEL),
            "legacy7_plist": _plist_state(legacy_plist),
            "tiered50_plist": _plist_state(tiered_plist),
            "mac_disk": {
                "total_bytes": shutil.disk_usage(ROOT).total,
                "free_bytes": shutil.disk_usage(ROOT).free,
            },
        },
    }
    vmrun = shutil.which("vmrun") or "/Applications/VMware Fusion.app/Contents/Public/vmrun"
    evidence["host"]["vmware"] = (
        _command([vmrun, "list"]) if Path(vmrun).exists() else {"status": "NOT_OBSERVABLE", "reason": "vmrun unavailable"}
    )

    settings = MssqlConnectionSettings.from_environment()
    if settings.database != EXPECTED_SQL_DATABASE:
        raise RuntimeError(f"SQL Server target database must be {EXPECTED_SQL_DATABASE}")
    persistence = MssqlPersistence(settings=settings)
    evidence["sql"] = {
        "connection": settings.redacted(),
        "runtime_login": persistence.runtime_login_status(),
        "runtime_schema": persistence.validate_runtime_schema(),
        "tiered50_schema": persistence.validate_tiered50_runtime_schema(),
        "quota_usage": persistence.current_month_usage(at_utc=generated_at),
    }

    connection = connect_mssql(settings)
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT @@SERVERNAME AS ServerName, DB_NAME() AS DatabaseName, SUSER_SNAME() AS LoginName, SYSUTCDATETIME() AS CapturedAtUtc")
        identity = cursor.fetchone()
        evidence["sql"]["identity"] = {
            "server_name": str(_row_value(identity, "ServerName", 0) or ""),
            "database_name": str(_row_value(identity, "DatabaseName", 1) or ""),
            "login_name": str(_row_value(identity, "LoginName", 2) or ""),
            "captured_at_utc": _row_value(identity, "CapturedAtUtc", 3),
        }
        cursor.execute(
            """
            SELECT name, type_desc, size, growth, is_percent_growth, max_size,
                   FILEPROPERTY(name, 'SpaceUsed') AS space_used_pages
            FROM sys.database_files
            ORDER BY file_id
            """
        )
        evidence["sql"]["database_files"] = [
            {
                "name": str(_row_value(row, "name", 0)),
                "type": str(_row_value(row, "type_desc", 1)),
                "size_pages": int(_row_value(row, "size", 2)),
                "growth": int(_row_value(row, "growth", 3)),
                "is_percent_growth": bool(_row_value(row, "is_percent_growth", 4)),
                "max_size_pages": int(_row_value(row, "max_size", 5)),
                "space_used_pages": int(_row_value(row, "space_used_pages", 6) or 0),
            }
            for row in cursor.fetchall()
        ]
        cursor.execute(
            """
            SELECT DISTINCT vs.volume_mount_point, vs.total_bytes, vs.available_bytes
            FROM sys.database_files AS df
            CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
            ORDER BY vs.volume_mount_point
            """
        )
        evidence["sql"]["volume_capacity"] = [
            {
                "volume": str(_row_value(row, "volume_mount_point", 0) or ""),
                "total_bytes": int(_row_value(row, "total_bytes", 1) or 0),
                "available_bytes": int(_row_value(row, "available_bytes", 2) or 0),
            }
            for row in cursor.fetchall()
        ]
        cursor.execute(
            """
            SELECT total_log_size_in_bytes, used_log_space_in_bytes, used_log_space_in_percent
            FROM sys.dm_db_log_space_usage
            WHERE database_id = DB_ID()
            """
        )
        log_row = cursor.fetchone()
        evidence["sql"]["log_space"] = {
            "total_bytes": int(_row_value(log_row, "total_log_size_in_bytes", 0) or 0),
            "used_bytes": int(_row_value(log_row, "used_log_space_in_bytes", 1) or 0),
            "used_percent": float(_row_value(log_row, "used_log_space_in_percent", 2) or 0),
        }
        cursor.execute(
            """
            SELECT TOP (1) bs.backup_finish_date, bs.is_copy_only,
                   bs.has_backup_checksums, bs.compressed_backup_size,
                   bmf.physical_device_name
            FROM msdb.dbo.backupset AS bs
            INNER JOIN msdb.dbo.backupmediafamily AS bmf
                ON bmf.media_set_id = bs.media_set_id
            WHERE bs.database_name = DB_NAME() AND bs.type = 'D'
            ORDER BY bs.backup_finish_date DESC
            """
        )
        backup = cursor.fetchone()
        backup_path = str(_row_value(backup, "physical_device_name", 4) or "") if backup else ""
        evidence["sql"]["latest_full_backup"] = (
            {
                "backup_finish_date": _row_value(backup, "backup_finish_date", 0),
                "copy_only": bool(_row_value(backup, "is_copy_only", 1)),
                "has_backup_checksums": bool(_row_value(backup, "has_backup_checksums", 2)),
                "compressed_backup_size": int(_row_value(backup, "compressed_backup_size", 3) or 0),
                "device_name_present": bool(backup_path),
            }
            if backup
            else None
        )
        if backup_path:
            verify_connection = connect_mssql(settings)
            try:
                evidence["sql"]["backup_verifyonly"] = _safe_backup_verify(verify_connection, backup_path)
            finally:
                verify_connection.close()
        else:
            evidence["sql"]["backup_verifyonly"] = {"status": "NOT_PROVEN", "reason": "no full backup path"}

        cursor.execute(
            f"""
            SELECT COUNT_BIG(*) AS total_count,
                   COALESCE(SUM(CASE WHEN IsEnabled = 1 THEN 1 ELSE 0 END), 0) AS active_count,
                   COALESCE(SUM(CASE WHEN ActivationStatus = 'PREPARED' THEN 1 ELSE 0 END), 0) AS prepared_count,
                   MIN(SamplingTargetId) AS minimum_id, MAX(SamplingTargetId) AS maximum_id
            FROM {TARGET_TABLE}
            WHERE PanelVersion = %s AND SamplingEra = %s
            """,
            ("TIERED50_PANEL_V1", "Tiered50"),
        )
        targets = cursor.fetchone()
        evidence["sql"]["tiered50_materialization"] = {
            "target_count": int(_row_value(targets, "total_count", 0) or 0),
            "active_count": int(_row_value(targets, "active_count", 1) or 0),
            "prepared_count": int(_row_value(targets, "prepared_count", 2) or 0),
            "minimum_id": int(_row_value(targets, "minimum_id", 3) or 0),
            "maximum_id": int(_row_value(targets, "maximum_id", 4) or 0),
        }
        cursor.execute(
            """
            SELECT TOP (40) LogicalDueBucket, COUNT_BIG(*) AS audit_count,
                   SUM(CASE WHEN Status = 'SUCCEEDED' THEN 1 ELSE 0 END) AS succeeded_count,
                   SUM(CASE WHEN Status = 'SKIPPED' THEN 1 ELSE 0 END) AS skipped_count,
                   MAX(CompletedAtUtc) AS last_completed_at_utc
            FROM ctl.MddMacTargetAudit
            WHERE CollectionContext = 'MAC_LEGACY7_PRODUCTION'
            GROUP BY LogicalDueBucket
            ORDER BY LogicalDueBucket DESC
            """
        )
        audit_rows = list(cursor.fetchall())
        recent = [
            {
                "logical_due_bucket": int(_row_value(row, "LogicalDueBucket", 0)),
                "audit_count": int(_row_value(row, "audit_count", 1) or 0),
                "succeeded_count": int(_row_value(row, "succeeded_count", 2) or 0),
                "skipped_count": int(_row_value(row, "skipped_count", 3) or 0),
                "last_completed_at_utc": _row_value(row, "last_completed_at_utc", 4),
            }
            for row in audit_rows
        ]
        evidence["sql"]["legacy7_recent_audit_buckets"] = recent[:12]
        if recent:
            latest_bucket = recent[0]["logical_due_bucket"]
            expected = list(range(latest_bucket - 5, latest_bucket + 1))
            by_bucket = {row["logical_due_bucket"]: row for row in recent}
            six = [by_bucket.get(bucket) for bucket in expected]
            evidence["sql"]["six_consecutive_expected_heartbeats"] = {
                "expected_buckets": expected,
                "accounted_buckets": [row for row in six if row is not None],
                "all_accounted": all(row is not None for row in six),
                "all_succeeded": all(row is not None and row["succeeded_count"] > 0 for row in six),
            }
        else:
            evidence["sql"]["six_consecutive_expected_heartbeats"] = {"all_accounted": False, "all_succeeded": False}

        cursor.execute(
            """
            SELECT TOP (1) RunId, LogicalDueBucket, CompletedAtUtc, Status
            FROM ctl.MddMacCollectorRun
            WHERE CollectionContext = 'MAC_LEGACY7_PRODUCTION'
              AND CompletedAtUtc IS NOT NULL
            ORDER BY CompletedAtUtc DESC, MacCollectorRunId DESC
            """
        )
        final_run = cursor.fetchone()
        evidence["sql"]["final_legacy7_run"] = (
            {
                "run_id": str(_row_value(final_run, "RunId", 0) or ""),
                "logical_due_bucket": int(_row_value(final_run, "LogicalDueBucket", 1) or 0),
                "completed_at_utc": _row_value(final_run, "CompletedAtUtc", 2),
                "status": str(_row_value(final_run, "Status", 3) or ""),
            }
            if final_run
            else None
        )
        cursor.execute(
            """
            SELECT TOP (20) h.HttpStatus, h.NetworkAttempted, h.HttpResponseReceived,
                   h.ProviderQuotaStatus, h.CompletedAtUtc
            FROM ctl.MddMacHttpAttempt AS h
            INNER JOIN ctl.MddMacTargetAudit AS a ON a.AuditId = h.AuditId
            WHERE a.CollectionContext = 'MAC_LEGACY7_PRODUCTION'
            ORDER BY h.CompletedAtUtc DESC
            """
        )
        evidence["sql"]["recent_http_attempts"] = [
            {
                "http_status": _row_value(row, "HttpStatus", 0),
                "network_attempted": bool(_row_value(row, "NetworkAttempted", 1)),
                "http_response_received": bool(_row_value(row, "HttpResponseReceived", 2)),
                "provider_quota_status": str(_row_value(row, "ProviderQuotaStatus", 3) or ""),
                "completed_at_utc": _row_value(row, "CompletedAtUtc", 4),
            }
            for row in cursor.fetchall()
        ]
        cursor.execute(
            """
            SELECT TOP (40) LogicalDueBucket, COUNT_BIG(*) AS audit_count,
                   SUM(CASE WHEN Status = 'SUCCEEDED' THEN 1 ELSE 0 END) AS succeeded_count,
                   SUM(CASE WHEN Status = 'SKIPPED' THEN 1 ELSE 0 END) AS skipped_count,
                   SUM(CASE WHEN Status = 'FAILED' THEN 1 ELSE 0 END) AS failed_count,
                   SUM(HttpAttempts) AS http_attempts,
                   MAX(CompletedAtUtc) AS last_completed_at_utc
            FROM ctl.MddMacTargetAudit
            WHERE CollectionContext = 'MAC_TIERED50_PRODUCTION'
            GROUP BY LogicalDueBucket
            ORDER BY LogicalDueBucket ASC
            """
        )
        tiered_rows = list(cursor.fetchall())
        evidence["sql"]["tiered50_recent_audit_buckets"] = [
            {
                "logical_due_bucket": int(_row_value(row, "LogicalDueBucket", 0)),
                "audit_count": int(_row_value(row, "audit_count", 1) or 0),
                "succeeded_count": int(_row_value(row, "succeeded_count", 2) or 0),
                "skipped_count": int(_row_value(row, "skipped_count", 3) or 0),
                "failed_count": int(_row_value(row, "failed_count", 4) or 0),
                "http_attempts": int(_row_value(row, "http_attempts", 5) or 0),
                "last_completed_at_utc": _row_value(row, "last_completed_at_utc", 6),
            }
            for row in tiered_rows
        ]
        tiered_by_bucket = {
            int(_row_value(row, "LogicalDueBucket", 0)): {
                "audit_count": int(_row_value(row, "audit_count", 1) or 0),
                "succeeded_count": int(_row_value(row, "succeeded_count", 2) or 0),
                "skipped_count": int(_row_value(row, "skipped_count", 3) or 0),
                "failed_count": int(_row_value(row, "failed_count", 4) or 0),
            }
            for row in tiered_rows
        }
        six_cycle = {"status": "NOT_PROVEN", "expected_distribution": list(APPROVED_SUPER_CYCLE_COUNTS)}
        for candidate in sorted(tiered_by_bucket):
            window = [tiered_by_bucket.get(candidate + offset) for offset in range(6)]
            if any(item is None for item in window):
                continue
            valid = all(
                item["audit_count"] == APPROVED_SUPER_CYCLE_COUNTS[(candidate + offset) % 6]
                and item["succeeded_count"] == item["audit_count"]
                and item["skipped_count"] == 0
                and item["failed_count"] == 0
                for offset, item in enumerate(window)
            )
            if valid:
                six_cycle = {
                    "status": "PASS",
                    "start_bucket": candidate,
                    "buckets": [candidate + offset for offset in range(6)],
                    "distribution": [item["audit_count"] for item in window],
                    "successful_target_audits": sum(item["succeeded_count"] for item in window),
                    "expected_distribution": list(APPROVED_SUPER_CYCLE_COUNTS),
                }
                break
        evidence["sql"]["tiered50_six_bucket_cycle"] = six_cycle
        cursor.execute(
            """
            SELECT TOP (1) RunId, LogicalDueBucket, StartedAtUtc, CompletedAtUtc,
                   Status, SummaryJson
            FROM ctl.MddMacCollectorRun
            WHERE CollectionContext = 'MAC_TIERED50_PRODUCTION'
            ORDER BY MacCollectorRunId ASC
            """
        )
        first_tiered_run = cursor.fetchone()
        first_cycle: dict[str, Any] = {
            "observed": bool(first_tiered_run),
            "validation_status": "NOT_OBSERVED",
        }
        if first_tiered_run:
            first_bucket = int(_row_value(first_tiered_run, "LogicalDueBucket", 1) or 0)
            summary: dict[str, Any] = {}
            try:
                decoded_summary = json.loads(str(_row_value(first_tiered_run, "SummaryJson", 5) or "{}"))
                if isinstance(decoded_summary, dict):
                    summary = decoded_summary
            except (TypeError, ValueError, json.JSONDecodeError):
                summary = {}
            cursor.execute(
                """
                SELECT COUNT_BIG(*) AS target_audits,
                       SUM(CASE WHEN Status = 'SUCCEEDED' THEN 1 ELSE 0 END) AS succeeded,
                       SUM(CASE WHEN Status = 'SKIPPED' THEN 1 ELSE 0 END) AS skipped,
                       SUM(CASE WHEN Status = 'FAILED' THEN 1 ELSE 0 END) AS failed,
                       SUM(HttpAttempts) AS http_attempts,
                       SUM(CASE WHEN PersistenceOutcome LIKE 'PERSISTED%%' THEN 1 ELSE 0 END) AS persisted,
                       SUM(CASE WHEN ErrorCategory IS NOT NULL THEN 1 ELSE 0 END) AS errors
                FROM ctl.MddMacTargetAudit
                WHERE CollectionContext = 'MAC_TIERED50_PRODUCTION'
                  AND LogicalDueBucket = %s
                """,
                (first_bucket,),
            )
            audit_summary = cursor.fetchone()
            cursor.execute(
                """
                SELECT COUNT_BIG(*) AS attempts,
                       SUM(CASE WHEN h.NetworkAttempted = 1 THEN 1 ELSE 0 END) AS network_attempts,
                       SUM(CASE WHEN h.AttemptNumber > 1 THEN 1 ELSE 0 END) AS retries,
                       SUM(CASE WHEN h.HttpResponseReceived = 1 THEN 1 ELSE 0 END) AS responses,
                       SUM(CASE WHEN h.HttpResponseReceived = 0 AND h.NetworkAttempted = 1 THEN 1 ELSE 0 END) AS transport_errors,
                       SUM(CASE WHEN h.QuotaPermitId IS NULL AND h.NetworkAttempted = 1 THEN 1 ELSE 0 END) AS attempts_without_permit
                FROM ctl.MddMacHttpAttempt AS h
                INNER JOIN ctl.MddMacTargetAudit AS a ON a.AuditId = h.AuditId
                WHERE a.CollectionContext = 'MAC_TIERED50_PRODUCTION'
                  AND a.LogicalDueBucket = %s
                """,
                (first_bucket,),
            )
            attempt_summary = cursor.fetchone()
            cursor.execute(
                """
                SELECT COUNT(DISTINCT p.PermitId) AS quota_permits,
                       COALESCE(SUM(p.Amount), 0) AS permitted_amount,
                       SUM(CASE WHEN p.DecisionKind = 'INITIAL' THEN 1 ELSE 0 END) AS initial_permits,
                       SUM(CASE WHEN p.DecisionKind = 'RETRY' THEN 1 ELSE 0 END) AS retry_permits,
                       SUM(CASE WHEN p.Status = 'HTTP_PROVIDER_ATTEMPT_CONFIRMED' THEN 1 ELSE 0 END) AS confirmed_permits,
                       SUM(CASE WHEN p.Status = 'CONSERVATIVE_LIVE_ATTEMPT' THEN 1 ELSE 0 END) AS uncertain_permits
                FROM ctl.MddMacQuotaPermit AS p
                INNER JOIN ctl.MddMacHttpAttempt AS h ON h.QuotaPermitId = p.PermitId
                INNER JOIN ctl.MddMacTargetAudit AS a ON a.AuditId = h.AuditId
                WHERE a.CollectionContext = 'MAC_TIERED50_PRODUCTION'
                  AND a.LogicalDueBucket = %s
                """,
                (first_bucket,),
            )
            permit_summary = cursor.fetchone()
            cursor.execute(
                """
                SELECT COALESCE(PersistenceOutcome, 'NONE') AS persistence_outcome,
                       COUNT_BIG(*) AS outcome_count
                FROM ctl.MddMacTargetAudit
                WHERE CollectionContext = 'MAC_TIERED50_PRODUCTION'
                  AND LogicalDueBucket = %s
                GROUP BY COALESCE(PersistenceOutcome, 'NONE')
                ORDER BY persistence_outcome
                """,
                (first_bucket,),
            )
            persistence_summary = [
                {
                    "persistence_outcome": str(_row_value(row, "persistence_outcome", 0) or ""),
                    "count": int(_row_value(row, "outcome_count", 1) or 0),
                }
                for row in cursor.fetchall()
            ]
            first_cycle = {
                "observed": True,
                "run_id": str(_row_value(first_tiered_run, "RunId", 0) or ""),
                "logical_due_bucket": first_bucket,
                "started_at_utc": _row_value(first_tiered_run, "StartedAtUtc", 2),
                "completed_at_utc": _row_value(first_tiered_run, "CompletedAtUtc", 3),
                "run_status": str(_row_value(first_tiered_run, "Status", 4) or ""),
                "target_audits": int(_row_value(audit_summary, "target_audits", 0) or 0),
                "successful_target_audits": int(_row_value(audit_summary, "succeeded", 1) or 0),
                "skipped_target_audits": int(_row_value(audit_summary, "skipped", 2) or 0),
                "failed_target_audits": int(_row_value(audit_summary, "failed", 3) or 0),
                "persisted_target_audits": int(_row_value(audit_summary, "persisted", 5) or 0),
                "audit_error_count": int(_row_value(audit_summary, "errors", 6) or 0),
                "http_attempts": int(_row_value(attempt_summary, "attempts", 0) or 0),
                "network_attempts": int(_row_value(attempt_summary, "network_attempts", 1) or 0),
                "retry_attempts": int(_row_value(attempt_summary, "retries", 2) or 0),
                "http_responses": int(_row_value(attempt_summary, "responses", 3) or 0),
                "transport_error_attempts": int(_row_value(attempt_summary, "transport_errors", 4) or 0),
                "attempts_without_permit": int(_row_value(attempt_summary, "attempts_without_permit", 5) or 0),
                "quota_permits": int(_row_value(permit_summary, "quota_permits", 0) or 0),
                "permitted_amount": int(_row_value(permit_summary, "permitted_amount", 1) or 0),
                "initial_permits": int(_row_value(permit_summary, "initial_permits", 2) or 0),
                "retry_permits": int(_row_value(permit_summary, "retry_permits", 3) or 0),
                "confirmed_permits": int(_row_value(permit_summary, "confirmed_permits", 4) or 0),
                "uncertain_permits": int(_row_value(permit_summary, "uncertain_permits", 5) or 0),
                "persistence_outcomes": persistence_summary,
                "coverage": summary.get("coverage"),
                "timing": summary.get("timing"),
                "validation_status": (
                    "PASS"
                    if int(_row_value(audit_summary, "failed", 3) or 0) == 0
                    and int(_row_value(audit_summary, "skipped", 2) or 0) == 0
                    and int(_row_value(attempt_summary, "attempts_without_permit", 5) or 0) == 0
                    and (
                        not isinstance(summary.get("coverage"), dict)
                        or bool(summary["coverage"].get("coverage_sla_met"))
                    )
                    else "FAILED_CADENCE_OR_PERSISTENCE_GATE"
                ),
            }
        evidence["sql"]["first_tiered50_scheduled_cycle"] = first_cycle
        cursor.execute(
            """
            SELECT TOP (1) CutoverKey, Tiered50ActivationStatus,
                   Tiered50ActivationAtUtc, RecordedAtUtc
            FROM ctl.MddMacProductionCutover
            ORDER BY CutoverId DESC
            """
        )
        cutover = cursor.fetchone()
        evidence["sql"]["latest_cutover_record"] = (
            {
                "cutover_key": str(_row_value(cutover, "CutoverKey", 0) or ""),
                "status": str(_row_value(cutover, "Tiered50ActivationStatus", 1) or ""),
                "activation_at_utc": _row_value(cutover, "Tiered50ActivationAtUtc", 2),
                "recorded_at_utc": _row_value(cutover, "RecordedAtUtc", 3),
            }
            if cutover
            else None
        )
    finally:
        connection.close()
    latest_cutover = evidence["sql"].get("latest_cutover_record") or {}
    latest_cutover_status = str(latest_cutover.get("status") or "") if isinstance(latest_cutover, dict) else ""
    tiered_plist = evidence["host"].get("tiered50_plist", {})
    evidence["status"] = (
        "BLOCKED — TIERED50 STOPPED AFTER FAILED FIRST SCHEDULED HEARTBEAT; LEGACY7 RETIRED"
        if latest_cutover_status == "OUTAGE"
        or evidence["sql"]["tiered50_materialization"]["active_count"] == 0
        or bool(isinstance(tiered_plist, dict) and tiered_plist.get("disabled"))
        else "ACTIVE — VALIDATION INCOMPLETE"
    )
    return evidence


def main(argv: list[str] | None = None) -> int:
    output = Path(argv[0]).expanduser() if argv else ROOT / "docs" / "operational" / "tiered50-production-evidence.json"
    try:
        evidence = collect()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        print(json.dumps({"status": "PASS", "output": str(output), "mdd_requests_issued": 0}, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error_type": type(exc).__name__}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
