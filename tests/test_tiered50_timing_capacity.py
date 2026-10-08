from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from cologne_transit_collector.collector import HeartbeatBudget  # noqa: E402
from cologne_transit_collector.manifest import load_default_manifest  # noqa: E402
from cologne_transit_collector.simulation import build_simulation  # noqa: E402
from tiered50_capacity_analysis import (  # noqa: E402
    analyze_worst_case_capacity,
    summarize_durations,
)


class Tiered50TimingAndCapacityTests(unittest.TestCase):
    def test_dispatcher_timing_and_budget_use_post_preflight_clock(self) -> None:
        manifest = load_default_manifest(ROOT)
        logical_time = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
        dispatcher_time = logical_time + timedelta(seconds=120)
        with tempfile.TemporaryDirectory() as directory:
            collector, _client, persistence, _now = build_simulation(
                manifest,
                Path(directory) / "timing.sqlite3",
                at_utc=logical_time,
            )
            try:
                result = collector.run_once(
                    mode="simulate",
                    at_utc=logical_time,
                    execution_now_utc=dispatcher_time,
                    execution_started_at_utc=logical_time,
                    runtime_timing={
                        "preflight_completed_at_utc": logical_time.isoformat().replace("+00:00", "Z"),
                    },
                    run_id="post-preflight-dispatch",
                )
                self.assertEqual(result.status, "COMPLETE")
                self.assertEqual(result.timing["preflight_to_dispatcher_delay_ms"], 120000.0)
                self.assertAlmostEqual(result.timing["initial_remaining_budget_seconds"], 175.0, delta=0.01)
                self.assertEqual(result.timing["launchd_trigger_at_utc"], None)
                self.assertEqual(
                    result.timing["launchd_trigger_time_source"],
                    "NOT_INDEPENDENTLY_OBSERVED",
                )
            finally:
                persistence.close()

    def test_wall_clock_correction_cannot_give_budget_back(self) -> None:
        bucket_end = datetime(2026, 10, 8, 12, 5, tzinfo=timezone.utc)
        wall_values = iter(
            (
                datetime(2026, 10, 8, 12, 1, tzinfo=timezone.utc),
                datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc),
                datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc),
            )
        )
        budget = HeartbeatBudget(
            bucket_end_utc=bucket_end,
            started_at_utc=datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc),
            current_at_utc=datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc),
            monotonic_fn=lambda: 0.0,
            wall_clock_fn=lambda: next(wall_values),
        )
        first = budget.remaining_seconds
        second = budget.remaining_seconds
        self.assertLessEqual(second, first)
        self.assertFalse(budget.can_start(operation_seconds=first + 0.1))

    def test_bulk_skip_audit_is_exact_idempotent_and_unclaimed(self) -> None:
        manifest = load_default_manifest(ROOT)
        logical_time = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            collector, _client, persistence, now = build_simulation(
                manifest,
                Path(directory) / "skip-batch.sqlite3",
                scenario="heartbeat-budget-initial-skip",
                at_utc=logical_time,
            )
            try:
                result = collector.run_once(mode="simulate", at_utc=now, run_id="skip-batch")
                self.assertEqual(result.coverage["skipped"], result.due_count)
                audit_count = persistence._connection.execute(
                    "SELECT COUNT(*) FROM target_audits WHERE collector_run_id = ?",
                    ("skip-batch",),
                ).fetchone()[0]
                claim_count = persistence._connection.execute(
                    "SELECT COUNT(*) FROM target_claims WHERE collector_run_id = ?",
                    ("skip-batch",),
                ).fetchone()[0]
                self.assertEqual(audit_count, result.due_count)
                self.assertEqual(claim_count, 0)
                self.assertEqual(
                    result.timing["budget_skip_audit_batches"][0]["status"],
                    "BATCH_APPLIED_OR_IDEMPOTENT",
                )

                plan = collector.plan(at_utc=logical_time)
                expired = HeartbeatBudget(
                    bucket_end_utc=plan.bucket.end_utc,
                    started_at_utc=logical_time,
                    current_at_utc=plan.bucket.end_utc + timedelta(seconds=1),
                    monotonic_fn=lambda: 0.0,
                )
                _outcomes, status = collector._budget_skipped_outcomes(
                    plan.due_targets,
                    plan.bucket.index,
                    budget=expired,
                    run_id="skip-batch",
                    collection_context="OFFLINE_DEVELOPMENT",
                    logical_time=logical_time,
                )
                self.assertEqual(status, "BATCH_APPLIED_OR_IDEMPOTENT")
                self.assertEqual(
                    persistence._connection.execute(
                        "SELECT COUNT(*) FROM target_audits WHERE collector_run_id = ?",
                        ("skip-batch",),
                    ).fetchone()[0],
                    result.due_count,
                )
            finally:
                persistence.close()

    def test_fixed_parameters_show_worst_case_gap_and_small_samples_are_not_percentiles(self) -> None:
        capacity = analyze_worst_case_capacity(due_targets=19)
        self.assertEqual(capacity["concurrent_batches"], 10)
        self.assertEqual(capacity["worst_case_http_seconds"], 300.0)
        self.assertFalse(capacity["worst_case_full_coverage_possible"])
        self.assertEqual(summarize_durations([10.0, 20.0])["status"], "INSUFFICIENT_EVIDENCE")
        summary = summarize_durations(list(range(1, 21)))
        self.assertEqual(summary["status"], "PASS")
        self.assertIsNotNone(summary["p99"])


if __name__ == "__main__":
    unittest.main()
