#!/usr/bin/env python3
"""Retire verified legacy observation storage and optionally shrink one MDF.

This command is intentionally conservative.  It will not execute the
retirement SQL without a fresh backup evidence artifact, complete exact
parity evidence, a completed compact migration, no active writer, and the
explicit ``--apply`` flag.  Shrinking is a second explicit gate and is
performed only as a bounded ``DBCC SHRINKFILE`` for the named data file.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

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
from validate_mssql_migration import split_batches  # noqa: E402


RETIREMENT_SQL = ROOT / "sql" / "mdd-mac-collector" / "retire-legacy-observation-storage-v1.sql"
SAFETY_FLOOR_GIB = 12.0
RETIREMENT_EVIDENCE_MAX_AGE = timedelta(minutes=30)
RETIREMENT_MAX_LOG_USED_PERCENT = 70.0
RETIREMENT_REQUIRED_VIEW_NAMES = (
    "MddMacSourceSnapshot",
    "MddMacCallObservation",
    "MddMacTimingObservation",
    "MddMacSituationEvidence",
    "MddMacServiceSectionEvidence",
    "MddMacAttributeEvidence",
    "MddMacResponseError",
    "MddMacParserGap",
)
RETIREMENT_RESERVE_FIELDS = (
    "transaction_log_reserve_bytes",
    "tempdb_reserve_bytes",
    "rollback_reserve_bytes",
    "ddl_index_reserve_bytes",
    "autogrowth_reserve_bytes",
    "external_disk_consumption_reserve_bytes",
)
RETIREMENT_CAPACITY_BASIS = "FULL_HISTORY_ISOLATED_LAB_AND_PRODUCTION_READONLY_AUDIT"
RETIREMENT_COMPACT_CONTRACT = {
    "stg.MddMacCompactStorageState": (
        "StateId",
        "StorageFormatVersion",
        "MigrationImplementationVersion",
        "FutureWriteEnabled",
    ),
    "stg.MddMacCompactObservationEnvelope": (
        "SnapshotId",
        "EnvelopeJsonGzip",
        "EnvelopeSha256",
        "VerificationStatus",
    ),
}
RETIREMENT_COMPACT_COLUMN_CONTRACT = {
    "stg.MddMacCompactStorageState": {
        "StateId": ("tinyint", 1, False),
        "StorageFormatVersion": ("smallint", 2, False),
        "MigrationImplementationVersion": ("varchar", 100, False),
        "FutureWriteEnabled": ("bit", 1, False),
    },
    "stg.MddMacCompactObservationEnvelope": {
        "SnapshotId": ("bigint", 8, False),
        "EnvelopeJsonGzip": ("varbinary", -1, False),
        "EnvelopeSha256": ("char", 64, False),
        "VerificationStatus": ("varchar", 20, False),
    },
}


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


def _drain(cursor: Any) -> None:
    """Consume every result set emitted by a multi-batch SQL script."""

    while True:
        try:
            cursor.fetchall()
        except Exception:
            pass
        try:
            has_next = cursor.nextset()
        except Exception:
            return
        if not has_next:
            return


def _collector_gate() -> dict[str, Any]:
    return local_collector_safety_gate()


def _load_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"evidence file is not a JSON object: {path}")
    return value


def _is_off_c_sql_server_path(path: Any) -> bool:
    normalized = str(path or "").strip().replace("/", "\\")
    return normalized.startswith("\\\\") or bool(re.match(r"^[d-zD-Z]:[\\/]", normalized))


def _backup_gate(path: Path) -> dict[str, Any]:
    data = _load_json(path)
    backup = data.get("backup") if isinstance(data.get("backup"), Mapping) else None
    verify = backup.get("verifyonly") if isinstance(backup, Mapping) else None
    if verify is None and isinstance(data.get("sql"), Mapping):
        verify = data["sql"].get("backup_verifyonly")
    status = data.get("status")
    if isinstance(verify, Mapping):
        verify_status = verify.get("status")
    else:
        verify_status = "PASS" if verify == "PASS" else None
    metadata_ok = (
        isinstance(backup, Mapping)
        and backup.get("status") == "PASS"
        and str(backup.get("database_name") or "") == "CologneTransitIntelligence"
        and backup.get("copy_only") is True
        and backup.get("checksum") is True
        and int(backup.get("compressed_backup_size") or 0) > 0
        and backup.get("backup_start_date") is not None
        and backup.get("backup_finish_date") is not None
        and str(backup.get("backup_path") or "").strip()
        and _is_off_c_sql_server_path(backup.get("backup_path"))
        and int(backup.get("backup_set_id") or 0) > 0
        and int(backup.get("media_set_id") or 0) > 0
        and int(backup.get("media_count") or 0) > 0
        and backup.get("destination_accessibility") == "PASS"
    )
    # Legacy evidence is intentionally not enough for irreversible retirement:
    # only the fresh verifier's metadata has a SQL-visible path and timestamp.
    passed = status == "PASS" and verify_status == "PASS" and metadata_ok
    return {
        "status": "PASS" if passed else "BLOCKED",
        "source": str(path),
        "verifyonly": verify_status,
        "fresh_metadata": "PASS" if metadata_ok else "BLOCKED",
    }


def _approval_gate(path: Path) -> dict[str, Any]:
    """Validate the one explicit human approval required before retirement."""

    data = _load_json(path)
    required = (
        "approved_by",
        "approved_at_utc",
        "sql_objects",
        "verified_row_counts",
        "backup_location",
        "backup_verification",
        "expected_space_recovery_gib",
        "worst_case_temporary_storage_gib",
        "rollback_procedure",
    )
    missing = [name for name in required if name not in data]
    objects = data.get("sql_objects")
    counts = data.get("verified_row_counts")
    try:
        expected_recovery = float(data.get("expected_space_recovery_gib"))
        temporary_storage = float(data.get("worst_case_temporary_storage_gib"))
    except (TypeError, ValueError):
        expected_recovery = -1.0
        temporary_storage = -1.0
    valid = (
        data.get("approved") is True
        and str(data.get("approved_by") or "").strip()
        and str(data.get("approved_at_utc") or "").strip()
        and not missing
        and isinstance(objects, list)
        and bool(objects)
        and isinstance(counts, Mapping)
        and bool(counts)
        and str(data.get("backup_location") or "").strip()
        and str(data.get("backup_verification")).strip()
        and expected_recovery >= 0
        and temporary_storage >= 0
        and str(data.get("rollback_procedure")).strip()
    )
    return {
        "status": "PASS" if valid else "BLOCKED",
        "source": str(path),
        "missing_fields": missing,
        "approved": data.get("approved") is True,
    }


def _fresh_utc(value: Any, *, field: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is missing")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _powerbi_gate(path: Path, *, now: datetime | None = None) -> dict[str, Any]:
    """Require evidence from the actual Power BI consumer, not a claim."""

    data = _load_json(path)
    required = (
        "consumer_id",
        "dataset_or_report",
        "validated_at_utc",
        "approved_by",
        "evidence_source",
        "schema_contract_status",
    )
    missing = [name for name in required if not str(data.get(name) or "").strip()]
    try:
        validated_at = _fresh_utc(data.get("validated_at_utc"), field="validated_at_utc")
        age = (now or datetime.now(timezone.utc)).astimezone(timezone.utc) - validated_at
        fresh = timedelta(0) <= age <= RETIREMENT_EVIDENCE_MAX_AGE
    except ValueError:
        age = None
        fresh = False
    passed = (
        data.get("status") == "PASS"
        and data.get("approved") is True
        and data.get("refresh_verified") is True
        and data.get("evidence_source") == "REAL_POWER_BI_CONSUMER"
        and data.get("schema_contract_status") == "PASS"
        and not missing
        and fresh
    )
    return {
        "status": "PASS" if passed else "BLOCKED",
        "source": str(path),
        "consumer_id": data.get("consumer_id"),
        "dataset_or_report": data.get("dataset_or_report"),
        "refresh_verified": data.get("refresh_verified") is True,
        "schema_contract_status": data.get("schema_contract_status"),
        "missing_fields": missing,
        "fresh": fresh,
        "age_seconds": age.total_seconds() if age is not None else None,
        "evidence_source": data.get("evidence_source"),
    }


def _retirement_reserve_gate(data: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Validate measured reserve evidence carried by the approval artifact."""

    reserve = data.get("capacity_reserves")
    if not isinstance(reserve, Mapping):
        return {
            "status": "BLOCKED",
            "reason": "approval evidence has no capacity_reserves object",
            "missing_fields": ["capacity_reserves"],
        }
    missing: list[str] = []
    values: dict[str, int] = {}
    for name in RETIREMENT_RESERVE_FIELDS:
        item = reserve.get(name)
        if isinstance(item, Mapping):
            status = item.get("status")
            raw_bytes = item.get("bytes")
        else:
            status = reserve.get("status")
            raw_bytes = item
        if status != "PASS" or isinstance(raw_bytes, bool) or not isinstance(raw_bytes, int) or raw_bytes <= 0:
            missing.append(name)
        else:
            values[name] = raw_bytes
    basis = str(reserve.get("measurement_basis") or data.get("capacity_measurement_basis") or "").strip()
    if basis != RETIREMENT_CAPACITY_BASIS:
        missing.append("measurement_basis")
    measured_at_value = reserve.get("measured_at_utc") or data.get("capacity_measurement_timestamp_utc")
    try:
        measured_at = _fresh_utc(measured_at_value, field="capacity_reserves.measured_at_utc")
        age = (now or datetime.now(timezone.utc)).astimezone(timezone.utc) - measured_at
        fresh = timedelta(0) <= age <= RETIREMENT_EVIDENCE_MAX_AGE
    except ValueError:
        age = None
        fresh = False
        missing.append("measured_at_utc")
    if not fresh:
        missing.append("fresh_capacity_measurement")
    return {
        "status": "PASS" if not missing else "BLOCKED",
        "values": values,
        "missing_fields": sorted(set(missing)),
        "measurement_basis": basis,
        "measured_at_utc": measured_at.isoformat().replace("+00:00", "Z") if age is not None else None,
        "age_seconds": age.total_seconds() if age is not None else None,
    }


def _parity_gate(path: Path, *, require_full_history: bool = False) -> dict[str, Any]:
    data = _load_json(path)
    units: list[Any] = []
    if isinstance(data.get("units"), list):
        units = data["units"]
    elif isinstance(data.get("run"), Mapping) and isinstance(data["run"].get("units"), list):
        units = data["run"]["units"]
    unit_results: list[bool] = []
    for unit in units:
        if not isinstance(unit, Mapping):
            unit_results.append(False)
            continue
        unit_status = str(unit.get("status") or "")
        comparison = unit.get("comparison") or unit.get("sql_readback_parity")
        offline = unit.get("offline_parity")
        comparison_status = comparison.get("status") if isinstance(comparison, Mapping) else None
        offline_status = offline.get("status") if isinstance(offline, Mapping) else None
        # The migration runner calls a verified unit VERIFIED, whereas the
        # standalone parity validator calls it PASS.  Both are valid only
        # when the actual field-level comparison is PASS.
        unit_results.append(
            unit_status in {"PASS", "VERIFIED"}
            and (comparison_status in {None, "PASS"})
            and (offline_status in {None, "PASS"})
            and comparison_status == "PASS"
            and (offline is None or offline_status == "PASS")
        )
    top_status = data.get("status")
    run = data.get("run") if isinstance(data.get("run"), Mapping) else data
    full_history_checks: dict[str, Any] = {}
    if require_full_history:
        selected_is_full_history = run.get("selected_is_full_history") is True
        total_source_count = run.get("total_source_snapshot_count")
        selected_count = run.get("selected_snapshot_count")
        full_history_checks = {
            "selected_is_full_history": selected_is_full_history,
            "total_source_snapshot_count": total_source_count,
            "selected_snapshot_count": selected_count,
            "counts_match": (
                isinstance(total_source_count, int)
                and isinstance(selected_count, int)
                and total_source_count > 0
                and total_source_count == selected_count == len(units)
            ),
        }
    passed = (
        top_status == "PASS"
        and bool(units)
        and all(unit_results)
        and (not require_full_history or all(full_history_checks.values()))
    )
    return {
        "status": "PASS" if passed else "BLOCKED",
        "source": str(path),
        "unit_count": len(units),
        "failed_unit_count": sum(1 for value in unit_results if not value),
        "full_history": full_history_checks if require_full_history else {"status": "NOT_REQUIRED"},
    }


def _file_capacity(cursor: Any, *, require_legacy_tables: bool = True) -> dict[str, Any]:
    def read_sql_files(query: str, *, label: str) -> list[dict[str, Any]]:
        cursor.execute(query)
        rows: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            file_id = _row_value(row, "file_id", 0)
            name = _row_value(row, "name", 1)
            type_name = str(_row_value(row, "type_desc", 2) or "").upper()
            size_pages = _row_value(row, "size", 3)
            space_used_pages = _row_value(row, "space_used_pages", 4)
            growth = _row_value(row, "growth", 5)
            is_percent_growth = _row_value(row, "is_percent_growth", 6)
            max_size_pages = _row_value(row, "max_size_pages", 7)
            physical_name = _row_value(row, "physical_name", 8)
            volume = _row_value(row, "volume_mount_point", 9)
            available_bytes = _row_value(row, "available_bytes", 10)
            required = (
                file_id,
                name,
                type_name,
                size_pages,
                growth,
                is_percent_growth,
                max_size_pages,
                physical_name,
                volume,
                available_bytes,
            )
            if any(value is None for value in required):
                raise RuntimeError(f"SQL {label} allocation/autogrowth measurement is incomplete")
            if space_used_pages is None and type_name != "LOG":
                raise RuntimeError(f"SQL {label} used-space measurement is incomplete")
            allocated_bytes = int(size_pages) * 8192
            used_bytes = None if space_used_pages is None else int(space_used_pages) * 8192
            rows.append(
                {
                    "file_id": int(file_id),
                    "name": str(name),
                    "type": type_name,
                    "size_pages": int(size_pages),
                    "space_used_pages": None if space_used_pages is None else int(space_used_pages),
                    "allocated_bytes": allocated_bytes,
                    "used_bytes": used_bytes,
                    "growth": int(growth),
                    "is_percent_growth": bool(is_percent_growth),
                    "max_size_pages": int(max_size_pages),
                    "physical_name": str(physical_name),
                    "volume": str(volume),
                    "available_bytes": int(available_bytes),
                    "allocated_gib": allocated_bytes / (1024**3),
                    "used_gib": (used_bytes or 0) / (1024**3),
                    "internal_free_gib": max(0, allocated_bytes - (used_bytes or 0)) / (1024**3),
                    "windows_free_gib": int(available_bytes) / (1024**3),
                }
            )
        if not rows:
            raise RuntimeError(f"SQL {label} allocation measurement is empty")
        return rows

    database_files = read_sql_files(
        """
        SELECT df.file_id, df.name, df.type_desc, df.size,
               FILEPROPERTY(df.name, 'SpaceUsed') AS space_used_pages,
               df.growth, df.is_percent_growth, df.max_size,
               df.physical_name, vs.volume_mount_point, vs.available_bytes
        FROM sys.database_files AS df
        CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
        ORDER BY df.file_id
        """,
        label="database-file",
    )
    tempdb_files = read_sql_files(
        """
        SELECT df.file_id, df.name, df.type_desc, df.size,
               CONVERT(BIGINT, fsu.allocated_extent_page_count) AS space_used_pages,
               df.growth, df.is_percent_growth, df.max_size,
               df.physical_name, vs.volume_mount_point, vs.available_bytes
        FROM tempdb.sys.database_files AS df
        LEFT JOIN tempdb.sys.dm_db_file_space_usage AS fsu
          ON fsu.file_id = df.file_id
        CROSS APPLY sys.dm_os_volume_stats(DB_ID(N'tempdb'), df.file_id) AS vs
        ORDER BY df.file_id
        """,
        label="tempdb-file",
    )
    cursor.execute(
        """
        SELECT total_log_size_in_bytes, used_log_space_in_bytes,
               used_log_space_in_percent
        FROM sys.dm_db_log_space_usage
        WHERE database_id = DB_ID()
        """
    )
    log_row = cursor.fetchone()
    log_values = (
        _row_value(log_row, "total_log_size_in_bytes", 0),
        _row_value(log_row, "used_log_space_in_bytes", 1),
        _row_value(log_row, "used_log_space_in_percent", 2),
    )
    if any(value is None for value in log_values):
        raise RuntimeError("SQL transaction-log measurement is incomplete")

    cursor.execute(
        """
        SELECT COUNT_BIG(*) AS active_user_transactions
        FROM sys.dm_tran_session_transactions AS st
        INNER JOIN sys.dm_exec_sessions AS es ON es.session_id = st.session_id
        WHERE st.session_id <> @@SPID AND es.is_user_process = 1
        """
    )
    transaction_row = cursor.fetchone()
    active_transactions = _row_value(transaction_row, "active_user_transactions", 0)
    if active_transactions is None:
        raise RuntimeError("SQL active-transaction measurement is incomplete")

    cursor.execute(
        """
        SELECT o.name,
               SUM(CONVERT(BIGINT, ps.reserved_page_count)) * CONVERT(BIGINT, 8192) AS reserved_bytes,
               SUM(CONVERT(BIGINT, ps.used_page_count)) * CONVERT(BIGINT, 8192) AS used_bytes,
               SUM(CONVERT(BIGINT, ps.index_reserved_page_count)) * CONVERT(BIGINT, 8192) AS index_reserved_bytes
        FROM sys.dm_db_partition_stats AS ps
        INNER JOIN sys.objects AS o ON o.object_id = ps.object_id
        INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        WHERE s.name = N'stg'
          AND o.type = 'U'
          AND o.name IN
          (
              N'MddMacSourceSnapshot', N'MddMacCallObservation',
              N'MddMacTimingObservation', N'MddMacSituationEvidence',
              N'MddMacServiceSectionEvidence', N'MddMacAttributeEvidence',
              N'MddMacResponseError', N'MddMacParserGap'
          )
        GROUP BY o.name
        ORDER BY o.name
        """
    )
    legacy_rows = []
    for row in cursor.fetchall():
        reserved_bytes = _row_value(row, "reserved_bytes", 1)
        used_bytes = _row_value(row, "used_bytes", 2)
        index_reserved_bytes = _row_value(row, "index_reserved_bytes", 3)
        if any(value is None for value in (reserved_bytes, used_bytes, index_reserved_bytes)):
            raise RuntimeError("legacy physical allocation measurement is incomplete")
        legacy_rows.append(
            {
                "name": str(_row_value(row, "name", 0)),
                "reserved_bytes": int(reserved_bytes),
                "used_bytes": int(used_bytes),
                "index_reserved_bytes": int(index_reserved_bytes),
            }
        )
    if require_legacy_tables and len(legacy_rows) != len(RETIREMENT_REQUIRED_VIEW_NAMES):
        raise RuntimeError("all legacy table physical allocations must be measured before retirement")

    all_files = database_files
    return {
        "data_files": [row for row in database_files if row["type"] == "ROWS"],
        "log_files": [row for row in database_files if row["type"] == "LOG"],
        "all_files": all_files,
        "tempdb_files": tempdb_files,
        "log": {
            "total_bytes": int(log_values[0]),
            "used_bytes": int(log_values[1]),
            "used_percent": float(log_values[2]),
            "free_bytes": max(0, int(log_values[0]) - int(log_values[1])),
        },
        "active_user_transactions": int(active_transactions),
        "legacy_allocations": legacy_rows,
    }


def _compatibility_dependency_gate(cursor: Any) -> dict[str, Any]:
    """Validate the objects, dependencies, grants, and compact contract."""

    cursor.execute(
        """
        SELECT s.name AS schema_name, o.name AS object_name,
               o.type AS object_type, o.object_id
        FROM sys.objects AS o
        INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        WHERE (s.name = N'stg' AND o.name IN
               (N'MddMacSourceSnapshot', N'MddMacCallObservation',
                N'MddMacTimingObservation', N'MddMacSituationEvidence',
                N'MddMacServiceSectionEvidence', N'MddMacAttributeEvidence',
                N'MddMacResponseError', N'MddMacParserGap'))
           OR (s.name = N'stg' AND o.name IN
               (N'MddMacCompactStorageState', N'MddMacCompactObservationEnvelope'))
        """
    )
    objects = [
        {
            "schema": str(_row_value(row, "schema_name", 0)),
            "name": str(_row_value(row, "object_name", 1)),
            "type": str(_row_value(row, "object_type", 2)),
            "object_id": int(_row_value(row, "object_id", 3) or 0),
        }
        for row in cursor.fetchall()
    ]
    object_map = {(item["schema"], item["name"]): item for item in objects}
    legacy_present = all(("stg", name) in object_map and object_map[("stg", name)]["type"] == "U" for name in RETIREMENT_REQUIRED_VIEW_NAMES)
    compact_present = all(
        (schema_name, object_name) in object_map and object_map[(schema_name, object_name)]["type"] == "U"
        for schema_name, object_name in RETIREMENT_COMPACT_CONTRACT
    )

    cursor.execute(
        """
        SELECT COUNT_BIG(*) AS external_fk_count
        FROM sys.foreign_keys AS fk
        WHERE fk.referenced_id IN
        (
            SELECT o.object_id
            FROM sys.objects AS o
            INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
            WHERE s.name = N'stg' AND o.name IN
            (N'MddMacSourceSnapshot', N'MddMacCallObservation',
             N'MddMacTimingObservation', N'MddMacSituationEvidence',
             N'MddMacServiceSectionEvidence', N'MddMacAttributeEvidence',
             N'MddMacResponseError', N'MddMacParserGap')
        )
        AND fk.parent_object_id NOT IN
        (
            SELECT o.object_id
            FROM sys.objects AS o
            INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
            WHERE s.name = N'stg' AND o.name IN
            (N'MddMacSourceSnapshot', N'MddMacCallObservation',
             N'MddMacTimingObservation', N'MddMacSituationEvidence',
             N'MddMacServiceSectionEvidence', N'MddMacAttributeEvidence',
             N'MddMacResponseError', N'MddMacParserGap')
        )
        """
    )
    external_fk_count = int(_row_value(cursor.fetchone(), "external_fk_count", 0) or 0)
    cursor.execute(
        """
        SELECT COUNT_BIG(*) AS external_dependency_count
        FROM sys.sql_expression_dependencies AS dep
        INNER JOIN sys.objects AS referencing_object ON referencing_object.object_id = dep.referencing_id
        INNER JOIN sys.schemas AS referencing_schema ON referencing_schema.schema_id = referencing_object.schema_id
        WHERE dep.referenced_id IN
        (
            SELECT o.object_id
            FROM sys.objects AS o
            INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
            WHERE s.name = N'stg' AND o.name IN
            (N'MddMacSourceSnapshot', N'MddMacCallObservation',
             N'MddMacTimingObservation', N'MddMacSituationEvidence',
             N'MddMacServiceSectionEvidence', N'MddMacAttributeEvidence',
             N'MddMacResponseError', N'MddMacParserGap')
        )
        AND NOT
        (
            (
                referencing_schema.name = N'stg'
                AND referencing_object.name IN
                (N'uspPersistMddMacSnapshot', N'vMddMacSourceSnapshotJson',
                 N'vMddMacCallObservationJson', N'vMddMacTimingObservationJson',
                 N'vMddMacSituationEvidenceJson', N'vMddMacServiceSectionEvidenceJson',
                 N'vMddMacAttributeEvidenceJson', N'vMddMacParserGapJson')
            )
            OR
            (
                referencing_schema.name = N'analytics'
                AND referencing_object.name IN
                (N'vwMddMacRecordedAtTimeHistory', N'vwMddMacSourceFieldCoverage')
            )
        )
        """
    )
    external_dependency_count = int(_row_value(cursor.fetchone(), "external_dependency_count", 0) or 0)
    cursor.execute(
        """
        SELECT
            SUM(CASE WHEN p.state_desc LIKE N'DENY%' THEN 1 ELSE 0 END) AS deny_count,
            COUNT_BIG(*) AS explicit_permission_count
        FROM sys.database_permissions AS p
        WHERE p.major_id IN
        (
            SELECT o.object_id
            FROM sys.objects AS o
            INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
            WHERE s.name = N'stg' AND o.name IN
            (N'MddMacSourceSnapshot', N'MddMacCallObservation',
             N'MddMacTimingObservation', N'MddMacSituationEvidence',
             N'MddMacServiceSectionEvidence', N'MddMacAttributeEvidence',
             N'MddMacResponseError', N'MddMacParserGap')
        )
        AND p.class = 1
        """
    )
    permission_row = cursor.fetchone()
    deny_count = int(_row_value(permission_row, "deny_count", 0) or 0)
    explicit_permission_count = int(_row_value(permission_row, "explicit_permission_count", 1) or 0)

    cursor.execute(
        """
        SELECT s.name AS schema_name, o.name AS object_name, c.name AS column_name,
               t.name AS type_name, c.max_length, c.is_nullable
        FROM sys.columns AS c
        INNER JOIN sys.objects AS o ON o.object_id = c.object_id
        INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        INNER JOIN sys.types AS t ON t.user_type_id = c.user_type_id
        WHERE s.name = N'stg'
          AND o.name IN (N'MddMacCompactStorageState', N'MddMacCompactObservationEnvelope')
        """
    )
    actual_columns: dict[str, dict[str, tuple[str, int, bool]]] = {}
    for row in cursor.fetchall():
        key = f"{_row_value(row, 'schema_name', 0)}.{_row_value(row, 'object_name', 1)}"
        actual_columns.setdefault(key, {})[str(_row_value(row, "column_name", 2))] = (
            str(_row_value(row, "type_name", 3)).casefold(),
            int(_row_value(row, "max_length", 4) or 0),
            bool(_row_value(row, "is_nullable", 5)),
        )
    contract_mismatches = []
    for object_name, expected in RETIREMENT_COMPACT_COLUMN_CONTRACT.items():
        for column_name, expected_metadata in expected.items():
            actual = actual_columns.get(object_name, {}).get(column_name)
            if actual is None:
                contract_mismatches.append({"column": f"{object_name}.{column_name}", "kind": "missing"})
            elif actual != (expected_metadata[0].casefold(), expected_metadata[1], expected_metadata[2]):
                contract_mismatches.append(
                    {
                        "column": f"{object_name}.{column_name}",
                        "kind": "metadata",
                        "expected": expected_metadata,
                        "actual": actual,
                    }
                )
    checks = {
        "legacy_objects": {
            "status": "PASS" if legacy_present else "BLOCKED",
            "expected_count": len(RETIREMENT_REQUIRED_VIEW_NAMES),
            "actual_count": sum(1 for name in RETIREMENT_REQUIRED_VIEW_NAMES if ("stg", name) in object_map),
        },
        "compact_objects": {"status": "PASS" if compact_present else "BLOCKED"},
        "external_foreign_keys": {"status": "PASS" if external_fk_count == 0 else "BLOCKED", "count": external_fk_count},
        "external_dependencies": {"status": "PASS" if external_dependency_count == 0 else "BLOCKED", "count": external_dependency_count},
        "object_permissions": {
            "status": "PASS" if deny_count == 0 and explicit_permission_count >= 0 else "BLOCKED",
            "deny_count": deny_count,
            "explicit_permission_count": explicit_permission_count,
        },
        "compact_schema_contract": {
            "status": "PASS" if not contract_mismatches else "BLOCKED",
            "mismatches": contract_mismatches,
        },
    }
    return {
        "status": "PASS" if all(check["status"] == "PASS" for check in checks.values()) else "BLOCKED",
        "checks": checks,
    }


def _retirement_capacity_preflight(
    cursor: Any,
    approval_data: Mapping[str, Any],
    *,
    capacity: Mapping[str, Any] | None = None,
    log_limit: float = RETIREMENT_MAX_LOG_USED_PERCENT,
) -> dict[str, Any]:
    """Measure every physical reserve before the destructive transaction."""

    reserve_gate = _retirement_reserve_gate(approval_data)
    measured = dict(capacity) if capacity is not None else _file_capacity(cursor)
    reserve_values = reserve_gate.get("values", {})
    checks: dict[str, Any] = {"reserve_evidence": reserve_gate}
    all_files = [*measured.get("all_files", ()), *measured.get("tempdb_files", ())]
    volume_available: dict[str, int] = {}
    for item in all_files:
        key = str(item.get("volume") or "").strip().casefold().rstrip("\\")
        if not key:
            continue
        available = int(item.get("available_bytes", 0) or 0)
        volume_available[key] = min(volume_available.get(key, available), available)
    volume_requirements = {key: 0 for key in volume_available}
    c_volumes = [key for key in volume_available if key.startswith("c:")]
    for item in measured.get("all_files", ()):
        key = str(item.get("volume") or "").strip().casefold().rstrip("\\")
        if key not in volume_requirements:
            continue
        growth = reserve_values.get("autogrowth_reserve_bytes", 0)
        volume_requirements[key] += growth
        if str(item.get("type") or "").upper() == "LOG":
            volume_requirements[key] += reserve_values.get("transaction_log_reserve_bytes", 0)
        elif str(item.get("type") or "").upper() == "ROWS":
            volume_requirements[key] += reserve_values.get("ddl_index_reserve_bytes", 0)
    for item in measured.get("tempdb_files", ()):
        key = str(item.get("volume") or "").strip().casefold().rstrip("\\")
        if key in volume_requirements:
            volume_requirements[key] += reserve_values.get("tempdb_reserve_bytes", 0)
    for key in c_volumes:
        volume_requirements[key] += reserve_values.get("rollback_reserve_bytes", 0)
        volume_requirements[key] += reserve_values.get("external_disk_consumption_reserve_bytes", 0)

    volume_results = []
    for key in sorted(volume_available):
        required = volume_requirements.get(key, 0)
        safety = int(12 * 1024**3) if key.startswith("c:") else 0
        volume_results.append(
            {
                "volume": key,
                "available_bytes": volume_available[key],
                "required_reserve_bytes": required,
                "safety_floor_bytes": safety,
                "projected_free_bytes": volume_available[key] - required,
                "status": "PASS" if volume_available[key] - required >= safety else "BLOCKED",
            }
        )
    checks["windows_volume_headroom"] = {
        "status": "PASS" if c_volumes and volume_results and all(item["status"] == "PASS" for item in volume_results) else "BLOCKED",
        "volumes": volume_results,
        "c_volume_count": len(c_volumes),
        "safety_floor_gib": SAFETY_FLOOR_GIB,
    }

    log = measured.get("log", {})
    log_free = int(log.get("free_bytes", 0) or 0)
    log_reserve = reserve_values.get("transaction_log_reserve_bytes", 0)
    log_used_percent = float(log.get("used_percent", 100.0))
    checks["transaction_log"] = {
        "status": "PASS" if log_free >= log_reserve and log_used_percent <= log_limit else "BLOCKED",
        "free_bytes": log_free,
        "reserve_bytes": log_reserve,
        "used_percent": log_used_percent,
        "max_used_percent": log_limit,
    }

    tempdb_files = list(measured.get("tempdb_files", ()))
    tempdb_internal_free = sum(
        max(0, int(item.get("allocated_bytes", 0) or 0) - int(item.get("used_bytes", 0) or 0))
        for item in tempdb_files
        if str(item.get("type") or "").upper() == "ROWS"
    )
    tempdb_windows_free = min(
        (int(item.get("available_bytes", 0) or 0) for item in tempdb_files),
        default=0,
    )
    tempdb_reserve = reserve_values.get("tempdb_reserve_bytes", 0)
    checks["tempdb"] = {
        "status": "PASS" if tempdb_files and tempdb_internal_free >= tempdb_reserve and tempdb_windows_free >= tempdb_reserve else "BLOCKED",
        "internal_free_bytes": tempdb_internal_free,
        "windows_free_bytes": tempdb_windows_free,
        "reserve_bytes": tempdb_reserve,
    }

    legacy_reserved = sum(int(item.get("reserved_bytes", 0) or 0) for item in measured.get("legacy_allocations", ()))
    legacy_index_reserved = sum(int(item.get("index_reserved_bytes", 0) or 0) for item in measured.get("legacy_allocations", ()))
    ddl_reserve = reserve_values.get("ddl_index_reserve_bytes", 0)
    checks["rollback"] = {
        "status": "PASS" if reserve_values.get("rollback_reserve_bytes", 0) > 0 else "BLOCKED",
        "reserve_bytes": reserve_values.get("rollback_reserve_bytes", 0),
        "active_user_transactions": int(measured.get("active_user_transactions", 0) or 0),
    }
    checks["ddl_and_index"] = {
        "status": "PASS" if ddl_reserve >= legacy_reserved and ddl_reserve >= legacy_index_reserved else "BLOCKED",
        "reserve_bytes": ddl_reserve,
        "legacy_reserved_bytes": legacy_reserved,
        "legacy_index_reserved_bytes": legacy_index_reserved,
    }

    autogrowth_failures = []
    for item in all_files:
        max_pages = int(item.get("max_size_pages", -1) or -1)
        growth = int(item.get("growth", 0) or 0)
        if growth <= 0:
            autogrowth_failures.append({"file": item.get("name"), "reason": "growth is disabled"})
            continue
        if max_pages < 0:
            continue
        max_bytes = max_pages * 8192
        headroom = max(0, max_bytes - int(item.get("allocated_bytes", 0) or 0))
        type_name = str(item.get("type") or "").upper()
        if type_name == "LOG":
            required = log_reserve
        elif item in tempdb_files:
            required = tempdb_reserve
        else:
            required = ddl_reserve + reserve_values.get("rollback_reserve_bytes", 0)
        if headroom < required:
            autogrowth_failures.append(
                {
                    "file": item.get("name"),
                    "headroom_bytes": headroom,
                    "required_bytes": required,
                    "max_size_pages": max_pages,
                }
            )
    checks["potential_autogrowth"] = {
        "status": "PASS" if not autogrowth_failures else "BLOCKED",
        "failures": autogrowth_failures,
        "reserve_bytes": reserve_values.get("autogrowth_reserve_bytes", 0),
    }
    checks["unrelated_external_disk_consumption"] = {
        "status": "PASS" if reserve_values.get("external_disk_consumption_reserve_bytes", 0) > 0 else "BLOCKED",
        "reserve_bytes": reserve_values.get("external_disk_consumption_reserve_bytes", 0),
    }
    checks["active_transactions"] = {
        "status": "PASS" if int(measured.get("active_user_transactions", 0) or 0) == 0 else "BLOCKED",
        "count": int(measured.get("active_user_transactions", 0) or 0),
    }
    status = "PASS" if all(
        isinstance(check, Mapping) and check.get("status") == "PASS"
        for check in checks.values()
    ) else "BLOCKED"
    return {
        "status": status,
        "checks": checks,
        "measured_capacity": measured,
        "reserve_values": reserve_values,
        "safety_floor_gib": SAFETY_FLOOR_GIB,
    }


def _space_delta(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, float]:
    """Report physical MDF and Windows C: deltas from two file snapshots."""

    before_files = {
        str(item.get("name")): item
        for item in before.get("data_files", ())
        if str(item.get("volume", "")).casefold().startswith("c:")
    }
    after_files = {
        str(item.get("name")): item
        for item in after.get("data_files", ())
        if str(item.get("volume", "")).casefold().startswith("c:")
    }
    common_names = sorted(set(before_files) & set(after_files))
    before_allocated = sum(float(before_files[name].get("allocated_gib", 0.0)) for name in common_names)
    after_allocated = sum(float(after_files[name].get("allocated_gib", 0.0)) for name in common_names)
    before_free = min(
        (float(before_files[name].get("windows_free_gib", 0.0)) for name in common_names),
        default=0.0,
    )
    after_free = min(
        (float(after_files[name].get("windows_free_gib", 0.0)) for name in common_names),
        default=0.0,
    )
    return {
        "mdf_allocated_before_gib": before_allocated,
        "mdf_allocated_after_gib": after_allocated,
        "windows_free_before_gib": before_free,
        "windows_free_after_gib": after_free,
        "actual_windows_space_recovered_gib": after_free - before_free,
    }


def _count_gate(cursor: Any) -> dict[str, Any]:
    names = (
        "MddMacSourceSnapshot",
        "MddMacCallObservation",
        "MddMacTimingObservation",
        "MddMacSituationEvidence",
        "MddMacServiceSectionEvidence",
        "MddMacAttributeEvidence",
        "MddMacResponseError",
        "MddMacParserGap",
    )
    old: dict[str, int] = {}
    for name in names:
        cursor.execute(f"SELECT COUNT_BIG(*) AS row_count FROM stg.{name}")
        old[name] = int(_row_value(cursor.fetchone(), "row_count", 0) or 0)
    cursor.execute(
        """
        SELECT COUNT_BIG(*) AS snapshot_count,
               COALESCE(SUM(CallRowCount), 0) AS call_count,
               COALESCE(SUM(TimingRowCount), 0) AS timing_count,
               COALESCE(SUM(SituationRowCount), 0) AS situation_count,
               COALESCE(SUM(ServiceSectionRowCount), 0) AS section_count,
               COALESCE(SUM(AttributeRowCount), 0) AS attribute_count,
               COALESCE(SUM(ResponseErrorRowCount), 0) AS error_count,
               COALESCE(SUM(ParserGapRowCount), 0) AS gap_count
        FROM stg.MddMacCompactObservationEnvelope
        WHERE VerificationStatus = 'VERIFIED'
        """
    )
    row = cursor.fetchone()
    compact = {
        "MddMacSourceSnapshot": int(_row_value(row, "snapshot_count", 0) or 0),
        "MddMacCallObservation": int(_row_value(row, "call_count", 1) or 0),
        "MddMacTimingObservation": int(_row_value(row, "timing_count", 2) or 0),
        "MddMacSituationEvidence": int(_row_value(row, "situation_count", 3) or 0),
        "MddMacServiceSectionEvidence": int(_row_value(row, "section_count", 4) or 0),
        "MddMacAttributeEvidence": int(_row_value(row, "attribute_count", 5) or 0),
        "MddMacResponseError": int(_row_value(row, "error_count", 6) or 0),
        "MddMacParserGap": int(_row_value(row, "gap_count", 7) or 0),
    }
    return {"status": "PASS" if old == compact else "BLOCKED", "legacy": old, "compact": compact}


def _active_writer_gate(cursor: Any) -> dict[str, Any]:
    cursor.execute(
        """
        SELECT COUNT_BIG(*) AS running_count
        FROM ctl.MddMacCollectorRun
        WHERE Status IN ('RUNNING', 'CLAIMED')
          AND CollectionContext IN ('MAC_LEGACY7_PRODUCTION', 'MAC_TIERED50_PRODUCTION')
        """
    )
    count = int(_row_value(cursor.fetchone(), "running_count", 0) or 0)
    cursor.execute(
        """
        SELECT COUNT_BIG(*) AS active_lease_count
        FROM ctl.MddMacProductionLease
        WHERE LeaseName = 'MDD_MAC_PRODUCTION' AND Status = 'ACTIVE'
        """
    )
    leases = int(_row_value(cursor.fetchone(), "active_lease_count", 0) or 0)
    return {"status": "PASS" if count == 0 and leases == 0 else "BLOCKED", "running_count": count, "active_lease_count": leases}


def _migration_gate(cursor: Any, migration_id: str) -> dict[str, Any]:
    cursor.execute(
        """
        SELECT Status, VerifiedUnitCount, QuarantinedUnitCount
        FROM stg.MddMacCompactMigration
        WHERE MigrationId = %s
        """,
        (migration_id,),
    )
    row = cursor.fetchone()
    status = str(_row_value(row, "Status", 0) or "") if row is not None else "MISSING"
    verified = int(_row_value(row, "VerifiedUnitCount", 1) or 0) if row is not None else 0
    quarantined = int(_row_value(row, "QuarantinedUnitCount", 2) or 0) if row is not None else 0
    return {
        "status": "PASS" if status == "COMPLETE" and quarantined == 0 else "BLOCKED",
        "migration_id": migration_id,
        "migration_status": status,
        "verified_unit_count": verified,
        "quarantined_unit_count": quarantined,
    }


def _execute_retirement(connection: Any, reserve_values: Mapping[str, int]) -> int:
    text = RETIREMENT_SQL.read_text(encoding="utf-8")
    text = text.replace("DECLARE @ApplyLegacyRetirement BIT = 0;", "DECLARE @ApplyLegacyRetirement BIT = 1;", 1)
    sql_reserve_names = {
        "transaction_log_reserve_bytes": "@RetirementTransactionLogReserveBytes",
        "tempdb_reserve_bytes": "@RetirementTempdbReserveBytes",
        "rollback_reserve_bytes": "@RetirementRollbackReserveBytes",
        "ddl_index_reserve_bytes": "@RetirementDdlIndexReserveBytes",
        "autogrowth_reserve_bytes": "@RetirementAutogrowthReserveBytes",
        "external_disk_consumption_reserve_bytes": "@RetirementExternalDiskReserveBytes",
    }
    for field, sql_name in sql_reserve_names.items():
        value = reserve_values.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise RuntimeError(f"retirement reserve is missing or invalid: {field}")
        text = text.replace(f"DECLARE {sql_name} BIGINT = NULL;", f"DECLARE {sql_name} BIGINT = {value};", 1)
    batches = split_batches(text)
    cursor = connection.cursor()
    cursor.execute("SET XACT_ABORT ON;")
    executed = 0
    try:
        for batch in batches:
            cursor.execute(batch)
            _drain(cursor)
            executed += 1
        connection.commit()
        return executed
    except BaseException:
        connection.rollback()
        raise


def _shrink_one_file(cursor: Any, *, headroom_gib: float, max_shrink_gib: float) -> dict[str, Any]:
    before = _file_capacity(cursor, require_legacy_tables=False)
    if len(before["data_files"]) != 1:
        raise RuntimeError("targeted shrink requires exactly one SQL data file")
    file = before["data_files"][0]
    if not file["volume"].casefold().startswith("c:"):
        raise RuntimeError("targeted shrink is limited to the Windows C: data volume")
    if headroom_gib <= 0 or max_shrink_gib < 0:
        raise RuntimeError("shrink headroom and bound must be non-negative, with positive headroom")
    free_gib = file["windows_free_gib"]
    if free_gib < SAFETY_FLOOR_GIB:
        raise RuntimeError("Windows C: free space is below the conservative 12 GiB shrink floor")
    target_pages = file["space_used_pages"] + int((headroom_gib * (1024**3) + 8191) // 8192)
    target_mb = int((target_pages * 8192 + (1024**2 - 1)) // (1024**2))
    current_mb = int((file["size_pages"] * 8192 + (1024**2 - 1)) // (1024**2))
    shrink_gib = max(0.0, file["allocated_gib"] - target_mb / 1024)
    if shrink_gib > max_shrink_gib:
        raise RuntimeError("computed shrink exceeds the bounded --max-shrink-gib limit")
    if target_mb >= current_mb:
        return {"status": "NOT_NEEDED", "before": before, "target_mb": target_mb}
    safe_name = str(file["name"]).replace("]", "]]" )
    cursor.execute(f"DBCC SHRINKFILE ([{safe_name}], {target_mb}) WITH NO_INFOMSGS")
    _drain(cursor)
    after = _file_capacity(cursor, require_legacy_tables=False)
    after_file = after["data_files"][0]
    if after_file["windows_free_gib"] < SAFETY_FLOOR_GIB:
        raise RuntimeError("Windows C: free space fell below the 12 GiB shrink floor")
    return {"status": "PASS", "before": before, "after": after, "target_mb": target_mb, "requested_shrink_gib": shrink_gib}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-evidence", type=Path, required=True)
    parser.add_argument("--parity-evidence", type=Path, required=True)
    parser.add_argument(
        "--approval-evidence",
        type=Path,
        help="one explicit human approval artifact for irreversible legacy retirement",
    )
    parser.add_argument(
        "--powerbi-evidence",
        type=Path,
        help="fresh evidence captured from the real Power BI consumer",
    )
    parser.add_argument(
        "--max-log-used-percent",
        type=float,
        default=RETIREMENT_MAX_LOG_USED_PERCENT,
        help="maximum current transaction-log utilization allowed before retirement",
    )
    parser.add_argument("--migration-id", required=True)
    parser.add_argument("--apply", action="store_true", help="execute the guarded retirement transaction")
    parser.add_argument("--shrink", action="store_true", help="after retirement, run one bounded DBCC SHRINKFILE")
    parser.add_argument("--shrink-headroom-gib", type=float, default=1.0)
    parser.add_argument("--max-shrink-gib", type=float, default=8.0)
    args = parser.parse_args(argv)
    if args.shrink and not args.apply:
        parser.error("--shrink requires --apply")
    result: dict[str, Any] = {
        "operation": "legacy_storage_retirement_and_reclamation",
        "status": "BLOCKED",
        "collector_gate": _collector_gate(),
        "storage_operation_gate": local_storage_operation_gate(),
        "apply": args.apply,
        "shrink_requested": args.shrink,
    }
    try:
        approval_data: Mapping[str, Any] | None = None
        result["backup_gate"] = _backup_gate(args.backup_evidence)
        result["parity_gate"] = _parity_gate(args.parity_evidence, require_full_history=args.apply)
        if args.approval_evidence is not None:
            result["approval_gate"] = _approval_gate(args.approval_evidence)
            approval_data = _load_json(args.approval_evidence)
        if args.powerbi_evidence is not None:
            result["powerbi_gate"] = _powerbi_gate(args.powerbi_evidence)
        if result["collector_gate"].get("status") != "PASS":
            raise RuntimeError("collector stopped/disabled gate is not proven")
        if result["storage_operation_gate"].get("status") != "PASS":
            raise RuntimeError("another compact-storage operation is active")
        if result["backup_gate"]["status"] != "PASS":
            raise RuntimeError("fresh checksum-verified off-drive backup gate is not proven")
        if result["parity_gate"]["status"] != "PASS":
            raise RuntimeError("complete exact compact readback parity is not proven")
        if args.apply:
            if approval_data is None or result.get("approval_gate", {}).get("status") != "PASS":
                raise RuntimeError("explicit human retirement approval is required before --apply")
            if result.get("powerbi_gate", {}).get("status") != "PASS":
                raise RuntimeError("fresh real Power BI consumer compatibility approval is required before --apply")
        settings, role_source = resolve_administrator_connection_settings(
            os.environ,
            secret_provider=MacOSKeychainSecretProvider(),
        )
        connection = connect_mssql(settings)
        try:
            cursor = connection.cursor()
            result["admin_role_source"] = role_source
            result["compatibility_dependency_gate"] = _compatibility_dependency_gate(cursor)
            result["migration_gate"] = _migration_gate(cursor, args.migration_id)
            result["counts_before"] = _count_gate(cursor)
            result["active_writer_gate"] = _active_writer_gate(cursor)
            result["files_before"] = _file_capacity(cursor)
            if args.apply:
                result["retirement_capacity_preflight"] = _retirement_capacity_preflight(
                    cursor,
                    approval_data or {},
                    capacity=result["files_before"],
                    log_limit=args.max_log_used_percent,
                )
            if result["counts_before"]["status"] != "PASS":
                raise RuntimeError("legacy and verified compact counts do not reconcile")
            if result["migration_gate"]["status"] != "PASS":
                raise RuntimeError("the requested compact migration is not COMPLETE")
            if result["active_writer_gate"]["status"] != "PASS":
                raise RuntimeError("active writer or production lease is present")
            if result["compatibility_dependency_gate"]["status"] != "PASS":
                raise RuntimeError("legacy compatibility dependencies or schema contract are not proven safe")
            if not args.apply:
                result["status"] = "PASS"
            else:
                capacity_preflight = result["retirement_capacity_preflight"]
                if capacity_preflight.get("status") != "PASS":
                    raise RuntimeError("live physical retirement capacity preflight failed")
                result["retirement"] = {
                    "status": "PASS",
                    "executed_batches": _execute_retirement(
                        connection,
                        capacity_preflight["reserve_values"],
                    ),
                }
                result["counts_after_retirement"] = _count_gate(cursor)
                if result["counts_after_retirement"]["status"] != "PASS":
                    raise RuntimeError("post-retirement compatibility-view counts do not reconcile")
                result["files_after_retirement"] = _file_capacity(cursor, require_legacy_tables=False)
                result["space_delta_after_retirement"] = _space_delta(
                    result["files_before"], result["files_after_retirement"]
                )
                result["physical_recovery"] = {
                    "status": "MEASURED" if result["space_delta_after_retirement"]["actual_windows_space_recovered_gib"] > 0 else "NO_PHYSICAL_RECOVERY_MEASURED",
                    "actual_windows_space_recovered_gib": result["space_delta_after_retirement"]["actual_windows_space_recovered_gib"],
                    "note": "retirement is not reported as solving disk pressure without a positive measured Windows free-space delta",
                }
                if args.shrink:
                    result["shrink_capacity_preflight"] = _retirement_capacity_preflight(
                        cursor,
                        approval_data or {},
                        capacity=result["files_after_retirement"],
                        log_limit=args.max_log_used_percent,
                    )
                    if result["shrink_capacity_preflight"].get("status") != "PASS":
                        raise RuntimeError("live physical shrink capacity preflight failed")
                    result["shrink"] = _shrink_one_file(
                        cursor,
                        headroom_gib=max(0.25, args.shrink_headroom_gib),
                        max_shrink_gib=max(0.0, args.max_shrink_gib),
                    )
                    connection.commit()
                    result["files_after_shrink"] = _file_capacity(cursor, require_legacy_tables=False)
                    result["space_delta_after_shrink"] = _space_delta(
                        result["files_before"], result["files_after_shrink"]
                    )
                    result["physical_recovery"] = {
                        "status": "MEASURED" if result["space_delta_after_shrink"]["actual_windows_space_recovered_gib"] > 0 else "NO_PHYSICAL_RECOVERY_MEASURED",
                        "actual_windows_space_recovered_gib": result["space_delta_after_shrink"]["actual_windows_space_recovered_gib"],
                        "note": "retirement is not reported as solving disk pressure without a positive measured Windows free-space delta",
                    }
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

