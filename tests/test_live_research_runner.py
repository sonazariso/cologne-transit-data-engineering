from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

_RUNNER_SPEC = importlib.util.spec_from_file_location(
    "live_research_runner",
    ROOT / "scripts" / "live_research_runner.py",
)
assert _RUNNER_SPEC is not None and _RUNNER_SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(_RUNNER_SPEC)
_RUNNER_SPEC.loader.exec_module(RUNNER)

_ZIP_SPEC = importlib.util.spec_from_file_location(
    "package_source_zip",
    ROOT / "scripts" / "package_source_zip.py",
)
assert _ZIP_SPEC is not None and _ZIP_SPEC.loader is not None
ZIP_HELPER = importlib.util.module_from_spec(_ZIP_SPEC)
_ZIP_SPEC.loader.exec_module(ZIP_HELPER)


class LiveResearchRunnerTests(unittest.TestCase):
    def test_duration_parser_supports_explicit_units(self) -> None:
        self.assertEqual(RUNNER._parse_duration("4h"), 14400.0)
        self.assertEqual(RUNNER._parse_duration("6h"), 21600.0)
        self.assertEqual(RUNNER._parse_duration("12 hours"), 43200.0)
        self.assertEqual(RUNNER._parse_duration("300s"), 300.0)
        self.assertEqual(RUNNER._parse_duration("2d"), 172800.0)
        with self.assertRaises(Exception):
            RUNNER._parse_duration("0h")

    def test_runner_default_is_four_hours_and_custom_duration_remains_supported(self) -> None:
        parser = RUNNER._parser()
        self.assertEqual(parser.parse_args([]).duration, 14400.0)
        self.assertEqual(parser.parse_args(["--duration", "6h"]).duration, 21600.0)
        self.assertIn("for example 4h or 14400", " ".join(parser.format_help().split()))
        self.assertIn("default: 4h", " ".join(parser.format_help().split()))

    def test_retry_diagnostics_separate_http_500_from_quota_denial(self) -> None:
        result = RUNNER._retry_diagnostics(
            attempts=[
                {
                    "audit_id": 7,
                    "attempt_number": 1,
                    "http_status": 500,
                    "transport_failure_category": None,
                    "retry_decision": "RETRY_CANDIDATE",
                }
            ],
            audits=[
                {
                    "audit_id": 7,
                    "collector_run_id": "run-7",
                    "source_parent_station_id": "de:05315:11110",
                    "logical_due_bucket": 123,
                }
            ],
            quota_audit=[
                {
                    "collector_run_id": "run-7",
                    "source_parent_station_id": "de:05315:11110",
                    "logical_due_bucket": 123,
                    "decision_kind": "RETRY",
                    "decision_json": json.dumps(
                        {
                            "allowed": False,
                            "decision": "RETRY_DENIED_QUOTA_STATE_UNAVAILABLE",
                            "reason": "safe shared-credential state is unavailable",
                        }
                    ),
                }
            ],
        )
        failure = result["failure_attempts"][0]
        self.assertEqual(
            failure["original_failure"]["classification"],
            "TRANSIENT_PROVIDER_SERVER_HTTP_FAILURE",
        )
        self.assertTrue(failure["retry_policy_eligible"])
        self.assertEqual(
            failure["retry_suppression_reason"],
            "RETRY_DENIED_QUOTA_STATE_UNAVAILABLE",
        )
        self.assertFalse(failure["original_failure"]["cause_inferred"])
        self.assertIn("does not reclassify an HTTP 500", result["statement"])

    def test_extension_report_recommends_eight_total_hours_without_auto_extension(self) -> None:
        start = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
        end = start + timedelta(hours=4)
        session = RUNNER._session_template(
            session_id="live-research-test",
            start=start,
            end=end,
            duration_seconds=4 * 60 * 60,
            report_json=Path("/tmp/report.json"),
            summary_markdown=Path("/tmp/report.md"),
            session_metadata=Path("/tmp/session.json"),
        )
        session["completion_state"] = "COMPLETED_DURATION"
        evidence = RUNNER.evidence_from_mappings()
        report = RUNNER.build_final_report(
            session=session,
            manifest=RUNNER.load_default_manifest(ROOT),
            repository_root=ROOT,
            full_evidence=evidence,
            session_evidence=evidence,
            database_status={"integrity_check": "ok"},
            backup_status={"integrity_check": "ok"},
        )
        extension = report["extension_rule"]
        self.assertEqual(extension["recommend_total_duration_hours"], 8)
        self.assertFalse(extension["automatic_extension_performed"])
        self.assertTrue(extension["extension_requires_explicit_later_invocation"])
        self.assertIn("recommended total duration: `8 hours`", RUNNER._markdown_summary(report))

    def test_expected_buckets_are_current_utc_buckets_without_catch_up(self) -> None:
        start = datetime(2026, 10, 1, 12, 2, tzinfo=timezone.utc)
        end = datetime(2026, 10, 1, 12, 12, tzinfo=timezone.utc)
        rows = RUNNER._expected_bucket_rows(start, end)
        self.assertEqual([row["logical_due_bucket"] for row in rows], [5969520, 5969521, 5969522])
        self.assertEqual(len({row["identity"] for row in rows}), 3)

    def test_recorded_time_lifecycle_report_preserves_latest_null_and_leaf_lineage(self) -> None:
        rows = [
            {
                "timing_observation_id": 1,
                "snapshot_id": 10,
                "source_event_identity": "MDD_SOURCE_EVENT_V2|one",
                "source_event_identity_status": "SOURCE_EVENT_IDENTITY_ESTABLISHED",
                "observed_at_utc": "2026-10-01T12:00:00Z",
                "estimated_time": "E1",
                "recorded_at_time": "R1",
                "recorded_at_time_source_path": "$.call.arrival.recordedAtTime",
            },
            {
                "timing_observation_id": 2,
                "snapshot_id": 11,
                "source_event_identity": "MDD_SOURCE_EVENT_V2|one",
                "source_event_identity_status": "SOURCE_EVENT_IDENTITY_ESTABLISHED",
                "observed_at_utc": "2026-10-01T12:05:00Z",
                "estimated_time": "E2",
                "recorded_at_time": None,
                "recorded_at_time_source_path": None,
            },
        ]
        projection = [
            {
                "LatestSourceRecordedAtTime": None,
                "LatestSourceRecordedAtTimeSourcePath": None,
                "LastObservedRecordedAtTime": "R1",
                "LastObservedRecordedAtTimeSourcePath": "$.call.arrival.recordedAtTime",
                "FirstObservedRecordedAtTime": "R1",
                "FirstObservedRecordedAtTimeSourcePath": "$.call.arrival.recordedAtTime",
            }
        ]
        result = RUNNER._timing_lifecycle_report(rows, projection)
        self.assertEqual(result["distinct_established_source_event_identities"], 1)
        self.assertEqual(result["events_seen_in_more_than_one_snapshot"], 1)
        self.assertEqual(result["events_with_estimated_time_revisions"], 1)
        self.assertEqual(result["total_estimated_time_revisions"], 1)
        self.assertEqual(result["total_non_null_recorded_at_time_occurrences"], 1)
        self.assertEqual(result["events_with_latest_recorded_at_time_null_and_historical_non_null"], 1)
        self.assertTrue(all(result["first_last_recorded_at_time_lineage_checks"].values()))

    def test_source_zip_filter_excludes_all_requested_macos_metadata_shapes(self) -> None:
        self.assertTrue(ZIP_HELPER.is_macos_metadata(".DS_Store"))
        self.assertTrue(ZIP_HELPER.is_macos_metadata("folder/._resource"))
        self.assertTrue(ZIP_HELPER.is_macos_metadata("__MACOSX/._resource"))
        self.assertFalse(ZIP_HELPER.is_macos_metadata("src/module.py"))

    def test_source_zip_workflow_verifies_archive_members(self) -> None:
        with tempfile.TemporaryDirectory(prefix="source-zip-") as directory:
            repository = Path(directory) / "repository"
            repository.mkdir()
            (repository / "src").mkdir()
            (repository / "README.md").write_text("legitimate\n", encoding="utf-8")
            (repository / "src" / "module.py").write_text("pass\n", encoding="utf-8")
            (repository / ".DS_Store").write_bytes(b"metadata")
            (repository / "._README.md").write_bytes(b"metadata")
            (repository / "__MACOSX").mkdir()
            (repository / "__MACOSX" / "._README.md").write_bytes(b"metadata")
            subprocess.run(["git", "init", "--quiet", str(repository)], check=True)
            subprocess.run(["git", "-C", str(repository), "add", "."], check=True)

            output = Path(directory) / "v80.zip"
            created = ZIP_HELPER.create_source_zip(repository, output)
            with zipfile.ZipFile(created, "r") as archive:
                members = archive.namelist()
            self.assertEqual(members, ["README.md", "src/module.py"])
            self.assertEqual(ZIP_HELPER.verify_source_zip(created), ())

    def test_session_evidence_filter_accepts_collector_run_table_run_id(self) -> None:
        evidence = RUNNER.evidence_from_mappings(
            collector_runs=[{"run_id": "run-1"}, {"run_id": "run-2"}],
            target_audits=[
                {"collector_run_id": "run-1", "audit_id": 11},
                {"collector_run_id": "run-2", "audit_id": 12},
            ],
        )
        filtered = RUNNER._filter_session_evidence(evidence, {"run-1"})
        self.assertEqual([row["run_id"] for row in filtered.collector_runs], ["run-1"])
        self.assertEqual([row["audit_id"] for row in filtered.target_audits], [11])


if __name__ == "__main__":
    unittest.main()
