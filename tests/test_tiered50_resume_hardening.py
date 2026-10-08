from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import activate_tiered50_production as activation  # noqa: E402
from cologne_transit_collector.admin_evidence import (  # noqa: E402
    sql_instance_identities_match,
)


class _LockCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def execute(self, statement, params=None):  # type: ignore[no-untyped-def]
        self.calls.append((str(statement), params))


class _LockConnection:
    def __init__(self) -> None:
        self.cursor_value = _LockCursor()
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):  # type: ignore[no-untyped-def]
        return self.cursor_value

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class Tiered50ResumeHardeningTests(unittest.TestCase):
    def test_zero_is_a_valid_known_total_and_missing_null_negative_fail_closed(self) -> None:
        self.assertEqual(activation._strict_quota_integer({"known_total": 0}, "known_total"), 0)
        self.assertEqual(activation._strict_quota_integer({"known_total": 17}, "known_total"), 17)
        for value, expected in ((None, "NULL"), (-1, "negative")):
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, expected):
                activation._strict_quota_integer({"known_total": value}, "known_total")
        with self.assertRaisesRegex(RuntimeError, "missing"):
            activation._strict_quota_integer({}, "known_total")

    def test_admin_keychain_path_is_separate_from_runtime_environment(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MSSQL_HOST": "runtime-host",
                "MSSQL_USER": "CologneTransitCollector",
                "MSSQL_PASSWORD": "runtime-secret",
                "MSSQL_ADMIN_HOST": "sql-alias",
                "MSSQL_ADMIN_DATABASE": "CologneTransitIntelligence",
                "MSSQL_ADMIN_USER": "separate-admin",
            },
            clear=True,
        ), patch.object(
            activation.MacOSKeychainSecretProvider,
            "get",
            return_value="keychain-secret",
        ) as get_secret:
            settings = activation._administrator_connection_settings()
        self.assertEqual(settings.user, "separate-admin")
        self.assertEqual(settings.password, "keychain-secret")
        get_secret.assert_called_once()

    def test_application_lock_is_session_scoped_and_exclusive(self) -> None:
        connection = _LockConnection()
        activation._acquire_resume_operation_lock(connection)
        statement = connection.cursor_value.calls[0][0]
        self.assertIn("sp_getapplock", statement)
        self.assertIn("LockOwner = N'Session'", statement)
        self.assertIn("LockMode = N'Exclusive'", statement)
        activation._release_resume_operation_lock(connection)
        self.assertIn("sp_releaseapplock", connection.cursor_value.calls[-1][0])

    def test_refresh_rechecks_sql_clock_and_quota_at_transition(self) -> None:
        first = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
        second = datetime(2026, 10, 8, 12, 1, tzinfo=timezone.utc)
        third = datetime(2026, 10, 8, 12, 1, 1, tzinfo=timezone.utc)
        runtime_values = [
            {
                "config_path": "/tmp/runtime.json",
                "quota": {"state_available": True, "reserved_shared_usage": 0, "known_total": 100},
            },
            {
                "config_path": "/tmp/runtime.json",
                "quota": {"state_available": True, "reserved_shared_usage": 0, "known_total": 101},
            },
        ]
        evidence = [{}, {}, {}]
        with patch.object(activation, "_assert_resume_operation_lock"), patch.object(
            activation, "_authoritative_sql_utc", side_effect=[first, second, third]
        ), patch.object(
            activation, "_validate_limited_runtime", side_effect=runtime_values
        ) as runtime, patch.object(
            activation, "_validate_operational_evidence", side_effect=evidence
        ), patch.object(activation, "_read_current_competing_writers", return_value=[]), patch.object(
            activation,
            "_installed_tiered50_resume_readiness",
            return_value={"loaded_before_resume": False},
        ):
            result = activation._refresh_resume_readiness(
                connection=_LockConnection(),
                runtime_config_path="/tmp/runtime.json",
                expected_runtime_sql_identity=None,
            )
        self.assertEqual(result["authoritative_sql_utc"], third)
        self.assertEqual(result["quota"]["known_total"], 101)
        self.assertEqual([call.kwargs["now"] for call in runtime.call_args_list], [first, second])

    def test_refresh_rejects_evidence_that_expires_during_preflight(self) -> None:
        first = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
        second = datetime(2026, 10, 8, 12, 31, tzinfo=timezone.utc)
        runtime = {
            "config_path": "/tmp/runtime.json",
            "quota": {"state_available": True, "reserved_shared_usage": 0, "known_total": 0},
        }
        with patch.object(activation, "_assert_resume_operation_lock"), patch.object(
            activation, "_authoritative_sql_utc", side_effect=[first, second]
        ), patch.object(activation, "_validate_limited_runtime", return_value=runtime), patch.object(
            activation,
            "_validate_operational_evidence",
            side_effect=[{}, RuntimeError("administrator operational evidence is stale")],
        ), patch.object(activation, "_read_current_competing_writers", return_value=[]), patch.object(
            activation, "_installed_tiered50_resume_readiness"
        ) as readiness:
            with self.assertRaisesRegex(RuntimeError, "evidence is stale"):
                activation._refresh_resume_readiness(
                    connection=_LockConnection(),
                    runtime_config_path="/tmp/runtime.json",
                    expected_runtime_sql_identity=None,
                )
        readiness.assert_not_called()

    def test_refresh_does_not_extend_the_october_hard_stop(self) -> None:
        first = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
        stop = datetime(2026, 10, 30, 23, 0, tzinfo=timezone.utc)
        runtime = {
            "config_path": "/tmp/runtime.json",
            "quota": {
                "state_available": True,
                "reserved_shared_usage": 0,
                "known_total": 0,
                "authorization_mode": activation.BOOTSTRAP_AUTHORIZATION_MODE,
                "month_key": "2026-10",
            },
        }
        with patch.object(activation, "_assert_resume_operation_lock"), patch.object(
            activation, "_authoritative_sql_utc", side_effect=[first, stop]
        ), patch.object(activation, "_validate_limited_runtime", return_value=runtime), patch.object(
            activation, "_validate_operational_evidence", return_value={}
        ), patch.object(activation, "_read_current_competing_writers", return_value=[]), patch.object(
            activation, "_installed_tiered50_resume_readiness"
        ) as readiness:
            with self.assertRaisesRegex(RuntimeError, "hard stop"):
                activation._refresh_resume_readiness(
                    connection=_LockConnection(),
                    runtime_config_path="/tmp/runtime.json",
                    expected_runtime_sql_identity=None,
                )
        readiness.assert_not_called()

    def test_successful_rollback_is_explicitly_verified(self) -> None:
        with patch.object(activation, "install_launch_agent"), patch.object(
            activation, "bootstrap_launch_agent"
        ), patch.object(
            activation,
            "_launchctl_state_after_rollback",
            return_value={"status": "READABLE", "loaded": False, "disabled": True},
        ), patch.object(
            activation,
            "_installed_tiered50_disabled_state",
            return_value={"status": "PASS", "exists": True, "disabled": True},
        ):
            result = activation._disable_tiered50_after_resume_failure(runtime_config_path="/tmp/runtime.json")
        self.assertEqual(result["status"], "STOPPED_VERIFIED")
        self.assertFalse(result["critical_operational_blocker"])

    def test_rollback_failures_and_unreadable_state_are_not_reported_as_stopped(self) -> None:
        with patch.object(activation, "install_launch_agent", side_effect=RuntimeError("install failed")), patch.object(
            activation, "bootstrap_launch_agent", side_effect=RuntimeError("unload failed")
        ), patch.object(
            activation,
            "_launchctl_state_after_rollback",
            return_value={"status": "UNREADABLE", "loaded": None, "disabled": None},
        ), patch.object(
            activation,
            "_installed_tiered50_disabled_state",
            return_value={"status": "PASS", "exists": True, "disabled": True},
        ):
            result = activation._disable_tiered50_after_resume_failure(runtime_config_path="/tmp/runtime.json")
        self.assertEqual(result["status"], "STOP_NOT_VERIFIED")
        self.assertTrue(result["critical_operational_blocker"])
        self.assertEqual(len(result["rollback_failures"]), 2)

    def test_install_failure_is_recorded_even_when_stop_verifies(self) -> None:
        with patch.object(activation, "install_launch_agent", side_effect=RuntimeError("install failed")), patch.object(
            activation, "bootstrap_launch_agent"
        ), patch.object(
            activation,
            "_launchctl_state_after_rollback",
            return_value={"status": "READABLE", "loaded": False, "disabled": True},
        ), patch.object(
            activation,
            "_installed_tiered50_disabled_state",
            return_value={"status": "PASS", "exists": True, "disabled": True},
        ):
            result = activation._disable_tiered50_after_resume_failure(runtime_config_path="/tmp/runtime.json")
        self.assertEqual(result["status"], "STOPPED_VERIFIED")
        self.assertEqual(result["rollback_failures"][0]["action"], "install_disabled_plist")

    def test_bootstrap_failure_is_recorded_even_when_stop_verifies(self) -> None:
        with patch.object(activation, "install_launch_agent"), patch.object(
            activation, "bootstrap_launch_agent", side_effect=RuntimeError("bootstrap failed")
        ), patch.object(
            activation,
            "_launchctl_state_after_rollback",
            return_value={"status": "READABLE", "loaded": False, "disabled": True},
        ), patch.object(
            activation,
            "_installed_tiered50_disabled_state",
            return_value={"status": "PASS", "exists": True, "disabled": True},
        ):
            result = activation._disable_tiered50_after_resume_failure(runtime_config_path="/tmp/runtime.json")
        self.assertEqual(result["status"], "STOPPED_VERIFIED")
        self.assertEqual(result["rollback_failures"][0]["action"], "bootout_and_disable_launchagent")

    def test_loaded_after_rollback_is_still_active(self) -> None:
        with patch.object(activation, "install_launch_agent"), patch.object(
            activation, "bootstrap_launch_agent"
        ), patch.object(
            activation,
            "_launchctl_state_after_rollback",
            return_value={"status": "READABLE", "loaded": True, "disabled": False},
        ), patch.object(
            activation,
            "_installed_tiered50_disabled_state",
            return_value={"status": "PASS", "exists": True, "disabled": True},
        ):
            result = activation._disable_tiered50_after_resume_failure(runtime_config_path="/tmp/runtime.json")
        self.assertEqual(result["status"], "STILL_ACTIVE")
        self.assertTrue(result["critical_operational_blocker"])

    def test_session_lock_contention_is_reported_as_concurrent_resume(self) -> None:
        class ContendedCursor(_LockCursor):
            def execute(self, statement, params=None):  # type: ignore[no-untyped-def]
                super().execute(statement, params)
                raise RuntimeError("SQL error 51001: Tiered50 resume operation is already in progress")

        connection = _LockConnection()
        connection.cursor_value = ContendedCursor()
        with self.assertRaisesRegex(RuntimeError, "TIERED50_RESUME_ALREADY_IN_PROGRESS"):
            activation._acquire_resume_operation_lock(connection)

    def test_failure_after_launchagent_start_preserves_original_and_rollback(self) -> None:
        targets = [
            {
                "SamplingTargetId": target_id,
                "IsEnabled": True,
                "ActivationStatus": "ACTIVE",
                "PanelVersion": "TIERED50_PANEL_V1",
                "SamplingEra": "Tiered50",
                "ActivatedAtUtc": activation._utc_datetime("2026-10-08T09:00:00Z"),
            }
            for target_id in range(8, 51)
        ]
        era = [{
            "SamplingEraId": 77,
            "PanelVersion": "TIERED50_PANEL_V1",
            "EffectiveAtUtc": activation._utc_datetime("2026-10-08T09:00:00Z"),
            "TimestampKind": "REAL_TIERED50_CUTOVER",
            "IsActive": True,
            "IsSynthetic": False,
        }]
        current = [{
            "CurrentMarkerId": 1,
            "SamplingEraId": 77,
            "EraCode": "TIERED50_PRODUCTION",
            "PanelVersion": "TIERED50_PANEL_V1",
            "EffectiveAtUtc": activation._utc_datetime("2026-10-08T09:00:00Z"),
        }]
        cutovers = [
            {"CutoverKey": "activation", "Tiered50ActivationStatus": "ACTIVATED", "Tiered50ActivationAtUtc": activation._utc_datetime("2026-10-08T09:00:00Z")},
            {"CutoverKey": "outage", "Tiered50ActivationStatus": "OUTAGE", "Tiered50ActivationAtUtc": activation._utc_datetime("2026-10-08T09:00:00Z")},
        ]

        class Cursor:
            def __init__(self) -> None:
                self.rows: list[object] = []
                self.one: object = None

            def execute(self, statement: str, params=()) -> None:
                del params
                if "AuthoritativeUtc" in statement:
                    self.one = {"AuthoritativeUtc": activation._utc_datetime("2026-10-08T12:00:00Z")}
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
                elif "MddMacCollectorRun" in statement or "MddMacProductionResumeEvent" in statement:
                    self.rows = []
                else:
                    self.one = None
                    self.rows = []

            def fetchall(self):
                return list(self.rows)

            def fetchone(self):
                result = self.one
                self.one = None
                return result

        class Connection(_LockConnection):
            def __init__(self) -> None:
                super().__init__()
                self.cursor_value = Cursor()

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
                "sql_instance_identity": {"server_name": "sql01", "database_name": "CologneTransitIntelligence"},
                "quota": {"state_available": True, "reserved_shared_usage": 0, "known_total": 0},
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
            activation, "_record_resume_event", side_effect=RuntimeError("resume event insert failed")
        ), patch.object(
            activation,
            "_disable_tiered50_after_resume_failure",
            return_value={
                "status": "STOPPED_VERIFIED",
                "critical_operational_blocker": False,
                "rollback_failures": [],
            },
        ):
            with self.assertRaises(activation.Tiered50ResumeFailure) as raised:
                activation.resume_tiered50_production(resume_key="TIERED50-RESUME-EVENT-FAILURE")
        self.assertEqual(raised.exception.original_failure_type, "RuntimeError")
        self.assertEqual(raised.exception.rollback["status"], "STOPPED_VERIFIED")

    def test_actual_instance_binding_rejects_same_database_on_different_instance(self) -> None:
        expected = {
            "server_name": "sql01\\COLLECTOR",
            "machine_name": "sql01",
            "instance_name": "COLLECTOR",
            "database_name": "CologneTransitIntelligence",
        }
        self.assertTrue(sql_instance_identities_match(dict(expected), dict(expected)))
        different = dict(expected)
        different["server_name"] = "sql02\\COLLECTOR"
        different["machine_name"] = "sql02"
        self.assertFalse(sql_instance_identities_match(different, expected))
        self.assertFalse(sql_instance_identities_match({"server_name": "sql01\\COLLECTOR"}, {}))


if __name__ == "__main__":
    unittest.main()
