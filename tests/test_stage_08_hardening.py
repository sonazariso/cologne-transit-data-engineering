from __future__ import annotations

import io
import json
import os
import stat
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

import stage_08_authorization_request_contract_diagnostic as diagnostic  # noqa: E402
import stage_08_mdd_realtime_pilot as pilot  # noqa: E402


TEST_API_KEY = "offline-test-key-123"


def synthetic_response_body(recorded_at_time: str | None) -> bytes:
    timing = {
        "timetabledTime": "2026-09-29T10:00:00Z",
        "estimatedTime": "2026-09-29T10:01:00Z",
    }
    if recorded_at_time is not None:
        timing["recordedAtTime"] = recorded_at_time
    return json.dumps(
        {
            "serviceDelivery": {
                "status": "OK",
                "responseTimestamp": "2026-09-29T09:59:00Z",
                "stopEventResponse": {
                    "stopEventResult": [
                        {
                            "resultId": "synthetic-result-1",
                            "stopEvent": {
                                "service": {
                                    "journeyRef": "synthetic-journey-1",
                                    "lineRef": "synthetic-line-1",
                                    "directionRef": "synthetic-direction-1",
                                },
                                "thisCall": [
                                    {
                                        "callAtStop": {
                                            "stopPointRef": "synthetic-stop-1",
                                            "stopSeqNumber": 7,
                                            "serviceArrival": timing,
                                        }
                                    }
                                ],
                            },
                        }
                    ]
                },
            }
        },
        ensure_ascii=False,
    ).encode("utf-8")


def http_result(body: bytes) -> pilot.HTTPResult:
    now = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)
    return pilot.HTTPResult(
        request_timestamp=now,
        request_timestamp_utc="2026-09-29T10:00:00Z",
        http_status=200,
        content_type="application/json",
        body=body,
        elapsed_ms=1.0,
        request_started_mono=1.0,
        request_ended_mono=1.001,
        failure=None,
        failure_detail=None,
    )


class Stage8HardeningTests(unittest.TestCase):
    def test_credential_preflight_is_secret_free_and_rejects_contamination(self) -> None:
        self.assertEqual(
            pilot.validate_api_key_format(None), pilot.CREDENTIAL_PREFLIGHT_MISSING
        )
        self.assertEqual(
            pilot.validate_api_key_format(TEST_API_KEY), pilot.CREDENTIAL_PREFLIGHT_VALID
        )
        contaminated_values = (
            f"{TEST_API_KEY}\u2028suffix",
            f"{TEST_API_KEY}\n",
            f"{TEST_API_KEY}\r",
        )
        with patch.object(pilot, "build_opener", side_effect=AssertionError("HTTP called")):
            for contaminated in contaminated_values:
                self.assertEqual(
                    pilot.validate_api_key_format(contaminated),
                    pilot.CREDENTIAL_PREFLIGHT_INVALID_FORMAT,
                )
                result = pilot.perform_http_request(contaminated)
                self.assertEqual(result.failure, "CREDENTIAL_FORMAT_INVALID")
                self.assertEqual(result.body, b"")
                self.assertEqual(
                    pilot.credential_preflight_output(
                        pilot.CREDENTIAL_PREFLIGHT_INVALID_FORMAT
                    ),
                    "MDD_API_KEY: INVALID_FORMAT",
                )

    def test_invalid_credential_blocks_pilot_and_diagnostic_before_temp_or_http(self) -> None:
        invalid_key = f"{TEST_API_KEY}\u2028suffix"
        with patch.dict(os.environ, {pilot.MDD_API_KEY_ENV: invalid_key}, clear=True):
            output = io.StringIO()
            with patch.object(
                pilot, "create_temp_directory", side_effect=AssertionError("temp created")
            ) as create_temp, patch.object(
                pilot, "perform_http_request", side_effect=AssertionError("HTTP called")
            ) as perform_http, redirect_stdout(output):
                pilot_summary = pilot.run_pilot()
            self.assertFalse(create_temp.called)
            self.assertFalse(perform_http.called)
            self.assertEqual(pilot_summary["attempted_http_requests"], 0)
            self.assertEqual(pilot_summary["stop_reason"], "CREDENTIAL_FORMAT_INVALID")
            self.assertEqual(pilot_summary["classification"], "CREDENTIAL_FORMAT_INVALID")
            self.assertEqual(pilot_summary["cleanup_result"], "NOT_CREATED")
            self.assertNotIn(invalid_key, output.getvalue())
            self.assertNotIn(invalid_key, json.dumps(pilot_summary, ensure_ascii=False))

            diagnostic_output = io.StringIO()
            with patch.object(
                pilot, "create_temp_directory", side_effect=AssertionError("temp created")
            ) as create_temp, patch.object(
                pilot, "perform_http_request", side_effect=AssertionError("HTTP called")
            ) as perform_http, redirect_stdout(diagnostic_output):
                diagnostic_summary = diagnostic.run_diagnostic()
            self.assertFalse(create_temp.called)
            self.assertFalse(perform_http.called)
            self.assertEqual(diagnostic_summary["attempted_http_requests"], 0)
            self.assertEqual(
                diagnostic_summary["classification"], "CREDENTIAL_FORMAT_INVALID"
            )
            self.assertEqual(diagnostic_summary["diagnostic_cleanup_result"], "NOT_CREATED")
            self.assertNotIn(invalid_key, diagnostic_output.getvalue())
            self.assertNotIn(
                invalid_key, json.dumps(diagnostic_summary, ensure_ascii=False)
            )

    def test_missing_key_preserves_existing_pilot_preflight_result(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            output = io.StringIO()
            with redirect_stdout(output):
                summary = pilot.run_pilot()
        self.assertEqual(summary["preflight"], "MDD_API_KEY: MISSING")
        self.assertEqual(summary["stop_reason"], "MDD_API_KEY_MISSING")
        self.assertEqual(summary["attempted_http_requests"], 0)
        self.assertEqual(summary["cleanup_result"], "NOT_CREATED")
        self.assertIn("MDD_API_KEY: MISSING", output.getvalue())

    def test_transport_boundary_rejects_invalid_key_without_building_opener(self) -> None:
        with patch.object(pilot, "build_opener", side_effect=AssertionError("HTTP called")):
            result = pilot.perform_http_request(f"{TEST_API_KEY}\u2028suffix")
        self.assertEqual(result.failure, "CREDENTIAL_FORMAT_INVALID")
        self.assertIsNone(result.http_status)
        self.assertEqual(result.body, b"")

    def run_synthetic_pilot(self, bodies: list[bytes]) -> tuple[dict, str | None]:
        responses = iter(bodies)

        def fake_request(*_args, **_kwargs):
            return http_result(next(responses))

        with patch.dict(os.environ, {pilot.MDD_API_KEY_ENV: TEST_API_KEY}, clear=True), patch.object(
            pilot, "MAX_LOGICAL_SLOTS", len(bodies)
        ), patch.object(
            pilot, "SAMPLE_INTERVAL_SECONDS", 60
        ), patch.object(
            pilot.time, "sleep", return_value=None
        ), patch.object(pilot, "perform_http_request", side_effect=fake_request):
            summary = pilot.run_pilot()
        target = summary.get("execution", {}).get("raw_and_jsonl_directory_cleanup_target")
        return summary, target

    def test_success_without_recorded_time_uses_normal_cleanup(self) -> None:
        summary, target = self.run_synthetic_pilot([synthetic_response_body(None)])
        self.assertEqual(summary["stage_8_status"], "COMPLETE")
        self.assertEqual(summary["execution"]["cleanup_result"], "SUCCEEDED")
        self.assertFalse(summary["execution"]["cleanup_pending"])
        self.assertFalse(summary["recorded_at_time_raw_evidence_response_count"])
        self.assertFalse(target and Path(target).exists())
        aggregate = summary["metrics"]["recorded_at_time_aggregate"]
        self.assertEqual(aggregate["successful_responses_inspected"], 1)
        self.assertEqual(aggregate["non_null_recorded_at_time_occurrences"], 0)
        self.assertEqual(aggregate["absence_interpretation"], "BOUNDED_OBSERVED_SAMPLE_ONLY")

    def test_positive_recorded_time_retains_only_raw_positive_response_for_24_hours(self) -> None:
        exact_recorded_time = "2026-09-29T10:01:23Z"
        summary, target = self.run_synthetic_pilot(
            [
                synthetic_response_body(exact_recorded_time),
                synthetic_response_body(None),
            ]
        )
        self.assertEqual(summary["stage_8_status"], "COMPLETE")
        self.assertEqual(
            summary["execution"]["cleanup_result"],
            pilot.RECORDED_AT_TIME_EVIDENCE_RETAINED_MAX_24_HOURS,
        )
        self.assertTrue(summary["execution"]["cleanup_pending"])
        self.assertEqual(
            summary["execution"]["cleanup_max_retention_hours"],
            pilot.MAX_RAW_EVIDENCE_RETENTION_HOURS,
        )
        self.assertIsNotNone(summary["execution"]["cleanup_retention_deadline_utc"])
        self.assertEqual(summary["recorded_at_time_raw_evidence_response_count"], 1)
        self.assertEqual(
            summary["recorded_at_time_raw_evidence_status"],
            pilot.RECORDED_AT_TIME_RAW_EVIDENCE,
        )

        occurrences = summary["recorded_at_time_occurrences"]
        self.assertEqual(len(occurrences), 1)
        occurrence = occurrences[0]
        self.assertEqual(occurrence["recorded_at_time"], exact_recorded_time)
        self.assertEqual(occurrence["call_scope"], "THIS")
        self.assertEqual(occurrence["arrival_departure_context"], "ARRIVAL")
        self.assertEqual(occurrence["recorded_at_time_observed_at"], occurrence["observed_at"])
        self.assertEqual(
            occurrence["later_response_result"],
            "OMITTED_OR_DISAPPEARED_IN_LATER_RESPONSE",
        )
        aggregate = summary["metrics"]["recorded_at_time_aggregate"]
        self.assertEqual(aggregate["non_null_recorded_at_time_occurrences"], 1)
        self.assertEqual(aggregate["distinct_events_with_recorded_at_time"], 1)
        self.assertEqual(aggregate["scope_distribution"], {"THIS": 4})
        self.assertEqual(
            aggregate["arrival_departure_distribution"], {"ARRIVAL": 2, "DEPARTURE": 2}
        )
        self.assertEqual(aggregate["recorded_at_time_scope_distribution"], {"THIS": 1})
        self.assertEqual(
            aggregate["recorded_at_time_arrival_departure_distribution"], {"ARRIVAL": 1}
        )

        self.assertIsNotNone(target)
        retained_directory = Path(target)
        self.assertTrue(retained_directory.exists())
        self.assertEqual(stat.S_IMODE(retained_directory.stat().st_mode), 0o700)
        retained_files = list(retained_directory.iterdir())
        self.assertEqual(len(retained_files), 2)
        raw_files = [path for path in retained_files if path.suffix == ".payload"]
        parsed_files = [path for path in retained_files if path.suffix == ".jsonl"]
        self.assertEqual(len(raw_files), 1)
        self.assertEqual(len(parsed_files), 1)
        self.assertEqual(stat.S_IMODE(raw_files[0].stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(parsed_files[0].stat().st_mode), 0o600)
        self.assertEqual(raw_files[0].read_bytes(), synthetic_response_body(exact_recorded_time))
        self.assertNotIn(TEST_API_KEY.encode("utf-8"), raw_files[0].read_bytes())
        parsed_evidence = json.loads(parsed_files[0].read_text(encoding="utf-8"))
        self.assertEqual(parsed_evidence["recorded_at_time"], exact_recorded_time)
        self.assertEqual(parsed_evidence["call_scope"], "THIS")
        self.assertEqual(parsed_evidence["arrival_departure_context"], "ARRIVAL")
        pilot.cleanup_directory(retained_directory)

    def test_raw_artifact_secret_checks_reject_key_and_authorization_material(self) -> None:
        directory = pilot.create_temp_directory()
        try:
            with self.assertRaises(pilot.CredentialContentError):
                pilot.write_raw_artifact(directory, "key.payload", TEST_API_KEY.encode(), TEST_API_KEY)
            with self.assertRaises(pilot.CredentialContentError):
                pilot.write_raw_artifact(
                    directory,
                    "auth.payload",
                    b"Authorization: Bearer synthetic-token",
                    TEST_API_KEY,
                )
        finally:
            pilot.cleanup_directory(directory)

    def test_authorization_control_marks_synthetic_positive_evidence(self) -> None:
        directory = pilot.create_temp_directory()
        evidence_writer = pilot.JsonlWriter(
            directory / "authorization_request_contract_diagnostic.jsonl"
        )
        diagnostic.diagnostic_run_id = "offline-diagnostic-run"
        try:
            with patch.object(
                diagnostic.pilot,
                "perform_http_request",
                return_value=http_result(synthetic_response_body("2026-09-29T10:01:23Z")),
            ):
                record = diagnostic.run_control(
                    control_name="A",
                    station=diagnostic.CONTROL_A_STATION,
                    stop_point_ref=diagnostic.CONTROL_A_STOP_POINT_REF,
                    number_of_results=diagnostic.CONTROL_RESULT_COUNT,
                    api_key=TEST_API_KEY,
                    directory=directory,
                    evidence_writer=evidence_writer,
                )
            self.assertEqual(
                record["raw_evidence_status"], pilot.RECORDED_AT_TIME_RAW_EVIDENCE
            )
            raw_path = Path(record["raw_artifact_path"])
            self.assertTrue(raw_path.exists())
            self.assertEqual(
                pilot.cleanup_directory(directory, retained_raw_paths={raw_path}),
                pilot.RECORDED_AT_TIME_EVIDENCE_RETAINED_MAX_24_HOURS,
            )
            self.assertTrue(raw_path.exists())
        finally:
            evidence_writer.close()
            if directory.exists():
                pilot.cleanup_directory(directory)


if __name__ == "__main__":
    unittest.main()
