from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from cologne_transit_collector.domain import SamplingEra, Target  # noqa: E402
from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    FULL_SCHEMA_TABLES,
    FULL_SCHEMA_ROUTINES,
    MssqlConnectionSettings,
    MssqlPersistence,
    RUNTIME_SCHEMA_TABLES,
    RUNTIME_SCHEMA_ROUTINES,
)
from cologne_transit_collector.runtime_configuration import (  # noqa: E402
    KEYCHAIN_ACCOUNT_MSSQL,
    KEYCHAIN_ACCOUNT_MSSQL_PENDING,
    KEYCHAIN_SERVICE_MSSQL,
    KEYCHAIN_SERVICE_MSSQL_PENDING,
    RuntimeConfigurationError,
)
from provision_macos_runtime_login import (  # noqa: E402
    GRANT_STATEMENTS,
    RUNTIME_PERMISSION_MATRIX,
    RUNTIME_LOGIN,
    RUNTIME_ROLE,
    _generate_password,
    expected_grant_statements,
    provision,
)


class _RuntimeCursor:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.params: list[object] = []

    def execute(self, statement: str, params=None) -> None:
        self.statements.append(statement)
        self.params.append(params)
        if "MddImportBatch" in statement or "MddMacSourceSnapshot" in statement:
            raise PermissionError("unrelated object is not granted to simulated runtime")

    def fetchall(self):
        statement = self.statements[-1]
        if "sys.columns" in statement:
            return [{"column_name": "SamplingSlot"}, {"column_name": "SelectionPolicyVersion"}]
        if "sys.objects" in statement:
            return [
                {
                    "object_name": name,
                    "object_type": "U" if name in RUNTIME_SCHEMA_TABLES else "P",
                }
                for name in RUNTIME_SCHEMA_TABLES + RUNTIME_SCHEMA_ROUTINES
            ]
        return []


class _RuntimeConnection:
    def __init__(self) -> None:
        self.cursor_instance = _RuntimeCursor()
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def rollback(self):
        pass

    def close(self):
        self.closed = True


class RuntimeSchemaValidationTests(unittest.TestCase):
    def test_least_privilege_runtime_validation_does_not_require_admin_objects(self) -> None:
        connection = _RuntimeConnection()
        adapter = MssqlPersistence(
            settings=MssqlConnectionSettings(host="runtime", user=RUNTIME_LOGIN, password="not-used"),
            connection_factory=lambda: connection,
        )

        result = adapter.validate_runtime_schema()

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["validation_scope"], "PRODUCTION_RUNTIME")
        self.assertFalse(result["unrelated_import_objects_required"])
        self.assertEqual(result["required_legacy7_audit_columns"], ["SamplingSlot", "SelectionPolicyVersion"])
        serialized_runtime_sql = "\n".join(connection.cursor_instance.statements)
        self.assertNotIn("MddImportBatch", serialized_runtime_sql)
        self.assertNotIn("MddMacSourceSnapshot", serialized_runtime_sql)
        self.assertNotIn("vwMddMacSourceFieldCoverage", serialized_runtime_sql)

        with self.assertRaises(PermissionError):
            connection.cursor_instance.execute("SELECT TOP (1) ImportBatchId FROM ctl.MddImportBatch")
        with self.assertRaises(PermissionError):
            connection.cursor_instance.execute("SELECT TOP (1) SnapshotId FROM stg.MddMacSourceSnapshot")

    def test_full_and_runtime_object_contracts_are_distinct(self) -> None:
        self.assertIn("ctl.MddImportBatch", FULL_SCHEMA_TABLES)
        self.assertIn("stg.MddMacSourceSnapshot", FULL_SCHEMA_TABLES)
        self.assertIn("analytics.vwMddMacSourceFieldCoverage", FULL_SCHEMA_ROUTINES)
        self.assertNotIn("ctl.MddImportBatch", RUNTIME_SCHEMA_TABLES)
        self.assertNotIn("stg.MddMacSourceSnapshot", RUNTIME_SCHEMA_TABLES)
        self.assertNotIn("analytics.vwMddMacSourceFieldCoverage", RUNTIME_SCHEMA_ROUTINES)


class PermissionContractTests(unittest.TestCase):
    def test_documented_permission_contract_matches_executable_grants(self) -> None:
        self.assertEqual(GRANT_STATEMENTS, expected_grant_statements())
        permissions_by_object: dict[str, set[str]] = {}
        for item in RUNTIME_PERMISSION_MATRIX:
            permissions_by_object.setdefault(item.object_name, set()).add(item.permission)
        serialized = "\n".join(GRANT_STATEMENTS)
        for object_name, permissions in permissions_by_object.items():
            statement = next(line for line in GRANT_STATEMENTS if f"OBJECT::{object_name} " in line)
            self.assertEqual(
                set(statement.split("GRANT ", 1)[1].split(" ON OBJECT::", 1)[0].split(", ")),
                permissions,
            )
        for forbidden in (
            "stg.MddRealtimeStopObservation",
            "stg.MddRealtimeSituationObservation",
            "stg.MddRealtimeStopSituationLink",
            "ctl.MddMacSamplingEra",
            "ctl.MddMacMethodologyEvidence",
            "analytics.vwMddMacRecordedAtTimeHistory",
            "analytics.vwMddMacSourceFieldCoverage",
            "ctl.MddImportBatch",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_documented_permission_table_mentions_the_exact_runtime_objects(self) -> None:
        documentation = (ROOT / "docs" / "macos-mssql-collector.md").read_text(encoding="utf-8")
        documented: dict[str, set[str]] = {}
        in_permission_table = False
        for line in documentation.splitlines():
            if line.strip() == "| Permission | Object |":
                in_permission_table = True
                continue
            if not in_permission_table:
                continue
            if not line.startswith("|"):
                break
            if line.startswith("| ---"):
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) != 2:
                continue
            permissions = {
                permission.strip().strip("`")
                for permission in cells[0].strip("`").split(",")
            }
            for object_name in cells[1].split(","):
                clean_name = object_name.strip().split(" (", 1)[0].strip("`")
                documented.setdefault(clean_name, set()).update(permissions)

        executable: dict[str, set[str]] = {}
        for statement in GRANT_STATEMENTS:
            permissions, object_name = statement.split(" ON OBJECT::", 1)
            executable[object_name.split(" TO ", 1)[0]] = set(
                permissions.removeprefix("GRANT ").split(", ")
            )
        self.assertEqual(documented, executable)


class _FakeKeychain:
    def __init__(self, pending: str | None = None) -> None:
        self.values: dict[tuple[str, str], str] = {}
        if pending is not None:
            self.values[(KEYCHAIN_SERVICE_MSSQL_PENDING, KEYCHAIN_ACCOUNT_MSSQL_PENDING)] = pending

    def get(self, *, service: str, account: str) -> str | None:
        return self.values.get((service, account))


class _AdminCursor:
    def __init__(self, login_exists: bool) -> None:
        self.login_exists = login_exists
        self.statements: list[str] = []
        self.params: list[object] = []

    def execute(self, statement: str, params=None) -> None:
        self.statements.append(statement)
        self.params.append(params)

    def fetchone(self):
        if "login_exists" in self.statements[-1]:
            return {"login_exists": int(self.login_exists)}
        return {"value": 0}


class _AdminConnection:
    def __init__(self, login_exists: bool) -> None:
        self.cursor_instance = _AdminCursor(login_exists)
        self.commit_count = 0
        self.rollback_count = 0
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1

    def close(self):
        self.closed = True


class _NegativeCursor:
    def execute(self, statement: str, params=None) -> None:
        if "HAS_PERMS_BY_NAME" in statement:
            self.last = statement
            return
        raise PermissionError("simulated unrelated permission denial")

    def fetchone(self):
        return {
            "is_sysadmin": 0,
            "is_serveradmin": 0,
            "is_securityadmin": 0,
            "is_db_owner": 0,
            "is_db_datareader": 0,
            "is_db_datawriter": 0,
            "control_server": 0,
            "alter_any_login": 0,
            "create_any_database": 0,
            "audit_control": 0,
            "audit_alter": 0,
            "audit_delete": 0,
        }


class _NegativeConnection:
    def cursor(self):
        return _NegativeCursor()

    def close(self):
        pass


class _PositivePersistence:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def runtime_login_status(self):
        return {
            "database_name": "CologneTransitIntelligence",
            "login_name": RUNTIME_LOGIN,
            "is_sysadmin": False,
            "is_serveradmin": False,
            "is_securityadmin": False,
            "is_db_owner": False,
            "is_db_datareader": False,
            "is_db_datawriter": False,
        }

    def validate_runtime_schema(self):
        return {"status": "PASS", "validation_scope": "PRODUCTION_RUNTIME"}

    def load_existing_sampling_targets(self):
        return tuple(object() for _ in range(7))

    def select_existing_legacy7_target(self, *, at_utc, targets):
        return SimpleNamespace(selected_sampling_slot=1, legacy_sampling_target_id=2)


def _provision_patches(
    *,
    provider: _FakeKeychain,
    admin_connection: _AdminConnection,
    runtime_config_path: Path,
    generated_password: str,
    finalization_failure: bool = False,
):
    admin_settings = MssqlConnectionSettings(host="sql", user="admin", password="admin-secret")

    def store(*, service: str, account: str, secret: str) -> None:
        if finalization_failure and service == KEYCHAIN_SERVICE_MSSQL:
            raise RuntimeConfigurationError("final approved Keychain credential could not be verified")
        provider.values[(service, account)] = secret

    def delete(*, service: str, account: str) -> None:
        provider.values.pop((service, account), None)

    return (
        patch("provision_macos_runtime_login.MssqlConnectionSettings.from_environment", return_value=admin_settings),
        patch("provision_macos_runtime_login.connect_mssql", side_effect=[admin_connection, _NegativeConnection()]),
        patch("provision_macos_runtime_login.MssqlPersistence", _PositivePersistence),
        patch("provision_macos_runtime_login._generate_password", return_value=generated_password),
        patch("provision_macos_runtime_login.store_keychain_secret", side_effect=store),
        patch("provision_macos_runtime_login.delete_keychain_secret", side_effect=delete),
        patch("provision_macos_runtime_login._runtime_local_values", return_value={"MSSQL_USER": RUNTIME_LOGIN}),
        patch("provision_macos_runtime_login.write_local_runtime_config", return_value=runtime_config_path),
    )


class ProvisioningRecoveryTests(unittest.TestCase):
    def test_successful_first_provisioning_uses_pending_then_promotes(self) -> None:
        secret = "Candidate-DO-NOT-LEAK-123!"
        provider = _FakeKeychain()
        admin = _AdminConnection(login_exists=False)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.json"
            patches = _provision_patches(
                provider=provider,
                admin_connection=admin,
                runtime_config_path=path,
                generated_password=secret,
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
                result = provision(runtime_config_path=path, keychain_provider=provider)

        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["pending_keychain_removed"])
        self.assertEqual(provider.get(service=KEYCHAIN_SERVICE_MSSQL, account=KEYCHAIN_ACCOUNT_MSSQL), secret)
        self.assertIsNone(provider.get(service=KEYCHAIN_SERVICE_MSSQL_PENDING, account=KEYCHAIN_ACCOUNT_MSSQL_PENDING))
        self.assertNotIn(secret, json.dumps(result))
        self.assertNotIn(secret, "\n".join(admin.cursor_instance.statements))
        self.assertIn(secret, str(admin.cursor_instance.params))
        self.assertEqual(admin.commit_count, 1)

    def test_keychain_finalization_failure_retains_recovery_credential_without_leakage(self) -> None:
        secret = "Candidate-RECOVERY-DO-NOT-LEAK-456!"
        provider = _FakeKeychain()
        admin = _AdminConnection(login_exists=False)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.json"
            patches = _provision_patches(
                provider=provider,
                admin_connection=admin,
                runtime_config_path=path,
                generated_password=secret,
                finalization_failure=True,
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
                result = provision(runtime_config_path=path, keychain_provider=provider)

        self.assertEqual(result["status"], "CREDENTIAL_FINALIZATION_RECOVERY_REQUIRED")
        self.assertTrue(result["pending_keychain_item_retained"])
        self.assertFalse(result["launchagent_enabled"])
        self.assertEqual(provider.get(service=KEYCHAIN_SERVICE_MSSQL_PENDING, account=KEYCHAIN_ACCOUNT_MSSQL_PENDING), secret)
        self.assertNotIn(secret, json.dumps(result))
        self.assertNotIn(secret, "\n".join(admin.cursor_instance.statements))
        self.assertIn(secret, str(admin.cursor_instance.params))

    def test_safe_resume_reuses_pending_password_and_does_not_rotate_existing_login(self) -> None:
        secret = "Candidate-RESUME-DO-NOT-LEAK-789!"
        provider = _FakeKeychain(pending=secret)
        admin = _AdminConnection(login_exists=True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.json"
            patches = _provision_patches(
                provider=provider,
                admin_connection=admin,
                runtime_config_path=path,
                generated_password="A-DIFFERENT-GENERATED-PASSWORD",
            )
            with patches[0], patches[1], patches[2], patch(
                "provision_macos_runtime_login._generate_password",
                side_effect=AssertionError("resume must not generate another password"),
            ), patches[4], patches[5], patches[6], patches[7]:
                result = provision(
                    resume=True,
                    runtime_config_path=path,
                    keychain_provider=provider,
                )

        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["resumed_pending_credential"])
        self.assertNotIn("ALTER LOGIN", "\n".join(admin.cursor_instance.statements))
        self.assertNotIn(secret, "\n".join(admin.cursor_instance.statements))
        self.assertNotIn(secret, str(admin.cursor_instance.params))
        self.assertNotIn(secret, json.dumps(result))

    def test_existing_login_without_explicit_rotation_is_refused(self) -> None:
        provider = _FakeKeychain()
        admin = _AdminConnection(login_exists=True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.json"
            patches = _provision_patches(
                provider=provider,
                admin_connection=admin,
                runtime_config_path=path,
                generated_password="must-not-be-generated",
            )
            with patches[0], patches[1], patches[2], patch(
                "provision_macos_runtime_login._generate_password",
                side_effect=AssertionError("existing login must not rotate"),
            ), patches[4], patches[5], patches[6], patches[7]:
                with self.assertRaisesRegex(RuntimeError, "already exists"):
                    provision(runtime_config_path=path, keychain_provider=provider)
        self.assertEqual(admin.commit_count, 0)

    def test_explicit_rotation_is_allowed_and_still_uses_pending_anchor(self) -> None:
        secret = "Candidate-ROTATION-DO-NOT-LEAK-012!"
        provider = _FakeKeychain()
        admin = _AdminConnection(login_exists=True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.json"
            patches = _provision_patches(
                provider=provider,
                admin_connection=admin,
                runtime_config_path=path,
                generated_password=secret,
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
                result = provision(
                    rotate_existing=True,
                    runtime_config_path=path,
                    keychain_provider=provider,
                )
        self.assertEqual(result["status"], "PASS")
        self.assertIn("ALTER LOGIN", "\n".join(admin.cursor_instance.statements))
        self.assertNotIn(secret, "\n".join(admin.cursor_instance.statements))
        self.assertIn(secret, str(admin.cursor_instance.params))
        self.assertNotIn(secret, json.dumps(result))

    def test_password_generator_does_not_share_any_runtime_contract_secret(self) -> None:
        password = _generate_password()
        self.assertNotIn(password, " ".join(GRANT_STATEMENTS))
        self.assertNotIn(password, json.dumps({"runtime_login": RUNTIME_LOGIN, "runtime_role": RUNTIME_ROLE}))


if __name__ == "__main__":
    unittest.main()
