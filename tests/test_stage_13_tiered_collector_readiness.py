from __future__ import annotations

import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

import stage_08_mdd_realtime_pilot as stage8  # noqa: E402
import stage_13_tiered_collector_readiness as stage13  # noqa: E402


FINAL_STATUS = "COMPLETE — APPROVED TIERED COLLECTOR READINESS DESIGN; PRODUCTION NOT STARTED"


class Stage13TieredCollectorReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.evidence_path = REPOSITORY_ROOT / "docs/stages/13-tiered-collector-readiness-evidence.json"
        cls.evidence = json.loads(cls.evidence_path.read_text(encoding="utf-8"))

    def test_final_status_panel_and_density_rank_membership(self) -> None:
        self.assertEqual(self.evidence["stage_13_status"], FINAL_STATUS)
        stage12 = self.evidence["stage_12_gate"]
        self.assertEqual(stage12["approved_panel_version"], "TIERED50_PANEL_V1")
        self.assertEqual(stage12["panel_station_count"], 50)

        density = self.evidence["static_event_density"]
        self.assertEqual(density["tier_assignment"]["tier_counts"], {"A": 8, "B": 20, "C": 22})
        rows = sorted(density["station_tier_rows"], key=lambda row: row["density_rank"])
        self.assertEqual(len(rows), 50)
        self.assertEqual([row["density_rank"] for row in rows], list(range(1, 51)))
        self.assertEqual([row["production_tier"] for row in rows], ["A"] * 8 + ["B"] * 20 + ["C"] * 22)
        self.assertFalse(density["tier_assignment"]["stage12_selection_rank_used"])
        self.assertEqual(
            density["analysis_contract"]["final_tier_membership_basis"],
            "density ranks 1–8 / 9–28 / 29–50 from retained exact-feed metrics",
        )

    def test_final_cadence_math_is_8_20_22_at_5_15_30(self) -> None:
        cadence = stage13.candidate_cadence()
        self.assertEqual(cadence["logical_requests_per_hour"], 220)
        self.assertEqual(cadence["logical_requests_per_day"], 5280)
        self.assertEqual(cadence["monthly_logical_requests"], {"30_day": 158400, "31_day": 163680})
        self.assertEqual(cadence["reconciliation"], {
            "requests_per_30_minutes": 110,
            "requests_per_hour_from_30_minute_super_cycle": 220,
        })
        self.assertEqual(
            [(row["tier"], row["station_count"], row["interval_minutes"]) for row in cadence["tiers"]],
            [("A", 8, 5), ("B", 20, 15), ("C", 22, 30)],
        )
        historical = stage13.candidate_cadence(stage13.HISTORICAL_TIERED50_PLAN, status="HISTORICAL_COMPARISON")
        self.assertEqual(historical["logical_requests_per_hour"], 280)

    def test_static_support_is_recalculated_from_retained_evidence(self) -> None:
        support = self.evidence["static_event_density"]["final_approved_static_support"]
        self.assertEqual(support["station_count"], 50)
        self.assertEqual(support["station_p95_supported_count"], 50)
        self.assertTrue(support["all_station_p95_supported"])
        expected = {
            "A": (8, 20, 19.0, 20.0),
            "B": (20, 30, 23.0, 23.0),
            "C": (22, 30, 29.0, 29.0),
        }
        for tier, values in expected.items():
            row = support["by_tier"][tier]
            self.assertEqual(
                (
                    row["station_count"],
                    row["result_limit"],
                    row["maximum_station_p95"],
                    row["maximum_observed_static_scheduled_arrival_count"],
                ),
                values,
            )

    def test_approved_number_of_results_policy_preserves_live_limitations(self) -> None:
        result_limit = self.evidence["number_of_results"]
        self.assertEqual(result_limit["production_number_of_results"], {"A": 20, "B": 30, "C": 30})
        self.assertEqual(result_limit["production_number_of_results_policy"], "A20/B30/C30")
        self.assertEqual(result_limit["provider_maximum_status"], "PROVIDER_MAXIMUM_NOT_ESTABLISHED")
        self.assertEqual(
            result_limit["corrected_live_limitation"],
            "CORRECTED_LIVE_FORWARD_COVERAGE_UNAVAILABLE_DUE_TO_EXECUTION_ENVIRONMENT",
        )
        self.assertEqual(result_limit["historical_provider_acceptance"]["accepted_result_limits"], [20, 30])
        self.assertFalse(result_limit["historical_provider_acceptance"]["provider_wide_maximum_established"])
        comparison = self.evidence["number_of_results_comparison"]
        self.assertEqual(comparison["fixed_n20"]["static_cadence_window_fit"]["station_p95_supported_count"], 32)
        self.assertEqual(comparison["fixed_n30"]["static_cadence_window_fit"]["station_p95_supported_count"], 50)
        self.assertEqual(
            comparison["tier_specific_a20_b30_c30"]["decision"],
            "APPROVED_USER_DESIGN_STATIC_SUPPORTED_LIVE_LIMITATION_RETAINED",
        )

    def test_quota_reserve_math_and_guardrail(self) -> None:
        quota = stage13.quota_projection_set(
            220,
            legacy_reserved_requests=stage13.LEGACY7_RESERVED_MONTHLY_REQUESTS,
            diagnostic_reserved_requests=100,
        )
        approved = next(item for item in quota["projections"] if item["label"] == "approved_normal_retry_reserve")
        self.assertEqual(
            (
                approved["30_day"]["base_logical_requests"],
                approved["30_day"]["retry_reserve_requests"],
                approved["30_day"]["projected_http_requests"],
                approved["30_day"]["conservative_reserve_reconciliation"]["conservative_total_http_requests"],
            ),
            (158400, 15840, 174240.0, 182980),
        )
        self.assertEqual(
            (
                approved["31_day"]["base_logical_requests"],
                approved["31_day"]["retry_reserve_requests"],
                approved["31_day"]["projected_http_requests"],
                approved["31_day"]["conservative_reserve_reconciliation"]["conservative_total_http_requests"],
            ),
            (163680, 16368, 180048.0, 189076),
        )
        self.assertEqual(
            self.evidence["quota"]["approved_production_design"]["automatic_retry_protection_boundary"],
            207500,
        )
        self.assertFalse(stage13.automatic_retry_guardrail(207500, 1)["automatic_retries_allowed"])
        self.assertTrue(stage13.automatic_retry_guardrail(207499, 1)["automatic_retries_allowed"])

    def test_retry_policy_max_attempts_statuses_and_station_isolation(self) -> None:
        policy = stage13.retry_policy_contract()
        self.assertEqual(policy["max_attempts"], 2)
        self.assertEqual(policy["automatic_retry_count_max"], 1)
        for status in (408, 429, 500, 502, 503, 504):
            with self.subTest(status=status):
                self.assertTrue(stage13.retry_eligible(http_status=status))
        for status in (401, 403):
            with self.subTest(status=status):
                self.assertFalse(stage13.retry_eligible(http_status=status))
        self.assertTrue(stage13.retry_eligible(transport_failure_category="TIMEOUT"))
        self.assertTrue(policy["respect_retry_after_for_http_429"])
        self.assertTrue(policy["one_failed_station_does_not_abort_other_due_targets"])

    def test_final_phase_distribution_and_identity_assignments(self) -> None:
        phase = stage13.heartbeat_phase_distribution()
        self.assertEqual(phase["total_due_targets_by_heartbeat"], [19, 18, 18, 18, 19, 18])
        self.assertEqual(sorted(phase["total_due_targets_by_heartbeat"]), [18, 18, 18, 18, 19, 19])
        self.assertEqual(phase["super_cycle_total_logical_requests"], 110)
        self.assertEqual(phase["hour_total_logical_requests"], 220)
        self.assertEqual(phase["max_due_targets_in_one_heartbeat"], 19)
        self.assertEqual(
            [row["phase_target_counts"] for row in phase["tier_phase_distribution"]],
            [[8], [7, 7, 6], [4, 3, 4, 3, 4, 4]],
        )
        rows = json.loads(json.dumps(self.evidence["static_event_density"]["station_tier_rows"]))
        assignments = self.evidence["phase_staggering"]["phase_assignments"]
        self.assertEqual(len(assignments), 50)
        self.assertEqual(assignments, stage13.phase_assignments_for_tier_rows(rows))
        shuffled = list(reversed(rows))
        for index, row in enumerate(shuffled, start=1):
            row["density_rank"] = index
        self.assertEqual(assignments, stage13.phase_assignments_for_tier_rows(shuffled))
        self.assertTrue(all("phase_capacity" in row for row in assignments))

    def test_gate_concurrency_overlap_continuity_and_stage14_boundary(self) -> None:
        gate = self.evidence["user_decision_gate"]
        for flag in (
            "production_number_of_results_approved",
            "production_concurrency_approved",
            "production_cadence_approved",
            "production_tier_membership_approved",
            "production_retry_policy_approved",
            "user_approval_received",
            "all_material_design_choices_user_approved",
            "stage_13_complete_allowed",
        ):
            self.assertTrue(gate[flag])
        concurrency = self.evidence["sequential_vs_bounded_concurrency"]
        self.assertEqual(concurrency["maximum_concurrency"], 2)
        self.assertEqual(concurrency["decision"], "APPROVED_MAXIMUM_CONCURRENCY_2")
        self.assertTrue(concurrency["values_above_two_require_new_evidence_and_decision"])
        self.assertIn("actual persistence/database behavior", concurrency["stage14_activation_condition"])
        contract = self.evidence["overlap_and_duplicate_execution_contract"]
        self.assertEqual(contract["unique_execution_identity"], ["SamplingTargetId", "LogicalDueBucket"])
        self.assertFalse(contract["catch_up_behavior"])
        self.assertFalse(self.evidence["historical_continuity"]["cutover_performed"])
        readiness = self.evidence["readiness_decision"]
        self.assertEqual(readiness["stage_14_status"], "NOT STARTED")
        self.assertFalse(readiness["stage_14_started"])
        self.assertFalse(readiness["tiered50_activated"])
        self.assertEqual(readiness["production_activation"], "NOT_STARTED")
        self.assertFalse(readiness["production_database_objects_created"])
        self.assertFalse(readiness["windows_scheduled_tasks_created"])
        self.assertFalse(readiness["power_bi_modified"])

    def test_retained_population_accounting_is_distinct(self) -> None:
        result_limit = self.evidence["number_of_results"]
        historical = self.evidence["sequential_vs_bounded_concurrency"]["sequential_live_diagnostic"]
        self.assertEqual(
            (historical["client_request_attempts_issued"], historical["http_responses_received"], historical["confirmed_provider_http_attempts"]),
            (24, 24, 24),
        )
        cap2 = self.evidence["sequential_vs_bounded_concurrency"]["bounded_live_diagnostic"]
        self.assertEqual(
            (cap2["client_request_attempts_issued"], cap2["http_responses_received"], cap2["confirmed_provider_http_attempts"]),
            (6, 6, 6),
        )
        failed = result_limit["failed_corrected_diagnostic_audit"]
        self.assertEqual(
            (failed["client_request_attempts_issued"], failed["http_responses_received"], failed["confirmed_provider_http_attempts"]),
            (24, 0, 0),
        )
        self.assertEqual(
            failed["provider_quota_consumption_status"],
            stage13.PROVIDER_QUOTA_UNKNOWN_BECAUSE_NO_HTTP_RESPONSE,
        )
        self.assertEqual(failed["conservative_quota_accounting_attempts"], 24)
        gate = result_limit["additional_request_gate"]
        self.assertEqual(
            (
                gate["historical_initial_diagnostic_budget"],
                gate["prior_cumulative_client_request_invocations"],
                gate["explicit_additional_user_authorization"],
                gate["authorized_cumulative_client_attempt_ceiling"],
            ),
            (100, 90, 24, 114),
        )
        self.assertTrue(all(row["forward_temporal_span_status"] == "INVALID_FOR_FORWARD_COVERAGE_DECISION" for row in result_limit["historical_invalidated_sequential_records"]))

    def test_evidence_validation_is_offline_and_reproducible(self) -> None:
        with patch.object(stage13.socket, "getaddrinfo", side_effect=AssertionError("network access in offline validation")):
            stage13.validate_evidence(self.evidence)
            rebuilt = stage13.build_evidence(
                root=REPOSITORY_ROOT,
                generated_at_utc="2026-09-30T00:00:00Z",
                reuse_existing_live=True,
            )
            stage13.validate_evidence(rebuilt)
        self.assertEqual(rebuilt["stage_13_status"], FINAL_STATUS)
        self.assertEqual(rebuilt["static_event_density"]["final_approved_static_support"], self.evidence["static_event_density"]["final_approved_static_support"])

    def test_forward_coverage_uses_thiscall_arrivals_only(self) -> None:
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
                        },
                    ],
                },
                {"timetabled_time": "2026-09-30T11:15:00Z", "call_observations": []},
            ]
        }
        metrics = stage13._forward_coverage_metrics(bundle, anchor)
        self.assertEqual(metrics["returned_thiscall_event_count"], 2)
        self.assertEqual(metrics["events_before_anchor_count"], 0)
        self.assertEqual(metrics["forward_coverage_seconds"], 2100.0)

    def test_network_preflight_failure_issues_zero_mdd_requests(self) -> None:
        failed_preflight = {
            "status": "DNS_RESOLUTION_FAILED",
            "hostname": stage13.MDD_HOSTNAME,
            "port": stage13.MDD_PORT,
            "resolved_address_count": 0,
            "address_families": [],
            "safe_failure_category": "DNS_RESOLUTION_FAILED",
        }
        with patch.object(stage13, "_network_dns_preflight", return_value=failed_preflight), patch.object(
            stage8,
            "perform_http_request",
            side_effect=AssertionError("MDD HTTP request issued after failed preflight"),
        ):
            result = stage13._run_live_diagnostics(
                [],
                api_key="offline-test-key-123",
                include_concurrency_probe=False,
                attempt_budget=24,
            )
        self.assertEqual(result["client_request_attempts_issued"], 0)
        self.assertEqual(result["stop_reason"], "DNS_RESOLUTION_FAILED_ZERO_MDD_REQUESTS_ISSUED")

    def test_smoke_gate_and_authorization_failures_stop_immediately(self) -> None:
        rows = self.evidence["static_event_density"]["station_tier_rows"]
        preflight = {
            "status": "DNS_RESOLUTION_OK",
            "hostname": stage13.MDD_HOSTNAME,
            "port": stage13.MDD_PORT,
            "resolved_address_count": 1,
            "address_families": ["IPv4"],
            "safe_failure_category": None,
        }
        for status in (401, 403):
            with self.subTest(status=status):
                calls: list[object] = []
                auth_result = stage8.HTTPResult(
                    request_timestamp=datetime(2026, 9, 30, tzinfo=timezone.utc),
                    request_timestamp_utc="2026-09-30T00:00:00Z",
                    http_status=status,
                    content_type="application/xml",
                    body=b"",
                    elapsed_ms=1.0,
                    request_started_mono=1.0,
                    request_ended_mono=2.0,
                    failure=None,
                    failure_detail=f"HTTP_{status}",
                )

                def fake_request(*_args: object, **_kwargs: object) -> stage8.HTTPResult:
                    calls.append(object())
                    return auth_result

                with patch.object(stage13, "_network_dns_preflight", return_value=preflight), patch.object(
                    stage8, "perform_http_request", side_effect=fake_request
                ):
                    result = stage13._run_live_diagnostics(
                        rows,
                        api_key="offline-test-key-123",
                        include_concurrency_probe=False,
                        attempt_budget=24,
                    )
                self.assertEqual(len(calls), 1)
                self.assertEqual(result["client_request_attempts_issued"], 1)
                self.assertEqual(result["stop_reason"], "SMOKE_GATE_STOPPED_HTTP_401_OR_403")

    def test_transport_categories_are_allow_listed_and_secret_free(self) -> None:
        secret = "offline-secret-api-key-123"
        self.assertEqual(stage8.classify_transport_failure(ConnectionRefusedError(secret)), "CONNECTION_REFUSED")
        self.assertEqual(stage13._safe_transport_failure_category("NETWORK_OR_DNS_FAILURE"), "OTHER_NETWORK_FAILURE")
        self.assertIn(stage13._safe_transport_failure_category("NETWORK_OR_DNS_FAILURE"), stage13.SAFE_TRANSPORT_FAILURE_CATEGORIES)

        captured_stdout = StringIO()
        captured_stderr = StringIO()

        class FailingOpener:
            def open(self, *_args: object, **_kwargs: object) -> object:
                raise stage8.URLError(f"proxy credentials {secret} leaked")

        with patch.object(stage8, "build_opener", return_value=FailingOpener()), redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
            result = stage8.perform_http_request(secret, pilot_anchor_dep_arr_time="2026-09-30T00:00:00Z")
        self.assertEqual(result.failure, "PROXY_OR_ENVIRONMENT_FAILURE")
        self.assertNotIn(secret, repr(result))
        self.assertNotIn(secret, captured_stdout.getvalue())
        self.assertNotIn(secret, captured_stderr.getvalue())

    def test_no_secret_or_raw_payload_persistence(self) -> None:
        rendered = self.evidence_path.read_text(encoding="utf-8").lower()
        for forbidden in ('"x-api-key:', '"authorization:', '"body"', '"response_body"', '"api_key_persisted": true'):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
