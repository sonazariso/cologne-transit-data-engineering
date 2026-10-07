#!/usr/bin/env python3
"""Read-only Legacy7 SQL evidence probe.

This command never sends an MDD request and never writes SQL state.  It
captures the live target/slot tables, the exact selector definition, the
deterministic selector result for one supplied UTC instant, and the bounded
three-run cutover audit counts required by the cadence review.
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

from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlConnectionSettings,
    MssqlPersistence,
    connect_mssql,
)


RUN_IDS = (
    "127809b0-70fa-4770-9943-664942be5fc3",
    "83c3f261-5bf0-484e-8de6-b6f0e0724a49",
    "5a7e014c-6266-421c-b402-98c4f1cb89b6",
)


def _parse_utc(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--at must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _rows(cursor: Any) -> list[dict[str, Any]]:
    description = getattr(cursor, "description", None) or ()
    names = [str(column[0]) for column in description]
    rows: list[dict[str, Any]] = []
    for row in cursor.fetchall():
        if isinstance(row, dict):
            rows.append(dict(row))
        elif names:
            rows.append({names[index]: value for index, value in enumerate(row)})
        else:
            rows.append(dict(enumerate(row)))
    return rows


def run_probe(*, at_utc: datetime) -> dict[str, Any]:
    settings = MssqlConnectionSettings.from_environment()
    adapter = MssqlPersistence(settings=settings, raw_evidence_directory=None)
    login_status = adapter.runtime_login_status()
    definition = adapter.legacy7_selector_definition()
    targets = adapter.load_existing_sampling_targets()
    selected = adapter.select_existing_legacy7_target(at_utc=at_utc, targets=targets)
    selector_reconciliation = adapter.reconcile_live_legacy7_selector(targets=targets)

    connection = connect_mssql(settings)
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT SamplingTargetId, StopPointRef, TargetName, NumberOfResults, IsEnabled
            FROM ctl.MddRealtimeSamplingTarget
            ORDER BY SamplingTargetId
            """
        )
        target_rows = _rows(cursor)
        cursor.execute(
            """
            SELECT SamplingSlot, SamplingTargetId
            FROM ctl.MddRealtimeSamplingSlot
            ORDER BY SamplingSlot
            """
        )
        slot_rows = _rows(cursor)
        placeholders = ", ".join("%s" for _ in RUN_IDS)
        cursor.execute(
            f"""
            SELECT r.RunId,
                   r.PanelVersion,
                   r.CollectionContext,
                   r.LogicalDueBucket,
                   r.StartedAtUtc,
                   r.CompletedAtUtc,
                   r.Status,
                   COUNT(DISTINCT a.AuditId) AS target_audit_count,
                   COUNT(DISTINCT CASE WHEN h.AttemptNumber = 1 THEN h.HttpAttemptId END)
                       AS initial_attempt_count,
                   COUNT(DISTINCT h.HttpAttemptId) AS http_attempt_count,
                   COUNT(DISTINCT s.SnapshotId) AS snapshot_count
            FROM ctl.MddMacCollectorRun AS r
            LEFT JOIN ctl.MddMacTargetAudit AS a ON a.RunId = r.RunId
            LEFT JOIN ctl.MddMacHttpAttempt AS h ON h.AuditId = a.AuditId
            LEFT JOIN stg.MddMacSourceSnapshot AS s ON s.AuditId = a.AuditId
            WHERE r.RunId IN ({placeholders})
            GROUP BY r.RunId, r.PanelVersion, r.CollectionContext,
                     r.LogicalDueBucket, r.StartedAtUtc, r.CompletedAtUtc, r.Status
            ORDER BY r.StartedAtUtc, r.RunId
            """,
            RUN_IDS,
        )
        affected_runs = _rows(cursor)
        cursor.execute(
            """
            SELECT COUNT_BIG(*) AS initial_attempt_count
            FROM ctl.MddMacHttpAttempt AS h
            INNER JOIN ctl.MddMacTargetAudit AS a ON a.AuditId = h.AuditId
            WHERE a.RunId IN (%s, %s, %s) AND h.AttemptNumber = 1
            """,
            RUN_IDS,
        )
        initial_count = cursor.fetchone()

    finally:
        connection.close()

    actual_initial_attempts = int(
        (initial_count.get("initial_attempt_count") if isinstance(initial_count, dict) else initial_count[0]) or 0
    )
    expected_initial_attempts = len(RUN_IDS)
    return {
        "status": "PASS",
        "live_sql_server": True,
        "mdd_requests_issued": 0,
        "at_utc": at_utc,
        "logical_due_bucket": int(at_utc.timestamp() // 300),
        "login_status": login_status,
        "target_rows": target_rows,
        "slot_rows": slot_rows,
        "selector": definition,
        "selector_reconciliation": selector_reconciliation,
        "selected_target": {
            "sampling_slot": selected.selected_sampling_slot,
            "sampling_target_id": selected.legacy_sampling_target_id,
            "stop_point_ref": selected.source_parent_station_id,
            "number_of_results": selected.number_of_results,
            "selection_policy_version": selected.selection_policy_version,
        },
        "affected_runs": affected_runs,
        "provider_impact": {
            "actual_initial_attempts": actual_initial_attempts,
            "historical_legacy7_equivalent_initial_attempts": expected_initial_attempts,
            "excess_initial_attempts": actual_initial_attempts - expected_initial_attempts,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--at", help="UTC ISO-8601 instant for read-only selector validation")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(run_probe(at_utc=_parse_utc(args.at)), ensure_ascii=False, sort_keys=True, default=str))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error_type": type(exc).__name__}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
