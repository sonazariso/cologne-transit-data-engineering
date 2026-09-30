from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

import stage_13_tiered_collector_readiness as stage13  # noqa: E402


class Stage13TieredCollectorReadinessTests(unittest.TestCase):
    def test_candidate_quota_math_and_30_31_day_projections(self) -> None:
        cadence = stage13.candidate_cadence()
        self.assertEqual(cadence["logical_requests_per_hour"], 280)
        self.assertEqual(cadence["logical_requests_per_day"], 6720)
        self.assertEqual(cadence["monthly_logical_requests"], {"30_day": 201600, "31_day": 208320})

        no_retry = stage13.quota_projection_set(280)["projections"][0]
        self.assertEqual(no_retry["30_day"]["projected_http_requests"], 201600)
        self.assertEqual(no_retry["31_day"]["projected_http_requests"], 208320)
        self.assertEqual(no_retry["30_day"]["headroom_to_250000_ceiling"], 48400)
        self.assertEqual(no_retry["31_day"]["headroom_to_250000_ceiling"], 41680)

    def test_retry_quota_accounting_is_additive_and_labeled(self) -> None:
        projection = stage13.quota_projection(280, 31, 0.05)
        self.assertEqual(projection["base_logical_requests"], 208320)
        self.assertEqual(projection["projected_http_requests"], 218736.0)
        self.assertLess(projection["headroom_to_250000_ceiling"], 40000)

    def test_phase_distribution_reconciles_to_140_and_280(self) -> None:
        phase = stage13.heartbeat_phase_distribution()
        self.assertEqual(phase["total_due_targets_by_heartbeat"], [24, 23, 23, 24, 23, 23])
        self.assertEqual(phase["super_cycle_total_logical_requests"], 140)
        self.assertEqual(phase["hour_total_logical_requests"], 280)
        self.assertTrue(phase["phase_distribution_reconciles"])

    def test_phase_assignment_is_deterministic_and_not_rank_based(self) -> None:
        target_id = "TIERED50|PROVISIONAL|station-001"
        first = stage13.deterministic_phase_index(target_id, 6)
        second = stage13.deterministic_phase_index(target_id, 6)
        self.assertEqual(first, second)
        self.assertGreaterEqual(first, 0)
        self.assertLess(first, 6)
        self.assertNotEqual(
            stage13.deterministic_phase_index("TIERED50|PROVISIONAL|station-002", 6),
            None,
        )

    def test_claim_identity_and_no_catch_up_replay(self) -> None:
        claim = stage13.claim_identity("target-1", 123)
        self.assertEqual(claim, ("target-1", 123))
        self.assertEqual(stage13.claim_identity("target-1", 123), claim)
        self.assertTrue(stage13.may_execute_bucket(123, 123))
        self.assertFalse(stage13.may_execute_bucket(124, 123))
        self.assertEqual(stage13.logical_due_bucket(1500), 5)

    def test_offline_artifact_records_stage12_and_result_limit_blockers(self) -> None:
        evidence = stage13.build_evidence(generated_at_utc="2026-09-29T00:00:00Z")
        self.assertEqual(evidence["stage_12_gate"]["authoritative_completion_artifact_present"], True)
        self.assertIsNone(evidence["stage_12_gate"]["blocker"])
        self.assertEqual(evidence["stage_12_gate"]["approved_panel_version"], "TIERED50_PANEL_V1")
        self.assertEqual(evidence["stage_12_gate"]["panel_station_count"], 50)
        self.assertEqual(
            evidence["number_of_results"]["required_blocker"],
            "PROVIDER_MAXIMUM_NOT_ESTABLISHED",
        )
        self.assertFalse(evidence["readiness_decision"]["tiered50_activated"])


if __name__ == "__main__":
    unittest.main()
