from __future__ import annotations

import io
import errno
import json
import os
import socket
import ssl
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError, URLError


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.collector import Collector
from cologne_transit_collector.domain import HttpResponse
from cologne_transit_collector.manifest import LEGACY7_SAMPLING_TARGET_IDS, load_default_manifest
from cologne_transit_collector.persistence import SQLitePersistence
from cologne_transit_collector.quota import InMemoryUsageProvider, QuotaGuard
from cologne_transit_collector.schedule import bucket_for
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
from cologne_transit_collector.trias import parse_trias_response


AT = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
JSON_FIXTURE = ROOT / "tests" / "fixtures" / "stage14_trias_equivalent.json"
XML_FIXTURE = ROOT / "tests" / "fixtures" / "stage14_trias_equivalent.xml"
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
                    "origin",
                    "destination",
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
                    "cancelled": call["cancelled"],
                    "deviation": call["deviation"],
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
        self.assertEqual(len(calls), 0)
        with mock.patch.dict(os.environ, {"MDD_API_KEY": "bad\nkey"}, clear=True):
            malformed = client.fetch(target, request_timestamp_utc=AT, timeout_seconds=30, attempt_number=1)
        self.assertEqual(malformed.category, CREDENTIAL_FORMAT_INVALID)
        self.assertEqual(len(calls), 0)
        with mock.patch.dict(os.environ, {"MDD_API_KEY": "safe-test-key"}, clear=True):
            redirected = client.fetch(target, request_timestamp_utc=AT, timeout_seconds=30, attempt_number=1)
        self.assertEqual(redirected.status_code, 302)
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


if __name__ == "__main__":
    unittest.main()
