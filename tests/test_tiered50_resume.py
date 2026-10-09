from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import activate_tiered50_production as activation  # noqa: E402
import resume_tiered50_production as resume_cli  # noqa: E402
from cologne_transit_collector.admin_evidence import (  # noqa: E402
    AdminEvidenceError,
    EVIDENCE_ARTIFACT_VERSION,
    REQUIRED_CHECK_IDS,
    payload_sha256,
    sign_payload,
    validate_admin_evidence_artifact,
)
from tiered50_production_evidence import derive_current_status  # noqa: E402


AT = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
ORIGINAL = datetime(2026, 10, 8, 9, 0, tzinfo=timezone.utc)


def _lifecycle_rows():
    targets = [
        {
            "SamplingTargetId": target_id,
            "IsEnabled": True,
            "ActivationStatus": "ACTIVE",
            "PanelVersion": "TIERED50_PANEL_V1",
            "SamplingEra": "Tiered50",
            "ActivatedAtUtc": ORIGINAL,
        }
        for target_id in range(8, 51)
    ]
    era = [{
        "SamplingEraId": 77,
        "PanelVersion": "TIERED50_PANEL_V1",
        "EffectiveAtUtc": ORIGINAL,
        "TimestampKind": "REAL_TIERED50_CUTOVER",
        "IsActive": True,
        "IsSynthetic": False,
    }]
    current = [{
        "CurrentMarkerId": 1,
        "SamplingEraId": 77,
        "EraCode": "TIERED50_PRODUCTION",
        "PanelVersion": "TIERED50_PANEL_V1",
        "EffectiveAtUtc": ORIGINAL,
    }]
    activated = [{
        "CutoverKey": "activation",
        "Tiered50ActivationStatus": "ACTIVATED",
        "Tiered50ActivationAtUtc": ORIGINAL,
    }]
    outage = [{
        "CutoverKey": "outage",
        "Tiered50ActivationStatus": "OUTAGE",
        "Tiered50ActivationAtUtc": ORIGINAL,
    }]
    return targets, era, current, activated, outage


class Tiered50ResumeTests(unittest.TestCase):
    def test_existing_earlier_immutable_sampling_era_is_valid_resume_state(self) -> None:
        rows = _lifecycle_rows()
        result = activation.validate_existing_tiered50_activation(
            target_rows=rows[0],
            era_rows=rows[1],
            current_era_rows=rows[2],
            activation_rows=rows[3],
            outage_rows=rows[4],
        )
        self.assertEqual(result["sampling_era_id"], 77)
        self.assertEqual(result["effective_at_utc"], ORIGINAL)
        self.assertTrue(result["historical_state_preserved"])

    def test_invalid_target_state_blocks_without_new_era(self) -> None:
        rows = _lifecycle_rows()
        rows[0][0]["SamplingTargetId"] = 51
        with self.assertRaisesRegex(RuntimeError, "immutable Tiered50 target IDs"):
            activation.validate_existing_tiered50_activation(
                target_rows=rows[0],
                era_rows=rows[1],
                current_era_rows=rows[2],
                activation_rows=rows[3],
                outage_rows=rows[4],
            )

    def test_missing_historical_outage_blocks_resume(self) -> None:
        rows = _lifecycle_rows()
        with self.assertRaisesRegex(RuntimeError, "outage record"):
            activation.validate_existing_tiered50_activation(
                target_rows=rows[0],
                era_rows=rows[1],
                current_era_rows=rows[2],
                activation_rows=rows[3],
                outage_rows=[],
            )

    def test_status_ignores_old_outage_after_valid_resume_and_active_scheduler(self) -> None:
        status = derive_current_status(
            scheduler={"returncode": 0},
            plist={"exists": True, "disabled": False, "contains_secret_marker": False},
            materialization={"target_count": 43, "active_count": 43},
            latest_cutover={"status": "OUTAGE"},
            latest_resume={"resume_event_id": 1},
            latest_tiered_run={"run_status": "COMPLETE", "successful_target_audits": 18},
            six_cycle={"status": "NOT_PROVEN"},
        )
        self.assertEqual(status["status"], "ACTIVE — VALIDATION INCOMPLETE")
        self.assertTrue(status["historical_outage_preserved"])
        self.assertTrue(status["tiered50_active"])

    def test_partial_launchagent_start_is_stopped(self) -> None:
        with patch.object(activation, "install_launch_agent") as install, patch.object(
            activation, "bootstrap_launch_agent"
        ) as bootstrap:
            activation._disable_tiered50_after_resume_failure(runtime_config_path="/tmp/runtime.json")
        install.assert_called_once()
        self.assertEqual(install.call_args.kwargs["activate"], False)
        bootstrap.assert_called_once_with(activate=False, mode=activation.TIERED50_MODE)

    def test_competing_worker_detection_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "competing production collector"):
            activation.validate_no_competing_production_writers(
                [{"CollectionContext": "MAC_TIERED50_PRODUCTION", "RunningCount": 1}]
            )

    def test_resume_after_failed_heartbeat_never_replays_or_retires_legacy7(self) -> None:
        targets, era, current, activated, outage = _lifecycle_rows()
        for row in targets:
            row["IsEnabled"] = True
            row["ActivationStatus"] = "ACTIVE"
        cutovers = activated + outage

        class Cursor:
            def __init__(self) -> None:
                self.rows: object = []
                self.one: object = None
                self.statements: list[str] = []

            def execute(self, statement: str, params=()) -> None:
                del params
                self.statements.append(statement)
                if "AuthoritativeUtc" in statement:
                    self.one = {"AuthoritativeUtc": AT}
                    self.rows = []
                elif "APPLOCK_MODE" in statement:
                    self.one = {"ResumeLockMode": "Exclusive"}
                    self.rows = []
                elif "MddMacTiered50ProductionTarget" in statement:
                    self.one = None
                    self.rows = targets
                elif "MddMacSamplingEra" in statement:
                    self.rows = era
                elif "MddMacCurrentProductionEra" in statement:
                    self.rows = current
                elif "MddMacProductionCutover" in statement:
                    self.rows = cutovers
                elif "MddMacCollectorRun" in statement:
                    self.rows = []
                elif "MddMacProductionResumeEvent" in statement:
                    self.rows = []
                else:
                    self.one = None
                    self.rows = []

            def fetchall(self):
                return list(self.rows)  # type: ignore[arg-type]

            def fetchone(self):
                result = self.one
                self.one = None
                return result

        class Connection:
            def __init__(self) -> None:
                self.cursor_value = Cursor()
                self.commits = 0

            def cursor(self):
                return self.cursor_value

            def commit(self) -> None:
                self.commits += 1

            def rollback(self) -> None:
                pass

            def close(self) -> None:
                pass

        connection = Connection()
        settings = activation.MssqlConnectionSettings(host="admin", user="admin", password="not-used")
        with patch.object(activation, "_administrator_connection_settings", return_value=settings), patch.object(
            activation,
            "_validate_limited_runtime",
            return_value={
                "config_path": "/tmp/runtime.json",
                "settings": {"host": "runtime"},
                "login": {"login_name": "CologneTransitCollector"},
                "quota": {"state_available": True, "reserved_shared_usage": 0, "known_total": 100},
            },
        ), patch.object(
            activation,
            "_installed_tiered50_resume_readiness",
            return_value={"loaded_before_resume": False, "disabled_before_resume": True, "secret_free": True},
        ), patch.object(activation, "_validate_operational_evidence", return_value={}), patch.object(
            activation, "connect_mssql", return_value=connection
        ), patch.object(
            activation,
            "install_launch_agent",
            return_value={"label": activation.TIERED50_LABEL, "installed_plist_path": "/tmp/tiered.plist"},
        ), patch.object(activation, "bootstrap_launch_agent"), patch.object(
            activation, "_launch_agent_loaded", return_value=True
        ), patch.object(
            activation,
            "_record_resume_event",
            return_value={"status": "RECORDED", "resume_event_id": 1, "resume_at_utc": AT},
        ):
            result = activation.resume_tiered50_production(resume_key="TIERED50-RESUME-FAILED-HEARTBEAT")

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["historical_requests_replayed"], 0)
        self.assertEqual(result["legacy7_status"], "RETIRED_NOT_MUTATED")
        self.assertEqual(result["target_rows_mutated"], 0)
        self.assertEqual(result["sampling_eras_created"], 0)

    def test_repeated_resume_event_is_idempotent(self) -> None:
        class Cursor:
            def __init__(self) -> None:
                self.statements: list[str] = []

            def execute(self, statement: str, params=()) -> None:
                del params
                self.statements.append(statement)

            def fetchone(self):
                return {
                    "ResumeEventId": 9,
                    "PanelVersion": "TIERED50_PANEL_V1",
                    "EraCode": activation.ERA_CODE,
                    "SamplingEraId": 77,
                    "OriginalActivationAtUtc": ORIGINAL,
                    "ResumeAtUtc": AT,
                    "LaunchAgentLabel": activation.TIERED50_LABEL,
                }

        class Connection:
            def __init__(self) -> None:
                self.cursor_value = Cursor()

            def cursor(self):
                return self.cursor_value

            def commit(self) -> None:
                pass

            def rollback(self) -> None:
                pass

            def close(self) -> None:
                pass

        connection = Connection()
        lifecycle = {"sampling_era_id": 77, "effective_at_utc": ORIGINAL}
        with patch.object(activation, "connect_mssql", return_value=connection):
            result = activation._record_resume_event(
                settings=activation.MssqlConnectionSettings(host="admin", user="admin", password="not-used"),
                resume_key="TIERED50-RESUME-IDEMPOTENT",
                lifecycle=lifecycle,
                evidence={"historical_requests_replayed": 0},
            )
        self.assertEqual(result["status"], "IDEMPOTENT_ALREADY_RECORDED")
        self.assertFalse(any("INSERT INTO" in statement for statement in connection.cursor_value.statements))

    def test_admin_connection_never_falls_back_to_runtime_account(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MSSQL_HOST": "runtime-host",
                "MSSQL_USER": "CologneTransitCollector",
                "MSSQL_PASSWORD": "runtime-secret",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "MISSING_ADMIN_CREDENTIALS_OR_PERMISSIONS"):
                activation._administrator_connection_settings()

    def test_resume_cli_rejects_effective_at(self) -> None:
        with patch.object(activation, "resume_tiered50_production") as resume:
            self.assertEqual(
                activation.main(["--apply", "--resume", "--effective-at", "2026-10-08T12:00:00Z"]),
                2,
            )
        resume.assert_not_called()

    def test_resume_cli_does_not_treat_stopped_idempotent_event_as_success(self) -> None:
        stopped = {
            "status": "IDEMPOTENT_ALREADY_RECORDED",
            "result_code": activation.RESUME_STOPPED_EXISTING_EVENT_STATUS,
            "service_state": "STOPPED",
            "operational_success": False,
        }
        self.assertEqual(resume_cli._resume_exit_code(stopped), 2)
        self.assertEqual(activation._resume_exit_code(stopped), 2)

    def test_loaded_idempotent_event_is_loaded_but_not_validated(self) -> None:
        loaded = {
            "status": "IDEMPOTENT_ALREADY_ACTIVE",
            "result_code": activation.RESUME_LOADED_EXISTING_EVENT_STATUS,
            "service_state": "LOADED_NOT_VALIDATED",
            "production_validation": "NOT_PROVEN",
        }
        self.assertEqual(resume_cli._resume_exit_code(loaded), 0)
        self.assertEqual(activation._resume_exit_code(loaded), 0)


class AdminEvidenceEnvelopeTests(unittest.TestCase):
    def _evidence(self) -> dict[str, object]:
        return {
            "artifact_version": EVIDENCE_ARTIFACT_VERSION,
            "generated_at_utc": AT.isoformat().replace("+00:00", "Z"),
            "capture_provenance": {
                "status": "PASS",
                "read_only": True,
                "execution_path": "SEPARATE_SQL_LOGIN",
                "server_name": "DataAnalyst-VM",
                "database_name": "CologneTransitIntelligence",
                "effective_login": "separate-admin",
                "captured_at_utc": AT.isoformat().replace("+00:00", "Z"),
                "required_check_ids": sorted(REQUIRED_CHECK_IDS),
            },
            "sql": {
                "admin_authorization": {"status": "PASS"},
                "runtime_health": {"status": "PASS"},
                "backup_gate": {"status": "PASS"},
                "capacity_gate": {"status": "PASS"},
                "log_space_gate": {"status": "PASS"},
                "quota_integrity": {"status": "PASS", "state_available": True},
            },
        }

    def test_signed_artifact_is_bound_to_payload_and_sql_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            private = directory_path / "private.pem"
            public = directory_path / "public.pem"
            os.system(f"/usr/bin/openssl genrsa -out {private} 2048 >/dev/null 2>&1")
            private.chmod(0o600)
            os.system(f"/usr/bin/openssl rsa -in {private} -pubout -out {public} >/dev/null 2>&1")
            evidence = self._evidence()
            evidence["artifact_integrity"] = sign_payload(evidence, signing_key_path=private)
            result = validate_admin_evidence_artifact(
                evidence,
                now_utc=AT,
                expected_database="CologneTransitIntelligence",
                public_key_path=public,
            )
            self.assertTrue(result["signature_verified"])
            self.assertEqual(evidence["artifact_integrity"]["payload_sha256"], payload_sha256(evidence))

    def test_structured_sql_identity_matches_runtime_and_rejects_same_database_elsewhere(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            private = directory_path / "private.pem"
            public = directory_path / "public.pem"
            os.system(f"/usr/bin/openssl genrsa -out {private} 2048 >/dev/null 2>&1")
            private.chmod(0o600)
            os.system(f"/usr/bin/openssl rsa -in {private} -pubout -out {public} >/dev/null 2>&1")
            evidence = self._evidence()
            identity = {
                "server_name": r"sql01\COLLECTOR",
                "machine_name": "sql01",
                "instance_name": "COLLECTOR",
                "database_name": "CologneTransitIntelligence",
            }
            evidence["capture_provenance"]["server_name"] = identity["server_name"]  # type: ignore[index]
            evidence["capture_provenance"]["sql_instance_identity"] = identity  # type: ignore[index]
            evidence["artifact_integrity"] = sign_payload(evidence, signing_key_path=private)
            result = validate_admin_evidence_artifact(
                evidence,
                now_utc=AT,
                expected_database="CologneTransitIntelligence",
                public_key_path=public,
                expected_sql_identity=identity,
            )
            self.assertEqual(result["sql_instance_identity"]["server_name"], r"sql01\COLLECTOR")
            different_instance = dict(identity)
            different_instance["server_name"] = r"sql02\COLLECTOR"
            different_instance["machine_name"] = "sql02"
            with self.assertRaisesRegex(AdminEvidenceError, "different SQL instance"):
                validate_admin_evidence_artifact(
                    evidence,
                    now_utc=AT,
                    expected_database="CologneTransitIntelligence",
                    public_key_path=public,
                    expected_sql_identity=different_instance,
                )

    def test_editing_signed_payload_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            private = directory_path / "private.pem"
            public = directory_path / "public.pem"
            os.system(f"/usr/bin/openssl genrsa -out {private} 2048 >/dev/null 2>&1")
            private.chmod(0o600)
            os.system(f"/usr/bin/openssl rsa -in {private} -pubout -out {public} >/dev/null 2>&1")
            evidence = self._evidence()
            evidence["artifact_integrity"] = sign_payload(evidence, signing_key_path=private)
            evidence["sql"]["quota_integrity"] = {"status": "PASS", "state_available": False}  # type: ignore[index]
            with self.assertRaises(AdminEvidenceError):
                validate_admin_evidence_artifact(
                    evidence,
                    now_utc=AT,
                    expected_database="CologneTransitIntelligence",
                    public_key_path=public,
                )

    def test_stale_signed_administrative_evidence_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            private = directory_path / "private.pem"
            public = directory_path / "public.pem"
            os.system(f"/usr/bin/openssl genrsa -out {private} 2048 >/dev/null 2>&1")
            private.chmod(0o600)
            os.system(f"/usr/bin/openssl rsa -in {private} -pubout -out {public} >/dev/null 2>&1")
            evidence = self._evidence()
            evidence["artifact_integrity"] = sign_payload(evidence, signing_key_path=private)
            with self.assertRaisesRegex(AdminEvidenceError, "stale"):
                validate_admin_evidence_artifact(
                    evidence,
                    now_utc=AT + timedelta(minutes=31),
                    expected_database="CologneTransitIntelligence",
                    public_key_path=public,
                )


if __name__ == "__main__":
    unittest.main()
