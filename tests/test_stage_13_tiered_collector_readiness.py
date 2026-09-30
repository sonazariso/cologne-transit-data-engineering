from __future__ import annotations

import json
import socket
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

import stage_13_tiered_collector_readiness as stage13  # noqa: E402
import stage_08_mdd_realtime_pilot as stage8  # noqa: E402


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
            "CORRECTED_DIAGNOSTIC_COMPLETED_NO_VALID_RESPONSES_OTHER_NETWORK_FAILURE",
        )
        live = result_limit["bounded_live_diagnostic"]
        self.assertEqual(live["historical_client_diagnostic_attempts"], 90)
        self.assertEqual(live["corrected_client_request_attempts"], 24)
        self.assertEqual(live["cumulative_client_request_invocations"], 114)
        self.assertEqual(live["client_request_attempts_issued"], 24)
        self.assertEqual(live["http_responses_received"], 0)
        self.assertEqual(live["confirmed_provider_http_attempts"], 0)
        self.assertEqual(
            live["provider_quota_consumption_status"],
            stage13.PROVIDER_QUOTA_UNKNOWN_BECAUSE_NO_HTTP_RESPONSE,
        )
        self.assertEqual(live["conservative_quota_accounting_attempts"], 24)
        self.assertEqual(live["historical_initial_diagnostic_budget"], 100)
        self.assertEqual(live["explicit_additional_user_authorization"], 24)
        self.assertEqual(live["authorized_cumulative_client_attempt_ceiling"], 114)
        gate = result_limit["additional_request_gate"]
        self.assertTrue(gate["approved"])
        self.assertEqual(gate["requested_additional_client_request_attempts"], 24)
        self.assertEqual(gate["authorized_cumulative_client_attempt_ceiling"], 114)
        self.assertEqual(gate["historical_initial_diagnostic_budget"], 100)
        self.assertEqual(gate["explicit_additional_user_authorization"], 24)
        self.assertFalse(gate["historical_budget_rewritten"])
        self.assertEqual(gate["additional_client_request_attempts_issued"], 24)
        self.assertTrue(gate["corrected_live_diagnostic_issued"])
        self.assertEqual(live["result_limit_probe"]["requested_result_limits"], [5, 10, 20, 30])
        for summary in live["result_limit_probe"]["summary_by_result_limit"].values():
            self.assertEqual(summary["client_request_attempts_issued"], 6)
            self.assertEqual(summary["http_responses_received"], 0)
            self.assertEqual(summary["confirmed_provider_http_attempts"], 0)
            self.assertEqual(summary["http_status_distribution"], {"None": 6})
            self.assertEqual(summary["parser_success_count"], 0)
            self.assertEqual(summary["valid_trias_response_count"], 0)
            self.assertEqual(summary["legacy_forward_temporal_span_status"], "NOT_PRESENT")
            self.assertEqual(summary["forward_coverage_statistics_seconds"]["count"], 0)
            self.assertEqual(summary["transport_failure_category_distribution"], {"OTHER_NETWORK_FAILURE": 6})
        audit = result_limit["failed_corrected_diagnostic_audit"]
        self.assertEqual(audit["status"], "INVALID_FOR_NUMBER_OF_RESULTS_DECISION")
        self.assertEqual(audit["client_request_attempts_issued"], 24)
        self.assertEqual(audit["http_responses_received"], 0)
        self.assertEqual(audit["confirmed_provider_http_attempts"], 0)
        self.assertEqual(
            audit["provider_quota_consumption_status"],
            stage13.PROVIDER_QUOTA_UNKNOWN_BECAUSE_NO_HTTP_RESPONSE,
        )
        self.assertEqual(audit["conservative_quota_accounting_attempts"], 24)
        self.assertEqual(audit["transport_failure_category_distribution"], {"OTHER_NETWORK_FAILURE": 24})
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
                row["transport_failure"] == "OTHER_NETWORK_FAILURE"
                and row["transport_failure_category"] == "OTHER_NETWORK_FAILURE"
                and row["raw_payload_persisted"] is False
                for row in live["result_limit_probe"]["sequential_records"]
            )
        )

    def test_historical_sequential_and_cap2_accounting_reconciles_from_retained_rows(self) -> None:
        result_limit = self.evidence["number_of_results"]
        historical_records = result_limit["historical_invalidated_sequential_records"]
        self.assertEqual(len(historical_records), 24)
        historical_aggregate = self.evidence["sequential_vs_bounded_concurrency"]["sequential_live_diagnostic"]
        self.assertEqual(
            {
                key: historical_aggregate[key]
                for key in (
                    "client_request_attempts_issued",
                    "http_responses_received",
                    "confirmed_provider_http_attempts",
                )
            },
            {
                "client_request_attempts_issued": 24,
                "http_responses_received": 24,
                "confirmed_provider_http_attempts": 24,
            },
        )
        for number in (5, 10, 20, 30):
            with self.subTest(number=number):
                rows = [row for row in historical_records if row["requested_result_limit"] == number]
                summary = result_limit["historical_invalidated_sequential_summary_by_result_limit"][str(number)]
                self.assertEqual(len(rows), 6)
                self.assertNotIn("request_count", summary)
                self.assertEqual(summary["client_request_attempts_issued"], 6)
                self.assertEqual(summary["http_responses_received"], 6)
                self.assertEqual(summary["confirmed_provider_http_attempts"], 6)
                self.assertTrue(
                    all(
                        row["http_status"] == 200
                        and row["forward_temporal_span_status"] == "INVALID_FOR_FORWARD_COVERAGE_DECISION"
                        for row in rows
                    )
                )

        cap2 = self.evidence["sequential_vs_bounded_concurrency"]["bounded_live_diagnostic"]
        self.assertEqual(len(cap2["records"]), 6)
        self.assertEqual(
            {
                key: cap2[key]
                for key in (
                    "client_request_attempts_issued",
                    "http_responses_received",
                    "confirmed_provider_http_attempts",
                )
            },
            {
                "client_request_attempts_issued": 6,
                "http_responses_received": 6,
                "confirmed_provider_http_attempts": 6,
            },
        )
        self.assertTrue(all(row["http_status"] == 200 for row in cap2["records"]))

    def test_failed_diagnostic_documentation_and_boundaries_remain_conservative_and_offline(self) -> None:
        audit = self.evidence["number_of_results"]["failed_corrected_diagnostic_audit"]
        self.assertEqual(
            {
                key: audit[key]
                for key in (
                    "client_request_attempts_issued",
                    "http_responses_received",
                    "confirmed_provider_http_attempts",
                    "conservative_quota_accounting_attempts",
                )
            },
            {
                "client_request_attempts_issued": 24,
                "http_responses_received": 0,
                "confirmed_provider_http_attempts": 0,
                "conservative_quota_accounting_attempts": 24,
            },
        )
        self.assertEqual(
            audit["provider_quota_consumption_status"],
            stage13.PROVIDER_QUOTA_UNKNOWN_BECAUSE_NO_HTTP_RESPONSE,
        )

        documentation_paths = (
            "README.md",
            "CHANGELOG.md",
            "docs/GUARDRAILS.md",
            "docs/DECISION-LOG.md",
            "docs/PROJECT-STATUS.md",
            "docs/ROADMAP.md",
            "docs/stages/13-tiered-collector-readiness.md",
            "docs/stages/13-tiered-collector-readiness-completion-decision.md",
        )
        documentation = "\n".join(
            (REPOSITORY_ROOT / path).read_text(encoding="utf-8") for path in documentation_paths
        ).lower()
        for ambiguous_phrase in (
            "24 attempts were consumed",
            "exactly 24 additional attempts were consumed",
            "consumed exactly 24 additional sequential attempts",
            "exactly 24 additional attempts were authorized and consumed",
            "24 additional sequential mdd requests",
        ):
            self.assertNotIn(ambiguous_phrase, documentation)
        self.assertNotRegex(
            documentation,
            r"24[^\n.]{0,100}(provider (?:http )?requests|quota units)[^\n.]{0,40}(consumed|used)",
        )

        gate = self.evidence["number_of_results"]["additional_request_gate"]
        self.assertEqual(gate["historical_initial_diagnostic_budget"], 100)
        self.assertEqual(gate["explicit_additional_user_authorization"], 24)
        self.assertEqual(gate["authorized_cumulative_client_attempt_ceiling"], 114)
        self.assertEqual(
            self.evidence["stage_13_status"],
            "IN PROGRESS — EVIDENCE READY; USER DESIGN APPROVAL REQUIRED",
        )
        self.assertFalse(self.evidence["readiness_decision"]["stage_14_started"])
        self.assertIsNone(self.evidence["number_of_results"]["production_number_of_results"])

        with patch.object(stage13.socket, "getaddrinfo", side_effect=AssertionError("network access in offline validation")):
            stage13.validate_evidence(self.evidence)

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
        self.assertEqual(result["client_request_attempts_issued"], 0)
        self.assertEqual(result["http_responses_received"], 0)

    def test_request_accounting_does_not_infer_provider_quota_from_client_invocations(self) -> None:
        accounting = stage13._request_accounting(
            24,
            0,
            conservative_quota_accounting_attempts=24,
        )
        self.assertEqual(accounting["client_request_attempts_issued"], 24)
        self.assertEqual(accounting["http_responses_received"], 0)
        self.assertEqual(accounting["confirmed_provider_http_attempts"], 0)
        self.assertEqual(
            accounting["provider_quota_consumption_status"],
            stage13.PROVIDER_QUOTA_UNKNOWN_BECAUSE_NO_HTTP_RESPONSE,
        )
        self.assertEqual(accounting["conservative_quota_accounting_attempts"], 24)

    def test_zero_request_dns_preflight_stops_before_transport(self) -> None:
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
        self.assertEqual(result["network_preflight"]["status"], "DNS_RESOLUTION_FAILED")
        self.assertEqual(result["client_request_attempts_issued"], 0)
        self.assertEqual(result["http_responses_received"], 0)
        self.assertEqual(result["stop_reason"], "DNS_RESOLUTION_FAILED_ZERO_MDD_REQUESTS_ISSUED")

    def test_transport_classification_is_allow_listed_and_secret_free(self) -> None:
        self.assertEqual(
            stage8.classify_transport_failure(socket.gaierror("private host detail")),
            "DNS_RESOLUTION_FAILED",
        )
        self.assertEqual(
            stage8.classify_transport_failure(ConnectionRefusedError("private proxy detail")),
            "CONNECTION_REFUSED",
        )
        self.assertEqual(
            stage13._safe_transport_failure_category("NETWORK_OR_DNS_FAILURE"),
            "OTHER_NETWORK_FAILURE",
        )
        self.assertIn(
            stage13._safe_transport_failure_category("NETWORK_OR_DNS_FAILURE"),
            stage13.SAFE_TRANSPORT_FAILURE_CATEGORIES,
        )

    def test_smoke_gate_stops_after_first_environment_failure(self) -> None:
        rows = self.evidence["static_event_density"]["station_tier_rows"]
        calls: list[object] = []
        failed_result = stage8.HTTPResult(
            request_timestamp=datetime(2026, 9, 30, tzinfo=timezone.utc),
            request_timestamp_utc="2026-09-30T00:00:00Z",
            http_status=None,
            content_type=None,
            body=b"",
            elapsed_ms=1.0,
            request_started_mono=1.0,
            request_ended_mono=2.0,
            failure="DNS_RESOLUTION_FAILED",
            failure_detail="not persisted",
        )

        def fake_request(*_args: object, **_kwargs: object) -> stage8.HTTPResult:
            calls.append(object())
            return failed_result

        preflight = {
            "status": "DNS_RESOLUTION_OK",
            "hostname": stage13.MDD_HOSTNAME,
            "port": stage13.MDD_PORT,
            "resolved_address_count": 1,
            "address_families": ["IPv4"],
            "safe_failure_category": None,
        }
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
        self.assertEqual(24 - len(calls), 23)
        self.assertEqual(result["client_request_attempts_issued"], 1)
        self.assertEqual(
            result["stop_reason"],
            "SMOKE_GATE_STOPPED_ENVIRONMENT_FAILURE_DNS_RESOLUTION_FAILED",
        )
        self.assertEqual(result["result_limit_probe"]["smoke_gate"]["status"], "FAILED")

    def test_smoke_gate_stops_on_401_and_403_without_spending_remaining_budget(self) -> None:
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
                self.assertEqual(24 - len(calls), 23)
                self.assertEqual(result["client_request_attempts_issued"], 1)
                self.assertEqual(result["http_responses_received"], 1)
                self.assertEqual(result["stop_reason"], "SMOKE_GATE_STOPPED_HTTP_401_OR_403")
                self.assertEqual(result["result_limit_probe"]["smoke_gate"]["status"], "FAILED")
                self.assertEqual(
                    result["result_limit_probe"]["sequential_records"][0]["error_classification"],
                    "HTTP_AUTHORIZATION_FAILURE",
                )

    def test_transport_failure_details_are_category_only_and_never_log_api_key(self) -> None:
        api_key = "offline-secret-api-key-123"

        class FailingOpener:
            def open(self, *_args: object, **_kwargs: object) -> object:
                raise stage8.URLError(f"proxy credentials {api_key} leaked")

        captured_stdout = StringIO()
        captured_stderr = StringIO()
        with patch.object(stage8, "build_opener", return_value=FailingOpener()), redirect_stdout(
            captured_stdout
        ), redirect_stderr(captured_stderr):
            result = stage8.perform_http_request(
                api_key,
                pilot_anchor_dep_arr_time="2026-09-30T00:00:00Z",
            )

        self.assertEqual(result.failure, "PROXY_OR_ENVIRONMENT_FAILURE")
        self.assertEqual(result.failure_detail, "PROXY_OR_ENVIRONMENT_FAILURE")
        self.assertNotIn(api_key, repr(result))
        self.assertNotIn(api_key, captured_stdout.getvalue())
        self.assertNotIn(api_key, captured_stderr.getvalue())
        self.assertNotIn(
            api_key,
            repr(stage8.classify_transport_failure(OSError(f"proxy password {api_key}"))),
        )

    def test_historical_budget_and_cumulative_client_accounting_are_explicit(self) -> None:
        gate = self.evidence["number_of_results"]["additional_request_gate"]
        live = self.evidence["number_of_results"]["bounded_live_diagnostic"]
        self.assertEqual(gate["historical_initial_diagnostic_budget"], 100)
        self.assertEqual(gate["explicit_additional_user_authorization"], 24)
        self.assertEqual(gate["prior_cumulative_client_request_invocations"], 90)
        self.assertEqual(gate["authorized_cumulative_client_attempt_ceiling"], 114)
        self.assertEqual(live["historical_client_diagnostic_attempts"], 90)
        self.assertEqual(live["corrected_client_request_attempts"], 24)
        self.assertEqual(live["cumulative_client_request_invocations"], 114)
        self.assertFalse(gate["historical_budget_rewritten"])
        for key in (
            "http_attempts_consumed",
            "current_run_http_attempts_consumed",
            "prior_attempts_consumed",
            "http_attempts_consumed_total",
            "max_http_attempts",
        ):
            self.assertNotIn(key, live)
            self.assertNotIn(key, gate)

    def test_no_secret_or_raw_payload_persistence(self) -> None:
        rendered = self.evidence_path.read_text(encoding="utf-8").lower()
        self.assertNotIn("x-api-key:", rendered)
        self.assertNotIn("authorization:", rendered)
        self.assertNotIn('"body"', rendered)
        self.assertNotIn('"response_body"', rendered)
        self.assertNotIn('"api_key_persisted": true', rendered)


if __name__ == "__main__":
    unittest.main()
