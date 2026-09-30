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
        self.assertEqual(self.evidence["stage_13_status"], "IN PROGRESS — EVIDENCE READY; USER DESIGN APPROVAL REQUIRED")
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
        self.assertIsNone(result_limit["production_number_of_results"])
        self.assertIsNone(result_limit["technically_evaluated_number_of_results"])
        self.assertEqual(result_limit["provider_maximum_status"], "PROVIDER_MAXIMUM_NOT_ESTABLISHED")
        self.assertEqual(result_limit["decision"], "NOT_ESTABLISHED")
        self.assertEqual(
            result_limit["corrected_metric_status"],
            "CORRECTED_DIAGNOSTIC_COMPLETED_NO_VALID_RESPONSES_NETWORK_OR_DNS_FAILURE",
        )
        live = result_limit["bounded_live_diagnostic"]
        self.assertEqual(live["http_attempts_consumed"], 114)
        self.assertEqual(live["current_run_http_attempts_consumed"], 24)
        self.assertEqual(live["prior_attempts_consumed"], 90)
        gate = result_limit["additional_request_gate"]
        self.assertTrue(gate["approved"])
        self.assertEqual(gate["requested_additional_http_attempts"], 24)
        self.assertEqual(gate["cumulative_attempts_if_approved"], 114)
        self.assertEqual(gate["historical_diagnostic_hard_ceiling"], 100)
        self.assertFalse(gate["historical_ceiling_changed"])
        self.assertEqual(gate["additional_requests_issued"], 24)
        self.assertTrue(gate["corrected_live_diagnostic_issued"])
        self.assertEqual(live["result_limit_probe"]["requested_result_limits"], [5, 10, 20, 30])
        for summary in live["result_limit_probe"]["summary_by_result_limit"].values():
            self.assertEqual(summary["request_count"], 6)
            self.assertEqual(summary["http_status_distribution"], {"None": 6})
            self.assertEqual(summary["parser_success_count"], 0)
            self.assertEqual(summary["valid_trias_response_count"], 0)
            self.assertEqual(summary["legacy_forward_temporal_span_status"], "NOT_PRESENT")
            self.assertEqual(summary["forward_coverage_statistics_seconds"]["count"], 0)
        self.assertEqual(len(result_limit["historical_invalidated_sequential_records"]), 24)
        self.assertTrue(
            all(
                row["forward_temporal_span_status"] == "INVALID_FOR_FORWARD_COVERAGE_DECISION"
                for row in result_limit["historical_invalidated_sequential_records"]
            )
        )
        self.assertEqual(len(live["result_limit_probe"]["sequential_records"]), 24)
        self.assertTrue(
            all(
                row["transport_failure"] == "NETWORK_OR_DNS_FAILURE"
                and row["raw_payload_persisted"] is False
                for row in live["result_limit_probe"]["sequential_records"]
            )
        )

    def test_corrected_comparison_remains_undecided_and_boundary_review_is_explicit(self) -> None:
        comparison = self.evidence["number_of_results_comparison"]
        for key in ("fixed_n20", "fixed_n30", "tier_specific_a20_b30_c30"):
            self.assertEqual(comparison[key]["decision"], "UNDECIDED_NO_VALID_CORRECTED_LIVE_RESPONSES")
        self.assertEqual(comparison["fixed_n30"]["static_cadence_window_fit"]["station_p95_supported_count"], 50)
        self.assertEqual(comparison["tier_specific_a20_b30_c30"]["static_cadence_window_fit"]["station_p95_supported_count"], 50)
        boundary = self.evidence["cadence_boundary_review"]
        self.assertEqual(boundary["requested_difference_count"], 2)
        self.assertEqual(boundary["actual_density_ranked_assignment_difference_count"], 4)
        self.assertEqual(
            [row["source_parent_station_id"] for row in boundary["changed_stations"]],
            ["de:05315:11710", "de:05315:11810", "de:05315:17311", "de:05315:11410"],
        )
        preferences = self.evidence["user_review_preferences"]
        self.assertEqual(preferences["preferred_cadence_candidate"], "8/20/22 @ 5/15/30")
        self.assertEqual(preferences["preferred_production_retry_policy"]["max_attempts"], 2)
        self.assertFalse(preferences["stage_13_complete_allowed"])

    def test_concurrency_contract_and_heartbeat_feasibility(self) -> None:
        concurrency = self.evidence["sequential_vs_bounded_concurrency"]
        self.assertEqual(concurrency["decision"], "TECHNICALLY_EVALUATED_OPTION_REQUIRES_USER_APPROVAL")
        self.assertIsNone(concurrency["maximum_concurrency"])
        self.assertEqual(concurrency["technically_evaluated_maximum_concurrency"], 2)
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

    def test_user_decision_gate_keeps_all_material_choices_unapproved(self) -> None:
        gate = self.evidence["user_decision_gate"]
        self.assertEqual(gate["status"], "USER_APPROVAL_REQUIRED")
        self.assertFalse(gate["all_material_design_choices_user_approved"])
        for field in (
            "production_number_of_results_approved",
            "production_concurrency_approved",
            "production_cadence_approved",
            "production_retry_policy_approved",
        ):
            self.assertFalse(gate[field])
            self.assertFalse(self.evidence["readiness_decision"][field])

    def test_corrected_forward_coverage_ignores_previous_onward_and_departure_timings(self) -> None:
        anchor = "2026-09-30T10:40:00Z"
        bundle = {
            "stop_events": [
                {
                    "timetabled_time": "2026-09-30T10:42:00Z",
                    "call_observations": [
                        {
                            "call_scope": "PREVIOUS",
                            "arrival_timing": [{"field_values": {"TimetabledTime": ["2026-09-30T09:12:00Z"]}}],
                            "departure_timing": [{"field_values": {"TimetabledTime": ["2026-09-30T09:13:00Z"]}}],
                        },
                        {
                            "call_scope": "THIS",
                            "arrival_timing": [{"field_values": {"TimetabledTime": ["2026-09-30T10:42:00Z"]}}],
                            "departure_timing": [{"field_values": {"TimetabledTime": ["2026-09-30T10:43:00Z"]}}],
                        },
                        {
                            "call_scope": "ONWARD",
                            "arrival_timing": [{"field_values": {"TimetabledTime": ["2026-09-30T12:45:00Z"]}}],
                            "departure_timing": [{"field_values": {"TimetabledTime": ["2026-09-30T12:46:00Z"]}}],
                        },
                    ],
                },
                {"timetabled_time": "2026-09-30T11:15:00Z", "call_observations": []},
                {
                    "timetabled_time": "2026-09-30T23:59:00Z",
                    "call_observations": [
                        {
                            "call_scope": "PREVIOUS",
                            "arrival_timing": [{"field_values": {"TimetabledTime": ["2026-09-30T09:00:00Z"]}}],
                        }
                    ],
                },
            ]
        }
        metrics = stage13._forward_coverage_metrics(bundle, anchor)
        self.assertEqual(metrics["returned_thiscall_event_count"], 2)
        self.assertEqual(metrics["events_before_anchor_count"], 0)
        self.assertEqual(metrics["events_at_or_after_anchor_count"], 2)
        self.assertEqual(metrics["earliest_thiscall_arrival"], "2026-09-30T10:42:00Z")
        self.assertEqual(metrics["latest_thiscall_arrival"], "2026-09-30T11:15:00Z")
        self.assertEqual(metrics["forward_coverage_seconds"], 2100.0)

        previous_changed = json.loads(json.dumps(bundle))
        previous_changed["stop_events"][0]["call_observations"][0]["arrival_timing"][0]["field_values"]["TimetabledTime"] = [
            "2026-09-30T07:00:00Z"
        ]
        onward_changed = json.loads(json.dumps(bundle))
        onward_changed["stop_events"][0]["call_observations"][2]["arrival_timing"][0]["field_values"]["TimetabledTime"] = [
            "2026-09-30T18:00:00Z"
        ]
        departure_changed = json.loads(json.dumps(bundle))
        departure_changed["stop_events"][0]["call_observations"][1]["departure_timing"][0]["field_values"]["TimetabledTime"] = [
            "2026-09-30T19:00:00Z"
        ]
        self.assertEqual(stage13._forward_coverage_metrics(previous_changed, anchor), metrics)
        self.assertEqual(stage13._forward_coverage_metrics(onward_changed, anchor), metrics)
        self.assertEqual(stage13._forward_coverage_metrics(departure_changed, anchor), metrics)

    def test_thiscall_arrival_before_anchor_is_separate_and_does_not_inflate_coverage(self) -> None:
        bundle = {
            "stop_events": [
                {"timetabled_time": "2026-09-30T10:30:00Z"},
                {"timetabled_time": "2026-09-30T10:42:00Z"},
                {"timetabled_time": "2026-09-30T11:15:00Z"},
            ]
        }
        metrics = stage13._forward_coverage_metrics(bundle, "2026-09-30T10:40:00Z")
        self.assertEqual(metrics["returned_thiscall_event_count"], 3)
        self.assertEqual(metrics["events_before_anchor_count"], 1)
        self.assertEqual(metrics["events_at_or_after_anchor_count"], 2)
        self.assertEqual(metrics["forward_coverage_seconds"], 2100.0)

    def test_corrected_diagnostic_is_closed_without_explicit_additional_request_approval(self) -> None:
        result = stage13._run_corrected_result_limit_diagnostic(
            [],
            prior_attempts=stage13.PRIOR_STAGE13_DIAGNOSTIC_ATTEMPTS,
            additional_requests_approved=False,
        )
        self.assertEqual(result["status"], "NOT_RUN_USER_APPROVAL_REQUIRED")
        self.assertEqual(result["http_attempts_consumed"], 0)

    def test_no_secret_or_raw_payload_persistence(self) -> None:
        rendered = self.evidence_path.read_text(encoding="utf-8").lower()
        self.assertNotIn("x-api-key:", rendered)
        self.assertNotIn("authorization:", rendered)
        self.assertNotIn('"body"', rendered)
        self.assertNotIn('"response_body"', rendered)
        self.assertNotIn('"api_key_persisted": true', rendered)


if __name__ == "__main__":
    unittest.main()
