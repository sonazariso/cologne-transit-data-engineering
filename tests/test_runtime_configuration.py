from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from cologne_transit_collector.mssql_persistence import MssqlConnectionSettings  # noqa: E402
from cologne_transit_collector.runtime_configuration import (  # noqa: E402
    DEFAULT_RUNTIME_CONFIG_PATH,
    KEYCHAIN_SERVICE_MDD_API,
    KEYCHAIN_SERVICE_MSSQL,
    MDD_API_CREDENTIAL,
    MSSQL_CREDENTIAL,
    RUNTIME_CONFIG_PATH_ENV,
    RuntimeConfiguration,
    RuntimeConfigurationError,
    SecretRequired,
    MacOSKeychainSecretProvider,
    merge_non_secret_runtime_values,
    resolve_required_secrets,
    resolve_secret,
    store_keychain_secret,
    write_local_runtime_config,
)
from install_macos_launch_agent import build_installed_plist  # noqa: E402


class _Provider:
    def __init__(self, values: dict[tuple[str, str], str | None]) -> None:
        self.values = values
        self.calls: list[tuple[str, str]] = []

    def get(self, *, service: str, account: str) -> str | None:
        self.calls.append((service, account))
        return self.values.get((service, account))


class RuntimeConfigurationTests(unittest.TestCase):
    def test_environment_values_win_over_local_non_secret_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.json"
            write_local_runtime_config(
                path,
                {
                    "MSSQL_HOST": "local-host",
                    "MSSQL_DATABASE": "CologneTransitIntelligence",
                    "MSSQL_USER": "local-user",
                },
            )
            configuration = merge_non_secret_runtime_values(
                {
                    RUNTIME_CONFIG_PATH_ENV: str(path),
                    "MSSQL_HOST": "explicit-host",
                }
            )
            self.assertEqual(configuration.values["MSSQL_HOST"], "explicit-host")
            self.assertEqual(configuration.values["MSSQL_USER"], "local-user")

    def test_secret_resolution_uses_environment_before_keychain(self) -> None:
        provider = _Provider(
            {
                (KEYCHAIN_SERVICE_MDD_API, "MDD_API_KEY"): "keychain-api",
                (KEYCHAIN_SERVICE_MSSQL, "MSSQL_PASSWORD"): "keychain-password",
            }
        )
        configuration = RuntimeConfiguration(
            values={"MDD_API_KEY": "environment-api", "MSSQL_HOST": "host", "MSSQL_USER": "user"},
            config_path=DEFAULT_RUNTIME_CONFIG_PATH,
            config_loaded=False,
        )
        resolved = resolve_required_secrets(configuration, provider=provider)
        self.assertEqual(resolved.values["MDD_API_KEY"], "environment-api")
        self.assertEqual(resolved.values["MSSQL_PASSWORD"], "keychain-password")
        self.assertEqual(provider.calls, [(KEYCHAIN_SERVICE_MSSQL, "MSSQL_PASSWORD")])

    def test_missing_secret_fails_closed_without_value_disclosure(self) -> None:
        with self.assertRaises(SecretRequired) as caught:
            resolve_secret(
                {"MSSQL_HOST": "host"},
                credential_type=MSSQL_CREDENTIAL,
                provider=_Provider({}),
            )
        self.assertEqual(str(caught.exception), "SECRET_REQUIRED MSSQL_PASSWORD")
        self.assertNotIn("host", str(caught.exception))

    def test_redacted_runtime_diagnostics_never_include_secret_values(self) -> None:
        configuration = RuntimeConfiguration(
            values={
                "MDD_API_KEY": "api-secret-value",
                "MSSQL_PASSWORD": "sql-secret-value",
                "MSSQL_HOST": "172.16.192.128",
                "MSSQL_DATABASE": "CologneTransitIntelligence",
                "MSSQL_USER": "collector",
            },
            config_path=DEFAULT_RUNTIME_CONFIG_PATH,
            config_loaded=True,
        )
        serialized = json.dumps(configuration.redacted(), sort_keys=True)
        self.assertNotIn("api-secret-value", serialized)
        self.assertNotIn("sql-secret-value", serialized)

    def test_local_config_rejects_secret_fields_and_is_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.json"
            with self.assertRaises(RuntimeConfigurationError):
                write_local_runtime_config(
                    path,
                    {
                        "MSSQL_HOST": "host",
                        "MSSQL_PASSWORD": "must-not-write",
                    },
                )
            write_local_runtime_config(
                path,
                {"MSSQL_HOST": "host", "MSSQL_DATABASE": "CologneTransitIntelligence"},
            )
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_connection_settings_can_resolve_password_from_keychain_provider(self) -> None:
        provider = _Provider({(KEYCHAIN_SERVICE_MSSQL, "MSSQL_PASSWORD"): "sql-secret"})
        settings = MssqlConnectionSettings.from_environment(
            {
                "MSSQL_HOST": "host",
                "MSSQL_DATABASE": "CologneTransitIntelligence",
                "MSSQL_USER": "user",
            },
            secret_provider=provider,
        )
        self.assertEqual(settings.password, "sql-secret")
        self.assertNotIn("sql-secret", json.dumps(settings.redacted()))

    def test_keychain_provider_captures_secret_without_emitting_it(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["security"],
            returncode=0,
            stdout=b"api-secret\n",
            stderr=b"",
        )
        with patch("cologne_transit_collector.runtime_configuration.subprocess.run", return_value=completed) as run:
            value = MacOSKeychainSecretProvider().get(
                service=KEYCHAIN_SERVICE_MDD_API,
                account="MDD_API_KEY",
            )
        self.assertEqual(value, "api-secret")
        self.assertNotIn("api-secret", run.call_args.args[0])

    def test_keychain_write_does_not_put_secret_in_process_arguments(self) -> None:
        with patch(
            "cologne_transit_collector.runtime_configuration._native_keychain_store",
            return_value=None,
        ), patch(
            "cologne_transit_collector.runtime_configuration._run_keychain_prompt",
            return_value=0,
        ) as run:
            store_keychain_secret(
                service=KEYCHAIN_SERVICE_MDD_API,
                account="MDD_API_KEY",
                secret="api-secret",
            )
        args = run.call_args.args[0]
        self.assertNotIn("api-secret", args)
        self.assertEqual(run.call_args.args[1], "api-secret")

    def test_installed_plist_is_secret_free_and_safe_by_default(self) -> None:
        plist = build_installed_plist(
            activate=False,
            runtime_config_path=Path("/tmp/cti-runtime.json"),
            mode="tiered50",
        )
        serialized = plistlib.dumps(plist).decode("utf-8")
        self.assertTrue(plist["Disabled"])
        self.assertEqual(plist["EnvironmentVariables"]["COLLECTOR_MAC_TIERED50_ENABLED"], "0")
        self.assertNotIn("MDD_API_KEY", serialized)
        self.assertNotIn("MSSQL_PASSWORD", serialized)
        self.assertNotEqual(plist["ProgramArguments"][0], "/usr/bin/env")
        self.assertIn("/runtime-bundles/tiered50-v1-", plist["ProgramArguments"][1])
        self.assertTrue(plist["ProgramArguments"][1].endswith("/macos_launchd_entry.py"))

    def test_installed_plist_open_values_are_exactly_the_approved_gates(self) -> None:
        plist = build_installed_plist(
            activate=True,
            runtime_config_path=Path("/tmp/cti-runtime.json"),
            mode="tiered50",
        )
        environment = plist["EnvironmentVariables"]
        self.assertFalse(plist["Disabled"])
        self.assertEqual(environment["COLLECTOR_MAC_SQL_MODE"], "TIERED50")
        self.assertEqual(environment["COLLECTOR_MAC_TIERED50_ENABLED"], "1")
        self.assertEqual(environment["COLLECTOR_WINDOWS_RUNTIME_STATE"], "CONFIRMED_RETIRED")
        self.assertEqual(environment["COLLECTOR_TIERED50_CUTOVER_APPROVED"], "1")
        self.assertEqual(environment["COLLECTOR_TIERED50_TARGET_SCOPE"], "TIERED50_PANEL_V1")
        self.assertEqual(environment["COLLECTOR_TIERED50_VALIDATION_POLICY"], "OFFLINE_RECONCILIATION_ONLY")


if __name__ == "__main__":
    unittest.main()
