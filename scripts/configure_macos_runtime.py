#!/usr/bin/env python3
"""Provision the local non-secret config and two native Keychain entries.

Credential values come only from the current environment or an explicitly
selected local env file.  They are passed to ``security`` through stdin, not
argv, and are never printed.  The env file is read for migration only and is
not copied into the production configuration.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.runtime_configuration import (  # noqa: E402
    DEFAULT_RUNTIME_CONFIG_PATH,
    EXPECTED_SQL_DATABASE,
    KEYCHAIN_ACCOUNT_MDD_API,
    KEYCHAIN_ACCOUNT_MSSQL,
    KEYCHAIN_SERVICE_MDD_API,
    KEYCHAIN_SERVICE_MSSQL,
    MDD_API_CREDENTIAL,
    MSSQL_CREDENTIAL,
    RuntimeConfigurationError,
    SecretRequired,
    store_keychain_secret,
    write_local_runtime_config,
)


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise RuntimeConfigurationError("source env file cannot be read") from exc
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        if "=" not in stripped:
            continue
        name, raw_value = stripped.split("=", 1)
        name = name.strip()
        if not name or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_" for character in name):
            continue
        value = raw_value.strip()
        if value:
            try:
                parsed = shlex.split(value, comments=True, posix=True)
            except ValueError as exc:
                raise RuntimeConfigurationError("source env file contains an invalid value") from exc
            value = parsed[0] if parsed else ""
        values[name] = value
    return values


def _first(values: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        value = values.get(name)
        if value and value.strip():
            return value.strip()
    return None


def configure(*, runtime_config_path: Path, source_env_file: Path | None) -> dict[str, object]:
    file_values = _read_env_file(source_env_file) if source_env_file else {}
    values = dict(file_values)
    # Current process values are the explicit highest-precedence source.
    values.update({key: value for key, value in os.environ.items() if value is not None})

    host = _first(values, "MSSQL_HOST", "SQL_SERVER_HOST")
    user = _first(values, "MSSQL_USER", "SQL_SERVER_USER")
    database = _first(values, "MSSQL_DATABASE", "SQL_SERVER_DB") or EXPECTED_SQL_DATABASE
    port = _first(values, "MSSQL_PORT", "SQL_SERVER_CONTAINER_PORT") or "1433"
    if not host:
        raise RuntimeConfigurationError("MSSQL_HOST is required")
    if not user:
        raise RuntimeConfigurationError("MSSQL_USER is required")
    if database != EXPECTED_SQL_DATABASE:
        raise RuntimeConfigurationError(f"SQL Server database must be {EXPECTED_SQL_DATABASE}")
    try:
        port_number = int(port)
    except ValueError as exc:
        raise RuntimeConfigurationError("MSSQL port must be an integer") from exc

    mdd_api_key = _first(values, "MDD_API_KEY")
    mssql_password = _first(values, "MSSQL_PASSWORD", "SQL_SERVER_PASSWORD")
    if not mdd_api_key:
        raise SecretRequired(MDD_API_CREDENTIAL)
    if not mssql_password:
        raise SecretRequired(MSSQL_CREDENTIAL)

    config_values = {
        "MSSQL_HOST": host,
        "MSSQL_PORT": str(port_number),
        "MSSQL_DATABASE": database,
        "MSSQL_USER": user,
        "MSSQL_LOGIN_TIMEOUT_SECONDS": _first(values, "MSSQL_LOGIN_TIMEOUT_SECONDS") or "8",
        "MSSQL_QUERY_TIMEOUT_SECONDS": _first(values, "MSSQL_QUERY_TIMEOUT_SECONDS") or "30",
        "MSSQL_RAW_EVIDENCE_DIR": str(runtime_config_path.expanduser().resolve().parent / "mdd-raw-evidence"),
        "COLLECTOR_MDD_ENDPOINT": _first(values, "COLLECTOR_MDD_ENDPOINT")
        or "https://mdd.gorheinland.com/delfi",
        "COLLECTOR_REQUESTOR_REF": _first(values, "COLLECTOR_REQUESTOR_REF") or "CologneTransitIntelligence",
        "COLLECTOR_HEARTBEAT_SAFETY_MARGIN_SECONDS": _first(
            values,
            "COLLECTOR_HEARTBEAT_SAFETY_MARGIN_SECONDS",
        )
        or "5",
    }
    written = write_local_runtime_config(runtime_config_path, config_values)
    # ``security`` receives each value on stdin.  Its argv contains only
    # stable service/account labels.
    store_keychain_secret(service=KEYCHAIN_SERVICE_MDD_API, account=KEYCHAIN_ACCOUNT_MDD_API, secret=mdd_api_key)
    store_keychain_secret(service=KEYCHAIN_SERVICE_MSSQL, account=KEYCHAIN_ACCOUNT_MSSQL, secret=mssql_password)
    return {
        "status": "PASS",
        "runtime_config_path": str(written),
        "runtime_config_mode": oct(written.stat().st_mode & 0o777),
        "keychain_entries": [KEYCHAIN_ACCOUNT_MDD_API, KEYCHAIN_ACCOUNT_MSSQL],
        "credentials_written_to_argv": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-config", type=Path, default=DEFAULT_RUNTIME_CONFIG_PATH)
    parser.add_argument(
        "--source-env-file",
        type=Path,
        help="optional local migration source for non-secret settings and MSSQL_PASSWORD",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = configure(
            runtime_config_path=args.runtime_config.expanduser().resolve(),
            source_env_file=args.source_env_file.expanduser().resolve() if args.source_env_file else None,
        )
    except SecretRequired as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (OSError, RuntimeConfigurationError, ValueError) as exc:
        print(f"runtime configuration failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
