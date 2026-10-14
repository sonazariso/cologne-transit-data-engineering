#!/usr/bin/env python3
"""Read-only in-flight peak sampler for the isolated Timing storage lab.

Run this from the approved macOS lab host in a second terminal while
``measure-timing-storage-options-lab.sql`` is running in SSMS.  It never
creates, alters, drops, migrates, shrinks, or restarts anything.  It refuses
the production host and any lab whose SQL data/log/tempdb files are on C:.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.mssql_persistence import connect_mssql  # noqa: E402
from cologne_transit_collector.runtime_configuration import (  # noqa: E402
    MacOSKeychainSecretProvider,
    resolve_isolated_lab_connection_settings,
)


PRODUCTION_HOST = "dataanalyst-vm"
LAB_DATABASE = "CologneTransitIntelligence"


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    if isinstance(row, Mapping):
        lowered = {str(name).casefold(): value for name, value in row.items()}
        return lowered.get(key.casefold())
    try:
        return row[index]
    except (IndexError, KeyError, TypeError):
        return None


def _normalized_host(value: Any) -> str:
    host = str(value or "").strip().casefold()
    if host.startswith("tcp:"):
        host = host[4:]
    if "," in host:
        host = host.split(",", 1)[0]
    if "\\" in host:
        host = host.split("\\", 1)[0]
    return host.strip("[]")


def _read_lab_identity(cursor: Any) -> dict[str, Any]:
    cursor.execute(
        """
        SELECT
            CONVERT(nvarchar(128), SERVERPROPERTY(N'ServerName')) AS server_name,
            DB_NAME() AS database_name,
            DATABASEPROPERTYEX(DB_NAME(), N'Status') AS database_status
        """
    )
    row = cursor.fetchone()
    identity = {
        "server_name": _row_value(row, "server_name"),
        "database_name": _row_value(row, "database_name"),
        "database_status": _row_value(row, "database_status"),
    }
    if _normalized_host(identity["server_name"]) == PRODUCTION_HOST:
        raise RuntimeError("peak sampler refuses the production SQL Server")
    if identity["database_name"] != LAB_DATABASE:
        raise RuntimeError("peak sampler target database must be CologneTransitIntelligence")
    if str(identity["database_status"] or "").upper() != "ONLINE":
        raise RuntimeError("peak sampler target database is not ONLINE")
    return identity


def _assert_non_c_files(cursor: Any) -> None:
    cursor.execute(
        """
        SELECT vs.volume_mount_point
        FROM sys.database_files AS df
        CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
        WHERE UPPER(LEFT(vs.volume_mount_point, 2)) = N'C:'
        UNION ALL
        SELECT vs.volume_mount_point
        FROM tempdb.sys.database_files AS df
        CROSS APPLY sys.dm_os_volume_stats(DB_ID(N'tempdb'), df.file_id) AS vs
        WHERE UPPER(LEFT(vs.volume_mount_point, 2)) = N'C:'
        """
    )
    if cursor.fetchone() is not None:
        raise RuntimeError("peak sampler requires SQL data, log, and tempdb files on non-C: volumes")


def _sample(cursor: Any) -> dict[str, Any]:
    cursor.execute(
        """
        SELECT
            SUM(CASE WHEN df.type = 0 THEN CONVERT(bigint, df.size) ELSE 0 END) * CONVERT(bigint, 8192)
                AS database_data_allocated_bytes,
            SUM(CASE WHEN df.type = 1 THEN CONVERT(bigint, df.size) ELSE 0 END) * CONVERT(bigint, 8192)
                AS database_log_allocated_bytes,
            (SELECT SUM(CONVERT(bigint, df.size)) * CONVERT(bigint, 8192)
             FROM tempdb.sys.database_files AS df) AS tempdb_file_allocated_bytes,
            (SELECT SUM(CONVERT(bigint, fsu.allocated_extent_page_count)) * CONVERT(bigint, 8192)
             FROM tempdb.sys.dm_db_file_space_usage AS fsu) AS tempdb_used_bytes,
            (SELECT CONVERT(bigint, used_log_space_in_bytes)
             FROM sys.dm_db_log_space_usage) AS transaction_log_used_bytes,
            (SELECT MIN(CONVERT(bigint, vs.available_bytes))
             FROM sys.database_files AS df
             CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs) AS volume_available_bytes
        FROM sys.database_files AS df
        """
    )
    row = cursor.fetchone()
    sample: dict[str, Any] = {
        "measured_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "database_data_allocated_bytes": int(_row_value(row, "database_data_allocated_bytes") or 0),
        "database_log_allocated_bytes": int(_row_value(row, "database_log_allocated_bytes") or 0),
        "tempdb_file_allocated_bytes": int(_row_value(row, "tempdb_file_allocated_bytes") or 0),
        "tempdb_used_bytes": int(_row_value(row, "tempdb_used_bytes") or 0),
        "transaction_log_used_bytes": int(_row_value(row, "transaction_log_used_bytes") or 0),
        "volume_available_bytes": int(_row_value(row, "volume_available_bytes") or 0),
    }
    cursor.execute(
        """
        SELECT
            obj.name AS object_name,
            SUM(CONVERT(bigint, ps.reserved_page_count)) AS reserved_pages,
            SUM(CONVERT(bigint, ps.used_page_count)) AS used_pages
        FROM sys.dm_db_partition_stats AS ps
        INNER JOIN sys.objects AS obj ON obj.object_id = ps.object_id
        INNER JOIN sys.schemas AS sch ON sch.schema_id = obj.schema_id
        WHERE sch.name = N'lab'
          AND obj.name IN
          (
              N'TimingPageCandidate', N'TimingDictionaryBase',
              N'TimingDictionaryValue', N'TimingDictionaryReference'
          )
        GROUP BY obj.name
        ORDER BY obj.name
        """
    )
    candidate_allocations: dict[str, dict[str, int]] = {}
    for candidate in cursor.fetchall():
        name = str(_row_value(candidate, "object_name") or "")
        candidate_allocations[name] = {
            "reserved_pages": int(_row_value(candidate, "reserved_pages", 1) or 0),
            "used_pages": int(_row_value(candidate, "used_pages", 2) or 0),
        }
    sample["candidate_allocations"] = candidate_allocations
    return sample


def _peak(samples: list[dict[str, Any]]) -> dict[str, Any]:
    scalar_fields = (
        "database_data_allocated_bytes",
        "database_log_allocated_bytes",
        "tempdb_file_allocated_bytes",
        "tempdb_used_bytes",
        "transaction_log_used_bytes",
    )
    result = {
        field: max(int(sample.get(field, 0)) for sample in samples)
        for field in scalar_fields
    }
    result["candidate_reserved_pages"] = {}
    for sample in samples:
        for name, allocation in sample.get("candidate_allocations", {}).items():
            result["candidate_reserved_pages"][name] = max(
                int(allocation.get("reserved_pages", 0)),
                int(result["candidate_reserved_pages"].get(name, 0)),
            )
    return result


def run(*, duration_seconds: float, interval_seconds: float) -> dict[str, Any]:
    if duration_seconds <= 0 or interval_seconds <= 0:
        raise ValueError("duration and interval must be positive")
    settings, role_source = resolve_isolated_lab_connection_settings(
        secret_provider=MacOSKeychainSecretProvider()
    )
    connection = connect_mssql(settings)
    samples: list[dict[str, Any]] = []
    try:
        cursor = connection.cursor()
        identity = _read_lab_identity(cursor)
        _assert_non_c_files(cursor)
        deadline = time.monotonic() + duration_seconds
        while True:
            samples.append(_sample(cursor))
            if time.monotonic() >= deadline:
                break
            time.sleep(min(interval_seconds, max(0.0, deadline - time.monotonic())))
    finally:
        connection.close()
    candidate_seen = any(sample.get("candidate_allocations") for sample in samples)
    canonical = json.dumps(samples, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "status": "PASS" if candidate_seen else "BLOCKED",
        "reason": None if candidate_seen else "no Timing storage candidate was observed",
        "execution_environment": "isolated-lab",
        "read_only": True,
        "connection_role_source": role_source,
        "identity": identity,
        "sample_count": len(samples),
        "interval_seconds": interval_seconds,
        "duration_seconds": duration_seconds,
        "peak_measurement_status": "IN_FLIGHT_SAMPLED",
        "peak": _peak(samples) if samples else {},
        "samples_sha256": hashlib.sha256(canonical).hexdigest(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-seconds", type=float, required=True)
    parser.add_argument("--interval-seconds", type=float, default=1.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = run(
            duration_seconds=args.duration_seconds,
            interval_seconds=args.interval_seconds,
        )
    except Exception as exc:
        result = {
            "status": "BLOCKED",
            "error_type": type(exc).__name__,
            "error": " ".join(str(exc).split())[:800],
            "execution_environment": "isolated-lab",
            "read_only": True,
        }
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
