from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime, timezone
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
        self.assertEqual(RUNNER._parse_duration("6h"), 21600.0)
        self.assertEqual(RUNNER._parse_duration("12 hours"), 43200.0)
        self.assertEqual(RUNNER._parse_duration("300s"), 300.0)
        self.assertEqual(RUNNER._parse_duration("2d"), 172800.0)
        with self.assertRaises(Exception):
            RUNNER._parse_duration("0h")

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
