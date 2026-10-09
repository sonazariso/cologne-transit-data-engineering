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
from collections.abc import Callable, Iterable, Mapping, Sequence
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
SCHEMA_REPAIR_APP_LOCK_RESOURCE = (
    "CologneTransitIntelligence:SCHEMA:ctl.MddMacProductionResumeEvent"
)

CREATION_STATUSES = frozenset(
    {
        "CREATED_BY_THIS_OPERATION",
        "ALREADY_EXISTS_AND_VALID",
        "CREATED_OR_CHANGED_BY_ANOTHER_OPERATION",
        "CREATION_FAILED",
        "COMMIT_OUTCOME_UNKNOWN",
    }
)


def production_ddl_change_plan() -> dict[str, Any]:
    """Return the exact, intentionally narrow operator approval scope."""

    return {
        "approved_object": RESUME_EVENT_OBJECT,
        "authoritative_source": str(MIGRATION),
        "operation": "CREATE_IF_OBJECT_DOES_NOT_EXIST",
        "requires_explicit_flag": "--authorize-production-ddl",
        "excluded_operations": [
            "GENERAL_MIGRATION",
            "SAMPLING_ERA_CREATION",
            "CUTOVER_REPEAT",
            "QUOTA_REWRITE",
            "RESUME_EVENT_INSERT",
            "COLLECTOR_ACTIVATION",
        ],
    }


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


def _split_definition_items(body: str) -> list[str]:
    """Split a CREATE TABLE body on top-level commas.

    The source DDL contains commas inside ``IDENTITY(1,1)`` and other SQL
    expressions.  Splitting the body this way keeps the parser tied to the
    authoritative batch instead of duplicating its table definition.
    """

    items: list[str] = []
    start = 0
    depth = 0
    quote = False
    index = 0
    while index < len(body):
        character = body[index]
        if character == "'":
            if quote and index + 1 < len(body) and body[index + 1] == "'":
                index += 2
                continue
            quote = not quote
        elif not quote:
            if character == "(":
                depth += 1
            elif character == ")":
                depth = max(0, depth - 1)
            elif character == "," and depth == 0:
                item = body[start:index].strip()
                if item:
                    items.append(item)
                start = index + 1
        index += 1
    item = body[start:].strip()
    if item:
        items.append(item)
    return items


def _unbracket_identifier(value: str) -> str:
    return value.strip().strip("[]").replace("][", ".")


def _parse_authoritative_contract(body: str) -> dict[str, Any]:
    columns: list[str] = []
    column_definitions: list[dict[str, Any]] = []
    constraints: list[str] = []
    key_constraints: list[dict[str, Any]] = []
    check_constraints: list[dict[str, Any]] = []
    foreign_keys: list[dict[str, Any]] = []
    default_constraints: list[dict[str, Any]] = []

    for item in _split_definition_items(body):
        column_match = re.match(
            r"(?P<name>[A-Za-z][A-Za-z0-9_]*)\s+"
            r"(?P<data_type>[A-Za-z][A-Za-z0-9_]*)"
            r"(?:\((?P<type_args>[^)]*)\))?\s*(?P<tail>.*)",
            item,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if column_match is not None and not item.casefold().startswith("constraint "):
            name = column_match.group("name")
            data_type = column_match.group("data_type").upper()
            raw_args = (column_match.group("type_args") or "").strip()
            type_args: str | int | None
            if not raw_args:
                type_args = None
            elif raw_args.upper() == "MAX":
                type_args = "MAX"
            elif raw_args.isdigit():
                type_args = int(raw_args)
            else:
                type_args = raw_args
            tail = column_match.group("tail") or ""
            identity_match = re.search(
                r"\bIDENTITY\s*\(\s*(?P<seed>-?\d+)\s*,\s*(?P<increment>-?\d+)\s*\)",
                tail,
                flags=re.IGNORECASE,
            )
            nullable = not bool(re.search(r"\bNOT\s+NULL\b", tail, flags=re.IGNORECASE))
            column = {
                "name": name,
                "data_type": data_type,
                "type_args": type_args,
                "nullable": nullable,
                "is_identity": identity_match is not None,
                "identity_seed": int(identity_match.group("seed")) if identity_match else None,
                "identity_increment": int(identity_match.group("increment")) if identity_match else None,
            }
            column_definitions.append(column)
            columns.append(name)

            constraint_matches = re.findall(
                r"\bCONSTRAINT\s+([A-Za-z][A-Za-z0-9_]*)\b", tail, flags=re.IGNORECASE
            )
            constraints.extend(constraint_matches)
            default_match = re.search(
                r"\bCONSTRAINT\s+(?P<name>[A-Za-z][A-Za-z0-9_]*)\s+DEFAULT\s+(?P<definition>.+)$",
                tail,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if default_match:
                default_constraints.append(
                    {
                        "name": default_match.group("name"),
                        "column": name,
                        "definition": default_match.group("definition").strip(),
                    }
                )
            key_match = re.search(
                r"\bCONSTRAINT\s+(?P<name>[A-Za-z][A-Za-z0-9_]*)\s+"
                r"(?P<kind>PRIMARY\s+KEY|UNIQUE)\b"
                r"(?:\s+(?P<index_type>CLUSTERED|NONCLUSTERED))?",
                tail,
                flags=re.IGNORECASE,
            )
            if key_match:
                kind = re.sub(r"\s+", " ", key_match.group("kind").upper())
                key_constraints.append(
                    {
                        "name": key_match.group("name"),
                        "kind": "PRIMARY_KEY" if kind == "PRIMARY KEY" else "UNIQUE",
                        "columns": [name],
                        "index_type": (key_match.group("index_type") or "NONCLUSTERED").upper(),
                        "is_unique": True,
                    }
                )
            continue

        check_match = re.match(
            r"CONSTRAINT\s+(?P<name>[A-Za-z][A-Za-z0-9_]*)\s+CHECK\s*\((?P<definition>.*)\)\s*$",
            item,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if check_match:
            name = check_match.group("name")
            constraints.append(name)
            check_constraints.append(
                {"name": name, "definition": check_match.group("definition").strip()}
            )
            continue

        foreign_key_match = re.match(
            r"CONSTRAINT\s+(?P<name>[A-Za-z][A-Za-z0-9_]*)\s+"
            r"FOREIGN\s+KEY\s*\((?P<columns>[^)]*)\)\s+"
            r"REFERENCES\s+(?P<object>[A-Za-z][A-Za-z0-9_.\[\]]*)\s*"
            r"\((?P<referenced_columns>[^)]*)\)\s*$",
            item,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if foreign_key_match:
            name = foreign_key_match.group("name")
            constraints.append(name)
            foreign_keys.append(
                {
                    "name": name,
                    "columns": [
                        _unbracket_identifier(value)
                        for value in foreign_key_match.group("columns").split(",")
                    ],
                    "referenced_object": _unbracket_identifier(foreign_key_match.group("object")),
                    "referenced_columns": [
                        _unbracket_identifier(value)
                        for value in foreign_key_match.group("referenced_columns").split(",")
                    ],
                }
            )
            continue

        raise ValueError("authoritative ResumeEvent table body contains an unsupported definition")

    if not columns or not constraints:
        raise ValueError("ResumeEvent CREATE batch has no complete identifier contract")

    indexes = [
        {
            "name": key["name"],
            "index_type": key["index_type"],
            "is_unique": True,
            "is_primary_key": key["kind"] == "PRIMARY_KEY",
            "is_unique_constraint": key["kind"] == "UNIQUE",
            "columns": list(key["columns"]),
            "descending_columns": [False] * len(key["columns"]),
            "has_filter": False,
            "filter_definition": None,
        }
        for key in key_constraints
    ]
    return {
        "schema_name": "ctl",
        "object_name": RESUME_EVENT_OBJECT,
        "columns": columns,
        "column_definitions": column_definitions,
        "constraints": constraints,
        "key_constraints": key_constraints,
        "check_constraints": check_constraints,
        "foreign_keys": foreign_keys,
        "default_constraints": default_constraints,
        "indexes": indexes,
    }


def resume_event_contract(ddl_batch: str | None = None) -> dict[str, Any]:
    """Derive the complete table contract from the authoritative batch."""

    batch = ddl_batch if ddl_batch is not None else resume_event_ddl_batch()
    match = re.search(
        r"CREATE\s+TABLE\s+ctl\.MddMacProductionResumeEvent\s*\((?P<body>.*?)\n\s*\);",
        batch,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        raise ValueError("ResumeEvent CREATE batch has no parseable table body")
    return _parse_authoritative_contract(match.group("body"))


def _validate_repair_batch(ddl_batch: str) -> None:
    """Reject a custom batch that could create or alter another object."""

    executable_batch = re.sub(r"/\*.*?\*/", "", ddl_batch, flags=re.DOTALL)
    executable_batch = re.sub(r"--[^\r\n]*", "", executable_batch)
    create_targets = [
        _unbracket_identifier(value)
        for value in re.findall(
            r"\bCREATE\s+TABLE\s+([A-Za-z][A-Za-z0-9_.\[\]]*)",
            executable_batch,
            flags=re.IGNORECASE,
        )
    ]
    if create_targets != [RESUME_EVENT_OBJECT]:
        raise ValueError("repair batch must contain exactly one CREATE TABLE for the approved ResumeEvent object")
    create_kinds = re.findall(
        r"\bCREATE\s+([A-Za-z]+)\b", executable_batch, flags=re.IGNORECASE
    )
    if create_kinds != ["TABLE"]:
        raise ValueError("repair batch contains a CREATE operation outside the approved table")
    if re.search(
        r"\b(?:ALTER|DROP|TRUNCATE)\s+TABLE\b|\bCREATE\s+(?:INDEX|VIEW|PROCEDURE|PROC|FUNCTION)\b|\b(?:INSERT|UPDATE|DELETE|MERGE)\b",
        executable_batch,
        flags=re.IGNORECASE,
    ):
        raise ValueError("repair batch contains an operation outside the approved additive table creation")


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


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value) if value is not None else ""


def _bool_value(value: Any) -> bool:
    if isinstance(value, bytes):
        value = _text(value)
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "y", "granted"}
    return value in (1, True)


def _normalized_sql_expression(value: Any) -> str:
    expression = re.sub(r"\[([^]]+)\]", r"\1", _text(value).strip()).casefold()
    expression = re.sub(r"\(\s*(-?\d+(?:\.\d+)?)\s*\)", r"\1", expression)
    expression = re.sub(r"\s+", " ", expression)
    while expression.startswith("(") and expression.endswith(")"):
        depth = 0
        encloses_all = True
        quote = False
        for index, character in enumerate(expression):
            if character == "'":
                quote = not quote
            if quote:
                continue
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0 and index != len(expression) - 1:
                    encloses_all = False
                    break
        if encloses_all and depth == 0:
            expression = expression[1:-1].strip()
        else:
            break
    expression = re.sub(r"\s*(>=|<=|<>|=|>|<|\+|-|\*)\s*", r"\1", expression)
    return expression


def _numeric_equal(left: Any, right: Any) -> bool:
    try:
        return float(left) == float(right)
    except (TypeError, ValueError):
        return _text(left).strip() == _text(right).strip()


def _expected_storage_length(column: Mapping[str, Any]) -> int | None:
    data_type = _text(column.get("data_type")).upper()
    type_args = column.get("type_args")
    if type_args == "MAX":
        return -1
    if isinstance(type_args, int) and data_type in {"NVARCHAR", "NCHAR", "VARCHAR", "CHAR", "VARBINARY", "BINARY"}:
        return type_args * 2 if data_type in {"NVARCHAR", "NCHAR"} else type_args
    return {
        "BIGINT": 8,
        "INT": 4,
        "TINYINT": 1,
        "BIT": 1,
        "DATETIME2": 8,
        "DATETIME": 8,
        "DATE": 3,
        "UNIQUEIDENTIFIER": 16,
    }.get(data_type)


def _actual_index_contract(index_rows: Sequence[Any]) -> dict[str, dict[str, Any]]:
    indexes: dict[str, dict[str, Any]] = {}
    for row in index_rows:
        name = _text(_row_value(row, "index_name", 0))
        if not name:
            continue
        spec = indexes.setdefault(
            name,
            {
                "name": name,
                "index_type": _text(_row_value(row, "index_type", 1)).upper(),
                "is_unique": _bool_value(_row_value(row, "is_unique", 2)),
                "is_primary_key": _bool_value(_row_value(row, "is_primary_key", 3)),
            "is_unique_constraint": _bool_value(_row_value(row, "is_unique_constraint", 4)),
            "columns": [],
            "included_columns": [],
            "descending_columns": [],
            "has_filter": _bool_value(_row_value(row, "has_filter", 9)),
            "filter_definition": _text(_row_value(row, "filter_definition", 10)) or None,
        },
        )
        column_name = _text(_row_value(row, "column_name", 6))
        if not column_name:
            continue
        if _bool_value(_row_value(row, "is_included_column", 8)):
            spec["included_columns"].append(column_name)
        else:
            key_ordinal = _row_value(row, "key_ordinal", 5)
            spec["columns"].append((int(key_ordinal or 0), column_name))
            spec["descending_columns"].append(
                (int(key_ordinal or 0), _bool_value(_row_value(row, "is_descending_key", 7)))
            )
    for spec in indexes.values():
        spec["columns"] = [column for _ordinal, column in sorted(spec["columns"])]
        spec["descending_columns"] = [value for _ordinal, value in sorted(spec["descending_columns"])]
    return indexes


def _actual_key_contract(key_rows: Sequence[Any]) -> dict[str, dict[str, Any]]:
    keys: dict[str, dict[str, Any]] = {}
    for row in key_rows:
        name = _text(_row_value(row, "constraint_name", 0))
        if not name:
            continue
        spec = keys.setdefault(
            name,
            {
                "name": name,
                "kind": (
                    "PRIMARY_KEY"
                    if "PRIMARY" in _text(_row_value(row, "key_type_desc", 1)).upper()
                    else "UNIQUE"
                ),
                "index_type": _text(_row_value(row, "index_type", 2)).upper(),
                "is_unique": _bool_value(_row_value(row, "is_unique", 3)),
                "columns": [],
            },
        )
        column_name = _text(_row_value(row, "column_name", 5))
        if column_name:
            spec["columns"].append(
                (
                    int(_row_value(row, "key_ordinal", 4) or 0),
                    column_name,
                )
            )
    for spec in keys.values():
        spec["columns"] = [column for _ordinal, column in sorted(spec["columns"])]
    return keys


def _schema_verification(cursor: Any, contract: Mapping[str, Any]) -> dict[str, Any]:
    """Prove that the deployed table matches the source-defined table contract."""

    cursor.execute(
        """
        SELECT s.name + N'.' + o.name AS object_name,
               s.name AS schema_name,
               USER_NAME(s.principal_id) AS schema_owner,
               o.type AS object_type,
               o.type_desc AS object_type_desc
        FROM sys.objects AS o
        INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        WHERE s.name = N'ctl' AND o.name = N'MddMacProductionResumeEvent'
        """
    )
    object_row = cursor.fetchone()
    cursor.execute(
        """
        SELECT c.column_id, c.name AS column_name, TYPE_NAME(c.user_type_id) AS data_type,
               c.max_length, c.precision, c.scale, c.is_nullable, c.is_identity,
               idc.seed_value AS identity_seed, idc.increment_value AS identity_increment,
               dc.name AS default_constraint_name, dc.definition AS default_definition
        FROM sys.columns AS c
        INNER JOIN sys.objects AS o ON o.object_id = c.object_id
        INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        LEFT JOIN sys.identity_columns AS idc ON idc.object_id = c.object_id AND idc.column_id = c.column_id
        LEFT JOIN sys.default_constraints AS dc ON dc.parent_object_id = c.object_id AND dc.parent_column_id = c.column_id
        WHERE s.name = N'ctl' AND o.name = N'MddMacProductionResumeEvent'
        ORDER BY c.column_id
        """
    )
    column_rows = _rows(cursor)
    cursor.execute(
        """
        SELECT kc.name AS constraint_name, kc.type_desc AS key_type_desc,
               i.type_desc AS index_type, i.is_unique,
               ic.key_ordinal, c.name AS column_name
        FROM sys.key_constraints AS kc
        INNER JOIN sys.objects AS o ON o.object_id = kc.parent_object_id
        INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        INNER JOIN sys.indexes AS i ON i.object_id = kc.parent_object_id AND i.index_id = kc.unique_index_id
        LEFT JOIN sys.index_columns AS ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
        LEFT JOIN sys.columns AS c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
        WHERE s.name = N'ctl' AND o.name = N'MddMacProductionResumeEvent'
        ORDER BY kc.name, ic.key_ordinal
        """
    )
    key_rows = _rows(cursor)
    cursor.execute(
        """
        SELECT cc.name AS constraint_name, cc.definition,
               cc.is_not_trusted, cc.is_disabled
        FROM sys.check_constraints AS cc
        INNER JOIN sys.objects AS o ON o.object_id = cc.parent_object_id
        INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        WHERE s.name = N'ctl' AND o.name = N'MddMacProductionResumeEvent'
        """
    )
    check_rows = _rows(cursor)
    cursor.execute(
        """
        SELECT fk.name AS constraint_name, fk.is_disabled, fk.is_not_trusted,
               fkc.constraint_column_id, pc.name AS column_name,
               rs.name + N'.' + ro.name AS referenced_object_name,
               rc.name AS referenced_column_name
        FROM sys.foreign_keys AS fk
        INNER JOIN sys.objects AS o ON o.object_id = fk.parent_object_id
        INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        INNER JOIN sys.foreign_key_columns AS fkc ON fkc.constraint_object_id = fk.object_id
        INNER JOIN sys.columns AS pc ON pc.object_id = fkc.parent_object_id AND pc.column_id = fkc.parent_column_id
        INNER JOIN sys.objects AS ro ON ro.object_id = fkc.referenced_object_id
        INNER JOIN sys.schemas AS rs ON rs.schema_id = ro.schema_id
        INNER JOIN sys.columns AS rc ON rc.object_id = fkc.referenced_object_id AND rc.column_id = fkc.referenced_column_id
        WHERE s.name = N'ctl' AND o.name = N'MddMacProductionResumeEvent'
        ORDER BY fk.name, fkc.constraint_column_id
        """
    )
    foreign_key_rows = _rows(cursor)
    cursor.execute(
        """
        SELECT dc.name AS constraint_name, c.name AS column_name, dc.definition
        FROM sys.default_constraints AS dc
        INNER JOIN sys.objects AS o ON o.object_id = dc.parent_object_id
        INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        INNER JOIN sys.columns AS c ON c.object_id = dc.parent_object_id AND c.column_id = dc.parent_column_id
        WHERE s.name = N'ctl' AND o.name = N'MddMacProductionResumeEvent'
        """
    )
    default_rows = _rows(cursor)
    cursor.execute(
        """
        SELECT i.name AS index_name, i.type_desc AS index_type,
               i.is_unique, i.is_primary_key, i.is_unique_constraint,
               ic.key_ordinal, c.name AS column_name,
               ic.is_descending_key, ic.is_included_column,
               i.has_filter, i.filter_definition
        FROM sys.indexes AS i
        INNER JOIN sys.objects AS o ON o.object_id = i.object_id
        INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        LEFT JOIN sys.index_columns AS ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
        LEFT JOIN sys.columns AS c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
        WHERE s.name = N'ctl' AND o.name = N'MddMacProductionResumeEvent'
          AND i.index_id > 0 AND i.name IS NOT NULL
        ORDER BY i.name, ic.key_ordinal
        """
    )
    index_rows = _rows(cursor)
    cursor.execute(
        """
        SELECT HAS_PERMS_BY_NAME(N'ctl.MddMacProductionResumeEvent', N'OBJECT', N'SELECT') AS can_select,
               HAS_PERMS_BY_NAME(N'ctl.MddMacProductionResumeEvent', N'OBJECT', N'INSERT') AS can_insert,
               HAS_PERMS_BY_NAME(N'ctl.MddMacProductionResumeEvent', N'OBJECT', N'VIEW DEFINITION') AS can_view_definition,
               HAS_PERMS_BY_NAME(N'ctl.MddMacProductionResumeEvent', N'OBJECT', N'ALTER') AS can_alter,
               HAS_PERMS_BY_NAME(N'ctl.MddMacProductionResumeEvent', N'OBJECT', N'CONTROL') AS can_control,
               IS_SRVROLEMEMBER(N'sysadmin') AS is_sysadmin
        """
    )
    permission_row = cursor.fetchone()

    expected_columns = list(contract.get("columns", ()))
    expected_column_definitions = list(contract.get("column_definitions", ()))
    if not expected_column_definitions:
        expected_column_definitions = [{"name": name} for name in expected_columns]
    expected_constraints = list(contract.get("constraints", ()))
    expected_keys = {
        _text(key.get("name")): key for key in contract.get("key_constraints", ())
    }
    expected_checks = {
        _text(check.get("name")): check for check in contract.get("check_constraints", ())
    }
    expected_foreign_keys = {
        _text(key.get("name")): key for key in contract.get("foreign_keys", ())
    }
    expected_defaults = {
        _text(default.get("name")): default for default in contract.get("default_constraints", ())
    }
    expected_indexes = {
        _text(index.get("name")): index for index in contract.get("indexes", ())
    }

    observed_columns = [_text(_row_value(row, "column_name", 1)) for row in column_rows]
    column_checks: dict[str, bool] = {}
    if observed_columns != expected_columns:
        column_checks["column_order"] = False
    else:
        column_checks["column_order"] = True
    for expected, row in zip(expected_column_definitions, column_rows):
        name = _text(expected.get("name"))
        actual_type = _text(_row_value(row, "data_type", 2)).upper()
        expected_type = _text(expected.get("data_type")).upper()
        type_matches = not expected_type or actual_type == expected_type
        expected_length = _expected_storage_length(expected)
        length_matches = expected_length is None or _numeric_equal(
            _row_value(row, "max_length", 3), expected_length
        )
        expected_scale = expected.get("type_args") if expected_type == "DATETIME2" else None
        scale_matches = expected_scale is None or _numeric_equal(
            _row_value(row, "scale", 5), expected_scale
        )
        nullable_matches = _bool_value(_row_value(row, "is_nullable", 6)) == bool(
            expected.get("nullable")
        )
        identity_matches = _bool_value(_row_value(row, "is_identity", 7)) == bool(
            expected.get("is_identity")
        )
        seed_matches = not expected.get("is_identity") or _numeric_equal(
            _row_value(row, "identity_seed", 8), expected.get("identity_seed")
        )
        increment_matches = not expected.get("is_identity") or _numeric_equal(
            _row_value(row, "identity_increment", 9), expected.get("identity_increment")
        )
        column_checks[f"column_{name}_type"] = type_matches
        column_checks[f"column_{name}_length"] = length_matches
        column_checks[f"column_{name}_scale"] = scale_matches
        column_checks[f"column_{name}_nullability"] = nullable_matches
        column_checks[f"column_{name}_identity"] = identity_matches and seed_matches and increment_matches

    actual_keys = _actual_key_contract(key_rows)
    actual_checks = {
        _text(_row_value(row, "constraint_name", 0)): {
            "name": _text(_row_value(row, "constraint_name", 0)),
            "definition": _text(_row_value(row, "definition", 1)),
            "is_not_trusted": _bool_value(_row_value(row, "is_not_trusted", 2)),
            "is_disabled": _bool_value(_row_value(row, "is_disabled", 3)),
        }
        for row in check_rows
        if _text(_row_value(row, "constraint_name", 0))
    }
    actual_foreign_keys: dict[str, dict[str, Any]] = {}
    for row in foreign_key_rows:
        name = _text(_row_value(row, "constraint_name", 0))
        if not name:
            continue
        spec = actual_foreign_keys.setdefault(
            name,
            {
                "name": name,
                "columns": [],
                "referenced_object": _text(_row_value(row, "referenced_object_name", 5)),
                "referenced_columns": [],
                "is_disabled": _bool_value(_row_value(row, "is_disabled", 1)),
                "is_not_trusted": _bool_value(_row_value(row, "is_not_trusted", 2)),
            },
        )
        ordinal = int(_row_value(row, "constraint_column_id", 3) or 0)
        spec["columns"].append((ordinal, _text(_row_value(row, "column_name", 4))))
        spec["referenced_columns"].append((ordinal, _text(_row_value(row, "referenced_column_name", 6))))
    for spec in actual_foreign_keys.values():
        spec["columns"] = [value for _ordinal, value in sorted(spec["columns"])]
        spec["referenced_columns"] = [value for _ordinal, value in sorted(spec["referenced_columns"])]
    actual_defaults = {
        _text(_row_value(row, "constraint_name", 0)): {
            "name": _text(_row_value(row, "constraint_name", 0)),
            "column": _text(_row_value(row, "column_name", 1)),
            "definition": _text(_row_value(row, "definition", 2)),
        }
        for row in default_rows
        if _text(_row_value(row, "constraint_name", 0))
    }
    actual_indexes = _actual_index_contract(index_rows)
    actual_constraint_names = sorted(
        set(actual_keys) | set(actual_checks) | set(actual_foreign_keys) | set(actual_defaults)
    )

    permission_values = {
        key: _row_value(permission_row, key, index)
        for index, key in enumerate(
            ("can_select", "can_insert", "can_view_definition", "can_alter", "can_control", "is_sysadmin")
        )
    }
    sysadmin = _bool_value(permission_values["is_sysadmin"])
    permission_granted = lambda value: _bool_value(value) or (sysadmin and value is None)
    permission_status = (
        all(
            permission_granted(permission_values[key])
            for key in ("can_select", "can_insert", "can_view_definition")
        )
        and (
            permission_granted(permission_values["can_alter"])
            or permission_granted(permission_values["can_control"])
        )
    )

    object_type = _text(_row_value(object_row, "object_type", 3)).strip().upper()
    schema_name = _text(_row_value(object_row, "schema_name", 1))
    schema_owner = _text(_row_value(object_row, "schema_owner", 2))
    keys_match = actual_keys == expected_keys
    checks_match = (
        set(actual_checks) == set(expected_checks)
        and all(
            not expected.get("definition")
            or _normalized_sql_expression(actual_checks[name].get("definition"))
            == _normalized_sql_expression(expected.get("definition"))
            for name, expected in expected_checks.items()
            if name in actual_checks
        )
        and all(not row.get("is_disabled") and not row.get("is_not_trusted") for row in actual_checks.values())
    )
    foreign_keys_match = (
        set(actual_foreign_keys) == set(expected_foreign_keys)
        and all(
            {
                "columns": actual_foreign_keys[name].get("columns"),
                "referenced_object": actual_foreign_keys[name].get("referenced_object"),
                "referenced_columns": actual_foreign_keys[name].get("referenced_columns"),
            }
            == {
                "columns": expected.get("columns"),
                "referenced_object": expected.get("referenced_object"),
                "referenced_columns": expected.get("referenced_columns"),
            }
            and not actual_foreign_keys[name].get("is_disabled")
            and not actual_foreign_keys[name].get("is_not_trusted")
            for name, expected in expected_foreign_keys.items()
            if name in actual_foreign_keys
        )
    )
    defaults_match = (
        set(actual_defaults) == set(expected_defaults)
        and all(
            actual_defaults[name].get("column") == expected.get("column")
            and _normalized_sql_expression(actual_defaults[name].get("definition"))
            == _normalized_sql_expression(expected.get("definition"))
            for name, expected in expected_defaults.items()
            if name in actual_defaults
        )
    )
    indexes_match = (
        set(actual_indexes) == set(expected_indexes)
        and all(
            {
                "index_type": actual_indexes[name].get("index_type"),
                "is_unique": actual_indexes[name].get("is_unique"),
                "is_primary_key": actual_indexes[name].get("is_primary_key"),
                "is_unique_constraint": actual_indexes[name].get("is_unique_constraint"),
                "columns": actual_indexes[name].get("columns"),
                "included_columns": actual_indexes[name].get("included_columns"),
                "descending_columns": actual_indexes[name].get("descending_columns"),
                "has_filter": actual_indexes[name].get("has_filter"),
                "filter_definition": actual_indexes[name].get("filter_definition"),
            }
            == {
                "index_type": expected.get("index_type"),
                "is_unique": expected.get("is_unique"),
                "is_primary_key": expected.get("is_primary_key"),
                "is_unique_constraint": expected.get("is_unique_constraint"),
                "columns": expected.get("columns"),
                "included_columns": [],
                "descending_columns": expected.get(
                    "descending_columns", [False] * len(expected.get("columns", []))
                ),
                "has_filter": expected.get("has_filter", False),
                "filter_definition": expected.get("filter_definition"),
            }
            for name, expected in expected_indexes.items()
            if name in actual_indexes
        )
    )
    unique_resume_key = next(
        (key for key in expected_keys.values() if key.get("columns") == ["ResumeKey"] and key.get("kind") == "UNIQUE"),
        None,
    )
    observed_unique_resume_key = next(
        (key for key in actual_keys.values() if key.get("columns") == ["ResumeKey"] and key.get("kind") == "UNIQUE"),
        None,
    )
    checks = {
        "object_present": object_row is not None,
        "object_is_user_table": object_type == "U",
        "schema_name_is_ctl": schema_name == _text(contract.get("schema_name") or "ctl"),
        "schema_owner_is_proven": bool(schema_owner),
        "columns_match_authoritative_definition": bool(column_checks) and all(column_checks.values()),
        "column_order_match": column_checks.get("column_order", False),
        "key_constraints_match_authoritative_definition": keys_match,
        "unique_resume_key_constraint_match": bool(unique_resume_key and observed_unique_resume_key),
        "foreign_keys_match_authoritative_definition": foreign_keys_match,
        "check_constraints_match_authoritative_definition": checks_match,
        "default_constraints_match_authoritative_definition": defaults_match,
        "indexes_match_authoritative_definition": indexes_match,
        "constraints_match_authoritative_definition": (
            set(actual_constraint_names) == set(expected_constraints)
            and keys_match
            and checks_match
            and foreign_keys_match
            and defaults_match
        ),
        "permissions_proven": permission_status,
    }
    return {
        "status": "PASS" if all(checks.values()) else "NOT_PROVEN",
        "checks": checks,
        "observed_schema_name": schema_name,
        "observed_schema_owner": schema_owner,
        "observed_columns": observed_columns,
        "expected_columns": expected_columns,
        "column_checks": column_checks,
        "observed_column_definitions": [dict(row) if isinstance(row, Mapping) else row for row in column_rows],
        "expected_column_definitions": expected_column_definitions,
        "observed_constraints": actual_constraint_names,
        "expected_constraints": expected_constraints,
        "observed_key_constraints": list(actual_keys.values()),
        "expected_key_constraints": list(expected_keys.values()),
        "observed_check_constraints": list(actual_checks.values()),
        "expected_check_constraints": list(expected_checks.values()),
        "observed_foreign_keys": list(actual_foreign_keys.values()),
        "expected_foreign_keys": list(expected_foreign_keys.values()),
        "observed_default_constraints": list(actual_defaults.values()),
        "expected_default_constraints": list(expected_defaults.values()),
        "indexes": [dict(row) if isinstance(row, Mapping) else row for row in index_rows],
        "observed_indexes": list(actual_indexes.values()),
        "expected_indexes": list(expected_indexes.values()),
        "permissions": permission_values,
    }


def _error_details(exc: BaseException) -> dict[str, Any]:
    return {
        "error_code": _sql_error_code(exc),
        "error_type": type(exc).__name__,
        "reason": _sanitize_sql_reason(exc),
    }


def _acquire_schema_repair_lock(connection: Any) -> None:
    """Serialize cooperating repair operations without changing source DDL."""

    cursor = connection.cursor()
    cursor.execute(
        """
        SET NOCOUNT ON;
        DECLARE @lock_result INT;
        EXEC @lock_result = sys.sp_getapplock
            @Resource = %s,
            @LockMode = N'Exclusive',
            @LockOwner = N'Session',
            @LockTimeout = 0;
        IF @lock_result < 0
            THROW 51002, 'ResumeEvent schema repair is already in progress', 1;
        """,
        (SCHEMA_REPAIR_APP_LOCK_RESOURCE,),
    )
    connection.commit()


def _release_schema_repair_lock(connection: Any) -> None:
    cursor = connection.cursor()
    cursor.execute(
        "EXEC sys.sp_releaseapplock @Resource = %s, @LockOwner = N'Session';",
        (SCHEMA_REPAIR_APP_LOCK_RESOURCE,),
    )
    connection.commit()


def _reconcile_schema_state(
    settings: MssqlConnectionSettings,
    contract: Mapping[str, Any],
    *,
    connection_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Inspect the result through a fresh authorized SQL connection."""

    connection: Any | None = None
    factory = connection_factory or (lambda: connect_mssql(settings))
    try:
        connection = factory()
        verification = _schema_verification(connection.cursor(), contract)
        object_exists = bool((verification.get("checks") or {}).get("object_present"))
        return {
            "status": verification.get("status", "NOT_PROVEN"),
            "object_exists": object_exists,
            "schema_verification": verification,
        }
    except BaseException as exc:
        return {
            "status": "NOT_PROVEN",
            "object_exists": None,
            "error": _error_details(exc),
        }
    finally:
        if connection is not None:
            try:
                connection.close()
            except BaseException:
                pass


def _repair_result(
    creation_status: str,
    *,
    transaction_execution_started: bool = False,
    commit_attempted: bool = False,
    commit_confirmed: bool = False,
    rollback_attempted: bool = False,
    rollback_confirmed: bool = False,
    transaction_final_state_unknown: bool = False,
    object_exists_after_operation: bool | None = None,
    **details: Any,
) -> dict[str, Any]:
    if creation_status not in CREATION_STATUSES:
        raise ValueError(f"unsupported ResumeEvent creation status: {creation_status}")
    successful = creation_status in {
        "CREATED_BY_THIS_OPERATION",
        "ALREADY_EXISTS_AND_VALID",
        "CREATED_OR_CHANGED_BY_ANOTHER_OPERATION",
    }
    transaction_final_state = (
        "COMMITTED"
        if commit_confirmed
        else "ROLLED_BACK"
        if rollback_confirmed
        else "UNKNOWN"
        if transaction_final_state_unknown
        else "NOT_STARTED"
        if not transaction_execution_started
        else "NOT_CONFIRMED"
    )
    result = {
        "status": "PASS" if successful else "NOT_PROVEN",
        "change_plan": production_ddl_change_plan(),
        "creation_status": creation_status,
        "object_creation_status": creation_status,
        "result_code": creation_status,
        "transaction_execution_started": transaction_execution_started,
        "transaction_started": transaction_execution_started,
        "commit_attempted": commit_attempted,
        "commit_was_attempted": commit_attempted,
        "commit_confirmed": commit_confirmed,
        "commit_was_confirmed": commit_confirmed,
        "transaction_committed": commit_confirmed,
        "rollback_attempted": rollback_attempted,
        "rollback_was_attempted": rollback_attempted,
        "rollback_confirmed": rollback_confirmed,
        "rollback_was_confirmed": rollback_confirmed,
        "transaction_rolled_back": rollback_confirmed,
        "transaction_final_state_unknown": transaction_final_state_unknown,
        "final_state_unknown": transaction_final_state_unknown,
        "transaction_final_state": transaction_final_state,
        "commit_outcome_unknown": creation_status == "COMMIT_OUTCOME_UNKNOWN",
        "object_exists_after_operation": object_exists_after_operation,
        "approved_object_exists_after_operation": object_exists_after_operation,
        "created_objects": (
            [RESUME_EVENT_OBJECT] if creation_status == "CREATED_BY_THIS_OPERATION" else []
        ),
    }
    result.update(details)
    return result


def apply_resume_event_repair(
    settings: MssqlConnectionSettings,
    *,
    inventory: Mapping[str, Any],
    backup: Mapping[str, Any],
    competing: Mapping[str, Any],
    ddl_batch: str | None = None,
    reconciliation_connection_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Create only the missing ResumeEvent table in one rollbackable unit.

    The CREATE batch is idempotent, so a successful execution is not evidence
    that this invocation created the table.  The result therefore reports the
    transaction lifecycle independently from the creation attribution.
    """

    missing = list(((inventory.get("source_defined_objects") or {}).get("missing") or []))
    batch = ddl_batch if ddl_batch is not None else resume_event_ddl_batch()
    try:
        _validate_repair_batch(batch)
        contract = resume_event_contract(batch)
    except BaseException as exc:
        return _repair_result(
            "CREATION_FAILED",
            reason="authoritative repair batch validation failed",
            original_error=_error_details(exc),
            ddl_source=str(MIGRATION),
        )

    if missing != [RESUME_EVENT_OBJECT]:
        if missing:
            return _repair_result(
                "CREATION_FAILED",
                reason="other required source-defined objects are missing",
                missing_objects=missing,
                ddl_source=str(MIGRATION),
            )
    elif backup.get("status") != "PASS":
        return _repair_result(
            "CREATION_FAILED",
            reason="backup VERIFYONLY readiness is not proven",
            ddl_source=str(MIGRATION),
        )
    elif competing.get("status") != "PASS":
        return _repair_result(
            "CREATION_FAILED",
            reason="an active competing collector or lease is present",
            ddl_source=str(MIGRATION),
        )

    connection: Any | None = None
    lock_acquired = False
    transaction_execution_started = False
    commit_attempted = False
    commit_confirmed = False
    rollback_attempted = False
    rollback_confirmed = False
    transaction_final_state_unknown = False
    original_error: BaseException | None = None
    rollback_error: BaseException | None = None
    lock_release_error: BaseException | None = None
    pre_ddl_verification: dict[str, Any] | None = None
    before_commit_verification: dict[str, Any] | None = None
    commit_outcome_unknown = False
    initial_inventory_said_missing = missing == [RESUME_EVENT_OBJECT]
    object_exists_after_operation: bool | None = None
    reconciliation: dict[str, Any] | None = None
    creation_status: str = "CREATION_FAILED"
    reason = "repair did not complete"

    try:
        connection = connect_mssql(settings)
        _acquire_schema_repair_lock(connection)
        lock_acquired = True
        cursor = connection.cursor()

        # This is the authoritative recheck after the stale read-only
        # inventory and after the repair lock is held.
        pre_ddl_verification = _schema_verification(cursor, contract)
        pre_object_exists = bool((pre_ddl_verification.get("checks") or {}).get("object_present"))
        if pre_object_exists:
            object_exists_after_operation = True
            if pre_ddl_verification.get("status") == "PASS":
                creation_status = (
                    "CREATED_OR_CHANGED_BY_ANOTHER_OPERATION"
                    if initial_inventory_said_missing
                    else "ALREADY_EXISTS_AND_VALID"
                )
                reason = (
                    "approved object appeared after the initial inventory and matches the authoritative contract"
                    if initial_inventory_said_missing
                    else "approved ResumeEvent object already exists and matches the authoritative contract"
                )
            else:
                creation_status = "CREATION_FAILED"
                reason = "existing ResumeEvent object is incompatible with the authoritative contract; no DDL executed"
            return _repair_result(
                creation_status,
                object_exists_after_operation=object_exists_after_operation,
                reason=reason,
                schema_verification=pre_ddl_verification,
                ddl_source=str(MIGRATION),
            )

        # If the first inventory did not identify exactly this one missing
        # table, a disappearance between reads is not permission to recreate it.
        if not initial_inventory_said_missing:
            reason = "schema state changed after inventory; the approved object is no longer present"
            return _repair_result(
                "CREATION_FAILED",
                object_exists_after_operation=False,
                reason=reason,
                schema_verification=pre_ddl_verification,
                ddl_source=str(MIGRATION),
            )

        transaction_execution_started = True
        cursor.execute("SET XACT_ABORT ON; BEGIN TRANSACTION;")
        try:
            cursor.execute(batch)
            before_commit_verification = _schema_verification(cursor, contract)
            if before_commit_verification.get("status") != "PASS":
                raise RuntimeError("authoritative ResumeEvent schema verification failed before commit")
        except BaseException as exc:
            original_error = exc

        if original_error is None:
            commit_attempted = True
            try:
                connection.commit()
                commit_confirmed = True
            except BaseException as exc:
                # A DB-API commit exception is ambiguous: the server may have
                # committed before the client observed the error.
                original_error = exc
                commit_outcome_unknown = True
                transaction_final_state_unknown = True

        if original_error is not None and not commit_outcome_unknown:
            rollback_attempted = True
            try:
                connection.rollback()
                rollback_confirmed = True
            except BaseException as exc:
                rollback_error = exc
                rollback_confirmed = False
                transaction_final_state_unknown = True
    except BaseException as exc:
        if original_error is None:
            original_error = exc
        if transaction_execution_started and not commit_attempted and not rollback_attempted:
            rollback_attempted = True
            if connection is not None:
                try:
                    connection.rollback()
                    rollback_confirmed = True
                except BaseException as rollback_exc:
                    rollback_error = rollback_exc
                    rollback_confirmed = False
                    transaction_final_state_unknown = True
    finally:
        safe_to_release_lock = (
            not transaction_execution_started or commit_confirmed or rollback_confirmed
        )
        if connection is not None and lock_acquired and safe_to_release_lock:
            try:
                _release_schema_repair_lock(connection)
            except BaseException as exc:
                lock_release_error = exc
                transaction_final_state_unknown = True
        if connection is not None:
            try:
                connection.close()
            except BaseException as exc:
                if original_error is None:
                    original_error = exc
                transaction_final_state_unknown = True

    if transaction_execution_started and (commit_confirmed or commit_outcome_unknown or original_error is not None):
        reconciliation = _reconcile_schema_state(
            settings,
            contract,
            connection_factory=reconciliation_connection_factory,
        )
        object_exists_after_operation = reconciliation.get("object_exists")
        if reconciliation.get("error") is not None:
            transaction_final_state_unknown = True
    if commit_outcome_unknown:
        creation_status = "COMMIT_OUTCOME_UNKNOWN"
        reason = "COMMIT raised an ambiguous error; the resulting schema state was independently reconciled"
    elif original_error is not None:
        creation_status = "CREATION_FAILED"
        reason = "ResumeEvent repair failed before commit"
    elif commit_confirmed:
        if reconciliation and reconciliation.get("status") == "PASS":
            creation_status = "CREATED_BY_THIS_OPERATION"
            reason = "the table was absent under the repair lock, the authoritative batch committed, and independent verification passed"
        else:
            creation_status = "CREATION_FAILED"
            reason = "COMMIT was confirmed but independent authoritative schema verification failed"

    details: dict[str, Any] = {
        "reason": reason,
        "ddl_source": str(MIGRATION),
        "schema_verification_before_commit": before_commit_verification,
        "schema_verification_after_commit": (
            reconciliation.get("schema_verification") if reconciliation else None
        ),
        "reconciliation": reconciliation,
        "initial_inventory_missing": initial_inventory_said_missing,
    }
    if original_error is not None:
        details["original_error"] = _error_details(original_error)
        details.update(
            {
                "error_code": _sql_error_code(original_error),
                "error_type": type(original_error).__name__,
                "reason": reason,
            }
        )
    if rollback_error is not None:
        details["rollback_error"] = _error_details(rollback_error)
    if lock_release_error is not None:
        details["lock_release_error"] = _error_details(lock_release_error)
    if reconciliation and reconciliation.get("error") is not None:
        details["reconciliation_error"] = reconciliation["error"]

    return _repair_result(
        creation_status,
        transaction_execution_started=transaction_execution_started,
        commit_attempted=commit_attempted,
        commit_confirmed=commit_confirmed,
        rollback_attempted=rollback_attempted,
        rollback_confirmed=rollback_confirmed,
        transaction_final_state_unknown=transaction_final_state_unknown,
        object_exists_after_operation=object_exists_after_operation,
        **details,
    )


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
            "change_plan": production_ddl_change_plan(),
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
