from __future__ import annotations

import plistlib
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from cologne_transit_collector.collector import HeartbeatBudget  # noqa: E402
from cologne_transit_collector.domain import COMPLETED_WITH_COVERAGE_GAP  # noqa: E402
from cologne_transit_collector.manifest import load_default_manifest  # noqa: E402
from cologne_transit_collector.mssql_persistence import MssqlConnectionSettings, MssqlPersistence  # noqa: E402
from cologne_transit_collector.quota import InMemoryUsageProvider, QuotaGuard  # noqa: E402
from cologne_transit_collector.schedule import (  # noqa: E402
    bucket_for,
    launchd_wall_clock_schedule,
    validate_launchd_wall_clock_schedule,
)
from cologne_transit_collector.simulation import build_simulation  # noqa: E402


class Tiered50RuntimeCorrectionTests(unittest.TestCase):
    def test_wall_clock_schedule_replaces_relative_interval(self) -> None:
        path = ROOT / "deployment" / "launchd" / "com.cologne-transit-intelligence.mdd-tiered50-collector.plist"
        with path.open("rb") as handle:
            plist = plistlib.load(handle)
        self.assertEqual(plist["StartCalendarInterval"], launchd_wall_clock_schedule())
        self.assertNotIn("StartInterval", plist)
        self.assertEqual(validate_launchd_wall_clock_schedule(plist)["calendar_minutes"], list(range(0, 60, 5)))

        invalid = dict(plist)
        invalid["StartInterval"] = 300
        with self.assertRaises(ValueError):
            validate_launchd_wall_clock_schedule(invalid)

    def test_budget_is_bounded_by_bucket_end_and_safety_margin(self) -> None:
        clock = iter((0.0, 10.0, 10.0))
        budget = HeartbeatBudget(
            bucket_end_utc=datetime(2026, 10, 8, 12, 30, tzinfo=timezone.utc),
            started_at_utc=datetime(2026, 10, 8, 12, 29, tzinfo=timezone.utc),
            monotonic_fn=lambda: next(clock),
        )
        self.assertEqual(budget.budget_seconds, 55.0)
        self.assertTrue(budget.can_start(operation_seconds=30))
        self.assertFalse(budget.can_start(operation_seconds=60))

    def test_skipped_targets_are_a_coverage_gap(self) -> None:
        manifest = load_default_manifest(ROOT)
        with tempfile.TemporaryDirectory() as directory:
            collector, _client, persistence, now = build_simulation(
                manifest,
                Path(directory) / "state.sqlite3",
                scenario="heartbeat-budget-initial-skip",
            )
            try:
                result = collector.run_once(mode="simulate", at_utc=now, run_id="coverage-gap")
                self.assertEqual(result.status, COMPLETED_WITH_COVERAGE_GAP)
                self.assertFalse(result.coverage["coverage_sla_met"])
                self.assertEqual(result.coverage["succeeded"], 0)
                self.assertEqual(result.coverage["skipped"], result.due_count)
                summary = persistence._connection.execute(
                    "SELECT status, summary_json FROM collector_runs WHERE run_id = ?",
                    ("coverage-gap",),
                ).fetchone()
                self.assertEqual(summary["status"], COMPLETED_WITH_COVERAGE_GAP)
            finally:
                persistence.close()

    def test_six_aligned_offline_buckets_preserve_approved_distribution(self) -> None:
        manifest = load_default_manifest(ROOT)
        base = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            collector, _client, persistence, _now = build_simulation(
                manifest,
                Path(directory) / "cycle.sqlite3",
                at_utc=base,
            )
            try:
                results = [
                    collector.run_once(
                        mode="simulate",
                        at_utc=base + timedelta(minutes=5 * offset),
                        run_id=f"cycle-{offset}",
                    )
                    for offset in range(6)
                ]
                self.assertEqual([result.due_count for result in results], [19, 18, 18, 18, 19, 18])
                self.assertTrue(all(result.status == "COMPLETE" for result in results))
                self.assertTrue(all(result.coverage["coverage_sla_met"] for result in results))
                self.assertEqual(sum(result.due_count for result in results), 110)
            finally:
                persistence.close()

    def test_offline_authorization_keeps_injected_timestamp(self) -> None:
        at = datetime(2026, 10, 30, 22, 59, tzinfo=timezone.utc)
        decision = QuotaGuard(InMemoryUsageProvider()).allow_initial(at_utc=at)
        self.assertEqual(decision.authorization_at_utc, "2026-10-30T22:59:00Z")

    def test_live_authorization_clock_does_not_accept_stale_bucket_time(self) -> None:
        authoritative = datetime(2026, 10, 31, 0, 0, 1, tzinfo=timezone.utc)
        persistence = MssqlPersistence(
            settings=MssqlConnectionSettings(host="127.0.0.1", user="u", password="p"),
            authorization_clock_fn=lambda: authoritative,
        )
        stale = datetime(2026, 10, 30, 22, 59, tzinfo=timezone.utc)
        self.assertEqual(persistence._authoritative_authorization_time(stale), authoritative)

    def test_dst_fold_maps_to_distinct_utc_buckets_without_relative_drift(self) -> None:
        zone = ZoneInfo("Europe/Berlin")
        first = datetime(2026, 10, 25, 2, 0, tzinfo=zone, fold=0)
        second = datetime(2026, 10, 25, 2, 0, tzinfo=zone, fold=1)
        self.assertNotEqual(bucket_for(first).index, bucket_for(second).index)
        self.assertEqual(first.astimezone(timezone.utc).minute % 5, 0)
        self.assertEqual(second.astimezone(timezone.utc).minute % 5, 0)


if __name__ == "__main__":
    unittest.main()
