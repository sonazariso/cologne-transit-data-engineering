#!/usr/bin/env python3
"""Inventory and, only with explicit authorization, repair one schema drift.

The repair is intentionally narrower than the general additive migration.  It
extracts the already-authoritative ``MddMacProductionResumeEvent`` CREATE
batch from ``create-source-faithful-persistence.sql``; it does not duplicate
or reinterpret that table definition.  The default command is read-only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from tiered50_production_evidence import (  # noqa: E402
    _safe_backup_verify,
    _sanitize_sql_reason,
    _sql_error_code,
)
from validate_mssql_migration import split_batches  # noqa: E402
from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    FULL_SCHEMA_ROUTINES,
    FULL_SCHEMA_TABLES,
    MssqlConnectionSettings,
    connect_mssql,
)
from cologne_transit_collector.runtime_configuration import (  # noqa: E402
    EXPECTED_SQL_DATABASE,
    MacOSKeychainSecretProvider,
    resolve_administrator_environment,
)


MIGRATION = ROOT / "sql" / "mdd-mac-collector" / "create-source-faithful-persistence.sql"
RESUME_EVENT_OBJECT = "ctl.MddMacProductionResumeEvent"
EXPECTED_SQL_HOST = "172.16.192.128"
EXPECTED_SQL_SERVER = "DataAnalyst-VM"
RELATED_OBJECTS = (
    RESUME_EVENT_OBJECT,
    "ctl.MddMacSamplingEra",
    "ctl.MddMacProductionCutover",
    "ctl.MddMacQuotaResetContract",
    "ctl.uspEnsureMddMacQuotaPeriod",
)


def _row_value(row: Any, name: str, index: int = 0) -> Any:
    if isinstance(row, Mapping):
        if name in row:
            return row[name]
        folded = name.casefold()
        for key, value in row.items():
            if str(key).casefold() == folded:
                return value
        return None
    if row is None:
        return None
    try:
        return row[index]
    except (IndexError, KeyError, TypeError):
        return None


def _rows(cursor: Any) -> list[Any]:
    return list(cursor.fetchall())


def _sql_names(names: Iterable[str]) -> str:
    return ", ".join("N'" + name.replace("'", "''") + "'" for name in names)


def resume_event_ddl_batch(path: Path = MIGRATION) -> str:
    """Return the exact idempotent ResumeEvent batch from the source DDL."""

    batches = split_batches(path.read_text(encoding="utf-8"))
    candidates = [
        batch
        for batch in batches
        if re.search(
            r"\bCREATE\s+TABLE\s+ctl\.MddMacProductionResumeEvent\b",
            batch,
            flags=re.IGNORECASE,
        )
    ]
    if len(candidates) != 1:
        raise ValueError(
            "authoritative migration must contain exactly one ResumeEvent CREATE batch"
        )
    return candidates[0]


def resume_event_contract(ddl_batch: str | None = None) -> dict[str, list[str]]:
    """Derive the table's identifier contract from the authoritative batch."""

    batch = ddl_batch if ddl_batch is not None else resume_event_ddl_batch()
    match = re.search(
        r"CREATE\s+TABLE\s+ctl\.MddMacProductionResumeEvent\s*\((?P<body>.*?)\n\s*\);",
        batch,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        raise ValueError("ResumeEvent CREATE batch has no parseable table body")
    body = match.group("body")
    columns: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        column = re.match(
            r"([A-Za-z][A-Za-z0-9_]*)\s+"
            r"(?:BIGINT|INT|TINYINT|BIT|NVARCHAR|VARCHAR|DATETIME2|DATETIME|DATE|UNIQUEIDENTIFIER)\b",
            stripped,
            flags=re.IGNORECASE,
        )
        if column is not None:
            columns.append(column.group(1))
    constraints = re.findall(
        r"\bCONSTRAINT\s+([A-Za-z][A-Za-z0-9_]*)\b", body, flags=re.IGNORECASE
    )
    if not columns or not constraints:
        raise ValueError("ResumeEvent CREATE batch has no complete identifier contract")
    return {"columns": columns, "constraints": constraints}


def _inventory_query(cursor: Any, names: Sequence[str]) -> tuple[list[Any], list[Any], list[Any], list[Any], list[Any]]:
    """Collect object, column, index, constraint, and dependency metadata."""

    name_list = _sql_names(names)
    cursor.execute(
        """
        SELECT s.name + N'.' + o.name AS object_name,
               o.type AS object_type,
               o.type_desc AS object_type_desc,
               o.object_id
        FROM sys.objects AS o
        INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        WHERE s.name + N'.' + o.name IN ("""
        + name_list
        + ")"
    )
    object_rows = _rows(cursor)

    cursor.execute(
        """
        SELECT s.name + N'.' + o.name AS object_name,
               c.column_id,
               c.name AS column_name,
               TYPE_NAME(c.user_type_id) AS data_type,
               c.max_length,
               c.precision,
               c.scale,
               c.is_nullable,
               c.is_identity,
               dc.name AS default_constraint_name,
               dc.definition AS default_definition
        FROM sys.columns AS c
        INNER JOIN sys.objects AS o ON o.object_id = c.object_id
        INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        LEFT JOIN sys.default_constraints AS dc ON dc.object_id = c.default_object_id
        WHERE s.name + N'.' + o.name IN ("""
        + name_list
        + ") ORDER BY object_name, c.column_id"
    )
    column_rows = _rows(cursor)

    cursor.execute(
        """
        SELECT s.name + N'.' + o.name AS object_name,
               i.name AS index_name,
               i.type_desc AS index_type,
               i.is_unique,
               i.is_primary_key,
               i.is_unique_constraint,
               ic.key_ordinal,
               c.name AS column_name,
               ic.is_descending_key,
               i.has_filter,
               i.filter_definition
        FROM sys.indexes AS i
        INNER JOIN sys.objects AS o ON o.object_id = i.object_id
        INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        LEFT JOIN sys.index_columns AS ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
        LEFT JOIN sys.columns AS c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
        WHERE s.name + N'.' + o.name IN ("""
        + name_list
        + ") ORDER BY object_name, index_name, ic.key_ordinal"
    )
    index_rows = _rows(cursor)

    constraint_sql = f"""
        SELECT s.name + N'.' + o.name AS object_name,
               kc.name AS constraint_name,
               N'KEY' AS constraint_type,
               NULL AS referenced_object_name,
               NULL AS definition
        FROM sys.key_constraints AS kc
        INNER JOIN sys.objects AS o ON o.object_id = kc.parent_object_id
        INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        WHERE s.name + N'.' + o.name IN ({name_list})
        UNION ALL
        SELECT s.name + N'.' + o.name, cc.name, N'CHECK', NULL, cc.definition
        FROM sys.check_constraints AS cc
        INNER JOIN sys.objects AS o ON o.object_id = cc.parent_object_id
        INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        WHERE s.name + N'.' + o.name IN ({name_list})
        UNION ALL
        SELECT s.name + N'.' + o.name, fk.name, N'FOREIGN_KEY',
               rs.name + N'.' + ro.name, NULL
        FROM sys.foreign_keys AS fk
        INNER JOIN sys.objects AS o ON o.object_id = fk.parent_object_id
        INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        INNER JOIN sys.objects AS ro ON ro.object_id = fk.referenced_object_id
        INNER JOIN sys.schemas AS rs ON rs.schema_id = ro.schema_id
        WHERE s.name + N'.' + o.name IN ({name_list})
        UNION ALL
        SELECT s.name + N'.' + o.name, dc.name, N'DEFAULT', NULL, dc.definition
        FROM sys.default_constraints AS dc
        INNER JOIN sys.objects AS o ON o.object_id = dc.parent_object_id
        INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        WHERE s.name + N'.' + o.name IN ({name_list})
    """
    cursor.execute(constraint_sql)
    constraint_rows = _rows(cursor)

    cursor.execute(
        """
        SELECT rs.name + N'.' + ro.name AS referencing_object_name,
               dep.referenced_schema_name + N'.' + dep.referenced_entity_name AS referenced_object_name,
               dep.referenced_entity_name,
               dep.referencing_id,
               dep.is_schema_bound_reference
        FROM sys.sql_expression_dependencies AS dep
        INNER JOIN sys.objects AS ro ON ro.object_id = dep.referencing_id
        INNER JOIN sys.schemas AS rs ON rs.schema_id = ro.schema_id
        WHERE rs.name + N'.' + ro.name IN ("""
        + name_list
        + ") OR dep.referenced_schema_name + N'.' + dep.referenced_entity_name IN ("
        + name_list
        + ")"
    )
    dependency_rows = _rows(cursor)
    return object_rows, column_rows, index_rows, constraint_rows, dependency_rows


def inventory_schema(connection: Any, *, migration_path: Path = MIGRATION) -> dict[str, Any]:
    """Read-only inventory of source-defined and related deployed objects."""

    source_objects = tuple(dict.fromkeys(FULL_SCHEMA_TABLES + FULL_SCHEMA_ROUTINES))
    contract = resume_event_contract(resume_event_ddl_batch(migration_path))
    cursor = connection.cursor()
    object_rows, column_rows, index_rows, constraint_rows, dependency_rows = _inventory_query(
        cursor, tuple(dict.fromkeys(source_objects + RELATED_OBJECTS))
    )
    observed_objects = {
        str(_row_value(row, "object_name", 0)): {
            "object_type": str(_row_value(row, "object_type", 1) or ""),
            "object_type_desc": str(_row_value(row, "object_type_desc", 2) or ""),
            "object_id": _row_value(row, "object_id", 3),
        }
        for row in object_rows
    }
    missing = sorted(name for name in source_objects if name not in observed_objects)
    related_present = RESUME_EVENT_OBJECT in observed_objects
    return {
        "status": "PASS" if missing == [RESUME_EVENT_OBJECT] else "NOT_PROVEN" if missing else "PASS",
        "source_defined_objects": {
            "expected": list(source_objects),
            "expected_count": len(source_objects),
            "observed_count": sum(name in observed_objects for name in source_objects),
            "missing": missing,
            "resume_event_is_only_missing": missing == [RESUME_EVENT_OBJECT],
        },
        "observed_objects": observed_objects,
        "related_objects": list(RELATED_OBJECTS),
        "related_object_details": {
            "columns": [dict(row) if isinstance(row, Mapping) else row for row in column_rows],
            "indexes": [dict(row) if isinstance(row, Mapping) else row for row in index_rows],
            "constraints": [dict(row) if isinstance(row, Mapping) else row for row in constraint_rows],
            "dependencies": [dict(row) if isinstance(row, Mapping) else row for row in dependency_rows],
        },
        "resume_event": {
            "present": related_present,
            "expected_contract": contract,
            "observed_column_count": sum(
                str(_row_value(row, "object_name", 0)) == RESUME_EVENT_OBJECT for row in column_rows
            ),
            "observed_constraint_count": sum(
                str(_row_value(row, "object_name", 0)) == RESUME_EVENT_OBJECT
                for row in constraint_rows
            ),
        },
    }


def _identity(connection: Any) -> dict[str, Any]:
    cursor = connection.cursor()
    cursor.execute(
        """
        SELECT DB_NAME() AS database_name,
               CAST(SERVERPROPERTY('ServerName') AS NVARCHAR(128)) AS server_name,
               CAST(SERVERPROPERTY('MachineName') AS NVARCHAR(128)) AS machine_name,
               CAST(@@SERVERNAME AS NVARCHAR(128)) AS configured_server_name,
               SUSER_SNAME() AS login_name,
               IS_SRVROLEMEMBER(N'sysadmin') AS is_sysadmin
        """
    )
    row = cursor.fetchone()
    return {
        "database_name": _row_value(row, "database_name", 0),
        "server_name": _row_value(row, "server_name", 1),
        "machine_name": _row_value(row, "machine_name", 2),
        "configured_server_name": _row_value(row, "configured_server_name", 3),
        "login_name": _row_value(row, "login_name", 4),
        "is_sysadmin": _row_value(row, "is_sysadmin", 5),
    }


def validate_expected_target(settings: MssqlConnectionSettings, identity: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed if the repair is pointed at another database or instance."""

    observed_names = {
        str(identity.get(name) or "").strip().casefold()
        for name in ("server_name", "machine_name", "configured_server_name")
    }
    expected_server = EXPECTED_SQL_SERVER.casefold()
    host_ok = str(settings.host).strip().casefold() in {
        EXPECTED_SQL_HOST.casefold(),
        EXPECTED_SQL_SERVER.casefold(),
    }
    database_ok = settings.database == EXPECTED_SQL_DATABASE and identity.get("database_name") == EXPECTED_SQL_DATABASE
    server_ok = expected_server in observed_names
    return {
        "status": "PASS" if host_ok and database_ok and server_ok else "NOT_PROVEN",
        "configured_host": settings.host,
        "expected_host": EXPECTED_SQL_HOST,
        "database_name": identity.get("database_name"),
        "expected_database": EXPECTED_SQL_DATABASE,
        "server_names": sorted(name for name in observed_names if name),
        "expected_server": EXPECTED_SQL_SERVER,
        "login_name": identity.get("login_name"),
        "is_sysadmin": identity.get("is_sysadmin"),
        "reason": None
        if host_ok and database_ok and server_ok
        else "configured database or SQL instance does not match the confirmed production target",
    }


def competing_collectors(connection: Any) -> dict[str, Any]:
    cursor = connection.cursor()
    cursor.execute(
        """
        SELECT CollectionContext, COUNT_BIG(*) AS running_count
        FROM ctl.MddMacCollectorRun
        WHERE Status IN ('RUNNING', 'CLAIMED')
          AND CollectionContext IN ('MAC_LEGACY7_PRODUCTION', 'MAC_TIERED50_PRODUCTION')
        GROUP BY CollectionContext
        """
    )
    run_rows = _rows(cursor)
    cursor.execute(
        """
        SELECT LeaseName, CollectionContext, RunId, Status, ExpiresAtUtc
        FROM ctl.MddMacProductionLease
        WHERE Status = 'ACTIVE' AND ExpiresAtUtc > SYSUTCDATETIME()
        """
    )
    lease_rows = _rows(cursor)
    return {
        "status": "PASS" if not run_rows and not lease_rows else "NOT_PROVEN",
        "running_collectors": [dict(row) if isinstance(row, Mapping) else row for row in run_rows],
        "active_leases": [dict(row) if isinstance(row, Mapping) else row for row in lease_rows],
        "reason": None if not run_rows and not lease_rows else "an active production writer or lease is present",
    }


def backup_readiness(settings: MssqlConnectionSettings) -> dict[str, Any]:
    """Recheck backup VERIFYONLY on a separate read-only connection."""

    connection = connect_mssql(settings)
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT TOP (1) bs.media_set_id, bs.backup_finish_date,
                           bs.database_name, bs.has_backup_checksums
            FROM msdb.dbo.backupset AS bs
            WHERE bs.database_name = DB_NAME() AND bs.type = 'D'
            ORDER BY bs.backup_finish_date DESC
            """
        )
        backup = cursor.fetchone()
        if backup is None:
            return {"status": "NOT_PROVEN", "reason": "no latest full backup was returned"}
        media_set_id = _row_value(backup, "media_set_id", 0)
        cursor.execute(
            """
            SELECT family_sequence_number, physical_device_name
            FROM msdb.dbo.backupmediafamily
            WHERE media_set_id = %s
            ORDER BY family_sequence_number
            """,
            (media_set_id,),
        )
        paths = [
            str(_row_value(row, "physical_device_name", 1) or "")
            for row in _rows(cursor)
            if str(_row_value(row, "physical_device_name", 1) or "").strip()
        ]
        verification = _safe_backup_verify(connection, paths)
        return {
            "status": "PASS" if verification.get("status") == "PASS" else "NOT_PROVEN",
            "backup_finish_date": _row_value(backup, "backup_finish_date", 1),
            "database_name": _row_value(backup, "database_name", 2),
            "has_backup_checksums": _row_value(backup, "has_backup_checksums", 3),
            "media_count": len(paths),
            "verifyonly": verification,
        }
    finally:
        connection.close()


def _schema_verification(cursor: Any, contract: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    """Verify the exact ResumeEvent identifiers after a transactional DDL."""

    cursor.execute(
        """
        SELECT s.name + N'.' + o.name AS object_name, o.type AS object_type
        FROM sys.objects AS o
        INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        WHERE s.name = N'ctl' AND o.name = N'MddMacProductionResumeEvent'
        """
    )
    object_row = cursor.fetchone()
    cursor.execute(
        """
        SELECT c.name AS column_name, TYPE_NAME(c.user_type_id) AS data_type,
               c.max_length, c.precision, c.scale, c.is_nullable, c.is_identity
        FROM sys.columns AS c
        INNER JOIN sys.objects AS o ON o.object_id = c.object_id
        INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        WHERE s.name = N'ctl' AND o.name = N'MddMacProductionResumeEvent'
        ORDER BY c.column_id
        """
    )
    column_rows = _rows(cursor)
    cursor.execute(
        """
        SELECT kc.name AS constraint_name, N'KEY' AS constraint_type
        FROM sys.key_constraints AS kc
        INNER JOIN sys.objects AS o ON o.object_id = kc.parent_object_id
        INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        WHERE s.name = N'ctl' AND o.name = N'MddMacProductionResumeEvent'
        UNION ALL
        SELECT cc.name, N'CHECK'
        FROM sys.check_constraints AS cc
        INNER JOIN sys.objects AS o ON o.object_id = cc.parent_object_id
        INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        WHERE s.name = N'ctl' AND o.name = N'MddMacProductionResumeEvent'
        UNION ALL
        SELECT fk.name, N'FOREIGN_KEY'
        FROM sys.foreign_keys AS fk
        INNER JOIN sys.objects AS o ON o.object_id = fk.parent_object_id
        INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        WHERE s.name = N'ctl' AND o.name = N'MddMacProductionResumeEvent'
        UNION ALL
        SELECT dc.name, N'DEFAULT'
        FROM sys.default_constraints AS dc
        INNER JOIN sys.objects AS o ON o.object_id = dc.parent_object_id
        INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        WHERE s.name = N'ctl' AND o.name = N'MddMacProductionResumeEvent'
        """
    )
    constraint_rows = _rows(cursor)
    cursor.execute(
        """
        SELECT i.name AS index_name, i.is_unique, i.is_primary_key,
               i.is_unique_constraint
        FROM sys.indexes AS i
        INNER JOIN sys.objects AS o ON o.object_id = i.object_id
        INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        WHERE s.name = N'ctl' AND o.name = N'MddMacProductionResumeEvent'
          AND i.name IS NOT NULL
        """
    )
    index_rows = _rows(cursor)
    cursor.execute(
        """
        SELECT HAS_PERMS_BY_NAME(N'ctl.MddMacProductionResumeEvent', N'OBJECT', N'SELECT') AS can_select,
               HAS_PERMS_BY_NAME(N'ctl.MddMacProductionResumeEvent', N'OBJECT', N'INSERT') AS can_insert,
               HAS_PERMS_BY_NAME(N'ctl.MddMacProductionResumeEvent', N'OBJECT', N'VIEW DEFINITION') AS can_view_definition,
               IS_SRVROLEMEMBER(N'sysadmin') AS is_sysadmin
        """
    )
    permission_row = cursor.fetchone()

    observed_columns = [str(_row_value(row, "column_name", 0)) for row in column_rows]
    observed_constraints = [str(_row_value(row, "constraint_name", 0)) for row in constraint_rows]
    expected_columns = list(contract.get("columns", ()))
    expected_constraints = list(contract.get("constraints", ()))
    permission_values = {
        key: _row_value(permission_row, key, index)
        for index, key in enumerate(("can_select", "can_insert", "can_view_definition", "is_sysadmin"))
    }
    sysadmin = permission_values["is_sysadmin"] in (1, True, "1", "TRUE", "true")
    permission_status = all(
        value in (1, True, "1", "TRUE", "true") or (sysadmin and value is None)
        for value in (
            permission_values["can_select"],
            permission_values["can_insert"],
            permission_values["can_view_definition"],
        )
    )
    checks = {
        "object_present": object_row is not None,
        "object_is_user_table": _row_value(object_row, "object_type", 1) in ("U", "U "),
        "columns_match_authoritative_definition": observed_columns == expected_columns,
        "constraints_match_authoritative_definition": set(observed_constraints) == set(expected_constraints),
        "permissions_proven": permission_status,
    }
    return {
        "status": "PASS" if all(checks.values()) else "NOT_PROVEN",
        "checks": checks,
        "observed_columns": observed_columns,
        "expected_columns": expected_columns,
        "observed_constraints": observed_constraints,
        "expected_constraints": expected_constraints,
        "indexes": [dict(row) if isinstance(row, Mapping) else row for row in index_rows],
        "permissions": permission_values,
    }


def apply_resume_event_repair(
    settings: MssqlConnectionSettings,
    *,
    inventory: Mapping[str, Any],
    backup: Mapping[str, Any],
    competing: Mapping[str, Any],
    ddl_batch: str | None = None,
) -> dict[str, Any]:
    """Create only the missing ResumeEvent table in one rollbackable unit."""

    missing = list(
        ((inventory.get("source_defined_objects") or {}).get("missing") or [])
    )
    if missing != [RESUME_EVENT_OBJECT]:
        if not missing:
            connection = connect_mssql(settings)
            try:
                verification = _schema_verification(
                    connection.cursor(),
                    resume_event_contract(ddl_batch if ddl_batch is not None else resume_event_ddl_batch()),
                )
            finally:
                connection.close()
            return {
                "status": verification.get("status", "NOT_PROVEN"),
                "transaction_committed": False,
                "created_objects": [],
                "reason": "approved ResumeEvent object already exists; no DDL executed",
                "schema_verification": verification,
            }
        return {
            "status": "PASS" if not missing else "NOT_PROVEN",
            "transaction_committed": False,
            "created_objects": [],
            "reason": "no approved single-object repair is required" if not missing else "other required source-defined objects are missing",
            "missing_objects": missing,
        }
    if backup.get("status") != "PASS":
        return {
            "status": "NOT_PROVEN",
            "transaction_committed": False,
            "created_objects": [],
            "reason": "backup VERIFYONLY readiness is not proven",
        }
    if competing.get("status") != "PASS":
        return {
            "status": "NOT_PROVEN",
            "transaction_committed": False,
            "created_objects": [],
            "reason": "an active competing collector or lease is present",
        }

    batch = ddl_batch if ddl_batch is not None else resume_event_ddl_batch()
    contract = resume_event_contract(batch)
    connection = connect_mssql(settings)
    committed = False
    try:
        cursor = connection.cursor()
        cursor.execute("SET XACT_ABORT ON;")
        cursor.execute(batch)
        before_commit = _schema_verification(cursor, contract)
        if before_commit.get("status") != "PASS":
            raise RuntimeError("authoritative ResumeEvent schema verification failed before commit")
        connection.commit()
        committed = True
        after_commit = _schema_verification(cursor, contract)
        return {
            "status": "PASS" if after_commit.get("status") == "PASS" else "NOT_PROVEN",
            "transaction_committed": True,
            "created_objects": [RESUME_EVENT_OBJECT],
            "schema_verification_before_commit": before_commit,
            "schema_verification_after_commit": after_commit,
            "ddl_source": str(MIGRATION),
        }
    except BaseException as exc:
        if not committed:
            try:
                connection.rollback()
            except BaseException:
                pass
        return {
            "status": "NOT_PROVEN",
            "transaction_committed": committed,
            "created_objects": [],
            "transaction_rolled_back": not committed,
            "error_code": _sql_error_code(exc),
            "error_type": type(exc).__name__,
            "reason": _sanitize_sql_reason(exc),
        }
    finally:
        connection.close()


def _resolved_admin_settings() -> tuple[MssqlConnectionSettings, str]:
    values, source = resolve_administrator_environment(
        secret_provider=MacOSKeychainSecretProvider()
    )
    missing = values.get("__ADMIN_MISSING_FIELDS__")
    if missing:
        raise RuntimeError(
            "MISSING_ADMIN_CREDENTIALS_OR_PERMISSIONS: explicitly configure " + str(missing)
        )
    settings = MssqlConnectionSettings.from_environment(
        {key: value for key, value in values.items() if not key.startswith("__")}
    )
    return settings, source


def read_only_inventory() -> dict[str, Any]:
    settings, source = _resolved_admin_settings()
    connection = connect_mssql(settings)
    try:
        identity = _identity(connection)
        target = validate_expected_target(settings, identity)
        inventory = inventory_schema(connection)
        competing = competing_collectors(connection)
        return {
            "status": "PASS" if target["status"] == "PASS" and inventory["status"] == "PASS" and competing["status"] == "PASS" else "NOT_PROVEN",
            "production_ddl_applied": False,
            "admin_resolution_source": source,
            "identity": identity,
            "target": target,
            "inventory": inventory,
            "competing_collectors": competing,
            "mdd_requests_issued": 0,
        }
    finally:
        connection.close()


def authorized_repair() -> dict[str, Any]:
    settings, source = _resolved_admin_settings()
    connection = connect_mssql(settings)
    try:
        identity = _identity(connection)
        target = validate_expected_target(settings, identity)
        inventory = inventory_schema(connection)
        competing = competing_collectors(connection)
    finally:
        connection.close()
    if target["status"] != "PASS":
        return {"status": "NOT_PROVEN", "production_ddl_applied": False, "target": target}
    backup = backup_readiness(settings)
    result = apply_resume_event_repair(
        settings,
        inventory=inventory,
        backup=backup,
        competing=competing,
    )
    return {
        **result,
        "production_ddl_applied": bool(result.get("transaction_committed")),
        "admin_resolution_source": source,
        "identity": identity,
        "target": target,
        "inventory": inventory,
        "backup": backup,
        "competing_collectors": competing,
        "mdd_requests_issued": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="prepare and execute the one-object repair after all safety checks",
    )
    parser.add_argument(
        "--authorize-production-ddl",
        action="store_true",
        help="explicit operator authorization required together with --apply",
    )
    args = parser.parse_args(argv)
    if args.apply and not args.authorize_production_ddl:
        result = {
            "status": "NOT_PROVEN",
            "production_ddl_applied": False,
            "created_objects": [],
            "reason": "explicit --authorize-production-ddl is required before production DDL",
            "mdd_requests_issued": 0,
        }
    else:
        try:
            result = authorized_repair() if args.apply else read_only_inventory()
        except Exception as exc:
            result = {
                "status": "NOT_PROVEN",
                "production_ddl_applied": False,
                "error_code": _sql_error_code(exc),
                "error_type": type(exc).__name__,
                "reason": _sanitize_sql_reason(exc),
                "mdd_requests_issued": 0,
            }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
