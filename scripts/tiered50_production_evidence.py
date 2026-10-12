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
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlConnectionSettings,
    MssqlPersistence,
    connect_mssql,
)
from cologne_transit_collector.domain import APPROVED_SUPER_CYCLE_COUNTS  # noqa: E402
from cologne_transit_collector.runtime_configuration import (  # noqa: E402
    ADMIN_KEYCHAIN_SERVICE as RUNTIME_ADMIN_KEYCHAIN_SERVICE,
    EXPECTED_SQL_DATABASE,
    MacOSKeychainSecretProvider,
    resolve_administrator_environment,
)
from cologne_transit_collector.admin_evidence import (  # noqa: E402
    EVIDENCE_ARTIFACT_VERSION,
    AdminEvidenceError,
    REQUIRED_CHECK_IDS,
    json_safe,
    normalize_sql_instance_identity,
    payload_sha256,
    resolve_current_evidence_path,
    sign_payload,
    validate_admin_evidence_artifact,
    verify_signature,
)
from provision_macos_runtime_login import inspect_runtime_permissions  # noqa: E402


LEGACY_LABEL = "com.cologne-transit-intelligence.mdd-collector"
TIERED_LABEL = "com.cologne-transit-intelligence.mdd-tiered50-collector"
TARGET_TABLE = "ctl.MddMacTiered50ProductionTarget"
MISSING_ADMIN_CREDENTIALS_OR_PERMISSIONS = "MISSING_ADMIN_CREDENTIALS_OR_PERMISSIONS"
ADMIN_KEYCHAIN_SERVICE = RUNTIME_ADMIN_KEYCHAIN_SERVICE
OPERATIONAL_EVIDENCE_PATH = resolve_current_evidence_path(repository_root=ROOT)


def _row_value(row: Any, name: str, index: int = 0) -> Any:
    if isinstance(row, dict):
        if name in row:
            return row.get(name)
        folded_name = str(name).casefold()
        for key, value in row.items():
            if str(key).casefold() == folded_name:
                return value
        return None
    if row is None:
        return None
    try:
        return row[index]
    except (IndexError, KeyError, TypeError):
        return None


def _normalize_sql_text(value: Any) -> str | None:
    """Normalize SQL driver text without turning malformed values into PASS."""

    if value is None:
        return None
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        try:
            value = bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized if normalized else None


def _permission_state(value: Any) -> str:
    """Classify SQL permission metadata without treating NULL as denial."""

    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        try:
            value = bytes(value).decode("ascii")
        except UnicodeDecodeError:
            return "UNKNOWN"
    if value is True or value == 1 or str(value).strip().casefold() in {"1", "true", "yes"}:
        return "GRANTED"
    if value is False or value == 0 or str(value).strip().casefold() in {"0", "false", "no"}:
        return "DENIED"
    return "UNKNOWN"


def _query_status(value: Any) -> str:
    if isinstance(value, Mapping):
        return str(value.get("status") or "").upper()
    return str(value or "").upper()


def _successful_dmv_access(query_statuses: Mapping[str, Any] | None) -> tuple[bool, bool]:
    """Return (proven, denied) for the relevant server DMV reads."""

    if not query_statuses:
        return False, False
    relevant = (
        "capacity.volume_capacity_dm_os_volume_stats",
        "capacity.database_log_space_dm_db_log_space_usage",
    )
    observed = [_query_status(query_statuses.get(name)) for name in relevant if name in query_statuses]
    if not observed:
        return False, False
    if any(status != "PASS" for status in observed):
        return False, True
    return True, False


VALID_SQL_RECOVERY_MODELS = frozenset({"FULL", "SIMPLE", "BULK_LOGGED"})


def _evaluate_runtime_health(
    runtime_health_evidence: Mapping[str, Any],
    runtime_health_row: Any,
) -> dict[str, Any]:
    """Evaluate one SQL runtime-health row after safe driver decoding."""

    database_name = _normalize_sql_text(_row_value(runtime_health_row, "database_name", 0)) or ""
    database_status = _normalize_sql_text(_row_value(runtime_health_row, "database_status", 1)) or ""
    recovery_model = _normalize_sql_text(_row_value(runtime_health_row, "recovery_model", 2)) or ""
    runtime_values_valid = (
        runtime_health_row is not None
        and database_name == EXPECTED_SQL_DATABASE
        and database_status.casefold() == "online"
        and recovery_model.casefold() in {value.casefold() for value in VALID_SQL_RECOVERY_MODELS}
    )
    return {
        "status": "PASS"
        if runtime_health_evidence.get("status") == "PASS" and runtime_values_valid
        else "NOT_PROVEN",
        "database_name": database_name,
        "database_status": database_status,
        "recovery_model": recovery_model,
        "valid_recovery_models": sorted(VALID_SQL_RECOVERY_MODELS),
        "evidence": dict(runtime_health_evidence),
    }


def _role_environment(role: str) -> tuple[dict[str, str], str]:
    """Resolve runtime/admin connection inputs without cross-role fallback.

    Runtime evidence may use the installed collector configuration.  Admin
    evidence is different: every credential-bearing admin field must be
    explicitly supplied under ``MSSQL_ADMIN_*``.  In particular, a partial
    admin environment never inherits the runtime user or password.
    """

    if role == "admin":
        return resolve_administrator_environment(
            os.environ,
            secret_provider=MacOSKeychainSecretProvider(),
        )

    values = dict(os.environ)
    prefix = role.upper()
    found = False
    for suffix in (
        "HOST",
        "DATABASE",
        "USER",
        "PASSWORD",
        "PORT",
        "LOGIN_TIMEOUT_SECONDS",
        "QUERY_TIMEOUT_SECONDS",
    ):
        role_name = f"MSSQL_{prefix}_{suffix}"
        if role_name in values:
            values[f"MSSQL_{suffix}"] = values[role_name]
            found = True
    return values, (f"MSSQL_{prefix}_*" if found else "BASE_MSSQL_*_FALLBACK")


def _sql_error_code(exc: BaseException) -> int | None:
    match = re.search(r"\b(\d{3,5})\b", str(exc))
    return int(match.group(1)) if match else None


def _sanitize_sql_reason(exc: BaseException) -> str:
    """Retain diagnostic text without credential or physical-path leakage."""

    reason = str(exc)
    reason = re.sub(
        r"(?i)(password|secret|token|apikey|credential)\s*[=:]\s*[^,;\s]+",
        r"\1=<redacted>",
        reason,
    )
    reason = re.sub(r"(?i)(?:[A-Za-z]:[\\/]|/)[^\s']+", "<path redacted>", reason)
    return reason[:500]


def _statement_id(statement: str) -> str:
    normalized = " ".join(statement.lower().split())
    if "dm_os_volume_stats" in normalized:
        return "capacity.volume_capacity_dm_os_volume_stats"
    if "dm_db_log_space_usage" in normalized:
        return "capacity.database_log_space_dm_db_log_space_usage"
    if "backupset" in normalized or "backupmediafamily" in normalized:
        return "backup.latest_full_backup_history"
    if "restore verifyonly" in normalized:
        return "backup.restore_verifyonly_with_checksum"
    if "sys.database_files" in normalized:
        return "capacity.database_files"
    if "mddmachttpattempt" in normalized:
        return "collector.http_attempt_history"
    if "mddmactargetaudit" in normalized:
        return "collector.historical_target_audits"
    if "mddmaccollectorrun" in normalized:
        return "collector.historical_runs"
    if "mddmac" in normalized or "mdd_mac" in normalized:
        return "collector_runtime_history_or_materialization"
    if "@@servername" in normalized or "suser_sname" in normalized:
        return "admin.identity"
    if "databasepropertyex" in normalized:
        return "admin.sql_runtime_health"
    return "admin.read_only_query"


def _evaluate_admin_permissions(
    permission_values: dict[str, Any],
    *,
    query_statuses: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate permissions while separating unknown metadata from denial.

    SQL Server 2025 can return NULL for the newer server DMV permission
    metadata even when an effective sysadmin can successfully execute the
    relevant DMVs.  The executed DMV evidence is therefore authoritative for
    that capability.  A failed or permission-denied DMV query never becomes a
    PASS merely because the login claims to be sysadmin.
    """

    try:
        product_major_version = int(permission_values.get("product_major_version") or 0)
    except (TypeError, ValueError):
        product_major_version = 0
    sysadmin_state = _permission_state(permission_values.get("is_sysadmin"))
    is_sysadmin = sysadmin_state == "GRANTED"
    dmv_access_proven, dmv_access_denied = _successful_dmv_access(query_statuses)

    if product_major_version >= 16:
        server_dmv_permission = "VIEW SERVER PERFORMANCE STATE"
        server_dmv_metadata = _permission_state(permission_values.get("has_view_server_performance_state"))
    elif product_major_version > 0:
        server_dmv_permission = "VIEW SERVER STATE"
        server_dmv_metadata = _permission_state(permission_values.get("has_view_server_state"))
    else:
        server_dmv_permission = "VIEW SERVER STATE or VIEW SERVER PERFORMANCE STATE"
        server_dmv_metadata = (
            "GRANTED"
            if _permission_state(permission_values.get("has_view_server_state")) == "GRANTED"
            or _permission_state(permission_values.get("has_view_server_performance_state")) == "GRANTED"
            else "DENIED"
            if _permission_state(permission_values.get("has_view_server_state")) == "DENIED"
            and _permission_state(permission_values.get("has_view_server_performance_state")) == "DENIED"
            else "UNKNOWN"
        )

    if server_dmv_metadata == "GRANTED":
        server_dmv_state = True
        server_dmv_effective_state = "GRANTED_BY_METADATA"
    elif dmv_access_denied:
        server_dmv_state = False
        server_dmv_effective_state = "DENIED_BY_EXECUTION"
    elif dmv_access_proven:
        server_dmv_state = True
        server_dmv_effective_state = "GRANTED_BY_SUCCESSFUL_DMV_ACCESS"
    elif server_dmv_metadata == "DENIED":
        server_dmv_state = False
        server_dmv_effective_state = "DENIED_BY_METADATA"
    elif is_sysadmin and query_statuses:
        server_dmv_state = False
        server_dmv_effective_state = "NOT_PROVEN_BY_EXECUTION"
    else:
        server_dmv_state = False
        server_dmv_effective_state = "UNKNOWN"

    def check(name: str, *, effective_access: bool = False) -> tuple[bool, str]:
        state = _permission_state(permission_values.get(name))
        if state == "GRANTED":
            return True, "GRANTED_BY_METADATA"
        if state == "DENIED":
            return False, "DENIED_BY_METADATA"
        if effective_access:
            return True, "GRANTED_BY_SUCCESSFUL_QUERY"
        if is_sysadmin:
            return True, "GRANTED_BY_SYSADMIN"
        return False, "UNKNOWN_METADATA"

    check_results = {
        "database_view_definition": check("has_view_definition"),
        "database_view_state": check("has_view_database_state"),
        "server_dmv_state": (server_dmv_state, server_dmv_effective_state),
        "backup_history_backupset_select": check("has_msdb_backupset_select"),
        "backup_history_backupmediafamily_select": check("has_msdb_backupmediafamily_select"),
        "operational_history_database_select": check("has_database_select"),
        # RESTORE VERIFYONLY is an administrative operation. SQL Server
        # versions expose its authorization through database-creation or
        # equivalent administrative role membership rather than a distinct
        # VERIFYONLY permission name.
        "backup_verifyonly_authorization": check("has_create_any_database")
        if _permission_state(permission_values.get("has_create_any_database")) != "UNKNOWN"
        else (
            True,
            "GRANTED_BY_SYSADMIN_OR_DBCREATOR",
        )
        if is_sysadmin or _permission_state(permission_values.get("is_dbcreator")) == "GRANTED"
        else (False, "UNKNOWN_METADATA"),
    }
    checks = {name: result[0] for name, result in check_results.items()}
    permission_states = {name: result[1] for name, result in check_results.items()}
    missing = [name for name, present in checks.items() if not present]
    denied = [name for name, state in permission_states.items() if state.startswith("DENIED")]
    unknown = [name for name, state in permission_states.items() if state.startswith("UNKNOWN") or state == "NOT_PROVEN_BY_EXECUTION"]
    return {
        "status": "PASS" if not missing else "NOT_PROVEN",
        "product_major_version": product_major_version or None,
        "is_sysadmin": is_sysadmin,
        "sysadmin_membership_state": sysadmin_state,
        "server_dmv_required_permission": server_dmv_permission,
        "permission_checks": checks,
        "permission_states": permission_states,
        "missing_permissions": missing,
        "denied_permissions": denied,
        "unknown_permissions": unknown,
        "verified_dmv_access": dmv_access_proven,
    }


class _SafeAdminCursor:
    """Keep one denied admin statement from hiding independent evidence."""

    def __init__(self, connection: Any, failures: list[dict[str, Any]]) -> None:
        self._connection = connection
        self._failures = failures
        self._cursor: Any | None = None
        self._last_statement = ""
        self._last_statement_id = ""
        self._failed = False
        self.last_failure: dict[str, Any] | None = None

    def _record_failure(self, exc: BaseException, *, phase: str = "execute") -> None:
        failure = {
            "statement_id": self._last_statement_id or "admin.read_only_query",
            "phase": phase,
            "statement": self._last_statement,
            "error_code": _sql_error_code(exc),
            "error_type": type(exc).__name__,
            "reason": _sanitize_sql_reason(exc),
            "status": "NOT_PROVEN",
        }
        self.last_failure = failure
        self._failures.append(failure)

    def execute(self, statement: str, params: Any = None) -> None:
        self._last_statement = " ".join(str(statement).strip().split())
        self._last_statement_id = _statement_id(self._last_statement)
        self._failed = False
        self.last_failure = None
        try:
            if self._cursor is None:
                self._cursor = self._connection.cursor()
            if params is None:
                self._cursor.execute(statement)
            else:
                self._cursor.execute(statement, params)
        except Exception as exc:
            self._failed = True
            self._record_failure(exc)

    def fetchone(self) -> Any:
        if self._failed:
            return None
        try:
            return self._cursor.fetchone() if self._cursor is not None else None
        except Exception as exc:
            self._failed = True
            self._record_failure(exc, phase="fetchone")
            return None

    def fetchall(self) -> list[Any]:
        if self._failed:
            return []
        try:
            return list(self._cursor.fetchall()) if self._cursor is not None else []
        except Exception as exc:
            self._failed = True
            self._record_failure(exc, phase="fetchall")
            return []


def _query_evidence(cursor: _SafeAdminCursor, *, statement_id: str) -> dict[str, Any]:
    """Return explicit query status; never turn a denied query into zeros."""

    if cursor.last_failure is not None:
        return dict(cursor.last_failure)
    return {"status": "PASS", "statement_id": statement_id}


def derive_current_status(
    *,
    scheduler: Mapping[str, Any],
    plist: Mapping[str, Any],
    materialization: Mapping[str, Any],
    latest_cutover: Mapping[str, Any] | None,
    latest_resume: Mapping[str, Any] | None,
    latest_tiered_run: Mapping[str, Any] | None,
    six_cycle: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Use current scheduler/SQL/activity state without erasing outage history."""

    scheduler_active = (
        scheduler.get("returncode") == 0
        and bool(plist.get("exists"))
        and not bool(plist.get("disabled"))
        and not bool(plist.get("contains_secret_marker"))
    )
    sql_active = (
        int(materialization.get("target_count", 0) or 0) == 43
        and int(materialization.get("active_count", 0) or 0) == 43
    )
    historical_outage = (
        None
        if latest_cutover is None
        else str(latest_cutover.get("status") or "") == "OUTAGE"
    )
    if not sql_active or not scheduler_active:
        status = "BLOCKED — TIERED50 STOPPED; LEGACY7 RETIRED"
        stations = 0
    elif isinstance(six_cycle, Mapping) and six_cycle.get("status") == "PASS":
        status = "ACTIVE — SIX-BUCKET CYCLE VALIDATED"
        stations = 50
    else:
        status = "ACTIVE — VALIDATION INCOMPLETE"
        stations = int((latest_tiered_run or {}).get("successful_target_audits", 0) or 0)
    return {
        "status": status,
        "tiered50_active": scheduler_active and sql_active,
        "stations_actually_collecting": stations,
        "scheduler_active": scheduler_active,
        "sql_activation_valid": sql_active,
        "resume_event_present": latest_resume is not None,
        "historical_outage_preserved": historical_outage,
        "historical_outage_evidence": (
            "OUTAGE_RECORD_PRESENT"
            if historical_outage is True
            else "NO_OUTAGE_RECORD_FOUND"
            if historical_outage is False
            else "NOT_PROVEN_ADMIN_QUERY_UNAVAILABLE"
        ),
        "latest_run_status": (latest_tiered_run or {}).get("run_status"),
        "current_evidence_basis": [
            "fresh_launchd_state",
            "immutable_sql_activation_state",
            "latest_tiered50_collection_evidence",
        ],
    }


class _UnavailableConnection:
    """Cursor-compatible placeholder used when the admin connection itself fails."""

    def __init__(self, error: BaseException) -> None:
        self._error = error

    def cursor(self) -> Any:
        error = self._error

        class _UnavailableCursor:
            def execute(self, *_args: Any, **_kwargs: Any) -> None:
                raise error

            def fetchone(self) -> None:
                return None

            def fetchall(self) -> list[Any]:
                return []

        return _UnavailableCursor()

    def close(self) -> None:
        return None


def _safe_runtime_read(label: str, callback: Any) -> dict[str, Any]:
    try:
        value = callback()
        if isinstance(value, dict):
            result = dict(value)
            result.setdefault("status", "PASS")
            result["evidence_role"] = "DEDICATED_RUNTIME_LOGIN"
            return result
        return {"status": "PASS", "value": value, "evidence_role": "DEDICATED_RUNTIME_LOGIN"}
    except Exception as exc:
        return {
            "status": "NOT_PROVEN",
            "evidence_role": "DEDICATED_RUNTIME_LOGIN",
            "read": label,
            "error_code": _sql_error_code(exc),
            "error_type": type(exc).__name__,
            "reason": _sanitize_sql_reason(exc),
        }


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


def _load_external_admin_evidence(path: str | Path, *, now_utc: datetime) -> dict[str, Any]:
    """Load and authenticate a Windows-side SQL evidence envelope."""

    source = Path(path).expanduser().resolve()
    try:
        artifact = json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("external administrative evidence is not readable JSON") from exc
    if not isinstance(artifact, dict):
        raise RuntimeError("external administrative evidence must be a JSON object")
    try:
        validation = validate_admin_evidence_artifact(
            artifact,
            now_utc=now_utc,
            expected_database=EXPECTED_SQL_DATABASE,
            public_key_path=os.environ.get("MSSQL_ADMIN_EVIDENCE_PUBLIC_KEY"),
        )
    except AdminEvidenceError as exc:
        raise RuntimeError(f"external administrative evidence is not authenticated: {exc}") from exc
    if not isinstance(artifact.get("sql"), Mapping):
        raise RuntimeError("external administrative evidence has no SQL payload")
    return {
        "artifact": artifact,
        "validation": validation,
    }


def _backup_path_list(paths: str | Sequence[str]) -> list[str]:
    if isinstance(paths, str):
        values = [paths]
    else:
        values = [str(path) for path in paths]
    return [path for path in values if path.strip()]


def _safe_backup_verify(connection: Any, paths: str | Sequence[str]) -> dict[str, Any]:
    """Run VERIFYONLY across the complete native backup media set.

    Error 3013 is deliberately reported as undetermined.  It can accompany a
    permission, service-account file-access, driver, unavailable-file, or
    integrity problem; the evidence must retain those alternatives until the
    server-side diagnostics resolve them.
    """

    media = _backup_path_list(paths)
    statement = "RESTORE VERIFYONLY FROM " + ", ".join(
        "DISK = N'<backup path redacted>'" for _ in media
    ) + " WITH CHECKSUM"
    if not media:
        return {
            "status": "NOT_PROVEN",
            "statement_id": "backup.restore_verifyonly_with_checksum",
            "statement": statement,
            "reason": "the latest full backup has no usable media paths",
            "cause_classification": "UNDETERMINED",
            "checksum_verified": False,
        }
    try:
        autocommit = getattr(connection, "autocommit", None)
        if callable(autocommit):
            autocommit(True)
        cursor = connection.cursor()
        clauses = ", ".join(
            f"DISK = N'{path.replace(chr(39), chr(39) + chr(39))}'" for path in media
        )
        cursor.execute(f"RESTORE VERIFYONLY FROM {clauses} WITH CHECKSUM")
        try:
            cursor.fetchall()
        except Exception:
            pass
        return {
            "status": "PASS",
            "statement_id": "backup.restore_verifyonly_with_checksum",
            "statement": statement,
            "checksum_verified": True,
            "media_count": len(media),
            "cause_classification": "VERIFIED",
        }
    except Exception as exc:
        return {
            "status": "NOT_PROVEN",
            "statement_id": "backup.restore_verifyonly_with_checksum",
            "statement": statement,
            "error_code": _sql_error_code(exc),
            "error_type": type(exc).__name__,
            "reason": _sanitize_sql_reason(exc),
            "diagnostic_messages": [_sanitize_sql_reason(exc)],
            "cause_classification": "UNDETERMINED",
            "checksum_verified": False,
            "media_count": len(media),
        }


def _safe_backup_access_diagnostics(connection: Any, path: str) -> dict[str, Any]:
    """Probe server-side backup visibility without changing production data."""

    result: dict[str, Any] = {
        "statement_id": "backup.server_side_file_access",
        "statement": "EXEC master.dbo.xp_fileexist N'<backup path redacted>'",
        "execution_context": "SQL_SERVER_SERVICE_ACCOUNT",
        "status": "NOT_PROVEN",
    }
    try:
        cursor = connection.cursor()
        escaped = str(path).replace("'", "''")
        cursor.execute(f"EXEC master.dbo.xp_fileexist N'{escaped}'")
        rows = []
        try:
            rows = list(cursor.fetchall())
        except Exception:
            row = cursor.fetchone()
            if row is not None:
                rows = [row]
        row = rows[0] if rows else None
        file_exists = _row_value(row, "File Exists", 0) if row is not None else None
        parent_exists = _row_value(row, "Parent Directory Exists", 2) if row is not None else None
        result.update(
            {
                "status": "PASS" if bool(file_exists) else "NOT_PROVEN",
                "file_exists": bool(file_exists),
                "parent_directory_exists": bool(parent_exists),
            }
        )
        if not file_exists:
            result["reason"] = "SQL Server service-account file visibility was not proven"
    except Exception as exc:
        result.update(
            {
                "status": "NOT_PROVEN",
                "error_code": _sql_error_code(exc),
                "error_type": type(exc).__name__,
                "reason": _sanitize_sql_reason(exc),
            }
        )
    return result


def _safe_backup_header(connection: Any, path: str) -> dict[str, Any]:
    """Read backup header metadata without treating it as checksum proof."""

    result: dict[str, Any] = {
        "statement_id": "backup.restore_headeronly",
        "statement": "RESTORE HEADERONLY FROM DISK = N'<backup path redacted>'",
        "status": "NOT_PROVEN",
    }
    try:
        cursor = connection.cursor()
        escaped = str(path).replace("'", "''")
        cursor.execute(f"RESTORE HEADERONLY FROM DISK = N'{escaped}'")
        try:
            rows = list(cursor.fetchall())
        except Exception:
            rows = []
        result.update(
            {
                "status": "PASS" if rows else "NOT_PROVEN",
                "header_row_count": len(rows),
                "checksum_verified": False,
            }
        )
        if not rows:
            result["reason"] = "RESTORE HEADERONLY returned no rows"
    except Exception as exc:
        result.update(
            {
                "status": "NOT_PROVEN",
                "error_code": _sql_error_code(exc),
                "error_type": type(exc).__name__,
                "reason": _sanitize_sql_reason(exc),
                "diagnostic_messages": [_sanitize_sql_reason(exc)],
            }
        )
    return result


def collect(
    *,
    signing_key_path: str | Path | None = None,
    admin_evidence_path: str | Path | None = None,
) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc)
    external_admin = (
        _load_external_admin_evidence(admin_evidence_path, now_utc=generated_at)
        if admin_evidence_path is not None
        else None
    )
    user_agent_dir = Path.home() / "Library" / "LaunchAgents"
    legacy_plist = user_agent_dir / f"{LEGACY_LABEL}.plist"
    tiered_plist = user_agent_dir / f"{TIERED_LABEL}.plist"
    evidence: dict[str, Any] = {
        "artifact_version": EVIDENCE_ARTIFACT_VERSION,
        "capture_role": "CURRENT_READ_ONLY_ROLE_SEPARATED_CAPTURE",
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

    runtime_environment, runtime_role_source = _role_environment("runtime")
    admin_environment, admin_role_source = _role_environment("admin")
    sql_evidence: dict[str, Any] = {
        "runtime_role_source": runtime_role_source,
        "admin_role_source": admin_role_source,
        "privileged_query_failures": [],
        "permission_error_297": [],
    }
    try:
        runtime_settings = MssqlConnectionSettings.from_environment(runtime_environment)
        runtime_persistence = MssqlPersistence(settings=runtime_settings)
        sql_evidence["runtime_connection"] = runtime_settings.redacted()
        sql_evidence["runtime_login"] = _safe_runtime_read(
            "runtime_login_status", runtime_persistence.runtime_login_status
        )
        runtime_login = sql_evidence.get("runtime_login")
        sql_evidence["runtime_sql_instance_identity"] = (
            normalize_sql_instance_identity(runtime_login)
            if isinstance(runtime_login, Mapping) and runtime_login.get("status", "PASS") == "PASS"
            else {"status": "NOT_PROVEN", "reason": "runtime SQL instance identity was not available"}
        )
        sql_evidence["runtime_schema"] = _safe_runtime_read(
            "validate_runtime_schema", runtime_persistence.validate_runtime_schema
        )
        sql_evidence["tiered50_schema"] = _safe_runtime_read(
            "validate_tiered50_runtime_schema", runtime_persistence.validate_tiered50_runtime_schema
        )
        sql_evidence["runtime_permission_matrix"] = _safe_runtime_read(
            "inspect_runtime_permissions", lambda: inspect_runtime_permissions(runtime_settings)
        )
        sql_evidence["quota_usage"] = _safe_runtime_read(
            "current_month_usage", lambda: runtime_persistence.current_month_usage(at_utc=generated_at)
        )
    except Exception as exc:
        sql_evidence["runtime_connection"] = {
            "status": "NOT_PROVEN",
            "error_code": _sql_error_code(exc),
            "error_type": type(exc).__name__,
            "reason": str(exc)[:500],
        }
        for name in ("runtime_login", "runtime_schema", "tiered50_schema", "runtime_permission_matrix", "quota_usage"):
            sql_evidence[name] = {
                "status": "NOT_PROVEN",
                "evidence_role": "DEDICATED_RUNTIME_LOGIN",
                "reason": "runtime connection configuration was unavailable",
            }
        sql_evidence["runtime_sql_instance_identity"] = {
            "status": "NOT_PROVEN",
            "reason": "runtime connection configuration was unavailable",
        }

    admin_configuration_error: BaseException | None = None
    admin_settings: MssqlConnectionSettings | None = None
    try:
        missing_admin = str(admin_environment.get("__ADMIN_MISSING_FIELDS__") or "")
        if missing_admin:
            raise RuntimeError(
                f"{MISSING_ADMIN_CREDENTIALS_OR_PERMISSIONS}: explicitly configure {missing_admin}"
            )
        admin_settings = MssqlConnectionSettings.from_environment(
            {key: value for key, value in admin_environment.items() if not key.startswith("__")}
        )
        if admin_settings.database != EXPECTED_SQL_DATABASE:
            raise RuntimeError(f"SQL Server target database must be {EXPECTED_SQL_DATABASE}")
        sql_evidence["admin_connection"] = {
            "status": "NOT_PROVEN",
            "connection_status": "CONFIGURED",
            "authorization_status": "NOT_PROVEN",
            "connection": admin_settings.redacted(),
            "evidence_role": "PRIVILEGED_READ_ONLY_ADMIN",
        }
    except Exception as exc:
        admin_configuration_error = exc
        sql_evidence["admin_connection"] = {
            "status": "NOT_PROVEN",
            "connection_status": "NOT_CONFIGURED",
            "authorization_status": "NOT_PROVEN",
            "evidence_role": "PRIVILEGED_READ_ONLY_ADMIN",
            "blocker_code": MISSING_ADMIN_CREDENTIALS_OR_PERMISSIONS,
            "error_code": _sql_error_code(exc),
            "error_type": type(exc).__name__,
            "reason": _sanitize_sql_reason(exc),
        }

    try:
        if admin_configuration_error is not None or admin_settings is None:
            raise admin_configuration_error or RuntimeError("admin SQL configuration is unavailable")
        connection = connect_mssql(admin_settings)
        sql_evidence["admin_connection"]["connection_status"] = "PASS"
    except Exception as exc:
        sql_evidence["admin_connection"] = {
            **sql_evidence.get("admin_connection", {}),
            "status": "NOT_PROVEN",
            "connection_status": "FAILED",
            "authorization_status": "NOT_PROVEN",
            "blocker_code": MISSING_ADMIN_CREDENTIALS_OR_PERMISSIONS,
            "error_code": _sql_error_code(exc),
            "error_type": type(exc).__name__,
            "reason": _sanitize_sql_reason(exc),
        }
        connection = _UnavailableConnection(exc)
    evidence["sql"] = sql_evidence
    permission_values: dict[str, Any] | None = None
    try:
        cursor = _SafeAdminCursor(connection, evidence["sql"]["privileged_query_failures"])
        cursor.execute(
            """
            SELECT CAST(SERVERPROPERTY('ServerName') AS NVARCHAR(128)) AS ServerName,
                   DB_NAME() AS DatabaseName,
                   SUSER_SNAME() AS LoginName,
                   SYSUTCDATETIME() AS CapturedAtUtc,
                   CAST(SERVERPROPERTY('MachineName') AS NVARCHAR(128)) AS MachineName,
                   CAST(SERVERPROPERTY('InstanceName') AS NVARCHAR(128)) AS InstanceName,
                   CAST(@@SERVERNAME AS NVARCHAR(128)) AS ConfiguredServerName
            """
        )
        identity = cursor.fetchone()
        identity_failure = cursor.last_failure
        effective_login = str(_row_value(identity, "LoginName", 2) or "")
        runtime_login = str(
            (sql_evidence.get("runtime_login") or {}).get("login_name")
            if isinstance(sql_evidence.get("runtime_login"), dict)
            else ""
        )
        evidence["sql"]["identity"] = {
            "status": "PASS" if identity_failure is None and effective_login else "NOT_PROVEN",
            "server_name": str(_row_value(identity, "ServerName", 0) or ""),
            "database_name": str(_row_value(identity, "DatabaseName", 1) or ""),
            "login_name": effective_login,
            "captured_at_utc": _row_value(identity, "CapturedAtUtc", 3),
            "machine_name": str(_row_value(identity, "MachineName", 4) or ""),
            "instance_name": str(_row_value(identity, "InstanceName", 5) or ""),
            "configured_server_name": str(_row_value(identity, "ConfiguredServerName", 6) or ""),
        }
        evidence["sql"]["identity"]["sql_instance_identity"] = normalize_sql_instance_identity(
            evidence["sql"]["identity"]
        )
        cursor.execute(
            """
            SELECT CAST(SERVERPROPERTY('ProductMajorVersion') AS INT) AS product_major_version,
                   IS_SRVROLEMEMBER(N'sysadmin') AS is_sysadmin,
                   IS_SRVROLEMEMBER(N'serveradmin') AS is_serveradmin,
                   IS_SRVROLEMEMBER(N'securityadmin') AS is_securityadmin,
                   IS_SRVROLEMEMBER(N'dbcreator') AS is_dbcreator,
                   IS_MEMBER(N'db_owner') AS is_db_owner,
                   HAS_PERMS_BY_NAME(DB_NAME(), N'DATABASE', N'VIEW DEFINITION') AS has_view_definition,
                   HAS_PERMS_BY_NAME(DB_NAME(), N'DATABASE', N'VIEW DATABASE STATE') AS has_view_database_state,
                   HAS_PERMS_BY_NAME(NULL, N'SERVER', N'VIEW SERVER STATE') AS has_view_server_state,
                   HAS_PERMS_BY_NAME(NULL, N'SERVER', N'VIEW SERVER PERFORMANCE STATE') AS has_view_server_performance_state,
                   HAS_PERMS_BY_NAME(DB_NAME(), N'DATABASE', N'BACKUP DATABASE') AS has_backup_database,
                   HAS_PERMS_BY_NAME(NULL, N'SERVER', N'CREATE ANY DATABASE') AS has_create_any_database,
                   HAS_PERMS_BY_NAME(DB_NAME(), N'DATABASE', N'SELECT') AS has_database_select,
                   HAS_PERMS_BY_NAME(N'msdb.dbo.backupset', N'OBJECT', N'SELECT') AS has_msdb_backupset_select,
                   HAS_PERMS_BY_NAME(N'msdb.dbo.backupmediafamily', N'OBJECT', N'SELECT') AS has_msdb_backupmediafamily_select
            """
        )
        permission_row = cursor.fetchone()
        permission_failure = cursor.last_failure
        permission_fields = (
            "product_major_version",
            "is_sysadmin",
            "is_serveradmin",
            "is_securityadmin",
            "is_dbcreator",
            "is_db_owner",
            "has_view_definition",
            "has_view_database_state",
            "has_view_server_state",
            "has_view_server_performance_state",
            "has_backup_database",
            "has_create_any_database",
            "has_database_select",
            "has_msdb_backupset_select",
            "has_msdb_backupmediafamily_select",
        )
        if permission_failure is None and permission_row is not None:
            permission_values = {
                field: _row_value(permission_row, field, index)
                for index, field in enumerate(permission_fields)
            }
            evidence["sql"]["admin_permissions"] = {
                **permission_values,
                **_evaluate_admin_permissions(permission_values),
            }
        else:
            evidence["sql"]["admin_permissions"] = {
                "status": "NOT_PROVEN",
                "reason": "effective SQL permission inspection failed",
            }
        cursor.execute(
            """
            SELECT DB_NAME() AS database_name,
                   DATABASEPROPERTYEX(DB_NAME(), 'Status') AS database_status,
                   DATABASEPROPERTYEX(DB_NAME(), 'Recovery') AS recovery_model
            """
        )
        runtime_health_evidence = _query_evidence(cursor, statement_id="admin.sql_runtime_health")
        runtime_health_row = cursor.fetchone()
        evidence["sql"]["runtime_health"] = _evaluate_runtime_health(
            runtime_health_evidence,
            runtime_health_row,
        )
        same_as_runtime = bool(effective_login and runtime_login and effective_login.casefold() == runtime_login.casefold())
        evidence["sql"]["admin_identity_gate"] = {
            "status": "NOT_PROVEN"
            if identity_failure is not None
            or not effective_login
            or not evidence["sql"]["identity"].get("server_name")
            or evidence["sql"]["identity"].get("database_name") != EXPECTED_SQL_DATABASE
            else (
                "FAILED" if same_as_runtime else "PASS"
            ),
            "effective_login": effective_login,
            "runtime_login_match": same_as_runtime,
            "reason": (
                "administrative identity matches the dedicated runtime login"
                if same_as_runtime
                else None
            ),
        }
        cursor.execute(
            """
            SELECT name, type_desc, size, growth, is_percent_growth, max_size,
                   FILEPROPERTY(name, 'SpaceUsed') AS space_used_pages
            FROM sys.database_files
            ORDER BY file_id
            """
        )
        database_files_evidence = _query_evidence(cursor, statement_id="capacity.database_files")
        database_file_rows = cursor.fetchall()
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
            for row in database_file_rows
        ]
        evidence["sql"]["database_files_evidence"] = database_files_evidence
        cursor.execute(
            """
            SELECT DISTINCT vs.volume_mount_point, vs.total_bytes, vs.available_bytes
            FROM sys.database_files AS df
            CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
            ORDER BY vs.volume_mount_point
            """
        )
        volume_evidence = _query_evidence(cursor, statement_id="capacity.volume_capacity_dm_os_volume_stats")
        volume_rows = cursor.fetchall()
        evidence["sql"]["volume_capacity"] = [
            {
                "volume": str(_row_value(row, "volume_mount_point", 0) or ""),
                "total_bytes": int(_row_value(row, "total_bytes", 1) or 0),
                "available_bytes": int(_row_value(row, "available_bytes", 2) or 0),
            }
            for row in volume_rows
        ]
        evidence["sql"]["volume_capacity_evidence"] = volume_evidence
        cursor.execute(
            """
            SELECT total_log_size_in_bytes, used_log_space_in_bytes, used_log_space_in_percent
            FROM sys.dm_db_log_space_usage
            WHERE database_id = DB_ID()
            """
        )
        log_space_evidence = _query_evidence(cursor, statement_id="capacity.database_log_space_dm_db_log_space_usage")
        log_row = cursor.fetchone()
        evidence["sql"]["log_space"] = (
            {
                "total_bytes": int(_row_value(log_row, "total_log_size_in_bytes", 0)),
                "used_bytes": int(_row_value(log_row, "used_log_space_in_bytes", 1)),
                "used_percent": float(_row_value(log_row, "used_log_space_in_percent", 2)),
            }
            if log_space_evidence.get("status") == "PASS" and log_row is not None
            else None
        )
        evidence["sql"]["log_space_evidence"] = log_space_evidence
        if permission_values is not None:
            evidence["sql"]["admin_permissions"] = {
                **permission_values,
                **_evaluate_admin_permissions(
                    permission_values,
                    query_statuses={
                        "capacity.volume_capacity_dm_os_volume_stats": volume_evidence,
                        "capacity.database_log_space_dm_db_log_space_usage": log_space_evidence,
                    },
                ),
            }
        cursor.execute(
            """
            SELECT TOP (1) bs.backup_set_id, bs.media_set_id,
                   bs.backup_finish_date, bs.is_copy_only,
                   bs.has_backup_checksums, bs.compressed_backup_size,
                   bs.database_name
            FROM msdb.dbo.backupset AS bs
            WHERE bs.database_name = DB_NAME() AND bs.type = 'D'
            ORDER BY bs.backup_finish_date DESC
            """
        )
        backup_history_evidence = _query_evidence(cursor, statement_id="backup.latest_full_backup_history")
        backup = cursor.fetchone()
        backup_media_set_id = _row_value(backup, "media_set_id", 1) if backup else None
        backup_paths: list[str] = []
        backup_media_evidence: dict[str, Any] = {
            "status": "NOT_PROVEN",
            "statement_id": "backup.latest_full_backup_media_set",
        }
        if backup_history_evidence.get("status") == "PASS" and backup_media_set_id is not None:
            cursor.execute(
                """
                SELECT family_sequence_number, physical_device_name
                FROM msdb.dbo.backupmediafamily
                WHERE media_set_id = %s
                ORDER BY family_sequence_number
                """,
                (backup_media_set_id,),
            )
            backup_media_evidence = _query_evidence(
                cursor,
                statement_id="backup.latest_full_backup_media_set",
            )
            backup_media_rows = cursor.fetchall()
            backup_paths = [
                str(_row_value(row, "physical_device_name", 1) or "")
                for row in backup_media_rows
                if str(_row_value(row, "physical_device_name", 1) or "").strip()
            ]
        evidence["sql"]["latest_full_backup"] = (
            {
                "backup_set_id": int(_row_value(backup, "backup_set_id", 0) or 0),
                "media_set_id": int(_row_value(backup, "media_set_id", 1) or 0),
                "backup_finish_date": _row_value(backup, "backup_finish_date", 2),
                "copy_only": bool(_row_value(backup, "is_copy_only", 3)),
                "has_backup_checksums": bool(_row_value(backup, "has_backup_checksums", 4)),
                "compressed_backup_size": int(_row_value(backup, "compressed_backup_size", 5) or 0),
                "media_count": len(backup_paths),
                "complete_media_set_proven": backup_media_evidence.get("status") == "PASS"
                and bool(backup_paths),
            }
            if backup_history_evidence.get("status") == "PASS" and backup and backup_media_evidence.get("status") == "PASS"
            else None
        )
        evidence["sql"]["latest_full_backup_evidence"] = backup_history_evidence
        evidence["sql"]["latest_full_backup_media_evidence"] = backup_media_evidence
        if backup_paths:
            try:
                if admin_settings is None:
                    raise RuntimeError("admin SQL settings are unavailable")
                verify_connection = connect_mssql(admin_settings)
                try:
                    access_checks = [_safe_backup_access_diagnostics(verify_connection, path) for path in backup_paths]
                    header_checks = [_safe_backup_header(verify_connection, path) for path in backup_paths]
                    evidence["sql"]["backup_server_side_access"] = {
                        "status": "PASS" if all(item.get("status") == "PASS" for item in access_checks) else "NOT_PROVEN",
                        "media_count": len(backup_paths),
                        "media": access_checks,
                    }
                    evidence["sql"]["backup_headeronly"] = {
                        "status": "PASS" if all(item.get("status") == "PASS" for item in header_checks) else "NOT_PROVEN",
                        "media_count": len(backup_paths),
                        "media": header_checks,
                    }
                    evidence["sql"]["backup_verifyonly"] = _safe_backup_verify(verify_connection, backup_paths)
                finally:
                    verify_connection.close()
            except Exception as exc:
                evidence["sql"]["backup_verifyonly"] = {
                    "status": "NOT_PROVEN",
                    "statement_id": "backup.restore_verifyonly_with_checksum",
                    "statement": "RESTORE VERIFYONLY FROM DISK = N'<backup path redacted>' WITH CHECKSUM",
                    "error_code": _sql_error_code(exc),
                    "error_type": type(exc).__name__,
                    "reason": _sanitize_sql_reason(exc),
                    "cause_classification": "UNDETERMINED",
                }
        else:
            evidence["sql"]["backup_server_side_access"] = {
                "status": "NOT_PROVEN",
                "statement_id": "backup.server_side_file_access",
                "reason": "the complete full-backup media set was not returned by msdb history",
            }
            evidence["sql"]["backup_headeronly"] = {
                "status": "NOT_PROVEN",
                "statement_id": "backup.restore_headeronly",
                "reason": "the complete full-backup media set was not returned by msdb history",
            }
            evidence["sql"]["backup_verifyonly"] = {
                "status": "NOT_PROVEN",
                "statement_id": "backup.restore_verifyonly_with_checksum",
                "statement": "RESTORE VERIFYONLY FROM DISK = N'<backup path redacted>' WITH CHECKSUM",
                "reason": "the complete full-backup media set was not returned by msdb history",
                "cause_classification": "UNDETERMINED",
            }

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
        materialization_evidence = _query_evidence(cursor, statement_id="collector.tiered50_materialization")
        targets = cursor.fetchone()
        evidence["sql"]["tiered50_materialization"] = {
            "status": materialization_evidence.get("status", "NOT_PROVEN"),
            "target_count": int(_row_value(targets, "total_count", 0) or 0) if targets else None,
            "active_count": int(_row_value(targets, "active_count", 1) or 0) if targets else None,
            "prepared_count": int(_row_value(targets, "prepared_count", 2) or 0) if targets else None,
            "minimum_id": int(_row_value(targets, "minimum_id", 3) or 0) if targets else None,
            "maximum_id": int(_row_value(targets, "maximum_id", 4) or 0) if targets else None,
        }
        evidence["sql"]["tiered50_materialization_evidence"] = materialization_evidence
        cursor.execute(
            """
            SELECT CutoverKey, PanelVersion, FinalLegacy7RunId,
                   Tiered50ActivationAtUtc, Tiered50ActivationStatus,
                   RecordedAtUtc
            FROM ctl.MddMacProductionCutover
            WHERE PanelVersion = 'TIERED50_PANEL_V1'
            ORDER BY CutoverId
            """
        )
        cutover_history_evidence = _query_evidence(cursor, statement_id="collector.production_cutover_history")
        cutover_history = [
            {
                "cutover_key": str(_row_value(row, "CutoverKey", 0) or ""),
                "panel_version": str(_row_value(row, "PanelVersion", 1) or ""),
                "final_legacy7_run_id": str(_row_value(row, "FinalLegacy7RunId", 2) or "") or None,
                "tiered50_activation_at_utc": _row_value(row, "Tiered50ActivationAtUtc", 3),
                "tiered50_activation_status": str(_row_value(row, "Tiered50ActivationStatus", 4) or ""),
                "recorded_at_utc": _row_value(row, "RecordedAtUtc", 5),
            }
            for row in cursor.fetchall()
        ]
        evidence["sql"]["production_cutover_history"] = cutover_history
        evidence["sql"]["production_cutover_history_evidence"] = cutover_history_evidence
        cursor.execute(
            """
            SELECT CollectionContext, COUNT_BIG(*) AS RunningCount
            FROM ctl.MddMacCollectorRun
            WHERE Status IN ('RUNNING', 'CLAIMED')
              AND CollectionContext IN ('MAC_LEGACY7_PRODUCTION', 'MAC_TIERED50_PRODUCTION')
            GROUP BY CollectionContext
            """
        )
        competing_writer_evidence = _query_evidence(cursor, statement_id="collector.competing_production_writers")
        competing_writers = [
            {
                "collection_context": str(_row_value(row, "CollectionContext", 0) or ""),
                "running_count": int(_row_value(row, "RunningCount", 1) or 0),
            }
            for row in cursor.fetchall()
        ]
        evidence["sql"]["competing_production_writers"] = competing_writers
        evidence["sql"]["competing_production_writers_evidence"] = competing_writer_evidence
        cursor.execute(
            """
            SELECT TOP (2) MonthKey, ProviderQuotaCeiling,
                   NormalOperationalMaximum, RetryProtectionBoundary,
                   ConfirmedProviderAttempts, ConservativeUnknownAttempts,
                   ExplicitSharedConsumerReservedAttempts, AccountingStatus,
                   IncludesAllSharedConsumers, ProviderPeriodStartUtc,
                   ProviderPeriodEndUtc, LocalUsageIncludedThroughUtc,
                   SourceEvidenceHash, ReconciliationKey, AccountingModel,
                   ProviderPeriodTimezone
            FROM ctl.MddMacSharedQuotaAccounting
            WHERE ProviderPeriodStartUtc <= SYSUTCDATETIME()
              AND ProviderPeriodEndUtc > SYSUTCDATETIME()
            ORDER BY ProviderPeriodStartUtc DESC, RecordedAtUtc DESC
            """
        )
        quota_ledger_evidence = _query_evidence(cursor, statement_id="quota.current_shared_ledger")
        quota_rows = list(cursor.fetchall())
        evidence["sql"]["current_quota_ledger"] = [
            {
                "month_key": str(_row_value(row, "MonthKey", 0) or ""),
                "provider_quota_ceiling": _row_value(row, "ProviderQuotaCeiling", 1),
                "normal_operational_maximum": _row_value(row, "NormalOperationalMaximum", 2),
                "retry_protection_boundary": _row_value(row, "RetryProtectionBoundary", 3),
                "confirmed_provider_attempts": _row_value(row, "ConfirmedProviderAttempts", 4),
                "conservative_unknown_attempts": _row_value(row, "ConservativeUnknownAttempts", 5),
                "reserved_shared_usage": _row_value(row, "ExplicitSharedConsumerReservedAttempts", 6),
                "accounting_status": str(_row_value(row, "AccountingStatus", 7) or ""),
                "includes_all_shared_consumers": bool(_row_value(row, "IncludesAllSharedConsumers", 8)),
                "provider_period_start_utc": _row_value(row, "ProviderPeriodStartUtc", 9),
                "provider_period_end_utc": _row_value(row, "ProviderPeriodEndUtc", 10),
                "local_usage_included_through_utc": _row_value(row, "LocalUsageIncludedThroughUtc", 11),
                "source_evidence_hash": str(_row_value(row, "SourceEvidenceHash", 12) or ""),
                "reconciliation_key": str(_row_value(row, "ReconciliationKey", 13) or ""),
                "accounting_model": str(_row_value(row, "AccountingModel", 14) or ""),
                "provider_period_timezone": str(_row_value(row, "ProviderPeriodTimezone", 15) or ""),
                "provider_period_metadata_verified": str(
                    _row_value(row, "AccountingModel", 14) or ""
                ) != "AUTOMATIC_MONTHLY_ROLLOVER_BOUNDED",
                "provider_period_metadata_source": (
                    "OPERATOR_APPROVED_BOUNDED_ASSUMPTION"
                    if str(_row_value(row, "AccountingModel", 14) or "")
                    == "AUTOMATIC_MONTHLY_ROLLOVER_BOUNDED"
                    else "PROVIDER_VERIFIED_EXACT_CONTRACT"
                    if str(_row_value(row, "AccountingModel", 14) or "")
                    == "AUTOMATIC_MONTHLY_ROLLOVER"
                    else "PROVIDER_VERIFIED_EXACT_PERIOD"
                ),
                "bounded_timezone_uncertainty": str(
                    _row_value(row, "AccountingModel", 14) or ""
                )
                == "AUTOMATIC_MONTHLY_ROLLOVER_BOUNDED",
            }
            for row in quota_rows
        ]
        evidence["sql"]["current_quota_ledger_evidence"] = quota_ledger_evidence
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
        legacy_audit_evidence = _query_evidence(cursor, statement_id="collector.legacy7_audit_history")
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
        evidence["sql"]["legacy7_audit_history_evidence"] = legacy_audit_evidence
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
        final_run_evidence = _query_evidence(cursor, statement_id="collector.legacy7_final_run")
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
        evidence["sql"]["final_legacy7_run_evidence"] = final_run_evidence
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
        recent_http_evidence = _query_evidence(cursor, statement_id="collector.http_attempt_history")
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
        evidence["sql"]["recent_http_attempts_evidence"] = recent_http_evidence
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
        tiered_audit_evidence = _query_evidence(cursor, statement_id="collector.historical_target_audits")
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
        evidence["sql"]["tiered50_audit_history_evidence"] = tiered_audit_evidence
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
        first_run_evidence = _query_evidence(cursor, statement_id="collector.tiered50_first_run")
        first_tiered_run = cursor.fetchone()
        first_cycle: dict[str, Any] = {
            "observed": bool(first_tiered_run),
            "validation_status": "NOT_OBSERVED" if first_run_evidence.get("status") == "PASS" else "NOT_PROVEN",
            "run_query_evidence": first_run_evidence,
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
            audit_summary_evidence = _query_evidence(cursor, statement_id="collector.tiered50_first_run_audits")
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
            attempt_summary_evidence = _query_evidence(cursor, statement_id="collector.tiered50_first_run_attempts")
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
            permit_summary_evidence = _query_evidence(cursor, statement_id="collector.tiered50_first_run_permits")
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
            persistence_summary_evidence = _query_evidence(
                cursor, statement_id="collector.tiered50_first_run_persistence"
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
                "target_audits": int(_row_value(audit_summary, "target_audits", 0) or 0)
                if audit_summary_evidence.get("status") == "PASS" and audit_summary is not None
                else None,
                "successful_target_audits": int(_row_value(audit_summary, "succeeded", 1) or 0)
                if audit_summary_evidence.get("status") == "PASS" and audit_summary is not None
                else None,
                "skipped_target_audits": int(_row_value(audit_summary, "skipped", 2) or 0)
                if audit_summary_evidence.get("status") == "PASS" and audit_summary is not None
                else None,
                "failed_target_audits": int(_row_value(audit_summary, "failed", 3) or 0)
                if audit_summary_evidence.get("status") == "PASS" and audit_summary is not None
                else None,
                "persisted_target_audits": int(_row_value(audit_summary, "persisted", 5) or 0)
                if audit_summary_evidence.get("status") == "PASS" and audit_summary is not None
                else None,
                "audit_error_count": int(_row_value(audit_summary, "errors", 6) or 0)
                if audit_summary_evidence.get("status") == "PASS" and audit_summary is not None
                else None,
                "http_attempts": int(_row_value(attempt_summary, "attempts", 0) or 0)
                if attempt_summary_evidence.get("status") == "PASS" and attempt_summary is not None
                else None,
                "network_attempts": int(_row_value(attempt_summary, "network_attempts", 1) or 0)
                if attempt_summary_evidence.get("status") == "PASS" and attempt_summary is not None
                else None,
                "retry_attempts": int(_row_value(attempt_summary, "retries", 2) or 0)
                if attempt_summary_evidence.get("status") == "PASS" and attempt_summary is not None
                else None,
                "http_responses": int(_row_value(attempt_summary, "responses", 3) or 0)
                if attempt_summary_evidence.get("status") == "PASS" and attempt_summary is not None
                else None,
                "transport_error_attempts": int(_row_value(attempt_summary, "transport_errors", 4) or 0)
                if attempt_summary_evidence.get("status") == "PASS" and attempt_summary is not None
                else None,
                "attempts_without_permit": int(_row_value(attempt_summary, "attempts_without_permit", 5) or 0)
                if attempt_summary_evidence.get("status") == "PASS" and attempt_summary is not None
                else None,
                "quota_permits": int(_row_value(permit_summary, "quota_permits", 0) or 0)
                if permit_summary_evidence.get("status") == "PASS" and permit_summary is not None
                else None,
                "permitted_amount": int(_row_value(permit_summary, "permitted_amount", 1) or 0)
                if permit_summary_evidence.get("status") == "PASS" and permit_summary is not None
                else None,
                "initial_permits": int(_row_value(permit_summary, "initial_permits", 2) or 0)
                if permit_summary_evidence.get("status") == "PASS" and permit_summary is not None
                else None,
                "retry_permits": int(_row_value(permit_summary, "retry_permits", 3) or 0)
                if permit_summary_evidence.get("status") == "PASS" and permit_summary is not None
                else None,
                "confirmed_permits": int(_row_value(permit_summary, "confirmed_permits", 4) or 0)
                if permit_summary_evidence.get("status") == "PASS" and permit_summary is not None
                else None,
                "uncertain_permits": int(_row_value(permit_summary, "uncertain_permits", 5) or 0)
                if permit_summary_evidence.get("status") == "PASS" and permit_summary is not None
                else None,
                "persistence_outcomes": persistence_summary,
                "query_evidence": {
                    "audits": audit_summary_evidence,
                    "attempts": attempt_summary_evidence,
                    "permits": permit_summary_evidence,
                    "persistence": persistence_summary_evidence,
                },
                "coverage": summary.get("coverage"),
                "timing": summary.get("timing"),
                "validation_status": (
                    "NOT_PROVEN"
                    if any(
                        item.get("status") != "PASS"
                        for item in (
                            audit_summary_evidence,
                            attempt_summary_evidence,
                            permit_summary_evidence,
                            persistence_summary_evidence,
                        )
                    )
                    else "PASS"
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
        cutover_evidence = _query_evidence(cursor, statement_id="collector.latest_cutover")
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
        evidence["sql"]["latest_cutover_evidence"] = cutover_evidence
        cursor.execute(
            """
            SELECT TOP (20) ResumeEventId, ResumeKey, PanelVersion, EraCode,
                   SamplingEraId, OriginalActivationAtUtc, ResumeAtUtc,
                   LaunchAgentLabel, RecordedAtUtc
            FROM ctl.MddMacProductionResumeEvent
            WHERE PanelVersion = 'TIERED50_PANEL_V1'
            ORDER BY ResumeEventId DESC
            """
        )
        resume_evidence = _query_evidence(cursor, statement_id="collector.tiered50_resume_history")
        resume_rows = list(cursor.fetchall())
        evidence["sql"]["tiered50_resume_history"] = [
            {
                "resume_event_id": int(_row_value(row, "ResumeEventId", 0) or 0),
                "resume_key": str(_row_value(row, "ResumeKey", 1) or ""),
                "panel_version": str(_row_value(row, "PanelVersion", 2) or ""),
                "era_code": str(_row_value(row, "EraCode", 3) or ""),
                "sampling_era_id": int(_row_value(row, "SamplingEraId", 4) or 0),
                "original_activation_at_utc": _row_value(row, "OriginalActivationAtUtc", 5),
                "resume_at_utc": _row_value(row, "ResumeAtUtc", 6),
                "launchagent_label": str(_row_value(row, "LaunchAgentLabel", 7) or ""),
                "recorded_at_utc": _row_value(row, "RecordedAtUtc", 8),
            }
            for row in resume_rows
        ]
        evidence["sql"]["tiered50_resume_history_evidence"] = resume_evidence
        evidence["sql"]["latest_tiered50_resume"] = (
            evidence["sql"]["tiered50_resume_history"][0]
            if evidence["sql"]["tiered50_resume_history"]
            else None
        )
        cursor.execute(
            """
            SELECT TOP (1) RunId, LogicalDueBucket, StartedAtUtc,
                   CompletedAtUtc, Status, SummaryJson
            FROM ctl.MddMacCollectorRun
            WHERE CollectionContext = 'MAC_TIERED50_PRODUCTION'
            ORDER BY MacCollectorRunId DESC
            """
        )
        latest_run_evidence = _query_evidence(cursor, statement_id="collector.tiered50_latest_run")
        latest_run = cursor.fetchone()
        latest_summary: dict[str, Any] = {}
        if latest_run:
            try:
                decoded = json.loads(str(_row_value(latest_run, "SummaryJson", 5) or "{}"))
                if isinstance(decoded, dict):
                    latest_summary = decoded
            except (TypeError, ValueError, json.JSONDecodeError):
                latest_summary = {}
            cursor.execute(
                """
                SELECT COUNT_BIG(*) AS target_audits,
                       SUM(CASE WHEN Status = 'SUCCEEDED' THEN 1 ELSE 0 END) AS succeeded,
                       SUM(CASE WHEN Status = 'SKIPPED' THEN 1 ELSE 0 END) AS skipped,
                       SUM(CASE WHEN Status = 'FAILED' THEN 1 ELSE 0 END) AS failed
                FROM ctl.MddMacTargetAudit
                WHERE RunId = %s
                """,
                (_row_value(latest_run, "RunId", 0),),
            )
            latest_audit_evidence = _query_evidence(cursor, statement_id="collector.tiered50_latest_run_audits")
            latest_audit = cursor.fetchone()
        else:
            latest_audit_evidence = {"status": "NOT_PROVEN", "reason": "no Tiered50 run exists"}
            latest_audit = None
        evidence["sql"]["latest_tiered50_run"] = {
            "run_id": str(_row_value(latest_run, "RunId", 0) or "") if latest_run else None,
            "logical_due_bucket": int(_row_value(latest_run, "LogicalDueBucket", 1) or 0) if latest_run else None,
            "started_at_utc": _row_value(latest_run, "StartedAtUtc", 2) if latest_run else None,
            "completed_at_utc": _row_value(latest_run, "CompletedAtUtc", 3) if latest_run else None,
            "run_status": str(_row_value(latest_run, "Status", 4) or "") if latest_run else None,
            "target_audits": int(_row_value(latest_audit, "target_audits", 0) or 0) if latest_audit else None,
            "successful_target_audits": int(_row_value(latest_audit, "succeeded", 1) or 0) if latest_audit else None,
            "skipped_target_audits": int(_row_value(latest_audit, "skipped", 2) or 0) if latest_audit else None,
            "failed_target_audits": int(_row_value(latest_audit, "failed", 3) or 0) if latest_audit else None,
            "query_evidence": {
                "run": latest_run_evidence,
                "audits": latest_audit_evidence,
            },
            "coverage": latest_summary.get("coverage"),
        }
    finally:
        connection.close()
    external_provenance: Mapping[str, Any] | None = None
    if external_admin is not None:
        local_sql = evidence["sql"]
        external_artifact = external_admin["artifact"]
        external_sql = dict(external_artifact.get("sql") or {})
        runtime_fields = {
            "runtime_role_source",
            "runtime_connection",
            "runtime_login",
            "runtime_schema",
            "tiered50_schema",
            "runtime_permission_matrix",
            "quota_usage",
        }
        merged_sql = dict(external_sql)
        for field in runtime_fields:
            if field in local_sql:
                merged_sql[field] = local_sql[field]
        merged_sql["admin_role_source"] = "SIGNED_EXTERNAL_ADMIN_EVIDENCE"
        merged_sql["privileged_query_failures"] = []
        merged_sql["permission_error_297"] = []
        evidence["sql"] = merged_sql
        external_provenance = external_artifact.get("capture_provenance")
        evidence["external_admin_evidence"] = {
            "artifact_version": external_artifact.get("artifact_version"),
            "payload_sha256": (external_artifact.get("artifact_integrity") or {}).get("payload_sha256"),
            "signature_verified": bool(external_admin["validation"].get("signature_verified")),
            "source_path": str(Path(admin_evidence_path).expanduser().resolve()),
        }
    privileged_failures = evidence["sql"]["privileged_query_failures"]
    evidence["sql"]["permission_error_297"] = [
        failure for failure in privileged_failures if failure.get("error_code") == 297
    ]
    volume_rows = evidence["sql"].get("volume_capacity") or []
    volume_failure = any(
        failure.get("statement_id") == "capacity.volume_capacity_dm_os_volume_stats"
        for failure in privileged_failures
    )
    log_space = evidence["sql"].get("log_space")
    log_space_evidence = evidence["sql"].get("log_space_evidence") or {}
    log_space_ok = bool(
        log_space_evidence.get("status") == "PASS"
        and isinstance(log_space, dict)
        and int(log_space.get("total_bytes", 0) or 0) > 0
        and 0 <= float(log_space.get("used_percent", -1) or -1) <= 100
    )
    evidence["sql"]["log_space_gate"] = {
        "status": "PASS" if log_space_ok else "NOT_PROVEN",
        "reason": None if log_space_ok else "log-space DMV evidence is unavailable or invalid",
        "required_statement_id": "capacity.database_log_space_dm_db_log_space_usage",
    }
    volume_ok = bool(
        volume_rows
        and not volume_failure
        and evidence["sql"].get("volume_capacity_evidence", {}).get("status") == "PASS"
        and all(int(row.get("available_bytes", 0) or 0) > 0 for row in volume_rows if isinstance(row, dict))
    )
    evidence["sql"]["capacity_gate"] = {
        "status": "PASS" if volume_ok and log_space_ok else "NOT_PROVEN",
        "reason": None
        if volume_ok and log_space_ok
        else "volume and log-space capacity evidence is incomplete or invalid",
        "required_statement_ids": [
            "capacity.volume_capacity_dm_os_volume_stats",
            "capacity.database_log_space_dm_db_log_space_usage",
        ],
    }
    backup_verify = evidence["sql"].get("backup_verifyonly") or {}
    if backup_verify.get("status") != "PASS" and backup_verify.get("error_code") is not None:
        evidence["sql"]["privileged_query_failures"].append(
            {
                "statement_id": backup_verify.get("statement_id", "backup.restore_verifyonly_with_checksum"),
                "phase": "execute",
                "statement": backup_verify.get("statement"),
                "error_code": backup_verify.get("error_code"),
                "error_type": backup_verify.get("error_type"),
                "reason": backup_verify.get("reason"),
                "status": "NOT_PROVEN",
            }
        )
    evidence["sql"]["permission_error_297"] = [
        failure
        for failure in evidence["sql"]["privileged_query_failures"]
        if failure.get("error_code") == 297
    ]
    evidence["sql"]["backup_gate"] = {
        "status": "PASS" if backup_verify.get("status") == "PASS" else "NOT_PROVEN",
        "reason": "backup verification did not complete successfully"
        if backup_verify.get("status") != "PASS"
        else None,
        "required_statement_id": "backup.restore_verifyonly_with_checksum",
    }
    identity_gate = evidence["sql"].get("admin_identity_gate") or {}
    permissions = evidence["sql"].get("admin_permissions") or {}
    required_query_statuses = (
        evidence["sql"].get("runtime_health", {}).get("status"),
        evidence["sql"].get("database_files_evidence", {}).get("status"),
        evidence["sql"].get("volume_capacity_evidence", {}).get("status"),
        evidence["sql"].get("log_space_evidence", {}).get("status"),
        evidence["sql"].get("latest_full_backup_evidence", {}).get("status"),
        evidence["sql"].get("recent_http_attempts_evidence", {}).get("status"),
        evidence["sql"].get("tiered50_audit_history_evidence", {}).get("status"),
        evidence["sql"].get("production_cutover_history_evidence", {}).get("status"),
        evidence["sql"].get("competing_production_writers_evidence", {}).get("status"),
        evidence["sql"].get("current_quota_ledger_evidence", {}).get("status"),
    )
    admin_authorized = (
        evidence["sql"].get("identity", {}).get("status") == "PASS"
        and identity_gate.get("status") == "PASS"
        and permissions.get("status") == "PASS"
        and all(status == "PASS" for status in required_query_statuses)
        and evidence["sql"].get("capacity_gate", {}).get("status") == "PASS"
        and evidence["sql"].get("backup_gate", {}).get("status") == "PASS"
    )
    evidence["sql"]["admin_authorization"] = {
        "status": "PASS" if admin_authorized else "NOT_PROVEN",
        "required_query_statuses": {
            "runtime_health": required_query_statuses[0],
            "database_files": required_query_statuses[1],
            "volume_capacity": required_query_statuses[2],
            "log_space": required_query_statuses[3],
            "latest_full_backup_history": required_query_statuses[4],
            "operational_history": required_query_statuses[5],
            "tiered50_audit_history": required_query_statuses[6],
            "production_cutover_history": required_query_statuses[7],
            "competing_production_writers": required_query_statuses[8],
            "current_quota_ledger": required_query_statuses[9],
        },
        "reason": None
        if admin_authorized
        else "one or more administrative identity, permission, capacity, backup, or operational-history checks is not proven",
    }
    admin_connection = evidence["sql"].get("admin_connection")
    if isinstance(admin_connection, dict):
        admin_connection["authorization_status"] = evidence["sql"]["admin_authorization"]["status"]
        admin_connection["status"] = "PASS" if admin_authorized else "NOT_PROVEN"
        if not admin_authorized:
            admin_connection.setdefault("blocker_code", MISSING_ADMIN_CREDENTIALS_OR_PERMISSIONS)

    runtime_permission_matrix = evidence["sql"].get("runtime_permission_matrix")
    evidence["sql"]["runtime_permission_gate"] = {
        "status": "PASS"
        if isinstance(runtime_permission_matrix, Mapping)
        and runtime_permission_matrix.get("status") == "PASS"
        else "NOT_PROVEN",
        "required_permission_count": len(
            runtime_permission_matrix.get("positive_permissions", [])
            if isinstance(runtime_permission_matrix, Mapping)
            else []
        ),
        "reason": None
        if isinstance(runtime_permission_matrix, Mapping)
        and runtime_permission_matrix.get("status") == "PASS"
        else "actual runtime permission matrix is incomplete or failing",
    }

    identity = evidence["sql"].get("identity") or {}
    identity_database = str(identity.get("database_name") or "") if isinstance(identity, dict) else ""
    identity_login = str(identity.get("login_name") or "") if isinstance(identity, dict) else ""
    identity_server = str(identity.get("server_name") or "") if isinstance(identity, dict) else ""
    identity_captured_at = identity.get("captured_at_utc") if isinstance(identity, dict) else None
    identity_instance = identity.get("sql_instance_identity") if isinstance(identity, dict) else None
    provenance_ok = bool(
        evidence["sql"].get("admin_authorization", {}).get("status") == "PASS"
        and identity.get("status") == "PASS"
        and identity_database == EXPECTED_SQL_DATABASE
        and identity_login
        and identity_server
        and isinstance(identity_instance, Mapping)
        and identity_instance.get("server_name")
        and identity_captured_at
    )
    existing_quota_integrity = evidence["sql"].get("quota_integrity")
    if isinstance(existing_quota_integrity, Mapping) and existing_quota_integrity.get("status") == "PASS":
        evidence["sql"]["quota_integrity"] = {
            **dict(existing_quota_integrity),
            "evidence_role": "SIGNED_ADMIN_EVIDENCE",
        }
    else:
        evidence["sql"]["quota_integrity"] = {
            **(
                dict(evidence["sql"].get("quota_usage") or {})
                if isinstance(evidence["sql"].get("quota_usage"), dict)
                else {}
            ),
            "status": "PASS"
            if isinstance(evidence["sql"].get("quota_usage"), dict)
            and bool(evidence["sql"]["quota_usage"].get("state_available"))
            and int(evidence["sql"]["quota_usage"].get("reserved_shared_usage", 0) or 0) == 0
            else "NOT_PROVEN",
            "evidence_role": "SQL_LEDGER_REFRESH",
        }
    evidence["capture_provenance"] = {
        "status": "PASS" if provenance_ok else "NOT_PROVEN",
        "read_only": True,
        "execution_path": (
            str(external_provenance.get("execution_path"))
            if provenance_ok and isinstance(external_provenance, Mapping)
            else "SEPARATE_SQL_LOGIN"
            if provenance_ok
            else "NOT_PROVEN"
        ),
        "server_name": identity_server,
        "database_name": identity_database,
        "sql_instance_identity": dict(identity_instance) if isinstance(identity_instance, Mapping) else None,
        "effective_login": identity_login,
        "captured_at_utc": identity_captured_at or evidence["generated_at_utc"],
        "required_check_ids": sorted(
            set(REQUIRED_CHECK_IDS)
            | (
                set(external_provenance.get("required_check_ids") or ())
                if provenance_ok and isinstance(external_provenance, Mapping)
                else set()
            )
        ),
        "authentication_source": (
            external_provenance.get("authentication_source")
            if isinstance(external_provenance, Mapping)
            else sql_evidence.get("admin_role_source")
        ),
        "tls": (
            dict(external_provenance.get("tls") or {})
            if isinstance(external_provenance, Mapping)
            else {"status": "NOT_APPLICABLE", "driver": "pymssql"}
        ),
        "windows_integrated_auth_supported": True,
    }
    latest_cutover_evidence = evidence["sql"].get("latest_cutover_evidence") or {}
    latest_cutover = evidence["sql"].get("latest_cutover_record")
    if not isinstance(latest_cutover_evidence, Mapping) or latest_cutover_evidence.get("status") != "PASS":
        latest_cutover = None
    elif not isinstance(latest_cutover, Mapping):
        latest_cutover = {}
    tiered_plist = evidence["host"].get("tiered50_plist", {})
    current_status = derive_current_status(
        scheduler=evidence["host"].get("tiered50_launchd", {}),
        plist=tiered_plist if isinstance(tiered_plist, dict) else {},
        materialization=evidence["sql"].get("tiered50_materialization", {}),
        latest_cutover=latest_cutover if isinstance(latest_cutover, dict) else None,
        latest_resume=evidence["sql"].get("latest_tiered50_resume"),
        latest_tiered_run=evidence["sql"].get("latest_tiered50_run"),
        six_cycle=evidence["sql"].get("tiered50_six_bucket_cycle"),
    )
    evidence["current_status"] = current_status
    evidence["status"] = current_status["status"]
    # Normalize SQL driver's naive datetime2 values before signing and writing.
    # SQL datetime2 fields in this schema are UTC by contract; json_safe assigns
    # UTC rather than allowing the Mac host timezone to reinterpret them.
    evidence = json_safe(evidence)
    # Sign only after every signed field, including current_status and status,
    # has been finalized.  Resume authenticates this exact stable artifact.
    if signing_key_path is not None:
        public_key_path = os.environ.get("MSSQL_ADMIN_EVIDENCE_PUBLIC_KEY")
        if not public_key_path:
            raise AdminEvidenceError(
                "MSSQL_ADMIN_EVIDENCE_PUBLIC_KEY must be explicitly configured when signing final evidence"
            )
        evidence["artifact_integrity"] = sign_payload(evidence, signing_key_path=signing_key_path)
        if not verify_signature(
            evidence,
            public_key_path=public_key_path,
            integrity=evidence["artifact_integrity"],
        ):
            raise AdminEvidenceError("newly signed administrative evidence could not be independently verified")
    else:
        evidence["artifact_integrity"] = {
            "status": "UNSIGNED",
            "payload_sha256": payload_sha256(evidence),
            "reason": "operator-controlled signing key was not supplied",
        }
    return evidence


def main(argv: list[str] | None = None) -> int:
    output = OPERATIONAL_EVIDENCE_PATH
    signing_key: Path | None = None
    admin_evidence: Path | None = None
    if argv:
        # Keep the historical positional output contract while allowing a
        # secure, operator-supplied signing key path for the V3 envelope.
        remaining: list[str] = []
        index = 0
        while index < len(argv):
            item = argv[index]
            if item == "--signing-key" and index + 1 < len(argv):
                signing_key = Path(argv[index + 1]).expanduser().resolve()
                index += 2
                continue
            if item == "--admin-evidence" and index + 1 < len(argv):
                admin_evidence = Path(argv[index + 1]).expanduser().resolve()
                index += 2
                continue
            remaining.append(item)
            index += 1
        output = (
            Path(remaining[0]).expanduser()
            if remaining
            else OPERATIONAL_EVIDENCE_PATH
        )
    try:
        from cologne_transit_collector.admin_evidence import require_current_evidence_path

        output = require_current_evidence_path(output, repository_root=ROOT)
        evidence = collect(signing_key_path=signing_key, admin_evidence_path=admin_evidence)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        output.chmod(0o600)
        print(json.dumps({"status": "PASS", "output": str(output), "mdd_requests_issued": 0}, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error_type": type(exc).__name__}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
