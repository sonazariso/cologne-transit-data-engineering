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


def _parity_gate(path: Path) -> dict[str, Any]:
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
    passed = top_status == "PASS" and bool(units) and all(unit_results)
    return {
        "status": "PASS" if passed else "BLOCKED",
        "source": str(path),
        "unit_count": len(units),
        "failed_unit_count": sum(1 for value in unit_results if not value),
    }


def _file_capacity(cursor: Any) -> dict[str, Any]:
    cursor.execute(
        """
        SELECT df.file_id, df.name, df.type_desc, df.size,
               FILEPROPERTY(df.name, 'SpaceUsed') AS space_used_pages,
               vs.volume_mount_point, vs.available_bytes
        FROM sys.database_files AS df
        CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
        WHERE df.type = 0
        ORDER BY df.file_id
        """
    )
    rows = []
    for row in cursor.fetchall():
        file_id = _row_value(row, "file_id", 0)
        name = _row_value(row, "name", 1)
        size_pages = _row_value(row, "size", 3)
        space_used_pages = _row_value(row, "space_used_pages", 4)
        volume = _row_value(row, "volume_mount_point", 5)
        available_bytes = _row_value(row, "available_bytes", 6)
        if any(value is None for value in (file_id, name, size_pages, space_used_pages, volume, available_bytes)):
            raise RuntimeError("SQL file-space measurement is incomplete; shrink is blocked")
        rows.append(
            {
                "file_id": int(file_id),
                "name": str(name),
                "type": str(_row_value(row, "type_desc", 2) or ""),
                "size_pages": int(size_pages),
                "space_used_pages": int(space_used_pages),
                "volume": str(volume),
                "available_bytes": int(available_bytes),
            }
        )
    for row in rows:
        row["allocated_gib"] = row["size_pages"] * 8192 / (1024**3)
        row["used_gib"] = row["space_used_pages"] * 8192 / (1024**3)
        row["internal_free_gib"] = row["allocated_gib"] - row["used_gib"]
        row["windows_free_gib"] = row["available_bytes"] / (1024**3)
    return {"data_files": rows}


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


def _execute_retirement(connection: Any) -> int:
    text = RETIREMENT_SQL.read_text(encoding="utf-8")
    text = text.replace("DECLARE @ApplyLegacyRetirement BIT = 0;", "DECLARE @ApplyLegacyRetirement BIT = 1;", 1)
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
    before = _file_capacity(cursor)
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
    after = _file_capacity(cursor)
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
        result["backup_gate"] = _backup_gate(args.backup_evidence)
        result["parity_gate"] = _parity_gate(args.parity_evidence)
        if args.approval_evidence is not None:
            result["approval_gate"] = _approval_gate(args.approval_evidence)
        if result["collector_gate"].get("status") != "PASS":
            raise RuntimeError("collector stopped/disabled gate is not proven")
        if result["storage_operation_gate"].get("status") != "PASS":
            raise RuntimeError("another compact-storage operation is active")
        if result["backup_gate"]["status"] != "PASS":
            raise RuntimeError("fresh checksum-verified off-drive backup gate is not proven")
        if result["parity_gate"]["status"] != "PASS":
            raise RuntimeError("complete exact compact readback parity is not proven")
        settings, role_source = resolve_administrator_connection_settings(
            os.environ,
            secret_provider=MacOSKeychainSecretProvider(),
        )
        connection = connect_mssql(settings)
        try:
            cursor = connection.cursor()
            result["admin_role_source"] = role_source
            result["migration_gate"] = _migration_gate(cursor, args.migration_id)
            result["counts_before"] = _count_gate(cursor)
            result["active_writer_gate"] = _active_writer_gate(cursor)
            result["files_before"] = _file_capacity(cursor)
            if result["counts_before"]["status"] != "PASS":
                raise RuntimeError("legacy and verified compact counts do not reconcile")
            if result["migration_gate"]["status"] != "PASS":
                raise RuntimeError("the requested compact migration is not COMPLETE")
            if result["active_writer_gate"]["status"] != "PASS":
                raise RuntimeError("active writer or production lease is present")
            if not args.apply:
                result["status"] = "PASS"
            else:
                if result.get("approval_gate", {}).get("status") != "PASS":
                    raise RuntimeError("explicit human retirement approval is required before --apply")
                result["retirement"] = {"status": "PASS", "executed_batches": _execute_retirement(connection)}
                result["counts_after_retirement"] = _count_gate(cursor)
                if result["counts_after_retirement"]["status"] != "PASS":
                    raise RuntimeError("post-retirement compatibility-view counts do not reconcile")
                result["files_after_retirement"] = _file_capacity(cursor)
                result["space_delta_after_retirement"] = _space_delta(
                    result["files_before"], result["files_after_retirement"]
                )
                if args.shrink:
                    result["shrink"] = _shrink_one_file(
                        cursor,
                        headroom_gib=max(0.25, args.shrink_headroom_gib),
                        max_shrink_gib=max(0.0, args.max_shrink_gib),
                    )
                    connection.commit()
                    result["files_after_shrink"] = _file_capacity(cursor)
                    result["space_delta_after_shrink"] = _space_delta(
                        result["files_before"], result["files_after_shrink"]
                    )
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

