#!/usr/bin/env python3
"""Append verified Legacy7 cadence/era evidence after a live SQL review.

The default operation is read-only.  ``--record-oversampling`` is an explicit
append-only write that refuses to run unless the three preserved cutover runs
really contain 21 initial attempts, versus the three historical-equivalent
attempts.  ``--record-corrected-era`` records the first ordinary corrected
heartbeat as a separate active era; it never changes an existing row.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from cologne_transit_collector.domain import MAC_LEGACY7_PRODUCTION_COLLECTION_CONTEXT  # noqa: E402
from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlPersistence,
    MssqlSafetyError,
)
from legacy7_sql_probe import RUN_IDS, run_probe  # noqa: E402


OVERSAMPLING_ERA = "MAC_CUTOVER_VALIDATION_OVERSAMPLING"
LEGACY7_PANEL_VERSION = "MDD_MAC_LEGACY7_EXISTING_V1"
METHODOLOGY_VERSION = "LEGACY7_CADENCE_REVIEW_V1"


def _parse_utc(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _review_login_status(adapter: MssqlPersistence, *, allow_administrator: bool) -> dict[str, Any]:
    status = dict(adapter.runtime_login_status())
    login_name = str(status.get("login_name", ""))
    if not login_name:
        raise MssqlSafetyError("review login identity is unavailable")
    if login_name.casefold() == "sa" and not allow_administrator:
        raise MssqlSafetyError("append-only methodology/era writes require --allow-administrator-write when using sa")
    if any(
        bool(status.get(name))
        for name in (
            "is_sysadmin",
            "is_serveradmin",
            "is_securityadmin",
            "is_db_owner",
            "is_db_datareader",
            "is_db_datawriter",
        )
    ):
        if not (allow_administrator and login_name.casefold() == "sa"):
            raise MssqlSafetyError("review login has a prohibited broad privilege")
    return status


def _as_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _run_shape(probe: dict[str, Any]) -> list[dict[str, Any]]:
    rows = probe.get("affected_runs")
    if not isinstance(rows, list) or len(rows) != len(RUN_IDS):
        raise MssqlSafetyError("live probe did not return exactly the three preserved Mac runs")
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise MssqlSafetyError("live run evidence has an invalid row shape")
        normalized.append(row)
        for key in ("target_audit_count", "initial_attempt_count", "snapshot_count"):
            if int(row.get(key) or 0) != 7:
                raise MssqlSafetyError("preserved run shape is not 7 audits/7 initial attempts/7 snapshots")
    return normalized


def record_oversampling(*, at_utc: datetime, allow_administrator: bool) -> dict[str, Any]:
    probe = run_probe(at_utc=at_utc)
    shape = _run_shape(probe)
    impact = probe.get("provider_impact") or {}
    actual = int(impact.get("actual_initial_attempts") or 0)
    expected = int(impact.get("historical_legacy7_equivalent_initial_attempts") or 0)
    excess = int(impact.get("excess_initial_attempts") or 0)
    if (actual, expected, excess) != (21, 3, 18):
        raise MssqlSafetyError("live audit counts differ from the bounded 21/3/18 oversampling contract")

    adapter = MssqlPersistence(raw_evidence_directory=None)
    login_status = _review_login_status(adapter, allow_administrator=allow_administrator)
    starts = [_as_utc(row.get("StartedAtUtc")) for row in shape if row.get("StartedAtUtc") is not None]
    completes = [_as_utc(row.get("CompletedAtUtc")) for row in shape if row.get("CompletedAtUtc") is not None]
    methodology_result = adapter.record_methodology_evidence_idempotent(
        era_code=OVERSAMPLING_ERA,
        panel_version=LEGACY7_PANEL_VERSION,
        collection_context="MAC_PYTHON_MSSQL_PREPARED",
        methodology_version=METHODOLOGY_VERSION,
        run_ids=RUN_IDS,
        window_start_utc=min(value for value in starts if value is not None) if starts and all(value is not None for value in starts) else None,
        window_end_utc=max(value for value in completes if value is not None) if completes and all(value is not None for value in completes) else None,
        actual_initial_attempts=actual,
        historical_equivalent_initial_attempts=expected,
        excess_initial_attempts=excess,
        evidence={
            "classification": "BOUNDED_CUTOVER_VALIDATION_OVERSAMPLING",
            "approved_legacy7_behavior": "ONE_TARGET_PER_UTC_FIVE_MINUTE_HEARTBEAT",
            "finding": "historical cutover runner expanded the seven-target scope on each heartbeat",
            "excluded_from_cadence_volume_comparisons": True,
            "run_shape": shape,
            "slot_mapping": probe.get("slot_rows"),
            "selector": probe.get("selector"),
            "selector_reconciliation": probe.get("selector_reconciliation"),
            "login_status": login_status,
        },
    )
    return {
        "status": "PASS",
        "operation": "RECORD_OVERSAMPLING_METHODOLOGY",
        "run_ids": list(RUN_IDS),
        "actual_initial_attempts": actual,
        "historical_equivalent_initial_attempts": expected,
        "excess_initial_attempts": excess,
        "methodology_evidence_id": methodology_result["methodology_evidence_id"],
        "recording_action": methodology_result["action"],
        "login_name": login_status.get("login_name"),
        "collection_context_preserved": "MAC_PYTHON_MSSQL_PREPARED",
        "rows_mutated": 0,
        "rows_written": methodology_result["rows_written"],
        "rows_appended": methodology_result["rows_written"],
    }


def record_corrected_era(*, effective_at_utc: datetime, allow_administrator: bool) -> dict[str, Any]:
    probe = run_probe(at_utc=effective_at_utc)
    selected = probe.get("selected_target") or {}
    if not selected.get("sampling_slot") or not selected.get("sampling_target_id"):
        raise MssqlSafetyError("corrected-era boundary lacks a live selected Legacy7 target/slot")
    adapter = MssqlPersistence(raw_evidence_directory=None)
    login_status = _review_login_status(adapter, allow_administrator=allow_administrator)
    era_result = adapter.record_sampling_era_cutover_idempotent(
        era_code=MAC_LEGACY7_PRODUCTION_COLLECTION_CONTEXT,
        panel_version=LEGACY7_PANEL_VERSION,
        effective_at_utc=effective_at_utc,
        timestamp_kind="FIRST_CORRECTED_SCHEDULED_HEARTBEAT",
        is_active=True,
        is_synthetic=False,
    )
    return {
        "status": "PASS",
        "operation": "RECORD_CORRECTED_LEGACY7_ERA",
        "era_code": MAC_LEGACY7_PRODUCTION_COLLECTION_CONTEXT,
        "effective_at_utc": effective_at_utc,
        "sampling_era_id": era_result["sampling_era_id"],
        "recording_action": era_result["action"],
        "selected_target": selected,
        "login_name": login_status.get("login_name"),
        "rows_mutated": 0,
        "rows_written": era_result["rows_written"],
        "rows_appended": era_result["rows_written"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--at", help="UTC instant used for the read-only selector probe")
    parser.add_argument("--record-oversampling", action="store_true")
    parser.add_argument("--record-corrected-era", action="store_true")
    parser.add_argument("--effective-at", help="UTC first corrected scheduled heartbeat instant")
    parser.add_argument(
        "--allow-administrator-write",
        action="store_true",
        help="explicitly permit the one-time append-only write under the configured administrator login",
    )
    args = parser.parse_args(argv)
    if args.record_oversampling and args.record_corrected_era:
        parser.error("choose one append-only write operation")
    try:
        if args.record_oversampling:
            result = record_oversampling(
                at_utc=_parse_utc(args.at),
                allow_administrator=args.allow_administrator_write,
            )
        elif args.record_corrected_era:
            if not args.effective_at:
                parser.error("--record-corrected-era requires --effective-at")
            result = record_corrected_era(
                effective_at_utc=_parse_utc(args.effective_at),
                allow_administrator=args.allow_administrator_write,
            )
        else:
            result = run_probe(at_utc=_parse_utc(args.at))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error_type": type(exc).__name__}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
