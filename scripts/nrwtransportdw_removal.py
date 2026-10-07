#!/usr/bin/env python3
"""Remove the archived legacy NRWTransportDW database with a live safety gate.

The default mode is read-only.  ``--execute-drop`` is required before the
script can issue the one approved ``ALTER DATABASE``/``DROP DATABASE``
sequence.  The script never drops, alters, or deletes any other database and
never deletes archive material.  It writes sanitized JSON/Markdown evidence
for the pre-drop state, destructive result, and post-drop validation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlConnectionSettings,
    connect_mssql,
)


ACTIVE_DATABASE = "CologneTransitIntelligence"
LEGACY_DATABASE = "NRWTransportDW"
ARCHIVE_DIR = Path("/Users/admin/Documents/CologneTransitData")
ARTIFACT_DIR = ROOT / "docs" / "archives" / "nrwtransportdw" / "20261007T112120Z"
SQL_BACKUP_PATH = r"\\vmware-host\Shared Folders\CologneTransitData\NRWTransportDW-Archive-20261007T112120Z.bak"
MAC_BACKUP_PATH = ARCHIVE_DIR / "NRWTransportDW-Archive-20261007T112120Z.bak"
ZIP_PATH = ARCHIVE_DIR / "NRWTransportDW-Archive-20261007T112120Z.zip"
MANIFEST_PATH = ARTIFACT_DIR / "NRWTransportDW-archive-manifest-20261007T112120Z.json"
EXPECTED_BAK_BYTES = 1518825984
EXPECTED_BAK_SHA256 = "573faa7598bd37e1f70300f04d165f2c441fabdbdad816f30987d27210ea665b"
EXPECTED_ZIP_BYTES = 1518968697
EXPECTED_ZIP_SHA256 = "d096366e88a4b823bb8e4ad3b493dea274061419d8d65700f540582efb9aba7c"
CHUNK_BYTES = 8 * 1024 * 1024
SYSTEM_DATABASES = {"master", "model", "msdb", "tempdb"}


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
    if isinstance(value, Mapping):
        return {str(key): json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(child) for child in value]
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(CHUNK_BYTES)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


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


def close(connection: Any) -> None:
    try:
        connection.close()
    except Exception:
        pass


def fetch(connection: Any, statement: str, parameters: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
    cursor = connection.cursor()
    cursor.execute(statement, parameters or ())
    return [dict(row) for row in cursor.fetchall()]


def fetch_one(connection: Any, statement: str, parameters: tuple[Any, ...] | None = None) -> dict[str, Any]:
    rows = fetch(connection, statement, parameters)
    return rows[0] if rows else {}


def file_exists_from_xp(master: Any, path: str) -> bool:
    rows = fetch(master, "EXEC master.dbo.xp_fileexist %s", (path,))
    for row in rows:
        for key, value in row.items():
            if str(key).lower().replace(" ", "") == "fileexists":
                return value in (1, True, "1")
    return False


def file_evidence(path: Path, expected_bytes: int, expected_sha256: str, *, hash_file: bool = True) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "path": str(path),
        "exists": path.is_file(),
        "expected_byte_size": expected_bytes,
        "expected_sha256": expected_sha256,
    }
    if not path.is_file():
        evidence.update({"actual_byte_size": None, "actual_sha256": None, "size_matches": False, "sha256_matches": False})
        return evidence
    evidence["actual_byte_size"] = path.stat().st_size
    evidence["size_matches"] = evidence["actual_byte_size"] == expected_bytes
    if hash_file:
        evidence["actual_sha256"] = sha256_file(path)
        evidence["sha256_matches"] = evidence["actual_sha256"] == expected_sha256
    else:
        evidence["actual_sha256"] = None
        evidence["sha256_matches"] = None
    return evidence


def archive_preflight() -> dict[str, Any]:
    manifest: dict[str, Any] = {}
    if MANIFEST_PATH.is_file():
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    bak = file_evidence(MAC_BACKUP_PATH, EXPECTED_BAK_BYTES, EXPECTED_BAK_SHA256)
    zip_evidence = file_evidence(ZIP_PATH, EXPECTED_ZIP_BYTES, EXPECTED_ZIP_SHA256)
    manifest_matches = {
        "manifest_exists": bool(manifest),
        "database": manifest.get("database") == LEGACY_DATABASE,
        "backup_filename": manifest.get("backup_filename") == MAC_BACKUP_PATH.name,
        "byte_size": manifest.get("byte_size") == EXPECTED_BAK_BYTES,
        "sha256": manifest.get("sha256") == EXPECTED_BAK_SHA256,
        "dependency_classification": manifest.get("dependency_classification") == "LEGACY_SAFE_TO_ARCHIVE",
        "migration_status": manifest.get("cologne_asset_migration_summary", {}).get("status")
        == "NO_NRWTRANSPORTDW_ASSET_REQUIRED_BY_ACTIVE_COLOGNE_PROJECT",
        "headeronly": manifest.get("HEADERONLY", {}).get("status") == "PASS",
        "filelistonly": manifest.get("FILELISTONLY", {}).get("status") == "PASS",
        "verifyonly_with_checksum": manifest.get("VERIFYONLY_WITH_CHECKSUM", {}).get("status") == "PASS",
    }
    return {
        "user_confirmed_verified_usb_copy": True,
        "sql_server_backup_path": SQL_BACKUP_PATH,
        "bak": bak,
        "zip": zip_evidence,
        "manifest_path": str(MANIFEST_PATH),
        "manifest_checks": manifest_matches,
        "archive_gate_pass": all(
            [
                bak.get("exists"),
                bak.get("size_matches"),
                bak.get("sha256_matches"),
                zip_evidence.get("exists"),
                zip_evidence.get("size_matches"),
                zip_evidence.get("sha256_matches"),
                *manifest_matches.values(),
            ]
        ),
    }


def database_snapshot(master: Any) -> dict[str, Any]:
    server = fetch_one(
        master,
        """
        SELECT CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128)) AS product_version,
               CAST(SERVERPROPERTY('ProductLevel') AS nvarchar(128)) AS product_level,
               CAST(SERVERPROPERTY('Edition') AS nvarchar(256)) AS edition,
               CAST(SERVERPROPERTY('MachineName') AS nvarchar(128)) AS machine_name,
               CAST(SERVERPROPERTY('ServerName') AS nvarchar(128)) AS server_name,
               CAST(SERVERPROPERTY('Collation') AS nvarchar(128)) AS collation,
               CONVERT(datetime2(7), osi.sqlserver_start_time) AS sqlserver_start_time
        FROM sys.dm_os_sys_info AS osi
        """,
    )
    states = fetch(
        master,
        """
        SELECT database_id, name, state_desc, user_access_desc, recovery_model_desc,
               is_read_only, create_date, log_reuse_wait_desc
        FROM sys.databases
        ORDER BY database_id
        """,
    )
    files = fetch(
        master,
        """
        SELECT mf.database_id, DB_NAME(mf.database_id) AS database_name,
               mf.file_id, mf.name AS logical_name, mf.type_desc, mf.physical_name,
               CAST(mf.size AS bigint) * 8192 AS allocated_bytes,
               mf.size * 8.0 / 1024 AS allocated_mb,
               mf.growth, mf.is_percent_growth,
               CASE WHEN mf.max_size = -1 THEN NULL ELSE CAST(mf.max_size AS bigint) * 8192 END AS max_size_bytes
        FROM sys.master_files AS mf
        WHERE DB_NAME(mf.database_id) IN (N'CologneTransitIntelligence', N'NRWTransportDW')
        ORDER BY database_name, mf.file_id
        """,
    )
    legacy_files = [row for row in files if row.get("database_name") == LEGACY_DATABASE]
    active_files = [row for row in files if row.get("database_name") == ACTIVE_DATABASE]
    c_volume = fetch_one(
        master,
        """
        SELECT TOP (1) v.volume_mount_point, v.total_bytes, v.available_bytes
        FROM sys.master_files AS mf
        CROSS APPLY sys.dm_os_volume_stats(mf.database_id, mf.file_id) AS v
        WHERE v.volume_mount_point LIKE N'C:%'
        ORDER BY v.volume_mount_point
        """,
    )
    states_by_name = {row["name"]: json_safe(row) for row in states}
    return {
        "server": json_safe(server),
        "states": [json_safe(row) for row in states],
        "states_by_name": states_by_name,
        "files": [json_safe(row) for row in files],
        "legacy_files": [json_safe(row) for row in legacy_files],
        "active_files": [json_safe(row) for row in active_files],
        "legacy_allocated_bytes": sum(int(row.get("allocated_bytes") or 0) for row in legacy_files),
        "active_allocated_bytes": sum(int(row.get("allocated_bytes") or 0) for row in active_files),
        "c_volume": json_safe(c_volume),
    }


def active_schema_snapshot(active: Any) -> dict[str, Any]:
    rows = fetch(
        active,
        """
        SELECT s.name AS schema_name, o.name AS object_name, o.type, o.type_desc
        FROM sys.objects AS o
        JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        WHERE o.is_ms_shipped = 0
        ORDER BY s.name, o.type, o.name
        """,
    )
    tables = fetch(
        active,
        """
        SELECT s.name AS schema_name, t.name AS table_name
        FROM sys.tables AS t
        JOIN sys.schemas AS s ON s.schema_id = t.schema_id
        WHERE t.is_ms_shipped = 0
        ORDER BY s.name, t.name
        """,
    )
    return {
        "objects": [json_safe(row) for row in rows],
        "tables": [json_safe(row) for row in tables],
        "object_keys": [f"{row['schema_name']}.{row['object_name']}:{row['type']}" for row in rows],
        "table_keys": [f"{row['schema_name']}.{row['table_name']}" for row in tables],
    }


def module_and_synonym_references(connection: Any, database: str) -> dict[str, Any]:
    modules = fetch(
        connection,
        """
        SELECT s.name AS schema_name, o.name AS object_name, o.type_desc,
               sm.definition
        FROM sys.sql_modules AS sm
        JOIN sys.objects AS o ON o.object_id = sm.object_id
        JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        WHERE o.is_ms_shipped = 0
          AND (sm.definition LIKE N'%NRWTransportDW%'
               OR sm.definition LIKE N'%NRWTransport%')
        ORDER BY s.name, o.name
        """,
    )
    module_refs: list[dict[str, Any]] = []
    for row in modules:
        definition = str(row.pop("definition", "") or "")
        module_refs.append(
            {
                **json_safe(row),
                "definition_length": len(definition),
                "definition_sha256": hashlib.sha256(definition.encode("utf-8", "replace")).hexdigest(),
            }
        )
    dependencies = fetch(
        connection,
        """
        SELECT referenced_database_name, COUNT_BIG(*) AS dependency_count
        FROM sys.sql_expression_dependencies
        WHERE referenced_database_name IS NOT NULL
        GROUP BY referenced_database_name
        ORDER BY referenced_database_name
        """,
    )
    synonyms = fetch(
        connection,
        """
        SELECT s.name AS schema_name, sy.name AS synonym_name, sy.base_object_name
        FROM sys.synonyms AS sy
        JOIN sys.schemas AS s ON s.schema_id = sy.schema_id
        WHERE sy.base_object_name LIKE N'%NRWTransportDW%'
           OR sy.base_object_name LIKE N'%NRWTransport%'
        ORDER BY s.name, sy.name
        """,
    )
    return {
        "database": database,
        "module_references": module_refs,
        "cross_database_dependencies": json_safe(dependencies),
        "synonym_references": json_safe(synonyms),
    }


def sql_agent_references(msdb: Any) -> dict[str, Any]:
    rows = fetch(
        msdb,
        """
        SELECT j.name AS job_name, j.enabled, js.step_id, js.step_name,
               js.subsystem, js.command
        FROM dbo.sysjobs AS j
        JOIN dbo.sysjobsteps AS js ON js.job_id = j.job_id
        WHERE j.name LIKE N'%NRWTransport%'
           OR js.command LIKE N'%NRWTransport%'
        ORDER BY j.name, js.step_id
        """,
    )
    refs: list[dict[str, Any]] = []
    for row in rows:
        command = str(row.pop("command", "") or "")
        refs.append(
            {
                **json_safe(row),
                "command_length": len(command),
                "command_sha256": hashlib.sha256(command.encode("utf-8", "replace")).hexdigest(),
            }
        )
    return {"references": refs, "status": "PASS"}


def linked_servers(master: Any) -> list[dict[str, Any]]:
    return json_safe(
        fetch(
            master,
            """
            SELECT name, is_linked, data_source, provider, product
            FROM sys.servers
            WHERE is_linked = 1
            ORDER BY name
            """,
        )
    )


def active_requests(master: Any) -> list[dict[str, Any]]:
    rows = fetch(
        master,
        """
        SELECT r.session_id, s.login_name, s.host_name, s.program_name,
               r.status, r.command, r.database_id,
               DB_NAME(r.database_id) AS database_name,
               CONVERT(nvarchar(4000), st.text) AS query_text
        FROM sys.dm_exec_requests AS r
        JOIN sys.dm_exec_sessions AS s ON s.session_id = r.session_id
        OUTER APPLY sys.dm_exec_sql_text(r.sql_handle) AS st
        WHERE s.is_user_process = 1
          AND r.session_id <> @@SPID
          AND (r.database_id = DB_ID(N'NRWTransportDW')
               OR CONVERT(nvarchar(max), st.text) LIKE N'%NRWTransportDW%'
               OR CONVERT(nvarchar(max), st.text) LIKE N'%NRWTransport%')
        ORDER BY r.session_id
        """,
    )
    evidence: list[dict[str, Any]] = []
    for row in rows:
        query_text = str(row.pop("query_text", "") or "")
        evidence.append(
            {
                **json_safe(row),
                "query_text_length": len(query_text),
                "query_text_sha256": hashlib.sha256(query_text.encode("utf-8", "replace")).hexdigest(),
            }
        )
    return evidence


def make_archive_and_dependency_gate(settings: MssqlConnectionSettings) -> tuple[dict[str, Any], Any, dict[str, Any]]:
    archive = archive_preflight()
    if not archive["archive_gate_pass"]:
        raise RuntimeError("archive preflight failed; refusing database removal")

    master = connect_database(settings, "master")
    try:
        initial_snapshot = database_snapshot(master)
        if not initial_snapshot["c_volume"]:
            raise RuntimeError("C: volume free-space evidence is unavailable; refusing database removal")
        linked = linked_servers(master)
        database_names = [
            row["name"]
            for row in initial_snapshot["states"]
            if row["name"] not in SYSTEM_DATABASES
            and row["name"] != LEGACY_DATABASE
            and row["state_desc"] == "ONLINE"
        ]
    finally:
        close(master)

    if ACTIVE_DATABASE not in database_names:
        raise RuntimeError("CologneTransitIntelligence is not an ONLINE user database; refusing database removal")

    database_dependency_audit: list[dict[str, Any]] = []
    for database in database_names:
        connection = connect_database(settings, database)
        try:
            database_dependency_audit.append(module_and_synonym_references(connection, database))
        finally:
            close(connection)

    msdb = connect_database(settings, "msdb")
    try:
        agent = sql_agent_references(msdb)
    finally:
        close(msdb)

    active = connect_database(settings, ACTIVE_DATABASE)
    try:
        active_schema = active_schema_snapshot(active)
    finally:
        close(active)

    # The final master connection is deliberately kept open so the last
    # read-only session check and the controlled ALTER/DROP sequence use one
    # connection with no intervening project operation.
    final_master = connect_database(settings, "master")
    final_snapshot = database_snapshot(final_master)
    final_requests = active_requests(final_master)
    sql_shared_file_exists = file_exists_from_xp(final_master, SQL_BACKUP_PATH)
    module_references = [
        ref
        for audit in database_dependency_audit
        for ref in audit["module_references"]
    ]
    synonym_references = [
        ref
        for audit in database_dependency_audit
        for ref in audit["synonym_references"]
    ]
    active_external_dependencies = [
        row
        for audit in database_dependency_audit
        if audit["database"] == ACTIVE_DATABASE
        for row in audit["cross_database_dependencies"]
        if row.get("referenced_database_name") == LEGACY_DATABASE
    ]
    legacy_state = final_snapshot["states_by_name"].get(LEGACY_DATABASE, {})
    active_state = final_snapshot["states_by_name"].get(ACTIVE_DATABASE, {})
    if legacy_state.get("state_desc") != "ONLINE":
        close(final_master)
        raise RuntimeError(f"NRWTransportDW is not ONLINE: {legacy_state.get('state_desc')!r}")
    if active_state.get("state_desc") != "ONLINE":
        close(final_master)
        raise RuntimeError(f"CologneTransitIntelligence is not ONLINE: {active_state.get('state_desc')!r}")
    if not sql_shared_file_exists:
        close(final_master)
        raise RuntimeError("SQL Server cannot see the verified archive .bak at the expected shared path")
    if module_references or agent["references"] or synonym_references or active_external_dependencies:
        close(final_master)
        raise RuntimeError("a live SQL module, SQL Agent, synonym, or cross-database dependency references NRWTransportDW")
    if final_requests:
        close(final_master)
        raise RuntimeError("an active SQL session is using NRWTransportDW or contains an NRWTransport reference")

    preflight = {
        "archive": archive,
        "dependency_classification": "LEGACY_SAFE_TO_ARCHIVE",
        "migration_status": "NO_NRWTRANSPORTDW_ASSET_REQUIRED_BY_ACTIVE_COLOGNE_PROJECT",
        "database_snapshot": final_snapshot,
        "active_schema_before": active_schema,
        "linked_servers": linked,
        "database_dependency_audit": database_dependency_audit,
        "sql_agent": agent,
        "active_requests": final_requests,
        "sql_server_shared_backup_file_exists": sql_shared_file_exists,
        "gates": {
            "legacy_exists_and_online": True,
            "active_exists_and_online": True,
            "no_active_sql_module_reference": not module_references,
            "no_sql_agent_reference": not agent["references"],
            "no_synonym_dependency_reference": not synonym_references,
            "no_active_cross_database_dependency": not active_external_dependencies,
            "no_currently_executing_nrw_session": not final_requests,
            "verified_bak_present_and_matching": archive["bak"]["exists"]
            and archive["bak"]["size_matches"]
            and archive["bak"]["sha256_matches"],
            "verified_zip_present_and_matching": archive["zip"]["exists"]
            and archive["zip"]["size_matches"]
            and archive["zip"]["sha256_matches"],
            "sql_server_can_see_shared_bak": sql_shared_file_exists,
            "c_free_space_captured": bool(final_snapshot["c_volume"].get("available_bytes") is not None),
        },
    }
    if not all(preflight["gates"].values()):
        close(final_master)
        raise RuntimeError("one or more final pre-drop safety gates failed")
    return preflight, final_master, active_schema


def execute_drop(master: Any) -> dict[str, Any]:
    master.autocommit(True)
    cursor = master.cursor()
    result = {
        "alter_database_single_user": "NOT_RUN",
        "drop_database": "NOT_RUN",
        "rollback_scope": "NRWTransportDW only",
    }
    try:
        cursor.execute("ALTER DATABASE [NRWTransportDW] SET SINGLE_USER WITH ROLLBACK IMMEDIATE;")
        result["alter_database_single_user"] = "PASS"
        cursor.execute("DROP DATABASE [NRWTransportDW];")
        result["drop_database"] = "PASS"
        return result
    except Exception as exc:
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)[:1000]
        # If ALTER succeeded but DROP failed, restore only the legacy database
        # to MULTI_USER so a partial failure does not leave it stranded.
        if result["alter_database_single_user"] == "PASS":
            try:
                cursor.execute("IF DB_ID(N'NRWTransportDW') IS NOT NULL ALTER DATABASE [NRWTransportDW] SET MULTI_USER WITH ROLLBACK IMMEDIATE;")
                result["partial_failure_recovery"] = "NRWTransportDW returned to MULTI_USER"
            except Exception as recovery_exc:
                result["partial_failure_recovery"] = f"FAILED: {type(recovery_exc).__name__}: {str(recovery_exc)[:500]}"
        raise RuntimeError(json.dumps(result, ensure_ascii=False)) from exc


def post_database_snapshot(master: Any) -> dict[str, Any]:
    snapshot = database_snapshot(master)
    snapshot["active_file_checks_after_drop"] = [
        {"path": row["physical_name"], "exists": file_exists_from_xp(master, row["physical_name"])}
        for row in snapshot["active_files"]
    ]
    return snapshot


def latest_active_evidence(settings: MssqlConnectionSettings) -> dict[str, Any]:
    connection = connect_database(settings, ACTIVE_DATABASE)
    try:
        latest = fetch_one(
            connection,
            """
            SELECT MAX(ObservedAtUtc) AS latest_realtime_observation_utc,
                   COUNT_BIG(*) AS realtime_observation_rows
            FROM stg.MddRealtimeStopObservation
            """,
        )
        latest_situation = fetch_one(
            connection,
            """
            SELECT MAX(ObservedAtUtc) AS latest_situation_observation_utc,
                   COUNT_BIG(*) AS situation_observation_rows
            FROM stg.MddRealtimeSituationObservation
            """,
        )
        targets = fetch(
            connection,
            """
            SELECT SamplingTargetId, StopPointRef, TargetName, IsEnabled
            FROM ctl.MddRealtimeSamplingTarget
            WHERE SamplingTargetId BETWEEN 1 AND 7
            ORDER BY SamplingTargetId
            """,
        )
        observation_columns = {
            row["name"]
            for row in fetch(
                connection,
                """
                SELECT c.name
                FROM sys.columns AS c
                WHERE c.object_id = OBJECT_ID(N'stg.MddRealtimeStopObservation')
                """,
            )
        }
        historical_target_observations: list[dict[str, Any]] = []
        if "SamplingTargetId" in observation_columns:
            historical_target_observations = fetch(
                connection,
                """
                SELECT SamplingTargetId, COUNT_BIG(*) AS observation_count,
                       MAX(ObservedAtUtc) AS latest_observation_utc
                FROM stg.MddRealtimeStopObservation
                WHERE SamplingTargetId BETWEEN 1 AND 7
                GROUP BY SamplingTargetId
                ORDER BY SamplingTargetId
                """,
            )
        return {
            "latest_realtime": json_safe(latest),
            "latest_situation": json_safe(latest_situation),
            "sampling_targets_1_to_7": json_safe(targets),
            "sampling_target_ids_present": sorted(int(row["SamplingTargetId"]) for row in targets),
            "sampling_target_ids_missing": [
                target_id for target_id in range(1, 8) if target_id not in {int(row["SamplingTargetId"]) for row in targets}
            ],
            "historical_target_observations": json_safe(historical_target_observations),
            "observation_has_sampling_target_id": "SamplingTargetId" in observation_columns,
        }
    finally:
        close(connection)


def run_dbcc(settings: MssqlConnectionSettings) -> dict[str, Any]:
    connection = connect_database(settings, ACTIVE_DATABASE, timeout=3600)
    try:
        cursor = connection.cursor()
        statement = "DBCC CHECKDB ('CologneTransitIntelligence') WITH PHYSICAL_ONLY, NO_INFOMSGS;"
        cursor.execute(statement)
        return {"status": "PASS", "statement": statement, "repair_option_used": False}
    except Exception as exc:
        return {
            "status": "FAIL",
            "statement": "DBCC CHECKDB ('CologneTransitIntelligence') WITH PHYSICAL_ONLY, NO_INFOMSGS;",
            "repair_option_used": False,
            "error_type": type(exc).__name__,
            "error": str(exc)[:1000],
        }
    finally:
        close(connection)


def state_changes(pre: Mapping[str, Any], post: Mapping[str, Any]) -> list[dict[str, Any]]:
    ignored = {LEGACY_DATABASE}
    pre_states = pre.get("states_by_name", {})
    post_states = post.get("states_by_name", {})
    changes: list[dict[str, Any]] = []
    for name in sorted(set(pre_states) | set(post_states)):
        if name in ignored:
            continue
        pre_row = pre_states.get(name)
        post_row = post_states.get(name)
        pre_values = {
            key: pre_row.get(key) if pre_row else None
            for key in ("state_desc", "user_access_desc", "recovery_model_desc", "is_read_only")
        }
        post_values = {
            key: post_row.get(key) if post_row else None
            for key in ("state_desc", "user_access_desc", "recovery_model_desc", "is_read_only")
        }
        if pre_values != post_values:
            changes.append({"database": name, "before": pre_values, "after": post_values})
    return changes


def write_report(path: Path, evidence: Mapping[str, Any]) -> None:
    pre = evidence["pre_drop"]["database_snapshot"]
    post = evidence.get("post_drop", {})
    archive = evidence["archive"]
    drop = evidence.get("drop_result", {})
    active = post.get("active_state", {})
    dbcc = evidence.get("dbcc", {})
    active_file_summary = ", ".join(
        f"{row.get('logical_name')}: {row.get('allocated_bytes')} bytes ({row.get('allocated_mb')} MB)"
        for row in post.get("active_files", [])
    )
    lines = [
        "# NRWTransportDW removal evidence",
        "",
        f"- Operation time (UTC): `{evidence.get('operation_completed_utc')}`",
        f"- Dependency classification: `{evidence.get('dependency_classification')}`",
        f"- Cologne asset migration: `{evidence.get('migration_status')}`",
        f"- Verified archive .bak preserved: `{archive.get('bak', {}).get('exists')}`; SHA-256 match: `{archive.get('bak', {}).get('sha256_matches')}`",
        f"- Verified archive ZIP preserved: `{archive.get('zip', {}).get('exists')}`; SHA-256 match: `{archive.get('zip', {}).get('sha256_matches')}`",
        "",
        "## Pre-drop",
        "",
        f"- NRWTransportDW allocated bytes: `{pre.get('legacy_allocated_bytes')}`",
        f"- C: free bytes before: `{pre.get('c_volume', {}).get('available_bytes')}`",
        f"- Recorded files: `{', '.join(row.get('physical_name', '') for row in pre.get('legacy_files', []))}`",
        "",
        "## Drop",
        "",
        f"- ALTER DATABASE SINGLE_USER: `{drop.get('alter_database_single_user')}`",
        f"- DROP DATABASE: `{drop.get('drop_database')}`",
        "",
        "## Post-drop",
        "",
        f"- NRWTransportDW absent from sys.databases: `{post.get('legacy_absent')}`",
        f"- Previous files absent at recorded paths: `{post.get('previous_files_absent')}`",
        f"- C: free bytes after: `{post.get('c_volume', {}).get('available_bytes')}`",
        f"- Actual space recovered: `{evidence.get('space_recovered_bytes')}` bytes (`{evidence.get('space_recovered_gib')}` GiB)",
        f"- CologneTransitIntelligence: `{active.get('state_desc')}` / `{active.get('user_access_desc')}`",
        f"- Current active MDF/LDF allocation: `{active_file_summary}`",
        f"- DBCC CHECKDB PHYSICAL_ONLY: `{dbcc.get('status')}`",
        f"- SamplingTargetIds 1-7 present: `{evidence.get('active_validation', {}).get('sampling_target_ids_missing') == []}`",
        f"- Latest realtime observation: `{evidence.get('active_validation', {}).get('latest_realtime', {}).get('latest_realtime_observation_utc')}`",
        "",
        "Unexpected issues: " + ("none recorded" if not evidence.get("unexpected_issues") else json.dumps(evidence["unexpected_issues"], ensure_ascii=False)),
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    settings = MssqlConnectionSettings.from_environment()
    artifact_dir = Path(args.artifact_dir).expanduser().resolve()
    stamp = args.stamp or utc_now().strftime("%Y%m%dT%H%M%SZ")
    if not re.fullmatch(r"\d{8}T\d{6}Z", stamp):
        raise ValueError("--stamp must use UTC format YYYYMMDDTHHMMSSZ")
    preflight, master, active_schema_before = make_archive_and_dependency_gate(settings)
    preflight_path = artifact_dir / f"NRWTransportDW-removal-preflight-{stamp}.json"
    write_json(preflight_path, preflight)
    result: dict[str, Any] = {
        "status": "PREFLIGHT_PASS",
        "preflight_evidence": str(preflight_path),
        "dependency_classification": preflight["dependency_classification"],
        "migration_status": preflight["migration_status"],
        "pre_drop": preflight,
    }
    if not args.execute_drop:
        close(master)
        return result

    drop_result: dict[str, Any] = {}
    try:
        drop_result = execute_drop(master)
    except Exception as exc:
        close(master)
        try:
            drop_result = json.loads(str(exc))
        except json.JSONDecodeError:
            drop_result = {"error": str(exc)[:2000]}
        result.update({"status": "DROP_FAILED", "drop_result": drop_result, "error": str(exc)[:2000]})
        evidence_path = artifact_dir / f"NRWTransportDW-removal-evidence-{stamp}.json"
        write_json(evidence_path, result)
        result["evidence"] = str(evidence_path)
        return result

    time.sleep(2)
    post_snapshot = post_database_snapshot(master)
    close(master)
    active = connect_database(settings, ACTIVE_DATABASE)
    try:
        active_schema_after = active_schema_snapshot(active)
        active_state = fetch_one(
            active,
            "SELECT DB_NAME() AS database_name, state_desc, user_access_desc FROM sys.databases WHERE name = DB_NAME()",
        )
    finally:
        close(active)

    active_validation = latest_active_evidence(settings)
    dbcc = run_dbcc(settings)
    pre_snapshot = preflight["database_snapshot"]
    pre_c_free = int(pre_snapshot["c_volume"].get("available_bytes") or 0)
    post_c_free = int(post_snapshot["c_volume"].get("available_bytes") or 0)
    recovered = post_c_free - pre_c_free
    # Re-check file existence with one master connection for deterministic
    # evidence; this is read-only and does not delete any leftover file.
    checker = connect_database(settings, "master")
    try:
        previous_file_checks = [
            {
                "logical_name": row.get("logical_name"),
                "type_desc": row.get("type_desc"),
                "physical_name": row.get("physical_name"),
                "exists_after_drop": file_exists_from_xp(checker, row["physical_name"]),
            }
            for row in pre_snapshot["legacy_files"]
        ]
    finally:
        close(checker)
    removed_tables = sorted(set(active_schema_before["table_keys"]) - set(active_schema_after["table_keys"]))
    removed_objects = sorted(set(active_schema_before["object_keys"]) - set(active_schema_after["object_keys"]))
    issues: list[str] = []
    if not post_snapshot["states_by_name"].get(LEGACY_DATABASE) is None:
        issues.append("NRWTransportDW remains in sys.databases")
    if post_snapshot["legacy_files"]:
        issues.append("NRWTransportDW still has rows in sys.master_files")
    if any(row["exists_after_drop"] for row in previous_file_checks):
        issues.append("a previous NRWTransportDW file remains at its recorded path")
    if removed_tables or removed_objects:
        issues.append("active project schema objects were removed or changed")
    changes = state_changes(pre_snapshot, post_snapshot)
    if changes:
        issues.append("an unrelated database state changed")
    if pre_snapshot["server"].get("sqlserver_start_time") != post_snapshot["server"].get("sqlserver_start_time"):
        issues.append("SQL Server start time changed during the operation")
    if dbcc.get("status") != "PASS":
        issues.append("DBCC CHECKDB PHYSICAL_ONLY did not pass")
    if active_state.get("state_desc") != "ONLINE" or active_state.get("user_access_desc") != "MULTI_USER":
        issues.append("CologneTransitIntelligence is not ONLINE/MULTI_USER after removal")
    if active_validation["sampling_target_ids_missing"]:
        issues.append("one or more SamplingTargetIds 1-7 are missing after removal")

    result = {
        "status": "PASS" if not issues and drop_result.get("drop_database") == "PASS" else "PASS_WITH_ISSUES",
        "operation_completed_utc": iso(utc_now()),
        "dependency_classification": preflight["dependency_classification"],
        "migration_status": preflight["migration_status"],
        "archive": preflight["archive"],
        "pre_drop": preflight,
        "drop_result": drop_result,
        "post_drop": {
            **post_snapshot,
            "legacy_absent": LEGACY_DATABASE not in post_snapshot["states_by_name"],
            "previous_file_checks": previous_file_checks,
            "previous_files_absent": not any(row["exists_after_drop"] for row in previous_file_checks),
            "active_state": json_safe(active_state),
            "active_schema_after": active_schema_after,
            "active_schema_intact": not removed_tables and not removed_objects,
            "removed_active_tables": removed_tables,
            "removed_active_objects": removed_objects,
            "unrelated_database_state_changes": changes,
            "sql_server_service_health": {
                "connection_after_drop": True,
                "sqlserver_start_time_unchanged": pre_snapshot["server"].get("sqlserver_start_time")
                == post_snapshot["server"].get("sqlserver_start_time"),
            },
        },
        "space_free_bytes_before": pre_c_free,
        "space_free_bytes_after": post_c_free,
        "space_recovered_bytes": recovered,
        "space_recovered_gib": round(recovered / 1024**3, 3),
        "active_validation": active_validation,
        "dbcc": dbcc,
        "unexpected_issues": issues,
        "archive_material_deleted": False,
        "next_operation_started": False,
    }
    evidence_path = artifact_dir / f"NRWTransportDW-removal-evidence-{stamp}.json"
    report_path = artifact_dir / f"NRWTransportDW-removal-report-{stamp}.md"
    write_json(evidence_path, result)
    write_report(report_path, result)
    result["evidence"] = str(evidence_path)
    result["report"] = str(report_path)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-drop", action="store_true", help="perform the guarded ALTER/DROP sequence")
    parser.add_argument("--artifact-dir", default=str(ARTIFACT_DIR), help="directory for sanitized evidence")
    parser.add_argument("--stamp", help="UTC stamp YYYYMMDDTHHMMSSZ; defaults to current UTC")
    args = parser.parse_args(argv)
    try:
        result = run(args)
    except Exception as exc:
        result = {"status": "FAIL", "error_type": type(exc).__name__, "error": str(exc)[:2000]}
    print(json.dumps(json_safe(result), ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") in {"PREFLIGHT_PASS", "PASS"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
