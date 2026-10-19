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
from cologne_transit_collector.schedule import bucket_for
from cologne_transit_collector.timing import (
    SOURCE_EVENT_IDENTITY_AMBIGUOUS_CORE,
    SOURCE_EVENT_IDENTITY_ESTABLISHED,
    SOURCE_EVENT_IDENTITY_VERSION,
    iter_timing_observations,
    source_event_identity,
)
from cologne_transit_collector.trias import parse_trias_response
from cologne_transit_stage15 import Stage15Analyzer
from cologne_transit_stage15.evidence import evidence_from_mappings


AT = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _event(
    *,
    event_ordinal: int = 0,
    result_id: str = "result-1",
    line_ref: str | None = None,
    direction_ref: str | None = None,
    operator_ref: str | None = None,
    stop_seq_number: int | None = None,
    estimated: str = "E1",
    recorded: str | None = None,
) -> dict:
    return {
        "event_ordinal": event_ordinal,
        "result_id": result_id,
        "operating_day_ref": "2026-10-01",
        "journey_ref": "journey-1",
        "line_ref": line_ref,
        "direction_ref": direction_ref,
        "operator_ref": operator_ref,
        "calls": [
            {
                "call_scope": "THIS",
                "call_ordinal": 0,
                "source_path": f"$.result[{event_ordinal}].thisCall",
                "stop_point_ref": "stop-1",
                "stop_seq_number": stop_seq_number,
                "arrival": {
                    "timetabled_time": "2026-10-01T12:05:00Z[GMT]",
                    "estimated_time": estimated,
                    "recorded_at_time": recorded,
                    "source_path": "$.result.thisCall.serviceArrival",
                    "source_paths": {
                        "timetabled_time": "$.result.thisCall.serviceArrival.timetabledTime",
                        "estimated_time": "$.result.thisCall.serviceArrival.estimatedTime",
                        "recorded_at_time": (
                            "$.result.thisCall.serviceArrival.recordedAtTime"
                            if recorded is not None
                            else None
                        ),
                    },
                },
            }
        ],
    }


def _snapshot(event: dict) -> dict:
    return {
        "parser_version": "stage80-test",
        "events": [event],
        "parser_gaps": [],
        "recorded_at_time_occurrences": [],
        "raw_payload_persisted": False,
    }


def _service_body(*, xml: bool = False) -> bytes:
    if xml:
        return b"""<?xml version="1.0" encoding="UTF-8"?>
<Trias xmlns="http://www.vdv.de/trias">
  <ServiceDelivery><Status>OK</Status><DeliveryPayload><StopEventResponse>
    <StopEventResult><ResultId>result-1</ResultId><StopEvent><Service>
      <ServiceSection><LineRef><Value>line-1</Value></LineRef><DirectionRef><Value>out</Value></DirectionRef><OperatorRef><Value>op-1</Value></OperatorRef><Mode><PtMode>TRAM</PtMode><TramSubmode>CITY_TRAM</TramSubmode><Name><Text>Tram</Text></Name></Mode><PublishedLineName><Text>9</Text></PublishedLineName><Name>Section 1</Name><ShortName>S1</ShortName><Description>First</Description></ServiceSection>
      <ServiceSection><LineRef><Value>line-2</Value></LineRef><DirectionRef><Value>back</Value></DirectionRef><OperatorRef><Value>op-2</Value></OperatorRef><Mode><PtMode>BUS</PtMode><BusSubmode>CITY_BUS</BusSubmode></Mode><PublishedLineName><Text>120</Text></PublishedLineName><Name>Section 2</Name><UnmappedServiceSectionExtension><Value>keep-gap</Value></UnmappedServiceSectionExtension></ServiceSection>
    </Service></StopEvent></StopEventResult>
  </StopEventResponse></DeliveryPayload></ServiceDelivery>
</Trias>"""
    body = {
        "serviceDelivery": {
            "status": "OK",
            "deliveryPayload": {
                "stopEventResponse": {
                    "stopEventResult": [
                        {
                            "resultId": "result-1",
                            "stopEvent": {
                                "service": {
                                    "serviceSection": [
                                        {
                                            "lineRef": {"value": "line-1"},
                                            "directionRef": {"value": "out"},
                                            "operatorRef": {"value": "op-1"},
                                            "mode": {
                                                "ptMode": "TRAM",
                                                "tramSubmode": "CITY_TRAM",
                                                "name": [{"text": "Tram"}],
                                            },
                                            "publishedLineName": [{"text": "9"}],
                                            "name": "Section 1",
                                            "shortName": "S1",
                                            "description": "First",
                                        },
                                        {
                                            "lineRef": {"value": "line-2"},
                                            "directionRef": {"value": "back"},
                                            "operatorRef": {"value": "op-2"},
                                            "mode": {"ptMode": "BUS", "busSubmode": "CITY_BUS"},
                                            "publishedLineName": [{"text": "120"}],
                                            "name": "Section 2",
                                            "unmappedServiceSectionExtension": {"value": "keep-gap"},
                                        },
                                    ]
                                }
                            }
                        }
                    ]
                }
            }
        }
    }
    return json.dumps(body).encode("utf-8")


class Stage80CorrectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_default_manifest(ROOT)
        cls.target = cls.manifest.target_by_id("de:05315:11201")

    def test_identity_version_and_optional_fields_never_change_hash(self) -> None:
        variants = [
            _event(result_id="A"),
            _event(result_id="B", line_ref="line-1"),
            _event(result_id="C", stop_seq_number=7),
            _event(result_id="D", estimated="E2", recorded="R1", direction_ref="out", operator_ref="op"),
        ]
        identities = [
            source_event_identity(event, event["calls"][0], "ARRIVAL")
            for event in variants
        ]
        self.assertEqual({item["source_event_identity"] for item in identities}, {identities[0]["source_event_identity"]})
        self.assertEqual(identities[0]["source_event_identity_version"], SOURCE_EVENT_IDENTITY_VERSION)
        self.assertTrue(identities[1]["source_event_identity_basis"]["disambiguating_source_fields"])

    def test_same_snapshot_core_collision_is_explicit_and_not_aggregated(self) -> None:
        rows = iter_timing_observations(
            {
                "events": [
                    _event(event_ordinal=0, result_id="A", line_ref="line-a"),
                    _event(event_ordinal=1, result_id="B", line_ref="line-b"),
                ]
            }
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {row["source_event_identity_status"] for row in rows},
            {SOURCE_EVENT_IDENTITY_AMBIGUOUS_CORE},
        )
        self.assertEqual(len({row["source_event_identity"] for row in rows}), 1)
        self.assertTrue(all(row["source_event_identity_ambiguity"] for row in rows))

    def test_service_section_json_xml_parity_and_unknown_extension_gap(self) -> None:
        parsed_json = parse_trias_response(_service_body(), "application/json")
        parsed_xml = parse_trias_response(_service_body(xml=True), "application/xml")
        self.assertEqual(len(parsed_json.service_section_evidence), 2)
        self.assertEqual(len(parsed_xml.service_section_evidence), 2)
        for parsed in (parsed_json, parsed_xml):
            first, second = parsed.events[0]["service_section_evidence"]
            self.assertEqual(first["section_ordinal"], 0)
            self.assertEqual(second["section_ordinal"], 1)
            self.assertEqual(first["line_ref"], "line-1")
            self.assertEqual(first["direction_ref"], "out")
            self.assertEqual(first["operator_ref"], "op-1")
            self.assertEqual(first["pt_mode"], "TRAM")
            self.assertEqual(first["tram_submode"], "CITY_TRAM")
            self.assertEqual(first["published_line_name_text"], "9")
            self.assertIn("servicesection[1]", second["source_path"])
            self.assertTrue(second["mode_source_representation"])
            self.assertTrue(second["source_representation"])
            self.assertEqual(
                {gap["normalized_field_name"] for gap in parsed.parser_gaps},
                {"unmappedservicesectionextension"},
            )
        self.assertEqual(
            [
                (item["line_ref"], item["direction_ref"], item["pt_mode"], item["published_line_name_text"])
                for item in parsed_json.service_section_evidence
            ],
            [
                (item["line_ref"], item["direction_ref"], item["pt_mode"], item["published_line_name_text"])
                for item in parsed_xml.service_section_evidence
            ],
        )

    def _state(self) -> tuple[SQLitePersistence, int, Path]:
        directory = tempfile.TemporaryDirectory(prefix="stage80-")
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "evidence.sqlite3"
        persistence = SQLitePersistence(path)
        self.addCleanup(persistence.close)
        persistence.materialize_manifest(self.manifest)
        persistence.start_collector_run(
            run_id="stage80-run",
            panel_version=self.target.panel_version,
            logical_due_bucket=1,
            mode="simulate",
            started_at=AT,
        )
        persistence.claim_dispatcher_bucket(
            run_id="stage80-run",
            panel_version=self.target.panel_version,
            logical_due_bucket=1,
            claimed_at=AT,
        )
        persistence.claim_target(
            run_id="stage80-run",
            target=self.target,
            logical_due_bucket=1,
            claimed_at=AT,
        )
        audit_id = persistence.start_target_audit(
            run_id="stage80-run",
            target=self.target,
            logical_due_bucket=1,
            started_at=AT,
        )
        return persistence, audit_id, path

    def test_backfill_is_idempotent_and_preserves_original_snapshot_json(self) -> None:
        persistence, audit_id, _path = self._state()
        event = _event(recorded="R1")
        event["service_section_evidence"] = [
            {
                "section_ordinal": 0,
                "source_path": "$.service.section[0]",
                "result_id": "result-1",
                "line_ref": "line-1",
                "line_ref_source_path": "$.service.section[0].lineRef",
                "mode_source_representation": {"ptMode": "TRAM"},
                "pt_mode": "TRAM",
                "published_line_name": [{"text": "9"}],
                "published_line_name_text": "9",
                "source_representation": {"lineRef": {"value": "line-1"}},
            }
        ]
        payload = _snapshot(event)
        persistence.persist_snapshot(
            audit_id=audit_id,
            target=self.target,
            logical_due_bucket=1,
            parsed_snapshot=payload,
            persisted_at=AT,
        )
        original = persistence.fetch_snapshots()[0]["snapshot_json"]
        persistence._connection.execute("DELETE FROM timing_observations")
        persistence._connection.execute("DELETE FROM recorded_at_time_evidence")
        persistence._connection.execute("DELETE FROM service_section_observations")
        persistence._connection.commit()

        first = persistence.backfill_existing_snapshots()
        self.assertEqual(first["timing_rows_inserted"], 1)
        self.assertEqual(first["service_section_rows_inserted"], 1)
        self.assertEqual(first["recorded_at_time_reconciliation"]["unmatched_timing_rows"], 0)
        self.assertEqual(persistence.fetch_snapshots()[0]["snapshot_json"], original)

        second = persistence.backfill_existing_snapshots()
        self.assertEqual(second["timing_rows_inserted"], 0)
        self.assertEqual(second["service_section_rows_inserted"], 0)
        self.assertEqual(len(persistence.fetch_timing_observations()), 1)
        self.assertEqual(len(persistence.fetch_recorded_time_evidence()), 1)
        self.assertEqual(len(persistence.fetch_service_section_observations()), 1)

    def test_current_database_open_keeps_migration_timestamp(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stage80-metadata-") as directory:
            path = Path(directory) / "evidence.sqlite3"
            first = SQLitePersistence(path)
            initial = first.get_metadata()
            first.close()
            second = SQLitePersistence(path)
            self.assertEqual(second.get_metadata()["created_at_utc"], initial["created_at_utc"])
            self.assertEqual(
                second.get_metadata()["last_schema_migration_at_utc"],
                initial["last_schema_migration_at_utc"],
            )
            second.close()

    def test_historical_window_does_not_see_future_recorded_time_state(self) -> None:
        bucket = bucket_for(AT).index
        identity = "MDD_SOURCE_EVENT_V2|stable"
        rows = []
        for index, (offset, recorded) in enumerate(
            ((1, "R1"), (6, None), (7, "R2")),
            start=1,
        ):
            rows.append(
                {
                    "timing_observation_id": index,
                    "logical_due_bucket": bucket,
                    "snapshot_id": index,
                    "audit_id": 1,
                    "source_event_identity": identity,
                    "source_event_identity_status": SOURCE_EVENT_IDENTITY_ESTABLISHED,
                    "observed_at_utc": (AT + timedelta(minutes=offset)).isoformat().replace("+00:00", "Z"),
                    "captured_at_utc": (AT + timedelta(minutes=offset)).isoformat().replace("+00:00", "Z"),
                    "recorded_at_time": recorded,
                    "estimated_time": f"E{index}",
                    "timing_context": "ARRIVAL",
                }
            )
        evidence = evidence_from_mappings(timing_observations=rows)
        contract = json.loads((ROOT / "config" / "stage15_validation_contract.json").read_text())
        report = Stage15Analyzer(
            self.manifest,
            contract=contract,
            evidence_adapter=type("Adapter", (), {"read_only": True, "read_evidence": lambda self: evidence})(),
            window_start_utc=AT,
            window_end_utc=AT + timedelta(minutes=5),
        ).analyze()
        projection = report["quality"]["timing_history"]["reporting_projection"][0]
        self.assertEqual(projection["LastObservedRecordedAtTime"], "R1")
        self.assertEqual(projection["LatestSourceRecordedAtTime"], "R1")
        self.assertEqual(report["quality"]["timing_history"]["timing_observation_count"], 1)
        self.assertEqual(report["quality"]["timing_history"]["events_with_estimate_revision_history"], 0)


if __name__ == "__main__":
    unittest.main()
