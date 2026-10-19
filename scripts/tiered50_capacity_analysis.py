#!/usr/bin/env python3
"""Small, evidence-preserving Tiered50 capacity calculations.

This module intentionally refuses to emit percentile values for a small
sample.  The worst-case concurrency arithmetic is deterministic; latency
percentiles are evidence-derived only.
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


DEFAULT_MAX_CONCURRENCY = 2
DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0
DEFAULT_BUCKET_SECONDS = 300.0
DEFAULT_SAFETY_MARGIN_SECONDS = 5.0
DEFAULT_MINIMUM_PERCENTILE_SAMPLE_COUNT = 20


def analyze_worst_case_capacity(
    *,
    due_targets: int,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    bucket_seconds: float = DEFAULT_BUCKET_SECONDS,
    safety_margin_seconds: float = DEFAULT_SAFETY_MARGIN_SECONDS,
    non_http_overhead_seconds: float = 0.0,
) -> dict[str, Any]:
    """Return the fixed-parameter arithmetic without claiming live coverage."""

    if due_targets < 0 or max_concurrency <= 0:
        raise ValueError("due_targets must be non-negative and max_concurrency must be positive")
    if request_timeout_seconds < 0 or bucket_seconds < 0 or safety_margin_seconds < 0:
        raise ValueError("duration parameters must be non-negative")
    batches = math.ceil(due_targets / max_concurrency) if due_targets else 0
    worst_case_http_seconds = batches * request_timeout_seconds
    usable_seconds = max(0.0, bucket_seconds - safety_margin_seconds)
    total_worst_case_seconds = worst_case_http_seconds + max(0.0, non_http_overhead_seconds)
    return {
        "due_targets": int(due_targets),
        "max_concurrency": int(max_concurrency),
        "request_timeout_seconds": float(request_timeout_seconds),
        "bucket_seconds": float(bucket_seconds),
        "safety_margin_seconds": float(safety_margin_seconds),
        "concurrent_batches": int(batches),
        "worst_case_http_seconds": float(worst_case_http_seconds),
        "non_http_overhead_seconds": float(max(0.0, non_http_overhead_seconds)),
        "total_worst_case_seconds": float(total_worst_case_seconds),
        "usable_seconds_before_overhead": float(usable_seconds),
        "headroom_seconds": float(usable_seconds - total_worst_case_seconds),
        "worst_case_full_coverage_possible": total_worst_case_seconds <= usable_seconds,
        "interpretation": (
            "FIXED_CONFIGURATION_CANNOT_GUARANTEE_FULL_COVERAGE"
            if total_worst_case_seconds > usable_seconds
            else "ARITHMETICALLY_WITHIN_USABLE_BUCKET_BEFORE_UNMEASURED_VARIANCE"
        ),
    }


def _percentile(sorted_values: Sequence[float], fraction: float) -> float:
    if not sorted_values:
        raise ValueError("at least one value is required")
    position = (len(sorted_values) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[lower])
    weight = position - lower
    return float(sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * weight)


def summarize_durations(
    values: Sequence[float],
    *,
    minimum_sample_count: int = DEFAULT_MINIMUM_PERCENTILE_SAMPLE_COUNT,
) -> dict[str, Any]:
    """Summarize a duration series or explicitly mark it insufficient."""

    numeric = sorted(float(value) for value in values)
    if minimum_sample_count <= 0:
        raise ValueError("minimum_sample_count must be positive")
    base: dict[str, Any] = {
        "sample_count": len(numeric),
        "minimum_sample_count": int(minimum_sample_count),
        "maximum": max(numeric) if numeric else None,
        "status": "INSUFFICIENT_EVIDENCE",
        "p50": None,
        "p95": None,
        "p99": None,
    }
    if len(numeric) < minimum_sample_count:
        return base
    base.update(
        {
            "status": "PASS",
            "p50": _percentile(numeric, 0.50),
            "p95": _percentile(numeric, 0.95),
            "p99": _percentile(numeric, 0.99),
        }
    )
    return base


def summarize_duration_fields(
    records: Sequence[Mapping[str, Any]],
    fields: Sequence[str],
    *,
    minimum_sample_count: int = DEFAULT_MINIMUM_PERCENTILE_SAMPLE_COUNT,
) -> dict[str, dict[str, Any]]:
    """Summarize named numeric fields from sanitized timing records."""

    return {
        field: summarize_durations(
            [float(record[field]) for record in records if record.get(field) is not None],
            minimum_sample_count=minimum_sample_count,
        )
        for field in fields
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, help="JSON array of sanitized timing records")
    parser.add_argument("--minimum-sample-count", type=int, default=DEFAULT_MINIMUM_PERCENTILE_SAMPLE_COUNT)
    args = parser.parse_args(argv)
    output: dict[str, Any] = {
        "fixed_capacity": analyze_worst_case_capacity(due_targets=19),
        "percentiles": {},
    }
    if args.records is not None:
        records = json.loads(args.records.read_text(encoding="utf-8"))
        if not isinstance(records, list) or not all(isinstance(record, Mapping) for record in records):
            raise ValueError("--records must contain a JSON array of objects")
        output["percentiles"] = summarize_duration_fields(
            records,
            (
                "startup_delay_ms",
                "dispatcher_claim_duration_ms",
                "network_attempt_duration_ms",
                "response_parsing_duration_ms",
                "snapshot_persistence_duration_ms",
                "target_audit_finalization_duration_ms",
                "run_finalization_duration_ms",
            ),
            minimum_sample_count=args.minimum_sample_count,
        )
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
