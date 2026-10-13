#!/usr/bin/env python3
"""Validate exact compact-envelope parity for bounded real SQL units.

This validator compares every normalized SQL column, every identity, every
NULL/empty value, and duplicate multiplicity.  Counts alone are never used as
the acceptance condition.  Online mode is read-only and uses the explicit
administrator connection path; offline mode accepts a sanitized JSON fixture
with ``expected`` and ``actual`` envelope objects.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from cologne_transit_collector.compact_observation import (  # noqa: E402
    CompactEnvelopeError,
    compare_row_family,
    decode_compact_envelope,
    row_multiset_digest,
)
from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    connect_mssql,
)
from cologne_transit_collector.runtime_configuration import (  # noqa: E402
    MacOSKeychainSecretProvider,
    resolve_administrator_connection_settings,
)
from migrate_historical_compact_observations import (  # noqa: E402
    FAMILIES,
    _dict_row,
    _launchd_gate,
    _normalized_unit,
    _read_unit,
)


def _safe_error(exc: BaseException) -> str:
    return " ".join(str(exc).split())[:800]


def _compare(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> dict[str, Any]:
    results = [
        compare_row_family(
            "source_snapshot",
            [expected["source_snapshot"]],
            [actual["source_snapshot"]],
        )
    ]
    results.extend(
        compare_row_family(family, expected[family], actual[family])
        for family, _table in FAMILIES
    )
    return {
        "status": "PASS" if all(result.passed for result in results) else "FAIL",
        "families": [
            {
                "family": result.family,
                "status": "PASS" if result.passed else "FAIL",
                "expected_count": result.expected_count,
                "actual_count": result.actual_count,
                "expected_digest": result.expected_digest,
                "actual_digest": result.actual_digest,
                "missing_identity_count": len(result.missing_rows),
                "unexpected_identity_count": len(result.unexpected_rows),
                "changed_identity_count": len(result.changed_rows),
                "missing_identities": list(result.missing_rows),
                "unexpected_identities": list(result.unexpected_rows),
            }
            for result in results
        ],
    }


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_offline(expected_path: Path, actual_path: Path) -> dict[str, Any]:
    expected = _load_json(expected_path)
    actual = _load_json(actual_path)
    if isinstance(expected, Mapping) and "expected" in expected:
        expected = expected["expected"]
    if isinstance(actual, Mapping) and "actual" in actual:
        actual = actual["actual"]
    if not isinstance(expected, Mapping) or not isinstance(actual, Mapping):
        raise CompactEnvelopeError("offline parity files must contain envelope objects")
    return {"mode": "OFFLINE", "comparison": _compare(expected, actual)}


def _validate_online(args: argparse.Namespace) -> dict[str, Any]:
    collector_gate = _launchd_gate()
    if collector_gate.get("status") != "PASS":
        raise RuntimeError("collector stopped/disabled gate is not proven")
    settings, _ = resolve_administrator_connection_settings(
        os.environ,
        secret_provider=MacOSKeychainSecretProvider(),
    )
    connection = connect_mssql(settings)
    try:
        cursor = connection.cursor()
        if args.snapshot_id is not None:
            snapshot_ids = [args.snapshot_id]
        else:
            cursor.execute("SELECT SnapshotId FROM stg.MddMacSourceSnapshot ORDER BY SnapshotId")
            snapshot_ids = [int(row[0] if not isinstance(row, Mapping) else row["SnapshotId"]) for row in cursor.fetchall()]
            if args.max_snapshots is not None:
                snapshot_ids = snapshot_ids[: args.max_snapshots]
        units: list[dict[str, Any]] = []
        overall = "PASS"
        for snapshot_id in snapshot_ids:
            raw_snapshot, raw_rows = _read_unit(cursor, snapshot_id)
            expected_snapshot, expected_rows = _normalized_unit(raw_snapshot, raw_rows)
            cursor.execute(
                """
                SELECT EnvelopeJsonGzip, EnvelopeSha256, VerificationStatus,
                       EnvelopeLogicalBytes, EnvelopeCompressedBytes
                FROM stg.MddMacCompactObservationEnvelope
                WHERE SnapshotId = %s
                """,
                (snapshot_id,),
            )
            row = cursor.fetchone()
            if row is None:
                units.append({"snapshot_id": snapshot_id, "status": "MISSING_COMPACT_UNIT"})
                overall = "FAIL"
                continue
            payload = row[0] if not isinstance(row, Mapping) else row["EnvelopeJsonGzip"]
            if isinstance(payload, memoryview):
                payload = payload.tobytes()
            expected_hash = row[1] if not isinstance(row, Mapping) else row["EnvelopeSha256"]
            actual = decode_compact_envelope(bytes(payload), expected_sha256=str(expected_hash))
            comparison = _compare(
                {"source_snapshot": expected_snapshot, **expected_rows},
                actual,
            )
            status = str(row[2] if not isinstance(row, Mapping) else row["VerificationStatus"])
            unit_status = "PASS" if comparison["status"] == "PASS" and status == "VERIFIED" else "FAIL"
            overall = "PASS" if overall == "PASS" and unit_status == "PASS" else "FAIL"
            units.append(
                {
                    "snapshot_id": snapshot_id,
                    "verification_status": status,
                    "stored_logical_bytes": int(row[3] if not isinstance(row, Mapping) else row["EnvelopeLogicalBytes"]),
                    "stored_compressed_bytes": int(row[4] if not isinstance(row, Mapping) else row["EnvelopeCompressedBytes"]),
                    "comparison": comparison,
                    "status": unit_status,
                }
            )
        if not units:
            overall = "FAIL"
        return {
            "mode": "ONLINE_READ_ONLY",
            "status": overall,
            "collector_gate": collector_gate,
            "unit_count": len(units),
            "units": units,
        }
    finally:
        connection.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", type=Path, help="offline expected envelope JSON")
    parser.add_argument("--actual", type=Path, help="offline actual envelope JSON")
    parser.add_argument("--snapshot-id", type=int, help="one production SnapshotId to validate")
    parser.add_argument("--max-snapshots", type=int)
    args = parser.parse_args(argv)
    try:
        if (args.expected is None) != (args.actual is None):
            raise ValueError("--expected and --actual must be supplied together")
        result = (
            validate_offline(args.expected, args.actual)
            if args.expected is not None
            else _validate_online(args)
        )
        if "status" not in result:
            result["status"] = result["comparison"]["status"]
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
        return 0 if result["status"] == "PASS" else 1
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error_type": type(exc).__name__, "error": _safe_error(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

