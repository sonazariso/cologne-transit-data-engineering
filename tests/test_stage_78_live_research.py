from __future__ import annotations

import json
import os
import sqlite3
import stat
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.cli import main as cli_main
from cologne_transit_collector.collector import (
    Collector,
    LiveModeBlocked,
    LiveResearchModeBlocked,
)
from cologne_transit_collector.configuration import CollectorSettings
from cologne_transit_collector.domain import (
    LIVE_RESEARCH_COLLECTION_CONTEXT,
    LIVE_RESEARCH_TARGET_ID,
    PersistenceProfile,
    HttpResponse,
)
from cologne_transit_collector.manifest import load_default_manifest
from cologne_transit_collector.persistence import (
    LiveResearchSafetyError,
    SQLitePersistence,
    validate_live_research_path,
)
from cologne_transit_collector.quota import QuotaGuard
from cologne_transit_collector.trias import parse_trias_response
from cologne_transit_stage15 import SQLiteEvidenceAdapter, Stage15Analyzer


AT = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
SUCCESS_FIXTURE = ROOT / "tests" / "fixtures" / "stage14_trias_success.json"
SOURCE_FIXTURE = ROOT / "tests" / "fixtures" / "stage14_trias_source_contract_correction.json"


class LiveFakeMddClient:
    offline_safe = False

    def __init__(self, responses: list[HttpResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []

    def fetch(self, target, *, request_timestamp_utc, timeout_seconds, attempt_number):
        self.calls.append(target.source_parent_station_id)
        response = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        return HttpResponse(
            response.status_code,
            response.body,
            response.headers,
            response.elapsed_seconds,
            network_attempted=True,
            http_response_received=True,
            transport_kind="LIVE_TEST_DOUBLE",
            live_client_invocation=True,
        )


class Stage78LiveResearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_default_manifest(ROOT)
        cls.contract = json.loads(
            (ROOT / "config" / "stage15_validation_contract.json").read_text(encoding="utf-8")
        )

    def _live_state(self, *, responses: list[HttpResponse] | None = None):
        directory = tempfile.TemporaryDirectory(prefix="s78-live-")
        self.addCleanup(directory.cleanup)
        data_root = Path(directory.name) / "live-research"
        persistence = SQLitePersistence(
            data_root / "collector.sqlite3",
            profile=PersistenceProfile.LIVE_RESEARCH,
            repository_root=ROOT,
        )
        persistence.materialize_manifest(self.manifest)
        self.addCleanup(persistence.close)
        client = LiveFakeMddClient(
            responses
            or [
                HttpResponse(
                    200,
                    SUCCESS_FIXTURE.read_bytes(),
                    {"Content-Type": "application/json"},
                )
            ]
        )
        collector = Collector(
            manifest=self.manifest,
            persistence=persistence,
            mdd_client=client,
            quota_guard=QuotaGuard(persistence, reservation_scope="LIVE_RESEARCH"),
            safety_margin_seconds=0,
        )
        return persistence, collector, client

    def test_explicit_modes_and_storage_paths_fail_closed(self) -> None:
        persistence, collector, client = self._live_state()
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(LiveResearchModeBlocked):
            collector.run_once(
                mode="live-research",
                allow_live=True,
                pilot_target=LIVE_RESEARCH_TARGET_ID,
                at_utc=AT,
            )
        self.assertEqual(client.calls, [])
        with self.assertRaises(LiveResearchModeBlocked):
            collector.run_once(mode="live-research", pilot_target=LIVE_RESEARCH_TARGET_ID, at_utc=AT)
        with self.assertRaises(LiveModeBlocked):
            collector.run_once(mode="live", at_utc=AT)
        with self.assertRaises(LiveResearchSafetyError):
            SQLitePersistence(ROOT / "unsafe.sqlite3", profile=PersistenceProfile.LIVE_RESEARCH, repository_root=ROOT)
        with self.assertRaises(LiveResearchSafetyError):
            validate_live_research_path(Path(tempfile.gettempdir()) / "tests" / "collector.sqlite3")
        self.assertEqual(persistence.database_role, PersistenceProfile.LIVE_RESEARCH.value)

    def test_cli_requires_explicit_opt_in_and_live_root_without_network(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(cli_main(["live-research", "--live-root", tempfile.gettempdir()]), 2)
        self.assertEqual(cli_main(["live"]), 2)

    def test_live_plan_selects_only_heumarkt(self) -> None:
        _persistence, collector, client = self._live_state()
        plan = collector.live_research_plan(at_utc=AT)
        self.assertEqual(plan.due_count, 1)
        self.assertEqual(plan.due_targets[0].source_parent_station_id, LIVE_RESEARCH_TARGET_ID)
        self.assertEqual(plan.due_targets[0].station_name, "Köln Heumarkt")
        self.assertEqual(plan.due_targets[0].tier, "A")
        self.assertEqual(plan.due_targets[0].cadence_minutes, 5)
        self.assertEqual(plan.due_targets[0].number_of_results, 20)
        self.assertEqual(client.calls, [])

    def test_successful_heartbeat_is_durable_and_not_production_cutover(self) -> None:
        persistence, collector, client = self._live_state()
        with patch.dict(os.environ, {"MDD_API_KEY": "s78-safe-key"}, clear=True):
            result = collector.run_once(
                mode="live-research",
                allow_live=True,
                pilot_target=LIVE_RESEARCH_TARGET_ID,
                run_id="s78-success",
                at_utc=AT,
            )
        self.assertEqual(result.status, "COMPLETE")
        self.assertEqual(result.collection_context, LIVE_RESEARCH_COLLECTION_CONTEXT)
        self.assertEqual(client.calls, [LIVE_RESEARCH_TARGET_ID])
        self.assertEqual(len(persistence.fetch_target_audits()), 1)
        self.assertEqual(persistence.fetch_target_audits()[0]["collection_context"], LIVE_RESEARCH_COLLECTION_CONTEXT)
        self.assertEqual(len(persistence.fetch_snapshots()), 1)
        self.assertEqual(persistence.fetch_snapshots()[0]["collection_context"], LIVE_RESEARCH_COLLECTION_CONTEXT)
        self.assertEqual(persistence.fetch_raw_evidence(), [])
        self.assertEqual(result.maintenance["verification"]["status"], "COMPLETE")
        usage = persistence.current_month_usage(at_utc=AT)
        self.assertFalse(usage["state_available"])
        self.assertIsNone(usage["known_total"])
        connection = sqlite3.connect(persistence.path)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sampling_eras WHERE is_active = 1").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM target_audits").fetchone()[0], 1)
        finally:
            connection.close()

        report = Stage15Analyzer(
            self.manifest,
            contract=self.contract,
            evidence_adapter=SQLiteEvidenceAdapter(persistence.path),
            window_start_utc=AT,
            window_end_utc=AT + timedelta(minutes=5),
        ).analyze()
        self.assertEqual(report["stage15_live_status"], "NOT STARTED — AWAITS TIERED50 CUTOVER")
        self.assertEqual(report["evidence"]["source"], "sqlite-live-research")
        self.assertTrue(report["evidence"]["live_research_is_not_production_evidence"])
        self.assertTrue(
            any(item["validation_id"] == "continuity.live_research_context_is_not_cutover" for item in report["checks"])
        )

    def test_unknown_shared_quota_allows_initial_but_denies_retry(self) -> None:
        response_500 = HttpResponse(
            500,
            b'{"provider_error":"temporary"}',
            {"Content-Type": "application/json"},
        )
        persistence, collector, client = self._live_state(responses=[response_500, response_500])
        with patch.dict(os.environ, {"MDD_API_KEY": "s78-safe-key"}, clear=True):
            result = collector.run_once(
                mode="live-research",
                allow_live=True,
                pilot_target=LIVE_RESEARCH_TARGET_ID,
                run_id="s78-quota-unknown",
                at_utc=AT,
            )
        self.assertEqual(client.calls, [LIVE_RESEARCH_TARGET_ID])
        self.assertEqual(result.outcomes[0].http_attempts, 1)
        self.assertEqual(result.outcomes[0].retry_outcome, "RETRY_SKIPPED_QUOTA")
        usage = persistence.current_month_usage(at_utc=AT)
        self.assertFalse(usage["state_available"])
        self.assertIsNone(usage["known_total"])
        connection = sqlite3.connect(persistence.path)
        try:
            decision_rows = connection.execute(
                "SELECT decision_json FROM quota_audit WHERE decision_kind = 'RETRY'"
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(len(decision_rows), 1)
        self.assertIn("RETRY_DENIED_QUOTA_STATE_UNAVAILABLE", decision_rows[0][0])

    def test_parsed_source_evidence_recorded_time_and_raw_policy(self) -> None:
        persistence, _collector, _client = self._live_state()
        target = self.manifest.live_research_target()
        run_id = "s78-evidence"
        persistence.start_collector_run(
            run_id=run_id,
            panel_version=target.panel_version,
            logical_due_bucket=123,
            mode="live-research",
            started_at=AT,
            collection_context=LIVE_RESEARCH_COLLECTION_CONTEXT,
        )
        persistence.claim_dispatcher_bucket(
            run_id=run_id,
            panel_version=target.panel_version,
            logical_due_bucket=123,
            claimed_at=AT,
        )
        persistence.claim_target(run_id=run_id, target=target, logical_due_bucket=123, claimed_at=AT)
        audit_id = persistence.start_target_audit(
            run_id=run_id,
            target=target,
            logical_due_bucket=123,
            started_at=AT,
            collection_context=LIVE_RESEARCH_COLLECTION_CONTEXT,
        )
        parsed = parse_trias_response(SOURCE_FIXTURE.read_bytes(), "application/json").to_dict()
        occurrence = {
            "result_id": "recorded-result",
            "call_scope": "THIS",
            "call_ordinal": 1,
            "timing_context": "ARRIVAL",
            "recorded_at_time": "2026-10-01T12:01:02Z[GMT]",
            "source_path": "$.serviceDelivery.deliveryPayload.stopEventResponse.stopEventResult[0]",
        }
        parsed["events"][0]["recorded_at_time_occurrences"] = [occurrence]
        parsed["recorded_at_time_occurrences"] = [occurrence]
        persistence.persist_snapshot(
            audit_id=audit_id,
            target=target,
            logical_due_bucket=123,
            parsed_snapshot=parsed,
            persisted_at=AT,
            collection_context=LIVE_RESEARCH_COLLECTION_CONTEXT,
        )
        evidence = persistence.fetch_recorded_time_evidence()
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["recorded_at_time"], occurrence["recorded_at_time"])
        self.assertEqual(evidence[0]["collection_context"], LIVE_RESEARCH_COLLECTION_CONTEXT)
        snapshot = json.loads(persistence.fetch_snapshots()[0]["snapshot_json"])
        self.assertEqual(len(snapshot["parser_gaps"]), 4)
        self.assertEqual(len(snapshot["events"][0]["service_attribute_evidence"]), 2)

        with patch.dict(os.environ, {"MDD_API_KEY": "s78-safe-key"}, clear=True):
            retained = persistence.retain_raw_evidence(
                body=SOURCE_FIXTURE.read_bytes(),
                reason="PARSER_GAP",
                observed_at=AT,
                target=target,
                logical_due_bucket=123,
            )
        self.assertEqual(retained, "RETAINED")
        raw_row = persistence.fetch_raw_evidence()[0]
        self.assertLessEqual(
            datetime.fromisoformat(raw_row["delete_after_utc"].replace("Z", "+00:00"))
            - datetime.fromisoformat(raw_row["created_at_utc"].replace("Z", "+00:00")),
            timedelta(hours=24),
        )
        raw_path = Path(raw_row["file_path"])
        self.assertTrue(raw_path.exists())
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(raw_path.stat().st_mode), 0o600)
        cleanup = persistence.cleanup_expired_raw_evidence(now_utc=AT + timedelta(hours=24, seconds=1))
        self.assertEqual(cleanup["status"], "COMPLETE")
        self.assertFalse(raw_path.exists())
        self.assertEqual(persistence.fetch_raw_evidence()[0]["status"], "DELETED")

    def test_secret_detection_refuses_raw_write_and_backup_rotation_is_online(self) -> None:
        persistence, _collector, _client = self._live_state()
        with patch.dict(os.environ, {"MDD_API_KEY": "s78-secret"}, clear=True):
            self.assertEqual(
                persistence.retain_raw_evidence(body=b'{"token":"s78-secret"}', reason="ANOMALY"),
                "REFUSED_SECRET_DETECTED",
            )
        refused = persistence.fetch_raw_evidence()[0]
        self.assertEqual(refused["status"], "REFUSED_SECRET_DETECTED")
        self.assertIsNone(refused["file_path"])
        self.assertEqual(list(persistence.raw_evidence_dir.glob("*")), [])

        stale_sidecar = persistence.backup_dir / ".collector-2026-10-01-0123456789abcdef0123456789abcdef.tmp-wal"
        stale_sidecar.write_bytes(b"stale sidecar")
        for offset in range(4):
            summary = persistence.backup_live_database(now_utc=AT + timedelta(days=offset))
            self.assertEqual(summary["status"], "COMPLETE")
            self.assertEqual(list(persistence.backup_dir.glob(".*.tmp*")), [])
        backups = sorted(path.name for path in persistence.backup_dir.glob("collector-*.sqlite3"))
        self.assertEqual(
            backups,
            ["collector-2026-10-02.sqlite3", "collector-2026-10-03.sqlite3", "collector-2026-10-04.sqlite3"],
        )
        connection = sqlite3.connect(persistence.backup_dir / "collector-2026-10-04.sqlite3")
        try:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            connection.close()
        with patch("cologne_transit_collector.persistence.os.replace", side_effect=OSError("publish failure")):
            failed = persistence.backup_live_database(now_utc=AT + timedelta(days=4))
        self.assertEqual(failed["status"], "FAILED")
        self.assertEqual(list(persistence.backup_dir.glob(".*.tmp*")), [])
        self.assertEqual(persistence.integrity_check(quick=False), "ok")

    def test_live_settings_require_explicit_external_location(self) -> None:
        with self.assertRaises(ValueError):
            CollectorSettings.for_live_research(ROOT)
        with tempfile.TemporaryDirectory(prefix="s78-settings-") as directory:
            settings = CollectorSettings.for_live_research(ROOT, data_root=directory)
            self.assertEqual(settings.persistence_profile, PersistenceProfile.LIVE_RESEARCH)
            self.assertEqual(settings.state_path, (Path(directory) / "collector.sqlite3").resolve())
            self.assertEqual(settings.collection_context, LIVE_RESEARCH_COLLECTION_CONTEXT)


if __name__ == "__main__":
    unittest.main()
