from __future__ import annotations

import io
import errno
import gc
import json
import os
import socket
import ssl
import sys
import tempfile
import threading
import unittest
import warnings
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError, URLError


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.collector import CLIENT_EXECUTION_FAILURE, Collector
from cologne_transit_collector.domain import HttpResponse, TransportFailure
from cologne_transit_collector.manifest import LEGACY7_SAMPLING_TARGET_IDS, load_default_manifest
from cologne_transit_collector.persistence import SQLitePersistence
from cologne_transit_collector.quota import (
    CONSERVATIVE_LIVE_ATTEMPT,
    HTTP_PROVIDER_ATTEMPT_CONFIRMED,
    RELEASED_NO_NETWORK,
    InMemoryUsageProvider,
    QuotaGuard,
    UsageSnapshot,
)
from cologne_transit_collector.schedule import bucket_for
from cologne_transit_collector.simulation import run_simulation
from cologne_transit_collector.transport import (
    CREDENTIAL_FORMAT_INVALID,
    CREDENTIAL_MISSING,
    RESPONSE_SIZE_EXCEEDED,
    FakeMddClient,
    LiveMddClient,
    ResponseTooLarge,
    _read_bounded,
    _transport_category,
)
from cologne_transit_collector.trias import _path_is_retained, parse_trias_response


AT = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
JSON_FIXTURE = ROOT / "tests" / "fixtures" / "stage14_trias_equivalent.json"
XML_FIXTURE = ROOT / "tests" / "fixtures" / "stage14_trias_equivalent.xml"
SOURCE_FAITHFUL_JSON_FIXTURE = ROOT / "tests" / "fixtures" / "stage14_trias_source_faithful.json"
SOURCE_FAITHFUL_XML_FIXTURE = ROOT / "tests" / "fixtures" / "stage14_trias_source_faithful.xml"
SOURCE_CONTRACT_JSON_FIXTURE = ROOT / "tests" / "fixtures" / "stage14_trias_source_contract_correction.json"
SOURCE_CONTRACT_XML_FIXTURE = ROOT / "tests" / "fixtures" / "stage14_trias_source_contract_correction.xml"
SUCCESS_FIXTURE = ROOT / "tests" / "fixtures" / "stage14_trias_success.json"


class Stage14CorrectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_default_manifest(ROOT)
        cls.target = cls.manifest.target_by_id("de:05315:11201")

    def _new_state(self, directory: str, name: str) -> SQLitePersistence:
        persistence = SQLitePersistence(Path(directory) / name)
        persistence.materialize_manifest(self.manifest)
        return persistence

    @staticmethod
    def _semantic_snapshot(parsed):
        event = parsed.events[0]
        return {
            "identity": {
                key: event[key]
                for key in (
                    "result_id",
                    "journey_ref",
                    "operating_day_ref",
                    "line_ref",
                    "direction_ref",
                    "operator_ref",
                    "origin_stop_point_ref",
                    "origin_text",
                    "destination_stop_point_ref",
                    "destination_text",
                    "pt_mode",
                    "rail_submode",
                    "published_line_name",
                )
            },
            "calls": [
                {
                    "scope": call["call_scope"],
                    "ordinal": call["call_ordinal"],
                    "stop": call["stop_point_ref"],
                    "name": call["stop_display_text"],
                    "sequence": call["stop_seq_number"],
                    "arrival": {
                        key: call["arrival"][key]
                        for key in (
                            "timetabled_time",
                            "estimated_time",
                            "recorded_at_time",
                            "estimated_time_low",
                            "estimated_time_high",
                        )
                    },
                    "departure": {
                        key: call["departure"][key]
                        for key in (
                            "timetabled_time",
                            "estimated_time",
                            "recorded_at_time",
                            "estimated_time_low",
                            "estimated_time_high",
                        )
                    },
                    "planned_bay": call["planned_bay"],
                    "estimated_bay": call["estimated_bay"],
                    "unplanned_stop": call["unplanned_stop"],
                    "not_serviced_stop": call["not_serviced_stop"],
                    "no_boarding_at_stop": call["no_boarding_at_stop"],
                    "no_alighting_at_stop": call["no_alighting_at_stop"],
                }
                for call in event["calls"]
            ],
            "recorded": [
                (
                    occurrence["call_scope"],
                    occurrence["call_ordinal"],
                    occurrence["timing_context"],
                    occurrence["recorded_at_time"],
                )
                for occurrence in parsed.recorded_at_time_occurrences
            ],
            "situations": event["situation_references"],
        }

    def test_json_and_xml_normalize_equivalently_without_network(self) -> None:
        json_parsed = parse_trias_response(JSON_FIXTURE.read_bytes(), "application/json")
        xml_parsed = parse_trias_response(XML_FIXTURE.read_bytes(), "application/xml")
        self.assertEqual(self._semantic_snapshot(json_parsed), self._semantic_snapshot(xml_parsed))

    def test_source_faithful_identity_operational_grains_and_parser_gaps(self) -> None:
        json_parsed = parse_trias_response(
            SOURCE_FAITHFUL_JSON_FIXTURE.read_bytes(), "application/json"
        )
        xml_parsed = parse_trias_response(
            SOURCE_FAITHFUL_XML_FIXTURE.read_bytes(), "application/xml"
        )
        json_event = json_parsed.events[0]
        xml_event = xml_parsed.events[0]

        for event in (json_event, xml_event):
            self.assertEqual(event["operational_event_fields"], {"cancelled": True, "deviation": True})
            self.assertTrue(event["operational_event_field_source_paths"]["cancelled"])
            self.assertTrue(event["operational_event_field_source_paths"]["deviation"])
            self.assertEqual(event["pt_mode"], "tram")
            self.assertEqual(event["rail_submode"], "lightRail")
            self.assertEqual(event["published_line_name"], "Line 18")
            self.assertTrue(event["pt_mode_source_path"])
            self.assertTrue(event["rail_submode_source_path"])
            self.assertTrue(event["published_line_name_source_path"])
            self.assertEqual(event["origin_stop_point_ref"], "de:05315:origin")
            self.assertEqual(event["origin_text"], "Origin Station")
            self.assertEqual(event["destination_stop_point_ref"], "de:05315:destination")
            self.assertEqual(event["destination_text"], "Destination Station")
            for field_name in (
                "origin_stop_point_ref",
                "origin_text",
                "destination_stop_point_ref",
                "destination_text",
            ):
                self.assertTrue(event[f"{field_name}_source_path"])

            previous, current, onward = (
                event["previous_calls"][0],
                event["this_calls"][0],
                event["onward_calls"][0],
            )
            self.assertIsNone(previous["stop_point_ref"])
            self.assertIsNone(previous["planned_bay"])
            self.assertTrue(previous["not_serviced_stop"])
            self.assertEqual(current["stop_point_ref"], "de:05315:this")
            self.assertEqual(current["planned_bay"], "B")
            self.assertIsNone(onward["stop_point_ref"])
            self.assertIsNone(onward["planned_bay"])
            for call in (previous, current, onward):
                self.assertNotIn("cancelled", call)
                self.assertNotIn("deviation", call)
                self.assertNotEqual(call.get("stop_point_ref"), "de:05315:service-context")
                self.assertNotEqual(call.get("planned_bay"), "SERVICE-BAY")

        self.assertEqual(
            [
                (
                    event["origin_stop_point_ref"],
                    event["origin_text"],
                    event["destination_stop_point_ref"],
                    event["destination_text"],
                    event["pt_mode"],
                    event["rail_submode"],
                    event["published_line_name"],
                )
                for event in (json_event, xml_event)
            ][0],
            [
                (
                    event["origin_stop_point_ref"],
                    event["origin_text"],
                    event["destination_stop_point_ref"],
                    event["destination_text"],
                    event["pt_mode"],
                    event["rail_submode"],
                    event["published_line_name"],
                )
                for event in (json_event, xml_event)
            ][1],
        )
        for parsed in (json_parsed, xml_parsed):
            self.assertEqual(len(parsed.parser_gaps), 1)
            gap = parsed.parser_gaps[0]
            self.assertEqual(gap["field_name"], "UnknownEstimatedQuality")
            self.assertEqual(gap["normalized_field_name"], "unknownestimatedquality")
            self.assertIn("unknownestimatedquality", gap["source_path"])
            self.assertEqual(gap["source_value"], "raw-estimate-evidence")
            self.assertEqual(gap["result_id"], "source-faithful-result-1")
            self.assertEqual(gap["result_ordinal"], 0)
            self.assertEqual(gap["event_context"], "STOP_EVENT_RESULT")
            self.assertEqual(gap["call_scope"], "THIS")

        unrelated_envelope = json.loads(JSON_FIXTURE.read_text(encoding="utf-8"))
        unrelated_envelope["diagnosticEnvelope"] = {
            "traceId": "offline-test-trace",
            "requestMetadata": {"source": "fixture"},
        }
        unrelated = parse_trias_response(
            json.dumps(unrelated_envelope).encode("utf-8"), "application/json"
        )
        self.assertEqual(unrelated.parser_gaps, ())

    def test_source_contract_identity_separation_and_service_scope_json_xml(self) -> None:
        parsed_values = []
        for fixture, content_type in (
            (SOURCE_CONTRACT_JSON_FIXTURE, "application/json"),
            (SOURCE_CONTRACT_XML_FIXTURE, "application/xml"),
        ):
            parsed = parse_trias_response(fixture.read_bytes(), content_type)
            event = parsed.events[0]
            parsed_values.append(
                {
                    "journey_ref": event["journey_ref"],
                    "vehicle_journey_ref": event["vehicle_journey_ref"],
                    "dated_vehicle_journey_ref": event["dated_vehicle_journey_ref"],
                    "direction_ref": event["direction_ref"],
                    "direction": event["direction"],
                    "pt_mode": event["pt_mode"],
                    "rail_submode": event["rail_submode"],
                    "published_line_name": event["published_line_name"],
                    "origin_stop_point_ref": event["origin_stop_point_ref"],
                    "origin_text": event["origin_text"],
                    "destination_stop_point_ref": event["destination_stop_point_ref"],
                    "destination_text": event["destination_text"],
                }
            )

            # JourneyRef and DirectionRef remain NULL/distinct when only the
            # richer/candidate evidence is supplied.
            self.assertIsNone(event["journey_ref"])
            self.assertIsNone(event["journey_ref_source_path"])
            self.assertEqual(event["vehicle_journey_ref"], "vehicle-journey-candidate-1")
            self.assertEqual(event["dated_vehicle_journey_ref"], "dated-vehicle-journey-candidate-1")
            self.assertTrue(event["vehicle_journey_ref_source_path"])
            self.assertTrue(event["dated_vehicle_journey_ref_source_path"])
            self.assertEqual(event["direction_ref"], "direction-ref-1")
            self.assertEqual(event["direction"], "Northbound candidate evidence")
            self.assertNotEqual(event["direction_ref"], event["direction"])

            # Service identity/display fields never fall back to StopEvent
            # lookalikes when the Service scope is absent.
            for field_name in (
                "pt_mode",
                "rail_submode",
                "published_line_name",
                "origin_stop_point_ref",
                "origin_text",
                "destination_stop_point_ref",
                "destination_text",
            ):
                self.assertIsNone(event[field_name])
                self.assertIsNone(event[f"{field_name}_source_path"])

            self.assertEqual(event["affected_vehicle_journey"], "affected-vehicle-journey-1")
            self.assertEqual(
                event["vehicle_journeys"],
                ["vehicle-journey-candidate-2", "vehicle-journey-candidate-3"],
            )
            self.assertEqual(event["operating_days_description"], "Monday-Friday")
            self.assertEqual(event["origins"], ["candidate-origin-1", "candidate-origin-2"])
            self.assertEqual(event["destinations"], "candidate-destination-1")
            for field_name in (
                "affected_vehicle_journey",
                "vehicle_journeys",
                "operating_days_description",
                "direction",
                "origins",
                "destinations",
            ):
                self.assertTrue(event[f"{field_name}_source_path"])

            attributes = event["service_attribute_evidence"]
            self.assertEqual(
                {
                    (item["attribute_field_name"].casefold(), item["code"], item["text"])
                    for item in attributes
                },
                {
                    ("attribute", "INC-001", "Provider-reported incident text"),
                    ("generalattribute", "GEN-001", "Provider-reported general text"),
                },
            )
            for attribute in attributes:
                self.assertTrue(attribute["source_path"])
                self.assertTrue(attribute["association_context_path"])
                self.assertTrue(attribute["code_source_paths"])
                self.assertTrue(attribute["text_source_paths"])
                self.assertEqual(
                    attribute["semantic_role"], "PROVIDER_REPORTED_EVIDENCE_ONLY"
                )
                self.assertNotIn("cause", attribute)

            situation = event["situation_evidence"][0]
            self.assertEqual(situation["affected_entity_context"]["affects"], "SERVICE")
            self.assertEqual(
                next(iter(situation["affected_entity_context"]["affected_journey"].values())),
                "affected-journey-1",
            )
            self.assertTrue(situation["affected_entity_field_paths"]["affected_line"])
            self.assertTrue(situation["source_path"])

            gap_names = {gap["normalized_field_name"] for gap in parsed.parser_gaps}
            self.assertEqual(
                gap_names,
                {"ptmode", "origin", "destination", "unmappedserviceextension"},
            )
            self.assertEqual(
                sum(gap["normalized_field_name"] == "unmappedserviceextension" for gap in parsed.parser_gaps),
                1,
            )
            self.assertFalse(any(gap["normalized_field_name"] in {"attribute", "generalattribute"} for gap in parsed.parser_gaps))
            for gap in parsed.parser_gaps:
                self.assertTrue(gap["source_path"])
                self.assertEqual(gap["result_id"], "source-contract-correction-1")
                self.assertEqual(gap["event_ordinal"], 0)

        self.assertEqual(parsed_values[0], parsed_values[1])

    def test_recognized_vocabulary_does_not_suppress_unmapped_relevant_evidence(self) -> None:
        parsed = parse_trias_response(
            SOURCE_CONTRACT_JSON_FIXTURE.read_bytes(), "application/json"
        )
        # The intentionally unmapped extension is a relevant source-contract
        # field. It is emitted as a bounded gap even though the parser has a
        # broad recognized vocabulary for the surrounding TRIAS structures.
        gap = next(
            item
            for item in parsed.parser_gaps
            if item["normalized_field_name"] == "unmappedserviceextension"
        )
        self.assertEqual(gap["mapping_status"], "UNMAPPED_RELEVANT_SOURCE_FIELD")
        self.assertEqual(gap["source_value"], "retained-parser-gap-evidence")
        self.assertEqual(gap["association_scope"], "SERVICE")
        self.assertEqual(gap["call_scope"], None)

        # Regression: preserve path boundaries and avoid full scans.
        retained = {
            "$.result[0].call": True,
            "$.result[0].arrival": False,
            "$.result[12].call": True,
        }

        for path, expected in (
            ("$.result[0].call", True),
            ("$.result[0].call[0].time", True),
            ("$.result[0].call.time", True),
            ("$.result[0].callback", False),
            ("$.result[0].arrival", True),
            ("$.result[0].arrival.time", False),
            ("$.result[1].call.time", False),
            ("$.result[12].call.time", True),
        ):
            with self.subTest(path=path):
                self.assertEqual(
                    _path_is_retained(path, retained), expected
                )

        class NoScanDict(dict):
            def items(self):
                raise AssertionError("Full retained-path scan detected")

        self.assertTrue(
            _path_is_retained(
                "$.result[0].call[0].time",
                NoScanDict(retained),
            )
        )

    def test_parser_does_not_project_event_fields_or_call_data_from_broader_scopes(self) -> None:
        parsed = parse_trias_response(SOURCE_FAITHFUL_JSON_FIXTURE.read_bytes(), "application/json")
        event = parsed.events[0]
        self.assertEqual(event["operational_event_fields"]["cancelled"], True)
        self.assertEqual(event["operational_event_fields"]["deviation"], True)
        for call in event["calls"]:
            self.assertIsNone(call.get("cancelled"))
            self.assertIsNone(call.get("deviation"))
        self.assertIsNone(event["previous_calls"][0]["stop_point_ref"])
        self.assertIsNone(event["onward_calls"][0]["planned_bay"])
        self.assertTrue(event["previous_calls"][0]["not_serviced_stop"])

    def test_recorded_at_time_is_retained_in_all_call_and_timing_contexts(self) -> None:
        parsed = parse_trias_response(JSON_FIXTURE.read_bytes(), "application/json")
        self.assertEqual(
            [
                (item["call_scope"], item["timing_context"])
                for item in parsed.recorded_at_time_occurrences
            ],
            [
                ("PREVIOUS", "ARRIVAL"),
                ("PREVIOUS", "DEPARTURE"),
                ("THIS", "ARRIVAL"),
                ("THIS", "DEPARTURE"),
                ("ONWARD", "ARRIVAL"),
                ("ONWARD", "DEPARTURE"),
            ],
        )
        self.assertEqual(
            parsed.events[0]["previous_calls"][0]["arrival"]["recorded_at_time"],
            "2026-10-01T11:56:30Z[GMT]",
        )
        self.assertEqual(
            parsed.events[0]["onward_calls"][0]["departure"]["recorded_at_time"],
            "2026-10-01T12:06:30Z[GMT]",
        )

    def test_recorded_at_time_evidence_is_append_only_and_audited(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            persistence = self._new_state(directory, "evidence.sqlite3")
            bucket = bucket_for(AT)
            persistence.start_collector_run(
                run_id="evidence-run",
                panel_version=self.manifest.panel_version,
                logical_due_bucket=bucket.index,
                mode="simulate",
                started_at=AT,
            )
            persistence.claim_dispatcher_bucket(
                run_id="evidence-run",
                panel_version=self.manifest.panel_version,
                logical_due_bucket=bucket.index,
                claimed_at=AT,
            )
            persistence.claim_target(
                run_id="evidence-run",
                target=self.target,
                logical_due_bucket=bucket.index,
                claimed_at=AT,
            )
            audit_id = persistence.start_target_audit(
                run_id="evidence-run",
                target=self.target,
                logical_due_bucket=bucket.index,
                started_at=AT,
            )
            parsed = parse_trias_response(JSON_FIXTURE.read_bytes(), "application/json")
            persistence.persist_snapshot(
                audit_id=audit_id,
                target=self.target,
                logical_due_bucket=bucket.index,
                parsed_snapshot=parsed.to_dict(),
                persisted_at=AT,
            )
            later = parsed.to_dict()
            for event in later["events"]:
                event["recorded_at_time_occurrences"] = []
                for call in event["calls"]:
                    call["arrival"]["recorded_at_time"] = None
                    call["departure"]["recorded_at_time"] = None
                event["previous_calls"] = event["calls"][:1]
            persistence.persist_snapshot(
                audit_id=audit_id,
                target=self.target,
                logical_due_bucket=bucket.index,
                parsed_snapshot=later,
                persisted_at=AT,
            )
            evidence = persistence.fetch_recorded_time_evidence()
            self.assertEqual(len(evidence), 6)
            self.assertEqual(evidence[0]["recorded_at_time"], "2026-10-01T11:56:30Z[GMT]")
            self.assertEqual(evidence[0]["call_scope"], "PREVIOUS")
            self.assertEqual(evidence[0]["timing_context"], "ARRIVAL")
            self.assertEqual(persistence.retain_raw_evidence(body=b"fixture"), "DISABLED_OFFLINE")
            persistence.close()

    def test_dispatcher_claim_wins_across_independent_connections_repeatedly(self) -> None:
        for iteration in range(4):
            with self.subTest(iteration=iteration), tempfile.TemporaryDirectory() as directory:
                p1 = self._new_state(directory, "shared.sqlite3")
                p2 = self._new_state(directory, "shared.sqlite3")
                body = SUCCESS_FIXTURE.read_bytes()
                c1 = Collector(
                    manifest=self.manifest,
                    persistence=p1,
                    mdd_client=FakeMddClient(default_response=HttpResponse(200, body, {"Content-Type": "application/json"})),
                    quota_guard=QuotaGuard(InMemoryUsageProvider(), reservation_scope="OFFLINE_RACE"),
                    safety_margin_seconds=0,
                )
                c2 = Collector(
                    manifest=self.manifest,
                    persistence=p2,
                    mdd_client=FakeMddClient(default_response=HttpResponse(200, body, {"Content-Type": "application/json"})),
                    quota_guard=QuotaGuard(InMemoryUsageProvider(), reservation_scope="OFFLINE_RACE"),
                    safety_margin_seconds=0,
                )
                results = []
                barrier = threading.Barrier(2)

                def invoke(collector, run_id):
                    barrier.wait()
                    results.append(collector.run_once(mode="simulate", at_utc=AT, run_id=run_id))

                threads = [
                    threading.Thread(target=invoke, args=(c1, f"one-{iteration}")),
                    threading.Thread(target=invoke, args=(c2, f"two-{iteration}")),
                ]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
                calls = len(c1.mdd_client.calls) + len(c2.mdd_client.calls)
                self.assertEqual(calls, 19)
                self.assertEqual(sum(result.dispatcher_claim_won for result in results), 1)
                self.assertEqual(len(p1.fetch_dispatcher_claim_events()), 2)
                self.assertTrue(all(result.live_network_calls == 0 for result in results))
                winner = next(result for result in results if result.dispatcher_claim_won)
                self.assertLessEqual(winner.max_observed_concurrency, 2)
                p1.close()
                p2.close()

    def test_target_claims_are_not_preclaimed_while_workers_are_blocked(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        body = SUCCESS_FIXTURE.read_bytes()

        class BlockingClient(FakeMddClient):
            def fetch(self, target, **kwargs):
                entered.set()
                release.wait(timeout=10)
                return super().fetch(target, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            persistence = self._new_state(directory, "jit.sqlite3")
            client = BlockingClient()
            collector = Collector(
                manifest=self.manifest,
                persistence=persistence,
                mdd_client=client,
                quota_guard=QuotaGuard(InMemoryUsageProvider(), reservation_scope="OFFLINE_JIT"),
                safety_margin_seconds=0,
            )
            holder = {}
            thread = threading.Thread(
                target=lambda: holder.setdefault("result", collector.run_once(mode="simulate", at_utc=AT, run_id="jit-run"))
            )
            thread.start()
            self.assertTrue(entered.wait(timeout=5))
            self.assertLessEqual(len(persistence.fetch_claims()), 2)
            release.set()
            thread.join(timeout=15)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(client.calls), 19)
            persistence.close()

    def test_atomic_retry_and_initial_permits_across_connections(self) -> None:
        for decision_kind, usage, expected_boundary in (
            ("retry", 207_499, 207_500),
            ("initial", 209_999, 210_000),
        ):
            with self.subTest(decision_kind=decision_kind), tempfile.TemporaryDirectory() as directory:
                p1 = self._new_state(directory, "quota.sqlite3")
                p2 = self._new_state(directory, "quota.sqlite3")
                month = AT.strftime("%Y-%m")
                p1.set_shared_usage(month_key=month, confirmed_provider_attempts=usage)
                guards = [QuotaGuard(p1), QuotaGuard(p2)]
                decisions = []
                barrier = threading.Barrier(2)

                def authorize(guard):
                    barrier.wait()
                    decisions.append(
                        guard.allow_retry(at_utc=AT)
                        if decision_kind == "retry"
                        else guard.allow_initial(at_utc=AT)
                    )

                threads = [threading.Thread(target=authorize, args=(guard,)) for guard in guards]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
                self.assertEqual(sum(decision.allowed for decision in decisions), 1)
                permitted = next(decision for decision in decisions if decision.allowed)
                self.assertEqual(permitted.known_total, usage)
                self.assertEqual(permitted.boundary, expected_boundary)
                for guard, decision in zip(guards, decisions):
                    guard.release(decision)
                p1.close()
                p2.close()

    def test_simulated_calls_never_become_confirmed_provider_usage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            persistence = self._new_state(directory, "offline.sqlite3")
            client = FakeMddClient(
                default_response=HttpResponse(200, SUCCESS_FIXTURE.read_bytes(), {"Content-Type": "application/json"})
            )
            collector = Collector(
                manifest=self.manifest,
                persistence=persistence,
                mdd_client=client,
                quota_guard=QuotaGuard(persistence, reservation_scope="OFFLINE_AUDIT"),
                safety_margin_seconds=0,
            )
            result = collector.run_once(mode="simulate", at_utc=AT, run_id="offline-usage")
            usage = persistence.current_month_usage(at_utc=AT)
            self.assertEqual(result.simulated_transport_calls, 19)
            self.assertEqual(result.live_network_calls, 0)
            self.assertEqual(usage["confirmed_provider_attempts"], 0)
            self.assertEqual(len(persistence.fetch_attempts()), 19)
            self.assertTrue(all(row["transport_kind"] == "SIMULATED_TRANSPORT" for row in persistence.fetch_attempts()))
            persistence.close()

    def test_credential_preflight_redirects_classification_and_bounded_reader(self) -> None:
        target = self.target
        calls = []

        class Opener:
            def open(self, request, timeout):
                calls.append((request, timeout))
                raise HTTPError(request.full_url, 302, "redirect", {"Location": "https://other.invalid"}, io.BytesIO(b""))

        client = LiveMddClient("https://example.invalid", opener_factory=lambda: Opener(), enabled=True)
        with mock.patch.dict(os.environ, {}, clear=True):
            missing = client.fetch(target, request_timestamp_utc=AT, timeout_seconds=30, attempt_number=1)
        self.assertEqual(missing.category, CREDENTIAL_MISSING)
        self.assertFalse(missing.network_attempted)
        self.assertFalse(missing.http_response_received)
        self.assertTrue(missing.live_client_invocation)
        self.assertEqual(len(calls), 0)
        with mock.patch.dict(os.environ, {"MDD_API_KEY": "bad\nkey"}, clear=True):
            malformed = client.fetch(target, request_timestamp_utc=AT, timeout_seconds=30, attempt_number=1)
        self.assertEqual(malformed.category, CREDENTIAL_FORMAT_INVALID)
        self.assertEqual(len(calls), 0)
        with mock.patch.dict(os.environ, {"MDD_API_KEY": "safe-test-key"}, clear=True):
            redirected = client.fetch(target, request_timestamp_utc=AT, timeout_seconds=30, attempt_number=1)
        self.assertEqual(redirected.status_code, 302)
        self.assertTrue(redirected.network_attempted)
        self.assertTrue(redirected.http_response_received)
        self.assertTrue(redirected.live_client_invocation)
        self.assertEqual(len(calls), 1)
        self.assertEqual(_transport_category(URLError(socket.gaierror("no such host"))), "DNS_RESOLUTION_FAILED")
        self.assertEqual(_transport_category(URLError(ConnectionRefusedError())), "CONNECTION_REFUSED")
        self.assertEqual(
            _transport_category(URLError(OSError(errno.ENETUNREACH, "network is unreachable"))),
            "NETWORK_UNREACHABLE",
        )
        self.assertEqual(_transport_category(ssl.SSLError("certificate verify failed")), "TLS_FAILURE")
        self.assertEqual(_transport_category(URLError("proxy environment failure")), "PROXY_OR_ENVIRONMENT_FAILURE")
        self.assertEqual(_transport_category(URLError(socket.timeout())), "TIMEOUT")
        self.assertEqual(_transport_category(URLError("unclassified transport detail")), "OTHER_NETWORK_FAILURE")
        self.assertEqual(_read_bounded(io.BytesIO(b"x"), 2), b"x")
        self.assertEqual(_read_bounded(io.BytesIO(b"xx"), 2), b"xx")
        with self.assertRaises(ResponseTooLarge):
            _read_bounded(io.BytesIO(b"xxx"), 2)
        self.assertEqual(RESPONSE_SIZE_EXCEEDED, "RESPONSE_SIZE_EXCEEDED")

    def test_legacy_lineage_is_exact_and_new_targets_are_null(self) -> None:
        actual = {
            target.source_parent_station_id: target.legacy_sampling_target_id
            for target in self.manifest.legacy7_targets
        }
        self.assertEqual(actual, LEGACY7_SAMPLING_TARGET_IDS)
        self.assertTrue(all(target.legacy_sampling_target_id is None for target in self.manifest.new_tiered50_targets))

    def test_in_memory_permits_remain_consumed_at_retry_and_initial_boundaries(self) -> None:
        for decision_kind, starting_usage, expected_boundary in (
            ("retry", 207_499, 207_500),
            ("initial", 209_999, 210_000),
        ):
            with self.subTest(decision_kind=decision_kind):
                provider = InMemoryUsageProvider(
                    UsageSnapshot(confirmed_provider_attempts=starting_usage)
                )
                guards = [QuotaGuard(provider), QuotaGuard(provider)]
                decisions = []
                barrier = threading.Barrier(2)

                def authorize(guard: QuotaGuard) -> None:
                    barrier.wait()
                    decisions.append(
                        guard.allow_retry(at_utc=AT)
                        if decision_kind == "retry"
                        else guard.allow_initial(at_utc=AT)
                    )

                threads = [threading.Thread(target=authorize, args=(guard,)) for guard in guards]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()

                self.assertEqual(sum(decision.allowed for decision in decisions), 1)
                permitted = next(decision for decision in decisions if decision.allowed)
                self.assertEqual(permitted.boundary, expected_boundary)
                QuotaGuard(provider).finalize_conservative(permitted)
                follow_up = (
                    QuotaGuard(provider).allow_retry(at_utc=AT)
                    if decision_kind == "retry"
                    else QuotaGuard(provider).allow_initial(at_utc=AT)
                )
                self.assertFalse(follow_up.allowed)
                usage = provider.current_month_usage(at_utc=AT)
                self.assertEqual(usage["confirmed_provider_attempts"], starting_usage)
                self.assertEqual(usage["conservative_live_attempts"], 1)
                self.assertLessEqual(
                    usage["confirmed_provider_attempts"]
                    + usage["conservative_live_attempts"],
                    expected_boundary,
                )

    def test_permit_finalization_distinguishes_http_unknown_no_network_and_simulation(self) -> None:
        provider = InMemoryUsageProvider()

        confirmed = QuotaGuard(provider).allow_initial(at_utc=AT)
        self.assertTrue(confirmed.allowed)
        QuotaGuard(provider).finalize_http_response(confirmed)
        QuotaGuard(provider).finalize_http_response(confirmed)
        self.assertEqual(provider.snapshot.confirmed_provider_attempts, 1)
        self.assertEqual(provider.snapshot.http_responses_received, 1)

        unknown = QuotaGuard(provider).allow_initial(at_utc=AT)
        self.assertTrue(unknown.allowed)
        QuotaGuard(provider).finalize_conservative(unknown)
        self.assertEqual(provider.snapshot.confirmed_provider_attempts, 1)
        self.assertEqual(provider.snapshot.conservative_live_attempts, 1)
        self.assertEqual(provider.snapshot.provider_quota_unknown_attempts, 1)

        credential = QuotaGuard(provider).allow_initial(at_utc=AT)
        self.assertTrue(credential.allowed)
        QuotaGuard(provider).finalize_no_network(credential)
        self.assertEqual(provider.snapshot.confirmed_provider_attempts, 1)
        self.assertEqual(provider.snapshot.conservative_live_attempts, 1)
        self.assertEqual(provider.current_month_usage(at_utc=AT)["reserved_shared_usage"], 0)

        before = provider.current_month_usage(at_utc=AT)
        for _ in range(3):
            simulated = QuotaGuard(provider).allow_initial(at_utc=AT)
            self.assertTrue(simulated.allowed)
            QuotaGuard(provider).finalize_no_network(simulated)
        after = provider.current_month_usage(at_utc=AT)
        for key in (
            "confirmed_provider_attempts",
            "conservative_live_attempts",
            "http_responses_received",
            "provider_quota_unknown_attempts",
        ):
            self.assertEqual(after[key], before[key])

    def test_sqlite_finalization_is_atomic_and_records_explicit_attempt_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            persistence = self._new_state(directory, "lifecycle.sqlite3")
            target_rows = iter(self.manifest.targets[:4])

            def record(target, result, *, run_id: str):
                persistence.start_collector_run(
                    run_id=run_id,
                    panel_version=self.manifest.panel_version,
                    logical_due_bucket=bucket_for(AT).index,
                    mode="test",
                    started_at=AT,
                )
                audit_id = persistence.start_target_audit(
                    run_id=run_id,
                    target=target,
                    logical_due_bucket=bucket_for(AT).index,
                    started_at=AT,
                )
                decision = QuotaGuard(persistence).allow_initial(at_utc=AT)
                self.assertTrue(decision.allowed)
                completed = AT
                common = {
                    "audit_id": audit_id,
                    "attempt_number": 1,
                    "started_at": AT,
                    "completed_at": completed,
                    "retry_decision": "NO_RETRY",
                    "transport_kind": getattr(result, "transport_kind", "UNKNOWN"),
                    "quota_permit_id": decision.permit_id,
                    "quota_finalization": (
                        HTTP_PROVIDER_ATTEMPT_CONFIRMED
                        if result.http_response_received and result.network_attempted
                        else CONSERVATIVE_LIVE_ATTEMPT
                        if result.network_attempted
                        else RELEASED_NO_NETWORK
                    ),
                    "finalized_at": completed,
                }
                if isinstance(result, HttpResponse):
                    persistence.record_http_attempt_and_finalize_quota(response=result, **common)
                else:
                    persistence.record_http_attempt_and_finalize_quota(transport_failure=result, **common)

            target = next(target_rows)
            record(
                target,
                HttpResponse(200, b"{}", {}, network_attempted=True, http_response_received=True, transport_kind="LIVE_CLIENT", live_client_invocation=True),
                run_id="confirmed",
            )
            target = next(target_rows)
            record(
                target,
                TransportFailure("TIMEOUT", "sanitized", network_attempted=True, transport_kind="LIVE_CLIENT", live_client_invocation=True),
                run_id="unknown",
            )
            target = next(target_rows)
            record(
                target,
                TransportFailure("CREDENTIAL_MISSING", "credential preflight failed", transport_kind="LIVE_CLIENT", live_client_invocation=True),
                run_id="credential",
            )
            target = next(target_rows)
            record(
                target,
                HttpResponse(200, b"{}", {}, network_attempted=False, http_response_received=True, transport_kind="SIMULATED_TRANSPORT"),
                run_id="simulation",
            )

            attempts = persistence.fetch_attempts()
            self.assertEqual([row["provider_attempt_confirmed"] for row in attempts], [1, 0, 0, 0])
            self.assertEqual([row["network_attempted"] for row in attempts], [1, 1, 0, 0])
            self.assertEqual([row["http_response_received"] for row in attempts], [1, 0, 0, 1])
            self.assertEqual([row["conservative_quota_accounted"] for row in attempts], [0, 1, 0, 0])
            self.assertEqual([row["live_client_invocation"] for row in attempts], [1, 1, 1, 0])
            statuses = [row["status"] for row in persistence.fetch_quota_permits()]
            self.assertEqual(
                sorted(statuses),
                sorted([
                    HTTP_PROVIDER_ATTEMPT_CONFIRMED,
                    CONSERVATIVE_LIVE_ATTEMPT,
                    RELEASED_NO_NETWORK,
                    RELEASED_NO_NETWORK,
                ]),
            )
            usage = persistence.current_month_usage(at_utc=AT)
            self.assertEqual(usage["confirmed_provider_attempts"], 1)
            self.assertEqual(usage["http_responses_received"], 1)
            self.assertEqual(usage["conservative_live_attempts"], 1)
            self.assertEqual(usage["provider_quota_unknown_attempts"], 1)
            self.assertEqual(usage["live_client_invocations"], 3)
            self.assertEqual(usage["reserved_shared_usage"], 0)
            persistence.close()

    def test_run_simulation_closes_owned_sqlite_without_resource_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", ResourceWarning)
                result = run_simulation(
                    self.manifest,
                    Path(directory) / "owned.sqlite3",
                    scenario="all-success",
                    at_utc=AT,
                )
                gc.collect()
            self.assertEqual(result["status"], "COMPLETE")
            self.assertEqual(
                [warning for warning in caught if "database" in str(warning.message).lower()],
                [],
            )

    def test_unstructured_client_exception_is_non_retryable_and_no_network(self) -> None:
        target_id = "de:05315:11110"
        with tempfile.TemporaryDirectory() as directory:
            persistence = self._new_state(directory, "client-exception.sqlite3")
            client = FakeMddClient(responses={target_id: [RuntimeError("unpersisted detail")]})
            collector = Collector(
                manifest=self.manifest,
                persistence=persistence,
                mdd_client=client,
                quota_guard=QuotaGuard(persistence, reservation_scope="CLIENT_EXCEPTION"),
                safety_margin_seconds=0,
            )
            result = collector.run_once(mode="simulate", at_utc=AT, run_id="client-exception")
            outcome = next(item for item in result.outcomes if item.target_id == target_id)
            self.assertEqual(outcome.http_attempts, 1)
            self.assertEqual(outcome.error_category, CLIENT_EXECUTION_FAILURE)
            self.assertEqual(client.call_count(target_id), 1)
            self.assertEqual(result.live_network_calls, 0)
            self.assertEqual(result.live_client_invocations, 0)
            audit_ids = {
                row["audit_id"]
                for row in persistence.fetch_target_audits()
                if row["source_parent_station_id"] == target_id
            }
            attempts = [row for row in persistence.fetch_attempts() if row["audit_id"] in audit_ids]
            self.assertEqual(len(attempts), 1)
            self.assertEqual(attempts[0]["network_attempted"], 0)
            self.assertEqual(attempts[0]["provider_attempt_confirmed"], 0)
            self.assertEqual(attempts[0]["conservative_quota_accounted"], 0)
            self.assertNotIn("unpersisted detail", json.dumps(attempts))
            usage = persistence.current_month_usage(at_utc=AT)
            self.assertEqual(usage["confirmed_provider_attempts"], 0)
            self.assertEqual(usage["conservative_live_attempts"], 0)
            self.assertTrue(persistence.fetch_quota_permits())
            self.assertTrue(
                all(row["status"] == RELEASED_NO_NETWORK for row in persistence.fetch_quota_permits())
            )
            persistence.close()


if __name__ == "__main__":
    unittest.main()
