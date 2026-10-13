#!/usr/bin/env python3
"""Apply the additive compact-storage SQL migration through the admin path.

The SQL file itself remains a reviewable, fail-closed source artifact.  This
runner is the only supported apply path: without ``--apply`` it performs only
static validation and never opens a database connection.  With ``--apply`` it
replaces the source file's explicit no-op marker, executes every batch in the
DB-API transaction, drains procedure result sets, and validates the installed
object contract before committing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlPersistence,
    connect_mssql,
)
from cologne_transit_collector.compact_observation import (  # noqa: E402
    local_collector_safety_gate,
    local_storage_operation_gate,
)
from cologne_transit_collector.runtime_configuration import (  # noqa: E402
    MacOSKeychainSecretProvider,
    resolve_administrator_connection_settings,
)
from reclaim_sql_datafile_space import _backup_gate  # noqa: E402
from validate_mssql_migration import split_batches  # noqa: E402


MIGRATION = ROOT / "sql" / "mdd-mac-collector" / "compact-observation-storage-v1.sql"
APPLY_MARKER = "DECLARE @ApplyCompactStorage BIT = 0;"
REQUIRED_MARKERS = (
    "stg.MddMacCompactStorageState",
    "stg.MddMacCompactObservationEnvelope",
    "stg.uspMddMacCompactStoreHistoricalEnvelope",
    "stg.uspMddMacCompactMarkUnit",
    "stg.uspPersistMddMacSnapshotCompact",
)
SAFETY_FLOOR_GIB = 12.0
MAX_LOG_USED_PERCENT = 70.0


def _collector_gate() -> dict[str, Any]:
    return local_collector_safety_gate()


def _drain(cursor: Any) -> None:
    fetchall = getattr(cursor, "fetchall", None)
    if callable(fetchall):
        try:
            fetchall()
        except Exception:
            pass
    nextset = getattr(cursor, "nextset", None)
    if callable(nextset):
        while True:
            try:
                has_next = nextset()
            except Exception:
                break
            if not has_next:
                break
            if callable(fetchall):
                try:
                    fetchall()
                except Exception:
                    pass


def _row_value(row: Any, name: str, index: int = 0) -> Any:
    if isinstance(row, Mapping):
        lowered = {str(key).casefold(): value for key, value in row.items()}
        return lowered.get(name.casefold())
    if row is None:
        return None
    try:
        return row[index]
    except (IndexError, KeyError, TypeError):
        return None


def _production_preflight(cursor: Any) -> dict[str, Any]:
    cursor.execute(
        """
        SELECT DB_NAME() AS database_name,
               CAST(SERVERPROPERTY('ServerName') AS NVARCHAR(128)) AS server_name,
               CAST(SERVERPROPERTY('InstanceName') AS NVARCHAR(128)) AS instance_name,
               DATABASEPROPERTYEX(DB_NAME(), 'Status') AS database_status,
               DATABASEPROPERTYEX(DB_NAME(), 'Recovery') AS recovery_model
        """
    )
    identity = cursor.fetchone()
    cursor.execute(
        """
        SELECT df.file_id, df.name, df.type_desc,
               df.size * 8192 AS allocated_bytes,
               FILEPROPERTY(df.name, 'SpaceUsed') * 8192 AS used_bytes,
               vs.volume_mount_point, vs.available_bytes
        FROM sys.database_files AS df
        CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
        ORDER BY df.file_id
        """
    )
    files = []
    for row in cursor.fetchall():
        raw_values = (
            _row_value(row, "file_id", 0),
            _row_value(row, "name", 1),
            _row_value(row, "type_desc", 2),
            _row_value(row, "allocated_bytes", 3),
            _row_value(row, "used_bytes", 4),
            _row_value(row, "volume_mount_point", 5),
            _row_value(row, "available_bytes", 6),
        )
        if any(value is None for value in raw_values):
            raise RuntimeError("SQL file-space measurement is incomplete")
        allocated = int(raw_values[3])
        used = int(raw_values[4])
        available = int(raw_values[6])
        files.append(
            {
                "file_id": int(raw_values[0]),
                "name": str(raw_values[1]),
                "type": str(raw_values[2]),
                "allocated_bytes": allocated,
                "used_bytes": used,
                "internal_free_bytes": max(0, allocated - used),
                "volume": str(raw_values[5]),
                "available_bytes": available,
                "windows_free_gib": available / (1024**3),
            }
        )
    cursor.execute(
        """
        SELECT total_log_size_in_bytes, used_log_space_in_bytes,
               used_log_space_in_percent
        FROM sys.dm_db_log_space_usage
        WHERE database_id = DB_ID()
        """
    )
    log = cursor.fetchone()
    cursor.execute(
        """
        SELECT COUNT_BIG(*) AS active_runs
        FROM ctl.MddMacCollectorRun
        WHERE Status IN ('RUNNING', 'CLAIMED')
          AND CollectionContext IN ('MAC_LEGACY7_PRODUCTION', 'MAC_TIERED50_PRODUCTION')
        """
    )
    active_runs = int(_row_value(cursor.fetchone(), "active_runs", 0) or 0)
    cursor.execute(
        """
        SELECT COUNT_BIG(*) AS active_leases
        FROM ctl.MddMacProductionLease
        WHERE LeaseName = 'MDD_MAC_PRODUCTION' AND Status = 'ACTIVE'
        """
    )
    active_leases = int(_row_value(cursor.fetchone(), "active_leases", 0) or 0)
    cursor.execute(
        """
        SELECT COUNT_BIG(*) AS active_user_transactions
        FROM sys.dm_tran_session_transactions AS st
        INNER JOIN sys.dm_exec_sessions AS es ON es.session_id = st.session_id
        WHERE st.session_id <> @@SPID AND es.is_user_process = 1
        """
    )
    active_user_transactions = int(_row_value(cursor.fetchone(), "active_user_transactions", 0) or 0)
    data_files = [item for item in files if item["type"].casefold() == "rows"]
    c_files = [item for item in data_files if item["volume"].casefold().startswith("c:")]
    free_gib = min((item["windows_free_gib"] for item in c_files), default=0.0)
    log_used = float(_row_value(log, "used_log_space_in_percent", 2) or 100.0)
    database_name = str(_row_value(identity, "database_name", 0) or "")
    database_status = str(_row_value(identity, "database_status", 3) or "")
    status = (
        "PASS"
        if database_name == "CologneTransitIntelligence"
        and database_status.casefold() == "online"
        and c_files
        and free_gib >= SAFETY_FLOOR_GIB
        and log_used <= MAX_LOG_USED_PERCENT
        and active_runs == 0
        and active_leases == 0
        and active_user_transactions == 0
        else "BLOCKED"
    )
    return {
        "status": status,
        "database_name": database_name,
        "server_name": str(_row_value(identity, "server_name", 1) or ""),
        "instance_name": str(_row_value(identity, "instance_name", 2) or ""),
        "database_status": database_status,
        "recovery_model": str(_row_value(identity, "recovery_model", 4) or ""),
        "data_files": data_files,
        "windows_free_gib": free_gib,
        "required_windows_free_gib": SAFETY_FLOOR_GIB,
        "log": {
            "total_bytes": int(_row_value(log, "total_log_size_in_bytes", 0) or 0),
            "used_bytes": int(_row_value(log, "used_log_space_in_bytes", 1) or 0),
            "used_percent": log_used,
            "max_used_percent": MAX_LOG_USED_PERCENT,
        },
        "active_runs": active_runs,
        "active_leases": active_leases,
        "active_user_transactions": active_user_transactions,
    }


def _source_text() -> str:
    text = MIGRATION.read_text(encoding="utf-8")
    missing = [marker for marker in REQUIRED_MARKERS if marker not in text]
    if APPLY_MARKER not in text:
        raise RuntimeError("compact migration is missing its fail-closed apply marker")
    if missing:
        raise RuntimeError("compact migration is missing required markers: " + ", ".join(missing))
    return text


def _batches(*, apply: bool) -> list[str]:
    text = _source_text()
    if not apply:
        return split_batches(text)
    return split_batches(text.replace(APPLY_MARKER, "DECLARE @ApplyCompactStorage BIT = 1;", 1))


def _static_result() -> dict[str, Any]:
    text = _source_text()
    return {
        "status": "PASS",
        "migration": str(MIGRATION.relative_to(ROOT)),
        "fail_closed_default": APPLY_MARKER in text,
        "required_markers": list(REQUIRED_MARKERS),
        "batch_count": len(_batches(apply=False)),
    }


def _apply(backup_evidence: Path) -> dict[str, Any]:
    backup_gate = _backup_gate(backup_evidence)
    if backup_gate.get("status") != "PASS":
        raise RuntimeError("fresh checksum-verified off-drive backup gate is not proven")
    collector_gate = _collector_gate()
    if collector_gate.get("status") != "PASS":
        raise RuntimeError("collector stopped/disabled gate is not proven")
    operation_gate = local_storage_operation_gate()
    if operation_gate.get("status") != "PASS":
        raise RuntimeError("another compact-storage operation is active")
    settings, role_source = resolve_administrator_connection_settings(
        os.environ,
        secret_provider=MacOSKeychainSecretProvider(),
    )
    adapter = MssqlPersistence(settings)
    connection = connect_mssql(settings)
    executed = 0
    try:
        cursor = connection.cursor()
        preflight = _production_preflight(cursor)
        if preflight.get("status") != "PASS":
            raise RuntimeError("SQL identity, active-writer, log, or disk-capacity preflight failed")
        cursor.execute("SET XACT_ABORT ON;")
        _drain(cursor)
        for batch in _batches(apply=True):
            cursor.execute(batch)
            _drain(cursor)
            executed += 1
        connection.commit()
    except BaseException:
        try:
            connection.rollback()
        finally:
            connection.close()
        raise
    connection.close()
    schema = adapter.validate_compact_storage_schema()
    return {
        "status": "PASS",
        "backup_gate": backup_gate,
        "collector_gate": collector_gate,
        "storage_operation_gate": operation_gate,
        "production_preflight": preflight,
        "admin_role_source": role_source,
        "executed_batches": executed,
        "schema": schema,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="execute the additive migration")
    parser.add_argument(
        "--backup-evidence",
        type=Path,
        help="fresh off-C COPY_ONLY/CHECKSUM/VERIFYONLY evidence required for --apply",
    )
    args = parser.parse_args(argv)
    try:
        if args.apply and args.backup_evidence is None:
            raise RuntimeError("--backup-evidence is required for additive production apply")
        result = _apply(args.backup_evidence) if args.apply else _static_result()
        if not args.apply:
            result["collector_gate"] = _collector_gate()
    except Exception as exc:
        result = {"status": "BLOCKED", "error_type": type(exc).__name__, "error": str(exc)[:800]}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
