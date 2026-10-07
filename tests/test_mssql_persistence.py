from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlConnectionSettings,
    MssqlPersistence,
    build_mssql_snapshot_rows,
)
from cologne_transit_collector.trias import parse_trias_response  # noqa: E402


AT = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


class FailingConnection:
    def __init__(self) -> None:
        self.rollback_count = 0
        self.commit_count = 0
        self.closed = False

    def cursor(self):
        raise RuntimeError("fixture database failure")

    def rollback(self):
        self.rollback_count += 1

    def commit(self):
        self.commit_count += 1

    def close(self):
        self.closed = True


class MssqlPersistenceTests(unittest.TestCase):
    @staticmethod
    def _snapshot(name: str) -> dict:
        path = ROOT / "tests" / "fixtures" / name
        return parse_trias_response(path.read_bytes(), "application/json").to_dict()

    def test_driver_settings_use_external_credentials_and_redacted_diagnostics(self) -> None:
        settings = MssqlConnectionSettings.from_environment(
            {
                "MSSQL_HOST": "172.16.192.128",
                "MSSQL_DATABASE": "CologneTransitIntelligence",
                "MSSQL_USER": "collector-user",
                "MSSQL_PASSWORD": "secret-value",
                "MSSQL_PORT": "1433",
            }
        )
        self.assertEqual(settings.port, 1433)
        self.assertEqual(settings.database, "CologneTransitIntelligence")
        diagnostics = settings.redacted()
        self.assertNotIn("secret-value", json.dumps(diagnostics))
        self.assertTrue(diagnostics["password_configured"])

    def test_source_contract_rows_preserve_all_six_timing_contexts(self) -> None:
        rows = build_mssql_snapshot_rows(self._snapshot("stage14_trias_equivalent.json"))
        self.assertEqual(len(rows["calls"]), 3)
        self.assertEqual(len(rows["timings"]), 6)
        self.assertEqual(
            {(row["call_scope"], row["timing_context"]) for row in rows["timings"]},
            {
                ("PREVIOUS", "ARRIVAL"),
                ("PREVIOUS", "DEPARTURE"),
                ("THIS", "ARRIVAL"),
                ("THIS", "DEPARTURE"),
                ("ONWARD", "ARRIVAL"),
                ("ONWARD", "DEPARTURE"),
            },
        )
        recorded = [row for row in rows["timings"] if row["recorded_at_time"] is not None]
        self.assertEqual(len(recorded), 6)
        self.assertTrue(all(row["recorded_at_time_source_path"] for row in recorded))
        self.assertTrue(all(row["source_event_identity"] for row in recorded))

    def test_multiple_service_sections_and_provider_attributes_keep_occurrence_grain(self) -> None:
        rows = build_mssql_snapshot_rows(self._snapshot("mdd_mac_multi_occurrence.json"))
        self.assertEqual(len(rows["service_sections"]), 2)
        self.assertEqual(
            [row["line_ref"] for row in rows["service_sections"]],
            ["line-section-1", "line-section-2"],
        )
        self.assertEqual(len(rows["attributes"]), 2)
        self.assertEqual([row["code_source_text"] for row in rows["attributes"]], ["INC-1", "INC-2"])
        self.assertNotIn("root_cause", json.dumps(rows).lower())

    def test_parser_gaps_and_situation_evidence_are_not_dropped(self) -> None:
        rows = build_mssql_snapshot_rows(self._snapshot("stage14_trias_source_contract_correction.json"))
        self.assertEqual(len(rows["situations"]), 1)
        situation = rows["situations"][0]
        self.assertEqual(situation["situation_number"], "situation-1")
        affected_entity = json.loads(situation["affected_entity_json"])
        self.assertEqual(affected_entity["affected_entity"]["vehicleJourneyRef"], "affected-vehicle-journey-2")
        self.assertEqual(affected_entity["affected_journey"]["journeyRef"], "affected-journey-1")
        self.assertEqual(len(rows["attributes"]), 2)
        gap_names = {row["normalized_field_name"] for row in rows["parser_gaps"]}
        self.assertIn("unmappedserviceextension", gap_names)
        self.assertNotIn("attribute", gap_names)

    def test_later_null_recorded_at_time_is_an_append_only_row(self) -> None:
        first = self._snapshot("stage14_trias_equivalent.json")
        second = json.loads(json.dumps(first))
        second["events"][0]["previous_calls"][0]["arrival"]["recorded_at_time"] = None
        second["events"][0]["previous_calls"][0]["arrival"]["source_paths"]["recorded_at_time"] = None
        second["events"][0]["calls"][0]["arrival"]["recorded_at_time"] = None
        second["events"][0]["calls"][0]["arrival"]["source_paths"]["recorded_at_time"] = None
        second["events"][0]["recorded_at_time_occurrences"][0]["recorded_at_time"] = None
        second["events"][0]["recorded_at_time_occurrences"][0]["source_path"] = None
        first_rows = build_mssql_snapshot_rows(first)["timings"]
        second_rows = build_mssql_snapshot_rows(second)["timings"]
        first_value = next(row for row in first_rows if row["call_scope"] == "PREVIOUS" and row["timing_context"] == "ARRIVAL")
        second_value = next(row for row in second_rows if row["call_scope"] == "PREVIOUS" and row["timing_context"] == "ARRIVAL")
        self.assertEqual(first_value["recorded_at_time"], "2026-10-01T11:56:30Z[GMT]")
        self.assertIsNone(second_value["recorded_at_time"])
        self.assertIsNone(second_value["recorded_at_time_source_path"])
        self.assertNotEqual(first_value["source_event_identity"], None)
        self.assertEqual(first_value["source_event_identity"], second_value["source_event_identity"])

    def test_database_failure_rolls_back_and_closes_connection(self) -> None:
        connection = FailingConnection()
        adapter = MssqlPersistence(
            MssqlConnectionSettings(host="127.0.0.1", user="u", password="p"),
            connection_factory=lambda: connection,
        )
        with self.assertRaises(RuntimeError):
            adapter.start_collector_run(
                run_id="offline-failure",
                panel_version="TIERED50_PANEL_V1",
                logical_due_bucket=1,
                mode="offline-test",
                started_at=AT,
            )
        self.assertEqual(connection.rollback_count, 1)
        self.assertTrue(connection.closed)
        self.assertEqual(connection.commit_count, 0)

    def test_temporary_raw_evidence_is_bounded_owner_only_and_secret_safe(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mssql-raw-") as directory:
            adapter = MssqlPersistence(
                MssqlConnectionSettings(host="127.0.0.1", user="u", password="p"),
                connection_factory=lambda: FailingConnection(),
                raw_evidence_directory=directory,
            )
            with patch.dict(os.environ, {"MDD_API_KEY": "key-not-to-write"}, clear=False):
                self.assertEqual(adapter.retain_raw_evidence(body=b"<Trias />", observed_at=AT), "RETAINED")
                self.assertEqual(adapter.retain_raw_evidence(body=b"apikey=key-not-to-write", observed_at=AT), "REFUSED_SECRET_DETECTED")
            files = list(Path(directory).glob("mdd-response-*.bin"))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
            cleanup = adapter.cleanup_expired_raw_evidence(now_utc=AT + timedelta(hours=25))
            self.assertEqual(cleanup["status"], "COMPLETE")
            self.assertEqual(list(Path(directory).glob("mdd-response-*.bin")), [])


if __name__ == "__main__":
    unittest.main()
