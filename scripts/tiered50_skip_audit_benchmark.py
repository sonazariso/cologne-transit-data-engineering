#!/usr/bin/env python3
"""Offline benchmark for the late-bucket skip-audit persistence path.

The sequential case represents the former start-audit/complete-audit shape and
is run only against disposable SQLite state. It is not a production fallback:
the current collector never creates retroactive target claims for a late
pending target.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.manifest import load_default_manifest  # noqa: E402
from cologne_transit_collector.persistence import SQLitePersistence  # noqa: E402
from cologne_transit_collector.schedule import bucket_for  # noqa: E402


def _outcome() -> dict[str, Any]:
    return {
        "status": "SKIPPED",
        "http_attempts": 0,
        "skip_reason": "SKIPPED_HEARTBEAT_BUDGET",
        "error_category": "INSUFFICIENT_BUDGET",
        "duration_seconds": 0.0,
        "timing": {"target_started": False},
    }


def _prepare(path: Path, run_id: str) -> tuple[SQLitePersistence, tuple[Any, ...], int, datetime]:
    manifest = load_default_manifest(ROOT)
    observed_at = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
    plan_bucket = bucket_for(observed_at)
    persistence = SQLitePersistence(path)
    persistence.materialize_manifest(manifest)
    persistence.start_collector_run(
        run_id=run_id,
        panel_version=manifest.panel_version,
        logical_due_bucket=plan_bucket.index,
        mode="simulate",
        started_at=observed_at,
    )
    return persistence, plan_bucket_for_targets(manifest.targets, plan_bucket.index), plan_bucket.index, observed_at


def plan_bucket_for_targets(targets: tuple[Any, ...], bucket_index: int) -> tuple[Any, ...]:
    from cologne_transit_collector.schedule import due_targets

    return tuple(due_targets(targets, bucket_index))


def run_benchmark() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        batch_persistence, targets, bucket_index, observed_at = _prepare(
            Path(directory) / "batch.sqlite3", "benchmark-batch"
        )
        try:
            batch_entries = tuple(
                {
                    "target": target,
                    "outcome": _outcome(),
                    "started_at": observed_at,
                    "completed_at": observed_at,
                }
                for target in targets
            )
            started = time.perf_counter()
            batch_persistence.persist_skipped_target_audits(
                run_id="benchmark-batch",
                logical_due_bucket=bucket_index,
                skipped_audits=batch_entries,
                collection_context="OFFLINE_DEVELOPMENT",
            )
            batch_ms = (time.perf_counter() - started) * 1000.0
            batch_rows = int(
                batch_persistence._connection.execute(
                    "SELECT COUNT(*) FROM target_audits WHERE collector_run_id = ?",
                    ("benchmark-batch",),
                ).fetchone()[0]
            )
            batch_claims = int(
                batch_persistence._connection.execute(
                    "SELECT COUNT(*) FROM target_claims WHERE collector_run_id = ?",
                    ("benchmark-batch",),
                ).fetchone()[0]
            )
        finally:
            batch_persistence.close()

        sequential_persistence, targets, bucket_index, observed_at = _prepare(
            Path(directory) / "sequential.sqlite3", "benchmark-sequential"
        )
        try:
            started = time.perf_counter()
            for target in targets:
                audit_id = sequential_persistence.start_target_audit(
                    run_id="benchmark-sequential",
                    target=target,
                    logical_due_bucket=bucket_index,
                    started_at=observed_at,
                )
                sequential_persistence.complete_target_audit(
                    audit_id=audit_id,
                    outcome=_outcome(),
                    completed_at=observed_at,
                )
            sequential_ms = (time.perf_counter() - started) * 1000.0
            sequential_rows = int(
                sequential_persistence._connection.execute(
                    "SELECT COUNT(*) FROM target_audits WHERE collector_run_id = ?",
                    ("benchmark-sequential",),
                ).fetchone()[0]
            )
        finally:
            sequential_persistence.close()

    return {
        "status": "PASS",
        "target_count": len(targets),
        "batch": {
            "duration_ms": batch_ms,
            "audit_rows": batch_rows,
            "target_claim_rows": batch_claims,
            "transaction_shape": "ONE_BATCH_TRANSACTION_NO_TARGET_CLAIMS",
        },
        "former_sequential_shape": {
            "duration_ms": sequential_ms,
            "audit_rows": sequential_rows,
            "transaction_shape": "ONE_START_AND_ONE_COMPLETE_TRANSACTION_PER_TARGET",
        },
        "speedup_ratio": sequential_ms / batch_ms if batch_ms > 0 else None,
        "network_requests_issued": 0,
        "interpretation": "DISPOSABLE_SQLITE_OVERHEAD_COMPARISON_NOT_PRODUCTION_LATENCY_EVIDENCE",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = run_benchmark()
    encoded = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
