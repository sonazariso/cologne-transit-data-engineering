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
from cologne_transit_collector.domain import (  # noqa: E402
    COMPLETED_WITH_COVERAGE_GAP,
    MAC_TIERED50_PRODUCTION_COLLECTION_CONTEXT,
)
from cologne_transit_collector.manifest import load_default_manifest  # noqa: E402
from cologne_transit_collector.macos_runner import main as macos_runner_main  # noqa: E402
from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlConnectionSettings,
    MssqlPersistence,
    MssqlSafetyError,
)
from cologne_transit_collector.quota import InMemoryUsageProvider, QuotaGuard  # noqa: E402
from cologne_transit_collector.schedule import (  # noqa: E402
    bucket_for,
    launchd_wall_clock_schedule,
    validate_launchd_wall_clock_schedule,
)
from cologne_transit_collector.simulation import build_simulation  # noqa: E402


class _ClockCursor:
    def __init__(self, value=None, failure: bool = False) -> None:
        self.value = value
        self.failure = failure

    def execute(self, statement, params=None):
        if self.failure:
            raise RuntimeError("simulated SQL clock failure")

    def fetchone(self):
        return {"AuthoritativeUtc": self.value}


class _ClockConnection:
    def __init__(self, value=None, failure: bool = False) -> None:
        self.cursor_instance = _ClockCursor(value=value, failure=failure)
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def rollback(self):
        pass

    def close(self):
        self.closed = True


class _ExpiringPermitProvider(InMemoryUsageProvider):
    def __init__(self, valid_until: datetime) -> None:
        super().__init__()
        self.valid_until = valid_until

    def reserve_quota_attempt(self, **kwargs):
        result = dict(super().reserve_quota_attempt(**kwargs))
        if result.get("allowed"):
            result["authorization_at_utc"] = "2026-10-08T12:00:00Z"
            result["valid_until_utc"] = self.valid_until.isoformat().replace("+00:00", "Z")
        return result


class Tiered50RuntimeCorrectionTests(unittest.TestCase):
    def test_wall_clock_schedule_replaces_relative_interval(self) -> None:
        path = ROOT / "deployment" / "launchd" / "com.cologne-transit-intelligence.mdd-tiered50-collector.plist"
        with path.open("rb") as handle:
            plist = plistlib.load(handle)
        self.assertEqual(plist["StartCalendarInterval"], launchd_wall_clock_schedule())
        self.assertNotIn("StartInterval", plist)
        self.assertNotIn("ThrottleInterval", plist)
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

    def test_budget_deducts_slow_preflight_from_absolute_bucket_deadline(self) -> None:
        process_started = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
        preflight_completed = datetime(2026, 10, 8, 12, 2, tzinfo=timezone.utc)
        budget = HeartbeatBudget(
            bucket_end_utc=datetime(2026, 10, 8, 12, 5, tzinfo=timezone.utc),
            started_at_utc=process_started,
            current_at_utc=preflight_completed,
            execution_started_at_utc=process_started,
            monotonic_fn=lambda: 120.0,
            monotonic_origin=0.0,
        )
        self.assertEqual(budget.budget_seconds, 175.0)
        self.assertEqual(budget.elapsed_seconds, 120.0)
        self.assertTrue(budget.can_start(operation_seconds=30))
        self.assertFalse(budget.can_start(operation_seconds=180))

    def test_expired_bucket_cannot_be_reopened_by_a_new_budget_object(self) -> None:
        end = datetime(2026, 10, 8, 12, 5, tzinfo=timezone.utc)
        expired = datetime(2026, 10, 8, 12, 6, tzinfo=timezone.utc)
        budget = HeartbeatBudget(
            bucket_end_utc=end,
            started_at_utc=datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc),
            current_at_utc=expired,
            monotonic_fn=lambda: 10.0,
        )
        self.assertEqual(budget.budget_seconds, 0.0)
        self.assertFalse(budget.can_start(operation_seconds=1))

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
            collection_context="OFFLINE_DEVELOPMENT",
        )
        stale = datetime(2026, 10, 30, 22, 59, tzinfo=timezone.utc)
        self.assertEqual(persistence._authoritative_authorization_time(stale), authoritative)

    def test_sql_clock_failure_denies_authorization_without_local_fallback(self) -> None:
        persistence = MssqlPersistence(
            settings=MssqlConnectionSettings(host="127.0.0.1", user="u", password="p"),
            connection_factory=lambda: _ClockConnection(failure=True),
        )
        with self.assertRaisesRegex(MssqlSafetyError, "authoritative SQL UTC time is unavailable"):
            persistence._authoritative_authorization_time(datetime(2026, 10, 8, tzinfo=timezone.utc))

    def test_production_rejects_injected_authorization_clock(self) -> None:
        with self.assertRaisesRegex(MssqlSafetyError, "isolated offline context"):
            MssqlPersistence(
                settings=MssqlConnectionSettings(host="127.0.0.1", user="u", password="p"),
                authorization_clock_fn=lambda: datetime(2026, 10, 8, tzinfo=timezone.utc),
            )

    def test_sql_clock_disagreement_wins_over_caller_timestamp(self) -> None:
        authoritative = datetime(2026, 10, 8, 12, 0, 1, tzinfo=timezone.utc)
        persistence = MssqlPersistence(
            settings=MssqlConnectionSettings(host="127.0.0.1", user="u", password="p"),
            connection_factory=lambda: _ClockConnection(authoritative),
        )
        stale = datetime(2026, 10, 8, 11, 0, tzinfo=timezone.utc)
        self.assertEqual(persistence._authoritative_authorization_time(stale), authoritative)

    def test_permit_expiry_recheck_releases_without_transmission(self) -> None:
        now = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
        authoritative_after_expiry = datetime(2026, 10, 8, 12, 0, 31, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            collector, client, persistence, _simulation_now = build_simulation(
                load_default_manifest(ROOT),
                Path(directory) / "expiry.sqlite3",
                at_utc=now,
            )
            try:
                collector.wall_clock_fn = lambda: now
                collector.monotonic_fn = lambda: 0.0
                provider = _ExpiringPermitProvider(
                    datetime(2026, 10, 8, 12, 0, 30, tzinfo=timezone.utc)
                )
                collector.quota_guard = QuotaGuard(provider)
                persistence.authoritative_utc_now = lambda: authoritative_after_expiry
                plan = collector.plan(at_utc=now)
                run_id = "expiry-recheck"
                persistence.start_collector_run(
                    run_id=run_id,
                    panel_version=collector.manifest.panel_version,
                    logical_due_bucket=plan.bucket.index,
                    mode="mac-tiered50",
                    started_at=now,
                    collection_context=MAC_TIERED50_PRODUCTION_COLLECTION_CONTEXT,
                )
                persistence.claim_dispatcher_bucket(
                    run_id=run_id,
                    panel_version=collector.manifest.panel_version,
                    logical_due_bucket=plan.bucket.index,
                    claimed_at=now,
                )
                target = plan.due_targets[0]
                claim = persistence.claim_target(
                    run_id=run_id,
                    target=target,
                    logical_due_bucket=plan.bucket.index,
                    claimed_at=now,
                )
                self.assertTrue(claim.won)
                outcome = collector._execute_target(
                    run_id,
                    target,
                    plan.bucket.index,
                    HeartbeatBudget(
                        bucket_end_utc=plan.bucket.end_utc,
                        started_at_utc=now,
                        current_at_utc=now,
                        monotonic_fn=lambda: 0.0,
                    ),
                    now,
                    MAC_TIERED50_PRODUCTION_COLLECTION_CONTEXT,
                    target_claimed_at_utc=now,
                )
                self.assertEqual(outcome.status, "SKIPPED")
                self.assertEqual(outcome.error_category, "QUOTA_EXPIRED_BEFORE_NETWORK")
                self.assertEqual(client.call_count(), 0)
            finally:
                persistence.close()

    def test_live_runner_rejects_at_before_resolving_production_credentials(self) -> None:
        self.assertEqual(
            macos_runner_main(["--mode", "tiered50", "--at", "2026-10-08T12:00:00Z"]),
            2,
        )

    def test_dst_fold_maps_to_distinct_utc_buckets_without_relative_drift(self) -> None:
        zone = ZoneInfo("Europe/Berlin")
        first = datetime(2026, 10, 25, 2, 0, tzinfo=zone, fold=0)
        second = datetime(2026, 10, 25, 2, 0, tzinfo=zone, fold=1)
        self.assertNotEqual(bucket_for(first).index, bucket_for(second).index)
        self.assertEqual(first.astimezone(timezone.utc).minute % 5, 0)
        self.assertEqual(second.astimezone(timezone.utc).minute % 5, 0)


if __name__ == "__main__":
    unittest.main()
