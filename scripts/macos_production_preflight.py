#!/usr/bin/env python3
"""Read-only preflight for the exact macOS production configuration path.

This command never calls the MDD client.  It resolves the same local
non-secret configuration and Keychain credentials used by the LaunchAgent,
then performs only SQL connectivity/schema/target reads.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.configuration import CollectorSettings  # noqa: E402
from cologne_transit_collector.manifest import LEGACY7_SAMPLING_TARGET_IDS  # noqa: E402
from cologne_transit_collector.mssql_persistence import (  # noqa: E402
    MssqlConnectionSettings,
    MssqlDependencyError,
    MssqlPersistence,
    connect_mssql,
)
from cologne_transit_collector.runtime_configuration import (  # noqa: E402
    DEFAULT_RUNTIME_CONFIG_PATH,
    EXPECTED_SQL_DATABASE,
    REQUIRED_PYTHON_INTERPRETER,
    RuntimeConfigurationError,
    SecretRequired,
    merge_non_secret_runtime_values,
    resolve_required_secrets,
)
from cologne_transit_collector.transport import validate_api_key_format  # noqa: E402


INSTALLED_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.cologne-transit-intelligence.mdd-collector.plist"
SECRET_MARKERS = ("MDD_API_KEY", "MSSQL_PASSWORD", "SQL_SERVER_PASSWORD", "password", "secret", "token")


def _database_name(settings: MssqlConnectionSettings) -> str:
    connection = connect_mssql(settings)
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT DB_NAME() AS database_name")
        row = cursor.fetchone()
        if isinstance(row, dict):
            return str(row.get("database_name") or "")
        return str(row[0] if row else "")
    finally:
        connection.close()


def _sqlite_active(path: Path) -> bool:
    if not path.exists():
        return False
    lsof = Path("/usr/sbin/lsof")
    if not lsof.exists():
        return False
    completed = subprocess.run(
        [str(lsof), "-t", "--", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        text=True,
    )
    return bool(completed.stdout.strip())


def _validate_plist(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError("installed LaunchAgent plist is missing")
    with path.open("rb") as handle:
        plist = plistlib.load(handle)
    serialized = plistlib.dumps(plist, fmt=plistlib.FMT_XML).decode("utf-8", errors="replace")
    if any(marker in serialized for marker in SECRET_MARKERS):
        raise RuntimeError("LaunchAgent plist contains a prohibited secret marker")
    environment = dict(plist.get("EnvironmentVariables", {}))
    required = {"PYTHONPATH", "COLLECTOR_RUNTIME_CONFIG"}
    if not required.issubset(environment):
        raise RuntimeError("LaunchAgent plist is missing non-secret runtime paths")
    if plist.get("ProgramArguments", [None])[0] != REQUIRED_PYTHON_INTERPRETER:
        raise RuntimeError("LaunchAgent does not use the approved absolute interpreter")
    return {
        "path": str(path),
        "disabled": bool(plist.get("Disabled", False)),
        "run_at_load": bool(plist.get("RunAtLoad", False)),
        "start_interval": plist.get("StartInterval"),
        "secret_free": True,
        "runtime_config_path": environment["COLLECTOR_RUNTIME_CONFIG"],
    }


def run_preflight(*, plist_path: Path = INSTALLED_PLIST) -> dict[str, Any]:
    if sys.executable != REQUIRED_PYTHON_INTERPRETER:
        raise RuntimeError("preflight must run under the approved absolute interpreter")
    if not Path(REQUIRED_PYTHON_INTERPRETER).is_file() or not os.access(REQUIRED_PYTHON_INTERPRETER, os.X_OK):
        raise RuntimeError("approved Python interpreter is missing or not executable")

    import cologne_transit_collector  # noqa: F401
    import pymssql

    base = merge_non_secret_runtime_values()
    configuration = resolve_required_secrets(base)
    values = dict(configuration.values)
    if validate_api_key_format(values.get("MDD_API_KEY")) != "VALID":
        raise RuntimeError("MDD_API_KEY credential format is invalid")
    settings = MssqlConnectionSettings.from_environment(values)
    if settings.database != EXPECTED_SQL_DATABASE:
        raise RuntimeError(f"SQL Server target database must be {EXPECTED_SQL_DATABASE}")
    database_name = _database_name(settings)
    if database_name != EXPECTED_SQL_DATABASE:
        raise RuntimeError(f"connected SQL Server database must be {EXPECTED_SQL_DATABASE}")

    persistence = MssqlPersistence(settings=settings, raw_evidence_directory=None)
    schema = persistence.validate_schema()
    targets = persistence.load_existing_sampling_targets()
    target_ids = [int(target.legacy_sampling_target_id or 0) for target in targets]
    target_mapping = {target.source_parent_station_id: int(target.legacy_sampling_target_id or 0) for target in targets}
    if target_ids != list(range(1, 8)):
        raise RuntimeError("SamplingTargetIds are not exactly 1 through 7")
    if target_mapping != dict(LEGACY7_SAMPLING_TARGET_IDS):
        raise RuntimeError("Legacy7 StopPointRef mapping changed")

    runtime_settings = CollectorSettings.for_repository(ROOT, environ=values)
    sqlite_active = _sqlite_active(runtime_settings.state_path)
    if sqlite_active:
        raise RuntimeError("SQLite runtime database is active")

    plist = _validate_plist(plist_path)
    if plist["runtime_config_path"] != str(configuration.config_path):
        raise RuntimeError("LaunchAgent and preflight runtime configuration paths differ")

    return {
        "status": "PASS",
        "interpreter": sys.executable,
        "pymssql_version": str(getattr(pymssql, "__version__", "UNKNOWN")),
        "collector_import": "PASS",
        "runtime_config": configuration.redacted(),
        "keychain_secret_resolution": "PASS",
        "mdd_api_key_format": "VALID",
        "mdd_network_calls": 0,
        "sql_database": database_name,
        "schema": schema,
        "sampling_target_ids": target_ids,
        "legacy7_mapping": target_mapping,
        "sqlite_runtime_database": "NOT_ACTIVE",
        "postgresql_runtime": "NOT_USED",
        "launchd": plist,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plist", type=Path, default=INSTALLED_PLIST)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = run_preflight(plist_path=args.plist.expanduser().resolve())
    except SecretRequired as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (MssqlDependencyError, RuntimeConfigurationError, OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "FAIL", "error_type": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
