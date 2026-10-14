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
    resolve_isolated_lab_connection_settings,
)
from reclaim_sql_datafile_space import _backup_gate  # noqa: E402
from validate_mssql_migration import split_batches  # noqa: E402
from migrate_historical_compact_observations import (  # noqa: E402
    ISOLATED_LAB_EXECUTION_ENVIRONMENT,
    PRODUCTION_EXECUTION_ENVIRONMENT,
    _live_capacity_preflight,
    _load_capacity_plan,
)


MIGRATION = ROOT / "sql" / "mdd-mac-collector" / "compact-observation-storage-v1.sql"
APPLY_MARKER = "DECLARE @ApplyCompactStorage BIT = 0;"
REQUIRED_MARKERS = (
    "stg.MddMacCompactStorageState",
    "stg.MddMacCompactObservationEnvelope",
    "stg.uspMddMacCompactStoreHistoricalEnvelope",
    "stg.uspMddMacCompactMarkUnit",
    "stg.uspPersistMddMacSnapshotCompact",
    "stg.vMddMacCompactPhysicalFieldReadback",
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


def _production_preflight(
    cursor: Any,
    *,
    execution_environment: str = PRODUCTION_EXECUTION_ENVIRONMENT,
    capacity_plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
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
               CONVERT(BIGINT, df.size) * CONVERT(BIGINT, 8192) AS allocated_bytes,
               CONVERT(BIGINT, FILEPROPERTY(df.name, 'SpaceUsed'))
                   * CONVERT(BIGINT, 8192) AS used_bytes,
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
        if any(value is None for index, value in enumerate(raw_values) if index != 4) or (
            raw_values[4] is None and str(raw_values[2]).casefold() != "log"
        ):
            raise RuntimeError("SQL file-space measurement is incomplete")
        allocated = int(raw_values[3])
        used = 0 if raw_values[4] is None else int(raw_values[4])
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
        SELECT df.file_id, df.name, df.type_desc,
               CONVERT(BIGINT, df.size) * CONVERT(BIGINT, 8192) AS allocated_bytes,
               CONVERT(BIGINT, fsu.allocated_extent_page_count)
                   * CONVERT(BIGINT, 8192) AS used_bytes,
               vs.volume_mount_point, vs.available_bytes
        FROM tempdb.sys.database_files AS df
        LEFT JOIN tempdb.sys.dm_db_file_space_usage AS fsu
          ON fsu.file_id = df.file_id
        CROSS APPLY sys.dm_os_volume_stats(DB_ID(N'tempdb'), df.file_id) AS vs
        ORDER BY df.file_id
        """
    )
    tempdb_files = []
    for row in cursor.fetchall():
        tempdb_values = (
            _row_value(row, "file_id", 0),
            _row_value(row, "name", 1),
            _row_value(row, "type_desc", 2),
            _row_value(row, "allocated_bytes", 3),
            _row_value(row, "used_bytes", 4),
            _row_value(row, "volume_mount_point", 5),
            _row_value(row, "available_bytes", 6),
        )
        if any(value is None for value in (tempdb_values[0], tempdb_values[1], tempdb_values[2], tempdb_values[3], tempdb_values[5], tempdb_values[6])) or (
            tempdb_values[4] is None and str(tempdb_values[2]).casefold() != "log"
        ):
            raise RuntimeError("SQL tempdb-space measurement is incomplete")
        tempdb_files.append(
            {
                "file_id": int(tempdb_values[0]),
                "name": str(tempdb_values[1]),
                "type": str(tempdb_values[2]),
                "allocated_bytes": int(tempdb_values[3]),
                "used_bytes": int(tempdb_values[4] or 0),
                "volume": str(tempdb_values[5]),
                "available_bytes": int(tempdb_values[6]),
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
    transaction_row = cursor.fetchone()
    if transaction_row is None or _row_value(transaction_row, "active_user_transactions", 0) is None:
        raise RuntimeError("SQL active-transaction measurement is incomplete")
    active_user_transactions = int(_row_value(transaction_row, "active_user_transactions", 0) or 0)
    cursor.execute(
        """
        SELECT COUNT_BIG(*) AS active_collector_sessions
        FROM sys.dm_exec_sessions AS es
        INNER JOIN sys.dm_exec_connections AS ec ON ec.session_id = es.session_id
        WHERE es.session_id <> @@SPID
          AND es.is_user_process = 1
          AND es.database_id = DB_ID()
          AND (
              LOWER(COALESCE(es.program_name, N'')) LIKE N'%cologne%transit%'
              OR LOWER(COALESCE(es.program_name, N'')) LIKE N'%mdd%collector%'
              OR LOWER(COALESCE(es.host_name, N'')) LIKE N'%mdd%collector%'
          )
        """
    )
    collector_session_row = cursor.fetchone()
    if collector_session_row is None or _row_value(collector_session_row, "active_collector_sessions", 0) is None:
        raise RuntimeError("SQL active-collector-session measurement is incomplete")
    active_collector_sessions = int(
        _row_value(collector_session_row, "active_collector_sessions", 0) or 0
    )
    data_files = [item for item in files if item["type"].casefold() == "rows"]
    if execution_environment == PRODUCTION_EXECUTION_ENVIRONMENT:
        target_files = [item for item in data_files if item["volume"].casefold().startswith("c:")]
    else:
        target_files = [item for item in data_files if not item["volume"].casefold().startswith("c:")]
    free_gib = min((item["windows_free_gib"] for item in target_files), default=0.0)
    log_used = float(_row_value(log, "used_log_space_in_percent", 2) or 100.0)
    database_name = str(_row_value(identity, "database_name", 0) or "")
    database_status = str(_row_value(identity, "database_status", 3) or "")
    status = (
        "PASS"
        if database_name == "CologneTransitIntelligence"
        and database_status.casefold() == "online"
        and target_files
        and tempdb_files
        and free_gib >= SAFETY_FLOOR_GIB
        and (
            execution_environment == PRODUCTION_EXECUTION_ENVIRONMENT
            or all(not item["volume"].casefold().startswith("c:") for item in [*files, *tempdb_files])
        )
        and log_used <= MAX_LOG_USED_PERCENT
        and active_runs == 0
        and active_leases == 0
        and active_user_transactions == 0
        and active_collector_sessions == 0
        else "BLOCKED"
    )
    result: dict[str, Any] = {
        "status": status,
        "database_name": database_name,
        "server_name": str(_row_value(identity, "server_name", 1) or ""),
        "instance_name": str(_row_value(identity, "instance_name", 2) or ""),
        "database_status": database_status,
        "recovery_model": str(_row_value(identity, "recovery_model", 4) or ""),
        "data_files": data_files,
        "tempdb_files": tempdb_files,
        "execution_environment": execution_environment,
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
        "active_collector_sessions": active_collector_sessions,
    }
    if capacity_plan is not None:
        binding = _live_capacity_preflight(
            cursor,
            capacity_plan,
            execution_environment=execution_environment,
            log_limit=MAX_LOG_USED_PERCENT,
            require_installed_implementation_version=False,
        )
        result["capacity_evidence_binding"] = binding
        if binding.get("status") != "PASS":
            result["status"] = "BLOCKED"
    return result


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


def _validate_batches_without_persisting(settings: Any, batches: Sequence[str]) -> dict[str, Any]:
    """Compile/execute the complete guarded deployment inside a rollback scope."""

    validation_connection = connect_mssql(settings)
    executed = 0
    try:
        cursor = validation_connection.cursor()
        cursor.execute("SET XACT_ABORT ON; BEGIN TRANSACTION;")
        _drain(cursor)
        for batch in batches:
            cursor.execute(batch)
            _drain(cursor)
            executed += 1
        # The migration's own transaction is nested under this outer
        # transaction.  Rolling back this connection removes every catalog or
        # ledger change made during validation, including successful DDL.
        validation_connection.rollback()
        return {"status": "PASS", "validated_batches": executed, "persistent_writes": 0}
    except BaseException:
        try:
            validation_connection.rollback()
        finally:
            validation_connection.close()
        raise
    finally:
        try:
            validation_connection.close()
        except Exception:
            pass


def _apply(
    backup_evidence: Path,
    *,
    capacity_plan: Path | None = None,
    capacity_evidence_public_key: Path | None = None,
    execution_environment: str = PRODUCTION_EXECUTION_ENVIRONMENT,
) -> dict[str, Any]:
    backup_gate = _backup_gate(backup_evidence)
    if backup_gate.get("status") != "PASS":
        raise RuntimeError("fresh checksum-verified off-drive backup gate is not proven")
    collector_gate = _collector_gate()
    if collector_gate.get("status") != "PASS":
        raise RuntimeError("collector stopped/disabled gate is not proven")
    operation_gate = local_storage_operation_gate()
    if operation_gate.get("status") != "PASS":
        raise RuntimeError("another compact-storage operation is active")
    loaded_capacity_plan: Mapping[str, Any] | None = None
    if execution_environment == PRODUCTION_EXECUTION_ENVIRONMENT:
        if capacity_plan is None:
            raise RuntimeError("--capacity-plan is required before the first production migration write")
        public_key = capacity_evidence_public_key
        if public_key is None and os.environ.get("MSSQL_ADMIN_EVIDENCE_PUBLIC_KEY"):
            public_key = Path(os.environ["MSSQL_ADMIN_EVIDENCE_PUBLIC_KEY"])
        if public_key is None:
            raise RuntimeError(
                "--capacity-evidence-public-key or MSSQL_ADMIN_EVIDENCE_PUBLIC_KEY is required"
            )
        loaded_capacity_plan = _load_capacity_plan(
            capacity_plan,
            public_key_path=public_key,
            execution_environment=execution_environment,
        )
    elif capacity_plan is not None:
        public_key = capacity_evidence_public_key
        if public_key is None and os.environ.get("CTI_LAB_CAPACITY_EVIDENCE_PUBLIC_KEY"):
            public_key = Path(os.environ["CTI_LAB_CAPACITY_EVIDENCE_PUBLIC_KEY"])
        if public_key is None:
            raise RuntimeError(
                "isolated-lab capacity plans require --capacity-evidence-public-key or "
                "CTI_LAB_CAPACITY_EVIDENCE_PUBLIC_KEY"
            )
        loaded_capacity_plan = _load_capacity_plan(
            capacity_plan,
            public_key_path=public_key,
            execution_environment=execution_environment,
        )
    if execution_environment == ISOLATED_LAB_EXECUTION_ENVIRONMENT:
        settings, role_source = resolve_isolated_lab_connection_settings(
            os.environ,
            secret_provider=MacOSKeychainSecretProvider(),
        )
    else:
        settings, role_source = resolve_administrator_connection_settings(
            os.environ,
            secret_provider=MacOSKeychainSecretProvider(),
        )
    adapter = MssqlPersistence(settings)
    connection = connect_mssql(settings)
    executed = 0
    try:
        cursor = connection.cursor()
        preflight = _production_preflight(
            cursor,
            execution_environment=execution_environment,
            capacity_plan=loaded_capacity_plan,
        )
        if preflight.get("status") != "PASS":
            raise RuntimeError("SQL identity, active-writer, log, or disk-capacity preflight failed")
        validation = _validate_batches_without_persisting(settings, _batches(apply=True))
        if loaded_capacity_plan is not None:
            # The rollback-only compile validation uses a separate connection.
            # Rebind immediately on the write connection so a capacity or
            # identity change during validation cannot authorize the first DDL
            # batch.
            final_binding = _live_capacity_preflight(
                cursor,
                loaded_capacity_plan,
                execution_environment=execution_environment,
                log_limit=MAX_LOG_USED_PERCENT,
                require_installed_implementation_version=False,
            )
            preflight["capacity_evidence_binding_immediately_before_write"] = final_binding
            if final_binding.get("status") != "PASS":
                raise RuntimeError("fresh SQL capacity binding failed immediately before the first migration write")
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
        "execution_environment": execution_environment,
        "capacity_plan": loaded_capacity_plan,
        "deployment_validation": validation,
        "admin_role_source": role_source,
        "executed_batches": executed,
        "schema": schema,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="execute the additive migration")
    parser.add_argument(
        "--execution-environment",
        choices=(PRODUCTION_EXECUTION_ENVIRONMENT, ISOLATED_LAB_EXECUTION_ENVIRONMENT),
        default=PRODUCTION_EXECUTION_ENVIRONMENT,
    )
    parser.add_argument(
        "--capacity-plan",
        type=Path,
        help="signed physical capacity plan required before production writes",
    )
    parser.add_argument("--capacity-evidence-public-key", type=Path)
    parser.add_argument(
        "--backup-evidence",
        type=Path,
        help="fresh off-C COPY_ONLY/CHECKSUM/VERIFYONLY evidence required for --apply",
    )
    args = parser.parse_args(argv)
    try:
        if args.apply and args.backup_evidence is None:
            raise RuntimeError("--backup-evidence is required for additive production apply")
        result = (
            _apply(
                args.backup_evidence,
                capacity_plan=args.capacity_plan,
                capacity_evidence_public_key=args.capacity_evidence_public_key,
                execution_environment=args.execution_environment,
            )
            if args.apply
            else _static_result()
        )
        if not args.apply:
            result["collector_gate"] = _collector_gate()
    except Exception as exc:
        result = {"status": "BLOCKED", "error_type": type(exc).__name__, "error": str(exc)[:800]}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
