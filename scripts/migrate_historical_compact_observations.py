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
import json
import os
import platform
import re
import subprocess
import sys
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.compact_observation import (  # noqa: E402
    CompactEnvelopeError,
    build_compact_envelope,
    compare_row_family,
    decode_compact_envelope,
    encode_compact_envelope,
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


FAMILIES: tuple[tuple[str, str], ...] = (
    ("calls", "stg.MddMacCallObservation"),
    ("timings", "stg.MddMacTimingObservation"),
    ("situations", "stg.MddMacSituationEvidence"),
    ("service_sections", "stg.MddMacServiceSectionEvidence"),
    ("attributes", "stg.MddMacAttributeEvidence"),
    ("response_errors", "stg.MddMacResponseError"),
    ("parser_gaps", "stg.MddMacParserGap"),
)
COLLECTOR_LABELS = (
    "com.cologne-transit-intelligence.mdd-collector",
    "com.cologne-transit-intelligence.mdd-tiered50-collector",
)
SAFETY_FLOOR_GIB = 12.0
DEFAULT_MAX_LOG_USED_PERCENT = 70.0


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

    if platform.system() != "Darwin":
        return {"status": "NOT_PROVEN", "reason": "launchd is only inspectable on macOS"}
    try:
        disabled = subprocess.run(
            ["/bin/launchctl", "print-disabled", f"user/{os.getuid()}"],
            check=False,
            capture_output=True,
            text=True,
        )
        disabled_text = disabled.stdout + disabled.stderr
        disabled_ok = all(
            f'"{label}" => disabled' in disabled_text for label in COLLECTOR_LABELS
        )
        loaded: dict[str, int] = {}
        for label in COLLECTOR_LABELS:
            result = subprocess.run(
                ["/bin/launchctl", "print", f"gui/{os.getuid()}/{label}"],
                check=False,
                capture_output=True,
                text=True,
            )
            loaded[label] = result.returncode
        loaded_ok = all(code != 0 for code in loaded.values())
        return {
            "status": "PASS" if disabled_ok and loaded_ok else "NOT_PROVEN",
            "disabled": disabled_ok,
            "loaded_exit_codes": loaded,
        }
    except OSError as exc:
        return {"status": "NOT_PROVEN", "reason": _safe_error(exc)}


def _read_capacity(cursor: Any) -> dict[str, Any]:
    cursor.execute(
        """
        SELECT DISTINCT vs.volume_mount_point, vs.total_bytes, vs.available_bytes
        FROM sys.database_files AS df
        CROSS APPLY sys.dm_os_volume_stats(DB_ID(), df.file_id) AS vs
        ORDER BY vs.volume_mount_point
        """
    )
    volumes = [
        {
            "volume": str(_row_value(row, "volume_mount_point", 0) or ""),
            "total_bytes": int(_row_value(row, "total_bytes", 1) or 0),
            "available_bytes": int(_row_value(row, "available_bytes", 2) or 0),
        }
        for row in cursor.fetchall()
    ]
    cursor.execute(
        """
        SELECT total_log_size_in_bytes, used_log_space_in_bytes,
               used_log_space_in_percent
        FROM sys.dm_db_log_space_usage
        WHERE database_id = DB_ID()
        """
    )
    log_row = cursor.fetchone()
    log = {
        "total_bytes": int(_row_value(log_row, "total_log_size_in_bytes", 0) or 0),
        "used_bytes": int(_row_value(log_row, "used_log_space_in_bytes", 1) or 0),
        "used_percent": float(_row_value(log_row, "used_log_space_in_percent", 2) or 0),
    }
    return {"volumes": volumes, "log": log}


def _capacity_gate(capacity: Mapping[str, Any], *, log_limit: float) -> dict[str, Any]:
    volumes = list(capacity.get("volumes", ()))
    rows_volume = next(
        (item for item in volumes if str(item.get("volume", "")).casefold().startswith("c:")),
        None,
    )
    available = int(rows_volume.get("available_bytes", 0)) if rows_volume else 0
    free_gib = available / (1024**3)
    log_used = float((capacity.get("log") or {}).get("used_percent", 100.0))
    return {
        "status": "PASS" if rows_volume and free_gib >= SAFETY_FLOOR_GIB and log_used <= log_limit else "BLOCKED",
        "windows_free_gib": round(free_gib, 3),
        "required_windows_free_gib": SAFETY_FLOOR_GIB,
        "log_used_percent": log_used,
        "max_log_used_percent": log_limit,
        "volume": rows_volume,
    }


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
        IF NOT EXISTS (SELECT 1 FROM stg.MddMacCompactMigration WHERE MigrationId = %s)
        INSERT INTO stg.MddMacCompactMigration
            (MigrationId, StorageFormatVersion, MigrationKind, Status)
        VALUES (%s, 1, 'HISTORICAL', 'RUNNING')
        """,
        (str(migration_id), str(migration_id)),
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
) -> None:
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
    try:
        cursor.fetchall()
    except Exception:
        pass


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


def _process(
    *,
    connection: Any,
    snapshot_ids: Sequence[int],
    migration_id: uuid.UUID,
    apply: bool,
    log_limit: float,
    batch_size: int,
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
    }
    cursor.execute("SELECT COUNT_BIG(*) AS source_snapshot_count FROM stg.MddMacSourceSnapshot")
    total_source_snapshots = int(_row_value(cursor.fetchone(), "source_snapshot_count", 0) or 0)
    summary["total_source_snapshot_count"] = total_source_snapshots
    summary["selected_is_full_history"] = len(snapshot_ids) == total_source_snapshots
    if apply:
        _make_migration(cursor, migration_id, apply=True)
        connection.commit()

    for index, snapshot_id in enumerate(snapshot_ids):
        unit: dict[str, Any] = {"snapshot_id": snapshot_id}
        try:
            capacity = _read_capacity(cursor)
            gate = _capacity_gate(capacity, log_limit=log_limit)
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
                _store_unit(
                    cursor,
                    migration_id=migration_id,
                    snapshot_id=snapshot_id,
                    source_row=raw_snapshot,
                    normalized_snapshot=normalized_snapshot,
                    encoded=encoded,
                    counts=unit["counts"],
                )
                connection.commit()
                stored_gzip, stored_hash = _read_stored_envelope(cursor, snapshot_id)
                stored_envelope = decode_compact_envelope(stored_gzip, expected_sha256=stored_hash)
                stored_parity = _unit_parity(normalized_snapshot, normalized_rows, stored_envelope)
                unit["sql_readback_parity"] = stored_parity
                if stored_parity["status"] != "PASS":
                    _mark_unit(cursor, migration_id, snapshot_id, "QUARANTINED", "SQL readback field parity failed")
                    connection.commit()
                    unit["status"] = "QUARANTINED"
                    summary["quarantined"] += 1
                    summary["status"] = "BLOCKED"
                else:
                    _mark_unit(cursor, migration_id, snapshot_id, "VERIFIED")
                    connection.commit()
                    unit["status"] = "VERIFIED"
                    summary["verified"] += 1
                post_gate = _capacity_gate(_read_capacity(cursor), log_limit=log_limit)
                unit["postflight_capacity"] = post_gate
                if post_gate["status"] != "PASS":
                    summary["status"] = "BLOCKED"
                    unit["status"] = "BLOCKED_AFTER_COMMIT"
                    break
            summary["processed"] += 1
        except Exception as exc:
            try:
                connection.rollback()
            except Exception:
                pass
            unit["status"] = "BLOCKED" if isinstance(exc, MssqlSafetyError) else "QUARANTINED"
            unit["error_type"] = type(exc).__name__
            unit["error"] = _safe_error(exc)
            summary["status"] = "BLOCKED"
            if apply:
                try:
                    _mark_unit(cursor, migration_id, snapshot_id, "QUARANTINED", unit["error"])
                    connection.commit()
                    summary["quarantined"] += 1
                except Exception:
                    connection.rollback()
            summary["units"].append(unit)
            break
        summary["units"].append(unit)
        if (index + 1) % max(batch_size, 1) == 0:
            gate = _capacity_gate(_read_capacity(cursor), log_limit=log_limit)
            if gate["status"] != "PASS":
                summary["status"] = "BLOCKED"
                summary["stop_reason"] = "capacity gate failed between bounded batches"
                break
    if (
        apply
        and summary["status"] == "PASS"
        and summary["verified"] == len(snapshot_ids)
        and summary["selected_is_full_history"]
    ):
        cursor.execute(
            """
            UPDATE stg.MddMacCompactMigration
            SET Status = 'COMPLETE', CompletedAtUtc = SYSUTCDATETIME(),
                LastSnapshotId = %s
            WHERE MigrationId = %s
            """,
            (snapshot_ids[-1] if snapshot_ids else None, str(migration_id)),
        )
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
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)
    result: dict[str, Any] = {
        "status": "BLOCKED",
        "operation": "historical_compact_migration",
        "safety_floor_gib": SAFETY_FLOOR_GIB,
        "collector_gate": _launchd_gate(),
    }
    try:
        if result["collector_gate"].get("status") != "PASS":
            raise MssqlSafetyError("collector stopped/disabled gate is not proven")
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

