#!/usr/bin/env python3
"""Assess or explicitly apply bounded C: data-file reclamation after Snapshot GZIP.

The default path is read-only.  ``--apply`` requires a fresh backup artifact,
complete Snapshot GZIP/readback evidence, an independently captured live
MDF/LDF/tempdb/session/collector preflight, measured log/rollback/autogrowth/
external reserves, a separate human approval artifact, the stopped-collector
gate, and the live 12 GiB C: floor.  It never moves SQL files, adds storage,
retires legacy tables, or uses SHRINKDATABASE.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from cologne_transit_collector.compact_observation import (  # noqa: E402
    local_collector_safety_gate,
    local_storage_operation_gate,
)
from cologne_transit_collector.mssql_persistence import connect_mssql  # noqa: E402
from cologne_transit_collector.runtime_configuration import (  # noqa: E402
    MacOSKeychainSecretProvider,
    resolve_administrator_connection_settings,
)
from reclaim_sql_datafile_space import (  # noqa: E402
    SAFETY_FLOOR_GIB,
    _backup_gate,
    _file_capacity,
    _row_value,
    _safe_error,
    _shrink_one_file,
    _space_delta,
)


EVIDENCE_MAX_AGE = timedelta(minutes=30)
CAPACITY_RESERVE_FIELDS = (
    "transaction_log_reserve_bytes",
    "rollback_reserve_bytes",
    "tempdb_reserve_bytes",
    "autogrowth_reserve_bytes",
    "external_c_drive_reserve_bytes",
)


def _load_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"evidence file is not a JSON object: {path}")
    return value


def _fresh_utc(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("evidence timestamp is missing")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("evidence timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _approval_gate(path: Path, *, now: datetime | None = None) -> dict[str, Any]:
    data = _load_json(path)
    missing = [
        name
        for name in (
            "approved_by",
            "approved_at_utc",
            "operation",
            "snapshot_gzip_readback_status",
            "windows_floor_confirmed",
            "live_preflight_evidence_reference",
            "live_preflight_captured_at_utc",
            "capacity_reserves",
        )
        if not str(data.get(name) or "").strip()
    ]
    try:
        approved_at = _fresh_utc(data.get("approved_at_utc"))
        age = (now or datetime.now(timezone.utc)).astimezone(timezone.utc) - approved_at
        fresh = timedelta(0) <= age <= EVIDENCE_MAX_AGE
    except (TypeError, ValueError):
        age = None
        fresh = False
        missing.append("approved_at_utc")
    if data.get("operation") != "SNAPSHOT_GZIP_BOUNDED_SHRINKFILE":
        missing.append("operation")
    if data.get("snapshot_gzip_readback_status") != "PASS":
        missing.append("snapshot_gzip_readback_status")
    if data.get("windows_floor_confirmed") is not True:
        missing.append("windows_floor_confirmed")
    if not str(data.get("live_preflight_evidence_reference") or "").strip():
        missing.append("live_preflight_evidence_reference")
    try:
        preflight_at = _fresh_utc(data.get("live_preflight_captured_at_utc"))
        preflight_age = (now or datetime.now(timezone.utc)).astimezone(timezone.utc) - preflight_at
        preflight_fresh = timedelta(0) <= preflight_age <= EVIDENCE_MAX_AGE
    except (TypeError, ValueError):
        preflight_age = None
        preflight_fresh = False
        missing.append("live_preflight_captured_at_utc")
    if not preflight_fresh:
        missing.append("fresh_live_preflight")
    reserves = data.get("capacity_reserves")
    if not isinstance(reserves, Mapping):
        missing.append("capacity_reserves")
    else:
        for field in CAPACITY_RESERVE_FIELDS:
            item = reserves.get(field)
            if not isinstance(item, Mapping) or item.get("status") != "PASS":
                missing.append(f"capacity_reserves.{field}")
                continue
            try:
                if int(item.get("bytes")) < 0:
                    raise ValueError
            except (TypeError, ValueError):
                missing.append(f"capacity_reserves.{field}.bytes")
    if not fresh:
        missing.append("fresh_approval")
    return {
        "status": "PASS" if data.get("approved") is True and not missing else "BLOCKED",
        "approved": data.get("approved") is True,
        "approved_by": data.get("approved_by"),
        "missing_fields": sorted(set(missing)),
        "fresh": fresh,
        "age_seconds": age.total_seconds() if age is not None else None,
        "live_preflight_fresh": preflight_fresh,
        "live_preflight_age_seconds": preflight_age.total_seconds() if preflight_age is not None else None,
        "capacity_reserve_fields": list(CAPACITY_RESERVE_FIELDS),
    }


def _snapshot_gzip_gate(cursor: Any) -> dict[str, Any]:
    cursor.execute(
        """
        SELECT
            COUNT_BIG(*) AS snapshot_rows,
            SUM(CONVERT(BIGINT, CASE WHEN SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NULL THEN 1 ELSE 0 END)) AS plaintext_rows,
            SUM(CONVERT(BIGINT, CASE WHEN SnapshotJson IS NULL AND SnapshotJsonGzip IS NOT NULL THEN 1 ELSE 0 END)) AS compressed_rows,
            SUM(CONVERT(BIGINT, CASE WHEN SnapshotJson IS NULL AND SnapshotJsonGzip IS NULL THEN 1 ELSE 0 END)) AS missing_payload_rows,
            SUM(CONVERT(BIGINT, CASE WHEN SnapshotJson IS NOT NULL AND SnapshotJsonGzip IS NOT NULL THEN 1 ELSE 0 END)) AS dual_payload_rows
        FROM stg.MddMacSourceSnapshot
        """
    )
    row = cursor.fetchone()
    values = {
        "snapshot_rows": int(_row_value(row, "snapshot_rows", 0) or 0),
        "plaintext_rows": int(_row_value(row, "plaintext_rows", 1) or 0),
        "compressed_rows": int(_row_value(row, "compressed_rows", 2) or 0),
        "missing_payload_rows": int(_row_value(row, "missing_payload_rows", 3) or 0),
        "dual_payload_rows": int(_row_value(row, "dual_payload_rows", 4) or 0),
    }
    cursor.execute(
        """
        SELECT COUNT_BIG(*) AS view_rows
        FROM stg.vMddMacSourceSnapshotJson
        """
    )
    view_rows = int(_row_value(cursor.fetchone(), "view_rows", 0) or 0)
    values["view_rows"] = view_rows
    cursor.execute(
        """
        SELECT
            SUM(CONVERT(BIGINT, CASE WHEN SnapshotJson IS NULL AND SnapshotJsonGzip IS NOT NULL
                                     AND DECOMPRESS(SnapshotJsonGzip) IS NULL THEN 1 ELSE 0 END)) AS corrupt_gzip_rows,
            SUM(CONVERT(BIGINT, CASE WHEN SnapshotJson IS NULL AND SnapshotJsonGzip IS NOT NULL
                                     AND SnapshotJsonGzip IS NOT NULL THEN 1 ELSE 0 END)) AS compressed_payload_rows
        FROM stg.MddMacSourceSnapshot
        """
    )
    parity_row = cursor.fetchone()
    values["corrupt_gzip_rows"] = int(_row_value(parity_row, "corrupt_gzip_rows", 0) or 0)
    values["compressed_payload_rows"] = int(_row_value(parity_row, "compressed_payload_rows", 1) or 0)
    values["status"] = (
        "PASS"
        if values["snapshot_rows"] == values["compressed_rows"]
        and values["snapshot_rows"] == view_rows
        and values["missing_payload_rows"] == 0
        and values["dual_payload_rows"] == 0
        and values["corrupt_gzip_rows"] == 0
        and values["snapshot_rows"] > 0
        else "BLOCKED"
    )
    values["contract"] = "ALL_SNAPSHOTS_COMPRESSED_AND_VIEW_READBACK_COMPLETE"
    return values


def _capacity_projection_gate(data: Mapping[str, Any]) -> dict[str, Any]:
    """Require a measured physical C: projection before a shrink write."""

    projection = data.get("physical_capacity_projection")
    required = (
        "status",
        "current_windows_c_free_bytes",
        "worst_case_physical_c_reserve_bytes",
        "projected_worst_case_c_free_bytes",
        "required_windows_c_floor_bytes",
    )
    if not isinstance(projection, Mapping):
        return {"status": "BLOCKED", "missing_fields": ["physical_capacity_projection"]}
    missing = [name for name in required if name not in projection]
    try:
        current = int(projection.get("current_windows_c_free_bytes"))
        reserve = int(projection.get("worst_case_physical_c_reserve_bytes"))
        projected = int(projection.get("projected_worst_case_c_free_bytes"))
        floor = int(projection.get("required_windows_c_floor_bytes"))
    except (TypeError, ValueError):
        missing.append("physical_capacity_projection.numeric_fields")
        current = reserve = projected = floor = -1
    if projection.get("status") != "PASS":
        missing.append("physical_capacity_projection.status")
    if current < floor or reserve < 0 or projected < floor or current - reserve != projected:
        missing.append("physical_capacity_projection.floor_proof")
    return {
        "status": "PASS" if not missing else "BLOCKED",
        "missing_fields": sorted(set(missing)),
        "current_windows_c_free_bytes": current,
        "worst_case_physical_c_reserve_bytes": reserve,
        "projected_worst_case_c_free_bytes": projected,
        "required_windows_c_floor_bytes": floor,
    }


def _active_writer_gate(cursor: Any) -> dict[str, Any]:
    cursor.execute(
        """
        SELECT
            (SELECT COUNT_BIG(*) FROM ctl.MddMacCollectorRun
             WHERE Status IN ('RUNNING', 'CLAIMED')
               AND CollectionContext IN ('MAC_LEGACY7_PRODUCTION', 'MAC_TIERED50_PRODUCTION')) AS active_runs,
            (SELECT COUNT_BIG(*) FROM ctl.MddMacProductionLease
             WHERE LeaseName = N'MDD_MAC_PRODUCTION' AND Status = N'ACTIVE') AS active_leases,
            (SELECT COUNT_BIG(*) FROM sys.dm_tran_session_transactions AS st
             INNER JOIN sys.dm_exec_sessions AS es ON es.session_id = st.session_id
             WHERE st.session_id <> @@SPID AND es.is_user_process = 1) AS active_transactions,
            (SELECT COUNT_BIG(*) FROM sys.dm_exec_sessions AS es
             WHERE es.session_id <> @@SPID AND es.is_user_process = 1
               AND es.database_id = DB_ID()) AS active_sql_sessions,
            (SELECT COUNT_BIG(*) FROM sys.dm_exec_requests AS er
             WHERE er.session_id <> @@SPID AND er.database_id = DB_ID()) AS active_sql_requests,
            (SELECT COUNT_BIG(*) FROM sys.dm_exec_sessions AS es
             WHERE es.session_id <> @@SPID AND es.is_user_process = 1
               AND es.database_id = DB_ID()
               AND (LOWER(COALESCE(es.program_name, N'')) LIKE N'%cologne%transit%'
                    OR LOWER(COALESCE(es.program_name, N'')) LIKE N'%mdd%collector%'
                    OR LOWER(COALESCE(es.host_name, N'')) LIKE N'%mdd%collector%')) AS collector_sessions
        """
    )
    row = cursor.fetchone()
    values = {
        "active_runs": int(_row_value(row, "active_runs", 0) or 0),
        "active_leases": int(_row_value(row, "active_leases", 1) or 0),
        "active_transactions": int(_row_value(row, "active_transactions", 2) or 0),
        "active_sql_sessions": int(_row_value(row, "active_sql_sessions", 3) or 0),
        "active_sql_requests": int(_row_value(row, "active_sql_requests", 4) or 0),
        "collector_sessions": int(_row_value(row, "collector_sessions", 5) or 0),
    }
    values["status"] = "PASS" if not any(values.values()) else "BLOCKED"
    return values


def _fragmentation_snapshot(cursor: Any) -> list[dict[str, Any]]:
    """Capture read-only fragmentation evidence around a separate shrink."""

    cursor.execute(
        """
        SELECT
            OBJECT_SCHEMA_NAME(ps.object_id) AS schema_name,
            OBJECT_NAME(ps.object_id) AS object_name,
            ps.index_id,
            ps.index_type_desc,
            ps.page_count,
            CONVERT(decimal(9, 3), ps.avg_fragmentation_in_percent) AS avg_fragmentation_in_percent,
            ps.fragment_count,
            ps.avg_page_space_used_in_percent
        FROM sys.dm_db_index_physical_stats(
            DB_ID(), NULL, NULL, NULL, 'LIMITED'
        ) AS ps
        WHERE OBJECT_SCHEMA_NAME(ps.object_id) = N'stg'
          AND OBJECT_NAME(ps.object_id) IN (N'MddMacSourceSnapshot', N'MddMacCallObservation',
              N'MddMacTimingObservation', N'MddMacSituationEvidence', N'MddMacServiceSectionEvidence',
              N'MddMacAttributeEvidence', N'MddMacResponseError', N'MddMacParserGap')
        ORDER BY ps.object_id, ps.index_id;
        """
    )
    columns = (
        "schema_name", "object_name", "index_id", "index_type_desc",
        "page_count", "avg_fragmentation_in_percent", "fragment_count",
        "avg_page_space_used_in_percent",
    )
    return [
        {name: _row_value(row, name, index) for index, name in enumerate(columns)}
        for row in cursor.fetchall()
    ]


def _shrink_assessment(
    capacity: Mapping[str, Any], *, headroom_gib: float, max_shrink_gib: float
) -> dict[str, Any]:
    files = list(capacity.get("data_files", ()))
    if len(files) != 1:
        return {"status": "BLOCKED", "reason": "bounded shrink requires exactly one data file", "files": files}
    file = files[0]
    if not str(file.get("volume") or "").casefold().startswith("c:"):
        return {"status": "BLOCKED", "reason": "bounded shrink is restricted to C:", "file": file}
    if float(file.get("windows_free_gib", 0.0)) < SAFETY_FLOOR_GIB:
        return {"status": "BLOCKED", "reason": "C: free space is below the 12 GiB floor", "file": file}
    if headroom_gib <= 0 or max_shrink_gib < 0:
        return {"status": "BLOCKED", "reason": "shrink bounds are invalid", "file": file}
    used_pages = int(file.get("space_used_pages") or 0)
    target_pages = used_pages + int((headroom_gib * (1024**3) + 8191) // 8192)
    target_mb = int((target_pages * 8192 + (1024**2 - 1)) // (1024**2))
    current_mb = int((int(file.get("size_pages") or 0) * 8192 + (1024**2 - 1)) // (1024**2))
    requested_gib = max(0.0, float(file.get("allocated_gib", 0.0)) - target_mb / 1024)
    if target_mb >= current_mb:
        status = "NOT_NEEDED"
    elif requested_gib > max_shrink_gib:
        status = "BLOCKED"
    else:
        status = "APPROVAL_REQUIRED"
    return {
        "status": status,
        "logical_file_name": file.get("name"),
        "current_size_mb": current_mb,
        "target_size_mb": target_mb,
        "requested_shrink_gib": requested_gib,
        "windows_free_gib": file.get("windows_free_gib"),
        "internal_free_gib": file.get("internal_free_gib"),
        "physical_recovery_status": "NOT_MEASURED_UNTIL_EXECUTED",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-evidence", type=Path)
    parser.add_argument("--approval-evidence", type=Path)
    parser.add_argument("--apply", action="store_true", help="execute one explicitly approved DBCC SHRINKFILE")
    parser.add_argument("--shrink-headroom-gib", type=float, default=1.0)
    parser.add_argument("--max-shrink-gib", type=float, default=8.0)
    args = parser.parse_args(argv)
    if args.apply and (args.backup_evidence is None or args.approval_evidence is None):
        parser.error("--apply requires --backup-evidence and --approval-evidence")

    result: dict[str, Any] = {
        "operation": "snapshot_gzip_bounded_datafile_reclamation",
        "status": "BLOCKED",
        "apply": args.apply,
        "collector_gate": local_collector_safety_gate(),
        "storage_operation_gate": local_storage_operation_gate(),
    }
    connection = None
    try:
        if args.apply:
            result["backup_gate"] = _backup_gate(args.backup_evidence)
            result["approval_gate"] = _approval_gate(args.approval_evidence)
            approval_data = _load_json(args.approval_evidence)
            result["capacity_projection_gate"] = _capacity_projection_gate(approval_data)
            if result["backup_gate"]["status"] != "PASS":
                raise RuntimeError("verified backup coverage is required before shrink")
            if result["approval_gate"]["status"] != "PASS":
                raise RuntimeError("fresh explicit Snapshot GZIP shrink approval is required")
            if result["capacity_projection_gate"]["status"] != "PASS":
                raise RuntimeError("measured worst-case physical C: floor projection is required before shrink")
        if result["collector_gate"].get("status") != "PASS":
            raise RuntimeError("collector stopped/disabled gate is not proven")
        if result["storage_operation_gate"].get("status") != "PASS":
            raise RuntimeError("another compact-storage operation is active")

        settings, role_source = resolve_administrator_connection_settings(
            os.environ,
            secret_provider=MacOSKeychainSecretProvider(),
        )
        connection = connect_mssql(settings)
        cursor = connection.cursor()
        result["admin_role_source"] = role_source
        result["snapshot_gzip_gate"] = _snapshot_gzip_gate(cursor)
        if result["snapshot_gzip_gate"]["status"] != "PASS":
            raise RuntimeError("complete Snapshot GZIP readback is not proven")
        active = _active_writer_gate(cursor)
        result["active_writer_gate"] = active
        if active["status"] != "PASS":
            raise RuntimeError("active writer, lease, or user transaction is present")
        before = _file_capacity(cursor, require_legacy_tables=False)
        result["files_before"] = before
        result["fragmentation_before"] = _fragmentation_snapshot(cursor)
        assessment = _shrink_assessment(
            before,
            headroom_gib=max(0.25, args.shrink_headroom_gib),
            max_shrink_gib=max(0.0, args.max_shrink_gib),
        )
        result["shrink_assessment"] = assessment
        if not args.apply:
            result["status"] = "PASS" if assessment["status"] in {"NOT_NEEDED", "APPROVAL_REQUIRED"} else "BLOCKED"
        else:
            if assessment["status"] != "APPROVAL_REQUIRED":
                raise RuntimeError("bounded shrink assessment did not produce an approved target")
            result["shrink"] = _shrink_one_file(
                cursor,
                headroom_gib=max(0.25, args.shrink_headroom_gib),
                max_shrink_gib=max(0.0, args.max_shrink_gib),
            )
            connection.commit()
            after = _file_capacity(cursor, require_legacy_tables=False)
            result["files_after"] = after
            result["fragmentation_after"] = _fragmentation_snapshot(cursor)
            result["space_delta"] = _space_delta(before, after)
            result["physical_recovery"] = {
                "status": (
                    "MEASURED"
                    if result["space_delta"]["actual_windows_space_recovered_gib"] > 0
                    else "NO_PHYSICAL_RECOVERY_MEASURED"
                ),
                "actual_windows_space_recovered_gib": result["space_delta"]["actual_windows_space_recovered_gib"],
                "note": "A shrink is not reported as recovery without a positive C: free-space delta",
            }
            result["status"] = "PASS"
    except Exception as exc:
        result["status"] = "BLOCKED"
        result["error_type"] = type(exc).__name__
        result["error"] = _safe_error(exc)
    finally:
        if connection is not None:
            connection.close()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
