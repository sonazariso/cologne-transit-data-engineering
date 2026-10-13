#!/usr/bin/env python3
"""Calculate the Tiered50 seven-day physical-capacity gates.

This calculator never changes SQL Server, the collector, the sampling guard,
or the target panel.  Pass measured *total SQL allocation* per successful
snapshot when available; logical GZIP lengths alone are deliberately not
accepted as physical allocation evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.compact_observation import capacity_projection


REQUESTS_PER_HOUR = 220
HOURS_PER_DAY = 24
DAYS_PER_WEEK = 7
WEEKLY_BUDGET_GIB = 10.0
WINDOWS_RESERVE_GIB = 8.0
SAFETY_FLOOR_GIB = 12.0
DEFAULT_GUARD_BASELINE_GIB = 21.62
DEFAULT_GUARD_MAX_CUMULATIVE_GROWTH_GIB = 17.0


def _measurement_from_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("measurement JSON must be an object")
    return dict(value)


def calculate(args: argparse.Namespace) -> dict[str, Any]:
    measurement: dict[str, Any] = {}
    if args.measurement_json:
        measurement = _measurement_from_json(args.measurement_json)
    bytes_per_snapshot = args.bytes_per_snapshot
    if bytes_per_snapshot is None:
        bytes_per_snapshot = measurement.get("physical_bytes_per_successful_snapshot")
    if bytes_per_snapshot is None:
        bytes_per_snapshot = measurement.get("bytes_per_successful_snapshot")
    if bytes_per_snapshot is None:
        raise ValueError("measured physical bytes per successful snapshot are required")
    if isinstance(bytes_per_snapshot, bool) or not isinstance(bytes_per_snapshot, int):
        raise ValueError("bytes per snapshot must be an integer")
    if bytes_per_snapshot <= 0:
        raise ValueError("bytes per snapshot must be positive")
    measurement_basis = str(
        getattr(args, "measurement_basis", None)
        or measurement.get("measurement_basis")
        or ""
    ).strip()
    physical_allocation_measured = (
        measurement.get("physical_allocation_measured") is True
        if args.measurement_json
        else bool(getattr(args, "measurement_basis", None))
    )
    physical_allocation_gate = (
        physical_allocation_measured
        and "sql" in measurement_basis.casefold()
        and "physical" in measurement_basis.casefold()
        and "allocation" in measurement_basis.casefold()
    )
    projection = capacity_projection(
        requests_per_hour=REQUESTS_PER_HOUR,
        hours=HOURS_PER_DAY * DAYS_PER_WEEK,
        bytes_per_snapshot=bytes_per_snapshot,
        weekly_budget_gib=WEEKLY_BUDGET_GIB,
        windows_free_gib=float(args.windows_free_gib),
        windows_reserve_gib=WINDOWS_RESERVE_GIB,
        operational_floor_gib=SAFETY_FLOOR_GIB,
    )
    weekly_requests = int(projection["requests"])
    day = capacity_projection(
        requests_per_hour=REQUESTS_PER_HOUR,
        hours=HOURS_PER_DAY,
        bytes_per_snapshot=bytes_per_snapshot,
        weekly_budget_gib=WEEKLY_BUDGET_GIB,
        windows_free_gib=float(args.windows_free_gib),
        windows_reserve_gib=WINDOWS_RESERVE_GIB,
        operational_floor_gib=SAFETY_FLOOR_GIB,
    )
    guard_remaining = None
    if args.current_allocation_gib is not None:
        # Keep a negative allowance visible. Clamping it to zero would hide
        # that the existing seven-day guard has already been exceeded.
        guard_remaining = float(args.guard_max_cumulative_growth_gib) - (
            float(args.current_allocation_gib) - float(args.guard_baseline_gib)
        )
    guard_gate = guard_remaining is None or projection["growth_gib"] <= guard_remaining
    return {
        "status": "PASS"
        if (
            physical_allocation_gate
            and projection["weekly_budget_pass"]
            and projection["windows_reserve_pass"]
            and projection["operational_floor_pass"]
            and guard_gate
        )
        else "NO_GO",
        "requests_per_hour": REQUESTS_PER_HOUR,
        "requests_per_day": REQUESTS_PER_HOUR * HOURS_PER_DAY,
        "requests_per_seven_days": weekly_requests,
        "physical_bytes_per_successful_snapshot": bytes_per_snapshot,
        "physical_kib_per_successful_snapshot": bytes_per_snapshot / 1024.0,
        "per_request_budget_kib": projection["per_request_budget_bytes"] / 1024.0,
        "projected_one_day_growth_gib": day["growth_gib"],
        "projected_seven_day_growth_gib": projection["growth_gib"],
        "weekly_budget_gib": WEEKLY_BUDGET_GIB,
        "windows_free_before_gib": float(args.windows_free_gib),
        "windows_free_after_seven_days_gib": projection["windows_free_after_gib"],
        "windows_reserve_gib": WINDOWS_RESERVE_GIB,
        "conservative_operational_floor_gib": SAFETY_FLOOR_GIB,
        "operational_floor_gate": projection["operational_floor_pass"],
        "weekly_budget_gate": projection["weekly_budget_pass"],
        "windows_reserve_gate": projection["windows_reserve_pass"],
        "physical_allocation_gate": physical_allocation_gate,
        "original_guard_remaining_gib": guard_remaining,
        "original_guard_allowance_gate": guard_gate,
        "guard_baseline_gib": float(args.guard_baseline_gib),
        "guard_max_cumulative_growth_gib": float(args.guard_max_cumulative_growth_gib),
        "guard_remaining_gib": guard_remaining,
        "measurement_basis": measurement_basis or "NOT_PROVEN",
        "physical_allocation_measured": physical_allocation_measured,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bytes-per-snapshot", type=int)
    parser.add_argument("--measurement-json", type=Path)
    parser.add_argument(
        "--measurement-basis",
        help="explicit SQL physical-allocation evidence basis for direct byte input",
    )
    parser.add_argument("--windows-free-gib", type=float, required=True)
    parser.add_argument("--current-allocation-gib", type=float)
    parser.add_argument("--guard-baseline-gib", type=float, default=DEFAULT_GUARD_BASELINE_GIB)
    parser.add_argument("--guard-max-cumulative-growth-gib", type=float, default=DEFAULT_GUARD_MAX_CUMULATIVE_GROWTH_GIB)
    args = parser.parse_args(argv)
    try:
        result = calculate(args)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["status"] == "PASS" else 1
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED", "error_type": type(exc).__name__, "error": str(exc)[:500]}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

