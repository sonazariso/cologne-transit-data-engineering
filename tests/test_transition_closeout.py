import ast
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import record_legacy7_methodology_evidence as closeout_script  # noqa: E402
from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlConnectionSettings,
    MssqlPersistence,
    MssqlSafetyError,
)


AT = datetime(2026, 10, 7, 19, 51, 46, 41000, tzinfo=timezone.utc)
PANEL_VERSION = closeout_script.LEGACY7_PANEL_VERSION
ERA_CODE = closeout_script.OVERSAMPLING_ERA
METHOD_VERSION = closeout_script.METHODOLOGY_VERSION
RUN_IDS = tuple(closeout_script.RUN_IDS)
CORRECTED_ERA = "MAC_LEGACY7_PRODUCTION"
CORRECTED_PANEL = PANEL_VERSION


def _methodology_row(
    evidence_id: int,
    *,
    run_ids: tuple[str, ...] = RUN_IDS,
    era_code: str = ERA_CODE,
    panel_version: str = PANEL_VERSION,
    collection_context: str = "MAC_PYTHON_MSSQL_PREPARED",
    methodology_version: str = METHOD_VERSION,
    actual: int = 21,
    historical: int = 3,
    excess: int = 18,
) -> dict[str, object]:
    return {
        "MethodologyEvidenceId": evidence_id,
        "EraCode": era_code,
        "PanelVersion": panel_version,
        "CollectionContext": collection_context,
        "MethodologyVersion": methodology_version,
        "ActualInitialAttempts": actual,
        "HistoricalEquivalentInitialAttempts": historical,
        "ExcessInitialAttempts": excess,
        "RunIdsJson": json.dumps(list(run_ids)),
    }


def _era_row(
    era_id: int,
    *,
    era_code: str = CORRECTED_ERA,
    panel_version: str = CORRECTED_PANEL,
    effective_at: datetime = AT,
    timestamp_kind: str = "FIRST_CORRECTED_SCHEDULED_HEARTBEAT",
    is_active: bool = True,
    is_synthetic: bool = False,
) -> dict[str, object]:
    return {
        "SamplingEraId": era_id,
        "EraCode": era_code,
        "PanelVersion": panel_version,
        "EffectiveAtUtc": effective_at,
        "TimestampKind": timestamp_kind,
        "IsActive": is_active,
        "IsSynthetic": is_synthetic,
    }


class _EvidenceBackend:
    def __init__(self, *, methodology=None, eras=None) -> None:
        self.methodology = list(methodology or [])
        self.eras = list(eras or [])
        self.methodology_inserts = 0
        self.era_inserts = 0
        self.update_or_delete_statements = 0
        self.commit_count = 0
        self.rollback_count = 0


class _EvidenceCursor:
    def __init__(self, backend: _EvidenceBackend) -> None:
        self.backend = backend
        self.result = []

    def execute(self, statement, params=()) -> None:
        normalized = " ".join(str(statement).split()).upper()
        if normalized.startswith("UPDATE ") or normalized.startswith("DELETE "):
            self.backend.update_or_delete_statements += 1
        if "FROM CTL.MDDMACMETHODOLOGYEVIDENCE" in normalized:
            self.result = list(self.backend.methodology)
            return
        if "INSERT INTO CTL.MDDMACMETHODOLOGYEVIDENCE" in normalized:
            values = tuple(params)
            evidence_id = max((int(row["MethodologyEvidenceId"]) for row in self.backend.methodology), default=0) + 1
            self.backend.methodology.append(
                _methodology_row(
                    evidence_id,
                    run_ids=tuple(json.loads(values[9])),
                    era_code=str(values[0]),
                    panel_version=str(values[1]),
                    collection_context=str(values[2]),
                    methodology_version=str(values[5]),
                    actual=values[6],
                    historical=values[7],
                    excess=values[8],
                )
            )
            self.backend.methodology_inserts += 1
            self.result = [{"MethodologyEvidenceId": evidence_id}]
            return
        if "FROM CTL.MDDMACSAMPLINGERA" in normalized:
            self.result = list(self.backend.eras)
            return
        if "INSERT INTO CTL.MDDMACSAMPLINGERA" in normalized:
            values = tuple(params)
            era_id = max((int(row["SamplingEraId"]) for row in self.backend.eras), default=0) + 1
            self.backend.eras.append(
                _era_row(
                    era_id,
                    era_code=str(values[0]),
                    panel_version=str(values[1]),
                    effective_at=values[2],
                    timestamp_kind=str(values[3]),
                    is_active=bool(values[4]),
                    is_synthetic=bool(values[5]),
                )
            )
            self.backend.era_inserts += 1
            self.result = [{"SamplingEraId": era_id}]
            return
        self.result = []

    def fetchall(self):
        return list(self.result)

    def fetchone(self):
        return self.result[0] if self.result else None


class _EvidenceConnection:
    def __init__(self, backend: _EvidenceBackend) -> None:
        self.backend = backend

    def cursor(self):
        return _EvidenceCursor(self.backend)

    def commit(self) -> None:
        self.backend.commit_count += 1

    def rollback(self) -> None:
        self.backend.rollback_count += 1

    def close(self) -> None:
        pass


def _adapter(backend: _EvidenceBackend) -> MssqlPersistence:
    return MssqlPersistence(
        settings=MssqlConnectionSettings(host="fake", user="operator", password="not-used"),
        connection_factory=lambda: _EvidenceConnection(backend),
        raw_evidence_directory=None,
    )


def _methodology_kwargs() -> dict[str, object]:
    return {
        "era_code": ERA_CODE,
        "panel_version": PANEL_VERSION,
        "collection_context": "MAC_PYTHON_MSSQL_PREPARED",
        "methodology_version": METHOD_VERSION,
        "run_ids": RUN_IDS,
        "evidence": {"classification": "BOUNDED_CUTOVER_VALIDATION_OVERSAMPLING"},
        "window_start_utc": datetime(2026, 10, 7, 16, 25, tzinfo=timezone.utc),
        "window_end_utc": datetime(2026, 10, 7, 16, 37, tzinfo=timezone.utc),
        "actual_initial_attempts": 21,
        "historical_equivalent_initial_attempts": 3,
        "excess_initial_attempts": 18,
    }


def _probe() -> dict[str, object]:
    return {
        "affected_runs": [
            {
                "RunId": run_id,
                "StartedAtUtc": datetime(2026, 10, 7, 16, 25 + index * 5, tzinfo=timezone.utc),
                "CompletedAtUtc": datetime(2026, 10, 7, 16, 26 + index * 5, tzinfo=timezone.utc),
                "target_audit_count": 7,
                "initial_attempt_count": 7,
                "snapshot_count": 7,
            }
            for index, run_id in enumerate(RUN_IDS)
        ],
        "provider_impact": {
            "actual_initial_attempts": 21,
            "historical_legacy7_equivalent_initial_attempts": 3,
            "excess_initial_attempts": 18,
        },
        "selected_target": {"sampling_slot": 3, "sampling_target_id": 1},
        "slot_rows": [],
        "selector": {},
        "selector_reconciliation": {},
    }


class TransitionCloseoutTests(unittest.TestCase):
    def test_oversampling_recording_reuses_one_matching_row_without_insert(self) -> None:
        backend = _EvidenceBackend()
        adapter = _adapter(backend)
        login_status = {
            "login_name": "CologneTransitCollector",
            "is_sysadmin": False,
            "is_serveradmin": False,
            "is_securityadmin": False,
            "is_db_owner": False,
            "is_db_datareader": False,
            "is_db_datawriter": False,
        }
        with (
            patch.object(closeout_script, "run_probe", return_value=_probe()),
            patch.object(closeout_script, "MssqlPersistence", return_value=adapter),
            patch.object(closeout_script, "_review_login_status", return_value=login_status),
        ):
            first = closeout_script.record_oversampling(at_utc=AT, allow_administrator=False)
            second = closeout_script.record_oversampling(at_utc=AT, allow_administrator=False)
        self.assertEqual(first["recording_action"], "INSERTED")
        self.assertEqual(second["recording_action"], "REUSED")
        self.assertEqual(first["methodology_evidence_id"], second["methodology_evidence_id"])
        self.assertEqual(backend.methodology_inserts, 1)
        self.assertEqual(second["rows_written"], 0)

    def test_conflicting_oversampling_evidence_fails_closed(self) -> None:
        backend = _EvidenceBackend(methodology=[_methodology_row(1, actual=20, historical=3, excess=17)])
        with self.assertRaisesRegex(MssqlSafetyError, "conflicting methodology evidence"):
            _adapter(backend).record_methodology_evidence_idempotent(**_methodology_kwargs())
        self.assertEqual(backend.methodology_inserts, 0)

    def test_multiple_matching_methodology_rows_fail_closed(self) -> None:
        backend = _EvidenceBackend(methodology=[_methodology_row(1), _methodology_row(2)])
        with self.assertRaisesRegex(MssqlSafetyError, "duplicate historical methodology evidence"):
            _adapter(backend).record_methodology_evidence_idempotent(**_methodology_kwargs())
        self.assertEqual(backend.methodology_inserts, 0)

    def test_corrected_era_recording_reuses_existing_without_insert(self) -> None:
        backend = _EvidenceBackend()
        adapter = _adapter(backend)
        kwargs = {
            "era_code": CORRECTED_ERA,
            "panel_version": CORRECTED_PANEL,
            "effective_at_utc": AT,
            "timestamp_kind": "FIRST_CORRECTED_SCHEDULED_HEARTBEAT",
            "is_active": True,
            "is_synthetic": False,
        }
        first = adapter.record_sampling_era_cutover_idempotent(**kwargs)
        second = adapter.record_sampling_era_cutover_idempotent(**kwargs)
        self.assertEqual(first["action"], "INSERTED")
        self.assertEqual(second["action"], "REUSED")
        self.assertEqual(first["sampling_era_id"], second["sampling_era_id"])
        self.assertEqual(backend.era_inserts, 1)
        self.assertEqual(second["rows_written"], 0)

    def test_conflicting_corrected_era_evidence_fails_closed(self) -> None:
        backend = _EvidenceBackend(
            eras=[
                _era_row(
                    1,
                    effective_at=datetime(2026, 10, 7, 19, 56, 54, tzinfo=timezone.utc),
                )
            ]
        )
        with self.assertRaisesRegex(MssqlSafetyError, "conflicting corrected-production sampling era"):
            _adapter(backend).record_sampling_era_cutover_idempotent(
                era_code=CORRECTED_ERA,
                panel_version=CORRECTED_PANEL,
                effective_at_utc=AT,
                timestamp_kind="FIRST_CORRECTED_SCHEDULED_HEARTBEAT",
                is_active=True,
                is_synthetic=False,
            )
        self.assertEqual(backend.era_inserts, 0)

    def test_tiered50_active_recording_and_historical_rows_are_untouched(self) -> None:
        historical_methodology = _methodology_row(
            7,
            era_code="HISTORICAL_LEGACY7",
            panel_version="HISTORICAL_PANEL",
            collection_context="HISTORICAL",
            methodology_version="HISTORICAL_V1",
            run_ids=("historical-run",),
            actual=1,
            historical=1,
            excess=0,
        )
        historical_era = _era_row(
            4,
            era_code="HISTORICAL_LEGACY7",
            panel_version="HISTORICAL_PANEL",
            effective_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            timestamp_kind="HISTORICAL",
            is_active=False,
        )
        backend = _EvidenceBackend(methodology=[historical_methodology], eras=[historical_era])
        original_methodology = json.loads(json.dumps(backend.methodology, default=str))
        original_eras = json.loads(json.dumps(backend.eras, default=str))
        result = _adapter(backend).record_sampling_era_cutover(
            era_code="Tiered50",
            panel_version="TIERED50_PANEL_V1",
            effective_at_utc=AT,
            timestamp_kind="REAL_CUTOVER",
            is_active=True,
            is_synthetic=False,
        )
        self.assertEqual(result, 5)
        self.assertEqual(backend.era_inserts, 1)
        _adapter(backend).record_methodology_evidence_idempotent(**_methodology_kwargs())
        self.assertEqual(json.loads(json.dumps(backend.methodology, default=str))[:1], original_methodology)
        self.assertEqual(json.loads(json.dumps(backend.eras, default=str))[:1], original_eras)
        self.assertEqual(backend.update_or_delete_statements, 1)

    def test_closeout_evidence_contains_no_secret_values(self) -> None:
        path = ROOT / "docs" / "operational" / "legacy7-production-transition-closeout-evidence.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        serialized = path.read_text(encoding="utf-8")
        for marker in ("MDD_API_KEY=", "MSSQL_PASSWORD=", "SQL_SERVER_PASSWORD=", "password_value", "api_key_value"):
            self.assertNotIn(marker, serialized)
        self.assertFalse(document["sanitization"]["credentials_included"])
        self.assertFalse(document["sanitization"]["secret_values_included"])
        self.assertEqual(document["runtime_configuration"]["secret_keys_present"], [])

    def test_current_docs_and_suite_count_are_fresh(self) -> None:
        project_status = (ROOT / "docs" / "PROJECT-STATUS.md").read_text(encoding="utf-8")
        stage14 = (ROOT / "docs" / "stages" / "14-collector-implementation.md").read_text(encoding="utf-8")
        self.assertNotIn("Mac scheduler activation is paused until the corrected Legacy7 live gates pass.", project_status)
        discovered = 0
        for path in (ROOT / "tests").glob("test_*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            discovered += sum(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test")
                for node in ast.walk(tree)
            )
        self.assertIn(f"current repository suite reports {discovered} tests per run", project_status)
        self.assertIn(f"current repository suite reports {discovered} tests per run", stage14)
        self.assertIn("TIERED50 ACTIVATION OUTAGE — NO ACTIVE STATIONS; LEGACY7 RETIRED", project_status)
        self.assertIn("STAGE 14 COLLECTOR IMPLEMENTATION COMPLETE", project_status)
        self.assertIn("NOT STARTED — FIRST SCHEDULED TIERED50 HEARTBEAT FAILED", project_status)


if __name__ == "__main__":
    unittest.main()
