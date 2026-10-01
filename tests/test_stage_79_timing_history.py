from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.manifest import load_default_manifest
from cologne_transit_collector.persistence import SQLitePersistence
from cologne_transit_collector.timing import (
    SOURCE_EVENT_IDENTITY_ESTABLISHED,
    SOURCE_EVENT_IDENTITY_INSUFFICIENT,
    source_event_identity,
)
from cologne_transit_collector.trias import parse_trias_response


AT = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _event(
    *,
    result_id: str,
    stop_point_ref: str,
    timetable: str,
    estimated: str,
    recorded: str | None,
    journey_ref: str = "journey-1",
) -> dict:
    timing_path = "$.serviceDelivery.deliveryPayload.stopEventResponse.stopEventResult[0].stopEvent.thisCall.callAtStop.serviceArrival"
    return {
        "event_ordinal": 0,
        "result_id": result_id,
        "operating_day_ref": "2026-10-01",
        "journey_ref": journey_ref,
        "vehicle_journey_ref": None,
        "dated_vehicle_journey_ref": None,
        "line_ref": "line-1",
        "direction_ref": "outbound",
        "operator_ref": "operator-1",
        "calls": [
            {
                "call_scope": "THIS",
                "call_ordinal": 0,
                "source_path": "$.serviceDelivery.deliveryPayload.stopEventResponse.stopEventResult[0].stopEvent.thisCall",
                "stop_point_ref": stop_point_ref,
                "stop_seq_number": 7,
                "source_paths": {
                    "stop_point_ref": "$.serviceDelivery.deliveryPayload.stopEventResponse.stopEventResult[0].stopEvent.thisCall.callAtStop.stopPointRef",
                    "stop_seq_number": "$.serviceDelivery.deliveryPayload.stopEventResponse.stopEventResult[0].stopEvent.thisCall.callAtStop.stopSeqNumber",
                },
                "arrival": {
                    "timetabled_time": timetable,
                    "estimated_time": estimated,
                    "estimated_time_low": None,
                    "estimated_time_high": None,
                    "recorded_at_time": recorded,
                    "source_path": timing_path,
                    "source_paths": {
                        "timetabled_time": f"{timing_path}.timetabledTime",
                        "estimated_time": f"{timing_path}.estimatedTime",
                        "estimated_time_low": None,
                        "estimated_time_high": None,
                        "recorded_at_time": f"{timing_path}.recordedAtTime" if recorded else None,
                    },
                },
            }
        ],
    }


def _snapshot(*, event: dict) -> dict:
    return {
        "parser_version": "stage79-test",
        "events": [event],
        "parser_gaps": [],
        "recorded_at_time_occurrences": [],
        "raw_payload_persisted": False,
    }


class Stage79TimingHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_default_manifest(ROOT)
        cls.target = cls.manifest.target_by_id("de:05315:11201")

    def _state(self) -> tuple[SQLitePersistence, int]:
        directory = tempfile.TemporaryDirectory(prefix="stage79-timing-")
        self.addCleanup(directory.cleanup)
        persistence = SQLitePersistence(Path(directory.name) / "evidence.sqlite3")
        self.addCleanup(persistence.close)
        persistence.materialize_manifest(self.manifest)
        persistence.start_collector_run(
            run_id="stage79-run",
            panel_version=self.target.panel_version,
            logical_due_bucket=1,
            mode="simulate",
            started_at=AT,
        )
        persistence.claim_dispatcher_bucket(
            run_id="stage79-run",
            panel_version=self.target.panel_version,
            logical_due_bucket=1,
            claimed_at=AT,
        )
        persistence.claim_target(
            run_id="stage79-run",
            target=self.target,
            logical_due_bucket=1,
            claimed_at=AT,
        )
        audit_id = persistence.start_target_audit(
            run_id="stage79-run",
            target=self.target,
            logical_due_bucket=1,
            started_at=AT,
        )
        return persistence, audit_id

    def test_identity_is_deterministic_and_excludes_mutable_timing_and_result_id(self) -> None:
        first = _event(
            result_id="result-A",
            stop_point_ref="stop-1",
            timetable="2026-10-01T12:05:00Z[GMT]",
            estimated="2026-10-01T12:06:00Z[GMT]",
            recorded="R1",
        )
        second = _event(
            result_id="result-B",
            stop_point_ref="stop-1",
            timetable="2026-10-01T12:05:00Z[GMT]",
            estimated="2026-10-01T12:07:00Z[GMT]",
            recorded="R2",
        )
        first_identity = source_event_identity(first, first["calls"][0], "ARRIVAL")
        second_identity = source_event_identity(second, second["calls"][0], "ARRIVAL")
        self.assertEqual(first_identity["source_event_identity_status"], SOURCE_EVENT_IDENTITY_ESTABLISHED)
        self.assertEqual(first_identity["source_event_identity"], second_identity["source_event_identity"])

        insufficient = source_event_identity(
            _event(
                result_id="result-C",
                stop_point_ref="stop-1",
                timetable="2026-10-01T12:05:00Z[GMT]",
                estimated="2026-10-01T12:08:00Z[GMT]",
                recorded=None,
                journey_ref="",
            ),
            first["calls"][0],
            "ARRIVAL",
        )
        self.assertEqual(
            insufficient["source_event_identity_status"],
            SOURCE_EVENT_IDENTITY_INSUFFICIENT,
        )
        self.assertIsNone(insufficient["source_event_identity"])
        self.assertIn("journey_ref", insufficient["source_event_identity_missing_fields"])

    def test_clear_structural_gap_containers_do_not_create_nested_gaps(self) -> None:
        body = {
            "serviceDelivery": {
                "deliveryPayload": {
                    "stopEventResponse": {
                        "stopEventResponseContext": {
                            "situations": {"ptSituation": [], "roadSituation": []}
                        },
                        "stopEventResult": [
                            {
                                "resultId": "result-1",
                                "stopEvent": {
                                    "parallelService": [],
                                    "service": {"serviceSection": []},
                                    "thisCall": {
                                        "callAtStop": {
                                            "stopPointRef": "stop-1",
                                            "stopSeqNumber": 1,
                                            "serviceArrival": {"timetabledTime": "T1"},
                                        }
                                    },
                                },
                            }
                        ],
                    }
                }
            }
        }
        parsed = parse_trias_response(json.dumps(body).encode("utf-8"), "application/json")
        gap_names = {gap["normalized_field_name"] for gap in parsed.parser_gaps}
        self.assertNotIn("parallelservice", gap_names)
        self.assertNotIn("situations", gap_names)
        self.assertNotIn("ptsituation", gap_names)
        self.assertNotIn("roadsituation", gap_names)

    def test_three_snapshot_lifecycle_preserves_latest_null_and_last_observed_lineage(self) -> None:
        persistence, audit_id = self._state()
        timetable = "2026-10-01T12:05:00Z[GMT]"
        snapshots = (
            ("result-E1", "E1", None),
            ("result-E2", "E2", "R1"),
            ("result-E3", "E3", None),
            ("result-E4", "E4", None),
        )
        for index, (result_id, estimated, recorded) in enumerate(snapshots):
            persistence.persist_snapshot(
                audit_id=audit_id,
                target=self.target,
                logical_due_bucket=1,
                parsed_snapshot=_snapshot(
                    event=_event(
                        result_id=result_id,
                        stop_point_ref="stop-1",
                        timetable=timetable,
                        estimated=estimated,
                        recorded=recorded,
                    )
                ),
                persisted_at=AT + timedelta(minutes=index),
            )

        rows = persistence.fetch_timing_observations()
        self.assertEqual(len(rows), 4)
        self.assertEqual(len(persistence.fetch_snapshots()), 4)
        self.assertNotIn("ActualTime", rows[0])
        self.assertEqual([row["estimated_time"] for row in rows], ["E1", "E2", "E3", "E4"])
        self.assertEqual([row["recorded_at_time"] for row in rows], [None, "R1", None, None])
        self.assertEqual(len(persistence.fetch_recorded_time_evidence()), 1)

        report = persistence.fetch_timing_recorded_at_time_reporting()
        self.assertEqual(len(report), 1)
        projection = report[0]
        self.assertIsNone(projection["LatestSourceRecordedAtTime"])
        self.assertEqual(projection["LastObservedRecordedAtTime"], "R1")
        self.assertEqual(projection["LastObservedRecordedAtTimeSnapshotId"], 2)
        self.assertEqual(projection["LastObservedRecordedAtTimeAuditId"], audit_id)
        self.assertEqual(projection["RecordedAtTimeNonNullObservationCount"], 1)
        self.assertEqual(projection["FirstObservedRecordedAtTime"], "R1")
        self.assertEqual(projection["FirstObservedRecordedAtTimeSnapshotId"], 2)

    def test_two_source_events_do_not_share_recorded_lineage(self) -> None:
        persistence, audit_id = self._state()
        for index, (stop, journey, recorded) in enumerate(
            (("stop-1", "journey-1", "R1"), ("stop-2", "journey-2", "R2"))
        ):
            persistence.persist_snapshot(
                audit_id=audit_id,
                target=self.target,
                logical_due_bucket=1,
                parsed_snapshot=_snapshot(
                    event=_event(
                        result_id=f"result-{index}",
                        stop_point_ref=stop,
                        timetable=f"2026-10-01T12:0{index}:00Z[GMT]",
                        estimated=f"E{index}",
                        recorded=recorded,
                        journey_ref=journey,
                    )
                ),
                persisted_at=AT + timedelta(minutes=index),
            )
        report = persistence.fetch_timing_recorded_at_time_reporting()
        self.assertEqual(len(report), 2)
        by_stop = {row["SourceParentStationId"] + ":" + row["SourceEventIdentity"]: row for row in report}
        self.assertEqual({row["LastObservedRecordedAtTime"] for row in report}, {"R1", "R2"})
        self.assertEqual(
            sum(row["RecordedAtTimeNonNullObservationCount"] for row in report),
            2,
        )
        self.assertEqual(len(persistence.fetch_recorded_time_evidence()), 2)
        self.assertEqual(len(by_stop), 2)


if __name__ == "__main__":
    unittest.main()
