#!/usr/bin/env python3
"""Migrate SQL Server observation history into verified compact envelopes.

The runner is deliberately fail-closed:

* without ``--apply`` it performs only bounded reads and prints a plan;
* it never starts or enables either collector;
* every SnapshotId is read, encoded, stored, decoded from SQL Server, and
  compared field-by-field before the unit is marked VERIFIED;
* one SQL transaction is used per unit; and
* a failed parity check is quarantined and never authorizes legacy cleanup.

The runner does not retire old tables or shrink files.  Those operations use
the separate recovery-gated reclamation runner after a fresh verified backup.
"""

from __future__ import annotations

import argparse
import collections
import gzip
import json
import os
import re
import sys
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from cologne_transit_collector.compact_observation import (  # noqa: E402
    CompactEnvelopeError,
    build_compact_envelope,
    compare_row_family,
    decode_compact_envelope,
    encode_compact_envelope,
    local_collector_safety_gate,
    local_storage_operation_gate,
    normalize_sql_row,
)
from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlSafetyError,
    connect_mssql,
)
from cologne_transit_collector.runtime_configuration import (  # noqa: E402
    MacOSKeychainSecretProvider,
    resolve_administrator_connection_settings,
)
from reclaim_sql_datafile_space import _backup_gate  # noqa: E402


FAMILIES: tuple[tuple[str, str], ...] = (
    ("calls", "stg.MddMacCallObservation"),
    ("timings", "stg.MddMacTimingObservation"),
    ("situations", "stg.MddMacSituationEvidence"),
    ("service_sections", "stg.MddMacServiceSectionEvidence"),
    ("attributes", "stg.MddMacAttributeEvidence"),
    ("response_errors", "stg.MddMacResponseError"),
    ("parser_gaps", "stg.MddMacParserGap"),
)
SAFETY_FLOOR_GIB = 12.0
DEFAULT_MAX_LOG_USED_PERCENT = 70.0
CAPACITY_PLAN_SCOPE = "FULL_HISTORY_MIGRATION_PEAK"
CAPACITY_PLAN_FIELDS = (
    "sample_snapshots",
    "envelope_allocation_bytes",
    "index_overhead_bytes",
    "transaction_log_bytes",
    "tempdb_bytes",
    "rollback_bytes",
    "peak_temporary_duplication_bytes",
)

COMPATIBILITY_VIEWS: tuple[tuple[str, str], ...] = (
    ("source_snapshot", "stg.vMddMacCompactSourceSnapshot"),
    ("calls", "stg.vMddMacCompactCallObservation"),
    ("timings", "stg.vMddMacCompactTimingObservation"),
    ("situations", "stg.vMddMacCompactSituationEvidence"),
    ("service_sections", "stg.vMddMacCompactServiceSectionEvidence"),
    ("attributes", "stg.vMddMacCompactAttributeEvidence"),
    ("response_errors", "stg.vMddMacCompactResponseError"),
    ("parser_gaps", "stg.vMddMacCompactParserGap"),
)
SOURCE_TABLES: tuple[tuple[str, str], ...] = (
    ("source_snapshot", "stg.MddMacSourceSnapshot"),
    *FAMILIES,
)
PHYSICAL_FIELD_READBACK_VIEW = "stg.vMddMacCompactPhysicalFieldReadback"
READBACK_SESSION_CONTEXT_KEY = "CTI.CompactStorageReadback"


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    if isinstance(row, Mapping):
        lowered = {str(k).casefold(): v for k, v in row.items()}
        return lowered.get(key.casefold())
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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _launchd_gate() -> dict[str, Any]:
    """Read collector state without changing it."""
    return local_collector_safety_gate()


def _read_capacity(cursor: Any) -> dict[str, Any]:
    cursor.execute(
        """
        SELECT DISTINCT vs.volume_mount_point, vs.total_bytes, vs.available_bytes
        FROM sys.database_files AS df
        CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
        ORDER BY vs.volume_mount_point
        """
    )
    volumes = []
    for row in cursor.fetchall():
        volume = _row_value(row, "volume_mount_point", 0)
        total_bytes = _row_value(row, "total_bytes", 1)
        available_bytes = _row_value(row, "available_bytes", 2)
        if any(value is None for value in (volume, total_bytes, available_bytes)):
            raise MssqlSafetyError("SQL volume-capacity measurement is incomplete")
        volumes.append(
            {
                "volume": str(volume),
                "total_bytes": int(total_bytes),
                "available_bytes": int(available_bytes),
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
    log_row = cursor.fetchone()
    if log_row is None or any(
        _row_value(log_row, name, index) is None
        for index, name in enumerate(
            ("total_log_size_in_bytes", "used_log_space_in_bytes", "used_log_space_in_percent")
        )
    ):
        raise MssqlSafetyError("SQL log-space measurement is incomplete")
    log = {
        "total_bytes": int(_row_value(log_row, "total_log_size_in_bytes", 0) or 0),
        "used_bytes": int(_row_value(log_row, "used_log_space_in_bytes", 1) or 0),
        "used_percent": float(_row_value(log_row, "used_log_space_in_percent", 2) or 0),
    }
    cursor.execute(
        """
        SELECT file_id, name, type_desc,
               CONVERT(BIGINT, size) * CONVERT(BIGINT, 8192) AS allocated_bytes,
               CONVERT(BIGINT, FILEPROPERTY(name, 'SpaceUsed'))
                   * CONVERT(BIGINT, 8192) AS used_bytes
        FROM sys.database_files
        ORDER BY file_id
        """
    )
    files = []
    for row in cursor.fetchall():
        values = (
            _row_value(row, "file_id", 0),
            _row_value(row, "name", 1),
            _row_value(row, "type_desc", 2),
            _row_value(row, "allocated_bytes", 3),
            _row_value(row, "used_bytes", 4),
        )
        if any(value is None for value in values):
            raise MssqlSafetyError("SQL data-file allocation measurement is incomplete")
        files.append(
            {
                "file_id": int(values[0]),
                "name": str(values[1]),
                "type": str(values[2]),
                "allocated_bytes": int(values[3]),
                "used_bytes": int(values[4]),
            }
        )
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
        raise MssqlSafetyError("SQL active-transaction measurement is incomplete")
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
        raise MssqlSafetyError("SQL active-collector-session measurement is incomplete")
    active_collector_sessions = int(
        _row_value(collector_session_row, "active_collector_sessions", 0) or 0
    )
    return {
        "volumes": volumes,
        "log": log,
        "files": files,
        "active_user_transactions": active_user_transactions,
        "active_collector_sessions": active_collector_sessions,
    }


def _capacity_gate(
    capacity: Mapping[str, Any],
    *,
    log_limit: float,
    required_extra_bytes: int = 0,
) -> dict[str, Any]:
    volumes = list(capacity.get("volumes", ()))
    c_volumes = [
        item for item in volumes
        if str(item.get("volume", "")).casefold().startswith("c:")
    ]
    rows_volume = min(
        c_volumes,
        key=lambda item: int(item.get("available_bytes", 0) or 0),
        default=None,
    )
    available = int(rows_volume.get("available_bytes", 0)) if rows_volume else 0
    if isinstance(required_extra_bytes, bool) or not isinstance(required_extra_bytes, int):
        raise ValueError("required_extra_bytes must be an integer")
    if required_extra_bytes < 0:
        raise ValueError("required_extra_bytes cannot be negative")
    projected_free_gib = (available - required_extra_bytes) / (1024**3)
    free_gib = available / (1024**3)
    log_used = float((capacity.get("log") or {}).get("used_percent", 100.0))
    active_user_transactions = int(capacity.get("active_user_transactions", 0) or 0)
    active_collector_sessions = int(capacity.get("active_collector_sessions", 0) or 0)
    return {
        "status": "PASS"
        if rows_volume
        and projected_free_gib >= SAFETY_FLOOR_GIB
        and log_used <= log_limit
        and active_user_transactions == 0
        and active_collector_sessions == 0
        else "BLOCKED",
        "windows_free_gib": round(free_gib, 3),
        "required_extra_bytes": required_extra_bytes,
        "peak_migration_space_gib": round(required_extra_bytes / (1024**3), 6),
        "peak_capacity_plan_exact": required_extra_bytes > 0,
        "projected_windows_free_gib": round(projected_free_gib, 3),
        "required_windows_free_gib": SAFETY_FLOOR_GIB,
        "log_used_percent": log_used,
        "max_log_used_percent": log_limit,
        "active_user_transactions": active_user_transactions,
        "active_collector_sessions": active_collector_sessions,
        "volume": rows_volume,
        "c_volumes": c_volumes,
    }


def _strict_nonnegative_integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"capacity plan field {name} must be a non-negative integer")
    return value


def _validate_capacity_plan(value: Mapping[str, Any]) -> dict[str, Any]:
    missing = [name for name in CAPACITY_PLAN_FIELDS if name not in value]
    for required_name in (
        "planned_snapshot_count",
        "measurement_basis",
        "physical_allocation_measured",
        "allocation_scope",
        "peak_additional_bytes",
    ):
        if required_name not in value:
            missing.append(required_name)
    if missing:
        raise ValueError("capacity plan is missing: " + ", ".join(missing))
    sample_snapshots = _strict_nonnegative_integer(value["sample_snapshots"], "sample_snapshots")
    planned_snapshot_count = _strict_nonnegative_integer(value["planned_snapshot_count"], "planned_snapshot_count")
    if sample_snapshots <= 0 or planned_snapshot_count <= 0 or sample_snapshots > planned_snapshot_count:
        raise ValueError("capacity plan snapshot counts are invalid")
    components = {
        name: _strict_nonnegative_integer(value[name], name)
        for name in CAPACITY_PLAN_FIELDS
        if name != "sample_snapshots"
    }
    measurement_basis = str(value.get("measurement_basis") or "").strip()
    if (
        "sql" not in measurement_basis.casefold()
        or "physical" not in measurement_basis.casefold()
        or "allocation" not in measurement_basis.casefold()
    ):
        raise ValueError("capacity plan measurement_basis must identify measured SQL physical allocation")
    if value.get("physical_allocation_measured") is not True:
        raise ValueError("capacity plan must set physical_allocation_measured=true")
    allocation_scope = str(value.get("allocation_scope") or "").strip()
    if allocation_scope != CAPACITY_PLAN_SCOPE:
        raise ValueError(
            "capacity plan must explicitly cover the full-history migration peak"
        )
    peak = sum(components.values())
    declared_peak = _strict_nonnegative_integer(value["peak_additional_bytes"], "peak_additional_bytes")
    if declared_peak != peak:
        raise ValueError("capacity plan peak_additional_bytes does not equal its measured components")
    if declared_peak <= 0:
        raise ValueError("capacity plan peak_additional_bytes must be positive")
    return {
        "status": "PASS",
        "measurement_basis": str(value.get("measurement_basis", "")),
        "allocation_scope": allocation_scope,
        "sample_snapshots": sample_snapshots,
        "planned_snapshot_count": planned_snapshot_count,
        "physical_allocation_measured": True,
        **components,
        "peak_additional_bytes": peak,
    }


def _load_capacity_plan(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("capacity plan must be a JSON object")
    return _validate_capacity_plan(value)


def _dict_row(row: Any, cursor: Any) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    descriptions = getattr(cursor, "description", None) or ()
    names = [str(item[0]) for item in descriptions]
    return {name: row[index] for index, name in enumerate(names)}


def _read_unit(cursor: Any, snapshot_id: int) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    cursor.execute("SELECT * FROM stg.MddMacSourceSnapshot WHERE SnapshotId = %s", (snapshot_id,))
    raw_snapshot = cursor.fetchone()
    if raw_snapshot is None:
        raise MssqlSafetyError(f"SnapshotId {snapshot_id} does not exist")
    snapshot_row = _dict_row(raw_snapshot, cursor)
    rows: dict[str, list[dict[str, Any]]] = {}
    for family, table in FAMILIES:
        cursor.execute(f"SELECT * FROM {table} WHERE SnapshotId = %s", (snapshot_id,))
        rows[family] = [_dict_row(item, cursor) for item in cursor.fetchall()]
    return snapshot_row, rows


def _normalized_unit(
    snapshot_row: Mapping[str, Any],
    rows: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    snapshot = normalize_sql_row(snapshot_row, family="source_snapshot")
    normalized = {
        family: [normalize_sql_row(row, family=family) for row in values]
        for family, values in rows.items()
    }
    return snapshot, normalized


def _metadata_value(row: Mapping[str, Any], name: str) -> Any:
    for key, value in row.items():
        if str(key).casefold() == name.casefold():
            return value
    return None


def _make_migration(cursor: Any, migration_id: uuid.UUID, *, apply: bool) -> None:
    if not apply:
        return
    cursor.execute(
        """
        IF EXISTS
        (
            SELECT 1 FROM stg.MddMacCompactMigration
            WHERE MigrationId = %s AND Status IN ('BLOCKED', 'FAILED')
        )
            UPDATE stg.MddMacCompactMigration
            SET Status = 'RUNNING', CompletedAtUtc = NULL,
                FailureCode = NULL, FailureMessage = NULL
            WHERE MigrationId = %s;
        IF NOT EXISTS (SELECT 1 FROM stg.MddMacCompactMigration WHERE MigrationId = %s)
        INSERT INTO stg.MddMacCompactMigration
            (MigrationId, StorageFormatVersion, MigrationKind, Status)
        VALUES (%s, 1, 'HISTORICAL', 'RUNNING')
        """,
        (
            str(migration_id), str(migration_id), str(migration_id), str(migration_id),
        ),
    )


def _set_migration_status(
    cursor: Any,
    migration_id: uuid.UUID,
    status: str,
    message: str | None = None,
) -> None:
    if status not in {"BLOCKED", "FAILED", "COMPLETE"}:
        raise ValueError("invalid compact migration terminal status")
    cursor.execute(
        """
        UPDATE stg.MddMacCompactMigration
        SET Status = %s,
            CompletedAtUtc = CASE WHEN %s = 'COMPLETE' THEN SYSUTCDATETIME() ELSE NULL END,
            FailureCode = CASE WHEN %s = 'COMPLETE' THEN NULL ELSE 'PYTHON_RUNNER_BLOCKED' END,
            FailureMessage = CASE WHEN %s = 'COMPLETE' THEN NULL ELSE %s END
        WHERE MigrationId = %s
        """,
        (status, status, status, status, message, str(migration_id)),
    )


def _store_unit(
    cursor: Any,
    *,
    migration_id: uuid.UUID,
    snapshot_id: int,
    source_row: Mapping[str, Any],
    normalized_snapshot: Mapping[str, Any],
    encoded: Any,
    counts: Mapping[str, int],
) -> str:
    cursor.execute(
        """
        EXEC stg.uspMddMacCompactStoreHistoricalEnvelope
            @MigrationId=%s,
            @SnapshotId=%s,
            @EnvelopeJson=%s,
            @EnvelopeSha256=%s,
            @RunId=%s,
            @AuditId=%s,
            @TargetIdentity=%s,
            @SamplingTargetId=%s,
            @LogicalDueBucket=%s,
            @ObservedAtUtc=%s,
            @ResponseTimestampSourceText=%s,
            @ParserVersion=%s,
            @ApplicationStatus=%s,
            @PayloadHashSha256=%s,
            @SourceSnapshotRowCount=%s,
            @CallRowCount=%s,
            @TimingRowCount=%s,
            @SituationRowCount=%s,
            @ServiceSectionRowCount=%s,
            @AttributeRowCount=%s,
            @ResponseErrorRowCount=%s,
            @ParserGapRowCount=%s
        """,
        (
            str(migration_id), snapshot_id, encoded.canonical_json, encoded.sha256,
            _metadata_value(source_row, "RunId"), _metadata_value(source_row, "AuditId"),
            _metadata_value(source_row, "TargetIdentity"), _metadata_value(source_row, "SamplingTargetId"),
            _metadata_value(source_row, "LogicalDueBucket"), _metadata_value(source_row, "ObservedAtUtc"),
            _metadata_value(source_row, "ResponseTimestampSourceText"), _metadata_value(source_row, "ParserVersion"),
            _metadata_value(source_row, "ApplicationStatus"), _metadata_value(source_row, "PayloadHashSha256"),
            counts["source_snapshot"], counts["calls"], counts["timings"], counts["situations"],
            counts["service_sections"], counts["attributes"], counts["response_errors"], counts["parser_gaps"],
        ),
    )
    outcome = "STORED"
    try:
        result_rows = cursor.fetchall()
        if result_rows:
            outcome = str(_row_value(result_rows[0], "Outcome", 1) or outcome)
    except Exception:
        pass
    return outcome


def _read_stored_envelope(cursor: Any, snapshot_id: int) -> tuple[bytes, str]:
    cursor.execute(
        """
        SELECT EnvelopeJsonGzip, EnvelopeSha256
        FROM stg.MddMacCompactObservationEnvelope
        WHERE SnapshotId = %s
        """,
        (snapshot_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise MssqlSafetyError(f"stored compact envelope is missing for SnapshotId {snapshot_id}")
    payload = _row_value(row, "EnvelopeJsonGzip", 0)
    if isinstance(payload, memoryview):
        payload = payload.tobytes()
    return bytes(payload), str(_row_value(row, "EnvelopeSha256", 1))


def _set_readback_context(cursor: Any, enabled: bool) -> None:
    """Expose BUILT rows only to this bounded verifier session.

    Public compact compatibility views remain VERIFIED-only.  The migration
    must nevertheless inspect the real views before promotion, so the SQL
    views accept this short-lived, explicit readback context for BUILT rows.
    The context is session-local and never changes database state.
    """

    cursor.execute(
        "EXEC sys.sp_set_session_context @key=%s, @value=%s",
        (READBACK_SESSION_CONTEXT_KEY, 1 if enabled else 0),
    )
    try:
        cursor.fetchall()
    except Exception:
        pass


def _description_names(cursor: Any) -> set[str]:
    description = getattr(cursor, "description", None) or ()
    return {str(item[0]) for item in description if item}


def _logical_view_row(row: Mapping[str, Any], *, family: str) -> dict[str, Any]:
    """Build the separately validated decoded contract for a view row."""

    normalized = normalize_sql_row(row, family=family)
    for key, value in tuple(normalized.items()):
        if not key.endswith("_gzip_hex") or value is None:
            continue
        logical_key = key[: -len("_gzip_hex")]
        if normalized.get(logical_key) is not None:
            continue
        try:
            normalized[logical_key] = gzip.decompress(bytes.fromhex(str(value))).decode("utf-16le")
        except (ValueError, UnicodeDecodeError, OSError) as exc:
            raise MssqlSafetyError(
                f"SQL compatibility view returned an invalid compressed value for {family}.{key}"
            ) from exc
    return normalized


def _json_field_type(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return 3
    if isinstance(value, (int, float)):
        return 2
    if isinstance(value, list):
        return 4
    if isinstance(value, Mapping):
        return 5
    return 1


def _raw_json_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, Mapping)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(value)


def _expected_physical_field_records(
    snapshot: Mapping[str, Any],
    rows: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[tuple[Any, ...]]:
    records: list[tuple[Any, ...]] = []
    families: list[tuple[str, Sequence[Mapping[str, Any]]]] = [("source_snapshot", [snapshot])]
    families.extend((family, rows[family]) for family, _table in FAMILIES)
    for family, family_rows in families:
        for row_ordinal, row in enumerate(family_rows):
            for field_name, value in row.items():
                raw_binary = None
                if field_name.endswith("_gzip_hex") and value is not None:
                    try:
                        raw_binary = bytes.fromhex(str(value))
                    except ValueError as exc:
                        raise MssqlSafetyError(
                            f"source row contains invalid hexadecimal binary field {family}.{field_name}"
                        ) from exc
                records.append(
                    (
                        family,
                        row_ordinal,
                        field_name,
                        _json_field_type(value),
                        _raw_json_value(value),
                        raw_binary.hex() if raw_binary is not None else None,
                    )
                )
    return records


def _read_compatibility_view_parity(
    cursor: Any,
    *,
    snapshot: Mapping[str, Any],
    rows: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    expected_by_family: dict[str, Sequence[Mapping[str, Any]]] = {
        "source_snapshot": [snapshot],
        **{family: values for family, values in rows.items()},
    }
    family_results: list[dict[str, Any]] = []
    for family, view in COMPATIBILITY_VIEWS:
        cursor.execute(f"SELECT * FROM {view} WHERE SnapshotId = %s", (snapshot["snapshot_id"],))
        actual_description_names = _description_names(cursor)
        actual_rows = [_dict_row(item, cursor) for item in cursor.fetchall()]
        actual_columns = {
            key
            for name in actual_description_names
            for key in normalize_sql_row({name: None}, family=family)
        }
        source_table = dict(SOURCE_TABLES)[family]
        cursor.execute(f"SELECT TOP (0) * FROM {source_table}")
        source_description_names = _description_names(cursor)
        if not source_description_names:
            raise MssqlSafetyError(f"SQL source-table schema measurement is incomplete for {family}")
        expected_columns = {
            key
            for name in source_description_names
            for key in normalize_sql_row({name: None}, family=family)
        }
        expected_rows = expected_by_family[family]
        column_status = "PASS" if actual_columns == expected_columns else "FAIL"
        decoded_expected = [_logical_view_row(item, family=family) for item in expected_rows]
        decoded_actual = [_logical_view_row(item, family=family) for item in actual_rows]
        comparison = compare_row_family(family, decoded_expected, decoded_actual)
        family_results.append(
            {
                "family": family,
                "view": view,
                "column_status": column_status,
                "expected_columns": sorted(expected_columns),
                "actual_columns": sorted(actual_columns),
                "comparison_status": "PASS" if comparison.passed else "FAIL",
                "expected_count": comparison.expected_count,
                "actual_count": comparison.actual_count,
                "expected_digest": comparison.expected_digest,
                "actual_digest": comparison.actual_digest,
                "missing_rows": list(comparison.missing_rows),
                "unexpected_rows": list(comparison.unexpected_rows),
                "changed_rows": [
                    {
                        "identity": change.get("identity"),
                        "expected_count": len(change.get("expected", ())),
                        "actual_count": len(change.get("actual", ())),
                    }
                    for change in comparison.changed_rows
                ],
            }
        )
    return {
        "status": "PASS"
        if all(
            item["column_status"] == "PASS" and item["comparison_status"] == "PASS"
            for item in family_results
        )
        else "FAIL",
        "families": family_results,
    }


def _read_physical_field_parity(
    cursor: Any,
    *,
    snapshot: Mapping[str, Any],
    rows: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    cursor.execute(
        f"SELECT SnapshotId, FamilyName, RowOrdinal, FieldName, FieldType, RawJsonValue, RawBinaryValue "
        f"FROM {PHYSICAL_FIELD_READBACK_VIEW} WHERE SnapshotId = %s",
        (snapshot["snapshot_id"],),
    )
    actual: list[tuple[Any, ...]] = []
    for item in cursor.fetchall():
        row = _dict_row(item, cursor)
        binary = _row_value(row, "RawBinaryValue", 6)
        if isinstance(binary, memoryview):
            binary = binary.tobytes()
        if isinstance(binary, (bytes, bytearray)):
            binary = bytes(binary).hex()
        actual.append(
            (
                str(_row_value(row, "FamilyName", 1)),
                int(_row_value(row, "RowOrdinal", 2)),
                str(_row_value(row, "FieldName", 3)),
                int(_row_value(row, "FieldType", 4)),
                _row_value(row, "RawJsonValue", 5),
                binary,
            )
        )
    expected = _expected_physical_field_records(snapshot, rows)
    expected_counter = collections.Counter(expected)
    actual_counter = collections.Counter(actual)
    missing = list((expected_counter - actual_counter).elements())[:20]
    unexpected = list((actual_counter - expected_counter).elements())[:20]
    return {
        "status": "PASS" if not missing and not unexpected else "FAIL",
        "expected_field_count": len(expected),
        "actual_field_count": len(actual),
        "missing_fields": [list(item) for item in missing],
        "unexpected_fields": [list(item) for item in unexpected],
    }


def _sql_readback_parity(
    cursor: Any,
    *,
    snapshot: Mapping[str, Any],
    rows: Mapping[str, Sequence[Mapping[str, Any]]],
    stored_envelope_parity: Mapping[str, Any],
) -> dict[str, Any]:
    _set_readback_context(cursor, True)
    try:
        compatibility = _read_compatibility_view_parity(
            cursor,
            snapshot=snapshot,
            rows=rows,
        )
        physical = _read_physical_field_parity(
            cursor,
            snapshot=snapshot,
            rows=rows,
        )
    finally:
        _set_readback_context(cursor, False)
    return {
        "status": "PASS"
        if stored_envelope_parity.get("status") == "PASS"
        and compatibility.get("status") == "PASS"
        and physical.get("status") == "PASS"
        else "FAIL",
        "stored_envelope": dict(stored_envelope_parity),
        "compatibility_views": compatibility,
        "physical_sql_fields": physical,
    }


def _mark_unit(cursor: Any, migration_id: uuid.UUID, snapshot_id: int, status: str, error: str | None = None) -> None:
    cursor.execute(
        """
        EXEC stg.uspMddMacCompactMarkUnit
            @MigrationId=%s, @SnapshotId=%s, @Status=%s,
            @FailureCode=%s, @FailureMessage=%s
        """,
        (str(migration_id), snapshot_id, status, "FIELD_PARITY_MISMATCH" if error else None, error),
    )


def _unit_parity(
    expected_snapshot: Mapping[str, Any],
    expected_rows: Mapping[str, Sequence[Mapping[str, Any]]],
    actual_envelope: Mapping[str, Any],
) -> dict[str, Any]:
    results = [
        compare_row_family("source_snapshot", [expected_snapshot], [actual_envelope["source_snapshot"]]),
    ]
    results.extend(
        compare_row_family(family, expected_rows[family], actual_envelope[family])
        for family, _table in FAMILIES
    )
    return {
        "status": "PASS" if all(item.passed for item in results) else "FAIL",
        "families": [
            {
                "family": item.family,
                "expected_count": item.expected_count,
                "actual_count": item.actual_count,
                "expected_digest": item.expected_digest,
                "actual_digest": item.actual_digest,
                "missing_rows": list(item.missing_rows),
                "unexpected_rows": list(item.unexpected_rows),
                "changed_rows": [
                    {
                        "identity": change.get("identity"),
                        "expected_count": len(change.get("expected", ())),
                        "actual_count": len(change.get("actual", ())),
                    }
                    for change in item.changed_rows
                ],
                "status": "PASS" if item.passed else "FAIL",
            }
            for item in results
        ],
    }


def _snapshot_ids(cursor: Any, args: argparse.Namespace) -> list[int]:
    predicates = []
    params: list[Any] = []
    if args.from_snapshot_id is not None:
        predicates.append("SnapshotId >= %s")
        params.append(args.from_snapshot_id)
    if args.to_snapshot_id is not None:
        predicates.append("SnapshotId <= %s")
        params.append(args.to_snapshot_id)
    where = " WHERE " + " AND ".join(predicates) if predicates else ""
    cursor.execute(f"SELECT SnapshotId FROM stg.MddMacSourceSnapshot{where} ORDER BY SnapshotId", tuple(params))
    values = [int(_row_value(row, "SnapshotId", 0)) for row in cursor.fetchall()]
    return values[: args.max_snapshots] if args.max_snapshots is not None else values


def _batch_reconciliation(cursor: Any, snapshot_ids: Sequence[int]) -> dict[str, Any]:
    """Reconcile source and verified-envelope counts for one bounded batch."""

    if not snapshot_ids:
        return {"status": "PASS", "snapshot_count": 0, "source": {}, "compact": {}}
    placeholders = ", ".join("%s" for _ in snapshot_ids)
    params = tuple(int(value) for value in snapshot_ids)
    source: dict[str, int] = {}
    cursor.execute(
        f"SELECT COUNT_BIG(*) AS row_count FROM stg.MddMacSourceSnapshot WHERE SnapshotId IN ({placeholders})",
        params,
    )
    source["source_snapshot"] = int(_row_value(cursor.fetchone(), "row_count", 0) or 0)
    for family, table in FAMILIES:
        cursor.execute(
            f"SELECT COUNT_BIG(*) AS row_count FROM {table} WHERE SnapshotId IN ({placeholders})",
            params,
        )
        source[family] = int(_row_value(cursor.fetchone(), "row_count", 0) or 0)
    cursor.execute(
        f"""
        SELECT COUNT_BIG(*) AS source_snapshot_count,
               COALESCE(SUM(CallRowCount), 0) AS calls,
               COALESCE(SUM(TimingRowCount), 0) AS timings,
               COALESCE(SUM(SituationRowCount), 0) AS situations,
               COALESCE(SUM(ServiceSectionRowCount), 0) AS service_sections,
               COALESCE(SUM(AttributeRowCount), 0) AS attributes,
               COALESCE(SUM(ResponseErrorRowCount), 0) AS response_errors,
               COALESCE(SUM(ParserGapRowCount), 0) AS parser_gaps
        FROM stg.MddMacCompactObservationEnvelope
        WHERE VerificationStatus = 'VERIFIED'
          AND SnapshotId IN ({placeholders})
        """,
        params,
    )
    row = cursor.fetchone()
    compact = {
        "source_snapshot": int(_row_value(row, "source_snapshot_count", 0) or 0),
        "calls": int(_row_value(row, "calls", 1) or 0),
        "timings": int(_row_value(row, "timings", 2) or 0),
        "situations": int(_row_value(row, "situations", 3) or 0),
        "service_sections": int(_row_value(row, "service_sections", 4) or 0),
        "attributes": int(_row_value(row, "attributes", 5) or 0),
        "response_errors": int(_row_value(row, "response_errors", 6) or 0),
        "parser_gaps": int(_row_value(row, "parser_gaps", 7) or 0),
    }
    return {
        "status": "PASS" if source == compact else "FAIL",
        "snapshot_count": len(snapshot_ids),
        "first_snapshot_id": int(snapshot_ids[0]),
        "last_snapshot_id": int(snapshot_ids[-1]),
        "source": source,
        "compact": compact,
    }


def _migration_conflict_gate(cursor: Any, migration_id: uuid.UUID) -> dict[str, Any]:
    """Reject a second historical operation before its first ledger write."""

    cursor.execute(
        """
        SELECT MigrationId, Status, MigrationKind
        FROM stg.MddMacCompactMigration
        WHERE Status = 'RUNNING'
          AND MigrationKind = 'HISTORICAL'
          AND MigrationId <> %s
        """,
        (str(migration_id),),
    )
    rows = cursor.fetchall()
    conflicts = [
        {
            "migration_id": str(_row_value(row, "MigrationId", 0)),
            "status": str(_row_value(row, "Status", 1)),
            "kind": str(_row_value(row, "MigrationKind", 2)),
        }
        for row in rows
    ]
    return {"status": "PASS" if not conflicts else "BLOCKED", "conflicts": conflicts}


def _recovery_decision(store_outcome: str | None, exc: BaseException) -> dict[str, Any]:
    """Classify a failed unit without turning a connection failure into success."""

    retryable_built = store_outcome in {"BUILT", "ALREADY_BUILT", "STORED"}
    quarantineable = retryable_built and isinstance(exc, CompactEnvelopeError)
    return {
        "retryable_built": retryable_built,
        "quarantineable": quarantineable,
        "status": "QUARANTINED" if quarantineable else "BLOCKED",
        "recovery": "RETRY_BUILT_UNIT" if retryable_built and not quarantineable else None,
    }


def _process(
    *,
    connection: Any,
    snapshot_ids: Sequence[int],
    migration_id: uuid.UUID,
    apply: bool,
    log_limit: float,
    batch_size: int,
    capacity_plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    cursor = connection.cursor()
    summary: dict[str, Any] = {
        "status": "PASS",
        "migration_id": str(migration_id),
        "apply": apply,
        "selected_snapshot_count": len(snapshot_ids),
        "processed": 0,
        "verified": 0,
        "quarantined": 0,
        "planned": 0,
        "units": [],
        "batch_checkpoints": [],
    }
    cursor.execute("SELECT COUNT_BIG(*) AS source_snapshot_count FROM stg.MddMacSourceSnapshot")
    total_source_snapshots = int(_row_value(cursor.fetchone(), "source_snapshot_count", 0) or 0)
    summary["total_source_snapshot_count"] = total_source_snapshots
    summary["selected_is_full_history"] = len(snapshot_ids) == total_source_snapshots
    required_extra_bytes = 0
    if capacity_plan is not None:
        if not isinstance(capacity_plan, Mapping):
            raise MssqlSafetyError("capacity plan must be an object")
        capacity_plan = _validate_capacity_plan(capacity_plan)
        required_extra_bytes = capacity_plan["peak_additional_bytes"]
    summary["capacity_plan"] = dict(capacity_plan) if capacity_plan else None
    summary["peak_migration_space_gib"] = (
        round(required_extra_bytes / (1024**3), 6)
        if capacity_plan is not None
        else None
    )
    summary["peak_capacity_plan_exact"] = capacity_plan is not None
    if apply and capacity_plan is None:
        summary["status"] = "BLOCKED"
        summary["stop_reason"] = "a measured bounded SQL capacity plan is required for apply"
        return summary
    if apply and capacity_plan is not None:
        planned_count = int(capacity_plan.get("planned_snapshot_count", 0) or 0)
        if planned_count < len(snapshot_ids):
            summary["status"] = "BLOCKED"
            summary["stop_reason"] = "capacity plan covers fewer snapshots than the selected migration"
            return summary
        if summary["selected_is_full_history"] and planned_count != total_source_snapshots:
            summary["status"] = "BLOCKED"
            summary["stop_reason"] = "full-history migration requires a capacity plan for the current source count"
            return summary
    if apply and not summary["selected_is_full_history"] and capacity_plan is not None:
        # A full-history plan is intentionally conservative for a subset; it
        # may be reused, but the runner records that it was not an extrapolated
        # subset estimate.
        summary["capacity_plan_scope"] = "FULL_HISTORY_PLAN_USED_FOR_SELECTED_SUBSET"
    if apply:
        # This is deliberately before _make_migration: a failed capacity,
        # writer, or conflict check must leave the production database with no
        # migration metadata write at all.
        capacity = _read_capacity(cursor)
        preflight_capacity = _capacity_gate(
            capacity,
            log_limit=log_limit,
            required_extra_bytes=required_extra_bytes,
        )
        summary["production_capacity_preflight"] = preflight_capacity
        if preflight_capacity.get("status") != "PASS":
            summary["status"] = "BLOCKED"
            summary["stop_reason"] = "production capacity, log, or active-writer preflight failed"
            return summary
        conflict_gate = _migration_conflict_gate(cursor, migration_id)
        summary["migration_conflict_preflight"] = conflict_gate
        if conflict_gate.get("status") != "PASS":
            summary["status"] = "BLOCKED"
            summary["stop_reason"] = "another historical compact migration is RUNNING"
            return summary
        _make_migration(cursor, migration_id, apply=True)
        connection.commit()
    else:
        summary["production_capacity_preflight"] = {"status": "NOT_APPLICABLE_PLAN"}

    batch_start = 0
    for index, snapshot_id in enumerate(snapshot_ids):
        unit: dict[str, Any] = {"snapshot_id": snapshot_id}
        stop_after_unit = False
        try:
            capacity = _read_capacity(cursor)
            gate = _capacity_gate(
                capacity,
                log_limit=log_limit,
                required_extra_bytes=required_extra_bytes,
            )
            unit["preflight_capacity"] = gate
            if gate["status"] != "PASS":
                raise MssqlSafetyError("capacity gate failed before compact unit")
            raw_snapshot, raw_rows = _read_unit(cursor, snapshot_id)
            normalized_snapshot, normalized_rows = _normalized_unit(raw_snapshot, raw_rows)
            envelope = build_compact_envelope(
                source_snapshot=normalized_snapshot,
                row_sets=normalized_rows,
            )
            encoded = encode_compact_envelope(envelope)
            local_roundtrip = decode_compact_envelope(
                encoded.gzip_bytes,
                expected_sha256=encoded.sha256,
            )
            parity = _unit_parity(normalized_snapshot, normalized_rows, local_roundtrip)
            if parity["status"] != "PASS":
                raise CompactEnvelopeError("offline canonical envelope parity failed")
            unit.update(
                {
                    "counts": {"source_snapshot": 1, **{family: len(rows) for family, rows in normalized_rows.items()}},
                    "logical_bytes": encoded.logical_bytes,
                    "offline_gzip_bytes": encoded.compressed_bytes,
                    "envelope_sha256": encoded.sha256,
                    "offline_parity": parity,
                }
            )
            if not apply:
                unit["status"] = "PLANNED"
                summary["planned"] += 1
            else:
                store_outcome = _store_unit(
                    cursor,
                    migration_id=migration_id,
                    snapshot_id=snapshot_id,
                    source_row=raw_snapshot,
                    normalized_snapshot=normalized_snapshot,
                    encoded=encoded,
                    counts=unit["counts"],
                )
                unit["store_outcome"] = store_outcome
                connection.commit()
                stored_gzip, stored_hash = _read_stored_envelope(cursor, snapshot_id)
                stored_envelope = decode_compact_envelope(stored_gzip, expected_sha256=stored_hash)
                stored_parity = _unit_parity(normalized_snapshot, normalized_rows, stored_envelope)
                sql_readback = _sql_readback_parity(
                    cursor,
                    snapshot=normalized_snapshot,
                    rows=normalized_rows,
                    stored_envelope_parity=stored_parity,
                )
                unit["sql_readback_parity"] = sql_readback
                if sql_readback["status"] != "PASS":
                    _mark_unit(cursor, migration_id, snapshot_id, "QUARANTINED", "SQL readback field parity failed")
                    connection.commit()
                    unit["status"] = "QUARANTINED"
                    summary["quarantined"] += 1
                    summary["status"] = "BLOCKED"
                    stop_after_unit = True
                    _set_migration_status(
                        cursor,
                        migration_id,
                        "BLOCKED",
                        "SQL compatibility-view or physical-field readback parity failed",
                    )
                    connection.commit()
                else:
                    _mark_unit(cursor, migration_id, snapshot_id, "VERIFIED")
                    connection.commit()
                    unit["status"] = "VERIFIED"
                    summary["verified"] += 1
                post_gate = _capacity_gate(
                    _read_capacity(cursor),
                    log_limit=log_limit,
                    required_extra_bytes=required_extra_bytes,
                )
                unit["postflight_capacity"] = post_gate
                if post_gate["status"] != "PASS":
                    summary["status"] = "BLOCKED"
                    unit["status"] = (
                        "BLOCKED_AFTER_QUARANTINE"
                        if unit.get("status") == "QUARANTINED"
                        else "BLOCKED_AFTER_COMMIT"
                    )
                    stop_after_unit = True
                    _set_migration_status(
                        cursor,
                        migration_id,
                        "BLOCKED",
                        "capacity gate failed after compact unit commit",
                    )
                    connection.commit()
            summary["processed"] += 1
        except Exception as exc:
            try:
                connection.rollback()
            except Exception:
                pass
            recovery = _recovery_decision(unit.get("store_outcome"), exc)
            retryable_built = recovery["retryable_built"]
            quarantineable = recovery["quarantineable"]
            unit["status"] = recovery["status"]
            if recovery["recovery"] is not None:
                unit["recovery"] = recovery["recovery"]
            unit["error_type"] = type(exc).__name__
            unit["error"] = _safe_error(exc)
            summary["status"] = "BLOCKED"
            if apply:
                if quarantineable:
                    try:
                        _mark_unit(cursor, migration_id, snapshot_id, "QUARANTINED", unit["error"])
                        connection.commit()
                        summary["quarantined"] += 1
                    except Exception:
                        connection.rollback()
                try:
                    _set_migration_status(cursor, migration_id, "BLOCKED", unit["error"])
                    connection.commit()
                except Exception:
                    connection.rollback()
            summary["units"].append(unit)
            break
        summary["units"].append(unit)
        if stop_after_unit:
            break
        if (index + 1) % max(batch_size, 1) == 0 or index + 1 == len(snapshot_ids):
            batch_ids = snapshot_ids[batch_start : index + 1]
            # The SQL ledger already checkpoints every verified unit.  This
            # aggregate reconciliation makes the batch boundary explicit and
            # proves source/compact counts before proceeding to the next one.
            checkpoint = _batch_reconciliation(cursor, batch_ids) if apply else {
                "status": "NOT_APPLICABLE_PLAN",
                "snapshot_count": len(batch_ids),
            }
            summary["batch_checkpoints"].append(checkpoint)
            if apply and checkpoint.get("status") != "PASS":
                summary["status"] = "BLOCKED"
                summary["stop_reason"] = "batch row-count reconciliation failed"
                _set_migration_status(
                    cursor,
                    migration_id,
                    "BLOCKED",
                    "batch row-count reconciliation failed",
                )
                connection.commit()
                break
            gate = _capacity_gate(
                _read_capacity(cursor),
                log_limit=log_limit,
                required_extra_bytes=required_extra_bytes,
            )
            if gate["status"] != "PASS":
                summary["status"] = "BLOCKED"
                summary["stop_reason"] = "capacity gate failed between bounded batches"
                _set_migration_status(
                    cursor,
                    migration_id,
                    "BLOCKED",
                    "capacity gate failed between bounded batches",
                )
                connection.commit()
                break
            batch_start = index + 1
    if (
        apply
        and summary["status"] == "PASS"
        and summary["verified"] == len(snapshot_ids)
        and summary["selected_is_full_history"]
    ):
        cursor.execute(
            """
            UPDATE stg.MddMacCompactMigration
            SET LastSnapshotId = %s
            WHERE MigrationId = %s
            """,
            (snapshot_ids[-1] if snapshot_ids else None, str(migration_id)),
        )
        _set_migration_status(cursor, migration_id, "COMPLETE")
        connection.commit()
    elif apply and summary["status"] == "PASS" and summary["verified"] == len(snapshot_ids):
        summary["status"] = "PARTIAL"
        summary["stop_reason"] = "subset completed; migration remains RUNNING until full history is verified"
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write compact envelopes; otherwise plan only")
    parser.add_argument("--migration-id", type=uuid.UUID, help="resume an existing migration ledger")
    parser.add_argument("--from-snapshot-id", type=int)
    parser.add_argument("--to-snapshot-id", type=int)
    parser.add_argument("--max-snapshots", type=int)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--max-log-used-percent", type=float, default=DEFAULT_MAX_LOG_USED_PERCENT)
    parser.add_argument(
        "--capacity-plan",
        type=Path,
        help="measured bounded SQL allocation plan required for --apply",
    )
    parser.add_argument(
        "--backup-evidence",
        type=Path,
        help="fresh off-C COPY_ONLY/CHECKSUM/VERIFYONLY evidence required for --apply",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)
    result: dict[str, Any] = {
        "status": "BLOCKED",
        "operation": "historical_compact_migration",
        "safety_floor_gib": SAFETY_FLOOR_GIB,
        "collector_gate": _launchd_gate(),
        "storage_operation_gate": local_storage_operation_gate(),
    }
    try:
        capacity_plan = None
        if args.apply:
            if args.capacity_plan is None:
                raise MssqlSafetyError("--capacity-plan is required for historical migration apply")
            if args.backup_evidence is None:
                raise MssqlSafetyError("--backup-evidence is required for historical migration apply")
            result["backup_gate"] = _backup_gate(args.backup_evidence)
            if result["backup_gate"].get("status") != "PASS":
                raise MssqlSafetyError("fresh checksum-verified off-drive backup gate is not proven")
            capacity_plan = _load_capacity_plan(args.capacity_plan)
            result["capacity_plan"] = capacity_plan
        if result["collector_gate"].get("status") != "PASS":
            raise MssqlSafetyError("collector stopped/disabled gate is not proven")
        if result["storage_operation_gate"].get("status") != "PASS":
            raise MssqlSafetyError("another compact-storage operation is active")
        settings, _role_source = resolve_administrator_connection_settings(
            os.environ,
            secret_provider=MacOSKeychainSecretProvider(),
        )
        connection = connect_mssql(settings)
        try:
            cursor = connection.cursor()
            snapshot_ids = _snapshot_ids(cursor, args)
            migration_id = args.migration_id or uuid.uuid4()
            result["migration_id"] = str(migration_id)
            result["selected_snapshot_ids"] = snapshot_ids
            result["mode"] = "APPLY" if args.apply else "PLAN"
            result["run"] = _process(
                connection=connection,
                snapshot_ids=snapshot_ids,
                migration_id=migration_id,
                apply=args.apply,
                log_limit=args.max_log_used_percent,
                batch_size=max(1, args.batch_size),
                capacity_plan=capacity_plan,
            )
            result["status"] = result["run"].get("status", "BLOCKED")
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

