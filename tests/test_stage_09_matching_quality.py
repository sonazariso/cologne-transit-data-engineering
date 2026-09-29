from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

import stage_08_mdd_realtime_pilot as stage8  # noqa: E402
import stage_09_matching_quality as stage9  # noqa: E402
import stage_09_source_backed_acceptance as stage9_source  # noqa: E402


class Stage9MatchingQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = stage9._fixture_bridge()
        self.schedule = stage9._fixture_schedule()
        self.observations = stage9._fixture_observations()
        self.synthetic_validation = stage9.MatchingEvidenceValidation(
            mapping_version="2026-09-28",
            validated_sequence_scopes=(
                (stage9.SOURCE_MDD, "VRS_GLOBAL_ID"),
                (stage9.SOURCE_GTFS_RT, "GTFS_DE"),
            ),
            validated_direction_mappings={
                (stage9.SOURCE_MDD, "vrs_global_id", "n"): ("N",),
                (stage9.SOURCE_GTFS_RT, "gtfs_de", "n"): ("N",),
            },
        )

    def test_offline_acceptance_population_passes_without_network(self) -> None:
        summary = stage9.run_offline_validation()
        self.assertEqual(summary["overall_status"], "PASS")
        self.assertEqual(summary["stage_9_status"], "BLOCKED")
        self.assertEqual(summary["implementation_regression_status"], "PASS")
        self.assertEqual(
            summary["stage_9_evidence_status"],
            "BLOCKED_SOURCE_BACKED_CORE_MATCH_POPULATION_NOT_ESTABLISHED",
        )
        self.assertEqual(summary["network_requests"], 0)
        self.assertEqual(summary["database_connections"], 0)
        self.assertTrue(all(summary["checks"].values()))

    def test_mdd_and_gtfsrt_known_good_events_match_the_same_canonical_event(self) -> None:
        mdd = stage9.match_source_event(
            self.observations[0], self.schedule, self.bridge,
            evidence_validation=self.synthetic_validation,
        )
        gtfsrt = stage9.match_source_event(
            self.observations[1], self.schedule, self.bridge,
            evidence_validation=self.synthetic_validation,
        )
        self.assertEqual(mdd.match_confidence, stage9.MATCH_HIGH)
        self.assertEqual(gtfsrt.match_confidence, stage9.MATCH_HIGH)
        self.assertEqual(mdd.stop_mapping_version, "2026-09-28")
        self.assertEqual(mdd.line_mapping_version, "2026-09-28")
        self.assertEqual(mdd.canonical_service_event_id, gtfsrt.canonical_service_event_id)
        cross_source = stage9.reconcile_cross_source([mdd, gtfsrt])
        self.assertEqual(len(cross_source["agreements"]), 1)
        self.assertFalse(cross_source["agreements"][0]["source_identifier_equality_used"])

    def test_mdd_journeyref_never_becomes_a_static_trip_key(self) -> None:
        decision = stage9.match_source_event(self.observations[0], self.schedule, self.bridge)
        self.assertFalse(decision.evidence["source_trip_identity_used"])
        self.assertTrue(decision.evidence["prohibited_cross_source_identity_equality_observed"])
        spoofed = replace(self.observations[0], source_trip_id="vrs-trip-neu-n")
        spoofed_decision = stage9.match_source_event(spoofed, self.schedule, self.bridge)
        self.assertFalse(spoofed_decision.evidence["source_trip_identity_used"])
        self.assertTrue(spoofed_decision.evidence["prohibited_cross_source_identity_equality_observed"])

    def test_ambiguous_top_candidates_are_unmatched(self) -> None:
        decision = stage9.match_source_event(self.observations[2], self.schedule, self.bridge)
        self.assertEqual(decision.match_confidence, stage9.MATCH_UNMATCHED)
        self.assertEqual(decision.match_method, stage9.METHOD_UNMATCHED)
        self.assertEqual(decision.data_quality_classification, stage9.QUALITY_AMBIGUOUS_MULTIPLE_CANDIDATES)
        self.assertEqual(decision.canonical_service_event_id, None)
        self.assertEqual(decision.candidate_count, 2)

    def test_unresolved_railway_hbf_does_not_use_adjacent_kvb_parent(self) -> None:
        decision = stage9.match_source_event(self.observations[3], self.schedule, self.bridge)
        self.assertEqual(decision.match_confidence, stage9.MATCH_UNMATCHED)
        self.assertEqual(decision.data_quality_classification, stage9.QUALITY_SOURCE_IDENTITY_UNRESOLVED)
        self.assertIn("no adjacent stop substitution", decision.evidence["detail"])

    def test_missing_service_date_is_not_replaced_by_observed_at(self) -> None:
        decision = stage9.match_source_event(self.observations[4], self.schedule, self.bridge)
        self.assertFalse(decision.matched)
        self.assertEqual(decision.data_quality_classification, stage9.QUALITY_MISSING_REQUIRED_IDENTITY)
        self.assertIn("observed_at is not a fallback", decision.evidence["detail"])

    def test_incomplete_collector_lineage_is_retained_as_a_quality_issue(self) -> None:
        observation = replace(self.observations[0], sampling_era_id=None)
        decision = stage9.match_source_event(
            observation,
            self.schedule,
            self.bridge,
            evidence_validation=self.synthetic_validation,
        )
        self.assertEqual(decision.match_confidence, stage9.MATCH_MEDIUM)
        self.assertEqual(decision.data_quality_classification, stage9.QUALITY_LINEAGE_INCOMPLETE)
        self.assertEqual(decision.evidence["lineage_missing_fields"], ["sampling_era_id"])
        audit = stage9.audit_observation_lineage([observation])
        self.assertEqual(audit["status"], "WARN")
        self.assertEqual(audit["lineage_incomplete_count"], 1)

    def test_unresolved_line_identity_is_not_promoted_to_validated_match(self) -> None:
        observation = replace(self.observations[0], source_line_id="line-not-in-bridge")
        decision = stage9.match_source_event(
            observation,
            self.schedule,
            self.bridge,
            evidence_validation=self.synthetic_validation,
        )
        self.assertEqual(decision.match_confidence, stage9.MATCH_MEDIUM)
        self.assertEqual(
            decision.data_quality_classification,
            stage9.QUALITY_SOURCE_LINE_IDENTITY_UNRESOLVED,
        )

    def test_repeated_observations_remain_distinct_across_sampling_lineage(self) -> None:
        continuity = stage9.audit_observation_continuity(self.observations)
        self.assertTrue(continuity["repeated_observations_are_not_collapsed"])
        self.assertEqual(continuity["observation_count"], len(self.observations))
        self.assertEqual(continuity["distinct_sampling_era_count"], 2)

    def test_false_is_present_and_not_missing(self) -> None:
        coverage = stage9.audit_field_coverage(
            [
                stage9.SourceEventObservation.from_mapping(
                    {
                        "source_system": stage9.SOURCE_MDD,
                        "source_namespace": "VRS_GLOBAL_ID",
                        "source_event_id": "false-test",
                        "observation_id": "false-test-observation",
                        "observed_at": "2026-09-29T10:00:00Z",
                        "service_date": "2026-09-29",
                        "stop_point_ref": "de:05315:11111",
                        "event_type": "ARRIVAL",
                        "scheduled_time": "10:15:00",
                        "collector_run_id": "false-test-run",
                        "sampling_policy_id": "P1",
                        "sampling_era_id": "E1",
                        "source_attributes": {"cancelled": False},
                    }
                )
            ]
        )
        self.assertEqual(coverage["status"], "PASS")
        self.assertTrue(stage9._present(False))

    def test_extended_gtfs_time_is_not_wrapped_at_midnight(self) -> None:
        self.assertEqual(stage9.schedule_time_seconds("25:05:00"), 90_300)
        self.assertEqual(stage9.schedule_time_seconds("47:59:59"), 172_799)

    def test_route_alert_is_context_not_causality(self) -> None:
        report = stage9.classify_alert_association(
            {"alert_id": "a1", "association_scope": "ROUTE", "reported_cause": "CONGESTION"},
            [],
        )
        self.assertEqual(report["association_status"], "ROUTE_CONTEXT_ONLY")
        self.assertFalse(report["causal_claim_permitted"])

    def test_gtfs_trip_alert_can_match_same_source_trip_context_only(self) -> None:
        decision = stage9.match_source_event(
            self.observations[1], self.schedule, self.bridge,
            evidence_validation=self.synthetic_validation,
        )
        report = stage9.classify_alert_association(
            {
                "alert_id": "trip-alert",
                "association_scope": "TRIP",
                "source_namespace": "GTFS_DE",
                "trip_id": "gtfs-trip-neu-1",
            },
            [decision],
        )
        self.assertEqual(report["association_status"], "TRIP_CONTEXT_HIGH_MATCH")
        self.assertFalse(report["causal_claim_permitted"])

    def test_stage8_parser_retains_richer_mdd_identity_fields(self) -> None:
        body = json.dumps(
            {
                "serviceDelivery": {
                    "status": "OK",
                    "stopEventResponse": {
                        "stopEventResult": [
                            {
                                "resultId": "rich-result-1",
                                "stopEvent": {
                                    "service": {
                                        "journeyRef": "mdd-journey-1",
                                        "vehicleJourneyRef": "mdd-vj-1",
                                        "datedVehicleJourneyRef": "mdd-dvj-1",
                                        "affectedVehicleJourney": "mdd-affected-1",
                                        "vehicleJourneys": ["mdd-vj-1"],
                                        "operatingDaysDescription": "daily",
                                        "direction": "north",
                                        "origins": ["origin-source"],
                                        "destinations": ["destination-source"],
                                    },
                                    "thisCall": [
                                        {
                                            "callAtStop": {
                                                "stopPointRef": "de:05315:11111",
                                                "stopSeqNumber": 7,
                                                "serviceArrival": {"timetabledTime": "2026-09-29T10:15:00Z"},
                                            }
                                        }
                                    ],
                                },
                            }
                        ]
                    },
                }
            }
        ).encode("utf-8")
        parsed = stage8.parse_response(body, "application/json")
        bundle = stage8.build_bundle(
            parsed=parsed,
            response_id="rich-response",
            run_id="rich-run",
            observed_at="2026-09-29T10:00:00Z",
        )
        record = bundle["stop_events"][0]
        extensions = record["source_identity_extensions"]
        self.assertEqual(extensions["vehicle_journey_ref"]["value"], "mdd-vj-1")
        self.assertEqual(extensions["dated_vehicle_journey_ref"]["value"], "mdd-dvj-1")
        self.assertEqual(extensions["affected_vehicle_journey"]["value"], "mdd-affected-1")
        self.assertEqual(extensions["operating_days_description"]["value"], "daily")
        self.assertFalse(
            any(
                item["field_name"] in {
                    "vehicleJourneyRef",
                    "datedVehicleJourneyRef",
                    "affectedVehicleJourney",
                    "vehicleJourneys",
                }
                for item in parsed.parser_gaps
            )
        )

    def test_stage8_parser_bundle_adapter_and_match_are_end_to_end(self) -> None:
        body = json.dumps(
            {
                "serviceDelivery": {
                    "status": "OK",
                    "stopEventResponse": {
                        "stopEventResult": [
                            {
                                "resultId": "integration-result-1",
                                "stopEvent": {
                                    "service": {
                                        "operatingDayRef": {"value": "20260929"},
                                        "journeyRef": {"value": "mdd-journey-1"},
                                        "lineRef": {"value": "mdd-line-18"},
                                        "directionRef": {"value": "N"},
                                        "operatorRef": {"value": "KVB"},
                                        "originStopPointRef": {"value": "de:05315:10000"},
                                        "destinationStopPointRef": {"value": "de:05315:20000"},
                                        "originText": [{"text": "Origin"}],
                                        "destinationText": [{"text": "Destination"}],
                                    },
                                    "thisCall": [
                                        {
                                            "callAtStop": {
                                                "stopPointRef": {"value": "de:05315:11111"},
                                                "stopSeqNumber": 7,
                                                "serviceArrival": {
                                                    "timetabledTime": "2026-09-29T08:15:00Z[GMT]"
                                                },
                                            }
                                        }
                                    ],
                                    "previousCall": [
                                        {
                                            "callAtStop": {
                                                "stopPointRef": {"value": "de:05315:00000"},
                                                "serviceArrival": {
                                                    "timetabledTime": "2026-09-29T08:00:00Z[GMT]"
                                                },
                                            }
                                        }
                                    ],
                                    "onwardCall": [
                                        {
                                            "callAtStop": {
                                                "stopPointRef": {"value": "de:05315:22222"},
                                                "serviceArrival": {
                                                    "timetabledTime": "2026-09-29T08:30:00Z[GMT]"
                                                },
                                            }
                                        }
                                    ],
                                },
                            }
                        ]
                    },
                }
            }
        ).encode("utf-8")
        parsed = stage8.parse_response(body, "application/json")
        bundle = stage8.build_bundle(
            parsed=parsed,
            response_id="integration-response",
            run_id="integration-run",
            observed_at="2026-09-29T08:00:00Z",
            sampling_policy_id="MDD_STAGE_8_KOELN_HBF_3S_60MIN_V3",
            sampling_era_id="ERA_STAGE_8_V3",
        )
        observation = stage9.adapt_stage8_stop_event(
            bundle["stop_events"][0],
            service_date="2026-09-29",
        )
        decision = stage9.match_source_event(
            observation,
            self.schedule,
            self.bridge,
            evidence_validation=self.synthetic_validation,
        )
        self.assertEqual(observation.source_system, stage9.SOURCE_MDD)
        self.assertEqual(observation.source_namespace, "VRS_GLOBAL_ID")
        self.assertEqual(observation.source_event_id, bundle["stop_events"][0]["event_source_identity"])
        self.assertEqual(observation.observation_id, "integration-response|stop_event|0")
        self.assertEqual(observation.operating_day_ref, "20260929")
        self.assertEqual(observation.normalized_service_date, "2026-09-29")
        self.assertEqual(observation.source_stop_id, "de:05315:11111")
        self.assertEqual(observation.scheduled_time_seconds, 10 * 3600 + 15 * 60)
        self.assertEqual(observation.stop_sequence, 7)
        self.assertEqual(observation.origin_stop_point_ref, "de:05315:10000")
        self.assertEqual(observation.destination_stop_point_ref, "de:05315:20000")
        self.assertEqual(observation.origin_text, "Origin")
        self.assertEqual(observation.destination_text, "Destination")
        self.assertEqual(observation.direction_ref, "N")
        self.assertIsNone(observation.direction)
        self.assertEqual(observation.sampling_policy_id, "MDD_STAGE_8_KOELN_HBF_3S_60MIN_V3")
        self.assertEqual(observation.sampling_era_id, "ERA_STAGE_8_V3")
        self.assertIn("THIS", observation.source_attributes["selected_call_scope"])
        self.assertEqual(
            observation.source_attributes["non_selected_call_scopes"],
            ["ONWARD", "PREVIOUS"],
        )
        self.assertIn("serviceArrival.timetabledTime", observation.source_paths["scheduled_time"] if "scheduled_time" in observation.source_paths else observation.source_attributes["scheduled_time_source_path"])
        self.assertTrue(observation.source_paths["origin_stop_point_ref"])
        self.assertTrue(observation.source_paths["destination_stop_point_ref"])
        self.assertEqual(decision.match_confidence, stage9.MATCH_HIGH)
        self.assertTrue(decision.matched)

    def test_source_backed_preparation_adapts_raw_this_call_before_filtering(self) -> None:
        body = json.dumps(
            {
                "serviceDelivery": {
                    "status": "OK",
                    "stopEventResponse": {
                        "stopEventResult": [
                            {
                                "resultId": "preparation-result-1",
                                "stopEvent": {
                                    "service": {
                                        "operatingDayRef": {"value": "20260929"},
                                        "lineRef": {"value": "mdd-line-18"},
                                    },
                                    "previousCall": [
                                        {
                                            "callAtStop": {
                                                "stopPointRef": {"value": "de:05315:00000"},
                                                "serviceArrival": {
                                                    "timetabledTime": "2026-09-29T08:00:00Z[GMT]"
                                                },
                                            }
                                        }
                                    ],
                                    "thisCall": [
                                        {
                                            "callAtStop": {
                                                "stopPointRef": {"value": "de:05315:11111"},
                                                "stopSeqNumber": 7,
                                                "serviceArrival": {
                                                    "timetabledTime": "2026-09-29T08:15:00Z[GMT]"
                                                },
                                            }
                                        }
                                    ],
                                    "onwardCall": [
                                        {
                                            "callAtStop": {
                                                "stopPointRef": {"value": "de:05315:22222"},
                                                "serviceArrival": {
                                                    "timetabledTime": "2026-09-29T08:30:00Z[GMT]"
                                                },
                                            }
                                        }
                                    ],
                                },
                            }
                        ]
                    },
                }
            }
        ).encode("utf-8")
        parsed = stage8.parse_response(body, "application/json")
        bundle = stage8.build_bundle(
            parsed=parsed,
            response_id="preparation-response",
            run_id="preparation-run",
            observed_at="2026-09-29T08:00:00Z",
        )
        raw_record = bundle["stop_events"][0]

        self.assertNotIn("source_attributes", raw_record)
        self.assertTrue(
            any(
                call.get("call_scope") == "THIS"
                for call in raw_record["call_observations"]
            )
        )
        self.assertNotEqual(
            raw_record.get("source_attributes", {}).get("selected_call_scope"),
            "THIS",
        )

        prepared, diagnostics = stage9_source._prepare_preliminary_observations(
            [
                (
                    raw_record,
                    {"target_index": 0, "bundle_stop_event_count": len(bundle["stop_events"])},
                )
            ]
        )

        self.assertEqual(len(prepared), 1)
        preliminary = prepared[0][2]
        self.assertEqual(preliminary.source_attributes["selected_call_scope"], "THIS")
        self.assertEqual(preliminary.scheduled_time, "2026-09-29T08:15:00Z[GMT]")
        self.assertEqual(
            diagnostics["0"],
            {
                "bundle_stop_event_count": 1,
                "raw_stop_events_with_this_call_count": 1,
                "adapted_stop_events_with_selected_this_count": 1,
                "adapted_stop_events_missing_this_count": 0,
            },
        )

    def test_stage8_adapter_does_not_use_previous_or_onward_timing(self) -> None:
        record = {
            "record_type": "stop_event_observation",
            "source_system": "MDD_TRIAS",
            "source_namespace": "VRS_GLOBAL_ID",
            "event_source_identity": "event-1",
            "stop_event_observation_id": "observation-1",
            "pilot_run_id": "run-1",
            "response_id": "response-1",
            "observed_at": "2026-09-29T08:00:00Z",
            "operating_day_ref": "20260929",
            "stop_point_ref": "de:05315:11111",
            "line_ref": "mdd-line-18",
            "event_type": "ARRIVAL",
            "call_observations": [
                {
                    "call_scope": "PREVIOUS",
                    "stop_point_ref": "de:05315:00000",
                    "arrival_timing": [{"field_values": {"TimetabledTime": ["2026-09-29T08:00:00Z"]}}],
                },
                {
                    "call_scope": "ONWARD",
                    "stop_point_ref": "de:05315:22222",
                    "arrival_timing": [{"field_values": {"TimetabledTime": ["2026-09-29T08:30:00Z"]}}],
                },
            ],
        }
        observation = stage9.adapt_stage8_stop_event(record, service_date="2026-09-29")
        self.assertIsNone(observation.scheduled_time)
        self.assertIsNone(observation.scheduled_time_seconds)
        self.assertEqual(observation.scheduled_time_alignment["status"], stage9.TIME_ALIGNMENT_INVALID)

    def test_operating_day_ref_is_not_aliased_to_service_date(self) -> None:
        observation = stage9.SourceEventObservation.from_mapping(
            {
                "source_system": stage9.SOURCE_MDD,
                "source_namespace": "VRS_GLOBAL_ID",
                "source_event_id": "date-test",
                "observation_id": "date-test-observation",
                "observed_at": "2026-09-29T10:00:00Z",
                "operating_day_ref": "20260929",
                "stop_point_ref": "de:05315:11111",
                "event_type": "ARRIVAL",
                "scheduled_time": "10:15:00",
            }
        )
        self.assertIsNone(observation.service_date)
        self.assertEqual(observation.operating_day_ref, "20260929")
        report = stage9.validate_service_date_alignment([observation])
        self.assertEqual(report["missing_service_date_count"], 1)
        self.assertEqual(report["operating_day_ref"]["present_count"], 1)
        self.assertEqual(report["operating_day_ref"]["unresolved_count"], 1)

    def test_absolute_timetable_alignment_handles_timezone_dst_midnight_and_extended_hours(self) -> None:
        self.assertEqual(
            stage9.schedule_time_seconds(
                "2026-01-15T08:00:00Z[GMT]",
                service_date="2026-01-15",
            ),
            9 * 3600,
        )
        self.assertEqual(
            stage9.schedule_time_seconds(
                "2026-07-01T08:00:00Z[GMT]",
                service_date="2026-07-01",
            ),
            10 * 3600,
        )
        self.assertEqual(
            stage9.schedule_time_seconds(
                "2026-03-29T01:30:00Z",
                service_date="2026-03-29",
            ),
            3 * 3600 + 30 * 60,
        )
        self.assertEqual(
            stage9.schedule_time_seconds(
                "2026-09-28T22:30:00Z",
                service_date="2026-09-29",
            ),
            30 * 60,
        )
        self.assertEqual(
            stage9.schedule_time_seconds(
                "2026-09-29T23:05:00Z",
                service_date="2026-09-29",
            ),
            25 * 3600 + 5 * 60,
        )
        fall_back_first = stage9.align_schedule_time(
            "2026-10-25T00:30:00Z",
            service_date="2026-10-25",
        )
        fall_back_second = stage9.align_schedule_time(
            "2026-10-25T01:30:00Z",
            service_date="2026-10-25",
        )
        self.assertEqual(fall_back_first["service_day_seconds"], 2 * 3600 + 30 * 60)
        self.assertEqual(fall_back_second["service_day_seconds"], 2 * 3600 + 30 * 60)
        self.assertIn("+02:00", fall_back_first["local_timestamp"])
        self.assertIn("+01:00", fall_back_second["local_timestamp"])
        self.assertIsNone(
            stage9.schedule_time_seconds(
                "2026-09-29T10:15:00",
                service_date="2026-09-29",
            )
        )

    def test_timezone_unresolved_absolute_time_is_insufficient_evidence(self) -> None:
        observation = replace(
            self.observations[0],
            scheduled_time="2026-09-29T10:15:00",
        )
        decision = stage9.match_source_event(observation, self.schedule, self.bridge)
        self.assertEqual(decision.match_confidence, stage9.MATCH_UNMATCHED)
        self.assertEqual(decision.data_quality_classification, stage9.QUALITY_INSUFFICIENT_EVIDENCE)
        self.assertEqual(
            decision.evidence["scheduled_time_alignment"]["status"],
            stage9.TIME_ALIGNMENT_TIMEZONE_UNRESOLVED,
        )

    def test_sequence_and_direction_supporting_evidence_do_not_raise_high_by_themselves(self) -> None:
        decision = stage9.match_source_event(self.observations[0], self.schedule, self.bridge)
        self.assertEqual(decision.match_confidence, stage9.MATCH_UNMATCHED)
        self.assertEqual(
            decision.data_quality_classification,
            stage9.QUALITY_AMBIGUOUS_MULTIPLE_CANDIDATES,
        )
        candidate_evidence = list(decision.evidence["candidate_evidence"].values())
        self.assertTrue(any(item["stop_sequence_match"] for item in candidate_evidence))
        self.assertTrue(any(item["direction_match"] for item in candidate_evidence))
        self.assertTrue(all(not item["stop_sequence_evidence_validated"] for item in candidate_evidence))
        self.assertTrue(all(not item["direction_evidence_validated"] for item in candidate_evidence))

    def test_unvalidated_stop_sequence_cannot_break_candidate_tie(self) -> None:
        rows = [
            stage9.StaticServiceEvent(
                feed_snapshot_key="tie-feed",
                source_namespace="VRS_GLOBAL_ID",
                source_trip_id="trip-sequence-match",
                source_route_id="route-18",
                source_stop_id="de:05315:11111",
                canonical_stop_id="CS:COLOGNE:NEUMARKT",
                canonical_line_id="CL:KVB:18",
                service_date="2026-09-29",
                event_type="ARRIVAL",
                scheduled_time="10:15:00",
                direction="N",
                stop_sequence=7,
                source_row_identity="tie:sequence:7",
            ),
            stage9.StaticServiceEvent(
                feed_snapshot_key="tie-feed",
                source_namespace="VRS_GLOBAL_ID",
                source_trip_id="trip-sequence-other",
                source_route_id="route-18",
                source_stop_id="de:05315:11111",
                canonical_stop_id="CS:COLOGNE:NEUMARKT",
                canonical_line_id="CL:KVB:18",
                service_date="2026-09-29",
                event_type="ARRIVAL",
                scheduled_time="10:15:00",
                direction="N",
                stop_sequence=8,
                source_row_identity="tie:sequence:8",
            ),
        ]
        schedule = stage9.build_schedule_index(rows)
        observation = replace(self.observations[0], direction=None, direction_ref=None, stop_sequence=7)
        decision = stage9.match_source_event(observation, schedule, self.bridge)
        self.assertEqual(decision.data_quality_classification, stage9.QUALITY_AMBIGUOUS_MULTIPLE_CANDIDATES)
        self.assertEqual(decision.candidate_count, 2)
        self.assertIsNone(decision.canonical_service_event_id)

    def test_unvalidated_direction_cannot_break_candidate_tie(self) -> None:
        observation = replace(self.observations[0], stop_sequence=None)
        decision = stage9.match_source_event(observation, self.schedule, self.bridge)
        self.assertEqual(decision.data_quality_classification, stage9.QUALITY_AMBIGUOUS_MULTIPLE_CANDIDATES)
        self.assertEqual(decision.candidate_count, 2)
        self.assertIsNone(decision.canonical_service_event_id)

    def test_validated_auxiliary_direction_resolves_an_otherwise_ambiguous_candidate(self) -> None:
        observation = replace(self.observations[0], stop_sequence=None)
        decision = stage9.match_source_event(
            observation,
            self.schedule,
            self.bridge,
            evidence_validation=self.synthetic_validation,
        )
        self.assertTrue(decision.matched)
        self.assertEqual(decision.match_confidence, stage9.MATCH_HIGH)
        self.assertEqual(decision.evidence["direction_evidence_validated"], True)

    def test_versioned_mapping_validity_blocks_future_mapping_for_historical_event(self) -> None:
        bridge = stage9.IdentityBridge(
            stop_mappings=[
                stage9.IdentityMapping(
                    stage9.SOURCE_MDD,
                    "VRS_GLOBAL_ID",
                    "historical-stop",
                    "CS:OLD",
                    "map-v2",
                    valid_from="2026-10-01",
                )
            ],
            active_mapping_version="map-v2",
        )
        resolution = bridge.resolve_stop(
            stage9.SOURCE_MDD,
            "VRS_GLOBAL_ID",
            "historical-stop",
            service_date="2026-09-29",
        )
        self.assertEqual(resolution.status, "NO_VALID_MAPPING_FOR_DATE")
        self.assertIsNone(resolution.canonical_identifier)

    def test_overlapping_conflicting_mapping_windows_are_detected(self) -> None:
        bridge = stage9.IdentityBridge(
            stop_mappings=[
                stage9.IdentityMapping(
                    stage9.SOURCE_MDD,
                    "VRS_GLOBAL_ID",
                    "overlap-stop",
                    "CS:ONE",
                    "map-v1",
                    valid_from="2026-01-01",
                    valid_to="2026-12-31",
                ),
                stage9.IdentityMapping(
                    stage9.SOURCE_MDD,
                    "VRS_GLOBAL_ID",
                    "overlap-stop",
                    "CS:TWO",
                    "map-v1",
                    valid_from="2026-06-01",
                    valid_to="2027-01-01",
                ),
            ],
            active_mapping_version="map-v1",
        )
        report = bridge.validation_report()
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["stops"]["overlapping_conflict_count"], 1)
        resolution = bridge.resolve_stop(
            stage9.SOURCE_MDD,
            "VRS_GLOBAL_ID",
            "overlap-stop",
            service_date="2026-07-01",
        )
        self.assertEqual(resolution.status, "AMBIGUOUS")

    def test_overlapping_conflicting_mapping_versions_are_detected(self) -> None:
        bridge = stage9.IdentityBridge(
            stop_mappings=[
                stage9.IdentityMapping(
                    stage9.SOURCE_MDD,
                    "VRS_GLOBAL_ID",
                    "cross-version-overlap-stop",
                    "CS:ONE",
                    "map-v1",
                    valid_from="2026-01-01",
                    valid_to="2026-12-31",
                ),
                stage9.IdentityMapping(
                    stage9.SOURCE_MDD,
                    "VRS_GLOBAL_ID",
                    "cross-version-overlap-stop",
                    "CS:TWO",
                    "map-v2",
                    valid_from="2026-06-01",
                    valid_to="2027-01-01",
                ),
            ]
        )
        report = bridge.validation_report()
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["stops"]["overlapping_conflict_count"], 1)
        overlap = report["stops"]["overlap_examples"][0]
        self.assertEqual(overlap["left_mapping_version"], "map-v1")
        self.assertEqual(overlap["right_mapping_version"], "map-v2")
        self.assertTrue(overlap["conflicting"])

    def test_non_overlapping_mapping_versions_resolve_by_service_date(self) -> None:
        bridge = stage9.IdentityBridge(
            stop_mappings=[
                stage9.IdentityMapping(
                    stage9.SOURCE_MDD,
                    "VRS_GLOBAL_ID",
                    "versioned-stop",
                    "CS:OLD",
                    "map-v1",
                    valid_to="2026-09-28",
                ),
                stage9.IdentityMapping(
                    stage9.SOURCE_MDD,
                    "VRS_GLOBAL_ID",
                    "versioned-stop",
                    "CS:NEW",
                    "map-v2",
                    valid_from="2026-09-29",
                ),
            ]
        )
        self.assertEqual(bridge.validation_report()["status"], "PASS")
        old = bridge.resolve_stop(
            stage9.SOURCE_MDD,
            "VRS_GLOBAL_ID",
            "versioned-stop",
            service_date="2026-09-28",
        )
        new = bridge.resolve_stop(
            stage9.SOURCE_MDD,
            "VRS_GLOBAL_ID",
            "versioned-stop",
            service_date="2026-09-29",
        )
        self.assertEqual(old.canonical_identifier, "CS:OLD")
        self.assertEqual(new.canonical_identifier, "CS:NEW")

    def test_sql_overlap_review_includes_cross_version_ranges_and_targets(self) -> None:
        sql = (REPOSITORY_ROOT / "sql" / "stage_09_matching_quality.sql").read_text(encoding="utf-8")
        view_sql = sql.split("CREATE OR REPLACE VIEW wrk.v_identity_mapping_overlap_conflicts AS", 1)[1]
        view_sql = view_sql.split("COMMENT ON TABLE", 1)[0]
        self.assertNotIn("right_row.mapping_version = left_row.mapping_version", view_sql)
        for fragment in (
            "left_mapping_version",
            "right_mapping_version",
            "left_valid_from",
            "left_valid_to",
            "right_valid_from",
            "right_valid_to",
            "left_canonical_key",
            "right_canonical_key",
            "conflicting",
        ):
            self.assertIn(fragment, view_sql)

    def _valid_source_backed_row(self) -> dict:
        candidate = {
            "canonical_service_event_id": "CSE|feed-v1|event-1",
            "feed_snapshot_key": "feed-v1",
            "service_date": "2026-09-29",
            "event_type": "ARRIVAL",
            "stop_id": "de:05315:11111",
        }
        return {
            "source_system": stage9.SOURCE_MDD,
            "source_namespace": "VRS_GLOBAL_ID",
            "mdd_source_event_identity": "mdd-event-1",
            "mdd_source_observation_identity": "mdd-observation-1",
            "source_event_identity": "MDD_TRIAS|VRS_GLOBAL_ID|mdd-event-1",
            "source_observation_identity": "mdd-observation-1",
            "response_id": "response-1",
            "collector_run_id": "run-1",
            "observation_timestamp": "2026-09-29T10:00:00Z",
            "operating_day_ref": "2026-09-29",
            "stop_point_ref": "de:05315:11111",
            "stop_seq_number": 7,
            "timetabled_time": "2026-09-29T10:15:00Z",
            "selected_this_call_source_path": "$.thisCall[0]",
            "selected_this_call_observation_id": "call-1",
            "scheduled_time_source_path": "$.thisCall[0].arrivalTiming[0].TimetabledTime",
            "stage8_selected_this_call_lineage": {
                "selected_call_scope": "THIS",
                "selected_call_observation_id": "call-1",
                "selected_call_source_path": "$.thisCall[0]",
                "scheduled_time_source_path": "$.thisCall[0].arrivalTiming[0].TimetabledTime",
            },
            "static_feed_version": "feed-v1",
            "static_feed_sha256": "feed-hash",
            "independent_gtfs_service_date": "2026-09-29",
            "independent_service_date_status": "ESTABLISHED",
            "independent_service_date_evidence": {"status": "ESTABLISHED", "matching_candidate_event_ids": [candidate["canonical_service_event_id"]]},
            "static_candidate_identities": [candidate],
            "selected_candidate": candidate,
            "selected_candidate_id": candidate["canonical_service_event_id"],
            "candidate_count": 1,
            "candidate_ids": [candidate["canonical_service_event_id"]],
            "confidence": stage9.MATCH_MEDIUM,
            "match_method": stage9.METHOD_STOP_TIME_CONTEXT_ONLY,
            "data_quality_classification": stage9.QUALITY_CONTEXTUAL_MATCH,
            "selected_score": 10,
            "decision_evidence": {
                "canonical_stop_match": True,
                "service_date_match": True,
                "event_type_match": True,
                "scheduled_time_match": True,
            },
            "stop_hierarchy": {"static_stop_found": True, "explicit_parent_relationship": True},
            "stop_hierarchy_evidence": {"static_stop_found": True, "explicit_parent_relationship": True},
            "identity_mapping_version": {"requested": "VRS_GTFS|feed-v1", "stop": "VRS_GTFS|feed-v1", "line": None},
            "evidence_used": {"exact_stop_identity": True, "independent_gtfs_service_date": True},
            "evidence_explicitly_not_used": ["OperatingDayRef as the input GTFS service date"],
        }

    def _valid_source_backed_summary(self) -> dict:
        return {
            "source_backed_observation_count": 1,
            "source_backed_confidence_counts": {"HIGH": 0, "MEDIUM": 1, "LOW": 0, "UNMATCHED": 0},
            "ambiguity_count": 0,
            "independently_established_service_date": {
                "count": 1,
                "unresolved_count": 0,
                "status_counts": {"ESTABLISHED": 1},
            },
            "core_deterministic_match_count": 1,
            "decision_classification_counts": {stage9.QUALITY_CONTEXTUAL_MATCH: 1},
        }

    def _validate_temp_source_artifact(self, row: dict, summary: dict | None = None) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            evidence_path = Path(directory) / "evidence.jsonl"
            evidence_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            return stage9_source.validate_source_backed_evidence_artifact(
                evidence_path,
                summary or self._valid_source_backed_summary(),
            )

    def test_required_source_fields_with_null_values_fail_integrity_gate(self) -> None:
        row = self._valid_source_backed_row()
        row["confidence"] = None
        row["static_candidate_identities"] = None
        row["evidence_used"] = None
        report = self._validate_temp_source_artifact(row)
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(any("confidence" in error for error in report["errors"]))
        self.assertTrue(any("static_candidate_identities" in error for error in report["errors"]))
        self.assertTrue(any("evidence_used" in error for error in report["errors"]))

    def test_malformed_jsonl_cannot_complete_source_backed_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evidence_path = Path(directory) / "evidence.jsonl"
            evidence_path.write_text('{"confidence":\n', encoding="utf-8")
            report = stage9_source.validate_source_backed_evidence_artifact(
                evidence_path,
                self._valid_source_backed_summary(),
            )
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(
            stage9_source.source_backed_acceptance_status(
                evidence_origin=stage9_source.SOURCE_BACKED_EVIDENCE_ORIGIN,
                static_validation_status="PASS",
                source_observation_count=1,
                independent_service_date_count=1,
                core_deterministic_match_count=1,
                mdd_http_attempts=1,
                artifact_written=True,
                artifact_integrity_status=report["status"],
            ),
            "BLOCKED",
        )

    def test_summary_confidence_counts_must_reconcile_with_jsonl(self) -> None:
        summary = self._valid_source_backed_summary()
        summary["source_backed_confidence_counts"] = {"HIGH": 1, "MEDIUM": 0, "LOW": 0, "UNMATCHED": 0}
        report = self._validate_temp_source_artifact(self._valid_source_backed_row(), summary)
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(any("confidence counts do not reconcile" in error for error in report["errors"]))

    def test_established_service_date_rows_must_retain_their_gtfs_date(self) -> None:
        row = self._valid_source_backed_row()
        row["independent_gtfs_service_date"] = None
        report = self._validate_temp_source_artifact(row)
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(any("established service-date row" in error for error in report["errors"]))

    def test_matched_rows_must_retain_selected_candidate_identity(self) -> None:
        row = self._valid_source_backed_row()
        row["selected_candidate"] = None
        row["selected_candidate_id"] = None
        report = self._validate_temp_source_artifact(row)
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(any("non-UNMATCHED row has no selected candidate" in error for error in report["errors"]))

    def test_valid_row_level_evidence_passes_integrity_gate(self) -> None:
        row = self._valid_source_backed_row()
        report = self._validate_temp_source_artifact(row)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(
            stage9_source.source_backed_acceptance_status(
                evidence_origin=stage9_source.SOURCE_BACKED_EVIDENCE_ORIGIN,
                static_validation_status="PASS",
                source_observation_count=1,
                independent_service_date_count=1,
                core_deterministic_match_count=1,
                mdd_http_attempts=1,
                artifact_written=True,
                artifact_integrity_status=report["status"],
            ),
            "PASS",
        )

    def test_jsonl_serialization_retains_decision_fields_without_truncation(self) -> None:
        observation = self.observations[0]
        decision = stage9.match_source_event(
            observation,
            self.schedule,
            self.bridge,
            evidence_validation=self.synthetic_validation,
        )
        record = stage9_source._evidence_record(
            observation,
            decision,
            {"status": "ESTABLISHED", "established_service_date": "2026-09-29"},
            {"static_stop_found": True, "explicit_parent_relationship": True},
            self.schedule,
            {
                "feed_version": "feed-v1",
                "zip_sha256": "feed-hash",
                "feed_start_date": "2026-09-01",
                "feed_end_date": "2026-12-31",
            },
            {"response_id": "response-1", "target_index": 0},
        )
        with tempfile.TemporaryDirectory() as directory:
            evidence_path = Path(directory) / "evidence.jsonl"
            stage9_source._write_jsonl(evidence_path, [record])
            written = json.loads(evidence_path.read_text(encoding="utf-8").strip())
        self.assertEqual(written["confidence"], decision.match_confidence)
        self.assertEqual(written["match_method"], decision.match_method)
        self.assertEqual(written["data_quality_classification"], decision.data_quality_classification)
        self.assertEqual(written["selected_candidate_id"], decision.canonical_service_event_id)
        self.assertEqual(written["candidate_ids"], list(decision.candidate_event_ids))
        self.assertIsInstance(written["static_candidate_identities"], list)
        self.assertIsInstance(written["evidence_used"], dict)
        self.assertFalse(stage9_source._contains_truncation_marker(written["decision_evidence"]))

    def test_source_backed_evidence_schema_contract_is_explicit(self) -> None:
        expected = {
            "source_system",
            "source_namespace",
            "mdd_source_event_identity",
            "mdd_source_observation_identity",
            "operating_day_ref",
            "stop_point_ref",
            "timetabled_time",
            "static_feed_version",
            "static_feed_sha256",
            "independent_gtfs_service_date",
            "static_candidate_identities",
            "selected_candidate_id",
            "confidence",
            "match_method",
            "data_quality_classification",
            "identity_mapping_version",
            "evidence_used",
            "evidence_explicitly_not_used",
        }
        self.assertTrue(expected.issubset(stage9_source.SOURCE_BACKED_REQUIRED_FIELDS))
        evidence_path = REPOSITORY_ROOT / "docs" / "stages" / "09-source-backed-matching-evidence.jsonl"
        if evidence_path.exists():
            rows = [json.loads(line) for line in evidence_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertTrue(rows)
            self.assertTrue(all(expected.issubset(row) for row in rows))

    def test_source_backed_acceptance_cannot_complete_from_synthetic_fixtures(self) -> None:
        self.assertEqual(
            stage9_source.source_backed_acceptance_status(
                evidence_origin="SYNTHETIC_UNIT_TEST_FIXTURES",
                static_validation_status="PASS",
                source_observation_count=10,
                independent_service_date_count=10,
                core_deterministic_match_count=10,
                mdd_http_attempts=0,
                artifact_written=True,
            ),
            "BLOCKED",
        )

    def test_incomplete_lineage_match_cannot_satisfy_source_backed_acceptance(self) -> None:
        observation = replace(self.observations[0], sampling_era_id=None)
        decision = stage9.match_source_event(
            observation,
            self.schedule,
            self.bridge,
            evidence_validation=self.synthetic_validation,
        )
        self.assertEqual(decision.data_quality_classification, stage9.QUALITY_LINEAGE_INCOMPLETE)
        core_matches = stage9_source._core_deterministic_matches([decision])
        self.assertEqual(core_matches, [])
        self.assertEqual(
            stage9_source.source_backed_acceptance_status(
                evidence_origin=stage9_source.SOURCE_BACKED_EVIDENCE_ORIGIN,
                static_validation_status="PASS",
                source_observation_count=1,
                independent_service_date_count=1,
                core_deterministic_match_count=len(core_matches),
                mdd_http_attempts=1,
                artifact_written=True,
            ),
            "BLOCKED",
        )

    def test_cross_source_without_validated_correlation_is_not_evaluated(self) -> None:
        mdd = stage9.match_source_event(
            replace(self.observations[0], correlation_group_id=None),
            self.schedule,
            self.bridge,
        )
        gtfsrt = stage9.match_source_event(
            replace(self.observations[1], correlation_group_id=None),
            self.schedule,
            self.bridge,
        )
        report = stage9.reconcile_cross_source([mdd, gtfsrt])
        self.assertEqual(report["status"], "NOT_EVALUATED")
        self.assertFalse(report["disagreements"])

    def test_cross_source_group_without_validated_basis_is_not_evaluated(self) -> None:
        mdd = stage9.match_source_event(self.observations[0], self.schedule, self.bridge)
        gtfsrt = stage9.match_source_event(self.observations[1], self.schedule, self.bridge)
        report = stage9.reconcile_cross_source(
            [
                replace(mdd, correlation_validated=False),
                replace(gtfsrt, correlation_validated=False),
            ]
        )
        self.assertEqual(report["status"], "NOT_EVALUATED")
        self.assertFalse(report["agreements"])

    def test_out_of_core_static_candidate_is_not_reported_as_static_gap(self) -> None:
        out_of_scope_schedule = stage9.build_schedule_index(
            [
                stage9.StaticServiceEvent(
                    feed_snapshot_key="VERSION__20260928_0042",
                    source_namespace="VRS_GLOBAL_ID",
                    source_trip_id="excluded-trip",
                    source_route_id="excluded-route",
                    source_stop_id="de:05315:11111",
                    canonical_stop_id="CS:COLOGNE:NEUMARKT",
                    canonical_line_id="CL:KVB:18",
                    service_date="2026-09-29",
                    event_type="ARRIVAL",
                    scheduled_time="10:15:00",
                    direction="N",
                    stop_sequence=7,
                    scope_code="OUT_OF_SCOPE",
                    scope_reason="long-distance service excluded by Stage 4 core scope",
                )
            ]
        )
        decision = stage9.match_source_event(
            self.observations[0],
            out_of_scope_schedule,
            self.bridge,
            evidence_validation=self.synthetic_validation,
        )
        self.assertEqual(decision.match_confidence, stage9.MATCH_UNMATCHED)
        self.assertEqual(decision.data_quality_classification, stage9.QUALITY_OUT_OF_CORE_SCOPE)
        self.assertIn("out_of_scope_candidate_ids", decision.evidence)


if __name__ == "__main__":
    unittest.main()
