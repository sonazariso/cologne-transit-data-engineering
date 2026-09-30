from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

import stage_13_tiered_collector_readiness as stage13  # noqa: E402


class Stage13TieredCollectorReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.evidence_path = REPOSITORY_ROOT / "docs/stages/13-tiered-collector-readiness-evidence.json"
        cls.evidence = json.loads(cls.evidence_path.read_text(encoding="utf-8"))

    def test_historical_candidate_math_and_approved_cadence_math(self) -> None:
        historical = stage13.candidate_cadence()
        self.assertEqual(historical["logical_requests_per_hour"], 280)
        self.assertEqual(historical["monthly_logical_requests"], {"30_day": 201600, "31_day": 208320})

        approved = stage13.candidate_cadence(stage13.PRODUCTION_TIER_PLAN)
        self.assertEqual(approved["logical_requests_per_hour"], 240)
        self.assertEqual(approved["logical_requests_per_day"], 5760)
        self.assertEqual(approved["monthly_logical_requests"], {"30_day": 172800, "31_day": 178560})

    def test_quota_reconciliation_includes_legacy_reserve_and_30_31_day_projections(self) -> None:
        quota = stage13.quota_projection_set(
            240,
            legacy_reserved_requests=stage13.LEGACY7_RESERVED_MONTHLY_REQUESTS,
            diagnostic_reserved_requests=100,
        )
        nominal = quota["projections"][0]
        self.assertEqual(nominal["30_day"]["base_logical_requests"], 172800)
        self.assertEqual(nominal["31_day"]["base_logical_requests"], 178560)
        self.assertEqual(
            nominal["31_day"]["conservative_reserve_reconciliation"]["conservative_total_http_requests"],
            187588,
        )
        stress = next(item for item in quota["projections"] if item["label"] == "ten_percent_stress_sensitivity")
        self.assertEqual(
            stress["31_day"]["conservative_reserve_reconciliation"]["conservative_total_http_requests"],
            205444,
        )
        self.assertTrue(stress["31_day"]["conservative_reserve_reconciliation"]["within_normal_envelope_max"])

    def test_final_heartbeat_distribution_reconciles(self) -> None:
        phase = stage13.heartbeat_phase_distribution(stage13.PRODUCTION_TIER_PLAN)
        self.assertEqual(phase["total_due_targets_by_heartbeat"], [21, 21, 19, 20, 20, 19])
        self.assertEqual(phase["max_due_targets_in_one_heartbeat"], 21)
        self.assertEqual(phase["super_cycle_total_logical_requests"], 120)
        self.assertEqual(phase["hour_total_logical_requests"], 240)
        self.assertTrue(phase["phase_distribution_reconciles"])

    def test_phase_assignment_is_stable_and_not_rank_based(self) -> None:
        rows = [
            {
                "source_parent_station_id": f"de:05315:test:{index:03d}",
                "station_name": f"Station {index}",
                "production_tier": "B" if index < 20 else "C",
                "density_rank": index + 1,
            }
            for index in range(40)
        ]
        first = stage13.phase_assignments_for_tier_rows(rows)
        shuffled = list(reversed(rows))
        for index, row in enumerate(shuffled, start=1):
            row["density_rank"] = index
        second = stage13.phase_assignments_for_tier_rows(shuffled)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 40)
        self.assertEqual(
            {row["phase_index"] for row in first if row["production_tier"] == "B"},
            {0, 1, 2},
        )
        self.assertEqual(
            {row["phase_index"] for row in first if row["production_tier"] == "C"},
            set(range(6)),
        )

    def test_claim_identity_and_no_historical_replay(self) -> None:
        claim = stage13.claim_identity("target-1", 123)
        self.assertEqual(claim, ("target-1", 123))
        self.assertEqual(stage13.claim_identity("target-1", 123), claim)
        self.assertTrue(stage13.may_execute_bucket(123, 123))
        self.assertFalse(stage13.may_execute_bucket(124, 123))
        self.assertEqual(stage13.logical_due_bucket(1500), 5)

    def test_approved_stage12_panel_and_exact_50_station_density_evidence(self) -> None:
        self.assertEqual(self.evidence["stage_13_status"], "COMPLETE — APPROVED TIERED COLLECTOR READINESS DESIGN; PRODUCTION NOT STARTED")
        gate = self.evidence["stage_12_gate"]
        self.assertTrue(gate["authoritative_completion_artifact_present"])
        self.assertEqual(gate["approved_panel_version"], "TIERED50_PANEL_V1")
        self.assertEqual(gate["panel_station_count"], 50)
        density = self.evidence["static_event_density"]
        self.assertEqual(density["status"], "COMPLETE")
        self.assertEqual(len(density["station_tier_rows"]), 50)
        self.assertEqual(density["source_snapshot"]["feed_version"], "VERSION__20260930_0053")
        self.assertEqual(density["source_snapshot"]["zip_sha256"], stage13.STAGE12_ZIP_SHA256)
        self.assertEqual(density["tier_assignment"]["tier_counts"], {"A": 10, "B": 20, "C": 20})
        self.assertTrue(
            all(
                row["window_event_count_statistics"][window]["p95"] is not None
                for row in density["station_tier_rows"]
                for window in ("5", "10", "15", "30")
            )
        )

    def test_result_limit_provider_status_and_sanitized_live_evidence(self) -> None:
        result_limit = self.evidence["number_of_results"]
        self.assertEqual(result_limit["production_number_of_results"], 30)
        self.assertEqual(result_limit["provider_maximum_status"], "PROVIDER_MAXIMUM_NOT_ESTABLISHED")
        self.assertEqual(result_limit["decision"], "APPROVED_FIXED_30_FROM_BOUNDED_COVERAGE_EVIDENCE")
        live = result_limit["bounded_live_diagnostic"]
        self.assertEqual(live["http_attempts_consumed"], 90)
        self.assertLessEqual(live["http_attempts_consumed"], live["max_http_attempts"])
        self.assertEqual(live["result_limit_probe"]["requested_result_limits"], [5, 10, 20, 30])
        for summary in live["result_limit_probe"]["summary_by_result_limit"].values():
            self.assertEqual(summary["request_count"], 6)
            self.assertEqual(summary["http_status_distribution"], {"200": 6})
            self.assertEqual(summary["parser_success_count"], 6)
            self.assertEqual(summary["valid_trias_response_count"], 6)

    def test_concurrency_contract_and_heartbeat_feasibility(self) -> None:
        concurrency = self.evidence["sequential_vs_bounded_concurrency"]
        self.assertEqual(concurrency["decision"], "APPROVED_MAXIMUM_CONCURRENCY_2")
        self.assertEqual(concurrency["maximum_concurrency"], 2)
        probe = concurrency["bounded_live_diagnostic"]
        self.assertFalse(probe["provider_pressure_signal"])
        self.assertTrue(probe["failure_isolation_observed"])
        self.assertTrue(self.evidence["heartbeat_feasibility"]["finish_within_five_minute_heartbeat_at_observed_p95"])

    def test_overlap_identity_historical_continuity_and_schema(self) -> None:
        stage13.validate_evidence(self.evidence)
        self.assertEqual(
            self.evidence["overlap_and_duplicate_execution_contract"]["unique_execution_identity"],
            ["SamplingTargetId", "LogicalDueBucket"],
        )
        self.assertFalse(self.evidence["readiness_decision"]["stage_14_started"])
        self.assertFalse(self.evidence["readiness_decision"]["tiered50_activated"])
        self.assertFalse(self.evidence["historical_continuity"]["cutover_performed"])

    def test_no_secret_or_raw_payload_persistence(self) -> None:
        rendered = self.evidence_path.read_text(encoding="utf-8").lower()
        self.assertNotIn("x-api-key:", rendered)
        self.assertNotIn("authorization:", rendered)
        self.assertNotIn('"body"', rendered)
        self.assertNotIn('"response_body"', rendered)
        self.assertNotIn('"api_key_persisted": true', rendered)


if __name__ == "__main__":
    unittest.main()
