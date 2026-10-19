#!/usr/bin/env python3
"""Run the fresh, read-only SQL Server import-capacity preflight.

This command proves the explicitly supplied SQLite source, audits prior FAILED
batches for the same SHA-256, and reports SQL Server ROWS/LOG files separately.
It never opens an import batch and never changes SQL Server, SQLite, Windows, or
the collector runtime.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cologne_transit_collector.mssql_persistence import MssqlConnectionSettings  # noqa: E402
from cologne_transit_collector.sqlite_import import (  # noqa: E402
    EXPECTED_SQL_DATABASE,
    SQLiteImportError,
    inspect_sqlite_source,
    read_only_capacity_audit,
    read_only_failed_import_audit,
)


def _redact(message: str) -> str:
    message = " ".join(str(message).split())
    return re.sub(
        r"(?i)(password|token|secret|authorization|api[_-]?key)\s*[=:]\s*[^,; ]+",
        r"\1=<redacted>",
        message,
    )[:2000]


def _settings(args: argparse.Namespace) -> MssqlConnectionSettings:
    values = dict(os.environ)
    for name, value in (
        ("MSSQL_HOST", args.mssql_host),
        ("MSSQL_DATABASE", args.mssql_database),
        ("MSSQL_USER", args.mssql_user),
    ):
        if value:
            values[name] = value
    if args.mssql_port is not None:
        values["MSSQL_PORT"] = str(args.mssql_port)
    if args.mssql_password_env:
        password = values.get(args.mssql_password_env)
        if password:
            values["MSSQL_PASSWORD"] = password
    settings = MssqlConnectionSettings.from_environment(values)
    if settings.database != EXPECTED_SQL_DATABASE:
        raise SQLiteImportError(f"SQL Server target database must be {EXPECTED_SQL_DATABASE}")
    return settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="explicit proven SQLite source path")
    parser.add_argument("--report", type=Path, help="optional JSON report path")
    parser.add_argument("--mssql-host")
    parser.add_argument("--mssql-port", type=int)
    parser.add_argument("--mssql-database", default=EXPECTED_SQL_DATABASE)
    parser.add_argument("--mssql-user")
    parser.add_argument("--mssql-password-env", default="MSSQL_PASSWORD")
    return parser


def run(argv: list[str] | None = None) -> dict[str, object]:
    args = _parser().parse_args(argv)
    proof, inventory = inspect_sqlite_source(args.source)
    settings = _settings(args)
    capacity = read_only_capacity_audit(settings, proof)
    failed = read_only_failed_import_audit(settings, proof.sha256)
    result: dict[str, object] = {
        "status": "PASS"
        if capacity["status"] == "PASS" and failed["resume_reconcile_safe"]
        else "FAIL",
        "source": proof.to_dict(),
        "inventory": [item.to_dict() for item in inventory],
        "capacity_audit": capacity,
        "previous_failed_import_audit": failed,
        "writes_performed": False,
    }
    if args.report:
        report = args.report.expanduser().resolve()
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    try:
        result = run(argv)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
        return 0 if result["status"] == "PASS" else 1
    except (SQLiteImportError, ValueError, OSError) as exc:
        result = {"status": "FAIL", "error_type": type(exc).__name__, "reason": _redact(str(exc))}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 1
    except Exception as exc:
        result = {"status": "FAIL", "error_type": type(exc).__name__, "reason": _redact(str(exc))}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
