from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from cologne_transit_collector.collector import Collector, LiveModeBlocked, heartbeat_budget_decision
from cologne_transit_collector.domain import (
    APPROVED_TRANSIENT_HTTP_STATUS_CODES,
    MAX_ATTEMPTS,
    MAXIMUM_CONCURRENCY,
    RETRY_SKIPPED_HEARTBEAT_BUDGET,
    RETRY_SKIPPED_QUOTA,
    SKIPPED_HEARTBEAT_BUDGET,
    HttpResponse,
    TransportFailure,
)
from cologne_transit_collector.manifest import LEGACY7_IDS, ManifestValidationError, load_default_manifest
from cologne_transit_collector.persistence import SQLitePersistence
from cologne_transit_collector.quota import InMemoryUsageProvider, QuotaGuard, UsageSnapshot
from cologne_transit_collector.request import build_request_body
from cologne_transit_collector.schedule import (
    bucket_for,
    due_targets,
    logical_due_bucket,
    may_execute_bucket,
    schedule_reconciliation,
)
from cologne_transit_collector.simulation import build_simulation, run_simulation
from cologne_transit_collector.transport import FakeMddClient, LiveMddClient
from cologne_transit_collector.trias import parse_trias_response


FIXTURE = REPOSITORY_ROOT / "tests" / "fixtures" / "stage14_trias_success.json"
TEST_AT = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


class Stage14ManifestAndScheduleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_default_manifest(REPOSITORY_ROOT)

    def test_manifest_has_exact_panel_split_and_persisted_phases(self) -> None:
        self.assertEqual(len(self.manifest.targets), 50)
        self.assertEqual(len({target.source_parent_station_id for target in self.manifest.targets}), 50)
        self.assertEqual(len(self.manifest.legacy7_targets), 7)
        self.assertEqual(len(self.manifest.new_tiered50_targets), 43)
        self.assertEqual({target.source_parent_station_id for target in self.manifest.legacy7_targets}, LEGACY7_IDS)
        self.assertTrue(all(target.production_sampling_target_id is None for target in self.manifest.new_tiered50_targets))
        self.assertTrue(all(target.production_sampling_target_id is None for target in self.manifest.legacy7_targets))

    def test_manifest_rejects_phase_mutation(self) -> None:
        payload = json.loads(self.manifest.source_path.read_text(encoding="utf-8"))
        payload["targets"][0]["phase_index"] = 99
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ManifestValidationError):
                type(self.manifest).load(path, stage13_evidence_path=self.manifest.stage13_evidence_path)

    def test_schedule_reconciles_exactly_and_is_independent_of_target_order(self) -> None:
        reconciliation = schedule_reconciliation(self.manifest.targets)
        self.assertEqual(reconciliation["super_cycle_counts"], (19, 18, 18, 18, 19, 18))
        self.assertEqual(reconciliation["super_cycle_total"], 110)
        self.assertEqual(reconciliation["requests_per_hour"], 220)
        reversed_targets = tuple(reversed(self.manifest.targets))
        for offset, expected in enumerate((19, 18, 18, 18, 19, 18)):
            self.assertEqual(len(due_targets(reversed_targets, 5969232 + offset)), expected)

    def test_utc_bucket_is_restart_safe_and_has_no_catch_up(self) -> None:
        first = datetime(2026, 10, 25, 2, 55, tzinfo=timezone(timedelta(hours=2)))
        second = datetime(2026, 10, 25, 0, 55, tzinfo=timezone.utc)
        self.assertEqual(logical_due_bucket(first), logical_due_bucket(second))
        bucket = bucket_for(TEST_AT)
        self.assertTrue(may_execute_bucket(bucket.index, bucket.index))
        self.assertFalse(may_execute_bucket(bucket.index, bucket.index - 1))


class Stage14ParserAndRequestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_default_manifest(REPOSITORY_ROOT)
        cls.target = cls.manifest.target_by_id("de:05315:11201")
        cls.body = FIXTURE.read_bytes()

    def test_request_is_approved_post_body_without_secret(self) -> None:
        body = build_request_body(self.target, request_timestamp_utc=TEST_AT)
        text = body.decode("utf-8")
        self.assertIn('version="1.2"', text)
        self.assertIn("<StopPointRef>de:05315:11201</StopPointRef>", text)
        self.assertIn("<NumberOfResults>30</NumberOfResults>", text)
        self.assertIn("<StopEventType>arrival</StopEventType>", text)
        self.assertIn("<IncludePreviousCalls>true</IncludePreviousCalls>", text)
        self.assertNotIn("MDD_API_KEY", text)

    def test_parser_preserves_nulls_and_does_not_invent_actual_delay(self) -> None:
        parsed = parse_trias_response(self.body, "application/json")
        self.assertEqual(parsed.result_count, 1)
        event = parsed.events[0]
        this_call = event["this_call"]
        self.assertEqual(this_call["arrival"]["timetabled_time"], "2026-09-30T12:05:00Z[GMT]")
        self.assertIsNone(this_call["arrival"]["estimated_time"])
        self.assertIsNone(this_call["arrival"]["recorded_at_time"])
        self.assertEqual(event["call_scope_counts"], {"previous": 1, "this": 1, "onward": 1})
        self.assertNotIn("actual_delay", event)
        self.assertNotIn("physical_delay", event)
        self.assertFalse(parsed.to_dict()["raw_payload_persisted"])

    def test_live_client_reads_secret_only_when_fetch_is_called(self) -> None:
        seen = {}

        class Response:
            status = 200
            headers = {"Content-Type": "application/json"}

            def __init__(self) -> None:
                self._read = False

            def read(self, _size: int) -> bytes:
                if self._read:
                    return b""
                self._read = True
                return self_body

            def close(self) -> None:
                pass

        self_body = self.body

        def fake_urlopen(request, timeout):
            seen["method"] = request.method
            seen["headers"] = dict(request.header_items())
            seen["timeout"] = timeout
            seen["body"] = request.data
            return Response()

        client = LiveMddClient("https://example.invalid", urlopen_fn=fake_urlopen, enabled=True)
        with patch.dict(os.environ, {"MDD_API_KEY": "unit-test-secret"}, clear=False):
            response = client.fetch(self.target, request_timestamp_utc=TEST_AT, timeout_seconds=30, attempt_number=1)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["timeout"], 30.0)
        self.assertIn("X-api-key", seen["headers"])
        self.assertEqual(seen["headers"]["X-api-key"], "unit-test-secret")
        self.assertNotIn("unit-test-secret", seen["body"].decode("utf-8"))


class Stage14QuotaAndOfflineExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_default_manifest(REPOSITORY_ROOT)

    def run_scenario(self, scenario: str) -> tuple[dict, SQLitePersistence, object]:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "state.sqlite3"
        collector, client, persistence, now = build_simulation(self.manifest, path, scenario=scenario, at_utc=TEST_AT)
        result = collector.run_once(mode="simulate", at_utc=now, run_id=f"test-{scenario}")
        self.addCleanup(persistence.close)
        return result.to_dict(), persistence, client

    def test_success_and_concurrency_limit(self) -> None:
        result, persistence, client = self.run_scenario("all-success")
        self.assertEqual(result["due_count"], 19)
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["http_attempt_count"], 19)
        self.assertLessEqual(result["max_observed_concurrency"], MAXIMUM_CONCURRENCY)
        self.assertEqual(len(client.calls), 19)
        self.assertTrue(all(row["http_attempts"] <= MAX_ATTEMPTS for row in persistence.fetch_target_audits()))

    def test_retry_success_and_retry_exhaustion(self) -> None:
        success, _persistence, client = self.run_scenario("http-500-then-success")
        first = next(item for item in success["outcomes"] if item["target_id"] == "de:05315:11110")
        self.assertEqual(first["http_attempts"], 2)
        self.assertEqual(first["status"], "SUCCEEDED")
        self.assertEqual(len(client.calls), 20)
        failed, _persistence, client = self.run_scenario("http-500-twice")
        first = next(item for item in failed["outcomes"] if item["target_id"] == "de:05315:11110")
        self.assertEqual(first["http_attempts"], 2)
        self.assertEqual(first["status"], "FAILED")
        self.assertEqual(len(client.calls), 20)
        self.assertEqual(sum(item["status"] == "SUCCEEDED" for item in failed["outcomes"]), 18)

    def test_401_403_and_parser_failure_are_not_retried(self) -> None:
        for scenario in ("http-401", "http-403", "parser-failure"):
            with self.subTest(scenario=scenario):
                result, _persistence, client = self.run_scenario(scenario)
                first = result["outcomes"][0]
                self.assertEqual(first["http_attempts"], 1)
                self.assertEqual(client.call_count("de:05315:11110"), 1)

    def test_retry_after_budget_and_transport_timeout(self) -> None:
        fitting, _persistence, client = self.run_scenario("http-429-retry-after-fits")
        first = next(item for item in fitting["outcomes"] if item["target_id"] == "de:05315:11110")
        self.assertEqual(first["status"], "SUCCEEDED")
        self.assertEqual(first["http_attempts"], 2)
        exceeding, _persistence, client = self.run_scenario("http-429-retry-after-exceeds-budget")
        first = next(item for item in exceeding["outcomes"] if item["target_id"] == "de:05315:11110")
        self.assertEqual(first["status"], "SKIPPED")
        self.assertEqual(first["retry_outcome"], RETRY_SKIPPED_HEARTBEAT_BUDGET)
        self.assertEqual(client.call_count("de:05315:11110"), 1)
        timed_out, _persistence, client = self.run_scenario("transport-timeout")
        first = next(item for item in timed_out["outcomes"] if item["target_id"] == "de:05315:11110")
        self.assertEqual(first["http_attempts"], 2)
        self.assertEqual(first["status"], "SUCCEEDED")

    def test_quota_boundary_and_shared_consumer_state(self) -> None:
        result, _persistence, client = self.run_scenario("quota-retry-denied")
        first = next(item for item in result["outcomes"] if item["target_id"] == "de:05315:11110")
        self.assertEqual(first["http_attempts"], 1)
        self.assertEqual(first["retry_outcome"], RETRY_SKIPPED_QUOTA)
        self.assertEqual(client.call_count("de:05315:11110"), 1)
        provider = InMemoryUsageProvider(UsageSnapshot(actual_attempts=207_500, reserved_shared_usage=1))
        guard = QuotaGuard(provider)
        self.assertFalse(guard.allow_retry().allowed)
        self.assertTrue(guard.allow_initial().allowed)
        unavailable = QuotaGuard(InMemoryUsageProvider(UsageSnapshot(state_available=False)))
        self.assertFalse(unavailable.allow_retry().allowed)

    def test_initial_and_retry_heartbeat_budget_outcomes(self) -> None:
        initial, _persistence, client = self.run_scenario("heartbeat-budget-initial-skip")
        self.assertTrue(all(item["skip_reason"] == SKIPPED_HEARTBEAT_BUDGET for item in initial["outcomes"]))
        self.assertEqual(len(client.calls), 0)
        retry, _persistence, client = self.run_scenario("heartbeat-budget-retry-skip")
        first = next(item for item in retry["outcomes"] if item["target_id"] == "de:05315:11110")
        self.assertEqual(first["retry_outcome"], RETRY_SKIPPED_HEARTBEAT_BUDGET)
        self.assertEqual(client.call_count("de:05315:11110"), 1)
        self.assertFalse(heartbeat_budget_decision(25)["start_allowed"])
        self.assertEqual(heartbeat_budget_decision(25)["outcome"], SKIPPED_HEARTBEAT_BUDGET)

    def test_duplicate_and_concurrent_duplicate_claims_use_zero_duplicate_http(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.sqlite3"
            collector, client, persistence, now = build_simulation(self.manifest, path, at_utc=TEST_AT)
            first = collector.run_once(mode="simulate", at_utc=now, run_id="first")
            second = collector.run_once(mode="simulate", at_utc=now, run_id="second")
            self.assertEqual(first.http_attempt_count, 19)
            self.assertEqual(second.status, "DUPLICATE_NO_WORK")
            self.assertEqual(len(client.calls), 19)
            self.assertEqual(sum(event["outcome"] == "DUPLICATE_CLAIM" for event in persistence.fetch_claim_events()), 19)
            persistence.close()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "concurrent.sqlite3"
            collector, client, persistence, now = build_simulation(self.manifest, path, scenario="concurrent-duplicate", at_utc=TEST_AT)
            results: list[object] = []

            def invoke(run_id: str) -> None:
                results.append(collector.run_once(mode="simulate", at_utc=now, run_id=run_id))

            threads = [threading.Thread(target=invoke, args=(f"run-{i}",)) for i in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(len(client.calls), 19)
            self.assertEqual(sum(item.status == "COMPLETE" for item in results), 1)
            persistence.close()

    def test_restart_durable_claim_and_stale_visibility(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "restart.sqlite3"
            persistence = SQLitePersistence(path)
            target = self.manifest.targets[0]
            bucket = bucket_for(TEST_AT)
            persistence.start_collector_run(run_id="crashed", panel_version=self.manifest.panel_version, logical_due_bucket=bucket.index, mode="simulate", started_at=TEST_AT)
            first = persistence.claim_target(run_id="crashed", target=target, logical_due_bucket=bucket.index, claimed_at=TEST_AT)
            self.assertTrue(first.won)
            persistence.close()
            restarted = SQLitePersistence(path)
            restarted.start_collector_run(run_id="restart", panel_version=self.manifest.panel_version, logical_due_bucket=bucket.index, mode="simulate", started_at=TEST_AT)
            duplicate = restarted.claim_target(run_id="restart", target=target, logical_due_bucket=bucket.index, claimed_at=TEST_AT)
            self.assertFalse(duplicate.won)
            self.assertEqual(len(restarted.fetch_claims()), 1)
            stale = restarted.list_stale_claims(now_utc=datetime(2026, 10, 1, tzinfo=timezone.utc))
            self.assertEqual(len(stale), 1)
            restarted.close()

    def test_crash_scenario_and_failure_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = run_simulation(self.manifest, Path(directory) / "crash.sqlite3", scenario="crash-after-claim", at_utc=TEST_AT)
            self.assertTrue(result["claim_won"])
            self.assertGreater(result["restart"]["due_count"], 0)
        result, _persistence, _client = self.run_scenario("failure-isolation")
        self.assertEqual(result["status"], "COMPLETED_WITH_FAILURES")
        self.assertEqual(sum(item["status"] == "SUCCEEDED" for item in result["outcomes"]), 18)

    def test_persistence_failure_is_audited_without_silent_success(self) -> None:
        result, persistence, client = self.run_scenario("persistence-failure")
        self.assertEqual(result["status"], "COMPLETED_WITH_FAILURES")
        self.assertTrue(all(item["status"] == "FAILED" for item in result["outcomes"]))
        self.assertEqual(len(client.calls), 19)
        self.assertEqual(len(persistence.fetch_snapshots()), 0)
        self.assertTrue(all(item["error_category"] == "PERSISTENCE_FAILURE" for item in result["outcomes"]))

    def test_audit_contains_no_secret_or_raw_response(self) -> None:
        result, persistence, _client = self.run_scenario("all-success")
        self.assertEqual(result["status"], "COMPLETE")
        serialized = json.dumps(persistence.fetch_snapshots(), ensure_ascii=False)
        self.assertNotIn("MDD_API_KEY", serialized)
        self.assertNotIn("serviceDelivery", serialized)
        self.assertTrue(all(row["raw_payload_persisted"] == 0 for row in persistence.fetch_snapshots()))

    def test_shutdown_marks_work_as_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            persistence = SQLitePersistence(Path(directory) / "shutdown.sqlite3")
            persistence.materialize_manifest(self.manifest)
            client = FakeMddClient()
            collector = Collector(
                manifest=self.manifest,
                persistence=persistence,
                mdd_client=client,
                quota_guard=QuotaGuard(persistence),
            )
            collector.request_shutdown()
            result = collector.run_once(mode="simulate", at_utc=TEST_AT, run_id="shutdown")
            self.assertEqual(result.status, "INTERRUPTED")
            self.assertEqual(len(client.calls), 0)
            self.assertTrue(all(item.status == "SKIPPED" for item in result.outcomes))
            self.assertTrue(all(item.skip_reason == "INTERRUPTED_SHUTDOWN" for item in result.outcomes))
            persistence.close()

    def test_live_mode_is_blocked_before_transport(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            persistence = SQLitePersistence(Path(directory) / "live.sqlite3")
            persistence.materialize_manifest(self.manifest)
            client = FakeMddClient()
            collector = Collector(
                manifest=self.manifest,
                persistence=persistence,
                mdd_client=client,
                quota_guard=QuotaGuard(persistence),
            )
            with self.assertRaises(LiveModeBlocked):
                collector.run_once(mode="live", at_utc=TEST_AT)
            self.assertEqual(len(client.calls), 0)
            persistence.close()

    def test_no_network_or_platform_specific_core_dependency(self) -> None:
        package = REPOSITORY_ROOT / "src" / "cologne_transit_collector"
        source = "\n".join(path.read_text(encoding="utf-8") for path in package.glob("*.py"))
        for banned in ("psycopg", "pyodbc", "win32", "powershell", "sqlserver", "windows\\"):
            self.assertNotIn(banned, source.lower())
        self.assertNotIn("MDD_API_KEY", (package / "trias.py").read_text(encoding="utf-8"))
        roadmap = (REPOSITORY_ROOT / "docs" / "ROADMAP.md").read_text(encoding="utf-8")
        self.assertIn("## Stage 15 — Post-Cutover Validation", roadmap)
        self.assertIn("Stage 15 remains **NOT STARTED — FIRST SCHEDULED TIERED50 HEARTBEAT FAILED**", roadmap)

    def test_approved_retry_statuses_are_exact(self) -> None:
        self.assertEqual(APPROVED_TRANSIENT_HTTP_STATUS_CODES, frozenset({408, 429, 500, 502, 503, 504}))


if __name__ == "__main__":
    unittest.main()
