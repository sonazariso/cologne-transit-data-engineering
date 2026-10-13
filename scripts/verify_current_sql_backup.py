#!/usr/bin/env python3
"""Create and verify a current off-C: SQL Server recovery point.

The default is a read-only preflight.  ``--apply`` is required to issue the
native ``COPY_ONLY, COMPRESSION, CHECKSUM`` FULL backup and
``RESTORE VERIFYONLY``.  A destination-free-space measurement is also
required for apply; this prevents a large backup from silently landing on the
capacity-constrained Windows C: volume.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.mssql_persistence import (  # noqa: E402
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


MIN_DESTINATION_FREE_GIB = 5.0


def _row_value(row: Any, name: str, index: int = 0) -> Any:
    if isinstance(row, Mapping):
        lowered = {str(k).casefold(): v for k, v in row.items()}
        return lowered.get(name.casefold())
    if row is None:
        return None
    try:
        return row[index]
    except (IndexError, KeyError, TypeError):
        return None


def _safe_error(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    text = re.sub(r"(?i)(password|secret|token|authorization|apikey|credential)\s*[=:]\s*[^,;\s]+", r"\1=<redacted>", text)
    text = re.sub(r"(?i)(?:[A-Za-z]:[\\/]|/)[^\s']+", "<path redacted>", text)
    return text[:800]


def _destination_is_off_c(path: str) -> bool:
    normalized = str(path).strip().replace("/", "\\")
    return normalized.startswith("\\\\") or bool(re.match(r"^[d-zD-Z]:[\\/]", normalized))


def _collector_gate() -> dict[str, Any]:
    return local_collector_safety_gate()


def _preflight(cursor: Any, destination: str, destination_free_gib: float | None) -> dict[str, Any]:
    cursor.execute(
        """
        SELECT DB_NAME() AS database_name,
               CAST(SERVERPROPERTY('ServerName') AS NVARCHAR(128)) AS server_name,
               CAST(SERVERPROPERTY('InstanceName') AS NVARCHAR(128)) AS instance_name,
               DATABASEPROPERTYEX(DB_NAME(), 'Status') AS database_status,
               DATABASEPROPERTYEX(DB_NAME(), 'Recovery') AS recovery_model,
               DATABASEPROPERTYEX(DB_NAME(), 'UserAccess') AS user_access
        """
    )
    runtime = cursor.fetchone()
    cursor.execute(
        """
        SELECT DISTINCT vs.volume_mount_point, vs.total_bytes, vs.available_bytes
        FROM sys.database_files AS df
        CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
        """
    )
    volumes = [
        {
            "volume": str(_row_value(row, "volume_mount_point", 0) or ""),
            "total_bytes": int(_row_value(row, "total_bytes", 1) or 0),
            "available_bytes": int(_row_value(row, "available_bytes", 2) or 0),
        }
        for row in cursor.fetchall()
    ]
    cursor.execute(
        """
        SELECT TOP (1) backup_finish_date, has_backup_checksums,
               is_copy_only, compressed_backup_size
        FROM msdb.dbo.backupset
        WHERE database_name = DB_NAME() AND type = 'D'
        ORDER BY backup_finish_date DESC
        """
    )
    previous = cursor.fetchone()
    cursor.execute(
        """
        SELECT
            (SELECT COUNT_BIG(*) FROM ctl.MddMacCollectorRun
             WHERE Status IN ('RUNNING', 'CLAIMED')
               AND CollectionContext IN ('MAC_LEGACY7_PRODUCTION', 'MAC_TIERED50_PRODUCTION')) AS active_runs,
            (SELECT COUNT_BIG(*) FROM ctl.MddMacProductionLease
             WHERE LeaseName = 'MDD_MAC_PRODUCTION' AND Status = 'ACTIVE') AS active_leases,
            (SELECT COUNT_BIG(*)
             FROM sys.dm_tran_session_transactions AS st
             INNER JOIN sys.dm_exec_sessions AS es ON es.session_id = st.session_id
             WHERE st.session_id <> @@SPID AND es.is_user_process = 1) AS active_user_transactions
        """
    )
    active = cursor.fetchone()
    result = {
        "database_name": str(_row_value(runtime, "database_name", 0) or ""),
        "server_name": str(_row_value(runtime, "server_name", 1) or ""),
        "instance_name": str(_row_value(runtime, "instance_name", 2) or ""),
        "database_status": str(_row_value(runtime, "database_status", 3) or ""),
        "recovery_model": str(_row_value(runtime, "recovery_model", 4) or ""),
        "user_access": str(_row_value(runtime, "user_access", 5) or ""),
        "volumes": volumes,
        "previous_full_backup": {
            "finish_date": _row_value(previous, "backup_finish_date", 0),
            "has_checksum": bool(_row_value(previous, "has_backup_checksums", 1)),
            "copy_only": bool(_row_value(previous, "is_copy_only", 2)),
            "compressed_size": int(_row_value(previous, "compressed_backup_size", 3) or 0),
        }
        if previous is not None
        else None,
        "active_runs": int(_row_value(active, "active_runs", 0) or 0),
        "active_leases": int(_row_value(active, "active_leases", 1) or 0),
        "active_user_transactions": int(_row_value(active, "active_user_transactions", 2) or 0),
        "destination": destination,
        "destination_is_off_c": _destination_is_off_c(destination),
        "destination_free_gib": destination_free_gib,
    }
    return result


def _drain(cursor: Any) -> None:
    fetchall = getattr(cursor, "fetchall", None)
    nextset = getattr(cursor, "nextset", None)
    while True:
        if callable(fetchall):
            try:
                fetchall()
            except Exception:
                pass
        if not callable(nextset):
            return
        try:
            if not nextset():
                return
        except Exception:
            return


def _fresh_backup_metadata(cursor: Any, *, started_at: Any, destination: str) -> dict[str, Any]:
    """Prove the backup just created is a checked, compressed disk backup."""

    cursor.execute(
        """
        SELECT TOP (2)
               bs.backup_set_id, bs.media_set_id, bs.database_name,
               bs.backup_start_date, bs.backup_finish_date,
               bs.type, bs.is_copy_only, bs.has_backup_checksums,
               bs.compressed_backup_size, bs.position
        FROM msdb.dbo.backupset AS bs
        WHERE bs.database_name = DB_NAME()
          AND bs.type = 'D'
          AND bs.is_copy_only = 1
          AND bs.has_backup_checksums = 1
          AND bs.backup_finish_date >= %s
        ORDER BY bs.backup_finish_date DESC, bs.backup_set_id DESC
        """,
        (started_at,),
    )
    backup_rows = cursor.fetchall()
    if len(backup_rows) != 1:
        raise RuntimeError("fresh backup metadata did not identify exactly one COPY_ONLY full backup")
    backup = backup_rows[0]
    backup_set_id = _row_value(backup, "backup_set_id", 0)
    media_set_id = _row_value(backup, "media_set_id", 1)
    database_name = str(_row_value(backup, "database_name", 2) or "")
    if database_name != "CologneTransitIntelligence":
        raise RuntimeError("fresh backup metadata database identity is unexpected")
    if not bool(_row_value(backup, "is_copy_only", 6)):
        raise RuntimeError("fresh backup metadata is not COPY_ONLY")
    if not bool(_row_value(backup, "has_backup_checksums", 7)):
        raise RuntimeError("fresh backup metadata has no checksum")
    if int(_row_value(backup, "compressed_backup_size", 8) or 0) <= 0:
        raise RuntimeError("fresh backup metadata has no compressed payload size")
    backup_position = int(_row_value(backup, "position", 9) or 0)
    if backup_position <= 0:
        raise RuntimeError("fresh backup metadata has no valid backup-set position")
    cursor.execute(
        """
        SELECT family_sequence_number, physical_device_name, device_type
        FROM msdb.dbo.backupmediafamily
        WHERE media_set_id = %s
        ORDER BY family_sequence_number
        """,
        (media_set_id,),
    )
    media_rows = cursor.fetchall()
    if not media_rows or any(int(_row_value(row, "device_type", 2) or 0) != 2 for row in media_rows):
        raise RuntimeError("fresh backup metadata is missing or is not disk media")
    media_paths = [str(_row_value(row, "physical_device_name", 1) or "") for row in media_rows]
    if not any(path.casefold() == str(destination).casefold() for path in media_paths):
        raise RuntimeError("fresh backup metadata does not match the requested SQL-visible destination")
    return {
        "status": "PASS",
        "database_name": database_name,
        "backup_set_id": int(backup_set_id),
        "media_set_id": int(media_set_id),
        "backup_start_date": _row_value(backup, "backup_start_date", 3),
        "backup_finish_date": _row_value(backup, "backup_finish_date", 4),
        "copy_only": bool(_row_value(backup, "is_copy_only", 6)),
        "checksum": bool(_row_value(backup, "has_backup_checksums", 7)),
        "compressed_backup_size": int(_row_value(backup, "compressed_backup_size", 8) or 0),
        "backup_position": backup_position,
        "media_count": len(media_rows),
        "destination_accessibility": "PASS",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-path", required=True, help="SQL Server-visible UNC/share path; C: is rejected")
    parser.add_argument("--destination-free-gib", type=float, help="fresh measured free space at the off-C destination")
    parser.add_argument("--minimum-destination-free-gib", type=float, default=MIN_DESTINATION_FREE_GIB)
    parser.add_argument("--apply", action="store_true", help="perform BACKUP DATABASE and RESTORE VERIFYONLY")
    args = parser.parse_args(argv)
    result: dict[str, Any] = {
        "operation": "current_sql_backup_verification",
        "mode": "APPLY" if args.apply else "PREFLIGHT",
        "collector_gate": _collector_gate(),
        "storage_operation_gate": local_storage_operation_gate(),
        "destination": args.backup_path,
        "destination_is_off_c": _destination_is_off_c(args.backup_path),
        "restore_test": "NOT_RUN",
        "status": "BLOCKED",
    }
    try:
        if result["collector_gate"].get("status") != "PASS":
            raise RuntimeError("collector stopped/disabled gate is not proven")
        if result["storage_operation_gate"].get("status") != "PASS":
            raise RuntimeError("another compact-storage operation is active")
        if not result["destination_is_off_c"]:
            raise RuntimeError("backup destination is on Windows C:; refusing to write a large backup")
        if args.destination_free_gib is not None and args.destination_free_gib < args.minimum_destination_free_gib:
            raise RuntimeError("measured backup destination free space is below the safety threshold")
        if args.apply and args.destination_free_gib is None:
            raise RuntimeError("--destination-free-gib is required for an applied backup")
        settings, role_source = resolve_administrator_connection_settings(
            os.environ,
            secret_provider=MacOSKeychainSecretProvider(),
        )
        connection = connect_mssql(settings)
        try:
            cursor = connection.cursor()
            result["admin_role_source"] = role_source
            result["preflight"] = _preflight(cursor, args.backup_path, args.destination_free_gib)
            if result["preflight"]["database_name"] != "CologneTransitIntelligence":
                raise RuntimeError("connected database is not CologneTransitIntelligence")
            if result["preflight"]["database_status"].casefold() != "online":
                raise RuntimeError("database is not ONLINE")
            if any(result["preflight"][name] != 0 for name in ("active_runs", "active_leases", "active_user_transactions")):
                raise RuntimeError("an active collector, production lease, or unexpected user transaction was detected")
            if args.apply:
                if result["preflight"]["user_access"].casefold() not in {"multi_user", ""}:
                    raise RuntimeError("database is not in a safe MULTI_USER state")
                # backupset dates use the SQL Server host clock. Capture the
                # same clock domain so a non-UTC Windows host cannot make a
                # fresh backup look stale (or accidentally select an older one).
                cursor.execute("SELECT SYSDATETIME() AS BackupStartedAt")
                started_at = _row_value(cursor.fetchone(), "BackupStartedAt", 0)
                if started_at is None:
                    raise RuntimeError("SQL Server did not return a backup start timestamp")
                cursor.execute(
                    """
                    DECLARE @BackupPath NVARCHAR(4000) = CAST(%s AS NVARCHAR(4000));
                    BACKUP DATABASE [CologneTransitIntelligence]
                      TO DISK = @BackupPath
                      WITH COPY_ONLY, COMPRESSION, CHECKSUM, STATS = 5;
                    """,
                    (args.backup_path,),
                )
                # pymssql can leave additional result sets from BACKUP pending
                # on the cursor. Drain them before committing so the native
                # operation is fully consumed.
                _drain(cursor)
                connection.commit()
                backup = _fresh_backup_metadata(
                    cursor,
                    started_at=started_at,
                    destination=args.backup_path,
                )
                cursor.execute(
                    """
                    DECLARE @BackupPath NVARCHAR(4000) = CAST(%s AS NVARCHAR(4000));
                    DECLARE @BackupPosition INT = %s;
                    RESTORE VERIFYONLY FROM DISK = @BackupPath
                        WITH FILE = @BackupPosition, CHECKSUM;
                    """,
                    (args.backup_path, backup["backup_position"]),
                )
                _drain(cursor)
                connection.commit()
                result["backup"] = backup
                result["backup"].update({"verifyonly": "PASS", "backup_path": args.backup_path})
                result["status"] = "PASS"
            else:
                result["status"] = "PASS"
        finally:
            connection.close()
    except Exception as exc:
        result["status"] = "BLOCKED"
        result["error_type"] = type(exc).__name__
        result["error"] = _safe_error(exc)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

