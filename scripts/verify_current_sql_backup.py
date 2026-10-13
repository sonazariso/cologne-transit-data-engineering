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
import platform
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    connect_mssql,
)
from cologne_transit_collector.runtime_configuration import (  # noqa: E402
    MacOSKeychainSecretProvider,
    resolve_administrator_connection_settings,
)


COLLECTOR_LABELS = (
    "com.cologne-transit-intelligence.mdd-collector",
    "com.cologne-transit-intelligence.mdd-tiered50-collector",
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
    return not normalized.casefold().startswith("c:")


def _collector_gate() -> dict[str, Any]:
    if platform.system() != "Darwin":
        return {"status": "NOT_PROVEN", "reason": "launchd state is not inspectable on this host"}
    try:
        disabled_result = subprocess.run(
            ["/bin/launchctl", "print-disabled", f"user/{os.getuid()}"],
            capture_output=True,
            text=True,
            check=False,
        )
        text = disabled_result.stdout + disabled_result.stderr
        disabled = all(f'"{label}" => disabled' in text for label in COLLECTOR_LABELS)
        loaded: dict[str, int] = {}
        for label in COLLECTOR_LABELS:
            loaded[label] = subprocess.run(
                ["/bin/launchctl", "print", f"gui/{os.getuid()}/{label}"],
                capture_output=True,
                text=True,
                check=False,
            ).returncode
        unloaded = all(code != 0 for code in loaded.values())
        return {"status": "PASS" if disabled and unloaded else "NOT_PROVEN", "disabled": disabled, "loaded_exit_codes": loaded}
    except OSError as exc:
        return {"status": "NOT_PROVEN", "reason": _safe_error(exc)}


def _preflight(cursor: Any, destination: str, destination_free_gib: float | None) -> dict[str, Any]:
    cursor.execute(
        """
        SELECT DB_NAME() AS database_name,
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
    result = {
        "database_name": str(_row_value(runtime, "database_name", 0) or ""),
        "database_status": str(_row_value(runtime, "database_status", 1) or ""),
        "recovery_model": str(_row_value(runtime, "recovery_model", 2) or ""),
        "user_access": str(_row_value(runtime, "user_access", 3) or ""),
        "volumes": volumes,
        "previous_full_backup": {
            "finish_date": _row_value(previous, "backup_finish_date", 0),
            "has_checksum": bool(_row_value(previous, "has_backup_checksums", 1)),
            "copy_only": bool(_row_value(previous, "is_copy_only", 2)),
            "compressed_size": int(_row_value(previous, "compressed_backup_size", 3) or 0),
        }
        if previous is not None
        else None,
        "destination": destination,
        "destination_is_off_c": _destination_is_off_c(destination),
        "destination_free_gib": destination_free_gib,
    }
    return result


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
        "destination": args.backup_path,
        "destination_is_off_c": _destination_is_off_c(args.backup_path),
        "restore_test": "NOT_RUN",
        "status": "BLOCKED",
    }
    try:
        if result["collector_gate"].get("status") != "PASS":
            raise RuntimeError("collector stopped/disabled gate is not proven")
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
            if args.apply:
                if result["preflight"]["user_access"].casefold() not in {"multi_user", ""}:
                    raise RuntimeError("database is not in a safe MULTI_USER state")
                cursor.execute(
                    """
                    DECLARE @BackupPath NVARCHAR(4000) = CAST(%s AS NVARCHAR(4000));
                    BACKUP DATABASE [CologneTransitIntelligence]
                      TO DISK = @BackupPath
                      WITH COPY_ONLY, COMPRESSION, CHECKSUM, INIT, STATS = 5;
                    RESTORE VERIFYONLY FROM DISK = @BackupPath WITH CHECKSUM;
                    """,
                    (args.backup_path,),
                )
                # pymssql can leave additional result sets from BACKUP and
                # RESTORE VERIFYONLY pending on the cursor. Drain them before
                # committing so the native operation is fully consumed.
                while True:
                    try:
                        cursor.fetchall()
                    except Exception:
                        pass
                    nextset = getattr(cursor, "nextset", None)
                    if not callable(nextset) or not nextset():
                        break
                connection.commit()
                result["backup"] = {
                    "status": "PASS",
                    "copy_only": True,
                    "checksum": True,
                    "verifyonly": "PASS",
                    "backup_path": args.backup_path,
                }
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

