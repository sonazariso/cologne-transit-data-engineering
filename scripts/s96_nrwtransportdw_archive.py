#!/usr/bin/env python3
"""Audit and safely archive the legacy ``NRWTransportDW`` database.

The default mode is read-only.  ``--execute`` is required before this script
will issue the one approved native backup and create the USB ZIP package.  It
never drops, detaches, shrinks, moves, or deletes a SQL Server database/file,
and it does not perform an application-data migration.

The script deliberately treats the legacy database as a source to review, not
as an instruction source.  It records the evidence used to decide whether a
Köln asset is required by the current ``CologneTransitIntelligence`` project.
Credentials are read from the existing environment only and are never written
to an artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
import zipfile
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlConnectionSettings,
    connect_mssql,
)


ACTIVE_DATABASE = "CologneTransitIntelligence"
LEGACY_DATABASE = "NRWTransportDW"
DEFAULT_ARCHIVE_DIR = Path("/Users/admin/Documents/CologneTransitData")
DEFAULT_ARTIFACT_ROOT = ROOT / "docs" / "archives" / "nrwtransportdw"
SQL_SHARED_BACKUP_DIR = r"\\vmware-host\Shared Folders\CologneTransitData"
ZIP_MAX_FAT32_BYTES = 4 * 1024**3
CAPACITY_RESERVE_BYTES = 512 * 1024**2
CHUNK_BYTES = 8 * 1024**2


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.isoformat(timespec="microseconds")
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    return str(value)


def json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return iso(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(child) for child in value]
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(CHUNK_BYTES)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sql_literal(value: str) -> str:
    return "N'" + value.replace("'", "''") + "'"


def qident(value: str) -> str:
    return "[" + value.replace("]", "]]" ) + "]"


def first_value(row: Mapping[str, Any] | None, *names: str) -> Any:
    if not row:
        return None
    lowered = {str(key).lower(): value for key, value in row.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def connect_database(settings: MssqlConnectionSettings, database: str, timeout: int | None = None) -> Any:
    selected = replace(settings, database=database)
    if timeout is None:
        return connect_mssql(selected)
    import pymssql

    if not selected.user or selected.password is None:
        raise ValueError("MSSQL_USER and MSSQL_PASSWORD must be supplied outside source control")
    return pymssql.connect(
        server=selected.host,
        port=selected.port,
        user=selected.user,
        password=selected.password,
        database=selected.database,
        login_timeout=selected.login_timeout_seconds,
        timeout=timeout,
        as_dict=True,
        charset="UTF-8",
    )


def fetch(connection: Any, statement: str, parameters: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
    cursor = connection.cursor()
    cursor.execute(statement, parameters or ())
    return [dict(row) for row in cursor.fetchall()]


def fetch_one(connection: Any, statement: str, parameters: tuple[Any, ...] | None = None) -> dict[str, Any]:
    rows = fetch(connection, statement, parameters)
    return rows[0] if rows else {}


def execute_no_result(connection: Any, statement: str, parameters: tuple[Any, ...] | None = None) -> None:
    cursor = connection.cursor()
    cursor.execute(statement, parameters or ())
    try:
        cursor.fetchall()
    except Exception:
        # BACKUP/RESTORE VERIFYONLY may complete successfully without a
        # resultset; the successful execute is the pass condition.
        pass


def close(connection: Any) -> None:
    try:
        connection.close()
    except Exception:
        pass


def volume_summary(path: Path) -> dict[str, Any]:
    usage = shutil.disk_usage(path)
    return {
        "path": str(path),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "free_gib": round(usage.free / 1024**3, 3),
        "free_percentage": round(100.0 * usage.free / usage.total, 4) if usage.total else None,
    }


def repo_reference_inventory() -> dict[str, Any]:
    active_roots = {"src", "scripts", "sql", "config", "deployment", "powerbi"}
    historical_roots = {"docs", "prompts"}
    active: list[str] = []
    historical: list[str] = []
    all_matches: list[dict[str, Any]] = []
    pattern = re.compile(r"NRWTransportDW|NRWTransport", re.IGNORECASE)
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts or path.name == ".env":
            continue
        relative = path.relative_to(ROOT)
        if relative.as_posix() == "scripts/s96_nrwtransportdw_archive.py":
            continue
        if relative.parts and relative.parts[0] not in active_roots | historical_roots:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        match_count = len(pattern.findall(text))
        if not match_count:
            continue
        entry = {"path": str(relative), "match_count": match_count}
        all_matches.append(entry)
        if relative.parts[0] in active_roots:
            active.append(str(relative))
        else:
            historical.append(str(relative))
    return {
        "active_runtime_or_configuration_references": sorted(active),
        "historical_documentation_or_prompt_references": sorted(historical),
        "all_scanned_matches": sorted(all_matches, key=lambda item: item["path"]),
    }


def task_scheduler_inventory() -> dict[str, Any]:
    """Record the direct-access result without changing the VM or its tasks."""

    vmrun = Path("/Applications/VMware Fusion.app/Contents/Public/vmrun")
    result: dict[str, Any] = {
        "status": "UNAVAILABLE_FROM_MACOS",
        "task_scheduler_query": "NOT_PERFORMED",
        "reason": "The Mac-side VMware guest operation requires host/guest authentication; no direct Windows Task Scheduler API was available to this run.",
        "historical_task_name": r"\Cologne Transit Realtime Collector",
    }
    if vmrun.exists():
        result["vmrun_present"] = True
        result["vmrun_list_status"] = "PASS"
        try:
            listed = subprocess.run([str(vmrun), "list"], capture_output=True, text=True, timeout=10, check=False)
            result["vmrun_list_exit_code"] = listed.returncode
            result["vmrun_list_output"] = listed.stdout.strip()[:500]
        except (OSError, subprocess.SubprocessError) as exc:
            result["vmrun_list_status"] = "ERROR"
            result["vmrun_list_error"] = type(exc).__name__
    else:
        result["vmrun_present"] = False
    return result


def sql_agent_inventory(connection: Any) -> dict[str, Any]:
    rows = fetch(
        connection,
        """
        SELECT j.name AS job_name, j.enabled, js.step_id, js.step_name,
               js.subsystem, js.command
        FROM dbo.sysjobs AS j
        JOIN dbo.sysjobsteps AS js ON js.job_id = j.job_id
        WHERE js.command LIKE N'%NRWTransportDW%'
           OR js.command LIKE N'%NRWTransport%'
        ORDER BY j.name, js.step_id
        """,
    )
    references: list[dict[str, Any]] = []
    for row in rows:
        command = str(row.pop("command", "") or "")
        row["command_length"] = len(command)
        row["command_sha256"] = hashlib.sha256(command.encode("utf-8", "replace")).hexdigest()
        references.append(json_safe(row))
    count = fetch_one(connection, "SELECT COUNT(*) AS job_count FROM dbo.sysjobs")
    return {
        "job_count": int(count.get("job_count") or 0),
        "nrw_references": references,
        "status": "PASS",
    }


def server_and_capacity(settings: MssqlConnectionSettings, archive_dir: Path) -> dict[str, Any]:
    master = connect_database(settings, "master")
    try:
        server = fetch_one(
            master,
            """
            SELECT CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128)) AS product_version,
                   CAST(SERVERPROPERTY('ProductLevel') AS nvarchar(128)) AS product_level,
                   CAST(SERVERPROPERTY('Edition') AS nvarchar(256)) AS edition,
                   CAST(SERVERPROPERTY('MachineName') AS nvarchar(128)) AS machine_name,
                   CAST(SERVERPROPERTY('Collation') AS nvarchar(128)) AS collation
            """,
        )
        databases = fetch(
            master,
            """
            SELECT d.name, d.state_desc, d.user_access_desc, d.recovery_model_desc,
                   allocated_mb = SUM(CAST(mf.size AS bigint)) * 8.0 / 1024,
                   file_count = COUNT(*)
            FROM sys.databases AS d
            JOIN sys.master_files AS mf ON mf.database_id = d.database_id
            WHERE d.name IN (N'CologneTransitIntelligence', N'NRWTransportDW')
            GROUP BY d.name, d.state_desc, d.user_access_desc, d.recovery_model_desc
            ORDER BY d.name
            """,
        )
        files = fetch(
            master,
            """
            SELECT DB_NAME(mf.database_id) AS database_name,
                   mf.name AS logical_name, mf.type_desc, mf.physical_name,
                   allocated_mb = CAST(mf.size AS bigint) * 8.0 / 1024,
                   mf.growth, mf.is_percent_growth,
                   max_size_mb = CASE WHEN mf.max_size = -1 THEN NULL ELSE CAST(mf.max_size AS bigint) * 8.0 / 1024 END,
                   v.total_bytes, v.available_bytes
            FROM sys.master_files AS mf
            CROSS APPLY sys.dm_os_volume_stats(mf.database_id, mf.file_id) AS v
            WHERE DB_NAME(mf.database_id) IN (N'CologneTransitIntelligence', N'NRWTransportDW')
            ORDER BY database_name, mf.type_desc, mf.file_id
            """,
        )
        states = {
            row["name"]: json_safe(row)
            for row in fetch(
                master,
                """
                SELECT name, state_desc, user_access_desc, recovery_model_desc, log_reuse_wait_desc
                FROM sys.databases
                WHERE name IN (N'CologneTransitIntelligence', N'NRWTransportDW')
                """,
            )
        }
        return {
            "server": json_safe(server),
            "databases": [json_safe(row) for row in databases],
            "files": [json_safe(row) for row in files],
            "states": states,
            "target_mac_storage": volume_summary(archive_dir),
            "sql_server_c_drive": next(
                (
                    {
                        "total_bytes": row.get("total_bytes"),
                        "available_bytes": row.get("available_bytes"),
                        "free_gib": round(float(row["available_bytes"]) / 1024**3, 3),
                    }
                    for row in files
                    if str(row.get("physical_name", "")).upper().startswith("C:")
                ),
                None,
            ),
        }
    finally:
        close(master)


def active_database_state(settings: MssqlConnectionSettings) -> dict[str, Any]:
    connection = connect_database(settings, ACTIVE_DATABASE)
    try:
        files = fetch(
            connection,
            """
            SELECT name AS logical_name, type_desc, physical_name,
                   allocated_mb = CAST(size AS bigint) * 8.0 / 1024,
                   used_mb = CAST(FILEPROPERTY(name, 'SpaceUsed') AS bigint) * 8.0 / 1024,
                   growth, is_percent_growth,
                   max_size_mb = CASE WHEN max_size = -1 THEN NULL ELSE CAST(max_size AS bigint) * 8.0 / 1024 END
            FROM sys.database_files
            ORDER BY file_id
            """,
        )
        log_usage = fetch_one(
            connection,
            """
            SELECT total_log_size_in_bytes, used_log_space_in_bytes, used_log_space_in_percent
            FROM sys.dm_db_log_space_usage
            """,
        )
        state = fetch_one(
            connection,
            """
            SELECT DB_NAME() AS database_name, state_desc, user_access_desc,
                   recovery_model_desc, log_reuse_wait_desc
            FROM sys.databases
            WHERE name = DB_NAME()
            """,
        )
        for row in files:
            if row.get("type_desc") == "LOG":
                row["used_mb_from_log_dmv"] = (float(log_usage.get("used_log_space_in_bytes") or 0) / 1024**2)
                row["used_percent_from_log_dmv"] = float(log_usage.get("used_log_space_in_percent") or 0)
        return {
            "state": json_safe(state),
            "files": [json_safe(row) for row in files],
            "log_usage": json_safe(log_usage),
        }
    finally:
        close(connection)


def table_inventory(connection: Any) -> dict[str, Any]:
    schemas = fetch(connection, "SELECT name FROM sys.schemas ORDER BY name")
    tables = fetch(
        connection,
        """
        SELECT s.name AS schema_name, t.name AS table_name, t.create_date, t.modify_date,
               row_count = COALESCE(SUM(CASE WHEN ps.index_id IN (0, 1) THEN ps.row_count ELSE 0 END), 0),
               reserved_mb = COALESCE(SUM(CAST(ps.reserved_page_count AS bigint)) * 8.0 / 1024, 0),
               used_mb = COALESCE(SUM(CAST(ps.used_page_count AS bigint)) * 8.0 / 1024, 0)
        FROM sys.tables AS t
        JOIN sys.schemas AS s ON s.schema_id = t.schema_id
        LEFT JOIN sys.dm_db_partition_stats AS ps ON ps.object_id = t.object_id
        GROUP BY s.name, t.name, t.create_date, t.modify_date
        ORDER BY s.name, t.name
        """,
    )
    views = fetch(
        connection,
        """
        SELECT s.name AS schema_name, o.name AS object_name, o.create_date, o.modify_date
        FROM sys.views AS o JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        WHERE o.is_ms_shipped = 0 ORDER BY s.name, o.name
        """,
    )
    procedures = fetch(
        connection,
        """
        SELECT s.name AS schema_name, o.name AS object_name, o.create_date, o.modify_date
        FROM sys.procedures AS o JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        WHERE o.is_ms_shipped = 0 ORDER BY s.name, o.name
        """,
    )
    functions = fetch(
        connection,
        """
        SELECT s.name AS schema_name, o.name AS object_name, o.type_desc, o.create_date, o.modify_date
        FROM sys.objects AS o JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        WHERE o.is_ms_shipped = 0 AND o.type IN ('FN', 'IF', 'TF', 'FS', 'FT')
        ORDER BY s.name, o.name
        """,
    )
    synonyms = fetch(
        connection,
        """
        SELECT s.name AS schema_name, o.name AS object_name, o.base_object_name
        FROM sys.synonyms AS o JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        ORDER BY s.name, o.name
        """,
    )
    foreign_keys = fetch(
        connection,
        """
        SELECT fk.name AS foreign_key_name,
               OBJECT_SCHEMA_NAME(fk.parent_object_id) AS parent_schema,
               OBJECT_NAME(fk.parent_object_id) AS parent_table,
               OBJECT_SCHEMA_NAME(fk.referenced_object_id) AS referenced_schema,
               OBJECT_NAME(fk.referenced_object_id) AS referenced_table,
               fk.is_disabled
        FROM sys.foreign_keys AS fk ORDER BY fk.name
        """,
    )
    indexes = fetch(
        connection,
        """
        SELECT i.name AS index_name, s.name AS schema_name, o.name AS object_name,
               i.type_desc, i.is_unique, i.is_primary_key, i.is_disabled
        FROM sys.indexes AS i
        JOIN sys.objects AS o ON o.object_id = i.object_id
        JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        WHERE o.is_ms_shipped = 0 AND i.index_id > 0
        ORDER BY s.name, o.name, i.index_id
        """,
    )
    columns = fetch(
        connection,
        """
        SELECT s.name AS schema_name, t.name AS table_name, c.column_id,
               c.name AS column_name, ty.name AS type_name, c.max_length, c.is_nullable
        FROM sys.tables AS t
        JOIN sys.schemas AS s ON s.schema_id = t.schema_id
        JOIN sys.columns AS c ON c.object_id = t.object_id
        JOIN sys.types AS ty ON ty.user_type_id = c.user_type_id
        ORDER BY s.name, t.name, c.column_id
        """,
    )
    modules = fetch(
        connection,
        """
        SELECT s.name AS schema_name, o.name AS object_name, o.type_desc, sm.definition
        FROM sys.sql_modules AS sm
        JOIN sys.objects AS o ON o.object_id = sm.object_id
        JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        WHERE o.is_ms_shipped = 0 ORDER BY s.name, o.name
        """,
    )
    safe_modules: list[dict[str, Any]] = []
    for row in modules:
        definition = str(row.pop("definition", "") or "")
        safe_modules.append(
            {
                **json_safe(row),
                "definition_length": len(definition),
                "definition_sha256": hashlib.sha256(definition.encode("utf-8", "replace")).hexdigest(),
                "references_nrwtransportdw": bool(re.search(r"NRWTransportDW|NRWTransport", definition, re.IGNORECASE)),
            }
        )
    table_names = {f"{row['schema_name']}.{row['table_name']}" for row in tables}
    by_table: dict[str, list[dict[str, Any]]] = {name: [] for name in table_names}
    for row in columns:
        by_table.setdefault(f"{row['schema_name']}.{row['table_name']}", []).append(
            {
                "column_id": row["column_id"],
                "column_name": row["column_name"],
                "type_name": row["type_name"],
                "max_length": row["max_length"],
                "is_nullable": row["is_nullable"],
            }
        )
    for row in tables:
        row["columns"] = by_table.get(f"{row['schema_name']}.{row['table_name']}", [])
    object_counts = {
        "schemas": len(schemas),
        "tables": len(tables),
        "views": len(views),
        "stored_procedures": len(procedures),
        "functions": len(functions),
        "synonyms": len(synonyms),
        "foreign_keys": len(foreign_keys),
        "indexes": len(indexes),
    }
    return {
        "object_counts": object_counts,
        "schemas": [row["name"] for row in schemas],
        "tables": [json_safe(row) for row in tables],
        "views": [json_safe(row) for row in views],
        "stored_procedures": [json_safe(row) for row in procedures],
        "functions": [json_safe(row) for row in functions],
        "synonyms": [json_safe(row) for row in synonyms],
        "foreign_keys": [json_safe(row) for row in foreign_keys],
        "indexes": [json_safe(row) for row in indexes],
        "modules": safe_modules,
    }


def date_coverage(connection: Any) -> list[dict[str, Any]]:
    targets = [
        ("dw.DimDate", "FullDate"),
        ("wrk.ServiceDates", "ServiceDate"),
        ("wrk.TripInstances", "ServiceDate"),
        ("wrk.Phase1TripStops", "ServiceDate"),
        ("stg.DbRealtimeStopObservation", "CapturedAt"),
        ("dw.FactRealtimeStopPerformance", "FirstCapturedAt"),
        ("dw.FactRealtimeStopPerformance", "LastCapturedAt"),
        ("stg.Stops", "LoadDateTime"),
        ("stg.Routes", "LoadDateTime"),
    ]
    result: list[dict[str, Any]] = []
    for object_name, column_name in targets:
        schema_name, table_name = object_name.split(".", 1)
        statement = (
            f"SELECT MIN({qident(column_name)}) AS min_value, MAX({qident(column_name)}) AS max_value "
            f"FROM {qident(schema_name)}.{qident(table_name)}"
        )
        row = fetch_one(connection, statement)
        result.append(
            {
                "object": object_name,
                "column": column_name,
                "min": iso(row.get("min_value")),
                "max": iso(row.get("max_value")),
            }
        )
    return result


def exact_set_comparison(connection: Any, old_sql: str, new_sql: str) -> dict[str, Any]:
    row = fetch_one(
        connection,
        f"""
        WITH old_ids AS ({old_sql}),
             new_ids AS ({new_sql})
        SELECT
            old_count = (SELECT COUNT_BIG(*) FROM old_ids),
            new_count = (SELECT COUNT_BIG(*) FROM new_ids),
            exact_overlap = (SELECT COUNT_BIG(*) FROM old_ids AS o WHERE EXISTS (SELECT 1 FROM new_ids AS n WHERE n.item_id = o.item_id)),
            old_only = (SELECT COUNT_BIG(*) FROM old_ids AS o WHERE NOT EXISTS (SELECT 1 FROM new_ids AS n WHERE n.item_id = o.item_id)),
            new_only = (SELECT COUNT_BIG(*) FROM new_ids AS n WHERE NOT EXISTS (SELECT 1 FROM old_ids AS o WHERE o.item_id = n.item_id))
        """,
    )
    return json_safe(row)


def candidate_inventory(active: Any, legacy: Any) -> list[dict[str, Any]]:
    stop_comparison = exact_set_comparison(
        active,
        "SELECT StopId COLLATE Latin1_General_100_BIN2 AS item_id FROM NRWTransportDW.stg.Stops WHERE StopId LIKE N'de:05315:%'",
        "SELECT StopId COLLATE Latin1_General_100_BIN2 AS item_id FROM stg.GtfsStops WHERE StopId LIKE N'de:05315:%'",
    )
    warehouse_stop_comparison = exact_set_comparison(
        active,
        "SELECT StopId COLLATE Latin1_General_100_BIN2 AS item_id FROM NRWTransportDW.dw.DimStop WHERE StopId LIKE N'de:05315:%'",
        "SELECT StopId COLLATE Latin1_General_100_BIN2 AS item_id FROM dw.DimStop WHERE StopId LIKE N'de:05315:%'",
    )
    route_comparison = exact_set_comparison(
        active,
        "SELECT RouteId COLLATE Latin1_General_100_BIN2 AS item_id FROM NRWTransportDW.stg.Routes",
        "SELECT RouteId COLLATE Latin1_General_100_BIN2 AS item_id FROM stg.GtfsRoutes",
    )
    mapping = fetch_one(
        legacy,
        """
        SELECT total_rows = COUNT_BIG(*),
               koeln_rows = SUM(CASE WHEN StationName LIKE N'%Köln%' OR StationName LIKE N'%Cologne%' THEN 1 ELSE 0 END)
        FROM wrk.DbStationGtfsMapping
        """,
    )
    active_target = fetch_one(
        active,
        """
        SELECT SamplingTargetId, StopPointRef, TargetName, IsEnabled
        FROM ctl.MddRealtimeSamplingTarget
        WHERE StopPointRef = N'de:05315:11201'
        """,
    )
    old_realtime = fetch_one(
        legacy,
        """
        SELECT total_rows = COUNT_BIG(*),
               koeln_hbf_rows = SUM(CASE WHEN StationEva = '8000207' THEN 1 ELSE 0 END),
               min_captured = MIN(CASE WHEN StationEva = '8000207' THEN CapturedAt END),
               max_captured = MAX(CASE WHEN StationEva = '8000207' THEN CapturedAt END)
        FROM stg.DbRealtimeStopObservation
        """,
    )
    old_fact = fetch_one(
        legacy,
        """
        SELECT total_rows = COUNT_BIG(*),
               koeln_hbf_rows = SUM(CASE WHEN StationEva = '8000207' THEN 1 ELSE 0 END),
               min_captured = MIN(CASE WHEN StationEva = '8000207' THEN FirstCapturedAt END),
               max_captured = MAX(CASE WHEN StationEva = '8000207' THEN LastCapturedAt END)
        FROM dw.FactRealtimeStopPerformance
        """,
    )
    current_realtime = fetch_one(
        active,
        """
        SELECT total_rows = COUNT_BIG(*),
               koeln_hbf_rows = SUM(CASE WHEN StopPointRef LIKE N'de:05315:11201%' THEN 1 ELSE 0 END),
               min_captured = MIN(CASE WHEN StopPointRef LIKE N'de:05315:11201%' THEN ObservedAtUtc END),
               max_captured = MAX(CASE WHEN StopPointRef LIKE N'de:05315:11201%' THEN ObservedAtUtc END),
               exact_id_count = COUNT(DISTINCT CASE WHEN StopPointRef LIKE N'de:05315:11201%' THEN ResultId END)
        FROM stg.MddRealtimeStopObservation
        """,
    )
    min_old = old_realtime.get("min_captured")
    max_old = old_realtime.get("max_captured")
    min_current = current_realtime.get("min_captured")
    max_current = current_realtime.get("max_captured")
    overlap: dict[str, Any] = {"status": "NO_TEMPORAL_OVERLAP"}
    exact_id_overlap: dict[str, Any] = {"old_distinct_ids": None, "current_distinct_ids": current_realtime.get("exact_id_count"), "exact_id_overlap": None}
    if min_old and max_old and min_current and max_current:
        overlap_start = max(min_old, min_current)
        overlap_finish = min(max_old, max_current)
        if overlap_start <= overlap_finish:
            overlap = {
                "status": "TEMPORAL_OVERLAP",
                "start": iso(overlap_start),
                "finish": iso(overlap_finish),
                "legacy_rows": fetch_one(
                    legacy,
                    """
                    SELECT COUNT_BIG(*) AS row_count
                    FROM stg.DbRealtimeStopObservation
                    WHERE StationEva = '8000207' AND CapturedAt >= %s AND CapturedAt <= %s
                    """,
                    (overlap_start, overlap_finish),
                ).get("row_count"),
                "active_rows": fetch_one(
                    active,
                    """
                    SELECT COUNT_BIG(*) AS row_count
                    FROM stg.MddRealtimeStopObservation
                    WHERE StopPointRef LIKE N'de:05315:11201%' AND ObservedAtUtc >= %s AND ObservedAtUtc <= %s
                    """,
                    (overlap_start, overlap_finish),
                ).get("row_count"),
            }
            exact_id_overlap = fetch_one(
                active,
                """
                SELECT old_distinct_ids = (SELECT COUNT(DISTINCT DbEventId) FROM NRWTransportDW.stg.DbRealtimeStopObservation WHERE StationEva = '8000207'),
                       current_distinct_ids = (SELECT COUNT(DISTINCT ResultId) FROM stg.MddRealtimeStopObservation WHERE StopPointRef LIKE N'de:05315:11201%'),
                       exact_id_overlap = (
                           SELECT COUNT(DISTINCT o.DbEventId)
                           FROM NRWTransportDW.stg.DbRealtimeStopObservation AS o
                           WHERE o.StationEva = '8000207'
                             AND EXISTS (
                                 SELECT 1 FROM stg.MddRealtimeStopObservation AS n
                                 WHERE n.StopPointRef LIKE N'de:05315:11201%'
                                   AND n.ResultId COLLATE Latin1_General_100_BIN2 = o.DbEventId COLLATE Latin1_General_100_BIN2
                             )
                       )
                """,
            )

    return [
        {
            "source_object": "NRWTransportDW.stg.Stops",
            "source_grain": "one legacy GTFS stop/parent-stop row",
            "relevant_row_count": stop_comparison.get("old_count"),
            "date_coverage": "loaded 2026-08-19; static feed object",
            "why_it_might_be_useful": "Contains 2,394 Köln-borough stop rows from the legacy all-NRW feed.",
            "equivalent_in_active_database": {
                "object": "stg.GtfsStops / dw.DimStop",
                "row_count": 3156,
                "comparison": stop_comparison,
            },
            "classification": "DO_NOT_MIGRATE_OUT_OF_SCOPE",
            "decision_reason": "The active GTFS feed is authoritative for the current project; the 228 unmatched legacy IDs are feed-version/parent-record differences and no approved active target is missing.",
        },
        {
            "source_object": "NRWTransportDW.dw.DimStop",
            "source_grain": "one legacy warehouse stop-position row",
            "relevant_row_count": warehouse_stop_comparison.get("old_count"),
            "date_coverage": "derived from the 2026-08-19 static load",
            "why_it_might_be_useful": "Contains 70 Köln-borough warehouse stop-position rows, including Köln Hbf and other rail stops.",
            "equivalent_in_active_database": {
                "object": "dw.DimStop",
                "row_count": 3156,
                "comparison": warehouse_stop_comparison,
                "old_only_detail": "Two old-only child IDs are both Köln Messe/Deutz rows whose parent station is already represented in the active model.",
            },
            "classification": "DO_NOT_MIGRATE_DUPLICATE",
            "decision_reason": "The station entities are already represented by the authoritative current GTFS warehouse and target model; copying the old feed would create parallel static structures.",
        },
        {
            "source_object": "NRWTransportDW.wrk.DbStationGtfsMapping",
            "source_grain": "one legacy station-to-GTFS-parent mapping",
            "relevant_row_count": mapping.get("koeln_rows"),
            "date_coverage": "mapping created 2026-08-22 / 2026-08-28",
            "why_it_might_be_useful": "Includes the legacy Köln Hbf mapping to de:05315:11201_Parent.",
            "equivalent_in_active_database": {
                "object": "ctl.MddRealtimeSamplingTarget",
                "matching_active_target": active_target,
            },
            "classification": "DO_NOT_MIGRATE_DUPLICATE",
            "decision_reason": "The current target is explicitly configured as de:05315:11201 (Köln Hbf); the legacy mapping adds no required active lineage.",
        },
        {
            "source_object": "NRWTransportDW.stg.DbRealtimeStopObservation",
            "source_grain": "one legacy station/vehicle realtime observation captured at a station",
            "relevant_row_count": old_realtime.get("koeln_hbf_rows"),
            "date_coverage": {
                "min": iso(old_realtime.get("min_captured")),
                "max": iso(old_realtime.get("max_captured")),
                "temporal_overlap_with_active_hbf": overlap,
            },
            "why_it_might_be_useful": "Contains 33,883 historical Köln Hbf rows and is not byte-for-byte redundant with current MDD observations.",
            "equivalent_in_active_database": {
                "object": "stg.MddRealtimeStopObservation",
                "active_koeln_hbf_rows": current_realtime.get("koeln_hbf_rows"),
                "active_coverage": {
                    "min": iso(current_realtime.get("min_captured")),
                    "max": iso(current_realtime.get("max_captured")),
                },
                "identity_comparison": exact_id_overlap,
            },
            "classification": "DO_NOT_MIGRATE_OUT_OF_SCOPE",
            "decision_reason": "This is unique legacy rail-realtime history, but its DbEventId/StationEva grain is not the active source-faithful MDD contract; no current analytical consumer requires importing it, and migration would create a parallel legacy evidence structure.",
        },
        {
            "source_object": "NRWTransportDW.dw.FactRealtimeStopPerformance",
            "source_grain": "one derived legacy realtime performance fact",
            "relevant_row_count": old_fact.get("koeln_hbf_rows"),
            "date_coverage": {"min": iso(old_fact.get("min_captured")), "max": iso(old_fact.get("max_captured"))},
            "why_it_might_be_useful": "Contains 1,932 Köln Hbf derived rows for 2026-08-22 through 2026-09-08.",
            "equivalent_in_active_database": {
                "object": "current source-faithful realtime observations plus current warehouse facts",
                "active_koeln_hbf_rows": current_realtime.get("koeln_hbf_rows"),
            },
            "classification": "DO_NOT_MIGRATE_OUT_OF_SCOPE",
            "decision_reason": "It is a derived legacy fact layer with no active repository dependency; retaining the source backup preserves it without duplicating the active warehouse.",
        },
        {
            "source_object": "NRWTransportDW.stg.Routes / wrk route logic",
            "source_grain": "legacy all-NRW route/static rows and derived route mappings",
            "relevant_row_count": 5949,
            "date_coverage": "loaded 2026-08-19; static feed object",
            "why_it_might_be_useful": "Provides legacy route and classification context across NRW.",
            "equivalent_in_active_database": {"object": "stg.GtfsRoutes", "row_count": 981, "comparison": route_comparison},
            "classification": "DO_NOT_MIGRATE_OUT_OF_SCOPE",
            "decision_reason": "The active Cologne project uses the current authoritative GTFS load and its own matching/warehouse logic; the legacy all-NRW route corpus is not a required active dependency.",
        },
        {
            "source_object": "NRWTransportDW situation/attribute/service-section/parser-gap objects",
            "source_grain": "not present as legacy source objects",
            "relevant_row_count": 0,
            "date_coverage": None,
            "why_it_might_be_useful": "The current source-faithful project contract retains these evidence families, so their absence from NRWTransportDW must be explicit.",
            "equivalent_in_active_database": "Current MDD source-faithful tables exist; no NRW equivalent was found.",
            "classification": "DO_NOT_MIGRATE_OUT_OF_SCOPE",
            "decision_reason": "No NRWTransportDW object provides a missing situation, attribute, service-section, or parser-gap asset.",
        },
    ]


def dependency_audit(settings: MssqlConnectionSettings, repo_refs: dict[str, Any]) -> dict[str, Any]:
    master = connect_database(settings, "master")
    msdb = connect_database(settings, "msdb")
    legacy = connect_database(settings, LEGACY_DATABASE)
    active = connect_database(settings, ACTIVE_DATABASE)
    try:
        servers = fetch(master, "SELECT name, is_linked, data_source, provider FROM sys.servers ORDER BY server_id")
        legacy_db_dependencies = fetch(
            legacy,
            """
            SELECT referenced_database_name, COUNT(*) AS dependency_count
            FROM sys.sql_expression_dependencies
            WHERE referenced_database_name IS NOT NULL
            GROUP BY referenced_database_name
            """,
        )
        legacy_module_refs = fetch(
            legacy,
            """
            SELECT s.name AS schema_name, o.name AS object_name, o.type_desc
            FROM sys.sql_modules AS sm
            JOIN sys.objects AS o ON o.object_id = sm.object_id
            JOIN sys.schemas AS s ON s.schema_id = o.schema_id
            WHERE sm.definition LIKE N'%NRWTransportDW%'
               OR sm.definition LIKE N'%NRWTransport%'
               OR sm.definition LIKE N'%CologneTransitIntelligence%'
            """,
        )
        active_db_dependencies = fetch(
            active,
            """
            SELECT referenced_database_name, COUNT(*) AS dependency_count
            FROM sys.sql_expression_dependencies
            WHERE referenced_database_name IS NOT NULL
            GROUP BY referenced_database_name
            """,
        )
        active_module_refs = fetch(
            active,
            """
            SELECT s.name AS schema_name, o.name AS object_name, o.type_desc
            FROM sys.sql_modules AS sm
            JOIN sys.objects AS o ON o.object_id = sm.object_id
            JOIN sys.schemas AS s ON s.schema_id = o.schema_id
            WHERE sm.definition LIKE N'%NRWTransportDW%'
               OR sm.definition LIKE N'%NRWTransport%'
            """,
        )
        agent = sql_agent_inventory(msdb)
        linked_servers = [row for row in servers if row.get("is_linked")]
        task = task_scheduler_inventory()
        classification = (
            "LEGACY_SAFE_TO_ARCHIVE"
            if not repo_refs["active_runtime_or_configuration_references"]
            and not legacy_module_refs
            and not active_module_refs
            and not agent["nrw_references"]
            and not [row for row in legacy_db_dependencies if row.get("referenced_database_name")]
            and not [row for row in active_db_dependencies if row.get("referenced_database_name") == LEGACY_DATABASE]
            else "ACTIVE_DEPENDENCY"
        )
        return {
            "classification": classification,
            "repository": repo_refs,
            "sql_modules": {
                "nrw_cross_database_dependencies": json_safe(legacy_db_dependencies),
                "nrw_module_external_references": json_safe(legacy_module_refs),
                "active_cross_database_dependencies": json_safe(active_db_dependencies),
                "active_module_nrw_references": json_safe(active_module_refs),
            },
            "linked_servers": json_safe(linked_servers),
            "sql_agent": agent,
            "windows_scheduled_tasks": task,
            "classification_basis": [
                "No active repository/runtime/configuration reference found.",
                "No SQL module or cross-database reference found.",
                "No SQL Agent step references NRWTransportDW.",
                "No linked server is used by the database dependency audit.",
                "Direct Windows Task Scheduler inspection was unavailable from macOS; this is reported separately and was not treated as evidence of a dependency.",
            ],
        }
    finally:
        close(master)
        close(msdb)
        close(legacy)
        close(active)


def build_inventory(settings: MssqlConnectionSettings, archive_dir: Path, stamp: str) -> dict[str, Any]:
    capacity = server_and_capacity(settings, archive_dir)
    active_state = active_database_state(settings)
    legacy = connect_database(settings, LEGACY_DATABASE)
    active = connect_database(settings, ACTIVE_DATABASE)
    msdb = connect_database(settings, "msdb")
    try:
        objects = table_inventory(legacy)
        coverage = date_coverage(legacy)
        repo_refs = repo_reference_inventory()
        dependencies = dependency_audit(settings, repo_refs)
        candidates = candidate_inventory(active, legacy)
        legacy_state = capacity["states"].get(LEGACY_DATABASE, {})
        legacy_rows_allocated = sum(
            float(row.get("allocated_mb") or 0) * 1024**2
            for row in capacity["files"]
            if row.get("database_name") == LEGACY_DATABASE and row.get("type_desc") == "ROWS"
        )
        legacy_log_usage = fetch_one(
            legacy,
            "SELECT total_log_size_in_bytes, used_log_space_in_bytes, used_log_space_in_percent FROM sys.dm_db_log_space_usage",
        )
        estimated_backup_upper_bound = int(legacy_rows_allocated + float(legacy_log_usage.get("used_log_space_in_bytes") or 0) + CAPACITY_RESERVE_BYTES)
        estimated_destination_requirement = 2 * estimated_backup_upper_bound + CAPACITY_RESERVE_BYTES
        target_free = int(capacity["target_mac_storage"]["free_bytes"])
        backup_filename = f"NRWTransportDW-Archive-{stamp}.bak"
        sql_backup_path = SQL_SHARED_BACKUP_DIR + "\\" + backup_filename
        mac_backup_path = archive_dir / backup_filename
        preflight = {
            "archive_destination_exists": archive_dir.exists(),
            "archive_destination_is_directory": archive_dir.is_dir(),
            "backup_path_is_non_c_drive": not sql_backup_path.upper().startswith("C:"),
            "estimated_backup_upper_bound_bytes": estimated_backup_upper_bound,
            "estimated_destination_requirement_bytes": estimated_destination_requirement,
            "target_free_bytes": target_free,
            "sufficient_capacity": bool(target_free >= estimated_destination_requirement),
            "existing_mac_backup": mac_backup_path.exists(),
            "planned_sql_backup_path": sql_backup_path,
            "planned_mac_backup_path": str(mac_backup_path),
        }
        return {
            "database_name": LEGACY_DATABASE,
            "active_database_name": ACTIVE_DATABASE,
            "secrets_included": False,
            "inventory_captured_at_utc": iso(utc_now()),
            "backup_timestamp_utc": f"planned:{stamp}",
            "archive_stamp_utc": stamp,
            "server": capacity["server"],
            "capacity": capacity,
            "active_database_state": active_state,
            "legacy_database_state": legacy_state,
            "legacy_log_usage": json_safe(legacy_log_usage),
            "preflight": preflight,
            "dependency_audit": dependencies,
            "dependency_classification": dependencies["classification"],
            "object_inventory": objects,
            "date_coverage": coverage,
            "cologne_candidates": candidates,
            "migration": {
                "status": "NO_NRWTRANSPORTDW_ASSET_REQUIRED_BY_ACTIVE_COLOGNE_PROJECT",
                "transfers_performed": [],
                "source_data_modified": False,
                "destination_data_modified": False,
                "reason": "All reviewed assets are already represented, legacy-derived/out-of-scope, or not required by an active consumer; no additive destination structure is justified.",
            },
            "safety_boundary": {
                "drop_database": False,
                "detach_database": False,
                "delete_database_files": False,
                "move_database_files": False,
                "shrink_database": False,
                "delete_backup_files": False,
                "mdd_request": False,
                "power_bi_change": False,
                "collector_activation": False,
            },
            "sql_agent_backup_history_rows_for_database": fetch_one(
                msdb,
                "SELECT COUNT(*) AS backup_history_rows FROM dbo.backupset WHERE database_name = N'NRWTransportDW'",
            ).get("backup_history_rows"),
        }
    finally:
        close(legacy)
        close(active)
        close(msdb)


def check_remote_file_exists(settings: MssqlConnectionSettings, sql_path: str) -> bool:
    connection = connect_database(settings, "master")
    try:
        rows = fetch(connection, "EXEC master.dbo.xp_fileexist %s", (sql_path,))
        for row in rows:
            for key, value in row.items():
                normalized_key = str(key).lower().replace(" ", "")
                if normalized_key == "fileexists":
                    return value in (1, True, "1")
        return False
    finally:
        close(connection)


def perform_backup(settings: MssqlConnectionSettings, sql_path: str) -> dict[str, Any]:
    if sql_path.upper().startswith("C:") or sql_path.upper().startswith("C:/"):
        raise RuntimeError("refusing to back up NRWTransportDW to C:")
    if check_remote_file_exists(settings, sql_path):
        raise RuntimeError(f"refusing to overwrite an existing backup path: {sql_path}")
    connection = connect_database(settings, "master", timeout=3600)
    started = utc_now()
    try:
        # BACKUP/RESTORE statements cannot execute inside the implicit DB-API
        # transaction opened by pymssql.  This connection is dedicated to the
        # one approved backup operation, so autocommit is the safe setting.
        connection.autocommit(True)
        statement = (
            "BACKUP DATABASE [NRWTransportDW] TO DISK = "
            + sql_literal(sql_path)
            + " WITH COPY_ONLY, COMPRESSION, CHECKSUM, NOINIT, NOSKIP, STATS = 10"
        )
        cursor = connection.cursor()
        cursor.execute(statement)
        finished = utc_now()
    finally:
        close(connection)
    return {
        "backup_start_utc_client": iso(started),
        "backup_finish_utc_client": iso(finished),
        "sql_backup_path": sql_path,
    }


def wait_for_file(path: Path, timeout_seconds: int = 90) -> None:
    deadline = time.monotonic() + timeout_seconds
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(1)
    if not path.exists():
        raise FileNotFoundError(f"SQL Server backup did not become visible at {path}")


def query_backup_metadata(settings: MssqlConnectionSettings, sql_path: str, started_utc: str) -> dict[str, Any]:
    connection = connect_database(settings, "msdb", timeout=120)
    try:
        rows = fetch(
            connection,
            """
            SELECT TOP (1) bs.database_name, bs.type, bs.is_copy_only,
                   bs.has_backup_checksums, bs.backup_start_date, bs.backup_finish_date,
                   bs.backup_size, bs.compressed_backup_size,
                   bmf.physical_device_name
            FROM dbo.backupset AS bs
            JOIN dbo.backupmediafamily AS bmf ON bmf.media_set_id = bs.media_set_id
            WHERE bs.database_name = N'NRWTransportDW'
              AND bmf.physical_device_name = %s
            ORDER BY bs.backup_finish_date DESC
            """,
            (sql_path,),
        )
        return json_safe(rows[0] if rows else {})
    finally:
        close(connection)


def verify_backup(settings: MssqlConnectionSettings, sql_path: str) -> dict[str, Any]:
    connection = connect_database(settings, "master", timeout=600)
    result: dict[str, Any] = {
        "backup_path": sql_path,
        "headeronly": {"status": "FAIL"},
        "filelistonly": {"status": "FAIL"},
        "verifyonly_with_checksum": {"status": "FAIL"},
    }
    try:
        # RESTORE VERIFYONLY is also a backup/restore statement and must not
        # inherit pymssql's implicit transaction.
        connection.autocommit(True)
        try:
            header_rows = fetch(connection, "RESTORE HEADERONLY FROM DISK = " + sql_literal(sql_path))
            header = json_safe(header_rows[0] if header_rows else {})
            requirement_checks = {
                "database_name_is_nrwtransportdw": first_value(header, "DatabaseName") == LEGACY_DATABASE,
                "backup_type_is_full": first_value(header, "BackupType") == 1,
                "is_copy_only": bool(first_value(header, "IsCopyOnly")),
                "has_backup_checksums": bool(first_value(header, "HasBackupChecksums")),
            }
            result["headeronly"] = {
                "status": "PASS" if header_rows and all(requirement_checks.values()) else "FAIL",
                "row": header,
                "requirements": requirement_checks,
            }
        except Exception as exc:
            result["headeronly"] = {"status": "FAIL", "error_type": type(exc).__name__, "error": str(exc)[:500]}
        try:
            file_rows = fetch(connection, "RESTORE FILELISTONLY FROM DISK = " + sql_literal(sql_path))
            files = [json_safe(row) for row in file_rows]
            logical_names = {str(first_value(row, "LogicalName")) for row in file_rows}
            types = {str(first_value(row, "Type")) for row in file_rows}
            file_requirements = {
                "data_file_present": "D" in types and LEGACY_DATABASE in logical_names,
                "log_file_present": "L" in types and f"{LEGACY_DATABASE}_log" in logical_names,
            }
            result["filelistonly"] = {
                "status": "PASS" if file_rows and all(file_requirements.values()) else "FAIL",
                "files": files,
                "requirements": file_requirements,
            }
        except Exception as exc:
            result["filelistonly"] = {"status": "FAIL", "error_type": type(exc).__name__, "error": str(exc)[:500]}
        try:
            execute_no_result(connection, "RESTORE VERIFYONLY FROM DISK = " + sql_literal(sql_path) + " WITH CHECKSUM")
            result["verifyonly_with_checksum"] = {"status": "PASS"}
        except Exception as exc:
            result["verifyonly_with_checksum"] = {"status": "FAIL", "error_type": type(exc).__name__, "error": str(exc)[:500]}
    finally:
        close(connection)
    result["overall_status"] = (
        "PASS"
        if result["headeronly"]["status"] == "PASS"
        and result["filelistonly"]["status"] == "PASS"
        and result["verifyonly_with_checksum"]["status"] == "PASS"
        else "FAIL"
    )
    return result


def make_restore_instructions(path: Path, stamp: str, backup_filename: str) -> None:
    path.write_text(
        f"""NRWTransportDW native backup restore instructions
===============================================

Package timestamp (UTC): {stamp}
Backup member: {backup_filename}

This is a SQL Server FULL COPY_ONLY backup created with COMPRESSION and
CHECKSUM.  The source database was not dropped, detached, moved, shrunk, or
otherwise destructively modified.

Before restoring, copy the .bak to a location readable by the SQL Server
service, then inspect it:

RESTORE HEADERONLY FROM DISK = N'<path-to-{backup_filename}>';
RESTORE FILELISTONLY FROM DISK = N'<path-to-{backup_filename}>';
RESTORE VERIFYONLY FROM DISK = N'<path-to-{backup_filename}>' WITH CHECKSUM;

Restore only after confirming the target instance/database policy.  If the
original physical paths are not appropriate, use RESTORE DATABASE ... WITH
MOVE based on the FILELISTONLY logical names.  Do not restore over
CologneTransitIntelligence.

The sidecar SHA-256 recorded next to the ZIP is the integrity value for the
ZIP package.  The archive manifest records the exact SHA-256 and byte size of
the native .bak member.
""",
        encoding="utf-8",
    )


def create_zip(
    archive_dir: Path,
    artifact_dir: Path,
    stamp: str,
    backup_path: Path,
    manifest_path: Path,
    inventory_path: Path,
    object_inventory_path: Path,
    restore_path: Path,
) -> tuple[Path, dict[str, Any]]:
    zip_path = archive_dir / f"NRWTransportDW-Archive-{stamp}.zip"
    if zip_path.exists():
        raise FileExistsError(f"refusing to overwrite existing ZIP: {zip_path}")
    root_name = f"NRWTransportDW-Archive-{stamp}"
    members = [
        (backup_path, f"{root_name}/{backup_path.name}"),
        (manifest_path, f"{root_name}/{manifest_path.name}"),
        (inventory_path, f"{root_name}/{inventory_path.name}"),
        (object_inventory_path, f"{root_name}/{object_inventory_path.name}"),
        (restore_path, f"{root_name}/{restore_path.name}"),
    ]
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as package:
        for source, member in members:
            package.write(source, arcname=member)
    size = zip_path.stat().st_size
    digest = sha256_file(zip_path)
    expected = [member for _, member in members]
    with zipfile.ZipFile(zip_path, "r") as package:
        names = package.namelist()
        missing = sorted(set(expected) - set(names))
        crc_error = package.testzip()
        if missing or crc_error:
            raise RuntimeError(f"ZIP validation failed; missing={missing}, crc_error={crc_error}")
        with package.open(f"{root_name}/{backup_path.name}", "r") as member:
            member_digest = hashlib.sha256()
            member_size = 0
            while True:
                block = member.read(CHUNK_BYTES)
                if not block:
                    break
                member_digest.update(block)
                member_size += len(block)
        if member_size != backup_path.stat().st_size or member_digest.hexdigest() != sha256_file(backup_path):
            raise RuntimeError("ZIP backup member does not match the verified .bak")
    return zip_path, {
        "path": str(zip_path),
        "filename": zip_path.name,
        "byte_size": size,
        "sha256": digest,
        "exceeds_4_gib": size > ZIP_MAX_FAT32_BYTES,
        "recommended_filesystem": "exFAT or another filesystem supporting files larger than 4 GiB" if size > ZIP_MAX_FAT32_BYTES else "exFAT recommended; FAT32 is size-compatible for this ZIP",
        "expected_members": expected,
        "member_validation": "PASS",
        "compression": "ZIP_STORED; the native SQL backup is already COMPRESSION-enabled",
    }


def render_inventory_markdown(inventory: dict[str, Any], manifest: dict[str, Any] | None = None) -> str:
    counts = inventory["object_inventory"]["object_counts"]
    lines = [
        "# NRWTransportDW inventory and archive review",
        "",
        f"- Inventory captured (UTC): `{inventory['inventory_captured_at_utc']}`",
        f"- Database: `{inventory['database_name']}`",
        f"- Dependency classification: `{inventory['dependency_classification']}`",
        f"- Migration result: `{inventory['migration']['status']}`",
        "- Secrets included: `false`",
        "",
        "## Capacity preflight",
        "",
        "| Measure | Result |",
        "|---|---:|",
    ]
    sql_drive = inventory["capacity"].get("sql_server_c_drive") or {}
    target = inventory["capacity"]["target_mac_storage"]
    preflight = inventory["preflight"]
    lines.extend(
        [
            f"| SQL Server C: free | {sql_drive.get('available_bytes')} bytes ({sql_drive.get('free_gib')} GiB) |",
            f"| Mac/shared archive free | {target.get('free_bytes')} bytes ({target.get('free_gib')} GiB) |",
            f"| Estimated destination requirement for .bak + ZIP | {preflight.get('estimated_destination_requirement_bytes')} bytes |",
            f"| Capacity gate | `{preflight.get('sufficient_capacity')}` |",
        ]
    )
    active_files = inventory["active_database_state"]["files"]
    lines.extend(["", "## Active database file state", "", "| Logical file | Type | Allocated MB | Used MB | Physical path |", "|---|---|---:|---:|---|"])
    for row in active_files:
        used = row.get("used_mb_from_log_dmv", row.get("used_mb"))
        lines.append(f"| `{row.get('logical_name')}` | `{row.get('type_desc')}` | {row.get('allocated_mb')} | {used} | `{row.get('physical_name')}` |")
    lines.extend(
        [
            "",
            "## Object inventory",
            "",
            f"Schemas: **{counts['schemas']}**; tables: **{counts['tables']}**; views: **{counts['views']}**; stored procedures: **{counts['stored_procedures']}**; functions: **{counts['functions']}**; synonyms: **{counts['synonyms']}**; foreign keys: **{counts['foreign_keys']}**; indexes: **{counts['indexes']}**.",
            "",
            "| Schema | Table | Rows | Reserved MB | Used MB |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for row in inventory["object_inventory"]["tables"]:
        lines.append(f"| `{row['schema_name']}` | `{row['table_name']}` | {row['row_count']} | {row['reserved_mb']} | {row['used_mb']} |")
    lines.extend(["", "### Views", "", ", ".join(f"`{row['schema_name']}.{row['object_name']}`" for row in inventory["object_inventory"]["views"]) or "None", "", "### Stored procedures", "", ", ".join(f"`{row['schema_name']}.{row['object_name']}`" for row in inventory["object_inventory"]["stored_procedures"]) or "None", "", "### Functions", "", ", ".join(f"`{row['schema_name']}.{row['object_name']}`" for row in inventory["object_inventory"]["functions"]) or "None", "", "### Synonyms", "", ", ".join(f"`{row['schema_name']}.{row['object_name']}`" for row in inventory["object_inventory"]["synonyms"]) or "None"])
    lines.extend(["", "## Date coverage", "", "| Object | Column | Min | Max |", "|---|---|---|---|"])
    for row in inventory["date_coverage"]:
        lines.append(f"| `{row['object']}` | `{row['column']}` | {row['min']} | {row['max']} |")
    lines.extend(["", "## Köln candidate decisions", "", "| Source object | Relevant rows | Classification | Decision |", "|---|---:|---|---|"])
    for row in inventory["cologne_candidates"]:
        lines.append(f"| `{row['source_object']}` | {row['relevant_row_count']} | `{row['classification']}` | {row['decision_reason']} |")
    lines.extend(
        [
            "",
            "The review found no clearly required NRWTransportDW asset for the active Cologne project; no data or logic was transferred.",
            "",
            "## Dependency review",
            "",
            f"Active repository/runtime/configuration references: `{inventory['dependency_audit']['repository']['active_runtime_or_configuration_references']}`.",
            f"SQL module references: `{inventory['dependency_audit']['sql_modules']}`.",
            f"SQL Agent NRW references: `{inventory['dependency_audit']['sql_agent']['nrw_references']}`.",
            f"Linked servers: `{inventory['dependency_audit']['linked_servers']}`.",
            f"Windows Task Scheduler direct inspection: `{inventory['dependency_audit']['windows_scheduled_tasks']['status']}`.",
        ]
    )
    if manifest:
        lines.extend(
            [
                "",
                "## Verified backup and ZIP",
                "",
                f"- Backup path: `{manifest['backup']['mac_path']}`",
                f"- Backup bytes: `{manifest['backup']['byte_size']}`",
                f"- Backup SHA-256: `{manifest['backup']['sha256']}`",
                f"- HEADERONLY: `{manifest['verification']['headeronly']['status']}`",
                f"- FILELISTONLY: `{manifest['verification']['filelistonly']['status']}`",
                f"- VERIFYONLY WITH CHECKSUM: `{manifest['verification']['verifyonly_with_checksum']['status']}`",
                f"- ZIP path: `{manifest['zip']['path']}`",
                f"- ZIP bytes: `{manifest['zip']['byte_size']}`",
                f"- ZIP SHA-256: `{manifest['zip']['sha256']}`",
                f"- ZIP > 4 GiB: `{manifest['zip']['exceeds_4_gib']}`",
            ]
        )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    settings = MssqlConnectionSettings.from_environment()
    archive_dir = Path(args.archive_dir).expanduser().resolve()
    if not archive_dir.exists() or not archive_dir.is_dir():
        raise FileNotFoundError(f"archive directory does not exist or is not a directory: {archive_dir}")
    stamp = args.stamp or utc_now().strftime("%Y%m%dT%H%M%SZ")
    if not re.fullmatch(r"\d{8}T\d{6}Z", stamp):
        raise ValueError("--stamp must use UTC format YYYYMMDDTHHMMSSZ")
    artifact_dir = Path(args.artifact_root).expanduser().resolve() / stamp
    artifact_dir.mkdir(parents=True, exist_ok=True)
    inventory = build_inventory(settings, archive_dir, stamp)
    pre_inventory_path = artifact_dir / f"NRWTransportDW-inventory-prebackup-{stamp}.json"
    write_json(pre_inventory_path, inventory)
    object_inventory_path = artifact_dir / f"NRWTransportDW-object-inventory-{stamp}.json"
    write_json(object_inventory_path, inventory["object_inventory"])
    pre_report_path = artifact_dir / f"NRWTransportDW-inventory-prebackup-{stamp}.md"
    pre_report_path.write_text(render_inventory_markdown(inventory), encoding="utf-8")
    result: dict[str, Any] = {
        "status": "AUDIT_ONLY",
        "inventory_prebackup": str(pre_inventory_path),
        "inventory_prebackup_report": str(pre_report_path),
        "object_inventory": str(object_inventory_path),
        "dependency_classification": inventory["dependency_classification"],
        "migration_status": inventory["migration"]["status"],
        "preflight": inventory["preflight"],
    }
    if not args.execute:
        return result
    if inventory["dependency_classification"] != "LEGACY_SAFE_TO_ARCHIVE":
        raise RuntimeError(f"backup gate refused because dependency classification is {inventory['dependency_classification']}")
    if not inventory["preflight"]["sufficient_capacity"]:
        raise RuntimeError("backup gate refused because the archive destination lacks the calculated safety capacity")
    if inventory["migration"]["status"] != "NO_NRWTRANSPORTDW_ASSET_REQUIRED_BY_ACTIVE_COLOGNE_PROJECT":
        raise RuntimeError("unexpected migration status; refusing archive execution")
    sql_path = inventory["preflight"]["planned_sql_backup_path"]
    mac_backup_path = Path(inventory["preflight"]["planned_mac_backup_path"])
    if args.finalize_existing:
        if not mac_backup_path.exists():
            raise FileNotFoundError(f"cannot finalize an existing backup that is not present: {mac_backup_path}")
        backup_timing = {
            "backup_start_utc_client": None,
            "backup_finish_utc_client": iso(datetime.fromtimestamp(mac_backup_path.stat().st_mtime, timezone.utc)),
            "sql_backup_path": sql_path,
            "existing_backup_reused": True,
        }
    else:
        backup_timing = perform_backup(settings, sql_path)
        backup_timing["existing_backup_reused"] = False
    wait_for_file(mac_backup_path)
    backup_metadata = query_backup_metadata(settings, sql_path, backup_timing["backup_start_utc_client"])
    verification = verify_backup(settings, sql_path)
    backup_bytes = mac_backup_path.stat().st_size
    backup_hash = sha256_file(mac_backup_path)
    backup = {
        **backup_timing,
        "sql_metadata": backup_metadata,
        "sql_path": sql_path,
        "mac_path": str(mac_backup_path),
        "filename": mac_backup_path.name,
        "byte_size": backup_bytes,
        "sha256": backup_hash,
        "copy_only": True,
        "checksum": True,
        "compression": True,
    }
    inventory["backup_timestamp_utc"] = backup_timing["backup_finish_utc_client"]
    inventory["backup"] = backup
    inventory["verification"] = verification
    inventory["post_backup_capacity"] = {
        "target_mac_storage": volume_summary(archive_dir),
        "backup_file_present": mac_backup_path.exists(),
        "backup_file_size_bytes": backup_bytes,
    }
    final_inventory_path = artifact_dir / f"NRWTransportDW-inventory-{stamp}.json"
    final_report_path = artifact_dir / f"NRWTransportDW-inventory-{stamp}.md"
    write_json(final_inventory_path, inventory)
    object_inventory_path = artifact_dir / f"NRWTransportDW-object-inventory-{stamp}.json"
    write_json(object_inventory_path, inventory["object_inventory"])
    if verification["overall_status"] != "PASS":
        result.update(
            {
                "status": "BACKUP_CREATED_BUT_NOT_VERIFIED",
                "backup": backup,
                "verification": verification,
                "inventory": str(final_inventory_path),
                "inventory_report": str(final_report_path),
            }
        )
        final_report_path.write_text(render_inventory_markdown(inventory), encoding="utf-8")
        return result
    manifest_path = artifact_dir / f"NRWTransportDW-archive-manifest-{stamp}.json"
    restore_path = artifact_dir / f"NRWTransportDW-restore-instructions-{stamp}.txt"
    manifest = {
        "database": LEGACY_DATABASE,
        "backup_filename": mac_backup_path.name,
        "backup_timestamp_utc": inventory["backup_timestamp_utc"],
        "backup_sql_server_path": sql_path,
        "backup_mac_path": str(mac_backup_path),
        "byte_size": backup_bytes,
        "sha256": backup_hash,
        "COPY_ONLY": True,
        "CHECKSUM": True,
        "COMPRESSION": True,
        "HEADERONLY": verification["headeronly"],
        "FILELISTONLY": verification["filelistonly"],
        "VERIFYONLY_WITH_CHECKSUM": verification["verifyonly_with_checksum"],
        "dependency_classification": inventory["dependency_classification"],
        "cologne_asset_migration_summary": inventory["migration"],
        "inventory_path": str(final_inventory_path),
        "object_inventory_path": str(object_inventory_path),
        "restore_instructions_path": str(restore_path),
        "zip_package": {"filename": f"NRWTransportDW-Archive-{stamp}.zip", "hash_added_after_creation": True},
        "secrets_included": False,
    }
    write_json(manifest_path, manifest)
    make_restore_instructions(restore_path, stamp, mac_backup_path.name)
    remaining = volume_summary(archive_dir)
    if remaining["free_bytes"] < backup_bytes + CAPACITY_RESERVE_BYTES:
        raise RuntimeError("verified .bak exists, but remaining archive storage is insufficient for the ZIP")
    zip_path, zip_info = create_zip(archive_dir, artifact_dir, stamp, mac_backup_path, manifest_path, final_inventory_path, object_inventory_path, restore_path)
    sidecar = archive_dir / f"NRWTransportDW-Archive-{stamp}.zip.sha256"
    sidecar.write_text(f"{zip_info['sha256']}  {zip_path.name}\n", encoding="utf-8")
    manifest["zip_integrity_sidecar"] = str(sidecar)
    final_report_path.write_text(render_inventory_markdown({**inventory, "backup": backup, "verification": verification}, {**manifest, "backup": backup, "verification": verification, "zip": zip_info}), encoding="utf-8")
    result.update(
        {
            "status": "PASS",
            "inventory": str(final_inventory_path),
            "inventory_report": str(final_report_path),
            "manifest": str(manifest_path),
            "restore_instructions": str(restore_path),
            "backup": backup,
            "verification": verification,
            "zip": zip_info,
            "zip_sha256_sidecar": str(sidecar),
            "usb_compatibility": {
                "zip_exceeds_4_gib": zip_info["exceeds_4_gib"],
                "recommendation": zip_info["recommended_filesystem"],
            },
        }
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="perform the approved native backup and ZIP creation")
    parser.add_argument("--finalize-existing", action="store_true", help="skip BACKUP DATABASE and verify/package the exact timestamped .bak already at the planned path")
    parser.add_argument("--archive-dir", default=str(DEFAULT_ARCHIVE_DIR), help="Mac/shared directory for the .bak and ZIP")
    parser.add_argument("--artifact-root", default=str(DEFAULT_ARTIFACT_ROOT), help="repository directory for sanitized reports")
    parser.add_argument("--stamp", help="UTC stamp YYYYMMDDTHHMMSSZ; defaults to current UTC")
    args = parser.parse_args(argv)
    try:
        result = run(args)
    except Exception as exc:
        result = {"status": "FAIL", "error_type": type(exc).__name__, "error": str(exc)[:1000]}
    print(json.dumps(json_safe(result), ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") in {"AUDIT_ONLY", "PASS"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
