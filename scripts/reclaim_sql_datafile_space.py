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
import platform
import re
import subprocess
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
from cologne_transit_collector.runtime_configuration import (  # noqa: E402
    MacOSKeychainSecretProvider,
    resolve_administrator_connection_settings,
)
from validate_mssql_migration import split_batches  # noqa: E402


RETIREMENT_SQL = ROOT / "sql" / "mdd-mac-collector" / "retire-legacy-observation-storage-v1.sql"
COLLECTOR_LABELS = (
    "com.cologne-transit-intelligence.mdd-collector",
    "com.cologne-transit-intelligence.mdd-tiered50-collector",
)
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


def _collector_gate() -> dict[str, Any]:
    if platform.system() != "Darwin":
        return {"status": "NOT_PROVEN", "reason": "launchd state is not inspectable on this host"}
    try:
        result = subprocess.run(
            ["/bin/launchctl", "print-disabled", f"user/{os.getuid()}"],
            capture_output=True,
            text=True,
            check=False,
        )
        text = result.stdout + result.stderr
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


def _load_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"evidence file is not a JSON object: {path}")
    return value


def _backup_gate(path: Path) -> dict[str, Any]:
    data = _load_json(path)
    verify = data.get("backup", {}).get("verifyonly") if isinstance(data.get("backup"), Mapping) else None
    if verify is None and isinstance(data.get("sql"), Mapping):
        verify = data["sql"].get("backup_verifyonly")
    status = data.get("status")
    if isinstance(verify, Mapping):
        verify_status = verify.get("status")
    else:
        verify_status = "PASS" if verify == "PASS" else None
    passed = status == "PASS" and verify_status == "PASS"
    return {"status": "PASS" if passed else "BLOCKED", "source": str(path), "verifyonly": verify_status}


def _parity_gate(path: Path) -> dict[str, Any]:
    data = _load_json(path)
    units: list[Any] = []
    if isinstance(data.get("units"), list):
        units = data["units"]
    elif isinstance(data.get("run"), Mapping) and isinstance(data["run"].get("units"), list):
        units = data["run"]["units"]
    comparisons = []
    for unit in units:
        if not isinstance(unit, Mapping):
            comparisons.append("INVALID")
            continue
        comparisons.append(unit.get("status"))
        comparison = unit.get("comparison") or unit.get("sql_readback_parity")
        if isinstance(comparison, Mapping):
            comparisons.append(comparison.get("status"))
    top_status = data.get("status")
    passed = top_status == "PASS" and bool(units) and all(value == "PASS" for value in comparisons)
    return {"status": "PASS" if passed else "BLOCKED", "source": str(path), "unit_count": len(units)}


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
    rows = [
        {
            "file_id": int(_row_value(row, "file_id", 0)),
            "name": str(_row_value(row, "name", 1)),
            "type": str(_row_value(row, "type_desc", 2)),
            "size_pages": int(_row_value(row, "size", 3)),
            "space_used_pages": int(_row_value(row, "space_used_pages", 4) or 0),
            "volume": str(_row_value(row, "volume_mount_point", 5) or ""),
            "available_bytes": int(_row_value(row, "available_bytes", 6) or 0),
        }
        for row in cursor.fetchall()
    ]
    for row in rows:
        row["allocated_gib"] = row["size_pages"] * 8192 / (1024**3)
        row["used_gib"] = row["space_used_pages"] * 8192 / (1024**3)
        row["internal_free_gib"] = row["allocated_gib"] - row["used_gib"]
        row["windows_free_gib"] = row["available_bytes"] / (1024**3)
    return {"data_files": rows}


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
            try:
                cursor.fetchall()
            except Exception:
                pass
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
    try:
        cursor.fetchall()
    except Exception:
        pass
    after = _file_capacity(cursor)
    return {"status": "PASS", "before": before, "after": after, "target_mb": target_mb, "requested_shrink_gib": shrink_gib}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-evidence", type=Path, required=True)
    parser.add_argument("--parity-evidence", type=Path, required=True)
    parser.add_argument("--migration-id", required=True)
    parser.add_argument("--apply", action="store_true", help="execute the guarded retirement transaction")
    parser.add_argument("--shrink", action="store_true", help="after retirement, run one bounded DBCC SHRINKFILE")
    parser.add_argument("--shrink-headroom-gib", type=float, default=1.0)
    parser.add_argument("--max-shrink-gib", type=float, default=8.0)
    args = parser.parse_args(argv)
    result: dict[str, Any] = {
        "operation": "legacy_storage_retirement_and_reclamation",
        "status": "BLOCKED",
        "collector_gate": _collector_gate(),
        "apply": args.apply,
        "shrink_requested": args.shrink,
    }
    try:
        result["backup_gate"] = _backup_gate(args.backup_evidence)
        result["parity_gate"] = _parity_gate(args.parity_evidence)
        if result["collector_gate"].get("status") != "PASS":
            raise RuntimeError("collector stopped/disabled gate is not proven")
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
                result["retirement"] = {"status": "PASS", "executed_batches": _execute_retirement(connection)}
                result["files_after_retirement"] = _file_capacity(cursor)
                if args.shrink:
                    result["shrink"] = _shrink_one_file(
                        cursor,
                        headroom_gib=max(0.25, args.shrink_headroom_gib),
                        max_shrink_gib=max(0.0, args.max_shrink_gib),
                    )
                    connection.commit()
                    result["files_after_shrink"] = _file_capacity(cursor)
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

