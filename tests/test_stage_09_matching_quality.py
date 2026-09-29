from __future__ import annotations

import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

import stage_08_mdd_realtime_pilot as stage8  # noqa: E402
import stage_09_matching_quality as stage9  # noqa: E402


class Stage9MatchingQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = stage9._fixture_bridge()
        self.schedule = stage9._fixture_schedule()
        self.observations = stage9._fixture_observations()

    def test_offline_acceptance_population_passes_without_network(self) -> None:
        summary = stage9.run_offline_validation()
        self.assertEqual(summary["overall_status"], "PASS")
        self.assertEqual(summary["stage_9_status"], "COMPLETE")
        self.assertEqual(summary["network_requests"], 0)
        self.assertEqual(summary["database_connections"], 0)
        self.assertTrue(all(summary["checks"].values()))

    def test_mdd_and_gtfsrt_known_good_events_match_the_same_canonical_event(self) -> None:
        mdd = stage9.match_source_event(self.observations[0], self.schedule, self.bridge)
        gtfsrt = stage9.match_source_event(self.observations[1], self.schedule, self.bridge)
        self.assertEqual(mdd.match_confidence, stage9.MATCH_HIGH)
        self.assertEqual(gtfsrt.match_confidence, stage9.MATCH_HIGH)
        self.assertEqual(mdd.canonical_service_event_id, gtfsrt.canonical_service_event_id)
        cross_source = stage9.reconcile_cross_source([mdd, gtfsrt])
        self.assertEqual(len(cross_source), 1)
        self.assertFalse(cross_source[0]["source_identifier_equality_used"])

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
        self.assertEqual(decision.data_quality_classification, stage9.QUALITY_AMBIGUOUS_MATCH)
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
        decision = stage9.match_source_event(observation, self.schedule, self.bridge)
        self.assertEqual(decision.match_confidence, stage9.MATCH_MEDIUM)
        self.assertEqual(decision.data_quality_classification, stage9.QUALITY_LINEAGE_INCOMPLETE)
        self.assertEqual(decision.evidence["lineage_missing_fields"], ["sampling_era_id"])
        audit = stage9.audit_observation_lineage([observation])
        self.assertEqual(audit["status"], "WARN")
        self.assertEqual(audit["lineage_incomplete_count"], 1)

    def test_unresolved_line_identity_is_not_promoted_to_validated_match(self) -> None:
        observation = replace(self.observations[0], source_line_id="line-not-in-bridge")
        decision = stage9.match_source_event(observation, self.schedule, self.bridge)
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
        decision = stage9.match_source_event(self.observations[1], self.schedule, self.bridge)
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


if __name__ == "__main__":
    unittest.main()
