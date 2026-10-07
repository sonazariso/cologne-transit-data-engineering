from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.manifest import LEGACY7_IDS, load_default_manifest
from cologne_transit_collector.simulation import run_simulation
from cologne_transit_stage15 import (
    SQLiteEvidenceAdapter,
    Stage15Analyzer,
    duration_statistics,
)


AT = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
END = datetime(2026, 9, 30, 12, 5, tzinfo=timezone.utc)


class Stage15ValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_default_manifest(ROOT)
        cls.contract = json.loads(
            (ROOT / "config" / "stage15_validation_contract.json").read_text(encoding="utf-8")
        )

    def _state(self, scenario: str = "all-success") -> Path:
        directory = tempfile.TemporaryDirectory(prefix="stage15-test-")
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "evidence.sqlite3"
        run_simulation(self.manifest, path, scenario=scenario, at_utc=AT)
        return path

    def _report(self, path: Path, *, start: datetime = AT, end: datetime = END) -> dict:
        return Stage15Analyzer(
            self.manifest,
            contract=self.contract,
            evidence_adapter=SQLiteEvidenceAdapter(path),
            window_start_utc=start,
            window_end_utc=end,
        ).analyze()

    @staticmethod
    def _check(report: dict, validation_id: str) -> dict:
        return next(item for item in report["checks"] if item["validation_id"] == validation_id)

    def test_contract_and_configuration_invariants_are_machine_readable(self) -> None:
        report = Stage15Analyzer(self.manifest, contract=self.contract).analyze()
        self.assertEqual(
            report["stage14_overall_status"],
            "LEGACY7 SQL SERVER RUNTIME ACTIVE — TIERED50 CUTOVER DEFERRED",
        )
        self.assertEqual(report["stage14_collector_status"], "STAGE 14 COLLECTOR IMPLEMENTATION COMPLETE")
        self.assertEqual(report["stage15_readiness_status"], "IN PROGRESS")
        self.assertEqual(report["stage15_live_status"], "NOT STARTED — AWAITS TIERED50 CUTOVER")
        self.assertEqual(self._check(report, "panel.target_count")["actual_value"], 50)
        self.assertEqual(self._check(report, "panel.legacy7_count")["actual_value"], 7)
        self.assertEqual(self._check(report, "panel.new_tiered50_count")["actual_value"], 43)
        self.assertEqual(self._check(report, "panel.legacy7_ids")["actual_value"], [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(self._check(report, "schedule.tier_counts")["actual_value"], {"A": 8, "B": 20, "C": 22})
        self.assertEqual(self._check(report, "schedule.cadence_minutes")["actual_value"], {"A": [5], "B": [15], "C": [30]})
        self.assertEqual(self._check(report, "schedule.number_of_results")["actual_value"], {"A": [20], "B": [30], "C": [30]})
        self.assertEqual(self._check(report, "schedule.heartbeat_distribution")["actual_value"], [19, 18, 18, 18, 19, 18])
        self.assertEqual(self._check(report, "schedule.phase_capacities")["actual_value"], {"A": [8], "B": [7, 7, 6], "C": [4, 3, 4, 3, 4, 4]})
        self.assertEqual(self._check(report, "schedule.logical_requests_per_hour")["actual_value"], 220)
        self.assertEqual(self._check(report, "execution.maximum_concurrency")["actual_value"], 2)
        self.assertEqual(self._check(report, "execution.max_attempts")["actual_value"], 2)

    def test_active_status_guardrails_match_s87_without_rewriting_history(self) -> None:
        stage14_doc = (ROOT / "docs" / "stages" / "14-collector-implementation.md").read_text(
            encoding="utf-8"
        )
        guardrails = (ROOT / "docs" / "GUARDRAILS.md").read_text(encoding="utf-8")
        historical_prompt = (ROOT / "prompts" / "S80.md").read_text(encoding="utf-8")

        self.assertIn("LEGACY7 SQL SERVER RUNTIME ACTIVE — TIERED50 CUTOVER DEFERRED", stage14_doc)
        self.assertIn("LEGACY7 SQL SERVER RUNTIME ACTIVE — TIERED50 CUTOVER DEFERRED", guardrails)
        self.assertIn("NOT STARTED — AWAITS TIERED50 CUTOVER", guardrails)
        self.assertIn("IN PROGRESS — LIVE SQLITE RESEARCH PILOT; PRODUCTION CUTOVER DEFERRED", historical_prompt)

    def test_all_success_reconciles_every_expected_target_and_leaves_live_stage_unstarted(self) -> None:
        report = self._report(self._state())
        self.assertEqual(report["reconciliation"]["expected_count"], 19)
        self.assertEqual(report["reconciliation"]["counts"], {"EXPECTED_AND_SUCCEEDED": 19})
        self.assertEqual(report["stage15_live_status"], "NOT STARTED — AWAITS TIERED50 CUTOVER")
        self.assertFalse(report["evidence"]["collector_evidence_mutated"])
        self.assertEqual(self._check(report, "architecture.read_only_evidence_adapter")["status"], "PASS")

    def test_missing_failed_and_budget_skipped_targets_are_not_success(self) -> None:
        failed = self._report(self._state("http-500-twice"))
        self.assertEqual(failed["reconciliation"]["counts"]["EXPECTED_AND_FAILED"], 1)

        skipped = self._report(self._state("heartbeat-budget-initial-skip"))
        self.assertEqual(skipped["reconciliation"]["counts"], {"EXPECTED_AND_SKIPPED": 19})

        path = self._state()
        connection = sqlite3.connect(path)
        try:
            audit_id = connection.execute(
                "SELECT audit_id FROM target_audits ORDER BY audit_id LIMIT 1"
            ).fetchone()[0]
            connection.execute("DELETE FROM recorded_at_time_evidence WHERE audit_id = ?", (audit_id,))
            connection.execute("DELETE FROM parsed_snapshots WHERE audit_id = ?", (audit_id,))
            connection.execute("DELETE FROM http_attempts WHERE audit_id = ?", (audit_id,))
            connection.execute("DELETE FROM target_audits WHERE audit_id = ?", (audit_id,))
            connection.commit()
        finally:
            connection.close()
        missing = self._report(path)
        self.assertEqual(missing["reconciliation"]["counts"]["EXPECTED_BUT_MISSING"], 1)

    def test_duplicate_dispatcher_and_target_claim_events_are_reconciled(self) -> None:
        report = self._report(self._state("duplicate-bucket"))
        self.assertGreaterEqual(report["reconciliation"]["counts"]["DUPLICATE_INVOCATION_PREVENTED"], 19)
        self.assertEqual(self._check(report, "execution.duplicate_dispatcher_no_duplicate_transport")["status"], "PASS")
        self.assertEqual(self._check(report, "claims.duplicate_attempts_auditable")["status"], "PASS")
        self.assertEqual(self._check(report, "claims.at_most_one_winning_claim")["status"], "PASS")

    def test_unexpected_execution_is_a_hard_reconciliation_failure(self) -> None:
        path = self._state()
        bucket = 5969232
        unexpected = next(target for target in self.manifest.targets if target.source_parent_station_id == "de:05315:11112")
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                """
                INSERT INTO target_audits
                (collector_run_id, panel_version, source_parent_station_id, station_name,
                 tier, phase_index, number_of_results, logical_due_bucket, sampling_era,
                 continuity_classification, started_at_utc, completed_at_utc, status,
                 http_attempts, duration_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "unexpected-run",
                    unexpected.panel_version,
                    unexpected.source_parent_station_id,
                    unexpected.station_name,
                    unexpected.tier,
                    unexpected.phase_index,
                    unexpected.number_of_results,
                    bucket,
                    unexpected.sampling_era.value,
                    unexpected.continuity_classification,
                    "2026-09-30T12:00:00Z",
                    "2026-09-30T12:00:01Z",
                    "SUCCEEDED",
                    0,
                    1000,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        report = self._report(path)
        self.assertEqual(self._check(report, "reconciliation.expected_vs_actual")["status"], "FAIL")
        self.assertGreater(report["reconciliation"]["counts"]["UNEXPECTED_EXECUTION"], 0)

    def test_retry_reconciliation_allows_one_retry_and_rejects_attempt_three(self) -> None:
        allowed = self._report(self._state("http-500-then-success"))
        self.assertEqual(self._check(allowed, "retry.structural_rules")["status"], "PASS")
        self.assertEqual(self._check(allowed, "retry.automatic_retry_max")["status"], "PASS")

        path = self._state("http-500-then-success")
        connection = sqlite3.connect(path)
        try:
            audit_id = connection.execute(
                "SELECT audit_id FROM target_audits ORDER BY audit_id LIMIT 1"
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO http_attempts
                (audit_id, attempt_number, started_at_utc, completed_at_utc,
                 http_status, outcome, duration_ms, transport_kind,
                 network_attempted, http_response_received, live_client_invocation,
                 conservative_quota_accounted, provider_quota_status,
                 provider_attempt_confirmed)
                VALUES (?, 3, ?, ?, 500, 'HTTP_RESPONSE', 10, 'SIMULATED_TRANSPORT', 0, 1, 0, 0, 'NOT_CONSUMED', 0)
                """,
                (audit_id, "2026-09-30T12:00:02Z", "2026-09-30T12:00:03Z"),
            )
            connection.commit()
        finally:
            connection.close()
        invalid = self._report(path)
        self.assertEqual(self._check(invalid, "retry.attempt_number_max")["status"], "FAIL")
        self.assertEqual(self._check(invalid, "retry.automatic_retry_max")["status"], "FAIL")

    def test_retry_after_401_is_structural_failure(self) -> None:
        path = self._state("http-401")
        connection = sqlite3.connect(path)
        try:
            audit_id = connection.execute(
                "SELECT audit_id FROM target_audits ORDER BY audit_id LIMIT 1"
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO http_attempts
                (audit_id, attempt_number, started_at_utc, completed_at_utc,
                 http_status, outcome, retry_decision, duration_ms,
                 transport_kind, network_attempted, http_response_received,
                 live_client_invocation, conservative_quota_accounted,
                 provider_quota_status, provider_attempt_confirmed)
                VALUES (?, 2, ?, ?, 401, 'HTTP_RESPONSE', 'RETRY_CANDIDATE', 10,
                        'SIMULATED_TRANSPORT', 0, 1, 0, 0, 'NOT_CONSUMED', 0)
                """,
                (audit_id, "2026-09-30T12:00:02Z", "2026-09-30T12:00:03Z"),
            )
            connection.commit()
        finally:
            connection.close()
        report = self._report(path)
        self.assertEqual(self._check(report, "retry.no_retry_after_deterministic_failure")["status"], "FAIL")

    def test_retry_after_deterministic_client_failure_is_structural_failure(self) -> None:
        path = self._state()
        connection = sqlite3.connect(path)
        try:
            audit_id = connection.execute(
                "SELECT audit_id FROM target_audits ORDER BY audit_id LIMIT 1"
            ).fetchone()[0]
            connection.execute(
                "UPDATE http_attempts SET transport_failure_category = 'CLIENT_EXECUTION_FAILURE', outcome = 'TRANSPORT_FAILURE' WHERE audit_id = ? AND attempt_number = 1",
                (audit_id,),
            )
            connection.execute(
                """
                INSERT INTO http_attempts
                (audit_id, attempt_number, started_at_utc, completed_at_utc,
                 http_status, outcome, retry_decision, duration_ms,
                 transport_kind, network_attempted, http_response_received,
                 live_client_invocation, conservative_quota_accounted,
                 provider_quota_status, provider_attempt_confirmed)
                VALUES (?, 2, ?, ?, 200, 'HTTP_RESPONSE', 'RETRY_CANDIDATE', 10,
                        'SIMULATED_TRANSPORT', 0, 1, 0, 0, 'NOT_CONSUMED', 0)
                """,
                (audit_id, "2026-09-30T12:00:02Z", "2026-09-30T12:00:03Z"),
            )
            connection.commit()
        finally:
            connection.close()
        report = self._report(path)
        self.assertEqual(self._check(report, "retry.no_retry_after_deterministic_failure")["status"], "FAIL")

    def test_quota_boundaries_and_unavailable_state_are_explicit(self) -> None:
        for value, check_id in (
            (207500, "quota.retry_protection_boundary_observed"),
            (210000, "quota.normal_operational_maximum_observed"),
            (250000, "quota.provider_absolute_ceiling_observed"),
        ):
            path = self._state()
            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    "UPDATE shared_usage SET confirmed_provider_attempts = ? WHERE month_key = '2026-09'",
                    (value,),
                )
                connection.commit()
            finally:
                connection.close()
            report = self._report(path)
            self.assertEqual(self._check(report, check_id)["status"], "PASS")

        path = self._state()
        connection = sqlite3.connect(path)
        try:
            connection.execute("UPDATE shared_usage SET state_available = 0 WHERE month_key = '2026-09'")
            connection.commit()
        finally:
            connection.close()
        unavailable = self._report(path)
        self.assertEqual(self._check(unavailable, "quota.state_availability")["status"], "INSUFFICIENT_EVIDENCE")
        self.assertIn("2026-09", unavailable["quota_reconciliation"]["state_unavailable_months"])

    def test_quota_normal_maximum_and_provider_ceiling_violations_fail(self) -> None:
        for value, check_id in (
            (210001, "quota.normal_operational_maximum_observed"),
            (250001, "quota.provider_absolute_ceiling_observed"),
        ):
            path = self._state()
            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    "UPDATE shared_usage SET confirmed_provider_attempts = ? WHERE month_key = '2026-09'",
                    (value,),
                )
                connection.commit()
            finally:
                connection.close()
            report = self._report(path)
            self.assertEqual(self._check(report, check_id)["status"], "FAIL")

    def test_allowed_retry_crossing_protection_boundary_fails(self) -> None:
        path = self._state()
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                """
                INSERT INTO quota_audit
                (collector_run_id, source_parent_station_id, logical_due_bucket,
                 decision_kind, decision_json, recorded_at_utc)
                VALUES ('run', 'de:05315:11110', 5969232, 'RETRY', ?, '2026-09-30T12:00:00Z')
                """,
                (json.dumps({"allowed": True, "known_total": 207500, "proposed_attempts": 1, "boundary": 207500}),),
            )
            connection.commit()
        finally:
            connection.close()
        report = self._report(path)
        self.assertEqual(self._check(report, "quota.retry_protection_boundary_observed")["status"], "FAIL")

    def test_simulation_transport_does_not_count_as_provider_usage(self) -> None:
        report = self._report(self._state())
        quota = report["quota_reconciliation"]
        self.assertEqual(quota["scheduled_logical_requests"], 19)
        self.assertEqual(quota["network_attempts"], 0)
        self.assertEqual(quota["confirmed_provider_http_attempts"], 0)
        self.assertEqual(self._check(report, "quota.simulation_excluded_from_provider_usage")["status"], "PASS")

    def test_quality_metrics_are_policy_pending_and_parser_persistence_evidence_is_measured(self) -> None:
        path = self._state()
        connection = sqlite3.connect(path)
        try:
            row = connection.execute(
                "SELECT snapshot_id, snapshot_json FROM parsed_snapshots ORDER BY snapshot_id LIMIT 1"
            ).fetchone()
            payload = json.loads(row[1])
            payload["parser_gaps"] = [{"normalized_field_name": "fixture-gap"}]
            connection.execute(
                "UPDATE parsed_snapshots SET snapshot_json = ? WHERE snapshot_id = ?",
                (json.dumps(payload), row[0]),
            )
            connection.commit()
        finally:
            connection.close()
        report = self._report(path)
        self.assertEqual(self._check(report, "quality.parser_gap_count_rate")["status"], "POLICY_PENDING")
        self.assertEqual(report["quality"]["parser_gap_count"], 1)
        self.assertEqual(self._check(report, "quality.target_failure_rate")["status"], "POLICY_PENDING")

        failure = self._report(self._state("persistence-failure"))
        self.assertEqual(self._check(failure, "quality.persistence_failure_rate")["status"], "POLICY_PENDING")
        self.assertEqual(failure["quality"]["persistence_outcomes"]["PERSISTENCE_FAILURE"], 19)

        parser_failure = self._report(self._state("parser-failure"))
        self.assertEqual(self._check(parser_failure, "quality.parser_failure_rate")["status"], "POLICY_PENDING")
        self.assertEqual(parser_failure["quality"]["parser_outcomes"]["PARSER_FAILURE"], 1)

    def test_successful_parse_without_persisted_snapshot_is_detected(self) -> None:
        path = self._state()
        connection = sqlite3.connect(path)
        try:
            connection.execute("DELETE FROM recorded_at_time_evidence")
            connection.execute("DELETE FROM parsed_snapshots")
            connection.commit()
        finally:
            connection.close()
        report = self._report(path)
        self.assertEqual(len(report["quality"]["successful_parse_without_persisted_snapshot"]), 19)

    def test_duration_statistics_are_deterministic_without_acceptance_threshold(self) -> None:
        self.assertEqual(
            duration_statistics([1, 2, 3, 4, 5]),
            {"count": 5, "min": 1.0, "P50": 3.0, "P90": 4.6, "P95": 4.8, "P99": 4.96, "max": 5.0},
        )
        report = self._report(self._state())
        duration_check = self._check(report, "quality.target_duration_statistics")
        self.assertEqual(duration_check["status"], "POLICY_PENDING")
        self.assertIsNotNone(report["duration_statistics"]["target_duration"]["P99"])

    def test_legacy7_continuity_and_real_cutover_remain_unclaimed(self) -> None:
        report = Stage15Analyzer(self.manifest, contract=self.contract).analyze()
        self.assertEqual(set(report["continuity"]["legacy7_source_ids"]), set(LEGACY7_IDS))
        self.assertEqual(self._check(report, "continuity.sampling_era_transition")["status"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(self._check(report, "continuity.post_cutover_attribution")["status"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(report["stage15_live_status"], "NOT STARTED — AWAITS TIERED50 CUTOVER")


if __name__ == "__main__":
    unittest.main()
